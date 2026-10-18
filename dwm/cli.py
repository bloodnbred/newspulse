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
def link(
    db: DbOpt = None, sample: SampleOpt = None, years: YearsOpt = 5,
    chunk: ChunkOpt = 100_000,
    precision: Annotated[
        bool, typer.Option("--precision", help="Compute mention precision from the "
                          "human-filled reports/mention_audit_sample.csv instead of "
                          "re-linking.")
    ] = False,
) -> None:
    """Flag market reports, link headlines to stocks and sectors.

    Writes fact_news_stock_mention and fact_news_sector_daily, refreshes
    reports/data_quality.md, and writes a blank mention audit sample for a
    person to label.
    """
    if precision:
        from dwm.db import connect
        from dwm.linking.audit_sample import precision as compute_precision

        con = connect(_settings(db, sample, years, chunk))
        try:
            _echo_json(compute_precision(con))
        finally:
            con.close()
        return

    from dwm.linking import run_link

    settings = _settings(db, sample, years, chunk)
    _echo_json(run_link(settings))


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
    operation: Annotated[
        str | None,
        typer.Option("--op", help="Named v2 operation: events, stockclusters, "
                                  "marketrules, direction, surprise, all. "
                                  "Omit to run the seven v1 questions."),
    ] = None,
) -> None:
    """Run the mining stage.

    Without --op this runs the seven v1 research questions, unchanged. With
    --op it runs one of the five v2 analyses, or `all` for all five.
    """
    settings = _settings(db, sample, years, 100_000)

    if operation:
        from dwm.mining import v2

        _echo_json(v2.run(settings, operation=operation))
        return

    from dwm.mining import run_mining

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


@app.command("all")
def run_all(
    db: DbOpt = None,
    sample: SampleOpt = None,
    years: YearsOpt = 5,
    chunk: ChunkOpt = 100_000,
    skip_fetch: Annotated[
        bool, typer.Option("--skip-fetch", help="Use the raw files already on disk.")
    ] = False,
    skip_report: Annotated[
        bool, typer.Option("--skip-report", help="Stop after the mining stage.")
    ] = False,
) -> None:
    """Run the whole pipeline in order: ingest, etl, features, build, link, mine, report.

    This is the command a new machine needs. `AGENTS.md` has always documented
    `python -m dwm all`, and `start.ps1` calls it, but no such command existed -
    so the documented way to build a warehouse from scratch failed with
    "No such command 'all'", on a machine where the only thing you have is the
    setup script.

    Each stage calls the same function as its own subcommand rather than
    shelling out, so `dwm all` and running the seven commands by hand produce
    identical output. The order is the dependency order: dimensions before
    facts, facts before cubes, cubes before mining, mining before the report.

    A failure stops the run and names the stage. There is deliberately no
    `--force` that carries on past a broken stage, because every later stage
    would report a confidently wrong number.
    """
    stages: list[tuple[str, object]] = [
        # `--dataset all`, not the bare default. The default is the three v1
        # sources; without meta and stocks there is no constituent list, no
        # trading calendar and no per-company prices, and the `link` stage
        # fails asking for a `dwm build` that already ran.
        ("ingest", lambda: ingest(  # noqa: PLR1702
            db=db, sample=sample, years=years, chunk=chunk,
            dataset=["all"], force_download=False, skip_fetch=skip_fetch, verbose=False,
        )),
        ("etl", lambda: etl(db=db, sample=sample, years=years, chunk=chunk)),
        ("features", lambda: features(db=db, sample=sample, years=years, chunk=chunk)),
        ("build", lambda: build(db=db, sample=sample, years=years, chunk=chunk)),
        ("link", lambda: link(db=db, sample=sample, years=years, chunk=chunk)),
        ("mine", lambda: mine(db=db, sample=sample, years=years, operation="all")),
    ]
    if not skip_report:
        stages.append(("report", lambda: report(db=db, years=years, out=None)))

    for name, run in stages:
        get("dwm").info("pipeline stage: %s", name)
        try:
            run()  # type: ignore[operator]
        except SystemExit as exc:  # `report` raises Exit(1) on a failed gate
            raise typer.Exit(code=exc.code) from exc
        except Exception as exc:  # noqa: BLE001 - re-raised with the stage name
            get("dwm").error("pipeline stage '%s' failed: %s", name, exc)
            raise typer.Exit(code=1) from exc
    get("dwm").info("pipeline complete: %s", " -> ".join(n for n, _ in stages))


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
    """Start the API server.

    Takes a copy of the warehouse to serve from. DuckDB locks its file
    exclusively even for a read-only connection, so serving the live file
    would block every CLI command for as long as the server ran. Serving a
    byte-identical snapshot keeps the API and the CLI usable at the same time,
    which is what a demonstration needs.

    This serves the API only. The dashboard is the React app in `web/`, started
    with `npm run dev`; `start.ps1` and `start.sh` start both. This process used
    to mount the v1 vanilla-JS dashboard as static files on the same origin,
    which meant one terminal instead of two - but it also meant two front ends
    reading the same endpoints, and only one of them is maintained.
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
