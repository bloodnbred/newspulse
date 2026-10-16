-- Schema additions for NewsPulse Nifty (v2).
--
-- Run AFTER the v1 warehouse exists. Idempotent: safe to re-run.
-- v1 DDL lives inline in Python (dwm/etl/dims.py, dwm/warehouse/facts.py,
-- dwm/warehouse/cubes.py); sql/schema.sql is a generated reference dump of the
-- built warehouse, not the source of truth.
--
-- Conventions inherited from v1 (verified against warehouse/dwm.duckdb):
--   * Returns and volatilities are PERCENTAGE POINTS, not fractions.
--     fact_market_daily.return_pct = 0.5042 means +0.5042%.
--   * abs_change is a price difference (close - prev_close), not an absolute return.
--   * range_pct = (high - low) / prev_close * 100.
--   * v1 declares NO primary keys, unique or foreign key constraints anywhere. The
--     PRIMARY KEYs below are new and safe, because every table they belong to is new.
--     Uniqueness of v2 fact grains is asserted in tests, as v1 does.
--
-- Key naming: fact_headline's key column is `headline_id` (BIGINT), NOT `headline_key`.

-- ---------- New dimensions ----------
CREATE TABLE IF NOT EXISTS dim_sector (
  sector_key  INTEGER PRIMARY KEY,
  sector_name VARCHAR UNIQUE       -- Financial Services, IT, Automobile, Pharma,
                                   -- Energy, FMCG, Metals, Infrastructure, Telecom, Consumer
);

CREATE TABLE IF NOT EXISTS dim_stock (
  stock_key       INTEGER PRIMARY KEY,
  symbol          VARCHAR UNIQUE,   -- NSE symbol, e.g. RELIANCE
  company_name    VARCHAR,
  sector_key      INTEGER,
  isin            VARCHAR,
  in_nifty50      BOOLEAN,
  first_trade_date DATE,
  last_trade_date  DATE,
  sector_source   VARCHAR,          -- 'override' | 'config' | 'meta' | 'unknown'
                                   -- 'override' = config/stocks.yaml `overrides`
                                   --   won over the provider's own industry label
                                   -- 'config'   = per-symbol fallback in stocks.yaml
                                   -- 'meta'     = mapped from the NSE industry label
                                   -- 'unknown'  = no sector could be assigned; reported,
                                   --   never guessed
  price_source    VARCHAR,          -- 'yahoo_chart_api' | NULL when no prices
  is_survivor     BOOLEAN           -- FALSE when the stock first trades after the
                                   -- window starts, i.e. it is in the index today
                                   -- but was not there when the news was published
);

-- Alias -> stock. One row per spelling a headline might use.
CREATE TABLE IF NOT EXISTS dim_stock_alias (
  stock_key      INTEGER,
  alias          VARCHAR,
  alias_type     VARCHAR,           -- name | short | ticker
  case_sensitive BOOLEAN,
  needs_context  BOOLEAN,
  provider_symbol VARCHAR           -- Yahoo ticker if it differs from `alias` (e.g. ETERNAL -> ZOMATO)
);

CREATE TABLE IF NOT EXISTS dim_event (
  event_key   INTEGER PRIMARY KEY,
  event_label VARCHAR,
  event_type  VARCHAR,              -- policy | shock | election | budget | global | crisis
  start_date  DATE,
  end_date    DATE,
  sector_key  INTEGER               -- NULL = market-wide
);

-- One row per CALENDAR date in the window, so weekend and holiday news has a home.
-- is_trading_day is COPIED from dim_date.is_trading_day, never recomputed here.
-- effective_trading_date_key = date_key when trading, else the next trading day's key.
-- NULL after the last trading day (news with no price yet is kept, it simply has no join target).
CREATE TABLE IF NOT EXISTS dim_trading_calendar (
  date_key                   INTEGER PRIMARY KEY,
  is_trading_day             BOOLEAN,
  effective_trading_date_key INTEGER
);

-- ---------- Existing table extensions ----------
-- Headline that REPORTS a market move ("Sensex tanks 500 points"). Excluded from
-- predictive analysis to break reverse causality.
ALTER TABLE fact_headline ADD COLUMN IF NOT EXISTS is_market_report BOOLEAN;
ALTER TABLE fact_headline ADD COLUMN IF NOT EXISTS effective_trading_date_key INTEGER;

