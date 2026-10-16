"""fact_stock_daily: one row per stock per trading session.

Returns here are PERCENTAGE POINTS, matching fact_market_daily.return_pct.
That convention is not a style choice: the pack specified fractions with a
0.25 outlier threshold, which against v1's units would mean 0.25 percentage
points and would flag essentially nothing while looking like it worked.

They are computed from `adj_close`, not `close`. On this source `close` is
split-adjusted but not dividend-adjusted (ITC in July 2020: 199.85 against
147.14), so `close` alone silently understates total return. The basis used is
recorded per row in `return_basis`, so a later reader can tell which one a
number came from.

`turnover` is NOT published by the price source. It is derived here as
close * volume and flagged `turnover_is_derived`, because a figure that looks
exchange-reported and is not is worse than no figure at all.
"""

from __future__ import annotations

from typing import Any

import duckdb

from dwm.logging_utils import get, human_int

log = get("dwm.warehouse.stock_facts")

TABLE = "fact_stock_daily"
CLEAN_VIEW = "v_stock_daily_clean"

# AGENTS.md rule 14. Percentage points, so 25.0 means 25%.
OUTLIER_THRESHOLD_PCT = 25.0
VOLATILITY_WINDOW = 20

RETURN_BASIS = "adj_close"

DDL = """
CREATE OR REPLACE TABLE fact_stock_daily AS
WITH src AS (
    SELECT
        CAST(strftime(s.trade_date, '%Y%m%d') AS INTEGER) AS date_key,
        d.stock_key                                      AS stock_key,
        s.trade_date                                     AS trade_date,
        s.open                                           AS open,
        s.high                                           AS high,
        s.low                                            AS low,
        s.close                                          AS close,
        s.adj_close                                      AS adj_close,
        s.volume                                         AS volume
    FROM stg_stocks s
    -- stock_key comes from the dimension, matched on symbol. stg_stocks is
    -- keyed by symbol because that is what the source file gives us.
    JOIN dim_stock d ON d.symbol = s.symbol
    WHERE s.adj_close IS NOT NULL
      AND s.trade_date IS NOT NULL
),
lagged AS (
    SELECT
        *,
        lag(adj_close) OVER w        AS prev_adj_close,
        lag(adj_close, 5) OVER w     AS adj_close_5d_ago,
        lag(close)    OVER w        AS prev_close,
        lead(adj_close) OVER w       AS next_adj_close
    FROM src
    WINDOW w AS (PARTITION BY stock_key ORDER BY trade_date)
),
measured AS (
    SELECT
        *,
        CASE WHEN prev_adj_close IS NULL OR prev_adj_close = 0 THEN NULL
             ELSE round((adj_close / prev_adj_close - 1.0) * 100.0, 6)
        END AS return_pct
    FROM lagged
)
SELECT
    m.date_key,
    m.stock_key,
    {dataset_key}                                     AS dataset_key,
    m.trade_date,
    m.open,
    m.high,
    m.low,
    m.close,
    m.adj_close,
    m.volume,
    -- Derived, not exchange-reported. See the module docstring.
    CASE WHEN m.close IS NOT NULL AND m.volume IS NOT NULL
         THEN m.close * m.volume END                   AS turnover,
    m.return_pct,
    CASE WHEN m.adj_close IS NOT NULL AND m.prev_adj_close IS NOT NULL
         THEN round(m.adj_close - m.prev_adj_close, 4) END AS abs_change,
    round(abs(m.return_pct), 6)                        AS abs_return_pct,
    CASE WHEN m.prev_close IS NULL OR m.prev_close = 0 THEN NULL
         ELSE round((m.high - m.low) / m.prev_close * 100.0, 6)
    END                                               AS range_pct,
    -- Identical definition to fact_market_daily.volatility_20d: sample
    -- standard deviation over 20 rows, NULL until the window is full. A
    -- "20-day volatility" from three observations is not a 20-day volatility.
    CASE WHEN count(m.return_pct) OVER vol >= {vol_window}
         THEN round(stddev_samp(m.return_pct) OVER vol, 6)
    END                                               AS volatility_20d,
    CASE WHEN m.adj_close_5d_ago IS NULL OR m.adj_close_5d_ago = 0 THEN NULL
         ELSE round((m.adj_close / m.adj_close_5d_ago - 1.0) * 100.0, 6)
    END                                               AS return_5d,
    CASE WHEN m.return_pct IS NULL THEN NULL
         WHEN m.return_pct > 0 THEN 1
         WHEN m.return_pct < 0 THEN -1
         ELSE 0 END                                   AS return_sign,
    coalesce(abs(m.return_pct) > {outlier}, false)    AS is_outlier,
    CASE WHEN m.next_adj_close IS NULL OR m.adj_close IS NULL OR m.adj_close = 0 THEN NULL
         ELSE round((m.next_adj_close / m.adj_close - 1.0) * 100.0, 6)
    END                                               AS next_day_return_pct,
    CASE WHEN m.next_adj_close IS NULL OR m.adj_close IS NULL OR m.adj_close = 0 THEN NULL
         ELSE m.next_adj_close > m.adj_close
    END                                               AS next_day_up,
    '{basis}'                                         AS return_basis,
    true                                              AS turnover_is_derived
FROM measured m
WINDOW vol AS (
    PARTITION BY m.stock_key ORDER BY m.trade_date
    ROWS BETWEEN {vol_window_minus_1} PRECEDING AND CURRENT ROW
)
ORDER BY m.stock_key, m.trade_date
"""

