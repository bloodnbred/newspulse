"""Phase V3 tests: market-report flagging and news-to-stock/sector linking.

The tests that matter most here are the negative ones. A linker that matches
everything scores well on any "did you find the mentions" check, because finding
"Max" and "Titan" in a headline about a hospital is not a finding. So:

  * an ambiguous alias WITHOUT context is rejected
  * an ambiguous alias WITH context is accepted
  * a bare ticker is case-sensitive
  * ordinary English that merely contains a company word is not a mention

None of these touch the network, and none depend on a live warehouse.
"""
from __future__ import annotations

import pytest

from dwm.config import AliasRules, load_stock_aliases
from dwm.features.market_report import is_market_report, matched_terms
from dwm.linking.aliases import build_matchers, find_matches
from dwm.linking.sector_news import build_keyword_patterns, keyword_sector

# ---------------------------------------------------------------------------
# market-report flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Sensex tanks 500 points on FII selling",
        "Nifty ends 2% lower amid heavy selling",
        "Markets close in green for third straight session",
        "Wall Street ends mixed on trade data",
        "NSE and BSE announce new norms",
        "Sensex gives up early gains; drops 54 points",
    ],
)
def test_headlines_reporting_a_move_are_flagged(text: str) -> None:
    assert is_market_report(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # The regression this file exists for: bare move and session words are
        # not market reports on their own.
        "Heavy rain falls in Kerala",
        "India is three points behind in the series",
        "Cricket team gains three points",
        "RBI keeps repo rate unchanged",
        "Reliance Industries posts record profit",
        "Tata Motors launches new electric vehicle",
        "Doctor shares health tips for winter",
        "IT services firm wins multi-year contract",
        # BSE inside a longer acronym must not match.
        "CBSE launches exam centre locator mobile app",
    ],
)
def test_ordinary_headlines_are_not_flagged(text: str) -> None:
    assert is_market_report(text) is False


def test_the_flag_rate_is_plausible_on_the_corpus() -> None:
    """A lexicon that fires on 5% of headlines is not finding market reports,
    it is finding the word 'falls'. The band here is deliberately wide; the
    point is to catch a runaway flag rate, not to pin the value."""
    samples = [
        "RBI keeps repo rate unchanged",
        "Sensex tanks 500 points",
        "Heavy rain falls in Kerala",
        "Tata Consultancy Services wins deal",
        "Nifty ends 2% lower",
        "Three points behind in the series",
        "Nifty touches all-time high",
        "Cricket team gains three points",
        "Markets close in green",
        "IT services firm wins contract",
    ]
    flagged = sum(1 for s in samples if is_market_report(s))
    assert flagged == 4, "expected the four genuine market reports"


def test_matched_terms_explain_the_flag() -> None:
    assert "sensex" in matched_terms("Sensex tanks 500 points")
    assert matched_terms("RBI keeps repo rate unchanged") == []
    assert is_market_report(None) is False


# ---------------------------------------------------------------------------
# alias matching
# ---------------------------------------------------------------------------


def _rules() -> AliasRules:
    return AliasRules(
        context_words=["shares", "profit", "q1", "results", "stock"],
        use_topic_as_context="Business",
        aliases={
            # unambiguous full name plus a bare ticker
            "TCS": {
                "names": ["Tata Consultancy Services", "TCS"],
                "ambiguous": [],
                "case_sensitive": ["TCS"],
            },
            # "Max" is also an ordinary adjective
            "MAXHEALTH": {
                "names": ["Max Healthcare", "Max"],
                "ambiguous": ["Max"],
                "case_sensitive": [],
            },
            # bare ticker, also an ordinary acronym
            "BEL": {
                "names": ["Bharat Electronics", "BEL"],
                "ambiguous": ["BEL"],
                "case_sensitive": ["BEL"],
            },
        },
    )


def _matchers(rules: AliasRules | None = None):
    return build_matchers(
        rules or _rules(), {"TCS": 1, "MAXHEALTH": 2, "BEL": 3}
    )


def test_an_unambiguous_name_matches_without_context() -> None:
    found = find_matches(
        1, "Tata Consultancy Services signs a new contract", _matchers(), _rules()
    )
    assert [m.symbol for m in found] == ["TCS"]
    assert found[0].is_ambiguous_alias is False


def test_a_bare_ticker_is_case_sensitive() -> None:
    """Lowercase 'tcs' is not the company. Compiling every alias with
    re.IGNORECASE, as an earlier version did, would have matched it."""
    rules = _rules()
    assert find_matches(1, "TCS wins a deal", _matchers(rules), rules)
    assert find_matches(1, "tcs wins a deal", _matchers(rules), rules) == []


def test_an_ambiguous_alias_without_context_is_rejected() -> None:
    """'Max 5' is not the hospital chain."""
    found = find_matches(1, "Max 5 bridges collapse in Bihar", _matchers(), _rules())
    assert found == []