-- ---------- New facts ----------
-- Grain: one stock per trading day.
-- Returns are computed from adjclose (dividend-adjusted). close is kept raw for reference.
CREATE TABLE IF NOT EXISTS fact_stock_daily (
  date_key      INTEGER,
  stock_key     INTEGER,
  dataset_key   INTEGER,
  trade_date    DATE,
  open          DOUBLE,
  high          DOUBLE,
  low           DOUBLE,
  close         DOUBLE,             -- as published
  adj_close     DOUBLE,             -- dividend-adjusted; returns use this
  volume        DOUBLE,
  turnover      DOUBLE,             -- DERIVED as close * volume, not exchange-reported
                                   -- (turnover_is_derived below records this)
  turnover_is_derived BOOLEAN,      -- always TRUE for rows built from this source
  return_pct    DOUBLE,             -- (adj_close / prev_adj_close - 1) * 100
  abs_change    DOUBLE,             -- adj_close - prev_adj_close
  abs_return_pct DOUBLE,            -- abs(return_pct); named apart from abs_change on purpose
  range_pct     DOUBLE,             -- (high - low) / prev_close * 100
  volatility_20d DOUBLE,            -- stddev_samp of the last 20 return_pct, percentage
                                   -- points; NULL until the window is full. Same
                                   -- definition as fact_market_daily.volatility_20d
  return_5d     DOUBLE,             -- vs the close 5 sessions ago, not vs the day before
  return_sign   INTEGER,            -- -1 | 0 | 1
  is_outlier    BOOLEAN,            -- abs(return_pct) > 25.0 (percentage points)
  next_day_return_pct DOUBLE,       -- target source, modelling only
  next_day_up   BOOLEAN,
  return_basis  VARCHAR             -- 'adjclose' | 'close'; records what was actually used
);

-- Grain: one headline x one stock.
CREATE TABLE IF NOT EXISTS fact_news_stock_mention (
  headline_id               BIGINT,
  stock_key                 INTEGER,
  date_key                  INTEGER,
  effective_trading_date_key INTEGER,
  matched_alias             VARCHAR,
  match_method              VARCHAR,   -- exact | ticker | context
  is_ambiguous_alias        BOOLEAN,
  is_market_report          BOOLEAN
);

-- Grain: one sector per calendar date (news side).
CREATE TABLE IF NOT EXISTS fact_news_sector_daily (
  date_key                   INTEGER,
  effective_trading_date_key INTEGER,
  sector_key                 INTEGER,
  news_count                 BIGINT,
  news_count_non_market      BIGINT,   -- excludes market-report headlines
  sum_sentiment              DOUBLE,
  high_sensational_count     BIGINT,
  source                     VARCHAR    -- 'mention' | 'keyword' | 'both'
);

-- ---------- Cubes (sums and counts only, never averages) ----------
CREATE TABLE IF NOT EXISTS cube_sector_month (
  year SMALLINT, month TINYINT, sector_key INTEGER,
  news_count BIGINT, news_count_non_market BIGINT,
  sum_sentiment DOUBLE, high_sensational_count BIGINT,
  trading_days INTEGER, sum_return DOUBLE, sum_abs_return DOUBLE, stock_obs BIGINT
);

-- ---------- Mining outputs ----------
CREATE TABLE IF NOT EXISTS mining_event_study (
  event_key INTEGER, stock_key INTEGER, sector_key INTEGER,
  alpha DOUBLE, beta DOUBLE, est_days INTEGER,
  car_0_1 DOUBLE, car_0_5 DOUBLE, car_m1_5 DOUBLE,
  valid BOOLEAN, reason VARCHAR
);

CREATE TABLE IF NOT EXISTS mining_stock_clusters (
  stock_key INTEGER, cluster_id SMALLINT,
  ann_volatility DOUBLE, mean_daily_return DOUBLE, beta DOUBLE, max_drawdown DOUBLE,
  mentions_per_1000_days DOUBLE, mention_sentiment DOUBLE, news_sensitivity DOUBLE,
  pc1 DOUBLE, pc2 DOUBLE
);

CREATE TABLE IF NOT EXISTS mining_market_rules (
  rule_id INTEGER, antecedents VARCHAR, consequents VARCHAR,
  support DOUBLE, confidence DOUBLE, lift DOUBLE, support_count INTEGER
);

CREATE TABLE IF NOT EXISTS mining_direction_results (
  model VARCHAR, split VARCHAR, n INTEGER,
  accuracy DOUBLE, balanced_accuracy DOUBLE, f1 DOUBLE,
  baseline_majority DOUBLE, baseline_yesterday DOUBLE, binom_p_vs_baseline DOUBLE,
  notes VARCHAR
);

CREATE TABLE IF NOT EXISTS mining_surprise_days (
  date_key INTEGER, sector_key INTEGER,
  news_heat_z DOUBLE, abs_return_z DOUBLE, avg_return DOUBLE,
  kind VARCHAR                       -- news_driven | quiet_news_move
);

-- Grain: one audited mention. is_correct is filled BY HAND; the pipeline never writes it.
CREATE TABLE IF NOT EXISTS mining_mention_audit (
  headline_id BIGINT, stock_key INTEGER, matched_alias VARCHAR, headline_text VARCHAR,
  is_correct BOOLEAN
);