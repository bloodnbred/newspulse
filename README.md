# NewsPulse Nifty

**What this is.** A research project that puts Indian business news and the Indian stock market side by side, so you can ask *"on days when the market moved a lot, what was the news saying?"* — and get an answer you can check.

It does three things: it **collects** the raw material, it **organises** it into a proper database that can answer questions fast, and it **puts a dashboard in front of it** so you can ask the questions yourself without writing any code.

**Who it's for.** Anyone curious whether news and prices move together. It is a university project, not a trading tool.

---

## Run it

You need **Python 3.11+** and **Node.js 18+**. Then, from this folder:

| Your computer | Type this |
|---|---|
| Windows | `.\start.ps1` |
| Mac / Linux | `./start.sh` |

That's it. The script installs what's missing, builds the database the first time, starts everything and opens the dashboard at **http://localhost:5173**. Press `Ctrl+C` to stop it.

The first build downloads about 30–60 minutes' worth of data. That is the only way to get the whole thing, and I would recommend it — most pages need it.

**If you just want a quick look**, add `-Sample 50000` (Windows) or `-s 50000` (Mac/Linux). It runs in a few minutes, and the homepage, the filter bar, Cube Studio and Headline Lab all work. But be aware what it is not:

- It takes the **earliest** rows of each file, so you get roughly the first year of the five, not all five years. Anything that spans time — the Time Machine, the calendar, the surprise-day count — will look sparse.
- Several mining results will be empty or will report "not enough data". Stock clustering, the event study and the surprise days all need the full history. The pages say so rather than showing a wrong number.
- It is a preview. **Run it once without `-Sample` before you show anyone a number.**

Already got the data? Then `./start.sh` skips straight to starting.

---

## The honest warning, up front

**This is not investment advice, and nothing here predicts anything.**

What the project can tell you is *"these two things happen at the same time."* It cannot tell you *"this news caused that move,"* because proving that would need data this project does not have.

Where a number is weaker than it looks, the page says so next to the number. This is deliberate and it is the most important thing to know about the project: a tool that only shows its successes is worse than useless.

---

## What you can do with it

### The home page — your starting point

The big picture. Six numbers at the top, then the interesting parts.

- **The six numbers.** How many headlines were read, how many companies are tracked, how many trading days, what the Nifty did, what share of headlines were flagged as "risky writing", and how many surprising days were found. Every one comes from a database query, never a typed-in constant.
- **Market mood.** The gauge on the right. It answers *"how busy has the news been lately, compared with its own history?"* — a percentile, not a score. Out of the whole corpus it currently reads **27th percentile, "Calm"**: quieter than 73% of the days in the window. Note the caption: it always says what it's being compared against, because "busy" only means something relative to something.
- **Time Machine.** The chart in the middle. Drag the slider to travel through the five years. Red marks are days the market moved unusually far. Click any day to see what was written that day.
- **The calendar.** Every day of the corpus as a coloured square. Dark means busy. Trading days and market holidays are marked differently — a great many headlines land on days the market was shut.
- **The sector square-map.** Each rectangle is an industry. Bigger = more news about it. Green or red = whether the stocks in it went up or down that month. **Click any sector to filter the whole page to it.**
- **Surprise days.** Days where the price moved far but the news was quiet. Click one to see it.

### The filter bar — at the top of the home page

Four controls: a date range, sector chips, and a checkbox for "market-report headlines".

Pick a date range and everything on the page narrows to it. Pick sectors and the sector figures narrow.

**One thing to know:** picking a sector does *not* narrow the headline count. That's not a bug — it's how the data is built. A headline only "belongs to" a sector if it happens to name a company in that sector, so most headlines belong to no sector at all. When you have a filter on, an amber strip appears under the bar telling you exactly what moved and what didn't. Read it; it's the honest bit.

### Search anything — press `Ctrl + K`

Type a company, a sector, or a page name and jump straight there. Type "TCS" and you get the company's page.

### Cube Studio — ask your own questions

The most powerful page, and the one that rewards curiosity.

Pick a **cube** (a pre-calculated table). Pick **measures** (headline count, average sentiment, average return, and so on). Put measures on **rows** and **columns**. Pick **filters**. Drag dimensions around.

The chart redraws as you go. Underneath, the drawer shows **the actual SQL query** that produced what you're looking at, and you can copy it. You never have to trust the chart — you can read the question it asked.

It will also refuse things, and tell you why. Try dragging **Sector** in while the **News** cube is selected. *Sector* belongs to the **Market** cube, and the two cubes are different shapes — one row per day per topic against one row per stock per day — so a query across both would count every headline once for every stock that traded that day. Cube Studio says exactly that and suggests slicing one and using Drill-across to bring the other in. A tool that silently gives you a wrong answer is worse than one that says no.

