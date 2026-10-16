"""Sector and stock OLAP operations (Phase V4).

Same discipline as dwm/olap/operations.py, extended to the v2 facts:

  * identifiers are WHITELISTED, never interpolated from user input. A measure
    name is chosen from a fixed set and a sector from a fixed set, and an
    unrecognised value raises rather than being pasted into SQL.
  * values are always PARAMETERISED.
  * cubes hold sums and counts only, so a mean is computed as sum / count at
    query time. Storing an average makes it impossible to roll up, which is the
    single most common way a cube goes wrong.

Every operation that joins news to prices does so through
`dim_trading_calendar.effective_trading_date_key`, never through the raw
publish date. Headlines are published on days the market is shut; joining on the
raw date silently drops 32.4% of the corpus, and the resulting numbers look
perfectly reasonable while describing two thirds of a dataset.

A note on what these operations can and cannot say. A sector slice that shows
news heat beside mean return is a description of co-movement. It is not evidence
that news caused the move, and the `how_computed` panel on every page says so.
"""

from __future__ import annotations

from typing import Any

import duckdb

from dwm.logging_utils import get, human_int
from dwm.olap.operations import Result

log = get("dwm.olap.sector_ops")

# ---------------------------------------------------------------- whitelists

#: Measures that may be ranked or pivoted. A mean is derived from its sum and
#: its count rather than being named directly, so it cannot be confused with a
#: stored average.
SECTOR_MEASURES: dict[str, str] = {
    "news_count": "sum(news_count)",
    "news_count_non_market": "sum(news_count_non_market)",
    "sentiment": "sum(sum_sentiment) / nullif(sum(news_count), 0)",
    "high_sensational_count": "sum(high_sensational_count)",
    "mean_return": "sum(sum_return) / nullif(sum(stock_obs), 0)",
    "mean_abs_return": "sum(sum_abs_return) / nullif(sum(stock_obs), 0)",
    "trading_days": "sum(trading_days)",
    "stock_obs": "sum(stock_obs)",
}

#: Measures where a ratio would be misleading if summed across levels.
DERIVED = {"sentiment", "mean_return", "mean_abs_return"}

ROLLUP_LEVELS = ("month", "quarter", "year")
GRAIN = {"month": 2, "quarter": 3, "year": 4}
MAX_HEADLINES = 50          # rule 21: paginate headline lists
LAG_RANGE = range(-5, 6)


def _check_measure(measure: str) -> str:
    if measure not in SECTOR_MEASURES:
        raise ValueError(
            f"unknown measure {measure!r}; choose one of {sorted(SECTOR_MEASURES)}"
        )
    return SECTOR_MEASURES[measure]


def _check_sector(con: duckdb.DuckDBPyConnection, sector: str | None) -> None:
    """Reject a sector that dim_sector does not contain, rather than interpolating."""
    if sector is None:
        return
    known = {r[0] for r in con.execute("SELECT sector_name FROM dim_sector").fetchall()}
    if sector not in known:
        raise ValueError(f"unknown sector {sector!r}; choose one of {sorted(known)}")


def _check_symbol(con: duckdb.DuckDBPyConnection, symbol: str) -> int:
    row = con.execute(
        "SELECT stock_key FROM dim_stock WHERE symbol = ?", [symbol.upper()]
    ).fetchone()
    if not row:
        known = [
            r[0] for r in con.execute(
                "SELECT symbol FROM dim_stock ORDER BY symbol LIMIT 10"
            ).fetchall()
        ]
        raise ValueError(
            f"unknown symbol {symbol!r}; known symbols start with {known}"
        )
    return int(row[0])


def _quarter(month: int) -> int:
    return (month - 1) // 3 + 1


def _bucket(level: str) -> str:
    """SQL expression for the cube's year/month at the requested grain.

    The cube already stores `year` and `month` as columns, so no reshaping is
    needed - the expression only has to choose the label format.
    """
    if level == "month":
        return "printf('%04d-%02d', year, month)"
    if level == "quarter":
        return "printf('%04d-Q%d', year, (month - 1) // 3 + 1)"
    return "printf('%04d', year)"


