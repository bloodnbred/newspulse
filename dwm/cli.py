"""Command line interface.

Run as `python -m dwm <command>` (a Makefile is awkward on Windows, see
BLUEPRINT section 1). Every stage is exposed from day one so the pipeline
stays runnable end to end while the later stages are still stubs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from dwm import __version__
from dwm.config import (
    PROJECT_ROOT,
    Settings,
    default_db_path,
    ensure_dirs,
    load_datasets_config,
    load_topic_map,
)
from dwm.logging_utils import get, setup

app = typer.Typer(
    name="dwm",
    help="Indian News Warehouse: ingest, ETL, OLAP, mining and inference.",
    no_args_is_help=True,
    add_completion=False,
)
log = get("dwm.cli")

DbOpt = Annotated[Path, typer.Option("--db", help="Path to the DuckDB warehouse file.")]
SampleOpt = Annotated[
    int | None,
    typer.Option("--sample", min=1, help="Load only the first N rows per dataset."),
]
YearsOpt = Annotated[int, typer.Option("--years", min=1, help="Length of the analysis window.")]
ChunkOpt = Annotated[int, typer.Option("--chunk", min=1, help="Rows per processing chunk.")]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")]


def _settings(db: Path | None, sample: int | None, years: int, chunk: int) -> Settings:
    ensure_dirs()
    return Settings(db_path=db or default_db_path(), sample=sample, years=years, chunk=chunk)


def _echo_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, default=str))


# ---------------------------------------------------------------------------
# meta commands
# ---------------------------------------------------------------------------


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def config(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    """Show resolved paths, datasets and topic map.

    Useful before a run: it shows which URLs and column aliases are in force
    without touching the warehouse.
    """
    ensure_dirs()
    specs = load_datasets_config()
    topics = load_topic_map()
    payload = {
        "version": __version__,
        "project_root": str(PROJECT_ROOT),
        "default_db": str(default_db_path()),
        "datasets": {
            code: {
                "name": spec.name,
                "url": spec.url,
                "local_path": str(spec.local_path()),
                "present": spec.local_path().exists(),
                "labelled": spec.is_labelled,
                "date_precision": spec.declared_date_precision,
                "citation": spec.citation,
            }
            for code, spec in sorted(specs.items())
        },
        "topics": {"count": len(topics.topics), "fallback": topics.fallback_topic},
    }
    if verbose:
        payload["topics"]["names"] = topics.topics
        payload["topics"]["prefix_map_size"] = len(topics.prefix_map)
    _echo_json(payload)


# ---------------------------------------------------------------------------
# pipeline stages
# ---------------------------------------------------------------------------


@app.command()
def ingest(
    db: DbOpt = None,
    sample: SampleOpt = None,
    years: YearsOpt = 5,
    chunk: ChunkOpt = 100_000,
    dataset: Annotated[
        list[str] | None,
        typer.Option("--dataset", "-d", help="Ingest only these datasets."),
    ] = None,
    force_download: Annotated[
        bool, typer.Option("--force", help="Re-download even if the file is present.")
    ] = False,
    skip_fetch: Annotated[
        bool, typer.Option("--skip-fetch", help="Use the raw files already on disk.")
    ] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Download raw CSVs and load them into the stg_* tables."""
    setup("DEBUG" if verbose else "INFO")
    from dwm.ingest import ingest as run_ingest

    settings = _settings(db, sample, years, chunk)
    outcomes = run_ingest(
        settings,
        datasets=list(dataset) if dataset else None,
        force_download=force_download,
        skip_fetch=skip_fetch,
    )
    _echo_json(
        [
            {
                "dataset": o.code,
                "ok": o.ok,
                "table": o.staging.get("table"),
                "rows_read": o.staging.get("rows_read"),
                "rows_loaded": o.staging.get("rows_loaded"),
                "rows_rejected": o.staging.get("rows_rejected"),
                # v1 datasets never populate these; v2 stocks reports which
                # index members had no usable price history, and hiding that
                # would make a 47-stock index look like a 50-stock one.
                "unmatched": o.staging.get("unmatched"),
                "error": o.error,
            }
            for o in outcomes
        ]
    )
    if not all(o.ok for o in outcomes):
        raise typer.Exit(code=1)


@app.command()
def etl(
    db: DbOpt = None,
    sample: SampleOpt = None,
    years: YearsOpt = 5,
    chunk: ChunkOpt = 100_000,
) -> None:
    """Clean, dedupe, parse dates, map topics and apply the window."""
    from dwm.etl import run_etl

    settings = _settings(db, sample, years, chunk)
    _echo_json(run_etl(settings))


@app.command()
def features(
    db: DbOpt = None, sample: SampleOpt = None, years: YearsOpt = 5,
    chunk: ChunkOpt = 100_000,
) -> None:
    """Compute word counts, sentiment, sensationalism and keywords."""
    from dwm.features import run_features

    settings = _settings(db, sample, years, chunk)
    _echo_json(run_features(settings))


@app.command()
def build(
    db: DbOpt = None, sample: SampleOpt = None, years: YearsOpt = 5,
    chunk: ChunkOpt = 100_000,
) -> None:
    """Build the conformed dimensions, facts, bridge and cubes."""
    from dwm.warehouse import run_build

    settings = _settings(db, sample, years, chunk)
    _echo_json(run_build(settings))


