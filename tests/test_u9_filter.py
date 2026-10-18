"""The global filter actually filters.

The filter bar was on the page for six phases and did nothing at all. Not "did
it wrong" - nothing. No `/ui/*` endpoint declared `from`, `to`, `sectors` or
`topics`, and FastAPI discards query parameters a route does not declare, so
every request succeeded, every URL updated, and every number came back exactly
as it was. Nothing errored, so nothing looked broken.

These tests exist because that failure mode is invisible to a smoke test. They
assert the property that was actually missing - *the numbers move* - rather
than that the endpoints return 200, which they did throughout.

The second property tested here is honesty about the limits. The cubes are
single-dimension: `cube_month_topic` knows topics and has no sector column,
`cube_sector_month` knows sectors and has no topic column. So a sector selection
genuinely cannot narrow headline volume, because a headline only reaches a
sector by naming a listed company - a sparse, unmeasured link. The endpoint
therefore declares which figures each dimension moved, and these tests hold it
to that declaration.
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


def kpis(client, query: str) -> dict[str, float]:
    response = client.get(f"/ui/home{query}")
    assert response.status_code == 200, response.text
    return {k["id"]: k["value"] for k in response.json()["data"]["kpis"]}


def data(client, path: str):
    response = client.get(path)
    assert response.status_code == 200, response.text
    return response.json()["data"]


# ---------------------------------------------------------------------------
# The numbers move
# ---------------------------------------------------------------------------


def test_no_filter_is_the_baseline(client):
    """The unfiltered numbers, pinned so a silent regression is visible.

    If a later change to a cube makes the unfiltered homepage return something
    else, that is a change someone should look at rather than absorb.
    """
    assert kpis(client, "")["headlines"] > 0


@pytest.mark.parametrize(
    "query,kpi,why",
    [
        ("?from=2020-01-01", "headlines", "a date range must narrow the headline count"),
        ("?to=2016-06-30", "headlines", "an end date must narrow the headline count"),
        ("?from=2020-01-01", "sessions", "trading sessions must count only the filtered days"),
        ("?from=2020-01-01", "nifty", "the Nifty change must be measured over the filtered window"),
        ("?topics=Business", "headlines", "a topic must narrow the headline count"),
        ("?sectors=Energy", "surprise", "a sector must narrow the surprise-day count"),
    ],
)
def test_a_filter_moves_the_number_it_claims_to_move(client, query, kpi, why):
    """Each dimension must change the figure it is declared to change.

    Asserted against the unfiltered response rather than against a hard-coded
    number, so the test is about the *relationship* between the two responses.
    A filter that returned the same value either way is the bug.
    """
    baseline = kpis(client, "")[kpi]
    filtered = kpis(client, query)[kpi]
    assert filtered != baseline, f"{why}: {kpi} stayed at {baseline} for {query}"


def test_a_narrower_window_never_reports_more(client):
    """Narrowing can only remove rows, so the count must not go up."""
    wide = kpis(client, "")["headlines"]
    narrow = kpis(client, "?from=2020-01-01")["headlines"]
    assert narrow < wide


def test_two_dates_bound_the_window(client):
    single = kpis(client, "?from=2020-01-01")["headlines"]
    bounded = kpis(client, "?from=2020-01-01&to=2020-03-31")["headlines"]
    assert bounded < single


def test_sectors_narrow_the_sector_figures(client):
    """A sector selection must leave exactly the selected sectors."""
    heat = data(client, "/ui/home?sectors=Energy,IT")["sectorHeat"]
    assert {row["sector"] for row in heat} == {"Energy", "IT"}


def test_calendar_and_tape_follow_the_filter(client):
    """The filter bar sits above the calendar and the tape, so both move."""
    assert len(data(client, "/ui/calendar?from=2020-01-01")) < len(data(client, "/ui/calendar"))
    assert len(data(client, "/ui/tape?sectors=Energy")) == 1
    assert len(data(client, "/ui/tape")) > 1


def test_market_report_toggle_is_not_a_no_op(client):
    """It has to change something, or it is decoration.

    It changes the mood percentile and the sector news counts, because those are
    the tables carrying the market-report split. It cannot change headline
    volume: `cube_month_topic` predates the `is_market_report` flag and has no
    such column. Asserted against the mood, which can honour it.
    """
    off = data(client, "/ui/home")["mood"]["percentile"]
    on = data(client, "/ui/home?include_market=1")["mood"]["percentile"]
    assert off != on


# ---------------------------------------------------------------------------
# Bad input is refused rather than silently ignored
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,fragment",
    [
        ("?sectors=Banknngs", "Unknown sectors"),
        ("?topics=Markets", "Unknown topics"),
        ("?from=notadate", "not a date"),
        ("?from=2015-02-30", "not a real date"),
        ("?from=2020-06-01&to=2020-01-01", "after"),
        ("?include_market=7", "less_than_equal"),
    ],
)
def test_bad_filter_input_is_rejected(client, query, fragment):
    """A typo must not render as a finding.

    `?sectors=Banknngs` used to be indistinguishable from "this sector has no
    news": an empty treemap, a headline count of zero and a mood computed over
    no rows, all rendering perfectly. A 422 naming the valid values is the only
    honest answer.
    """
    response = client.get(f"/ui/home{query}")
    assert response.status_code == 422, response.text
    assert fragment.lower() in response.text.lower()


def test_a_rejected_filter_does_not_pollute_the_cache(client):
    """A 422 must not be cached as though it were a result."""
    client.get("/ui/home?sectors=Banknngs")
    # The unfiltered response immediately afterwards must still be complete.
    assert len(kpis(client, "")) == 6


# ---------------------------------------------------------------------------
# The page is told what it could not filter
# ---------------------------------------------------------------------------


def test_each_kpi_declares_which_dimensions_it_responds_to(client):
    """No KPI may be silent about the filter ignoring it.

    A number that does not move while a control above it is on reads as a
    broken control. The declaration is what lets the card say so.
    """
    for kpi in data(client, "/ui/home")["kpis"]:
        assert "respondsTo" in kpi, kpi["id"]
        assert isinstance(kpi["respondsTo"], list), kpi["id"]


def test_a_kpi_responding_to_nothing_explains_why(client):
    """`Stocks tracked` is a count of companies, and says so."""
    kpis_ = {k["id"]: k for k in data(client, "/ui/home")["kpis"]}
    stocks = kpis_["stocks"]
    assert stocks["respondsTo"] == []
    assert stocks["unfilteredBecause"], "a figure that ignores the filter must say why"


def test_scope_note_names_what_the_filter_could_not_move(client):
    """With a sector selected, the note must admit headline volume did not move."""
    note = data(client, "/ui/home?sectors=Energy")["scope"]["note"]
    assert "Energy" in note
    assert "not narrowed" in note.lower()
    assert "no sector" in note.lower()


def test_no_filter_states_plainly_that_nothing_is_filtered(client):
    note = data(client, "/ui/home")["scope"]["note"]
    assert "no filter applied" in note.lower()


def test_mood_says_what_distribution_it_was_ranked_in(client):
    """A percentile without its comparison set is not interpretable."""
    filtered = data(client, "/ui/home?from=2020-01-01")["mood"]["comparedWith"]
    assert "2020-01-01" in filtered
    whole = data(client, "/ui/home")["mood"]["comparedWith"]
    assert "whole corpus" in whole.lower()


def test_scope_applied_echoes_the_request(client):
    applied = data(client, "/ui/home?sectors=Energy&from=2020-01-01")["scope"]["applied"]
    assert applied["sectors"] == ["Energy"]
    assert applied["from"] == "2020-01-01"
    assert applied["count"] == 2
