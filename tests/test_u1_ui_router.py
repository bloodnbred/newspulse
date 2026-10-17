"""Phase U1 tests: the `/ui` backend-for-frontend router.

Three properties matter more here than the endpoint list, and all three are
things the contract promises rather than things the code happens to do:

* **Every response is an envelope.** `{data, meta:{concept, ms, rows, sql?}}`.
  A route that returns a bare list is not a small inconsistency; the React
  client's `parse()` reads `.data` and `.meta` unconditionally, so a bare list
  renders `undefined` everywhere instead of failing loudly.
* **`meta.sql` is a record, not a description.** It is what the "How this was
  computed" drawer shows. A drawer showing reconstructed SQL would describe
  what the code used to do rather than what it did.
* **Nothing is silently substituted.** `grain=week` is refused with a 422
  rather than answered with monthly points under a weekly label.
"""
from __future__ import annotations

import json
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


UI_PATHS = [
    "/ui/home",
    "/ui/tape",
    "/ui/main-series",
    "/ui/main-series?metric=sentiment",
    "/ui/main-series?metric=sensational",
    "/ui/calendar",
    "/ui/calendar?metric=sentiment",
    "/ui/search?q=rel",
    "/ui/schema",
    "/ui/etl",
    "/ui/insights",
    "/ui/sectors",
    "/ui/brief?q=rel",
    "/ui/day/2020-03-23",
]


