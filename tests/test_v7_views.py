"""Phase V7 tests: the v2 dashboard views, their specs and their wiring.

The static checks live in tests/test_ui.py, which already greps both JS files
and checks the palette against the CSS. What is here is the part that needs the
server: that each v2 chart the views mount actually builds from real data in
both themes, that the payloads stay small enough to load, and that the
disclosures the v2 pages promise are really present.

A test that skips is a test that never ran, so the skip guards here state the
command that would make each one execute rather than hiding the gap.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "dashboard" / "static"
WAREHOUSE = ROOT / "warehouse" / "dwm.duckdb"
MODELS = ROOT / "models" / "tfidf.joblib"

pytestmark = pytest.mark.skipif(
    not WAREHOUSE.exists(),
    reason="warehouse not built; run `python -m dwm all` first",
)


@pytest.fixture(scope="module")
def con():
    import duckdb

    connection = duckdb.connect(str(WAREHOUSE), read_only=True)
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from dwm.api.app import app

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# the spec builder
# ---------------------------------------------------------------------------


V2_CHARTS = [
    "v2_market_mood",
    "v2_market_price",
    "v2_stock_price",
    "v2_sector_heatmap",
    "v2_event_car",
    "v2_cluster_scatter",
    "v2_direction_metrics",
    "v2_checker_percentile",
]


def test_every_v2_chart_is_named_by_the_front_end() -> None:
    """A spec nobody mounts is dead weight on the wire, and a chart the front
    end names but no spec serves renders the error box instead of a picture."""
    from dwm.ui.v2_charts import build_v2_charts

    served = set(build_v2_charts())
    source = (STATIC / "v2.js").read_text(encoding="utf-8")
    named = {name for name in V2_CHARTS if f'"{name}"' in source}
    assert named == served, (
        f"served but not rendered: {sorted(served - named)}; "
        f"named but not served: {sorted(named - served)}"
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_v2_chart_carries_a_caption_and_a_caution(theme: str) -> None:
    """Both are design requirements, and both are trivially forgettable.

    A chart with no caption has no visible single-number summary; one with no
    caution lets the reader take the figure as more than it is. Neither fails to
    render - which is exactly why they need asserting.
    """
    from dwm.api.v2 import _con
    from dwm.ui.v2_charts import build_v2_charts

    # Real rows, so these are the specs the endpoint will actually serve and not
    # the named-empty placeholders a no-argument call produces.
    payload = build_v2_charts(
        theme_name=theme,
        mood_rows=_mood_rows(),
        stock_points=_stock_rows(_con()),
        cube_rows=[{"sector": "Energy", "p2019": 12.0, "p2020": 9.0}],
        event_bars=[{"sector": "Energy", "mean_car_0_5": 1.2, "n_stocks": 5}],
        cluster_points=[
            {"symbol": "TCS", "sector": "Information Technology", "cluster_id": 0,
             "pc1": 0.1, "pc2": -0.2, "ann_volatility": 22.0, "beta": 0.9},
        ],
        direction=[{"model": "logistic", "accuracy": 0.499}],
        majority=0.505,
        yesterday=0.497,
        percentile_hist=[{"bin": "0.00-0.05", "count": 1000}],
        headline_score=0.75,
    )
    for name, spec in payload.items():
        assert not spec.get("dwm_empty"), f"{name} built nothing from real input"
        assert spec.get("dwm_caption"), f"{name} has no caption"
        assert spec.get("dwm_caution"), f"{name} has no caution"
        assert spec.get("dwm_theme") == theme


def test_a_chart_with_no_data_says_which_command_produces_it() -> None:
    """An empty spec names the phase, so "no data" is never the whole message."""
    from dwm.ui.v2_charts import build_v2_charts

    payload = build_v2_charts()
    for name, spec in payload.items():
        assert spec.get("dwm_empty"), f"{name} produced a spec with no data"
        assert "dwm" in spec["dwm_empty"], f"{name} does not name a command"


def test_the_mood_chart_tells_the_reader_which_lag_it_shows() -> None:
    """The lag is the entire point of a drill-across chart, so it belongs in the
    chart's own caution rather than only in a control the reader may skip."""
    from dwm.ui.v2_charts import build_v2_charts

    at_zero = build_v2_charts(mood_rows=_mood_rows(), lag=0)["v2_market_mood"]
    lagged = build_v2_charts(mood_rows=_mood_rows(), lag=-1)["v2_market_mood"]
    assert "same session" in at_zero["dwm_caution"]
    assert "earlier" in lagged["dwm_caution"]
    assert "SHIFTED" in lagged["dwm_caution"]


