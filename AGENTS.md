# AGENTS.md (v2): rules for coding agents (OpenCode)

Read this file fully before every task. There is one front end (`web/`) and it is the only one.

## Project
NewsPulse Nifty: a Data Warehousing and Mining decision-support system. It combines Indian news headlines (Times of India), labelled statements (IFND), Nifty 50 index prices and per-stock Nifty 50 prices in a DuckDB fact-constellation warehouse, runs OLAP and mining operations, serves them through FastAPI, and presents them in a dashboard. This is an upgrade of the existing `dwm-india-news` v1 code. **Extend it. Do not rewrite working v1 code.**

## Stack
Python 3.11+, DuckDB, pandas, numpy, pyarrow, scikit-learn, mlxtend, scipy, vaderSentiment, PyYAML, Typer, FastAPI, uvicorn, httpx, pytest, ruff. Environment: Windows. Use `pathlib`, no Makefile, run everything as `python -m dwm <command>`.

**Dependency discipline.** Everything in that list is declared in `requirements.txt`. Three things the pack asked for are **not** dependencies and must not become ones:
- **`streamlit` and `plotly`** — not used. See "Corrections to the v2 pack". (`streamlit` happens to be present in `.venv`; ignore it. It is not declared and the app does not use it.)
- **`statsmodels`** — not needed. Fit the event-study OLS with `numpy.polyfit` or `scipy.stats.linregress`.
- **`yfinance`** — not needed. Fetch with stdlib `urllib` (see below).

`joblib` and `requests` are present in `.venv` but only transitively. `joblib` arrives with scikit-learn and is fine for `models/`; do not add it to `requirements.txt`. To add any genuine new dependency, edit `requirements.txt` in its own commit with a stated reason.

## Layout (this repo, as it actually is)
```
dwm/                              # NOTE: package is at the ROOT. There is no src/.
  __main__.py cli.py config.py db.py logging_utils.py parse.py
  ingest/   runner.py fetch.py normalise.py staging.py audit.py
            stocks.py meta.py                                    # v2
  etl/      __init__.py clean.py dates.py dims.py
            calendar.py                                          # v2 (trading calendar)
  features/ runner.py scoring.py lexicons.py
            market_report.py                                     # v2
  linking/  aliases.py mentions.py sector_news.py audit_sample.py # v2
  warehouse/ __init__.py facts.py cubes.py
            stock_facts.py                                       # v2
  olap/     operations.py readings.py charts.py
            sector_ops.py                                        # v2
  mining/   __init__.py trends.py sensationalism.py clustering.py rules.py
            market.py classifier.py sampling.py
            event_study.py stock_clusters.py market_rules.py
            direction_model.py surprise_days.py                    # v2
  inference/ __init__.py facts.py plain.py report.py
            insights.py brief.py                                  # v2
  api/      __init__.py app.py store.py                           # v2: new routers in app.py or api/routers/
  ui/       charts.py        # shared Vega theme/axis helpers, used by olap/charts.py

web/                        # the dashboard. React 19 + Vite + Tailwind + ECharts
  src/pages/   components/  lib/  hooks/  styles/
  public/mock/ui/*.json      # recorded payloads for `?mock=1`

sql/      schema.sql schema_v2.sql olap_cookbook.sql
config/   datasets.yaml features.yaml mining.yaml topics.yaml        # v1
          datasets_v2.yaml events_v2.yaml stocks.yaml
          stock_aliases.yaml sector_keywords.yaml                   # v2
models/   *.joblib (gitignored)
tests/    test_<phase>_*.py
reports/ data/raw/ warehouse/ web/src/
```

**One front end.** `web/` is the product. There is no second dashboard and no second set of tests for one; a second path to the same number is two things that can disagree.

