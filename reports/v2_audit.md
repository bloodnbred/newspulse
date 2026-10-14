# v2 Audit (Phase V0)

**Date:** 2026-10-14
**Scope:** audit only. No v1 code was changed, moved or refactored.
**Warehouse audited:** `warehouse/dwm.duckdb` (423.8 MB, built 2026-10-02)

Every figure below came from a query or a command run during this audit. The commands are
named so each number can be reproduced. Phase gate for V0: *`pytest` still green, report
lists what exists and what is missing.* Both hold.

---

## 1. Repository tree (v2-relevant parts)

```
D:\Projects\DWM-Project\
├── AGENTS.md                  merged/corrected in P0
├── BLUEPRINT.md               v1 spec
├── BLUEPRINT_V2.md            v2 spec
├── OPENCODE_START_HERE.md     pack bootstrap
├── prompts/v2-phase-prompts.md
├── requirements.txt  pyproject.toml  README.md
├── dwm/                       THE PACKAGE (root level; there is no src/)
│   ├── __init__.py __main__.py cli.py config.py db.py logging_utils.py parse.py
│   ├── api/       __init__.py app.py store.py
│   ├── etl/       __init__.py clean.py dates.py dims.py
│   ├── features/  __init__.py runner.py scoring.py lexicons.py
│   ├── inference/ __init__.py facts.py plain.py report.py
│   ├── ingest/    __init__.py runner.py fetch.py normalise.py staging.py audit.py
│   ├── mining/    __init__.py trends.py sensationalism.py clustering.py rules.py
│   │              market.py classifier.py sampling.py
│   ├── olap/      __init__.py operations.py readings.py charts.py
│   ├── ui/        __init__.py charts.py        <- Vega-Lite SPECS, not a UI
│   └── warehouse/ __init__.py facts.py cubes.py
├── dashboard/
│   ├── helpers.py                        palette mirror + formatting helpers
│   └── static/  index.html  app.css  app.js     <- the dashboard (vanilla-JS SPA)
├── tests/      conftest.py + 14 test modules (413 tests)
├── sql/        schema.sql (generated)  schema_v2.sql
├── config/     datasets.yaml features.yaml mining.yaml topics.yaml
│               datasets_v2.yaml events_v2.yaml stocks.yaml
│               stock_aliases.yaml sector_keywords.yaml
├── docs/       01..05 (v1)  06-stocks-linking 07-mining-insights-api
│               08-dashboard-usability (v2)  DEMO STATUS VIVA
├── reports/    facts.json  report.md  mining.json
├── data/raw/   toi/  ifnd/  nifty/        <- NO stocks/ and NO meta/
└── warehouse/  dwm.duckdb  dwm.serve.duckdb
```

**Missing entirely from v1:** `sql/` (DDL was inline in Python), `models/`, `src/`,
`config/lexicons/`, `dashboard/ui.py`, `dashboard/pages/`, `data/raw/stocks/`,
`data/raw/meta/`, and any `AGENTS.md` rulebook.

## 2. v1 modules and CLI

Public surface, by sub-package:

| Sub-package | Public functions / classes |
|---|---|
| `config` | `data_dir` `raw_dir` `warehouse_dir` `default_db_path` `reports_dir` `logs_dir` `ensure_dirs` `analysis_window` `window_start_from_iso` `days_in_window` `load_datasets_config` `get_dataset` `enabled_datasets` `load_topic_map` `threads` `memory_limit`; classes `Settings` `DatasetSpec` `TopicMap` |
| `db` | `connect` `table_exists` `count_rows` `all_counts` `scalar` |
| `parse` | `ParsedDate` `parse_date` `parse_date_column` `normalise_label` `category_prefix` |
| `ingest` | `IngestOutcome` `ingest` `ingest_one` `load_raw`; plus `FetchError` `FetchResult` `sha256_file` `fetch_dataset` `stage_table_name` `resolve_columns` `count_source_rows` `stage_dataset` `describe` `ensure_audit_table` `new_run_id` `record` `funnel` `normalise` `normalised_path` |
| `etl` | `run_etl` `build_clean` `build_dims` `dim_date_bounds` `staging_ready` `check_gates` `register_date_functions` |
| `features` | `run_features` `TextMeasures` `TextScorer` `eligible_terms` `all_lexicons` `build_vocabulary` `feature_fingerprint` |
| `warehouse` | `run_build` `build_facts` `build_cubes` `check_gates` `dataset_keys` `label_keys` `instrument_keys` |
| `olap` | `Result` `slice_by_topic` `slice_by_year` `dice_by_topic_and_year` `roll_up_to_year` `drill_down_to_month` `top_n_months` `pivot_month_by_topic` `cube_by_year_topic_band` `drill_across_market_and_headlines` `topic_mix_over_time` `list_operations` `run_operation` `run_olap` `reading_for` `known_operations` `olap_chart_spec` |
| `mining` | `run_mining` `load_mining_config` `Guard` `topic_mix_by_year` `composition_shift` `raw_category_dominance` `monthly_volume` `detect_bursts` `wilson_interval` `sensationalism_by_topic` `threshold_sensitivity` `sample_corpus` `cluster_headlines` `build_projection` `sample_stability` `mine_rules` `mine_keyword_rules` `paired_series` `lagged_correlation` `volatility_association` `run_classifier` `hash_expr` `sample_rows` |
| `inference` | `run_inference` `load_facts` `build_facts` `write_facts` `check_guards` `build_outcomes` `render_report` `write_report` `build_intro` `build_summary` `build_glossary` `build_scales` `build_plain` `build_cluster_glosses` |
| `api` | `app` `DataUnavailable` `create_app`; 16 routes (below) |
| `ui` | `build_charts` `theme` `available_themes` + 17 chart-spec builders |

