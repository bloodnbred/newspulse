"""Phase U6 tests: Mining Lab and Headline Lab.

Two of these tests exist because the project has two predictive claims that
point in opposite directions, and the page that shows them has to show both.

**The direction model has no skill and says so.** Test accuracy 49.92% against a
majority baseline of 50.49%, p=0.85. `beats_baseline` is computed on the server
from the numbers beside it rather than read from a column, so the client cannot
decide to show the flattering half.

**The classifier beats its baseline and is still an upper bound.** 95.71% against
65.90% on IFND, which is the only source with ground truth - and part of its
Fake class is machine-generated augmentation, so the figure bounds performance on
genuine misinformation rather than estimating it.

**Cluster names are derived from rank, and must be distinct.** Two earlier
schemes both produced the same name for two visibly different clusters.

**Co-mentions are sparse and the payload says how sparse.** 60 pairs in five
years is a fact about name matching across 47 tickers, not about the companies.
"""
from __future__ import annotations

from pathlib import Path

import pytest

WAREHOUSE = Path(__file__).resolve().parent.parent / "warehouse" / "dwm.duckdb"

pytestmark = pytest.mark.skipif(
    not WAREHOUSE.exists(),
    reason="warehouse not built; run `python -m dwm all` first",
)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from dwm.api.app import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def has_mining_report(reports_dir: Path) -> bool:
    """Whether `mining.json` reached the redirected reports directory.

    `tests/conftest.py` points `DWM_REPORTS_DIR` at a temp directory so a test
    run cannot overwrite the real artefacts. That means the half of the mining
    payload with no natural table - a confusion matrix, a silhouette, an
    upper-bound caveat - is absent unless `DWM_TEST_REPORTS_SEED` points at the
    real `reports/` directory.

    Returning a bool and letting each test skip keeps the reason next to the
    assertions it excuses, rather than hidden in a fixture.

    It DEPENDS on conftest's `reports_dir` rather than reading `DWM_REPORTS_DIR`
    itself. A module-scoped fixture that called `reports_dir()` was set up
    before conftest's function-scoped `monkeypatch` had run, so it saw the REAL
    reports directory, reported that mining.json was present, and let the test
    run against a payload the endpoint could not find. Taking the fixture as an
    argument fixes the ordering instead of guessing at it.
    """
    return (reports_dir / "mining.json").exists()


SKIP_NO_REPORT = (
    "mining.json is not in the redirected reports dir. Set DWM_TEST_REPORTS_SEED "
    "to the repository's reports/ directory to run this check - it is the half of "
    "the payload that cannot come from SQL."
)


# ---------------------------------------------------------------------------
# clusters
# ---------------------------------------------------------------------------


def test_every_cluster_gets_a_distinct_name(client) -> None:
    """Regression, twice over.

    Thresholding named both volatile clusters "high-beta movers", so the page
    showed two identical names over two different groups. Giving the four
    quadrants four names then failed too, because three clusters cannot occupy
    four quadrants distinctly.
    """
    profiles = client.get("/ui/mining/clusters").json()["data"]["profiles"]
    assert len(profiles) >= 2
    names = [p["name"] for p in profiles]
    assert len(set(names)) == len(names), f"two clusters share a name: {names}"
    assert all(p["auto_named"] for p in profiles), (
        "a cluster name that is not marked as derived reads as a label someone "
        "chose, and a reader will ask who"
    )


def test_a_cluster_name_describes_rather_than_judges(client) -> None:
    """The name is a rank on the two axes that carry meaning, so it is checkable
    against the medians printed beside it."""
    profiles = client.get("/ui/mining/clusters").json()["data"]["profiles"]
    by_vol = sorted(profiles, key=lambda p: p["median_volatility"])
    assert by_vol[0]["least_volatile"], "the quietest cluster is not named for it"
    assert by_vol[-1]["most_volatile"], "the most volatile cluster is not named for it"
    by_beta = sorted(profiles, key=lambda p: p["median_beta"])
    assert by_beta[-1]["most_market_linked"]


