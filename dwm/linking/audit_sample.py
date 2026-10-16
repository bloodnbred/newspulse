"""A sample of matched mentions, for a human to judge.

This module exists because name matching is the weakest link in the system and
because "we matched 1.1M mentions to stocks" is not evidence of anything. The
only way to know whether "Max" was the hospital chain or a headline about a
maximum is to read the matched rows.

Two rules, both non-negotiable:

* **The pipeline never writes `is_correct`.** It writes an EMPTY column. Filling
  it in from a heuristic would make the precision figure a measurement of the
  heuristic that produced it, which is circular. A human fills it in.
* **Precision is only reported when enough rows have been filled.** If 3 of 200
  are labelled, the result is not a precision and is not published as one.

The sample is stratified by stock and by whether the alias needed context,
because the ambiguous matches are exactly the ones that determine whether the
matcher is trustworthy, and a uniform sample would under-weight them.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import duckdb

from dwm.config import reports_dir
from dwm.logging_utils import get, human_int

log = get("dwm.linking.audit_sample")

SAMPLE_FILE = "mention_audit_sample.csv"
COLUMNS = ["headline_id", "symbol", "sector", "matched_alias",
           "match_method", "is_ambiguous_alias", "headline_text", "is_correct"]

DEFAULT_SIZE = 200
# Below this many labels, the precision estimate is too thin to publish.
MIN_LABELLED = 30

SEED = 42


def write_sample(
    con: duckdb.DuckDBPyConnection, size: int = DEFAULT_SIZE
) -> dict[str, Any]:
    """Write reports/mention_audit_sample.csv with an EMPTY is_correct column.

    Stratified two ways: round-robin across stocks, and separately across the
    ambiguous / unambiguous split, so the sample is not dominated by whichever
    alias happens to fire most often.
    """
    rows = con.execute(
        """
        SELECT m.headline_id, d.symbol, s.sector_name AS sector_name,
               m.matched_alias, m.match_method, m.is_ambiguous_alias,
               h.headline_text
        FROM fact_news_stock_mention m
        JOIN dim_stock d ON d.stock_key = m.stock_key
        LEFT JOIN dim_sector s ON s.sector_key = d.sector_key
        JOIN fact_headline h ON h.headline_id = m.headline_id
        WHERE h.in_window
        """
    ).fetchdf()

    if rows.empty:
        raise RuntimeError(
            "no mentions to sample; run `python -m dwm link` first"
        )

    picked = _stratify(rows, size)
    path = reports_dir() / SAMPLE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        for row in picked:
            # is_correct is deliberately empty. See the module docstring.
            # The column is named sector_name in the frame; `sector` reads
            # None and every row would ship with a blank sector.
            writer.writerow([
                row["headline_id"], row["symbol"], row.get("sector_name") or "",
                row["matched_alias"], row["match_method"],
                "TRUE" if row["is_ambiguous_alias"] else "FALSE",
                (row["headline_text"] or "").replace("\n", " "), "",
            ])

    ambiguous = int(sum(1 for r in picked if r["is_ambiguous_alias"]))
    log.info(
        "wrote %s: %s rows (%s ambiguous, %s symbols)",
        path.name, human_int(len(picked)), ambiguous,
        len({r["symbol"] for r in picked}),
    )
    return {
        "path": str(path),
        "rows": len(picked),
        "ambiguous": ambiguous,
        "symbols": len({r["symbol"] for r in picked}),
        "is_correct_filled": False,
        "next_step": (
            f"Read {path.name}, put TRUE/FALSE in the is_correct column, then run "
            "`python -m dwm link --precision`."
        ),
    }


def _stratify(rows, size: int) -> list:
    """Round-robin across (symbol, is_ambiguous) so the sample is spread."""
    buckets: dict[tuple[str, bool], list] = {}
    for row in rows.to_dict("records"):
        key = (row["symbol"], bool(row["is_ambiguous_alias"]))
        buckets.setdefault(key, []).append(row)

    # Deterministic order, then round-robin so no bucket can be starved.
    keys = sorted(buckets)
    for key in keys:
        buckets[key].sort(key=lambda r: r["headline_id"])
        # A fixed stride rather than the first N, so the sample spans the
        # window instead of clustering in its earliest months.
        bucket = buckets[key]
        if len(bucket) > size:
            stride = max(1, len(bucket) // size)
            buckets[key] = bucket[::stride][:size]

    picked: list = []
    index = 0
    while len(picked) < size and any(buckets[k] for k in keys):
        key = keys[index % len(keys)]
        if buckets[key]:
            picked.append(buckets[key].pop(0))
        index += 1
        if index > size * len(keys) * 4:      # pragma: no cover - safety valve
            break
    return picked


def _read_rows(path: Path) -> list[dict[str, Any]]:
    """Every labelled row, fully parsed.

    Blank, unrecognised and missing cells are all treated as unlabelled rather
    than as False, so an unfinished spreadsheet cannot silently become a
    precision of zero. One reader, so every consumer sees the same rows.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist. Run `python -m dwm link` to write it first."
        )
    truthy = {"true", "yes", "1", "y"}
    falsy = {"false", "no", "0", "n"}
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("is_correct") or "").strip().lower()
            if raw not in truthy | falsy:
                continue
            try:
                headline_id = int(row["headline_id"])
            except (KeyError, TypeError, ValueError):
                continue
            out.append(
                {
                    "headline_id": headline_id,
                    "symbol": (row.get("symbol") or "").strip(),
                    "matched_alias": (row.get("matched_alias") or "").strip(),
                    "headline_text": (row.get("headline_text") or "").strip(),
                    "ambiguous": (row.get("is_ambiguous_alias") or "").upper() == "TRUE",
                    "is_correct": raw in truthy,
                }
            )
    return out


