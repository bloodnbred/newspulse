# AGENTS.md addendum: front end (append to AGENTS.md, or keep beside it)

## Front-end layout (new top-level folder `web/`)
```
web/
  src/
    main.tsx App.tsx types.ts
    styles/tokens.css            # design tokens and effects (source of truth)
    lib/ api.ts filters.ts format.ts echartsTheme.ts
    hooks/ useCountUp.ts
    components/ AppShell Ticker KpiCard ChartCard (+ more per phase)
    pages/ Home Explore Stocks Events Mining Headline Data Insights
  public/mock/ui/*.json          # DEV ONLY mock responses, flagged mock:true
```
Stack: React + TypeScript + Vite, Tailwind CSS (utilities), Framer Motion, Apache ECharts (`echarts`, `echarts-for-react`), TanStack Query, Zustand, React Router, lucide-react, cmdk, @tanstack/react-table, @xyflow/react. Install with npm; do not pin versions by hand, let npm resolve and commit the lockfile.

## UI rules
1. **The web app talks only to `/ui/*` endpoints** (backend-for-frontend router). It contains no SQL, no analytics, no model code.
2. **Every number on screen comes from an API response.** No hard-coded statistics, sector lists, stock lists or event lists.
3. **Mock data is allowed only for development** (`?mock=1`), lives in `web/public/mock/`, every file has `"meta": {"mock": true}`, and the UI shows a visible red "MOCK DATA" ribbon when it is used. Never ship or screenshot mock data for the report.
4. **Every chart sits inside `ChartCard`** (title, concept chip, one-line data-generated caption, "How this was computed" drawer showing concept, SQL, milliseconds and rows, PNG export where possible).
5. **Cross-filtering:** global filters (date range, sectors, topics, include-market-reports) live in the Zustand store and the URL. Clicking a chart element updates the store; all charts refetch through `useApi`.
6. **Effects budget:** animations must respect `prefers-reduced-motion`; no more than 3 simultaneously animating charts above the fold; lazy-render charts below the fold with IntersectionObserver; lists over 50 rows are virtualised or paginated.
7. **Wording:** "risk-signal", "coincides with", "associated with"; the disclaimer footer appears in the shell. The direction model page must show the baseline comparison honestly.
8. **Accessibility:** keyboard reachable controls, visible focus, aria-labels on icon buttons, colour-blind-safe categorical palette (from `echartsTheme.ts`), chart "View as table" fallback.
9. **Responsive:** works from 360px to 1920px; bottom navigation on mobile; charts resize.
10. **Scope discipline:** one phase per session; do not touch backend files except in phase U1 and the backend parts of later phases; keep the Streamlit dashboard until phase U3 is accepted.
11. Definition of done per phase: `npm run build` succeeds with no TypeScript errors, `npm run lint` clean (if configured), pages render without console errors in the browser, and a short summary (files, what to look at, anything skipped).