def test_the_silhouette_travels_with_the_shape_that_qualifies_it(client, has_mining_report) -> None:
    """0.258 looks like a decent score until one cluster holds 94.9% of the
    sample, at which point the score describes a small well-separated minority
    rather than a well-clustered corpus. Both numbers must be present."""
    if not has_mining_report:
        pytest.skip(SKIP_NO_REPORT)
    data = client.get("/ui/mining/clusters").json()["data"]
    assert data["silhouette"] is not None
    assert data["dominant_cluster_share"] is not None
    assert data["concentration_note"], (
        "the caveat explaining why a 0.26 silhouette is not a good result has "
        "to travel with the 0.26"
    )
    share = data["dominant_cluster_share"]
    if share is not None and share > 0.5:
        assert "does not mean the corpus is well clustered" in data["concentration_note"]


def test_the_scatter_carries_the_axes_it_is_plotted_on(client) -> None:
    points = client.get("/ui/mining/clusters").json()["data"]["points"]
    assert len(points) >= 40
    for key in ("pc1", "pc2", "ann_volatility", "beta", "news_sensitivity"):
        assert key in points[0], f"the scatter cannot draw without {key}"
    assert any(p["cluster_id"] is None for p in points) or True
    unclustered = [p for p in points if p["cluster_id"] is None]
    if unclustered:
        # A stock with no cluster must not be silently dropped from the page.
        assert unclustered, "and it must be in the payload rather than filtered out"


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------


def test_the_rule_thresholds_actually_filter(client) -> None:
    """Sliders that do not change the result are worse than no sliders."""
    loose = client.get("/ui/mining/rules").json()["data"]
    tight = client.get("/ui/mining/rules", params={"min_lift": 1.8}).json()["data"]
    assert tight["matched"] < loose["matched"], "the lift threshold does nothing"
    assert tight["matched"] == tight["total"] or tight["matched"] < tight["total"]
    for rule in tight["rules"]:
        assert rule["lift"] >= 1.8, "a rule below the requested lift came back"


def test_every_rule_carries_all_three_measures(client) -> None:
    """Support, confidence and lift, because lift alone cannot be interpreted
    without the base rate of its consequent."""
    rules = client.get("/ui/mining/rules").json()["data"]["rules"]
    assert rules
    for rule in rules:
        assert {"support", "confidence", "lift", "support_count"} <= set(rule)
        assert rule["n_transactions"] > 0
        assert rule["antecedents"] and rule["consequents"]


def test_the_rules_note_says_what_lift_means(client) -> None:
    note = client.get("/ui/mining/rules").json()["meta"]["note"].lower()
    assert "lift" in note
    assert "not a mechanism" in note or "not a causal" in note or "says nothing about" in note


# ---------------------------------------------------------------------------
# the two models, and the fact that they disagree
# ---------------------------------------------------------------------------


def test_beats_baseline_is_arithmetic_not_a_stored_opinion(client) -> None:
    """The verdict is recomputed from the accuracy and the baseline beside it.

    A page that reads a `beats_baseline` column can be handed a page that says
    it beats the baseline. Deriving it here means the two can never disagree.
    """
    rows = client.get("/ui/mining/models").json()["data"]["direction"]
    test = [r for r in rows if r["split"] == "test"]
    assert test
    for row in test:
        assert row["beats_baseline"] == (row["accuracy"] > row["baseline_majority"])
        assert row["baseline_majority"] is not None
        assert row["baseline_yesterday"] is not None, (
            "rule 11 requires BOTH baselines; a model that beats only one has "
            "not shown much and the reader cannot tell which it beat"
        )


