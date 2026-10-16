"""Quick Brief: a plain-English answer about a company or sector.

This is the page a first-time visitor reaches, so it is the one that must not
require any DWM knowledge to read. It is template-based: every sentence is
composed from measured numbers, and no model generated any of the prose.

Three honesty constraints:

1. **"Our news" not "the news".** This corpus is one outlet over five years. A
   brief that said "Reliance has had 412 headlines" would be read as a claim
   about the world; it is a claim about one newspaper's coverage, and the
   wording says so.

2. **Comparison is always within this corpus.** "Its news mood is well below its
   own five-year average", never "negative".

3. **Mention counts carry their precision.** A headline naming a company proves
   the name appeared, not that the story was about it, and the brief says so
   next to the number rather than in a footnote.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any

import duckdb

from dwm.logging_utils import get

log = get("dwm.inference.brief")

#: Above this similarity, the suggestion is offered without being asked for.
STRONG_MATCH = 0.82
#: Below it, the answer refuses rather than guessing at a company.
ACCEPT_MATCH = 0.62

Fuzzy = difflib.SequenceMatcher


@dataclass(slots=True)
class Resolution:
    kind: str            # 'stock' | 'sector' | 'none'
    name: str
    stock_key: int | None = None
    sector_key: int | None = None
    match_quality: float = 0.0
    alternatives: list[tuple[str, float]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.alternatives is None:
            self.alternatives = []


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] = ()) -> list[tuple]:
    return con.execute(sql, list(params)).fetchall()


def resolve(
    con: duckdb.DuckDBPyConnection, query: str, rules
) -> Resolution:
    """Resolve a name, ticker, alias or sector to a stock or a sector.

    Tries, in order: an exact symbol, an exact alias, an exact sector name, then
    the closest matches by sequence similarity. The fallback returns what it
    found as ALTERNATIVES rather than committing to the nearest one, because
    "Reliance" could mean Reliance Industries and picking wrong would put a
    confident-sounding paragraph about the wrong company in front of the reader.
    """
    text = (query or "").strip()
    if not text:
        return Resolution(kind="none", name="")

    lowered = text.casefold()

    row = _rows(
        con, "SELECT stock_key, symbol FROM dim_stock WHERE lower(symbol) = ?",
        [lowered],
    )
    if row:
        return Resolution("stock", row[0][1], stock_key=int(row[0][0]), match_quality=1.0)

    alias = _rows(
        con,
        """
        SELECT DISTINCT d.stock_key, d.symbol
        FROM dim_stock_alias a JOIN dim_stock d ON d.stock_key = a.stock_key
        WHERE lower(a.alias) = ?
        """,
        [lowered],
    )
    if alias:
        return Resolution(
            "stock", alias[0][1], stock_key=int(alias[0][0]), match_quality=1.0
        )

    sector = _rows(
        con, "SELECT sector_key, sector_name FROM dim_sector WHERE lower(sector_name) = ?",
        [lowered],
    )
    if sector:
        return Resolution(
            "sector", sector[0][1], sector_key=int(sector[0][0]), match_quality=1.0
        )

    # Fuzzy: score every symbol and every alias, keep the best few.
    candidates: list[tuple[str, int, float]] = []
    for stock_key, symbol in _rows(con, "SELECT stock_key, symbol FROM dim_stock"):
        score = Fuzzy(None, lowered, symbol.casefold()).ratio()
        candidates.append((symbol, int(stock_key), score))
    for symbol, stock_key, alias_name in _rows(
        con,
        """
        SELECT d.symbol, d.stock_key, a.alias
        FROM dim_stock_alias a JOIN dim_stock d ON d.stock_key = a.stock_key
        """,
    ):
        score = Fuzzy(None, lowered, alias_name.casefold()).ratio()
        candidates.append((symbol, int(stock_key), score))
    for sector_key, name in _rows(con, "SELECT sector_key, sector_name FROM dim_sector"):
        score = Fuzzy(None, lowered, name.casefold()).ratio()
        candidates.append((name, int(sector_key), score))

    candidates.sort(key=lambda c: -c[2])
    best = candidates[0] if candidates else None
    if not best or best[2] < ACCEPT_MATCH:
        return Resolution(
            kind="none", name=text,
            alternatives=[(c[0], round(c[2], 3)) for c in candidates[:5]],
        )

    is_sector = _rows(
        con, "SELECT 1 FROM dim_sector WHERE lower(sector_name) = ?", [best[0].casefold()]
    )
    return Resolution(
        kind="sector" if is_sector else "stock",
        name=best[0],
        stock_key=None if is_sector else best[1],
        sector_key=best[1] if is_sector else None,
        match_quality=round(best[2], 3),
        alternatives=[(c[0], round(c[2], 3)) for c in candidates[1:4]],
    )


def brief_for_stock(
    con: duckdb.DuckDBPyConnection, stock_key: int, *, limit: int = 5
) -> dict[str, Any]:
    """A plain-English summary of one company's coverage in this corpus."""
    meta = _rows(
        con,
        """
        SELECT d.symbol, d.company_name, s.sector_name, d.is_survivor,
               d.first_trade_date, d.last_trade_date, d.price_source
        FROM dim_stock d LEFT JOIN dim_sector s ON s.sector_key = d.sector_key
        WHERE d.stock_key = ?
        """,
        [stock_key],
    )[0]
    symbol, company, sector, survivor, first_trade, last_trade, price_source = meta

    news = _rows(
        con,
        """
        SELECT count(*) AS mentions,
               count(DISTINCT m.headline_id) AS headlines,
               round(avg(h.sentiment_compound), 4) AS mean_sentiment,
               round(sum(CASE WHEN h.is_sensational THEN 1 ELSE 0 END) * 100.0
                     / nullif(count(*), 0), 2) AS sensational_pct,
               round(sum(CASE WHEN h.is_market_report THEN 1 ELSE 0 END) * 100.0
                     / nullif(count(*), 0), 2) AS market_report_pct
        FROM fact_news_stock_mention m
        JOIN fact_headline h ON h.headline_id = m.headline_id
        WHERE m.stock_key = ? AND h.in_window
        """,
        [stock_key],
    )[0]
    mentions, headlines, mean_sentiment, sensational_pct, market_report_pct = news

    recent = _rows(
        con,
        """
        SELECT h.publish_date, h.headline_text, h.sentiment_compound
        FROM fact_news_stock_mention m
        JOIN fact_headline h ON h.headline_id = m.headline_id
        WHERE m.stock_key = ? AND h.in_window
        ORDER BY h.publish_date DESC LIMIT ?
        """,
        [stock_key, limit],
    )

    price = None
    if price_source:
        price = _rows(
            con,
            """
            SELECT round(avg(f.return_pct), 4), round(avg(f.volatility_20d), 4),
                   count(*), min(f.trade_date), max(f.trade_date)
            FROM v_stock_daily_clean f
            WHERE f.stock_key = ? AND f.return_pct IS NOT NULL
            """,
            [stock_key],
        )[0]

    sentiment_line = (
        f"Its headlines here ran {'above' if (mean_sentiment or 0) > 0 else 'below'} "
        f"neutral on average ({mean_sentiment:+.3f} on this corpus's sentiment "
        f"scale)."
        if mean_sentiment is not None
        else "Too few headlines here to summarise a mood."
    )

    return {
        "kind": "stock",
        "name": company or symbol,
        "symbol": symbol,
        "sector": sector,
        "is_survivor": bool(survivor),
        "paragraphs": [
            (
                f"**{company or symbol}** is in the {sector or 'unclassified'} sector"
                + (". " if sector else " and has no sector assigned. ")
                + (
                    "This corpus carries "
                    f"{headlines:,} of our headline{'s' if headlines != 1 else ''} "
                    f"naming it, out of 1.1 million in the window."
                    if headlines
                    else "and none of our headlines name it."
                )
            ),
            sentiment_line,
            (
                f"About {sensational_pct:.1f}% of those headlines scored as "
                f"high-sensational writing in our measures, and "
                f"{market_report_pct:.1f}% were reports of a market move rather "
                f"than news about the company."
                if headlines
                else ""
            ),
            (
                f"On price, we hold {price[2]:,} sessions for it"
                + (f" from {price[3]} to {price[4]}" if price and price[2] else "")
                + (
                    f", averaging {price[0]:+.3f}% a day at {price[1]:.2f}% "
                    f"20-day volatility."
                    if price and price[0] is not None
                    else "."
                )
                if price
                else (
                    "We hold no price history for it inside the window, so nothing "
                    "here says anything about how the shares behaved."
                    if not price_source
                    else ""
                )
            ),
        ],
        "numbers": {
            "headlines_naming_it": int(headlines or 0),
            "mention_rows": int(mentions or 0),
            "mean_sentiment": mean_sentiment,
            "sensational_pct": sensational_pct,
            "market_report_pct": market_report_pct,
            "sessions": int(price[2]) if price else 0,
            "mean_daily_return_pct": price[0] if price else None,
            "mean_volatility_pct": price[1] if price else None,
        },
        "recent_headlines": [
            {
                "date": str(r[0]),
                "text": r[1],
                "sentiment": round(float(r[2]), 4) if r[2] is not None else None,
            }
            for r in recent
        ],
        "links": {"profile": f"/stocks/{symbol}", "sector": f"/sectors/{sector}"},
        "caveats": [
            "These are OUR headlines from one outlet. Coverage is not coverage of "
            "the world.",
            "A mention means the company name appeared in the headline, not that "
            "the story was about it. Match precision has not been humanly audited.",
            "Price returns are percentage points from adjusted closes.",
        ],
    }