### CLI (`python -m dwm ...`)

`version` `config` `ingest` `etl` `features` `build` `load`* `olap` `mine` `report` `infer`*
`audit` `tables` `serve`  (* added in P0 as aliases)

Shared options: `--db`, `--sample/-s`, `--years`, `--chunk`, `--verbose/-v`.
OLAP operations registered: `slice` `slice_year` `dice` `roll_up` `drill_down`
`top_months` `pivot` `cube` `drill_across` `topic_mix`.

### API routes
`GET /health` `/operations` `/facts` `/facts/{section}` `/summary` `/report` `/topics`
`/headlines` `/series/monthly` `/tables` `/market/daily` `/audit` `/query/{operation}`
`/ui/charts` `/ui/manifest`; `POST /query/reload`.

**Not present, needed by v2:** `link` `usability` `all` commands; `/stocks` `/sectors`
`/events` `/brief` `/insights` `/check` `/quality` endpoints.

## 3. Test baseline

`python -m pytest` → **413 passed, 27 skipped** in 105.93s. No failures.

The 27 skips are pre-existing and self-declaring: they need artefacts a bare test run does
not have (`reports/facts.json`, i.e. `python -m dwm report`) or a running API on
`127.0.0.1:8000`. They are not new breakage.

## 4. Warehouse inventory (27 tables, 17,239,980 rows)

| Table | Rows | Table | Rows |
|---|---:|---|---:|
| fact_headline | 3,154,916 | cln_headline | 3,154,916 |
| bridge_headline_keyword | 4,223,280 | feat_headline | 3,154,916 |
| stg_toi | 3,297,172 | fact_statement | 51,110 |
| cln_statement | 51,110 | feat_statement | 51,110 |
| stg_ifnd | 56,714 | fact_market_daily | 3,718 |
| cln_market_daily | 3,718 | feat_market_daily | 3,718 |
| stg_nifty | 3,718 | cube_day_topic | 19,481 |
| cube_month_topic | 835 | cube_year_topic | 92 |
| cube_topic_sentiment | 17 | dim_date | 8,059 |
| dim_topic | 20 | dim_keyword | 300 |
| dim_dataset | 3 | dim_instrument | 1 |
| dim_label | 3 | map_category_topic | 1,025 |
| map_label_value | 2 | etl_audit | 25 |
| feat_config | 1 | | |

**Corpus coverage** (queries against `fact_headline` / `fact_market_daily`):

- Headlines: 2001-01-01 → **2020-06-30**. The 5-year window is therefore **2015-07-01 → 2020-06-30**, containing **1,113,427** headlines.
- Nifty 50 index: 2008-01-21 → 2023-01-24. It runs **7 months past the end of the news**. Drill-across must clip to the news window or it will silently show "no news" months.
- `dim_date` spans 2001-01-01 → 2023-01-24 with **8,059** dates of which **3,718** are trading days. `dim_trading_calendar` can be built directly from this; the calendar gap problem is already solved by v1.

## 5. What `sql/schema_v2.sql` must add (verified, not estimated)

Produced by diffing the DDL text against DuckDB's own catalog.

**15 new tables, 118 columns:**

`dim_sector` (2) · `dim_stock` (11) · `dim_stock_alias` (6) · `dim_event` (6) ·
`dim_trading_calendar` (3) · `fact_stock_daily` (22) · `fact_news_stock_mention` (8) ·
`fact_news_sector_daily` (8) · `cube_sector_month` (11) · `mining_event_study` (11) ·
`mining_stock_clusters` (11) · `mining_market_rules` (7) · `mining_direction_results` (10) ·
`mining_surprise_days` (6) · `mining_mention_audit` (5)

