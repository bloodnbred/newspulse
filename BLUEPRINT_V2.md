# NewsPulse Nifty: Blueprint v2

A news-and-stock decision-support system built on a Data Warehouse. A user can see how Indian news and the Nifty 50 market move together, explore it with OLAP, and see data mining results, all in a dashboard. This upgrades the existing `dwm-india-news` project; it does not replace it.

---

## 1. Why this version exists
The professor's feedback on v1: the backend was strong, but nobody could *use* it, and the DWM concepts were invisible. v2 keeps the backend and adds a product on top. **Every screen shows which DWM concept produced its answer.**

### Who uses it (answer this in the viva)
| User | Job they do with it |
|---|---|
| Retail investor / finance student | "How did this sector behave around big news or events? Is today unusual?" |
| Media-literacy student / editor | "Is this headline hyped compared to 5 years of Indian news?" |
| Analyst | "Which sectors react most to news? Which stocks behave alike?" |

### User stories (drive every page)
1. As an investor, I pick a sector and see news heat next to price moves, so I can tell whether a move was news-driven.
2. As a student, I choose an event (demonetisation, COVID lockdown, a Budget day) and see which sectors reacted, to learn how markets respond to events.
3. As an analyst, I slice by year and sector and drill down to the headlines behind a spike.
4. As an analyst, I see stocks grouped by behaviour, so I can compare similar stocks.
5. As an editor, I paste a headline and get its hype percentile against history, with related stocks and sectors tagged.

---

## 2. What changes vs v1

| Area | v1 | v2 |
|---|---|---|
| Datasets | TOI headlines, IFND, Nifty 50 index | + **per-stock daily prices for Nifty 50 constituents** + stock/sector master + events |
| Facts | headline, statement, market_daily | + `fact_stock_daily`, `fact_news_stock_mention`, `fact_news_sector_daily` |
| Dimensions | date, topic, dataset, label, instrument, keyword | + `dim_stock`, `dim_sector`, `dim_event`, `dim_stock_alias`, `dim_trading_calendar` |
| Mining | trends, bursts, clusters, rules, NB | + event study (abnormal returns), stock clustering, market rules, next-day direction classifier, "surprise days" |
| Front end | single Streamlit page | **multipage dashboard with a design system**, concept labels, how-computed panels, insights feed |
| Evaluation | tests only | + usability study (SUS) with 15+ users |

---

## 3. Architecture

```
data/raw/{toi,ifnd,nifty,stocks,meta}
      |
  INGEST -> ETL -> FEATURES -> LINKING (news<->stock/sector) -> WAREHOUSE (fact constellation) -> CUBES
      |
  OLAP engine ----+---- MINING engine ----+---- INSIGHTS engine
                  |                       |
                FastAPI  (/olap /mining /stocks /events /check /insights /export)
                  |
         Streamlit multipage dashboard (talks to the API only)
```

### Fact constellation v2
```
dim_date / dim_trading_calendar ---+---------------------------------------+
dim_topic -- fact_headline --------+-- bridge_headline_keyword -- dim_keyword
dim_sector -- fact_news_sector_daily
dim_stock  -- fact_news_stock_mention (headline x stock)
dim_stock  -- fact_stock_daily ----+
dim_instrument -- fact_market_daily (Nifty 50 index)
dim_event (event windows, optional sector)
dim_label -- fact_statement (IFND)
```
Conformed dimensions: date, sector, stock, topic, dataset. This allows drill-across: news facts to price facts through date and sector.

---

## 4. Key design decisions (read these before coding)

1. **Trading calendar.** News is published on weekends and holidays; markets are not open. `dim_trading_calendar` maps every calendar date to its *effective trading date* (same day if open, otherwise the next trading day). All news-to-price joins use the effective trading date.
2. **Reverse causality.** Many business headlines *report* the market move ("Sensex tanks 500 points"). `fact_headline.is_market_report` is flagged by a lexicon. Predictive analysis uses non-market-report news only. Descriptive dashboards show both with a toggle.
3. **No look-ahead.** Any prediction uses only information known at the end of day t to predict day t+1. Train, validation and test splits are chronological, never shuffled.
4. **Corporate actions.** Raw prices are not adjusted for splits and bonus issues. Any daily |return| > 25% is flagged `is_outlier` and excluded from statistics (kept in the table).
5. **Stock-name matching is noisy.** Ambiguous aliases (Titan, Axis, Tata, HDFC, Reliance) require business context. A manual precision audit of 200 matches is part of the deliverable. Sector-level news (keyword lexicon) is the robust fallback when stock-level mentions are sparse.
6. **Honest claims.** Disclaimer on every page: educational, not investment advice, associations not causes, headline signals measure style not truth. If prediction is near the baseline, the app says so.
7. **Front end only calls the API.** No analytics logic in the dashboard.

---

## 5. Phases and gates (v2)

