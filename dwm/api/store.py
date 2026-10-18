"""The query layer behind the API and the dashboard (Phase 8).

**The API serves results; it does not recompute them.** Every figure it returns
comes from `facts.json`, which the inference stage rendered from the warehouse.
An endpoint that ran its own SQL would be a second, unreviewed path to the
same numbers, and the two would drift.

One exception, and it is deliberate: `/query` runs the Phase 4 OLAP operations
against the warehouse, because those exist precisely to answer ad-hoc slicing
questions. It is read-only, whitelisted to the ten named operations, and every
response states which operation ran.

**Read-only is a property of the file, not a promise.** The DuckDB file is
opened `read_only=True`, so a malformed parameter cannot write to the
warehouse even if validation were bypassed.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import threading
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb

from dwm.config import default_db_path, reports_dir
from dwm.db import connect
from dwm.logging_utils import get

log = get("dwm.api.store")

SNAPSHOT_NAME = "dwm.serve.duckdb"


# Resolved when used, not at import. A module-level constant froze the reports
# directory at import time, so redirection depended on whether this module
# happened to be imported before or after the environment was set — which
# produced a "results not found" 503 while the file was sitting on disk.
def facts_path() -> Path:
    return reports_dir() / "facts.json"


def report_path() -> Path:
    return reports_dir() / "report.md"


class DataUnavailable(RuntimeError):
    """Raised when the warehouse or the results file is not there yet.

    A distinct type so the API can answer 503 with an actionable message
    instead of a 500 that reads like a bug.
    """


def _read_only_settings():
    from dwm.config import Settings

    return Settings(db_path=snapshot_path(), read_only=True)


def snapshot_path() -> Path:
    """Where the served copy of the warehouse lives."""
    return default_db_path().parent / SNAPSHOT_NAME


def ensure_snapshot(force: bool = False) -> Path:
    """Copy the warehouse to a snapshot the API can hold open.

    **DuckDB takes an exclusive lock on the file even for a read-only
    connection.** One process holding the warehouse therefore blocks every
    other process, and the consequence for a demonstration is bad: with the API
    running, `dwm olap`, `dwm tables` and `dwm audit` all fail with "the
    process cannot access the file because it is being used by another
    process". You would have to stop the dashboard to show a CLI command and
    stop the CLI to show the dashboard.

    Serving a copy removes the conflict entirely, and it is the right shape
    anyway: a read-only API answering from a point-in-time snapshot cannot be
    half-way through a rebuild, and the numbers it returns belong to a specific
    run rather than to whatever the file holds at the moment of the request.

    The copy is byte-exact, so every count and every OLAP result is identical
    to querying the live warehouse. It costs about 0.2 seconds and 424 MB, and
    is refreshed by restarting `dwm serve` or by POSTing `/query/reload`.
    """
    source = default_db_path()
    if not source.exists():
        raise DataUnavailable(
            f"no warehouse at {source}. Run `.\\start.ps1` (Windows) or "
            "`./start.sh` (Mac/Linux) to build it, or "
            "`python -m dwm all` if the environment is already set up."
        )
    target = snapshot_path()
    if target.exists() and not force and target.stat().st_mtime >= source.stat().st_mtime:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    # A stale -wal alongside a cleanly-closed database would make the copy
    # incomplete, so it is removed rather than trusted.
    wal = source.with_suffix(source.suffix + ".wal")
    if wal.exists():
        wal.unlink()

    try:
        shutil.copyfile(source, target)
    except PermissionError as exc:
        # On Windows DuckDB holds its file with a share mode that denies
        # reading, so the source cannot be copied while a build or another
        # process has it open. Keeping the existing snapshot is the right
        # answer: serving slightly older numbers beats serving nothing, and the
        # next refresh picks up the rebuild.
        if target.exists():
            log.warning(
                "warehouse is locked, keeping the existing snapshot (%s); "
                "restart `dwm serve` to pick up a rebuild",
                target,
            )
            return target
        raise DataUnavailable(
            f"cannot copy {source} to a serving snapshot: the file is in use. "
            "Close any running build or another `dwm serve`, then start again."
        ) from exc

    stale_wal = target.with_suffix(target.suffix + ".wal")
    if stale_wal.exists():
        stale_wal.unlink()
    log.info("warehouse snapshot: %s (%.0f MB)", target, target.stat().st_size / 1_048_576)
    return target


@lru_cache(maxsize=1)
def _open_connection():
    """One long-lived read-only handle on the warehouse snapshot."""
    return connect(_read_only_settings())


_LOCK = threading.RLock()


@contextlib.contextmanager
def reading() -> Iterator[duckdb.DuckDBPyConnection]:
    """Hold the shared warehouse handle for one complete read.

    THE LOCK LIVES HERE because this module owns the connection, and because
    every router that reads the warehouse has to come through it.

    `_open_connection()` is `@lru_cache`d, so every caller shares ONE handle.
    And `con.execute()` returns the connection while the row is read by a
    separate `fetchall()`/`fetchone()`, so two threads interleaving between
    those two calls read each other's rows.

    That is not hypothetical, and this module had the bug twice.

    Once as a `TypeError: 'NoneType' object is not subscriptable`, and once -
    worse, because it returned a well-formed answer - as
    `ValueError: zip() argument 2 is shorter than argument 1`, meaning column
    names from one query zipped onto rows from a different one.

    And once on `/health`, as `SELECT count(*) FROM 2015-06-30`.
    `table_counts()` lists table names and interpolates each into a `count(*)`.
    A thread interleaved there handed it a `publish_date` value from a
    concurrent request, and it built a query against a table called
    "2015-06-30". The v2 and ui routers had been locked and this module had
    not, so the same defect stayed live on every v1 route while looking fixed.
    """
    with _LOCK:
        yield _open_connection()


def connection():
    """The raw shared handle.

    Prefer `with reading() as con:`. This stays for callers outside this
    module that have not been converted, and returning an UNLOCKED handle
    is the trap: a lock that only some readers take protects nothing.
    """
    return _open_connection()


def reset_connection() -> None:
    """Drop the cached handle so the next read reopens it.

    Held under the lock, because dropping a handle another thread is mid-query
    on is the exact failure this module exists to prevent.
    """
    with _LOCK:
        _open_connection.cache_clear()


def _load_facts() -> dict[str, Any]:
    path = facts_path()
    if not path.exists():
        raise DataUnavailable(
            f"{path} not found. Run `python -m dwm report` to build it."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _load_report_markdown() -> str:
    path = report_path()
    if not path.exists():
        raise DataUnavailable(
            f"{path} not found. Run `python -m dwm report` to build it."
        )
    return path.read_text(encoding="utf-8")


# Cached, because re-reading a 72 KB file per request buys nothing while the
# results file cannot change underneath a running server. `reload()` drops
# them, so a rebuilt warehouse is picked up without a restart.
load_facts = lru_cache(maxsize=1)(_load_facts)
load_report_markdown = lru_cache(maxsize=1)(_load_report_markdown)


def reload() -> None:
    """Drop the caches and re-snapshot, so a rebuilt warehouse is picked up.

    The snapshot is refreshed as well as the caches, because a warehouse that
    was rebuilt while the API was running is newer than the copy being served.
    """
    _open_connection.cache_clear()
    load_facts.cache_clear()
    load_report_markdown.cache_clear()
    # A missing warehouse is not this function's problem to report: the next
    # request raises DataUnavailable with the command to run.
    with contextlib.suppress(DataUnavailable):
        ensure_snapshot(force=True)


# ---------------------------------------------------------------------------
# warehouse-backed queries, for the interactive part of the dashboard
# ---------------------------------------------------------------------------


def execute(operation: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one of the Phase 4 OLAP operations, read-only."""
    from dwm.olap import OPERATIONS, run_operation

    if operation not in OPERATIONS:
        raise KeyError(
            f"unknown operation {operation!r}; available: {sorted(OPERATIONS)}"
        )
    with reading() as con:
        return run_operation(con, operation, **(params or {}))


