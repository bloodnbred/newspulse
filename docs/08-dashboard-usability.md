# Phases V7 and V8: dashboard v2, usability, evaluation

> **Reconciled with this repo (Phase P0).** This document originally described a Streamlit
> multipage app. **There is no v1 Streamlit code and there will be no Streamlit app.**
> The v1 front end is the vanilla-JS single-page dashboard in `dashboard/static/`, with
> Vega-Lite specs built in `dwm/ui/charts.py` and served by `dwm/api/app.py`.
>
> What this means in practice:
> - A "page" below is a **view in the SPA**: a `pageX()` function in `dashboard/static/app.js`,
>   registered in the `NAV` array, routed by the existing `{page, sub, tab}` model.
>   It is **not** a separate `.py` file. There is no `dashboard/Home.py` and no `dashboard/pages/`.
> - `st.cache_data` becomes the existing payload-caching in `app.js` / the API snapshot
>   (`dwm/api/store.py`, `ensure_snapshot`). Loading states already exist.
> - Plotly becomes **Vega-Lite** via `dwm/ui/charts.py` and `dwm/olap/charts.py`, which is what the SPA already renders.
> - `dashboard/ui.py` does not exist and is not to be created. Its helpers were Streamlit-only.
>   The equivalents here are the `.card` / `.kpi` / `.answer` / `.callout--*` / `.chart` CSS
>   components in `app.css` and the render helpers in `app.js`.
> - The mandatory-elements test (`tests/test_ui.py`) already exists and already asserts that
>   every chart the JS names is served by the API. Extend it; do not replace it.
>
> Do not delete or rewrite an existing v1 view until its v2 replacement renders.

Reuse: the existing views and charts. Add new ones; keep v1 behaviour working.

## Design system
- **Do not introduce a new palette.** Tokens live in `dashboard/static/app.css` and the chart-relevant
  subset is mirrored in `dashboard/helpers.py` (`CHART_PALETTE`); `tests/test_ui.py` asserts they agree.
- The pack's teal/amber palette is **dropped**. In this app the subject is news, so:
  - news series = `--accent` (the only accent);
  - price / market = `--muted`, or `--faint` for a reference line, so news stays the subject;
  - `--positive` / `--negative` mean direction (up/down returns) and nothing else;
  - `--caution` marks what may not be claimed, next to the number it qualifies.
- Build surfaces from the existing components: `.card`, `.kpi` (+ `--accent|positive|caution|muted`),
  `.answer`, `.callout--caution|evidence|note`, `.chart` with `.chart__head` / `.chart__body` / `.chart__cap`,
  `.badge--guard`, `.tablewrap` + `.tablescroll` + `table.data`, `.tabs` / `.tab.is-active`.
  No inline colours, no new spacing values, no box-shadow as structure.
- Every view opens with `.answer` (the question it answers and the decision it supports).
- Every chart has a title, labelled axes, and a data-generated one-line caption in `.chart__cap`.
- Every view carries a "how this was computed" panel naming the DWM concept and showing the SQL or algorithm.
- Every view ends with the disclaimer footer.
- Paginate lists at 50 rows. Empty states name the command to run.

## Views

Each is a `pageX()` in `app.js`, a `NAV` entry, and a payload served by `dwm/api/app.py`. New
API endpoints are defined in doc 07's table.

### Overview (extends the existing `pageOverview`) — landing and Quick Brief
- Hero: "See how Indian news and the Nifty 50 move together."
- **Search box:** "Type a company or sector" calls `/brief?q=` and renders the plain-English brief with "did you mean" suggestions. This is what a first-time user needs; it must work with no DWM knowledge.
- KPI strip (from `/tables`, `/quality`): headlines analysed, stocks, trading days, window.
- Three links: "Explore a sector", "Study an event", "Check a headline".
- Insights feed preview (top 3 cards from `/insights`).

### 1 Market Mood (concept: drill-across)
Question: does news mood move with the market? Controls: topic (all or Business), include market-report headlines toggle, lag slider -5..+5. Charts: news mood index (`--accent`) with Nifty (`--muted`) on a shared time axis, with event markers; lag-correlation bar chart carrying n and tests-run in the caption.

