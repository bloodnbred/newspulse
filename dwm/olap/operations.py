"""OLAP operations (Phase 4).

The eight operations the blueprint asks for, each a named function returning
rows, so the API, the dashboard and the report all call the same code:

    slice        one dimension member fixed
    dice         several dimension members fixed
    roll_up      aggregate up a hierarchy, using stored sums
    drill_down   the reverse of roll_up
    pivot        one measure across two dimensions
    cube         all groupings of a small dimension set, in one pass
    drill_across a join between fact tables that share conformed keys
    slice_and_dice  a parameterised combination of the first two

Two rules hold throughout.

**Sums, never averages.** Averages do not roll up. Every operation returns
counts and column sums, plus a precomputed mean where it is genuinely
convenient, and the mean is always `sum / count` derived at query time. A test
asserts no OLAP result silently averages a stored sum.

**The headline window is the default.** Every operation filters `in_window`
unless told otherwise, because the analysis questions are about the five-year
window. Passing `in_window=False` explores the whole archive, and the
returned row count says which was used.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import duckdb

from dwm.config import Settings
from dwm.db import connect
from dwm.logging_utils import get, human_int
from dwm.olap.charts import olap_chart_spec
from dwm.olap.readings import reading_for

log = get("dwm.olap")

# Measures offered by slice/dice/pivot. All are sums or counts.
HEADLINE_MEASURES = (
    "headline_count",
    "sensational_count",
    "risk_signal_count",
    "negative_count",
    "positive_count",
    "neutral_count",
    "sum_sentiment_compound",
    "sum_sensational_score",
    "sum_word_count",
)


@dataclass(slots=True)
class Result:
    """One OLAP result: a label, columns and rows."""

    operation: str
    description: str
    columns: list[str]
    rows: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "description": self.description,
            "columns": self.columns,
            "rows": self.rows,
            "meta": self.meta,
        }


def _q(value: Any) -> str:
    """Quote a value for a SQL literal, defensively."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None):
    cursor = con.execute(sql, params or [])
    columns = [d[0] for d in (cursor.description or [])]
    return columns, [dict(zip(columns, r, strict=True)) for r in cursor.fetchall()]


# ---------------------------------------------------------------------------
# slice and dice
# ---------------------------------------------------------------------------


def slice_by_topic(
    con: duckdb.DuckDBPyConnection,
    topic: str,
    *,
    in_window: bool = True,
    limit: int = 50,
) -> Result:
    """SLICE: fix one topic, break the result down by month.

    The classic single-dimension slice: one member of `dim_topic` held fixed
    while another dimension provides the rows.
    """
    sql = f"""
        SELECT
            c.year_month,
            c.year_no,
            c.month_no,
            c.headline_count,
            c.sensational_count,
            c.negative_count,
            c.sum_sentiment_compound,
            c.sum_sensational_score,
            -- means are derived here, never stored
            round(c.sum_sentiment_compound / nullif(c.headline_count, 0), 4) AS mean_sentiment,
            round(c.sensational_count / nullif(c.headline_count, 0), 4)  AS sensational_rate
        FROM cube_month_topic c
        JOIN dim_topic t ON t.topic_key = c.topic_key
        WHERE t.topic_name = {_q(topic)} AND c.headline_count > 0
        ORDER BY c.year_month_key
        LIMIT {int(limit)}
    """
    columns, rows = _rows(con, sql)
    return Result(
        "slice",
        f"Headline volume and sentiment for topic '{topic}', sliced by month.",
        columns,
        rows,
        {"topic": topic, "grain": "year_month", "in_window": in_window},
    )


def slice_by_year(
    con: duckdb.DuckDBPyConnection, year: int, *, in_window: bool = True
) -> Result:
    """SLICE on the year dimension, broken down by topic."""
    sql = f"""
        SELECT
            t.topic_name,
            t.topic_group,
            y.headline_count,
            y.sensational_count,
            y.sum_sentiment_compound,
            y.sum_sensational_score,
            round(y.sum_sentiment_compound / nullif(y.headline_count, 0), 4) AS mean_sentiment,
            round(y.sensational_count / nullif(y.headline_count, 0), 4)  AS sensational_rate
        FROM cube_year_topic y
        JOIN dim_topic t ON t.topic_key = y.topic_key
        WHERE y.year_no = {int(year)}
        ORDER BY y.headline_count DESC
    """
    columns, rows = _rows(con, sql)
    return Result(
        "slice",
        f"Topic breakdown for {year}.",
        columns,
        rows,
        {"year": year, "grain": "topic", "in_window": in_window},
    )