| Phase | Output | Gate |
|---|---|---|
| V0 Audit | audit report of existing repo, no rewrites | `pytest` still green, report lists what exists/missing |
| V1 Stock ingest + dims | `stg_stocks`, `stg_meta`, `dim_sector`, `dim_stock`, `dim_stock_alias`, `dim_event`, `dim_trading_calendar` | every price file maps to a stock; every stock has a sector |
| V2 Stock facts | `fact_stock_daily` with returns, volatility, flags | return recompute check; outlier flags counted; no duplicate (date, stock) |
| V3 News linking | `fact_news_stock_mention`, `fact_news_sector_daily`, `is_market_report` | mention audit file produced; sector facts reconcile to headline counts |
| V4 Cubes + OLAP v2 | `cube_sector_month`, sector/stock OLAP ops, drill-across v2 | roll-up equals raw; drill-across row counts match |
| V5 Mining v2 | event study, stock clusters, market rules, classifier, surprise days | chronological split test; baseline comparison present |
| V6 Insights + API v2 | `/insights`, `/stocks`, `/events`, `/check` v2 | all endpoints 200 on dev sample |
| V7 Dashboard v2 | multipage Streamlit with design system | all pages render; every page has question box, how-computed panel, disclaimer |
| V8 Evaluation + polish | SUS results, demo script, final report | 15+ responses recorded, report updated |

Dev rule: `--sample` on every command, full run only at end of a phase.

---

## 6. Professor demo script (5 minutes)
1. **Home** (20 s): the three questions the app answers, KPI strip, insights feed.
2. **Market Mood** (45 s): news mood vs Nifty, show lag chart. *Concept: drill-across.*
3. **Sector Cube** (60 s): slice to 2018, roll up to quarter, drill into one month's headlines, show the SQL panel. *Concept: OLAP.*
4. **Event Study** (60 s): choose COVID lockdown, show sector abnormal returns. *Concept: fact joins, event dimension.*
5. **Mining Lab** (60 s): stock clusters, a rule with lift, classifier vs baseline (honest result). *Concept: clustering, Apriori, classification.*
6. **Checker** (30 s): paste a headline, show percentile and tagged sectors.
7. **Data and Schema** (25 s): ETL funnel, constellation diagram, mention-match precision.

## 7. Deliverables
- Working repo and hosted link (or local run instructions plus video).
- `reports/final_report.md` plus project report chapters: problem, users, datasets, ETL, schema, OLAP, mining, results, usability evaluation, limitations.
- Usability study results table (n >= 15).
- 5-minute demo script above.

## 8. Viva additions
- **Why an event dimension?** Events are descriptive context shared by all facts; modelling them as a dimension lets any fact be sliced by "within N days of event X".
- **Why a trading calendar table?** News and prices have different calendars; the mapping prevents joining weekend news to nothing.
- **Why chronological splits?** Shuffled splits leak the future into training and inflate accuracy.
- **Why abnormal returns?** Raw returns include the market's move; subtracting beta times the market return isolates the stock's own reaction.
- **Why store sums in cubes?** Sums roll up exactly; ratios do not.
- **What are the limits?** Style-based headline signals, name-matching noise, unadjusted prices, one news outlet, 5-year window, correlation not causation.

## 9. Risks and fallbacks
| Risk | Fallback |
|---|---|
| Per-stock dataset unavailable | Use sector-level analysis on the Nifty 50 index plus sector keyword news only; say so |
| Few stock mentions in TOI | Lean on sector-level news facts; report mention coverage |
| Price files have gaps or different symbols over time | Map by ISIN/symbol alias table; log unmatched in `etl_audit` |
| Streamlit slow on big tables | Query cubes, not facts; `st.cache_data`; paginate headline lists |
| Hosting limits | Ship aggregates and models only; raw headlines stay local (also avoids redistributing data) |

---

## 10. Reuse map (do not scrap v1)

| Existing v1 piece | v2 use |
|---|---|
| Loaders, `etl_audit`, data-quality report | Same interface for stocks and meta; report gains outlier and mention coverage sections |
| `dim_date`, `dim_topic`, `dim_dataset`, `dim_label`, `dim_keyword` | Unchanged; shared by all new facts |
| `fact_headline`, features, keyword bridge | Unchanged; two new columns (`is_market_report`, `effective_trading_date_key`) |
| `fact_market_daily` (Nifty index) | Benchmark for beta, abnormal returns and the trading calendar |
| Cubes and OLAP module | Extended with sector and stock operations |
| Trends, bursts, clustering, Apriori, classifier, IFND | Kept; they feed Data and Schema, Checker and Insights |
| Inference engine and `final_report.md` | Extended with a market section; insights engine added |
| FastAPI v1 routes | Unchanged; new routers added |
| Streamlit v1 pages and charts | Moved into pages 1 to 8; old pages retired only after replacements work |
| Headline checker | Reused; gains percentile, neighbours and stock/sector tags |
| Tests | All stay green; new phase tests added |

## 11. Why people would actually use it
- **Quick Brief:** type a company or sector, get a plain-English summary. No finance or DWM knowledge needed.
- **Headline Checker:** paste a headline, learn how hyped it is versus five years of news.
- **Event Study:** see how sectors reacted to Budgets, GST, demonetisation, the lockdown.
- **Surprise days:** find days when the market moved without much news, or news was hot and prices barely moved.
- Evidence: 15+ users, SUS score, task success rates (Phase V8).

## 12. Token and effort budget
- One phase per session, `--sample 50000`, summary-only replies.
- Roughly 9 sessions in total. Phases V1 to V4 are mostly SQL; V5 and V7 are the heaviest.
- If time is short, build in this order: V0, V1, V2, V3, V4, V7 (Home, Sector Cube, Stock Explorer, Checker), then V5 (event study first), V6, V8.
