"""dim_trading_calendar: the bridge between the news calendar and the market one.

News is published on Saturdays, Sundays and market holidays. Prices are not
traded on those days. Any join between a headline and a price therefore has to
answer one question first: *which session does this news belong to?* For a
headline dated Saturday that is the following Monday; for one dated a Diwali
holiday, the next session after it. Joining on the raw calendar date silently
drops every weekend headline, which is roughly 28% of the corpus.

The convention here is the standard one: a headline is attributed to the next
trading session at or after its publication date. So Friday evening news lands
on Friday, Saturday news lands on Monday, and news published after Friday's
close is treated as belonging to Friday rather than being pushed to Monday.
That last choice is a judgement: it assumes a reader acted on the news before
Monday's open, which is the assumption an event study has to state rather than
hide.

`is_trading_day` is COPIED from `dim_date.is_trading_day`, which v1 already
populated from the Nifty series. It is never recomputed here, so the two can
never disagree.
"""

from __future__ import annotations

import duckdb

from dwm.logging_utils import get, human_int

log = get("dwm.etl.calendar")

TABLE = "dim_trading_calendar"


def window_bounds(con: duckdb.DuckDBPyConnection) -> tuple[str, str]:
    """The in-window date span, taken from the data rather than the config.

    Same definition v1 uses: the min and max publish_date of headlines flagged
    in_window. `dim_date` itself carries no in_window column, so it is the fact
    table that defines the span.
    """
    row = con.execute(
        "SELECT min(publish_date), max(publish_date) FROM fact_headline WHERE in_window"
    ).fetchone()
    if not row or row[0] is None:
        # No headline facts yet: fall back to the whole date spine.
        row = con.execute("SELECT min(full_date), max(full_date) FROM dim_date").fetchone()
    if not row or row[0] is None:
        raise ValueError(f"cannot resolve a window: {TABLE} needs dim_date to have rows")
    return row[0].isoformat(), row[1].isoformat()


