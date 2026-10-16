"""Surprise days: when news and prices disagree.

Two kinds, and the second is the more interesting one:

    news_driven     both news heat and the move are unusually high. The obvious
                    case, and the one a reader already expected to find.
    quiet_news_move the price moved unusually but news was quiet. The market
                    moved on something this corpus does not contain.

The second is why the study is worth running. A headline corpus from one outlet
cannot see earnings calls filed with the exchange, analyst notes, or global news
carried by a wire service. Days where price moved and our news did not are
therefore evidence about the DATA, not about the market's rationality.

Z-scores use a TRAILING 60-session window. A centred window would score a day
using later information, which is exactly the look-ahead rule 11 forbids.
"""

from __future__ import annotations

from typing import Any

import duckdb
import numpy as np

from dwm.logging_utils import get, human_int
from dwm.mining.common import rolling_z, safe_round

log = get("dwm.mining.surprise_days")

DDL = """
CREATE OR REPLACE TABLE mining_surprise_days (
    date_key INTEGER, sector_key INTEGER, sector_name VARCHAR,
    session DATE,
    news_count BIGINT, news_heat_z DOUBLE, abs_return_z DOUBLE,
    avg_return DOUBLE, kind VARCHAR
)
"""

WINDOW = 60
NEWS_Z = 2.0
RETURN_Z = 2.0


def run(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    con.execute(DDL)
    rows = con.execute(
        """
        WITH news AS (
            SELECT sector_key, effective_trading_date_key AS date_key,
                   sum(news_count_non_market) AS news_count
            FROM fact_news_sector_daily
            GROUP BY 1, 2
        ),
        prices AS (
            SELECT d.sector_key, f.date_key,
                   sum(f.return_pct) / nullif(count(*), 0)     AS avg_return,
                   sum(f.abs_return_pct) / nullif(count(*), 0) AS avg_abs_return
            FROM v_stock_daily_clean f
            JOIN dim_stock d ON d.stock_key = f.stock_key
            WHERE f.return_pct IS NOT NULL
            GROUP BY 1, 2
        )
        SELECT n.sector_key, s.sector_name, n.date_key, d.full_date,
               n.news_count, p.avg_return, p.avg_abs_return
        FROM news n
        JOIN prices p ON p.sector_key = n.sector_key AND p.date_key = n.date_key
        JOIN dim_sector s ON s.sector_key = n.sector_key
        JOIN dim_date d ON d.date_key = n.date_key
        ORDER BY n.sector_key, n.date_key
        """
    ).fetchall()

    by_sector: dict[int, list[tuple[Any, ...]]] = {}
    for row in rows:
        by_sector.setdefault(int(row[0]), []).append(row)

    out: list[tuple[Any, ...]] = []
    counts = {"news_driven": 0, "quiet_news_move": 0}

    for sector_key, series in by_sector.items():
        if len(series) < WINDOW + 1:
            continue
        news = np.array([float(r[4] or 0) for r in series])
        abs_return = np.array([float(r[6] or 0) for r in series])
        news_z = rolling_z(news, WINDOW)
        ret_z = rolling_z(abs_return, WINDOW)

        for i, row in enumerate(series):
            if not (np.isfinite(news_z[i]) and np.isfinite(ret_z[i])):
                continue
            kind = None
            if news_z[i] > NEWS_Z and ret_z[i] > RETURN_Z:
                kind = "news_driven"
            elif ret_z[i] > RETURN_Z and news_z[i] < 0:
                kind = "quiet_news_move"
            if kind is None:
                continue
            counts[kind] += 1
            out.append(
                (
                    int(row[2]), sector_key, row[1], row[3],
                    int(row[4] or 0), safe_round(news_z[i], 4),
                    safe_round(ret_z[i], 4), safe_round(row[5], 4), kind,
                )
            )

    if out:
        con.executemany("INSERT INTO mining_surprise_days VALUES (?,?,?,?,?,?,?,?,?)", out)

    total = len(by_sector)
    quiet_share = (
        counts["quiet_news_move"]
        / (counts["news_driven"] + counts["quiet_news_move"])
        if (counts["news_driven"] + counts["quiet_news_move"])
        else None
    )
    summary = {
        "sectors_scored": total,
        "surprise_days": len(out),
        "news_driven": counts["news_driven"],
        "quiet_news_move": counts["quiet_news_move"],
        "quiet_share_of_surprises": safe_round(quiet_share, 4),
        "window": WINDOW,
        "thresholds": {"news_z": NEWS_Z, "abs_return_z": RETURN_Z},
        "caveat": (
            "quiet_news_move days are where price moved and this corpus had "
            "little to say. That is a statement about the data: one news outlet "
            "does not see earnings calls, analyst notes, or wire-service "
            "headlines, so a quiet day here does not mean a quiet market."
        ),
    }
    log.info(
        "surprise days: %s news_driven, %s quiet_news_move across %s sectors",
        human_int(counts["news_driven"]), human_int(counts["quiet_news_move"]), total,
    )
    return summary


def top(con: duckdb.DuckDBPyConnection, kind: str, limit: int = 10) -> list[dict[str, Any]]:
    """The largest surprises of one kind, for the insights feed."""
    return [
        {
            "sector": r[0], "session": str(r[1]), "news_count": int(r[2]),
            "news_heat_z": r[3], "abs_return_z": r[4], "avg_return": r[5],
        }
        for r in con.execute(
            f"""
            SELECT sector_name, session, news_count, news_heat_z, abs_return_z, avg_return
            FROM mining_surprise_days WHERE kind = ?
            ORDER BY abs_return_z DESC LIMIT {int(limit)}
            """,
            [kind],
        ).fetchall()
    ]
