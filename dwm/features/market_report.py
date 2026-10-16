"""The market-report lexicon.

Many business headlines REPORT the market move rather than cause it: "Sensex
tanks 500 points", "Nifty ends 2% lower". Including those when asking "does news
predict prices?" is reverse causation, and it inflates every correlation
measured, because the headline was written after the price moved.

The flag is deliberately conservative. Rule 13 excludes these headlines from
predictive analysis, so a false positive here deletes a real headline from the
training set, while a false negative leaves reverse causation in and biases the
answer upward. Over-flagging therefore costs data; under-flagging costs
truth. It was measured against the corpus rather than guessed - see
`tests/test_v3_linking.py::test_the_flag_rate_is_plausible_on_the_corpus`.

Design: a headline counts as a market report when it contains either

  * a market NAME - Sensex, Nifty, Dalal Street. These are unambiguous in a
    financial-news corpus, so a single mention is enough.
  * a REPORTING PHRASE - "market closes", "ends higher", "closing bell". These
    describe a finished session and carry the reverse-causation signal even
    without naming an index.

An earlier version of this file also matched bare move words ("falls", "gains")
and bare session words ("points"). That was wrong: "rain falls in Kerala" and
"three points behind" are not market reports, and the flag fired on a large
slice of the corpus. The move words now only appear inside phrases.

Bare acronyms (BSE, NSE) are matched case-sensitively, because "BSE" in upper
case is the exchange and "bse" is often something else entirely.
"""

from __future__ import annotations

import re

from dwm.logging_utils import get

log = get("dwm.features.market_report")

# Index and venue names. A mention is enough on its own.
MARKET_NAMES: tuple[str, ...] = (
    "sensex", "nifty", "nifty fifty", "nifty50", "dalal street",
    "bombay stock exchange", "national stock exchange",
    "bse sensex", "cnx nifty",
)

# Phrases that describe a session that has already finished.
REPORTING_PHRASES: tuple[str, ...] = (
    "market closes", "market closed", "market ends", "market ended",
    "markets end", "markets close", "markets closed",
    "market falls", "market fell", "market drops", "market dropped",
    "market rises", "market rose", "market gains", "market gained",
    "market slips", "market slumped", "market tanks", "market tanked",
    "market plunges", "market plunged", "market rallies", "market rallied",
    "stock market", "share market", "equity market", "equities end",
    "closing bell", "opening bell", "market opening", "market opening",
    "stock indices", "share prices", "share price", "wall street",
    "all-time high", "52-week high", "52-week low", "nifty today",
    "sensex today", "markets today", "stock markets",
)

# Bare acronyms, matched case-sensitively: "BSE" is the exchange, "bse" often
# is not. The boundaries matter: without them "CBSE launches exam centre
# locator app" is flagged, because BSE sits inside CBSE.
CASE_SENSITIVE_ACRONYMS: tuple[str, ...] = ("BSE", "NSE", "NIFTY", "SENSEX")

# Move words only ever appear inside a phrase, never alone. "rain falls in
# Kerala" is not a market report and matching bare "falls" flagged it as one.
_MOVE_IN_PHRASE = (
    "tanks", "tanked", "plunges", "plunged", "slumps", "slumped", "slides",
    "drops", "dropped", "falls", "fell", "rises", "rose", "gains", "gained",
    "jumps", "jumped", "soars", "surges", "surged", "nudges", "climbs",
    "climbed", "advances", "retreated", "ends higher", "ends lower",
    "ends flat", "edges up", "edges down", "up on", "down on",
)


def _bounded(terms: tuple[str, ...]) -> str:
    parts = []
    for term in terms:
        escaped = re.escape(term.strip().lower())
        parts.append(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])")
    return "|".join(parts)


_LOWER_PATTERN = re.compile(
    _bounded(MARKET_NAMES + REPORTING_PHRASES), re.IGNORECASE
)
_ACRONYM_PATTERN = re.compile(
    "|".join(
        rf"(?<![A-Za-z]){re.escape(a)}(?![A-Za-z])"
        for a in CASE_SENSITIVE_ACRONYMS
    )
)

# Reported so the exact term list travels with the flag rate.
LEXICON: list[str] = sorted(
    set(MARKET_NAMES) | set(REPORTING_PHRASES) | set(CASE_SENSITIVE_ACRONYMS)
)


def is_market_report(text: str | None) -> bool:
    """True when the headline appears to report a market move.

    Case-insensitive on names and phrases, case-sensitive on bare acronyms.
    Returns False for empty text rather than raising, because a NULL headline
    should not stop a chunk.
    """
    if not text:
        return False
    raw = str(text)
    if _LOWER_PATTERN.search(raw.lower()):
        return True
    return _ACRONYM_PATTERN.search(raw) is not None


def matched_terms(text: str | None) -> list[str]:
    """Which lexicon terms fired. Used to justify individual flags in the audit."""
    if not text:
        return []
    raw = str(text)
    found = {m.group(0).lower() for m in _LOWER_PATTERN.finditer(raw.lower())}
    found |= {m.group(0) for m in _ACRONYM_PATTERN.finditer(raw)}
    return sorted(found)


def terms() -> list[str]:
    return list(LEXICON)
