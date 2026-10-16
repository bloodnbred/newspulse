"""fact_news_sector_daily: one row per sector per calendar date.

Sector news comes from two independent routes and the fact records which one
fired:

  * `mention` - the headline named a company, so its sector inherits the mention.
  *keyword`  - the headline matched config/sector_keywords.yaml.
  * both      - the two routes agreed.

Sector is the robust signal. Stock-level mentions are sparse and noisy; a
sector can be identified from a single unambiguous keyword, so sector-level
aggregation is what most of the analysis should lean on.

Grain discipline: a headline counts **once per sector per day**, however many
times it matched. Without that, a headline naming "Reliance Industries" while
also matching the "oil prices" keyword would contribute twice to Energy and
every sector total would over-count.
"""

from __future__ import annotations

import re
from typing import Any

import duckdb
import yaml

from dwm.config import CONFIG_DIR
from dwm.linking.mentions import HEADLINE_COLUMNS
from dwm.logging_utils import get, human_int

log = get("dwm.linking.sector_news")

DDL = """
CREATE OR REPLACE TABLE fact_news_sector_daily (
    date_key                   INTEGER,
    effective_trading_date_key INTEGER,
    sector_key                 INTEGER,
    news_count                 BIGINT,
    news_count_non_market      BIGINT,
    sum_sentiment              DOUBLE,
    high_sensational_count     BIGINT,
    source                     VARCHAR
)
"""


def load_sector_keywords(path: Any = None) -> dict[str, list[str]]:
    """config/sector_keywords.yaml -> {sector: [terms]}.

    Loaded as data, not code, because the lexicon is the part most likely to
    need extending once real matches are reviewed.
    """
    path = path or (CONFIG_DIR / "sector_keywords.yaml")
    if not path.exists():
        raise FileNotFoundError(f"missing sector keyword config: {path}")
    with path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    out: dict[str, list[str]] = {}
    for sector, terms in (doc.get("sectors") or {}).items():
        cleaned = [str(t).strip().lower() for t in (terms or []) if str(t).strip()]
        if cleaned:
            out[str(sector)] = cleaned
    return out


def build_keyword_patterns(lexicon: dict[str, list[str]]) -> dict[str, re.Pattern[str]]:
    """One compiled word-boundary pattern per sector.

    Compiled once: these run against millions of headlines, and building a regex
    per row per sector would dominate the runtime.
    """
    patterns: dict[str, re.Pattern[str]] = {}
    for sector, terms in lexicon.items():
        parts = [
            rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])" for t in terms
        ]
        patterns[sector] = re.compile("|".join(parts), re.IGNORECASE)
    return patterns


def keyword_sector(
    text: str, patterns: dict[str, re.Pattern[str]]
) -> list[str]:
    """Every sector whose lexicon fires. A headline can be about several."""
    if not text:
        return []
    return [
        sector for sector, pattern in patterns.items() if pattern.search(text)
    ]