CLEAN_VIEW_DDL = f"""
CREATE OR REPLACE VIEW {CLEAN_VIEW} AS
SELECT *
FROM {TABLE}
WHERE is_outlier IS NOT TRUE
"""


def _dataset_key(con: duckdb.DuckDBPyConnection) -> int:
    """The dataset_key for 'stocks', read from dim_dataset rather than assumed."""
    row = con.execute(
        "SELECT dataset_key FROM dim_dataset WHERE dataset_code = 'stocks'"
    ).fetchone()
    if not row:
        raise RuntimeError(
            "dim_dataset has no 'stocks' row; run `python -m dwm etl` so it is built"
        )
    return int(row[0])


def build_stock_daily(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Build fact_stock_daily and the clean view. Returns row counts and flags."""
    for table in ("stg_stocks", "dim_stock"):
        if not con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone()[0]:
            raise FileNotFoundError(
                f"cannot build {TABLE}: {table} is missing. "
                "Run `python -m dwm ingest --dataset stocks` and `python -m dwm etl` first."
            )

    key = _dataset_key(con)
    con.execute(
        DDL.format(
            dataset_key=key,
            outlier=OUTLIER_THRESHOLD_PCT,
            vol_window=VOLATILITY_WINDOW,
            vol_window_minus_1=VOLATILITY_WINDOW - 1,
            basis=RETURN_BASIS,
        )
    )
    con.execute(CLEAN_VIEW_DDL)

    counts: dict[str, Any] = {
        "rows": int(con.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]),
        "symbols": int(
            con.execute(f"SELECT count(DISTINCT stock_key) FROM {TABLE}").fetchone()[0]
        ),
        "outliers": int(
            con.execute(f"SELECT count(*) FROM {TABLE} WHERE is_outlier").fetchone()[0]
        ),
        "first_rows_without_return": int(
            con.execute(f"SELECT count(*) FROM {TABLE} WHERE return_pct IS NULL").fetchone()[0]
        ),
        "threshold_pct": OUTLIER_THRESHOLD_PCT,
        "return_basis": RETURN_BASIS,
    }
    counts["duplicate_keys"] = int(
        con.execute(
            f"SELECT count(*) FROM (SELECT stock_key, trade_date FROM {TABLE} "
            f"GROUP BY 1, 2 HAVING count(*) > 1) t"
        ).fetchone()[0]
    )
    log.info(
        "%s: %s rows, %s symbols, %s outliers above %s%%",
        TABLE, human_int(counts["rows"]), counts["symbols"],
        human_int(counts["outliers"]), OUTLIER_THRESHOLD_PCT,
    )
    if counts["outliers"]:
        log.warning(
            "%s: %s outlier rows kept but flagged; statistics and models exclude them",
            TABLE, human_int(counts["outliers"]),
        )
    return counts


def outlier_rows(con: duckdb.DuckDBPyConnection, limit: int = 50) -> list[dict[str, Any]]:
    """The flagged rows themselves, so a reviewer can judge the flag."""
    return [
        {
            "symbol": r[0],
            "trade_date": r[1].isoformat(),
            "return_pct": round(float(r[2]), 4),
            "close": r[3],
            "adj_close": r[4],
        }
        for r in con.execute(
            f"""
            SELECT d.symbol, f.trade_date, f.return_pct, f.close, f.adj_close
            FROM {TABLE} f JOIN dim_stock d USING (stock_key)
            WHERE f.is_outlier
            ORDER BY abs(f.return_pct) DESC
            LIMIT {int(limit)}
            """
        ).fetchall()
    ]


def coverage(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Per-symbol row counts and gaps, for the data-quality report."""
    return {
        "symbols_staged": int(
            con.execute("SELECT count(DISTINCT symbol) FROM stg_stocks").fetchone()[0]
        ),
        "symbols_in_fact": int(
            con.execute(f"SELECT count(DISTINCT stock_key) FROM {TABLE}").fetchone()[0]
        ),
        "symbols_without_prices": [
            r[0]
            for r in con.execute(
                "SELECT symbol FROM dim_stock "
                "WHERE price_source IS NULL ORDER BY symbol"
            ).fetchall()
        ],
        "sparse": [
            {"symbol": r[0], "rows": int(r[1])}
            for r in con.execute(
                f"""
                SELECT d.symbol, count(*) AS n FROM {TABLE} f
                JOIN dim_stock d USING (stock_key)
                GROUP BY 1
                HAVING count(*) < {VOLATILITY_WINDOW * 4}
                ORDER BY n, 1
                """
            ).fetchall()
        ],
        "non_survivors": [
            r[0]
            for r in con.execute(
                "SELECT symbol FROM dim_stock WHERE NOT is_survivor ORDER BY symbol"
            ).fetchall()
        ],
        "trading_weekend_sessions": _weekend_sessions(con),
    }


def _weekend_sessions(con: duckdb.DuckDBPyConnection) -> list[str]:
    if not con.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name = 'dim_trading_calendar'"
    ).fetchone()[0]:
        return []
    from dwm.etl.calendar import trading_weekend_sessions

    return trading_weekend_sessions(con)


def check(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Invariants the tests assert."""
    if not con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [TABLE]
    ).fetchone()[0]:
        return {"present": False}
    total = int(con.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0])
    dupes = int(
        con.execute(
            f"SELECT count(*) FROM (SELECT stock_key, trade_date FROM {TABLE} "
            f"GROUP BY 1,2 HAVING count(*) > 1) t"
        ).fetchone()[0]
    )
    return {
        "present": True,
        "rows": total,
        "duplicate_keys": dupes,
        "clean_view_rows": int(
            con.execute(f"SELECT count(*) FROM {CLEAN_VIEW}").fetchone()[0]
        ),
        "clean_view_excludes_every_outlier": (
            int(con.execute(f"SELECT count(*) FROM {CLEAN_VIEW} WHERE is_outlier").fetchone()[0])
            == 0
        ),
        "orphan_stock_keys": int(
            con.execute(
                f"SELECT count(*) FROM {TABLE} f WHERE NOT EXISTS "
                "(SELECT 1 FROM dim_stock d WHERE d.stock_key = f.stock_key)"
            ).fetchone()[0]
        ),
        "outliers": int(
            con.execute(f"SELECT count(*) FROM {TABLE} WHERE is_outlier").fetchone()[0]
        ),
    }