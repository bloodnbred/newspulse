# START HERE (paste into OpenCode)

## Setup (2 minutes)
1. Open your existing project folder.
2. Copy this pack contents into it. **Overwrite** `AGENTS.md`; add the new `docs/`, `config/`, `sql/`, `dashboard/ui.py`, `.streamlit/config.toml`, `prompts/`, `BLUEPRINT_V2.md`.
3. `git add . && git commit -m "add v2 blueprint pack"`
4. Download the extra data into `data/raw/stocks/` (one CSV per stock) and `data/raw/meta/` (stock list with company name, symbol, industry). Compare the headers with `config/datasets_v2.yaml`.
5. Start `opencode` and paste the prompt below. Then continue phase by phase using `prompts/v2-phase-prompts.md`.

## Prompt to paste first (Phase V0, audit only)

You are upgrading an existing Data Warehousing and Mining project called dwm-india-news into NewsPulse Nifty (v2).

Read these files completely, in this order: AGENTS.md, BLUEPRINT_V2.md, sql/schema_v2.sql, config/datasets_v2.yaml.

Task (Phase V0, AUDIT ONLY, do not change or rewrite any existing code):
1. Print the repository tree (src/, tests/, dashboard/, sql/, config/).
2. For each v1 module that exists, list its public functions and CLI commands.
3. Run `pytest` and report pass/fail counts.
4. Compare the existing warehouse (warehouse/dwm.duckdb, if present) with sql/schema.sql: list tables and row counts.
5. Compare sql/schema_v2.sql with the existing schema and list exactly which new tables and columns must be added.
6. List what v2 needs that is missing, mapped to phases V1 to V8 in BLUEPRINT_V2.md.
7. Write the result to reports/v2_audit.md.

Rules: follow AGENTS.md. Do not install new libraries yet; if a library is missing for a later phase, list it. Do not delete or refactor v1 code. Finish with a summary and wait for my next phase prompt.