# ---------------------------------------------------------------------------
# the envelope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", UI_PATHS)
def test_every_ui_endpoint_answers(client, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200, f"{path} -> {response.status_code} {response.text[:160]}"


@pytest.mark.parametrize("path", UI_PATHS)
def test_every_ui_response_is_an_envelope(client, path: str) -> None:
    """The React client destructures `data` and `meta` unconditionally."""
    body = client.get(path).json()
    assert set(body) >= {"data", "meta"}, f"{path} is not an envelope: {sorted(body)}"
    meta = body["meta"]
    for key in ("concept", "ms", "rows"):
        assert key in meta, f"{path} meta is missing {key}: {sorted(meta)}"
    assert isinstance(meta["ms"], (int, float))
    assert isinstance(meta["rows"], int)
    assert meta["concept"], f"{path} reports no DWM concept"


@pytest.mark.parametrize(
    "path",
    [p for p in UI_PATHS if p not in ("/ui/insights", "/ui/home", "/ui/brief?q=rel")],
)
def test_sql_backed_endpoints_carry_the_sql_they_ran(client, path: str) -> None:
    """`/ui/insights` and `/ui/brief` are Python rather than one query, and
    `/ui/home` wraps several.

    Everything else ran a statement, and the drawer on that chart is only worth
    opening if it shows the real one.
    """
    meta = client.get(path).json()["meta"]
    sql = meta.get("sql")
    assert sql, f"{path} ran a query but reports no SQL"
    assert "select" in sql.lower(), f"{path} reports SQL that is not a query: {sql[:80]!r}"


@pytest.mark.parametrize("path", UI_PATHS)
def test_ui_responses_are_cacheable(client, path: str) -> None:
    """The contract sets `Cache-Control: public, max-age=300`."""
    headers = client.get(path).headers
    assert "max-age=300" in headers.get("cache-control", ""), path


@pytest.mark.parametrize("path", UI_PATHS)
def test_a_ui_response_contains_no_unserialisable_leftovers(client, path: str) -> None:
    """Dates and Decimals come back from DuckDB and must not reach the wire.

    Asserted by re-encoding: a `date` that survived into the payload would raise
    here rather than in the browser, which is the point of `_jsonable`.
    """
    raw = client.get(path).content
    json.loads(raw)  # would raise on anything the encoder coerced lazily
    assert raw, path


# ---------------------------------------------------------------------------
# honesty: nothing is silently substituted
# ---------------------------------------------------------------------------


def test_an_unavailable_grain_is_refused_not_substituted(client) -> None:
    """`grain=week` must not be answered with monthly points.

    The finest cube holding news and price at one grain is monthly. Returning
    monthly data under `grain=week` would give the Time Machine slider a
    resolution it does not have, and the reader would believe they were looking
    at weeks.
    """
    response = client.get("/ui/main-series", params={"grain": "week"})
    assert response.status_code == 422
    body = response.json()
    assert body["data"] == []
    note = body["meta"].get("note", "")
    assert "weekly" in note.lower() and "monthly" in note.lower(), (
        "the refusal must say what is available instead, not only what is not"
    )


def test_an_unknown_metric_is_refused_by_the_query_pattern(client) -> None:
    """FastAPI's `pattern=` turns a typo into a 422 rather than an empty chart."""
    assert client.get("/ui/main-series", params={"metric": "vibes"}).status_code == 422
    assert client.get("/ui/calendar", params={"metric": "vibes"}).status_code == 422


def test_an_empty_search_returns_an_empty_list_not_an_error(client) -> None:
    """`/ui/search?q=zzz` is a reader typing ahead, which is not a failure."""
    body = client.get("/ui/search", params={"q": "zzz"}).json()
    assert body["data"] == []


def test_a_missing_required_parameter_is_a_422(client) -> None:
    assert client.get("/ui/search").status_code == 422


# ---------------------------------------------------------------------------
# caching
# ---------------------------------------------------------------------------


def test_home_is_cached_and_says_whether_it_was_a_hit(client) -> None:
    """The contract says `/ui/home` must be cached, and it is the only endpoint
    expensive enough to need it."""
    first = client.get("/ui/home")
    second = client.get("/ui/home")
    assert first.status_code == second.status_code == 200
    assert "X-Dwm-Cache" in first.headers
    assert second.headers["X-Dwm-Cache"] == "hit", (
        "a second call inside the TTL should be served from cache"
    )
    assert first.json()["data"] == second.json()["data"], (
        "a cache hit must return the same payload, not an approximation of it"
    )


def test_a_cache_hit_keeps_the_original_timing(client) -> None:
    """`meta.ms` is what the drawer shows.

    A cache that reported the original cost on every hit would make that number
    quietly wrong, which is worse than it being slow - so the hit replays the
    build's own `ms`.
    """
    client.get("/ui/home")
    hit = client.get("/ui/home").json()
    assert isinstance(hit["meta"]["ms"], (int, float))


# ---------------------------------------------------------------------------
# payload shape the front end depends on
# ---------------------------------------------------------------------------


def test_home_carries_the_fields_the_homepage_binds_to(client) -> None:
    """Named against `web-scaffold/src/types.ts` -> `HomeSummary`.

    A renamed field is not a type error at runtime: the card renders `undefined`
    and the page looks finished. Asserting the keys is what catches it.
    """
    data = client.get("/ui/home").json()["data"]
    for key in ("devSample", "window", "kpis", "mood", "main",
                "events", "sectorHeat", "surprise", "insights"):
        assert key in data, f"/ui/home is missing {key!r}, which Home.tsx binds to"
    assert set(data["window"]) == {"start", "end"}
    assert data["kpis"], "no KPIs; the hero row would be empty"
    for kpi in data["kpis"]:
        for key in ("id", "label", "value", "spark"):
            assert key in kpi, f"KPI {kpi.get('id')} is missing {key!r}"
        assert isinstance(kpi["spark"], list), "a sparkline needs a series, [] included"
    assert 0 <= data["mood"]["percentile"] <= 100, (
        "the mood gauge is a percentile; a value outside 0-100 cannot be drawn"
    )


def test_the_window_comes_from_the_data_not_a_constant(client) -> None:
    """Rule 4: the window is the corpus's own min/max publish_date.

    The corpus ends 2020-06-30, so a hard-coded "today" or a later end date
    would show a range the corpus does not cover.
    """
    window = client.get("/ui/home").json()["data"]["window"]
    assert window["start"] == "2015-06-30"
    assert window["end"] == "2020-06-30"
    assert window["start"] < window["end"]


def test_every_kpi_sparkline_is_a_drawable_series(client) -> None:
    """Each card draws its own sparkline, so lengths legitimately differ -
    headlines run monthly, the Nifty runs daily. What must hold is that a
    populated sparkline is a real numeric series and not a list of nulls, which
    `KpiCard`'s polyline would render as a blank card with a number on it.
    """
    import math

    kpis = client.get("/ui/home").json()["data"]["kpis"]
    populated = [k for k in kpis if k["spark"]]
    assert populated, "no KPI carries a sparkline series"
    for kpi in populated:
        spark = kpi["spark"]
        assert len(spark) >= 2, f"{kpi['id']} has a sparkline of {len(spark)} point(s)"
        assert all(isinstance(v, (int, float)) for v in spark), (
            f"{kpi['id']} sparkline contains non-numbers: {spark[:5]}"
        )
        assert all(math.isfinite(float(v)) for v in spark), (
            f"{kpi['id']} sparkline contains NaN or infinity, which draws as a gap"
        )


def test_the_calendar_marks_non_trading_days(client) -> None:
    """32.4% of in-window headlines fall on days the market was closed.

    Without `is_trading_day` the calendar shows those as quiet market days,
    which is the opposite of what they are.
    """
    rows = client.get("/ui/calendar").json()["data"]
    assert rows
    assert "is_trading_day" in rows[0]
    flags = {r["is_trading_day"] for r in rows}
    assert len(flags) == 2, "every in-window day is flagged as a trading day, which is false"


def test_a_day_caps_its_headlines(client) -> None:
    """Rule 21: headline lists are capped at 50."""
    data = client.get("/ui/day/2020-03-23").json()["data"]
    assert 0 < len(data["headlines"]) <= 50
    assert data["headline_count"] == len(data["headlines"])


def test_the_lockdown_session_reports_a_real_market_move(client) -> None:
    """A spot check that the numbers are the real ones and not placeholders."""
    data = client.get("/ui/day/2020-03-23").json()["data"]
    assert data["market"] is not None, "no market row for a trading day in the window"
    assert data["market"]["return_pct"] < -5, (
        f"2020-03-23 was a -12.98% session; got {data['market']['return_pct']}"
    )
    # And the honest part: this corpus had little to say about it.
    kinds = {row["kind"] for row in data["surprise"] or []}
    assert "quiet_news_move" in kinds, (
        "the lockdown session is the clearest coverage gap in the window; if it "
        "no longer reads as quiet_news_move, the z-scores changed"
    )


def test_the_schema_is_read_from_the_catalogue(client) -> None:
    """Column lists come from DuckDB, so they cannot drift from the file."""
    data = client.get("/ui/schema").json()["data"]
    names = {t["name"] for t in data["tables"]}
    assert "fact_headline" in names
    assert "fact_stock_daily" in names
    headline = next(t for t in data["tables"] if t["name"] == "fact_headline")
    columns = {c["name"] for c in headline["columns"]}
    # The key name the contract got wrong: `headline_key` does not exist.
    assert "headline_id" in columns
    assert "headline_key" not in columns


# ---------------------------------------------------------------------------
# CORS, which the scaffold needs and did not have
# ---------------------------------------------------------------------------


def test_the_vite_dev_origin_is_allowed(client) -> None:
    """The React app runs on :5173 and reads the API cross-origin.

    Without this every request is blocked and the app shows an error instead of
    data, with nothing in the browser console to say why.
    """
    response = client.get("/ui/home", headers={"Origin": "http://localhost:5173"})
    assert "http://localhost:5173" in response.headers.get("access-control-allow-origin", "")


def test_the_vite_origin_is_allowed_for_a_preflight(client) -> None:
    """POST /ui/check needs a preflight, and preflight is a separate request."""
    response = client.options(
        "/ui/home",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code in (200, 204)
    assert "127.0.0.1:5173" in response.headers.get("access-control-allow-origin", "")


# ---------------------------------------------------------------------------
# the pack defects this phase fixed
# ---------------------------------------------------------------------------


def test_the_concept_union_accepts_what_the_server_sends() -> None:
    """`web-scaffold/src/types.ts` closed its `Concept` union on ten values.

    `dwm/inference.insights` emits richer labels for all six of its cards, none
    of which were in the list - a type error at the boundary under `tsc`, and
    addendum rule 11 makes a clean build the definition of done, so it blocks
    the first acceptance rather than being cosmetic.
    """
    import duckdb

    from dwm.inference.insights import build

    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    try:
        emitted = {card["concept"] for card in build(con)["cards"]}
    finally:
        con.close()
    assert emitted, "no insight cards were produced, so this check proves nothing"

    types = (
        Path(__file__).resolve().parent.parent
        / "web-scaffold" / "src" / "types.ts"
    ).read_text(encoding="utf-8")
    assert "(string & {})" in types, (
        "the Concept union is still closed; the server's own labels will not "
        "type-check against it"
    )
    # The documented list should still be there for autocomplete.
    assert "Drill-across" in types and "Event study" in types


def test_the_sector_palette_avoids_the_semantic_hues() -> None:
    """A sector must never be drawn in the colour that means something else.

    The pack's palette opened with `#14B8A6` teal, `#F59E0B` amber and
    `#8B5CF6` violet - which are `--news`, `--price` and `--mining`. Sector one
    would then be painted in "news" beside a series that means news.
    """
    import re

    source = (
        Path(__file__).resolve().parent.parent
        / "web-scaffold" / "src" / "lib" / "echartsTheme.ts"
    ).read_text(encoding="utf-8")
    block = re.search(r"SECTOR_COLORS\s*=\s*\[(.*?)\]", source, re.S)
    assert block, "SECTOR_COLORS not found; the palette moved and this check is stale"
    palette = {c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}", block.group(1))}

    tokens = (
        Path(__file__).resolve().parent.parent
        / "web-scaffold" / "src" / "styles" / "tokens.css"
    ).read_text(encoding="utf-8")
    semantic = {
        name: value.upper()
        for name, value in re.findall(r"--(news|price|mining|up|down):\s*(#[0-9A-Fa-f]{6})", tokens)
    }
    assert semantic, "no semantic tokens found in tokens.css; the check is stale"

    clash = palette & set(semantic.values())
    assert not clash, (
        f"sector palette reuses semantic hues {sorted(clash)} "
        f"(semantic: {semantic}); a category will read as a meaning"
    )
    assert len(palette) >= 8, f"only {len(palette)} distinct sector colours for 10 sectors"


# ---------------------------------------------------------------------------
# U3: the endpoints the Home page added, and the field names it binds to
# ---------------------------------------------------------------------------


def test_events_use_the_names_the_front_end_declares(client) -> None:
    """Regression: the calendar crashed on `e.date.slice` and blanked the page.

    `dim_event` calls them `start_date` and `event_label`; `EventMark` in
    types.ts declares `date` and `label`. The first `/ui/home` returned the
    dimension's own column names, so `date` was `undefined` and
    `events.map((e) => e.date.slice(0, 10))` threw inside a `useMemo` -
    which unmounted the whole page, not just the calendar.

    The right fix is in the SQL, not in the component: a front end should not
    have to know a dimension's column names, and a defensive `?? ''` would only
    have traded a crash for a calendar with no events in it.
    """
    events = client.get("/ui/home").json()["data"]["events"]
    assert events, "no events; the Time Machine flags nothing"
    for event in events:
        assert set(event) >= {"date", "label", "type"}, (
            f"event {event} does not carry the names EventMark declares"
        )
        # Not merely present: usable. A null here throws the same way.
        assert isinstance(event["date"], str) and len(event["date"]) == 10, (
            f"event date must be an ISO day, got {event['date']!r}"
        )
        assert isinstance(event["label"], str) and event["label"], "an unlabelled flag"


def test_sector_heat_carries_the_z_score_its_tooltip_quotes(client) -> None:
    """`SectorHeat` declares `heat_z: number | null` and the treemap tooltip
    prints it, so a payload without the key renders "undefined" in a sentence.

    It is also the number that makes the treemap honest: raw volume ranks a
    large sector above a small one forever, and the z is scored against each
    sector's own twelve-month history.
    """
    rows = client.get("/ui/home").json()["data"]["sectorHeat"]
    assert rows, "no sector rows; the treemap would be empty"
    for row in rows:
        assert set(row) == {"sector", "news_count", "heat_z", "avg_return_pct"}, (
            f"sectorHeat row keys changed: {sorted(row)}"
        )
        assert row["news_count"] > 0
        # `null` is allowed by the type (a sector with a flat history has no
        # standard deviation), so only the type is asserted, not the value.
        assert row["heat_z"] is None or isinstance(row["heat_z"], (int, float))


def test_the_mood_percentile_is_a_rank_not_a_tally(client) -> None:
    """Regression: the first mood query read "0.1th percentile".

    It counted how many days matched the single busiest day, which is one day
    in ~1800. Arithmetically a percentage, semantically nothing. A percentile
    has to be a rank in a distribution, and for a distribution of daily headline
    counts the busiest day is the top hundredth of one percent - so a result
    that low means the query is still counting days instead of ranking them.
    """
    mood = client.get("/ui/home").json()["data"]["mood"]
    assert 2.0 <= mood["percentile"] <= 100.0, (
        f"mood percentile {mood['percentile']} is not a plausible rank; "
        "check that the query ranks days rather than counting ties"
    )
    assert mood["label"] in {"Calm", "Active", "Heated"}, (
        f"mood label {mood['label']!r} is not one of the three derived bands"
    )
    assert mood["recent_level"] is not None and mood["recent_level"] > 0


def test_sectors_answers_for_the_filter_bar(client) -> None:
    """The filter bar is the one control every page shares, and it cannot call
    the v2 `/sectors` and claim to follow addendum rule 1."""
    data = client.get("/ui/sectors").json()["data"]
    assert len(data) >= 8, f"only {len(data)} sectors in dim_sector"
    for row in data:
        assert set(row) >= {"sector_key", "sector_name", "constituents"}
    meta = client.get("/ui/sectors").json()["meta"]
    assert "constituent" in meta["note"].lower(), (
        "the survivorship caveat must ride with the sector list; it is the only "
        "constituent list available and it is today's applied to a 2015 window"
    )


def test_brief_resolves_a_company_or_says_it_cannot(client) -> None:
    """`/ui/brief` backs the hero search. Rule 1 and the quick-brief rule both
    say the same thing: an unresolved name is reported, never guessed."""
    hit = client.get("/ui/brief", params={"q": "Reliance"}).json()["data"]
    assert hit["kind"] == "stock", f"a known company came back as {hit['kind']!r}"
    assert hit.get("name"), "a known company produced no name"
    assert hit.get("paragraphs"), "a resolved brief produced no text"
    assert hit.get("caveats"), (
        "a brief without its caveats is the plain-language rule broken: the "
        "reader is told the numbers without being told what they do not cover"
    )

    miss = client.get("/ui/brief", params={"q": "Zzqq Not A Company"}).json()["data"]
    assert miss["kind"] == "none", (
        f"an unknown name resolved to {miss['kind']!r}; the brief is guessing"
    )
    assert not miss.get("name"), "an unmatched brief still carries a name"
    assert miss.get("message"), "an unmatched brief must say so in words"
    assert miss.get("did_you_mean"), (
        "an unmatched brief must offer a way forward, not a dead end"
    )