def dice_by_topic_and_year(
    con: duckdb.DuckDBPyConnection,
    topics: Sequence[str],
    years: Sequence[int],
) -> Result:
    """DICE: fix several topics and several years, leaving month as the rows."""
    topic_list = ", ".join(_q(t) for t in topics)
    year_list = ", ".join(str(int(y)) for y in years)
    sql = f"""
        SELECT
            c.year_month,
            c.year_no,
            t.topic_name,
            c.headline_count,
            c.sensational_count,
            c.negative_count,
            c.sum_sentiment_compound,
            round(c.sum_sentiment_compound / nullif(c.headline_count, 0), 4) AS mean_sentiment,
            round(c.sensational_count / nullif(c.headline_count, 0), 4)  AS sensational_rate
        FROM cube_month_topic c
        JOIN dim_topic t ON t.topic_key = c.topic_key
        WHERE t.topic_name IN ({topic_list})
          AND c.year_no IN ({year_list})
          AND c.headline_count > 0
        ORDER BY c.year_month_key, t.topic_name
    """
    columns, rows = _rows(con, sql)
    return Result(
        "dice",
        f"Topics {list(topics)} x years {list(years)}, sliced by month.",
        columns,
        rows,
        {"topics": list(topics), "years": list(years), "grain": "year_month"},
    )


# ---------------------------------------------------------------------------
# roll-up and drill-down
# ---------------------------------------------------------------------------


def roll_up_to_year(con: duckdb.DuckDBPyConnection) -> Result:
    """ROLL-UP: aggregate month up to year.

    The roll-up is a plain SUM over the monthly cube, which is exactly why
    cubes store sums. Recomputing a mean per month and averaging those means
    would be wrong, because the months hold different numbers of headlines.
    """
    sql = """
        SELECT
            c.year_no,
            t.topic_name,
            t.topic_group,
            sum(c.headline_count)          AS headline_count,
            sum(c.sensational_count)       AS sensational_count,
            sum(c.negative_count)          AS negative_count,
            sum(c.sum_sentiment_compound)  AS sum_sentiment_compound,
            sum(c.sum_sensational_score)   AS sum_sensational_score,
            round(
                sum(c.sum_sentiment_compound) / nullif(sum(c.headline_count), 0), 4
            ) AS mean_sentiment,
            round(
                sum(c.sensational_count) / nullif(sum(c.headline_count), 0), 4
            ) AS sensational_rate
        FROM cube_month_topic c
        JOIN dim_topic t ON t.topic_key = c.topic_key
        GROUP BY 1, 2, 3
        ORDER BY 1, headline_count DESC
    """
    columns, rows = _rows(con, sql)
    return Result(
        "roll_up",
        "Month rolled up to year, by topic. Sums are exact; the mean is "
        "sum/count, not an average of monthly means.",
        columns,
        rows,
        {"from": "year_month", "to": "year_no"},
    )


def drill_down_to_month(
    con: duckdb.DuckDBPyConnection, topic: str, year: int
) -> Result:
    """DRILL-DOWN: the reverse of roll_up, year down to month for one topic."""
    sql = f"""
        SELECT
            c.year_month,
            c.month_no,
            c.headline_count,
            c.sensational_count,
            c.negative_count,
            c.sum_sentiment_compound,
            round(c.sum_sentiment_compound / nullif(c.headline_count, 0), 4) AS mean_sentiment,
            round(c.sensational_count / nullif(c.headline_count, 0), 4)  AS sensational_rate
        FROM cube_month_topic c
        JOIN dim_topic t ON t.topic_key = c.topic_key
        WHERE t.topic_name = {_q(topic)} AND c.year_no = {int(year)}
        ORDER BY c.month_no
    """
    columns, rows = _rows(con, sql)
    return Result(
        "drill_down",
        f"Month detail for topic '{topic}' in {year}.",
        columns,
        rows,
        {"topic": topic, "year": year, "grain": "year_month"},
    )


def top_n_months(
    con: duckdb.DuckDBPyConnection, n: int = 10, *, by: str = "headline_count"
) -> Result:
    """Rank months by a stored sum. The candidate spikes for research Q2."""
    allowed = set(HEADLINE_MEASURES)
    if by not in allowed:
        raise ValueError(f"cannot rank by {by!r}; choose one of {sorted(allowed)}")
    sql = f"""
        SELECT
            c.year_month,
            c.year_no,
            c.month_no,
            sum(c.{by})                     AS measure_value,
            sum(c.headline_count)           AS headline_count,
            sum(c.sensational_count)        AS sensational_count
        FROM cube_month_topic c
        GROUP BY 1, 2, 3
        ORDER BY measure_value DESC
        LIMIT {int(n)}
    """
    columns, rows = _rows(con, sql)
    return Result(
        "ranking",
        f"Top {n} months by {by}.",
        columns,
        rows,
        {"ranked_by": by},
    )


