# Phases V1 to V4: stock data, news linking, cubes, OLAP v2

> **Reconciled with this repo (Phase P0).** The original pack text assumed `src/dwm/`,
> a `build_dims.py` / `load_facts.py` pair and fraction-valued returns. Corrected facts:
> - The package is `dwm/` at the root. The real modules are `dwm/etl/dims.py` and `dwm/warehouse/facts.py`; there is no `build_dims.py` / `load_facts.py`.
> - v1 has no `config/events.yaml`. `config/events_v2.yaml` is the only event file.
> - v1 has no `config/lexicons/` directory; word lists live inline in `dwm/features/lexicons.py`. Add the market-report lexicon there.
> - The loader interface is `dwm/ingest/runner.py` (`ingest_one`, `IngestOutcome`, `etl_audit`), not a bare `load(path, conn, sample)`.
> - **Returns are percentage points, matching `fact_market_daily.return_pct`.** See below.
> - `fact_headline`'s key column is `headline_id`, not `headline_key`.

Reuse first: extend v1 loaders (`ingest/`), the audit table, `etl/dims.py`, `warehouse/facts.py`, `cubes.py` and the OLAP module. Add files, do not rewrite them.

## V1: Stock ingest and dimensions
- `ingest/stocks.py`, `ingest/meta.py`: same loader interface as v1 (`load(path, conn, sample)`), configured by `config/datasets_v2.yaml`. Stage into `stg_stocks(symbol, date, open, high, low, close, volume, turnover)` and `stg_meta`. Symbol comes from the file name if no symbol column. Log to `etl_audit`.
- Run `sql/schema_v2.sql`.
- `dim_sector`: from `config/stocks.yaml: sectors`.
- `dim_stock`: join price symbols with `stg_meta`; map industry to sector via `industry_to_sector`, fall back to `config/stocks.yaml`; unmapped goes to `sector_source='unknown'` and is listed in the audit. Stocks without a sector are reported, not guessed.
- `dim_stock_alias`: from `config/stock_aliases.yaml` (name, short or ticker; `case_sensitive`, `needs_context`).
- `dim_event`: from `config/events.yaml` (v1) plus `config/events_v2.yaml`; keep only events inside the window; dedupe by date and label.
- `dim_trading_calendar` (`etl/calendar.py`): every calendar date in the window; `is_trading_day` = date exists in `fact_market_daily` (Nifty index); `effective_trading_date_key` = same date if trading, else the next trading date.
- Tests: every price file maps to one `dim_stock` row; no stock without sector except those logged as unknown; calendar has no gaps; effective date of a Saturday equals the following Monday (derive the expected value from the data, not a hard-coded date).

## V2: `fact_stock_daily` (`warehouse/stock_facts.py`)
SQL window functions in DuckDB over `stg_stocks` joined to dims. **All returns are percentage points**, matching `fact_market_daily.return_pct` (which is `(close/lag(close)-1)*100`, so `0.5042` means +0.50%):
- `return_pct = (adj_close / lag(adj_close) - 1) * 100` partitioned by stock, ordered by date; `abs_return_pct`; `range_pct = (high - low) / lag(close) * 100`; `volatility_20d = stddev(return_pct) over 20 rows`; `return_5d`; `next_day_return_pct = lead(return_pct)`; `next_day_up = next_day_return_pct > 0`.
- Compute from **`adj_close`** (dividend-adjusted), not raw `close`. Record which basis was used in the `return_basis` column.
- `is_outlier = abs_return_pct > 25.0` (**percentage points**). The original pack said `0.25`, which would have been wrong by a factor of 100. Count and list them in `reports/data_quality.md`.
- Tests: recompute returns for one stock in pandas and compare; (date, stock) unique; first row per stock has NULL return; flagged outliers excluded by a view `v_stock_daily_clean`.

## V3: News linking (`linking/`)
1. `features/market_report.py`: flag `fact_headline.is_market_report` with a lexicon in `config/lexicons/market_report.txt` (e.g. sensex, nifty, "points", rally, plunge, tanks, "market closes"). Also fill `effective_trading_date_key` through `dim_trading_calendar`.
2. `linking/mentions.py`: for each alias build a word-boundary regex (compile once). Case-insensitive unless `case_sensitive`. An alias with `needs_context` or in `ambiguous` counts only if the headline contains a word from `context_words` **or** its topic equals `use_topic_as_context`. Set `match_method` (`exact`, `ticker`, `context`). Process headlines in chunks; write to `fact_news_stock_mention` with `INSERT ... SELECT` from a pandas chunk table.
3. `linking/sector_news.py`: sector from (a) stock mentions via `dim_stock.sector_key` and (b) `config/sector_keywords.yaml` keyword matches. Aggregate to `fact_news_sector_daily` by calendar date and sector: `news_count`, `news_count_non_market`, `sum_sentiment`, `high_sensational_count`; `source` = mention, keyword or both. A headline counts once per sector per day.
4. `linking/audit_sample.py`: write `reports/mention_audit_sample.csv` (200 matches stratified by stock and by ambiguous vs not; columns `headline_id`, `symbol`, `matched_alias`, `headline_text`, `is_correct` empty). A second command computes precision from the filled CSV and writes `mining_mention_audit`. **Never write `is_correct` yourself** — it is a human judgement.
5. Coverage report in `reports/data_quality.md`: share of headlines with a stock mention, share with a sector, top 10 mentioned stocks, ambiguous-match share.
- Tests: mention rules on fixture headlines (ambiguous alias without context is rejected, with context accepted, ticker case enforced); sector fact sum equals distinct headline-sector pairs; no mention row without a stock key.

## V4: Cubes and OLAP v2
- `cube_sector_month`: join `fact_news_sector_daily` (via effective trading date) with `fact_stock_daily` (clean view) by sector and month. Store sums and counts only (`trading_days`, `sum_return`, `sum_abs_return`, `stock_obs`).
- `olap/sector_ops.py`: reuse the v1 pattern (whitelisted identifiers, parameterised values). New functions: `sector_slice(year|sector)`, `sector_dice(years, sectors, include_market_reports)`, `sector_rollup(level)` with `ROLLUP`, `sector_drilldown(year, quarter, month)` ending at the headlines behind a month (max 50 rows, with their stock tags), `sector_pivot(metric)`, `sector_cube()`, `stock_profile(symbol)`, and `drill_across_sector(sector, lag)` joining news and price facts through the effective trading date, lags -5 to +5.
- Add the SQL of each to `sql/olap_cookbook.sql`.
- Tests: roll-up totals equal raw sums; sector-month cube equals the same query on the facts; drill-across row count equals trading dates present on both sides; invalid names rejected.