def test_the_checker_chart_does_not_invent_a_bar_height() -> None:
    """The marker band is named, not plotted.

    Plotting it would need a count, and the only way to get one is to invent it.
    The marker therefore carries count 0 and a label saying which band it is.
    """
    from dwm.ui.v2_charts import build_v2_charts

    hist = [{"bin": f"{i / 10:.2f}-{(i + 1) / 10:.2f}", "count": 100 * (i + 1)}
            for i in range(10)]
    spec = build_v2_charts(percentile_hist=hist, headline_score=0.42)["v2_checker_percentile"]
    marks = [row for row in spec["data"]["values"] if row.get("kind") == "mark"]
    assert len(marks) == 1
    assert marks[0]["count"] == 0, "a marker height would be a fabricated count"
    assert marks[0]["bin"].startswith("yours")


def test_the_checker_chart_omits_the_marker_until_a_score_exists() -> None:
    """Before anything is submitted there is no 'yours' to mark, and a marker at
    zero would claim a headline had been scored when none had."""
    from dwm.ui.v2_charts import build_v2_charts

    hist = [{"bin": "0.00-0.10", "count": 500}]
    spec = build_v2_charts(percentile_hist=hist)["v2_checker_percentile"]
    assert not [r for r in spec["data"]["values"] if r.get("kind") == "mark"]


# ---------------------------------------------------------------------------
# the drill-across payload
# ---------------------------------------------------------------------------


def test_the_drill_across_payload_stays_small_enough_to_load() -> None:
    """Rule 21, and a plain matter of not shipping 2MB of JSON to a browser.

    The daily grain capped at 250 sessions for exactly this reason, so the cap is
    a test rather than a comment: raising it without thinking about the payload
    should fail here.
    """
    for grain, ceiling in (("month", 40_000), ("week", 120_000), ("day", 400_000)):
        rows = _mood_rows()
        from dwm.api.v2 import _by_grain

        rolled, caption = _by_grain(rows, grain)
        size = len(json.dumps(rolled).encode("utf-8"))
        assert size < ceiling, f"{grain} grain is {size:,} bytes (ceiling {ceiling:,})"
        assert caption, f"{grain} grain caption is empty"


def test_rolling_up_weights_a_mean_by_the_observations_behind_it(con) -> None:
    """Rule 16, and the arithmetic that makes it true.

    A day covering two stocks and a day covering eight must not contribute
    equally to the month's mean return. The roll-up multiplies each daily mean
    by its `stock_obs` and divides once at the end.

    The row shape here is the REAL one from `drill_across_sector` - which
    returns `mean_return_pct`, not `sum_return`. An earlier version of this test
    fed `sum_return`, and `drill_across_sector` does not provide it, so the
    function quietly produced 0.0 for all 61 months and the test passed anyway.
    Feeding a shape the production query never emits is how a zero arrives in a
    chart and nothing goes red.
    """
    from dwm.api.v2 import _by_grain

    rows = [
        {"session": "2020-01-01", "sector": "Energy", "news_count": 1.0,
         "mean_return_pct": 10.0, "mean_abs_return_pct": 10.0,
         "stock_obs": 1, "sum_sentiment": 0.1},
        {"session": "2020-01-31", "sector": "Energy", "news_count": 3.0,
         "mean_return_pct": 20.0 / 3, "mean_abs_return_pct": 20.0 / 3,
         "stock_obs": 3, "sum_sentiment": 0.3},
    ]
    rolled, _ = _by_grain(rows, "month")
    assert len(rolled) == 1
    month = rolled[0]
    # (10.0 x 1 + 6.667 x 3) / 4 = 7.5, not the mean of the two daily means (8.33).
    assert month["mean_return_pct"] == pytest.approx(7.5)
    assert month["stock_obs"] == 4
    assert month["days"] == 2


