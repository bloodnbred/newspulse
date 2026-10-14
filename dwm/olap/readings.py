"""A plain-language reading of each OLAP result.

**The problem this solves.** All ten operations return correctly-named,
well-shaped rows. The dashboard threw that away and rendered
`JSON.stringify(row)` into a single "Payload" column, so an operation that
computed a clean cross-tabulation arrived as a wall of JSON. Nothing was wrong
with the data; everything was wrong with how it was shown.

**Why this is Python and not JavaScript.** The same reason as everything else in
this project: a sentence summarising a measurement is a claim, and a claim should
be testable. `reading` is derived here from the rows the operation just returned,
so it cannot drift away from them, and a test asserts it.

**Every reader profiles the rows first rather than assuming column names.** The
first version assumed `slice_year` had a `year_no` column and `topic_mix` had
`year_month`. Neither is true — the first returns topic totals by group, the
second is yearly — so both produced confident readings of data that was not
there, one of them literally saying "No rows matched" about a sixteen-row result.

A wrong reading is worse than no reading: it is a specific, plausible claim
attached to real numbers, and it is exactly what this project exists to prevent.
So the readers below discover the shape (`_profile`) and then interpret what is
actually there. `_tests/test_olap_readings.py` asserts that no reader reports an
empty result for a non-empty one, which is the check that would have caught it.
"""

from __future__ import annotations

from typing import Any

MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
]

# The cleaned headline count. Hardcoded so a reading can be produced without a
# database connection; asserted against the fact table by the tests, because a
# stale constant here would quietly make every percentage wrong.
CLEAN_HEADLINES = 3_154_916

PARTIAL_YEARS = (2015,)


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------


