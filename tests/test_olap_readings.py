"""The OLAP readings and their charts.

The readings are the reason this module is tested rather than trusted. They are
sentences making specific claims about specific numbers, derived in Python from
the rows an operation just returned — which means they *can* be wrong, and four
of them were:

- `slice_year` assumed a `year_no` column. There isn't one; the rows are topic
  totals by group. It reported **"No rows matched" about a sixteen-row result.**
- `topic_mix` assumed `year_month`. It is yearly, and reported "across 1 months".
- `cube` read whichever text column came first, which was `topic_name`, and
  reported **"sentiment is mostly Entertainment"**.
- `drill_across` claimed volatility "starts once twenty days have accumulated"
  while reporting it present on every single row.

Every one of those was a confident sentence attached to real numbers, which is
the precise failure this project exists to prevent. Hence `test_a_reading_never_claims_an_empty_result`,
which is the check that would have caught all of them.
"""

from __future__ import annotations

import re

import pytest

from dwm.olap import readings
from dwm.olap.operations import DEFAULT_PARAMS, OPERATIONS


def _real_connection():
    from dwm.api import store

    try:
        con = store.connection()
        con.execute("SELECT 1 FROM fact_headline LIMIT 1").fetchall()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"needs the built warehouse (`dwm mine`): {exc}")
    return con


def _real_results():
    """Run every operation against the built warehouse, or skip.

    These need the real thing. The test fixtures build small CSV samples, and a
    reading is only meaningful against the shape the real rows have — a reader
    that works on a 200-row fixture and fails on 1,235 real ones has not been
    tested at all.
    """
    from dwm.api import store
    from dwm.olap import run_operation

    try:
        con = store.connection()
        con.execute("SELECT 1 FROM fact_headline LIMIT 1").fetchall()
    except Exception as exc:  # noqa: BLE001 - any failure means "not built"
        pytest.skip(f"needs the built warehouse (`dwm mine`): {exc}")

    out = {}
    for name in OPERATIONS:
        out[name] = run_operation(
            con, name, **dict(DEFAULT_PARAMS.get(name, {}))
        )
    return out


@pytest.fixture(scope="module")
def results():
    return _real_results()


# ---------------------------------------------------------------------------
# coverage
# ---------------------------------------------------------------------------


def test_every_operation_has_a_reader() -> None:
    assert set(OPERATIONS) <= set(readings.READINGS), (
        f"no reading written for {sorted(set(OPERATIONS) - set(readings.READINGS))}"
    )


def test_every_operation_returns_a_reading_and_a_chart(results) -> None:
    for name, result in results.items():
        assert result.get("reading"), f"{name} returned no reading"
        assert len(result["reading"]) > 60, f"{name} has a stub reading"
        chart = result.get("chart")
        assert chart, f"{name} returned no chart"
        assert not chart.get("dwm_reason"), (
            f"{name} has no chart: {chart.get('dwm_reason')}"
        )


def test_a_reading_never_claims_an_empty_result(results) -> None:
    """The check that would have caught all four wrong readings.

    "No rows matched" must mean the rows really are absent. A reader that reports
    it for a non-empty result is describing data it does not have, which is the
    failure this project is built to prevent.
    """
    offenders = [
        name for name, result in results.items()
        if result["rows"] and "No rows matched." in result["reading"]
    ]
    assert not offenders, (
        f"{offenders} report no rows for a non-empty result — the reader is "
        f"looking for a column that is not there"
    )


