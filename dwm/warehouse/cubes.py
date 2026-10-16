"""Pre-aggregated cubes.

The blueprint makes one rule about these explicit, and the viva sheet repeats
it: **store sums, never averages.** Sums roll up exactly; averages do not. So
every cube holds a count and a set of column sums, and any mean is recomputed
as `sum / count` at query time.

Cubes exist for one reason: the OLAP operations in Phase 4 should not have to
rescan 3.15M fact rows to answer a roll-up. They are derived and can be
dropped and rebuilt at any time.

    cube_month_topic        year-month x topic, headlines in window
    cube_year_topic         year x topic
    cube_topic_sentiment    topic totals, the denominator for every rate
    cube_day_topic          day x topic, for drill-across onto market days
"""

from __future__ import annotations

import duckdb

from dwm.logging_utils import get, human_int

log = get("dwm.warehouse.cubes")

# Measures summed in every cube. Names are explicit about being sums.
SUM_MEASURES = (
    "sentiment_compound",
    "sentiment_positive",
    "sentiment_negative",
    "sentiment_neutral",
    "caps_token_ratio",
    "superlative_count",
    "urgency_count",
    "exclamation_count",
    "question_count",
    "word_count",
    "sensational_score",
)

# Boolean measures are summed as 0/1 counts, which is how a rate becomes
# sum/count rather than a stored percentage.
FLAG_MEASURES = (("is_sensational", "sensational_count"),
                 ("is_risk_signal", "risk_signal_count"))

DDL = {
    # One row per (year_month, topic) inside the analysis window.
    "cube_month_topic": """
        CREATE OR REPLACE TABLE cube_month_topic AS
        SELECT
            d.year_month_key        AS year_month_key,
            d.year_month            AS year_month,
            d.year_no               AS year_no,
            d.month_no              AS month_no,
            f.topic_key             AS topic_key,
            count(*)                AS headline_count,
            count(*) FILTER (WHERE f.is_multi_category)      AS multi_category_count,
            count(*) FILTER (WHERE f.sentiment_band = 'negative')  AS negative_count,
            count(*) FILTER (WHERE f.sentiment_band = 'positive')  AS positive_count,
            count(*) FILTER (WHERE f.is_multi_category IS FALSE)    AS single_category_count,
            {SUMS}
        FROM fact_headline f
        JOIN dim_date d ON d.date_key = f.date_key
        WHERE f.in_window
        GROUP BY 1, 2, 3, 4, 5
    """,
    # One row per (year, topic) inside the window.
    "cube_year_topic": """
        CREATE OR REPLACE TABLE cube_year_topic AS
        SELECT
            d.year_no               AS year_no,
            f.topic_key             AS topic_key,
            count(*)                AS headline_count,
            count(*) FILTER (WHERE f.sentiment_band = 'negative')  AS negative_count,
            count(*) FILTER (WHERE f.is_multi_category)             AS multi_category_count,
            {SUMS}
        FROM fact_headline f
        JOIN dim_date d ON d.date_key = f.date_key
        WHERE f.in_window
        GROUP BY 1, 2
    """,
    # Topic totals across the whole window: the denominator for every rate.
    "cube_topic_sentiment": """
        CREATE OR REPLACE TABLE cube_topic_sentiment AS
        SELECT
            f.topic_key             AS topic_key,
            count(*)                AS headline_count,
            count(*) FILTER (WHERE f.is_sensational) AS sensational_count,
            count(*) FILTER (WHERE f.is_risk_signal) AS risk_signal_count,
            count(*) FILTER (WHERE f.sentiment_band = 'negative') AS negative_count,
            count(*) FILTER (WHERE f.sentiment_band = 'positive') AS positive_count,
            count(*) FILTER (WHERE f.sentiment_band = 'neutral')  AS neutral_count,
            {SUMS}
        FROM fact_headline f
        WHERE f.in_window
        GROUP BY 1
    """,
    # Day x topic. This is the bridge to market data: it lets a drill-across
    # line up daily headline counts and sentiment sums with daily Nifty
    # returns without scanning the fact table.
    "cube_day_topic": """
        CREATE OR REPLACE TABLE cube_day_topic AS
        SELECT
            f.date_key              AS date_key,
            f.topic_key             AS topic_key,
            count(*)                AS headline_count,
            {SUMS}
        FROM fact_headline f
        WHERE f.in_window
        GROUP BY 1, 2
    """,
"cube_sector_month": """
        CREATE OR REPLACE TABLE cube_sector_month AS
        WITH news AS (
            -- Sector news for one calendar month, keyed by the TRADING session
            -- the headlines belong to, not by their raw publish date. Joining
            -- on the raw date would drop the 32.4% of headlines published on a
            -- day the market was shut, and this cube would quietly lose a third
            -- of its news.
            --
            -- date_key is YYYYMMDD, so year and month are integer division.
            -- `//` on purpose: plain `/` yields a DOUBLE and the GROUP BY keys
            -- would come back as 2.019e3 instead of 2019.
            SELECT
                s.effective_trading_date_key // 10000          AS year,
                (s.effective_trading_date_key // 100) % 100    AS month,
                s.sector_key,
                sum(s.news_count)             AS news_count,
                sum(s.news_count_non_market)  AS news_count_non_market,
                sum(s.sum_sentiment)          AS sum_sentiment,
                sum(s.high_sensational_count) AS high_sensational_count
            FROM fact_news_sector_daily s
            WHERE s.effective_trading_date_key IS NOT NULL
            GROUP BY 1, 2, 3
        ),
        prices AS (
            -- Sums and counts only. A sector's MEAN return is
            -- sum_return / stock_obs at query time; storing an average here
            -- would make it un-aggregatable and break roll-up, which is the
            -- whole reason cubes hold sums.
            SELECT
                f.date_key // 10000                       AS year,
                (f.date_key // 100) % 100                AS month,
                d.sector_key,
                sum(f.return_pct)           AS sum_return,
                sum(f.abs_return_pct)       AS sum_abs_return,
                count(*)                    AS stock_obs,
                count(DISTINCT f.trade_date) AS trading_days
            FROM v_stock_daily_clean f
            JOIN dim_stock d ON d.stock_key = f.stock_key
            WHERE f.return_pct IS NOT NULL
            GROUP BY 1, 2, 3
        )
        SELECT
            coalesce(news.year, prices.year)             AS year,
            coalesce(news.month, prices.month)           AS month,
            coalesce(news.sector_key, prices.sector_key) AS sector_key,
            coalesce(news.news_count, 0)                 AS news_count,
            coalesce(news.news_count_non_market, 0)      AS news_count_non_market,
            news.sum_sentiment                           AS sum_sentiment,
            coalesce(news.high_sensational_count, 0)     AS high_sensational_count,
            coalesce(prices.trading_days, 0)             AS trading_days,
            prices.sum_return                             AS sum_return,
            prices.sum_abs_return                         AS sum_abs_return,
            coalesce(prices.stock_obs, 0)                AS stock_obs
        FROM news
        FULL OUTER JOIN prices
            ON  news.year = prices.year
            AND news.month = prices.month
            AND news.sector_key = prices.sector_key
        ORDER BY year, month, sector_key
    """,
}