def topics() -> list[str]:
    with reading() as con:
        return [r[0] for r in con.execute(
            "SELECT topic_name FROM dim_topic ORDER BY topic_name"
        ).fetchall()]


def headline_sample(
    topic: str | None = None, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    """A page of headlines, for the dashboard's browse panel.

    Paged rather than returned whole: the corpus is 3.1 million rows, and an
    endpoint that tried to serialise it would exhaust memory in the client.
    """
    with reading() as con:
        where = "WHERE f.in_window"
        args: list[Any] = []
        if topic:
            # Bound parameter, never string-formatted into the SQL.
            where += " AND t.topic_name = ?"
            args.append(topic)
        total = con.execute(
            f"SELECT count(*) FROM fact_headline f "
            f"JOIN dim_topic t ON t.topic_key = f.topic_key {where}",
            args,
        ).fetchone()[0]
        rows = con.execute(
            f"""
            SELECT f.publish_date, t.topic_name, f.headline_text,
                   f.sentiment_compound, f.sentiment_band, f.sensational_score,
                   f.is_risk_signal, f.raw_category
            FROM fact_headline f
            JOIN dim_topic t ON t.topic_key = f.topic_key
            {where}
            ORDER BY f.publish_date DESC, f.headline_id
            LIMIT ? OFFSET ?
            """,
            [*args, limit, offset],
        ).fetchall()
        columns = [
            "publish_date", "topic", "headline_text", "sentiment_compound",
            "sentiment_band", "sensational_score", "is_risk_signal", "raw_category",
        ]
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "rows": [dict(zip(columns, r, strict=True)) for r in rows],
        }


