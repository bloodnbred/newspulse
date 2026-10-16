"""Vega-Lite chart specifications, built in Python.

**Why these live here and not in the browser.** Rendering a chart is not
computing a finding, so putting the specs in JavaScript would not violate the
"the dashboard computes nothing" rule. They are here anyway, for two reasons
that turned out to matter:

1. **They are testable.** A malformed spec is a crash at page load, and the only
   way to catch that without a browser is to build the spec in Python and
   validate it. That is a unit test. A spec assembled in JS is only ever
   exercised when someone opens the page.
2. **They stay consistent.** One palette, one axis style, one set of rules, in
   one file. Six charts built in three places drift immediately, and "drift" in
   a chart library looks like a subtle wrongness nobody can name.

The theme is a parameter rather than a constant. A hard-coded light palette is
what made the previous dashboard unreadable on a machine set to dark: the CSS
forced a light canvas while the framework rendered dark charts over it. Here
both are designed and the client asks for the one it is actually using.

**The sorting rule, which exists because of a bug.** A nominal axis in
Vega-Lite is a set of unordered strings, and its default order is whatever the
data happens to produce. An earlier version of the RQ2 chart sorted its month
axis with `sort="-x"`, which ordered the event months by their *z-score*: the
chart showed 2020-05 next to 2016-12 and read as a chronological series that was
not one. Every nominal axis in this module is therefore given an explicit sort
list, sorted by the natural order of the label, never by a measure.
"""

from __future__ import annotations

from typing import Any

import altair as alt

# ---------------------------------------------------------------------------
# themes
# ---------------------------------------------------------------------------
# Two complete palettes rather than one palette plus overrides. A theme that
# derives its dark variant by inverting its light one always ends up with a
# contrast problem somewhere; these were each picked as a set.
#
# **These mirror `dashboard/static/app.css` and must stay identical to it.** A
# spec is JSON built in Python and cannot read a stylesheet, so the values are
# duplicated deliberately — and `tests/test_ui.py::test_chart_palette_matches_css`
# asserts they have not drifted. Note that these are the *text* colours, which
# are darker than the pure brand hues: `#0071E3` on a tinted background measures
# 4.15:1 and fails WCAG AA, so the accent here is `#0062C4` at 5.2:1.

LIGHT: dict[str, Any] = {
    "name": "light",
    "surface": "#FFFFFF",
    "canvas": "#F5F5F7",
    "ink": "#1D1D1F",
    "muted": "#5E5E63",
    "grid": "#E3E3E8",
    "axis": "#C7C7CC",
    "accent": "#0062C4",
    "positive": "#157F3C",
    "caution": "#9A6200",
    "negative": "#C8102E",
    # First four repeat the accents so a two-state chart still reads correctly.
    "series": ["#0062C4", "#157F3C", "#9A6200", "#C8102E", "#3B9BFF", "#8E4FBF"],
    # Sequential ramp for the heatmap: near-white to saturated accent.
    "heat": ["#FFFFFF", "#0062C4"],
}

DARK: dict[str, Any] = {
    "name": "dark",
    "surface": "#151518",
    "canvas": "#0A0A0C",
    "ink": "#F5F5F7",
    "muted": "#A0A0A8",
    "grid": "#26262B",
    "axis": "#3A3A40",
    "accent": "#3B9BFF",
    "positive": "#3DD168",
    "caution": "#FFB340",
    "negative": "#FF5C6C",
    "series": ["#3B9BFF", "#3DD168", "#FFB340", "#FF5C6C", "#64D2FF", "#BF5AF2"],
    "heat": ["#151518", "#3B9BFF"],
}

THEMES = {"light": LIGHT, "dark": DARK}

# Every categorical chart draws from this order, so a topic that appears in two
# charts is the same colour in both.
SERIES = LIGHT["series"]


def theme(name: str) -> dict[str, Any]:
    return THEMES.get(name, LIGHT)


# ---------------------------------------------------------------------------
# shared configuration
# ---------------------------------------------------------------------------


def _axis(t: dict[str, Any], title: str | None = None, **over: Any) -> dict[str, Any]:
    """One axis style for every chart in the dashboard.

    `domain=False` and no axis line because the gridline is enough structure;
    keeping the line makes small multiples look like they are floating.
    """
    config: dict[str, Any] = {
        "gridColor": t["grid"],
        "gridWidth": 1,
        "domain": False,
        "tickColor": t["axis"],
        "labelFontSize": 11,
        "titleFontSize": 11,
        "titleColor": t["muted"],
        "labelColor": t["muted"],
        "titleFontWeight": "normal",
        "labelFont": "system-ui, -apple-system, Segoe UI, sans-serif",
    }
    if title is not None:
        config["title"] = title
    config.update(over)
    return config