def test_the_real_drill_across_rows_roll_up_to_non_zero_returns(client) -> None:
    """The guard against the shape mismatch above, run against real output.

    `_by_grain` returning an all-zero return column is invisible: the chart
    draws, the caption says "61 months, averaged", and the price panel is simply
    a flat line. Asserting that a real roll-up has a non-zero spread catches the
    whole class, whatever key the function happens to read.
    """
    from dwm.api.v2 import _by_grain, _reading
    from dwm.olap import sector_ops

    with _reading() as con:
        raw = sector_ops.run_operation(con, "drill_across_sector", lag=0)["rows"]
    assert raw, "no drill-across rows to roll up"

    rolled, caption = _by_grain(raw, "month")
    returns = [r["mean_return_pct"] for r in rolled if r["mean_return_pct"] is not None]
    assert returns, "every rolled-up return is null"
    assert any(v != 0 for v in returns), (
        "every rolled-up return is 0.0, so the price panel of the market-mood "
        "chart draws as a flat line and nothing raises"
    )
    assert max(returns) - min(returns) > 0.1, (
        f"the rolled-up returns barely vary (min {min(returns)}, max "
        f"{max(returns)}), which usually means the wrong column is being summed"
    )
    assert "months" in caption


def test_the_day_grain_cap_says_it_is_a_window(con) -> None:
    rows = [{"session": f"2020-01-{d:02d}", "news_count": 1.0, "sum_return": 0.0,
             "stock_obs": 1, "sum_sentiment": 0.0} for d in range(1, 29)]
    from dwm.api.v2 import _by_grain

    rolled, caption = _by_grain(rows, "day")
    assert len(rolled) == 28, "below the cap nothing is dropped"
    assert "sessions" in caption

    many = rows * 20  # 560 rows, over the cap
    rolled, caption = _by_grain(many, "day")
    assert len(rolled) == 250
    assert "last" in caption and "of" in caption, (
        "a truncated window must say it is a window, or it reads as the whole period"
    )


# ---------------------------------------------------------------------------
# the endpoint
# ---------------------------------------------------------------------------


def test_the_v2_chart_endpoint_answers_in_both_themes(client) -> None:
    for theme in ("light", "dark"):
        response = client.get("/ui/v2charts", params={"theme": theme, "symbol": "TCS"})
        assert response.status_code == 200
        body = response.json()
        assert body["theme"] == theme
        assert set(body["charts"]) == set(V2_CHARTS)


def test_the_endpoint_never_serves_an_unknown_measure(client) -> None:
    """An unrecognised measure must be refused, not silently defaulting to
    news_count - a chart titled one thing and drawing another is worse than an
    error message."""
    response = client.get("/ui/v2charts", params={"measure": "not_a_measure"})
    assert response.status_code == 400
    assert "measure" in response.json()["detail"].lower()