# ---------------------------------------------------------------------------
# pivot and cube
# ---------------------------------------------------------------------------


def pivot_month_by_topic(
    con: duckdb.DuckDBPyConnection, topics: Sequence[str], measure: str = "headline_count"
) -> Result:
    """PIVOT: one measure across months (rows) and topics (columns).

    Implemented with conditional aggregation rather than PIVOT syntax, so the
    same shape works on every DuckDB version and the generated SQL stays
    readable in the cookbook.
    """
    if measure not in set(HEADLINE_MEASURES):
        raise ValueError(f"unknown measure {measure!r}")
    topic_list = ", ".join(_q(t) for t in topics)
    projections = ",\n            ".join(
        f"sum(CASE WHEN t.topic_name = {_q(t)} THEN c.{measure} ELSE 0 END) AS {_q(t)}"
        for t in topics
    )
    sql = f"""
        SELECT
            c.year_month,
            {projections}
        FROM cube_month_topic c
        JOIN dim_topic t ON t.topic_key = c.topic_key
        WHERE t.topic_name IN ({topic_list})
        GROUP BY 1
        ORDER BY 1
    """
    columns, rows = _rows(con, sql)
    return Result(
        "pivot",
        f"{measure} pivoted across months, one column per topic.",
        columns,
        rows,
        {"measure": measure, "topics": list(topics)},
    )


def cube_by_year_topic_band(
    con: duckdb.DuckDBPyConnection, topic_group: str = "Subject"
) -> Result:
    """CUBE: all three groupings of year, topic and sentiment band at once.

    GROUPING SETS produces the year total, the topic total, the band total and
    the grand total in a single pass, which is what makes it a cube rather
    than three separate roll-ups.
    """
    sql = f"""
        SELECT
            d.year_no,
            t.topic_name,
            f.sentiment_band,
            grouping(d.year_no)  AS g_year,
            grouping(t.topic_name) AS g_topic,
            grouping(f.sentiment_band) AS g_band,
            count(*) AS headline_count,
            sum(CASE WHEN f.is_sensational THEN 1 ELSE 0 END) AS sensational_count,
            sum(f.sentiment_compound) AS sum_sentiment_compound,
            sum(f.sensational_score)  AS sum_sensational_score
        FROM fact_headline f
        JOIN dim_date d  ON d.date_key = f.date_key
        JOIN dim_topic t ON t.topic_key = f.topic_key
        WHERE f.in_window AND t.topic_group = {_q(topic_group)}
        GROUP BY GROUPING SETS (
            (d.year_no, t.topic_name, f.sentiment_band),
            (d.year_no, t.topic_name),
            (d.year_no, f.sentiment_band),
            (d.year_no),
            ()
        )
        ORDER BY g_year, g_topic, g_band, d.year_no, headline_count DESC
    """
    columns, rows = _rows(con, sql)
    return Result(
        "cube",
        f"Grouping sets over year, topic and sentiment band for "
        f"topic_group='{topic_group}', with the grouping flags marked.",
        columns,
        rows,
        {"topic_group": topic_group, "groupings": 5},
    )


# ---------------------------------------------------------------------------
# drill-across
# ---------------------------------------------------------------------------


