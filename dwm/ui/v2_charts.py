"""Vega-Lite specs for the v2 dashboard views.

Separate from dwm/ui/charts.py on purpose. The v1 specs are built from
facts.json, which the inference stage renders and which never changes once
published. The v2 views read live warehouse and mining tables, so they are built
here from a connection and served on their own endpoint. Keeping them apart
means a v2 spec can never alter a published v1 chart, and a v1 chart can never
break because a v2 phase has not been run.

Colour follows the project rule set, unchanged from v1:

  * `--accent` is the only accent, and in this product the SUBJECT is news, so
    the news series takes it;
  * price and market are RECESSIVE (`--muted`), so the reader's eye lands on the
    news rather than on the index;
  * `--positive` and `--negative` mean direction and nothing else.

Every spec carries `dwm_caption` where a single number summarises it, which the
front end renders as a badge beside the caption.
"""

from __future__ import annotations

from typing import Any

import altair as alt

from dwm.ui.charts import _axis, _config, theme

__all__ = ["build_v2_charts", "available_themes"]

# Re-exported so v2 code does not reach into the v1 module for the theme name.
from dwm.ui.charts import THEMES as _THEMES  # noqa: E402


def available_themes() -> list[str]:
    return sorted(_THEMES)


def _empty(t: dict[str, Any], why: str) -> dict[str, Any]:
    """A named-but-empty spec, so the front end can say 'not measured'."""
    return {"dwm_empty": why, "dwm_theme": t["name"]}