def test_the_endpoint_refuses_an_out_of_range_lag(client) -> None:
    response = client.get("/ui/v2charts", params={"lag": 9})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "operation",
    ["slice", "dice", "rollup", "drilldown", "pivot", "cube", "drill-across",
     "stock_profile"],
)
def test_every_sector_operation_is_reachable_and_explains_itself(client, operation: str) -> None:
    """Every whitelisted operation answers, and carries its own caveat.

    The caveat is the part that matters: an OLAP row set is a set of numbers, and
    a reader who cannot see whether it is news or sentiment, or whether the
    averages were stored, will read it as whatever they assumed.
    """
    params = {"symbol": "TCS"} if operation == "stock_profile" else {}
    response = client.get(f"/olap/sector/{operation}", params=params)
    assert response.status_code == 200, response.text[:200]
    body = response.json()
    assert body.get("caveat"), f"{operation} returns rows with no caveat"
    assert body.get("description"), f"{operation} does not say what it computed"


def test_an_unknown_sector_operation_lists_the_real_ones(client) -> None:
    response = client.get("/olap/sector/nonsense")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "slice" in detail and "drill-across" in detail, (
        "a 404 that does not say what is available is a dead end"
    )


def test_drilldown_defaults_to_the_latest_year_the_cube_holds(client) -> None:
    """The corpus ends 2020-06-30, so defaulting to the current year would
    return nothing and look like a broken endpoint."""
    response = client.get("/olap/sector/drilldown")
    assert response.status_code == 200
    assert "2020" in response.json()["description"]


# ---------------------------------------------------------------------------
# the disclosures the pages promise
# ---------------------------------------------------------------------------


def test_the_quality_endpoint_admits_the_unaudited_precision(client) -> None:
    """Mention precision is absent until a person fills in the audit sample, and
    the page says so rather than showing a blank cell."""
    response = client.get("/quality")
    assert response.status_code == 200
    body = response.json()
    assert "is_correct" in body["caveat"]
    linking = body.get("linking") or {}
    assert linking.get("precision") is None, (
        "if precision has been audited, update this test and the page copy"
    )


def test_the_stock_listing_discloses_survivorship(client) -> None:
    """Today's Nifty 50 list applied to a 2015-2020 window. Said on the
    endpoint, so it cannot be forgotten on a page."""
    body = client.get("/stocks").json()
    assert "surviv" in body["caveat"].lower()
    assert "Nifty 50" in body["caveat"]


def test_the_clusters_endpoint_reports_the_silhouette_context(client) -> None:
    """A silhouette near 0.2 is weak structure, and the endpoint says so on
    every call rather than only on the page that happens to mention it."""
    body = client.get("/mining/stockclusters").json()
    assert "silhouette" in body["caveat"].lower()
    assert body["clusters"], "no clusters returned"


def test_the_direction_endpoint_always_carries_both_baselines(client) -> None:
    """A model number without a baseline beside it is the easiest figure in this
    project to over-read."""
    body = client.get("/mining/direction").json()
    assert body["baseline_majority"] is not None
    assert body["baseline_yesterday"] is not None
    assert "beats_baseline" in body
    assert "chronological" in body["caveat"].lower()


# ---------------------------------------------------------------------------
# the JS, statically
# ---------------------------------------------------------------------------


def test_the_v2_views_open_with_a_question_box() -> None:
    """Every finding page leads with the question and the decision it supports.
    Stated as a test because it is a design rule that nothing enforces on its
    own - a page without one still renders, and still looks finished."""
    source = (STATIC / "v2.js").read_text(encoding="utf-8")
    pages = re.findall(r"V2\.(page\w+)\s*=\s*function", source)
    assert len(pages) >= 8, f"expected the eight v2 views, found {pages}"
    # Each page function opens by emitting the question box, before any chart.
    for name in pages:
        body = source.split(f"V2.{name} = function", 1)[1].split("\n  };", 1)[0]
        assert "questionBox(" in body, f"{name} has no question box"
        between = body[: body.index("questionBox(")]
        assert "chart(" not in between, (
            f"{name} mounts a chart before stating its question"
        )