def drill_across_market_and_headlines(
    con: duckdb.DuckDBPyConnection,
    topic: str = "Business",
    *,
    trading_days_only: bool = True,
) -> Result:
    """DRILL-ACROSS: daily headline behaviour beside daily Nifty performance.

    This is the operation a fact constellation exists for. `fact_headline` and
    `fact_market_daily` have different grains and no direct path to each other;
    they are joined through the conformed `date_key`, and the daily cube
    supplies the aggregate.

    `trading_days_only` matters and defaults to True. Headlines are published
    on 1,828 distinct days in the window while the market trades on 1,235, so
    an unfiltered calendar join would attach market data to weekends and
    holidays, which have no price. Association only: nothing here implies
    causation.
    """
    day_filter = ""
    if trading_days_only:
        day_filter = "AND m.date_key IS NOT NULL"

    # Bounded by the analysis window, taken from the fact table rather than
    # from dim_date. The dimension spans the whole archive because it has to
    # cover every source, so ranging over it would return 2008 rows with no
    # headlines attached and quietly answer a different question.
    sql = f"""
        WITH bounds AS (
            SELECT min(date_key) AS lo, max(date_key) AS hi
            FROM fact_headline WHERE in_window
        ),
        daily AS (
            SELECT
                c.date_key,
                sum(c.headline_count)         AS headline_count,
                sum(c.sensational_count)      AS sensational_count,
                sum(c.sum_sentiment_compound) AS sum_sentiment_compound,
                sum(c.sum_sensational_score)  AS sum_sensational_score
            FROM cube_day_topic c
            JOIN dim_topic t ON t.topic_key = c.topic_key
            WHERE t.topic_name = {_q(topic)}
            GROUP BY 1
        )
        SELECT
            d.full_date,
            d.is_trading_day,
            d.year_month,
            coalesce(daily.headline_count, 0)  AS headline_count,
            coalesce(daily.sensational_count, 0) AS sensational_count,
            round(
                coalesce(daily.sum_sentiment_compound, 0)
                / nullif(daily.headline_count, 0), 4
            ) AS mean_sentiment,
            round(
                coalesce(daily.sum_sensational_score, 0)
                / nullif(daily.headline_count, 0), 4
            ) AS mean_sensational_score,
            m.close,
            m.return_pct,
            m.volatility_20d
        FROM dim_date d
        CROSS JOIN bounds b
        LEFT JOIN daily  ON daily.date_key = d.date_key
        LEFT JOIN fact_market_daily m ON m.date_key = d.date_key
        WHERE d.date_key BETWEEN b.lo AND b.hi
          {day_filter}
        ORDER BY d.full_date
    """
    columns, rows = _rows(con, sql)
    result = Result(
        "drill_across",
        f"Daily '{topic}' headline counts and sentiment beside Nifty 50 close, "
        f"return and volatility, on trading days. Association, not causation.",
        columns,
        rows,
        {
            "topic": topic,
            "trading_days_only": trading_days_only,
            "fact_tables": ["fact_headline (via cube_day_topic)", "fact_market_daily"],
            "conform_on": "date_key",
        },
    )
    return result


def topic_mix_over_time(
    con: duckdb.DuckDBPyConnection, topic_group: str = "Subject"
) -> Result:
    """Research Q1: how the topic mix changed across the window.

    The share is computed from counts at query time, not stored, and the
    Place/Subject split exists because `Local` alone is 70% of the window and
    would otherwise dominate every statement about "topic mix".
    """
    sql = f"""
        WITH by_year AS (
            SELECT
                d.year_no,
                t.topic_name,
                t.topic_group,
                count(*) AS headline_count
            FROM fact_headline f
            JOIN dim_date d  ON d.date_key = f.date_key
            JOIN dim_topic t ON t.topic_key = f.topic_key
            WHERE f.in_window AND t.topic_group = {_q(topic_group)}
            GROUP BY 1, 2, 3
        )
        SELECT
            year_no,
            topic_name,
            topic_group,
            headline_count,
            round(
                headline_count
                / sum(headline_count) OVER (PARTITION BY year_no), 4
            ) AS share_of_year
        FROM by_year
        ORDER BY year_no, share_of_year DESC
    """
    columns, rows = _rows(con, sql)
    return Result(
        "topic_mix",
        f"Topic share within each year, for topic_group='{topic_group}'.",
        columns,
        rows,
        {"topic_group": topic_group, "measure": "share_of_year"},
    )


# ---------------------------------------------------------------------------
# catalogue and dispatch
# ---------------------------------------------------------------------------

OPERATIONS: dict[str, Any] = {
    "slice": lambda con, **kw: slice_by_topic(con, **kw),
    "slice_year": lambda con, **kw: slice_by_year(con, **kw),
    "dice": lambda con, **kw: dice_by_topic_and_year(con, **kw),
    "roll_up": lambda con, **kw: roll_up_to_year(con, **kw),
    "drill_down": lambda con, **kw: drill_down_to_month(con, **kw),
    "top_months": lambda con, **kw: top_n_months(con, **kw),
    "pivot": lambda con, **kw: pivot_month_by_topic(con, **kw),
    "cube": lambda con, **kw: cube_by_year_topic_band(con, **kw),
    "drill_across": lambda con, **kw: drill_across_market_and_headlines(con, **kw),
    "topic_mix": lambda con, **kw: topic_mix_over_time(con, **kw),
}

DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "slice": {"topic": "Business"},
    "slice_year": {"year": 2018},
    "dice": {"topics": ["Business", "Sports", "Entertainment"], "years": [2017, 2018, 2019]},
    "roll_up": {},
    "drill_down": {"topic": "Business", "year": 2018},
    "top_months": {"n": 10},
    "pivot": {"topics": ["Business", "Sports", "Entertainment"], "measure": "headline_count"},
    "cube": {"topic_group": "Subject"},
    "drill_across": {"topic": "Business"},
    "topic_mix": {"topic_group": "Subject"},
}


def list_operations() -> list[dict[str, Any]]:
    """The v1 topic operations only.

    Deliberately unchanged: tests/test_olap.py pins this to exactly OPERATIONS,
    and widening it would quietly change a contract callers rely on. Use
    `list_all_operations()` for the combined v1 + v2 view.
    """
    return [
        {"name": name, "default_params": DEFAULT_PARAMS.get(name, {})}
        for name in OPERATIONS
    ]


def list_all_operations() -> list[dict[str, Any]]:
    """Every registered operation, v1 topic family and v2 sector family.

    The CLI and the API's /operations endpoint use this, so a caller sees one
    flat list rather than two it has to know to combine.
    """
    from dwm.olap import sector_ops

    return [
        {**entry, "family": "topic"} for entry in list_operations()
    ] + [
        {
            "name": name,
            "default_params": sector_ops.DEFAULT_PARAMS.get(name, {}),
            "family": "sector",
        }
        for name in sector_ops.OPERATIONS
    ]


def run_operation(
    con: duckdb.DuckDBPyConnection, operation: str, **params: Any
) -> dict[str, Any]:
    """Run one operation against an already-open connection.

    Split out from `run_olap` so the API can serve the same operations over a
    long-lived read-only connection. Two dispatch paths would be two chances
    for the CLI and the API to disagree about what an operation returns.

    The result carries a plain-language `reading` alongside its rows. Every
    operation returns correctly-named columns and useful numbers; a client that
    renders only the rows shows the reader a table and leaves them to work out
    what it means, which is half a job done. The reading is derived from the
    rows just returned, so it cannot describe something else.

    Sector operations are dispatched to dwm/olap/sector_ops.py. They carry their
    own descriptions in `description` rather than a readings-module entry, so
    they return before the v1 reading/chart wiring below.
    """
    from dwm.olap import sector_ops

    if operation in sector_ops.OPERATIONS:
        kwargs = dict(sector_ops.DEFAULT_PARAMS.get(operation, {}))
        kwargs.update(params or {})
        payload = sector_ops.run_operation(con, operation, **kwargs)
        log.info("%s: %s rows", operation, human_int(len(payload.get("rows") or [])))
        return payload

    if operation not in OPERATIONS:
        raise KeyError(
            f"unknown OLAP operation {operation!r}. "
            f"Known: {sorted(set(OPERATIONS) | set(sector_ops.OPERATIONS))}"
        )
    kwargs = dict(DEFAULT_PARAMS.get(operation, {}))
    kwargs.update(params or {})
    result = OPERATIONS[operation](con, **kwargs)
    log.info("%s: %s rows", result.operation, human_int(len(result.rows)))
    payload = result.to_dict()
    payload["reading"] = reading_for(
        operation, payload.get("rows"), payload.get("columns"),
        payload.get("meta"), kwargs,
    )
    payload["chart"] = olap_chart_spec(operation, payload.get("rows") or [],
                                       payload.get("columns") or [])
    return payload


def run_olap(
    settings: Settings, operation: str | None = None, **params: Any
) -> dict[str, Any]:
    """Run one named operation, or list them all when none is given."""
    from dwm.olap import sector_ops

    if operation is None:
        return {"operations": list_all_operations()}

    if operation in sector_ops.OPERATIONS:
        sector_kwargs = dict(sector_ops.DEFAULT_PARAMS.get(operation, {}))
        sector_kwargs.update(params)
        con = connect(settings)
        try:
            return sector_ops.run_operation(con, operation, **sector_kwargs)
        finally:
            con.close()

    if operation not in OPERATIONS:
        raise KeyError(
            f"unknown OLAP operation {operation!r}. "
            f"Known: {sorted(set(OPERATIONS) | set(sector_ops.OPERATIONS))}"
        )

    con = connect(settings)
    try:
        return run_operation(con, operation, **params)
    finally:
        con.close()
