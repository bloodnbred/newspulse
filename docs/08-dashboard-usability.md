# Phases V7 and V8: dashboard v2, usability, evaluation

Reuse: the v1 Streamlit code. Move useful v1 charts into pages; do not delete v1 pages until the matching v2 page works. Keep the FastAPI calls you already have; add new ones through `dashboard/ui.py`.

## Design system
- Theme from `.streamlit/config.toml`. Colours from `ui.py`: teal = news, amber = price, green/red = up/down returns only.
- Every page begins with `page_setup(title, icon, concept)` and `question_box(question, decision)`.
- Every chart has a title, axis labels, a data-generated one-line `insight(...)`, and a `how_computed(...)` panel with the SQL or algorithm.
- Every page ends with `footer()`.
- Use `st.cache_data` through `api_get`. Paginate lists (50 max). Clear empty states via `empty_state(command)`.
- Plotly for charts; add event markers as vertical lines labelled with the event name where time is on the x axis.

## Pages

### Home.py (landing and Quick Brief)
- Hero line: "See how Indian news and the Nifty 50 move together."
- **Search box:** "Type a company or sector" calls `/brief?q=` and renders the plain-English brief with "did you mean" suggestions. This is the page a first-time user needs; it must work without any DWM knowledge.
- KPI strip (from `/datasets`, `/quality`): headlines analysed, stocks, trading days, window.
- Three buttons: "Explore a sector", "Study an event", "Check a headline".
- Insights feed preview (top 3 cards from `/insights`).

### 1_Market_Mood.py (concept: drill-across)
Question: does news mood move with the market? Controls: topic (all or Business), include market-report headlines toggle, lag slider -5..+5. Charts: news mood index (teal) with Nifty (amber) on twin axes with event markers; lag-correlation bar chart with n and tests-run in the caption.

### 2_Stock_Explorer.py (concept: OLAP slice and drill-down)
Controls: stock picker (search), date range. Show: price and volume, volatility (20d), news mention timeline (bars) under the price, headlines list for a clicked month (50 rows), peers in the same sector and cluster, abnormal reaction to events.

### 3_Sector_Cube.py (concept: OLAP cube, roll-up, drill-down, pivot)
Interactive controls for each operation: Slice (year or sector), Dice (year range + sectors), Roll-up (month, quarter, year), Drill-down (year to month to headlines). Heatmap: sector x year of the chosen metric (news heat, mean return, volatility, risk-signal rate). Always show the SQL of the current operation.

### 4_Event_Study.py (concept: event dimension, regression, fact joins)
Pick an event. Show CAR[0,+5] by sector (bars with n stocks), top and bottom stocks, news volume around the event. Caption states average abnormal return and sample size. Overlap warning when events are close.

### 5_Mining_Lab.py (concepts: clustering, Apriori, classification)
Tabs: **Stock clusters** (PCA scatter coloured by cluster, cluster profile table); **Market rules** (table with support, confidence, lift sliders, plain-English first three); **Direction model** (metrics table with baselines, decision-tree rules, confusion matrix, honesty banner when near baseline).

### 6_Headline_Checker.py (reuse v1 checker)
Paste a headline: hype score, sentiment, topic, percentile vs 5 years, similar past headlines (with dates), tagged stocks and sectors. Caveat banner: style signal, not fact-check.

### 7_Data_and_Schema.py (concepts: ETL, fact constellation)
ETL funnel per dataset, schema diagram (Mermaid or image), data dictionary, mention coverage and precision, list of limitations from `/datasets`.

### 8_Insights.py
Card feed from `/insights` with concept tags and "open page" links. Filters by concept.

## Phase V7 definition of done
All pages render on the dev sample, every page has the five mandatory elements (check with a test that imports each page's source and asserts the helper calls exist), and the demo script in `BLUEPRINT_V2.md` can be run end to end.

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