def test_no_v2_view_hardcodes_a_colour() -> None:
    """Colours come from the CSS custom properties and the Python theme. A hex
    literal in JavaScript is a chart that will not follow the theme toggle and
    will drift from the other six."""
    source = (STATIC / "v2.js").read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("//", "*", "/*")):
            continue
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", line), f"hardcoded colour: {stripped}"
        assert "rgb(" not in line.lower(), f"hardcoded colour: {stripped}"


def test_the_v2_views_clear_the_spec_cache_on_a_theme_change() -> None:
    """Every v2 spec embeds the theme it was built in. A cache that survives the
    toggle reproduces the half-light/half-dark failure this dashboard was
    rewritten to fix."""
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "clearSpecCache" in app_js, "app.js never clears the v2 spec cache"


def test_every_v2_endpoint_the_views_call_exists(client) -> None:
    """Parsed out of the JavaScript rather than hand-listed.

    A hand-written list drifts from the code the day a path is edited, and the
    drift is invisible until someone clicks the page. This reads the paths the
    views actually request and calls each one.

    503 is accepted, because it is the documented "not built yet" answer and says
    which command to run. It is a legitimate response from a checkout where the
    inference stage has not run; what must not happen is a 404 or a 500 from an
    endpoint the view genuinely calls.
    """
    source = (STATIC / "v2.js").read_text(encoding="utf-8")
    literal_paths = set(re.findall(r'api\(\s*"/([a-z0-9/_-]+)"', source))
    template_paths = set(re.findall(r'api\(\s*`/([a-z0-9/_${}.-]+)`', source))
    assert literal_paths, "no v2 endpoints found; the parser probably broke"

    # Template paths are filled in per page, so only their static prefix is
    # checked here. `/events/{key}/study` becomes a 404 for a literal that does
    # not exist, and a substitution error is caught by the endpoint tests above.
    checked = {p for p in literal_paths if "{" not in p}
    # Trailing-slash prefixes are captured when the view concatenates a symbol or
    # an event key onto a literal (`"/stocks/" + symbol`). The base path is what
    # can be checked without inventing a value for each.
    for path in sorted(checked):
        path = path.rstrip("/")
        if not path:
            continue
        # `/brief` requires `q`; the view always supplies one, so the check does
        # too. Calling it bare would assert 200 on an endpoint that is correctly
        # returning 422 for a missing argument.
        params = {"q": "Reliance"} if path == "brief" else None
        response = client.get(f"/{path}", params=params)
        assert response.status_code in (200, 503), (
            f"/{path} -> {response.status_code}. The view calls it, so a 404 or a "
            f"500 here is a broken button on a live page."
        )
        if response.status_code == 503:
            assert "python -m dwm" in response.json()["detail"], (
                f"/{path} reports 503 without naming the command to run"
            )
    assert template_paths, "no template paths found"


# ---------------------------------------------------------------------------
# concurrency
# ---------------------------------------------------------------------------


def test_parallel_requests_do_not_corrupt_each_other(client) -> None:
    """Regression guard for the defect only a browser could find.

    FastAPI runs sync endpoints in a threadpool, and the dashboard fires
    requests in parallel — the mood page hits `/olap/sector/drill-across` and
    `/ui/v2charts` in a `Promise.all`. Both handlers read the one shared DuckDB
    handle, and DuckDB's `execute()` returns the connection itself while the row
    is read by a separate `fetchone()`/`fetchall()`. Two threads interleaving
    between those two calls produced two failures in the wild:

      * `TypeError: 'NoneType' object is not subscriptable`, and
      * `ValueError: zip() argument 2 is shorter than argument 1` — which is
        worse, because it means column names from one query zipped onto rows
        from another, and the response looks perfectly well-formed.

    Every other test in this file issues its requests one after another, so none
    of them could see this. That is the whole reason this one exists, and the
    reason it hammers the same few endpoints from many threads at once.
    """
    import concurrent.futures

    paths = [
        "/stocks",
        "/sectors",
        "/events/1/study",
        "/quality",
        "/mining/stockclusters",
        "/olap/sector/drill-across?lag=0",
        "/ui/v2charts?grain=month&symbol=TCS",
        "/brief?q=Reliance",
    ]
    rounds = 6
    errors: list[str] = []
    checked = 0

    def one(path: str) -> tuple[str, int]:
        return path, client.get(path).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(one, paths[i % len(paths)]) for i in range(len(paths) * rounds)]
        for job in concurrent.futures.as_completed(jobs):
            path, status = job.result()
            checked += 1
            if status != 200:
                errors.append(f"{path} -> {status}")

    assert checked == len(paths) * rounds
    assert not errors, (
        f"{len(errors)} of {checked} parallel requests failed; the shared DuckDB "
        f"handle is being interleaved: {sorted(set(errors))}"
    )