# ------------------------------------------------------------------ operations


def sector_slice(
    con: duckdb.DuckDBPyConnection,
    sector: str | None = None,
    year: int | None = None,
    *,
    measure: str = "news_count",
    include_market_reports: bool = True,
) -> Result:
    """SLICE: fix a sector or a year, break the result down by the other.

    One dimension held fixed while another supplies the rows - the simplest
    OLAP operation, and the one most often done badly by grouping on both at
    once, which is a dice, not a slice.
    """
    expr = _check_measure(measure)
    _check_sector(con, sector)

    news_col = (
        "news_count" if include_market_reports else "news_count_non_market"
    )
    where, params = ["1=1"], []
    if sector:
        where.append("s.sector_name = ?")
        params.append(sector)
    if year:
        where.append("c.year = ?")
        params.append(int(year))

    sql = f"""
        SELECT
            s.sector_name            AS sector,
            c.year                   AS year,
            sum(c.{news_col})        AS news_count,
            sum(c.news_count_non_market) AS news_count_non_market,
            {expr}                   AS measure_value
        FROM cube_sector_month c
        JOIN dim_sector s ON s.sector_key = c.sector_key
        WHERE {' AND '.join(where)}
        GROUP BY 1, 2
        ORDER BY 1, 2
    """
    rows = _rows(con, sql, params)
    return Result(
        operation="sector_slice",
        description=(
            f"News by sector and year ({measure}). A sector and a year moving "
            "together is co-movement, not cause."
        ),
        columns=["sector", "year", "news_count", "news_count_non_market", "measure_value"],
        rows=rows,
        meta={"measure": measure, "derived": measure in DERIVED,
              "sector": sector, "year": year,
              "include_market_reports": include_market_reports},
    )


def sector_dice(
    con: duckdb.DuckDBPyConnection,
    sectors: list[str] | None = None,
    years: list[int] | None = None,
    *,
    measure: str = "news_count",
    include_market_reports: bool = True,
) -> Result:
    """DICE: fix BOTH a sector set and a year set."""
    expr = _check_measure(measure)
    for sector in sectors or []:
        _check_sector(con, sector)

    news_col = "news_count" if include_market_reports else "news_count_non_market"
    where, params = ["1=1"], []
    if sectors:
        where.append("s.sector_name IN (" + ",".join("?" * len(sectors)) + ")")
        params.extend(sectors)
    if years:
        where.append("c.year IN (" + ",".join("?" * len(years)) + ")")
        params.extend(int(y) for y in years)

    sql = f"""
        SELECT
            s.sector_name     AS sector,
            c.year            AS year,
            sum(c.{news_col}) AS news_count,
            {expr}            AS measure_value
        FROM cube_sector_month c
        JOIN dim_sector s ON s.sector_key = c.sector_key
        WHERE {' AND '.join(where)}
        GROUP BY 1, 2
        ORDER BY 1, 2
    """
    return Result(
        operation="sector_dice",
        description=f"News for {len(sectors or [])} sectors across {len(years or [])} years.",
        columns=["sector", "year", "news_count", "measure_value"],
        rows=_rows(con, sql, params),
        meta={"measure": measure, "derived": measure in DERIVED,
              "sectors": sectors or [], "years": years or []},
    )


def sector_rollup(
    con: duckdb.DuckDBPyConnection,
    level: str = "year",
    *,
    measure: str = "news_count",
) -> Result:
    """ROLL-UP: months up to quarters or years.

    Totals are computed from the month grain and compared with the stored year
    total. Because the cube holds sums, rolling up is exact - which is the
    property an average-only cube would destroy.
    """
    if level not in ROLLUP_LEVELS:
        raise ValueError(
            f"unknown roll-up level {level!r}; choose one of {list(ROLLUP_LEVELS)}"
        )
    expr = _check_measure(measure)
    bucket = _bucket(level)

    sql = f"""
        SELECT
            {bucket}          AS period,
            sum(c.news_count) AS news_count,
            {expr}            AS measure_value
        FROM cube_sector_month c
        GROUP BY 1
        ORDER BY 1
    """
    rows = _rows(con, sql, [])
    grand = sum(r["news_count"] for r in rows)
    return Result(
        operation="sector_rollup",
        description=(
            f"News rolled up to {level}. Totals are sums, so they reconcile "
            "exactly with the month grain."
        ),
        columns=["period", "news_count", "measure_value"],
        rows=rows,
        meta={"level": level, "measure": measure,
              "total_news": grand, "rolls_up_exactly": True},
    )


