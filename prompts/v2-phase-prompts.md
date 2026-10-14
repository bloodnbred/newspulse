# OpenCode prompts for v2 (one per session)

## Saving tokens and resources
- One phase per session. Start a fresh session each time.
- Tell the model to read only the files named in the prompt. Do not paste the whole blueprint.
- Prefer reuse: the prompts tell the agent to extend existing modules. If it starts rewriting v1 code, say: "Stop. Do not rewrite v1 code. Extend it. Re-read AGENTS.md rule 9."
- Always develop on `--sample 50000`. Run the full dataset once at the end.
- After each phase: `git add . && git commit -m "v2 phase N"`. Reset the session if the model gets confused.
- If a phase output is large, ask the agent for "summary only, no file dumps".

---
## V0 (audit)
Use the prompt in OPENCODE_START_HERE.md.

## V1
Read AGENTS.md and docs/06-stocks-linking.md (section V1 only), plus config/datasets_v2.yaml, config/stocks.yaml, config/stock_aliases.yaml, config/events_v2.yaml and sql/schema_v2.sql. Reuse the v1 loader interface, etl_audit and build_dims.py: extend them, do not rewrite them. Implement stock and meta ingest, run schema_v2.sql, build dim_sector, dim_stock, dim_stock_alias, dim_event and dim_trading_calendar, and the tests listed. If data/raw/stocks or data/raw/meta is empty, implement and test with fixtures and tell me which files I must download. Run pytest and `python -m dwm ingest --dataset stocks --sample 50000`, then give a summary only.

## V2
Read AGENTS.md and docs/06-stocks-linking.md (section V2 only). Implement stock_facts.py to build fact_stock_daily and the clean view with the exact SQL window functions described, outlier flags, and the tests. Extend the data-quality report with outlier counts. Run pytest and the load command on the dev sample. Summary only: row counts, number of outliers, any stocks with missing data.

## V3
Read AGENTS.md and docs/06-stocks-linking.md (section V3 only), config/stock_aliases.yaml and config/sector_keywords.yaml. Reuse the v1 features chunking pattern. Implement the market-report flag, effective trading date, mention linking with the ambiguity rules, sector news facts, the 200-row mention audit CSV plus the precision script, and the coverage report. Write the tests listed. Run `python -m dwm link --sample 50000` and give me the coverage numbers and the top 10 matched stocks so I can check them by eye.

## V4
Read AGENTS.md and docs/06-stocks-linking.md (section V4 only). Extend the existing OLAP module and cubes.py: add cube_sector_month and sector_ops.py with the listed operations, whitelist identifiers and parameterise values, add the SQL to sql/olap_cookbook.sql, and write the reconciliation tests. Run pytest and show one example output per new operation (first 5 rows each).

## V5
Read AGENTS.md and docs/07-mining-insights-api.md (section V5 only). Add the five mining modules (event study, stock clusters, market rules, direction model, surprise days) following the v1 mining module pattern and writing mining_* tables and reports/mining/*.json. Enforce chronological splits and the baselines. Write the tests listed. Run each command on the dev sample and report the key metrics only. If the direction model is near the baseline, say so; do not tune until it looks good.

## V6
Read AGENTS.md and docs/07-mining-insights-api.md (section V6 only). Extend the existing inference module and FastAPI app (new routers only, v1 routes unchanged): insights.py, brief.py, stocks, sectors, events, brief, insights and the upgraded /check (reuse the v1 scorer). Save the TF-IDF model to models/ for neighbours. Write the endpoint tests. Run `python -m dwm serve`, call each new endpoint once, and give me the list of endpoints with status codes.

## V7
Read AGENTS.md, docs/08-dashboard-usability.md (sections Design system, Pages, V7) and dashboard/ui.py. Reuse the existing v1 dashboard code and charts: move them into the new pages, then retire old pages once replaced. Build Home.py and pages 1 to 8 as described. Use only the helpers in ui.py and the API; no SQL or analytics in dashboard files. Add the test that checks each page contains the mandatory helper calls. Run the dashboard on the dev sample and tell me the launch commands for Windows and any page that shows an empty state and why.

## V8
Read AGENTS.md and docs/08-dashboard-usability.md (Phase V8 only). Implement `python -m dwm usability`: read reports/usability_results.csv, compute success rate per task, median time per task, SUS mean, median and range, and write reports/usability_summary.md. Create a blank reports/usability_results.csv template with the right headers and a Google Forms question list in reports/usability_form.md. Do not invent any participant data.

## Final run
Run `python -m dwm all` without --sample, then `pytest`, and update README.md with setup, data download, commands and the demo script. Summary only; list anything that failed or was skipped.
