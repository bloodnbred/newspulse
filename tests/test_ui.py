"""Verification for the dashboard front end.

**These tests exist because the build had no way to see the page it was
producing.** Everything here checks something that is invisible in a passing
test suite and obvious on screen: a colour variable that was never defined, a
chart name that does not exist, a data path that silently renders "n/a", text
too faint to read.

That last class is the expensive one. A wrong key in a JavaScript template does
not raise — it renders `undefined`, or an em dash, or nothing. The page still
loads, the server still returns 200, and every other test still passes. Each of
those bugs shipped once during this build and was caught here rather than by a
reader.

One of them was not cosmetic. `build_outcomes` emitted its per-outcome caveat
under the key `caveat` while the rest of the project spells it `caution`, so
**every caution on the conclusions page silently rendered as absent** — on the
single page whose entire job is to state what may not be claimed. Nothing
raised. The only way it surfaced was asserting that each outcome carries one.

The four families:

* **Wiring** — every CSS variable is defined, every element id the JavaScript
  reaches for exists, every chart the JavaScript names is served.
* **Data paths** — every dotted path the front end reads exists in the real
  payload. Asserted against what mining actually produces, not a fixture,
  because the bug is a mismatch with reality.
* **Contrast** — WCAG AA for the text pairs in both themes.
* **Honesty** — no figure anywhere in the payload has a unit and no caution.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "dashboard" / "static"
CSS = STATIC / "app.css"
JS = STATIC / "app.js"
#: The v2 views. A separate file so app.js stays readable, which means every
#: check that greps the front end has to consult two files rather than one.
V2_JS = STATIC / "v2.js"
HTML = STATIC / "index.html"

FACTS = ROOT / "reports" / "facts.json"

WAREHOUSE = ROOT / "warehouse" / "dwm.duckdb"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _served_v2_chart_names() -> set[str]:
    """Chart names the v2 spec builder can produce.

    Derived from the builder's own output rather than hard-coded, so a chart
    added to `build_v2_charts` is covered here without touching this file. The
    names are the same with or without data because an empty input still yields
    a named-empty spec - which is the point: the name is always served.
    """
    from dwm.ui.v2_charts import build_v2_charts

    return set(build_v2_charts())


def _facts_from_warehouse():
    """The real payload, or skip.

    Some checks need the warehouse rather than the checked-in results, because
    they exercise the whole request path. The test session points
    `DWM_REPORTS_DIR` at a temporary directory, so these skip unless the
    warehouse has been built - with a reason that says what to run.
    """
    from dwm.api import store

    try:
        return store.load_facts()
    except Exception as exc:  # noqa: BLE001 - any failure means "not available"
        pytest.skip(f"needs the built warehouse (`dwm mine`): {exc}")


def _all_v1_charts() -> dict:
    """Every chart the endpoint actually serves.

    Built the way `/ui/charts` builds them, with the same optional inputs. That
    detail is the whole point of this helper: three of the twelve v1 charts -
    `rq6_returns`, `rq6_volatility`, `warehouse_rows` - are registered only
    when market points or table counts are supplied, so a test that calls
    `build_charts(facts)` alone concludes they are never served and reports a
    false alarm against working code. Mirroring the endpoint's arguments is
    what makes the check honest in both directions.
    """
    from dwm.api import store
    from dwm.ui.charts import build_charts

    facts = _facts_from_warehouse()
    try:
        points = store.market_daily()["points"]
    except Exception:  # noqa: BLE001 - a chart that needs no market data
        points = []
    try:
        tables = store.table_counts()
    except Exception:  # noqa: BLE001
        tables = []
    return build_charts(facts, market_points=points, tables=tables)


needs_warehouse = pytest.mark.usefixtures("_noop")


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------


def test_static_files_exist() -> None:
    for path in (HTML, CSS, JS):
        assert path.is_file(), f"missing {path}"
    assert "<style" not in _text(HTML), "styles belong in app.css, not inline"


def test_every_css_variable_is_defined() -> None:
    """An undefined `var(--x)` is not an error. It renders nothing.

    The quietest possible defect: the stylesheet parses, the page loads, and one
    property silently does not apply. Devtools would show it immediately; a test
    suite has to be told to look.
    """
    css = _text(CSS)
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", css))
    used = set(re.findall(r"var\((--[a-z0-9-]+)", css))
    missing = used - defined
    assert not missing, f"used but never defined: {sorted(missing)}"

    # Every core token must exist in both themes. Checked by name so a rename
    # that breaks a theme is caught rather than silently redefined once.
    core = {
        "canvas", "surface", "surface-2", "surface-sunk", "ink", "muted", "faint",
        "hairline", "hairline-firm", "accent", "accent-soft", "positive",
        "positive-soft", "caution", "caution-soft", "caution-edge", "negative",
    }
    names = {f"--{name}" for name in core}
    assert not (names - defined), f"missing core tokens: {sorted(names - defined)}"


def test_every_element_id_the_js_reaches_for_exists() -> None:
    """`$("#foo")` on an undeclared id returns null, and the failure surfaces
    on some unrelated-looking line rather than at the selector."""
    in_html = set(re.findall(r'id="([^"]+)"', _text(HTML)))
    # Ids the JavaScript creates when it renders a section, rather than declaring
    # them in the static markup. Listing them is the price of a client-rendered
    # view: this is the list a missing id has to be added to.
    runtime = {
        "explore-headlines", "ex-topic", "ex-limit", "ex-offset", "ex-go",
        "ex-csv", "ex-results",
        "explore-series", "sr-topic", "sr-go", "sr-csv", "sr-results",
        "ol-op", "ol-topic", "ol-year", "ol-run", "ol-results",
        "ol-grid", "ol-detail",
        "pipe-runs", "wh-tables", "how-tables",
        "report-body", "dl-report",
    }
    js = _text(JS)
    used = set(re.findall(r'\$\("#([a-zA-Z0-9_-]+)"\)', js))
    # Chart hosts are `chart-<name>` and `chart-<id>`, built at render time.
    used = {u for u in used if not u.startswith("chart-")}
    missing = used - in_html - runtime
    assert not missing, f"JavaScript reaches for ids not declared anywhere: {sorted(missing)}"


def test_every_chart_the_js_names_is_served() -> None:
    """A chart name with no matching spec renders the error box instead.

    That is deliberate — better than a blank rectangle — but it means a typo
    becomes a permanent error message on a live page, so the names are asserted
    against what the endpoint actually returns.
    """
    from dwm.ui.charts import build_charts  # noqa: F401 - documents the source

    served = set(_all_v1_charts())

    # The v2 views live in their own file and their own spec builder, so the
    # check has to consult both. A v2 chart name with no spec would render the
    # error box on a live page just as a v1 one would.
    served |= _served_v2_chart_names()

    named = set(re.findall(r'chart\(\s*"([a-z0-9_]+)"', _text(JS))) | set(
        re.findall(r'chart\(\s*"([a-z0-9_]+)"', _text(V2_JS))
    )
    # `__series` is assembled in the browser for the browse view only. It has no
    # published figure behind it, so it has no spec on the server.
    named.discard("__series")

    missing = named - served
    assert not missing, f"the front end asks for charts that are not served: {sorted(missing)}"

    # And the reverse: a spec shipped but never rendered is dead weight on the
    # wire, and usually means a chart was built and then forgotten.
    unused = served - named
    assert not unused, f"charts are served but never rendered: {sorted(unused)}"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_chart_fills_its_frame(theme: str) -> None:
    """A chart with no explicit width renders at Vega's 200px default.

    That is not a crash: it draws correctly, as a small square in the corner of
    its card. On the first build every chart on the dashboard did this, and it
    read as missing data rather than as a sizing mistake.
    """
    from dwm.ui.v2_charts import build_v2_charts

    specs = dict(_all_v1_charts())
    specs.update(build_v2_charts(theme_name=theme))
    for name, spec in specs.items():
        if not spec or spec.get("dwm_empty"):
            continue
        blob = json.dumps(spec)
        assert '"width": "container"' in blob, f"{name} ({theme}) has no container width"
        assert "autosize" in blob, f"{name} ({theme}) has no autosize, so it will not fit"


def _js_without_comments(source: str) -> str:
    """Strip `//` and block comments.

    Without this the test is worthless in a subtle way: the docstring above
    *mentions* `wireOlap`, so dropping its call still leaves two references and
    the check passes. Verified by reintroducing the bug and watching it pass.
    """
    without_block = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", " ", without_block)


def test_no_local_declaration_shadows_a_global_helper() -> None:
    """A shadowed global is a landmine that fires on one code path only.

    `olapColumns` declared `const num = isNumber && ...`, shadowing the global
    `num()` formatter. The closures below then called a boolean and threw
    "num is not a function" — for every integer column, which is every column in
    every OLAP result. The rate branch survived because it calls `pct()`, so the
    panel half-worked.

    `node --check` passes, no render test touches it, and the failure only appears
    when someone clicks a card. Hence a static check.
    """
    code = _js_without_comments(_text(JS))
    helpers = set(re.findall(r"^function (\w+)\(", code, flags=re.MULTILINE))
    helpers |= set(re.findall(r"^async function (\w+)\(", code, flags=re.MULTILINE))
    assert len(helpers) > 40, (
        f"only {len(helpers)} helpers found; the pattern is probably wrong and "
        f"this would pass vacuously"
    )

    locals_ = set(re.findall(r"\b(?:const|let)\s+(\w+)\s*=", code))
    shadowed = sorted(helpers & locals_)
    assert not shadowed, (
        f"locals shadow top-level helpers: {shadowed}. A call to one of these "
        f"inside that scope invokes the local instead, which fails on exactly "
        f"one code path and nowhere else."
    )


def _extract_js(source: str, *names: str) -> str:
    """Pull named top-level functions out of the script by brace matching.

    Slicing the file between comment markers worked once and broke the moment a
    marker moved, so the functions are located by name instead. Only the pure
    helpers are taken; the DOM-heavy app body never runs, which is the point —
    there is no browser here.
    """
    out = []
    for name in names:
        match = re.search(rf"^function {re.escape(name)}\s*\(", source, flags=re.MULTILINE)
        assert match, f"{name} not found in app.js"
        depth = 0
        closed = None
        for i in range(source.index("{", match.start()), len(source)):
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
                if depth == 0:
                    closed = i
                    break
        assert closed is not None, f"unbalanced braces extracting {name}"
        out.append(source[match.start():closed + 1])
    return "\n\n".join(out)


def test_every_column_type_gets_a_working_formatter() -> None:
    """All four `olapColumns` branches must produce a callable.

    Only the rate branch was ever reachable while the others threw, so a test
    that merely checked the function returned something would have passed. This
    runs the real JavaScript under Node, calls every branch, and asserts the
    output — the only way to know the formatter works without a browser.
    """
    helpers = _extract_js(_text(JS), "esc", "num", "pct", "olapColumns")
    script = helpers + """
