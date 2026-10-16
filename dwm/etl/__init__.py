"""ETL stage: clean, dedupe, parse dates, map topics, apply the window.

Phase 2 of BLUEPRINT section 4. Produces the cleaned tables and the five
conformed dimensions. Outputs:

    cln_headline, cln_statement, cln_market_daily
    dim_date, dim_topic, dim_dataset, dim_label, dim_instrument
    map_category_topic   (the raw-category to topic mapping, kept for audit)

Design decisions that affect the numbers are documented in `dwm/etl/clean.py`
and `docs/01-ingest-etl.md`. The short version:

  * headline grain is (date, text): 111,146 (date, text) pairs are filed under
    more than one category, so one row per category would multiply-count them
  * the window is a flag, not a filter, so the funnel can report what it drops
  * IFND `date_key` is nullable, because 33% of its dates are month-only or
    absent
"""

from __future__ import annotations

from typing import Any

import duckdb

from dwm.config import (
    Settings,
    load_datasets_config,
    load_sources_config,
    load_topic_map,
)
from dwm.db import connect
from dwm.etl.clean import build_clean
from dwm.etl.dates import register_date_functions
from dwm.etl.dims import build_dims, dim_date_bounds
from dwm.ingest.audit import ensure_audit_table, new_run_id
from dwm.logging_utils import get, human_int, step

__all__ = ["run_etl", "build_clean", "build_dims", "dim_date_bounds"]

log = get("dwm.etl")

CLEAN_TABLES = ("cln_headline", "cln_statement", "cln_market_daily")
DIM_TABLES = (
    "dim_date", "dim_topic", "dim_dataset", "dim_label", "dim_instrument",
)


def staging_ready(con) -> list[str]:
    """Datasets that have no staging table yet."""
    from dwm.ingest.staging import stage_table_name

    specs = load_datasets_config()
    missing = []
    for code, spec in specs.items():
        if not spec.enabled:
            continue
        present = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            [stage_table_name(code)],
        ).fetchone()[0]
        if not present:
            missing.append(code)
    return missing


def run_etl(
    settings: Settings, *, con: duckdb.DuckDBPyConnection | None = None
) -> dict[str, Any]:
    """Run the whole ETL stage. Returns a summary for the CLI.

    Pass `con` to reuse an existing connection. DuckDB does not support two
    write connections on one file, so a caller that has already staged data
    must hand its connection over rather than have a second one opened.
    """
    owned = con is None
    con = con or connect(settings)
    try:
        ensure_audit_table(con)

        missing = staging_ready(con)
        if missing:
            raise RuntimeError(
                f"no staging table for {missing}. Run `python -m dwm ingest` first."
            )

        run_id = new_run_id()
        specs = load_datasets_config()
        topic_map = load_topic_map()

        # The IFND date UDFs are connection-scoped and both steps need them:
        # dim_date spans IFND's range, and cln_statement carries the precision.
        register_date_functions(con, specs["ifnd"])

        with step("build dimensions") as dim_info:
            # The v2 sources are passed through so dim_dataset carries `stocks`
            # and `meta` with their provenance. Without them fact_stock_daily
            # has no dataset_key to point at.
            counts = build_dims(
                con, topic_map=topic_map, specs=specs, sources=load_sources_config()
            )
            dim_info["row_counts"] = counts

        with step("build clean tables"):
            info = build_clean(
                con, specs=specs, topic_map=topic_map, settings=settings, run_id=run_id
            )

        gates = check_gates(con, strict=False)
        summary: dict[str, Any] = {
            "run_id": run_id,
            "dimensions": counts,
            "window": {
                "start": info["window_start"],
                "end": info["window_end"],
                "years": info["window_years"],
                "max_publish_date": info["window_max_publish_date"],
            },
            "clean": {
                "rows": info["after"],
                "in_window": info["in_window"],
                "duplicates_removed": info["duplicates_removed"],
                "multi_category_headlines": info["multi_category_headlines"],
                "statement_date_precision": info["statement_date_precision"],
                "statement_without_date": info["statement_without_date"],
                "category_map": info["category_map"],
            },
            "gates": gates,
        }

        for table, n in info["after"].items():
            log.info("%s: %s rows", table, human_int(int(n)))
        return summary
    finally:
        if owned:
            con.close()


def check_gates(con, *, strict: bool) -> dict[str, Any]:
    """Phase 2 gate: no null date keys, every raw category maps to a topic.

    `strict` is False during the run, where the result is reported; set it to
    True from a test to assert. IFND is exempt from the date-key rule by
    design, because a third of its rows have no resolvable calendar date, and
    that exemption is asserted explicitly rather than hidden.
    """
    def scalar(sql: str) -> int:
        return int(con.execute(sql).fetchone()[0])

    headline_null_dates = scalar(
        "SELECT count(*) FROM cln_headline WHERE date_key IS NULL"
    )
    market_null_dates = scalar(
        "SELECT count(*) FROM cln_market_daily WHERE date_key IS NULL"
    )
    statement_null_dates = scalar(
        "SELECT count(*) FROM cln_statement WHERE date_key IS NULL"
    )
    unmapped_categories = scalar(
        "SELECT count(*) FROM cln_headline WHERE topic_key IS NULL"
    )
    orphan_date_keys = scalar(
        """
        SELECT count(*) FROM cln_headline f
        WHERE f.date_key IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM dim_date d WHERE d.date_key = f.date_key)
        """
    )
    orphan_topic_keys = scalar(
        """
        SELECT count(*) FROM cln_headline f
        WHERE f.topic_key IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM dim_topic t WHERE t.topic_key = f.topic_key)
        """
    )

    gates = {
        "headline_null_date_keys": headline_null_dates,
        "market_null_date_keys": market_null_dates,
        "statement_null_date_keys": statement_null_dates,
        "unmapped_categories": unmapped_categories,
        "orphan_date_keys": orphan_date_keys,
        "orphan_topic_keys": orphan_topic_keys,
    }
    gates["passed"] = (
        headline_null_dates == 0
        and market_null_dates == 0
        and unmapped_categories == 0
        and orphan_date_keys == 0
        and orphan_topic_keys == 0
    )
    gates["statement_dates_nullable_by_design"] = statement_null_dates
    gates["note"] = (
        "IFND date_key is nullable by design: 12.6% of its rows carry a day "
        "with no year and 20.2% carry no date at all, so no year is invented."
    )
    if strict and not gates["passed"]:
        raise AssertionError(f"Phase 2 gate failed: {gates}")
    return gates
