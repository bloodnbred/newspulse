"""The insights feed: six cards, each stating what it is based on.

Every card follows the same rule: it carries the number, the sample size, and
the sentence that limits the claim. A card that cannot show all three is not
emitted. An insights feed is where a reader takes a conclusion away, and a
conclusion without its sample size is the easiest kind of wrong to publish.

Each card also has an evidence floor below which it is skipped rather than
weakened. A "strongest association" computed from nine trading days is not a
weak finding, it is not a finding.

The sixth card is the direction model, and it exists to say out loud that the
model found no skill. That card is not optional.
"""

from __future__ import annotations

from typing import Any

import duckdb
import numpy as np
from scipy import stats

from dwm.logging_utils import get

log = get("dwm.inference.insights")

#: Below this, a card is withheld rather than published thinly.
MIN_EVENT_STOCKS = 5
MIN_LAG_SESSIONS = 60
MIN_SURPRISE_ROWS = 3
MIN_CLUSTER_MEMBERS = 4
MIN_CLUSTER_SILHOUETTE = 0.15

LAG_RANGE = range(-5, 6)

DEV_PREFIX = "DEV SAMPLE - computed from a subset, not the full corpus."


def _scalar(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] = ()) -> Any:
    return con.execute(sql, list(params)).fetchone()[0]


def _has(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(
        con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            [table],
        ).fetchone()[0]
    )


def build(con: duckdb.DuckDBPyConnection, *, dev_sample: bool = False) -> dict[str, Any]:
    """Every card that clears its evidence floor."""
    cards: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for name, builder in (
        ("surprise_days", _card_surprise_days),
        ("event_reaction", _card_event_reaction),
        ("lag_association", _card_lag_association),
        ("hottest_sector", _card_hottest_sector),
        ("stock_cluster", _card_stock_cluster),
        ("direction_honesty", _card_direction_honesty),
    ):
        try:
            card = builder(con)
        except Exception as exc:  # pragma: no cover - a card must never break the feed
            log.warning("insight card %s failed: %s", name, exc)
            card = None
        if card is None:
            skipped.append(
                {"card": name, "why": _why(con, name)}
            )
            continue
        if dev_sample:
            card["title"] = DEV_PREFIX + " " + card["title"]
            card["dev_sample"] = True
        cards.append(card)

    return {
        "cards": cards,
        "withheld": skipped,
        "count": len(cards),
        "of": 6,
    }


def _why(con: duckdb.DuckDBPyConnection, name: str) -> str:
    """Why a card was withheld. Says which floor, not just 'not available'."""
    floors = {
        "surprise_days": f"fewer than {MIN_SURPRISE_ROWS} news_driven days found",
        "event_reaction": f"fewer than {MIN_EVENT_STOCKS} stocks in any sector's reaction",
        "lag_association": f"fewer than {MIN_LAG_SESSIONS} sector-days to correlate",
        "hottest_sector": "no sector news facts in the last 90 days of the window",
        "stock_cluster": f"no cluster with at least {MIN_CLUSTER_MEMBERS} members",
        "direction_honesty": "the direction model has not been run",
    }
    return floors.get(name, "evidence below the minimum sample")


# ---------------------------------------------------------------------------


def _card_surprise_days(con: duckdb.DuckDBPyConnection) -> dict[str, Any] | None:
    if not _has(con, "mining_surprise_days"):
        return None
    rows = con.execute(
        """
        SELECT sector_name, session, news_count, news_heat_z, abs_return_z, avg_return
        FROM mining_surprise_days
        WHERE kind = 'news_driven'
        ORDER BY abs_return_z DESC
        LIMIT 3
        """
    ).fetchall()
    if len(rows) < MIN_SURPRISE_ROWS:
        return None
    return {
        "id": "surprise_days",
        "concept": "z-score surprise detection",
        "title": "The three sessions where news and price both spiked",
        "page": "explore",
        "evidence": {
            "days": [
                {
                    "sector": r[0],
                    "session": str(r[1]),
                    "news_count": int(r[2]),
                    "news_heat_z": r[3],
                    "abs_return_z": r[4],
                    "avg_return_pct": r[5],
                }
                for r in rows
            ],
            "n": len(rows),
            "z_thresholds": {"news": 2.0, "abs_return": 2.0},
        },
        "caution": (
            "Both measures are compared with the same sector's own preceding 60 "
            "sessions, so this is 'unusual for this sector', not 'unusual in the "
            "market'. News and price rising together is co-movement; it does not "
            "show the news moved the price."
        ),
    }


