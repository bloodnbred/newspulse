"""Phase U4 tests: the Cube Studio slicing engine.

`dwm.olap.operations` is a fixed catalogue of eight hand-written queries, so it
could be tested by comparing against known answers. Cube Studio builds its
statement from the request instead, and a statement built from a request is where
an OLAP tool normally starts lying to the reader. These tests are mostly about
the ways that happens, not about whether the numbers come out.

**The cubes hold sums and counts, never averages** (rule 16). So a "mean" is a
division evaluated in the same statement that grouped the rows. If it were
stored, the engine could not choose it; if the client computed it, a cell of 12
headlines would weigh the same as a cell of 700.

**Cells multiply across the axes, and ratios do not re-average.** The total row
has to come from a second statement, because summing per-cell means is
arithmetically wrong and looks right.

**Nothing from a request reaches the SQL text.** Field ids are dict keys into
whitelisted expressions; filter values are bound parameters.

**One cube per view.** `cube_day_topic` is one row per (day, topic);
`fact_stock_daily` is one row per (stock, session). A statement spanning both
would multiply every headline by the number of stocks that traded that day, so
it is refused rather than answered.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from dwm.olap import cube_studio as cs

WAREHOUSE = Path(__file__).resolve().parent.parent / "warehouse" / "dwm.duckdb"

pytestmark = pytest.mark.skipif(
    not WAREHOUSE.exists(),
    reason="warehouse not built; run `python -m dwm all` first",
)

WINDOW = ("2015-06-30", "2020-06-30")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(str(WAREHOUSE), read_only=True)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# the shelf
# ---------------------------------------------------------------------------


def test_every_field_explains_itself(con) -> None:
    """The shelf is a teaching surface, not a list of column names.

    A field with an empty description is a column name in a drag handle, and a
    reader who has to guess whether "risk_signal_rate" is a count or a
    percentage will guess wrong and then trust the wrong number.
    """
    shelf = cs.shelf()
    assert len(shelf["cubes"]) == 2
    for cube in shelf["cubes"]:
        assert cube["grain"], f"{cube['id']} has no stated grain"
        for f in (*cube["dimensions"], *cube["measures"]):
            assert f["description"].strip(), f"{cube['id']}.{f['id']} has no description"
            assert f["unit"] in {"label", "year", "count", "ratio", "pct", "score", "points"}
            assert f["kind"] in {"dimension", "measure"}


def test_every_dimension_is_filterable_and_every_measure_is_groupable(con) -> None:
    """A dimension the reader cannot filter by is decoration.

    And every measure must be an aggregate: a bare column inside a GROUP BY is
    a constant, so it would report the same number in every cell of the axis and
    look like a finding.
    """
    aggregates = ("sum(", "count(", "avg(", "min(", "max(", "stddev")
    for cube in cs.CUBES.values():
        for f in cube.fields:
            if f.kind == "dimension":
                assert f.id, "an unnamed dimension"
                continue
            if f.id in cs.COMPUTED_MEASURES:
                assert f.derived, f"{f.id} is computed in Python; say so"
                continue
            assert any(a in f.expr.lower() for a in aggregates), (
                f"{cube.id}.{f.id} = {f.expr!r} is not an aggregate; within a "
                "GROUP BY a bare column is a constant, not a measure"
            )
            # And it must be reachable: asking for it alone has to build.
            spec = cs.parse_spec(
                {"cube": cube.id, "rows": ["year"], "measures": [f.id]}
            )
            sql, _ = cs.build_sql(spec, WINDOW, totals=False)
            assert f"AS {f.id}" in sql, f"{cube.id}.{f.id} is not in its own statement"


def test_the_two_cubes_have_different_grains(con) -> None:
    """The refusal in `parse_spec` is only fair if the grains really differ."""
    grains = {c.grain for c in cs.CUBES.values()}
    assert len(grains) == 2, "the cubes share a grain, so refusing to mix them is wrong"
    assert cs.NEWS_CUBE.is_cube, "the news source is cube_day_topic"
    assert not cs.MARKET_CUBE.is_cube, "the market source is a fact, not a cube"


# ---------------------------------------------------------------------------
# ratios, and the one that catches everyone
# ---------------------------------------------------------------------------


def test_a_mean_is_a_division_evaluated_over_the_group(con) -> None:
    """Rule 16. The cube stores `sum_sentiment_compound` and `headline_count`;
    the mean is their quotient inside the same statement that grouped them."""
    sql, _ = cs.build_sql(
        cs.parse_spec(
            {"cube": "news", "rows": ["year"], "measures": ["news_count", "mean_sentiment"]}
        ),
        WINDOW,
        totals=False,
    )
    assert "sum(c.sum_sentiment_compound)" in sql
    assert "nullif(sum(c.headline_count), 0)" in sql
    assert "avg(" not in sql.lower(), (
        "the statement averages an average, or averages a stored mean; the cube "
        "has no mean column and must not be asked for one"
    )


def test_the_total_row_is_not_the_sum_of_the_cells(con) -> None:
    """The trap: summing per-cell means is arithmetically wrong and looks right.

    A month with 12 headlines and a month with 700 would contribute equally to
    a total formed by adding the two means. So the total comes from its own
    statement, over the same filter, with no axes.
    """
    spec = cs.parse_spec(
        {"cube": "news", "rows": ["month"], "measures": ["news_count", "mean_sentiment"]}
    )
    result = cs.run(con, spec, WINDOW)
    cells = result.rows
    assert len(cells) > 1

    total = result.totals["mean_sentiment"]
    naive = sum(float(r["mean_sentiment"]) for r in cells) / len(cells)
    weighted = sum(
        float(r["mean_sentiment"]) * int(r["news_count"]) for r in cells
    ) / sum(int(r["news_count"]) for r in cells)

    assert abs(total - naive) > 1e-6, (
        "the total equals the mean of the cell means, so it is being computed "
        "somewhere that has already lost the counts"
    )
    assert abs(total - weighted) < 1e-6, (
        "the total is not the headline-weighted mean, so the totals statement "
        "is not aggregating the same rows the cells are"
    )
    # And the headline count must reconcile exactly, or the two statements are
    # reading different data.
    assert int(result.totals["news_count"]) == sum(int(r["news_count"]) for r in cells)


def test_a_banded_dimension_is_derived_and_says_so(con) -> None:
    """Sentiment level is a CASE over the grouped mean, not a stored column."""
    band = cs.NEWS_CUBE.by_id()["sentiment_level"]
    assert band.derived
    assert band.band_of == "mean_sentiment"
    assert "CASE" in band.band

    spec = cs.parse_spec(
        {"cube": "news", "rows": ["year", "sentiment_level"], "measures": ["news_count"]}
    )
    result = cs.run(con, spec, WINDOW)
    bands = {r["sentiment_level"] for r in result.rows}
    assert bands <= {"Positive", "Negative", "Neutral"}, f"unexpected bands {bands}"
    assert bands, "the band produced nothing"
    # The measure the band is defined over must not leak into the result.
    assert "mean_sentiment" not in result.columns, (
        "the measure behind a band was selected but not requested; it should "
        "leave with the statement"
    )
    # And the band must not have collapsed the year axis.
    assert len({r["year"] for r in result.rows}) > 1


def test_a_banded_dimension_works_without_its_measure_in_values(con) -> None:
    """A reader who puts only the band on an axis did not ask for the measure.

    The first version raised `Referenced column "mean_sentiment" not found`,
    because the band is computed in an outer query over the grouped rows and
    the measure had not been selected inside.
    """
    spec = cs.parse_spec({"cube": "news", "rows": ["sentiment_level"], "measures": ["news_count"]})
    result = cs.run(con, spec, WINDOW)
    assert result.rows
    assert all("mean_sentiment" not in r for r in result.rows)


def test_news_heat_is_a_z_over_the_view_not_the_cube(con) -> None:
    """A z has no meaning without the set it is scored in.

    The measure is computed in Python over the returned cells, which is why it
    cannot be in the statement. What matters to a reader is that the result says
    so, because otherwise two views' heat values look comparable and are not.
    """
    spec = cs.parse_spec(
        {"cube": "news", "rows": ["month"], "measures": ["news_count", "news_heat"]}
    )
    result = cs.run(con, spec, WINDOW)
    zs = [r["news_heat"] for r in result.rows if r["news_heat"] is not None]
    assert len(zs) == len(result.rows)
    assert abs(sum(zs)) < 0.5, "a z-score column whose mean is not near zero was not standardised"
    assert any(
        "THIS result" in n for n in result.notes
    ), "the caption must say the z is relative to this view"


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------


def test_a_mixed_cube_spec_is_refused_with_the_reason(con) -> None:
    with pytest.raises(cs.CubeError) as caught:
        cs.parse_spec({"cube": "news", "rows": ["sector"], "measures": ["news_count"]})
    exc = caught.value
    assert exc.status == 409
    message = str(exc)
    assert "sector" in message and "Market" in message
    assert "once for each stock" in message, (
        "the refusal has to say why, or it reads as an arbitrary limit"
    )


def test_mixing_cubes_would_actually_inflate_the_counts(con) -> None:
    """The refusal is only justified if the fan-out is real. Measure it.

    A day's headlines joined to that day's stocks: the count comes out at the
    headline total multiplied by the stocks that traded. If this ever stops
    being true the refusal should be revisited rather than kept as folklore.
    """
    rows = con.execute(
        """
        SELECT count(*) AS joined_rows,
               (SELECT count(*) FROM fact_headline WHERE in_window) AS headlines
        FROM fact_headline h
        JOIN fact_stock_daily p ON p.date_key = h.effective_trading_date_key
        WHERE h.in_window
        """
    ).fetchone()
    assert rows[0] > rows[1], "the fan-out did not happen; re-check the refusal's reasoning"


def test_a_measure_cannot_be_dropped_on_an_axis(con) -> None:
    with pytest.raises(cs.CubeError) as caught:
        cs.parse_spec({"cube": "news", "rows": ["news_count"], "measures": ["news_count"]})
    assert "Values" in str(caught.value), "say where the field does belong"


def test_an_unknown_field_says_which_cube_was_searched(con) -> None:
    """A reader who typed `sector` into the news cube should be told which cube
    was searched, because the shelf they were looking at is grouped by cube."""
    with pytest.raises(cs.CubeError) as caught:
        cs.parse_spec({"cube": "news", "rows": ["phase_of_moon"], "measures": ["news_count"]})
    message = str(caught.value)
    assert "phase_of_moon" in message
    assert "News" in message
    assert "SELECT" not in message and "FROM" not in message, (
        "a refusal should not quote SQL back at the reader"
    )


def test_a_view_with_no_axes_is_refused(con) -> None:
    """A view with no dimensions is one number, and `/ui/home` is where single
    numbers live. Answering it here would give the reader two places to read the
    same figure, which is two places to read it differently."""
    with pytest.raises(cs.CubeError):
        cs.parse_spec({"cube": "news", "rows": [], "columns": [], "measures": ["news_count"]})


def test_too_many_dimensions_are_refused_before_the_query(con) -> None:
    """Refused by validation, so the cost is not paid discovering it."""
    with pytest.raises(cs.CubeError) as caught:
        cs.parse_spec(
            {
                "cube": "news",
                "rows": ["year", "month"],
                "columns": ["topic", "weekday"],
                "measures": ["news_count"],
            }
        )
    assert "multiply" in str(caught.value)


# ---------------------------------------------------------------------------
# nothing from the request reaches the SQL text
# ---------------------------------------------------------------------------


def test_a_filter_value_is_bound_not_interpolated(con) -> None:
    """The injection attempt is inert and the table survives.

    A filter value containing a quote and a statement must come back as a value
    that matches nothing. If it were interpolated, this would raise or - worse -
    succeed.
    """
    payload = "Business'); DROP TABLE fact_headline; --"
    spec = cs.parse_spec(
        {
            "cube": "news",
            "rows": ["topic"],
            "measures": ["news_count"],
            "filters": {"topic": [payload]},
        }
    )
    sql, params = cs.build_sql(spec, WINDOW, totals=False)
    assert payload not in sql, "a filter value reached the statement as text"
    assert payload in params, "a filter value was not bound as a parameter"
    result = cs.run(con, spec, WINDOW)
    assert result.rows == [], "the hostile filter matched something"
    assert con.execute("SELECT count(*) FROM fact_headline").fetchone()[0] > 0


def test_the_placeholders_line_up_with_the_values(con) -> None:
    """A mismatch here would silently filter by the wrong column."""
    spec = cs.parse_spec(
        {
            "cube": "news",
            "rows": ["topic"],
            "measures": ["news_count"],
            "filters": {"topic": ["Business", "Sports"]},
        }
    )
    sql, params = cs.build_sql(spec, WINDOW, totals=False)
    # Two window bounds plus one per filter value.
    assert sql.count("?") == 2 + 2
    assert len(params) == 4


def test_a_filter_cannot_name_a_measure(con) -> None:
    """Filtering on an aggregate is a different question, and a meaningless one
    at this grain - so it is refused rather than silently ignored."""
    with pytest.raises(cs.CubeError):
        cs.parse_spec(
            {
                "cube": "news",
                "rows": ["topic"],
                "measures": ["news_count"],
                "filters": {"news_count": [1]},
            }
        )


# ---------------------------------------------------------------------------
# the numbers are the corpus's numbers
# ---------------------------------------------------------------------------


def test_the_news_cube_total_is_the_corpus_total(con) -> None:
    spec = cs.parse_spec({"cube": "news", "rows": ["year"], "measures": ["news_count"]})
    result = cs.run(con, spec, WINDOW)
    assert int(result.totals["news_count"]) == 1_113_427, (
        "the headline total drifted from the known window total, so the cube is "
        "reading something other than the in-window corpus"
    )


def test_the_market_cube_excludes_outliers(con) -> None:
    """Rule 14: an outlier is kept in the table and left out of the statistics."""
    outliers = con.execute(
        "SELECT count(*) FROM fact_stock_daily WHERE is_outlier"
    ).fetchone()[0]
    assert outliers, "no outliers in the warehouse; this test is vacuous"

    spec = cs.parse_spec(
        {"cube": "market", "rows": ["year"], "measures": ["observations"]}
    )
    result = cs.run(con, spec, WINDOW)
    total = int(result.totals["observations"])
    with_outliers = con.execute(
        "SELECT count(*) FROM fact_stock_daily p JOIN dim_date d USING (date_key) "
        "WHERE d.full_date BETWEEN ? AND ?",
        list(WINDOW),
    ).fetchone()[0]
    assert total < with_outliers, "outliers are in the statistics"
    assert total > 0


def test_a_null_sort_goes_last_in_both_directions(con) -> None:
    """A blank cell sorted to the top would sit exactly where a reader looks
    for the peak, which is the one place it must not be."""
    spec = cs.parse_spec(
        {
            "cube": "news",
            "rows": ["topic"],
            "measures": ["mean_sentiment"],
            "orderBy": "mean_sentiment",
            "descending": True,
        }
    )
    descending = cs.run(con, spec, WINDOW).rows
    spec.descending = False
    ascending = cs.run(con, spec, WINDOW).rows
    assert descending and ascending
    for rows in (descending, ascending):
        seen_none = False
        for row in rows:
            if row["mean_sentiment"] is None:
                seen_none = True
            else:
                assert not seen_none, "a null appeared above a real value"


def test_the_result_is_truncated_loudly(con) -> None:
    """A silent truncation is a reader drawing a conclusion from a subset while
    believing they are looking at the whole."""
    spec = cs.parse_spec(
        {"cube": "news", "rows": ["topic"], "measures": ["news_count"], "limit": 3}
    )
    result = cs.run(con, spec, WINDOW)
    assert len(result.rows) == 3
    assert any("Only the first" in n for n in result.notes), result.notes


# ---------------------------------------------------------------------------
# the endpoints
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from dwm.api.app import app

    with TestClient(app) as c:
        yield c


def test_the_three_cube_endpoints_answer(client) -> None:
    assert client.get("/ui/cube/schema").status_code == 200
    query = client.post(
        "/ui/cube/query",
        json={"cube": "news", "rows": ["year"], "measures": ["news_count"]},
    )
    assert query.status_code == 200
    assert client.get("/ui/cube/headlines", params={"topic": "Business"}).status_code == 200


def test_a_cube_query_is_an_envelope_carrying_the_sql_it_ran(client) -> None:
    """The "How this was computed" drawer shows `meta.sql`.

    The engine builds its own statement rather than going through `_Queries`, so
    this is the only thing standing between the drawer and a blank panel. Both
    statements must be there: the cells and the totals, because they disagree by
    design and a reader comparing them should be able to see why.
    """
    response = client.post(
        "/ui/cube/query",
        json={
            "cube": "news",
            "rows": ["topic"],
            "columns": ["year"],
            "measures": ["news_count", "mean_sentiment"],
        },
    )
    meta = response.json()["meta"]
    assert meta["concept"]
    assert "select" in meta["sql"].lower()
    assert "totals" in meta["sql"].lower(), (
        "the totals statement is missing; the drawer would show only the cells "
        "and the total row would have no provenance"
    )
    assert "cube_day_topic" in meta["sql"]


def test_a_refused_spec_is_a_4xx_with_a_reason_not_a_500(client) -> None:
    """A reader who typed the wrong field must be told which field and why."""
    response = client.post(
        "/ui/cube/query",
        json={"cube": "news", "rows": ["sector"], "measures": ["news_count"]},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "sector" in detail
    assert "SELECT" not in detail


def test_a_refused_spec_is_not_cached(client) -> None:
    """Otherwise the correction a reader makes is answered from the refusal."""
    bad = client.post(
        "/ui/cube/query", json={"cube": "news", "rows": ["topic"], "measures": ["nope"]}
    )
    assert "no-store" in bad.headers.get("cache-control", "")


def test_the_headline_list_is_capped_by_the_api(client) -> None:
    """Rule 21 caps it at 50, and the cap belongs here: a limit the browser can
    raise is a limit that will be raised."""
    assert client.get("/ui/cube/headlines", params={"limit": 50}).status_code == 200
    assert client.get("/ui/cube/headlines", params={"limit": 500}).status_code == 422
    assert client.get("/ui/cube/headlines", params={"limit": 0}).status_code == 422


def test_the_headlines_return_the_text_they_exist_to_show(client) -> None:
    """A list of ids and scores is not something a reader can read."""
    rows = client.get(
        "/ui/cube/headlines", params={"topic": "Business", "year": 2020, "limit": 5}
    ).json()["data"]
    assert rows
    for row in rows:
        assert row["headline_text"].strip(), "a headline row with no headline"
        assert row["topic"] == "Business"


def test_the_headline_ranking_says_it_is_not_representative(client) -> None:
    """They are the most sensational rows in the cell. A reader who thinks they
    are a sample would draw the wrong conclusion about the cell."""
    note = client.get("/ui/cube/headlines", params={"topic": "Sports"}).json()["meta"]["note"]
    assert "not a sample" in note or "not a random" in note


def test_the_schema_response_ships_the_operation_chips(client) -> None:
    """The six operations are part of the page, and the explanation for each is
    part of the teaching, so they travel with the shelf rather than being
    hard-coded in the component."""
    data = client.get("/ui/cube/schema").json()["data"]
    ops = data["operations"]
    assert [o["id"] for o in ops] == [
        "slice", "dice", "roll_up", "drill_down", "pivot", "cube",
    ]
    for op in ops:
        assert op["blurb"].strip() and op["hint"].strip()
        assert op["zones"], f"{op['id']} does not say which zones it uses"


def test_the_shelf_states_the_survivorship_caveat(client) -> None:
    """The market cube's sector dimension is today's Nifty 50 applied to a
    2015-2020 window. That has to travel with the data, not with a footnote
    nobody opens."""
    data = client.get("/ui/cube/schema").json()["data"]
    market = next(c for c in data["cubes"] if c["id"] == "market")
    joined = " ".join(market["notes"]).lower()
    assert "survivorship" in joined or "since been removed" in joined
    news = next(c for c in data["cubes"] if c["id"] == "news")
    assert "market-report" in " ".join(news["notes"]).lower(), (
        "the news cube carries market-report headlines and cannot filter them; "
        "the shelf has to say so"
    )
