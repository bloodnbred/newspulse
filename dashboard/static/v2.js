/* NewsPulse Nifty — v2 dashboard views.
 *
 * Separate from app.js so the v1 pages stay readable and so a syntax error here
 * cannot stop the v1 shell from booting. Loaded AFTER app.js, so it can read the
 * helpers it reuses (`api`, `chart`, `table`, `tabs`, `callout`, `esc`, `md`,
 * `kpi`, `into`, `state`) rather than reimplementing them.
 *
 * The rule that governs every view below: the answer comes before the evidence.
 * Each page opens with the question it answers and the decision it supports,
 * then the chart, then how it was computed, then what may not be claimed.
 *
 * Nothing here computes. Every figure arrives from an endpoint in dwm/api/,
 * which is the only place allowed to touch a warehouse table.
 */
(function () {
  "use strict";

  const V2 = {};

  /* ── shared pieces ─────────────────────────────────────────────────────── */

  /** The question box every page opens with.
   *
   *  `decision` is not decoration. A chart without the decision it supports is
   *  a curiosity, and naming the decision is what makes the rest of the page
   *  worth reading. */
  function questionBox(rq, question, decision) {
    return `
      <div class="answer">
        <div class="answer__q">
          <span class="answer__rq">${esc(rq)}</span>
          <span>${esc(question)}</span>
        </div>
        <div class="answer__body">${md(decision)}</div>
      </div>`;
  }

  /** "How this was computed" - names the DWM concept, per the design rules. */
  function howComputed(concept, body) {
    return callout("evidence", "How this was computed", `**${concept}** — ${body}`);
  }

  /** The panel that says what may not be claimed. Sits with the figure. */
  function caution(body) {
    return callout("caution", "Before you quote this", body);
  }

  /** An empty state that names the command to run.
   *
   *  "No data" is a dead end. Every empty state in v2 says which phase produces
   *  this, so a reader who hits one knows what to do and a presenter is never
   *  stranded mid-demo. */
  function emptyState(message, command) {
    return `<div class="empty">${esc(message)}
      ${command ? `<div class="empty__cmd">Run <code>${esc(command)}</code> and reload.</div>` : ""}
    </div>`;
  }

  /** A form control row. `label` is always visible; placeholders are not labels. */
  function field(label, id, options) {
    const o = options || {};
    if (o.options) {
      return `<label class="field">
        <span class="field__label">${esc(label)}</span>
        <select class="field__input" id="${esc(id)}">
          ${o.options.map((v) => `<option value="${esc(v)}"${v === o.value ? " selected" : ""}>${esc(o.labels && o.labels[v] ? o.labels[v] : v)}</option>`).join("")}
        </select>
      </label>`;
    }
    return `<label class="field">
      <span class="field__label">${esc(label)}</span>
      <input class="field__input" id="${esc(id)}" type="${o.type || "text"}"
             value="${esc(o.value == null ? "" : o.value)}"
             placeholder="${esc(o.placeholder || "")}" ${o.attrs || ""}>
    </label>`;
  }

  function controls(inner, id) {
    return `<div class="controls" id="${esc(id)}">${inner}
      <button class="btn btn--primary" data-v2-refresh type="button">Show</button>
    </div>`;
  }

  /** Wait for the shell's chart mount pass, then render specs we fetched. */
  function mountSpecs(specs) {
    if (!specs) return;
    for (const [name, spec] of Object.entries(specs)) {
      const host = document.getElementById(`chart-${name}`);
      if (!host || !spec || !Object.keys(spec).length) continue;
      if (spec.dwm_empty) {
        host.classList.add("chart__body--empty");
        host.innerHTML = `<div class="chart__error">${esc(spec.dwm_empty)}</div>`;
        continue;
      }
      try {
        vegaEmbed(host, spec, { actions: false });
      } catch (error) {
        host.innerHTML = `<div class="chart__error">This chart could not be drawn: ${esc(String(error))}</div>`;
      }
    }
  }

  /** Load the v2 chart specs. Cached per query so paging does not refetch. */
  const specCache = new Map();
  async function loadSpecs(params) {
    const key = JSON.stringify(params);
    if (specCache.has(key)) return specCache.get(key);
    const promise = api("/ui/v2charts", params);
    specCache.set(key, promise);
    try {
      return await promise;
    } catch (error) {
      specCache.delete(key);
      throw error;
    }
  }

  /** Put fetched specs where `chart()` looks for them.
   *
   *  `chart()` in app.js resolves a spec out of `state.charts`. v2 fetches its
   *  specs separately, per page and per query, so without publishing them here
   *  `chart()` finds nothing and emits its "no data for the current results"
   *  box - which is exactly what every v2 chart did, and it looks like a data
   *  problem rather than a wiring one.
   *
   *  Must be called BEFORE the HTML that contains `chart(...)` is generated. */
  function publishSpecs(payload) {
    if (payload && payload.charts) {
      state.charts = Object.assign({}, state.charts, payload.charts);
    }
    return (payload && payload.charts) || null;
  }

  function clearSpecCache() { specCache.clear(); }

  /* ── 1. Overview: Quick Brief + insights feed ─────────────────────────── */

  V2.pageOverview = function () {
    const host = `
      ${questionBox(
        "Home",
        "What does the news say about a company or sector in this corpus?",
        "Type a company, a ticker or a sector below. The brief answers in plain English, " +
        "with the numbers it used and what those numbers cannot tell you. " +
        "**Decision it supports:** whether a name is worth a closer look before you go to a real data source."
      )}
      <div class="brief">
        <form class="brief__form" id="brief-form">
          ${field("Company, ticker or sector", "brief-q", {
            placeholder: "Reliance, TCS, Infosys, Energy…",
            attrs: 'autocomplete="off" list="brief-options"',
          })}
          <datalist id="brief-options"></datalist>
          <button class="btn btn--primary" type="submit">Brief me</button>
        </form>
        <div id="brief-out"></div>
      </div>

      ${sectionHead("What this study found", "Six findings, each carrying its own limit. Cards that could not clear their evidence floor are listed underneath, with the floor they missed.")}
      <div id="insights-out"></div>`;

    setTimeout(async () => {
      wireBrief();
      const out = $("#insights-out");
      if (!out) return;
      try {
        const payload = await api("/insights");
        into(out, renderInsights(payload));
      } catch (error) {
        into(out, emptyState(`The insights feed could not load: ${error.message}`, "python -m dwm mine all"));
      }
    }, 0);
    return host;
  };

  function renderInsights(payload) {
    const cards = (payload.cards || []).map((card) => `
      <article class="card insight">
        <header class="insight__head">
          <h3 class="insight__title">${esc(card.title)}</h3>
          ${card.dev_sample ? '<span class="tag tag--caution">dev sample</span>' : ""}
        </header>
        <div class="insight__concept">${esc(card.concept)}</div>
        ${insightBody(card)}
        ${card.caution ? caution(card.caution) : ""}
      </article>`).join("");

    const withheld = (payload.withheld || []).length
      ? `<div class="callout callout--muted">
           <span class="callout__label">Withheld</span>
           ${md("These findings were not shown because their evidence fell below the floor " +
                "this project sets for publishing a number:\n\n" +
                (payload.withheld || []).map((w) => `- **${w.card}** — ${w.why}`).join("\n"))}
         </div>`
      : "";

    if (!cards) return emptyState("No findings cleared their evidence floor yet.", "python -m dwm mine all");
    return `<div class="insightgrid">${cards}</div>${withheld}`;
  }

  /** Each card renders its own evidence; they are six different shapes. */
  function insightBody(card) {
    const e = card.evidence || {};
    switch (card.id) {
      case "surprise_days":
        return table([
          { key: "sector", label: "Sector" },
          { key: "session", label: "Session" },
          { key: "news_count", label: "Headlines", num: true },
          { key: "news_heat_z", label: "News z", num: true },
          { key: "abs_return_z", label: "Move z", num: true },
          { key: "avg_return_pct", label: "Mean return %", num: true },
        ], e.days || []);
      case "event_reaction":
        return kpiRow([
          ["Sector", e.sector],
          ["Mean CAR[0,+5]", e.mean_car_0_5_pct == null ? "n/a" : `${num(e.mean_car_0_5_pct)} %`],
          ["Stocks in the mean", e.n_stocks],
          ["p-value", e.p_value == null ? "not testable" : String(e.p_value)],
        ]);
      case "lag_association":
        return kpiRow([
          ["Strongest lag", `${e.lag > 0 ? "+" : ""}${e.lag} session(s)`],
          ["Spearman ρ", e.spearman_rho],
          ["p, corrected for the search", e.p_value_bonferroni],
          ["Sector-days correlated", num(e.n)],
        ]);
      case "hottest_sector":
        return kpiRow([
          ["Sector", e.sector],
          ["Headline-days, last 90 days", e.news_90d == null ? "n/a" : num(e.news_90d)],
          ["Its typical sector-month", e.typical_sector_month],
          ["Times its own average", e.times_typical == null ? "n/a" : `${e.times_typical}×`],
        ]);
      case "stock_cluster":
        return `<div class="clust">
            <div class="clust__members">${(e.members || []).map((m) =>
              `<span class="pill">${esc(m)}</span>`).join("")}</div>
            ${kpiRow([
              ["Mean annual volatility", e.mean_ann_volatility],
              ["Mean beta", e.mean_beta],
              ["All-stock mean volatility", e.all_stocks_mean_volatility],
              ["Mean mentions per 1000 days", e.mean_mentions_per_1000_days],
            ])}
          </div>`;
      case "direction_honesty":
        return kpiRow([
          ["Best model", e.best_model],
          ["Its test accuracy", pct(e.accuracy)],
          ["Majority-class baseline", pct(e.baseline_majority_class)],
          ["Same-as-yesterday baseline", pct(e.baseline_same_as_yesterday)],
        ]);
      default:
        return `<pre class="prejson">${esc(JSON.stringify(e, null, 2))}</pre>`;
    }
  }

  function kpiRow(pairs) {
    return `<div class="kpigrid">${pairs.map(([label, value]) => `
      <div class="kpi">
        <div class="kpi__label">${esc(label)}</div>
        <div class="kpi__value">${esc(value == null ? "n/a" : String(value))}</div>
      </div>`).join("")}</div>`;
  }

  function wireBrief() {
    const form = $("#brief-form");
    if (!form || form.dataset.wired) return;
    form.dataset.wired = "1";
    const input = $("#brief-q");

    api("/stocks").then((d) => {
      const list = $("#brief-options");
      if (!list) return;
      into(list, (d.stocks || []).slice(0, 50)
        .map((s) => `<option value="${esc(s.symbol)}">${esc(s.company_name || s.symbol)}</option>`).join(""));
    }).catch(() => { /* the list is a convenience; the search still works */ });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const out = $("#brief-out");
      const q = (input.value || "").trim();
      if (!q) {
        into(out, `<div class="empty">Type a company, ticker or sector first.</div>`);
        return;
      }
      into(out, `<div class="loading">Looking up ${esc(q)}…</div>`);
      try {
        into(out, renderBrief(await api("/brief", { q })));
      } catch (error) {
        into(out, emptyState(`The brief could not load: ${error.message}`,
                              "python -m dwm ingest --dataset stocks"));
      }
    });
  }

  function renderBrief(payload) {
    if (payload.kind === "none") {
      const options = (payload.did_you_mean || []).map((o) =>
        `<li><button class="linkish" data-brief-q="${esc(o.name)}">${esc(o.name)}</button>
             <span class="dim">${(o.similarity * 100).toFixed(0)}% similar</span></li>`).join("");
      return `<div class="callout callout--caution">
          <span class="callout__label">No match</span>
          ${md(payload.message)}
          ${options ? `<ul class="suggestions">${options}</ul>` : ""}
          ${payload.caveats.map((c) => `<p class="dim">${esc(c)}</p>`).join("")}
        </div>`;
    }

    const paragraphs = (payload.paragraphs || []).filter(Boolean)
      .map((p) => `<p>${md(p)}</p>`).join("");
    const numbers = payload.numbers || {};
    const kpis = Object.entries(numbers)
      .filter(([, v]) => v !== null && v !== undefined)
      .map(([k, v]) => `<div class="kpi">
          <div class="kpi__label">${esc(k.replace(/_/g, " "))}</div>
          <div class="kpi__value">${esc(typeof v === "number"
            ? (Math.abs(v) < 1 && v !== 0 ? v.toFixed(4) : num(v)) : String(v))}</div>
        </div>`).join("");

    const recent = payload.kind === "stock" && (payload.recent_headlines || []).length
      ? `<h3 class="subhead">Most recent of our headlines naming it</h3>` +
        table([
          { key: "date", label: "Date" },
          { key: "text", label: "Headline" },
          { key: "sentiment", label: "Sentiment", num: true,
            html: (r) => (r.sentiment == null ? "n/a" : num(r.sentiment, 3)) },
        ], payload.recent_headlines, {
          empty: "None of our headlines name it inside the window.",
          caption: "OUR headlines from one outlet. A mention means the name appeared, not that the story was about it.",
        })
      : "";

    const constituents = payload.kind === "sector" && (payload.constituents || []).length
      ? `<h3 class="subhead">Constituents we hold prices for</h3>
         <div class="pillrow">${payload.constituents.map((s) =>
           `<span class="pill">${esc(s)}</span>`).join("")}</div>
         ${caution("Today's Nifty 50 list applied to a historical window. Companies that were constituents in 2015–2020 and have since been removed are absent, and some present members listed after the window opened.")}`
      : "";

    return `
      <div class="brief__body">
        <div class="brief__title">${esc(payload.name)}
          ${payload.match_quality < 1
            ? `<span class="tag">matched at ${(payload.match_quality * 100).toFixed(0)}% similarity</span>`
            : ""}
        </div>
        ${paragraphs}
        ${numbers ? `<div class="kpigrid">${kpis}</div>` : ""}
        ${constituents}
        ${recent}
        <div class="callout callout--caution">
          <span class="callout__label">What this is not</span>
          <ul class="bullets">${(payload.caveats || []).map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
        </div>
      </div>`;
  }

  /* ── 2. Market Mood: drill-across ──────────────────────────────────────── */

  V2.pageMood = function () {
    const host = `
      ${questionBox(
        "RQ8",
        "Does a sector's news volume move with its share prices?",
        "Pick a sector and a lag. A lag of **−1** shifts news one session *earlier* than price, " +
        "which is the only view that can show news leading price rather than following it. " +
        "**Decision it supports:** whether the news stream carries any timing information at all."
      )}
      ${controls(
        field("Sector", "mood-sector", { placeholder: "all sectors" }) +
        field("Grain", "mood-grain", {
          value: "month",
          options: ["month", "week", "day"],
          labels: { month: "Monthly (whole window)", week: "Weekly", day: "Daily (last 250 sessions)" },
        }) +
        field("Lag", "mood-lag", { value: "0", type: "number", attrs: 'min="-5" max="5" step="1"' }),
        "mood-controls"
      )}
      <div id="mood-out"></div>`;

    setTimeout(async () => {
      const out = $("#mood-out");
      const refresh = async () => {
        const sector = $("#mood-sector").value.trim() || null;
        const grain = $("#mood-grain").value;
        const lag = Number($("#mood-lag").value || 0);
        into(out, `<div class="loading">Drilling across news and price…</div>`);
        try {
          const [specs, study] = await Promise.all([
            loadSpecs({ theme: state.theme, grain, lag, sector }),
            api("/olap/sector/drill-across", { sector, lag }),
          ]);
          const charts = publishSpecs(specs);
          into(out, `
            ${chart("v2_market_mood", "Sector news volume",
                    "Accent line: headline-days in the sector, averaged over each period. " +
                    "This is the series the study is about.",
                    "Read the shape, not the height. " +
                    (lag === 0 ? "News and price are on the same session here."
                               : `News is shifted ${Math.abs(lag)} session(s) ${lag > 0 ? "later" : "earlier"} than price.`))}
            ${chart("v2_market_price", "Sector mean return, same periods",
                    "Dashed grey: mean return across the sector's stocks, weighted by stock-days. " +
                    "Recessive on purpose - the market is context around the news, not the subject.",
                    "A separate panel on purpose, not a second axis on the one above. " +
                    "Compare the shapes; the heights are not comparable.")}
            ${howComputed("Drill-across",
              "`fact_news_sector_daily` and `fact_stock_daily` have different grains and no direct " +
              "path between them. They meet through the conformed trading-date key from " +
              "`dim_trading_calendar`, which is what a fact constellation exists for.")}
            ${caution(study.caution || "News volume is not sentiment, and a shared time axis is not a causal claim.")}
            ${tableFromOlap(study)}`);
          mountSpecs(charts);
        } catch (error) {
          into(out, emptyState(`The drill-across could not run: ${error.message}`, "python -m dwm link"));
        }
      };
      $("#mood-controls").addEventListener("click", (e) => {
        if (e.target.dataset.v2Refresh) refresh();
      });
      $("#mood-controls").addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); refresh(); }
      });
      await refresh();
    }, 0);
    return host;
  };

  /** Render an OLAP result using the column list the API already computed. */
  function tableFromOlap(result) {
    if (!result || !result.rows || !result.rows.length) {
      return emptyState("That slice returned no rows.", "python -m dwm link");
    }
    return table(olapColumns(result.rows, result.columns), result.rows, {
      caption: result.description,
    });
  }

  /* ── 3. Stock Explorer ─────────────────────────────────────────────────── */

  V2.pageStocks = function () {
    const host = `
      ${questionBox(
        "RQ9",
        "What did one company's coverage look like, and how did its shares behave?",
        "Search a ticker or a company name. You get a plain-English brief, the price series " +
        "with our headlines marked underneath it, and the mention counts. " +
        "**Decision it supports:** whether this name's coverage in our corpus is dense enough to analyse at all."
      )}
      ${controls(field("Ticker or company", "stk-q", {
        placeholder: "TCS, Reliance, Infosys…",
        attrs: 'list="stk-options" autocomplete="off"',
      }), "stk-controls")}
      <datalist id="stk-options"></datalist>
      <div id="stk-out"></div>`;

    setTimeout(async () => {
      api("/stocks").then((d) => {
        const list = $("#stk-options");
        if (list) into(list, (d.stocks || []).map((s) =>
          `<option value="${esc(s.symbol)}">${esc(s.company_name || s.symbol)}</option>`).join(""));
      }).catch(() => {});

      const out = $("#stk-out");
      const run = async (symbol) => {
        const q = symbol || $("#stk-q").value.trim();
        if (!q) { into(out, `<div class="empty">Type a ticker or company first.</div>`); return; }
        into(out, `<div class="loading">Loading ${esc(q)}…</div>`);
        try {
          const [brief, profile] = await Promise.all([
            api("/brief", { q }),
            api("/stocks/" + encodeURIComponent(brief.symbol || q.toUpperCase())),
          ]);
          const charts = publishSpecs(await loadSpecs({
            theme: state.theme, symbol: brief.symbol || q.toUpperCase(),
          }));
          into(out, `
            ${brief.kind === "none"
              ? `<div class="callout callout--caution"><span class="callout__label">No match</span>${md(brief.message)}</div>`
              : renderBrief(brief)}
            ${profile.operation
              ? chart("v2_stock_price", `${brief.symbol || q} price and mentions`,
                      "Accent line: adjusted close. Amber bars: our headlines naming it that session.",
                      "Returns are percentage points from adjusted closes.")
                + howComputed("Time series join",
                    "Prices and mentions meet on the trading-date key, so a headline only counts " +
                    "against a session the market was actually open.")
                + caution(profile.caution || "")
              : emptyState("No price history for this symbol inside the window.", "python -m dwm ingest --dataset stocks")}
            ${profile.operation ? table(olapColumns(profile.rows, profile.columns),
                                        profile.rows.slice(0, 50), {
                                          caption: profile.description,
                                          empty: "No rows.",
                                        }) : ""}`);
          mountSpecs(charts);
        } catch (error) {
          into(out, emptyState(`Could not load ${q}: ${error.message}`,
                                "python -m dwm ingest --dataset stocks && python -m dwm build"));
        }
      };
      $("#stk-controls").addEventListener("click", (e) => {
        if (e.target.dataset.v2Refresh) run();
      });
      $("#stk-controls").addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); run(); }
      });
    }, 0);
    return host;
  };

  /* ── 4. Sector Cube ────────────────────────────────────────────────────── */

  V2.pageCube = function () {
    const host = `
      ${questionBox(
        "RQ10",
        "How does news volume distribute across sectors and years?",
        "The cube holds sums and counts, never averages, so any ratio you see is derived at query " +
        "time and adds up correctly across levels. Switch the operation to see how slicing, dice, " +
        "roll-up and drill-down each answer the same question differently. " +
        "**Decision it supports:** which sectors are worth looking at closely, and where the gaps are."
      )}
      ${controls(
        field("Measure", "cube-measure", {
          value: "news_count",
          options: ["news_count", "news_count_non_market", "sentiment",
                    "high_sensational_count", "mean_return", "mean_abs_return"],
          labels: {
            news_count: "Headline-days",
            news_count_non_market: "Headline-days (excl. market reports)",
            sentiment: "Mean sentiment",
            high_sensational_count: "High-sensational headline-days",
            mean_return: "Mean return %",
            mean_abs_return: "Mean absolute return %",
          },
        }), "cube-controls"
      )}
      <div id="cube-out"></div>`;

    setTimeout(async () => {
      const out = $("#cube-out");
      const run = async () => {
        const measure = $("#cube-measure").value;
        into(out, `<div class="loading">Reading the cube…</div>`);
        try {
          const [specs, sectors, slice, rollup] = await Promise.all([
            loadSpecs({ theme: state.theme, measure }),
            api("/sectors"),
            api("/olap/sector/slice", { measure }),
            api("/olap/sector/rollup", { measure }),
          ]);
          const charts = publishSpecs(specs);
          const drilldown = await api("/olap/sector/drilldown", { measure }).catch(() => null);
          into(out, `
            ${chart("v2_sector_heatmap", `Every sector, every year — ${measure.replace(/_/g, " ")}`,
                    "Darker is more. Cells are means derived from stored sums and counts.",
                    "Survivorship: today's Nifty 50 list applied to a 2015–2020 window. " +
                    "Companies that were constituents then and have since been removed are absent.")}
            ${howComputed("OLAP cube", "`cube_sector_month` stores sums and counts at " +
              "(sector, year, month). Every average on this page is divided at query time, never stored.")}
            ${sectorStrip(sectors)}
            ${sectionHead("Slice — one sector across years", slice.description || "")}
            ${tableFromOlap(slice)}
            ${sectionHead("Roll-up — the same measure totalled across the grain", rollup.description || "")}
            ${tableFromOlap(rollup)}
            ${drilldown && drilldown.operation
              ? sectionHead(
                  "Drill-down — into one sector-period",
                  drilldown.rows.length
                    ? "The " + drilldown.rows.length + " most recent "
                      + (drilldown.description || "facts") + "."
                    : "")
              + tableFromOlap(drilldown)
              : ""}
            ${caution("A headline-day counts a sector once even when several of its companies are named. " +
              "Sector membership is today's list, not each month's.")}`);
          mountSpecs(charts);
        } catch (error) {
          into(out, emptyState(`The cube could not be read: ${error.message}`, "python -m dwm link"));
        }
      };
      $("#cube-controls").addEventListener("click", (e) => {
        if (e.target.dataset.v2Refresh) run();
      });
      await run();
    }, 0);
    return host;
  };

  function sectorStrip(payload) {
    const rows = payload.sectors || [];
    if (!rows.length) return "";
    return `<div class="pillrow pillrow--wide">${rows.map((s) => `
      <span class="pill pill--lg">
        <strong>${esc(s.sector_name)}</strong>
        <span class="dim">${s.with_prices || 0} with prices · ${s.constituents || 0} listed</span>
      </span>`).join("")}</div>`;
  }

  /* ── 5. Event Study ────────────────────────────────────────────────────── */

  V2.pageEvents = function () {
    const host = `
      ${questionBox(
        "RQ11",
        "Did any Nifty 50 stock move abnormally around a known event?",
        "Pick an event. You get the cumulative abnormal return by sector and by stock, " +
        "**and the stocks that were excluded** for lacking enough pre-event history — because a " +
        "sector average that quietly dropped its weakest rows would look stronger than its evidence. " +
        "**Decision it supports:** whether a headline date is worth studying at all, or whether the " +
        "market simply moved."
      )}
      ${controls(field("Event", "ev-key", { value: "", attrs: 'placeholder="loading…"' }), "ev-controls")}
      <div id="ev-out"></div>`;

    setTimeout(async () => {
      const out = $("#ev-out");
      const select = $("#ev-key");
      let events = [];
      try {
        events = (await api("/events")).events || [];
      } catch (error) {
        into(out, emptyState(`The event list could not load: ${error.message}`, "python -m dwm build"));
        return;
      }
      if (!events.length) {
        into(out, emptyState("No events are configured.", "python -m dwm build"));
        return;
      }
      select.outerHTML = `<select class="field__input" id="ev-key">
        ${events.map((e) => `<option value="${e.event_key}">${esc(e.event_label)} — ${esc(String(e.start_date))}</option>`).join("")}
      </select>`;

      const run = async () => {
        const key = Number($("#ev-key").value);
        into(out, `<div class="loading">Fitting the market model for event ${key}…</div>`);
        try {
          const [specs, study] = await Promise.all([
            loadSpecs({ theme: state.theme, event_key: key }),
            api(`/events/${key}/study`),
          ]);
          const charts = publishSpecs(specs);
          const excluded = study.excluded || [];
          into(out, `
            ${chart("v2_event_car", "Cumulative abnormal return by sector",
                    "Green above zero, red below. Each bar averages the stocks in that sector.",
                    "Abnormal means beyond what the market model explains — not caused by the event.")}
            ${howComputed("Event study, market model",
              "`return − (α + β·market return)`, summed from the event session to five sessions " +
              "after it, using a window of pre-event history to fit α and β. Fitted with " +
              "`numpy.polyfit`; no look-ahead past the event.")}
            ${caution(study.caution)}
            ${excluded.length
              ? `<div class="callout callout--caution">
                   <span class="callout__label">${excluded.length} stock(s) excluded</span>
                   ${md("Kept out of every average below, and listed so you can see the sample you are " +
                        "looking at:\n\n" + excluded.slice(0, 12).map((e) =>
                        `- **${e.symbol}** — ${e.reason} (${e.est_days} sessions of history)`).join("\n") +
                        (excluded.length > 12 ? `\n- …and ${excluded.length - 12} more` : ""))}
                 </div>`
              : ""}
            ${sectionHead("By sector", "")}
            ${table(olapColumns(study.by_sector, null), study.by_sector, { empty: "No valid results." })}
            ${sectionHead("The twenty largest absolute moves", "")}
            ${table(olapColumns(study.by_stock, null), study.by_stock, {
              empty: "No stock cleared the pre-event history requirement.",
              caption: "Signed CAR[0,+5] in per cent.",
            })}`);
          mountSpecs(charts);
        } catch (error) {
          into(out, emptyState(`Event ${key} has no usable result: ${error.message}`,
                                "python -m dwm mine --op events"));
        }
      };
      $("#ev-controls").addEventListener("click", (e) => {
        if (e.target.dataset.v2Refresh) run();
      });
      select.addEventListener("change", run);
      await run();
    }, 0);
    return host;
  };

  /* ── 6. Mining Lab ─────────────────────────────────────────────────────── */

  V2.pageMining = function () {
    const host = `
      ${questionBox(
        "RQ12",
        "What patterns survive when we look across all five years at once?",
        "Three analyses, and each is reported with the thing that would undermine it: the cluster " +
        "silhouette beside the clusters, the lift beside the support count, and both baselines beside " +
        "the direction model's accuracy. **Decision it supports:** whether any of this is strong " +
        "enough to build on, or whether the honest answer is that it is not."
      )}
      ${tabs(["Stock clusters", "Co-occurrence rules", "Direction model", "Surprise days"], "Stock clusters")}
      <div id="mining-out"></div>`;

    setTimeout(() => {
      const out = $("#mining-out");
      const tab = state.route.tab || "Stock clusters";

      const loaders = {
        "Stock clusters": async () => {
          const [specs, d] = await Promise.all([
            loadSpecs({ theme: state.theme }), api("/mining/stockclusters"),
          ]);
          const charts = publishSpecs(specs);
          const sizes = Object.entries(d.sizes || {});
          into(out, `
            ${chart("v2_cluster_scatter", "Where each stock sits after two principal components",
                    "Colour is the cluster the k-means fit assigned. Axes are components, so their " +
                    "scales are arbitrary and only the shape carries meaning.",
                    "The silhouette score for this fit is reported beside the caption below, not hidden.")}
            ${howComputed("Clustering, PCA",
              "Annualised volatility, mean daily return, beta, max drawdown and mention density, " +
              "standardised, reduced to two components, then k-means. Deterministic with `SEED = 42`.")}
            ${caution(d.caution)}
            ${sizes.length ? `<div class="kpigrid">${sizes.map(([id, n]) => `
                <div class="kpi"><div class="kpi__label">Cluster ${esc(id)}</div>
                  <div class="kpi__value">${n}</div>
                  <div class="kpi__unit">stocks</div></div>`).join("")}</div>` : ""}
            ${table(olapColumns(d.clusters, null), d.clusters, {
              caption: "All stocks, all features. Sorted by cluster.",
              empty: "The clustering step has not produced results.",
            })}`);
          mountSpecs(charts);
        },
        "Co-occurrence rules": async () => {
          const d = await api("/mining/marketrules", { limit: 50 });
          into(out, `
            ${howComputed("Association rules",
              "Apriori over sentiment bands and daily direction across trading sessions, " +
              "then lift as the ranking measure.")}
            ${caution(d.caution)}
            ${table([
              { key: "rule_id", label: "Rule" },
              { key: "antecedents", label: "When" },
              { key: "consequents", label: "Then" },
              { key: "support", label: "Support", num: true, html: (r) => pct(r.support) },
              { key: "confidence", label: "Confidence", num: true, html: (r) => pct(r.confidence) },
              { key: "lift", label: "Lift", num: true },
              { key: "support_count", label: "Sessions", num: true },
            ], d.rules, {
              empty: "No rules met the thresholds.",
              caption: "Support is the share of ALL sessions in which the whole rule fired. " +
                       "Lift above 1 means the consequent co-occurred more often than it does on its own.",
            })}`);
        },
        "Direction model": async () => {
          const [specs, d] = await Promise.all([
            loadSpecs({ theme: state.theme }), api("/mining/direction"),
          ]);
          const charts = publishSpecs(specs);
          const beats = d.beats_baseline;
          into(out, `
            ${answerBlock({
              rq: "RQ12",
              question: "Can news features tell you which way the market goes tomorrow?",
              headline: beats
                ? "Marginally, and not enough to act on."
                : "No. These features carry no usable skill.",
              kind: beats ? "qualified" : "null",
              evidence: "Chronological split — the last 15% of the window is the test set, " +
                        "never shuffled, tuned on the validation slice only.",
              caution: d.caution,
            })}
            ${chart("v2_direction_metrics", "Test accuracy against both baselines",
                    "Accent bars: the models. Grey bars: the two baselines. Nothing is at or above " +
                    "the majority baseline on its own.",
                    "If every accent bar sits below the grey, the answer is no.")}
            ${howComputed("Classification, chronological hold-out",
              "Features for day *t* use only data known by the end of day *t*; the target is day " +
              "*t+1*. Train/validation/test split by date, never shuffled.")}
            ${caution(d.caution)}
            ${table(olapColumns(d.models, null), d.models, {
              empty: "The direction model has not been run.",
            })}`);
          mountSpecs(charts);
        },
        "Surprise days": async () => {
          const [driven, quiet] = await Promise.all([
            api("/mining/surprise", { kind: "news_driven", limit: 40 }),
            api("/mining/surprise", { kind: "quiet_news_move", limit: 40 }),
          ]);
          const note = quiet.days && quiet.days.length && driven.days && driven.days.length
            ? Math.round(100 * quiet.days.length / (quiet.days.length + driven.days.length))
            : null;
          into(out, `
            ${howComputed("z-score surprise detection",
              "News heat and absolute return each scored against that sector's own preceding 60 " +
              "sessions, then flagged at |z| ≥ 2 on both axes.")}
            ${caution(driven.caution)}
            ${note != null
              ? kpiRow([["Days where BOTH news and price spiked", driven.days.length],
                        ["Days where the price moved and we had little to say", quiet.days.length],
                        ["Share that are a coverage gap", `${note}%`]])
              : ""}
            ${note != null && note > 50
              ? caution(`Most surprise days in this corpus are <strong>quiet_news_move</strong>. ` +
                        "That is a statement about OUR coverage — one outlet — not about the market. " +
                        "It is also the strongest argument against reading any market conclusion from " +
                        "this corpus alone.")
              : ""}
            ${sectionHead("News and price both spiked", "Both axes past |z| ≥ 2 for that sector.")}
            ${table(olapColumns(driven.days, null), driven.days, { empty: "None met both thresholds." })}
            ${sectionHead("The price moved, we had little to say", "A fact about this corpus's coverage.")}
            ${table(olapColumns(quiet.days, null), quiet.days, { empty: "None met the threshold." })}`);
        },
      };

      (loaders[tab] || loaders["Stock clusters"])()
        .catch((error) => into(out, emptyState(`Could not load ${tab}: ${error.message}`,
                                                "python -m dwm mine all")));
    }, 0);
    return host;
  };

  /* ── 7. Headline Checker ───────────────────────────────────────────────── */

  V2.pageChecker = function () {
    const host = `
      ${questionBox(
        "RQ13",
        "How does this headline's writing compare with the rest of our corpus?",
        "Paste any headline. You get a style score, the percentile of that score against all " +
        "3.1 million headlines we hold, the stocks and sectors it names, and the closest headlines " +
        "already in the corpus. **Decision it supports:** whether a headline is unusually written " +
        "for this corpus — which is a question about writing, never about truth."
      )}
      <form class="checker" id="check-form">
        <label class="field field--wide">
          <span class="field__label">Paste a headline</span>
          <textarea class="field__input" id="check-text" rows="2"
            placeholder="SHOCKING! Reliance shares CRASH 5000% in HUGE scam!!"></textarea>
        </label>
        <div class="checker__row">
          <button class="btn btn--primary" type="submit">Check it</button>
          <span class="dim checker__note">Style only. Nothing here is a verdict on the claim.</span>
        </div>
      </form>
      <div id="check-out">
        ${emptyState("Paste a headline above to see how its writing compares with ours.", null)}
      </div>`;

    setTimeout(() => {
      const form = $("#check-form");
      if (!form) return;
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const out = $("#check-out");
        const headline = $("#check-text").value.trim();
        if (!headline) {
          into(out, `<div class="empty">Nothing to check — the box is empty.</div>`);
          return;
        }
        into(out, `<div class="loading">Scoring the writing…</div>`);
        try {
          const payload = await postJson("/check", { headline });
          // The distribution chart needs the score we just computed, so the spec
          // is fetched with it. Fetched rather than patched in the browser: the
          // band the headline falls in is decided here, and a browser-side
          // calculation would be a number no test could check.
          const charts = publishSpecs(await loadSpecs({
            theme: state.theme,
            headline_score: payload.style.sensational_score,
          }).catch(() => ({ charts: null })));
          into(out, renderCheck(payload, charts));
          mountSpecs(charts);
        } catch (error) {
          into(out, emptyState(`The check failed: ${error.message}`, null));
        }
      });
    }, 0);
    return host;
  };

  function renderCheck(payload, charts) {
    const s = payload.style || {};
    const p = payload.percentile || {};
    const tags = payload.tags || {};
    const named = (tags.stocks || []).map((t) => `
      <span class="pill">${esc(t.symbol)}
        ${t.needs_context ? '<span class="tag tag--caution">needed context</span>' : ""}
      </span>`).join("");

    return `
      <div class="checkresult">
        ${payload.truncated
          ? `<div class="callout callout--caution"><span class="callout__label">Truncated</span>
              ${md("That was a long paste, so only the first 2,000 characters were scored.")}</div>`
          : ""}
        ${payload.is_verbatim_in_corpus
          ? `<div class="callout callout--evidence"><span class="callout__label">Already in our corpus</span>
              ${md(`This exact headline is one of ours, filed under **${payload.topic}**. ` +
                   "You are seeing how our own pipeline scored a headline it already holds.")}</div>`
          : ""}
        <h3 class="subhead">The headline we were given</h3>
        <blockquote class="headline">${esc(payload.headline)}</blockquote>

        ${kpiRow([
          ["Style score", s.sensational_score],
          ["Against the corpus", p.in_words || "n/a"],
          ["Percentile", p.percentile == null ? "n/a" : `${p.percentile}th`],
          ["Sentiment", s.sentiment_compound],
        ])}
        ${charts
          ? chart("v2_checker_percentile", "Where that score sits in our corpus",
                  "Every band of the corpus's own distribution, with the band this headline " +
                  "falls into marked at the end. The marker has no height because a bar height " +
                  "there would have to be invented.",
                  `${num(p.n_corpus)} headlines in the corpus. A high score means the WRITING is ` +
                  "more intense than most of ours — not that the claim is false.")
            + caution(charts.v2_checker_percentile &&
                      charts.v2_checker_percentile.dwm_caution)
          : ""}
        ${caution(payload.disclaimer)}
        ${caution(payload.caution)}

        <h3 class="subhead">What it names</h3>
        ${named || (tags.sectors || []).length
          ? `<div class="pillrow">${named}${(tags.sectors || []).map((x) =>
              `<span class="pill pill--lg">${esc(x)}</span>`).join("")}</div>`
          : `<div class="empty">No company or sector we recognise appears in it.</div>`}
        ${(tags.stocks || []).length
          ? caution("A name appearing in a headline is not the same as the story being about that " +
                    "company. Mention precision has not been humanly audited in this build — " +
                    "`reports/mention_audit_sample.csv` is waiting on a person to fill it in.")
          : ""}

        <h3 class="subhead">How its writing breaks down</h3>
        ${table([
          { key: "word_count", label: "Tokens" },
          { key: "caps_ratio", label: "ALL-CAPS ratio" },
          { key: "superlative_count", label: "Superlatives" },
          { key: "urgency_count", label: "Urgency words" },
          { key: "exclamation_count", label: "Exclamations" },
          { key: "risk_signal", label: "Flagged a risk signal",
            html: (r) => (r.risk_signal ? '<span class="pill-yes">yes</span>' : '<span class="pill-no">no</span>') },
        ], [s], { empty: "No measures." })}

        <h3 class="subhead">Closest headlines already in our corpus</h3>
        ${(payload.neighbours || []).length
          ? table([
              { key: "date", label: "Date" },
              { key: "text", label: "Headline" },
              { key: "similarity", label: "Cosine", num: true },
            ], payload.neighbours, {
              caption: "Textually similar, not related stories. Similarity is TF-IDF cosine over " +
                       "a deterministic hash sample of the corpus.",
            })
          : `<div class="empty">Nothing in our sample of the corpus scores above zero against it. ` +
            `Either it uses vocabulary our corpus does not, or the neighbour model has not been built ` +
            `(<code>python -m dwm build</code>).</div>`}
      </div>`;
  }

  /* ── 8. Data & Schema ──────────────────────────────────────────────────── */

  V2.pageQuality = function () {
    const host = `
      ${questionBox(
        "RQ14",
        "What went into this warehouse, and what did not?",
        "The ETL funnel, the coverage of the stock-linking step, and the rows excluded as outliers. " +
        "This page exists because the most important thing about a warehouse is what it is missing. " +
        "**Decision it supports:** whether a figure on any other page is strong enough to quote."
      )}
      ${tabs(["Coverage", "Schema", "Provenance"], "Coverage")}
      <div id="q-out"></div>`;

    setTimeout(() => {
      const out = $("#q-out");
      const tab = state.route.tab || "Coverage";

      const loaders = {
        Coverage: async () => {
          const d = await api("/quality");
          const linking = d.linking || {};
          into(out, `
            ${kpiRow([
              ["Outlier rows kept but excluded from statistics", d.outlier_rows == null ? "n/a" : d.outlier_rows],
              ["Headlines linked to a stock", linking.stock_coverage_pct == null ? "n/a" : `${linking.stock_coverage_pct}%`],
              ["Headlines linked to a sector", linking.sector_coverage_pct == null ? "n/a" : `${linking.sector_coverage_pct}%`],
              ["Mention precision", linking.precision == null ? "not yet audited" : pct(linking.precision)],
            ])}
            ${caution(d.caution)}
            ${linking.precision == null
              ? `<div class="callout callout--caution">
                   <span class="callout__label">An honesty gap, stated plainly</span>
                   ${md("`reports/mention_audit_sample.csv` holds 200 stratified matches with an " +
                        "**empty `is_correct` column**. Until a person fills that in, the project does " +
                        "not know how often name matching gets the wrong company, and refuses to " +
                        "publish a precision figure. `python -m dwm link --precision` will compute it " +
                        "once at least 30 rows are labelled — and it will refuse below that.")}
                 </div>`
              : ""}
            ${sectionHead("The ETL funnel", "Most recent rows from etl_audit, newest first.")}
            ${table([
              { key: "run_id", label: "Run" },
              { key: "dataset_code", label: "Dataset" },
              { key: "step", label: "Step" },
              { key: "status", label: "Status" },
              { key: "rows_read", label: "Read", num: true },
              { key: "rows_loaded", label: "Loaded", num: true },
              { key: "rows_rejected", label: "Rejected", num: true },
            ], d.funnel, { empty: "No audit rows. Run `python -m dwm all`." })}`);
        },
        Schema: async () => {
          const [tables, stocks] = await Promise.all([api("/tables"), api("/stocks")]);
          into(out, `
            ${sectionHead("Every table, largest first", "Row counts read from the warehouse.")}
            ${table(olapColumns(tables.tables || [], null), tables.tables || [],
                    { empty: "The warehouse could not be read." })}
            ${sectionHead("Stock coverage", "Which constituents we can and cannot analyse.")}
            ${table([
              { key: "symbol", label: "Symbol" },
              { key: "company_name", label: "Company" },
              { key: "sector", label: "Sector" },
              { key: "price_source", label: "Price source",
                html: (r) => (r.price_source
                  ? esc(r.price_source)
                  : '<span class="pill-no">no prices in window</span>') },
              { key: "is_survivor", label: "Survivor",
                html: (r) => (r.is_survivor ? '<span class="pill-yes">yes</span>' : '<span class="pill-no">no</span>') },
              { key: "first_trade_date", label: "First session" },
              { key: "last_trade_date", label: "Last session" },
            ], stocks.stocks || [], { empty: "No stocks loaded." })}
            ${caution("Three constituents — ETERNAL, JIOFIN, MAXHEALTH — list after the window " +
                      "opened, so there are no bars for it. They still have a `dim_stock` row, with " +
                      "`price_source` NULL, rather than being silently dropped.")}`);
        },
        Provenance: async () => {
          // `/ui/manifest`, not `/manifest`: the manifest is what renders the
          // shell, so it lives with the other ui endpoints.
          const d = await api("/ui/manifest");
          const prov = d.provenance || d.source || {};
          into(out, `
            ${sectionHead("Where the data came from", "Recorded at fetch time, not reconstructed later.")}
            ${table([
              { key: "name", label: "Dataset" },
              { key: "source_url", label: "Source URL" },
              { key: "fetched_at", label: "Fetched" },
              { key: "row_count", label: "Rows", num: true },
              { key: "sha256", label: "SHA-256", dim: true,
                html: (r) => r.sha256 ? `<code class="hash">${esc(String(r.sha256).slice(0, 16))}…</code>` : "n/a" },
            ], prov.datasets || Object.entries(prov).map(([k, v]) => ({ name: k, ...v })),
               { empty: "No provenance recorded." })}
            ${howComputed("Provenance",
              "Every fetch writes a `.source.json` sidecar beside the cached file under " +
              "`data/raw/`, carrying the URL, the fetch time, the row count and a SHA-256 of the " +
              "bytes. The pipeline can therefore rerun offline and say what it is reading.")}
            ${caution("Prices come from the Yahoo Finance chart API with explicit epoch bounds. " +
                      "Its `range=max` and `range=10y` parameters silently return MONTHLY bars, which " +
                      "look like data and are not — never use them here.")}`);
        },
      };

      (loaders[tab] || loaders.Coverage)()
        .catch((error) => into(out, emptyState(`Could not load ${tab}: ${error.message}`,
                                                "python -m dwm all")));
    }, 0);
    return host;
  };

    /** POST JSON. `api()` in app.js is GET-only by design - it builds a URL with
   *  query parameters - so the one POST endpoint gets its own helper rather than
   *  a second signature on the shared one. */
  async function postJson(path, body) {
    const response = await fetch(new URL(path, window.location.origin), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      let detail = response.statusText;
      try { detail = (await response.json()).detail || detail; } catch (e) { /* plain text */ }
      throw new Error(detail);
    }
    return response.json();
  }

  /* ── export ────────────────────────────────────────────────────────────── */

  V2.clearSpecCache = clearSpecCache;
  window.DWM_V2 = V2;
})();
