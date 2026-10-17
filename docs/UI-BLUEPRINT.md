# NewsPulse Nifty: UI Blueprint (max edition)

## 1. Goals
1. **Three-second rule.** A new visitor knows what this is, why it matters, and what to click within three seconds of landing.
2. **Make people want to play with it.** Motion, depth, instant feedback, and one signature interaction (the Time Machine).
3. **DWM visible everywhere.** Each chart carries a concept chip and a "How this was computed" drawer (concept, SQL, milliseconds, rows).
4. **Heavy slicing.** Every chart filters everything else; Cube Studio lets anyone build OLAP views.
5. **Honest.** Disclaimer always visible in the shell; model results shown against baselines.

Tagline: **"Watch India's news and the Nifty move together."**

## 2. Stack
React + TypeScript + Vite, Tailwind CSS (utilities), Framer Motion, Apache ECharts (heatmap, treemap, sankey, graph, calendar, candlestick, funnel, radar, gauge, timeline all built in), TanStack Query, Zustand (+ URL sync), React Router, lucide-react, cmdk (command palette), @tanstack/react-table, @xyflow/react (schema diagram), optional @dnd-kit/core (drag and drop). Backend unchanged: FastAPI `/ui/*` (see `UI-API-CONTRACT.md`).

## 3. Design system
- **Look:** dark control-room theme with a slowly drifting gradient mesh background, glass cards (blur + hairline border), soft glow on hover and on active items. Light theme available via toggle (`data-theme="light"` tokens).
- **Colour meaning (never break):** teal = news, amber = price/market, violet = mining outputs, green/red = up/down returns only. Categorical series (sectors) use the colour-blind-safe palette in `echartsTheme.ts`.
- **Type:** Inter for text, JetBrains Mono for numbers (tabular figures). Large numbers 28 to 40 px, labels 12 px uppercase muted.
- **Spacing and shape:** 16 px radius cards, 16 to 24 px gaps, 12-column grid on desktop, single column on mobile.
- **Motion rules:** entrances 400 to 600 ms with ease `cubic-bezier(.22,1,.36,1)`, staggered 50 ms; count-ups 900 ms; chart animation 600 ms; hover lift 2 px; page transitions fade/slide 250 ms. Everything off under `prefers-reduced-motion`.
- **Feedback:** skeleton shimmer while loading, friendly empty states ("Run `python -m dwm mine events` first"), inline error with retry.
- Tokens and effect classes: `src/styles/tokens.css` (`.glass`, `.glow`, `.mesh-bg`, `.skeleton`, `.tape`, `.icon-btn`, `.nav-link`).

## 4. App shell
- **Ticker tape** at the top: latest sector moves, auto-scrolling, pause on hover.
- **Sidebar** (desktop) with icons: Home, Cube Studio, Stock 360, Event Lab, Mining Lab, Headline Lab, Data & Schema, Insights. **Bottom nav** on mobile.
- **Global filter bar** (sticky under the ticker): date range, sector multi-select chips, topic select, toggle "Include market-report headlines", Reset. Shows active filter count with animated badge.
- **Command palette** (Ctrl/Cmd+K): search stocks, sectors, events, pages, actions ("Study demonetisation", "Open Cube Studio on Pharma").
- **Under-the-hood drawer** on every ChartCard.
- **Tour / Demo mode** button: guided highlights (step list in section 7) and a 60 s auto-play for presentations.
- **Theme toggle**, **keyboard shortcuts** (`?` help, `t` theme, `r` reset filters), **shareable URL** (filters live in the query string).

## 5. Interaction model
- **Cross-filtering:** clicking a treemap tile toggles that sector in the global filters; brushing the main chart's date slider sets `from`/`to`; clicking a calendar day sets the Time Machine focus date; clicking a KPI navigates with filters preserved. All charts refetch via `useApi`.
- **Hover:** tooltips with context (value, share, change vs previous period), linked highlighting across charts where easy (ECharts `connect`).
- **Drill:** breadcrumbs (All years / 2018 / Q3 / Aug / headlines). Clicking a bar or cell drills; breadcrumb click rolls up.
- **Everything shareable:** copy-link button copies the current URL with filters.

## 6. Page specs