const rows = [
  {topic_name: 'Business', headline_count: 57532, sensational_rate: 0.0567,
   sum_sentiment_compound: -1234.5678, sentiment_band: null,
   is_trading_day: true, year_month: '2017-01', share_of_year: 0.4302,
   year_no: 2017, month_no: 1}
];
process.stdout.write(JSON.stringify(
  olapColumns(rows, Object.keys(rows[0]))
    .map(c => ({key: c.key, num: c.num, out: c.html ? c.html(rows[0]) : null}))
));"""
    node = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True,
        cwd=str(ROOT), timeout=60,
    )
    assert node.returncode == 0, f"node failed: {node.stderr[:500]}"
    columns = {c["key"]: c for c in json.loads(node.stdout)}

    assert set(columns) >= {
        "topic_name", "headline_count", "sensational_rate", "share_of_year",
        "sum_sentiment_compound", "sentiment_band", "is_trading_day", "year_month",
    }, f"missing columns: {sorted(columns)}"

    # Integers, thousands-separated. This is the branch that threw.
    assert columns["headline_count"]["out"] == "57,532", columns["headline_count"]
    assert columns["headline_count"]["num"] is True

    # Rates as percentages.
    assert columns["sensational_rate"]["out"] == "5.67%", columns["sensational_rate"]
    assert columns["share_of_year"]["out"] == "43.02%", columns["share_of_year"]

    # Sums keep four decimals.
    assert columns["sum_sentiment_compound"]["out"] == "-1,234.5678", (
        columns["sum_sentiment_compound"]
    )

    # A null band renders as a pill, not the word "null".
    assert "no band" in columns["sentiment_band"]["out"], columns["sentiment_band"]

    # Booleans render as pills.
    assert "trading" in columns["is_trading_day"]["out"], columns["is_trading_day"]

    # Dates are not numbers: neither formatted nor right-aligned.
    assert columns["year_month"]["num"] is False

    # A calendar year is a number but not a quantity. `2,015` is worse than
    # `2015`, and it appeared in three of the ten tables before this was caught.
    assert columns["year_no"]["out"] is None, columns["year_no"]
    assert columns["year_no"]["num"] is False, columns["year_no"]
    assert columns["month_no"]["out"] is None, columns["month_no"]

    # Plain text has no formatter.
    assert columns["topic_name"]["out"] is None
    assert columns["topic_name"]["num"] is False


def test_the_olap_renderer_only_reads_fields_the_endpoint_sends() -> None:
    """`renderOlapResult` reads five fields; all five must be served.

    A missing one does not throw in a way that names itself — it renders
    `undefined` into the page, or silently omits the reading. Checked against
    the live payload rather than a fixture.
    """
    import json as _json
    import urllib.request

    base = os.environ.get("DWM_API", "http://127.0.0.1:8000")
    try:
        with urllib.request.urlopen(f"{base}/query/roll_up", timeout=40) as response:
            payload = _json.load(response)
    except Exception as exc:  # noqa: BLE001 - any failure means "not running"
        pytest.skip(f"needs the API: {exc}")

    needed = set(re.findall(r"result\.(\w+)", _text(JS)))
    assert needed, "the pattern found no result.* accesses; it is probably stale"
    missing = sorted(needed - set(payload))
    assert not missing, f"the renderer reads fields the endpoint does not send: {missing}"


def test_every_wired_function_is_actually_called() -> None:
    """A function that is defined but never used is invisible.

    `wireOlap` was defined, and its call was dropped while replacing the old OLAP
    form — so the ten operation cards rendered, clicking one did nothing, and
    nothing failed: `node --check` passes, the endpoint works, every render test
    passes, and the panel is simply inert.

    Counted by *reference*, not by syntactic call, because a function can be
    reached several ways: called directly, used as a fallback after `||`, or
    stored as a value in the tab-dispatch table. The one thing all of them have
    in common is that the name appears in **code**, somewhere other than its
    definition — hence the comment strip.
    """
    js = _text(JS)
    code = _js_without_comments(js)

    defined = set(re.findall(r"^function (\w+)\(", code, flags=re.MULTILINE))
    defined |= set(re.findall(r"^async function (\w+)\(", code, flags=re.MULTILINE))
    assert len(defined) > 40, (
        f"found only {len(defined)} top-level functions; the pattern is probably "
        f"wrong and this test would pass vacuously"
    )

    dead = []
    for name in sorted(defined):
        references = len(re.findall(rf"\b{re.escape(name)}\b", code))
        if references <= 1:  # the definition itself
            dead.append(name)
    assert not dead, (
        f"defined but never referenced: {dead}. Dead code, or — worse — a "
        f"listener that was wired and then orphaned, which renders a control "
        f"that does nothing."
    )


def test_every_explore_tab_is_reachable() -> None:
    """Every tab in the Explore list must dispatch to something real.

    A tab whose body function does not exist renders a blank panel with no
    error, which is the same class of failure as an orphaned listener.
    """
    js = _text(JS)
    body = re.search(
        r"const body = \{(.*?)\}\[tab\]", js, flags=re.DOTALL
    )
    assert body, "the Explore tab dispatch table was not found"
    entries = re.findall(r"(\w+)\s*:\s*(\w+)", body.group(1))
    assert len(entries) >= 4, f"only {len(entries)} Explore tabs are dispatched"

    for tab_name, fn in entries:
        assert fn in js, f"the {tab_name!r} tab points at {fn!r}, which does not exist"
        assert re.search(rf"function {re.escape(fn)}\s*\(", js), (
            f"the {tab_name!r} tab points at {fn!r}, which is never defined"
        )

    # And the tab list the user sees must match the dispatch table, or a tab
    # falls through to the default and silently shows the wrong panel.
    listed = re.search(
        r'pageExplore = \(\).*?tabs\(\[(.*?)\]', js, flags=re.DOTALL
    )
    if listed:
        names = re.findall(r'"([^"]+)"', listed.group(1))
        dispatched = {tab_name for tab_name, _ in entries}
        missing = [n for n in names if n not in dispatched]
        assert not missing, f"tabs shown but not dispatched: {missing}"


def test_the_document_declares_a_theme_before_first_paint() -> None:
    """A flash of the wrong theme reads as a broken page.

    The script must be in `<head>` and before any body content, because anything
    later runs after something has already been painted.
    """
    html = _text(HTML)
    head, _, _ = html.partition("</head>")
    assert "dataset.theme" in head, "the theme script is not in <head>"
    assert "prefers-color-scheme" in head, "the OS preference is not consulted"
    assert 'data-theme="light"' in html[:300], "the root element declares no default theme"
    assert html.index("dataset.theme") < html.index("<body"), (
        "the theme script runs after the body, so the page paints once in the "
        "wrong theme first"
    )


def test_both_themes_define_the_same_tokens() -> None:
    """A token defined only for light means dark silently falls back to
    something the browser guesses. That is how a half-themed page happens."""
    css = _text(CSS)
    light = set(re.findall(r"(--[a-z0-9-]+)\s*:", _block(css, '[data-theme="light"]')))
    dark = set(re.findall(r"(--[a-z0-9-]+)\s*:", _block(css, '[data-theme="dark"]')))
    assert not (light - dark), f"defined for light but not dark: {sorted(light - dark)}"


# ---------------------------------------------------------------------------
# contrast
# ---------------------------------------------------------------------------


def _block(css: str, selector: str) -> str:
    """The body of one rule, brace-matched."""
    start = css.index(selector)
    body = css[css.index("{", start) + 1:]
    depth = 1
    for i, ch in enumerate(body):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return body[:i]
    return body


def _tokens(css: str, selector: str) -> dict[str, str]:
    return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", _block(css, selector)))


def _rgb(colour: str) -> tuple[int, int, int]:
    value = colour.strip()
    if value.startswith("#") and len(value) == 7:
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    match = re.match(r"rgba?\(([^)]+)\)", value)
    if match:
        return tuple(int(float(p)) for p in match.group(1).split(",")[:3])  # type: ignore[return-value]
    raise ValueError(f"cannot parse colour {colour!r}")


def _luminance(colour: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(colour)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(fg: str, bg: str) -> float:
    a, b = _luminance(fg), _luminance(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


# Text pairs that must be legible. The third value is the minimum ratio: 4.5 for
# body text, 3.0 for large or non-essential text such as axis labels.
CONTRAST_PAIRS = [
    ("ink on canvas", "--ink", "--canvas", 4.5),
    ("ink on surface", "--ink", "--surface", 4.5),
    ("ink on surface-2", "--ink", "--surface-2", 4.5),
    ("muted on surface", "--muted", "--surface", 4.5),
    ("muted on canvas", "--muted", "--canvas", 4.5),
    ("muted on surface-2", "--muted", "--surface-2", 4.5),
    ("faint on surface", "--faint", "--surface", 3.0),
    ("accent on surface", "--accent", "--surface", 4.5),
    ("accent on accent-soft", "--accent", "--accent-soft", 4.5),
    ("positive on surface", "--positive", "--surface", 4.5),
    ("positive on positive-soft", "--positive", "--positive-soft", 4.5),
    ("caution on surface", "--caution", "--surface", 3.0),
    ("caution on caution-soft", "--caution", "--caution-soft", 4.5),
]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_text_contrast_meets_wcag_aa(theme: str) -> None:
    """This is the check that would have caught black widgets on a light page.

    It cannot see a *framework* overriding a colour, but it can see a palette
    that was never built for the theme it claims to serve, which is how that
    failure started: a light canvas with dark widgets bolted on it.
    """
    tokens = _tokens(_text(CSS), f'[data-theme="{theme}"]')
    failures = []
    for label, fg, bg, minimum in CONTRAST_PAIRS:
        if fg not in tokens or bg not in tokens:
            failures.append(f"{label}: token missing in {theme}")
            continue
        ratio = _contrast(tokens[fg], tokens[bg])
        if ratio < minimum:
            failures.append(
                f"{label}: {ratio:.2f}:1 in {theme}, needs {minimum}:1 "
                f"({tokens[fg]} on {tokens[bg]})"
            )
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_plain_layer_components_are_all_styled(theme: str) -> None:
    """The plain layer's components must exist as classes in both themes.

    Every one of these was added at once and each is a new surface, so each is a
    new chance to have readable text on an unstyled or wrong-coloured box.
    Asserted on class names rather than on rendered pixels because there is no
    browser here — the same reasoning as the token check.
    """
    css = _text(CSS)
    for selector in (
        ".intro", ".intro__p", ".finding", ".finding__claim", ".finding__body",
        ".finding__sowhat", ".finding__sowhatlabel", ".plainread",
        ".plainread__body", ".scale", ".glossary__grid", ".gloss__term",
        ".gloss__plain", ".term", ".summary__why", ".clustergloss__plain",
    ):
        assert selector in css, f"no rule for {selector}"

    # The accent-coloured labels on the plain blocks must clear AA on the surface
    # they sit on, in both themes, or the section headings disappear.
    tokens = _tokens(css, f'[data-theme="{theme}"]')
    ratio = _contrast(tokens["--accent"], tokens["--surface"])
    assert ratio >= 4.5, f"accent text on surface is {ratio:.2f}:1 in {theme}"

    ratio = _contrast(tokens["--faint"], tokens["--surface-sunk"])
    assert ratio >= 3.0, (
        f"the glossary term underline colour is {ratio:.2f}:1 on the sunk "
        f"surface in {theme}"
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_callout_surfaces_are_distinguishable(theme: str) -> None:
    """A caution callout has to look like a caution.

    If its background matches the surface it is meant to interrupt, it stops
    functioning as an interruption and becomes decoration — which is the one
    job on this page that matters.
    """
    tokens = _tokens(_text(CSS), f'[data-theme="{theme}"]')
    for soft, surface, label in (
        ("--caution-soft", "--surface", "caution"),
        ("--positive-soft", "--surface", "positive"),
        ("--accent-soft", "--surface", "accent"),
    ):
        ratio = _contrast(tokens[soft], tokens[surface])
        assert ratio > 1.03, (
            f"{label} callout is indistinguishable from the surface in {theme} "
            f"(ratio {ratio:.3f})"
        )


def test_chart_palette_matches_the_css() -> None:
    """The chart specs duplicate the CSS palette, so keep them from drifting.

    A Vega-Lite spec is JSON built in Python and cannot read a stylesheet, so
    the values are repeated in `dwm/ui/charts.py`. Duplication is only
    defensible while something checks it: a chart drawn in yesterday's blue next
    to today's blue text is a small wrongness nobody can name.
    """
    from dwm.ui.charts import DARK, LIGHT

    css = _text(CSS)
    pairs = [
        ("light", LIGHT, [
            ("--accent", "accent"), ("--positive", "positive"),
            ("--caution", "caution"), ("--negative", "negative"),
            ("--muted", "muted"), ("--surface", "surface"),
            ("--canvas", "canvas"), ("--grid", "grid"), ("--axis", "axis"),
        ]),
        ("dark", DARK, [
            ("--accent", "accent"), ("--positive", "positive"),
            ("--caution", "caution"), ("--negative", "negative"),
            ("--muted", "muted"), ("--surface", "surface"),
            ("--canvas", "canvas"), ("--grid", "grid"), ("--axis", "axis"),
        ]),
    ]
    failures = []
    for theme_name, palette, mapping in pairs:
        tokens = _tokens(css, f'[data-theme="{theme_name}"]')
        for css_token, palette_key in mapping:
            if palette_key not in palette:
                failures.append(f"{theme_name}: charts.py has no {palette_key!r}")
                continue
            expected = tokens.get(css_token, "").strip().upper()
            actual = palette[palette_key].strip().upper()
            if expected != actual:
                failures.append(
                    f"{theme_name} {css_token}: CSS is {expected}, charts.py is {actual}"
                )
    assert not failures, "\n".join(failures)


def test_no_encoding_damage_anywhere() -> None:
    """Encoding damage is invisible until someone reads the rendered page.

    A PowerShell round-trip double-encoded every non-ASCII character in the
    previous dashboard: a middle dot became two characters and four stray
    symbols appeared in the navigation labels. The app still ran and every other
    test still passed, because the damage is only visible in the output.

    Scoped to the *front end* the first time, which was too narrow: an em dash
    inside a caution string in `dwm/inference/facts.py` was corrupted the same
    way and only surfaced by reading the file. The detector is by codepoint
    rather than by literal character, so this test cannot itself contain the
    thing it looks for.
    """
    bad = re.compile("[\u00c3\u00c2\u00e2\u00c2]")
    replacement = "\ufffd"

    targets = list(STATIC.glob("*"))
    targets += [ROOT / "dwm" / "ui" / "charts.py"]
    targets += sorted((ROOT / "dwm").rglob("*.py"))
    targets += sorted((ROOT / "dashboard").rglob("*.py"))
    targets += sorted((ROOT / "docs").glob("*.md"))
    targets += [ROOT / "README.md", ROOT / "BLUEPRINT.md"]

    checked = 0
    for path in targets:
        if path.suffix not in {".html", ".css", ".js", ".py", ".md"}:
            continue
        text = path.read_text(encoding="utf-8")
        checked += 1
        assert not bad.search(text), f"{path.name} contains mojibake"
        assert replacement not in text, (
            f"{path.name} contains a replacement character, so some text was "
            "already corrupted when it was written"
        )
        assert not text.startswith("\ufeff"), f"{path.name} starts with a BOM"
    assert checked > 40, f"the encoding sweep only covered {checked} files"


# ---------------------------------------------------------------------------
# data paths
# ---------------------------------------------------------------------------


def _resolve(payload: dict, path: str):
    node = payload
    for part in path.split("."):
        if isinstance(node, list):
            node = node[0] if node else None
        if not isinstance(node, dict) or part not in node:
            raise AssertionError(
                f"path {path!r} is missing at {part!r}; available: "
                f"{sorted(node) if isinstance(node, dict) else type(node).__name__}"
            )
        node = node[part]
    return node


MANIFEST_PATHS = [
    "title", "subtitle",
    "provenance.generated_at", "provenance.mining_run_id",
    "provenance.config_version", "provenance.source",
    "guards.passed", "guards.total", "guards.succeeded", "guards.failed",
    "corpus.headlines_in_window", "corpus.start", "corpus.end",
    "honesty_rules", "datasets",
]


@pytest.mark.parametrize("path", MANIFEST_PATHS)
def test_manifest_paths_exist(path: str) -> None:
    from dwm.api import store

    _facts_from_warehouse()  # skips with a reason if the warehouse is not built
    _resolve(store.build_manifest(), path)


FINDING_PATHS = {
    "rq2_bursts": ["per_year_cv", "counter_signal_note", "event_months"],
    "rq1_topic_mix": [
        "taxonomy_artefacts.raw_category", "taxonomy_artefacts.share_of_year",
        "taxonomy_artefacts.count",
        "window_shares.topics.share_of_window", "window_shares.topics.topic_group",
    ],
    "rq3_sensationalism": [
        "corpus_rate", "ranked.risk_signal_rate", "ranked.ci95_low", "ranked.ci95_high",
        "ranked.headline_count", "ranked.above_corpus_average",
        "trend_by_year.risk_signal_rate", "trend_by_year.headline_count",
        "sensitivity.rows.threshold", "sensitivity.rows.top1",
        "sensitivity.ranking_is_stable",
    ],
    "rq4_clusters": [
        "silhouette", "chosen_k", "k_candidates.silhouette",
        "clusters.cluster_id", "clusters.share_of_sample", "clusters.size",
        "clusters.top_terms", "k_note", "concentration_note",
    ],
    "rq5_association_rules": [
        "rule_count", "fpgrowth_speedup", "tautological_rules", "mean_items",
        "redundancy_note", "lift_caveat", "top_rules.antecedent",
        "top_rules.consequent", "top_rules.support", "top_rules.confidence",
        "top_rules.lift", "keyword_run.mean_items", "keyword_run.rule_count",
    ],
    "rq6_market_association": [
        "trading_days_paired",
        "measures.mean_sentiment.by_lag.lag_trading_days",
        "measures.mean_sentiment.by_lag.pearson_r",
        "measures.mean_sentiment.by_lag.p_value",
        "measures.mean_sentiment.by_lag.n",
        "measures.mean_sentiment.by_lag.significant_at_alpha",
        "volatility.strongest.pearson_r",
        "significance_accounting.tests_run", "significance_accounting.alpha",
        "significance_accounting.expected_false_positives",
        "significance_accounting.significant_results",
    ],
    "rq7_classifier": [
        "chosen_model", "metrics.accuracy", "metrics.majority_baseline_accuracy",
        "metrics.lift_over_baseline_pp", "metrics.fake_recall",
        "metrics.confusion_matrix.matrix", "metrics.confusion_matrix.labels",
        "metrics.confusion_matrix.reading", "models",
        "top_terms.toward_fake", "top_terms.toward_real", "upper_bound_caveat",
    ],
}


@pytest.mark.parametrize("section,path", [
    (section, path) for section, paths in FINDING_PATHS.items() for path in paths
])
def test_finding_paths_exist(section: str, path: str) -> None:
    """Asserted against `facts.json` itself, which is what the API serves.

    Four of these were wrong on the first pass — `taxonomy_artefacts` uses
    `raw_category` not `category`, the lag rows carry `significant_at_alpha` not
    `significant`, the returns correlation is on the fact header rather than
    under `max_abs_r`, and the per-model rows have no `baseline` — and every one
    rendered a blank or an "n/a" instead of raising.
    """
    facts = json.loads(FACTS.read_text(encoding="utf-8"))
    _resolve(facts[section], path)


# ---------------------------------------------------------------------------
# honesty
# ---------------------------------------------------------------------------


def test_every_outcome_carries_a_caution() -> None:
    """The rule the whole project rests on, checked on the synthesis too.

    `outcomes` is a derived block rather than a measurement, so it has no single
    fact header and its cautions are per-outcome instead — which means nothing
    else enforces their presence. They were emitted under the key `caveat` while
    the project spells it `caution`, so all seven silently rendered as absent on
    the one page whose job is stating what may not be claimed.
    """
    facts = json.loads(FACTS.read_text(encoding="utf-8"))
    outcomes = facts["outcomes"]["outcomes"]
    assert len(outcomes) == 7, f"expected seven outcomes, found {len(outcomes)}"
    for outcome in outcomes:
        assert outcome.get("caution"), f"{outcome.get('id')} has no caution"
        assert outcome.get("headline"), f"{outcome.get('id')} has no headline"
        assert outcome.get("question"), f"{outcome.get('id')} has no question"
        assert outcome.get("evidence"), f"{outcome.get('id')} has no evidence"
        assert outcome.get("kind") in {"null", "qualified", "positive", "split"}, (
            f"{outcome.get('id')} has an unknown kind {outcome.get('kind')!r}"
        )
    # A null result must survive the synthesis. Dropping it would make "we looked
    # and found nothing" indistinguishable from "we did not look".
    assert facts["outcomes"]["counts"]["null"] >= 1


def test_outcomes_use_the_project_wide_caveat_spelling() -> None:
    """Guards against the key drifting back to `caveat`."""
    facts = json.loads(FACTS.read_text(encoding="utf-8"))
    for outcome in facts["outcomes"]["outcomes"]:
        assert "caveat" not in outcome, (
            f"{outcome.get('id')} uses 'caveat'; the fact structure and the "
            "front end both read 'caution'"
        )


def test_no_figure_in_the_payload_lacks_a_caution() -> None:
    from dashboard.helpers import find_uncautoned

    facts = json.loads(FACTS.read_text(encoding="utf-8"))
    problems = find_uncautoned(facts)
    assert not problems, f"figures with a unit but no caution: {problems[:8]}"


# ---------------------------------------------------------------------------
# charts
# ---------------------------------------------------------------------------


def _axis_encodings(node):
    """Yield every x/y axis encoding found anywhere in a Vega-Lite spec.

    Recurses because a spec is a tree: a layered chart, a facet, and a repeated
    chart each bury their encodings at a different depth.
    """
    if isinstance(node, list):
        for item in node:
            yield from _axis_encodings(item)
        return
    if not isinstance(node, dict):
        return
    for axis in ("x", "y"):
        block = node.get(axis)
        if isinstance(block, dict) and "field" in block:
            yield axis, block
    for value in node.values():
        yield from _axis_encodings(value)


def test_nominal_axes_are_sorted_by_their_own_labels() -> None:
    """A nominal axis with no explicit sort defaults to data-arrival order.

    The RQ2 event-month chart sorted its month axis by z-score, which put
    2020-05 beside 2016-12 and made an unordered comparison look chronological.

    Checked by walking the built spec's actual axis encodings rather than by
    counting `"type": "nominal"` occurrences. The count-based version was tried
    first and was useless: it counted tooltip and colour channels too, so every
    chart on the dashboard looked like an offender and the check could not
    distinguish a real problem from a busy legend. Only an axis is an axis.
    """
    from dwm.ui.v2_charts import build_v2_charts

    specs = dict(_all_v1_charts())
    specs.update(build_v2_charts())

    offenders = []
    for name, spec in specs.items():
        if not spec or spec.get("dwm_empty"):
            continue
        for axis, block in _axis_encodings(spec):
            if block.get("type") != "nominal":
                continue
            # Two legitimate ways to pin a nominal axis, and the test has to
            # accept both: an explicit `sort` list, or a `scale.domain` array.
            # The confusion matrix uses the latter, because its rows and columns
            # must share ONE order and a scale domain expresses that shared
            # order more directly than two independent sort lists would.
            has_sort = "sort" in block
            scale = block.get("scale")
            has_domain = isinstance(scale, dict) and isinstance(scale.get("domain"), list)
            if not (has_sort or has_domain):
                offenders.append(
                    f"{name}: {axis} axis on {block['field']!r} has neither a sort "
                    f"nor a scale domain, so it renders in data-arrival order"
                )
    assert not offenders, (
        "nominal axes that will render in data-arrival order:\n" + "\n".join(offenders)
    )


def test_the_event_month_chart_is_in_date_order() -> None:
    """The specific regression, pinned rather than left to a general rule."""
    from dwm.ui.charts import build_charts

    spec = build_charts(_facts_from_warehouse())["rq2_event_z"]
    blob = json.dumps(spec)
    sorts = re.findall(r'"sort":\s*\[([^\]]*)\]', blob)
    assert sorts, "the month axis carries no explicit sort at all"
    months = re.findall(r'"(\d{4}-\d{2})"', sorts[0])
    assert months, f"no month labels found in the sort list: {sorts[0][:80]}"
    assert months == sorted(months), f"months are out of date order: {months}"
    # And the data order must match, or the default takes over.
    rows = json.loads(blob)
    def find_values(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "values":
                    return value
                found = find_values(value)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = find_values(item)
                if found is not None:
                    return found
        return None
    values = find_values(rows) or []
    data_months = sorted({r["month"] for r in values if "month" in r})
    assert data_months == sorted(months), (
        f"the sort list and the data disagree: {data_months} vs {sorted(months)}"
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_chart_spec_declares_a_schema(theme: str) -> None:
    """A spec without `$schema` falls back to Vega-Lite 5 semantics."""
    from dwm.ui.charts import build_charts

    for name, spec in build_charts(_facts_from_warehouse(), theme_name=theme).items():
        if not spec:
            continue
        assert "$schema" in spec, f"{name} ({theme}) has no $schema"
        assert "v6" in spec["$schema"], f"{name} declares {spec['$schema']}"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_charts_are_serialisable_and_non_trivial(theme: str) -> None:
    """A spec that cannot be JSON-encoded cannot be embedded, and one with no
    mark draws nothing. Both are silent failures at page load."""
    from dwm.ui.charts import build_charts

    for name, spec in build_charts(_facts_from_warehouse(), theme_name=theme).items():
        if not spec:
            pytest.fail(f"{name} produced an empty spec in {theme}")
        blob = json.dumps(spec)
        assert len(blob) > 400, f"{name} is suspiciously small ({len(blob)} bytes)"
        assert '"mark"' in blob or '"layer"' in blob, f"{name} has no mark"


def test_the_volatility_scatter_reproduces_the_reported_correlation() -> None:
    """The plotted points must be the ones the reported r was computed on.

    An earlier version returned about 13,000 rows mixing every desk, which is a
    different quantity and would not reproduce the coefficient printed beside it.
    """
    from dwm.api import store
    from dwm.ui.charts import build_charts

    facts = _facts_from_warehouse()
    points = store.market_daily()["points"]
    spec = build_charts(facts, market_points=points)["rq6_volatility"]

    blob = json.dumps(spec)
    xs = [float(v) for v in re.findall(r'"x":\s*(-?\d+\.?\d*)', blob)]
    assert len(xs) >= 1000, f"expected the full daily series, found {len(xs)} points"

    ys = [float(v) for v in re.findall(r'"y":\s*(-?\d+\.?\d*)', blob)]
    n = min(len(xs), len(ys))
    a, b = xs[:n], ys[:n]
    mean_a, mean_b = sum(a) / n, sum(b) / n
    num = sum((p - mean_a) * (q - mean_b) for p, q in zip(a, b, strict=True))
    den = (sum((p - mean_a) ** 2 for p in a) * sum((q - mean_b) ** 2 for q in b)) ** 0.5
    recomputed = num / den

    reported = facts["rq6_market_association"]["volatility"]["strongest"]["pearson_r"]
    assert recomputed == pytest.approx(reported, abs=0.002)