@app.command()
def load(
    db: DbOpt = None, sample: SampleOpt = None, years: YearsOpt = 5,
    chunk: ChunkOpt = 100_000,
) -> None:
    """Alias of `build` (v2 name)."""
    build(db=db, sample=sample, years=years, chunk=chunk)


@app.command()
def infer(
    db: DbOpt = None, years: YearsOpt = 5,
    out: Annotated[Path | None, typer.Option("--out")] = None,
) -> None:
    """Alias of `report` (v2 name)."""
    report(db=db, years=years, out=out)


@app.command()
def olap(
    db: DbOpt = None,
    operation: Annotated[
        str | None,
        typer.Option("--op", help="Named operation: slice, slice_year, dice, roll_up, "
                                 "drill_down, top_months, pivot, cube, drill_across, "
                                 "topic_mix. Omit to list them."),
    ] = None,
    param: Annotated[
        list[str] | None,
        typer.Option("--param", "-p", help="Parameter as key=value, e.g. -p topic=Sports. Repeatable."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, help="Maximum rows printed.")] = 200,
) -> None:
    """Run OLAP operations: slice, dice, rollup, drill-down, pivot, drill-across."""
    from dwm.olap import run_olap

    settings = _settings(db, None, 5, 100_000)
    params: dict[str, Any] = {}
    for item in param or []:
        if "=" not in item:
            raise typer.BadParameter(f"--param expects key=value, got {item!r}")
        key, _, value = item.partition("=")
        params[key.strip()] = value.strip()

    result = run_olap(settings, operation, **params)
    # Truncation is stated in the payload, never silent.
    if "rows" in result and len(result["rows"]) > limit:
        result["meta"] = {
            **result.get("meta", {}),
            "truncated": True,
            "rows_available": len(result["rows"]),
            "rows_shown": limit,
        }
        result["rows"] = result["rows"][:limit]
    _echo_json(result)


@app.command()
def mine(
    db: DbOpt = None,
    years: YearsOpt = 5,
    sample: SampleOpt = None,
) -> None:
    """Run the mining stage: trends, bursts, clusters, rules, classifier."""
    from dwm.mining import run_mining

    settings = _settings(db, sample, years, 100_000)
    _echo_json(run_mining(settings))


@app.command()
def report(
    db: DbOpt = None, years: YearsOpt = 5, out: Annotated[Path | None, typer.Option("--out")] = None
) -> None:
    """Generate facts.json and report.md from the mining results.

    Renders only; it does not recompute. Exits non-zero when an inference
    guard fails, so a broken warehouse cannot pass silently.
    """
    from dwm.inference import run_inference

    settings = _settings(db, None, years, 100_000)
    result = run_inference(settings)
    if out is not None:
        import shutil

        destination = out if out.is_dir() else out.parent
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / "report.md" if out.is_dir() else out
        shutil.copyfile(result["report_path"], target)
        result["report_path"] = str(target)
    _echo_json(result)
    if not result["gate_passed"]:
        # Loud failure, by design: the blueprint's gate exists to stop a
        # pipeline passing on a warehouse that cannot support its claims.
        raise typer.Exit(code=1)


@app.command()
def audit(
    db: DbOpt = None, run: Annotated[str | None, typer.Option("--run", help="Run id.")] = None
) -> None:
    """Print the etl_audit funnel."""
    from dwm.db import connect
    from dwm.ingest.audit import funnel

    settings = _settings(db, None, 5, 100_000)
    con = connect(settings)
    try:
        _echo_json(funnel(con, run))
    finally:
        con.close()


@app.command()
def tables(db: DbOpt = None) -> None:
    """List warehouse tables with row counts."""
    from dwm.db import connect

    settings = _settings(db, None, 5, 100_000)
    con = connect(settings)
    try:
        names = [
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables ORDER BY table_name"
            ).fetchall()
        ]
        _echo_json(
            {name: int(con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]) for name in names}
        )
    finally:
        con.close()


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8000,
    reload: Annotated[bool, typer.Option("--reload")] = False,
    resnapshot: Annotated[
        bool,
        typer.Option(
            "--resnapshot/--no-resnapshot",
            help="Re-copy the warehouse even when the snapshot is already current.",
        ),
    ] = False,
) -> None:
    """Start the backend, which also serves the dashboard.

    Takes a copy of the warehouse to serve from. DuckDB locks its file
    exclusively even for a read-only connection, so serving the live file
    would block every CLI command for as long as the server ran. Serving a
    byte-identical snapshot keeps the dashboard and the CLI usable at the same
    time, which is what a demonstration needs.

    **This one process serves everything.** The dashboard is mounted as static
    files on the same application and reads the same endpoints on the same
    origin, so there is no second server to start and no CORS to configure. It
    used to be a separate Streamlit process on :8501, which meant two
    terminals to get wrong before a demonstration.
    """
    import uvicorn

    from dwm.api.store import DataUnavailable, ensure_snapshot
    from dwm.config import ensure_dirs as _ensure

    _ensure()
    try:
        snapshot = ensure_snapshot(force=resnapshot)
    except DataUnavailable as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"serving warehouse snapshot: {snapshot}")
    typer.echo(f"dashboard:  http://127.0.0.1:{port}/")
    typer.echo(f"api docs:   http://127.0.0.1:{port}/docs")
    uvicorn.run("dwm.api.app:app", host=host, port=port, reload=reload)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