**2 new columns on an existing table:**

| Column | Table | Current rows |
|---|---|---:|
| `is_market_report` | fact_headline | 3,154,916 |
| `effective_trading_date_key` | fact_headline | 3,154,916 |

Nothing is dropped, renamed or retyped. The DDL was executed against a stand-in warehouse
carrying the real `fact_headline` definition: it applies cleanly and is idempotent on re-run.

### Errors found in the pack's DDL and corrected in P0

1. **`headline_key` does not exist.** `fact_headline`'s key is **`headline_id`**. The pack's `fact_news_stock_mention` and `mining_mention_audit` both referenced `headline_key`; as written they would never have joined to anything. Corrected.
2. **Returns were fractions, not percentage points.** `fact_market_daily.return_pct` is `(close/lag-1)*100`, so `0.5042` means +0.50%. The pack declared `daily_return` as a fraction and `is_outlier = abs > 0.25`. Under v1's units that threshold is 0.25 **percentage points**, i.e. it would have flagged essentially nothing. Corrected to `return_pct`, `abs_return_pct`, `volatility_20d`, and `is_outlier = abs_return_pct > 25.0`.
3. **`dim_trading_calendar` duplicated `dim_date.is_trading_day`.** v1 already has it, populated from the Nifty series. Documented as derived, not recomputed.

## 6. Missing, mapped to phases

| Phase | What is missing | Size |
|---|---|---|
| **V1** | `dwm/ingest/stocks.py`, `dwm/ingest/meta.py`; `dwm/etl/calendar.py`; dims `dim_sector` `dim_stock` `dim_stock_alias` `dim_event` `dim_trading_calendar`; `data/raw/stocks/` and `data/raw/meta/` do not exist | Large |
| **V2** | `dwm/warehouse/stock_facts.py`, `v_stock_daily_clean`, `fact_stock_daily` | Medium |
| **V3** | whole `dwm/linking/` package, `dwm/features/market_report.py`, `fact_news_stock_mention`, `fact_news_sector_daily` | Large |
| **V4** | `cube_sector_month`, `dwm/olap/sector_ops.py`, `sql/olap_cookbook.sql` | Medium |
| **V5** | `event_study.py` `stock_clusters.py` `market_rules.py` `direction_model.py` `surprise_days.py`, `models/` dir | Largest |
| **V6** | `inference/insights.py`, `inference/brief.py`, 9 new API endpoints | Large |
| **V7** | 8 new SPA views in `app.js` + new Vega specs in `dwm/ui/charts.py` | Largest |
| **V8** | `usability` command, `reports/usability_results.csv`, summary script | Small |

**No v1 module needs replacing for any of this.** The v1 dims, facts, cubes, OLAP registry,
readings layer and mining scaffolding are reused as-is.

### V1-specific data risks (measured, not assumed)

Stock prices and the constituent list are **fetched**, not supplied:

- **Prices:** Yahoo chart API, `.NS` tickers, explicit `period1`/`period2` + `interval=1d`.
  Probed: **1,355 daily bars** for 2015-01-01 → 2020-07-03, worst gap 5 days. **All 50 NSE symbols resolve** on this source.
  - **Trap:** `range=max` and `range=10y` are silently downgraded to **monthly** bars. Must use explicit epochs.
  - `turnover` is not published; it would be **derived** as `close × volume` and must be labelled derived.
  - `adjclose ≠ close` (ITC: 199.85 vs 147.14). Returns must use **`adjclose`**.
- **Constituent list:** `nsearchives.nseindia.com/.../ind_nifty50list.csv`, 50 rows, columns
  Company Name / Industry / Symbol / Series / ISIN Code.
  - **It is the current (2026) list applied to a 2015-2020 window. That is survivorship bias**
    and must be stated on the Data and Schema page and in the report.
  - 7 constituents start trading after 2015-07-01 and so have partial coverage:
    BSE, HDFCLIFE, SBILIFE, INDIGO, MAXHEALTH, JIOFIN, ETERNAL.
  - **ETERNAL has no prices at all** (`ETERNAL.NS` → HTTP 400, former ticker `ZOMATO.NS` → 404).
    It goes to `etl_audit` as unmatched. It is not a reason to substitute a different source.
