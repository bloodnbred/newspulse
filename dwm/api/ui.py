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
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from dwm.logging_utils import get

log = get("dwm.api.ui")

router = APIRouter()

#: Sent on every read-only response. Five minutes is the contract's figure and
#: also the TanStack Query stale time, so a refetch in the browser and a
#: revalidation in the CDN do not disagree about what "fresh" means.
CACHE_CONTROL = "public, max-age=300"

_MAX_ROWS = 500
_MAX_HEADLINES = 50


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

    The connection is optional. Three endpoints - the field shelf, the sector
    list and the schema - run no SQL at all, and passing them a handle is the
    only reason those call sites needed one. They pass `None`, which makes the
    absence of a statement an explicit fact rather than an empty handle that
    could be used by accident.
    """

    def __init__(self, connection: duckdb.DuckDBPyConnection | None) -> None:
        self.con = connection
        self.statements: list[str] = []
        self.rows_returned = 0

    def _handle(self) -> duckdb.DuckDBPyConnection:
        if self.con is None:
            raise RuntimeError("this endpoint holds no connection; it runs no SQL")
        return self.con

    def _record(self, sql: str) -> None:
        text = " ".join(sql.split())
        self.statements.append(text if len(text) <= 1400 else text[:1400] + " ...")

    def rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        self._record(sql)
        cur = self._handle().execute(sql, params or [])
        columns = [d[0] for d in cur.description]
        out = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
        self.rows_returned += len(out)
        return out

    def one(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        found = self.rows(sql, params)
        return found[0] if found else None

    def scalar(self, sql: str, params: list[Any] | None = None) -> Any:
        self._record(sql)
        row = self._handle().execute(sql, params or []).fetchone()
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
    # Under the lock, like every other read. This one used a module-level
    # handle with no lock, so it could steal a concurrent request's result set:
    # the unlocked thread's `execute` overwrote the locked thread's, and the
    # locked thread's `fetchone()` then returned None - a TypeError on
    # `'NoneType' object is not subscriptable` raised inside a DIFFERENT
    # request's `_has`, from code that had been correct all along.
    with reading() as c:
        row = c.execute(
            """
            SELECT min(publish_date), max(publish_date) FROM fact_headline WHERE in_window
            """
        ).fetchone()
    if row and row[0] and row[1]:
        return str(row[0]), str(row[1])
    return "2015-06-30", "2020-06-30"


# ---------------------------------------------------------------------------
# The reader's global filter
# ---------------------------------------------------------------------------

#: ISO dates only. The value is checked against this and then converted to an
#: integer before it reaches a SQL string, so a reader-supplied parameter can
#: only ever contribute digits. FastAPI ignores query parameters an endpoint does
#: not declare, which is precisely why a filter the server does not understand
#: used to disappear without a word instead of being rejected.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Cube grain is single-dimension: `cube_month_topic` knows topics and has no
#: sector column, `cube_sector_month` knows sectors and has no topic column.
#: There is no cube carrying both, and building one is not possible from the
#: warehouse as it stands - `fact_headline` has a topic but NO sector, and a
#: headline only reaches a sector through `fact_news_stock_mention`, which is
#: sparse by construction and whose precision nobody has measured yet
#: (`reports/mention_audit_sample.csv` is still blank). So a sector filter
#: moves the sector-shaped figures and cannot move the topic-shaped ones, and
#: the response says which is which rather than letting a reader assume the
#: whole page narrowed.
TOPIC_DIMENSION = "topic"
SECTOR_DIMENSION = "sector"


def _date_int(value: str | None, field: str) -> int | None:
    """ISO `YYYY-MM-DD` to the INTEGER `date_key` the cubes are keyed on.

    `2015-06-30` becomes `20150630`. Rejects anything else with a 422 rather
    than passing it through, because a silently-ignored filter is the single
    most misleading thing this API could do.
    """
    if not value:
        return None
    if not _ISO_DATE.match(value):
        raise HTTPException(
            status_code=422,
            detail=f"{field}={value!r} is not a date. Use YYYY-MM-DD.",
        )
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:  # e.g. 2015-02-30 passes the regex but is not a date
        raise HTTPException(
            status_code=422, detail=f"{field}={value!r} is not a real date."
        ) from exc
    return parsed.year * 10000 + parsed.month * 100 + parsed.day


def _csv(value: str | None) -> tuple[str, ...]:
    """A comma-separated query parameter as a tuple, blanks dropped."""
    return tuple(p.strip() for p in (value or "").split(",") if p.strip())


@dataclass(frozen=True)
class Scope:
    """One request's filter, parsed once and expressed as SQL.

    Holds no connection and runs no query, so it can be built from query
    parameters before any handle is taken. Every fragment method returns a
    predicate beginning with `AND`, or an empty string when that dimension is
    not filtered - so a query with no filter reads exactly as it did before,
    rather than gaining `AND 1=1` noise in the "how this was computed" drawer.
    """

    lo: int | None = None
    hi: int | None = None
    sectors: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    include_market: bool = False

    @property
    def active(self) -> bool:
        return bool(self.lo or self.hi or self.sectors or self.topics or self.include_market)

    @property
    def count(self) -> int:
        return (
            (1 if self.lo else 0)
            + (1 if self.hi else 0)
            + len(self.sectors)
            + len(self.topics)
            + (1 if self.include_market else 0)
        )

    # -- SQL fragments ----------------------------------------------------
    # Names come from the reader, so they are never interpolated directly:
    # each is bound through a dimension table by a parameter placeholder.

    def day(self, alias: str = "", column: str = "date_key") -> str:
        """`cube_day_topic`, `fact_news_sector_daily`, `fact_stock_daily`."""
        prefix = f"{alias}." if alias else ""
        parts = []
        if self.lo is not None:
            parts.append(f"{prefix}{column} >= {self.lo}")
        if self.hi is not None:
            parts.append(f"{prefix}{column} <= {self.hi}")
        return " AND " + " AND ".join(parts) if parts else ""

    def month(self, alias: str = "") -> str:
        """`cube_month_topic`, which keys on the INTEGER `year_month_key`."""
        prefix = f"{alias}." if alias else ""
        parts = []
        if self.lo is not None:
            parts.append(f"{prefix}year_month_key >= {self.lo // 100}")
        if self.hi is not None:
            parts.append(f"{prefix}year_month_key <= {self.hi // 100}")
        return " AND " + " AND ".join(parts) if parts else ""

    def sector_month(self, alias: str = "") -> str:
        """`cube_sector_month` carries `year` and `month` separately."""
        prefix = f"{alias}." if alias else ""
        ym = f"({prefix}year * 100 + {prefix}month)"
        parts = []
        if self.lo is not None:
            parts.append(f"{ym} >= {self.lo // 100}")
        if self.hi is not None:
            parts.append(f"{ym} <= {self.hi // 100}")
        return " AND " + " AND ".join(parts) if parts else ""

    def topic(self, alias: str = "") -> tuple[str, list[Any]]:
        """Filter by topic NAME, resolved through `dim_topic`. Returns (sql, params)."""
        if not self.topics:
            return "", []
        prefix = f"{alias}." if alias else ""
        holes = ", ".join("?" for _ in self.topics)
        return (
            f" AND {prefix}topic_key IN "
            f"(SELECT topic_key FROM dim_topic WHERE topic_name IN ({holes}))",
            list(self.topics),
        )

    def sector(self, alias: str = "", column: str = "sector_key") -> tuple[str, list[Any]]:
        """Filter by sector NAME, resolved through `dim_sector`."""
        if not self.sectors:
            return "", []
        prefix = f"{alias}." if alias else ""
        holes = ", ".join("?" for _ in self.sectors)
        return (
            f" AND {prefix}{column} IN "
            f"(SELECT sector_key FROM dim_sector WHERE sector_name IN ({holes}))",
            list(self.sectors),
        )

    def sector_name(self, alias: str = "", column: str = "sector_name") -> tuple[str, list[Any]]:
        """Filter a table that stores the sector NAME rather than the key.

        `mining_surprise_days` is one - it was written by the mining stage from
        joined text, not built from the dimension, so comparing its
        `sector_name` against `sector_key` would silently match nothing. A
        filter that returns no rows without erroring is the failure mode worth
        writing a second method to avoid.
        """
        if not self.sectors:
            return "", []
        prefix = f"{alias}." if alias else ""
        holes = ", ".join("?" for _ in self.sectors)
        return (
            f" AND {prefix}{column} IN "
            f"(SELECT sector_name FROM dim_sector WHERE sector_name IN ({holes}))",
            list(self.sectors),
        )

    def news_column(self, base: str = "news_count") -> str:
        """Which news column to read, given the market-report toggle.

        The two tables that split market-report headlines out carry both
        `news_count` and `news_count_non_market`, so the toggle is a choice of
        column rather than a predicate. Getting this wrong - leaving one of them
        hard-coded - is what made the checkbox do nothing at all while still
        appearing to work.
        """
        return base if self.include_market else f"{base}_non_market"

    # `cube_month_topic` has no market-report split: it was built before the
    # `is_market_report` flag existed, so no column here can honour the toggle.
    # `note()` says so rather than letting the checkbox imply it did.

    def clause(self, *pieces: tuple[str, list[Any]]) -> tuple[str, list[Any]]:
        """Join predicates into a complete `WHERE`, or nothing at all.

        Every fragment above returns a leading `AND` so that it can be dropped
        into an existing predicate. That is convenient until a call site has no
        `WHERE` yet, where the fragment lands directly after the table name and
        DuckDB raises a ParserException on a bare `AND`. This assembles them
        into `WHERE a AND b`, gathering the bound parameters in the same order,
        so no call site has to remember which of the two shapes it is in.

        Empty fragments are dropped, so a query with no filter still reads
        exactly as it did before rather than gaining `WHERE TRUE`.
        """
        parts: list[str] = []
        params: list[Any] = []
        for sql, args in pieces:
            body = " ".join(sql.split())
            if not body:
                continue
            if body.upper().startswith("AND "):
                body = body[4:]
            parts.append(body)
            params.extend(args)
        return (" WHERE " + " AND ".join(parts) if parts else ""), params

    # -- reporting --------------------------------------------------------

    def as_json(self) -> dict[str, Any]:
        return {
            "from": f"{self.lo // 10000:04d}-{self.lo // 100 % 100:02d}-{self.lo % 100:02d}" if self.lo else None,
            "to": f"{self.hi // 10000:04d}-{self.hi // 100 % 100:02d}-{self.hi % 100:02d}" if self.hi else None,
            "sectors": list(self.sectors),
            "topics": list(self.topics),
            "include_market": self.include_market,
            "count": self.count,
        }

    def note(self, *, topic_shaped: bool, sector_shaped: bool) -> str:
        """One sentence saying exactly what the current filter moved.

        Built here rather than in the front end so that the claim and the query
        that backs it are written in the same place. The awkward case is the one
        worth being explicit about: with sectors selected, the topic-shaped
        figures on the page are NOT narrower, and a reader who assumes they are
        has been told something false by a control they did use.
        """
        if not self.active:
            return "No filter applied. Every figure covers the whole corpus window."
        bits: list[str] = []
        if self.lo or self.hi:
            window = self.as_json()
            lo, hi = window["from"] or "the start", window["to"] or "the end"
            bits.append(f"date range {lo} to {hi}")
        if self.topics:
            bits.append(f"topics: {', '.join(self.topics)}")
        if self.sectors:
            bits.append(f"sectors: {', '.join(self.sectors)}")
        if self.include_market:
            bits.append("market-report headlines included")

        head = "Filtered by " + "; ".join(bits) + "."
        limits: list[str] = []
        if self.sectors and topic_shaped:
            limits.append(
                "headline volume, sentiment and the risk-signal rate are counted "
                "by TOPIC and are not narrowed by this sector selection - a "
                "headline has no sector in this warehouse unless it names a "
                "listed company"
            )
        if self.topics and sector_shaped:
            limits.append(
                "sector figures are not narrowed by this topic selection - sector "
                "news volume comes from a different table that carries no topic"
            )
        if self.include_market:
            limits.append(
                "the market-report toggle applies to the sector and tape figures, "
                "which carry the split; the topic cubes predate that flag and "
                "cannot exclude them"
            )
        if limits:
            head += " Not affected: " + "; ".join(limits) + "."
        return head


#: Valid sector and topic names, read once. `dim_sector` and `dim_topic` are
#: static dimensions that no command rewrites at runtime, so a process-lifetime
#: cache cannot go stale in a way that matters - and if the warehouse is
#: rebuilt in place the server is restarted with it.
_NAMES: dict[str, tuple[str, ...]] = {}


def _valid_names(kind: str) -> tuple[str, ...]:
    if kind not in _NAMES:
        column, table = ("sector_name", "dim_sector") if kind == "sectors" else ("topic_name", "dim_topic")
        with reading() as c:
            rows = c.execute(f"SELECT {column} FROM {table} ORDER BY 1").fetchall()
        _NAMES[kind] = tuple(str(r[0]) for r in rows)
    return _NAMES[kind]


def _reject_unknown(names: tuple[str, ...], kind: str) -> None:
    """Refuse a filter value the dimension does not contain.

    Without this, `?sectors=Banknngs` returns an empty treemap, a headline count
    of zero and a mood computed over no rows - all of which render perfectly,
    look like a finding, and are entirely an artefact of a typo. A 422 with the
    valid names is the only honest answer.
    """
    if not names:
        return
    known = _valid_names(kind)
    unknown = [n for n in names if n not in known]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown {kind}: {', '.join(repr(u) for u in unknown)}. "
                f"Valid {kind} are: {', '.join(known)}."
            ),
        )


def scope_params(
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    sectors: str = Query(""),
    topics: str = Query(""),
    include_market: int = Query(0, ge=0, le=1),
) -> Scope:
    """The filter, as a FastAPI dependency.

    Declaring it here rather than repeating four parameters on every signature
    is what stops the endpoint set from drifting apart again - the reason the
    filter bar did nothing was that four endpoints each forgot to declare them.
    """
    scope = Scope(
        lo=_date_int(date_from, "from"),
        hi=_date_int(date_to, "to"),
        sectors=_csv(sectors),
        topics=_csv(topics),
        include_market=bool(include_market),
    )
    _reject_unknown(scope.sectors, "sectors")
    _reject_unknown(scope.topics, "topics")
    if scope.lo and scope.hi and scope.lo > scope.hi:
        raise HTTPException(
            status_code=422,
            detail=f"from={date_from} is after to={date_to}; there is no window between them.",
        )
    return scope


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
# /ui/cube - the slicing engine behind Cube Studio
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Stock 360 and Event Lab
# ---------------------------------------------------------------------------


#: The six axes of the peer radar, in the order they are drawn. Each is a
#: standardised feature from `mining_stock_clusters`, so "this stock versus its
#: cluster" is a comparison of like with like rather than of rupees with percent.
RADAR_AXES: tuple[tuple[str, str, str], ...] = (
    ("ann_volatility", "Annualised volatility", "Higher means a wider spread of daily moves."),
    ("beta", "Beta vs the Nifty", "1.0 moves with the index; above 1 amplifies it."),
    ("max_drawdown", "Max drawdown", "The worst peak-to-trough fall over the window, as a fraction."),
    ("mentions_per_1000_days", "Mention rate", "Headlines naming this stock per 1,000 trading days."),
    ("mention_sentiment", "Mention sentiment", "Mean sentiment of the headlines that named it."),
    ("news_sensitivity", "News sensitivity", "How strongly its returns track its own news volume."),
)


@router.get("/ui/stocks/{symbol}")
def stock_360(symbol: str, include_market: int = Query(0, ge=0, le=1)) -> JSONResponse:
    """One company, in one call.

    The page needs a candlestick, a rebase against the Nifty, a mention series,
    six peer metrics against their cluster average, this stock's reaction to
    every studied event, and its cluster neighbours. Six round trips to render
    one screen is six chances to show a half-loaded panel, so the series comes
    from the cube at daily grain rather than from `fact_headline`.

    **The series is the window, not the last 400 sessions.** `/stocks/{symbol}`
    returns the most recent 400 rows, which is right for a sparkline and wrong
    for a chart the reader is about to zoom across five years.
    """
    started = time.perf_counter()
    if not _has("fact_stock_daily"):
        return _not_built("fact_stock_daily", "python -m dwm ingest --dataset stocks")
    sym = symbol.strip().upper()
    with reading() as c:
        q = _Queries(c)
        profile = q.one(
            """
            SELECT d.symbol, d.company_name, d.sector_key, s.sector_name AS sector,
                   d.isin, d.is_survivor, d.price_source, d.first_trade_date,
                   d.last_trade_date
            FROM dim_stock d
            LEFT JOIN dim_sector s ON s.sector_key = d.sector_key
            WHERE d.symbol = ?
            """,
            [sym],
        )
        if profile is None:
            return JSONResponse(
                status_code=404,
                content={
                    "detail": (
                        f"No company with the symbol {sym!r}. Symbols come from the "
                        "NSE constituent list, so a company that has since been "
                        "removed from the index will not be here."
                    )
                },
                headers={"Cache-Control": "no-store"},
            )

        # Mentions are joined on `effective_trading_date_key`, never on the
        # calendar date (rule 12): a headline on a Sunday belongs to the next
        # session, and plotting it on Sunday would put activity on a day with no
        # price bar next to it.
        series = q.rows(
            """
            WITH m AS (
                SELECT effective_trading_date_key AS dk, count(*) AS mentions
                FROM fact_news_stock_mention
                WHERE stock_key = (SELECT stock_key FROM dim_stock WHERE symbol = ?)
                  AND (? = 1 OR NOT is_market_report)
                GROUP BY 1
            ),
            px AS (
                SELECT p.date_key, p.trade_date, p.open, p.high, p.low, p.close,
                       p.volume, p.return_pct, p.volatility_20d
                FROM fact_stock_daily p
                JOIN dim_date dd ON dd.date_key = p.date_key
                WHERE p.stock_key = (SELECT stock_key FROM dim_stock WHERE symbol = ?)
                  AND NOT p.is_outlier
                  AND dd.full_date BETWEEN ? AND ?
            ),
            idx AS (
                SELECT date_key, close AS nifty_close
                FROM fact_market_daily WHERE in_window
            ),
            rebased AS (
                SELECT *,
                       100.0 * close / first_value(close) OVER (ORDER BY trade_date) AS stock_rebased,
                       100.0 * nifty_close
                         / first_value(nifty_close) OVER (ORDER BY trade_date) AS nifty_rebased
                FROM px JOIN idx USING (date_key)
            )
            SELECT r.trade_date AS date, r.open, r.high, r.low, r.close,
                   r.volume, r.return_pct, r.volatility_20d,
                   coalesce(m.mentions, 0) AS mentions,
                   r.nifty_close, r.stock_rebased, r.nifty_rebased
            FROM rebased r LEFT JOIN m ON m.dk = r.date_key
            ORDER BY r.trade_date
            """,
            [sym, include_market, sym, *_window()],
        )

        mine = q.one(
            """
            SELECT cluster_id, ann_volatility, beta, max_drawdown,
                   mentions_per_1000_days, mention_sentiment, news_sensitivity
            FROM mining_stock_clusters WHERE symbol = ?
            """,
            [sym],
        )
        cluster_id = mine["cluster_id"] if mine else None
        axes: list[dict[str, Any]] = []
        peers: list[dict[str, Any]] = []
        if mine is not None:
            cols = ", ".join(a[0] for a in RADAR_AXES)
            cluster_avg = q.one(
                f"""
                SELECT {cols}
                FROM mining_stock_clusters
                WHERE cluster_id = ? AND symbol <> ?
                """,
                [cluster_id, sym],
            ) or {}
            # Per-axis population bounds, computed one axis at a time. A radar
            # scaled by a single min/max across all six axes is unreadable:
            # mention rate runs to 330 while news sensitivity sits near zero, so
            # four axes collapse onto the centre and the polygon becomes a
            # vertical spike that says nothing. Each axis is scaled against its
            # own spread, so "further out" means the same thing on every arm.
            population = q.rows(
                f"""
                SELECT symbol, {cols} FROM mining_stock_clusters
                """
            )
            for key, label, blurb in RADAR_AXES:
                values = [
                    float(r[key]) for r in population
                    if isinstance(r.get(key), (int, float))
                ]
                value = mine.get(key)
                lo = min(values) if values else 0.0
                hi = max(values) if values else 1.0
                below = sum(1 for v in values if isinstance(value, (int, float)) and v < value)
                axes.append(
                    {
                        "key": key,
                        "label": label,
                        "description": blurb,
                        "value": _round(value),
                        "cluster_mean": _round(cluster_avg.get(key)),
                        "pop_min": _round(lo),
                        "pop_max": _round(hi),
                        # A true rank, not a rescaled number: the share of the
                        # set this stock sits above on this axis.
                        "percentile": round(100.0 * below / len(values), 1) if values else None,
                    }
                )
            peers = q.rows(
                """
                SELECT symbol, sector_name AS sector, round(beta, 3) AS beta,
                       round(ann_volatility, 2) AS ann_volatility
                FROM mining_stock_clusters
                WHERE cluster_id = ? AND symbol <> ?
                ORDER BY symbol LIMIT 12
                """,
                [cluster_id, sym],
            )

        reactions = q.rows(
            """
            SELECT e.event_key, e.event_label AS label, e.event_type AS type,
                   e.start_date AS date,
                   round(m.car_0_5, 4) AS car_0_5,
                   round(m.car_0_1, 4) AS car_0_1,
                   round(m.beta, 3) AS beta,
                   m.est_days
            FROM mining_event_study m
            JOIN dim_event e ON e.event_key = m.event_key
            JOIN dim_stock d ON d.stock_key = m.stock_key
            WHERE d.symbol = ? AND m.valid AND m.car_0_5 IS NOT NULL
            ORDER BY e.start_date
            """,
            [sym],
        )
        # Counted HERE, inside the `with reading()` block. It used to be a
        # helper called after the block had exited, which meant the lock was
        # already released: this thread's `execute` overwrote whatever result
        # set another request had just prepared, and that request failed with a
        # 404 for a company or an event that plainly exists. The symptom named
        # the wrong thing in every case - this was invisible to a test that
        # hammered only module-level handles, because the handle was local.
        outliers_excluded = q.scalar(
            """
            SELECT count(*) FROM fact_stock_daily p
            JOIN dim_stock d ON d.stock_key = p.stock_key
            WHERE d.symbol = ? AND p.is_outlier
            """,
            [sym],
        )

    last = series[-1] if series else None
    prev = series[-2] if len(series) > 1 else None
    body = {
        "profile": profile,
        "last": last,
        # Named for what it holds. The first version called this `change_pct`
        # while putting a rupee difference in it, and the percentage the header
        # actually wanted was already in `last.return_pct`.
        "change_abs": round(last["close"] - prev["close"], 4) if last and prev else None,
        "series": series,
        "cluster": {"cluster_id": cluster_id, "axes": axes},
        "peers": peers,
        "reactions": reactions,
        "totals": {
            "sessions": len(series),
            "mentions": sum(int(r["mentions"] or 0) for r in series),
            "outliers_excluded": outliers_excluded,
        },
    }
    return envelope(
        "Fact constellation",
        body,
        q,
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            "A mention means the company name appeared in the headline, not that the "
            "story was about it; alias matching is noisy and ambiguous aliases are "
            "flagged in fact_news_stock_mention. The precision of that matching is not "
            "measured: reports/mention_audit_sample.csv needs a human to fill in "
            "`is_correct` before any precision figure can be published, and until then "
            "the mention counts on this page are a coverage measure and nothing more. "
            "Returns are percentage points from adjusted closes, so dividends do not "
            "read as losses. Daily moves beyond +/-25% are excluded here as outliers "
            "and kept in fact_stock_daily. The rebased series starts at 100 on the "
            "first session of the window, so it says how the stock moved relative to "
            "the index, not how much it is worth."
        ),
    )


def _round(value: Any, digits: int = 4) -> float | None:
    return round(float(value), digits) if isinstance(value, (int, float)) else None


@router.get("/ui/stocks/{symbol}/headlines")
def stock_headlines(
    symbol: str,
    limit: int = Query(50, ge=1, le=50),
    include_market: int = Query(0, ge=0, le=1),
) -> JSONResponse:
    """The headlines that named a company, newest first, capped at 50.

    Rule 21's cap is the API's. The page passes the visible date range, so the
    timeline follows the chart's zoom rather than fetching 4,686 rows and
    filtering in the browser.
    """
    started = time.perf_counter()
    if not _has("fact_news_stock_mention"):
        return _not_built("fact_news_stock_mention", "python -m dwm link")
    with reading() as c:
        q = _Queries(c)
        rows = q.rows(
            """
            SELECT h.headline_id, h.publish_date, tp.topic_name AS topic,
                   h.headline_text, h.sentiment_compound, h.sensational_score,
                   h.is_sensational, h.is_risk_signal,
                   m.matched_alias, m.is_ambiguous_alias
            FROM fact_news_stock_mention m
            JOIN fact_headline h ON h.headline_id = m.headline_id
            JOIN dim_topic tp ON tp.topic_key = h.topic_key
            JOIN dim_stock d ON d.stock_key = m.stock_key
            WHERE d.symbol = ? AND h.in_window AND (? = 1 OR NOT m.is_market_report)
            ORDER BY h.publish_date DESC, h.sensational_score DESC
            LIMIT ?
            """,
            [symbol.strip().upper(), include_market, limit],
        )
    return envelope(
        "Text mining",
        rows,
        q,
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            "Named by name, not by topic. A company can be mentioned in passing in a "
            "headline about something else entirely, which is why an ambiguous alias "
            "is flagged rather than resolved. Precision of the name matching is not "
            "measured: reports/mention_audit_sample.csv needs a human to fill in "
            "`is_correct` before any precision figure can be published."
        ),
    )


@router.get("/ui/events")
def event_timeline() -> JSONResponse:
    """Every event in the window, in order, with its type for the timeline."""
    started = time.perf_counter()
    if not _has("dim_event"):
        return _not_built("dim_event", "python -m dwm build")
    with reading() as c:
        q = _Queries(c)
        rows = q.rows(
            """
            SELECT e.event_key, e.event_label AS label, e.event_type AS type,
                   e.start_date AS date,
                   (SELECT count(*) FROM mining_event_study m
                     WHERE m.event_key = e.event_key AND m.valid) AS stocks_studied,
                   (SELECT count(*) FROM mining_event_study m
                     WHERE m.event_key = e.event_key AND NOT m.valid) AS stocks_excluded
            FROM dim_event e
            ORDER BY e.start_date
            """
        )
    return envelope(
        "Event study, market model",
        {"events": rows},
        q,
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            "Event dates were entered by hand from config/events_v2.yaml, not "
            "scraped from a news feed, and the event day is mapped to the next "
            "trading session - so a Sunday budget lands on the Monday bar. Two "
            "events close together cannot be told apart in a five-day window, and "
            "the page says so when that happens."
        ),
    )


@router.get("/ui/events/{event_key}")
def event_detail(event_key: int, include_market: int = Query(0, ge=0, le=1)) -> JSONResponse:
    """One event: how sectors reacted, which stocks moved, and what was said.

    The news-volume window is ten trading days either side of the mapped event
    session, joined on `effective_trading_date_key` so a headline printed over
    the weekend sits on the Monday bar rather than on a gap.
    """
    started = time.perf_counter()
    if not _has("mining_event_study"):
        return _not_built("mining_event_study", "python -m dwm mine --op events")
    with reading() as c:
        q = _Queries(c)
        event = q.one(
            """
            SELECT e.event_key, e.event_label AS label, e.event_type AS type,
                   e.start_date AS date,
                   (SELECT min(m.event_day) FROM mining_event_study m
                     WHERE m.event_key = e.event_key AND m.valid) AS session
            FROM dim_event e WHERE e.event_key = ?
            """,
            [event_key],
        )
        if event is None:
            return JSONResponse(
                status_code=404,
                content={"detail": f"No event with key {event_key}."},
                headers={"Cache-Control": "no-store"},
            )

        by_sector = q.rows(
            """
            SELECT coalesce(s.sector_name, 'Unclassified') AS sector,
                   count(*) AS n_stocks,
                   round(avg(m.car_0_5), 4) AS mean_car_0_5,
                   round(avg(m.car_0_1), 4) AS mean_car_0_1,
                   round(avg(m.car_m1_5), 4) AS mean_car_m1_5,
                   round(avg(m.beta), 3) AS mean_beta
            FROM mining_event_study m
            LEFT JOIN dim_sector s ON s.sector_key = m.sector_key
            WHERE m.event_key = ? AND m.valid AND m.car_0_5 IS NOT NULL
            GROUP BY 1 ORDER BY abs(avg(m.car_0_5)) DESC
            """,
            [event_key],
        )
        # The whole valid set, not the top 20 by |CAR|: a leaderboard that shows
        # only the extremes cannot show that 40 stocks did not move.
        by_stock = q.rows(
            """
            SELECT d.symbol, coalesce(s.sector_name, 'Unclassified') AS sector,
                   round(m.car_0_5, 4) AS car_0_5,
                   round(m.car_0_1, 4) AS car_0_1,
                   round(m.beta, 3) AS beta,
                   round(m.r_squared, 3) AS r_squared,
                   m.est_days
            FROM mining_event_study m
            JOIN dim_stock d ON d.stock_key = m.stock_key
            LEFT JOIN dim_sector s ON s.sector_key = m.sector_key
            WHERE m.event_key = ? AND m.valid AND m.car_0_5 IS NOT NULL
            ORDER BY m.car_0_5 DESC
            """,
            [event_key],
        )
        excluded = q.rows(
            """
            SELECT d.symbol, m.reason, m.est_days
            FROM mining_event_study m JOIN dim_stock d ON d.stock_key = m.stock_key
            WHERE m.event_key = ? AND NOT m.valid ORDER BY d.symbol
            """,
            [event_key],
        )
        # `event_day` is a DATE and `date_key` is a YYYYMMDD integer. The
        # mapping is a format, not a cast: `CAST(date AS BIGINT)` yields days
        # since the epoch and silently matches nothing. Resolved once here
        # instead of repeated as a correlated subquery in three statements, two
        # of which had the alias out of scope anyway.
        session = event.get("session")
        session_key = int(session.strftime("%Y%m%d")) if hasattr(session, "strftime") else None

        around = q.rows(
            # The offset is a row number's distance from the event session.
            # Subtracting two row_number()s over opposite orderings - the
            # obvious way to centre a window - yields only values of one parity,
            # so the first version returned days -9, -7, -5 and silently dropped
            # every other session.
            """
            WITH anchor AS (
                SELECT CAST(full_date AS DATE) AS d FROM dim_date WHERE date_key = ?
            ),
            ordered AS (
                SELECT date_key, trade_date,
                       row_number() OVER (ORDER BY trade_date) AS rn
                FROM fact_market_daily WHERE in_window
            ),
            anchor_rn AS (
                SELECT o.rn FROM ordered o, anchor a WHERE o.trade_date = a.d
            ),
            sessions AS (
                SELECT o.date_key, o.trade_date, o.rn - ar.rn AS offset
                FROM ordered o CROSS JOIN anchor_rn ar
                WHERE abs(o.rn - ar.rn) <= 10
            )
            SELECT s.offset, s.trade_date AS date,
                   coalesce(sum(f.news_count_non_market), 0) AS news
            FROM sessions s
            LEFT JOIN fact_news_sector_daily f
                   ON f.effective_trading_date_key = s.date_key
            GROUP BY 1, 2 ORDER BY s.offset
            """,
            [session_key],
        )
        headlines = q.rows(
            """
            SELECT h.headline_id, h.publish_date, tp.topic_name AS topic,
                   h.headline_text, h.sensational_score, h.is_market_report
            FROM fact_headline h
            JOIN dim_topic tp ON tp.topic_key = h.topic_key
            WHERE h.in_window AND h.date_key = ? AND (? = 1 OR NOT h.is_market_report)
            ORDER BY h.sensational_score DESC LIMIT 50
            """,
            [session_key, include_market],
        )
        # Two events within four days of each other cannot be told apart in a
        # five-day window, so the page is told which ones overlap.
        nearby = q.rows(
            """
            SELECT e.event_key, e.event_label AS label, e.start_date AS date,
                   abs(date_diff('day', e.start_date, a.d)) AS days_apart
            FROM dim_event e,
                 (SELECT CAST(full_date AS DATE) AS d FROM dim_date WHERE date_key = ?) a
            WHERE e.event_key <> ? AND abs(date_diff('day', e.start_date, a.d)) <= 4
            ORDER BY e.start_date
            """,
            [session_key, event_key],
        )

    body = {
        "event": event,
        "by_sector": by_sector,
        "by_stock": by_stock,
        "excluded": excluded,
        "news_around": around,
        "headlines": headlines,
        "nearby_events": nearby,
    }
    return envelope(
        "Event study, market model",
        body,
        q,
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            "Cumulative abnormal return is the move beyond what the market model "
            "explains, measured over the sessions after the event. It coincides "
            "with the event; it does not show the event caused it. Other news on "
            "the same day produces the same number, and two events within four days "
            "of each other cannot be separated in a five-day window - the page "
            "warns when that is the case. Stocks in `excluded` had too little "
            "pre-event history to fit a model and are kept out of every average."
        ),
    )


# ---------------------------------------------------------------------------
# Mining Lab and Headline Lab
# ---------------------------------------------------------------------------


@router.get("/ui/mining/clusters")
def mining_clusters() -> JSONResponse:
    """The PCA scatter, plus a profile card per cluster.

    **The cluster names are derived, not given.** `mining_stock_clusters` has
    numeric ids and no labels, so a page that prints "Cluster 2" makes the
    reader do the work of working out what it means. The name comes from the
    cluster's own medians - how volatile, how market-linked, how much the news
    moves it - so it describes the members and can be wrong in the way a
    measurement can be wrong, rather than being a name someone chose.
    """
    started = time.perf_counter()
    if not _has("mining_stock_clusters"):
        return _not_built("mining_stock_clusters", "python -m dwm mine --op stockclusters")
    cluster_run = _mining_report().get("rq4_clusters") or {}
    with reading() as c:
        q = _Queries(c)
        points = q.rows(
            """
            SELECT symbol, sector_name AS sector, cluster_id,
                   round(ann_volatility, 3) AS ann_volatility,
                   round(beta, 4) AS beta, round(max_drawdown, 4) AS max_drawdown,
                   round(mentions_per_1000_days, 2) AS mentions_per_1000_days,
                   round(mention_sentiment, 4) AS mention_sentiment,
                   round(news_sensitivity, 4) AS news_sensitivity,
                   round(pc1, 4) AS pc1, round(pc2, 4) AS pc2,
                   n_sessions
            FROM mining_stock_clusters
            ORDER BY symbol
            """
        )
        profiles = q.rows(
            """
            SELECT cluster_id,
                   count(*) AS n_stocks,
                   round(median(ann_volatility), 2) AS median_volatility,
                   round(median(beta), 3) AS median_beta,
                   round(median(max_drawdown), 3) AS median_drawdown,
                   round(median(mentions_per_1000_days), 1) AS median_mentions,
                   round(median(mention_sentiment), 4) AS median_sentiment,
                   round(median(news_sensitivity), 4) AS median_news_sensitivity,
                   string_agg(DISTINCT sector_name, ', ') AS sectors
            FROM mining_stock_clusters
            WHERE cluster_id IS NOT NULL
            GROUP BY 1 ORDER BY 1
            """
        )

    # Name each cluster by its RANK on the two axes that carry the meaning.
    #
    # Two earlier attempts failed the same way. Thresholding called both of the
    # volatile clusters "high-beta movers" - they clear the same cut, so the
    # page showed two identical names over two visibly different groups. Giving
    # the four quadrants four names then failed too, because three clusters
    # cannot occupy four quadrants distinctly.
    #
    # A rank pair is always distinct: "most volatile · mid market-linked" is not
    # the same string as "least volatile · least market-linked", whatever the
    # count. And it describes rather than judges - it says where the cluster sits
    # among its peers, which is checkable, instead of claiming a character.
    by_vol = sorted(profiles, key=lambda p: p["median_volatility"] or 0)
    by_beta = sorted(profiles, key=lambda p: p["median_beta"] or 0)
    beta_rank = {id(p): i for i, p in enumerate(by_beta)}
    n = len(profiles)

    def rank_term(rank: int, high: str, mid: str, low: str) -> str:
        if n == 1:
            return mid
        if rank == n - 1:
            return high
        if rank == 0:
            return low
        return mid

    for profile in profiles:
        vol_rank = by_vol.index(profile)
        profile["name"] = " · ".join(
            (
                rank_term(vol_rank, "most volatile", "mid volatility", "least volatile"),
                rank_term(
                    beta_rank[id(profile)],
                    "most market-linked",
                    "mid market-linked",
                    "least market-linked",
                ),
            )
        )
        profile["auto_named"] = True
        profile["most_volatile"] = vol_rank == n - 1
        profile["least_volatile"] = vol_rank == 0
        profile["most_market_linked"] = beta_rank[id(profile)] == n - 1

    return envelope(
        "Cluster analysis",
        {
            "points": points,
            "profiles": profiles,
            # The silhouette and, more importantly, its shape: 0.2582 is a
            # decent-looking number until you know one cluster holds 94.9% of the
            # sample, at which point the score describes a small well-separated
            # minority rather than a well-clustered corpus. Both travel together.
            "silhouette": _round(cluster_run.get("silhouette")),
            "silhouette_is_strong": cluster_run.get("silhouette_is_strong"),
            "dominant_cluster_share": _round(cluster_run.get("dominant_cluster_share"), 4),
            "concentration_note": cluster_run.get("concentration_note"),
            "chosen_k": cluster_run.get("chosen_k"),
            "k_note": cluster_run.get("k_note"),
            "cluster_note": cluster_run.get("note"),
        },
        q,
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            "Cluster names are computed from each cluster's medians and are this "
            "response's, not the project's. The clustering groups stocks by trading "
            "behaviour - volatility, beta, drawdown, how strongly returns follow "
            "their own news - so two companies in one cluster may have nothing to "
            "do with each other's businesses."
        ),
    )


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def _reports_path() -> Path:
    """The reports directory, located from the package rather than the cwd.

    The API is started from the repo root by `python -m dwm serve` but can be
    started from anywhere, and a path that resolves relative to the working
    directory makes a report silently missing on another machine.
    """
    from dwm.api.store import reports_dir

    return reports_dir()


#: Read once per process. `mining.json` is 600KB and four endpoints read it; a
#: file read per request would be the slowest thing in an otherwise fast page.
_MINING_REPORT: dict[str, Any] | None = None


def _mining_report() -> dict[str, Any]:
    """`reports/mining.json`, or `{}` if it is absent or unreadable.

    Mining results live in the warehouse where they are tables the dashboard
    queries, and in `mining.json` where they are things with no natural table -
    a confusion matrix, a silhouette with its shape, per-class precision. Both
    are the project's own output; neither is invented here.

    A missing report degrades to an empty dict so the endpoint still serves the
    half that does come from SQL, rather than 500-ing a page because one
    artefact is missing.
    """
    global _MINING_REPORT
    if _MINING_REPORT is None:
        path = _reports_path() / "mining.json"
        try:
            _MINING_REPORT = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("mining.json unavailable (%s); mining pages lose its halves", exc)
            _MINING_REPORT = {}
    return _MINING_REPORT


@router.get("/ui/mining/rules")
def mining_rules(
    min_support: float = Query(0.0, ge=0.0, le=1.0),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    min_lift: float = Query(1.0, ge=0.0),
) -> JSONResponse:
    """Association rules over market regimes, filtered in SQL.

    Lift is the filter that matters and it is not optional in practice: a rule
    with support 0.04 and confidence 0.55 can be entirely ordinary once you
    know its consequent is common. Lift above 1 is the only one of the three
    that says "these co-occur more than chance would predict".
    """
    started = time.perf_counter()
    if not _has("mining_market_rules"):
        return _not_built("mining_market_rules", "python -m dwm mine --op marketrules")
    with reading() as c:
        q = _Queries(c)
        rows = q.rows(
            """
            SELECT rule_id, sector_name, antecedents, consequents,
                   round(support, 5) AS support, round(confidence, 5) AS confidence,
                   round(lift, 4) AS lift, support_count, n_transactions
            FROM mining_market_rules
            WHERE support >= ? AND confidence >= ? AND lift >= ?
            ORDER BY lift DESC, support DESC
            LIMIT 200
            """,
            [min_support, min_confidence, min_lift],
        )
        total = q.scalar("SELECT count(*) FROM mining_market_rules") or 0

    return envelope(
        "Association rule mining",
        {
            "rules": rows,
            "matched": len(rows),
            "total": int(total),
            "thresholds": {
                "min_support": min_support,
                "min_confidence": min_confidence,
                "min_lift": min_lift,
            },
        },
        q,
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            "Lift is the share of this pattern's occurrences that exceed what the "
            "base rate of its consequent predicts. 1.0 means the two co-occur "
            "exactly as often as chance would give; above 1 means they belong "
            "together more than that. Association is not a mechanism and says "
            "nothing about which side causes which."
        ),
    )


@router.get("/ui/mining/models")
def mining_models() -> JSONResponse:
    """Both models, reported as they came out.

    The statement classifier separates REAL from FAKE on IFND and beats the
    majority baseline. The direction model predicts whether a stock is up
    tomorrow and **does not**, and this payload says so in a field the page
    cannot decide to soften: `beats_baseline` is the model's own verdict.

    Both are in one response on purpose. A page that shows the good result and
    a different page that shows the bad one is a reader who never learns that
    one of the project's two predictive claims failed.
    """
    started = time.perf_counter()
    if not _has("mining_direction_results"):
        return _not_built("mining_direction_results", "python -m dwm mine --op direction")

    # The classifier's per-class metrics, confusion matrix and top terms are
    # only in `mining.json`; the warehouse keeps just the direction model. A
    # missing report degrades to None rather than failing the endpoint, because
    # the direction half is still worth showing.
    classifier = _mining_report().get("rq7_classifier")

    with reading() as c:
        q = _Queries(c)
        direction = q.rows(
            """
            SELECT model, split, n, round(accuracy, 5) AS accuracy,
                   round(balanced_accuracy, 5) AS balanced_accuracy,
                   round(f1, 5) AS f1,
                   round(baseline_majority, 5) AS baseline_majority,
                   round(baseline_yesterday, 5) AS baseline_yesterday,
                   round(binom_p_vs_baseline, 5) AS p_vs_baseline,
                   round(precision_up, 5) AS precision_up,
                   round(recall_up, 5) AS recall_up,
                   notes
            FROM mining_direction_results
            ORDER BY split, accuracy DESC
            """
        )
    for row in direction:
        # The verdict is arithmetic, computed here rather than trusted from a
        # column, so it cannot drift from the numbers beside it.
        row["beats_baseline"] = (
            row["accuracy"] is not None
            and row["baseline_majority"] is not None
            and row["accuracy"] > row["baseline_majority"]
        )

    return envelope(
        "Classification",
        {
            "direction": direction,
            "classifier": classifier,
            "tree_rules_available": False,
        },
        q,
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            "The direction model's test accuracy is at or below the majority "
            "baseline, and the p-value is not significant: this project's news "
            "features did not predict tomorrow's direction. That is reported, not "
            "hidden. The classifier is the one result permitted to state an "
            "accuracy, because IFND is the only source with ground truth - and "
            "part of its Fake class is machine-generated augmentation, so even "
            "that number is an upper bound."
        ),
    )


@router.get("/ui/mining/co-mentions")
def mining_co_mentions(min_count: int = Query(2, ge=1, le=50)) -> JSONResponse:
    """Stocks that appear in the same headline.

    **This network is sparse and the page says so.** Only 60 distinct pairs
    exist in five years of one outlet's archive, which is a fact about stock-
    level mentions rather than about the companies involved: name matching
    across 47 tickers is noisy, and precision has not been measured because
    reports/mention_audit_sample.csv has no human labels yet.
    """
    started = time.perf_counter()
    if not _has("fact_news_stock_mention"):
        return _not_built("fact_news_stock_mention", "python -m dwm link")
    with reading() as c:
        q = _Queries(c)
        links = q.rows(
            """
            WITH pairs AS (
                SELECT a.stock_key AS a, b.stock_key AS b,
                       count(*) AS together,
                       max(h.publish_date) AS last_seen,
                       min(h.publish_date) AS first_seen
                FROM fact_news_stock_mention a
                JOIN fact_news_stock_mention b
                  ON a.headline_id = b.headline_id AND a.stock_key < b.stock_key
                JOIN fact_headline h ON h.headline_id = a.headline_id
                WHERE h.in_window
                GROUP BY 1, 2
            )
            SELECT da.symbol AS a_symbol, sec_a.sector_name AS a_sector, da.stock_key AS a,
                   db.symbol AS b_symbol, sec_b.sector_name AS b_sector, db.stock_key AS b,
                   p.together, p.first_seen, p.last_seen
            FROM pairs p
            JOIN dim_stock da ON da.stock_key = p.a
            JOIN dim_stock db ON db.stock_key = p.b
            LEFT JOIN dim_sector sec_a ON sec_a.sector_key = da.sector_key
            LEFT JOIN dim_sector sec_b ON sec_b.sector_key = db.sector_key
            WHERE p.together >= ?
            ORDER BY p.together DESC, da.symbol
            """,
            [min_count],
        )
        nodes = q.rows(
            """
            SELECT d.stock_key, d.symbol, s.sector_name AS sector,
                   count(*) AS mentions
            FROM fact_news_stock_mention m
            JOIN dim_stock d ON d.stock_key = m.stock_key
            LEFT JOIN dim_sector s ON s.sector_key = d.sector_key
            GROUP BY 1, 2, 3 ORDER BY 3, 2
            """
        )
        total_pairs = q.scalar(
            """
            SELECT count(*) FROM (
              SELECT 1 FROM fact_news_stock_mention a
              JOIN fact_news_stock_mention b
                ON a.headline_id = b.headline_id AND a.stock_key < b.stock_key
              GROUP BY a.stock_key, b.stock_key)
            """
        ) or 0

    return envelope(
        "Link mining",
        {"nodes": nodes, "links": links, "total_pairs": int(total_pairs)},
        q,
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            "An edge means both companies were named in the same headline - not "
            "that they are related, and not that one caused the other's move. "
            "The network is genuinely this sparse: the pair count is small because "
            "stock-level mentions are sparse, and the precision of the name "
            "matching has not been measured."
        ),
    )


@router.post("/ui/check")
def ui_check(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """Score a pasted headline.

    Delegates to the same `dwm.inference.checker.build_response` the v2 route
    uses, so the two cannot disagree about the same sentence - two
    implementations of one scorer is two answers, and only one of them would
    have been reviewed.
    """
    started = time.perf_counter()
    text = str(payload.get("headline") or "").strip()
    if not text:
        return JSONResponse(
            status_code=400,
            content={"detail": "Send {'headline': '...'} with a non-empty headline."},
            headers={"Cache-Control": "no-store"},
        )
    truncated = False
    if len(text) > 2000:
        text = text[:2000]
        truncated = True

    if not _has("fact_headline"):
        return _not_built("fact_headline", "python -m dwm build")
    from dwm.features.scoring import TextScorer
    from dwm.inference.checker import build_response

    with reading() as c:
        measures = TextScorer().measure(text)
        result = build_response(c, text, measures, truncated=truncated)
    return envelope(
        "Text mining",
        result,
        _Queries(None),
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            "This scores writing style, not truth. A sensational headline can be "
            "true and a plain one can be false, and nothing here fact-checks "
            "anything. The percentile says where this headline sits against five "
            "years of this outlet's own writing - not against the world."
        ),
    )


@router.get("/ui/check/samples")
def check_samples() -> JSONResponse:
    """Real headlines from the corpus, to try the scorer on.

    Real rather than invented, because a demo sentence tells a reader nothing
    about how the scorer behaves on actual TOI copy - and rule 1 means there is
    nothing in this repository that is not either real or from a fixture.
    """
    started = time.perf_counter()
    if not _has("fact_headline"):
        return _not_built("fact_headline", "python -m dwm build")
    with reading() as c:
        q = _Queries(c)
        rows = q.rows(
            """
            SELECT h.headline_text AS headline, h.publish_date,
                   round(h.sensational_score, 4) AS sensational_score,
                   tp.topic_name AS topic
            FROM fact_headline h
            JOIN dim_topic tp ON tp.topic_key = h.topic_key
            WHERE h.in_window AND length(h.headline_text) BETWEEN 40 AND 140
            ORDER BY h.sensational_score DESC
            LIMIT 3
            """
        )
        plain = q.rows(
            """
            SELECT h.headline_text AS headline, h.publish_date,
                   round(h.sensational_score, 4) AS sensational_score,
                   tp.topic_name AS topic
            FROM fact_headline h
            JOIN dim_topic tp ON tp.topic_key = h.topic_key
            WHERE h.in_window AND length(h.headline_text) BETWEEN 40 AND 140
            ORDER BY h.sensational_score ASC
            LIMIT 3
            """
        )
    return envelope(
        "Text mining",
        {"hype": rows, "plain": plain},
        q,
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            "The top three are the most sensational headlines in the corpus and "
            "the bottom three the plainest, by the same scorer the page runs. "
            "They are real sentences from this outlet, not invented examples."
        ),
    )


@router.get("/ui/cube/schema")
def cube_schema() -> JSONResponse:
    """The field shelf: what can be sliced, and what each thing means.

    Not cached. It is a few kilobytes of static descriptions, and a shelf that
    is a minute stale after a rebuild is worse than one that is not.
    """
    started = time.perf_counter()
    from dwm.olap.cube_studio import shelf

    payload = shelf()
    payload["operations"] = OPERATION_CHIPS
    return envelope(
        "Fact constellation",
        payload,
        _Queries(None),
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            "Dimensions name groups of rows; measures are numbers computed over "
            "each group. The two cubes have different grains and cannot be "
            "sliced together - the engine refuses that rather than multiplying "
            "headline counts by the number of stocks."
        ),
    )


#: The six OLAP operations, each in one line, each naming the zone it uses.
#: These are the classical terms, so they are explained rather than assumed -
#: but the explanation is a sentence, not a lecture, and it says what the
#: operation does to THIS view rather than what it does in general.
OPERATION_CHIPS: list[dict[str, str]] = [
    {
        "id": "slice",
        "label": "Slice",
        "blurb": "Fix one dimension member and look inside it.",
        "zones": ["filters"],
        "hint": "Put one value in Filters - a single year, or a single topic.",
    },
    {
        "id": "dice",
        "label": "Dice",
        "blurb": "Fix several dimension members at once.",
        "zones": ["filters"],
        "hint": "Choose several values on the same filter.",
    },
    {
        "id": "roll_up",
        "label": "Roll-up",
        "blurb": "Remove the finest dimension and aggregate what is left.",
        "zones": ["rows", "columns"],
        "hint": "Take Month off the axis and put Quarter or Year on it.",
    },
    {
        "id": "drill_down",
        "label": "Drill-down",
        "blurb": "Add a finer dimension to an existing view.",
        "zones": ["rows", "columns"],
        "hint": "Put Month on the axis beneath Year.",
    },
    {
        "id": "pivot",
        "label": "Pivot",
        "blurb": "One measure across two dimensions, as a grid.",
        "zones": ["rows", "columns", "values"],
        "hint": "Rows and Columns each carry a dimension; Values carries the measure.",
    },
    {
        "id": "cube",
        "label": "Cube",
        "blurb": "Every grouping of a small dimension set, in one pass.",
        "zones": ["rows", "columns", "values"],
        "hint": "Add a third axis, or read the totals row for the whole slice.",
    },
]


@router.post("/ui/cube/query")
def cube_query(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """Slice a cube by whatever the reader put in the zones.

    A POST because a spec is a structure - four lists and a filter object - and
    a GET would have to flatten it into query parameters and then parse it back
    into the same structure. Nothing here reaches SQL as text: field ids resolve
    to expressions written in `dwm.olap.cube_studio`, and filter values are
    bound parameters.
    """
    from dwm.olap.cube_studio import CUBES, CubeError, parse_spec, run

    started = time.perf_counter()
    cube_id = str(payload.get("cube") or "news")
    cube = CUBES.get(cube_id)
    if cube is not None and not _has("cube_day_topic" if cube.is_cube else "fact_stock_daily"):
        return _not_built(
            "cube_day_topic" if cube.is_cube else "fact_stock_daily",
            "python -m dwm build",
        )

    try:
        spec = parse_spec(payload)
    except CubeError as exc:
        return JSONResponse(
            status_code=exc.status,
            content={"detail": str(exc)},
            headers={"Cache-Control": "no-store"},
        )

    with reading() as c:
        result = run(c, spec, _window())

    # The engine recorded the two statements it ran. They go into `meta.sql` by
    # hand rather than through `_Queries`, because they were built from
    # whitelisted expressions and already normalised - running them through
    # `_Queries.rows` would run them a second time.
    notes = list(result.notes)
    body = {
        "cube": result.cube,
        "columns": result.columns,
        "rows": result.rows,
        "totals": result.totals,
        "truncated": any("Only the first" in n for n in notes),
        "spec": {
            "rows": spec.rows,
            "columns": spec.columns,
            "measures": spec.measures,
            "filters": spec.filters,
            "orderBy": spec.order_by,
            "descending": spec.descending,
        },
    }
    ms = (time.perf_counter() - started) * 1000.0
    meta: dict[str, Any] = {
        "concept": "Fact constellation",
        "ms": round(ms, 2),
        "rows": len(result.rows),
        "sql": "\n\n-- totals over the same filter, without the axes:\n\n".join(
            result.sql
        ),
    }
    if notes:
        meta["note"] = " ".join(notes)
    return JSONResponse(
        content={"data": _jsonable(body), "meta": _jsonable(meta)},
        headers={"Cache-Control": CACHE_CONTROL},
    )


@router.get("/ui/cube/headlines")
def cube_headlines(
    topic: str | None = None,
    year: int | None = None,
    month: str | None = None,
    limit: int = Query(50, ge=1, le=50),
) -> JSONResponse:
    """The headlines behind one cell, capped at 50.

    Rule 21 caps a headline list at 50 rows, and the cap is the API's, not the
    page's - a limit the browser can raise is a limit that will be raised.
    """
    started = time.perf_counter()
    if not _has("fact_headline"):
        return _not_built("fact_headline", "python -m dwm etl")

    predicates = ["h.in_window", "h.publish_date BETWEEN ? AND ?"]
    params: list[Any] = list(_window())
    if topic:
        predicates.append("tp.topic_name = ?")
        params.append(topic)
    if year:
        predicates.append("d.year_no = ?")
        params.append(year)
    if month:
        predicates.append("d.year_month = ?")
        params.append(month)

    with reading() as c:
        q = _Queries(c)
        rows = q.rows(
            f"""
            SELECT h.headline_id, h.publish_date, tp.topic_name AS topic,
                   h.headline_text,
                   h.sentiment_compound, h.sensational_score,
                   h.is_sensational, h.is_risk_signal, h.word_count
            FROM fact_headline h
            JOIN dim_date  d  ON d.date_key  = h.date_key
            JOIN dim_topic tp ON tp.topic_key = h.topic_key
            WHERE {" AND ".join(predicates)}
            ORDER BY h.sensational_score DESC, h.publish_date DESC
            LIMIT ?
            """,
            [*params, limit],
        )
    return envelope(
        "Text mining",
        rows,
        q,
        ms=(time.perf_counter() - started) * 1000.0,
        note=(
            "Ranked by sensationalism score, so this is the most striking slice "
            "of the cell and not a random or a representative one. The count "
            "behind the cell is the measure in the table; these 50 rows are a "
            "look, not a sample."
        ),
    )


# ---------------------------------------------------------------------------
# /ui/home - the whole first paint in one call
# ---------------------------------------------------------------------------


@router.get("/ui/home")
def home(scope: Scope = Depends(scope_params)) -> JSONResponse:
    """Cached wrapper. The work is in `_build_home`.

    The cache key carries the filter, because a cached unfiltered dashboard
    served to a reader who has narrowed to one sector is the exact failure this
    endpoint started with - the numbers simply did not move.
    """
    key = "/ui/home" + ("?" + json.dumps(scope.as_json(), sort_keys=True) if scope.active else "")
    return _cached_envelope(key, lambda: _build_home(scope))


def _build_home(scope: Scope) -> JSONResponse:
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

        # The filter's topic and sector predicates, assembled per table through
        # `clause`, which yields a complete WHERE (or nothing). Doing it this
        # way rather than by dropping bare `AND`s into each query is what stops
        # a table with no existing WHERE from failing to parse the moment a
        # filter arrives.
        month_topic_where, month_topic_args = scope.clause(
            (scope.month("m"), []), scope.topic("m")
        )
        day_topic_where, day_topic_args = scope.clause(
            (scope.day("c"), []), scope.topic("c")
        )
        sector_day_where, sector_day_args = scope.clause(
            (scope.day("f"), []), scope.sector("f", "sector_key")
        )
        surprise_where, surprise_args = scope.clause(
            (scope.day("m"), []), scope.sector("m")
        )
        sector_month_where, sector_month_args = scope.clause(
            (scope.sector_month("c"), []), scope.sector("c")
        )
        # The treemap's outer query has a WHERE of its own shape (joins plus a
        # literal TRUE anchor), so it takes the bare fragments and the
        # parameters directly rather than the assembled clause.
        outer_sector_sql, outer_sector_args = scope.sector("c")

        # --- KPIs, each with the monthly series that draws its sparkline -----
        # Headlines in window, by month, from the v1 cube rather than the
        # 3.15M-row fact: cube_month_topic already holds it per topic.
        monthly_news = q.rows(
            f"""
            SELECT year_month, sum(headline_count) AS headlines
            FROM cube_month_topic m{month_topic_where}
            GROUP BY 1 ORDER BY 1
            """,
            month_topic_args,
        )
        news_total = sum(int(r["headlines"]) for r in monthly_news)

        # Trading sessions inside the filter. Outside it this is the whole
        # calendar; a reader who asked for four months should not be told there
        # were 1,235 sessions.
        sessions = q.scalar(
            f"SELECT count(*) FROM dim_trading_calendar t WHERE t.is_trading_day{scope.day('t')}"
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
            f"""
            SELECT trade_date, close, return_pct
            FROM fact_market_daily
            WHERE trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
              AND close IS NOT NULL{scope.day("")}
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
            f"""
            SELECT round(100.0 * sum(risk_signal_count)
                         / nullif(sum(headline_count), 0), 2) AS pct
            FROM cube_day_topic c{day_topic_where}
            """,
            day_topic_args,
        )
        surprise_count = (
            q.scalar(
                f"SELECT count(*) FROM mining_surprise_days m{surprise_where}",
                surprise_args,
            )
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
            f"""
            WITH news AS (
                SELECT year_month,
                       sum(headline_count) AS headlines,
                       round(sum(sum_sentiment_compound)
                             / nullif(sum(headline_count), 0), 4) AS sentiment,
                       round(100.0 * sum(sensational_count)
                             / nullif(sum(headline_count), 0), 3) AS sensational
                FROM cube_month_topic m{month_topic_where}
                GROUP BY 1
            ),
            px AS (
                SELECT substr(CAST(trade_date AS VARCHAR), 1, 7) AS ym,
                       round(avg(close), 2) AS nifty
                FROM fact_market_daily
                WHERE trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                  AND close IS NOT NULL{scope.day("")}
                GROUP BY 1
            )
            SELECT n.year_month AS date, n.headlines, n.sentiment, n.sensational, p.nifty
            FROM news n LEFT JOIN px p ON p.ym = n.year_month
            ORDER BY 1
            """,
            month_topic_args + [start, end],
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
            f"""
            WITH daily AS (
                SELECT f.date_key, sum(f.{scope.news_column()}) AS news
                FROM fact_news_sector_daily f{sector_day_where}
                GROUP BY 1
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
            """,
            sector_day_args,
        )

        # Aliased to the names `EventMark` declares in web/src/types.ts.
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
            f"""
            WITH per_sector AS (
                SELECT c.sector_key, c.{scope.news_column()},
                       avg(c.{scope.news_column()}) OVER (
                         PARTITION BY c.sector_key ORDER BY c.year, c.month
                         ROWS BETWEEN 11 PRECEDING AND CURRENT ROW
                       ) AS rolling_mean,
                       stddev_samp(c.{scope.news_column()}) OVER (
                         PARTITION BY c.sector_key ORDER BY c.year, c.month
                         ROWS BETWEEN 11 PRECEDING AND CURRENT ROW
                       ) AS rolling_sd
                FROM cube_sector_month c{sector_month_where}
            ),
            agg AS (
                SELECT sector_key,
                       sum({scope.news_column()}) AS total_news,
                       round(avg({scope.news_column()} - rolling_mean)
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
            WHERE TRUE{scope.sector_month("c")}{outer_sector_sql}
            GROUP BY 1, 2, 3
            ORDER BY news_count DESC
            """,
            # The CTE's predicates come first in the statement text, so their
            # placeholders bind before the outer query's. Passing only the
            # outer list leaves the CTE's unbound, and DuckDB reports that as
            # "values were not provided for parameters" rather than guessing.
            sector_month_args + outer_sector_args,
        )
        surprise = []
        if _has("mining_surprise_days"):
            surprise = q.rows(
                f"""
                SELECT sector_name AS sector, session, kind, news_heat_z, abs_return_z, avg_return
                FROM mining_surprise_days m{surprise_where}
                ORDER BY abs_return_z DESC LIMIT 5
                """,
                surprise_args,
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

    # Which filter dimensions each figure actually moved. Declared per KPI
    # rather than left to the reader to guess: "Stocks tracked" is 47 whatever
    # you select, and a number that ignores a control sitting right above it
    # reads as though the control is broken. The card says so instead.
    def _responds(*dimensions: str) -> list[str]:
        return list(dimensions)

    data = {
        "devSample": False,
        "window": {"start": start, "end": end},
        "scope": {
            "applied": scope.as_json(),
            "note": scope.note(topic_shaped=True, sector_shaped=True),
        },
        "kpis": [
            {"id": "headlines", "label": "Headlines analysed", "value": news_total,
             "spark": spark, "href": "/explore",
             "respondsTo": _responds("date", "topic")},
            {"id": "stocks", "label": "Stocks tracked", "value": stocks_tracked,
             "spark": [], "href": "/stocks",
             "respondsTo": _responds(),
             "unfilteredBecause": "a count of the companies in dim_stock, not a measure of anything"},
            {"id": "sessions", "label": "Trading sessions", "value": sessions,
             "spark": [], "href": "/data",
             "respondsTo": _responds("date")},
            {"id": "nifty", "label": "Nifty over the window",
             "value": round(nifty_change, 2) if nifty_change is not None else 0,
             "decimals": 2, "unit": "%", "spark": [float(r["close"]) for r in nifty[-60:]],
             "href": "/events",
             "respondsTo": _responds("date"),
             "unfilteredBecause": "the index is one series; sectors and topics are news attributes"},
            {"id": "risk", "label": "Risk-signal rate",
             "value": risk["pct"] if risk and risk["pct"] is not None else 0,
             "decimals": 2, "unit": "%", "spark": [], "href": "/headline",
             "respondsTo": _responds("date", "topic")},
            {"id": "surprise", "label": "Surprise days found", "value": surprise_count or 0,
             "spark": [], "href": "/mining",
             "respondsTo": _responds("date", "sector")},
        ],
        "mood": {
            "score": _mood_pct,
            # The label is derived here rather than left to the front end, so
            # the band that a number falls into is decided in one place and the
            # gauge cannot disagree with the caption.
            "label": _mood_label(_mood_pct),
            "percentile": _mood_pct,
            "recent_level": round(float(mood["recent_level"] or 0), 1) if mood else None,
            # A percentile is only meaningful against the distribution it was
            # ranked in. Narrow the window and the same busy fortnight becomes
            # the 92nd percentile of a quiet quarter, so when a filter is on the
            # caption has to say what it is being compared against.
            "comparedWith": (
                f"the {scope.as_json()['from'] or start} to {scope.as_json()['to'] or end} window, "
                "not the whole corpus"
                if (scope.lo or scope.hi or scope.sectors)
                else "the whole corpus window"
            ),
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
            f"unlabelled headlines and is not a fake-news rate. Event markers "
            f"and the insight cards are whole-corpus and are NOT narrowed by "
            f"the filter - they are facts about the corpus, not about a slice."
            if scope.active
            else
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
def tape(scope: Scope = Depends(scope_params)) -> JSONResponse:
    """Latest sector moves for the ticker tape.

    The last five trading sessions in the window, per sector, so the tape moves
    as the data does instead of showing an arbitrary snapshot.
    """
    started = time.perf_counter()
    if not _has("fact_news_sector_daily"):
        return _not_built("fact_news_sector_daily", "python -m dwm link")
    with reading() as c:
        q = _Queries(c)
        # The tape is sector-shaped and price-shaped, so it answers a sector
        # selection and a date range. It has no topic to filter on: it is built
        # from `fact_stock_daily`, which carries returns, not headlines.
        sessions_where, sessions_args = scope.clause((scope.day("f"), []))
        sec_sql, sec_args = scope.sector("sec")
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
            f"""
            WITH sessions AS (
                SELECT max(trade_date) AS latest FROM fact_stock_daily f{sessions_where}
            )
            SELECT sec.sector_name AS sector,
                   round(avg(f.return_pct), 3) AS window_pct,
                   round(avg(CASE WHEN f.trade_date = (SELECT latest FROM sessions)
                             THEN f.return_pct END), 3) AS latest_pct
            FROM fact_stock_daily f
            JOIN dim_stock st ON st.stock_key = f.stock_key
            JOIN dim_sector sec ON sec.sector_key = st.sector_key
            WHERE NOT f.is_outlier AND f.return_pct IS NOT NULL{scope.day('f')}{sec_sql}
            GROUP BY 1 ORDER BY abs(window_pct) DESC NULLS LAST
            """,
            sessions_args + sec_args,
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
            "price - a sector has no price. "
            + scope.note(topic_shaped=False, sector_shaped=True)
        ),
    )


@router.get("/ui/main-series")
def main_series(
    grain: str = Query(default="month", pattern="^(week|month)$"),
    metric: str = Query(default="heat", pattern="^(heat|sentiment|sensational)$"),
    scope: Scope = Depends(scope_params),
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
        topic_where, topic_args = scope.clause((scope.month("m"), []), scope.topic("m"))
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
                FROM cube_month_topic m{topic_where}
                GROUP BY 1
            ),
            px AS (
                SELECT substr(CAST(trade_date AS VARCHAR), 1, 7) AS ym,
                       round(avg(close), 2) AS nifty
                FROM fact_market_daily
                WHERE trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                  AND close IS NOT NULL{scope.day('')}
                GROUP BY 1
            )
            SELECT n.ym AS date, n.value, p.nifty
            FROM news n LEFT JOIN px p ON p.ym = n.ym
            ORDER BY 1
            """,
            topic_args + [start, end],
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
def calendar(
    metric: str = Query(default="heat", pattern="^(heat|sentiment|sensational)$"),
    scope: Scope = Depends(scope_params),
) -> JSONResponse:
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
        # The calendar is daily x topic, so it answers a date range and a topic
        # selection and ignores a sector one - for the same reason as every
        # other topic-grain figure: a headline has no sector here.
        topic_sql, topic_args = scope.topic("c")
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
              AND TRUE{scope.day('c')}{topic_sql}
            GROUP BY 1, 3, 4 ORDER BY 1
            """,
            [start, end] + topic_args,
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
            "market holiday - a great many headlines fall on the latter. "
            + scope.note(topic_shaped=True, sector_shaped=False)
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
        _Queries(None),
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
        _Queries(None),
        ms=(time.perf_counter() - started) * 1000.0,
        note=payload.get("withheld") and
        "Cards below the evidence floor are listed in `withheld`, not dropped silently."
        or None,
    )


def build_insights(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    from dwm.inference.insights import build

    return build(connection)


__all__ = ["router", "envelope", "_Queries"]
