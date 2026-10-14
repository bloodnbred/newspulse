# Phases V5 and V6: mining v2, insights, API v2

Reuse: v1 `mining/` module layout, `reports/mining/*.json` outputs, the `inference/` templates and the v1 API router style. Add modules; keep v1 results.

## V5: Mining v2
Fixed seed, results to `mining_*` tables and `reports/mining/*.json`.

### Event study (`mining/event_study.py`)
For each event in `dim_event` and each stock with enough data:
- Event day = first trading day on or after the event start date.
- Estimation window: trading days [-130, -11] before the event day; require at least 100 valid days (exclude outliers). Fit `r_stock = alpha + beta * r_nifty` (OLS).
- Abnormal return `AR_t = r_t - (alpha + beta * r_nifty_t)`. Cumulative abnormal return: `CAR[0,+1]`, `CAR[0,+5]`, `CAR[-1,+5]`.
- Write `mining_event_study` (invalid rows kept with a reason). Aggregate by sector and event: mean CAR, number of stocks, cross-sectional t-statistic and p-value. Flag overlapping events (within 10 trading days).
- Wording: "sector X's average abnormal return was ... in the days after event Y", never "caused".

### Stock clusters (`mining/stock_clusters.py`)
Per stock over the full window (clean data): annualised volatility (std*sqrt(252)), mean daily return, beta vs Nifty, max drawdown, mentions per 1000 trading days, mean sentiment of mentioned headlines, news sensitivity (Spearman between daily non-market news count and abs_return; NULL if under 100 news days). Standardise, `KMeans` for k in 3..6, choose by silhouette, PCA to 2D for plotting. Auto-name clusters from standardised centroids (e.g. "high volatility, high news sensitivity"). Save `mining_stock_clusters`.

### Market rules (`mining/market_rules.py`)
Transactions = (sector, trading date). Items: `sector=`, `news_heat=low|mid|high` (tertiles of non-market news count within sector), `sentiment=neg|neu|pos`, `ret=down_big|down|up|up_big` (sector mean return quantile buckets), `next=up|down`, `vol_regime=low|high`, `weekday=`, `event_window=1`. Apriori with `min_support=0.02`, `max_len=3`, confidence 0.55 or more, lift 1.15 or more, support count 30 or more. Write `mining_market_rules`. Descriptive only; the page says so.

### Direction model (`mining/direction_model.py`)
- Rows = clean stock-days. Features known at end of day t: stock-level and sector-level non-market news counts and mean sentiment (trailing 1 and 5 days), `return_t`, `return_5d`, `vol_20d`, weekday, month, high-sensational share.
- Target: `next_day_up`.
- **Chronological** split by date: first 70% train, next 15% validation (tuning), last 15% test. No shuffling.
- Models: DecisionTree (max_depth 3 to 5, print readable rules), Naive Bayes (binned features), Logistic Regression.
- Baselines on test: majority class, "same direction as yesterday". Report accuracy, balanced accuracy, F1, confusion matrix, and a binomial test (`scipy.stats.binomtest`) of accuracy against the majority baseline. Save `mining_direction_results` and joblib models in `models/`.
- The app must state plainly when the test result is not meaningfully above baseline. That is an acceptable, honest result.

### Surprise days (`mining/surprise_days.py`)
Per sector-day, z-scores against the trailing 60 trading days: `news_heat_z` (non-market news count) and `abs_return_z`. `news_driven`: both z > 2. `quiet_news_move`: abs_return z > 2 and news z < 0. Save `mining_surprise_days`.

### Tests
Event study recovers alpha and beta on a planted fixture; CAR is zero when stock equals market exactly; split dates strictly increase train < validation < test; no feature uses data after day t (test with a shifted-feature assertion); baselines computed on test only; clusters reproducible.

## V6: Insights and API v2

### Insights engine (`inference/insights.py`)
Produces a list of cards (JSON): `id`, `title`, `text`, `evidence` (numbers), `concept`, `page`. Templates only, numbers from tables:
1. Top 3 surprise days (news_driven) with sector and date.
2. Biggest sector reaction to the most recent event in the window (mean CAR, n stocks).
3. Strongest news-price lag association (lag, correlation, n, tests run).
4. Hottest sector in the last 90 days of the window by news heat vs its own history.
5. One stock cluster insight (cluster name, members, defining trait).
6. Direction model honesty card (accuracy vs baseline).
Guard rails: skip a card when its evidence is below the minimum sample size; add DEV SAMPLE prefix in sample mode.

### Quick Brief (`inference/brief.py`) for ordinary users
Input: a company name, ticker or sector. Output (plain English, template-based): what the news says lately (counts, mood vs its own average), unusual days, latest event reaction, how it compares with peers, plus links to the detailed pages. Resolve names via `dim_stock_alias`, with fuzzy fallback (`difflib.get_close_matches`) and a "did you mean" list.

### API v2 (new routers; v1 routes unchanged)
| Endpoint | Returns |
|---|---|
| `GET /stocks` / `GET /stocks/{symbol}` | list; profile (price series, volatility, mentions, headlines page, peers) |
| `GET /sectors/heat` | sector news heat vs return by period |
| `GET /olap/sector/{slice,dice,rollup,drilldown,pivot,cube,drill-across}` | sector OLAP |
| `GET /events` / `GET /events/{id}/study` | event list; CAR by sector and stock |
| `GET /mining/{stockclusters,marketrules,direction,surprise}` | stored results |
| `GET /brief?q=` | Quick Brief |
| `POST /check` | headline checker: score, sentiment, topic, percentile vs warehouse, 5 similar past headlines, tagged stocks and sectors |
| `GET /insights` | cards |
| `GET /quality` | v1 funnel plus mention coverage and precision |
Return 404 with a clear "run X first" message when a phase has not been run. Enable response caching for read-only endpoints.

### Checker upgrade (reuse v1 checker)
Keep the existing scoring. Add: percentile vs `fact_headline` distribution (precomputed quantile table), nearest neighbours (TF-IDF cosine over a sample using saved `models/tfidf.joblib`), and stock and sector tags using the same alias rules as linking. Show only headline text and dates for neighbours.

### Tests
All endpoints 200 on the dev sample; 404 messages readable; `/check` handles empty, very long and non-English input; `/brief` resolves a ticker, a company name and a sector, and suggests alternatives for a typo.