def test_a_reading_quotes_numbers_that_are_in_the_result(results) -> None:
    """Every figure in a reading must be derivable from its rows.

    Not merely *present in* them — a reading that aggregates rows quotes a sum,
    so `57,532` will not appear in any single row. The set checked here is every
    row value, every column sum, the row count and the distinct-value count of
    each column, which is the set of things a reading can legitimately state.
    """
    offenders = []
    for name, result in results.items():
        rows = result["rows"]
        if not rows:
            continue
        plausible: set[int] = set()
        for column in rows[0]:
            values = [r.get(column) for r in rows]
            plausible.update(
                int(v) for v in values
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            )
            plausible.add(len({str(v) for v in values}))
            plausible.add(
                int(sum(v for v in values
                        if isinstance(v, (int, float)) and not isinstance(v, bool)))
            )

        # Group sums, per numeric column. A cross-tabulation reading states
        # "October 2017 had 7,547 headlines", which is `headline_count` summed
        # over every row sharing that month — present neither in any single row
        # nor in the grand total, and not derivable by adding the columns
        # together either.
        numeric = [
            c for c in rows[0]
            if isinstance(rows[0].get(c), (int, float))
            and not isinstance(rows[0].get(c), bool)
        ]
        labels = [c for c in rows[0] if isinstance(rows[0].get(c), str)]
        for label in labels:
            for column in numeric:
                buckets: dict[str, float] = {}
                for row in rows:
                    key = str(row.get(label))
                    buckets[key] = buckets.get(key, 0.0) + float(
                        row.get(column) or 0
                    )
                plausible.update(int(v) for v in buckets.values())
            # And two-column group sums, for a pivot read by its row key.
            for second in numeric:
                if second == numeric[0]:
                    continue
                pairs: dict[str, float] = {}
                for row in rows:
                    key = str(row.get(label))
                    pairs[key] = pairs.get(key, 0.0) + float(
                        row.get(numeric[0]) or 0
                    )
                plausible.update(int(v) for v in pairs.values())

        plausible.add(len(rows))

        for figure in re.findall(r"\b\d{1,3}(?:,\d{3})+\b", result["reading"]):
            if int(figure.replace(",", "")) not in plausible:
                offenders.append(f"{name}: {figure}")
    assert not offenders, (
        "readings quote figures not derivable from their rows: "
        + "; ".join(offenders)
    )


def test_a_reading_does_not_contradict_itself(results) -> None:
    """No sentence may assert two incompatible things.

    Catches the volatility case: "volatility needs twenty days before it starts"
    alongside "volatility is available for every one of them".
    """
    offenders = []
    for name, result in results.items():
        text = result["reading"].lower()
        if "needs twenty days" in text and "every one of them" in text:
            offenders.append(f"{name}: volatility both warm and present")
        if "no rows matched" in text and result["rows"]:
            offenders.append(f"{name}: says empty, is not")
    assert not offenders, "\n".join(offenders)


# ---------------------------------------------------------------------------
# per-operation content
# ---------------------------------------------------------------------------


def test_cube_reads_the_sentiment_band_not_the_topic(results) -> None:
    """The operation is about sentiment bands, so the reading must be too.

    It read whichever string column came first — `topic_name` — and reported
    "sentiment is mostly Entertainment".

    Asserted as "names the leading band" rather than "names every band": a
    reading that lists the top one and the runner-up is right, and demanding all
    three would be asserting verbosity rather than correctness.
    """
    reading = results["cube"]["reading"].lower()
    rows = results["cube"]["rows"]
    topics = {str(r.get("topic_name")).lower() for r in rows}

    bands: dict[str, float] = {}
    for row in rows:
        band = row.get("sentiment_band")
        if band is not None:
            bands[str(band).lower()] = bands.get(str(band).lower(), 0.0) + float(
                row.get("headline_count") or 0
            )
    assert bands, "the cube has no banded rows to check against"

    leader = max(bands, key=bands.get)
    assert leader in reading, (
        f"the cube reading never names the leading band {leader!r}: {reading}"
    )

    # The regression itself: a topic named as though it were the sentiment.
    offenders = [t for t in topics if f"mostly {t}" in reading or f"is mostly {t}" in reading]
    assert not offenders, (
        f"the cube reading describes topic(s) {offenders} as if they were "
        f"sentiment bands"
    )


