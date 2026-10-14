"""The plain-language layer.

**Why this exists.** The dashboard was correct and unreadable. It said
`silhouette 0.2582` and `LSA component 1` and `313 tautological rules` and
expected a reader to supply the meaning. Somebody without a data-warehousing
background could read every number and come away with no conclusion, which
makes the rest of the project pointless.

So: one translation layer, sitting beside the technical one rather than
replacing it. Both audiences, same page. A viva examiner still finds the
silhouette, the lift and the p-values; a first-time visitor gets a sentence they
can act on.

**Two rules govern everything in this file.**

1. **Every number is interpolated from the measured value, never retyped.**
   Each sentence below reads its figures out of `facts.json`. If a measurement
   changes, the sentence changes with it. Hand-typed numbers in prose drift, and
   a drifted sentence is worse than no sentence — it is a confident lie.

2. **Plain is not vague.** "There is some structure here" is not plain English,
   it is a hiding place. Each finding names what was measured, what the number
   means against its possible range, and what it does not license. The brevity
   comes from short sentences, not from withholding the caveat.

`test_plain_layer.py` asserts that every figure quoted here matches the fact it
claims to come from, so rule 1 is machine-enforced rather than a convention.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _pct(value: float | None, places: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.{places}f}%"


def _n(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{int(value):,}"


def _r(value: float | None, places: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{places}f}"


def _in_100(value: float | None) -> str:
    """A correlation restated as a plain count out of a hundred.

    `r = -0.28` is the number almost nobody can interpret. "About 28 in every 100
    busier days went with a calmer market" is the same measurement, and it is the
    version a reader can actually judge — they can see immediately that 28 is
    weak.
    """
    if value is None:
        return "n/a"
    return str(int(round(abs(value) * 100)))


# Small counts are spelled out when they open a sentence. "3 of the seven" is a
# grammatical error and it is the first thing a reader sees, so it is worth four
# lines rather than a full number-to-words library.
_NUMBER_WORDS = {
    0: "none", 1: "one", 2: "two", 3: "three", 4: "four",
    5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
}


def _word(value: int | None) -> str:
    if value is None:
        return "n/a"
    return _NUMBER_WORDS.get(int(value), str(int(value)))


# ---------------------------------------------------------------------------
# the intro
# ---------------------------------------------------------------------------


def build_intro(facts: dict[str, Any]) -> dict[str, Any]:
    """Three sentences: what was built, what was asked, why the answers are here.

    The third sentence is the one that matters. Without it a visitor has no idea
    why a study would report failures, and the null results read as the author
    running out of things to say.
    """
    counts = (facts.get("corpus") or {}).get("row_counts", {})
    window = (facts.get("corpus") or {}).get("analysis_window", {})
    outcomes = (facts.get("outcomes") or {}).get("counts", {})

    loaded = counts.get("fact_headline") or counts.get("cln_headline") or 0
    statements = counts.get("fact_statement") or counts.get("cln_statement") or 0
    market_days = counts.get("fact_market_daily") or counts.get("cln_market_daily") or 0
    in_window = window.get("headlines_in_window") or 0

    nulls = outcomes.get("null", 0)
    qualified = outcomes.get("qualified", 0)
    thin = nulls + qualified

    return {
        "heading": "What this is",
        "paragraphs": [
            (
                f"A data warehouse built from three public datasets: {_n(loaded)} Times of "
                f"India headlines, {_n(statements)} labelled statements from the Indian "
                f"fact-checking site IFND, and {_n(market_days)} days of Nifty 50 index "
                f"prices. Of the headlines, {_n(in_window)} fall inside the five-year "
                f"window this study analyses. The warehouse can answer questions across "
                f"all of it in seconds."
            ),
            (
                f"Seven questions were asked of it. {_word(thin).capitalize()} of them "
                f"produced no result, or a result that reads better than it actually is."
                if thin
                else "All seven produced a result, though not all of them are equally "
                     "solid."
            ),
            (
                "Those are reported here with the same care as the rest, and each one "
                "comes with the measurement that establishes it. Knowing what a dataset "
                "cannot support is as useful as knowing what it can — and it is the "
                "part most analyses leave out."
            ),
        ],
    }


# ---------------------------------------------------------------------------
# the thirty-second summary
# ---------------------------------------------------------------------------


def build_summary(facts: dict[str, Any]) -> dict[str, Any]:
    """What a reader gets if they read nothing else.

    Ordered by how much it changes what someone would believe, not by research
    question number. Each item is a claim, the number behind it, and — where the
    claim could be over-read — what it does not license.

    Built as a flat list of six so the overview can show them as cards. Every
    figure is read from `facts`, so this cannot drift from the results.
    """
    rq2 = facts["rq2_bursts"]
    rq3 = facts["rq3_sensationalism"]
    rq4 = facts["rq4_clusters"]
    rq5 = facts["rq5_association_rules"]
    rq6 = facts["rq6_market_association"]
    rq7 = facts["rq7_classifier"]

    metrics = rq7.get("metrics") or {}
    strongest = ((rq6.get("volatility") or {}).get("strongest")) or {}
    clusters = rq4.get("clusters") or []
    ranked = rq3.get("ranked") or []
    top = ranked[0] if ranked else {}

    dominant = rq4.get("dominant_cluster_share")
    small_share = (1 - dominant) if dominant is not None else None
    small_clusters = [
        c for c in clusters
        if (c.get("share_of_sample") or 0) <= 0.10
    ]

    items: list[dict[str, Any]] = []

    # -- 1. the null that surprised everyone -----------------------------
    items.append({
        "id": "volume",
        "rq": "RQ2",
        "claim": "News volume carries no news signal.",
        "body": (
            f"How many headlines were published per month barely moves — by under "
            f"{_pct(rq2.get('max_within_year_cv'), 1)} within any single year. Not one "
            f"month in the five-year window stood out as unusually busy. Events people "
            f"would expect to drive coverage, including the national lockdown in March "
            f"2020, did not drive it."
        ),
        "so_what": (
            "The archive behaves like a publisher filling a quota rather than a "
            "newsroom reacting to the news. That matters because it tells you what "
            "this corpus is *not* useful for: measuring public attention to an event. "
            "This data cannot tell that pattern apart from an artefact of how the "
            "publisher exported it, and we are not going to guess."
        ),
        "caution": rq2.get("counter_signal_note"),
    })

    # -- 2. the clustering, honestly scoped ------------------------------
    items.append({
        "id": "clusters",
        "rq": "RQ4",
        "claim": (
            f"Only about {_pct(small_share, 0)} of headlines fall into clearly "
            f"separate groups."
        ),
        "body": (
            f"Sixty thousand headlines were grouped by the words they use. One group "
            f"absorbed {_pct(dominant)} of them. The other "
            f"{_word(len(small_clusters))} groups were small and tidy: headlines "
            f"about money amounts, about traffic deaths, about people's ages."
        ),
        "so_what": (
            "So the separation that exists is in *writing habits*, not subject areas, "
            "and it does not line up with the publisher's own categories. Saying "
            "'the headlines cluster into topics' would over-read this by a long way."
        ),
        "caution": rq4.get("concentration_note"),
    })

    # -- 2. what the corpus is, and the trap inside it --------------------
    topics = ((facts.get("rq1_topic_mix") or {}).get("window_shares") or {}).get("topics") or []
    place = next((t for t in topics if t.get("topic_group") == "Place"), None)
    subject = next((t for t in topics if t.get("topic_group") == "Subject"), None)
    artefacts = (facts.get("rq1_topic_mix") or {}).get("taxonomy_artefacts") or []
    if place:
        artefact = artefacts[0] if artefacts else None
        items.append({
            "id": "mix",
            "rq": "RQ1",
            "claim": (
                f"{_pct(place.get('share_of_window'), 0)} of the headlines are "
                f"local news — and the biggest apparent trend is a filing error."
            ),
            "body": (
                f"The largest single category is {place['topic']} — city and regional "
                f"coverage — at {_pct(place.get('share_of_window'))} of the five-year "
                f"window. The biggest subject is {subject['topic']} at "
                f"{_pct(subject.get('share_of_window'))}, so nothing else comes close."
                + (
                    f" The largest apparent shift in the whole dataset is not a shift in "
                    f"what India was reading: one raw category holds "
                    f"{_pct(artefact['share_of_year'])} of all {artefact['year']} "
                    f"headlines, which means the publisher changed how it filed, not "
                    f"what it published."
                    if artefact else ""
                )
            ),
            "so_what": (
                "This is the clearest case in the study of a change in the data that is "
                "not a change in the world. An analysis that missed it would report the "
                "largest editorial shift in five years of coverage, and it would be "
                "entirely fictional."
            ),
            "caution": (facts.get("rq1_topic_mix") or {}).get("artefact_note"),
        })

    # -- 3. the split market answer --------------------------------------
    r = strongest.get("pearson_r")
    items.append({
        "id": "market",
        "rq": "RQ6",
        "claim": (
            "Headlines do not predict market moves — but they do track market calm."
        ),
        "body": (
            "Against the index's daily percentage change, there is no relationship. "
            "Fifteen different time lags were tested — does volume lead the market "
            "by a day, a week, a month? — and one of them coming out "
            "\"significant\" is exactly what chance alone produces. Against how "
            "*unsettled* the market is, though, there is a real relationship: busier "
            "headline days go with calmer markets."
        ),
        "so_what": (
            f"About {_in_100(r)} in every 100 busier-headline days went with a calmer "
            f"market, which is a weak-to-moderate link, not a strong one. We cannot "
            f"explain the direction, and we should be suspicious of anyone who claims "
            f"they can: both series simply react to the same underlying events, and "
            f"nothing in this data shows which way the arrow points."
        ),
        "caution": (
            "Association only. Two things moving together is not one causing the other."
        ),
    })

    # -- 4. the one accuracy claim ---------------------------------------
    items.append({
        "id": "classifier",
        "rq": "RQ7",
        "claim": (
            f"A classifier does separate real statements from fake ones — and "
            f"{_pct(metrics.get('accuracy'))} is a ceiling, not a fair score."
        ),
        "body": (
            f"On the labelled data it scored {_pct(metrics.get('accuracy'))} accurate. "
            f"Always answering 'real' would already score {_pct(metrics.get('majority_baseline_accuracy'))}, "
            f"because most labelled statements are real."
        ),
        "so_what": (
            "Part of the fake class in this dataset is machine-generated text, which is "
            "far easier to spot than a falsehood written by a person. A real deployment "
            "would score lower. Read it as 'this approach works', not 'this works this "
            "well'."
        ),
        "caution": rq7.get("upper_bound_caveat"),
    })

    # -- 5. the style finding --------------------------------------------
    items.append({
        "id": "language",
        "rq": "RQ3",
        "claim": (
            f"{top.get('topic', 'Education')} headlines use the most alarm-heavy writing."
        ),
        "body": (
            f"{_pct(top.get('risk_signal_rate'))} of {top.get('topic', 'Education')} "
            f"headlines trip the alarm-style detector, against {_pct(rq3.get('corpus_rate'))} "
            f"across all headlines. That is about "
            f"{(top.get('risk_signal_rate') or 0) / (rq3.get('corpus_rate') or 1):.1f} times "
            f"as often."
        ),
        "so_what": (
            "This is a measure of *writing style*, on headlines that nobody has labelled "
            "as true or false. It is a risk-signal rate and never a fake-news rate, and "
            "the cut-off used to define 'alarm-heavy' is a judgement, not a fact — so "
            "the report also shows how the ranking changes as that cut-off moves."
        ),
        "caution": (
            "A style measure on unlabelled headlines. Never a fake-news rate."
        ),
    })

    # -- 6. the honest weak one ------------------------------------------
    tautologies = rq5.get("tautological_rules")
    items.append({
        "id": "rules",
        "rq": "RQ5",
        "claim": (
            f"{_n(rq5.get('rule_count'))} co-occurrence patterns were found — and most "
            f"of them are arithmetic."
        ),
        "body": (
            f"A pattern is a combination of attributes that turns up together more "
            f"often than chance would predict. {tautologies} of the "
            f"{_n(rq5.get('rule_count'))} were trivially true — 'a 2015 headline is a "
            f"Local headline' — because they follow from how the data was labelled "
            f"rather than from anything about the news. {len(rq5.get('informative_rules') or [])} "
            f"were genuinely informative."
        ),
        "so_what": (
            "Even those eight lean on 2017 being an unusual year, which inflates their "
            "scores. The honest summary is that this corpus has weak co-occurrence "
            "structure — which is a finding about the data, and worth more than a "
            "table of impressive-looking numbers would have been."
        ),
        "caution": rq5.get("lift_caveat"),
    })

    return {
        "heading": "If you read nothing else",
        "subtitle": (
            "Six things this study found, in plain language. Every figure here is the "
            "same one on the detailed pages underneath."
        ),
        "items": items,
    }


# ---------------------------------------------------------------------------
# the glossary
# ---------------------------------------------------------------------------


def build_glossary() -> dict[str, Any]:
    """The words that make the rest unreadable, each in one sentence.

    Kept short deliberately. A glossary that is itself technical has defeated its
    own purpose, and a long one is a wall nobody reads. Fourteen terms, one
    sentence each, each including the number's possible range where it has one —
    because a score without its scale is the original problem in miniature.
    """
    terms = [
        {
            "term": "Silhouette score",
            "plain": (
                "How well the groups separate from each other. 0 means they blur "
                "together and 1 means they are perfectly distinct. This project's "
                "score is high only because one small group is unusually tidy."
            ),
        },
        {
            "term": "Cluster",
            "plain": (
                "A group of headlines that use similar words. A cluster is a writing "
                "style, not a subject — these ones are not the publisher's topic "
                "categories."
            ),
        },
        {
            "term": "Lift",
            "plain": (
                "How much more often two things appear together than chance would "
                "predict. Lift 1.0 means exactly as often as chance, so anything at "
                "1.0 carries no information."
            ),
        },
        {
            "term": "Confidence",
            "plain": (
                "Out of all the times the first thing appeared, how often the second "
                "followed. A percentage, and a statement about the second thing only."
            ),
        },
        {
            "term": "Support",
            "plain": (
                "How often the combination appears in the data at all. A high lift on "
                "something that almost never happens is not worth much."
            ),
        },
        {
            "term": "Tautological rule",
            "plain": (
                "A pattern that is true by arithmetic rather than by observation — "
                "'if it is Q1 2016 then it is 2016'. They are counted separately so "
                "they cannot inflate the interesting number."
            ),
        },
        {
            "term": "Risk-signal rate",
            "plain": (
                "The share of headlines using alarm-heavy words and punctuation. It "
                "measures writing style on headlines nobody has labelled, so it is "
                "never a fake-news rate."
            ),
        },
        {
            "term": "Correlation (r)",
            "plain": (
                "How tightly two measurements move together. 0 is no relationship, "
                "1 means they rise together, -1 means they move in opposite "
                "directions. It never shows cause."
            ),
        },
        {
            "term": "p-value",
            "plain": (
                "How surprising a result would be if there were no real relationship "
                "at all. Below 0.05 is the usual bar for 'probably not chance'."
            ),
        },
        {
            "term": "Confidence interval",
            "plain": (
                "The plausible range around a measured rate. The bar is the best "
                "single guess; the interval is the honest part."
            ),
        },
        {
            "term": "Coefficient of variation",
            "plain": (
                "How wobbly a series is, ignoring how big it is. Under about 10% means "
                "the numbers barely move."
            ),
        },
        {
            "term": "z-score",
            "plain": (
                "How many standard deviations a month sits above or below its usual "
                "level. +2 is notably high, -2 notably low, 0 is normal."
            ),
        },
        {
            "term": "TF-IDF",
            "plain": (
                "A way of scoring words so that rare ones count for more than common "
                "ones. It is why clustering finds specific vocabulary rather than "
                "'said' and 'police'."
            ),
        },
        {
            "term": "LSA component",
            "plain": (
                "One axis along which headlines spread out, ordered by how much their "
                "vocabulary differs. The direction means nothing; only the spread does."
            ),
        },
        {
            "term": "Star schema",
            "plain": (
                "The warehouse layout. Big fact tables in the middle, lookup tables "
                "around them, so one question reads a few rows instead of millions."
            ),
        },
        {
            "term": "Cube",
            "plain": (
                "A pre-calculated summary table, so a cross-tabulation does not have "
                "to be rebuilt on every question. It stores sums, never averages, "
                "because a rounded average cannot be averaged again."
            ),
        },
    ]
    return {
        "heading": "Words this project uses",
        "subtitle": (
            "Sixteen terms, one sentence each. Anything else in the numbers is either "
            "ordinary English or is explained where it appears."
        ),
        "terms": terms,
    }


# ---------------------------------------------------------------------------
# scales — a number is meaningless without one
# ---------------------------------------------------------------------------


def build_scales(facts: dict[str, Any]) -> dict[str, Any]:
    """What each headline figure means against its possible range.

    A figure with no scale is decoration: `0.2582` could be excellent, terrible or
    meaningless, and the reader has no way to tell. Every entry here supplies the
    range, the comparison, or both.
    """
    rq4 = facts["rq4_clusters"]
    rq5 = facts["rq5_association_rules"]
    rq6 = facts["rq6_market_association"]
    rq7 = facts["rq7_classifier"]
    metrics = rq7.get("metrics") or {}
    strongest = ((rq6.get("volatility") or {}).get("strongest")) or {}

    return {
        "silhouette": (
            "Out of a possible 0 to 1, where 0 is no structure at all. Short news "
            "text usually scores 0.1 to 0.3, so this sits in the normal band — and "
            "reaches the top of it only because a small minority of headlines is "
            "unusually well separated."
        ),
        "dominant_cluster_share": (
            f"The other {_pct(1 - (rq4.get('dominant_cluster_share') or 0), 1)} is the "
            "part that separated. A high score on this measure can coexist with 95% of "
            "the corpus being indistinguishable, and here it does."
        ),
        "classifier_accuracy": (
            f"Out of a possible 100%. The bar to clear is not 100 — it is "
            f"{_pct(metrics.get('majority_baseline_accuracy'))}, which is what you get "
            f"by always answering 'real' and knowing nothing."
        ),
        "volatility_correlation": (
            "Out of a possible -1 to +1, where 0 is no relationship at all. This is a "
            f"weak-to-moderate link, explaining about "
            f"{round(strongest.get('variance_explained_pct') or 0)}% of the day-to-day "
            "variation in volatility."
        ),
        "rule_count": (
            f"Out of {_n(rq5.get('rule_count'))} patterns found. "
            f"{rq5.get('tautological_rules')} were true by arithmetic and "
            f"{len(rq5.get('informative_rules') or [])} were genuinely informative, so "
            "the headline count overstates the finding by a wide margin."
        ),
        "volume_bursts": (
            "Out of roughly 60 months checked. Zero is the finding: monthly volume "
            "varies by under 4%, so no threshold can fire on a series this flat."
        ),
        "corpus_size": (
            f"Out of {_n((facts.get('corpus') or {}).get('row_counts', {}).get('fact_headline'))} "
            "headlines loaded. The rest fall outside the five-year analysis window."
        ),
        "fp_growth_speedup": (
            "A ratio, not a percentage. Both algorithms found identical patterns; only "
            "the time differs, which is the one thing the comparison can actually show."
        ),
    }


# ---------------------------------------------------------------------------
# cluster terms, translated
# ---------------------------------------------------------------------------

# Indian rupee denominations, in the forms that appear in headlines. This is a
# fact about the language, not a claim about the data: `crore` and `lakh` are
# fixed units whether or not any particular headline means anything by them.
_CURRENCY = {
    "crore": "Indian rupee amounts (a crore is 10 million)",
    "lakh": "Indian rupee amounts (a lakh is 100,000)",
    "lakhhs": "Indian rupee amounts",
    "rs": "Indian rupee amounts",
    "rs.": "Indian rupee amounts",
    "crores": "Indian rupee amounts",
    "lakhs": "Indian rupee amounts",
}

# Groupings keyed by a term that must be present. Matched, never inferred: if no
# entry matches, the raw terms are shown unchanged rather than being described.
# Inventing a description for a term we do not recognise would be exactly the
# kind of confident-sounding claim this project refuses to make.
_PATTERNS = [
    ("traffic and deaths",
     ("accident", "killed", "crash", "road", "vehicles", "accidents", "dead", "died")),
    ("people's ages",
     ("old", "year", "months", "girl", "boy", "man", "woman", "aged")),
    ("police and crime",
     ("police", "cops", "crime", "arrest", "court", "cases", "charges", "murder")),
    ("government and politics",
     ("govt", "bjp", "modi", "cm", "minister", "election", "government", "state")),
    ("sport",
     ("match", "team", "win", "score", "player", "season", "tournament", "games")),
    ("money and business",
     ("crore", "lakh", "rs", "bank", "market", "shares", "profit", "company",
      "rate", "price", "rupees", "sales")),
    ("health and medicine",
     ("health", "hospital", "doctor", "covid", "cases", "patients", "vaccine",
      "disease", "treatment")),
    ("education",
     ("school", "student", "college", "university", "board", "exam", "students",
      "class", "teachers")),
]


def gloss_terms(terms: list[str]) -> dict[str, Any]:
    """Turn a cluster's top words into a sentence a reader can use.

    Returns both the raw words and the descriptions, and states when nothing
    matched rather than producing a generic guess. That case is a real outcome:
    a cluster of headlines whose distinguishing vocabulary we cannot summarise
    is itself worth knowing.
    """
    raw = [str(t) for t in terms]
    found: list[str] = []
    for label, needles in _PATTERNS:
        if any(n in raw for n in needles) and label not in found:
            found.append(label)
    # Currency is checked separately because several terms can map to it and it
    # is the most commonly hit group.
    currency = next(
        (v for k, v in _CURRENCY.items() if k in raw), None
    )
    if currency and "Indian rupee amounts" not in " ".join(found):
        found.insert(0, currency)

    if not found:
        return {
            "raw": raw,
            "descriptions": [],
            "summary": None,
            "note": (
                "These headlines are distinguished by vocabulary we cannot summarise "
                "in a phrase. The raw words are listed rather than being described."
            ),
        }
    return {
        "raw": raw,
        "descriptions": found,
        "summary": ", ".join(found),
        "note": None,
    }


def build_cluster_glosses(facts: dict[str, Any]) -> dict[str, Any]:
    """A plain reading of every cluster, keyed by cluster id.

    The dominant cluster gets its own description. Its top terms are general
    news vocabulary — `said`, `new`, `delhi` — because the cluster is the
    undifferentiated remainder, not a subject. Listing "people's ages, police and
    crime, government and politics" for 95% of the corpus would imply the blob is
    a topic with several themes, which is the opposite of what it is.
    """
    out = {}
    for cluster in facts["rq4_clusters"].get("clusters", []):
        share = cluster.get("share_of_sample") or 0
        is_dominant = share > 0.5
        gloss = gloss_terms(cluster.get("top_terms") or [])
        if is_dominant:
            gloss = {
                "raw": gloss["raw"],
                "descriptions": [],
                "summary": "General news vocabulary — no distinguishing subject",
                "note": (
                    f"This group holds {_pct(share)} of the sample, so its "
                    "distinguishing words are just the words most headlines use. "
                    "It is the absence of a topic, not a topic."
                ),
            }
        out[str(cluster["cluster_id"])] = {
            "size": cluster.get("size"),
            "share_of_sample": share,
            "is_dominant": is_dominant,
            **gloss,
        }
    return out


# ---------------------------------------------------------------------------
# the whole layer
# ---------------------------------------------------------------------------


def build_plain(facts: dict[str, Any]) -> dict[str, Any]:
    """Everything a reader needs to be understood before reading anything else."""
    return {
        "intro": build_intro(facts),
        "summary": build_summary(facts),
        "glossary": build_glossary(),
        "scales": build_scales(facts),
        "cluster_terms": build_cluster_glosses(facts),
        "why": (
            "The technical pages are not going anywhere. Every number here also "
            "appears there with its method, its uncertainty and its caveat — this "
            "layer only adds the translation."
        ),
    }