### 6.1 Home (the showpiece)
Layout (desktop 12-col grid):
1. **Hero strip.** Left: tagline, one-line promise, big search ("Search a company, sector or event, Ctrl K") with Quick Brief result appearing below as an animated card. Right: **Market Mood gauge**: animated needle, score, label (Calm / Active / Heated), percentile vs the five-year history. Source: `/ui/home.mood`.
2. **KPI row (6 cards)** with count-up and sparklines: headlines analysed, stocks tracked, trading days, Nifty change over the window, risk-signal rate, surprise days found. Each is clickable.
3. **Time Machine** (signature, large, 8 cols): news heat (teal area) vs Nifty (amber line), event flags as labelled vertical markers, dataZoom slider that sets the global date range, metric switch (heat / sentiment / sensational), hover crosshair that updates the **Time Machine panel** (4 cols): date, Nifty move, top headlines of the day (max 8 with topic chips and stock tags), "Unusual day?" badge, link "Open full day". A **Replay** button animates the focus date through the window.
4. **Sector Heat treemap** (6 cols): tile size = news volume, tile colour = mean return (red to green); click to filter; hover shows heat z. **Surprise days** (6 cols): scrollable cards with badges `News-driven` / `Quiet-news move`, sector, date, z-scores; click opens that day in the Time Machine.
5. **Calendar heatmap** (8 cols): news heat per day across years with event dots; click selects the day. **Sector race** (4 cols): animated rank chart (bump or horizontal bars that reorder) of sectors by news heat per quarter with play button.
6. **Insights carousel**: auto-advance, pause on hover, concept chip, "Open" CTA.
7. **Action tiles (4)** with hover tilt: Build an OLAP view, Study an event, Check a headline, Find similar stocks.
8. Shell footer with disclaimer.
First paint budget: hero + KPIs visible with skeletons immediately; below-the-fold charts render when scrolled into view.

### 6.2 Cube Studio (heavy slicing)
- **Field shelf (left):** dimensions (Year, Quarter, Month, Weekday, Sector, Topic, Stock, Sentiment, Sensational level) and measures (News count, News heat, Mean sentiment, Risk-signal rate, Mean return, Volatility, Abs return). Drag onto **Rows / Columns / Filters / Values** zones (dnd-kit or native DnD), with click-to-add as a fallback.
- **Operation chips:** Slice, Dice, Roll-up, Drill-down, Pivot, Cube. Selecting one explains it in one line and highlights the relevant zone.
- **View switcher:** Heatmap, Table (sortable, sticky headers, virtualised), Bars, Treemap, Sunburst (hierarchy year to quarter to month).
- **Drill:** click a cell or bar to drill; breadcrumb to roll up; "Show headlines" opens the 50 headlines behind the cell.
- **Replay:** animate the heatmap over time (play, pause, speed) using `/ui/replay`.
- **SQL drawer** always available; **Save view** (localStorage, named) and **Copy link**; export CSV and PNG.

### 6.3 Stock 360
- Search or arrive from a link. Header: company, symbol, sector chip, cluster badge, last price and daily change with sparkline.
- Main chart: candlestick + volume with mention markers (amber dots on dates where headlines mention the stock), dataZoom; a **Rebased vs Nifty** toggle (both start at 100).
- Right panel: **headlines timeline** synced to the visible date range (virtualised), with sentiment dots.
- Below: **peer radar** (volatility, beta, news sensitivity, mention rate, sentiment) against the cluster average; **event reactions** bars (CAR per event); **Similar stocks** cards (same cluster) that open their pages.

### 6.4 Event Lab
- Horizontal **event timeline** with glowing nodes by type (budget, policy, shock, election, global, crisis); drag or click to select; keyboard arrows move between events.
- Selected event: **CAR by sector** animated bars (with n stocks and an uncertainty note), **stock leaderboard** (sortable table with top and bottom reactors), **news volume around the event** (-10 to +10 trading days), **event-day headlines**.
- **Compare mode:** two events side by side (e.g. Budget 2019 vs Budget 2020).
- Wording: "coincides with"; overlap warning chip when events are close.

### 6.5 Mining Lab (tabs)
- **Stock clusters:** PCA scatter with lasso brush; selected stocks appear in a table; legend filters clusters; cluster profile cards with auto-names.
- **Rules:** force-directed graph (nodes = items, edge width = lift) plus sortable table; sliders for min support, confidence, lift; the first three rules rewritten in plain English.
- **Direction model:** accuracy vs baselines bar chart, confusion matrix heatmap, decision-tree rules as readable text, honesty banner when `beats_baseline` is false.
- **Co-mention network:** stocks as nodes coloured by sector, edges = headlines mentioning both; click a node opens Stock 360.

