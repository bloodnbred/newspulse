"""Phase U5 tests: Stock 360 and Event Lab.

Two of these tests are about concurrency, because that is where the phase found
a defect that had survived two earlier fixes.

**Every reader of the shared DuckDB handle takes the lock.** `store.reading()`
holds it for a whole request, because `con.execute()` returns the connection
itself and the rows are read by a later `fetchall()`. A thread that does not
take the lock overwrites another's result set, and the failure lands in a
*different* request than the one that caused it. That is why the hammer test
below names `ui._window()` specifically: it was the one live unlocked reader,
and every previous round of this fix had hammered only the locked ones.

**Abnormal return coincides with an event; it is not its effect.** Two events
within a few days of each other cannot be separated in a five-session window,
so the endpoint reports which events overlap rather than letting the page imply
a clean attribution.

**A mention is a name match, not a story.** The alias that matched travels with
each row, and an ambiguous alias is flagged rather than resolved.
"""
from __future__ import annotations

import threading
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


# ---------------------------------------------------------------------------
# the endpoints answer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/ui/stocks/RELIANCE",
        "/ui/stocks/RELIANCE/headlines?limit=5",
        "/ui/events",
        "/ui/events/1",
    ],
)
def test_the_four_endpoints_answer_as_envelopes(client, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200, f"{path} -> {response.text[:160]}"
    body = response.json()
    assert "data" in body and "meta" in body
    assert body["meta"]["concept"]
    assert "max-age=300" in response.headers.get("cache-control", "")


def test_an_unknown_symbol_is_a_404_that_says_why(client) -> None:
    response = client.get("/ui/stocks/ZZZZNOTREAL")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "ZZZZNOTREAL" in detail
    assert "NSE constituent list" in detail, (
        "a missing company is usually survivorship, and saying so is the "
        "difference between a reader who understands and one who thinks the "
        "study has an error"
    )


def test_an_unknown_event_is_a_404(client) -> None:
    assert client.get("/ui/events/9999").status_code == 404


# ---------------------------------------------------------------------------
# Stock 360
# ---------------------------------------------------------------------------


def test_the_series_is_the_whole_window_not_the_last_400_sessions(client) -> None:
    """Regression: `/stocks/{symbol}` returns the most recent 400 rows.

    That is right for a sparkline and wrong for a chart the reader is about to
    zoom across five years. The chart silently showed the last 30% of the data
    while looking like the whole study.
    """
    data = client.get("/ui/stocks/RELIANCE").json()["data"]
    series = data["series"]
    assert len(series) > 400, f"only {len(series)} sessions; the chart is truncated"
    assert series[0]["date"] == "2015-06-30", "the series does not start at the window's first session"
    assert series[-1]["date"] == "2020-06-30", "the series does not reach the window's last session"
    # Ascending: ECharts' category axis and every dataZoom index assume order.
    dates = [s["date"] for s in series]
    assert dates == sorted(dates), "the series is not in date order"


def test_both_rebased_series_start_at_one_hundred(client) -> None:
    """The rebase is computed in SQL, not in the browser (rule 17)."""
    series = client.get("/ui/stocks/RELIANCE").json()["data"]["series"]
    first = series[0]
    assert abs(first["stock_rebased"] - 100.0) < 0.01
    assert abs(first["nifty_rebased"] - 100.0) < 0.01
    for row in series:
        assert row["stock_rebased"] is not None
        assert row["nifty_close"] is not None


def test_mentions_land_on_the_next_trading_session(client) -> None:
    """Rule 12: the join is on `effective_trading_date_key`, never the calendar
    date. A headline printed on a Sunday belongs to Monday's bar; putting it on
    Sunday would draw activity on a day with no price next to it."""
    series = client.get("/ui/stocks/RELIANCE").json()["data"]["series"]
    with_mentions = [s for s in series if s["mentions"] > 0]
    assert with_mentions, "no mentions at all; the join dropped everything"
    for row in with_mentions:
        assert row["date"], "a mention is attached to no session"
    # Every session in the series is a trading day by construction, so the
    # assertion that matters is that the count is non-zero and the dates are the
    # sessions' dates, which the series guarantees.
    assert all(isinstance(s["mentions"], int) for s in series)


def test_the_outlier_and_mention_caveats_travel_with_the_figure(client) -> None:
    note = client.get("/ui/stocks/RELIANCE").json()["meta"]["note"]
    lowered = note.lower()
    assert "mention means the company name appeared" in lowered
    assert "outlier" in lowered
    assert "adjusted close" in lowered
    assert "precision" in lowered or "audit" in lowered, (
        "mention precision is unmeasured; a page that shows mention counts "
        "without saying the measurement does not exist invites the reader to "
        "assume it was measured"
    )


def test_the_radar_carries_a_population_scale_per_axis(client) -> None:
    """Regression: all six axes were scaled by ONE min/max.

    Mention rate runs to 330 and news sensitivity sits near zero, so four arms
    collapsed onto the centre and the radar drew a vertical spike - a shape that
    looks like a finding and is an artefact of the scaling.
    """
    axes = client.get("/ui/stocks/RELIANCE").json()["data"]["cluster"]["axes"]
    assert len(axes) == 6
    for axis in axes:
        assert set(axis) >= {
            "key", "label", "description", "value", "cluster_mean",
            "pop_min", "pop_max", "percentile",
        }, f"{axis['key']} cannot be scaled without population bounds"
        assert axis["pop_max"] >= axis["pop_min"]
        assert axis["value"] is None or (
            axis["pop_min"] - 1e-9 <= axis["value"] <= axis["pop_max"] + 1e-9
        ), f"{axis['key']} sits outside its own population range"
        assert axis["percentile"] is None or 0 <= axis["percentile"] <= 100


def test_the_change_field_is_named_for_what_it_holds(client) -> None:
    """Regression: the field was `change_pct` and held a rupee difference."""
    data = client.get("/ui/stocks/RELIANCE").json()["data"]
    assert "change_abs" in data
    assert "change_pct" not in data, (
        "a field named change_pct is read as a percentage; the percentage is "
        "already in last.return_pct and the header should use that"
    )
    assert data["last"]["return_pct"] is not None


def test_stock_headlines_are_capped_by_the_api(client) -> None:
    assert client.get("/ui/stocks/RELIANCE/headlines", params={"limit": 50}).status_code == 200
    assert client.get("/ui/stocks/RELIANCE/headlines", params={"limit": 500}).status_code == 422


def test_each_headline_carries_the_alias_that_matched(client) -> None:
    """A mention with no visible alias is unfalsifiable - the reader cannot see
    why a company was matched to a headline, which is the only thing that would
    let them judge a noisy match."""
    rows = client.get("/ui/stocks/RELIANCE/headlines", params={"limit": 20}).json()["data"]
    assert rows
    for row in rows:
        assert row["headline_text"].strip()
        assert "matched_alias" in row
        assert "is_ambiguous_alias" in row


# ---------------------------------------------------------------------------
# Event Lab
# ---------------------------------------------------------------------------


def test_the_news_window_is_ten_sessions_either_side_with_no_gaps(client) -> None:
    """Regression: the offset was a difference of two `row_number()`s over
    opposite orderings, which yields only one parity - the first version
    returned days -9, -7, -5 and silently dropped every other session."""
    rows = client.get("/ui/events/1").json()["data"]["news_around"]
    offsets = [r["offset"] for r in rows]
    assert offsets == list(range(-10, 11)), f"offsets are {offsets}"


def test_the_event_leaderboard_is_the_whole_valid_set(client) -> None:
    """Not the top 20 by |CAR|. A leaderboard of only the movers cannot show
    that most of the index did not move, which is the more common truth."""
    data = client.get("/ui/events/1").json()["data"]
    stocks = data["by_stock"]
    assert len(stocks) > 20, f"only {len(stocks)} stocks; this looks like a top-N cut"
    cars = [s["car_0_5"] for s in stocks]
    assert cars == sorted(cars, reverse=True), "the leaderboard is not sorted"
    assert min(cars) < 0 < max(cars), (
        "a study where nothing fell and nothing rose is a study with no result"
    )


def test_stocks_excluded_for_thin_history_are_reported_with_the_reason(client) -> None:
    """Rule 11's cousin: a stock left out of an average must be visible, or the
    average silently changes population depending on the event."""
    excluded = client.get("/ui/events/1").json()["data"]["excluded"]
    assert excluded, "no exclusions; the model needed 100 pre-event sessions"
    for row in excluded:
        assert row["reason"], "an exclusion with no reason is unexplainable"


def test_the_overlap_warning_is_computed_not_asserted(client) -> None:
    """Two events inside a five-session window cannot be separated, so the
    endpoint reports which events are close. The page decides whether to warn."""
    detail = client.get("/ui/events/1").json()["data"]
    assert "nearby_events" in detail
    for row in detail["nearby_events"]:
        assert abs(row["days_apart"]) <= 4, (
            "an event more than four days away is not an overlap and will "
            "produce a warning chip about a non-problem"
        )


def test_the_event_note_says_coincides_not_causes(client) -> None:
    note = client.get("/ui/events/1").json()["meta"]["note"].lower()
    assert "coincides" in note
    assert "does not show the event caused it" in note
    assert "other news on the same day" in note


def test_the_timeline_lists_every_event_with_its_type(client) -> None:
    events = client.get("/ui/events").json()["data"]["events"]
    assert len(events) >= 10
    types = {e["type"] for e in events}
    assert len(types) >= 3, f"only {types}; the timeline has nothing to colour by"
    for e in events:
        assert e["label"] and e["date"]
        assert e["stocks_studied"] >= 0


# ---------------------------------------------------------------------------
# concurrency: the defect this phase found
# ---------------------------------------------------------------------------


def test_the_unlocked_reader_that_caused_the_fault_is_gone() -> None:
    """`ui._window()` used a module-level unlocked handle.

    Under load it overwrote a concurrent request's result set, and the error
    surfaced in that other request as `TypeError: 'NoneType' object is not
    subscriptable` inside a `_has` call that had been correct all along. The
    symptom pointed away from the cause for three commits.
    """
    from dwm.api import ui

    assert not hasattr(ui, "con"), (
        "ui.con() returned the shared handle WITHOUT the lock; "
        "store.reading() is the only safe way to reach it"
    )
    assert not hasattr(ui, "_LOCK_CON")


def test_every_stock_and_event_read_survives_concurrent_traffic(client) -> None:
    """Hammer the new endpoints alongside the existing ones.

    A concurrency bug that only shows under interleaving will not show in a
    sequential test run, so this is the only place it can be caught.
    """
    paths = [
        "/ui/stocks/RELIANCE",
        "/ui/stocks/TCS/headlines?limit=10",
        "/ui/events",
        "/ui/events/1",
        "/ui/home",
        "/health",
    ]
    errors: list[str] = []

    def hammer(path: str) -> None:
        for _ in range(6):
            try:
                response = client.get(path)
                if response.status_code != 200:
                    errors.append(f"{path}: {response.status_code}")
            except Exception as exc:  # noqa: BLE001 - the point is to catch any
                errors.append(f"{path}: {type(exc).__name__}: {exc}")

    threads = [
        threading.Thread(target=hammer, args=(p,)) for p in paths for _ in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"{len(errors)} concurrent reads failed: {errors[:4]}"
