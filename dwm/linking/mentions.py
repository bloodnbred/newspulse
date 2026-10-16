"""Populate fact_headline's v2 columns and build fact_news_stock_mention.

Two jobs, in this order because the second needs the first:

1. `flag_headlines` sets `is_market_report` and `effective_trading_date_key`
   on every headline.
2. `build_mentions` scans headlines for stock names and writes one row per
   (headline, stock).

Both stream. The corpus is 3.15M headlines and a pandas load of all of them
would be several GB, so rows are read in chunks bounded by `chunk` and pushed
back through DuckDB by registering each chunk as a temporary Arrow-backed
relation. Nothing accumulates in Python memory across chunks.

The effective trading date matters more than it looks. 32.4% of in-window
headlines fall on a day the market was shut; joining them on their raw date
silently discards a third of the corpus, which is the sort of loss that shows up
later as "news does not seem to explain prices" with no obvious cause.
"""

from __future__ import annotations

from typing import Any

import duckdb
import pandas as pd

from dwm.config import Settings, load_stock_aliases
from dwm.features.market_report import is_market_report
from dwm.linking.aliases import build_matchers, find_matches, load_stock_keys
from dwm.logging_utils import get, human_int, step

log = get("dwm.linking.mentions")

DDL = """
CREATE OR REPLACE TABLE fact_news_stock_mention (
    headline_id                BIGINT,
    stock_key                  INTEGER,
    date_key                   INTEGER,
    effective_trading_date_key INTEGER,
    matched_alias              VARCHAR,
    match_method               VARCHAR,
    is_ambiguous_alias         BOOLEAN,
    is_market_report           BOOLEAN
)
"""

# The two columns schema_v2.sql adds to fact_headline. Applied here as well so
# `link` works on a warehouse that has not had the v2 DDL run against it.
HEADLINE_COLUMNS = """
ALTER TABLE fact_headline ADD COLUMN IF NOT EXISTS is_market_report BOOLEAN;
ALTER TABLE fact_headline ADD COLUMN IF NOT EXISTS effective_trading_date_key INTEGER;
"""

MENTION_COLUMNS = ["headline_id", "stock_key", "matched_alias", "match_method",
                   "is_ambiguous_alias"]