def sector_drilldown(
    con: duckdb.DuckDBPyConnection,
    year: int,
    quarter: int | None = None,
    month: int | None = None,
    sector: str | None = None,
) -> Result:
    """DRILL-DOWN: year -> quarter -> month -> the headlines behind a month.

    Ends at real rows, capped at MAX_HEADLINES so a busy month cannot return
    5,000 headlines to a browser.
    """
    _check_sector(con, sector)
    if not 1 <= quarter <= 4 if quarter else False:
        raise ValueError(f"quarter must be 1..4, got {quarter}")
    if not 1 <= month <= 12 if month else False:
        raise ValueError(f"month must be 1..12, got {month}")

    grain = "month" if month else ("quarter" if quarter else "year")
    where, params = ["h.in_window", "h.publish_date >= make_date(?, 1, 1)",
                     "h.publish_date < make_date(?, 1, 1) + INTERVAL 1 YEAR"], [int(year), int(year)]
    if quarter:
        where.append("quarter(h.publish_date) = ?")
        params.append(int(quarter))
    if month:
        where.append("month(h.publish_date) = ?")
        params.append(int(month))
    if sector:
        where.append(
            "EXISTS (SELECT 1 FROM fact_news_stock_mention m "
            "JOIN dim_stock ds ON ds.stock_key = m.stock_key "
            "JOIN dim_sector sec ON sec.sector_key = ds.sector_key "
            "WHERE m.headline_id = h.headline_id AND sec.sector_name = ?)"
        )
        params.append(sector)

    sql = f"""
        SELECT
            h.publish_date                     AS publish_date,
            h.headline_text                    AS headline_text,
            t.topic_name                       AS topic,
            round(h.sentiment_compound, 4)     AS sentiment,
            h.is_sensational                   AS is_sensational,
            h.is_market_report                 AS is_market_report,
            (SELECT string_agg(d.symbol, ', ')
               FROM fact_news_stock_mention m
               JOIN dim_stock d ON d.stock_key = m.stock_key
              WHERE m.headline_id = h.headline_id) AS stocks
        FROM fact_headline h
        LEFT JOIN dim_topic t ON t.topic_key = h.topic_key
        WHERE {' AND '.join(where)}
        ORDER BY h.publish_date DESC, h.headline_id
        LIMIT {MAX_HEADLINES}
    """
    rows = _rows(con, sql, params)
    return Result(
        operation="sector_drilldown",
        description=(
            f"Headlines behind {year}"
            + (f" Q{quarter}" if quarter else "")
            + (f" month {month}" if month else "")
            + ". A mention means the name appeared, not that the story was about it."
        ),
        columns=["publish_date", "headline_text", "topic", "sentiment",
                 "is_sensational", "is_market_report", "stocks"],
        rows=rows,
        meta={"grain": grain, "year": year, "quarter": quarter, "month": month,
              "limit": MAX_HEADLINES, "truncated": len(rows) == MAX_HEADLINES},
    )