def brief_for_sector(
    con: duckdb.DuckDBPyConnection, sector_key: int
) -> dict[str, Any]:
    """A plain-English summary of one sector's coverage and price behaviour."""
    name = _rows(
        con, "SELECT sector_name FROM dim_sector WHERE sector_key = ?", [sector_key]
    )[0][0]
    row = _rows(
        con,
        """
        SELECT sum(news_count), sum(news_count_non_market), count(*)
        FROM cube_sector_month WHERE sector_key = ?
        """,
        [sector_key],
    )[0]
    prices = _rows(
        con,
        """
        SELECT sum(sum_return) / nullif(sum(stock_obs), 0),
               sum(sum_abs_return) / nullif(sum(stock_obs), 0), sum(stock_obs)
        FROM cube_sector_month WHERE sector_key = ?
        """,
        [sector_key],
    )[0]
    stocks = [
        r[0]
        for r in _rows(
            con,
            """
            SELECT d.symbol FROM dim_stock d
            WHERE d.sector_key = ? AND d.price_source IS NOT NULL ORDER BY d.symbol
            """,
            [sector_key],
        )
    ]
    busy = _rows(
        con,
        """
        SELECT c.year, c.month, c.news_count
        FROM cube_sector_month c
        WHERE c.sector_key = ? AND c.news_count > 0
        ORDER BY c.news_count DESC LIMIT 1
        """,
        [sector_key],
    )
    return {
        "kind": "sector",
        "name": name,
        "paragraphs": [
            (
                f"**{name}** carried {int(row[0] or 0):,} headline-days in our "
                f"corpus across the window, across {len(stocks)} constituents "
                f"we hold prices for."
            ),
            (
                f"On price, the sector's stocks averaged "
                f"{prices[0]:+.3f}% per session with {prices[1]:.2f}% average "
                f"absolute move, over {int(prices[2] or 0):,} stock-days."
                if prices[0] is not None
                else "We hold no usable prices for this sector inside the window."
            ),
            (
                f"Our busiest month for {name} was "
                f"{busy[0][0]}-{busy[0][1]:02d}, with {int(busy[0][2]):,} "
                f"headline-days."
                if busy
                else ""
            ),
        ],
        "numbers": {
            "headline_days": int(row[0] or 0),
            "months": int(row[2] or 0),
            "mean_return_pct": round(float(prices[0]), 4) if prices[0] else None,
            "mean_abs_return_pct": round(float(prices[1]), 4) if prices[1] else None,
            "stock_days": int(prices[2] or 0),
            "constituents": len(stocks),
        },
        "constituents": stocks,
        "links": {"cube": f"/olap/sector/slice?sector={name}"},
        "caveats": [
            "These are OUR headlines from one outlet, not coverage of the world.",
            "A headline-day counts a sector once even when several of its "
            "companies are named.",
            "Sector means are derived from sums and counts, not stored averages.",
            "Constituents are today's Nifty 50 list, not the list of each month "
            "in the window, so the sector is not the one that existed then.",
        ],
    }


