"""FastAPI backend (Phase 8): read-only access to the results and the warehouse.

Design constraints, all of them deliberate:

**The API never recomputes a published number.** Everything under `/facts`,
`/report` and `/summary` is read from `facts.json`, which the inference stage
rendered. If this layer ran its own aggregate for a headline figure, the report
and the dashboard would be two unreviewed paths to the same number, and they
would drift.

**Read-only is enforced by the file, not by good intentions.** The DuckDB
handle is opened `read_only=True`, so no malformed request can write to the
warehouse even if validation were bypassed entirely.

**Every response that reports a rate also reports its denominator and its
name.** The project's central honesty rule is that a rate on unlabelled
headlines is a *risk-signal rate*, never a fake-news rate. An API that returned
`{"rate": 0.109}` would strip the word that makes it correct, so the responses
carry `metric_name`, `denominator` and `caution` alongside every value.

**A missing warehouse is 503, not 500.** "You have not built it yet" is a
different situation from "the server is broken", and the message says which
command to run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from dwm.api import store
from dwm.api.store import DataUnavailable
from dwm.logging_utils import get
from dwm.olap.operations import OPERATIONS

log = get("dwm.api")

app = FastAPI(
    title="dwm-india-news",
    version="1.0.0",
    summary=(
        "Read-only access to a data warehouse built from 3.3M Times of India "
        "headlines, 57k labelled IFND statements and five years of Nifty 50 data."
    ),
    description=__doc__,
)

# The dashboard is served from this same process, so the browser sees one
# origin and CORS never comes into play for it. CORS is left open to loopback
# anyway so that a client started elsewhere on the machine — a notebook, a
# one-off script — can read the results without being fought. It is restricted
# to loopback rather than "*" because this service has no authentication and
# holds a file handle open.
#
# The dashboard used to be a separate Streamlit process on :8501, which is why
# :8501 appears in the list below. It no longer does.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501", "http://127.0.0.1:8501",
        "http://localhost:8000", "http://127.0.0.1:8000",
        "http://localhost:3000", "http://127.0.0.1:3000",
        # The Vite dev server for the React front end (web/, port 5173). The
        # web app reads the API cross-origin from localhost:5173, so without
        # this every request is blocked and the app shows an error instead of
        # data. Both spellings are listed because Vite serves on localhost and
        # browsers treat it and 127.0.0.1 as different origins.
        "http://localhost:5173", "http://127.0.0.1:5173",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(DataUnavailable)
async def _unavailable(_request, exc: DataUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


def _guard(fn, *args, **kwargs):
    """Run a store call, turning its failure modes into HTTP status codes."""
    try:
        return fn(*args, **kwargs)
    except DataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - surfaced to the client
        log.exception("endpoint failed")
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------


@app.get("/health", tags=["meta"])
def health() -> dict[str, Any]:
    """Liveness, and whether the results are actually there.

    A bare `{"ok": true}` would be true on a server with no warehouse behind
    it, which is exactly when a client most needs to be told something is
    wrong.
    """
    state: dict[str, Any] = {
        "status": "ok", "facts": False, "warehouse": False, "snapshot": None,
    }
    try:
        store.load_facts()
        state["facts"] = True
    except DataUnavailable as exc:
        state["status"] = "degraded"
        state["detail"] = str(exc)
    try:
        store.table_counts()
        state["warehouse"] = True
        state["snapshot"] = str(store.snapshot_path())
    except DataUnavailable as exc:
        state["status"] = "degraded"
        state.setdefault("detail", str(exc))
    return state


@app.get("/operations", tags=["meta"])
def operations() -> dict[str, Any]:
    """The ten OLAP operations, with their default parameters."""
    from dwm.olap.operations import DEFAULT_PARAMS

    return {
        "operations": [
            {"name": name, "default_params": DEFAULT_PARAMS.get(name, {})}
            for name in OPERATIONS
        ]
    }


# ---------------------------------------------------------------------------
# the seven research questions
# ---------------------------------------------------------------------------


@app.get("/facts", tags=["facts"])
def all_facts() -> dict[str, Any]:
    """Everything `facts.json` holds. The report quotes this file."""
    return _guard(store.load_facts)


@app.get("/facts/{section}", tags=["facts"])
def fact_section(section: str) -> dict[str, Any]:
    """One section, so a dashboard can load a single panel cheaply."""
    facts = _guard(store.load_facts)
    if section not in facts:
        raise HTTPException(
            status_code=404,
            detail=f"no section {section!r}; available: {sorted(facts)}",
        )
    return {"section": section, "value": facts[section]}


@app.get("/summary", tags=["facts"])
def summary() -> dict[str, Any]:
    """The handful of numbers a reader wants first, with their cautions.

    Each entry keeps the `unit` and `caution` from the fact it came from.
    Dropping them here would be the single easiest way for this API to make
    the project dishonest, since a dashboard showing bare percentages is
    exactly where a risk-signal rate stops being called one.
    """
    facts = _guard(store.load_facts)
    wanted = [
        "corpus", "outcomes", "plain", "rq1_topic_mix", "rq2_bursts",
        "rq3_sensationalism", "rq4_clusters", "rq5_association_rules",
        "rq6_market_association", "rq7_classifier", "guards", "honesty_rules",
    ]
    out: dict[str, Any] = {}
    for key in wanted:
        block = facts.get(key)
        if block is None:
            continue
        # `outcomes` and `plain` are syntheses, not measurements, so they have
        # no single fact header. They are returned as bare data; the dashboard
        # renders each item's own caution, and the per-item cautions travel with
        # the items.
        if key in {"outcomes", "plain"}:
            out[key] = block
            continue
        # The cluster projection holds 4,551 plotted headlines. It is a picture,
        # not a number, so it does not belong in a digest of results: it is
        # served once by /ui/charts and stripped here, which keeps /summary
        # small enough to fetch on every page load. The per-cluster metadata
        # stays, because the sampling note has to travel with anything that
        # quotes the cluster sizes.
        if key == "rq4_clusters" and isinstance(block, dict):
            projection = block.get("projection")
            if isinstance(projection, dict) and projection.get("points"):
                block = {
                    **block,
                    "projection": {k: v for k, v in projection.items() if k != "points"},
                }
        fact = block.get("fact") if isinstance(block, dict) else None
        out[key] = {
            "value": fact.get("value") if fact else None,
            "unit": fact.get("unit") if fact else None,
            "caution": fact.get("caution") if fact else None,
            "source": fact.get("source") if fact else "reports/facts.json",
            "data": block,
        }
    # Provenance, in one block. Every page shows it, so it belongs in the
    # response rather than being assembled by whichever client asks. It was
    # missing `config_version`, which the dashboard rendered as the literal
    # string "config vNone" — a null in a user-facing slot is a bug whether or
    # not the number behind it is load-bearing.
    out["provenance"] = {
        "generated_at": facts.get("generated_at"),
        "mining_run_id": facts.get("mining_run_id"),
        "config_version": facts.get("config_version"),
        "source": "reports/facts.json",
    }
    # Kept at the top level as well as inside `provenance`, because these three
    # were the original shape of this endpoint and existing clients read them.
    out["generated_at"] = facts.get("generated_at")
    out["mining_run_id"] = facts.get("mining_run_id")
    out["config_version"] = facts.get("config_version")
    return out


@app.get("/report", response_class=PlainTextResponse, tags=["facts"])
def report_markdown() -> str:
    """The rendered report, exactly as `python -m dwm report` wrote it."""
    return _guard(store.load_report_markdown)


# ---------------------------------------------------------------------------
# warehouse queries
# ---------------------------------------------------------------------------


@app.get("/topics", tags=["query"])
def topic_list() -> dict[str, Any]:
    return {"topics": _guard(store.topics)}


@app.get("/headlines", tags=["query"])
def headlines(
    topic: str | None = Query(default=None, description="Exact topic name."),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """A page of in-window headlines, newest first.

    `limit` is capped at 500. The corpus holds 1.1M in-window rows, and an
    unbounded page would be a denial of service against the client rather than
    a convenience.
    """
    return _guard(store.headline_sample, topic, limit, offset)


@app.get("/series/monthly", tags=["query"])
def series_monthly(topic: str | None = None) -> dict[str, Any]:
    """Monthly counts and rates, for the trend chart.

    Rates are recomputed here as sum ÷ count from the cube's stored sums. The
    cube holds sums, never averages, precisely so this is possible.
    """
    rows = _guard(store.monthly_series, topic)
    return {"topic": topic, "months": len(rows), "rows": rows}


@app.get("/tables", tags=["query"])
def tables() -> dict[str, Any]:
    """Every table with its row count, for the warehouse browser."""
    rows = _guard(store.table_counts)
    return {"tables": rows, "total_rows": sum(r["rows"] for r in rows)}


@app.get("/market/daily", tags=["query"])
def market_daily(topic: str | None = None) -> dict[str, Any]:
    """Paired daily headline-volume and volatility points, for the RQ6 scatter.

    The correlation is the finding, but a coefficient alone hides the shape of
    the cloud behind it, and the shape is what makes the negative slope
    believable. 1,235 points is small enough to return whole.
    """
    return _guard(store.market_daily, topic)


@app.get("/audit", tags=["query"])
def audit(limit: int = Query(default=25, ge=1, le=200)) -> dict[str, Any]:
    """The ingest and ETL funnel, so row counts can be traced to a run."""
    return {"runs": _guard(store.audit_funnel, limit)}


@app.post("/query/reload", tags=["query"])
def reload_caches() -> dict[str, Any]:
    """Re-snapshot the warehouse and drop cached results.

    A POST because it mutates process state and touches the filesystem. It
    cannot write to the warehouse; it copies it and forgets what it had read.
    """
    store.reload()
    return {
        "reloaded": True,
        "snapshot": str(store.snapshot_path()),
        "detail": "warehouse re-snapshotted, cached facts and report dropped",
    }


@app.get("/query/{operation}", tags=["query"])
def run_olap_operation(
    operation: str,
    topic: str | None = None,
    year: int | None = None,
    topic_group: str | None = None,
    n: int = Query(default=10, ge=1, le=200),
) -> dict[str, Any]:
    """Run one of the ten Phase 4 OLAP operations.

    Whitelisted to the named operations. This is the only endpoint that
    executes SQL, and it executes only code this project wrote — the operation
    is never interpolated into a query.
    """
    if operation not in OPERATIONS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown operation {operation!r}; known: {sorted(OPERATIONS)}",
        )
    params: dict[str, Any] = {}
    if topic is not None:
        params["topic"] = topic
    if year is not None:
        params["year"] = year
    if topic_group is not None:
        params["topic_group"] = topic_group
    if operation == "top_months":
        params["n"] = n
    return _guard(store.execute, operation, params)


# ---------------------------------------------------------------------------
# the dashboard
# ---------------------------------------------------------------------------


@app.get("/ui/charts", tags=["ui"])
def ui_charts(theme: str = Query(default="light", pattern="^(light|dark)$")) -> dict[str, Any]:
    """Every chart on the dashboard, as Vega-Lite specs, in one theme.

    **The specs are built here, in Python, and not in the browser.** That is not
    about the "computes nothing" rule — drawing a chart is not computing a
    finding. It is because a malformed spec is a blank page with no stack trace,
    and building it here means a unit test can catch it. It is also how six
    charts end up sharing one palette instead of drifting apart.

    The theme is a parameter rather than a constant because a hard-coded light
    palette is what made the previous dashboard unreadable on a machine set to
    dark: CSS forced a light canvas while the chart library rendered dark charts
    over it. Both are designed; the client asks for the one it is using.
    """
    from dwm.ui.charts import build_charts

    def run() -> dict[str, Any]:
        facts = store.load_facts()
        charts = build_charts(
            facts,
            market_points=store.market_daily()["points"],
            tables=store.table_counts(),
            theme_name=theme,
        )
        return {"theme": theme, "charts": charts, "count": len(charts)}

    return _guard(run)


@app.get("/ui/manifest", tags=["ui"])
def ui_manifest() -> dict[str, Any]:
    """Everything the front end needs to render its shell.

    Built in the store rather than inline, so a missing provenance field fails
    the manifest test rather than showing up as "vNone" in a browser.
    """
    return _guard(store.build_manifest)


# v2 routes, mounted BEFORE the static catch-all so an API path can never be
# swallowed by the file mount. v1 routes above are untouched.
from dwm.api.v2 import router as v2_router  # noqa: E402

app.include_router(v2_router)

# The /ui backend-for-frontend router, for the React front end in web/.
# Mounted before the static mount too: `/ui/charts` and `/ui/manifest` are v1
# routes, and `/ui/home` is the new one, and none of them may be shadowed by
# the file mount that follows.
from dwm.api.ui import router as ui_router  # noqa: E402

app.include_router(ui_router)


# The static front end, mounted last so every API route above wins the match.
# This is what makes the dashboard a single process: `dwm serve` serves the
# data and the page that reads it, on one origin, with no CORS and no second
# server to start before a demonstration.
_STATIC_DIR = Path(__file__).resolve().parents[2] / "dashboard" / "static"

if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="dashboard")
else:  # pragma: no cover - only when the front end has not been built
    log.warning("dashboard static directory missing at %s", _STATIC_DIR)
