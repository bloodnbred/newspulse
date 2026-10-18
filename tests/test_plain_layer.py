"""The plain-language layer must not drift from the measurements.

`dwm/inference/plain.py` exists so a reader with no data-warehousing background
can reach a conclusion from this dashboard. It does that by restating measured
values in ordinary English, and every number in it is interpolated from
`facts.json` rather than retyped — so it cannot drift *by construction*.

That property is easy to state and easy to lose. The moment someone adds a
sentence with a hard-coded figure, or a measurement changes and the sentence is
not revisited, the layer starts confidently asserting numbers nobody measured.
That is the single worst failure mode this project has: a fluent sentence
containing a figure nobody computed.

So the restating is asserted here, against the real `facts.json`. Each check
pairs a quoted fragment with the fact it must agree with.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dwm.inference import plain as plain_module

ROOT = Path(__file__).resolve().parent.parent
FACTS = ROOT / "reports" / "facts.json"

pytestmark = pytest.mark.skipif(
    not FACTS.is_file(),
    reason="needs reports/facts.json — run `dwm report` first",
)


@pytest.fixture(scope="module")
def facts() -> dict:
    return json.loads(FACTS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def layer(facts: dict) -> dict:
    return plain_module.build_plain(facts)


# ---------------------------------------------------------------------------
# structure
# ---------------------------------------------------------------------------


def test_every_part_of_the_layer_is_present(layer: dict) -> None:
    for key in ("intro", "summary", "glossary", "scales", "cluster_terms", "why"):
        assert key in layer, f"the plain layer is missing {key!r}"
    assert len(layer["intro"]["paragraphs"]) == 3
    assert len(layer["summary"]["items"]) >= 5
    assert len(layer["glossary"]["terms"]) >= 12


def test_the_summary_covers_all_seven_questions(layer: dict, facts: dict) -> None:
    """A plain summary that quietly drops a null result is the failure this
    project cares most about, so the null has to be in it too."""
    covered = {item["rq"] for item in layer["summary"]["items"]}
    assert covered == {"RQ1", "RQ2", "RQ3", "RQ4", "RQ5", "RQ6", "RQ7"}, covered

    # And the null and qualified findings specifically.
    ids = {item["id"] for item in layer["summary"]["items"]}
    for outcome in facts["outcomes"]["outcomes"]:
        if outcome["kind"] in {"null", "qualified"}:
            assert outcome["id"] in ids, (
                f"{outcome['id']} is {outcome['kind']} and is missing from the "
                "plain summary"
            )


def test_every_summary_item_carries_a_caution(layer: dict) -> None:
    """The same rule the technical layer obeys. Plain English is exactly where a
    caveat gets dropped, because a short sentence feels like it cannot need one."""
    for item in layer["summary"]["items"]:
        assert item.get("caution"), f"{item['id']} has no caution"
        assert item.get("so_what"), f"{item['id']} says what, but not why it matters"


# ---------------------------------------------------------------------------
# the numbers must match the measurements
# ---------------------------------------------------------------------------


def test_intro_counts_match_the_corpus(layer: dict, facts: dict) -> None:
    counts = facts["corpus"]["row_counts"]
    window = facts["corpus"]["analysis_window"]
    text = " ".join(layer["intro"]["paragraphs"])
    for expected in (
        f"{counts['fact_headline']:,}",
        f"{counts['fact_statement']:,}",
        f"{window['headlines_in_window']:,}",
    ):
        assert expected in text, f"the intro does not state {expected}"


def test_summary_quotes_the_measured_values(layer: dict, facts: dict) -> None:
    """Each summary item's figures must appear verbatim in the prose.

    Interpolated from the same source, so this is a tautology unless someone has
    typed a number by hand — which is exactly what it is here to catch.
    """
    metrics = facts["rq7_classifier"]["metrics"]
    top = facts["rq3_sensationalism"]["ranked"][0]
    strongest = facts["rq6_market_association"]["volatility"]["strongest"]
    corpus_rate = facts["rq3_sensationalism"]["corpus_rate"]
    rules = facts["rq5_association_rules"]

    items = {i["id"]: i for i in layer["summary"]["items"]}

    classifier = items["classifier"]["body"]
    assert f"{metrics['accuracy'] * 100:.1f}%" in classifier
    assert f"{metrics['majority_baseline_accuracy'] * 100:.1f}%" in classifier

    language = items["language"]["body"]
    assert f"{top['risk_signal_rate'] * 100:.1f}%" in language
    assert f"{corpus_rate * 100:.1f}%" in language

    rules_text = items["rules"]["body"]
    assert f"{rules['rule_count']:,}" in rules_text
    assert str(rules["tautological_rules"]) in rules_text

    # The correlation is restated as a count in a hundred, not as a coefficient,
    # so the coefficient itself must not appear in the plain prose.
    market = items["market"]
    assert f"{abs(strongest['pearson_r']):.4f}" not in market["so_what"]
    assert f"{round(abs(strongest['pearson_r']) * 100)}" in market["so_what"]

    # The volume item states a variation, and must not invent a month count it
    # did not measure. There is no month count in the data: two of the six years
    # are partial, so any total would be wrong.
    volume = items["volume"]["body"]
    assert f"{facts['rq2_bursts']['max_within_year_cv'] * 100:.1f}%" in volume
    assert not re.search(r"\b\d+\s+of\s+(roughly\s+)?\d+\s+months", volume)


def test_scales_never_overstate_a_measurement(layer: dict, facts: dict) -> None:
    """A scale that flatters the number is worse than no scale."""
    scales = layer["scales"]

    # The silhouette scale must not claim the score is high. It is in the normal
    # band for short text and reaches the top of it only because of one cluster.
    silhouette = scales["silhouette"].lower()
    assert "0 to 1" in silhouette
    assert "normal band" in silhouette or "sits in the normal" in silhouette
    assert "excellent" not in silhouette

    # The correlation scale must state the range, or it is not a scale.
    assert "-1 to +1" in scales["volatility_correlation"]
    assert "weak" in scales["volatility_correlation"].lower()

    # The rule-count scale must carry the tautology count, because the headline
    # figure overstates the finding by a wide margin.
    assert str(facts["rq5_association_rules"]["tautological_rules"]) in scales["rule_count"]


def test_cluster_terms_are_glossed_not_invented(layer: dict, facts: dict) -> None:
    """Every gloss comes from a real term in the cluster's own vocabulary.

    A cluster description that names a theme none of its words support would be
    the exact failure this project is built to avoid, in the one place a reader is
    most likely to take it on trust.
    """
    glosses = layer["cluster_terms"]
    clusters = {str(c["cluster_id"]): c for c in facts["rq4_clusters"]["clusters"]}

    assert set(glosses) == set(clusters)

    currency_units = {"crore", "lakh", "rs", "rupees", "crores", "lakhs"}
    for cluster_id, gloss in glosses.items():
        raw = {t.lower() for t in (gloss.get("raw") or [])}
        assert raw, f"cluster {cluster_id} has no raw terms recorded"

        if gloss.get("is_dominant"):
            # The big cluster must be described as the absence of a topic.
            assert "no distinguishing subject" in gloss["summary"].lower()
            continue

        # Every non-dominant cluster's gloss must be backed by at least one of
        # its own words, checked against the pattern table directly.
        backed = False
        for _label, needles in plain_module._PATTERNS:
            if any(n in raw for n in needles):
                backed = True
                break
        if any(k in raw for k in currency_units):
            backed = True
        assert backed, (
            f"cluster {cluster_id} is described as {gloss.get('summary')!r} but "
            f"none of its words {sorted(raw)} match any known pattern"
        )


def test_an_unrecognised_cluster_says_so_rather_than_guessing() -> None:
    """The fallback path is a real outcome and must be honest.

    A cluster of headlines whose distinguishing vocabulary cannot be summarised
    is worth knowing about. Describing it anyway would invent a finding.
    """
    result = plain_module.gloss_terms(["zzz", "qqq", "xxx"])
    assert result["summary"] is None
    assert result["descriptions"] == []
    assert "cannot summarise" in result["note"]

    # And a real cluster must gloss.
    known = plain_module.gloss_terms(["road", "accident", "killed"])
    assert known["summary"] == "traffic and deaths"


# ---------------------------------------------------------------------------
# language quality — the reason the layer exists
# ---------------------------------------------------------------------------


def test_the_inline_markup_renderer_handles_what_the_copy_uses(layer: dict) -> None:
    """The prose must render as prose, in whichever front end reads it.

    The plain layer marks a few words for emphasis with `*asterisks*`. It
    originally did not, and when it started, the front end's renderer knew only
    about backticks, so "Against how *unsettled* the market is" appeared on the
    page with the asterisks showing.

    This used to assert against the v1 SPA's JavaScript, which is gone. It now
    asserts two things that still hold: the React renderer strips the markers
    before rendering, and the prose contains nothing else the renderer would not
    handle. The first half is the bug that actually happened, so it is the half
    worth keeping a test on.
    """
    hero = (ROOT / "web" / "src" / "components" / "HeroSearch.tsx").read_text(
        encoding="utf-8"
    )
    assert "replace(/\\*\\*/g" in hero, (
        "HeroSearch renders `brief.paragraphs` without stripping the "
        "** emphasis markers the plain layer emits, so they will show as "
        "literal asterisks on the page"
    )

    text = "\n".join(
        [
            *layer["intro"]["paragraphs"],
            *[
                part
                for item in layer["summary"]["items"]
                for part in (item["claim"], item["body"], item["so_what"])
            ],
            *[t["plain"] for t in layer["glossary"]["terms"]],
        ]
    )

    # Emphasis markers must be balanced, or a stray asterisk shows on the page.
    offenders = [
        line for line in text.splitlines() if line.count("*") % 2
    ]
    assert not offenders, "unbalanced emphasis markers:\n" + "\n".join(offenders)

    # Backticks must be balanced too, for the same reason.
    unbalanced = [line for line in text.splitlines() if line.count("`") % 2]
    assert not unbalanced, "unbalanced code markers:\n" + "\n".join(unbalanced)

    # And no other markdown the renderer does not handle. Checked by looking for
    # markdown link and heading syntax specifically, because those are the ones
    # that would visibly break a sentence.
    for pattern, label in (
        (r"\[[^\]]+\]\([^)]+\)", "a markdown link"),
        (r"^\s*#{1,6}\s", "a markdown heading"),
        (r"^\s*[-*]\s", "a markdown list bullet"),
    ):
        hits = re.findall(pattern, text, flags=re.MULTILINE)
        assert not hits, f"the prose contains {label}: {hits[:3]}"


def test_no_jargon_leaks_into_the_intro_or_summary(layer: dict) -> None:
    """The whole point. A term-of-art in these blocks defeats the entire module.

    Checked against a denylist of the words that made the technical layer
    unreadable. The fix is not to remove the measurement but to phrase it
    differently, so this fails the copy rather than the code.
    """
    banned = [
        "silhouette", "tautolog", "tf-idf", "lsa", "z-score", "p-value",
        "coefficient of variation", "k-means", "apriori", "fp-growth",
        "variance", "confidence interval", "lift", "cohort", "etl", "olap",
        "star schema", "cube", "null result", "significance",
    ]
    blocks = [layer["intro"]["paragraphs"]] + [
        [i["claim"], i["body"], i["so_what"]] for i in layer["summary"]["items"]
    ]
    offenders = []
    for block in blocks:
        for sentence in block:
            low = sentence.lower()
            for word in banned:
                if word in low:
                    offenders.append(f"{word!r} in: {sentence[:90]}")
    assert not offenders, "jargon in the plain layer:\n" + "\n".join(offenders)


def test_the_glossary_explains_the_jargon(layer: dict) -> None:
    """If the plain layer may not use a term, the glossary must cover it, or the
    reader has no route to understanding it."""
    terms = " ".join(
        f"{t['term']} {t['plain']}" for t in layer["glossary"]["terms"]
    ).lower()
    for concept in (
        "silhouette", "lift", "confidence", "support", "risk-signal",
        "correlation", "p-value", "cluster", "z-score", "tf-idf", "lsa",
        "star schema", "cube", "tautological",
    ):
        assert concept in terms, f"the glossary never explains {concept!r}"


def test_glossary_entries_are_actually_plain(layer: dict) -> None:
    """One sentence each, and long enough to mean something.

    A glossary that is itself technical has defeated its own purpose.
    """
    for entry in layer["glossary"]["terms"]:
        words = entry["plain"].split()
        assert 12 <= len(words) <= 70, (
            f"{entry['term']!r} definition is {len(words)} words; a glossary "
            "entry should be one sentence"
        )
        assert not entry["plain"].endswith(",")


def test_plain_numbers_read_as_plain_numbers(layer: dict, facts: dict) -> None:
    """No digits the reader has to decode: no raw coefficients, no bare decimals
    standing in for a percentage, no thousands without separators."""
    blocks = [layer["intro"]["paragraphs"]] + [
        [i["claim"], i["body"], i["so_what"]] for i in layer["summary"]["items"]
    ]
    offenders = []
    for block in blocks:
        for sentence in block:
            low = sentence.lower()
            # "r = -0.28" or "silhouette of 0.2582" is the thing being avoided.
            for pattern, label in (
                (r"\br\s*=\s*-?0\.", "a raw correlation coefficient"),
                (r"\b0\.\d{3,}\b", "a raw decimal score"),
                (r"\d{5,}(?!\s*[,.]?\s*headlines)", "a number without separators"),
            ):
                if re.search(pattern, low):
                    offenders.append(f"{label} in: {sentence[:90]}")
    assert not offenders, "untranslated numbers:\n" + "\n".join(offenders)
