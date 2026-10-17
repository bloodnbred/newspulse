"""Backend-for-frontend router at `/ui`, for the React front end.

Implemented from `docs/UI-API-CONTRACT.md`. Three things distinguish this from
the v2 router and all three are load-bearing:

**Every response is an envelope.** `{data, meta:{concept, sql, ms, rows, note}}`.
The `sql` field is why this module exists rather than a set of route aliases: the
contract wants the reader to be able to open any chart and see the query that
produced it. That means the SQL has to be *recorded as it runs*, not
reconstructed afterwards from a template, because a reconstructed query is a
description of what the code used to do rather than a record of what it did.
`_Queries` below does the recording.

**Reuse is explicit.** Where a v2 endpoint already computes the thing, this
router calls the same function rather than writing a second query. `/ui/insights`
is `dwm.inference.insights.build`; `/ui/brief` is `dwm.inference.brief.build`.
Two queries for one number is two numbers that can disagree.

**`meta.concept` is the project's wording, not the contract's list.** The
contract names ten concepts; `dwm/inference/insights.py` emits richer labels
("drill-across, rank correlation"). The server's wording wins and the TypeScript
union was widened to accept it, because the concept chip exists to tell the
reader something and narrowing it to fit a type would lose exactly that.

Timing is measured per call rather than by middleware, because a middleware
cannot know how long the *queries* took - only how long the request did, and
most of a request here is the queries.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import duckdb
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from dwm.api import store
from dwm.logging_utils import get

log = get("dwm.api.ui")

router = APIRouter()

#: Sent on every read-only response. Five minutes is the contract's figure and
#: also the TanStack Query stale time, so a refetch in the browser and a
#: revalidation in the CDN do not disagree about what "fresh" means.
CACHE_CONTROL = "public, max-age=300"

_MAX_ROWS = 500
_MAX_HEADLINES = 50

_LOCK_CON: duckdb.DuckDBPyConnection | None = None


def con() -> duckdb.DuckDBPyConnection:
    """The one long-lived read-only handle on the API snapshot.

    Shared with the v2 router for the same reason it is shared there: DuckDB
    caches connection objects per database path, so a handle opened per request
    is not a private handle. Serialised by `_reading` in dwm.api.v2 rather than
    locking twice, so every reader of this warehouse - v1, v2 and ui - queues
    behind one lock.
    """
    global _LOCK_CON
    from dwm.api.v2 import _reading

    if _LOCK_CON is None:
        with _reading():
            _LOCK_CON = store.connection()
    return _LOCK_CON


@contextmanager
def reading() -> Iterator[duckdb.DuckDBPyConnection]:
    """Hold the shared handle for one complete read."""
    from dwm.api.v2 import _reading

    with _reading() as c:
        yield c


class _Queries:
    """Runs SQL against the shared handle and remembers what it ran.

    One instance per request. `sql` accumulates the statements in order, which
    is what the "How this was computed" drawer shows, so the answer to "what
    query produced this number" is the query rather than a prose description of
    it.
    """

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self.con = connection
        self.statements: list[str] = []
        self.rows_returned = 0

    def _record(self, sql: str) -> None:
        text = " ".join(sql.split())
        self.statements.append(text if len(text) <= 1400 else text[:1400] + " ...")

    def rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        self._record(sql)
        cur = self.con.execute(sql, params or [])
        columns = [d[0] for d in cur.description]
        out = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
        self.rows_returned += len(out)
        return out

    def one(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        found = self.rows(sql, params)
        return found[0] if found else None

    def scalar(self, sql: str, params: list[Any] | None = None) -> Any:
        self._record(sql)
        row = self.con.execute(sql, params or []).fetchone()
        return row[0] if row else None

    @property
    def sql(self) -> str | None:
        return "\n\n".join(self.statements) if self.statements else None


#: Response cache for the expensive aggregates. The contract says `/ui/home`
#: must be served from cubes and cached; the first call is still the slow one
#: because the insight cards scan sector-days in Python, and saying so is better
#: than a stale number presented as fresh.
_CACHE: dict[str, tuple[float, JSONResponse]] = {}
_CACHE_TTL_S = 300.0


def _cached_envelope(key: str, build: Callable[[], JSONResponse]) -> JSONResponse:
    """Serve a cached envelope, or build and remember one.

    Keeps the `JSONResponse` object rather than the payload so the recorded
    `ms` is the time the build actually took. A cache that reports the cost of
    the original computation on every subsequent hit would make the drawer
    quietly wrong, which is worse than it being slow.
    """
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL_S:
        return JSONResponse(
            content=json.loads(hit[1].body),
            headers={"Cache-Control": CACHE_CONTROL, "X-Dwm-Cache": "hit"},
        )
    response = build()
    _CACHE[key] = (time.time(), response)
    return JSONResponse(
        content=json.loads(response.body),
        headers={"Cache-Control": CACHE_CONTROL, "X-Dwm-Cache": "miss"},
    )


def _jsonable(value: Any) -> Any:
    """Make a payload safe for JSON, however it was assembled.

    DuckDB returns Python `date` and `Decimal` objects, and a bare `date` is not
    serialisable - so an endpoint that selects a DATE column and hands the rows
    straight up dies with `TypeError: Object of type date is not JSON
    serializable`, at the very last step, with the query already run. Fixing
    that per endpoint is how one endpoint ends up forgetting.

    Doing it here means the contract's promise ("every response is an envelope")
    is also a promise that every response is *encodable*. Dates become ISO
    strings, which is what the TypeScript types on the other side already
    declare.
    """
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def envelope(
    concept: str,
    data: Any,
    q: _Queries,
    *,
    note: str | None = None,
    ms: float,
    mock: bool = False,
    status: int = 200,
) -> JSONResponse:
    """One response, in the shape the contract specifies."""
    meta: dict[str, Any] = {
        "concept": concept,
        "ms": round(ms, 2),
        "rows": q.rows_returned,
    }
    if q.sql:
        meta["sql"] = q.sql
    if note:
        meta["note"] = note
    if mock:
        meta["mock"] = True
    return JSONResponse(
        content={"data": _jsonable(data), "meta": _jsonable(meta)},
        status_code=status,
        headers={"Cache-Control": CACHE_CONTROL},
    )


def _mood_label(percentile: float) -> str:
    """Three coarse bands, chosen so the gauge is not claiming precision.

    A dial with ten labelled zones would imply the difference between 61 and 63
    is meaningful. It is not: the percentile is one rank in a distribution of
    daily headline counts, and three words say what a reader can act on.
    """
    if percentile < 34:
        return "Calm"
    if percentile < 67:
        return "Active"
    return "Heated"


def _window() -> tuple[str, str]:
    """The corpus window, read from the data rather than hard-coded.

    Rule 4: the window is `max(publish_date)` of toi. Computing it here keeps
    every `/ui/*` payload consistent with `facts.json` without each endpoint
    having to remember to filter on it.
    """
    row = con().execute(
        """
        SELECT min(publish_date), max(publish_date) FROM fact_headline WHERE in_window
        """
    ).fetchone()
    if row and row[0] and row[1]:
        return str(row[0]), str(row[1])
    return "2015-06-30", "2020-06-30"


def _not_built(table: str, command: str) -> JSONResponse:
    """A phase that has not run yet, said as an action rather than an absence."""
    return JSONResponse(
        status_code=404,
        content={
            "detail": f"{table} has not been built yet. Run `{command}` and reload."
        },
        headers={"Cache-Control": "no-store"},
    )


def _has(table: str) -> bool:
    from dwm.api.v2 import _has as v2_has

    return v2_has(table)


# ---------------------------------------------------------------------------
# /ui/home - the whole first paint in one call
# ---------------------------------------------------------------------------


@router.get("/ui/home")
def home() -> JSONResponse:
    """Cached wrapper. The work is in `_build_home`."""
    return _cached_envelope("/ui/home", _build_home)


def _build_home() -> JSONResponse:
    """Everything the homepage needs, in one response.

    The contract's rule is that this must be served from cubes and mining
    tables and never scan `fact_headline`, because the headline table holds 3.15M
    rows and a per-day aggregate over it would blow the 1.5s target. Every
    figure below comes from a cube, a fact at daily grain, or a mining table.
    """
    started = time.perf_counter()
    if not _has("cube_sector_month"):
        return _not_built("cube_sector_month", "python -m dwm link")
    with reading() as c:
        q = _Queries(c)
        start, end = _window()

        # --- KPIs, each with the monthly series that draws its sparkline -----
        # Headlines in window, by month, from the v1 cube rather than the
        # 3.15M-row fact: cube_month_topic already holds it per topic.
        monthly_news = q.rows(
            """
            SELECT year_month, sum(headline_count) AS headlines
            FROM cube_month_topic GROUP BY 1 ORDER BY 1
            """
        )
        news_total = sum(int(r["headlines"]) for r in monthly_news)

        sessions = q.scalar(
            "SELECT count(*) FROM dim_trading_calendar WHERE is_trading_day"
        )

        # Nifty over the window, clipped to the news window on purpose: the
        # index runs to 2023-01-24 and a chart that stops in 2020 beside a
        # corpus that stops in 2020 is honest, whereas one that runs on past it
        # invites the reader to compare two different periods.
        #
        # `trade_date`, not `full_date` - fact_market_daily is keyed on its own
        # date column. Getting that wrong is a BinderException, not a null, so
        # it cannot be discovered by inspecting a response.
        nifty = q.rows(
            """
            SELECT trade_date, close, return_pct
            FROM fact_market_daily
            WHERE trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
              AND close IS NOT NULL
            ORDER BY trade_date
            """,
            [start, end],
        )
        for row in nifty:
            row["trade_date"] = str(row["trade_date"])
        nifty_first = nifty[0]["close"] if nifty else None
        nifty_last = nifty[-1]["close"] if nifty else None
        nifty_change = (
            (float(nifty_last) - float(nifty_first)) / float(nifty_first) * 100.0
            if nifty_first and nifty_last
            else None
        )

        stocks_tracked = q.scalar(
            "SELECT count(*) FROM dim_stock WHERE price_source IS NOT NULL"
        )
        sectors = q.scalar("SELECT count(*) FROM dim_sector")

        # Risk-signal rate: a rate on UNLABELLED headlines, so it is a
        # risk-signal rate and never a fake-news rate (rule 3).
        #
        # Read from the daily cube, not `count(*) FILTER (...)` over
        # fact_headline. That predicate is a full scan of 3.15M rows and was
        # most of this endpoint's 3.7 seconds; `cube_day_topic` already holds
        # `risk_signal_count` and `headline_count` per day and topic, so the
        # ratio is the same number for a hundredth of the work.
        risk = q.one(
            """
            SELECT round(100.0 * sum(risk_signal_count)
                         / nullif(sum(headline_count), 0), 2) AS pct
            FROM cube_day_topic
            """
        )
        surprise_count = (
            q.scalar("SELECT count(*) FROM mining_surprise_days")
            if _has("mining_surprise_days")
            else None
        )
        outliers = (
            q.scalar("SELECT count(*) FROM fact_stock_daily WHERE is_outlier")
            if _has("fact_stock_daily")
            else None
        )

        # --- main series: news heat against the Nifty, monthly --------------
        # `cube_month_topic` stores SUMS, not means: sentiment arrives as
        # `sum_sentiment_compound` and is divided here, which is rule 16 and
        # also the only way the months stay comparable.
        main = q.rows(
            """
            WITH news AS (
                SELECT year_month,
                       sum(headline_count) AS headlines,
                       round(sum(sum_sentiment_compound)
                             / nullif(sum(headline_count), 0), 4) AS sentiment,
                       round(100.0 * sum(sensational_count)
                             / nullif(sum(headline_count), 0), 3) AS sensational
                FROM cube_month_topic
                GROUP BY 1
            ),
            px AS (
                SELECT substr(CAST(trade_date AS VARCHAR), 1, 7) AS ym,
                       round(avg(close), 2) AS nifty
                FROM fact_market_daily
                WHERE trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                  AND close IS NOT NULL
                GROUP BY 1
            )
            SELECT n.year_month AS date, n.headlines, n.sentiment, n.sensational, p.nifty
            FROM news n LEFT JOIN px p ON p.ym = n.year_month
            ORDER BY 1
            """,
            [start, end],
        )

        # --- market mood: recent news heat against its own history ---
        # A percentile of the mean of the last 20 sessions, measured within the
        # distribution of ALL daily totals.
        #
        # The first version counted how many days matched the single busiest
        # day, which is 1 in ~1800 and read "0.1th percentile" - a number that
        # was arithmetically correct and completely meaningless. A mood has to
        # answer "how busy is it NOW, for this corpus", so `recent` is a window
        # and the comparison is a rank in a distribution.
        mood = q.one(
            """
            WITH daily AS (
                SELECT date_key, sum(news_count_non_market) AS news
                FROM fact_news_sector_daily GROUP BY 1
            ),
            recent AS (
                SELECT avg(news) AS level FROM (
                    SELECT news FROM daily ORDER BY date_key DESC LIMIT 20
                )
            )
            SELECT round(100.0 * sum(CASE WHEN d.news <= (SELECT level FROM recent)
                                         THEN 1 ELSE 0 END)
                         / nullif(count(*), 0), 1) AS percentile,
                   (SELECT level FROM recent) AS recent_level
            FROM daily d
            """
        )

        # Aliased to the names `EventMark` declares in web-scaffold/src/types.ts.
        # The dimension's own columns are `start_date` and `event_label`, and
        # returning those unchanged produced a payload where `date` was
        # undefined - which the calendar crashed on, blanking the entire page.
        # A front end should not have to know the dimension's column names.
        events = q.rows(
            """
            SELECT start_date AS date, event_label AS label, event_type AS type,
                   event_key
            FROM dim_event ORDER BY start_date
            """
        )
        # `heat_z` is each sector's news volume scored against its OWN history,
        # which is the only comparison that means anything across ten sectors of
        # very different sizes. It is a z over the sector's monthly series - the
        # same shape as the z in mining_surprise_days, and the treemap's tooltip
        # says so rather than presenting it as a market-wide measure.
        sector_heat = q.rows(
            """
            WITH per_sector AS (
                SELECT sector_key, news_count,
                       avg(news_count) OVER (
                         PARTITION BY sector_key ORDER BY year, month
                         ROWS BETWEEN 11 PRECEDING AND CURRENT ROW
                       ) AS rolling_mean,
                       stddev_samp(news_count) OVER (
                         PARTITION BY sector_key ORDER BY year, month
                         ROWS BETWEEN 11 PRECEDING AND CURRENT ROW
                       ) AS rolling_sd
                FROM cube_sector_month
            ),
            agg AS (
                SELECT sector_key,
                       sum(news_count) AS total_news,
                       round(avg(news_count - rolling_mean)
                             / nullif(avg(rolling_sd), 0), 2) AS heat_z
                FROM per_sector GROUP BY sector_key
            )
            SELECT s.sector_name AS sector,
                   a.total_news AS news_count,
                   a.heat_z,
                   round(sum(c.sum_return) / nullif(sum(c.stock_obs), 0), 4) AS avg_return_pct
            FROM cube_sector_month c
            JOIN dim_sector s ON s.sector_key = c.sector_key
            JOIN agg a ON a.sector_key = c.sector_key
            GROUP BY 1, 2, 3
            ORDER BY news_count DESC
            """
        )
        surprise = []
        if _has("mining_surprise_days"):
            surprise = q.rows(
                """
                SELECT sector_name AS sector, session, kind, news_heat_z, abs_return_z, avg_return
                FROM mining_surprise_days
                ORDER BY abs_return_z DESC LIMIT 5
                """
            )

    from dwm.inference.insights import build

    with reading() as c:
        insight_payload = build(c)
    insights = [
        {
            "id": card["id"],
            "title": card["title"],
            "text": card["caution"],
            "concept": card["concept"],
            "page": card.get("page", "explore"),
            "evidence": {
                k: v for k, v in card.get("evidence", {}).items()
                if isinstance(v, (int, float, str))
            },
        }
        for card in insight_payload["cards"][:6]
    ]

    ms = (time.perf_counter() - started) * 1000.0
    _mood_pct = float(mood["percentile"]) if mood and mood["percentile"] is not None else 50.0
    # One sparkline series per KPI, monthly, so the card can draw it without a
    # second request. Every KPI therefore reports over the SAME months.
    spark = [float(r["headlines"]) for r in monthly_news]
    data = {
        "devSample": False,
        "window": {"start": start, "end": end},
        "kpis": [
            {"id": "headlines", "label": "Headlines analysed", "value": news_total,
             "spark": spark, "href": "/explore"},
            {"id": "stocks", "label": "Stocks tracked", "value": stocks_tracked,
             "spark": [], "href": "/stocks"},
            {"id": "sessions", "label": "Trading sessions", "value": sessions,
             "spark": [], "href": "/data"},
            {"id": "nifty", "label": "Nifty over the window",
             "value": round(nifty_change, 2) if nifty_change is not None else 0,
             "decimals": 2, "unit": "%", "spark": [float(r["close"]) for r in nifty[-60:]],
             "href": "/events"},
            {"id": "risk", "label": "Risk-signal rate",
             "value": risk["pct"] if risk and risk["pct"] is not None else 0,
             "decimals": 2, "unit": "%", "spark": [], "href": "/headline"},
            {"id": "surprise", "label": "Surprise days found", "value": surprise_count or 0,
             "spark": [], "href": "/mining"},
        ],
        "mood": {
            "score": _mood_pct,
            # The label is derived here rather than left to the front end, so
            # the band that a number falls into is decided in one place and the
            # gauge cannot disagree with the caption.
            "label": _mood_label(_mood_pct),
            "percentile": _mood_pct,
            "recent_level": round(float(mood["recent_level"] or 0), 1) if mood else None,
        },
        "main": main,
        "events": events,
        "sectorHeat": sector_heat,
        "surprise": surprise,
        "insights": insights,
    }
    return envelope(
        "OLAP cube",
        data,
        q,
        ms=ms,
        note=(
            f"Served from cubes and mining tables. {sectors} sectors, "
            f"{outliers or 0} outlier rows kept but excluded from statistics. "
            f"Nifty is clipped to the news window ({start} to {end}); the index "
            f"series itself runs later. The risk-signal rate is a rate on "
            f"unlabelled headlines and is not a fake-news rate."
        ),
    )


# ---------------------------------------------------------------------------
# the rest of the U1 list
# ---------------------------------------------------------------------------


@router.get("/ui/tape")
def tape() -> JSONResponse:
    """Latest sector moves for the ticker tape.

    The last five trading sessions in the window, per sector, so the tape moves
    as the data does instead of showing an arbitrary snapshot.
    """
    started = time.perf_counter()
    if not _has("fact_news_sector_daily"):
        return _not_built("fact_news_sector_daily", "python -m dwm link")
    with reading() as c:
        q = _Queries(c)
        # Returns come from `fact_stock_daily`, not `fact_news_sector_daily` -
        # the latter holds news columns only (`news_count`, `sum_sentiment`,
        # `high_sensational_count`) and has no `sum_return`. Assuming otherwise
        # is a BinderException at request time rather than a null.
        #
        # `NOT is_outlier` is rule 14 and is not optional here: five rows in the
        # corpus have moves above 25%, and a tape is exactly where a 250% day
        # would be shown largest.
        #
        # Two figures per sector, because a tape needs both: the move across the
        # whole window (`value`, the level) and the last session (`change`, the
        # daily delta). Returning the day's change in both fields made the tape
        # print every number twice.
        rows = q.rows(
            """
            WITH sessions AS (
                SELECT max(trade_date) AS latest FROM fact_stock_daily
            )
            SELECT sec.sector_name AS sector,
                   round(avg(f.return_pct), 3) AS window_pct,
                   round(avg(CASE WHEN f.trade_date = (SELECT latest FROM sessions)
                             THEN f.return_pct END), 3) AS latest_pct
            FROM fact_stock_daily f
            JOIN dim_stock st ON st.stock_key = f.stock_key
            JOIN dim_sector sec ON sec.sector_key = st.sector_key
            WHERE NOT f.is_outlier AND f.return_pct IS NOT NULL
            GROUP BY 1 ORDER BY abs(window_pct) DESC NULLS LAST
            """
        )
    items = [
        {
            "label": r["sector"],
            "value": f"{r['window_pct']:+.2f}%" if r["window_pct"] is not None else "n/a",
            "change": float(r["latest_pct"] or 0.0),
        }
        for r in rows
    ]
    return envelope(
        "Drill-across",
        items,
        q,
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            "Two figures per sector: `value` is the mean daily return across the "
            "whole window and `change` is the last trading session, both in "
            "percentage points and both excluding outlier rows. Neither is a "
            "price - a sector has no price."
        ),
    )


@router.get("/ui/main-series")
def main_series(
    grain: str = Query(default="month", pattern="^(week|month)$"),
    metric: str = Query(default="heat", pattern="^(heat|sentiment|sensational)$"),
) -> JSONResponse:
    """News against the Nifty at weekly or monthly grain."""
    started = time.perf_counter()
    if not _has("cube_month_topic"):
        return _not_built("cube_month_topic", "python -m dwm build")
    column = {
        "heat": "sum(headline_count)",
        "sentiment": "sum(sum_sentiment_compound) / nullif(sum(headline_count), 0)",
        "sensational": "100.0 * sum(sensational_count) / nullif(sum(headline_count), 0)",
    }[metric]
    with reading() as c:
        q = _Queries(c)
        start, end = _window()
        # Only monthly is served. `cube_month_topic` is the finest cube that
        # carries both news and price at the same grain, and returning monthly
        # points under `grain=week` would be a mislabelled series - the Time
        # Machine slider would appear to offer resolution that is not there.
        # The refusal is in the response rather than a silent substitution.
        if grain != "month":
            return envelope(
                "OLAP cube",
                [],
                q,
                ms=(time.perf_counter() - started) * 1000.0,
                note=(
                    "grain=week is not available: the finest cube holding news and "
                    "price at one grain is monthly. Run the weekly ETL stage or "
                    "use grain=month. Nothing is substituted for what was asked for."
                ),
                status=422,
            )
        rows = q.rows(
            f"""
            WITH news AS (
                SELECT year_month AS ym, {column} AS value
                FROM cube_month_topic
                GROUP BY 1
            ),
            px AS (
                SELECT substr(CAST(trade_date AS VARCHAR), 1, 7) AS ym,
                       round(avg(close), 2) AS nifty
                FROM fact_market_daily
                WHERE trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                  AND close IS NOT NULL
                GROUP BY 1
            )
            SELECT n.ym AS date, n.value, p.nifty
            FROM news n LEFT JOIN px p ON p.ym = n.ym
            ORDER BY 1
            """,
            [start, end],
        )
    return envelope(
        "OLAP cube",
        rows,
        q,
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            "Monthly grain, from the cube rather than the 3.15M-row headline "
            "table. Sentiment is a sum divided by the headline count, not an "
            "average of averages."
        ),
    )


@router.get("/ui/day/{day}")
def day_panel(day: str) -> JSONResponse:
    """One session: its headlines, the index move, and whether it was unusual."""
    started = time.perf_counter()
    if not _has("fact_headline"):
        return _not_built("fact_headline", "python -m dwm build")
    with reading() as c:
        q = _Queries(c)
        headlines = q.rows(
            """
            SELECT headline_id, publish_date, topic_key, headline_text,
                   sentiment_compound, sensational_score, is_risk_signal,
                   is_market_report
            FROM fact_headline
            WHERE in_window AND publish_date = CAST(? AS DATE)
            ORDER BY is_risk_signal DESC, sensational_score DESC NULLS LAST
            LIMIT ?
            """,
            [day, _MAX_HEADLINES],
        )
        market = q.one(
            """
            SELECT trade_date, close, return_pct
            FROM fact_market_daily WHERE trade_date = CAST(? AS DATE)
            """,
            [day],
        )
        if market:
            market["trade_date"] = str(market["trade_date"])
        surprise = None
        if _has("mining_surprise_days"):
            surprise = q.rows(
                """
                SELECT sector_name AS sector, kind, news_heat_z, abs_return_z
                FROM mining_surprise_days WHERE session = ? LIMIT 5
                """,
                [day],
            )
    return envelope(
        "Text mining",
        {
            "date": day,
            "headlines": headlines,
            "headline_count": len(headlines),
            "market": market,
            "surprise": surprise,
        },
        q,
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            f"Headlines are capped at {_MAX_HEADLINES}. The 'unusual' flag comes "
            f"from mining_surprise_days and is a z-score against that sector's own "
            f"preceding 60 sessions, not a threshold on absolute size."
        ),
    )


@router.get("/ui/calendar")
def calendar(metric: str = Query(default="heat", pattern="^(heat|sentiment|sensational)$")) -> JSONResponse:
    """Per-day values for the calendar heatmap."""
    started = time.perf_counter()
    if not _has("cube_day_topic"):
        return _not_built("cube_day_topic", "python -m dwm build")
    column = {
        "heat": "sum(headline_count)",
        "sentiment": "sum(sum_sentiment_compound) / nullif(sum(headline_count), 0)",
        "sensational": "100.0 * sum(sensational_count) / nullif(sum(headline_count), 0)",
    }[metric]
    with reading() as c:
        q = _Queries(c)
        start, end = _window()
        # `cube_day_topic` is keyed on the INTEGER surrogate `date_key`
        # (20150908), not on a date string. Casting through dim_date is what
        # turns it back into something a calendar axis can read, and joining
        # dim_date also gives `day_of_week` and `is_trading_day` for free -
        # which is how the calendar can mark the closed days the corpus has
        # headlines on (32.4% of them, per the data-quality report).
        rows = q.rows(
            f"""
            SELECT strftime(d.full_date, '%Y-%m-%d') AS date,
                   {column} AS value,
                   d.day_of_week,
                   d.is_trading_day
            FROM cube_day_topic c
            JOIN dim_date d ON d.date_key = c.date_key
            WHERE d.full_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
            GROUP BY 1, 3, 4 ORDER BY 1
            """,
            [start, end],
        )
    if len(rows) > 2500:
        rows = rows[-2500:]
    return envelope(
        "OLAP cube",
        rows,
        q,
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            "One row per in-window day from the daily cube. is_trading_day is "
            "carried so the front end can distinguish a quiet trading day from a "
            "market holiday - a great many headlines fall on the latter."
        ),
    )


@router.get("/ui/search")
def search(q: str = Query(..., min_length=1)) -> JSONResponse:
    """Command palette: stocks, sectors, events and pages."""
    started = time.perf_counter()
    with reading() as c:
        queries = _Queries(c)
        needle = f"%{q.strip().lower()}%"
        results: list[dict[str, Any]] = []
        for row in queries.rows(
            """
            SELECT symbol, company_name, s.sector_name AS sector, 'stock' AS kind
            FROM dim_stock d LEFT JOIN dim_sector s ON s.sector_key = d.sector_key
            WHERE lower(symbol) LIKE ? OR lower(company_name) LIKE ?
            LIMIT 8
            """,
            [needle, needle],
        ):
            results.append({
                "id": row["symbol"], "kind": "stock",
                "label": f"{row['symbol']} - {row['company_name']}",
                "sub": row["sector"], "href": f"/stocks/{row['symbol']}",
            })
        for row in queries.rows(
            "SELECT sector_key, sector_name FROM dim_sector WHERE lower(sector_name) LIKE ? LIMIT 5",
            [needle],
        ):
            results.append({
                "id": row["sector_name"], "kind": "sector",
                "label": row["sector_name"], "sub": "sector",
                "href": "/explore",
            })
        for row in queries.rows(
            """
            SELECT event_key, event_label, event_type FROM dim_event
            WHERE lower(event_label) LIKE ? ORDER BY start_date LIMIT 5
            """,
            [needle],
        ):
            results.append({
                "id": str(row["event_key"]), "kind": "event",
                "label": row["event_label"], "sub": row["event_type"],
                "href": f"/events?event={row['event_key']}",
            })
    results = results[:12]
    return envelope(
        "Text mining",
        results,
        queries,
        ms=(time.perf_counter() - started) * 1000.0,
        note="Literal substring match over the dimensions, capped at 12 results.",
    )


@router.get("/ui/schema")
def schema() -> JSONResponse:
    """The fact constellation: tables, their grain, and the keys between them."""
    started = time.perf_counter()
    with reading() as c:
        q = _Queries(c)
        tables = q.rows(
            """
            SELECT table_name AS name, column_count, estimated_size AS rows
            FROM duckdb_tables()
            WHERE schema_name = 'main'
            ORDER BY estimated_size DESC
            """
        )
        columns = q.rows(
            """
            SELECT table_name, column_name, data_type
            FROM duckdb_columns() WHERE schema_name = 'main'
            ORDER BY table_name, column_index
            """
        )
    by_table: dict[str, list[dict[str, Any]]] = {}
    for row in columns:
        by_table.setdefault(row["table_name"], []).append(
            {"name": row["column_name"], "type": row["data_type"]}
        )
    for table in tables:
        table["columns"] = by_table.get(table["name"], [])
    return envelope(
        "Fact constellation",
        {"tables": tables},
        q,
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            "Read from DuckDB's own catalogue, so it cannot drift from the file. "
            "Grain and join keys are described in docs/, not here: inferring them "
            "from column names would be a guess presented as metadata."
        ),
    )


@router.get("/ui/etl")
def etl() -> JSONResponse:
    """The ETL funnel, mention coverage, and what has not been audited.

    Written out rather than wrapping `/quality`, even though the two report the
    same thing. Wrapping would have been less code, and it would have produced a
    response with no `meta.sql` - because the v2 handler never recorded one - so
    the drawer's SQL panel on this page would have been empty for no visible
    reason. The contract says the drawer is on every chart.
    """
    started = time.perf_counter()
    with reading() as c:
        q = _Queries(c)
        funnel = q.rows(
            """
            SELECT run_id, dataset_code, step, status,
                   rows_read, rows_loaded, rows_rejected, detail
            FROM etl_audit ORDER BY audit_id DESC LIMIT 40
            """
        )
        coverage = q.one(
            """
            SELECT detail FROM etl_audit
            WHERE dataset_code = 'link' AND step = 'link.quality'
            ORDER BY audit_id DESC LIMIT 1
            """
        )
        linking: dict[str, Any] | None = None
        if coverage and coverage.get("detail"):
            try:
                linking = json.loads(coverage["detail"])
            except (json.JSONDecodeError, TypeError):
                linking = None
        outliers = None
        if _has("fact_stock_daily"):
            row = q.one("SELECT count(*) AS n FROM fact_stock_daily WHERE is_outlier")
            outliers = row["n"] if row else None

    data = {
        "funnel": funnel,
        "outlier_rows": outliers,
        "linking": linking,
        "caveat": (
            "Mention precision is absent until a person fills in the is_correct "
            "column of reports/mention_audit_sample.csv. It is never computed "
            "from the matcher itself, and `python -m dwm link --precision` "
            "refuses to publish below 30 human labels."
        ),
    }
    return envelope(
        "ETL",
        data,
        q,
        ms=(time.perf_counter() - started) * 1000.0,
        note=data["caveat"],
    )


@router.get("/ui/sectors")
def sectors() -> JSONResponse:
    """Every sector, for the filter bar.

    Not in the contract's table, but addendum rule 1 says the web app talks only
    to `/ui/*`, and the filter bar cannot call the v2 `/sectors` and call that
    compliant. Thin alias over the same dimension rather than a second query.
    """
    started = time.perf_counter()
    if not _has("dim_sector"):
        return _not_built("dim_sector", "python -m dwm build")
    with reading() as c:
        q = _Queries(c)
        rows = q.rows(
            """
            SELECT s.sector_key, s.sector_name,
                   count(d.stock_key) AS constituents,
                   count(*) FILTER (WHERE d.is_survivor) AS survivors,
                   count(*) FILTER (WHERE d.price_source IS NOT NULL) AS with_prices
            FROM dim_sector s LEFT JOIN dim_stock d ON d.sector_key = s.sector_key
            GROUP BY 1, 2 ORDER BY 2
            """
        )
    return envelope(
        "Fact constellation",
        rows,
        q,
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            "Constituents are today's Nifty 50 list applied to a 2015-2020 "
            "window. Companies that were constituents in the window and have "
            "since been removed are absent, and some present members listed "
            "after the window opened."
        ),
    )


@router.get("/ui/brief")
def brief(q: str = Query(..., min_length=1)) -> JSONResponse:
    """Quick Brief for a company or sector, for the hero search.

    Calls `dwm.inference.brief.build` rather than reimplementing it, so the
    hero search and the existing `/brief` route cannot disagree about the same
    company - two queries for one paragraph is two paragraphs, and only one of
    them would be reviewed.
    """
    started = time.perf_counter()
    if not _has("dim_stock"):
        return _not_built("dim_stock", "python -m dwm ingest --dataset stocks")
    with reading() as c:
        payload = build_brief(c, q)
    return envelope(
        "Text mining",
        payload,
        _Queries(con()),
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            "A brief that cannot confidently resolve a name says so and offers "
            "alternatives rather than guessing. A confident paragraph about the "
            "wrong company is worse than no answer at all."
        ),
    )


def build_brief(connection: duckdb.DuckDBPyConnection, q: str) -> dict[str, Any]:
    from dwm.inference.brief import build

    return build(connection, q)


@router.get("/ui/insights")
def insights() -> JSONResponse:
    """The insight cards, reusing the module the overview page already uses."""
    started = time.perf_counter()
    with reading() as c:
        payload = build_insights(c)
    return envelope(
        "Text mining",
        payload,
        _Queries(con()),
        ms=(time.perf_counter() - started) * 1000.0,
        note=payload.get("withheld") and
        "Cards below the evidence floor are listed in `withheld`, not dropped silently."
        or None,
    )


def build_insights(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    from dwm.inference.insights import build

    return build(connection)


__all__ = ["router", "envelope", "_Queries"]
