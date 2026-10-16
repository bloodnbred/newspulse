"""Stock clusters: group Nifty 50 constituents by how they behave.

The features are chosen so that each is a different KIND of behaviour, not
another measure of the same thing:

    volatility        how much it moves
    mean return       which way it drifts
    beta              how much it follows the market
    max drawdown      how bad the worst fall was
    mentions / 1000d  how much news it gets
    mention sentiment the mood of that news
    news sensitivity  whether heavy news days come with big moves

`news_sensitivity` is a Spearman correlation between a stock's daily non-market
news count and its absolute return, over trailing days only. It is NULL below
100 news days rather than computed on a handful of points, because a
correlation from nine days is not a correlation.

Clusters are named from their standardised centroids, so "high volatility, high
news sensitivity" is a description of the group rather than a label invented
after seeing it.
"""

from __future__ import annotations

from typing import Any

import duckdb
import numpy as np
from scipy import stats
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from dwm.logging_utils import get
from dwm.mining.common import SEED, max_drawdown, safe_round

log = get("dwm.mining.stock_clusters")

DDL = """
CREATE OR REPLACE TABLE mining_stock_clusters (
    stock_key INTEGER, cluster_id SMALLINT, symbol VARCHAR, sector_name VARCHAR,
    ann_volatility DOUBLE, mean_daily_return DOUBLE, beta DOUBLE,
    max_drawdown DOUBLE, mentions_per_1000_days DOUBLE,
    mention_sentiment DOUBLE, news_sensitivity DOUBLE,
    pc1 DOUBLE, pc2 DOUBLE, n_sessions INTEGER
)
"""

FEATURES = [
    "ann_volatility", "mean_daily_return", "beta", "max_drawdown",
    "mentions_per_1000_days", "mention_sentiment", "news_sensitivity",
]

#: Below this many news days the news-sensitivity correlation is meaningless,
#: and a number off nine days is worse than no number.
MIN_NEWS_DAYS = 100

K_RANGE = range(3, 7)

FEATURE_LABELS = {
    "ann_volatility": "volatility",
    "mean_daily_return": "mean return",
    "beta": "market sensitivity",
    "max_drawdown": "drawdown risk",
    "mentions_per_1000_days": "news volume",
    "mention_sentiment": "news mood",
    "news_sensitivity": "news sensitivity",
}


def build_features(con: duckdb.DuckDBPyConnection) -> dict[str, dict[str, float]]:
    """One row per stock: the seven behavioural features, or None where undefined."""
    market = {
        r[0]: float(r[1])
        for r in con.execute(
            "SELECT trade_date, return_pct FROM fact_market_daily WHERE return_pct IS NOT NULL"
        ).fetchall()
    }

    features: dict[str, dict[str, float]] = {}
    rows = con.execute(
        """
        SELECT f.stock_key, f.trade_date, f.adj_close, f.return_pct
        FROM v_stock_daily_clean f
        WHERE f.return_pct IS NOT NULL AND f.adj_close IS NOT NULL
        ORDER BY f.stock_key, f.trade_date
        """
    ).fetchall()

    by_stock: dict[int, list[tuple[Any, float, float]]] = {}
    for stock_key, day, adj, ret in rows:
        by_stock.setdefault(int(stock_key), []).append((day, float(adj), float(ret)))

    mentions: dict[int, list[tuple[Any, float]]] = {}
    for stock_key, day, sentiment in con.execute(
        """
        SELECT m.stock_key, m.effective_trading_date_key, h.sentiment_compound
        FROM fact_news_stock_mention m
        JOIN fact_headline h ON h.headline_id = m.headline_id
        WHERE h.in_window AND h.sentiment_compound IS NOT NULL
        """
    ).fetchall():
        mentions.setdefault(int(stock_key), []).append((int(day), float(sentiment)))

    news_heat = {
        (int(sk), int(dk)): int(n)
        for sk, dk, n in con.execute(
            """
            SELECT d.stock_key, s.effective_trading_date_key, coalesce(s.news_count_non_market, 0)
            FROM fact_news_sector_daily s
            JOIN dim_stock d ON d.sector_key = s.sector_key
            """
        ).fetchall()
    }

    for stock_key, series in by_stock.items():
        returns = np.array([r[2] for r in series], dtype=float)
        prices = np.array([r[1] for r in series], dtype=float)
        days = [r[0] for r in series]
        if len(returns) < 2:
            continue

        ann_vol = float(returns.std(ddof=1) * np.sqrt(252)) if len(returns) > 1 else np.nan
        mean_ret = float(returns.mean())

        paired = [
            (r, market[d]) for r, d in zip(returns, days, strict=True) if d in market
        ]
        if len(paired) >= 30:
            beta = float(stats.linregress([p[1] for p in paired], [p[0] for p in paired]).slope)
        else:
            beta = np.nan

        stock_mentions = mentions.get(stock_key, [])
        mentions_per_1000 = (
            len(stock_mentions) / (len(returns) / 1000.0) if len(returns) >= 100 else np.nan
        )
        mention_sentiment = (
            float(np.mean([m[1] for m in stock_mentions])) if stock_mentions else np.nan
        )

        sector_news = np.array(
            [news_heat.get((stock_key, int(d.strftime("%Y%m%d"))), 0) for d in days],
            dtype=float,
        )
        abs_ret = np.abs(returns)
        if sector_news.std(ddof=1) > 0 and abs_ret.std(ddof=1) > 0:
            sensitivity = float(stats.spearmanr(sector_news, abs_ret).statistic)
            news_days = int((sector_news > 0).sum())
        else:
            sensitivity = np.nan
            news_days = 0
        if news_days < MIN_NEWS_DAYS:
            sensitivity = np.nan

        features[str(stock_key)] = {
            "ann_volatility": ann_vol,
            "mean_daily_return": mean_ret,
            "beta": beta,
            "max_drawdown": max_drawdown(prices),
            "mentions_per_1000_days": mentions_per_1000,
            "mention_sentiment": mention_sentiment,
            "news_sensitivity": sensitivity,
        }
    return features


