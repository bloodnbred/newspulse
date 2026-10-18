"""Ingest orchestration: fetch a dataset, then stage it, then audit it.

The audit row is written whether the stage succeeded or failed, so a broken
run leaves a visible trail instead of no trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dwm.config import DatasetSpec, Settings, load_datasets_config
from dwm.db import connect
from dwm.ingest import audit
from dwm.ingest.fetch import FetchError, FetchResult, fetch_dataset
from dwm.ingest.staging import stage_dataset, stage_table_name
from dwm.logging_utils import get, human_int, step

log = get("dwm.ingest")


@dataclass(slots=True)
class IngestOutcome:
    code: str
    fetch: FetchResult | None
    staging: dict[str, Any]
    audit_ids: list[int]
    ok: bool
    error: str | None = None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def ingest_one(
    spec: DatasetSpec,
    settings: Settings,
    *,
    force_download: bool = False,
    skip_fetch: bool = False,
) -> IngestOutcome:
    """Fetch and stage one dataset, writing audit rows as it goes."""
    con = connect(settings)
    run_id = audit.new_run_id()
    audit.ensure_audit_table(con)
    ids: list[int] = []
    fetch_result: FetchResult | None = None

    try:
        if skip_fetch and spec.local_path().exists():
            log.info("%s: skipping fetch, using %s", spec.code, spec.local_path())
            fetch_result = None
        else:
            started = _now()
            with step(f"fetch {spec.code}") as info:
                fetch_result = fetch_dataset(spec, force=force_download)
                info.update(
                    bytes_downloaded=fetch_result.bytes_downloaded,
                    sha256=fetch_result.sha256,
                    reused=fetch_result.reused,
                    source_file=spec.filename,
                )
            ids.append(
                audit.record(
                    con, run_id=run_id, dataset_code=spec.code, step="ingest.fetch",
                    info=info, status=info["status"], started_at=started,
                )
            )

        path = spec.local_path()
        if not path.exists():
            raise FileNotFoundError(f"{spec.code}: expected {path}")

        started = _now()
        with step(f"stage {spec.code} -> {stage_table_name(spec.code)}") as info:
            info.update(stage_dataset(con, spec, path, settings=settings))
        ids.append(
            audit.record(
                con, run_id=run_id, dataset_code=spec.code, step="ingest.stage",
                info=info, status=info["status"], started_at=started,
            )
        )
        return IngestOutcome(spec.code, fetch_result, info, ids, ok=True)

    except (FetchError, FileNotFoundError, KeyError) as exc:
        log.error("%s: ingest failed: %s", spec.code, exc)
        started = _now()
        info = {"error": str(exc)}
        try:
            ids.append(
                audit.record(
                    con, run_id=run_id, dataset_code=spec.code, step="ingest.stage",
                    info=info, status="failed", started_at=started,
                )
            )
        except Exception:  # pragma: no cover - audit must never mask the cause
            log.exception("could not write failure audit row for %s", spec.code)
        return IngestOutcome(spec.code, fetch_result, info, ids, ok=False, error=str(exc))
    finally:
        con.close()


V2_DATASETS = ("meta", "stocks")


def ingest_v2(
    settings: Settings,
    *,
    datasets: list[str],
    force_download: bool = False,
    skip_fetch: bool = False,
) -> dict[str, Any]:
    """Ingest the v2 sources (stocks, meta) through their own loaders.

    They are not v1 DatasetSpecs: `stocks` is a per-symbol glob rather than one
    file at one URL, so they cannot go through ingest_one. They share the same
    connection, the same etl_audit table and the same staged-table convention.
    """
    from dwm.config import load_sources_config
    from dwm.ingest import meta as meta_ingest
    from dwm.ingest import stocks as stocks_ingest

    specs = load_sources_config()
    unknown = [d for d in datasets if d not in V2_DATASETS]
    if unknown:
        raise KeyError(f"unknown v2 dataset(s) {unknown}; known: {list(V2_DATASETS)}")

    con = connect(settings)
    results: dict[str, Any] = {}
    try:
        # meta first: stocks reads its symbol list out of stg_meta.
        if "meta" in datasets:
            results["meta"] = _as_dict(
                meta_ingest.ingest(
                    con, specs.get("meta"),
                    force=force_download, skip_fetch=skip_fetch,
                )
            )
        if "stocks" in datasets:
            results["stocks"] = _as_dict(
                stocks_ingest.ingest(
                    con, settings, specs.get("stocks"),
                    force=force_download, skip_fetch=skip_fetch,
                )
            )
    finally:
        con.close()
    return results


def _as_dict(outcome: Any) -> dict[str, Any]:
    """Flatten a v2 outcome dataclass into something JSON-printable."""
    payload: dict[str, Any] = {}
    for field_ in outcome.__dataclass_fields__:
        value = getattr(outcome, field_)
        if isinstance(value, Path):
            value = str(value)
        elif isinstance(value, list):
            value = [dict(v) if isinstance(v, dict) else str(v) for v in value]
        payload[field_] = value
    return payload


def _as_outcome(code: str, payload: dict[str, Any]) -> IngestOutcome:
    """Present a v2 result in the same shape as a v1 one.

    The CLI prints table / rows_read / rows_loaded from `staging`, so a v2
    outcome that does not use those key names makes `python -m dwm ingest
    --dataset stocks` print a row of nulls and look like it had done nothing.
    """
    staging = dict(payload.get("staging") or {})
    audit_id = payload.get("audit_id")
    info = {k: v for k, v in staging.items() if k != "status"}
    info.setdefault("table", f"stg_{code}")
    if "rows_loaded" not in info and payload.get("rows") is not None:
        info["rows_loaded"] = payload["rows"]
    info.setdefault("rows_read", info.get("rows_loaded", 0))
    info.setdefault("rows_rejected", 0)
    if payload.get("unmatched"):
        info["unmatched"] = payload["unmatched"]
    return IngestOutcome(
        code=code,
        fetch=None,
        staging=info,
        audit_ids=[int(audit_id)] if audit_id else [],
        ok=bool(payload.get("ok", True)),
        error=payload.get("error"),
    )


def ingest(
    settings: Settings, *, datasets: list[str] | None = None, force_download: bool = False,
    skip_fetch: bool = False,
) -> list[IngestOutcome]:
    """Ingest the requested datasets, defaulting to every enabled v1 one.

    `datasets=None` means the v1 sources only, which is what it has always
    meant and what the v1 tests assume. `--dataset all` is the way to ask for
    the v2 sources as well, and it is the only spelling that reaches both loaders.

    That distinction was documented in `AGENTS.md` and never implemented:
    `--dataset all` fell through to the unknown-name check and raised
    `KeyError('unknown dataset(s): [all]')`, so the one command a new machine
    needs could not fetch the constituent list or the per-company prices. With
    no `stg_meta`, `dim_trading_calendar` is never built, and the `link` stage
    then stops with a message telling you to run a command you had just run.
    """
    specs = load_datasets_config()
    v2_results: list[IngestOutcome] = []
    if datasets is None:
        wanted = sorted(specs)
        v2_wanted: list[str] = []
    else:
        # `all` expands to every v1 spec plus both v2 loaders. The v2 sources
        # are routed to `ingest_v2` rather than rejected, so `--dataset all` and
        # `--dataset stocks` both behave as documented.
        requested: list[str] = []
        for name in datasets:
            if name == "all":
                requested.extend(sorted(specs))
                requested.extend(V2_DATASETS)
            else:
                requested.append(name)
        # `all` alongside an explicit name would otherwise list it twice.
        requested = list(dict.fromkeys(requested))
        wanted = [d for d in requested if d not in V2_DATASETS]
        v2_wanted = [d for d in requested if d in V2_DATASETS]
        unknown = [d for d in requested if d not in specs and d not in V2_DATASETS]
        if unknown:
            raise KeyError(
                f"unknown dataset(s) {unknown}; known: "
                f"{sorted(set(specs) | set(V2_DATASETS) | {'all'})}"
            )
        if v2_wanted:
            v2_results = [
                _as_outcome(code, payload)
                for code, payload in ingest_v2(
                    settings, datasets=v2_wanted,
                    force_download=force_download, skip_fetch=skip_fetch,
                ).items()
            ]

    outcomes: list[IngestOutcome] = []
    for code in wanted:
        spec = specs[code]
        if not spec.enabled and datasets is None:
            log.info("%s: disabled in config, skipping", code)
            continue
        outcome = ingest_one(
            spec, settings, force_download=force_download, skip_fetch=skip_fetch
        )
        outcomes.append(outcome)
        if outcome.ok:
            loaded = outcome.staging.get("rows_loaded", 0)
            log.info("%s: staged %s rows", code, human_int(int(loaded)))
    return outcomes + v2_results


def load_raw(
    settings: Settings, datasets: list[str] | None = None
) -> dict[str, Path]:
    """Paths of raw files already present, without downloading anything."""
    specs = load_datasets_config()
    found: dict[str, Path] = {}
    for code, spec in specs.items():
        if datasets and code not in datasets:
            continue
        path: Path = spec.local_path()
        if path.exists():
            found[code] = path
        else:
            log.warning("%s: no raw file at %s", code, path)
    return found