### Stock 360 — one company at a time

Every one of the 50 tracked companies has a page: its price history, what the news said about it on each day, its risk-signal rate, how often it's mentioned.

**Seven of them have gaps, and the pages say so rather than showing an empty chart.** Three (Eternal, Jio Financial, Max Healthcare) joined the index after our data ends, so there is no price history for them at all. Four (BSE, HDFC Life, IndiGo, SBI Life) joined partway through the window, so their charts start late. That's a gap in our data, not a gap in those companies.

### Event Lab — big market events

Defined events (budgets, elections, pandemic phases) with how the sectors and companies reacted around them.

The honest bit: it reports **"coincided with"**, never "caused". One event before the COVID crash is one observation. A test measures whether the pattern holds; if it doesn't, the page says so.

### Mining Lab — what the machine found

Four tabs of automated findings.

- **Clusters** — groups of companies that move together. Scroll to the bottom for the part that matters: one cluster holds **94.9%** of the companies. A clustering that puts 95% of things in one pile hasn't found much, and the page tells you that rather than showing you a pretty treemap.
- **Rules** — patterns like *"when these words appear together, that tends to follow."* Sliders let you demand stronger rules. Set the bar high and it honestly says fewer rules survive.
- **Models** — two models trained to predict things, shown together deliberately:
  - **Labelled-statement classifier: 95.7% accurate, against a 65.9% baseline.** It works.
  - **Direction model: 49.9% accurate, against a 50.5% baseline. p = 0.85 — no better than a coin flip.**

  The second one is on the same page as the first, on purpose. Hiding it would make the tool look better and be less trustworthy. A model that can't beat guessing has no skill, and that's a legitimate finding, not a failure.
- **Network** — which companies get mentioned in the same headlines. Sixty pairs, twenty-two above the threshold. Thin, and labelled as thin.

### Headline Lab — test a single headline

Paste any headline. The tool measures its writing style: how many exclamation marks, capital letters, superlatives, urgency words, and a **risk-signal rate**.

**Important:** it measures *style*, not truth. A sensational-sounding headline can be completely true, and a calm one can be false. Click one of the corpus's own headlines to see what the tool says about it.

### Data & Schema — what's actually in here

A map of the whole database. Every table, what each one holds, and how many rows. Click any table for its columns.

There's also an **ETL funnel** showing what was rejected and why — usually more interesting than what was accepted, because that's where "why is this number different from what I expected?" usually gets answered.

### Insights — the summary

Every finding the mining stage produced, on one page, each with its evidence and its caveat. Download any card as a PNG.

---

## Things this project genuinely cannot do

Listed because a tool that hides its limits is harder to use than one that names them.

| | Why |
|---|---|
| **It can't tell you what will happen tomorrow.** | The direction model is at chance level. The numbers on screen are the evidence for that. |
| **It can't tell you news *caused* a move.** | Only that they happened together. |
| **It can't tell you a headline is fake.** | It measures writing style. Style and truth are different things. |
| **Company mentions are unreliable.** | Matching " Infosys " in a headline is guesswork, and nobody has yet checked how often it's wrong. The project ships a sample for a human to check; until that's filled in, treat every company-specific number as approximate. |
| **Companies that left the Nifty are missing.** | We only have today's list of members, applied to a five-year-old window. Companies that were in it and are not now simply aren't there. |
| **It stops in June 2020.** | That's where the news data ends. The stock data continues past it, and the charts deliberately stop too, rather than comparing two different periods. |

---

## How it's put together, in one paragraph

The data arrives as several separate files — news headlines, labelled statements, index prices, per-company prices. They're cleaned, given a proper calendar, sorted into categories, and stored in a fast database in an arrangement where questions like "total headlines per month per topic" are already pre-calculated. Machine-learning models are then run over that to find clusters, patterns and models. A small web service reads the results, and the dashboard asks it for exactly what each screen needs. **Every number you see came from a query or a fitted model. None was typed in by hand.**

---

## Project layout

```
dwm/         the data work: collecting, cleaning, analysing, and the web service
web/         the dashboard you actually look at
sql/         the database structure
config/      settings for what to collect and how to categorise
tests/       the automated checks
reports/     generated summaries
start.ps1    Windows: set up and run
start.sh     Mac/Linux: set up and run
```

## If you want to work on it

```bash
python -m pytest            # run the checks
cd web && npm run build     # check the dashboard compiles
node scripts/uicheck.mjs    # open every page in a real browser and check nothing breaks
```

---

*Educational project. Not investment advice. Patterns shown are associations, not causes. Headline signals measure writing style, not truth.*