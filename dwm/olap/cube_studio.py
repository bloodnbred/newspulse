"""Cube Studio's slicing engine: arbitrary dimensions and measures over a cube.

The v1 operations in `dwm.olap.operations` are a fixed catalogue - eight
hand-written queries, each hard-coding which dimension it groups by. Cube Studio
lets a reader choose the dimensions, so the engine has to build the statement,
and building a statement from a request is where an OLAP tool usually starts
lying. Four rules keep this one honest.

**One cube per view, and the two cubes never mix.** `cube_day_topic` is one row
per (day, topic); `fact_stock_daily` is one row per (stock, day). A single GROUP
BY spanning both would join every headline to every stock that traded that day,
so a day with 700 headlines and 47 stocks would report 32,900 - and every
"news count" in the result would be wrong by a factor of the stock count. So a
spec naming fields from both cubes is refused with the reason, not answered
with a fan-out. Drill-across, which does combine them honestly, is a separate
operation in `dwm.olap.operations`.

**Ratios are computed at query time, never stored** (rule 16). The cubes hold
sums and counts. "Mean sentiment" is `sum_sentiment_compound / headline_count`
evaluated in the same statement that grouped the rows; it is not a column
anyone can read back, and it is not averaged across rows afterwards, which
would weight a day of 700 headlines the same as a day of 12.

**Field ids resolve to whitelisted SQL.** Nothing in a request reaches the
statement as text. A field id is a key into a dict of expressions written here;
a filter value is a bound parameter. There is no path from a request to the SQL
text, which is also why an unknown field can be answered with "no such field"
rather than a database error.

**Banded dimensions are derived, and say so.** "Sentiment level" and
"Sensational level" are CASE expressions over the grouped sums, not columns in
a dimension table. They are flagged `derived=True`, the shelf labels them, and
the SQL is recorded so the reader can see the thresholds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import duckdb

FieldKind = Literal["dimension", "measure"]

#: Units are carried so the front end can format without guessing, and so a
#: measure cannot be drawn as a count when it is a percentage.
Unit = Literal["label", "year", "count", "ratio", "pct", "score", "points"]


@dataclass(frozen=True, slots=True)
class Field:
    """One thing a reader can drop into a zone."""

    id: str
    label: str
    kind: FieldKind
    expr: str
    description: str
    unit: Unit = "label"
    derived: bool = False
    #: The cube this field belongs to. Used to refuse a mixed spec.
    cube: str = ""
    #: For a banded dimension: the measure it bands, and the CASE that bands it.
    #: A band cannot live in the inner statement, because it is computed from
    #: grouped sums and `GROUP BY 1` may not group by an aggregate. So the
    #: statement runs in two levels and the band is applied over the result.
    #: `band` is a template; `{m}` is the banded measure's column name.
    band_of: str = ""
    band: str = ""


@dataclass(frozen=True, slots=True)
class Cube:
    """One grain, with the joins needed to give its dimensions names."""

    id: str
    label: str
    grain: str
    source: str
    #: True when the source table is a cube rather than a fact. Rule 21 lets the
    #: dashboard read cubes freely and caps fact reads at 100k rows.
    is_cube: bool
    fields: tuple[Field, ...]
    notes: tuple[str, ...] = ()

    def by_id(self) -> dict[str, Field]:
        return {f.id: f for f in self.fields}


@dataclass(slots=True)
class CubeSpec:
    """A validated request. Nothing reaches `run` unvalidated."""

    cube: str
    rows: list[str]
    columns: list[str]
    filters: dict[str, list[Any]]
    measures: list[str]
    limit: int = 2000
    order_by: str | None = None
    descending: bool = True


@dataclass(slots=True)
class CubeResult:
    rows: list[dict[str, Any]]
    columns: list[str]
    totals: dict[str, Any]
    sql: list[str]
    cube: str
    notes: list[str] = field(default_factory=list)


class CubeError(ValueError):
    """A request the engine refuses, with the reason in words.

    Carries an HTTP status so the router can answer 422 for a malformed spec and
    409 for one that names fields from two cubes - a different kind of mistake,
    and the reader needs to be told which one they made.
    """

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


class _Rev:
    """A string that sorts backwards, so a descending sort needs one comparison.

    A plain sort key cannot mix directions: `sorted` takes one ascending or
    descending flag for the whole list, and a view sorted by a measure descending
    but a label ascending is a normal thing to want. Wrapping the label in a
    reversed-alphabet object keeps the key total while inverting only labels.
    """

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text

    def __lt__(self, other: _Rev) -> bool:
        return self.text > other.text

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Rev) and self.text == other.text


# ---------------------------------------------------------------------------
# the news cube
# ---------------------------------------------------------------------------

#: `cube_day_topic` is one row per (day, topic). 19,481 rows for the whole
#: window, so every query against it is well inside the latency a reader will
#: wait for, and it is a cube, so rule 21 lets the dashboard read it directly.
NEWS_SOURCE = """
    FROM cube_day_topic c
    JOIN dim_date  d ON d.date_key  = c.date_key
    JOIN dim_topic t ON t.topic_key = c.topic_key
    WHERE d.full_date BETWEEN ? AND ?