def build(con: duckdb.DuckDBPyConnection, query: str, rules=None) -> dict[str, Any]:
    """The full brief, including what we could NOT resolve."""
    if rules is None:
        from dwm.config import load_stock_aliases

        rules = load_stock_aliases()

    found = resolve(con, query, rules)

    if found.kind == "stock" and found.stock_key is not None:
        payload = brief_for_stock(con, found.stock_key)
        payload["query"] = query
        payload["matched"] = found.name
        payload["match_quality"] = found.match_quality
        payload["did_you_mean"] = [a for a in found.alternatives if a[1] >= STRONG_MATCH]
        return payload

    if found.kind == "sector" and found.sector_key is not None:
        payload = brief_for_sector(con, found.sector_key)
        payload["query"] = query
        payload["matched"] = found.name
        payload["match_quality"] = found.match_quality
        payload["did_you_mean"] = [a for a in found.alternatives if a[1] >= STRONG_MATCH]
        return payload

    return {
        "kind": "none",
        "query": query,
        "message": (
            f"We could not confidently match '{query}' to a company or sector in "
            f"this study. We cover the Nifty 50 constituents and ten sectors."
        ),
        "did_you_mean": [
            {"name": name, "similarity": score}
            for name, score in (found.alternatives or [])[:5]
        ],
        "caveats": [
            "Guessing a company here would produce a confident paragraph about "
            "the wrong company, which is worse than saying we did not match it.",
        ],
    }