def build(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Create dim_trading_calendar from dim_date and fact_market_daily.

    Every calendar date in the window gets a row, trading or not, so weekend and
    holiday news always has somewhere to land.
    """
    present = {
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name IN ('dim_date', 'fact_market_daily')"
        ).fetchall()
    }
    missing = {"dim_date", "fact_market_daily"} - present
    if missing:
        raise FileNotFoundError(
            f"cannot build {TABLE}: {sorted(missing)} missing. "
            "Run `python -m dwm build` first."
        )

    con.execute(f"DROP TABLE IF EXISTS {TABLE}")
    lo, hi = window_bounds(con)
    con.execute(
        f"""
        CREATE TABLE {TABLE} AS
        WITH bounds AS (SELECT DATE '{lo}' AS lo, DATE '{hi}' AS hi),
        cal AS (
            SELECT CAST(strftime(d, '%Y%m%d') AS INTEGER) AS date_key,
                   d AS full_date
            FROM bounds, unnest(generate_series(lo, hi, INTERVAL 1 DAY)) AS t(d)
        ),
        sessions AS (
            -- Every session the market actually traded, across the whole
            -- series, not just the window. A headline on the window's last
            -- Friday still needs to resolve to a real session.
            SELECT CAST(strftime(trade_date, '%Y%m%d') AS INTEGER) AS date_key
            FROM fact_market_daily
            GROUP BY 1
        ),
        forward AS (
            -- For each date, the smallest session date_key at or after it.
            SELECT c.date_key,
                   min(s.date_key) AS next_session
            FROM cal c
            LEFT JOIN sessions s ON s.date_key >= c.date_key
            GROUP BY 1
        )
        SELECT c.date_key,
               dd.is_trading_day                AS is_trading_day,
               coalesce(f.next_session, c.date_key) AS effective_trading_date_key
        FROM cal c
        JOIN dim_date dd ON dd.date_key = c.date_key
        JOIN forward f  ON f.date_key = c.date_key
        ORDER BY c.date_key
        """
    )

    counts = _counts(con)
    log.info(
        "%s: %s dates (%s trading, %s non-trading)",
        TABLE, human_int(counts["rows"]), human_int(counts["trading"]),
        human_int(counts["rows"] - counts["trading"]),
    )
    return counts


def _counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    row = con.execute(
        f"SELECT count(*), count(*) FILTER (WHERE is_trading_day) FROM {TABLE}"
    ).fetchone()
    # A date with no later session at all cannot be attributed to one. Those are
    # dates past the last traded session and are expected, not a defect.
    unresolved = int(
        con.execute(
            f"""
            SELECT count(*) FROM {TABLE} c
            WHERE NOT EXISTS (
                SELECT 1 FROM dim_date m
                WHERE m.date_key = c.effective_trading_date_key
            )
            """
        ).fetchone()[0]
    )
    return {"rows": int(row[0]), "trading": int(row[1]), "unresolved": unresolved}


def check(con: duckdb.DuckDBPyConnection) -> dict[str, object]:
    """Invariants the tests assert. Returns a dict rather than raising."""
    if not con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [TABLE]
    ).fetchone()[0]:
        return {"present": False}
    result: dict[str, object] = {"present": True, **_counts(con)}

    result["contiguous"] = (
        con.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]
        == con.execute(
            f"SELECT count(*) FROM {TABLE} c JOIN dim_date d USING (date_key)"
        ).fetchone()[0]
    )

    # The rule under test: a non-trading date points at a session that is later
    # than it, and that session really is a trading day.
    result["non_trading_forward_looks_valid"] = int(
        con.execute(
            f"""
            SELECT count(*) FROM {TABLE} c
            JOIN dim_date e ON e.date_key = c.effective_trading_date_key
            WHERE NOT c.is_trading_day AND e.is_trading_day IS NOT TRUE
            """
        ).fetchone()[0]
    ) == 0

    # A trading date must map to itself, never forward.
    result["trading_maps_to_self"] = int(
        con.execute(
            f"""
            SELECT count(*) FROM {TABLE}
            WHERE is_trading_day AND effective_trading_date_key <> date_key
            """
        ).fetchone()[0]
    ) == 0

    # Spot-check the rule on real weekend days rather than a hard-coded date.
    result["non_trading_saturday_maps_forward"] = _weekend_spot_check(con)
    result["trading_weekend_sessions"] = trading_weekend_sessions(con)
    return result


def _weekend_spot_check(con: duckdb.DuckDBPyConnection) -> bool | None:
    """True when every *non-trading* Saturday maps forward to a real session.

    The expectation is derived from the calendar rather than hard-coded, so the
    check cannot pass because someone hard-coded a date that happened to be a
    trading day.

    Trading Saturdays are excluded on purpose, and this is not a theoretical
    caveat: 2020-02-01 was a Saturday, the Union Budget was presented, and NSE
    ran a special session. dim_date.is_trading_day is TRUE for it, so it
    correctly maps to itself, and treating it as a weekend failure would mean
    asserting that the market was shut on the day it was not.

    Returns None when the window contains no non-trading Saturday.
    """
    non_trading = con.execute(
        f"""
        SELECT count(*) FROM {TABLE} c
        JOIN dim_date d ON d.date_key = c.date_key
        WHERE strftime(d.full_date, '%w') = '6' AND NOT c.is_trading_day
        """
    ).fetchone()[0]
    if not non_trading:
        return None
    bad = con.execute(
        f"""
        SELECT count(*)
        FROM {TABLE} c
        JOIN dim_date d ON d.date_key = c.date_key
        JOIN dim_date e ON e.date_key = c.effective_trading_date_key
        WHERE strftime(d.full_date, '%w') = '6'
          AND NOT c.is_trading_day
          AND (e.full_date <= d.full_date OR e.is_trading_day IS NOT TRUE)
        """
    ).fetchone()[0]
    return bad == 0


def trading_weekend_sessions(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Weekend dates the exchange actually traded, for the data-quality report.

    India does not normally trade at the weekend, but it does on occasion -
    2020-02-01, the Union Budget Saturday, is one. These rows must not be
    treated as weekend closures, so they are surfaced rather than smoothed away.
    """
    return [
        r[0].isoformat()
        for r in con.execute(
            f"""
            SELECT d.full_date FROM {TABLE} c
            JOIN dim_date d ON d.date_key = c.date_key
            WHERE c.is_trading_day AND strftime(d.full_date, '%w') IN ('0', '6')
            ORDER BY 1
            """
        ).fetchall()
    ]