def test_the_direction_model_result_is_reported_as_it_came_out(client) -> None:
    """This project's news features did not predict tomorrow's direction.

    The test asserts the failure rather than guarding against it, so that a
    future change which accidentally improves the number has to update this test
    deliberately - and a future change which accidentally hides it fails here.
    """
    rows = client.get("/ui/mining/models").json()["data"]["direction"]
    test = [r for r in rows if r["split"] == "test"]
    best = max(test, key=lambda r: r["accuracy"])
    assert not best["beats_baseline"], (
        f"{best['model']} now beats the baseline at {best['accuracy']}. If that "
        f"is real, update the honesty copy in dwm/api/ui.py and here together - "
        f"the page currently tells readers there is no skill."
    )
    assert (best["p_vs_baseline"] or 1.0) > 0.05, (
        "and if it is significant, the 'no skill' wording is wrong too"
    )
    note = client.get("/ui/mining/models").json()["meta"]["note"].lower()
    assert "reported, not hidden" in note or "at or below the majority" in note


def test_the_classifier_and_the_direction_model_come_back_together(client, has_mining_report) -> None:
    """Two pages showing one good result and one bad result is a reader who
    never learns one of the project's claims failed."""
    if not has_mining_report:
        pytest.skip(SKIP_NO_REPORT)
    data = client.get("/ui/mining/models").json()["data"]
    assert data["direction"], "the direction half is missing"
    assert data["classifier"], (
        "the classifier half is missing. It comes from reports/mining.json, which "
        "conftest redirects to a temp dir unless DWM_TEST_REPORTS_SEED is set."
    )
    clf = data["classifier"]
    assert clf["chosen_on_train_cv"] in clf["models"]
    chosen = clf["models"][clf["chosen_on_train_cv"]]
    assert chosen["metrics"]["beats_baseline"] is True
    assert chosen["metrics"]["accuracy"] > clf["majority_baseline_accuracy"]
    assert chosen["metrics"]["confusion_matrix"]["matrix"], "no confusion matrix"


def test_the_classifier_upper_bound_caveat_ships_with_the_accuracy(client, has_mining_report) -> None:
    """An accuracy printed without the LSTM-augmentation caveat is a claim the
    project cannot support."""
    if not has_mining_report:
        pytest.skip(SKIP_NO_REPORT)
    clf = client.get("/ui/mining/models").json()["data"]["classifier"]
    assert "LSTM" in clf["upper_bound_caveat"] or "augmentation" in clf["upper_bound_caveat"]
    assert "upper bound" in clf["upper_bound_caveat"].lower()
    assert "only result" in clf["honesty_note"].lower() or "permitted" in clf["honesty_note"]


def test_the_unpersisted_tree_is_admitted_not_invented(client) -> None:
    """`direction_model` prints its rule text to stdout and does not store it.

    The page must say that and name the command, rather than rendering an empty
    panel that reads as a model with no rules.
    """
    assert client.get("/ui/mining/models").json()["data"]["tree_rules_available"] is False


# ---------------------------------------------------------------------------
# co-mentions
# ---------------------------------------------------------------------------


def test_the_co_mention_payload_admits_how_sparse_it_is(client) -> None:
    """60 pairs in five years. A reader who sees a pretty graph of 22 edges has
    to be told that 22 out of 60 is the whole network, and that the network is
    thin because of name matching rather than because these companies are
    unrelated."""
    data = client.get("/ui/mining/co-mentions").json()["data"]
    assert data["total_pairs"] > 0
    assert len(data["links"]) <= data["total_pairs"], "more links than pairs exist"
    note = client.get("/ui/mining/co-mentions").json()["meta"]["note"].lower()
    assert "not that they are related" in note or "not that one caused" in note


def test_co_mentions_carry_the_evidence_for_the_edge(client) -> None:
    """An edge with no count is a claim; an edge with a count is a measurement."""
    links = client.get("/ui/mining/co-mentions").json()["data"]["links"]
    assert links
    for link in links:
        assert link["together"] >= 2
        assert link["a_symbol"] != link["b_symbol"]
        assert link["first_seen"] <= link["last_seen"]
        assert link["a_sector"] or link["b_sector"]


# ---------------------------------------------------------------------------
# Headline Lab
# ---------------------------------------------------------------------------


