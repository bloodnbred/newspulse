"""Warehouse build stage (Phase 3): fact tables, bridge, cubes.

Takes the clean tables and the feature tables from the feature stage and
produces the queryable warehouse:

    fact_headline, fact_statement, fact_market_daily
    cube_month_topic, cube_year_topic, cube_topic_sentiment, cube_day_topic

(dim_keyword and bridge_headline_keyword are written by the feature stage,
because keyword extraction has to happen in the same pass as scoring.)

The Phase 3 gate is *"fact row counts equal clean row counts; keys unique"*.
It is checked in `check_gates` and is enforced with `strict=True` by the
tests.
"""

from __future__ import annotations

from typing import Any

import duckdb

from dwm.config import Settings
from dwm.ingest.audit import ensure_audit_table, new_run_id
from dwm.logging_utils import get, human_int, step
from dwm.warehouse.cubes import build_cubes
from dwm.warehouse.facts import build_facts

__all__ = ["run_build", "build_facts", "build_cubes", "check_gates"]

log = get("dwm.warehouse")

FACT_TABLES = ("fact_headline", "fact_statement", "fact_market_daily")
CLEAN_TABLES = ("cln_headline", "cln_statement", "cln_market_daily")
CUBE_TABLES = ("cube_month_topic", "cube_year_topic", "cube_topic_sentiment", "cube_day_topic")


def run_build(settings: Settings, *, con: duckdb.DuckDBPyConnection | None = None) -> dict[str, Any]:
    """Build facts, then cubes, then check the gate."""
    owned = con is None
    if owned:
        from dwm.db import connect

        con = connect(settings)
    try:
        ensure_audit_table(con)
        run_id = new_run_id()

        with step("build fact tables"):
            facts = build_facts(con, run_id=run_id)

        with step("build cubes"):
            cubes = build_cubes(con)

        # v2 dimensions. Skipped with a warning when their staging tables are
        # absent, so a v1-only run (the default `python -m dwm build`) behaves
        # exactly as it did before v2 rather than failing on a missing dataset.
        stock_dims = _build_v2_dims(con)

        # fact_stock_daily needs stg_stocks, so it is skipped with a warning
        # on a v1-only run rather than failing the whole build.
        stock_daily = _build_stock_facts(con)

        gates = check_gates(con, strict=False)
        for name, n in facts.items():
            log.info("%s: %s rows", name, human_int(n))
        return {
            "run_id": run_id,
            "facts": facts,
            "cubes": cubes,
            "stock_dims": stock_dims,
            "stock_daily": stock_daily,
            "gates": gates,
        }
    finally:
        if owned:
            con.close()


def _build_stock_facts(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    from dwm.warehouse import stock_facts

    if not con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'stg_stocks'"
    ).fetchone()[0]:
        log.info(
            "fact_stock_daily skipped: run "
            "`python -m dwm ingest --dataset stocks` first"
        )
        return {"built": False}

    with step("build fact_stock_daily"):
        counts = stock_facts.build_stock_daily(con)
    from dwm.warehouse import quality

    report = quality.write(con)
    return {
        "built": True,
        **counts,
        "check": stock_facts.check(con),
        "coverage": stock_facts.coverage(con),
        "top_outliers": stock_facts.outlier_rows(con, limit=10),
        "quality_report": report,
    }