def monthly_series(topic: str | None = None) -> list[dict[str, Any]]:
    """Headline count and risk-signal rate per month, for the trend chart."""
    with reading() as con:
        where = "WHERE 1 = 1"
        args: list[Any] = []
        if topic:
            where += " AND t.topic_name = ?"
            args.append(topic)
        rows = con.execute(
            f"""
            SELECT c.year_month,
                   sum(c.headline_count)                                  AS n,
                   sum(c.risk_signal_count)                               AS risk,
                   sum(c.sum_sentiment_compound)                          AS sent,
                   sum(c.sum_sensational_score)                           AS sens
            FROM cube_month_topic c
            JOIN dim_topic t ON t.topic_key = c.topic_key
            {where}
            GROUP BY 1 ORDER BY 1
            """,
            args,
        ).fetchall()
        return [
            {
                "year_month": r[0],
                "headline_count": r[1],
                # Means recomputed from the stored sums, never averaged again.
                "risk_signal_rate": round(r[2] / r[1], 6) if r[1] else None,
                "mean_sentiment": round(r[3] / r[1], 6) if r[1] else None,
                "mean_sensational_score": round(r[4] / r[1], 6) if r[1] else None,
            }
            for r in rows
        ]


def market_daily(topic: str | None = None) -> dict[str, Any]:
    """Paired daily series for the volatility scatter.

    RQ6's headline result is that log headline volume correlates with 20-day
    volatility at r = -0.28, which is a claim about a cloud of points. A
    correlation coefficient on its own hides the shape, so the dashboard needs
    the points behind it.

    **The topic defaults to the one the correlation was actually computed on**,
    and the join is built the same way `dwm/mining/market.py` builds it: one row
    per trading day for a single topic, not one row per day-topic pair. Without
    that the query returns ~13,000 rows mixing every desk, which is a different
    quantity and would not reproduce the reported coefficient.
    """
    from dwm.mining import load_mining_config

    if topic is None:
        topic = str(load_mining_config().get("market", {}).get("topic", "Business"))
    escaped = topic.replace("'", "''")
    with reading() as con:
        rows = con.execute(
            f"""
            WITH daily AS (
                SELECT
                    c.date_key,
                    sum(c.headline_count)         AS n,
                    sum(c.sum_sentiment_compound) AS sum_sent
                FROM cube_day_topic c
                JOIN dim_topic t ON t.topic_key = c.topic_key
                WHERE t.topic_name = '{escaped}'
                GROUP BY 1
            )
            SELECT
                m.trade_date,
                daily.n,
                m.volatility_20d,
                m.return_pct,
                daily.sum_sent / nullif(daily.n, 0) AS mean_sentiment
            FROM fact_market_daily m
            JOIN daily ON daily.date_key = m.date_key
            WHERE m.volatility_20d IS NOT NULL AND daily.n > 0
            ORDER BY m.trade_date
            """
        ).fetchall()
        points = [
            {
                "trade_date": str(r[0]),
                "headline_count": int(r[1]),
                "volatility_20d": float(r[2]),
                "return_pct": float(r[3]) if r[3] is not None else None,
                "mean_sentiment": float(r[4]) if r[4] is not None else None,
            }
            for r in rows
        ]
        return {
            "topic": topic,
            "points": points,
            "observations": len(points),
            "note": (
                "x is the natural log of that topic's daily headline count, which is "
                "exactly what the reported correlation was computed on. A mean of "
                "logs would differ slightly from the log of a mean."
            ),
        }


