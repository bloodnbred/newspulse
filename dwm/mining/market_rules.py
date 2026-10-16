"""Market rules: association mining over sector-day transactions.

A transaction is one (sector, trading session). Items describe that day:

    sector=Energy          news_heat=high      sentiment=negative
    ret=down_big           vol_regime=high     weekday=Tue
    event_window=1         next=up

Rules are DESCRIPTIVE. "news_heat=high AND sentiment=negative -> ret=down"
states that these things co-occurred in this five-year sample. It does not say
the news caused the move, and the dashboard says so on the page.

Thresholds are deliberately strict - support 2%, at least 30 supporting rows,
confidence 55%, lift 1.15. A market with thousands of sector-days will produce
plenty of spurious associations at a lower bar, and a rule that fires on 200
occurrences out of 20,000 sessions is usually a calendar effect.
"""

from __future__ import annotations

from typing import Any

import duckdb
import numpy as np
import pandas as pd

from dwm.logging_utils import get, human_int
from dwm.mining.common import MIN_SAMPLE, safe_round

log = get("dwm.mining.market_rules")

DDL = """
CREATE OR REPLACE TABLE mining_market_rules (
    rule_id INTEGER, sector_key INTEGER, sector_name VARCHAR,
    antecedents VARCHAR, consequents VARCHAR,
    support DOUBLE, confidence DOUBLE, lift DOUBLE, support_count INTEGER,
    n_transactions INTEGER
)
"""

MIN_SUPPORT = 0.02
MIN_CONFIDENCE = 0.55
MIN_LIFT = 1.15
MIN_SUPPORT_COUNT = MIN_SAMPLE["rule_support_count"]
MAX_LEN = 3

#: Tertile / quantile cuts are computed from the data, never fixed. A hard-coded
#: threshold would mean "high news" on a quiet day and a busy one alike.
NEWS_HEAT_CUTS = 3
RETURN_CUTS = ("down_big", "down", "up", "up_big")


