# OpenCode prompts for the UI build (one per session)

## Saving tokens
- One phase per session; start fresh each time. Say "summary only, do not print file contents".
- Read only the files and sections named. Never paste the whole blueprint.
- Build mock-first (`?mock=1`) so the UI progresses even if a backend endpoint is not ready.
- Commit after each accepted phase: `git add . && git commit -m "ui U<n>"`.
- If the model starts rewriting backend code: "Stop. Re-read AGENTS_UI_ADDENDUM.md rule 10."

---
## U0 (audit)
Use the prompt in UI_MASTER_PROMPT.md.

## U1 Backend-for-frontend router
Read AGENTS.md, AGENTS_UI_ADDENDUM.md and docs/UI-API-CONTRACT.md. Create the `/ui` router in the existing FastAPI app: envelope helper with timing middleware, CORS for http://localhost:5173 plus an env origin, caching headers, an in-process LRU cache, and the endpoints `/ui/tape`, `/ui/home`, `/ui/main-series`, `/ui/day/{date}`, `/ui/calendar`, `/ui/search`, `/ui/schema`, `/ui/etl`, `/ui/insights`. Reuse existing olap, mining and inference functions; write new SQL only where needed, from cubes and mining tables. Return 404 with a clear "run X first" hint where results are missing. Add tests (200 on the dev sample, envelope shape, invalid params 422). Summary only: endpoint list with status codes and timings.

## U2 Web scaffold
Read AGENTS_UI_ADDENDUM.md and web-scaffold/README.md. Create `web/` with Vite, React and TypeScript; install the packages listed in the README; set up Tailwind per its current Vite guide; copy every file from web-scaffold/src into web/src keeping paths; wire QueryClientProvider, BrowserRouter, fonts and tokens.css; add routes for all pages using a simple placeholder component for pages not yet built. Add dev-only mock files in web/public/mock/ui/ (small, flagged mock:true) for /ui/tape and /ui/home and the red MOCK DATA ribbon. Run `npm run build` and `npm run dev`. Summary only: the run commands and what to see at http://localhost:5173/?mock=1 and without mock.

## U3 Home dashboard
Read docs/UI-BLUEPRINT.md section 3 and 6.1, and AGENTS_UI_ADDENDUM.md. Build the Home page exactly as specified: hero strip with search and Market Mood gauge, 6 KPI cards, Time Machine chart with event markers, date slider that sets global filters, hover-driven day panel and Replay, sector treemap with click-to-filter, surprise-days cards, calendar heatmap, sector race, insights carousel, action tiles. Add the global filter bar and the ChartCard drawer wiring. Use real `/ui/*` data where available (otherwise mock). Respect reduced motion; lazy-render below the fold. Run build and describe what to look at. When I accept this phase, remove the old Streamlit pages that Home replaces.

## U4 Cube Studio
Read docs/UI-BLUEPRINT.md section 6.2 and docs/UI-API-CONTRACT.md (the `/ui/cube`, `/ui/cube/headlines`, `/ui/replay` rows). Backend first: implement those endpoints reusing sector_ops with whitelisted dimensions and measures, returning the SQL. Then build the Cube Studio page: field shelf with drag and drop (click fallback), operation chips with one-line explanations, heatmap/table/bars/treemap/sunburst views, drill with breadcrumbs, headlines drawer, replay, SQL drawer, save view and copy link, CSV and PNG export. Tests for the endpoints. Summary only.

## U5 Stock 360 and Event Lab
Read docs/UI-BLUEPRINT.md sections 6.3 and 6.4 and the contract rows for `/ui/stock/*` and `/ui/events*`. Implement the backend endpoints reusing stock_profile and mining_event_study, then both pages: candlestick with mention markers and rebased toggle, synced headline timeline, peer radar, event reactions, similar stocks; event timeline, CAR by sector, leaderboard, news around the event, compare mode. Tests for endpoints. Summary only.

## U6 Mining Lab and Headline Lab
Read docs/UI-BLUEPRINT.md sections 6.5 and 6.6 and the contract rows for `/ui/mining/*`, `/ui/check` and `/ui/check/samples`. Implement the endpoints (including the co-mention network query), then the Mining Lab tabs (cluster scatter with lasso, rules graph and table with sliders, direction model with the honesty banner, co-mention network) and the live Headline Lab (debounced, hype gauge, percentile track, highlighted trigger words, neighbours with dates, tags, real sample chips). Tests for endpoints. Summary only.

## U7 Data & Schema, Insights, palette, tour
Read docs/UI-BLUEPRINT.md sections 4, 6.7, 6.8 and 7. Build the React Flow schema diagram and ETL funnel pages, the Insights grid with PNG card download, the Ctrl+K command palette using `/ui/search`, keyboard shortcuts, the guided tour and the 60-second demo mode. Summary only.

## U8 Polish and ship
Read docs/UI-BLUEPRINT.md sections 9, 10 and 11. Go through the acceptance checklist item by item: responsive layouts at 360, 768 and 1440 px, reduced-motion, keyboard use, aria labels, "View as table" fallbacks, performance (lazy loading, downsampling), no console errors, remove all mock files and the mock mode from production builds. Update README.md with run commands, environment variables (VITE_API_URL), and a short deployment note (build the web app, serve static files, point it at the API). Report each checklist item as pass or fail with notes.