# v2 cubes need tables that a plain `dwm build` has not created yet:
# fact_news_sector_daily is written by `dwm link`, and v_stock_daily_clean by the
# stock-fact step. Listing the requirement lets build_cubes skip the cube with a
# warning instead of failing the whole build, and lets `dwm link` pick it up
# afterwards once both sources exist.
REQUIRED_TABLES: dict[str, tuple[str, ...]] = {
    "cube_sector_month": ("fact_news_sector_daily", "v_stock_daily_clean", "dim_stock"),
}


def _sum_columns() -> str:
    parts = [f"sum({m}) AS sum_{m}" for m in SUM_MEASURES]
    parts += [
        f"sum(CASE WHEN {column} THEN 1 ELSE 0 END) AS {alias}"
        for column, alias in FLAG_MEASURES
    ]
    return ",\n            ".join(parts)


def build_cubes(
    con: duckdb.DuckDBPyConnection, *, only: list[str] | None = None
) -> dict[str, int]:
    """Build cubes. Returns row counts.

    `only` rebuilds a named subset, used by `dwm link` once it has created the
    sector facts that cube_sector_month depends on.
    """
    present = {
        r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
    built: dict[str, int] = {}
    skipped: dict[str, list[str]] = {}

    for name, template in DDL.items():
        if only is not None and name not in only:
            continue
        missing = [t for t in REQUIRED_TABLES.get(name, ()) if t not in present]
        if missing:
            skipped[name] = missing
            log.info(
                "%s skipped: %s not built yet (run `dwm link` / `dwm build` first)",
                name, missing,
            )
            continue
        con.execute(template.format(SUMS=_sum_columns()))
        n = int(con.execute(f"SELECT count(*) FROM {name}").fetchone()[0])
        built[name] = n
        log.info("%s: %s rows", name, human_int(n))

    if skipped:
        built["_skipped"] = skipped          # type: ignore[assignment]
    return built
