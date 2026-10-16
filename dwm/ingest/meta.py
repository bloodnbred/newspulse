"""v2 meta source: the Nifty 50 constituent master.

The pack assumed this file would be downloaded by hand into data/raw/meta/.
There is no need for that: NSE publishes it as a plain CSV with no key and no
session, so it is fetched the same way every other source is, cached under
data/raw/meta/ (gitignored, so the pipeline still reruns offline), and given a
provenance sidecar.

The file is the *current* constituent list. The analysis window is historical,
so companies that were constituents in 2015-2020 and have since been removed
are absent. That is survivorship bias and dim_stock records it rather than
hiding it (AGENTS.md, "Honesty requirements specific to v2").
"""

from __future__ import annotations

import csv
import io
import urllib.error
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from dwm.config import SourceSpec, raw_dir
from dwm.ingest import audit
from dwm.ingest.fetch import fetch_bytes, write_provenance
from dwm.logging_utils import get, human_int, step

log = get("dwm.ingest.meta")

# Verified reachable without a key or a session on 2026-10-14. Columns are
# "Company Name", "Industry", "Symbol", "Series", "ISIN Code".
DEFAULT_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"
FILENAME = "nifty50_constituents.csv"


@dataclass(slots=True)
class MetaOutcome:
    ok: bool
    path: Path | None
    rows: int
    audit_id: int | None = None
    error: str | None = None
    reused: bool = False
    # The staging step's own row counts, carried through so the CLI can report
    # them the same way it reports a v1 ingest.
    staging: dict[str, Any] = field(default_factory=dict)


def _pick(lookup: dict[str, str], aliases: list[str], label: str) -> str:
    for alias in aliases:
        if alias.lower() in lookup:
            return lookup[alias.lower()]
    raise KeyError(
        f"required column for {label!r} not found; tried {aliases}, header has "
        f"{sorted(lookup.values())}"
    )


def download(
    url: str = DEFAULT_URL,
    directory: Path | None = None,
    *,
    force: bool = False,
    timeout: int = 30,
) -> MetaOutcome:
    """Fetch the constituent list and cache it with a provenance sidecar."""
    directory = directory or raw_dir("meta")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / FILENAME

    if target.exists() and not force:
        log.info("meta: reusing cached %s", target)
        rows = len(parse_constituents(target.read_text(encoding="utf-8-sig")))
        return MetaOutcome(ok=True, path=target, rows=rows, reused=True)

    try:
        raw = fetch_bytes(url, timeout=timeout)
    except (urllib.error.URLError, OSError) as exc:
        return MetaOutcome(ok=False, path=None, rows=0, error=f"{type(exc).__name__}: {exc}")

    text = raw.decode("utf-8-sig", errors="replace")
    rows = parse_constituents(text)          # parse before writing, not after
    target.write_text(text, encoding="utf-8")
    write_provenance(
        target,
        {
            "dataset": "meta",
            "source_url": url,
            "description": "NSE Nifty 50 constituent list (symbol, company, industry, ISIN)",
            "rows": len(rows),
            "note": (
                "Current constituent list applied to a historical window. "
                "Constituents removed since the window are absent: survivorship bias."
            ),
        },
    )
    log.info("meta: fetched %s rows -> %s", human_int(len(rows)), target.name)
    return MetaOutcome(ok=True, path=target, rows=len(rows))


def parse_constituents(text: str) -> list[dict[str, str]]:
    """Normalise the NSE CSV into symbol / company / industry / isin rows.

    Raises KeyError when a column the mapping needs is missing, so an upstream
    header change is a loud failure rather than an empty dim_stock.
    """
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise KeyError("constituent list has no header")
    lookup = {name.strip().lower(): name for name in reader.fieldnames if name}

    symbol_col = _pick(lookup, ["symbol", "ticker"], "symbol")
    company_col = _pick(lookup, ["company name", "company", "name"], "company")
    industry_col = _pick(lookup, ["industry", "sector"], "industry")
    isin_col = lookup.get("isin code") or lookup.get("isin") or ""

    out: list[dict[str, str]] = []
    for row in reader:
        symbol = (row.get(symbol_col) or "").strip().upper()
        if not symbol:
            continue
        out.append(
            {
                "symbol": symbol,
                "company": (row.get(company_col) or "").strip(),
                "industry": (row.get(industry_col) or "").strip(),
                "isin": (row.get(isin_col) or "").strip() if isin_col else "",
            }
        )
    return out


DDL = """
CREATE OR REPLACE TABLE stg_meta (
    stg_row_id      BIGINT,
    stg_dataset_code VARCHAR,
    stg_filename    VARCHAR,
    stg_ingested_at TIMESTAMP,
    stg_row_num     BIGINT,
    symbol          VARCHAR,
    company_name    VARCHAR,
    industry        VARCHAR,
    isin            VARCHAR
)
"""


def stage(
    con: duckdb.DuckDBPyConnection,
    path: Path,
    *,
    code: str = "meta",
    dataset_code: str | None = None,
) -> dict[str, Any]:
    """Load the cached constituent list into stg_meta."""
    audit.ensure_audit_table(con)
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    rows = parse_constituents(text)

    con.execute(DDL)
    con.executemany(
        "INSERT INTO stg_meta VALUES (?, ?, ?, now(), ?, ?, ?, ?, ?)",
        [
            (
                i, dataset_code or code, path.name, i,
                r["symbol"], r["company"], r["industry"], r["isin"],
            )
            for i, r in enumerate(rows, start=1)
        ],
    )
    staged = int(con.execute("SELECT count(*) FROM stg_meta").fetchone()[0])
    industries = len(
        {r["industry"] for r in rows if r["industry"]}
    )
    info: dict[str, Any] = {
        "rows_read": len(rows),
        "rows_loaded": staged,
        "rows_rejected": max(0, len(rows) - staged),
        "reconciled": staged == len(rows),
        "table": "stg_meta",
        "source_file": path.name,
        "source_bytes": path.stat().st_size,
        "distinct_industries": industries,
        "distinct_symbols": len({r["symbol"] for r in rows}),
    }
    log.info("stg_meta: %s symbols, %s industries", info["distinct_symbols"], industries)
    return info


def ingest(
    con: duckdb.DuckDBPyConnection,
    spec: SourceSpec | None = None,
    *,
    force: bool = False,
    skip_fetch: bool = False,
) -> MetaOutcome:
    """Fetch (unless cached or skipped) then stage, auditing both ways."""
    run_id = audit.new_run_id()
    url = spec.url if spec and spec.url else DEFAULT_URL
    directory = spec.local_dir() if spec else raw_dir("meta")
    started = datetime.now(UTC).replace(tzinfo=None)

    cached = directory / FILENAME
    if skip_fetch and cached.exists():
        outcome = MetaOutcome(ok=True, path=cached, rows=0, reused=True)
    else:
        outcome = download(url, directory, force=force)

    if not outcome.ok or outcome.path is None:
        ids = audit.record(
            con, run_id=run_id, dataset_code="meta", step="ingest.meta",
            info={"error": outcome.error, "source_url": url}, status="failed",
            started_at=started,
        )
        return MetaOutcome(False, None, 0, ids, error=outcome.error)

    with step("stage meta -> stg_meta") as info:
        info.update(stage(con, outcome.path))
    outcome.rows = int(info["rows_loaded"])
    outcome.staging = dict(info)
    outcome.audit_id = audit.record(
        con, run_id=run_id, dataset_code="meta", step="ingest.meta",
        info={**info, "reused": outcome.reused, "source_url": url},
        status=info["status"], started_at=started,
    )
    return outcome