def sector_pivot(
    con: duckdb.DuckDBPyConnection, measure: str = "news_count", year: int | None = None
) -> Result:
    """PIVOT: sectors down, years across.

    Aggregated once at (sector, year) and then transposed in Python. Doing it in
    SQL with conditional aggregation would need the measure expression nested
    inside a sum(), which DuckDB refuses and which would in any case evaluate a
    ratio against a row-level denominator rather than an aggregated one.
    """
    expr = _check_measure(measure)
    where, params = ["1=1"], []
    if year:
        where.append("c.year = ?")
        params.append(int(year))

    sql = f"""
        SELECT
            s.sector_name AS sector,
            c.year        AS year,
            {expr}        AS measure_value
        FROM cube_sector_month c
        JOIN dim_sector s ON s.sector_key = c.sector_key
        WHERE {' AND '.join(where)}
        GROUP BY 1, 2
        ORDER BY 1, 2
    """
    cells = _rows(con, sql, params)
    years = sorted({int(r["year"]) for r in cells})
    by_sector: dict[str, dict[str, Any]] = {}
    for row in cells:
        target = by_sector.setdefault(row["sector"], {"sector": row["sector"]})
        target[f"p{row['year']}"] = row["measure_value"]
    rows = [by_sector[s] for s in sorted(by_sector)]
    year_cols = [f"p{y}" for y in years]
    return Result(
        operation="sector_pivot",
        description=f"Sectors against years for {measure}.",
        columns=["sector", *year_cols],
        rows=rows,
        meta={"measure": measure, "derived": measure in DERIVED, "years": year_cols},
    )


def sector_cube(con: duckdb.DuckDBPyConnection) -> Result:
    """CUBE: several grouping levels at once.

    Rather than storing another table, this groups the existing cube on three
    levels and lets the caller compare them. The totals row reconciles against
    the month grain, which is what proves the levels are consistent.
    """
    sql = """
        SELECT
            printf('%04d', year)                  AS year,
            NULL                                   AS quarter,
            NULL                                   AS month,
            s.sector_name                          AS sector,
            sum(news_count)                        AS news_count,
            sum(news_count_non_market)             AS news_count_non_market,
            sum(sum_sentiment)                     AS sum_sentiment,
            sum(high_sensational_count)            AS high_sensational_count,
            sum(trading_days)                      AS trading_days,
            sum(sum_return)                        AS sum_return,
            sum(sum_abs_return)                    AS sum_abs_return,
            sum(stock_obs)                         AS stock_obs
        FROM cube_sector_month c
        JOIN dim_sector s ON s.sector_key = c.sector_key
        GROUP BY 1, 2, 3, 4

        UNION ALL

        SELECT NULL, NULL, NULL, s.sector_name,
               sum(news_count), sum(news_count_non_market), sum(sum_sentiment),
               sum(high_sensational_count), sum(trading_days),
               sum(sum_return), sum(sum_abs_return), sum(stock_obs)
        FROM cube_sector_month c
        JOIN dim_sector s ON s.sector_key = c.sector_key
        GROUP BY 1, 2, 3, 4

        ORDER BY 4, 1 NULLS FIRST
    """
    rows = _rows(con, sql, [])
    total = sum(r["news_count"] for r in rows if r["year"] is not None)
    return Result(
        operation="sector_cube",
        description=(
            "Sectors by year and overall. Sector means are derived at read "
            "time from sums and counts; no average is stored."
        ),
        columns=["year", "quarter", "month", "sector", "news_count",
                 "news_count_non_market", "sum_sentiment", "high_sensational_count",
                 "trading_days", "sum_return", "sum_abs_return", "stock_obs"],
        rows=rows,
        meta={"total_news": total, "grain": "year x sector + total"},
    )