def precision(
    con: duckdb.DuckDBPyConnection, path: Path | None = None
) -> dict[str, Any]:
    """Compute mention precision from the human-filled sample.

    Returns `published: False` with an explanation when too few rows are
    labelled, rather than printing a confident number off 3 judgements.
    """
    path = path or (reports_dir() / SAMPLE_FILE)
    rows = _read_rows(path)

    if len(rows) < MIN_LABELLED:
        return {
            "published": False,
            "labelled": len(rows),
            "minimum": MIN_LABELLED,
            "reason": (
                f"only {len(rows)} of the sample rows have is_correct filled; "
                f"at least {MIN_LABELLED} are needed before a precision figure "
                f"means anything."
            ),
            "path": str(path),
        }

    labelled = [(r["symbol"], r["is_correct"]) for r in rows]
    correct = sum(1 for _, truth in labelled if truth)
    total = len(labelled)

    # Ambiguous and unambiguous matches are reported separately, because they
    # are different claims: an unambiguous full name is a different assertion
    # from a bare "Max" that needed a context word.
    by_symbol: dict[str, list[bool]] = {}
    for symbol, truth in labelled:
        by_symbol.setdefault(symbol, []).append(truth)

    ambiguous = _ambiguous_precision(rows)

    keys = {
        str(symbol): int(key)
        for symbol, key in con.execute(
            "SELECT symbol, stock_key FROM dim_stock"
        ).fetchall()
    }
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS mining_mention_audit (
            headline_id BIGINT, stock_key INTEGER, matched_alias VARCHAR,
            headline_text VARCHAR, is_correct BOOLEAN
        )
        """
    )
    con.execute("DELETE FROM mining_mention_audit")
    con.executemany(
        "INSERT INTO mining_mention_audit VALUES (?,?,?,?,?)",
        [
            (r["headline_id"], keys.get(r["symbol"]), r["matched_alias"],
             r["headline_text"], r["is_correct"])
            for r in rows
        ],
    )

    result: dict[str, Any] = {
        "published": True,
        "labelled": total,
        "correct": correct,
        "precision": round(correct / total, 4),
        "by_symbol": {
            symbol: {"n": len(v), "precision": round(sum(v) / len(v), 4)}
            for symbol, v in sorted(by_symbol.items())
            if len(v) >= 3
        },
        "path": str(path),
        "table": "mining_mention_audit",
    }
    if ambiguous is not None:
        result["ambiguous_precision"] = ambiguous
    log.info(
        "mention precision %s/%s = %.1f%% (ambiguous %s)",
        correct, total, 100 * correct / total, ambiguous,
    )
    return result


def _ambiguous_precision(rows: list[dict[str, Any]]) -> float | None:
    """Precision over only the rows whose alias needed context.

    Needs its own floor: if almost no sampled match was ambiguous, there is
    nothing to report and the number would be noise.
    """
    ambiguous = [r["is_correct"] for r in rows if r["ambiguous"]]
    if len(ambiguous) < MIN_LABELLED:
        return None
    return round(sum(ambiguous) / len(ambiguous), 4)


def summary_for_report(result: dict[str, Any] | None) -> str:
    """One paragraph for the data-quality report, whether or not it is filled."""
    if not result:
        return "Mention precision has not been measured."
    if not result.get("published"):
        return (
            f"Mention precision is **not yet measurable**: "
            f"{result['labelled']} of the {result['minimum']}-row minimum have "
            f"`is_correct` filled in `{SAMPLE_FILE}`. No precision figure is "
            f"published, because one computed from a handful of judgements would "
            f"look like a measurement and be a guess."
        )
    return (
        f"Mention precision **{result['precision']:.1%}** "
        f"({result['correct']} of {result['labelled']} sampled matches judged "
        f"correct by hand in `{SAMPLE_FILE}`)."
    )