def build_sector_facts(
    con: duckdb.DuckDBPyConnection,
    lexicon: dict[str, list[str]] | None = None,
    *,
    ceiling: int | None = None,
) -> dict[str, Any]:
    """Build fact_news_sector_daily from mentions and the keyword lexicon."""
    present = {
        r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
    missing = {"dim_sector", "fact_headline"} - present
    if missing:
        raise FileNotFoundError(
            f"missing {sorted(missing)}. Run `python -m dwm link` after "
            f"`python -m dwm build`."
        )
    if "fact_news_stock_mention" not in present:
        log.warning(
            "no stock mentions yet, so sector news rests on keywords alone. "
            "Run `python -m dwm link`."
        )

    lexicon = lexicon or load_sector_keywords()
    patterns = build_keyword_patterns(lexicon)
    con.execute(HEADLINE_COLUMNS)
    con.execute(DDL)

    # Sectors are matched by NAME so a renamed sector surfaces as unmapped
    # rather than silently disappearing.
    sector_rows = con.execute(
        "SELECT sector_key, sector_name FROM dim_sector"
    ).fetchall()
    if not sector_rows:
        raise RuntimeError("dim_sector is empty; run `python -m dwm build`")
    name_to_key = {str(n): int(k) for k, n in sector_rows}
    unknown = sorted(set(lexicon) - set(name_to_key))
    if unknown:
        log.warning(
            "sector keyword lexicon names %s, which are not in dim_sector; skipped",
            unknown,
        )
    known = {s: n_to_k for s, n_to_k in name_to_key.items() if s in lexicon}

    matched = _keyword_matches(con, patterns, name_to_key, ceiling)
    headlines_with_sector = _insert_sector_daily(con, matched)

    total = int(con.execute("SELECT count(*) FROM fact_news_sector_daily").fetchone()[0])
    by_source = {
        str(r[0]): int(r[1])
        for r in con.execute(
            "SELECT source, count(*) FROM fact_news_sector_daily GROUP BY 1"
        ).fetchall()
    }

    log.info(
        "fact_news_sector_daily: %s rows, sources %s", human_int(total), by_source
    )
    return {
        "rows": total,
        "by_source": by_source,
        "sector_days": con.execute(
            "SELECT count(DISTINCT sector_key) FROM fact_news_sector_daily"
        ).fetchone()[0] if total else 0,
        # Measured from the headline-level pairs, not by joining the daily fact
        # back on date_key, which would count every headline on a sector's day
        # as if it mentioned that sector.
        "headlines_with_a_sector": headlines_with_sector,
        "keywords_loaded": len(known),
        "keywords_unmapped_sectors": unknown,
    }


def _keyword_matches(
    con: duckdb.DuckDBPyConnection,
    patterns: dict[str, re.Pattern[str]],
    name_to_key: dict[str, int],
    ceiling: int | None = None,
) -> list[tuple[int, int, str, str]]:
    """(date_key, sector_key, headline_id, route) for every keyword hit.

    `ceiling` bounds the headline_id range so the keyword route covers exactly
    the same headlines as the mention route. Without it a `--sample` run would
    link mentions from 50,000 headlines but sector news from all 3.15M, and the
    two figures in the same summary would describe different populations.

    The scan is restricted to in-window headlines for the same reason the
    mention scan is. Without the filter the sector count included pre-window
    headlines while the coverage denominator counted only in-window ones, and
    the resulting share came out above 1.
    """
    rows: list[tuple[int, int, str, str]] = []
    top = ceiling if ceiling else None
    offset = 0
    scanned = 0
    while True:
        frame = con.execute(
            """
            SELECT headline_id, date_key, headline_text
            FROM fact_headline
            WHERE in_window AND headline_id > ?
            ORDER BY headline_id LIMIT 100000
            """,
            [offset],
        ).fetchdf()
        if frame.empty:
            break
        if top is not None:
            frame = frame[frame["headline_id"] <= top]
            if frame.empty:
                break
        for row in frame.itertuples(index=False):
            for sector in keyword_sector(row.headline_text, patterns):
                key = name_to_key.get(sector)
                if key is not None:
                    rows.append((int(row.date_key), key, str(row.headline_id), "keyword"))
        offset = int(frame["headline_id"].iloc[-1])
        scanned += len(frame)
        if top is not None and offset >= top:
            break
    log.debug("keyword route scanned %s in-window headlines", human_int(scanned))
    return rows


def _insert_sector_daily(
    con: duckdb.DuckDBPyConnection, keyword_rows: list[tuple[int, int, str, str]]
) -> int:
    """Union the two routes, then aggregate once per (date, sector, headline).

    Returns the number of distinct headlines that reached at least one sector.
    """
    import pandas as pd

    frames = [pd.DataFrame(
        keyword_rows, columns=["date_key", "sector_key", "headline_id", "route"]
    )]
    if con.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name = 'fact_news_stock_mention'"
    ).fetchone()[0]:
        mention_rows = [
            (int(r[0]), int(r[1]), str(r[2]), "mention")
            for r in con.execute(
                """
                SELECT h.date_key, m.stock_key, m.headline_id
                FROM fact_news_stock_mention m
                JOIN fact_headline h ON h.headline_id = m.headline_id
                WHERE h.in_window
                """
            ).fetchall()
            if r[1] is not None
        ]
        frames.append(pd.DataFrame(
            mention_rows, columns=["date_key", "sector_key", "headline_id", "route"]
        ))

    pairs = pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["date_key", "sector_key", "headline_id"]
    )

    # One row per (headline, sector): a headline that matched by both routes is
    # still one headline, but it is worth recording that both agreed.
    routes = (
        pairs.groupby(["headline_id", "date_key", "sector_key"])["route"]
        .agg(lambda v: "both" if v.nunique() > 1 else v.iloc[0])
        .reset_index(name="route")
    )

    con.register("_sector_pairs", routes)
    try:
        con.execute(
            """
            INSERT INTO fact_news_sector_daily
            SELECT
                p.date_key,
                coalesce(c.effective_trading_date_key, p.date_key),
                p.sector_key,
                count(*)                                              AS news_count,
                count(*) FILTER (WHERE coalesce(h.is_market_report, false) IS NOT TRUE)
                                                                    AS news_count_non_market,
                round(sum(h.sentiment_compound), 6)                 AS sum_sentiment,
                count(*) FILTER (WHERE h.is_sensational)             AS high_sensational_count,
                CASE
                    WHEN count(DISTINCT p.route) > 1 THEN 'both'
                    ELSE min(p.route)
                END                                                  AS source
            FROM _sector_pairs p
            JOIN fact_headline h ON h.headline_id = p.headline_id
            LEFT JOIN dim_trading_calendar c ON c.date_key = p.date_key
            GROUP BY p.date_key, coalesce(c.effective_trading_date_key, p.date_key),
                     p.sector_key
            """
        )
        return int(routes["headline_id"].nunique())
    finally:
        con.unregister("_sector_pairs")