def _card_event_reaction(con: duckdb.DuckDBPyConnection) -> dict[str, Any] | None:
    if not _has(con, "mining_event_study"):
        return None
    row = con.execute(
        """
        WITH latest AS (
            SELECT max(e.event_key) AS event_key
            FROM mining_event_study m
            JOIN dim_event e ON e.event_key = m.event_key
            WHERE m.valid
        )
        SELECT e.event_label, e.event_type, e.start_date, s.sector_name,
               avg(m.car_0_5) AS mean_car, count(*) AS n
        FROM mining_event_study m
        JOIN dim_event e ON e.event_key = m.event_key
        JOIN dim_sector s ON s.sector_key = m.sector_key
        JOIN latest l ON l.event_key = m.event_key
        WHERE m.valid AND m.car_0_5 IS NOT NULL
        GROUP BY 1, 2, 3, 4
        HAVING count(*) >= ?
        ORDER BY abs(avg(m.car_0_5)) DESC
        LIMIT 1
        """,
        [MIN_EVENT_STOCKS],
    ).fetchone()
    if not row:
        return None
    values = [
        r[0]
        for r in con.execute(
            """
            SELECT m.car_0_5 FROM mining_event_study m
            JOIN dim_sector s ON s.sector_key = m.sector_key
            WHERE m.valid AND m.event_key = ? AND s.sector_name = ?
              AND m.car_0_5 IS NOT NULL
            """,
            [con.execute(
                "SELECT max(event_key) FROM mining_event_study WHERE valid"
            ).fetchone()[0], row[3]],
        ).fetchall()
    ]
    t_stat = p_value = None
    if len(values) >= 3:
        t_stat, p_value = stats.ttest_1samp(np.array(values, dtype=float), 0.0)
    return {
        "id": "event_reaction",
        "concept": "event study, market model",
        "title": f"The sector that moved most around {row[0]}",
        "page": "explore",
        "evidence": {
            "event": row[0],
            "event_type": row[1],
            "event_date": str(row[2]),
            "sector": row[3],
            "mean_car_0_5_pct": round(float(row[4]), 3),
            "n_stocks": int(row[5]),
            "t_statistic": round(float(t_stat), 3) if t_stat is not None else None,
            "p_value": round(float(p_value), 5) if p_value is not None else None,
        },
        "caution": (
            "Abnormal return means 'beyond what the market explains', not 'caused "
            "by the event'. Other news on the same day would produce the same "
            "number, and this corpus only covers one outlet."
        ),
    }


def _card_lag_association(con: duckdb.DuckDBPyConnection) -> dict[str, Any] | None:
    """The lag with the strongest news/price correlation, across -5..+5.

    The strongest of eleven correlations is selected, so the reported p-value has
    to survive a multiple-comparison correction. Without one it would look
    significant eleven times out of eleven by chance.
    """
    if not (_has(con, "fact_news_sector_daily") and _has(con, "v_stock_daily_clean")):
        return None
    frame = con.execute(
        """
        WITH news AS (
            SELECT sector_key, effective_trading_date_key AS session,
                   sum(news_count_non_market) AS news
            FROM fact_news_sector_daily GROUP BY 1, 2
        ),
        prices AS (
            SELECT d.sector_key, f.date_key AS session,
                   sum(f.abs_return_pct) / nullif(count(*), 0) AS abs_return
            FROM v_stock_daily_clean f
            JOIN dim_stock d ON d.stock_key = f.stock_key
            WHERE f.return_pct IS NOT NULL
            GROUP BY 1, 2
        )
        SELECT n.sector_key, n.session, n.news, p.abs_return
        FROM news n JOIN prices p
          ON p.sector_key = n.sector_key AND p.session = n.session
        ORDER BY n.sector_key, n.session
        """
    ).fetchall()
    if not frame:
        return None

    by_sector: dict[int, list[tuple[Any, float, float]]] = {}
    for sector_key, session, news, ret in frame:
        by_sector.setdefault(int(sector_key), []).append(
            (session, float(news or 0), float(ret or 0))
        )

    results = []
    for lag in LAG_RANGE:
        xs, ys = [], []
        for series in by_sector.values():
            for i in range(len(series)):
                j = i - lag
                if 0 <= j < len(series):
                    xs.append(series[j][1])      # news, shifted
                    ys.append(series[i][2])       # return today
        if len(xs) < MIN_LAG_SESSIONS:
            continue
        x, y = np.array(xs), np.array(ys)
        if x.std() == 0 or y.std() == 0:
            continue
        rho, p_value = stats.spearmanr(x, y)
        results.append((int(lag), float(rho), float(p_value), len(xs)))

    if not results:
        return None
    best = max(results, key=lambda r: abs(r[1]))
    # Bonferroni over the eleven lags tested.
    corrected = min(1.0, best[2] * len(results))
    return {
        "id": "lag_association",
        "concept": "drill-across, rank correlation",
        "title": f"Where news and price line up best: a lag of {best[0]:+d} session(s)",
        "page": "explore",
        "evidence": {
            "lag": best[0],
            "spearman_rho": round(best[1], 4),
            "p_value": round(best[2], 6),
            "p_value_bonferroni": round(corrected, 6),
            "n": best[3],
            "lags_tested": len(results),
        },
        "caution": (
            f"This is the strongest of {len(results)} lags tested, so its p-value "
            f"is corrected for that search ({best[2] * len(results):.3f}). "
            f"A rank correlation near zero means news volume and the size of the "
            f"move are close to unrelated in this corpus."
        ),
    }