- **Sector mapping will not work as shipped.** `config/datasets_v2.yaml`'s `industry_to_sector`
  maps 5 of the 15 labels NSE actually returns, and its keys are UPPERCASE while the feed is
  Title Case:

  | NSE label | n | In pack map? |
  |---|---:|---|
  | Financial Services | 12 | yes |
  | Healthcare | 5 | **no** (pack has `PHARMA`) |
  | Automobile and Auto Components | 5 | **no** (pack has `AUTOMOBILE`) |
  | Metals & Mining | 4 | **no** (pack has `METALS`) |
  | Information Technology | 4 | yes |
  | Fast Moving Consumer Goods | 4 | **no** (pack has `FMCG`) |
  | Oil Gas & Consumable Fuels | 3 | **no** (pack has `OIL & GAS`) |
  | Services | 2 | **no** |
  | Consumer Durables | 2 | **no** |
  | Consumer Services | 2 | **no** |
  | Construction Materials | 2 | **no** (pack has `CEMENT & CEMENT PRODUCTS`) |
  | Power | 2 | yes |
  | Capital Goods | 1 | **no** |
  | Telecommunication | 1 | **no** (pack has `TELECOM`) |
  | Construction | 1 | yes |

  10 of 15 need new entries, and `Services`, `Capital Goods` and `Consumer Durables` have no
  obvious home among the ten target sectors. That is a judgement call for V1, made explicitly
  and recorded, not silently defaulted.

## 7. Dependencies

No new dependency is required for any phase.

| Pack asked for | Status |
|---|---|
| `statsmodels` | Not installed, **not needed.** `numpy.polyfit` / `scipy.stats.linregress` fit the event-study OLS. Verified working. |
| `streamlit`, `plotly` | Not used. See below. |
| `yfinance` | Not needed; stdlib `urllib`. |
| `joblib` | Present transitively via scikit-learn; fine for `models/`. Not added to `requirements.txt`. |

Everything else v2 needs — DuckDB, pandas, numpy, pyarrow, scipy, scikit-learn, mlxtend,
vaderSentiment, Typer, FastAPI, uvicorn, httpx, PyYAML, altair, pytest, ruff — is already declared.

## 8. Decisions taken in P0

Recorded here because they change what the pack told later phases to build.

1. **The dashboard stays the existing vanilla-JS SPA.** The pack mandated Streamlit. The SPA has a WCAG-AA-tested two-palette design system in `app.css`, a Vega-Lite layer in `dwm/ui/charts.py`, and 27 tests in `tests/test_ui.py` that a Streamlit rewrite would break. `dashboard/ui.py` and `.streamlit/config.toml` were **not** installed; `docs/08` was rewritten so V7 adds SPA views, not `.py` page files.
2. **`AGENTS.md` is a merge, not a paste.** All 21 hard rules kept; the false facts corrected (`src/dwm` → `dwm/`, `topics.yaml`, no `config/lexicons/`, real CLI names, no Streamlit).
3. **`sql/schema.sql` is generated, not written.** Produced from the live warehouse with `EXPORT DATABASE`, with a header stating it is a reference dump. The pack's V0 prompt assumed it already existed.
4. **The pack's teal/amber palette is dropped** in favour of the existing tokens. News = `--accent`, market = `--muted` (recessive), `--positive`/`--negative` = direction only.
5. **Docs 06, 07 and 08 carry "Reconciled with this repo" headers** recording each correction, so no future session builds from the original text.
6. **New CLI verbs are aliases.** `load` → `build`, `infer` → `report`. No v1 command renamed.

## 9. Open items and risks

| # | Item | Status |
|---|---|---|
| 1 | 7 pre-existing `ruff` errors in `dwm/inference/plain.py`, `dwm/olap/readings.py`, `tests/test_olap_readings.py` (unused locals, one nested `if`). | Reported, not fixed — AGENTS.md rule 9 forbids cross-phase fixes. They make "ruff clean" in the definition of done unreachable until someone owns them. **Needs a decision.** |
| 2 | Sector mapping for 10 unmapped NSE industry labels. | V1 decision, must be explicit |
| 3 | ETERNAL has no price data. | Permanent gap; report it |
| 4 | Survivorship bias from using the 2026 constituent list. | Must be stated on-page, not just in docs |
| 5 | `turnover` would be derived, not exchange-reported. | Must be labelled wherever shown |
| 6 | Nifty index extends 7 months beyond the news corpus. | Clip drill-across to the news window |
| 7 | Stock-level mentions in TOI may be too sparse to link. | Blueprint §9 fallback: lean on sector-level news; report coverage |

## 10. Gate

- [x] `pytest` still green — **413 passed, 27 skipped**
- [x] Report lists what exists and what is missing
- [x] No v1 code changed, moved or refactored
- [x] Every v2 schema addition identified and verified against the live catalog