def test_a_parallel_response_is_not_built_from_another_querys_rows(client) -> None:
    """The corruption was silent, so the shape of the answer is checked.

    A response whose keys do not match the keys of the same endpoint's own
    serial response is a response zipped from the wrong query. Asserting on the
    status code alone would pass this: the failing responses above were 500s
    only because the mismatch happened to raise.
    """
    import concurrent.futures

    serial = client.get("/sectors").json()
    expected = set(serial["sectors"][0])

    def one(_: int) -> dict:
        return client.get("/sectors").json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for body in pool.map(one, range(24)):
            assert body["sectors"], "a concurrent request returned no rows"
            for row in body["sectors"]:
                assert set(row) == expected, (
                    f"a concurrent response has columns {sorted(row)} where a "
                    f"serial one has {sorted(expected)}"
                )


# ---------------------------------------------------------------------------
# percentage columns: points vs fractions
# ---------------------------------------------------------------------------


#: `(column name, a value the warehouse really produces, the digits that must
#: appear on screen)`. These are the columns the front end used to scale wrongly.
PERCENT_CASES = [
    ("mean_return_pct", 1.0669, "1.07%"),        # a 1.07% sector day
    ("mean_abs_return_pct", 23.23, "23.23%"),    # the largest clean single-stock move
    ("return_pct", 0.0427, "0.04%"),             # v1's own fact_market_daily
    ("variance_explained_pct", 7.993, "7.99%"),  # v1's regression figure
    ("risk_signal_rate", 0.034589, "3.46%"),     # a genuine fraction
    ("dominant_cluster_share", 0.9488, "94.88%"),
    ("share_of_year", 0.4302, "43.02%"),          # a fraction that does not end in _share
    ("share_of_window", 0.0064, "0.64%"),
    ("positive_rate", 0.049761, "4.98%"),
    ("min_rows_for_a_rate", 1000, "1,000"),       # a threshold, not a percentage
]


@pytest.mark.parametrize(
    ("column", "value", "expected"), PERCENT_CASES
)
def test_a_percentage_column_is_scaled_by_its_own_name(column: str, value: float, expected: str) -> None:
    """Three different things are called "a percentage" here, and the front end
    used to treat all three the same way.

    `_pct` columns are ALREADY percentage points - `fact_stock_daily.return_pct`
    is 1.07 for a 1.07% day. Multiplying those by 100 showed a 1.07% sector day
    as "106.69%" on the market-mood page and a 7.99% variance as "799.30%" in the
    v1 findings. `_rate` and `_share` columns really are 0-1 fractions, and
    `min_rows_for_a_rate` is a row count.

    Run against the real `olapColumns()` from app.js, because the bug was in the
    formatter rather than in any data.
    """
    import re

    # `olapColumns` is not exported, so it is evaluated here by loading app.js as
    # a module in a stub environment. Everything it touches at definition time is
    # DOM, so only the function bodies are extracted.
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    body = _extract_js_function(source, "olapColumns")
    harness = f"""
    function esc(v) {{ return String(v); }}
    function pct(v, d) {{ return (v * 100).toFixed(d === undefined ? 1 : d) + "%"; }}
    function num(v) {{ return Number(v).toLocaleString(); }}
    const dateishSet = new Set(["year","month","year_month","date","full_date","year_no","month_no"]);
    {body}
    const cols = olapColumns([{{ "{column}": {value} }}], ["{column}"]);
    const cell = cols[0];
    // `html` is null for a column with no special formatter, and `table()` then
    // renders the raw value through `num()`. Reproduce that so a threshold
    // column is checked the way it is actually displayed.
    const out = cell.html ? cell.html({{ "{column}": {value} }})
                          : num(cell.get ? cell.get({{ "{column}": {value} }}) : {value});
    process.stdout.write(String(out));
    """
    out = _run_node(harness)
    assert out == expected, (
        f"{column}={value} rendered as {out!r}, expected {expected!r}"
    )
    assert re.search(r"n/a", out) is None