def coverage(
    con: duckdb.DuckDBPyConnection, scanned: int | None = None
) -> dict[str, Any]:
    """Share of headlines that reach a stock or a sector.

    Published next to any mention-based figure so a sparse result is never read
    as a low one.

    The denominator is the number of headlines actually SCANNED, not the size of
    the window. Under `--sample 50000` those differ by more than twenty times,
    and dividing a sample's matches by the whole corpus understates the share by
    the same factor - which is how a run reports 0.01% coverage when the real
    figure is 0.3%. `denominator_is_sample` says which was used.

    The sector share is read from the `etl_audit` row `link` wrote, because
    `fact_news_sector_daily` is aggregated to (date, sector) and keeps no
    headline id. Recomputing it by joining the daily fact on date_key would count
    every headline published on a sector's day, which is nearly all of them.
    """
    total = int(
        con.execute("SELECT count(*) FROM fact_headline WHERE in_window").fetchone()[0]
    )
    denominator = scanned if scanned else total
    with_stock = int(
        con.execute(
            """
            SELECT count(DISTINCT m.headline_id)
            FROM fact_news_stock_mention m
            JOIN fact_headline h ON h.headline_id = m.headline_id
            WHERE h.in_window
            """
        ).fetchone()[0]
    )

    with_sector = _last_audited_sector_coverage(con)
    denom = denominator or 1

    return {
        "headlines_in_window": total,
        "headlines_scanned": denominator,
        "denominator_is_sample": bool(scanned and scanned != total),
        "with_stock_mention": with_stock,
        "share_with_stock": round(with_stock / denom, 6),
        "with_sector": with_sector,
        "share_with_sector": (
            round(with_sector / denom, 6) if with_sector is not None else None
        ),
    }


def _last_audited_sector_coverage(con: duckdb.DuckDBPyConnection) -> int | None:
    import json

    if not con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'etl_audit'"
    ).fetchone()[0]:
        return None
    row = con.execute(
        """
        SELECT detail FROM etl_audit
        WHERE dataset_code = 'link' AND step = 'link.sector'
        ORDER BY audit_id DESC LIMIT 1
        """
    ).fetchone()
    if not row or not row[0]:
        return None
    try:
        detail = json.loads(row[0])
    except json.JSONDecodeError:
        return None
    # The audit detail is the step's own info dict, flattened, so the figure
    # sits at the top level rather than under a "sector" key.
    value = detail.get("headlines_with_a_sector")
    return int(value) if value is not None else None