## Commands
```
python -m dwm version | config | tables | audit
python -m dwm ingest --dataset toi|ifnd|nifty|stocks|meta|all [--sample N] [--skip-fetch]
python -m dwm etl [--years 5]
python -m dwm features [--chunk 100000]
python -m dwm build              # dims, facts, bridge, cubes   (v2 alias: load)
python -m dwm link               # v2: news<->stock/sector linking + mention audit sample
python -m dwm olap <operation> [--param k=v]
python -m dwm mine <operation|all>
python -m dwm report             # mining.json -> facts.json + report.md  (v2 alias: infer)
python -m dwm usability          # v2: SUS + task summary from reports/usability_results.csv
python -m dwm all --sample 50000 # v2: the whole pipeline in order
python -m dwm serve              # the API on :8000 (the dashboard is web/)
pytest
```
`load` and `infer` are aliases of `build` and `report`. All v1 command names still work. Do not rename or remove a v1 command.

## Hard rules
1. **Never fabricate data.** No invented headlines, labels, prices, tickers or mentions. Only `tests/fixtures/` may contain fake rows, clearly marked; they never reach reports or the dashboard.
2. **Every number shown to users comes from a query or a computed model result.** No hard-coded statistics.
3. **Wording:** "risk-signal rate", never "fake news rate" for unlabelled headlines. "coincides with" for events, "associated with" for correlations, never "caused" or "predicts" unless a held-out test shows predictive skill over the baseline.
4. **Window computed from data** (`max(publish_date)` of toi). Never hard-code years. The current corpus ends 2020-06-30, so the window is 2015-06-30..2020-06-30 (1,113,427 headlines in window, 2,041,489 out).
5. **No raw data, DuckDB file, models, secrets, `.env` in git.**
6. **Chunked reads** for big files. Aggregate in DuckDB SQL, not pandas loops.
7. **Idempotent** commands. **Deterministic**: `SEED = 42`.
8. **Windows safe:** multiprocessing only under main guards; `pathlib`; `encoding="utf-8"`.
9. **Scope discipline:** implement only the current phase. Report cross-phase issues instead of fixing them.
10. Every command supports `--sample N`.

### v2 rules
11. **No look-ahead / leakage.** Features for day t use only data known at the end of day t; target is day t+1. Train/validation/test splits are chronological, never shuffled. Tune on validation, report on test. Always report the majority-class baseline and the "yesterday's direction" baseline.
12. **Trading calendar.** Join news to prices via `dim_trading_calendar.effective_trading_date_key`, never the raw calendar date. `dim_date.is_trading_day` already exists and is the source; do not recompute it.
13. **Reverse-causality guard.** Predictive analysis excludes headlines with `is_market_report = TRUE`. Descriptive views offer a toggle.
14. **Outliers.** Daily returns with `abs > 25%` are flagged `is_outlier = TRUE`, kept in the table, excluded from statistics and models.
15. **Ambiguous stock aliases need context** (`config/stock_aliases.yaml`). Produce `reports/mention_audit_sample.csv` (200 stratified matches, empty `is_correct` column) and a script that computes precision once a human fills it. Never fill that column yourself.
16. **Cubes store sums and counts, never averages.** Derive ratios at query time.
17. **The front end talks to the API only.** All analytics live in `dwm/`, not in `web/`. No SQL, no pandas analytics, no model code in `web/src`. New dashboard data comes from a new endpoint in `dwm/api/ui.py`.
18. **Every page must state its question and its limit.** What the page answers, what decision it supports, and - beside the number - what may not be claimed about it.
19. **Design rules.** `web/src/styles/tokens.css` is the single source of truth for the look, and `web/src/lib/echartsTheme.ts` mirrors it for charts. `tests/test_u1_ui_router.py` asserts the two agree.
    - The palette is semantic: `--news` (teal) for news series, `--price` (amber) for market, `--mining` (violet) for mined results, `--up`/`--down` for direction, `--caution` for what may not be claimed, `--muted`/`--faint` for context.
    - **Do not introduce a new hue.** These are the colours; a seventh is a decision, not a convenience.
    - **Semantic hues are reserved.** A categorical palette must never contain `--news`, `--price`, `--mining`, `--up` or `--down` - painting sector one in "news" beside a series that means news destroys the meaning of both.
    - **Direction is direction only.** `--up`/`--down` mean a return went up or down, never "good news" / "bad news".
    - `--caution` sits next to the number it qualifies, never in a footnote.
    - Every surface builds from `.glass`, `.btn`, `.pill`, `.field`, `.kpi` and the ECharts theme. No inline colours, no new spacing values.
    - **A class name used in `web/src` must exist.** An undefined class renders as a native browser control and no type checker can see it; `scripts/uicheck.mjs` exists because of that.
    - Loading states via cached calls. Empty states must name the command to run.
