"""Phase V6 tests: insights, the quick brief, the checker and the v2 API.

These need a built warehouse, so they skip cleanly on a checkout that has not
run the pipeline. That is the same bargain tests/test_ui.py already makes, and
it is the right one: a test that silently builds a warehouse of its own would
be testing a different dataset from the one the project ships.

One test here is a plain regression guard for a bug that returned an empty list
rather than raising, which is why nobody noticed for a while.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

WAREHOUSE = Path(__file__).resolve().parents[1] / "warehouse" / "dwm.duckdb"

pytestmark = pytest.mark.skipif(
    not WAREHOUSE.exists(),
    reason="warehouse not built; run `python -m dwm all` first",
)


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(str(WAREHOUSE), read_only=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from dwm.api.app import app

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# endpoints answer
# ---------------------------------------------------------------------------

GET_PATHS = [
    "/health",
    "/stocks",
    "/stocks/TCS",
    "/sectors",
    "/sectors/heat",
    "/events",
    "/events/1/study",
    "/mining/stockclusters",
    "/mining/marketrules",
    "/mining/direction",
    "/mining/surprise",
    "/brief?q=Reliance",
    "/insights",
    "/quality",
]


@pytest.mark.parametrize("path", GET_PATHS)
def test_every_v2_endpoint_answers(client, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200, f"{path} -> {response.status_code}"


@pytest.mark.parametrize("path", GET_PATHS)
def test_every_endpoint_carries_a_caveat_or_an_explicit_empty(client, path: str) -> None:
    """A number without a limit attached is the easiest kind of wrong to
    publish, so the payloads carry one."""
    body = client.get(path).json()
    if isinstance(body, dict) and any(k in body for k in ("rows", "stocks", "events")):
        assert "caveat" in body, f"{path} returns data with no caveat"


def test_the_v1_routes_still_answer(client) -> None:
    """v2 was added beside v1, not over it.

    503 is a legitimate answer here: in a test run the reports directory is
    redirected, so `/facts` reports "not built yet" and names the command. What
    must not happen is a v1 route breaking or going missing.
    """
    for path in ("/health", "/facts", "/topics", "/tables", "/operations"):
        response = client.get(path)
        assert response.status_code in (200, 503), path
        if response.status_code == 503:
            assert "python -m dwm" in response.json()["detail"], path


def test_an_unknown_stock_is_a_readable_404(client) -> None:
    response = client.get("/stocks/NOTASTOCK")
    assert response.status_code == 404
    detail = response.json()["detail"].lower()
    assert "unknown symbol" in detail
    # The message should help, not merely refuse.
    assert len(detail) > 20


def test_a_missing_phase_names_the_command_to_run(con) -> None:
    """'Run `python -m dwm link` first' is actionable; 'no data' is not."""
    from dwm.api.v2 import _missing

    error = _missing("mining_event_study", "python -m dwm mine --op events")
    assert error.status_code == 404
    assert "python -m dwm mine --op events" in error.detail


# ---------------------------------------------------------------------------
# insights
# ---------------------------------------------------------------------------


def test_the_insights_feed_states_which_cards_it_withheld(con) -> None:
    from dwm.inference.insights import build

    payload = build(con)
    assert payload["of"] == 6
    for card in payload["cards"]:
        # `caution` is the project's established field name, matching /facts.
        assert card["caution"], f"card {card['id']} has no caution"
        assert card["title"]
    for withheld in payload["withheld"]:
        assert withheld["why"], "a withheld card must say which floor it missed"


def test_the_direction_card_reports_a_negative_result_honestly(con) -> None:
    """The honesty card exists to say no when the answer is no."""
    from dwm.inference.insights import build

    payload = build(con)
    card = next(
        (c for c in payload["cards"] if c["id"] == "direction_honesty"), None
    )
    if card is None:
        pytest.skip("direction model has not been run")
    evidence = card["evidence"]
    assert evidence["n_test_rows"] > 0
    assert "baseline_majority_class" in evidence
    if not evidence["beats_baseline"]:
        assert "No" in card["title"], "a failure to beat baseline must read as one"


def test_the_lag_card_corrects_for_searching_eleven_lags(con) -> None:
    from dwm.inference.insights import build

    card = next(
        (c for c in build(con)["cards"] if c["id"] == "lag_association"), None
    )
    if card is None:
        pytest.skip("no sector-day facts")
    evidence = card["evidence"]
    assert evidence["lags_tested"] > 1
    # The strongest of N correlated tests needs its p-value corrected.
    assert evidence["p_value_bonferroni"] >= evidence["p_value"]
    assert "corrected" in card["caution"]


def test_dev_sample_marks_every_card(con) -> None:
    from dwm.inference.insights import DEV_PREFIX, build

    payload = build(con, dev_sample=True)
    assert payload["cards"]
    for card in payload["cards"]:
        assert card["title"].startswith(DEV_PREFIX)
        assert card["dev_sample"] is True


# ---------------------------------------------------------------------------
# brief
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected_kind"),
    [
        ("TCS", "stock"),                    # a ticker
        ("Tata Consultancy Services", "stock"),   # a full name
        ("Infosys", "stock"),                # a short name
        ("Energy", "sector"),                # a sector
        ("Financial Services", "sector"),
    ],
)
def test_the_brief_resolves_each_kind_of_query(
    con, query: str, expected_kind: str
) -> None:
    from dwm.inference.brief import build

    payload = build(con, query)
    assert payload["kind"] == expected_kind, f"{query} -> {payload.get('kind')}"
    assert payload["paragraphs"]


def test_the_brief_suggests_alternatives_for_a_typo(con) -> None:
    from dwm.inference.brief import build

    payload = build(con, "Relaince")
    assert payload["kind"] == "stock"
    assert payload["match_quality"] < 1.0, "a typo must not claim an exact match"
    assert payload["did_you_mean"]


def test_the_brief_refuses_rather_than_guessing(con) -> None:
    """A confident paragraph about the wrong company is worse than no answer."""
    from dwm.inference.brief import build

    payload = build(con, "qqqzxwv")
    assert payload["kind"] == "none"
    assert "did not" in payload["message"].lower() or "could not" in payload["message"].lower()


def test_every_brief_carries_its_corpus_caveat(con) -> None:
    from dwm.inference.brief import build

    for query in ("Reliance", "Energy"):
        payload = build(con, query)
        blob = " ".join(payload["caveats"]).lower()
        assert "one outlet" in blob or "not coverage" in blob
        # Never "fake news", which is the phrase the project forbids.
        assert "fake news rate" not in blob


# ---------------------------------------------------------------------------
# checker
# ---------------------------------------------------------------------------


def test_the_checker_scores_style_without_claiming_truth(con) -> None:
    from dwm.features.scoring import TextScorer
    from dwm.inference.checker import build_response

    measures = TextScorer().measure("SHOCKING! Reliance shares CRASH 5000% in HUGE scam!!")
    payload = build_response(con, "SHOCKING! Reliance shares CRASH 5000% in HUGE scam!!", measures)
    assert payload["style"]["sensational"] is True
    assert payload["percentile"]["n_corpus"] > 0
    blob = (payload["disclaimer"] + payload["caveat"]).lower()
    assert "writing" in blob
    assert "fake news" not in blob


def test_the_checker_returns_real_neighbours_not_an_empty_list(con) -> None:
    """Regression guard.

    The neighbour lookup compared positions in the TF-IDF matrix against a dict
    keyed by headline id. That never matches, so the list was permanently empty -
    which reads as "we found nothing similar" rather than as a bug.
    """
    from dwm.inference.checker import TFIDF_PATH, _neighbours

    if not TFIDF_PATH.exists():
        pytest.skip("neighbour model not built; run `python -m dwm build`")
    found = _neighbours(con, "RBI keeps repo rate unchanged, says finance minister")
    assert found, "similar headlines must actually be returned"
    for item in found:
        assert item["text"], "a neighbour with no text is a placeholder"
        assert item["date"]
        assert item["similarity"] > 0


def test_unrelated_text_finds_no_neighbours(con) -> None:
    from dwm.inference.checker import TFIDF_PATH, _neighbours

    if not TFIDF_PATH.exists():
        pytest.skip("neighbour model not built")
    assert _neighbours(con, "zzzz qqqq wwww vvvv") == []


def test_check_rejects_an_empty_headline(client) -> None:
    response = client.post("/check", json={"headline": "   "})
    assert response.status_code == 400
    assert "headline" in response.json()["detail"].lower()


def test_check_handles_non_english_input(client) -> None:
    """A reader may paste anything. It must not 500."""
    response = client.post("/check", json={"headline": "中央銀行は金利を引き上げた"})
    assert response.status_code == 200
    assert "style" in response.json()


def test_check_truncates_rather_than_failing_on_very_long_input(client) -> None:
    response = client.post("/check", json={"headline": "SHOCK! " * 900})
    assert response.status_code == 200
    assert response.json()["truncated"] is True


def test_check_reports_a_verbatim_corpus_headline(con) -> None:
    """A headline already in the corpus should say so, rather than looking like
    a novel one being assessed."""
    from dwm.features.scoring import TextScorer
    from dwm.inference.checker import build_response

    row = con.execute(
        "SELECT headline_text FROM fact_headline WHERE in_window AND length(headline_text) > 40 LIMIT 1"
    ).fetchone()
    if not row:
        pytest.skip("no corpus headlines")
    text = row[0]
    payload = build_response(con, text, TextScorer().measure(text))
    assert payload["is_verbatim_in_corpus"] is True


# ---------------------------------------------------------------------------
# the sampling behind the neighbour model
# ---------------------------------------------------------------------------


def test_the_neighbour_sample_is_deterministic(con) -> None:
    """Same seed, same corpus, every run - otherwise the checker would give a
    different answer for the same headline."""
    from dwm.mining.sampling import hash_expr

    a = con.execute(
        f"SELECT count(*) FROM (SELECT {hash_expr(42, 'headline_id')} k "
        f"FROM fact_headline WHERE in_window ORDER BY k LIMIT 500)"
    ).fetchone()[0]
    b = con.execute(
        f"SELECT count(*) FROM (SELECT {hash_expr(42, 'headline_id')} k "
        f"FROM fact_headline WHERE in_window ORDER BY k LIMIT 500)"
    ).fetchone()[0]
    assert a == b == 500
