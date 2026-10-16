"""Linking headlines to stocks and sectors.

Stock-name matching is the weakest part of the whole system, and this module is
written to make that visible rather than to hide it. Two facts drive the design:

1. **Many company names are ordinary words.** "Titan", "Trent", "Indigo", "Max",
   "BEL", "BSE" all collide with English or with other abbreviations. Counting
   "Rain falls on Titan's plant" as a mention of Titan Company would put noise
   into every stock-level figure, and because the result would still be a number,
   nothing downstream would look wrong.

   So an alias flagged `needs_context` only counts when the headline also
   contains a business context word, or when the headline's topic is the
   configured context topic. Accepted rows carry `is_ambiguous_alias = TRUE` so
   they can be audited.

2. **Case sensitivity is a property of the alias, not of the symbol.** "ITC" and
   "itc" are not the same token, and a lowercase `itc` is usually not the
   company. Meanwhile "Tata Consultancy Services" has no meaningful case.

   An earlier version folded every alias for a symbol into ONE regex compiled
   with re.IGNORECASE, which quietly made bare tickers case-insensitive and would
   have matched "itc" as the tobacco company. So each alias gets its own
   compiled pattern carrying its own flags. It costs ~150 compiled patterns
   built once, which is nothing next to scanning 1.1M headlines.

A mention is not evidence of anything. Matching a name proves the word appeared,
not that the story was about the company. `audit_sample.py` exists so a human can
measure the precision of this module rather than assume it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from dwm.config import AliasRules

MATCH_EXACT = "exact"
MATCH_TICKER = "ticker"
MATCH_CONTEXT = "context"


@dataclass(slots=True)
class AliasSpec:
    """One alias for one stock, with its own matching rules."""

    stock_key: int
    symbol: str
    alias: str
    pattern: re.Pattern[str]
    case_sensitive: bool
    needs_context: bool
    kind: str

    def search(self, text: str) -> str | None:
        hit = self.pattern.search(text)
        return hit.group(0) if hit else None


@dataclass(slots=True)
class StockMatcher:
    stock_key: int
    symbol: str
    aliases: list[AliasSpec] = field(default_factory=list)

    def first_hit(self, text: str) -> AliasSpec | None:
        """The alias that matched, preferring an exact-case ticker hit.

        Tickers are checked before looser names so that a headline containing
        both "ITC" and "Indian Tobacco Company" reports the ticker, which is the
        more specific claim.
        """
        strict = [a for a in self.aliases if a.case_sensitive]
        loose = [a for a in self.aliases if not a.case_sensitive]
        for group in (strict, loose):
            for spec in group:
                if spec.search(text) is not None:
                    return spec
        return None


def _bounded(alias: str) -> str:
    return rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])"


def build_matchers(
    rules: AliasRules, stock_keys: dict[str, int]
) -> list[StockMatcher]:
    """One matcher per stock, from the alias config and the live dim_stock.

    Symbols in the alias config that are not current constituents are skipped:
    they are legitimate headline spellings but not stocks in this study, and
    loading them would quietly enlarge the denominator.
    """
    matchers: list[StockMatcher] = []
    for symbol in sorted(rules.aliases):
        stock_key = stock_keys.get(symbol)
        if stock_key is None:
            continue
        entry = rules.aliases[symbol]
        names = [str(n).strip() for n in dict.fromkeys(entry.get("names", []))]
        names = [n for n in names if n]

        sensitive = {str(a) for a in entry.get("case_sensitive", [])}
        ambiguous = {str(a) for a in entry.get("ambiguous", [])}

        matcher = StockMatcher(stock_key=stock_key, symbol=symbol)
        for alias in names:
            case_sensitive = alias in sensitive
            # A bare token equal to the listing symbol is a ticker, and a ticker
            # is case-sensitive whether or not config said so. Matching "infy"
            # or "tcs" as the company would be a false positive.
            is_ticker = alias == symbol
            if is_ticker:
                case_sensitive = True
            pattern = re.compile(
                _bounded(alias), 0 if case_sensitive else re.IGNORECASE
            )
            matcher.aliases.append(
                AliasSpec(
                    stock_key=stock_key,
                    symbol=symbol,
                    alias=alias,
                    pattern=pattern,
                    case_sensitive=case_sensitive,
                    needs_context=alias in ambiguous,
                    kind=MATCH_TICKER if (is_ticker or case_sensitive) else MATCH_EXACT,
                )
            )
        if matcher.aliases:
            matchers.append(matcher)
    return matchers


def context_present(text: str, rules: AliasRules) -> bool:
    """True when the headline carries business context."""
    lowered = text.lower()
    return any(word in lowered for word in rules.context_words)


@dataclass(slots=True)
class Match:
    headline_id: int
    stock_key: int
    symbol: str
    matched_alias: str
    match_method: str
    is_ambiguous_alias: bool


def find_matches(
    headline_id: int,
    text: str,
    matchers: Iterable[StockMatcher],
    rules: AliasRules,
    topic_name: str | None = None,
) -> list[Match]:
    """All stock mentions in one headline.

    A headline can mention several companies, so this returns a list. An
    ambiguous alias needs either a context word or the configured context topic;
    without one it is rejected, and the rejection is silent by design because a
    rejected alias produces no row.
    """
    if not text:
        return []
    has_context = context_present(text, rules)
    topic_ok = (
        bool(rules.use_topic_as_context) and topic_name == rules.use_topic_as_context
    )

    found: list[Match] = []
    for matcher in matchers:
        spec = matcher.first_hit(text)
        if spec is None:
            continue
        if spec.needs_context and not (has_context or topic_ok):
            continue
        found.append(
            Match(
                headline_id=headline_id,
                stock_key=matcher.stock_key,
                symbol=matcher.symbol,
                matched_alias=spec.search(text) or spec.alias,
                match_method=MATCH_CONTEXT if spec.needs_context else spec.kind,
                is_ambiguous_alias=spec.needs_context,
            )
        )
    return found


def load_stock_keys(con: Any, table: str = "dim_stock") -> dict[str, int]:
    return {
        str(symbol): int(key)
        for symbol, key in con.execute(
            f"SELECT symbol, stock_key FROM {table} ORDER BY symbol"
        ).fetchall()
    }