def _card_hottest_sector(con: duckdb.DuckDBPyConnection) -> dict[str, Any] | None:
    if not _has(con, "cube_sector_month"):
        return None
    row = con.execute(
        """
        WITH bounds AS (
            SELECT max(date_key) AS last_key FROM dim_trading_calendar
            WHERE is_trading_day
        ),
        recent AS (
            SELECT c.* FROM cube_sector_month c, bounds b
            WHERE c.year * 100 + c.month * 0
                  >= (b.last_key // 10000) * 100
              AND (c.year * 100 + c.month) >= ((b.last_key // 10000) * 100 + (b.last_key // 100) % 100 - 2)
        )
        SELECT s.sector_name, sum(r.news_count) AS news
        FROM recent r JOIN dim_sector s ON s.sector_key = r.sector_key
        GROUP BY 1 ORDER BY news DESC LIMIT 1
        """
    ).fetchone()
    if not row or not row[1]:
        return None
    typical = con.execute(
        "SELECT avg(monthly) FROM ("
        "  SELECT sector_key, sum(news_count) AS monthly FROM cube_sector_month"
        "  GROUP BY 1, year, month)"
    ).fetchone()[0]
    ratio = (float(row[1]) / 3.0) / float(typical) if typical else None
    return {
        "id": "hottest_sector",
        "concept": "cube, sector-month",
        "title": f"{row[0]} carried the most news in the last 90 days",
        "page": "explore",
        "evidence": {
            "sector": row[0],
            "news_90d": int(row[1]),
            "typical_sector_month": round(float(typical), 1) if typical else None,
            "times_typical": round(ratio, 2) if ratio else None,
        },
        "caution": (
            "News volume measured against this corpus's own average. It says "
            "nothing about whether the market cared, and a sector can be noisy "
            "simply because its companies are named more often."
        ),
    }


def _card_stock_cluster(con: duckdb.DuckDBPyConnection) -> dict[str, Any] | None:
    if not _has(con, "mining_stock_clusters"):
        return None
    row = con.execute(
        """
        SELECT cluster_id, count(*) AS n, string_agg(symbol, ', ')
        FROM mining_stock_clusters
        WHERE cluster_id IS NOT NULL
        GROUP BY 1 HAVING count(*) >= ? ORDER BY n DESC LIMIT 1
        """,
        [MIN_CLUSTER_MEMBERS],
    ).fetchone()
    if not row:
        return None
    stats_row = con.execute(
        """
        SELECT round(avg(ann_volatility), 2), round(avg(beta), 2),
               round(avg(mentions_per_1000_days), 2)
        FROM mining_stock_clusters WHERE cluster_id = ?
        """,
        [row[0]],
    ).fetchone()
    overall = con.execute(
        "SELECT round(avg(ann_volatility), 2), round(avg(beta), 2) "
        "FROM mining_stock_clusters"
    ).fetchone()
    return {
        "id": "stock_cluster",
        "concept": "clustering, PCA",
        "title": f"A group of {row[1]} Nifty 50 stocks that behave alike",
        "page": "explore",
        "evidence": {
            "cluster_id": int(row[0]),
            "n_members": int(row[1]),
            "members": row[2].split(", "),
            "mean_ann_volatility": stats_row[0],
            "mean_beta": stats_row[1],
            "mean_mentions_per_1000_days": stats_row[2],
            "all_stocks_mean_volatility": overall[0],
            "all_stocks_mean_beta": overall[1],
        },
        "caution": (
            "Similar behaviour, not similar business. Stocks move together "
            "partly because they share an industry, and the news-volume feature "
            "that helped build this group is itself an industry signal."
        ),
    }


def _card_direction_honesty(con: duckdb.DuckDBPyConnection) -> dict[str, Any] | None:
    """The honesty card. Always emitted when the model has been run, whatever
    the result says - and especially when it says nothing good."""
    if not _has(con, "mining_direction_results"):
        return None
    row = con.execute(
        """
        SELECT model, accuracy, baseline_majority, baseline_yesterday,
               binom_p_vs_baseline, n
        FROM mining_direction_results
        WHERE accuracy IS NOT NULL
        ORDER BY accuracy DESC LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    beats = row[2] is not None and row[1] is not None and row[1] > row[2]
    headline = (
        "No, news does not tell you tomorrow's direction"
        if not beats
        else "News features beat the majority baseline, narrowly"
    )
    return {
        "id": "direction_honesty",
        "concept": "classification, chronological hold-out",
        "title": headline,
        "page": "explore",
        "evidence": {
            "best_model": row[0],
            "accuracy": row[1],
            "baseline_majority_class": row[2],
            "baseline_same_as_yesterday": row[3],
            "p_value_vs_baseline": row[4],
            "n_test_rows": int(row[5]),
            "beats_baseline": beats,
        },
        "caution": (
            "Tested on the last 15% of the window only, after the training "
            "period, with no shuffling. A result at or below the majority "
            "baseline means these features carry no usable skill - which is a "
            "finding, not a failure to tune."
        ),
    }


def write(con: duckdb.DuckDBPyConnection, *, dev_sample: bool = False) -> dict[str, Any]:
    return build(con, dev_sample=dev_sample)