### 2 Stock Explorer (concept: OLAP slice and drill-down)
Controls: stock picker (search), date range. Show: price and volume, volatility (20d), a news-mention timeline (bars) under the price, a 50-row headline list for a clicked month, peers in the same sector and cluster, abnormal reaction to events.

### 3 Sector Cube (concept: OLAP cube, roll-up, drill-down, pivot)
Interactive controls per operation: Slice (year or sector), Dice (year range + sectors), Roll-up (month, quarter, year), Drill-down (year → month → headlines). Heatmap: sector × year of the chosen metric (news heat, mean return, volatility, risk-signal rate). Always show the SQL of the current operation.

### 4 Event Study (concept: event dimension, regression, fact joins)
Pick an event. Show CAR[0,+5] by sector (bars with n stocks), top and bottom stocks, news volume around the event. Caption states the average abnormal return and the sample size. Overlap warning when events are close.

### 5 Mining Lab (concepts: clustering, Apriori, classification)
Tabs: **Stock clusters** (PCA scatter coloured by cluster, cluster profile table); **Market rules** (table with support, confidence, lift controls, first three rules in plain English); **Direction model** (metrics table with both baselines, decision-tree rules, confusion matrix, honesty banner when near baseline).

### 6 Headline Checker
Paste a headline: hype score, sentiment, topic, percentile vs 5 years, 5 similar past headlines with dates, tagged stocks and sectors. Caution banner: a style signal, not a fact-check.

### 7 Data and Schema (concepts: ETL, fact constellation)
ETL funnel per dataset, schema diagram, data dictionary, mention coverage and precision, and the limitations list from `/quality` — including the survivorship-bias statement.

### 8 Insights
Card feed from `/insights` with concept tags and links. Filter by concept.

## Phase V7 definition of done
All views render on the dev sample, every view has the five mandatory elements, and the demo script in `BLUEPRINT_V2.md` runs end to end. Extend `tests/test_ui.py` so it asserts, per new view, that the answer block, at least one chart with a caption, the how-computed panel and the footer are all present — mirroring the existing checks.

## Hosting (optional, check current limits before choosing)
- Ship the aggregates, mining tables, models and a trimmed DuckDB file (cubes and mining outputs only); keep raw headlines and raw datasets out of the hosted copy.
- Run API and dashboard together (a single container, or two services with `DWM_API_URL` set).
- Free tiers may sleep after inactivity. Warm the service before demos.
- If hosting is not possible, record a 3 to 5 minute screen video of the demo script and include run instructions.

## Phase V8: Usability evaluation
Goal: show that real people can use it and benefit. Sample: at least 15 people (classmates, family, anyone interested in markets or news). Give no training beyond one sentence.

### Tasks (record success yes/no, time in seconds, and any confusion)
1. "Find out what the news has been saying about a company you know." (Home search)
2. "Find which sector's news was hottest in a year you choose." (Sector Cube heatmap)
3. "Pick a big event and tell me which sector reacted most." (Event Study)
4. "Paste any headline and tell me if it is hyped." (Checker)
5. "Find one stock that behaves like another." (Mining Lab clusters or Stock Explorer peers)

### SUS questionnaire (1 = strongly disagree, 5 = strongly agree)
1. I think I would like to use this system frequently.
2. I found the system unnecessarily complex.
3. I thought the system was easy to use.
4. I think I would need the support of a technical person to use it.
5. I found the various functions well integrated.
6. I thought there was too much inconsistency.
7. I imagine most people would learn to use it very quickly.
8. I found the system very cumbersome to use.
9. I felt very confident using the system.
10. I needed to learn a lot of things before I could get going.
Score: odd items (score - 1), even items (5 - score); sum all ten, multiply by 2.5 (0 to 100). Report the mean, median and range. A score around 68 is commonly treated as average.

### Extra questions
- "What would you use this for?" (open)
- "Which page was most useful, and which was confusing?" (open)
- "Would you use it again? yes / maybe / no"

### Output
Create `reports/usability_results.csv` (one row per participant) and a script `python -m dwm usability` that computes success rate per task, median time, SUS mean, and writes `reports/usability_summary.md` with a table and the three most common complaints. Use the summary as the Evaluation chapter. Fix the top two issues found, then rerun the tasks with 3 users and note the improvement.