def _build_v2_dims(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Build the v2 dimensions and the trading calendar, if their inputs exist."""
    from dwm.config import load_events, load_sector_config, load_stock_aliases
    from dwm.etl import calendar as calendar_mod
    from dwm.etl import dims as dims_mod

    present = {
        r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
    if not ({"stg_meta", "stg_stocks"} & present):
        log.info(
            "v2 dimensions skipped: run `python -m dwm ingest --dataset stocks --dataset meta` first"
        )
        return {"built": False}

    window = calendar_mod.window_bounds(con)
    result: dict[str, Any] = {"built": True, "window": list(window)}

    with step("build trading calendar"):
        result["calendar"] = calendar_mod.build(con)

    with step("build v2 dimensions"):
        result["dims"] = dims_mod.build_stock_dims(
            con,
            sector_cfg=load_sector_config(),
            alias_rules=load_stock_aliases(),
            events=load_events(),
            window=window,
        )
    result["calendar_check"] = calendar_mod.check(con)
    return result


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    return int(con.execute(sql).fetchone()[0])


def check_gates(con: duckdb.DuckDBPyConnection, *, strict: bool) -> dict[str, Any]:
    """Phase 3 gate: fact counts equal clean counts, and keys are unique.

    The count check is the one that matters most: a fact built with an inner
    join to the feature table silently drops any clean row the feature stage
    failed to score, so the counts are compared rather than assumed.
    """
    present = {
        r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
    if not set(FACT_TABLES) <= present:
        missing = sorted(set(FACT_TABLES) - present)
        return {"passed": False, "reason": f"missing fact tables {missing}"}

    pairs = (
        ("fact_headline", "cln_headline", "headline_id"),
        ("fact_statement", "cln_statement", "statement_id"),
        ("fact_market_daily", "cln_market_daily", "market_id"),
    )
    counts: dict[str, dict[str, int]] = {}
    mismatched: dict[str, dict[str, int]] = {}
    for fact, clean, key in pairs:
        f = _scalar(con, f"SELECT count(*) FROM {fact}")
        c = _scalar(con, f"SELECT count(*) FROM {clean}")
        counts[fact] = {"facts": f, "clean": c}
        if f != c:
            mismatched[fact] = {"facts": f, "clean": c}

    key_checks = {
        "fact_headline_id_not_unique": _scalar(
            con, "SELECT count(*) FROM (SELECT headline_id FROM fact_headline "
                 "GROUP BY 1 HAVING count(*) > 1) t"
        ),
        "fact_statement_id_not_unique": _scalar(
            con, "SELECT count(*) FROM (SELECT statement_id FROM fact_statement "
                 "GROUP BY 1 HAVING count(*) > 1) t"
        ),
        "fact_market_id_not_unique": _scalar(
            con, "SELECT count(*) FROM (SELECT market_id FROM fact_market_daily "
                 "GROUP BY 1 HAVING count(*) > 1) t"
        ),
        "bridge_duplicate_pairs": _scalar(
            con, "SELECT count(*) FROM (SELECT headline_id, keyword_key "
                 "FROM bridge_headline_keyword GROUP BY 1,2 HAVING count(*) > 1) t"
        ),
        "bridge_orphan_headline": _scalar(
            con, "SELECT count(*) FROM bridge_headline_keyword b "
                 "WHERE NOT EXISTS (SELECT 1 FROM fact_headline h "
                 "WHERE h.headline_id = b.headline_id)"
        ),
        "bridge_orphan_keyword": _scalar(
            con, "SELECT count(*) FROM bridge_headline_keyword b "
                 "WHERE NOT EXISTS (SELECT 1 FROM dim_keyword k "
                 "WHERE k.keyword_key = b.keyword_key)"
        ),
        "headline_orphan_date_key": _scalar(
            con, "SELECT count(*) FROM fact_headline f WHERE NOT EXISTS "
                 "(SELECT 1 FROM dim_date d WHERE d.date_key = f.date_key)"
        ),
        "headline_orphan_topic_key": _scalar(
            con, "SELECT count(*) FROM fact_headline f WHERE NOT EXISTS "
                 "(SELECT 1 FROM dim_topic t WHERE t.topic_key = f.topic_key)"
        ),
        "statement_label_not_in_dim": _scalar(
            con, "SELECT count(*) FROM fact_statement s WHERE NOT EXISTS "
                 "(SELECT 1 FROM dim_label l WHERE l.label_code = s.label_code)"
        ),
    }

    # Cube totals must reconcile with the fact table they summarise.
    cube_checks = {
        "cube_month_topic_total": _scalar(con, "SELECT coalesce(sum(headline_count), 0) FROM cube_month_topic"),
        "cube_year_topic_total": _scalar(con, "SELECT coalesce(sum(headline_count), 0) FROM cube_year_topic"),
        "cube_topic_sentiment_total": _scalar(
            con, "SELECT coalesce(sum(headline_count), 0) FROM cube_topic_sentiment"
        ),
        "cube_day_topic_total": _scalar(con, "SELECT coalesce(sum(headline_count), 0) FROM cube_day_topic"),
        "fact_headline_in_window": _scalar(
            con, "SELECT count(*) FROM fact_headline WHERE in_window"
        ),
    }

    passed = (
        not mismatched
        # Every key check must be zero. `not key_checks` would be False
        # whenever the dict is non-empty, which it always is.
        and not any(key_checks.values())
        and cube_checks["cube_month_topic_total"] == cube_checks["fact_headline_in_window"]
        and cube_checks["cube_year_topic_total"] == cube_checks["fact_headline_in_window"]
        and cube_checks["cube_topic_sentiment_total"] == cube_checks["fact_headline_in_window"]
        and cube_checks["cube_day_topic_total"] == cube_checks["fact_headline_in_window"]
    )

    gates: dict[str, Any] = {
        "counts": counts,
        "count_mismatches": mismatched,
        "key_violations": {k: v for k, v in key_checks.items() if v},
        "cube_reconciliation": cube_checks,
        "passed": passed,
    }
    if strict and not passed:
        raise AssertionError(f"Phase 3 gate failed: {gates}")
    return gates