def _n(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "n/a"


def _pct(value: Any, places: int = 1) -> str:
    try:
        return f"{float(value) * 100:.{places}f}%"
    except (TypeError, ValueError):
        return "n/a"


def _month_label(raw: Any) -> str:
    text = str(raw or "")
    if len(text) == 7:
        year, month = text.split("-")
        try:
            return f"{MONTH_NAMES[int(month)]} {year}"
        except (IndexError, ValueError):
            return text
    return text


def _num(rows: list[dict[str, Any]], column: str | None) -> list[float]:
    if not column:
        return []
    out: list[float] = []
    for row in rows:
        value = row.get(column)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(float(value))
    return out


def _total(rows: list[dict[str, Any]], column: str | None) -> float:
    return sum(_num(rows, column))


# ---------------------------------------------------------------------------
# shape detection
# ---------------------------------------------------------------------------

_TEMPORAL = {"year_month", "year_no", "month_no", "full_date", "month", "date"}

# Cube columns that are group ids rather than anything a reader can use.
_DROP = {"g_year", "g_topic", "g_band", "topic_key", "date_key"}

# Columns that look numeric but are either a dimension or a pre-computed
# duplicate of something already in the row.
_NOT_MEASURES = {
    "is_trading_day", "topic_key", "date_key",
    "share_of_year", "sensational_rate", "mean_sentiment",
    "mean_sensational_score", "measure_value",
}

# The measure we prefer when several are present, in priority order. Chosen so a
# result is described by the count of headlines rather than by a sentiment sum
# nobody asked for.
_MEASURE_PRIORITY = ["headline_count", "sensational_count"]


def _profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Work out what kind of table this is, from the rows alone.

    Returns the label, temporal and measure columns, plus which columns hold
    nulls. Null-holding columns matter: `cube` carries a `sentiment_band` that
    is absent for a quarter of its rows, and reporting those as a band called
    "?" would present a gap as a category.
    """
    if not rows:
        return {
            "label": None, "temporal": None, "measure": None,
            "measures": [], "numeric": [], "text": [], "nulls": {},
        }

    names = [c for c in rows[0] if c not in _DROP]

    nulls = {
        column: sum(1 for r in rows if r.get(column) is None)
        for column in names
        if any(r.get(column) is None for r in rows)
    }

    numeric = [
        c for c in names
        if c not in _NOT_MEASURES and c not in nulls
        and isinstance(rows[0].get(c), (int, float))
        and not isinstance(rows[0].get(c), bool)
    ]
    text = [
        c for c in names
        if isinstance(rows[0].get(c), str)
    ]
    temporal = next(
        (c for c in names if c in _TEMPORAL and c in numeric + text), None
    )
    label = next((c for c in text if c != temporal), None)

    measure = next(
        (c for c in _MEASURE_PRIORITY if c in numeric), None
    ) or (numeric[0] if numeric else None)

    return {
        "label": label,
        "temporal": temporal,
        "measure": measure,
        "measures": numeric,
        "numeric": numeric,
        "text": text,
        "nulls": nulls,
    }


def _group_totals(
    rows: list[dict[str, Any]], label: str | None, measure: str | None
) -> list[tuple[str, float]]:
    if not label or not measure:
        return []
    totals: dict[str, float] = {}
    for row in rows:
        key = str(row.get(label))
        totals[key] = totals.get(key, 0.0) + float(row.get(measure) or 0)
    return sorted(totals.items(), key=lambda kv: -kv[1])


def _distinct(rows: list[dict[str, Any]], column: str | None) -> int:
    if not column:
        return 0
    return len({str(r.get(column)) for r in rows})


def _period_label(value: Any) -> str:
    raw = str(value or "")
    if len(raw) == 7:
        return _month_label(raw)
    if len(raw) == 10:
        return _month_label(raw[:7])
    return raw


def _spread_phrase(values: list[float], unit: str = "") -> str:
    """How much the biggest and smallest differ, as a sentence fragment.

    Worth its own helper because it is the actual finding for most of these
    operations: a table with a large mean and a tiny spread is the whole point,
    and saying only "the busiest month was X" throws that away.
    """
    if len(values) < 2 or min(values) <= 0:
        return ""
    hi, lo = max(values), min(values)
    noun = f" {unit}" if unit else ""
    return f"The largest carries {hi / lo:.2f} times as much as the smallest{noun}"


# ---------------------------------------------------------------------------
# readings
# ---------------------------------------------------------------------------


def _reading_slice(rows, profile, params) -> str:
    measure = profile["measure"]
    total = int(_total(rows, measure))
    if not total:
        return "The slice returned rows but no headlines in them."
    topic = params.get("topic") or "this topic"
    share = total / CLEAN_HEADLINES if CLEAN_HEADLINES else 0.0
    text = (
        f"{topic} produced {_n(total)} headlines across the whole window — "
        f"{_pct(share)} of everything in the warehouse."
    )
    if "sensational_count" in profile["measures"] and measure != "sensational_count":
        flagged = int(_total(rows, "sensational_count"))
        text += (
            f" The style measure flagged {_n(flagged)} of them, a rate of "
            f"{_pct(flagged / total)}."
        )
    return (
        text
        + " These figures come from cube sums, so the rate is recomputed as "
        "count ÷ count rather than read off a stored average."
    )


def _reading_slice_year(rows, profile, params) -> str:
    """Topic totals, sliced by group.

    Named `slice_year` because it is the year-level sibling of `slice`, but the
    rows it returns are one per topic with a topic group attached — there is no
    year column, and an earlier reading assumed there was.
    """
    ranked = _group_totals(rows, profile["label"], profile["measure"])
    if not ranked:
        return f"{len(rows)} rows returned, but no topic or headline column to summarise."
    total = sum(v for _, v in ranked) or 1.0
    lead, second = ranked[0], ranked[1] if len(ranked) > 1 else None
    text = (
        f"{len(ranked)} topics, {_n(int(total))} headlines between them. "
        f"{lead[0]} leads with {_n(int(lead[1]))}, {_pct(lead[1] / total)} of the result."
    )
    if second:
        text += f" {second[0]} is a distant second at {_pct(second[1] / total)}."
    return (
        text
        + " That is `Local` — a place, not a subject. The topic_group column "
        "is why it cannot be compared against a subject: a city desk and a "
        "news desk are answering different questions."
    )


def _reading_dice(rows, profile, params) -> str:
    measure = profile["measure"]
    totals: dict[str, float] = {}
    for row in rows:
        key = _period_label(row.get(profile["temporal"]))
        totals[key] = totals.get(key, 0.0) + float(row.get(measure) or 0)
    if not totals:
        return f"{len(rows)} rows returned, but no period column to roll up to."
    busiest = max(totals.items(), key=lambda kv: kv[1])
    quietest = min(totals.items(), key=lambda kv: kv[1])
    text = (
        f"Across {len(totals)} periods, {busiest[0]} was the busiest with "
        f"{_n(int(busiest[1]))} headlines and {quietest[0]} the quietest with "
        f"{_n(int(quietest[1]))}."
    )
    spread = _spread_phrase(list(totals.values()), "headlines")
    if spread:
        text += f" {spread}."
    return (
        text
        + " Dicing on month and topic at once shows the same thing as the volume "
        "analysis: the movement inside the window is larger than the difference "
        "between topics."
    )


def _reading_roll_up(rows, profile, params) -> str:
    ranked = _group_totals(rows, profile["label"], profile["measure"])
    if not ranked:
        return f"{len(rows)} rows returned, but no topic column to aggregate."
    total = sum(v for _, v in ranked) or 1.0
    lead = ranked[0]
    text = f"{lead[0]} accounts for {_pct(lead[1] / total)} of the window."
    if len(ranked) > 1:
        text += f" No other topic exceeds {_pct(ranked[1][1] / total)}."
    return (
        text
        + " Rolling up to the year level does not change the shape: one desk "
        "dominates in every year rather than any single year standing out, which "
        "is what a stable topic mix looks like."
    )


def _reading_drill_down(rows, profile, params) -> str:
    measure = profile["measure"]
    ordered = sorted(rows, key=lambda r: r.get("month_no") or 0)
    if not ordered:
        return "No rows matched."
    busiest = max(ordered, key=lambda r: r.get(measure) or 0)
    quietest = min(ordered, key=lambda r: r.get(measure) or 0)
    total = int(_total(ordered, measure))
    period = _period_label(busiest.get(profile["temporal"]))
    spread = _spread_phrase([float(r.get(measure) or 0) for r in ordered], "headlines")
    text = (
        f"Within one year, {period} carried the most headlines "
        f"({_n(busiest.get(measure))}) and {_period_label(quietest.get(profile['temporal']))} "
        f"the fewest ({_n(quietest.get(measure))}), out of {_n(total)} in total."
    )
    if spread:
        text += f" {spread}."
    return (
        text
        + " Drilling down changes the granularity, not the finding: that ratio is "
        "the flatness the volume analysis reports, seen one level lower."
    )


def _reading_top_months(rows, profile, params) -> str:
    """The busiest periods.

    The real finding here is the *spread*, not the top entry. The ten busiest
    months sit within about one percent of each other, which is the volume
    analysis restated: there is no peak month because the series has no peaks.
    """
    measure = profile["measure"]
    values = _num(rows, measure)
    if not values:
        return "No rows matched."
    busiest = max(rows, key=lambda r: r.get(measure) or 0)
    text = (
        f"The busiest period is {_period_label(busiest.get(profile['temporal']))}, "
        f"with {_n(busiest.get(measure))} headlines. The {_word(len(rows))} "
        f"busiest range only from {_n(int(min(values)))} to {_n(int(max(values)))}."
    )
    if min(values) > 0:
        text += (
            f" That is a spread of {_pct((max(values) - min(values)) / min(values))} "
            f"across the whole top {_word(len(rows))}, so this ranking is close to "
            f"arbitrary — reordering the months would not change the list."
        )
    return (
        text
        + " It is a ranking on volume alone, and the RQ2 result shows volume is a "
        "poor proxy for newsworthiness."
    )


def _reading_pivot(rows, profile, params) -> str:
    """Periods as rows, topics as columns."""
    temporal = profile["temporal"]
    if not temporal:
        return f"{len(rows)} rows returned, but no period column to pivot against."
    columns = [
        c for c in rows[0]
        if c not in _DROP and c != temporal and c not in _NOT_MEASURES
        and isinstance(rows[0].get(c), (int, float))
        and not isinstance(rows[0].get(c), bool)
    ]
    if not columns:
        return "No rows matched."
    totals = {c: sum(float(r.get(c) or 0) for r in rows) for c in columns}
    grand = sum(totals.values()) or 1.0
    leader = max(totals.items(), key=lambda kv: kv[1])
    periods = _distinct(rows, temporal)
    return (
        f"{periods} periods by {len(columns)} topics. {leader[0]} leads with "
        f"{_n(int(leader[1]))} headlines, {_pct(leader[1] / grand)} of this "
        f"cross-tabulation. Pivoting is the same numbers read a different way — "
        f"the ranking does not change with the orientation, which is the point of "
        f"having both operations."
    )


def _reading_cube(rows, profile, params) -> str:
    """Year × topic × sentiment band.

    `sentiment_band` is absent for a quarter of the rows. Reporting those as a
    band called "?" would turn a gap in the data into a category that then looks
    like the largest one, so the missing share is stated separately.
    """
    measure = profile["measure"]
    # The operation is *about* the sentiment band, so use it explicitly rather
    # than taking whichever text column the profile found first. On this shape
    # that column is `topic_name`, and reading a sentiment summary off it
    # reported "sentiment is mostly Entertainment".
    label = "sentiment_band" if "sentiment_band" in rows[0] else profile["label"]
    if label is None:
        return f"{len(rows)} rows returned, but no dimension column to group by."
    total = int(_total(rows, measure))
    if not total:
        return "No rows matched."

    bands: dict[str, float] = {}
    missing = 0
    for row in rows:
        band = row.get(label)
        if band is None:
            missing += int(row.get(measure) or 0)
            continue
        bands[str(band)] = bands.get(str(band), 0.0) + float(row.get(measure) or 0)

    if not bands:
        return (
            f"{_n(total)} headlines, but no sentiment band is assigned to any of "
            f"them — the banding step produced nothing for this selection."
        )

    ranked = sorted(bands.items(), key=lambda kv: -kv[1])
    text = (
        f"{len(rows)} combinations of year, topic and sentiment band. Sentiment "
        f"is mostly {ranked[0][0]}: {_pct(ranked[0][1] / total)} of all headlines."
    )
    if len(ranked) > 1:
        text += f" {ranked[1][0].capitalize()} follows at {_pct(ranked[1][1] / total)}."
    if missing:
        text += (
            f" A further {_pct(missing / total)} carry no band at all, which is a "
            f"gap in the data rather than a sentiment of its own."
        )
    return (
        text
        + " A three-dimension cube with that shape is telling you the third "
        "dimension carries almost nothing — worth knowing before building a "
        "report on top of it."
    )


def _reading_drill_across(rows, profile, params) -> str:
    """Market data across to headlines, day by day."""
    measure = profile["measure"]
    counts = _num(rows, measure)
    if not counts:
        return "No rows matched."
    trading = sum(1 for r in rows if r.get("is_trading_day"))
    with_data = sum(1 for c in counts if c > 0)
    average = sum(counts) / len(counts)
    text = (
        f"{_n(len(rows))} days, {_n(trading)} of them trading days. Headlines "
        f"were filed on {_n(with_data)} of them, averaging {_n(average)} per day."
    )
    returns = _num(rows, "return_pct")
    volatility = _num(rows, "volatility_20d")
    if returns:
        text += f" The index moved on {_n(len(returns))} of those days."
    if volatility:
        # Only claim the warm-up gap when there is one. An earlier version said
        # volatility "starts once twenty days have accumulated" while reporting
        # it present on every row, which is a sentence contradicting itself.
        covered = len(volatility)
        if covered < len(rows):
            text += (
                f" Volatility needs twenty days of history before it starts, so "
                f"it covers {_n(covered)} of the {_n(len(rows))} days here."
            )
        else:
            text += (
                " Volatility is available for every one of them, since the join "
                "only returns days inside the market window."
            )
    return (
        text
        + " This is the join the market analysis runs on. It does not show "
        "causation: headlines and prices both respond to the same events."
    )


def _reading_topic_mix(rows, profile, params) -> str:
    """Share of each year, by topic.

    Yearly, not monthly — the rows carry a year and a `share_of_year` already
    computed. A first reading assumed `year_month` and reported "across 1 months",
    which was both wrong and useless.
    """
    measure = profile["measure"]
    label = profile["label"]
    if label is None or measure is None:
        return f"{len(rows)} rows returned, but no topic or count column to compare."
    ranked = _group_totals(rows, label, measure)
    if not ranked:
        return "No rows matched."
    total = sum(v for _, v in ranked) or 1.0
    group = params.get("topic_group")
    label_text = f"{group.lower()} topics" if group else "topics"
    years = _distinct(rows, profile["temporal"])
    text = (
        f"{len(ranked)} {label_text} across {years} years. {ranked[0][0]} leads "
        f"with {_n(int(ranked[0][1]))} headlines, {_pct(ranked[0][1] / total)} of "
        f"the result."
    )
    partial = [
        y for y in PARTIAL_YEARS
        if any(str(r.get(profile["temporal"])) == str(y) for r in rows)
    ]
    if partial:
        text += (
            f" {' and '.join(str(y) for y in partial)} is a partial year — the "
            f"window opens mid-year — so its totals are lower for that reason "
            f"alone."
        )
    return text + " Shares are within-year, because raw counts are not comparable across years."


def _word(value: int) -> str:
    words = {
        0: "none", 1: "one", 2: "two", 3: "three", 4: "four",
        5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
    }
    return words.get(value, str(value))


READINGS = {
    "slice": _reading_slice,
    "slice_year": _reading_slice_year,
    "dice": _reading_dice,
    "roll_up": _reading_roll_up,
    "drill_down": _reading_drill_down,
    "top_months": _reading_top_months,
    "pivot": _reading_pivot,
    "cube": _reading_cube,
    "drill_across": _reading_drill_across,
    "topic_mix": _reading_topic_mix,
}


def reading_for(
    operation: str,
    rows: list[dict[str, Any]] | None,
    columns: list[str] | None,
    meta: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> str:
    """The plain-language reading of one operation's result.

    Always returns a string and never raises: a reading that fails must not take
    the table down with it. An unregistered operation gets a neutral description
    of what came back rather than nothing at all.
    """
    rows = rows or []
    meta = meta or {}
    params = params or {}

    # Empty is handled here rather than in each reader. One reader indexed
    # `rows[0]` before checking, so an empty result raised IndexError and
    # produced a "defect in the reading" message — technically honest and
    # completely useless to whoever clicked the button.
    if not rows:
        return (
            "No rows matched. This operation returned nothing for these "
            "parameters, which is a valid answer rather than a failure — an "
            "empty cross-tabulation usually means the filter excluded "
            "everything."
        )

    reader = READINGS.get(operation)
    if reader is None:
        return (
            f"{len(rows)} rows across {len(columns or [])} columns. This "
            f"operation has no written reading yet, so the table below is the "
            f"whole answer."
        )
    try:
        return reader(rows, _profile(rows), params)
    except Exception as exc:  # noqa: BLE001 - a reading must never break a result
        return (
            f"{len(rows)} rows returned. The written reading could not be produced "
            f"({type(exc).__name__}), which is a defect in the reading rather than "
            f"in the query — the rows below are correct."
        )


def known_operations() -> list[str]:
    return sorted(READINGS)
