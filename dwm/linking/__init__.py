"""v2 linking stage: flag market reports, link headlines to stocks and sectors.

Order matters here and the reasons are not obvious:

1. `is_market_report` is set first, because every mention row carries the flag
   and predictive analysis later filters on it.
2. `effective_trading_date_key` is filled at the same time, because a headline
   on a Sunday has to be attributed to Monday's session before anything is
   joined to a price.
3. Stock mentions next, then sector facts, which combine those mentions with
   the sector keyword lexicon.

The mention audit sample is written last. Its `is_correct` column is left empty
on purpose and is filled by a person; see `audit_sample` for why that matters.
"""

from __future__ import annotations

from typing import Any

import duckdb

from dwm.config import Settings
from dwm.ingest.audit import ensure_audit_table, new_run_id, record
from dwm.linking import audit_sample, mentions, sector_news
from dwm.logging_utils import get, step

log = get("dwm.linking")

__all__ = ["run_link", "audit_sample", "mentions", "sector_news"]


def run_link(
    settings: Settings,
    *,
    con: duckdb.DuckDBPyConnection | None = None,
    sample_size: int | None = None,
    skip_audit_sample: bool = False,
) -> dict[str, Any]:
    """Run the whole linking stage and write one etl_audit row per step."""
    from datetime import UTC, datetime

    from dwm.db import connect

    owned = con is None
    con = con or connect(settings)
    run_id = new_run_id()
    started = datetime.now(UTC).replace(tzinfo=None)
    audit_ids: list[int] = []
    result: dict[str, Any] = {}

    try:
        ensure_audit_table(con)

        with step("flag market-report headlines") as info:
            result["market_report"] = mentions.flag_headlines(con, settings.chunk)
            info.update(result["market_report"])
        audit_ids.append(
            record(
                con, run_id=run_id, dataset_code="link", step="link.market_report",
                info=info, status=info["status"], started_at=started,
            )
        )

        with step("link headlines to stocks") as info:
            result["mentions"] = mentions.build_mentions(
                con, settings.chunk, sample=settings.sample
            )
            info.update(result["mentions"])
        # Both routes cover the same headline range, so the coverage share has
        # one honest denominator rather than two different populations.
        ceiling = result["mentions"].get("max_headline_id")
        audit_ids.append(
            record(
                con, run_id=run_id, dataset_code="link", step="link.mentions",
                info=info, status=info["status"], started_at=started,
            )
        )

        sector_started = datetime.now(UTC).replace(tzinfo=None)
        with step("build sector news facts") as info:
            result["sector"] = sector_news.build_sector_facts(con, ceiling=ceiling)
            info.update(result["sector"])
        audit_ids.append(
            record(
                con, run_id=run_id, dataset_code="link", step="link.sector",
                info=info, status=info["status"], started_at=sector_started,
            )
        )

        result["coverage"] = sector_news.coverage(
            con, scanned=result["mentions"].get("headlines_scanned")
        )
        log.info("linking coverage: %s", result["coverage"])

        if not skip_audit_sample:
            with step("write mention audit sample") as info:
                info.update(
                    audit_sample.write_sample(con, sample_size or 200)
                )
            audit_ids.append(
                record(
                    con, run_id=run_id, dataset_code="link", step="link.audit_sample",
                    info=info, status=info["status"],
                    started_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )

        # Regenerate the data-quality report so the coverage figures and the
        # mention-precision note appear alongside the stock figures.
        with step("refresh data-quality report") as info:
            from dwm.warehouse import quality

            info["path"] = quality.write(con, link=result)
        audit_ids.append(
            record(
                con, run_id=run_id, dataset_code="link", step="link.quality",
                info=info, status=info["status"],
                started_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )

        result["run_id"] = run_id
        result["audit_ids"] = audit_ids
        return result
    finally:
        if owned:
            con.close()