def test_cube_states_the_share_with_no_band(results) -> None:
    """A quarter of the cube rows have no band. That is a gap, not a category."""
    rows = results["cube"]["rows"]
    missing = [r for r in rows if r.get("sentiment_band") is None]
    if not missing:
        pytest.skip("this run has no missing bands")
    assert "no band" in results["cube"]["reading"].lower(), (
        "the cube reading does not mention the unbanded rows, so a gap in the "
        "data is invisible"
    )


def test_topic_mix_reports_years_not_months(results) -> None:
    """It assumed `year_month` and reported "across 1 months"."""
    reading = results["topic_mix"]["reading"]
    assert "year" in reading.lower(), reading
    assert not re.search(r"\b1 months\b", reading), reading
    years = {r.get("year_no") for r in results["topic_mix"]["rows"]}
    assert str(len(years)) in reading, (
        f"expected the count of years ({len(years)}) in: {reading}"
    )


def test_top_months_reports_the_spread_not_just_the_winner(results) -> None:
    """The ten busiest months sit within ~1% of each other. That is the finding."""
    reading = results["top_months"]["reading"].lower()
    assert "spread" in reading, (
        "the top-months reading names the busiest month but not how close the "
        f"rest are: {reading}"
    )


def test_market_reading_states_no_causation(results) -> None:
    """The one join that could be over-read must say what it does not show."""
    assert "causation" in results["drill_across"]["reading"].lower()


def test_slice_states_the_rate_is_recomputed(results) -> None:
    """Cubes store sums. A rate derived from them has to say so."""
    assert "recomputed" in results["slice"]["reading"].lower()


# ---------------------------------------------------------------------------
# robustness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_a_reading_survives_an_empty_result(operation: str) -> None:
    """A reading must never raise, and must never claim success on nothing."""
    text = readings.reading_for(operation, [], ["a", "b"], {}, {})
    assert isinstance(text, str) and text
    assert "no rows" in text.lower(), text


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_a_reading_survives_null_and_missing_fields(operation: str) -> None:
    """Half-null rows are the normal case, not an edge case."""
    rows = [{"a": None, "b": "x", "c": 1}, {"a": None, "b": None, "c": None}]
    text = readings.reading_for(operation, rows, ["a", "b", "c"], {}, {})
    assert isinstance(text, str) and text


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_a_reading_survives_wrongly_typed_rows(operation: str) -> None:
    """A reading that raises would take the result table down with it."""
    rows = [{"weird": object(), "headline_count": "not a number"}]
    text = readings.reading_for(operation, rows, ["weird", "headline_count"], {}, {})
    assert isinstance(text, str) and text
    assert "defect in the reading" not in text, (
        f"{operation} raised on malformed rows: {text}"
    )


def test_an_unregistered_operation_gets_a_neutral_description() -> None:
    text = readings.reading_for("not_an_operation", [{"a": 1}], ["a"], {}, {})
    assert "no written reading" in text


# ---------------------------------------------------------------------------
# the corpus constant
# ---------------------------------------------------------------------------


def test_the_corpus_constant_is_current() -> None:
    """`CLEAN_HEADLINES` is hardcoded so a reading needs no database.

    A stale constant would quietly make every percentage in the `slice` reading
    wrong, so it is asserted against the fact table.
    """
    con = _real_connection()
    actual = con.execute("SELECT count(*) FROM fact_headline").fetchone()[0]
    assert actual == readings.CLEAN_HEADLINES, (
        f"readings.CLEAN_HEADLINES is {readings.CLEAN_HEADLINES:,} but "
        f"fact_headline holds {actual:,}"
    )


# ---------------------------------------------------------------------------
# charts
# ---------------------------------------------------------------------------


def test_every_chart_fills_its_frame(results) -> None:
    """A width-less spec renders at Vega's 200px default — a small square."""
    import json

    for name, result in results.items():
        blob = json.dumps(result["chart"])
        assert '"width": "container"' in blob, f"{name} has no container width"
        assert "autosize" in blob, f"{name} has no autosize"