def build_manifest() -> dict[str, Any]:
    """What the dashboard needs to render its shell: identity, provenance, gate.

    Assembled here rather than in the endpoint because a client assembling its
    own provenance is how a null ends up in a user-facing slot. That happened:
    `/summary` returned `generated_at` and `mining_run_id` but not
    `config_version`, and the dashboard rendered the literal string
    "config vNone" in the sidebar for a whole build. A missing field is a bug
    whether or not the number behind it is load-bearing, and putting the block
    in one place makes the omission visible in review.
    """
    facts = load_facts()
    guards = facts.get("guards") or {}
    checks = guards.get("checks") or []
    failed = guards.get("failed") or []
    return {
        "title": "Indian News Warehouse",
        "subtitle": (
            "What the headlines, the labelled statements and five years of "
            "index data actually support"
        ),
        "provenance": {
            "generated_at": facts.get("generated_at"),
            "mining_run_id": facts.get("mining_run_id"),
            "config_version": facts.get("config_version"),
            "source": "reports/facts.json",
        },
        "guards": {
            "passed": bool(guards.get("passed")),
            "total": len(checks),
            "succeeded": len(checks) - len(failed),
            "failed": failed,
        },
        "corpus": (facts.get("corpus") or {}).get("analysis_window", {}),
        "honesty_rules": facts.get("honesty_rules") or [],
        "datasets": facts.get("datasets") or {},
    }


def table_counts() -> list[dict[str, Any]]:
    """Every table and its row count, for the warehouse browser.

    One locked read for the whole listing. It was previously: open a connection,
    run the names query, close the fetch, then loop over the names running one
    `count(*)` each with no lock - which is how a `publish_date` value once
    reached the loop and produced `SELECT count(*) FROM 2015-06-30`.

    The identifier is quoted as well. DuckDB accepts double-quoted identifiers,
    so a name containing a hyphen or starting with a digit parses. Interpolation
    of a catalogue-supplied name is safe here because the name cannot contain a
    quote character, but quoting it means a future rename with a hyphen does not
    turn into a parser error nobody can explain.
    """
    out: list[dict[str, Any]] = []
    with reading() as con:
        names = [
            r[0] for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY table_name"
            ).fetchall()
        ]
        for name in names:
            safe = '"' + name.replace('"', '""') + '"'
            count = con.execute(f"SELECT count(*) FROM {safe}").fetchone()[0]
            out.append({"table": name, "rows": count})
    return out


def audit_funnel(limit: int = 25) -> list[dict[str, Any]]:
    with reading() as con:
        columns = [
            "run_id", "dataset_code", "step", "status",
            "rows_read", "rows_loaded", "rows_rejected", "started_at", "finished_at",
        ]
        present = {
            r[0] for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'etl_audit'"
            ).fetchall()
        }
        if not present:
            return []
        selected = [c for c in columns if c in present]
        rows = con.execute(
            f"SELECT {', '.join(selected)} FROM etl_audit "
            f"ORDER BY started_at DESC NULLS LAST LIMIT {int(limit)}"
        ).fetchall()
        return [dict(zip(selected, r, strict=True)) for r in rows]