def _frame(spec: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
    """The properties every v2 spec must carry, whatever else it sets.

    `width="container"` is applied here for the same reason v1 applies it inside
    `_spec()`: it is the one property that is easy to forget, and forgetting it
    does not crash. Vega falls back to a 200px-wide square in the corner of the
    card, which reads as missing data rather than as a sizing mistake.

    Applied centrally rather than at each call site because seven call sites is
    seven chances to be inconsistent, and the failure is invisible.
    """
    spec["width"] = "container"
    spec["autosize"] = {"type": "fit", "contains": "padding"}
    spec["config"] = _config(t)
    spec["dwm_theme"] = t["name"]
    return spec


def _final(spec: dict[str, Any], t: dict[str, Any], caption: str, caution: str) -> dict[str, Any]:
    """`_frame`, plus the caption and caution a chart owes its reader.

    Every v2 spec carries a caption and a caution, not just a title. The design
    rules require a data-generated caption on every chart, and `dwm_caution` is
    how the front end puts the limit next to the figure rather than in a
    footnote.
    """
    spec["dwm_caption"] = caption
    spec["dwm_caution"] = caution
    return _frame(spec, t)


# ---------------------------------------------------------------------------
# 1. Market Mood: drill-across, news against price
# ---------------------------------------------------------------------------


def market_mood(
    rows: list[dict[str, Any]],
    t: dict[str, Any],
    caption: str = "",
    lag: int = 0,
) -> dict[str, Any]:
    """News count and sector return on one time axis.

    Two scales on one axis pair is the classic drill-across picture, and the
    usual way it misleads: two y-axes can be scaled so any two series look
    correlated. Here the series are not independently rescaled, and the caption
    states the lag being shown, so the reader is comparing SHAPE and should
    read the caption before drawing a conclusion from it.
    """
    if not rows:
        return _empty(t, "no sector-day rows; run `python -m dwm link`")

    frame = alt.Data(values=rows)

    news = alt.Chart(frame).mark_line(color=t["accent"], strokeWidth=1.6).encode(
        x=alt.X("session:T", title="Period", axis=_axis(t)),
        y=alt.Y(
            "news_count:Q",
            title="Headline-days in the sector",
            axis=_axis(t, title=None),
        ),
        tooltip=[
            alt.Tooltip("session:T", title="Period"),
            alt.Tooltip("news_count:Q", title="Headline-days", format=",.1f"),
            alt.Tooltip("days:Q", title="Sessions in the period"),
            alt.Tooltip("stock_obs:Q", title="Stock-days priced"),
        ],
    )

    price = alt.Chart(frame).mark_line(
        color=t["muted"], strokeWidth=1.2, strokeDash=[3, 2]
    ).encode(
        x=alt.X("session:T", axis=_axis(t)),
        y=alt.Y(
            "mean_return_pct:Q",
            title="Mean return, %",
            axis=_axis(t, title=None),
            scale=alt.Scale(zero=False),
        ),
        tooltip=[
            alt.Tooltip("session:T", title="Period"),
            alt.Tooltip("mean_return_pct:Q", title="Mean return %", format="+.3f"),
        ],
    )

    layered = (news + price).properties(height=300).configure_view(stroke=None)
    spec = layered.to_dict()
    spec["config"] = _config(t)
    spec["dwm_theme"] = t["name"]
    spec["dwm_caption"] = caption or f"{len(rows):,} sessions"
    lag_note = (
        "news and price on the same session"
        if lag == 0
        else f"news SHIFTED {abs(lag)} session(s) "
             f"{'later' if lag > 0 else 'earlier'} than price"
    )
    spec["dwm_caution"] = (
        f"Drill-across: {lag_note}. News is the accent, price is the recessive "
        f"line. Different units on one time axis, so only the SHAPE is being "
        f"compared. News volume is not sentiment, and neither one moves the "
        f"other in any way this chart can show."
    )
    return spec


# ---------------------------------------------------------------------------
# 2. Stock Explorer
# ---------------------------------------------------------------------------


def stock_price(points: list[dict[str, Any]], t: dict[str, Any], symbol: str) -> dict[str, Any]:
    """One stock's adjusted close with mentions marked underneath."""
    if not points:
        who = symbol or "that symbol"
        return _empty(
            t,
            f"no price history for {who} inside the window. "
            f"Run `python -m dwm ingest --dataset stocks`",
        )
    frame = alt.Data(values=points)
    with_mentions = alt.Chart(frame).mark_bar(
        color=t["caution"], opacity=0.55
    ).encode(
        x=alt.X("trade_date:T", title="Session", axis=_axis(t)),
        y=alt.Y("mentions:Q", title="Headlines naming it", axis=_axis(t, title=None)),
    )
    price = alt.Chart(frame).mark_line(color=t["accent"], strokeWidth=1.5).encode(
        x=alt.X("trade_date:T", axis=_axis(t)),
        y=alt.Y("adj_close:Q", title="Adjusted close", axis=_axis(t, title=None),
                scale=alt.Scale(zero=False)),
        tooltip=[
            alt.Tooltip("trade_date:T", title="Session"),
            alt.Tooltip("adj_close:Q", title="Adjusted close", format=",.2f"),
            alt.Tooltip("return_pct:Q", title="Return %", format="+.3f"),
            alt.Tooltip("volatility_20d:Q", title="20-day volatility %", format=".2f"),
        ],
    )
    spec = (price + with_mentions).properties(height=300).configure_view(stroke=None).to_dict()
    spec["config"] = _config(t)
    spec["dwm_theme"] = t["name"]
    spec["dwm_caption"] = f"{len(points):,} sessions"
    spec["dwm_caution"] = (
        "Returns are percentage points from adjusted closes, and the adjusted "
        "series is dividend-corrected while the raw close is not."
    )
    return spec


# ---------------------------------------------------------------------------
# 3. Sector Cube: pivot and heatmap
# ---------------------------------------------------------------------------


def sector_heatmap(rows: list[dict[str, Any]], t: dict[str, Any], measure: str) -> dict[str, Any]:
    """Sector by year for the chosen measure, as a heatmap."""
    if not rows:
        return _empty(t, "cube_sector_month is empty; run `python -m dwm link`")

    points: list[dict[str, Any]] = []
    for row in rows:
        for key, value in row.items():
            if key.startswith("p") and value is not None:
                points.append(
                    {"sector": row["sector"], "year": key[1:], "value": value}
                )
    if not points:
        return _empty(t, "the chosen measure produced no cells")

    chart = (
        alt.Chart(alt.Data(values=points))
        .mark_rect()
        .encode(
            x=alt.X("year:N", title="Year", axis=_axis(t), sort=None),
            y=alt.Y("sector:N", title=None, axis=_axis(t), sort="-x"),
            color=alt.Color(
                "value:Q",
                scale=alt.Scale(range=t["heat"]),
                legend=alt.Legend(title=measure, orient="right"),
            ),
            tooltip=[
                alt.Tooltip("sector:N", title="Sector"),
                alt.Tooltip("year:N", title="Year"),
                alt.Tooltip("value:Q", title=measure, format=",.3f"),
            ],
        )
        .properties(height={"step": 26})
        .configure_view(stroke=None)
    )
    spec = chart.to_dict()
    spec["config"] = _config(t)
    spec["dwm_theme"] = t["name"]
    spec["dwm_caption"] = f"{len(points):,} cells"
    spec["dwm_caution"] = (
        "Darker is more. Cells are means derived from stored sums and counts, "
        "so they can be added up correctly across levels."
    )
    return spec


# ---------------------------------------------------------------------------
# 4. Event Study
# ---------------------------------------------------------------------------


def event_car(bars: list[dict[str, Any]], t: dict[str, Any]) -> dict[str, Any]:
    """Cumulative abnormal return by sector, signed around zero."""
    if not bars:
        return _empty(t, "no valid event-study result; run `python -m dwm mine --op events`")

    chart = (
        alt.Chart(alt.Data(values=bars))
        .mark_bar()
        .encode(
            x=alt.X(
                "mean_car_0_5:Q",
                title="Mean CAR over the five sessions from the event, %",
                axis=_axis(t),
            ),
            y=alt.Y("sector:N", title=None, axis=_axis(t), sort="-x"),
            color=alt.condition(
                alt.datum.mean_car_0_5 > 0,
                alt.value(t["positive"]),
                alt.value(t["negative"]),
            ),
            tooltip=[
                alt.Tooltip("sector:N", title="Sector"),
                alt.Tooltip("mean_car_0_5:Q", title="Mean CAR[0,+5] %", format="+.3f"),
                alt.Tooltip("n_stocks:Q", title="Stocks"),
            ],
        )
        .properties(height={"step": 26})
        .configure_view(stroke=None)
    )
    spec = chart.to_dict()
    spec["config"] = _config(t)
    spec["dwm_theme"] = t["name"]
    tested = sum(1 for b in bars if (b.get("n_stocks") or 0) >= 3)
    spec["dwm_caption"] = f"{len(bars)} sectors · {tested} testable"
    spec["dwm_caution"] = (
        "Abnormal means beyond what the market model explains, not caused by "
        "the event. Sectors with fewer than three stocks carry no t-test and "
        "are shown without one."
    )
    return spec


# ---------------------------------------------------------------------------
# 5. Mining Lab
# ---------------------------------------------------------------------------


def cluster_scatter(points: list[dict[str, Any]], t: dict[str, Any]) -> dict[str, Any]:
    """PCA projection of the stock clusters."""
    if not points:
        return _empty(t, "no clusters; run `python -m dwm mine --op stockclusters`")

    chart = (
        alt.Chart(alt.Data(values=points))
        .mark_circle(size=110, opacity=0.85, stroke=t["surface"], strokeWidth=1)
        .encode(
            x=alt.X("pc1:Q", title="First principal component", axis=_axis(t)),
            y=alt.Y("pc2:Q", title="Second", axis=_axis(t)),
            color=alt.Color(
                "cluster_id:N",
                scale=alt.Scale(range=t["series"]),
                legend=alt.Legend(title="Cluster", orient="right"),
            ),
            tooltip=[
                alt.Tooltip("symbol:N", title="Symbol"),
                alt.Tooltip("cluster_id:N", title="Cluster"),
                alt.Tooltip("sector:N", title="Sector"),
                alt.Tooltip("ann_volatility:Q", title="Ann. volatility %", format=".2f"),
                alt.Tooltip("beta:Q", title="Beta", format=".2f"),
            ],
        )
        .properties(height=340)
        .configure_view(stroke=None)
    )
    spec = chart.to_dict()
    spec["config"] = _config(t)
    spec["dwm_theme"] = t["name"]
    spec["dwm_caption"] = f"{len(points)} stocks"
    spec["dwm_caution"] = (
        "Similar behaviour, not similar business. The axes are principal "
        "components, so their scales are arbitrary and only the shape carries "
        "meaning."
    )
    return spec


def direction_metrics(models: list[dict[str, Any]], t: dict[str, Any],
                      majority: float | None, yesterday: float | None) -> dict[str, Any]:
    """Test accuracy against both baselines, as bars."""
    if not models:
        return _empty(
            t,
            "the direction model has not been run. "
            "Run `python -m dwm mine --op direction`",
        )

    points = [
        {
            "model": m["model"],
            "accuracy": m.get("accuracy"),
            "kind": "model",
        }
        for m in models
    ]
    if majority is not None:
        points.append({"model": "majority class", "accuracy": majority, "kind": "baseline"})
    if yesterday is not None:
        points.append({"model": "same as yesterday", "accuracy": yesterday, "kind": "baseline"})

    chart = (
        alt.Chart(alt.Data(values=points))
        .mark_bar()
        .encode(
            x=alt.X("accuracy:Q", axis=alt.Axis(format=".0%", title="Accuracy on held-out data"),
                    scale=alt.Scale(domain=[0.45, 1.0])),
            y=alt.Y("model:N", title=None, axis=_axis(t), sort="-x"),
            color=alt.condition(
                alt.datum.kind == "baseline",
                alt.value(t["muted"]),
                alt.value(t["accent"]),
            ),
            tooltip=[
                alt.Tooltip("model:N", title="Model"),
                alt.Tooltip("accuracy:Q", title="Accuracy", format=".2%"),
                alt.Tooltip("kind:N", title="Kind"),
            ],
        )
        .properties(height={"step": 30})
        .configure_view(stroke=None)
    )
    spec = chart.to_dict()
    spec["config"] = _config(t)
    spec["dwm_theme"] = t["name"]
    spec["dwm_caption"] = "grey = baseline"
    spec["dwm_caution"] = (
        "If every bar is at or below the grey baselines, these features carry "
        "no usable skill. That is a result, not a failure to tune."
    )
    return spec


# ---------------------------------------------------------------------------
# 6. Checker: score against the corpus distribution
# ---------------------------------------------------------------------------


def checker_percentile(hist: list[dict[str, Any]], t: dict[str, Any],
                       score: float) -> dict[str, Any]:
    """Where this headline sits among the corpus's own scores."""
    if not hist:
        return _empty(
            t,
            "no score distribution available: the headline table is empty. "
            "Run `python -m dwm build`",
        )

    points = [{"bin": h["bin"], "count": h["count"], "kind": "hist"} for h in hist]
    # The marker is the band the submitted headline falls in, named rather than
    # plotted at a height: the bar height would have to be invented, and an
    # invented height on a frequency axis is a fabricated count. `score` is None
    # until a headline has actually been submitted, and the chart is a plain
    # distribution before that rather than showing a placeholder at zero.
    if score is not None:
        lo, hi = (hist[0]["bin"].split("-") + [""])[:2]
        try:
            target = int((float(score) - float(lo)) / max(float(hi) - float(lo), 1e-9) * len(hist))
        except (TypeError, ValueError):
            target = -1
        label = (
            next((h["bin"] for i, h in enumerate(hist) if i == target), hist[-1]["bin"])
            if 0 <= target < len(hist)
            else f"outside the corpus range ({score:.3f})"
        )
        points.append({"bin": f"yours → {label}", "count": 0, "kind": "mark"})

    chart = (
        alt.Chart(alt.Data(values=points))
        .mark_bar()
        .encode(
            x=alt.X("bin:N", title="Sensational-score band", axis=_axis(t)),
            y=alt.Y("count:Q", title="Headlines in the corpus", axis=_axis(t, title=None)),
            color=alt.condition(
                alt.datum.kind == "mark",
                alt.value(t["caution"]),
                alt.value(t["accent"]),
            ),
            opacity=alt.condition(alt.datum.kind == "mark", alt.value(1.0), alt.value(0.7)),
            tooltip=[
                alt.Tooltip("bin:N", title="Band"),
                alt.Tooltip("count:Q", title="Headlines"),
            ],
        )
        .properties(height=260)
        .configure_view(stroke=None)
    )
    spec = chart.to_dict()
    spec["config"] = _config(t)
    spec["dwm_theme"] = t["name"]
    spec["dwm_caption"] = "scoring style, not truth"
    spec["dwm_caution"] = (
        "The band a headline falls in says how its writing compares with this "
        "corpus. It says nothing about whether the claim is true."
    )
    return spec


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------


def build_v2_charts(
    *,
    theme_name: str = "light",
    mood_rows: list[dict[str, Any]] | None = None,
    mood_caption: str = "",
    lag: int = 0,
    stock_points: list[dict[str, Any]] | None = None,
    symbol: str = "",
    cube_rows: list[dict[str, Any]] | None = None,
    measure: str = "news_count",
    event_bars: list[dict[str, Any]] | None = None,
    cluster_points: list[dict[str, Any]] | None = None,
    direction: list[dict[str, Any]] | None = None,
    majority: float | None = None,
    yesterday: float | None = None,
    percentile_hist: list[dict[str, Any]] | None = None,
    headline_score: float | None = None,
) -> dict[str, Any]:
    """Every v2 chart, keyed by the name the front end mounts.

    A chart whose inputs are absent maps to a named-empty spec rather than
    being omitted, so the front end can tell "not measured" from "failed to
    load" and say which.
    """
    t = theme(theme_name)
    charts: dict[str, Any] = {
        "v2_market_mood": market_mood(mood_rows or [], t, mood_caption, lag),
        "v2_stock_price": stock_price(stock_points or [], t, symbol),
        "v2_sector_heatmap": sector_heatmap(cube_rows or [], t, measure),
        "v2_event_car": event_car(event_bars or [], t),
        "v2_cluster_scatter": cluster_scatter(cluster_points or [], t),
        "v2_direction_metrics": direction_metrics(
            direction or [], t, majority, yesterday
        ),
        "v2_checker_percentile": checker_percentile(
            percentile_hist or [], t, headline_score
        ),
    }
    # The single point every spec passes through, so a chart added later cannot
    # ship without `width="container"` and the shared config. Each chart still
    # writes its own caption and caution, because those are specific to what it
    # shows and cannot be derived here.
    for spec in charts.values():
        if not spec.get("dwm_empty"):
            _frame(spec, t)
    return charts