def _config(t: dict[str, Any]) -> dict[str, Any]:
    """Vega-Lite config block: transparent plot, quiet legends, no toolbar.

    `autosize: fit` is what makes a chart fill its frame. Without it Vega uses
    a fixed 200px width and every chart on the dashboard renders as a small
    square in the corner of its card — which is exactly what happened on the
    first build, and it looks like missing data rather than a sizing mistake.
    It has to be paired with `width="container"` on the chart itself; neither
    works alone.
    """
    return {
        "background": "transparent",
        "view": {"stroke": "transparent"},
        "autosize": {"type": "fit", "contains": "padding"},
        "axis": _axis(t),
        "legend": {
            "labelFontSize": 11,
            "titleFontSize": 11,
            "titleColor": t["muted"],
            "labelColor": t["muted"],
            "symbolType": "square",
            "symbolSize": 90,
            "orient": "bottom",
        },
        "title": {"color": t["muted"], "fontSize": 12, "anchor": "start"},
        "font": "system-ui, -apple-system, Segoe UI, sans-serif",
    }


def _named(yes: str, no: str, yes_colour: str, no_colour: str) -> alt.Color:
    """A two-state colour as a labelled nominal field.

    Not a conditional encoding. A conditional with two colours and no labels
    produces a legend of two unlabelled swatches, which tells the reader
    nothing; naming the states turns the same encoding into a sentence they can
    read. It also happens to be stable across Vega-Lite versions, where the
    conditional API has repeatedly changed shape.
    """
    return alt.Color(
        "state:N",
        scale=alt.Scale(domain=[yes, no], range=[yes_colour, no_colour]),
        legend=alt.Legend(title=None, orient="bottom"),
    )


def _label_state(
    rows: list[dict[str, Any]], key: str, test: Any, yes: str, no: str
) -> list[dict[str, Any]]:
    """Attach a readable `state` to each row for `_named`."""
    out = []
    for row in rows:
        copy = dict(row)
        copy["state"] = yes if test(row[key]) else no
        out.append(copy)
    return out


def _sorted_unique(values: list[str]) -> list[str]:
    """Explicit axis order: natural order of the label, never by a measure."""
    return sorted(set(values))


def _spec(chart: Any, t: dict[str, Any], height: Any = 280) -> dict[str, Any]:
    """Finalise a chart: fill the frame, apply the shared config, serialise.

    `width="container"` is what makes the chart fill its card. Set here rather
    than at each call site so a chart cannot be added with the default 200px
    width by mistake — which is precisely the bug this replaced.
    """
    return (
        chart.properties(width="container", height=height)
        .configure(**_config(t))
        .to_dict()
    )


# ---------------------------------------------------------------------------
# RQ2 - event months, diverging bars
# ---------------------------------------------------------------------------


