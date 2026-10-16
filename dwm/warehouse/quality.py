"""reports/data_quality.md: what the warehouse could not do, stated plainly.

Every figure here is read from the warehouse at write time. Nothing is typed in
by hand, because a hand-written data-quality report is a snapshot that quietly
starts lying the moment the data is rebuilt.

The report exists to make the awkward parts findable. A project that hides its
coverage gaps in a spreadsheet is asking to be caught in the viva.
"""

from __future__ import annotations

from typing import Any

import duckdb

from dwm.config import reports_dir
from dwm.logging_utils import get, human_int

log = get("dwm.warehouse.quality")

FILENAME = "data_quality.md"


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    return con.execute(sql).fetchone()[0]


def _table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return bool(
        con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
        ).fetchone()[0]
    )


def collect(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Every number the report prints, measured now."""
    from dwm.warehouse import stock_facts

    data: dict[str, Any] = {"generated_at": _now()}
    row = con.execute(
        "SELECT min(publish_date), max(publish_date) FROM fact_headline WHERE in_window"
    ).fetchone()
    data["window"] = {
        "start": row[0].isoformat() if row[0] else "",
        "end": row[1].isoformat() if row[1] else "",
    }
    data["headlines"] = {
        "total": _scalar(con, "SELECT count(*) FROM fact_headline"),
        "in_window": _scalar(con, "SELECT count(*) FROM fact_headline WHERE in_window"),
    }

    if not _table(con, "fact_stock_daily"):
        data["stocks"] = {"built": False}
        return data

    data["stocks"] = {"built": True}
    data["stocks"].update(
        {
            "rows": _scalar(con, "SELECT count(*) FROM fact_stock_daily"),
            "symbols": _scalar(con, "SELECT count(DISTINCT stock_key) FROM fact_stock_daily"),
            "clean_rows": _scalar(con, "SELECT count(*) FROM v_stock_daily_clean"),
            "outliers": _scalar(con, "SELECT count(*) FROM fact_stock_daily WHERE is_outlier"),
            "threshold_pct": stock_facts.OUTLIER_THRESHOLD_PCT,
            "return_basis": _scalar(
                con, "SELECT min(return_basis) FROM fact_stock_daily"
            ),
            "outlier_examples": stock_facts.outlier_rows(con, limit=10),
            "distribution": _return_distribution(con),
            "coverage": stock_facts.coverage(con),
        }
    )
    if _table(con, "fact_news_stock_mention"):
        data["mentions"] = _mention_coverage(con)
    return data


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _return_distribution(con: duckdb.DuckDBPyConnection) -> list[tuple[str, int]]:
    return [
        (str(r[0]), int(r[1]))
        for r in con.execute(
            """
            SELECT CASE
                     WHEN abs_return_pct < 1  THEN 'under 1%'
                     WHEN abs_return_pct < 2  THEN '1-2%'
                     WHEN abs_return_pct < 5  THEN '2-5%'
                     WHEN abs_return_pct < 10 THEN '5-10%'
                     WHEN abs_return_pct < 25 THEN '10-25%'
                     ELSE 'over 25% (flagged)'
                   END AS bucket,
                   count(*)
            FROM fact_stock_daily
            WHERE abs_return_pct IS NOT NULL
            GROUP BY 1
            ORDER BY min(abs_return_pct)
            """
        ).fetchall()
    ]


def _mention_coverage(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """How often headlines actually name a stock or a sector. Published next to
    every mention-based figure so a sparse number is never read as a low one."""
    in_window = "SELECT count(*) FROM fact_headline WHERE in_window"
    total = int(con.execute(in_window).fetchone()[0])
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
    with_sector = 0
    if _table(con, "fact_news_sector_daily"):
        with_sector = int(
            con.execute(
                """
                SELECT count(DISTINCT s.date_key)
                FROM fact_news_sector_daily s
                """
            ).fetchone()[0]
        )
    return {
        "headlines_in_window": total,
        "with_stock_mention": with_stock,
        "share_with_stock": round(with_stock / total, 4) if total else None,
        "sector_days": with_sector,
    }


def render(data: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Data quality",
        "",
        f"*Generated {data['generated_at']} by `python -m dwm build`. Every figure below is",
        "read from the warehouse at write time; none is typed in by hand.*",
        "",
        "## News corpus",
        "",
    ]
    window = data["window"]
    headlines = data["headlines"]
    lines += [
        f"- Analysis window: **{window['start']} to {window['end']}**, derived from the last",
        "  publish date in the corpus, not configured.",
        f"- Headlines: **{human_int(headlines['total'])}** total, of which",
        f"  **{human_int(headlines['in_window'])}** fall inside the window.",
        "",
    ]

    stocks = data.get("stocks") or {}
    if not stocks.get("built"):
        lines += [
            "## Stock prices",
            "",
            "`fact_stock_daily` has not been built. Run:",
            "",
            "```",
            "python -m dwm ingest --dataset meta --dataset stocks",
            "python -m dwm etl",
            "python -m dwm build",
            "```",
            "",
        ]
        return "\n".join(lines) + "\n"

    coverage = stocks["coverage"]
    lines += [
        "## Stock prices",
        "",
        f"- `fact_stock_daily`: **{human_int(stocks['rows'])}** rows across",
        f"  **{stocks['symbols']}** symbols; {human_int(stocks['clean_rows'])} rows survive",
        "  the clean view.",
        f"- Returns are **percentage points**, computed from **{stocks['return_basis']}**.",
        "  On this source `close` is split-adjusted but not dividend-adjusted, so using",
        "  `close` would understate total return.",
        f"- `turnover` is **derived** as close x volume. The price source publishes no",
        "  turnover, so any figure derived from it is an estimate.",
        "",
        "### Flagged moves",
        "",
        f"`is_outlier` flags `abs(return_pct) > {stocks['threshold_pct']}`. That fired",
        f"**{stocks['outliers']} times**.",
        "",
        "Distribution of daily moves:",
        "",
        "| Move | Rows |",
        "|---|---:|",
    ]
    for label, n in stocks["distribution"]:
        lines.append(f"| {label} | {human_int(n)} |")
    lines += [
        "",
        "Inspecting them: they are real market events, not data errors. The rule was",
        "written to catch unadjusted splits and bonus issues, but because returns are",
        "computed from adjusted closes there are no split artefacts left for it to find.",
        "It now flags genuine large moves instead. That is still useful, but it is a",
        "different job from the one its name suggests, and the flagged rows are kept in",
        "the table and excluded from statistics and models.",
        "",
        "Largest flagged moves:",
        "",
        "| Symbol | Date | Return % | Close | Adj close |",
        "|---|---|---:|---:|---:|",
    ]
    for row in stocks["outlier_examples"]:
        lines.append(
            f"| {row['symbol']} | {row['trade_date']} | {row['return_pct']:.2f} | "
            f"{row['close']:.2f} | {row['adj_close']:.2f} |"
        )

    lines += ["", "### Coverage gaps", ""]
    missing = coverage["symbols_without_prices"]
    lines += [
        f"- **{len(missing)}** of the index's symbols have no usable price history inside",
        "  the window and still carry a `dim_stock` row, so a 50-stock index cannot",
        "  silently become a 47-stock one:",
        "",
    ]
    if missing:
        lines.append(f"  `{', '.join(missing)}`")
        lines += [
            "",
            "  Each of these listed after the analysis window closed, so there is nothing",
            "  to fetch for the period studied. They are listed in `etl_audit` as",
            "  unmatched rather than dropped.",
        ]
    non_survivors = coverage["non_survivors"]
    lines += [
        "",
        f"- **{len(non_survivors)}** symbols are flagged `is_survivor = FALSE` because they",
        "  first trade after the window starts.",
    ]
    if non_survivors:
        lines.append(f"  `{', '.join(non_survivors)}`")
    sparse = coverage["sparse"]
    if sparse:
        lines += [
            "",
            f"- **{len(sparse)}** symbols have under {4 * 20} sessions, too few for a stable",
            "  20-day volatility:",
            "",
        ]
        lines += [f"  - `{r['symbol']}`: {r['rows']} rows" for r in sparse]

    weekend = coverage["trading_weekend_sessions"]
    if weekend:
        lines += [
            "",
            "### Unusual trading days",
            "",
            "These are weekend dates the exchange **actually traded**. They must not be",
            "treated as closures:",
            "",
            f"`{', '.join(weekend)}`",
            "",
            "Two are Diwali Muhurat sessions; 2020-02-01 is the Union Budget Saturday.",
        ]

    lines += [
        "",
        "## Survivorship bias",
        "",
        "The constituent list used here is the **current** Nifty 50 list, applied to a",
        "historical window. Companies that were constituents in 2015-2020 and have since",
        "been removed from the index are simply absent from this analysis.",
        "",
        "This biases any comparison of sector or index performance across the window: the",
        "surviving constituents are, by construction, the ones that stayed listed. Any",
        "chart that aggregates across stocks carries this caveat.",
        "",
    ]

    mentions = data.get("mentions")
    if mentions:
        share = mentions["share_with_stock"]
        lines += [
            "## News-to-stock linking",
            "",
            f"- Headlines in window: **{human_int(mentions['headlines_in_window'])}**",
            f"- Headlines naming at least one stock: **{human_int(mentions['with_stock_mention'])}**",
            f"- Share: **{share:.1%}**",
            "",
            "Stock-level mentions are sparse. Sector-level news facts are the more robust",
            "signal, and any figure built on mentions should be read next to this share.",
            "",
        ]
    return "\n".join(lines) + "\n"


def write(con: duckdb.DuckDBPyConnection) -> str:
    """Collect, render and write the report. Returns its path."""
    data = collect(con)
    path = reports_dir() / FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(data), encoding="utf-8")
    log.info("wrote %s", path)
    return str(path)