def cluster(features: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Standardise, fit KMeans for k in 3..6, choose by silhouette.

    Rows with a missing feature are dropped rather than imputed. Imputing a
    missing news-sensitivity with the mean would make that stock look average at
    exactly the thing it was missing.
    """
    usable = {
        key: row
        for key, row in features.items()
        if all(np.isfinite(v) for v in row.values())
    }
    dropped = sorted(set(features) - set(usable))
    if len(usable) < 4:
        return {"error": f"only {len(usable)} stocks have every feature; need 4"}

    keys = sorted(usable)
    matrix = np.array([[usable[k][f] for f in FEATURES] for k in keys], dtype=float)
    scaled = StandardScaler().fit_transform(matrix)

    best: dict[str, Any] = {}
    for k in K_RANGE:
        if len(keys) <= k:
            break
        model = KMeans(n_clusters=k, random_state=SEED, n_init=10).fit(scaled)
        score = float(silhouette_score(scaled, model.labels_))
        best[k] = {"silhouette": score, "labels": model.labels_, "inertia": float(model.inertia_)}
    if not best:
        return {"error": "not enough stocks for any k in 3..6"}

    chosen_k = max(best, key=lambda k: best[k]["silhouette"])
    labels = best[chosen_k]["labels"]

    projection = PCA(n_components=2, random_state=SEED).fit_transform(scaled)

    # Name each cluster from the direction of its standardised centroid. The
    # mean of already-standardised rows IS the standardised centroid, so no
    # second scaler is needed.
    centroids = np.array(
        [scaled[labels == c].mean(axis=0) for c in range(chosen_k)]
    )
    names: dict[int, str] = {}
    for c in range(chosen_k):
        parts = []
        for i, feature in enumerate(FEATURES):
            if abs(centroids[c][i]) < 0.35:
                continue
            word = "high" if centroids[c][i] > 0 else "low"
            parts.append(f"{word} {FEATURE_LABELS[feature]}")
        names[c] = ", ".join(parts) or "mixed"

    assignments = {
        key: {
            "cluster_id": int(labels[i]),
            "cluster_name": names[int(labels[i])],
            "pc1": float(projection[i][0]),
            "pc2": float(projection[i][1]),
        }
        for i, key in enumerate(keys)
    }
    return {
        "k": chosen_k,
        "silhouette": best[chosen_k]["silhouette"],
        "silhouette_by_k": {k: safe_round(v["silhouette"], 4) for k, v in best.items()},
        "cluster_names": names,
        "assignments": assignments,
        "dropped": dropped,
        "n": len(keys),
    }


def run(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    features = build_features(con)
    result = cluster(features)
    if "error" in result:
        log.warning("stock clusters skipped: %s", result["error"])
        return result

    meta = {
        r[0]: (r[1], r[2])
        for r in con.execute(
            "SELECT stock_key, symbol, sector_key FROM dim_stock"
        ).fetchall()
    }
    sector_name = {
        int(k): n for k, n in con.execute(
            "SELECT sector_key, sector_name FROM dim_sector"
        ).fetchall()
    }

    con.execute(DDL)
    rows = []
    for stock_key, values in features.items():
        key = int(stock_key)
        assignment = result["assignments"].get(stock_key)
        symbol, sector_key = meta.get(key, (None, None))
        rows.append(
            (
                key,
                assignment["cluster_id"] if assignment else None,
                symbol,
                sector_name.get(int(sector_key)) if sector_key else None,
                safe_round(values["ann_volatility"]),
                safe_round(values["mean_daily_return"]),
                safe_round(values["beta"]),
                safe_round(values["max_drawdown"]),
                safe_round(values["mentions_per_1000_days"]),
                safe_round(values["mention_sentiment"]),
                safe_round(values["news_sensitivity"]),
                safe_round(assignment["pc1"], 4) if assignment else None,
                safe_round(assignment["pc2"], 4) if assignment else None,
                con.execute(
                    "SELECT count(*) FROM fact_stock_daily WHERE stock_key = ?", [key]
                ).fetchone()[0],
            )
        )
    con.executemany(
        "INSERT INTO mining_stock_clusters VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )

    sizes: dict[int, int] = {}
    for assignment in result["assignments"].values():
        cid = assignment["cluster_id"]
        sizes[cid] = sizes.get(cid, 0) + 1

    summary = {
        "k": result["k"],
        "silhouette": safe_round(result["silhouette"], 4),
        "silhouette_by_k": result["silhouette_by_k"],
        "cluster_names": result["cluster_names"],
        "cluster_sizes": {str(k): v for k, v in sorted(sizes.items())},
        "clustered": result["n"],
        "dropped_for_missing_features": result["dropped"],
        "caveat": (
            "Clusters describe similarity in historical behaviour, not business "
            "similarity or future returns. Stocks can move together because of "
            "their industry, and industry membership is partly visible in the "
            "news-volume feature that produced the cluster."
        ),
    }
    log.info(
        "stock clusters: k=%s silhouette=%.3f over %s stocks",
        result["k"], result["silhouette"], result["n"],
    )
    return summary
