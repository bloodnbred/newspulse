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


def _con():
    """One long-lived read-only handle on the API snapshot.

    DuckDB caches its connection objects per database path, so opening and
    closing a handle per request does not give each request its own connection -
    it closes the one every other request is using, and the next call fails with
    "Connection already closed". So the handle is created once and kept for the
    life of the process, which is also what the snapshot exists for: the API
    holds a read-only copy open while the CLI keeps writing the real warehouse.
    """
    global _CON
    if _CON is None:
        _CON = store.connection()
    return _CON


def _reset_connection() -> None:
    """Drop the cached handle. Called by the reload endpoint and by tests."""
    global _CON
    _CON = None


def _rows(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cur = _con().execute(sql, params or [])
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
    present = _con().execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
        [table],
    ).fetchone()[0]
    if not present:
        raise _missing(table, command)


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
        payload = sector_ops.run_operation(_con(), "stock_profile", symbol=symbol)
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
        result = sector_ops.run_operation(_con(), "sector_pivot", measure=measure)
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

    return build(_con(), q)


@router.get("/insights", tags=["v2"])
def insights() -> dict[str, Any]:
    from dwm.inference.insights import build

    return _cached("insights", lambda: build(_con()))


@router.get("/quality", tags=["v2"])
def quality() -> dict[str, Any]:
    """The ETL funnel plus mention coverage and the outlier counts."""
    import json

    funnel = []
    con = _con()
    rows = con.execute(
        "SELECT run_id, dataset_code, step, status, rows_read, rows_loaded, "
        "rows_rejected FROM etl_audit ORDER BY audit_id DESC LIMIT 40"
    ).fetchall()
    columns = [
        "run_id", "dataset_code", "step", "status",
        "rows_read", "rows_loaded", "rows_rejected",
    ]
    funnel = [dict(zip(columns, r, strict=True)) for r in rows]

    linking = None
    detail = con.execute(
        "SELECT detail FROM etl_audit WHERE dataset_code='link' "
        "AND step='link.quality' ORDER BY audit_id DESC LIMIT 1"
    ).fetchone()
    if detail and detail[0]:
        try:
            linking = json.loads(detail[0])
        except json.JSONDecodeError:
            linking = None

    outliers = None
    if con.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name='fact_stock_daily'"
    ).fetchone()[0]:
        outliers = con.execute(
            "SELECT count(*) FROM fact_stock_daily WHERE is_outlier"
        ).fetchone()[0]

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
    return build_response(_con(), text, measures, truncated=truncated)


__all__ = ["router"]