### 6.6 Headline Lab
- Large textarea; analysis updates live (400 ms debounce): **hype gauge**, sentiment bar, topic chip, **percentile track** ("more hyped than 87% of five years of headlines"), **highlighted trigger words** in the text, up to 5 **similar past headlines** with dates, **tagged stocks and sectors** (click to open).
- "Try these" chips load real headlines from `/ui/check/samples`.
- Caveat banner: style signal, not a fact-check.

### 6.7 Data & Schema
- **Interactive schema** (React Flow): fact tables as large nodes, dimensions as small nodes, edges for keys; click a node for grain, row count, columns; layout makes the fact constellation obvious.
- **ETL funnel** per dataset (tabs): read, dropped by reason, kept (ECharts funnel or sankey); mention coverage and precision cards; limitations list; methodology accordion (OLAP, clustering, Apriori, event study, validation split).

### 6.8 Insights
Card grid (masonry) with concept filters; each card has evidence numbers, "Open page" link, and "Download card" (PNG).

## 7. Tour and demo mode
Guided tour steps (highlight element, caption, Next/Back/Skip):
1. Search bar: "Ask about any company or sector."
2. Market Mood gauge: "How heated news is versus the last five years."
3. Time Machine: "Hover to travel through time; drag the slider to filter the whole app."
4. Treemap: "Click a sector to filter everything."
5. Surprise days: "Days when news and prices disagreed."
6. Command palette: "Press Ctrl+K from anywhere."
7. Under-the-hood icon: "See the SQL and the DWM concept behind any chart."
**Demo mode (60 s auto-play):** Home (hover sweeps through an event) → Cube Studio (drill a cell) → Event Lab (select the lockdown event) → Mining Lab (cluster lasso) → Headline Lab (types a real sample) with a progress bar; any click stops it.

## 8. Effects catalogue (use, with budget)
| Effect | Where | Notes |
|---|---|---|
| Gradient mesh background drift | shell | CSS only |
| Glass cards with hover glow | everywhere | CSS |
| Count-up numbers | KPIs, mood | `useCountUp`, reduced-motion safe |
| Sparklines | KPI cards | inline SVG |
| Staggered entrance | card grids | Framer Motion |
| Gauge needle sweep | mood, hype | ECharts gauge |
| Ticker tape | shell | CSS marquee, pause on hover |
| Crosshair + linked highlighting | main charts | ECharts `connect` |
| Replay animation | Time Machine, Cube Studio, sector race | play/pause/speed |
| Treemap, sunburst, sankey, calendar, radar, graph, candlestick, funnel | pages above | all ECharts |
| Skeleton shimmer | loading | CSS |
| Page transitions | router | Framer Motion `AnimatePresence` |
| Tilt on action tiles | Home | small transform, no library needed |
| Confetti or sound | **not used** | keep it credible |

## 9. Performance and quality rules
- `/ui/home` served from cubes; one request for the homepage; TanStack Query caching (5 min stale time).
- Lazy-load pages with `React.lazy`; lazy-render below-the-fold charts; downsample long series (weekly grain for wide ranges).
- Virtualise lists over 50 rows; paginate headline lists.
- Targets: first meaningful paint under 2 s on a laptop with the dev backend; Lighthouse performance 80+ on desktop (check with the browser's built-in Lighthouse).
- No console errors; TypeScript strict; no hard-coded data.

## 10. Responsive and accessibility
- 360 to 1920 px; mobile uses a single column, bottom nav, collapsible filter bar, charts at 240 to 300 px height.
- Focus rings, aria-labels, keyboard navigation for timeline and palette, "View as table" fallback for each chart, colour-blind-safe palette, contrast checked on both themes.

## 11. Acceptance checklist
- [ ] Landing page explains itself in 3 seconds; search, mood gauge, 6 KPIs, Time Machine, treemap, surprise days, calendar, carousel, action tiles all render with real data.
- [ ] Clicking a treemap tile filters Time Machine, calendar and KPIs; URL updates; reload restores the state.
- [ ] Ctrl+K opens the palette and navigates.
- [ ] Every chart has concept chip, caption and SQL drawer.
- [ ] Cube Studio: build a pivot by drag and drop, drill to headlines, replay, save and share.
- [ ] Event Lab, Stock 360, Mining Lab, Headline Lab, Data & Schema all work on real data.
- [ ] Mobile layout usable; reduced-motion respected; disclaimer visible.
- [ ] `?mock=1` shows the red MOCK DATA ribbon; real mode does not.
- [ ] Tour and 60 s demo mode run end to end.