"""

_NEWS_FIELDS: tuple[Field, ...] = (
    Field(
        id="year",
        label="Year",
        kind="dimension",
        expr="d.year_no",
        description="Calendar year of publication.",
        unit="year",
        cube="news",
    ),
    Field(
        id="quarter",
        label="Quarter",
        kind="dimension",
        expr="d.quarter_no",
        description="Calendar quarter, 1 to 4.",
        unit="label",
        cube="news",
    ),
    Field(
        id="month",
        label="Month",
        kind="dimension",
        expr="d.year_month",
        description="Year and month as YYYY-MM, so months sort chronologically.",
        unit="label",
        cube="news",
    ),
    Field(
        id="weekday",
        label="Weekday",
        kind="dimension",
        # Ordered by day_of_week with Monday first, which is why this is a
        # CASE and not the raw column: `day_of_week` starts at Sunday, and a
        # weekday axis that runs Sunday-to-Saturday puts every trading week
        # across the break in the middle.
        expr=(
            "CASE d.day_of_week WHEN 0 THEN 'Mon' WHEN 1 THEN 'Tue' WHEN 2 THEN 'Wed' "
            "WHEN 3 THEN 'Thu' WHEN 4 THEN 'Fri' WHEN 5 THEN 'Sat' ELSE 'Sun' END"
        ),
        description=(
            "Day of publication. 32.4% of in-window headlines fall on a day the "
            "market was closed, so weekdays here are publication days, not "
            "trading days."
        ),
        unit="label",
        derived=True,
        cube="news",
    ),
    Field(
        id="topic",
        label="Topic",
        kind="dimension",
        expr="t.topic_name",
        description="The TOI category the headline was filed under.",
        unit="label",
        cube="news",
    ),
    Field(
        id="topic_group",
        label="Topic group",
        kind="dimension",
        expr="t.topic_group",
        description="Topics folded into a handful of families.",
        unit="label",
        cube="news",
    ),
    Field(
        id="sentiment_level",
        label="Sentiment level",
        kind="dimension",
        # Documented form. The executed form is `band`, applied over the grouped
        # rows; the two agree because both are computed from the same sums.
        expr=(
            "CASE WHEN mean_sentiment >= 0.05 THEN 'Positive' "
            "WHEN mean_sentiment <= -0.05 THEN 'Negative' ELSE 'Neutral' END"
        ),
        description=(
            "Mean sentiment in the cell, banded. Derived at query time: "
            "+/-0.05 on the mean of the cell's headlines."
        ),
        unit="label",
        derived=True,
        cube="news",
        band_of="mean_sentiment",
        band=(
            "CASE WHEN {m} >= 0.05 THEN 'Positive' "
            "WHEN {m} <= -0.05 THEN 'Negative' ELSE 'Neutral' END"
        ),
    ),
    Field(
        id="sensational_level",
        label="Sensational level",
        kind="dimension",
        expr=(
            "CASE WHEN sensational_rate >= 0.10 THEN 'High' "
            "WHEN sensational_rate >= 0.04 THEN 'Medium' ELSE 'Low' END"
        ),
        description=(
            "Share of the cell's headlines that tripped the sensationalism "
            "scorer. Derived at query time: 10% is High, 4% is Medium."
        ),
        unit="label",
        derived=True,
        cube="news",
        band_of="sensational_rate",
        band=(
            "CASE WHEN {m} >= 0.10 THEN 'High' "
            "WHEN {m} >= 0.04 THEN 'Medium' ELSE 'Low' END"
        ),
    ),
    Field(
        id="news_count",
        label="News count",
        kind="measure",
        expr="sum(c.headline_count)",
        description="Headlines in the cell.",
        unit="count",
        cube="news",
    ),
    Field(
        id="mean_sentiment",
        label="Mean sentiment",
        kind="measure",
        expr="sum(c.sum_sentiment_compound) / nullif(sum(c.headline_count), 0)",
        description=(
            "Mean VADER compound score, -1 to +1. Computed from the cube's "
            "sums over its counts, so it is weighted by headline rather than by "
            "cell."
        ),
        unit="score",
        cube="news",
    ),
    Field(
        id="risk_signal_rate",
        label="Risk-signal rate",
        kind="measure",
        expr="sum(c.risk_signal_count) / nullif(sum(c.headline_count), 0)",
        description=(
            "Share of headlines carrying the writing-style risk signal. This "
            "measures style, not truth."
        ),
        unit="ratio",
        cube="news",
    ),
    Field(
        id="sensational_rate",
        label="Sensational rate",
        kind="measure",
        expr="sum(c.sensational_count) / nullif(sum(c.headline_count), 0)",
        description="Share of headlines the sensationalism scorer flagged.",
        unit="ratio",
        cube="news",
    ),
    Field(
        id="negative_rate",
        label="Negative rate",
        kind="measure",
        expr="sum(c.negative_count) / nullif(sum(c.headline_count), 0)",
        description="Share of headlines the sentiment pass called negative.",
        unit="ratio",
        cube="news",
    ),
    Field(
        id="positive_rate",
        label="Positive rate",
        kind="measure",
        expr="sum(c.positive_count) / nullif(sum(c.headline_count), 0)",
        description="Share of headlines the sentiment pass called positive.",
        unit="ratio",
        cube="news",
    ),
    Field(
        id="mean_sensational_score",
        label="Mean sensational score",
        kind="measure",
        expr="sum(c.sum_sensational_score) / nullif(sum(c.headline_count), 0)",
        description="Mean sensationalism score per headline in the cell.",
        unit="score",
        cube="news",
    ),
    Field(
        id="mean_words",
        label="Mean words",
        kind="measure",
        expr="sum(c.sum_word_count) / nullif(sum(c.headline_count), 0)",
        description="Mean headline length in words.",
        unit="points",
        cube="news",
    ),
    Field(
        id="mean_caps_ratio",
        label="Mean caps ratio",
        kind="measure",
        expr="sum(c.sum_caps_token_ratio) / nullif(sum(c.headline_count), 0)",
        description=(
            "Mean share of a headline's tokens written in capitals. A style "
            "measure; shouting is not news."
        ),
        unit="ratio",
        cube="news",
    ),
    Field(
        id="news_heat",
        label="News heat (z)",
        kind="measure",
        # Computed in Python over the returned cells, because a z-score is only
        # defined relative to the set being displayed and a z needs the whole
        # set, which SQL cannot see before the limit is applied.
        expr="__COMPUTED_IN_PYTHON__",
        description=(
            "This cell's headline count scored against the other cells in the "
            "result. A z has no meaning without the set it is scored in, so it "
            "is computed over the view, not the cube."
        ),
        unit="score",
        derived=True,
        cube="news",
    ),
)


# ---------------------------------------------------------------------------
# the market cube
# ---------------------------------------------------------------------------

#: `fact_stock_daily` is a fact, not a cube, and 61,523 rows is inside rule 21's
#: 100k cap for a read the dashboard performs directly. Outlier returns are
#: excluded here (rule 14) - they stay in the table and out of the statistics.
MARKET_SOURCE = """
    FROM fact_stock_daily p
    JOIN dim_date   d ON d.date_key  = p.date_key
    JOIN dim_stock  s ON s.stock_key = p.stock_key
    LEFT JOIN dim_sector sec ON sec.sector_key = s.sector_key
    WHERE d.full_date BETWEEN ? AND ?
      AND p.is_outlier = FALSE