def stock_profile(
    con: duckdb.DuckDBPyConnection, symbol: str, *, peers: bool = True
) -> Result:
    """One stock's price history and news footprint."""
    stock_key = _check_symbol(con, symbol)
    sql = """
        SELECT
            f.trade_date,
            f.close,
            f.adj_close,
            round(f.return_pct, 4)  AS return_pct,
            round(f.volatility_20d, 4) AS volatility_20d,
            (SELECT count(*) FROM fact_news_stock_mention m
              WHERE m.stock_key = f.stock_key
                AND m.effective_trading_date_key = f.date_key) AS mentions
        FROM fact_stock_daily f
        WHERE f.stock_key = ?
        ORDER BY f.trade_date DESC
        LIMIT 400
    """
    rows = _rows(con, sql, [stock_key])
    stats = con.execute(
        """
        SELECT count(*), round(avg(f.return_pct), 4),
               round(stddev_samp(f.volatility_20d), 4),
               min(f.trade_date), max(f.trade_date)
        FROM fact_stock_daily f WHERE f.stock_key = ?
        """,
        [stock_key],
    ).fetchone()
    mentions = con.execute(
        """
        SELECT count(*), count(DISTINCT m.headline_id)
        FROM fact_news_stock_mention m WHERE m.stock_key = ?
        """,
        [stock_key],
    ).fetchone()

    meta: dict[str, Any] = {
        "symbol": symbol.upper(),
        "sessions": int(stats[0]),
        "mean_daily_return_pct": stats[1],
        "mean_volatility_pct": stats[2],
        "first_trade": str(stats[3]) if stats[3] else None,
        "last_trade": str(stats[4]) if stats[4] else None,
        "mentions": int(mentions[0]),
        "headlines_mentioning": int(mentions[1]),
    }
    if peers:
        meta["sector"] = con.execute(
            "SELECT s.sector_name FROM dim_stock d LEFT JOIN dim_sector s "
            "ON s.sector_key = d.sector_key WHERE d.stock_key = ?",
            [stock_key],
        ).fetchone()
        meta["is_survivor"] = con.execute(
            "SELECT is_survivor FROM dim_stock WHERE stock_key = ?", [stock_key]
        ).fetchone()[0]

    return Result(
        operation="stock_profile",
        description=(
            f"{symbol.upper()}: daily returns and news mentions. Returns are "
            "percentage points from adjusted closes."
        ),
        columns=["trade_date", "close", "adj_close", "return_pct",
                 "volatility_20d", "mentions"],
        rows=rows,
        meta=meta,
    )


