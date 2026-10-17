# UI API contract (backend-for-frontend)

New router mounted at `/ui`. Reuse existing olap, mining and inference functions; only new SQL is for UI-shaped aggregates. v1 and v2 routes stay unchanged.

## Conventions
- Every response is an **envelope**: `{ "data": ..., "meta": { "concept": string, "sql": string?, "ms": number, "rows": number, "note": string?, "mock": boolean? } }`. A small middleware measures `ms`; the query layer returns the SQL it ran and the row count.
- Common query params (where relevant): `from`, `to` (ISO dates, default full window), `sectors` (comma list), `topics` (comma list), `include_market` (0 or 1, default 0).
- Errors: 404 with `{"detail": "Run `python -m dwm mine events` first"}` style hints; 422 for invalid params (whitelist identifiers).
- CORS: allow the web dev origin (`http://localhost:5173`) and an env-configured production origin.
- Caching: `Cache-Control: public, max-age=300` on read-only endpoints; in-process LRU for repeated identical queries.
- Sample mode: responses include `"devSample": true` in `data` when the warehouse was built with `--sample`.
- Response size cap: lists max 500 items unless stated; headline lists max 50 per page with `page` param.
- TypeScript types for the shapes live in `web/src/types.ts`; keep them in sync.

## Endpoints

| Endpoint | Purpose | Notes / reuse |
|---|---|---|
| `GET /ui/tape` | ticker items: latest-window sector moves | last trading days of the window, sector mean return; concept "Drill-across" |
| `GET /ui/home` | everything the homepage needs in one call (`HomeSummary`) | KPIs with sparklines (monthly series), mood gauge (percentile of current news heat vs full history), main series (monthly or weekly: news heat and Nifty), event marks, sector heat (news count, heat z, mean return), top 5 surprise days, top 6 insight cards |
| `GET /ui/main-series?grain=week\|month&metric=heat\|sentiment\|sensational` | main chart series with filters | from `cube_day_topic` / `cube_sector_month`, joined to `fact_market_daily` |
| `GET /ui/day/{date}` | Time Machine panel: top headlines (max 50, with topic, sentiment, stock tags), Nifty move, sector moves, "unusual?" flag | uses effective trading date; surprise-days table |
| `GET /ui/calendar?metric=heat` | per-day values for the calendar heatmap | cube_day_topic aggregate |
| `GET /ui/search?q=` | command palette: stocks, sectors, events, pages | `dim_stock_alias` plus fuzzy; max 12 items |
| `GET /ui/brief?q=` | Quick Brief for a company or sector | reuse v2 brief |
| `GET /ui/cube` | generic OLAP: `rows`, `cols`, `measure`, `op` (slice, dice, rollup, drilldown, pivot, cube), filters, `level` | reuse `sector_ops`; whitelist dimensions {year, quarter, month, weekday, sector, topic, stock, sentiment, sensational_level} and measures {news_count, heat, mean_sentiment, risk_rate, mean_return, volatility, abs_return}; return `columns`, `rows`, `matrix`, plus the SQL |
| `GET /ui/cube/headlines?cell=...` | headlines behind a clicked cell (drill to detail) | max 50, page param |
| `GET /ui/replay?measure=heat\|mean_return&grain=month` | frames for the animated sector heatmap: `[ {period, values:{sector:number}} ]` | cube_sector_month |
| `GET /ui/sankey?level=topic-sector-sentiment` | nodes and links (topic to sector to sentiment) | news-side counts only |
| `GET /ui/stock/{symbol}` | Stock 360: profile, OHLCV series, mention markers, rebased vs Nifty, peer radar traits, cluster, event reactions, similar stocks | reuse `stock_profile` |
| `GET /ui/stock/{symbol}/headlines?from&to&page` | headlines mentioning the stock | max 50 |
| `GET /ui/events` | events with type and date | `dim_event` |
| `GET /ui/events/{id}` | event study: CAR by sector (bars with n), stock leaderboard, news volume -10..+10 days, event-day headlines | reuse `mining_event_study` |
| `GET /ui/mining/clusters` | stock cluster points (pc1, pc2, cluster, symbol, traits) and cluster profiles | `mining_stock_clusters` |
| `GET /ui/mining/rules?min_lift&min_conf` | rules table plus nodes/links for the graph | `mining_market_rules` |
| `GET /ui/mining/direction` | model metrics vs baselines, confusion matrices, tree rules, honesty flag `beats_baseline` | `mining_direction_results` |
| `GET /ui/mining/network` | stock co-mention graph: nodes (symbol, sector, mentions), links (co-mention count) | self-join of `fact_news_stock_mention` on `headline_key`; keep links with count above a threshold |
| `POST /ui/check` | live headline analysis: score, level, sentiment, topic, percentile, trigger-word spans, neighbours (max 5, with dates), stock and sector tags | reuse v2 checker; must be fast (under about 1 s) |
| `GET /ui/check/samples` | 6 random real headlines from the warehouse for "try these" chips | never invented text |
| `GET /ui/schema` | fact-constellation graph: nodes (name, kind fact/dim, grain, rows, columns) and edges | from `information_schema` plus a small descriptive config |
| `GET /ui/etl` | funnel per dataset (read, dropped by reason, kept), mention coverage and precision, limitations | `etl_audit`, `dim_dataset` |
| `GET /ui/insights` | insight cards | reuse `insights.py` |

## Home endpoint rules
`/ui/home` must return in a single response and be fast (target under about 1.5 s on the full warehouse): serve it from cubes and mining tables, never scan `fact_headline`. Cache it.

## Mock-first development
Phase U2 may create `web/public/mock/ui/*.json` (one file per endpoint above, small, clearly flagged `meta.mock: true`). The web app reads them when the URL has `?mock=1`. This lets the UI be built before every backend endpoint exists. Replace with real data in each phase and delete the matching mock file.