def test_an_ambiguous_alias_with_a_context_word_is_accepted() -> None:
    """Only the bare 'Max' needs context. When the full name appears, the
    match is unambiguous and no context is required - which is why this
    headline must contain the bare token and nothing more."""
    found = find_matches(1, "Max shares slipped after results", _matchers(), _rules())
    assert [m.symbol for m in found] == ["MAXHEALTH"]
    assert found[0].is_ambiguous_alias is True
    assert found[0].match_method == "context"
    assert found[0].matched_alias == "Max"


def test_the_unambiguous_full_name_needs_no_context() -> None:
    found = find_matches(1, "Max Healthcare opened a plant", _matchers(), _rules())
    assert [m.symbol for m in found] == ["MAXHEALTH"]
    assert found[0].is_ambiguous_alias is False
    assert found[0].matched_alias == "Max Healthcare"


def test_an_ambiguous_alias_on_the_context_topic_is_accepted() -> None:
    found = find_matches(
        1, "Max is the word on the street", _matchers(), _rules(),
        topic_name="Business",
    )
    assert [m.symbol for m in found] == ["MAXHEALTH"]


def test_the_context_topic_does_not_apply_to_other_topics() -> None:
    found = find_matches(
        1, "Max is the word on the street", _matchers(), _rules(),
        topic_name="Local",
    )
    assert found == []


def test_a_case_sensitive_acronym_needs_context_too() -> None:
    rules = _rules()
    assert find_matches(1, "BEL opens a new office", _matchers(rules), rules) == []
    assert find_matches(1, "BEL shares gain", _matchers(rules), rules)


def test_substrings_do_not_match() -> None:
    """'Maxwell' must not match 'Max'."""
    assert find_matches(1, "Maxwell wins a contract", _matchers(), _rules()) == []


def test_a_headline_can_mention_several_companies() -> None:
    found = find_matches(
        1,
        "Tata Consultancy Services and Max Healthcare shares",
        _matchers(), _rules(),
    )
    assert sorted(m.symbol for m in found) == ["MAXHEALTH", "TCS"]


def test_aliases_for_non_constituents_are_skipped() -> None:
    rules = _rules()
    rules.aliases["WIPRO"] = {"names": ["Wipro"], "ambiguous": [], "case_sensitive": []}
    matchers = build_matchers(rules, {"TCS": 1, "MAXHEALTH": 2, "BEL": 3})
    assert "WIPRO" not in {m.symbol for m in matchers}


def test_the_real_alias_config_covers_every_constituent() -> None:
    """A linker that cannot name a stock silently under-reports coverage, so
    every Nifty 50 constituent needs at least one alias."""
    from pathlib import Path

    # Skip rather than fail on a clean checkout with no fetched data.
    meta_file = Path("data/raw/meta/nifty50_constituents.csv")
    if not meta_file.exists():
        pytest.skip(
            "constituent list not fetched; run `python -m dwm ingest --dataset meta`"
        )
    import csv

    with meta_file.open(encoding="utf-8-sig") as fh:
        symbols = {r["Symbol"].strip().upper() for r in csv.DictReader(fh)}
    missing = sorted(symbols - set(load_stock_aliases().aliases))
    assert not missing, f"constituents with no alias: {missing}"


def test_tata_motors_headlines_reach_the_current_listing() -> None:
    """The 2025 demerger renamed TATAMOTORS to TMPV. Headlines inside the
    2015-2020 window still say 'Tata Motors'."""
    assert "Tata Motors" in load_stock_aliases().aliases["TMPV"]["names"]


# ---------------------------------------------------------------------------
# sector keywords
# ---------------------------------------------------------------------------


def test_sector_keywords_match_on_word_boundaries() -> None:
    patterns = build_keyword_patterns(
        {"Energy": ["oil prices", "crude"], "Metals": ["steel"]}
    )
    assert keyword_sector("Crude oil prices rise sharply", patterns) == ["Energy"]
    assert keyword_sector("Steel demand slows", patterns) == ["Metals"]
    # "steely" is not "steel", and "crude oil" alone is not the phrase.
    assert keyword_sector("A steely determination", patterns) == []


def test_a_headline_can_reach_several_sectors() -> None:
    patterns = build_keyword_patterns(
        {"Energy": ["crude"], "Metals": ["steel"], "IT": ["software"]}
    )
    assert keyword_sector("Crude and steel and software", patterns) == [
        "Energy", "Metals", "IT",
    ]


def test_the_real_sector_lexicon_covers_all_ten_sectors() -> None:
    from dwm.linking.sector_news import load_sector_keywords

    lexicon = load_sector_keywords()
    assert len(lexicon) == 10
    assert all(terms for terms in lexicon.values())


def test_the_sector_lexicon_only_names_configured_sectors() -> None:
    """A lexicon naming a sector that is not in dim_sector would silently drop
    every match for it."""
    from dwm.config import load_sector_config
    from dwm.linking.sector_news import load_sector_keywords

    unknown = set(load_sector_keywords()) - set(load_sector_config().sectors)
    assert not unknown, f"sector keywords name unknown sectors: {unknown}"
