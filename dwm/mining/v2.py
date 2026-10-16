"""V2 mining dispatcher: one entry point over the five v2 analyses.

Each analysis is a module with its own `run(con)`, writes its own `mining_*`
table, and returns a summary carrying its own caveat. This module adds the
routing, the audit rows and the JSON side-output, and nothing else - so each
analysis can be read, tested and corrected on its own.

Results go to `reports/mining/v2.json`. The v1 `reports/mining.json` is left
exactly where it is.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import duckdb

from dwm.config import Settings, reports_dir
from dwm.db import connect
from dwm.ingest.audit import ensure_audit_table, new_run_id, record
from dwm.logging_utils import get, human_int, step

log = get("dwm.mining.v2")

__all__ = ["run", "OPERATIONS", "list_operations"]

#: operation name -> (module attribute, one-line description)
OPERATIONS: dict[str, str] = {
    "events": "Event study: abnormal returns around named events",
    "stockclusters": "Group stocks by behaviour (KMeans, silhouette-chosen k)",
    "marketrules": "Association rules over sector-day transactions",
    "direction": "Next-day direction classifier against two baselines",
    "surprise": "Days where news and price disagreed",
}

MODULES = {
    "events": ("dwm.mining.event_study", "run"),
    "stockclusters": ("dwm.mining.stock_clusters", "run"),
    "marketrules": ("dwm.mining.market_rules", "run"),
    "direction": ("dwm.mining.direction_model", "run"),
    "surprise": ("dwm.mining.surprise_days", "run"),
}

#: Order matters only for reporting; nothing depends on it. Surprise days run
#: before the direction model because the model summary quotes them.
ORDER = ["events", "surprise", "direction", "stockclusters", "marketrules"]

RESULTS_FILENAME = "v2.json"


def _load(name: str):
    import importlib

    module_name, attribute = MODULES[name]
    return getattr(importlib.import_module(module_name), attribute)


def list_operations() -> list[dict[str, str]]:
    return [{"name": k, "description": v} for k, v in OPERATIONS.items()]


def run_one(con: duckdb.DuckDBPyConnection, name: str) -> dict[str, Any]:
    """Run a single v2 analysis and wrap its summary with its description."""
    if name not in OPERATIONS:
        raise KeyError(f"unknown v2 mining operation {name!r}. Known: {sorted(OPERATIONS)}")
    fn = _load(name)
    summary = fn(con)
    if not isinstance(summary, dict):
        summary = {"value": summary}
    return {"operation": name, "description": OPERATIONS[name], **summary}


def run(
    settings: Settings,
    *,
    operation: str | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
) -> dict[str, Any]:
    """Run one v2 analysis, or all five."""
    # `all` is documented in AGENTS.md, so it is honoured here rather than
    # raising "unknown operation".
    if operation in (None, "all"):
        wanted = list(ORDER)
    elif operation in OPERATIONS:
        wanted = [operation]
    else:
        raise KeyError(
            f"unknown v2 mining operation {operation!r}. "
            f"Known: {sorted(OPERATIONS)} or 'all'"
        )

    owned = con is None
    con = con or connect(settings)
    run_id = new_run_id()
    started = datetime.now(UTC).replace(tzinfo=None)
    audit_ids: list[int] = []
    results: dict[str, Any] = {}

    try:
        ensure_audit_table(con)
        for name in wanted:
            with step(f"mining v2: {name}") as info:
                info.update(run_one(con, name))
            results[name] = info
            audit_ids.append(
                record(
                    con, run_id=run_id, dataset_code="mine", step=f"mine.v2.{name}",
                    info=info, status=info["status"], started_at=started,
                )
            )

        payload = {
            "run_id": run_id,
            "operations": list_operations(),
            "results": results,
        }
        path = _write(payload)
        payload["path"] = str(path)
        payload["audit_ids"] = audit_ids
        log.info("v2 mining results written to %s", path)
        return payload
    finally:
        if owned:
            con.close()


def _write(payload: dict[str, Any]):
    """Write reports/mining/v2.json, defaulting values that JSON cannot hold."""
    directory = reports_dir() / "mining"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / RESULTS_FILENAME
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    return path


def headline(payload: dict[str, Any]) -> list[str]:
    """One line per analysis, for a phase summary."""
    out: list[str] = []
    for name in ORDER:
        result = payload.get("results", {}).get(name)
        if not result:
            continue
        if name == "events":
            out.append(
                f"events: {human_int(result.get('valid', 0))} valid of "
                f"{human_int(result.get('results', 0))} stock-event pairs"
            )
        elif name == "surprise":
            out.append(
                f"surprise: {human_int(result.get('news_driven', 0))} news_driven, "
                f"{human_int(result.get('quiet_news_move', 0))} quiet_news_move"
            )
        elif name == "direction":
            out.append(f"direction: {result.get('verdict', '')}")
        elif name == "stockclusters":
            out.append(
                f"stockclusters: k={result.get('k')} "
                f"(silhouette {result.get('silhouette')})"
            )
        elif name == "marketrules":
            out.append(
                f"marketrules: {result.get('rules', 0)} rules from "
                f"{human_int(result.get('transactions', 0))} transactions"
            )
    return out
