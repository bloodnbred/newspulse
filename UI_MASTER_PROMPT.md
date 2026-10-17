# UI MASTER PROMPT (paste into OpenCode first)

You are upgrading the front end of NewsPulse Nifty, an existing Data Warehousing and Mining project (DuckDB warehouse, Python mining code, FastAPI backend, a basic Streamlit dashboard). **Keep the backend and all mining code. Do not rewrite them.** You are replacing the Streamlit UI with a premium single-page web app that people actually want to open.

Mission: a "maxed-out" dashboard. The homepage must feel like a live market-and-news control room: animated KPIs with sparklines, a Market Mood gauge, a Time Machine chart that scrubs through five years of news versus the Nifty, a sector heat treemap, surprise-day cards, a calendar heatmap, an insights carousel, and a global search (Ctrl+K). Heavy slicing: every chart can filter the whole app (cross-filtering), and a Cube Studio lets users build OLAP views with drag-and-drop.

Read, in this order, and nothing else until told: AGENTS.md, AGENTS_UI_ADDENDUM.md, docs/UI-BLUEPRINT.md (sections 1 to 5 only), docs/UI-API-CONTRACT.md.

Phase U0 task (audit and plan only, change no code):
1. List the existing FastAPI routes and the olap/mining functions that already return the data described in docs/UI-API-CONTRACT.md.
2. For each `/ui/*` endpoint in the contract, say: reuse existing function X, or new SQL needed.
3. Check whether Node.js and npm are installed (`node -v`, `npm -v`).
4. List which parts of the old Streamlit dashboard have useful logic worth porting.
5. Write `reports/ui_audit.md` with a table and a proposed order of work (default order is U1 to U8 in prompts/ui-phase-prompts.md).
Finish with a short summary and wait for my next phase prompt.