"""

_MARKET_FIELDS: tuple[Field, ...] = (
    Field(
        id="year",
        label="Year",
        kind="dimension",
        expr="d.year_no",
        description="Calendar year of the session.",
        unit="year",
        cube="market",
    ),
    Field(
        id="quarter",
        label="Quarter",
        kind="dimension",
        expr="d.quarter_no",
        description="Calendar quarter, 1 to 4.",
        unit="label",
        cube="market",
    ),
    Field(
        id="month",
        label="Month",
        kind="dimension",
        expr="d.year_month",
        description="Year and month as YYYY-MM.",
        unit="label",
        cube="market",
    ),
    Field(
        id="weekday",
        label="Weekday",
        kind="dimension",
        expr=(
            "CASE d.day_of_week WHEN 0 THEN 'Mon' WHEN 1 THEN 'Tue' WHEN 2 THEN 'Wed' "
            "WHEN 3 THEN 'Thu' WHEN 4 THEN 'Fri' WHEN 5 THEN 'Sat' ELSE 'Sun' END"
        ),
        description="Weekday of the session, Monday first.",
        unit="label",
        derived=True,
        cube="market",
    ),
    Field(
        id="sector",
        label="Sector",
        kind="dimension",
        expr="coalesce(sec.sector_name, 'Unclassified')",
        description=(
            "The sector in today's Nifty 50 list. Applied to a 2015-2020 "
            "window, so it is survivorship-biased by construction."
        ),
        unit="label",
        cube="market",
    ),
    Field(
        id="stock",
        label="Stock",
        kind="dimension",
        expr="s.company_name",
        description="Company name from the constituent master.",
        unit="label",
        cube="market",
    ),
    Field(
        id="mean_return",
        label="Mean return",
        kind="measure",
        expr="sum(p.return_pct) / nullif(count(*), 0)",
        description=(
            "Mean daily percentage return across the stock-day rows in the "
            "cell. A sector cell averages over every stock and every session."
        ),
        unit="pct",
        cube="market",
    ),
    Field(
        id="mean_abs_return",
        label="Mean absolute return",
        kind="measure",
        expr="sum(p.abs_return_pct) / nullif(count(*), 0)",
        description=(
            "Mean size of a daily move, ignoring direction. This is a "
            "dispersion measure, not a volatility estimate."
        ),
        unit="pct",
        cube="market",
    ),
    Field(
        id="mean_volatility",
        label="Mean 20-day volatility",
        kind="measure",
        expr="avg(p.volatility_20d)",
        description=(
            "Mean of each row's trailing 20-session volatility, as computed in "
            "the warehouse at ingest."
        ),
        unit="pct",
        cube="market",
    ),
    Field(
        id="observations",
        label="Stock-days",
        kind="measure",
        expr="count(*)",
        description="How many stock-day rows the cell holds. The denominator.",
        unit="count",
        cube="market",
    ),
    Field(
        id="up_share",
        label="Up share",
        kind="measure",
        expr="sum(CASE WHEN p.return_pct > 0 THEN 1 ELSE 0 END) / nullif(count(*), 0)",
        description="Share of stock-days that closed up.",
        unit="ratio",
        cube="market",
    ),
)


NEWS_CUBE = Cube(
    id="news",
    label="News cube",
    grain="one row per (day, topic)",
    source=NEWS_SOURCE,
    is_cube=True,
    fields=_NEWS_FIELDS,
    notes=(
        "This cube includes market-report headlines, which describe a move that "
        "has already happened. The Home page's filter excludes them; this cube "
        "cannot, because cube_day_topic does not carry the flag.",
        "Sentiment is measured by VADER on headlines, not on the events they "
        "describe.",
    ),
)

MARKET_CUBE = Cube(
    id="market",
    label="Market cube",
    grain="one row per (stock, session)",
    source=MARKET_SOURCE,
    is_cube=False,
    fields=_MARKET_FIELDS,
    notes=(
        "Returns come from adjusted closes, so dividends do not read as losses.",
        "Daily returns beyond +/-25% are flagged as outliers and excluded here; "
        "they remain in fact_stock_daily.",
        "The constituent list is today's Nifty 50 applied to a 2015-2020 "
        "window, so companies that were constituents in the window and have "
        "since been removed are absent.",
    ),
)

CUBES: dict[str, Cube] = {NEWS_CUBE.id: NEWS_CUBE, MARKET_CUBE.id: MARKET_CUBE}


# ---------------------------------------------------------------------------
# the shelf
# ---------------------------------------------------------------------------


def shelf() -> dict[str, Any]:
    """The field shelf, as the page needs it.

    Ordered so the common cases are first: the calendar hierarchy, then the
    categorisations, then the measures. A shelf in definition order is a shelf
    nobody reads.
    """
    out: list[dict[str, Any]] = []
    for cube in CUBES.values():
        out.append(
            {
                "id": cube.id,
                "label": cube.label,
                "grain": cube.grain,
                "is_cube": cube.is_cube,
                "notes": list(cube.notes),
                "dimensions": [
                    _field_dict(f) for f in cube.fields if f.kind == "dimension"
                ],
                "measures": [_field_dict(f) for f in cube.fields if f.kind == "measure"],
            }
        )
    return {"cubes": out}


def _field_dict(f: Field) -> dict[str, Any]:
    return {
        "id": f.id,
        "label": f.label,
        "kind": f.kind,
        "description": f.description,
        "unit": f.unit,
        "derived": f.derived,
        "cube": f.cube,
    }


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

#: Beyond this the result stops being readable in a table and starts being a
#: number the reader cannot check, so it is refused rather than truncated.
MAX_CELLS = 2000
MAX_DIMENSIONS = 3


def parse_spec(payload: dict[str, Any]) -> CubeSpec:
    """Turn a request into a spec or refuse it, in words.

    Every refusal here is a mistake a reader can fix, and the message says what
    to do. A generic 422 from deep inside DuckDB would leave them guessing which
    of the four things they typed was wrong.
    """
    cube_id = str(payload.get("cube") or "news")
    cube = CUBES.get(cube_id)
    if cube is None:
        raise CubeError(
            f"no cube called {cube_id!r}; this study has "
            f"{', '.join(sorted(CUBES))}"
        )

    fields = cube.by_id()

    def pick(name: str, kind: FieldKind) -> list[str]:
        raw = payload.get(name) or []
        if not isinstance(raw, list):
            raise CubeError(f"{name} must be a list of field names")
        out: list[str] = []
        for item in raw:
            key = str(item)
            f = fields.get(key)
            if f is None:
                other = _which_cube(key)
                if other is not None:
                    raise CubeError(
                        f"{key!r} belongs to the {other.label}, not "
                        f"{cube.label}. The two are separate grains - one row "
                        "per (day, topic) against one row per (stock, session) - "
                        "so a single query across both would count every "
                        "headline once for each stock that traded that day. "
                        "Slice one, then use Drill-across to bring the other in.",
                        status=409,
                    )
                raise CubeError(f"{key!r} is not a field of the {cube.label}")
            if f.kind != kind:
                raise CubeError(
                    f"{key!r} is a {f.kind}, not a {kind}"
                    + (" - measures belong in Values, dimensions in the axes" if kind == "dimension" else "")
                )
            if key not in out:
                out.append(key)
        return out

    rows = pick("rows", "dimension")
    columns = pick("columns", "dimension")
    measures = pick("measures", "measure")

    if not rows and not columns:
        raise CubeError(
            "put at least one dimension on Rows or Columns - a view with no "
            "axes is one number, and /ui/home is where you read single numbers"
        )
    if not measures:
        raise CubeError("put at least one measure in Values, or there is nothing to show")
    if len(rows) + len(columns) > MAX_DIMENSIONS:
        raise CubeError(
            f"{len(rows) + len(columns)} dimensions on the axes is more than "
            "this view will hold. Cells multiply across the axes - Topic by Year "
            "by Month is already 17 x 6 x 61 - so a fourth dimension produces a "
            "grid nobody reads and a colour scale that hides the pattern. Take "
            "one off an axis, or filter instead."
        )

    filters: dict[str, list[Any]] = {}
    raw_filters = payload.get("filters") or {}
    if not isinstance(raw_filters, dict):
        raise CubeError("filters must be an object of field name to chosen values")
    for key, values in raw_filters.items():
        f = fields.get(str(key))
        if f is None or f.kind != "dimension":
            raise CubeError(f"cannot filter on {key!r}: not a dimension of this cube")
        if not isinstance(values, list):
            raise CubeError(f"filter on {key!r} must be a list of chosen values")
        if values:
            filters[f.id] = list(values)

    limit = payload.get("limit")
    if limit is not None:
        try:
            limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise CubeError("limit must be a whole number") from exc
        limit = max(1, min(int(limit), MAX_CELLS))
    else:
        limit = MAX_CELLS

    order_by = payload.get("orderBy")
    if order_by is not None:
        order_by = str(order_by)
        if order_by not in measures and order_by not in rows + columns:
            raise CubeError(f"cannot order by {order_by!r}: it is not in the result")

    return CubeSpec(
        cube=cube.id,
        rows=rows,
        columns=columns,
        filters=filters,
        measures=measures,
        limit=limit,
        order_by=order_by,
        descending=bool(payload.get("descending", True)),
    )


def _which_cube(field_id: str) -> Cube | None:
    for cube in CUBES.values():
        if field_id in cube.by_id():
            return cube
    return None


# ---------------------------------------------------------------------------
# the statement
# ---------------------------------------------------------------------------


def _in_clause(values: list[Any]) -> tuple[str, list[Any]]:
    """`IN (?, ?, ...)` with the values bound.

    The placeholders are generated from the length of the caller's list and the
    values themselves are passed as parameters, so a value containing a quote is
    a value and not a statement. There is no string concatenation of user data
    anywhere in this module.
    """
    marks = ", ".join("?" for _ in values)
    return f"({marks})", list(values)


#: Measures with no SQL of their own: they are a function of the other measures
#: in the same row, so they cannot appear in the statement at all.
COMPUTED_MEASURES = frozenset({"news_heat"})


def _band_inputs(spec: CubeSpec) -> list[str]:
    """Measures a banded dimension needs that the reader did not ask for.

    "Sentiment level" is a CASE over mean sentiment. If a reader puts the band
    on an axis and leaves mean sentiment out of Values, the outer query still
    has to be able to read it - so it is selected inside and dropped from the
    result. Refusing instead would mean the band was unavailable unless the
    reader already knew which measure defined it.
    """
    fields = CUBES[spec.cube].by_id()
    wanted = {fields[k].band_of for k in spec.rows + spec.columns if fields[k].band_of}
    return sorted(wanted - set(spec.measures) - COMPUTED_MEASURES)


def build_sql(
    spec: CubeSpec, window: tuple[str, str], totals: bool
) -> tuple[str, list[Any]]:
    """The statement for one of the two queries, and its bound parameters.

    Two levels, and the second only exists when a banded dimension is on an
    axis. A band is a CASE over a grouped sum, and DuckDB will not accept
    `GROUP BY 1` where the first select expression is an aggregate - so the
    inner statement groups by the plain dimensions and the outer one applies
    the band over the result. The outer GROUP BY repeats the measures, which
    changes nothing (each inner row is already unique per plain-dimension group)
    and keeps every ratio weighted exactly as the inner query computed it.

    `totals=True` drops the dimensions but keeps every ratio, so the total row is
    a real aggregate over the whole filtered slice rather than the client adding
    up percentages. A total formed by summing mean sentiments is the single most
    common way an OLAP tool lies to its reader.
    """
    cube = CUBES[spec.cube]
    fields = cube.by_id()
    params: list[Any] = [window[0], window[1]]

    dims = [] if totals else spec.rows + spec.columns
    plain = [k for k in dims if not fields[k].band_of]
    banded = [k for k in dims if fields[k].band_of]
    sql_measures = [m for m in spec.measures if m not in COMPUTED_MEASURES]

    predicates: list[str] = []
    for key, values in spec.filters.items():
        clause, bound = _in_clause(values)
        predicates.append(f"({fields[key].expr}) IN {clause}")
        params.extend(bound)
    where = " AND ".join(f"({p})" for p in predicates) if predicates else "TRUE"

    def select_of(items: list[str]) -> str:
        return "SELECT\n        " + ",\n        ".join(items)

    inner = select_of([f"{fields[k].expr} AS {k}" for k in plain]
                      + [f"{fields[m].expr} AS {m}" for m in sql_measures])
    inner += f"\n    {cube.source.strip()}"
    inner += f"\n      AND {where}"
    if plain:
        inner += "\n    GROUP BY " + ", ".join(fields[k].expr for k in plain)

    if not banded:
        return inner, params

    inner_measures = [*sql_measures, *_band_inputs(spec)]
    inner = select_of([f"{fields[k].expr} AS {k}" for k in plain]
                      + [f"{fields[m].expr} AS {m}" for m in inner_measures])
    inner += f"\n    {cube.source.strip()}"
    inner += f"\n      AND {where}"
    if plain:
        inner += "\n    GROUP BY " + ", ".join(fields[k].expr for k in plain)

    outer = select_of(
        # Plain dimensions pass through by column name: they were aliased to
        # their field id inside, so the JSON keys stay the field ids whatever
        # expression produced them.
        list(plain)
        + [fields[k].band.format(m=fields[k].band_of) + f" AS {k}" for k in banded]
        + list(sql_measures)
    )
    outer += "\n    FROM grouped"
    outer += "\n    GROUP BY " + ", ".join([*plain, *inner_measures])
    indented = "\n".join("    " + line for line in inner.splitlines())
    return f"WITH grouped AS (\n{indented}\n)\n{outer}", params


def run(
    con: duckdb.DuckDBPyConnection,
    spec: CubeSpec,
    window: tuple[str, str],
) -> CubeResult:
    """Answer a validated spec. Two statements: the cells, and their totals."""
    cube = CUBES[spec.cube]

    sql, params = build_sql(spec, window, totals=False)
    cursor = con.execute(sql, params)
    names = [d[0] for d in (cursor.description or [])]
    raw = [dict(zip(names, r, strict=True)) for r in cursor.fetchall()]

    # The measures a banded dimension needed internally are not part of the
    # view, so they leave with the statement. They were selected to make the
    # band computable, not because the reader asked to see them.
    internal = _band_inputs(spec)
    if internal:
        names = [n for n in names if n not in internal]
        for row in raw:
            for key in internal:
                row.pop(key, None)

    # `news_heat` is a z over the cells in this result, so it cannot be computed
    # before the GROUP BY has run. Adding it here rather than in the statement
    # would hide the fact that it is relative to the view; the caption says so.
    if "news_heat" in spec.measures:
        values = [float(r.get("news_count") or 0.0) for r in raw]
        mean = sum(values) / len(values) if values else 0.0
        var = sum((v - mean) ** 2 for v in values) / (len(values) - 1) if len(values) > 1 else 0.0
        sd = var**0.5
        for row, v in zip(raw, values, strict=True):
            row["news_heat"] = round((v - mean) / sd, 2) if sd else None

    # Sort here rather than in SQL so the sort applies identically to the two
    # column axes. `ORDER BY` in the statement would sort by an aliased column
    # DuckDB may not resolve inside a GROUP BY, and the failure would be silent.
    order = spec.order_by or (spec.measures[0] if spec.measures else None)
    if order and raw:
        descending = spec.descending

        def key(row: dict[str, Any]) -> tuple[bool, Any]:
            v = row.get(order)
            # Nulls last regardless of direction: a cell with no measure value
            # is not "the smallest", it is an absence, and sorting it to the top
            # would put a blank cell where a reader looks for the peak.
            if v is None:
                return (True, "")
            if isinstance(v, str):
                # A string axis sorts naturally, and reversing it is done by
                # reversing the comparison rather than by mangling the value.
                return (False, v) if not descending else (False, _Rev(v))
            return (False, -float(v) if descending else float(v))

        raw.sort(key=key)

    truncated = len(raw) > spec.limit
    raw = raw[: spec.limit]

    totals_sql, totals_params = build_sql(spec, window, totals=True)
    cursor = con.execute(totals_sql, totals_params)
    total_names = [d[0] for d in (cursor.description or [])]
    total_row = cursor.fetchone()
    totals: dict[str, Any] = (
        dict(zip(total_names, total_row, strict=True)) if total_row else {}
    )

    notes = list(cube.notes)
    if truncated:
        notes.append(
            f"Only the first {spec.limit} cells are shown. Narrow the window or "
            "add a filter rather than reading a truncated table as the whole."
        )
    if "news_heat" in spec.measures:
        notes.append(
            "News heat is a z-score across the cells in THIS result, so it "
            "changes when you change the view. It is not comparable between two "
            "different views."
        )

    return CubeResult(
        rows=raw,
        columns=list(names),
        totals=totals,
        sql=[sql, totals_sql],
        cube=cube.id,
        notes=notes,
    )