def _require(con: duckdb.DuckDBPyConnection, *tables: str) -> None:
    present = {
        r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
    missing = sorted(set(tables) - present)
    if missing:
        raise FileNotFoundError(
            f"missing {missing}. Run `python -m dwm build` and "
            "`python -m dwm etl` first."
        )


def flag_headlines(con: duckdb.DuckDBPyConnection, chunk: int) -> dict[str, Any]:
    """Set is_market_report and effective_trading_date_key, chunk by chunk."""
    _require(con, "fact_headline", "dim_trading_calendar")
    con.execute(HEADLINE_COLUMNS)

    total = int(con.execute("SELECT count(*) FROM fact_headline").fetchone()[0])
    flagged = 0
    updated = 0
    offset = 0

    while offset < total:
        size = int(chunk)
        frame = con.execute(
            """
            SELECT h.headline_id, h.headline_text,
                   coalesce(c.effective_trading_date_key, h.date_key)
                       AS effective_trading_date_key
            FROM fact_headline h
            LEFT JOIN dim_trading_calendar c ON c.date_key = h.date_key
            WHERE h.headline_id > ?
            ORDER BY h.headline_id
            LIMIT ?
            """,
            [offset, size],
        ).fetchdf()

        if frame.empty:
            break

        frame["is_market_report"] = [
            is_market_report(t) for t in frame["headline_text"]
        ]
        flagged += int(frame["is_market_report"].sum())

        con.register("_chunk_flags", frame)
        try:
            con.execute(
                """
                UPDATE fact_headline
                SET is_market_report = c.is_market_report,
                    effective_trading_date_key = c.effective_trading_date_key
                FROM _chunk_flags c
                WHERE fact_headline.headline_id = c.headline_id
                """
            )
        finally:
            con.unregister("_chunk_flags")

        offset = int(frame["headline_id"].iloc[-1])
        updated += len(frame)
        log.debug("flagged %s/%s", human_int(offset), human_int(total))

    log.info(
        "is_market_report set on %s headlines (%s of them true, %.2f%%)",
        human_int(updated), human_int(flagged), 100 * flagged / updated if updated else 0.0,
    )
    return {
        "headlines": total,
        "flagged": flagged,
        "share": round(flagged / total, 6) if total else None,
    }


def build_mentions(
    con: duckdb.DuckDBPyConnection, chunk: int, *, sample: int | None = None
) -> dict[str, Any]:
    """Build fact_news_stock_mention by streaming headlines in chunks."""
    _require(con, "fact_headline", "dim_stock", "dim_stock_alias", "dim_trading_calendar")
    con.execute(HEADLINE_COLUMNS)

    rules = load_stock_aliases()
    matchers = build_matchers(rules, load_stock_keys(con))
    log.info("matching %s symbols against the corpus", len(matchers))
    if not matchers:
        raise RuntimeError(
            "no aliases matched a dim_stock symbol; check config/stock_aliases.yaml"
        )

    con.execute(DDL)

    total = int(
        con.execute("SELECT count(*) FROM fact_headline WHERE in_window").fetchone()[0]
    )
    # --sample caps the highest headline_id processed, not a LIMIT applied to
    # every chunk. A LIMIT inside the loop would restart from the top of the
    # remaining rows on the second pass and process far more than asked for.
    if sample:
        ceiling = int(
            con.execute(
                "SELECT coalesce(max(headline_id), 0) FROM ("
                "  SELECT headline_id FROM fact_headline WHERE in_window"
                "  ORDER BY headline_id LIMIT ?)",
                [int(sample)],
            ).fetchone()[0]
        )
    else:
        ceiling = int(
            con.execute(
                "SELECT coalesce(max(headline_id), 0) FROM fact_headline WHERE in_window"
            ).fetchone()[0]
        )
    offset = 0
    written = 0
    ambiguous = 0

    while offset < ceiling:
        frame = con.execute(
            """
            SELECT headline_id, headline_text, date_key, is_market_report, topic_name
            FROM fact_headline h
            LEFT JOIN dim_topic t ON t.topic_key = h.topic_key
            WHERE h.in_window AND h.headline_id > ? AND h.headline_id <= ?
            ORDER BY h.headline_id
            """,
            [offset, ceiling],
        ).fetchdf()
        if frame.empty:
            break

        records: list[tuple[Any, ...]] = []
        for row in frame.itertuples(index=False):
            for match in find_matches(
                int(row.headline_id),
                row.headline_text,
                matchers,
                rules,
                topic_name=row.topic_name,
            ):
                records.append(
                    (
                        match.headline_id, match.stock_key, match.matched_alias,
                        match.match_method, match.is_ambiguous_alias,
                    )
                )
                if match.is_ambiguous_alias:
                    ambiguous += 1

        if records:
            out = pd.DataFrame(records, columns=MENTION_COLUMNS)
            con.register("_chunk_mentions", out)
            try:
                con.execute(
                    """
                    INSERT INTO fact_news_stock_mention
                    SELECT c.headline_id, c.stock_key, h.date_key,
                           coalesce(t.effective_trading_date_key, h.date_key),
                           c.matched_alias, c.match_method,
                           c.is_ambiguous_alias, coalesce(h.is_market_report, false)
                    FROM _chunk_mentions c
                    JOIN fact_headline h ON h.headline_id = c.headline_id
                    LEFT JOIN dim_trading_calendar t
                           ON t.date_key = h.date_key
                    """
                )
            finally:
                con.unregister("_chunk_mentions")
            written += len(out)

        offset = int(frame["headline_id"].iloc[-1])
        log.debug("mention scan at %s", human_int(offset))

    summary: dict[str, Any] = {
        "mentions": written,
        "headlines_scanned": int(
            con.execute(
                "SELECT count(*) FROM fact_headline "
                "WHERE in_window AND headline_id <= ?",
                [ceiling],
            ).fetchone()[0]
        ),
        "headlines_in_window": total,
        "headlines_with_a_mention": int(
            con.execute(
                "SELECT count(DISTINCT m.headline_id) FROM fact_news_stock_mention m "
                "JOIN fact_headline h ON h.headline_id = m.headline_id WHERE h.in_window"
            ).fetchone()[0]
        ),
        "ambiguous_mentions": int(
            con.execute(
                "SELECT count(*) FROM fact_news_stock_mention WHERE is_ambiguous_alias"
            ).fetchone()[0]
        ),
        "duplicate_pairs": int(
            con.execute(
                "SELECT count(*) FROM (SELECT headline_id, stock_key "
                "FROM fact_news_stock_mention GROUP BY 1,2 HAVING count(*) > 1) t"
            ).fetchone()[0]
        ),
        "orphan_stock_keys": int(
            con.execute(
                "SELECT count(*) FROM fact_news_stock_mention m WHERE NOT EXISTS "
                "(SELECT 1 FROM dim_stock d WHERE d.stock_key = m.stock_key)"
            ).fetchone()[0]
        ),
        "symbols_matched": int(
            con.execute("SELECT count(DISTINCT stock_key) FROM fact_news_stock_mention").fetchone()[0]
        ),
        # The highest headline_id the scan reached, so the sector-keyword route
        # can be bounded to exactly the same range.
        "max_headline_id": ceiling,
    }
    log.info(
        "fact_news_stock_mention: %s rows across %s symbols",
        human_int(summary["mentions"]), summary["symbols_matched"],
    )
    return summary


def run(
    con: duckdb.DuckDBPyConnection, settings: Settings
) -> dict[str, Any]:
    """Flag headlines, then build mentions. Returns both summaries."""
    with step("flag market-report headlines"):
        flags = flag_headlines(con, settings.chunk)
    with step("link headlines to stocks"):
        mentions = build_mentions(con, settings.chunk, sample=settings.sample)
    return {"market_report": flags, "mentions": mentions}