20. **Disclaimer text** (educational, not investment advice, associations not causes, style not truth) appears on every page via the footer.
21. **Performance:** the dashboard queries cubes and mining tables, not raw facts, for anything over 100k rows. Paginate headline lists (max 50 rows).
22. **Data provenance.** Every fetched dataset records source URL, fetch time, row count and sha256 in a `.source.json` sidecar next to the cached file. Cache under `data/raw/` (gitignored) so the pipeline can rerun offline.

## Corrections to the v2 pack (do not undo these)
The pack was written against a different repository. These points are settled. Do not re-litigate them.

- **The dashboard is React, not Streamlit and not the v1 vanilla SPA.** Both of those are gone. A Streamlit rewrite never happened; the v1 SPA was removed once `web/` could replace it, along with its Vega spec layer and the 27 tests that only guarded it.
- **Package is `dwm/` at the repo root**, not `src/dwm/`.
- **Config files are `topics.yaml` and there is no `config/lexicons/`** (word lists live in `dwm/features/lexicons.py`). The pack's layout section was wrong on both.
- **Stock and meta data are fetched, not hand-supplied.** See below.

## Where the v2 data comes from
- **Prices:** Yahoo Finance chart API, one request per symbol, `.NS` tickers, with explicit `period1`/`period2` epoch seconds and `interval=1d`. Stdlib `urllib` only.
  - **Never use `range=max` or `range=10y` alone** — Yahoo silently downgrades those to *monthly* bars, which look like data and are not. Always pass explicit epochs.
  - Store both `close` and `adjclose`. Returns are computed from **`adjclose`** (dividend-adjusted). Record which was used.
  - `turnover` is not published by this source. Any turnover figure is **derived** (`close * volume`) and must be labelled as derived wherever it is shown.
- **Constituent master:** `https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv` (Company Name, Industry, Symbol, Series, ISIN Code).
- **Known gaps, to be reported rather than hidden.** `ETERNAL`, `JIOFIN` and `MAXHEALTH` have no daily bars inside the analysis window: they list after 2020-06-30 and return data fine for a later window, so their HTTP 400 means "nothing in that range", **not** "ticker missing". They still get a `dim_stock` row with `price_source = NULL` and `is_survivor = FALSE`, and are listed in `etl_audit`. `dim_stock` also records `is_survivor = FALSE` for the four that do have prices but list mid-window (BSE, HDFCLIFE, INDIGO, SBILIFE).
- **NSE trades on some weekend days.** 2016-10-30, 2019-10-27 and 2020-02-01 are in the data as trading sessions (two Diwali Muhurat sessions and the Union Budget Saturday). Do not treat a weekend date as a closure; `dwm.etl.calendar.trading_weekend_sessions` reports them.
- **Yahoo's `range=max` / `range=10y` silently return monthly bars.** Only `chart_url`'s explicit `period1`/`period2` epochs are safe.

## Honesty requirements specific to v2
- **Survivorship bias.** The only constituent list available is the *current* Nifty 50 list applied to a *historical* window. Companies that were constituents in 2015-2020 and have since been removed are absent. This must be stated on the Data and Schema page, in the final report, and in any chart that aggregates across stocks.
- **Stock-level mentions are sparse and name matching is noisy.** Always publish mention coverage alongside any mention-based figure.
- **If a prediction is at or below its baseline, say so on the page.** Do not tune until it looks good.

## Definition of done (every phase)
- Code in the listed modules, type hints, short docstrings.
- Phase tests `pytest tests/test_<phase>_*.py` pass; all earlier tests still pass.
- The phase CLI command runs on `--sample 50000` without error.
- `ruff check` is clean.
- Summary: files changed, commands run, test results, anything not done.

## Task workflow
1. Read AGENTS.md, then the phase doc.
2. Write or update tests first when practical.
3. Implement.
4. Run phase tests, then the full suite.
5. Run the phase command on the dev sample.
6. Report the summary and stop. Do not start the next phase.