def test_a_chart_never_mixes_incommensurable_measures(results) -> None:
    """Measures four orders of magnitude apart cannot share a y-axis.

    `slice` and `drill_across` both carry headline counts in the thousands
    alongside sentiment means around 0.05 and index levels around 12,000. Drawn
    together the small series collapses onto the baseline and reads as a flat
    line at zero — which looks like a null result and is not one.

    So either the chart plots a single measure, or it says in its caption that it
    could not share the axis.
    """
    import json

    offenders = []
    for name, result in results.items():
        spec = result.get("chart") or {}
        if not spec or spec.get("dwm_reason"):
            continue
        # Collect the y fields actually plotted.
        plotted = re.findall(r'"field":\s*"([a-z_]+)"[^{}]*?"type":\s*"quantitative"',
                             json.dumps(spec))
        plotted = [p for p in set(plotted) if p in result["rows"][0]]
        numeric_rows = result["rows"][:200]
        maxima = []
        for column in plotted:
            values = [
                abs(float(r[column])) for r in numeric_rows
                if isinstance(r.get(column), (int, float))
                and not isinstance(r.get(column), bool)
            ]
            if values and max(values) > 0:
                maxima.append(max(values))
        if len(maxima) < 2:
            continue
        if max(maxima) / min(maxima) > 50:
            offenders.append(
                f"{name}: plots {len(maxima)} measures spanning "
                f"{max(maxima) / min(maxima):.0f}x"
            )
    assert not offenders, "\n".join(offenders)


def test_the_axis_never_plots_the_calendar_as_a_measure(results) -> None:
    """`year_no` and `month_no` are axis, not data.

    Left in the measure list they pushed several results over the
    multi-series threshold and got drawn as their own lines, so `top_months`
    rendered four series — two of them the year and the month number.
    """
    import json

    offenders = []
    for name, result in results.items():
        spec = result.get("chart") or {}
        if not spec or spec.get("dwm_reason"):
            continue
        # Only a *y* series is wrong. `roll_up` is year by topic, so year_no on
        # the x axis is the entire point of the operation.
        blob = json.dumps(spec)
        y_fields = re.findall(r'"y":\s*\{[^}]*?"field":\s*"([a-z_]+)"', blob)
        for column in ("year_no", "month_no"):
            if column in y_fields:
                offenders.append(f"{name} plots {column} as a y series")
    assert not offenders, "\n".join(offenders)


def test_headline_count_is_never_dropped(results) -> None:
    import json

    """It is the measure every cross-tabulation here is about.

    An early version excluded it from the measure list as a suspected
    duplicate, which meant the fallback plotted `sensational_count` instead —
    ten times smaller and about a different thing entirely.
    """
    for name in ("slice", "drill_across"):
        spec = results[name]["chart"]
        blob = json.dumps(spec)
        assert "headline_count" in blob, (
            f"{name} does not plot headline_count: {spec.get('dwm_caption')}"
        )


def test_a_time_axis_is_sorted_by_its_labels(results) -> None:
    """A nominal time axis with no sort defaults to data order.

    This is the RQ2 regression again: months sorted by their value, which put
    2020-05 beside 2016-12 and read as chronological when it was not.
    """
    import json

    offenders = []
    for name, result in results.items():
        spec = result.get("chart") or {}
        if not spec or spec.get("dwm_reason"):
            continue
        blob = json.dumps(spec)
        temporal = re.findall(r'"field":\s*"(year_month|year_no|month_no)"', blob)
        sorts = len(re.findall(r'"sort":\s*\[', blob))
        if temporal and not sorts:
            offenders.append(name)
    assert not offenders, f"time axes with no explicit sort: {offenders}"


def test_chart_column_names_exist_in_the_rows(results) -> None:
    """A chart referencing a column the result does not have draws nothing."""
    import json

    for name, result in results.items():
        spec = result.get("chart") or {}
        if not spec or spec.get("dwm_reason"):
            continue
        fields = set(re.findall(r'"field":\s*"([A-Za-z0-9_]+)"', json.dumps(spec)))
        present = set(result["rows"][0]) if result["rows"] else set()
        missing = fields - present
        assert not missing, f"{name} chart references absent columns: {missing}"