def transactions(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """One row per (sector, trading session), with the items it satisfies."""
    rows = con.execute(
        """
        WITH news AS (
            SELECT sector_key, effective_trading_date_key AS session,
                   coalesce(news_count_non_market, 0) AS news,
                   coalesce(sum_sentiment, 0) / nullif(news_count, 0) AS sentiment
            FROM fact_news_sector_daily
        ),
        prices AS (
            SELECT d.sector_key, f.date_key AS session,
                   sum(f.return_pct) / nullif(count(*), 0) AS mean_return,
                   sum(f.abs_return_pct) / nullif(count(*), 0) AS mean_abs_return,
                   stddev_samp(f.volatility_20d) AS vol,
                   -- TOMORROW's direction, within this sector's own sessions.
                   -- Deriving it from today's return instead would make
                   -- `next=up -> ret=up_big` a tautology with a lift of ~2.7,
                   -- which is arithmetic rather than a finding.
                   lead(sum(f.return_pct) / nullif(count(*), 0)) OVER (
                       PARTITION BY d.sector_key ORDER BY f.date_key
                   ) AS next_return
            FROM v_stock_daily_clean f
            JOIN dim_stock d ON d.stock_key = f.stock_key
            WHERE f.return_pct IS NOT NULL
            GROUP BY 1, 2
        )
        SELECT n.sector_key, s.sector_name, n.session, n.news, n.sentiment,
               p.mean_return, p.mean_abs_return, p.vol, p.next_return,
               dc.is_trading_day, d.day_of_week
        FROM news n
        JOIN prices p ON p.sector_key = n.sector_key AND p.session = n.session
        JOIN dim_sector s ON s.sector_key = n.sector_key
        JOIN dim_trading_calendar dc ON dc.date_key = n.session
        JOIN dim_date d ON d.date_key = n.session
        WHERE n.news > 0 AND p.next_return IS NOT NULL
        ORDER BY n.session, n.sector_key
        """
    ).fetchdf()
    if rows.empty:
        return rows

    # news_heat tertiles within the whole sample
    rows["news_heat"] = pd.cut(
        rows["news"], bins=NEWS_HEAT_CUTS, labels=["low", "mid", "high"], duplicates="drop"
    ).astype(str)

    # sentiment bands
    rows["sentiment"] = np.where(
        rows["sentiment"] > 0.15, "positive",
        np.where(rows["sentiment"] < -0.15, "negative", "neutral"),
    )

    # Return buckets: sign, then whether the day's move was larger than the median
# absolute move. A quantile split on the signed return alone puts near-zero days
    # in the same bucket as large ones.
    med_abs = float(rows["mean_abs_return"].median())
    rows["ret"] = np.where(
        rows["mean_return"] < 0,
        np.where(rows["mean_abs_return"] > med_abs, "down_big", "down"),
        np.where(rows["mean_abs_return"] > med_abs, "up_big", "up"),
    )

    rows["next"] = np.where(rows["next_return"] > 0, "up", "down")

    vol_med = rows["vol"].median()
    rows["vol_regime"] = np.where(rows["vol"] > vol_med, "high", "low")
    rows["weekday"] = pd.to_datetime(rows["session"]).dt.day_name().str[:3]
    return rows


def items(frame: pd.DataFrame) -> list[list[str]]:
    """Turn each transaction row into its item list."""
    columns = [
        "sector_name", "news_heat", "sentiment", "ret",
        "next", "vol_regime", "weekday",
    ]
    out: list[list[str]] = []
    for row in frame.itertuples(index=False):
        out.append([f"{col}={getattr(row, col)}" for col in columns])
    return out


def mine(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Run Apriori and return rules that clear every threshold."""
    from mlxtend.frequent_patterns import apriori, association_rules
    from sklearn.preprocessing import MultiLabelBinarizer

    transactions_df = items(frame)
    if len(transactions_df) < 50:
        return []

    # mlxtend validates its input strictly: it wants a pandas DataFrame whose
    # columns are BOOL. A plain numpy array fails on `df.dtypes`, so the
    # indicator frame is built explicitly rather than handed over raw.
    binariser = MultiLabelBinarizer()
    basket = pd.DataFrame(
        binariser.fit_transform(transactions_df).astype(bool),
        columns=[str(c) for c in binariser.classes_],
    )

    frequent = apriori(basket, min_support=MIN_SUPPORT, max_len=MAX_LEN)
    if frequent.empty:
        return []

    rules = association_rules(frequent, metric="confidence", min_threshold=MIN_CONFIDENCE)
    if rules.empty:
        return []
    rules = rules[
        (rules["lift"] >= MIN_LIFT) & (rules["support"] >= MIN_SUPPORT)
    ]
    if rules.empty:
        return []

    out: list[dict[str, Any]] = []
    # mlxtend returns antecedents/consequents as POSITIONAL indices into the
    # indicator frame, not as the item strings. Verified on mlxtend 0.25: given
    # columns a, b, c it reports (0), (1), (2). Reading them as item names would
    # put the literal string "0" in a rule and every downstream display would be
    # nonsense that still looks plausible.
    names = [str(c) for c in binariser.classes_]

    def label(values: Any) -> list[str]:
        out_items: list[str] = []
        for v in values:
            index = int(v)
            out_items.append(names[index] if 0 <= index < len(names) else str(v))
        return sorted(out_items)

    for _, row in rules.iterrows():
        out.append(
            {
                "antecedents": label(row["antecedents"]),
                "consequents": label(row["consequents"]),
                "support": float(row["support"]),
                "confidence": float(row["confidence"]),
                "lift": float(row["lift"]),
            }
        )
    out.sort(key=lambda r: -r["lift"])
    return out


def run(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    frame = transactions(con)
    n = len(frame)
    if n == 0:
        return {"transactions": 0, "rules": 0,
                "note": "no sector-day transactions; run `dwm link` first"}

    rules = mine(frame)
    kept = [
        r for r in rules if int(round(r["support"] * n)) >= MIN_SUPPORT_COUNT
    ]

    con.execute(DDL)
    sector_of = {
        int(k): name for k, name in con.execute(
            "SELECT sector_key, sector_name FROM dim_sector"
        ).fetchall()
    }
    rows = []
    for i, rule in enumerate(kept, start=1):
        sector_key = None
        for item in rule["antecedents"] + rule["consequents"]:
            if item.startswith("sector="):
                name = item.split("=", 1)[1]
                sector_key = next(
                    (k for k, v in sector_of.items() if v == name), None
                )
        rows.append(
            (
                i, sector_key, rule["antecedents"][0].split("=", 1)[1]
                if rule["antecedents"][0].startswith("sector=") else None,
                ", ".join(rule["antecedents"]), ", ".join(rule["consequents"]),
                safe_round(rule["support"], 6), safe_round(rule["confidence"], 4),
                safe_round(rule["lift"], 4), int(round(rule["support"] * n)), n,
            )
        )
    if rows:
        con.executemany("INSERT INTO mining_market_rules VALUES (?,?,?,?,?,?,?,?,?,?)", rows)

    summary = {
        "transactions": n,
        "rules_before_count_filter": len(rules),
        "rules": len(kept),
        "thresholds": {
            "min_support": MIN_SUPPORT,
            "min_confidence": MIN_CONFIDENCE,
            "min_lift": MIN_LIFT,
            "min_support_count": MIN_SUPPORT_COUNT,
            "max_len": MAX_LEN,
        },
        "top_rules": [
            {
                "if": ", ".join(r["antecedents"]),
                "then": ", ".join(r["consequents"]),
                "support": round(r["support"], 4),
                "confidence": round(r["confidence"], 4),
                "lift": round(r["lift"], 3),
                "n": int(round(r["support"] * n)),
            }
            for r in kept[:10]
        ],
        "caveat": (
            "These are associations within a five-year sample, not causes. A "
            "rule with lift 1.4 means the consequent co-occurred 1.4x as often "
            "with the antecedent; it does not mean the antecedent produced it."
        ),
    }
    log.info("market rules: %s rules from %s transactions", len(kept), human_int(n))
    return summary