def rq2_event_z(facts: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
    """Event-month z-scores for the three desks that should have risen.

    The finding is a counter-signal, so the chart has to show negative bars.
    Sorting the month axis by date is the whole reason this chart is honest:
    sorted by z-score it reads as a chronological series and is not one.
    """
    rows: list[dict[str, Any]] = []
    for event in facts["rq2_bursts"].get("event_months", []):
        measures = event.get("measures", {})
        for desk in ("Health", "Sports", "Entertainment"):
            value = measures.get(f"topic_volume:{desk}", {}).get("z_score")
            if value is not None:
                rows.append({
                    "month": event["year_month"],
                    "desk": desk,
                    "z": value,
                    "label": event.get("label", ""),
                })
    if not rows:
        return {}
    rows = _label_state(rows, "z", lambda v: v < 0, "coverage fell", "coverage rose")
    months = _sorted_unique([r["month"] for r in rows])
    chart = (
        alt.Chart(alt.Data(values=rows))
        .mark_bar(cornerRadiusEnd=3)
        .encode(
            y=alt.Y("month:N", sort=months, axis=_axis(t, None, labelAngle=0)),
            x=alt.X("z:Q", axis=_axis(t, "standard deviations above or below that desk's usual level")),
            color=_named("coverage fell", "coverage rose", t["caution"], t["accent"]),
            yOffset="desk:N",
            tooltip=[
                alt.Tooltip("month:N", title="month"),
                alt.Tooltip("desk:N", title="desk"),
                alt.Tooltip("z:Q", title="z-score", format=".2f"),
                alt.Tooltip("label:N", title="event"),
            ],
        )
    )
    return _spec(chart, t, height=max(200, 22 * len(months)))


# ---------------------------------------------------------------------------
# RQ1 - topic mix over time, stacked area
# ---------------------------------------------------------------------------


def rq1_topic_mix(facts: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
    """Within-year topic share.

    Within-year, not raw counts: 2015 is a partial year and comparing raw counts
    across years would show a cliff that is an artefact of the window, not of
    what India was reading.
    """
    rows = [
        r for r in facts["rq1_topic_mix"].get("rows", [])
        if r.get("topic_group") == "Subject"
    ]
    if not rows:
        return {}
    topics = _sorted_unique([r["topic"] for r in rows])
    chart = (
        alt.Chart(alt.Data(values=rows))
        .mark_area(opacity=0.9)
        .encode(
            x=alt.X("year:O", axis=_axis(t, None, labelAngle=0, gridColor=None)),
            y=alt.Y(
                "share_of_year:Q",
                stack="zero",
                axis=_axis(t, "share of that year's headlines"),
            ),
            color=alt.Color(
                "topic:N",
                scale=alt.Scale(domain=topics, range=SERIES),
                legend=alt.Legend(orient="bottom", columns=6, title=None),
            ),
            order=alt.Order("topic:N"),
            tooltip=[
                "year:O",
                "topic:N",
                alt.Tooltip("share_of_year:Q", format=".2%"),
                alt.Tooltip("headline_count:Q", format=","),
            ],
        )
    )
    return _spec(chart, t, height=300)


# ---------------------------------------------------------------------------
# RQ3 - rates with intervals, and the trend
# ---------------------------------------------------------------------------


def rq3_topic_rates(facts: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
    """Risk-signal rate by topic.

    The interval is drawn as a rule over the bar rather than hidden in a
    tooltip, because the bar alone implies a precision the measure does not
    have: these are point estimates over thousands of headlines.
    """
    rows = list(facts["rq3_sensationalism"].get("ranked", []))
    if not rows:
        return {}
    rows = _label_state(
        rows, "above_corpus_average", bool, "above the corpus rate", "within the corpus range"
    )
    topics = _sorted_unique([r["topic"] for r in rows])
    base = alt.Chart(alt.Data(values=rows)).encode(y=alt.Y("topic:N", sort=topics))
    bars = base.mark_bar(cornerRadiusEnd=3).encode(
        x=alt.X("risk_signal_rate:Q", axis=_axis(t, "risk-signal rate (not a fake-news rate)")),
        color=_named(
            "above the corpus rate", "within the corpus range", t["caution"], t["accent"]
        ),
        tooltip=[
            "topic:N",
            "topic_group:N",
            alt.Tooltip("risk_signal_rate:Q", format=".2%"),
            alt.Tooltip("ci95_low:Q", format=".2%"),
            alt.Tooltip("ci95_high:Q", format=".2%"),
            alt.Tooltip("headline_count:Q", format=","),
        ],
    )
    # 95% interval, drawn as a whisker: the two ends are what is being claimed.
    whiskers = base.mark_rule(color=t["ink"], strokeWidth=1.5, opacity=0.55).encode(
        x=alt.X("ci95_low:Q", axis=_axis(t)),
        x2=alt.X2("ci95_high:Q"),
    )
    corpus = float(facts["rq3_sensationalism"].get("corpus_rate") or 0)
    reference = (
        alt.Chart(alt.Data(values=[{"rate": corpus}]))
        .mark_rule(strokeDash=[4, 4], color=t["muted"], strokeWidth=1.5)
        .encode(x=alt.X("rate:Q", axis=_axis(t)))
    )
    return _spec(alt.layer(reference, bars, whiskers), t, height=max(240, 24 * len(topics)))


def rq3_trend(facts: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
    rows = list(facts["rq3_sensationalism"].get("trend_by_year", []))
    if not rows:
        return {}
    # Two marks rather than `mark_line(point=True)`: Altair 6's MarkDef has no
    # `pointSize`, so the combined form raises. Layering is also what lets the
    # dots carry their own tooltip.
    enc = {
        "x": alt.X("year:O", axis=_axis(t, None, labelAngle=0, gridColor=None)),
        "y": alt.Y("risk_signal_rate:Q", axis=_axis(t, "risk-signal rate")),
    }
    line = alt.Chart(alt.Data(values=rows)).mark_line(
        strokeWidth=2.5, color=t["accent"]
    ).encode(**enc)
    dots = alt.Chart(alt.Data(values=rows)).mark_point(
        size=70, filled=True, color=t["accent"]
    ).encode(
        **enc,
        tooltip=[
            "year:O",
            alt.Tooltip("risk_signal_rate:Q", format=".2%"),
            alt.Tooltip("headline_count:Q", format=","),
        ],
    )
    return _spec(alt.layer(line, dots), t, height=220)


# ---------------------------------------------------------------------------
# RQ4 - silhouette curve, and where the headlines went
# ---------------------------------------------------------------------------


def rq4_silhouette(facts: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
    """Silhouette against k, with the chosen value marked.

    Reported with the cluster shape as well, because 0.2582 clearing the
    conventional 0.25 threshold is only half a story: silhouette rewards one
    large cluster sitting far from a few small tight ones, and 94.88% of the
    corpus sits in one cluster here.
    """
    rows = list(facts["rq4_clusters"].get("k_candidates", []))
    if not rows:
        return {}
    chosen = facts["rq4_clusters"].get("chosen_k")
    line = (
        alt.Chart(alt.Data(values=rows))
        .mark_line(strokeWidth=2.5, color=t["accent"])
        .mark_point(size=70, filled=True, color=t["accent"])
        .encode(
            x=alt.X("k:O", axis=_axis(t, "k", labelAngle=0, gridColor=None)),
            y=alt.Y("silhouette:Q", axis=_axis(t, "separation score (0 to 1, higher is better)")),
            tooltip=["k:O", alt.Tooltip("silhouette:Q", format=".4f")],
        )
    )
    # The conventional threshold, drawn, so "clears 0.25" is visible rather than
    # something the caption has to assert.
    threshold = (
        alt.Chart(alt.Data(values=[{"s": 0.25}]))
        .mark_rule(strokeDash=[4, 4], color=t["muted"], strokeWidth=1.5)
        .encode(y=alt.Y("s:Q", axis=_axis(t)))
    )
    layers = [threshold, line]
    if chosen is not None:
        picked = [r for r in rows if r.get("k") == chosen]
        if picked:
            marker = (
                alt.Chart(alt.Data(values=picked))
                .mark_point(size=260, filled=False, color=t["caution"], strokeWidth=3)
                .encode(x=alt.X("k:O"), y=alt.Y("silhouette:Q"))
            )
            layers.append(marker)
    return _spec(alt.layer(*layers), t, height=240)


def rq4_cluster_sizes(facts: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
    """Share of the sample per cluster.

    The point of this chart is the shape, not the ranking: one bar should
    visibly dwarf the rest, which is the thing a silhouette number hides.
    """
    rows = list(facts["rq4_clusters"].get("clusters", []))
    if not rows:
        return {}
    rows = _label_state(
        rows, "share_of_sample", lambda v: v > 0.5,
        "the undifferentiated remainder", "a distinguishable topic",
    )
    ids = _sorted_unique([str(r["cluster_id"]) for r in rows])
    chart = (
        alt.Chart(alt.Data(values=rows))
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            y=alt.Y("cluster_id:N", sort=ids, axis=_axis(t, "cluster", gridColor=None)),
            x=alt.X("share_of_sample:Q", axis=_axis(t, "share of the 60,000 sampled headlines")),
            color=_named(
                "the undifferentiated remainder",
                "a distinguishable topic",
                t["muted"],
                t["accent"],
            ),
            tooltip=[
                "cluster_id:N",
                "top_terms:N",
                alt.Tooltip("size:Q", format=","),
                alt.Tooltip("share_of_sample:Q", format=".2%"),
            ],
        )
    )
    return _spec(chart, t, height=max(180, 42 * len(rows)))


def rq4_projection(facts: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
    """The clustering as a picture: 4,500 headlines in the LSA plane.

    A silhouette score and a bar chart of cluster sizes are both summaries, and
    neither shows what the clusters *are*. This plots the space K-Means actually
    clustered, so the reader can see the small topical groups sitting apart from
    the dominant cloud rather than being told that they do.

    The dominant cluster is deliberately thinned and the small ones shown in
    full — otherwise 57,000 identical-coloured points bury everything. That
    makes the large cluster over-represented here, and the caption says so; the
    cluster-size chart carries the true proportions.
    """
    block = facts["rq4_clusters"]
    projection = block.get("projection") or {}
    points = projection.get("points") or []
    clusters = projection.get("clusters") or []
    if len(points) < 50:
        return {}

    # Ordered by descending size, so the dominant cloud is drawn first and the
    # small groups land on top of it rather than underneath.
    ordered = sorted(clusters, key=lambda c: -c["size"])
    rank = {c["cluster_id"]: i for i, c in enumerate(ordered)}
    labels = {
        c["cluster_id"]: (
            f"cluster {c['cluster_id']} - {c['size']:,} headlines" +
            (" (sampled)" if c["sampled"] else "")
        )
        for c in ordered
    }
    for point in points:
        point["group"] = labels.get(point["c"], str(point["c"]))
        point["rank"] = rank.get(point["c"], 0)

    # Opacity falls as the cluster gets smaller so a 775-member group is still
    # visible against a 57,000-member cloud.
    #
    # The axis labels say "vocabulary spread" rather than "LSA component", because
    # the direction of these axes means nothing and only the spread does. The
    # technical name is in the tooltip for anyone who wants it.
    opacities = [0.16, 0.55, 0.7, 0.8, 0.8, 0.85, 0.85, 0.9]
    layers = []
    for index, cluster in enumerate(ordered):
        members = [p for p in points if p["c"] == cluster["cluster_id"]]
        colour = SERIES[index % len(SERIES)]
        layers.append(
            alt.Chart(alt.Data(values=members))
            .mark_circle(size=13, opacity=opacities[index % len(opacities)], color=colour)
            .encode(
                x=alt.X("x:Q", axis=_axis(t, "Vocabulary spread, left to right")),
                y=alt.Y("y:Q", axis=_axis(t, "Vocabulary spread, bottom to top")),
                tooltip=[
                    alt.Tooltip("group:N"),
                    alt.Tooltip("x:Q", title="LSA component 1", format=".3f"),
                    alt.Tooltip("y:Q", title="LSA component 2", format=".3f"),
                ],
            )
        )
    return _spec(alt.layer(*layers), t, height=420)


def rq4_cluster_topics(facts: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
    """Which publisher topics dominate each cluster.

    The evidence that these clusters are not the publisher's desks. Every cluster
    is dominated by `Local`, because 70% of the corpus is, and the clusters
    separate on vocabulary rather than on filing.
    """
    clusters = facts["rq4_clusters"].get("clusters", [])
    rows = []
    for cluster in clusters:
        for entry in (cluster.get("dominant_supplied_topics") or [])[:4]:
            rows.append({
                "cluster": f"cluster {cluster['cluster_id']}",
                "topic": entry.get("topic", "n/a"),
                "share": entry.get("share") or 0.0,
                "count": entry.get("count") or 0,
            })
    if not rows:
        return {}
    clusters_sorted = _sorted_unique([r["cluster"] for r in rows])
    topics = _sorted_unique([r["topic"] for r in rows])
    chart = (
        alt.Chart(alt.Data(values=rows))
        .mark_bar(cornerRadiusEnd=3)
        .encode(
            y=alt.Y("cluster:N", sort=clusters_sorted, axis=_axis(t, None, gridColor=None)),
            x=alt.X("share:Q", axis=_axis(t, "share of that cluster's members")),
            color=alt.Color(
                "topic:N",
                scale=alt.Scale(domain=topics, range=SERIES),
                legend=alt.Legend(orient="bottom", columns=5, title=None),
            ),
            order=alt.Order("topic:N"),
            tooltip=[
                "cluster:N",
                "topic:N",
                alt.Tooltip("count:Q", format=","),
                alt.Tooltip("share:Q", format=".1%"),
            ],
        )
    )
    return _spec(chart, t, height=max(180, 46 * len(clusters_sorted)))


# ---------------------------------------------------------------------------
# RQ5 - algorithm timing
# ---------------------------------------------------------------------------


def rq5_timing(facts: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
    """Apriori against FP-Growth on identical input.

    Both return the same rules, so the only variable being measured is time.
    Charting it as a comparison rather than reporting a number makes that
    explicit.
    """
    block = facts["rq5_association_rules"]
    rows = [
        {"algorithm": "Apriori", "seconds": block.get("apriori_seconds")},
        {"algorithm": "FP-Growth", "seconds": block.get("fpgrowth_seconds")},
    ]
    rows = [r for r in rows if r["seconds"] is not None]
    if not rows:
        return {}
    chart = (
        alt.Chart(alt.Data(values=rows))
        .mark_bar(cornerRadiusEnd=5, height=52)
        .encode(
            y=alt.Y("algorithm:N", sort=["Apriori", "FP-Growth"],
                    axis=_axis(t, None, gridColor=None)),
            x=alt.X("seconds:Q", axis=_axis(t, "seconds")),
            color=alt.Color(
                "algorithm:N",
                scale=alt.Scale(domain=["Apriori", "FP-Growth"],
                                range=[t["accent"], t["positive"]]),
                legend=None,
            ),
            tooltip=["algorithm:N", alt.Tooltip("seconds:Q", format=".2f")],
        )
    )
    return _spec(chart, t, height=150)


# ---------------------------------------------------------------------------
# RQ6 - the scatter behind the correlation
# ---------------------------------------------------------------------------


def rq6_returns(points: list[dict[str, Any]], t: dict[str, Any],
                stats: dict[str, Any]) -> dict[str, Any]:
    """Headline volume against daily returns — the null result, as a picture.

    RQ6's most important output is a negative one, and a negative result with no
    chart is indistinguishable from a section that failed. Drawing the cloud
    shows what "no relationship" actually looks like: a full, unstructured
    scatter rather than an absence.
    """
    import math

    rows = []
    for p in points:
        count = p.get("headline_count")
        ret = p.get("return_pct")
        if count and ret is not None and count > 0:
            rows.append({"x": math.log(count), "y": ret,
                         "date": p.get("trade_date", "")})
    if len(rows) < 50:
        return {}
    n = len(rows)
    mean_x = sum(r["x"] for r in rows) / n
    mean_y = sum(r["y"] for r in rows) / n
    sxx = sum((r["x"] - mean_x) ** 2 for r in rows)
    sxy = sum((r["x"] - mean_x) * (r["y"] - mean_y) for r in rows)
    slope = (sxy / sxx) if sxx else 0.0
    intercept = mean_y - slope * mean_x
    fit = [{"x": r["x"], "fit": intercept + slope * r["x"]} for r in rows]

    zero = (
        alt.Chart(alt.Data(values=[{"y": 0.0}]))
        .mark_rule(color=t["axis"], strokeWidth=1)
        .encode(y=alt.Y("y:Q", axis=_axis(t)))
    )
    cloud = (
        alt.Chart(alt.Data(values=rows))
        .mark_circle(size=14, opacity=0.25, color=t["muted"])
        .encode(
            x=alt.X("x:Q", axis=_axis(t, "headlines per day (log scale)")),
            y=alt.Y("y:Q", axis=_axis(t, "daily return (%)")),
            tooltip=[
                "date:T",
                alt.Tooltip("y:Q", title="return %", format=".2f"),
            ],
        )
    )
    line = (
        alt.Chart(alt.Data(values=fit))
        .mark_line(strokeWidth=2.5, color=t["negative"], strokeDash=[6, 4])
        .encode(x=alt.X("x:Q", axis=_axis(t)), y=alt.Y("fit:Q", axis=_axis(t)))
    )
    r = stats.get("pearson_r")
    spec = _spec(alt.layer(zero, cloud, line), t, height=320)
    if isinstance(r, (int, float)):
        spec["dwm_caption"] = f"r = {r:+.4f}"
    return spec


def topic_share(facts: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
    """Share of the window by topic, places and subjects labelled separately.

    `Local` is 70% of the corpus and is a *where*, not a *what*. Splitting the
    bar by `topic_group` keeps a city desk from reading as a subject.
    """
    topics = ((facts["rq1_topic_mix"].get("window_shares") or {}).get("topics")) or []
    rows = [t_ for t_ in topics if t_.get("share_of_window") is not None][:14]
    if not rows:
        return {}
    rows = _label_state(
        rows, "share_of_window", lambda v: v > 0.5,
        "a where, not a what", "a subject",
    )
    names = _sorted_unique([r["topic"] for r in rows])
    chart = (
        alt.Chart(alt.Data(values=rows))
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            y=alt.Y("topic:N", sort=names, axis=_axis(t, None, gridColor=None)),
            x=alt.X("share_of_window:Q", axis=_axis(t, "share of the window")),
            color=_named("a where, not a what", "a subject", t["muted"], t["accent"]),
            tooltip=[
                "topic:N",
                "topic_group:N",
                alt.Tooltip("headline_count:Q", format=","),
                alt.Tooltip("share_of_window:Q", format=".2%"),
            ],
        )
    )
    return _spec(chart, t, height=max(220, 26 * len(rows)))


def rq5_lift_distribution(facts: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
    """The lift distribution across every mined rule.

    A ranked table shows the best rules; a histogram shows the shape, which is
    the thing that says whether the top of the list is a real effect or the tail
    of a distribution that mostly sits at 1.0.

    The bins are computed in the mining stage over all 644 rules, not here: the
    report stores only the top eight informative rules, which is enough to draw
    the strongest and far too few to show a distribution.
    """
    block = facts["rq5_association_rules"]
    rows = block.get("lift_distribution") or []
    if len(rows) < 3:
        return {}
    rows = _label_state(
        rows, "low", lambda v: v > 1.0, "lift above chance", "lift at chance"
    )
    # Ordered by the bin's own lower edge, not alphabetically: "10 or more" would
    # otherwise sort before "1 to 1.1".
    order = [r["range"] for r in sorted(rows, key=lambda r: r["low"])]
    chart = (
        alt.Chart(alt.Data(values=rows))
        .mark_bar(cornerRadiusEnd=3)
        .encode(
            x=alt.X("range:N", sort=order,
                    axis=_axis(t, "lift", labelAngle=-30, gridColor=None)),
            y=alt.Y("count:Q", axis=_axis(t, "rules")),
            color=_named("lift above chance", "lift at chance", t["accent"], t["muted"]),
            tooltip=["range:N", alt.Tooltip("count:Q", format=",d")],
        )
    )
    return _spec(chart, t, height=260)


def rq6_volatility(points: list[dict[str, Any]], t: dict[str, Any],
                   stats: dict[str, Any]) -> dict[str, Any]:
    """Headline volume against 20-day volatility.

    A correlation coefficient on its own hides the shape of the cloud, and the
    shape is what makes a negative slope believable rather than surprising.
    The fitted line is drawn because the reported r was computed on exactly
    these points, and a chart that disagreed with the number beside it would be
    worse than no chart.
    """
    import math

    rows = []
    for p in points:
        count = p.get("headline_count")
        vol = p.get("volatility_20d")
        if count and vol is not None and count > 0:
            rows.append({
                "x": math.log(count),
                "y": vol,
                "date": p.get("trade_date", ""),
                "count": count,
            })
    if len(rows) < 3:
        return {}
    n = len(rows)
    mean_x = sum(r["x"] for r in rows) / n
    mean_y = sum(r["y"] for r in rows) / n
    sxx = sum((r["x"] - mean_x) ** 2 for r in rows)
    sxy = sum((r["x"] - mean_x) * (r["y"] - mean_y) for r in rows)
    slope = sxy / sxx if sxx else 0.0
    intercept = mean_y - slope * mean_x
    line_rows = [
        {"x": r["x"], "fit": intercept + slope * r["x"]} for r in rows
    ]
    points_chart = (
        alt.Chart(alt.Data(values=rows))
        .mark_circle(size=16, opacity=0.3, color=t["accent"])
        .encode(
            x=alt.X("x:Q", axis=_axis(t, "headlines per day (log scale)")),
            y=alt.Y("y:Q", axis=_axis(t, "20-day volatility")),
            tooltip=[
                "date:T",
                alt.Tooltip("count:Q", title="headlines", format=","),
                alt.Tooltip("y:Q", title="volatility", format=".3f"),
            ],
        )
    )
    fit_chart = (
        alt.Chart(alt.Data(values=line_rows))
        .mark_line(strokeWidth=2.5, color=t["negative"], strokeDash=[6, 4])
        .encode(
            x=alt.X("x:Q", axis=_axis(t)),
            y=alt.Y("fit:Q", axis=_axis(t)),
        )
    )
    r = stats.get("pearson_r")
    title = f"r = {r:+.4f}" if isinstance(r, (int, float)) else None
    chart = alt.layer(points_chart, fit_chart)
    return _spec(chart, t, height=340) if title is None else {
        **_spec(chart, t, height=340),
        "dwm_caption": title,
    }


# ---------------------------------------------------------------------------
# RQ7 - confusion matrix
# ---------------------------------------------------------------------------


def rq7_confusion(facts: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
    """Confusion matrix as a heatmap.

    Counts, not a proportion, because the cell sizes are the evidence that the
    minority class was not simply absorbed into the majority one.
    """
    matrix = (facts["rq7_classifier"].get("metrics") or {}).get("confusion_matrix") or {}
    labels = matrix.get("labels") or []
    values = matrix.get("matrix") or []
    if not labels or not values:
        return {}
    total = sum(sum(row) for row in values) or 1
    cells = []
    for i, row in enumerate(values):
        for j, value in enumerate(row):
            cells.append({
                "actual": labels[i] if i < len(labels) else f"row {i}",
                "predicted": labels[j] if j < len(labels) else f"col {j}",
                "count": value,
                "share": value / total,
                "correct": i == j,
            })
    # Fixed domain so the colour ramp means the same thing regardless of the
    # data: a diagonal cell is always the darkest, an off-diagonal always pale.
    scale = alt.Scale(domain=[0.0, 1.0], range=t["heat"])
    # Both axes pinned to the same class order. Without it each takes
    # data-arrival order independently, so the rows can be labelled one way and
    # the columns another - and a confusion matrix whose two axes disagree is
    # not a chart of anything, because the diagonal stops being the diagonal.
    axis_scale = alt.Scale(domain=labels)
    cells_chart = (
        alt.Chart(alt.Data(values=cells))
        .mark_rect(cornerRadius=4)
        .encode(
            x=alt.X("predicted:N", scale=axis_scale,
                    axis=_axis(t, "predicted", gridColor=None)),
            y=alt.Y("actual:N", scale=axis_scale,
                    axis=_axis(t, "actual", gridColor=None)),
            color=alt.Color("share:Q", scale=scale,
                            legend=alt.Legend(orient="bottom", format=".0%")),
            tooltip=[
                "actual:N",
                "predicted:N",
                alt.Tooltip("count:Q", format=","),
                alt.Tooltip("share:Q", format=".2%"),
            ],
        )
    )
    # A text layer, so the counts are readable rather than decoded from a
    # colour. `font` takes the CSS shorthand rather than a separate weight,
    # because Altair's TextMarkDef has no `fontWeight` parameter and passing one
    # raises instead of being ignored.
    labels_chart = cells_chart.mark_text(
        font="600 14px system-ui, -apple-system, Segoe UI, sans-serif",
        color=t["ink"],
    ).encode(
        text=alt.Text("count:Q", format=","),
    )
    return _spec(alt.layer(cells_chart, labels_chart), t, height=180)


# ---------------------------------------------------------------------------
# warehouse
# ---------------------------------------------------------------------------


def warehouse_rows(tables: list[dict[str, Any]], t: dict[str, Any]) -> dict[str, Any]:
    rows = sorted(tables, key=lambda r: r.get("rows", 0), reverse=True)[:14]
    if not rows:
        return {}
    names = _sorted_unique([r["table"] for r in rows])
    chart = (
        alt.Chart(alt.Data(values=rows))
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            y=alt.Y("table:N", sort=names, axis=_axis(t, None, gridColor=None)),
            x=alt.X("rows:Q", axis=_axis(t, "rows")),
            color=alt.Color(
                "table:N",
                scale=alt.Scale(domain=names, range=[t["accent"]] * len(names)),
                legend=None,
            ),
            tooltip=["table:N", alt.Tooltip("rows:Q", format=",")],
        )
    )
    return _spec(chart, t, height=max(200, 22 * len(rows)))


# ---------------------------------------------------------------------------
# the collection
# ---------------------------------------------------------------------------


def build_charts(
    facts: dict[str, Any],
    market_points: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
    theme_name: str = "light",
) -> dict[str, Any]:
    """Every chart on the dashboard, for one theme.

    Returns a name-to-spec mapping. A chart whose data is missing maps to an
    empty spec rather than being omitted, so the front end can tell "not
    applicable" from "failed to load" and say which.
    """
    t = theme(theme_name)
    rq6 = facts.get("rq6_market_association") or {}
    vol_stats = ((rq6.get("volatility") or {}).get("strongest")) or {}
    # The returns correlation is not a `strongest` block: it is reported as the
    # section's own fact, because "the strongest of fifteen lags" is not a
    # meaningful summary of a null.
    return_fact = (rq6.get("fact") or {}).get("value")
    charts: dict[str, Any] = {
        "rq2_event_z": rq2_event_z(facts, t),
        "rq1_topic_mix": rq1_topic_mix(facts, t),
        "rq3_topic_rates": rq3_topic_rates(facts, t),
        "rq3_trend": rq3_trend(facts, t),
        "rq4_silhouette": rq4_silhouette(facts, t),
        "rq4_projection": rq4_projection(facts, t),
        "rq4_cluster_sizes": rq4_cluster_sizes(facts, t),
        "rq4_cluster_topics": rq4_cluster_topics(facts, t),
        "rq5_timing": rq5_timing(facts, t),
        "rq5_lift": rq5_lift_distribution(facts, t),
        "rq7_confusion": rq7_confusion(facts, t),
        "topic_share": topic_share(facts, t),
    }
    if market_points:
        charts["rq6_volatility"] = rq6_volatility(market_points, t, vol_stats)
        charts["rq6_returns"] = rq6_returns(
            market_points, t, {"pearson_r": return_fact}
        )
    if tables:
        charts["warehouse_rows"] = warehouse_rows(tables, t)
    return charts


def available_themes() -> list[str]:
    return sorted(THEMES)