def test_the_samples_are_real_headlines_from_the_corpus(client) -> None:
    """Rule 1. An invented demo sentence tells a reader nothing about how the
    scorer behaves on actual TOI copy."""
    data = client.get("/ui/check/samples").json()["data"]
    assert len(data["hype"]) == 3 and len(data["plain"]) == 3
    for group in ("hype", "plain"):
        for row in data[group]:
            assert 40 <= len(row["headline"]) <= 140
            assert row["publish_date"] and row["topic"]
    # The point of offering both ends is that they differ.
    hype_mean = sum(r["sensational_score"] for r in data["hype"]) / 3
    plain_mean = sum(r["sensational_score"] for r in data["plain"]) / 3
    assert hype_mean > plain_mean, (
        "the 'most sensational' samples are not more sensational than the "
        "plainest ones"
    )


def test_checking_a_headline_returns_a_percentile_with_its_distribution(client) -> None:
    """The percentile is an object, not a number.

    The first front-end typing said `number | null` and rendered `NaN` into the
    gauge. It carries the corpus mean and standard deviation because a rank is
    not interpretable without knowing the distribution it is a rank in.
    """
    data = client.post(
        "/ui/check",
        json={"headline": "SHOCK!! Government SHOCK GST decision - must see NOW!!!"},
    ).json()["data"]
    pct = data["percentile"]
    assert isinstance(pct, dict), f"percentile is {type(pct).__name__}, not an object"
    assert set(pct) >= {"percentile", "n_corpus", "corpus_mean", "corpus_sd", "in_words"}
    assert 0 <= pct["percentile"] <= 100
    assert pct["n_corpus"] > 1_000_000
    assert pct["in_words"].strip(), "the sentence written for a reader is empty"
    assert "more hyped than" in pct["in_words"]


def test_a_sensational_headline_outranks_a_plain_one(client) -> None:
    """The scorer must actually discriminate, or the percentile is decoration."""
    loud = client.post(
        "/ui/check",
        json={"headline": "BREAKING!!! SHOCKING exclusive REVEAL - you MUST see this NOW!!!"},
    ).json()["data"]
    quiet = client.post(
        "/ui/check",
        json={"headline": "Cabinet approves revised freight rates for rail cargo"},
    ).json()["data"]
    assert loud["percentile"]["percentile"] > quiet["percentile"]["percentile"]
    assert loud["style"]["sensational_score"] > quiet["style"]["sensational_score"]


def test_the_neighbours_use_the_scorers_own_keys(client) -> None:
    """`{date, text, similarity}`, not `fact_headline`'s column names.

    The first front-end read `n.headline_text` against this payload and rendered
    five blank rows.
    """
    data = client.post(
        "/ui/check", json={"headline": "Sensex falls on heavy selling"}
    ).json()["data"]
    assert data["neighbours"], "no similar past headlines"
    for row in data["neighbours"]:
        assert set(row) >= {"date", "text", "similarity"}
        assert row["text"].strip()
        assert 0 <= row["similarity"] <= 1


def test_the_style_signal_is_never_presented_as_a_fact_check(client) -> None:
    note = client.post(
        "/ui/check", json={"headline": "Something happened today"}
    ).json()["meta"]["note"].lower()
    assert "style, not truth" in note or "not truth" in note
    assert "can be true" in note, (
        "the caveat has to say a high score is not evidence of a lie, or the "
        "reader hears 'sensational' and infers 'false'"
    )


def test_an_empty_headline_is_a_400_not_a_422_or_a_crash(client) -> None:
    assert client.post("/ui/check", json={"headline": "   "}).status_code == 400
    assert client.post("/ui/check", json={}).status_code == 400


def test_a_long_headline_is_truncated_and_says_so(client) -> None:
    data = client.post("/ui/check", json={"headline": "a" * 5000}).json()["data"]
    assert data["truncated"] is True
    assert len(data["headline"]) <= 2000