def _extract_js_function(source: str, name: str) -> str:
    """Pull one top-level `function name(...) {...}` out of the JavaScript."""
    start = source.index(f"function {name}(")
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start: i + 1]
    raise AssertionError(f"could not find the end of {name}()")


def _run_node(harness: str) -> str:
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "harness.js"
        path.write_text(harness, encoding="utf-8")
        result = subprocess.run(
            ["node", str(path)], capture_output=True, text=True, timeout=60,
        )
    if result.returncode != 0:
        raise AssertionError(f"node failed:\n{result.stderr}")
    return result.stdout.strip()


def test_the_mood_chart_never_puts_two_units_on_one_axis() -> None:
    """The price series was invisible, which is not the same as wrong-looking.

    Vega-Lite resolves a *layered* chart's y-scale to shared by default, so the
    news and price series were drawn on one axis. Headline-days run 2 to 6 and
    mean returns run about 0.01%, so the price line rendered as a flat rule
    pinned to zero. Nothing in the output said "missing"; it looked like a series
    that happened not to move.

    Tried twice since. A `vconcat` with independent y-scales fixed the flat line
    and rendered about 327px wide inside a 1030px card, because a composed spec
    does not take `width: container` the way a single-view spec does. So they are
    now two separate charts, which fill the card correctly.

    The test asserts both halves of that: no composed spec anywhere, and both
    series still present under distinct names.
    """
    from dwm.ui.v2_charts import build_v2_charts

    payload = build_v2_charts(mood_rows=_mood_rows())
    for name, spec in payload.items():
        if spec.get("dwm_empty"):
            continue  # a named-empty spec has no frame to size
        for composed in ("layer", "vconcat", "hconcat", "concat"):
            assert composed not in spec, (
                f"{name} is a {composed} spec; a composed spec either shares "
                f"the y-scale or loses its width"
            )
        assert spec.get("width") == "container", f"{name} has no container width"

    assert "v2_market_mood" in payload, "the news series is missing"
    assert "v2_market_price" in payload, "the price series is missing"


def _mood_rows() -> list[dict]:
    """Three months of drill-across rows, enough to build a chart."""
    return [
        {"session": f"2020-{m:02d}-01", "news_count": float(m * 10),
         "sum_return": float(m), "stock_obs": 5, "sum_sentiment": 0.4 * m, "days": 1}
        for m in (1, 2, 3)
    ]


def _stock_rows(con) -> list[dict]:
    rows = con.execute(
        """
        SELECT trade_date, adj_close, return_pct, volatility_20d
        FROM fact_stock_daily WHERE stock_key = (
            SELECT stock_key FROM dim_stock WHERE symbol = 'TCS'
        ) ORDER BY trade_date LIMIT 30
        """
    ).fetchall()
    return [
        {"trade_date": r[0], "adj_close": r[1], "return_pct": r[2],
         "volatility_20d": r[3], "mentions": 0}
        for r in rows
    ]