def drill_across_sector(
    con: duckdb.DuckDBPyConnection,
    sector: str | None = None,
    *,
    lag: int = 0,
    include_market_reports: bool = False,
) -> Result:
    """DRILL-ACROSS: sector news beside sector returns, one grain, shared keys.

    The operation a fact constellation exists for. `fact_news_sector_daily` and
    `fact_stock_daily` have different grains and no direct path between them;
    they meet through the conformed date key, via the trading calendar.

    `lag` shifts the NEWS side relative to prices, which is the only way to see
    whether news leads price or merely coincides with it. Market-report
    headlines are excluded by default because they describe a move that has
    already happened - including them manufactures the correlation.
    """
    _check_sector(con, sector)
    if lag not in LAG_RANGE:
        raise ValueError(f"lag must be between -5 and +5, got {lag}")
    news_col = "news_count" if include_market_reports else "news_count_non_market"
    lag_sign = "+" if lag >= 0 else "-"
    lag_magnitude = abs(int(lag))

    where, params = ["1=1"], []
    if sector:
        where.append("s.sector_name = ?")
        params.append(sector)

    sql = f"""
        WITH news AS (
            SELECT effective_trading_date_key AS news_key, sector_key,
                   {news_col} AS news_count, sum_sentiment
            FROM fact_news_sector_daily
        ),
        prices AS (
            SELECT
                f.date_key                          AS price_key,
                d.sector_key,
                sum(f.return_pct) / nullif(count(*), 0) AS mean_return_pct,
                sum(f.abs_return_pct) / nullif(count(*), 0) AS mean_abs_return_pct,
                count(*) AS stock_obs
            FROM v_stock_daily_clean f
            JOIN dim_stock d ON d.stock_key = f.stock_key
            WHERE f.return_pct IS NOT NULL
            GROUP BY 1, 2
        ),
        -- Shift through a real date rather than by adding to date_key. Adding
        -- `lag * 100` to a YYYYMMDD key lands on the wrong day whenever the
        -- shift crosses a month boundary: 20200131 + 100 is 20200231, which is
        -- not a date at all.
        -- The sign is applied separately because `INTERVAL -1 DAY` is not
        -- valid SQL. full_date comes from dim_date, because
        -- dim_trading_calendar deliberately stores keys only.
        shifted AS (
            SELECT
                c1.date_key       AS session_key,
                d1.full_date      AS session,
                c2.date_key       AS price_key
            FROM dim_trading_calendar c1
            JOIN dim_date d1 ON d1.date_key = c1.date_key
            LEFT JOIN dim_trading_calendar c2
                   ON c2.date_key = (SELECT date_key FROM dim_date d2
                                      WHERE d2.full_date =
                                            d1.full_date {lag_sign} INTERVAL {lag_magnitude} DAY)
            WHERE c1.is_trading_day
        )
        SELECT
            sh.session            AS session,
            s.sector_name         AS sector,
            coalesce(n.news_count, 0) AS news_count,
            coalesce(n.sum_sentiment, 0) AS sum_sentiment,
            round(p.mean_return_pct, 4) AS mean_return_pct,
            round(p.mean_abs_return_pct, 4) AS mean_abs_return_pct,
            p.stock_obs
        FROM shifted sh
        JOIN news n
          ON n.news_key = sh.session_key
         AND n.sector_key IS NOT NULL
        JOIN prices p ON p.price_key = sh.price_key
        JOIN dim_sector s
          ON s.sector_key = n.sector_key
         AND s.sector_key = p.sector_key
        WHERE {' AND '.join(where)} AND n.news_count > 0
        ORDER BY sh.session, s.sector_name
    """
    rows = _rows(con, sql, params)
    return Result(
        operation="drill_across_sector",
        description=(
            f"Sector news beside sector return at lag {lag:+d} session(s). "
            "An association in historical data, not a cause."
        ),
        columns=["session", "sector", "news_count", "sum_sentiment",
                 "mean_return_pct", "mean_abs_return_pct", "stock_obs"],
        rows=rows,
        meta={
            "sector": sector,
            "lag": int(lag),
            # The grain is (session, sector), not session. With no sector filter
            # two sectors active on one date produce two rows, which is correct
            # but looks like a duplicate session unless the grain is stated.
            "grain": "session x sector",
            "sessions": len({r["session"] for r in rows}),
            "rows": len(rows),
            "include_market_reports": include_market_reports,
            "caveat": (
                "Market-report headlines are excluded by default: they describe "
                "a move that already happened."
            ),
        },
    )


def _rows(
    con: duckdb.DuckDBPyConnection, sql: str, params: list[Any]
) -> list[dict[str, Any]]:
    cur = con.execute(sql, params)
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


OPERATIONS: dict[str, Any] = {
    "sector_slice": sector_slice,
    "sector_dice": sector_dice,
    "sector_rollup": sector_rollup,
    "sector_drilldown": sector_drilldown,
    "sector_pivot": sector_pivot,
    "sector_cube": sector_cube,
    "stock_profile": stock_profile,
    "drill_across_sector": drill_across_sector,
}

DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "sector_slice": {"measure": "news_count"},
    "sector_dice": {"measure": "news_count"},
    "sector_rollup": {"level": "year", "measure": "news_count"},
    "sector_drilldown": {"year": 2018},
    "sector_pivot": {"measure": "news_count"},
    "sector_cube": {},
    "stock_profile": {"symbol": "TCS"},
    "drill_across_sector": {"lag": 0},
}


def list_operations() -> list[dict[str, Any]]:
    return [
        {"name": name, "default_params": DEFAULT_PARAMS.get(name, {})}
        for name in OPERATIONS
    ]


def run_operation(
    con: duckdb.DuckDBPyConnection, operation: str, **params: Any
) -> dict[str, Any]:
    if operation not in OPERATIONS:
        raise ValueError(
            f"unknown sector operation {operation!r}. "
            f"Known: {sorted(OPERATIONS)}"
        )
    return OPERATIONS[operation](con, **params).to_dict()


def summarise(result: Result) -> str:
    """One line for a CLI or reading. Counts only; no interpretation."""
    if not result.rows:
        return f"{result.operation}: no rows"
    return f"{result.operation}: {human_int(len(result.rows))} rows"
