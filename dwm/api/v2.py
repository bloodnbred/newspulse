"""v2 API routes: stocks, sectors, events, mining, brief, check, insights.

v1 routes are untouched. Every endpoint here follows the same three rules:

* **404 names the command.** "Run `python -m dwm link` first" is actionable;
  "no data" is not. A phase that has not been run is a normal state, not an
  error to report as a 500.
* **Every figure carries its denominator and its limit.** The response body
  carries `caveat` wherever a number could be over-read, matching what the
  project already does on `/facts`.
* **Nothing is recomputed per request** beyond the queries here, and the
  heavier ones cache on a TTL so the dashboard can poll.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from dwm.api import store
from dwm.logging_utils import get

log = get("dwm.api.v2")

router = APIRouter()

#: Light TTL cache for the heavier reads. The dashboard polls; recomputing a
#: lag scan on every poll would be wasteful and would not change the answer.
_CACHE: dict[str, tuple[float, Any]] = {}
CACHE_TTL_S = 60.0

MAX_HEADLINES = 50


_CON = None

#: One reader at a time on the shared handle.
#:
#: This exists because of a bug only a browser could find. FastAPI runs sync
#: endpoints in a threadpool, and the dashboard fires several requests at once -
#: `Promise.all` on the mood page hits `/olap/sector/drill-across` and
#: `/ui/v2charts` simultaneously. DuckDB's `execute()` returns the connection
#: itself and the result is read afterwards by `fetchone()`/`fetchall()`, so two
#: threads interleave between those two calls and one of them reads the OTHER
#: one's row. It surfaced as `TypeError: 'NoneType' object is not
#: subscriptable` from inside `_has`, on a page that loads fine when the two
#: requests happen to be issued one after the other.
#:
#: Per-request connections are not the fix, and were already ruled out: DuckDB
#: caches connection objects per database path, which is why closing a handle per
#: request used to break every other holder. One shared handle plus a lock is the
#: arrangement that is actually safe. Queries here are milliseconds against a
#: local snapshot, so serialising them costs nothing a reader can perceive.
_LOCK = threading.RLock()


def _con():
    """One long-lived read-only handle on the API snapshot.

    DuckDB caches its connection objects per database path, so opening and
    closing a handle per request does not give each request its own connection -
    it closes the one every other request is using, and the next call fails with
    "Connection already closed". So the handle is created once and kept for the
    life of the process, which is also what the snapshot exists for: the API
    holds a read-only copy open while the CLI keeps writing the real warehouse.

    Callers must hold `_LOCK` for the whole execute-then-read sequence.
    """
    global _CON
    if _CON is None:
        _CON = store.connection()
    return _CON


def _reset_connection() -> None:
    """Drop the cached handle. Called by the reload endpoint and by tests."""
    global _CON
    _CON = None


@contextmanager
def _reading():
    """Hold the shared handle for one complete read.

    The whole query, not just the `execute()` call: the result is read in a
    separate call, and locking only the first half is what let two requests
    interleave in the first place.
    """
    with _LOCK:
        yield _con()


def _rows(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    with _reading() as con:
        cur = con.execute(sql, params or [])
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def _one(sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
    rows = _rows(sql, params)
    return rows[0] if rows else None


def _missing(table: str, command: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=(
            f"{table} has not been built yet. "
            f"Run `{command}` and try again."
        ),
    )


def _require(table: str, command: str) -> None:
    present = _has(table)
    if not present:
        raise _missing(table, command)


def _has(table: str) -> bool:
    with _reading() as con:
        present = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            [table],
        ).fetchone()[0]
    return bool(present)


def _cached(key: str, builder):
    import time

    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < CACHE_TTL_S:
        return hit[1]
    value = builder()
    _CACHE[key] = (time.time(), value)
    return value


# ---------------------------------------------------------------------------
# stocks and sectors
# ---------------------------------------------------------------------------


@router.get("/stocks", tags=["v2"])
def stocks_list(
    sector: str | None = None, limit: int = Query(default=100, ge=1, le=200)
) -> dict[str, Any]:
    _require("dim_stock", "python -m dwm ingest --dataset stocks")
    where, params = ["1=1"], []
    if sector:
        where.append("s.sector_name = ?")
        params.append(sector)
    rows = _rows(
        f"""
        SELECT d.symbol, d.company_name, s.sector_name AS sector, d.isin,
               d.in_nifty50, d.is_survivor, d.price_source, d.sector_source,
               d.first_trade_date, d.last_trade_date
        FROM dim_stock d LEFT JOIN dim_sector s ON s.sector_key = d.sector_key
        WHERE {' AND '.join(where)}
        ORDER BY d.symbol LIMIT {int(limit)}
        """,
        params,
    )
    return {
        "stocks": rows,
        "n": len(rows),
        "caveat": (
            "Constituents are today's Nifty 50 list applied to a historical "
            "window. Companies removed from the index since are absent, and "
            "rows with is_survivor false list after the window opened."
        ),
    }


@router.get("/stocks/{symbol}", tags=["v2"])
def stock_profile(symbol: str) -> dict[str, Any]:
    _require("fact_stock_daily", "python -m dwm ingest --dataset stocks && python -m dwm build")
    from dwm.olap import sector_ops

    try:
        with _reading() as con:
            payload = sector_ops.run_operation(con, "stock_profile", symbol=symbol)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    payload["caveat"] = (
        "Returns are percentage points from adjusted closes, not fractions. A "
        "mention means the company name appeared in the headline, not that the "
        "story was about it."
    )
    return payload


@router.get("/sectors", tags=["v2"])
def sectors_list() -> dict[str, Any]:
    _require("dim_sector", "python -m dwm build")
    rows = _rows(
        """
        SELECT s.sector_key, s.sector_name,
               count(d.stock_key) AS constituents,
               count(*) FILTER (WHERE d.is_survivor) AS survivors,
               count(*) FILTER (WHERE d.price_source IS NOT NULL) AS with_prices
        FROM dim_sector s LEFT JOIN dim_stock d ON d.sector_key = s.sector_key
        GROUP BY 1, 2 ORDER BY 2
        """
    )
    return {"sectors": rows, "n": len(rows)}


@router.get("/sectors/heat", tags=["v2"])
def sector_heat(measure: str = Query(default="news_count")) -> dict[str, Any]:
    _require("cube_sector_month", "python -m dwm link")
    from dwm.olap import sector_ops

    try:
        with _reading() as con:
            result = sector_ops.run_operation(con, "sector_pivot", measure=measure)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **result,
        "caveat": (
            "News volume against price per sector per year. Co-movement between "
            "two series is not evidence that one moved the other."
        ),
    }


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------


@router.get("/events", tags=["v2"])
def events_list() -> dict[str, Any]:
    _require("dim_event", "python -m dwm build")
    rows = _rows(
        """
        SELECT e.event_key, e.event_label, e.event_type, e.start_date,
               (SELECT count(*) FROM mining_event_study m
                 WHERE m.event_key = e.event_key AND m.valid) AS stocks_studied
        FROM dim_event e ORDER BY e.start_date
        """
    )
    return {
        "events": rows,
        "n": len(rows),
        "caveat": (
            "Event dates come from config/events_v2.yaml and were entered by "
            "hand, not scraped. The event day is mapped to the next trading "
            "session, so a weekend or holiday date lands on the following Monday."
        ),
    }


@router.get("/events/{event_key}/study", tags=["v2"])
def event_study(event_key: int) -> dict[str, Any]:
    _require("mining_event_study", "python -m dwm mine --op events")
    by_sector = _rows(
        """
        SELECT s.sector_name AS sector, count(*) AS n_stocks,
               round(avg(m.car_0_5), 4) AS mean_car_0_5,
               round(avg(m.car_0_1), 4) AS mean_car_0_1,
               round(avg(m.car_m1_5), 4) AS mean_car_m1_5,
               round(avg(m.beta), 4) AS mean_beta
        FROM mining_event_study m
        LEFT JOIN dim_sector s ON s.sector_key = m.sector_key
        WHERE m.event_key = ? AND m.valid
        GROUP BY 1 ORDER BY abs(avg(m.car_0_5)) DESC
        """,
        [event_key],
    )
    stocks = _rows(
        """
        SELECT d.symbol, s.sector_name AS sector, round(m.car_0_5, 4) AS car_0_5,
               round(m.beta, 4) AS beta, m.est_days
        FROM mining_event_study m
        JOIN dim_stock d ON d.stock_key = m.stock_key
        LEFT JOIN dim_sector s ON s.sector_key = m.sector_key
        WHERE m.event_key = ? AND m.valid AND m.car_0_5 IS NOT NULL
        ORDER BY abs(m.car_0_5) DESC LIMIT 20
        """,
        [event_key],
    )
    excluded = _rows(
        """
        SELECT d.symbol, m.reason, m.est_days
        FROM mining_event_study m JOIN dim_stock d ON d.stock_key = m.stock_key
        WHERE m.event_key = ? AND NOT m.valid ORDER BY d.symbol
        """,
        [event_key],
    )
    if not by_sector and not stocks:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No valid result for event {event_key}. It may be outside the "
                f"window, or every stock lacked enough pre-event history. "
                f"Run `python -m dwm mine --op events`."
            ),
        )
    return {
        "event_key": event_key,
        "by_sector": by_sector,
        "by_stock": stocks,
        "excluded": excluded,
        "caveat": (
            "Cumulative abnormal return means the move beyond what the market "
            "model explains. It is not a causal effect: other news on the same "
            "day produces the same number. Rows in `excluded` had too little "
            "pre-event history to fit a model and were kept out of the average."
        ),
    }


# ---------------------------------------------------------------------------
# mining results
# ---------------------------------------------------------------------------


@router.get("/mining/stockclusters", tags=["v2"])
def mining_clusters() -> dict[str, Any]:
    _require("mining_stock_clusters", "python -m dwm mine --op stockclusters")
    rows = _rows(
        """
        SELECT cluster_id, symbol, sector_name AS sector, ann_volatility,
               mean_daily_return, beta, max_drawdown, mentions_per_1000_days,
               news_sensitivity, pc1, pc2
        FROM mining_stock_clusters ORDER BY cluster_id, symbol
        """
    )
    sizes: dict[str, int] = {}
    for r in rows:
        sizes[str(r["cluster_id"])] = sizes.get(str(r["cluster_id"]), 0) + 1
    return {
        "clusters": rows,
        "sizes": sizes,
        "caveat": (
            "Behavioural similarity, not business similarity. A silhouette score "
            "near 0.2 means weak structure: these are not tidy groups."
        ),
    }


@router.get("/mining/marketrules", tags=["v2"])
def mining_rules(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    _require("mining_market_rules", "python -m dwm mine --op marketrules")
    rows = _rows(
        f"""
        SELECT rule_id, antecedents, consequents, support, confidence, lift,
               support_count, n_transactions
        FROM mining_market_rules ORDER BY lift DESC LIMIT {int(limit)}
        """
    )
    return {
        "rules": rows,
        "caveat": (
            "Association within a five-year sample. Lift 1.4 means the "
            "consequent co-occurred 1.4x as often, not that the antecedent "
            "produced it."
        ),
    }


@router.get("/mining/direction", tags=["v2"])
def mining_direction() -> dict[str, Any]:
    _require("mining_direction_results", "python -m dwm mine --op direction")
    rows = _rows(
        "SELECT * FROM mining_direction_results WHERE accuracy IS NOT NULL"
    )
    best = max(rows, key=lambda r: r["accuracy"]) if rows else None
    return {
        "models": rows,
        "baseline_majority": best["baseline_majority"] if best else None,
        "baseline_yesterday": best["baseline_yesterday"] if best else None,
        "beats_baseline": (
            bool(best["accuracy"] > best["baseline_majority"])
            if best and best["baseline_majority"] is not None
            else None
        ),
        "caveat": (
            "Held-out test rows only, chronologically split, never shuffled. A "
            "result at or below the majority baseline means these features carry "
            "no usable skill."
        ),
    }


@router.get("/mining/surprise", tags=["v2"])
def mining_surprise(
    kind: str = Query(default="news_driven", pattern="^(news_driven|quiet_news_move)$"),
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    _require("mining_surprise_days", "python -m dwm mine --op surprise")
    rows = _rows(
        f"""
        SELECT sector_name AS sector, session, news_count, news_heat_z,
               abs_return_z, avg_return, kind
        FROM mining_surprise_days WHERE kind = ?
        ORDER BY abs_return_z DESC LIMIT {int(limit)}
        """,
        [kind],
    )
    return {
        "days": rows,
        "kind": kind,
        "caveat": (
            "Scored against each sector's own preceding 60 sessions. "
            "quiet_news_move means the price moved and this corpus had little to "
            "say - a fact about our coverage, not about the market."
        ),
    }


# ---------------------------------------------------------------------------
# brief, insights, quality, check
# ---------------------------------------------------------------------------


@router.get("/brief", tags=["v2"])
def brief(q: str = Query(..., min_length=1)) -> dict[str, Any]:
    _require("dim_stock", "python -m dwm ingest --dataset stocks")
    from dwm.inference.brief import build

    with _reading() as con:
        return build(con, q)


@router.get("/insights", tags=["v2"])
def insights() -> dict[str, Any]:
    from dwm.inference.insights import build

    def run() -> dict[str, Any]:
        with _reading() as con:
            return build(con)

    return _cached("insights", run)


@router.get("/quality", tags=["v2"])
def quality() -> dict[str, Any]:
    """The ETL funnel plus mention coverage and the outlier counts."""
    import json

    # One locked read for all three queries. They used to run as three separate
    # unlocked `execute()` calls on the shared handle, which is the interleave
    # described on `_LOCK`: another request could take the connection between
    # any two of them.
    with _reading():
        funnel = _rows(
            "SELECT run_id, dataset_code, step, status, rows_read, rows_loaded, "
            "rows_rejected FROM etl_audit ORDER BY audit_id DESC LIMIT 40"
        )

        linking = None
        detail = _one(
            "SELECT detail FROM etl_audit WHERE dataset_code='link' "
            "AND step='link.quality' ORDER BY audit_id DESC LIMIT 1"
        )
        if detail and detail.get("detail"):
            try:
                linking = json.loads(detail["detail"])
            except json.JSONDecodeError:
                linking = None

        outliers = None
        if _has("fact_stock_daily"):
            row = _one("SELECT count(*) AS n FROM fact_stock_daily WHERE is_outlier")
            outliers = row["n"] if row else None

    return {
        "funnel": funnel,
        "outlier_rows": outliers,
        "linking": linking,
        "caveat": (
            "Mention precision is absent until a person fills in the is_correct "
            "column of reports/mention_audit_sample.csv. It is never computed "
            "from the matcher itself."
        ),
    }


@router.get("/ui/v2charts", tags=["ui", "v2"])
def ui_v2_charts(
    theme: str = Query(default="light", pattern="^(light|dark)$"),
    sector: str | None = None,
    symbol: str | None = None,
    measure: str = Query(default="news_count"),
    event_key: int = Query(default=0),
    grain: str = Query(default="month", pattern="^(day|week|month)$"),
    lag: int = Query(default=0, ge=-5, le=5),
    headline_score: float | None = None,
) -> dict[str, Any]:
    """Every v2 chart as Vega-Lite specs, in one theme.

    Built in Python for the same reason `/ui/charts` is: a malformed spec is a
    blank page with no stack trace, whereas here a test can catch it. It also
    means no dashboard file ever touches a warehouse table.

    `grain` exists because the drill-across fact has 12,775 daily rows. Inlined
    into a 300px line chart that is a 2.4MB payload and, worse, a solid block
    of colour - there is no shape to read. Monthly by default, with daily and
    weekly available, and the caption always names the grain actually used so
    the reader is never guessing at the resolution.

    Each chart is built independently and a failure in one degrades that chart
    to a named-empty spec rather than failing the page. A reader who cannot see
    the cluster scatter still gets the direction-model bars.
    """
    from dwm.ui.v2_charts import build_v2_charts

    def run() -> dict[str, Any]:
        mood: list[dict[str, Any]] = []
        cube: list[dict[str, Any]] = []
        points: list[dict[str, Any]] = []
        bars: list[dict[str, Any]] = []
        scatter: list[dict[str, Any]] = []
        direction: list[dict[str, Any]] = []
        majority: float | None = None
        yesterday: float | None = None
        hist: list[dict[str, Any]] = []

        if _has("cube_sector_month"):
            from dwm.olap import sector_ops

            try:
                with _reading() as con:
                    raw = sector_ops.run_operation(
                        con, "drill_across_sector", sector=sector, lag=lag
                    )["rows"]
                mood, mood_caption = _by_grain(raw, grain)
            except ValueError:
                mood, mood_caption = [], ""
            # An unknown measure is refused, not swallowed. Catching the
            # ValueError here would quietly produce an empty heatmap under a
            # title naming the measure the reader asked for, which is worse
            # than an error: it looks like the answer.
            with _reading() as con:
                cube = sector_ops.run_operation(
                    con, "sector_pivot", measure=measure
                )["rows"]

        if symbol and _has("fact_stock_daily"):
            from dwm.olap import sector_ops

            try:
                with _reading() as con:
                    points = sector_ops.run_operation(
                        con, "stock_profile", symbol=symbol, peers=False
                    )["rows"]
            except ValueError:
                points = []

        if _has("mining_event_study"):
            if event_key:
                key = event_key
            else:
                with _reading() as con:
                    key = con.execute(
                        "SELECT max(event_key) FROM mining_event_study WHERE valid"
                    ).fetchone()[0]
            if key is not None:
                bars = _rows(
                    """
                    SELECT s.sector_name AS sector, count(*) AS n_stocks,
                           round(avg(m.car_0_5), 4) AS mean_car_0_5
                    FROM mining_event_study m
                    LEFT JOIN dim_sector s ON s.sector_key = m.sector_key
                    WHERE m.event_key = ? AND m.valid AND m.car_0_5 IS NOT NULL
                    GROUP BY 1 ORDER BY 2 DESC
                    """,
                    [int(key)],
                )

        if _has("mining_stock_clusters"):
            scatter = _rows(
                """
                SELECT symbol, sector_name AS sector, cluster_id, pc1, pc2,
                       round(ann_volatility, 3) AS ann_volatility, round(beta, 3) AS beta
                FROM mining_stock_clusters
                WHERE pc1 IS NOT NULL AND cluster_id IS NOT NULL
                ORDER BY cluster_id, symbol
                """
            )

        if _has("mining_direction_results"):
            direction = _rows(
                "SELECT model, accuracy FROM mining_direction_results "
                "WHERE accuracy IS NOT NULL ORDER BY accuracy DESC"
            )
            best = direction[0] if direction else None
            if best is not None:
                row = _one(
                    "SELECT baseline_majority, baseline_yesterday "
                    "FROM mining_direction_results WHERE accuracy IS NOT NULL "
                    "ORDER BY accuracy DESC LIMIT 1"
                )
                majority = row["baseline_majority"] if row else None
                yesterday = row["baseline_yesterday"] if row else None

        if _has("fact_headline"):
            # Ten equal-width bands over the corpus's OWN score range rather than
            # an assumed 0-1: the scale is a weighted sum of features and is not
            # bounded by construction, so fixed bands would put almost every
            # headline in one bar and make the histogram useless.
            bounds = _one(
                "SELECT min(sensational_score) AS lo, max(sensational_score) AS hi "
                "FROM fact_headline WHERE sensational_score IS NOT NULL"
            )
            if bounds and bounds["hi"] is not None and bounds["hi"] > bounds["lo"]:
                hist = _rows(
                    """
                    SELECT least(9, greatest(0, floor(
                        (? - sensational_score) / ((? - ?) / 10.0)
                    ))::int) AS bin,
                    count(*) AS count
                    FROM fact_headline
                    WHERE sensational_score IS NOT NULL
                    GROUP BY 1 ORDER BY 1
                    """,
                    [bounds["hi"], bounds["hi"], bounds["lo"]],
                )
                width = (bounds["hi"] - bounds["lo"]) / 10.0
                hist = [
                    {
                        "bin": f"{bounds['lo'] + h['bin'] * width:.2f}"
                               f"-{bounds['lo'] + (h['bin'] + 1) * width:.2f}",
                        "count": h["count"],
                    }
                    for h in hist
                ]

        charts = build_v2_charts(
            theme_name=theme,
            mood_rows=mood,
            mood_caption=mood_caption,
            lag=lag,
            stock_points=points,
            symbol=symbol or "",
            cube_rows=cube,
            measure=measure,
            event_bars=bars,
            cluster_points=scatter,
            direction=direction,
            majority=majority,
            yesterday=yesterday,
            percentile_hist=hist,
            headline_score=headline_score,
        )
        return {"theme": theme, "charts": charts, "count": len(charts)}

    return _guard(run)


def _by_grain(rows: list[dict[str, Any]], grain: str) -> tuple[list[dict[str, Any]], str]:
    """Roll daily drill-across rows up to the requested grain.

    A mean is never averaged from means. `drill_across_sector` returns
    `mean_return_pct` per (session, sector) alongside `stock_obs`, the count of
    stock-days behind it, so the period mean is
    `sum(mean_return_pct * stock_obs) / sum(stock_obs)` - weighted by the number
    of observations each daily mean was built from.

    This was silently producing zeros. The first version of this function read a
    `sum_return` key, which the drill-across does not provide, so every period
    returned exactly 0.0 and the price panel of the market-mood chart drew as a
    flat line pinned to zero. Nothing raised; the chart simply had nothing in
    it. A row carrying a real `sum_return` is still handled, so the function
    works on either shape.
    """
    from datetime import date

    if grain == "day":
        # Capped rather than shipped whole. 10,342 daily rows is under the
        # 100k pagination threshold but still a 2MB payload, and it renders as a
        # solid block on a 300px chart with no shape to read. The most recent
        # MAX_DAY_POINTS sessions is the window a reader actually uses, and the
        # caption says it is a window so nobody reads it as the whole period.
        MAX_DAY_POINTS = 250
        total = len(rows)
        if total > MAX_DAY_POINTS:
            rows = rows[-MAX_DAY_POINTS:]
            return rows, f"last {MAX_DAY_POINTS:,} of {total:,} sessions"
        return rows, f"{total:,} sessions"

    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        session = row.get("session")
        if session is None:
            continue
        when = session if isinstance(session, date) else date.fromisoformat(str(session))
        if grain == "month":
            key = f"{when.year:04d}-{when.month:02d}-01"
        else:
            iso = when.isocalendar()
            key = f"{iso[0]:04d}-W{iso[1]:02d}-1"

        bucket = buckets.setdefault(
            key,
            {"session": key, "news_count": 0.0, "weighted_return": 0.0,
             "weighted_abs": 0.0, "raw_sum_return": 0.0, "stock_obs": 0.0,
             "sum_sentiment": 0.0, "days": 0, "has_sum_return": False},
        )
        obs = float(row.get("stock_obs") or 0)
        bucket["news_count"] += float(row.get("news_count") or 0)
        bucket["stock_obs"] += obs
        bucket["sum_sentiment"] += float(row.get("sum_sentiment") or 0)
        bucket["days"] += 1

        if row.get("sum_return") is not None:
            bucket["raw_sum_return"] += float(row["sum_return"])
            bucket["has_sum_return"] = True
        elif row.get("mean_return_pct") is not None:
            # Weighted by the observations behind that daily mean, so a day
            # covering eight stocks counts for eight, not one.
            bucket["weighted_return"] += float(row["mean_return_pct"]) * obs
        if row.get("mean_abs_return_pct") is not None:
            bucket["weighted_abs"] += float(row["mean_abs_return_pct"]) * obs

    out: list[dict[str, Any]] = []
    for key in sorted(buckets):
        b = buckets[key]
        obs = b["stock_obs"]
        total_return = b["raw_sum_return"] if b["has_sum_return"] else b["weighted_return"]
        out.append(
            {
                "session": b["session"],
                "news_count": round(b["news_count"] / b["days"], 1),
                "mean_return_pct": (
                    round(total_return / obs, 5) if obs else None
                ),
                "mean_abs_return_pct": (
                    round(b["weighted_abs"] / obs, 5) if obs else None
                ),
                "sum_sentiment": round(b["sum_sentiment"], 3),
                "stock_obs": int(obs),
                "days": b["days"],
            }
        )
    label = {"month": "months", "week": "weeks"}[grain]
    return out, f"{len(out):,} {label}, averaged"


def _latest_news_year() -> int:
    """The most recent year the cube actually holds.

    `sector_drilldown` requires a year and has no useful default, so the API
    supplies one rather than making the caller guess. Derived from the cube
    rather than from today's date, because the corpus ends 2020-06-30 and
    defaulting to the current year would return nothing with no explanation.
    """
    row = _one("SELECT max(year) AS y FROM cube_sector_month")
    return int(row["y"]) if row and row["y"] else 2015


def _guard(fn):
    """Turn a call's failure modes into status codes.

    A local copy rather than an import from dwm.api.app, because app.py imports
    this module to register the router: importing it back would be a cycle.
    Same mapping, so a v2 endpoint fails the way a v1 one does.
    """
    try:
        return fn()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - surfaced to the client
        log.exception("v2 endpoint failed")
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


# ---------------------------------------------------------------------------
# sector OLAP operations
# ---------------------------------------------------------------------------

#: Short URL name -> the name `sector_ops.OPERATIONS` registers it under.
#:
#: Kept as an explicit mapping rather than a prefix convention. `sector_ops`
#: prefixes its operations (`sector_slice`, `sector_pivot`) because they share a
#: module with other v1 operations; the URL does not need to carry that
#: disambiguation, and guessing at it with a string rule is how a future rename
#: silently 404s. An unmapped name is refused, never constructed.
SECTOR_OPERATIONS = {
    "slice": "sector_slice",
    "dice": "sector_dice",
    "rollup": "sector_rollup",
    "drilldown": "sector_drilldown",
    "pivot": "sector_pivot",
    "cube": "sector_cube",
    "drill-across": "drill_across_sector",
    "stock_profile": "stock_profile",
}


@router.get("/olap/sector/{operation}", tags=["olap", "v2"])
def olap_sector(
    operation: str,
    sector: str | None = None,
    sectors: str | None = None,
    years: str | None = None,
    year: int | None = None,
    quarter: int | None = None,
    month: int | None = None,
    # Default matches `sector_rollup`'s own default. It used to be "month",
    # which the roll-up refuses - it rolls months UP to quarters or years, so
    # "month" is not a level it can roll up to. The mismatch made every
    # unparameterised call a 400, which is what the Sector Cube page does.
    level: str = Query(default="year", pattern="^(quarter|year)$"),
    measure: str = Query(default="news_count"),
    lag: int = Query(default=0, ge=-5, le=5),
    include_market_reports: bool = False,
    symbol: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """Run one sector OLAP operation and return it with its own description.

    The description comes from the operation itself rather than from this
    handler, so the words under a table always match the SQL that produced it.
    """
    if operation not in SECTOR_OPERATIONS:
        raise HTTPException(
            status_code=404,
            detail=(
                f"unknown operation '{operation}'. Available: "
                + ", ".join(sorted(SECTOR_OPERATIONS))
            ),
        )
    if not _has("cube_sector_month"):
        raise _missing("cube_sector_month", "python -m dwm link")

    from dwm.olap import sector_ops

    def run() -> dict[str, Any]:
        wanted: dict[str, Any] = {}
        if operation == "slice":
            wanted = {
                "sector": sector, "measure": measure, "year": year,
                "include_market_reports": include_market_reports,
            }
        elif operation == "dice":
            # Both lists default to empty, which the operation reads as "all".
            # `sectors` arrives as one comma-separated query parameter because
            # that is what a form can send; it is split here rather than in the
            # operation, which takes real lists.
            wanted = {
                "sectors": [s.strip() for s in (sectors or "").split(",") if s.strip()],
                "years": [int(y) for y in (years or "").split(",") if y.strip()],
                "measure": measure,
                "include_market_reports": include_market_reports,
            }
        elif operation == "rollup":
            wanted = {"level": level, "measure": measure, "sector": sector}
        elif operation == "drilldown":
            # `sector_drilldown` takes year (required), optional quarter/month
            # and an optional sector. It has no `measure`: the drill-down shows
            # the individual facts, not a measure total.
            wanted = {
                "sector": sector,
                "year": year if year is not None else _latest_news_year(),
                "quarter": quarter,
                "month": month,
            }
        elif operation == "pivot":
            wanted = {"measure": measure, "year": year}
        elif operation == "cube":
            wanted = {}
        elif operation == "drill-across":
            wanted = {
                "sector": sector, "lag": lag,
                "include_market_reports": include_market_reports,
            }
        else:  # stock_profile
            wanted = {"symbol": symbol}

        usable = {k: v for k, v in wanted.items() if v is not None}
        with _reading() as con:
            result = sector_ops.run_operation(
                con, SECTOR_OPERATIONS[operation], **usable
            )
        return {
            **result,
            "caveat": OPERATION_CAVEATS[operation],
        }

    return _guard(run)


OPERATION_CAVEATS = {
    "slice": "Rows are sums and counts; any mean shown is divided here, not stored.",
    "dice": "An empty sector or year list means 'all', which is a different query from an "
            "explicit list and can return a different row count.",
    "rollup": "A roll-up must equal the cube it came from. The response reports whether it does.",
    "drilldown": "Limited to the most recent facts in one sector-period. Truncated, not sampled.",
    "pivot": "Cells are means derived from sums and counts, so they can be added up correctly.",
    "cube": "The stored rows, unaltered, so the arithmetic above can be checked by hand.",
    "drill-across": "Two grains joined on the conformed trading-date key. `lag` shifts the news "
                    "side relative to price, which is the only way to see whether news leads "
                    "price or merely coincides with it. Market-report headlines are excluded by "
                    "default because they describe a move that already happened.",
    "stock_profile": "Returns are percentage points from adjusted closes. A mention means the name "
                     "appeared, not that the story was about it.",
}


@router.post("/check", tags=["v2"])
def check(payload: dict[str, Any]) -> dict[str, Any]:
    """Score a pasted headline: style signals, percentile, neighbours, tags."""
    text = str(payload.get("headline") or "").strip()
    if not text:
        raise HTTPException(
            status_code=400,
            detail="send {'headline': '...'} with a non-empty headline.",
        )
    if len(text) > 2000:
        # Long input is a paste accident, not an attack, and truncating with a
        # note beats a 500. The scorer only reads the first few hundred chars
        # anyway.
        text = text[:2000]
        truncated = True
    else:
        truncated = False

    _require("fact_headline", "python -m dwm build")
    from dwm.features.scoring import TextScorer
    from dwm.inference.checker import build_response

    measures = TextScorer().measure(text)
    with _reading() as con:
        return build_response(con, text, measures, truncated=truncated)


__all__ = ["router"]
