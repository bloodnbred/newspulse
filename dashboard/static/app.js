/* ==========================================================================
   Indian News Warehouse — dashboard front end

   **This computes nothing.** Every figure below was measured once by the mining
   stage, written to facts.json by the inference stage, and served unchanged by
   the API. The job here is to render it and to carry the cautions with it.

   That second part is the one worth defending. A bare "10.9%" on a screen,
   detached from "risk-signal rate on unlabelled headlines", is exactly how a
   risk-signal rate stops being called one. So `figure()` refuses to render a
   number that has a unit and no caution, and `auditPayload()` walks the whole
   API response at start-up looking for the same problem. If it finds one, the
   page says so rather than quietly showing the bare value.

   Charts arrive as Vega-Lite specs built in Python (dwm/ui/charts.py) and are
   only embedded here. That is presentation, not computation, and keeping the
   spec construction in Python means a malformed spec is a failing unit test
   rather than a blank rectangle in a presentation.
   ========================================================================== */

"use strict";

/* ── state ─────────────────────────────────────────────────────────────── */

const state = {
  theme: document.documentElement.dataset.theme || "light",
  manifest: null,
  summary: null,
  charts: {},
  route: { page: "overview", sub: null, tab: null },
  uncautoned: [],
};

const QUESTIONS = [
  { id: "volume",     rq: "RQ2", nav: "Volume and events",      title: "Volume and events" },
  { id: "mix",        rq: "RQ1", nav: "Topic mix",              title: "Topic mix" },
  { id: "language",   rq: "RQ3", nav: "Sensational language",   title: "Sensational language" },
  { id: "clusters",   rq: "RQ4", nav: "Topic clusters",         title: "Topic clusters" },
  { id: "rules",      rq: "RQ5", nav: "Co-occurrence rules",    title: "Co-occurrence rules" },
  { id: "market",     rq: "RQ6", nav: "Headlines and market",   title: "Headlines and the market" },
  { id: "classifier", rq: "RQ7", nav: "Real vs Fake",           title: "The real/fake classifier" },
];

/* Sections. The v1 five answer the seven research questions about the news
   corpus itself; the v2 four answer what that corpus says about companies,
   sectors and markets. They are grouped in the nav so a reader can tell which
   half of the study a page belongs to. */
const SECTIONS = [
  { id: "overview", glyph: "◧", label: "Overview", group: "The news" },
  { id: "findings", glyph: "◇", label: "Findings", group: "The news" },
  { id: "explore",  glyph: "⌗", label: "Explore",  group: "The news" },
  { id: "mood",     glyph: "↯", label: "Market mood", group: "News and markets" },
  { id: "stocks",   glyph: "⌸", label: "Stocks", group: "News and markets" },
  { id: "cube",     glyph: "▦", label: "Sector cube", group: "News and markets" },
  { id: "events",   glyph: "⊕", label: "Event study", group: "News and markets" },
  { id: "mining",   glyph: "⌬", label: "Mining lab", group: "News and markets" },
  { id: "checker",  glyph: "⌕", label: "Checker", group: "About the data" },
  { id: "quality",  glyph: "⚖", label: "Data & schema", group: "About the data" },
  { id: "how",      glyph: "⚙", label: "How it works", group: "About the data" },
  { id: "report",   glyph: "▤", label: "Full report", group: "About the data" },
];

const SECTIONS_OF_QUESTION = {
  volume: "rq2_bursts",
  mix: "rq1_topic_mix",
  language: "rq3_sensationalism",
  clusters: "rq4_clusters",
  rules: "rq5_association_rules",
  market: "rq6_market_association",
  classifier: "rq7_classifier",
};

/* Outcome kind → colour and label. A null result is not a failure, so it is
   grey rather than red; only a genuine negative earns red. */
const KIND = {
  null:      { label: "No result",  colour: "var(--muted)" },
  qualified: { label: "Qualified",  colour: "var(--caution)" },
  positive:  { label: "Finding",    colour: "var(--positive)" },
  split:     { label: "Split answer", colour: "var(--accent)" },
};

/* ── helpers ───────────────────────────────────────────────────────────── */

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** Minimal inline markup for trusted prose.
 *
 *  Handles `code`, **bold** and *italic*. The emphasis cases were added when the
 *  plain-language layer started marking words like "*unsettled*" and they
 *  rendered as literal asterisks, because `md` only knew about backticks. The
 *  order matters: code first, then bold, then italic, so `**x**` does not become
 *  a bolded pair of italic markers.
 *
 *  Only ever applied to strings this project wrote into facts.json, never to
 *  user input. `esc` runs first, so the result cannot introduce a tag. */
function md(text) {
  return esc(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

function num(value) {
  if (value === null || value === undefined) return "n/a";
  return Number(value).toLocaleString("en-US");
}

function pct(value, places = 2) {
  if (value === null || value === undefined) return "n/a";
  return (Number(value) * 100).toFixed(places) + "%";
}

function signed(value, places = 4) {
  if (value === null || value === undefined) return "n/a";
  const n = Number(value);
  return (n >= 0 ? "+" : "") + n.toFixed(places);
}

function shortDate(iso) {
  if (!iso) return "n/a";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleDateString("en-GB", {
    day: "2-digit", month: "short", year: "numeric",
  });
}

/* ── API ───────────────────────────────────────────────────────────────── */

async function api(path, params) {
  const url = new URL(path, window.location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== null && v !== undefined && v !== "") url.searchParams.set(k, v);
    }
  }
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail || detail; } catch (e) { /* plain text */ }
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function toast(message, ms = 2600) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { el.hidden = true; }, ms);
}

/* ── the honesty check ─────────────────────────────────────────────────── */

/** Walk a payload for any figure that has a unit but no caution.
 *
 *  Run once at start-up over the whole `/summary` response rather than trusting
 *  that each call site remembered. Returns dotted paths so a failure names the
 *  section rather than just counting. */
function auditPayload(node, path = "", found = []) {
  if (Array.isArray(node)) {
    node.forEach((item, i) => auditPayload(item, `${path}[${i}]`, found));
  } else if (node && typeof node === "object") {
    if (node.unit && !node.caution && "value" in node) found.push(path || "<root>");
    for (const [key, value] of Object.entries(node)) {
      auditPayload(value, path ? `${path}.${key}` : key, found);
    }
  }
  return found;
}

/* ── components ────────────────────────────────────────────────────────── */

/** A figure with its unit and its caution. The caution is not optional.
 *
 *  A figure that arrives without one renders a visible warning in its place.
 *  That is deliberately louder than just hiding the value: a page that quietly
 *  drops the number is harder to notice than one that complains. */
function figure(label, block, format = pct, tone = "") {
  if (!block) return "";
  const value = format(block.value);
  const unit = block.unit || "";
  const caution = block.caution || "";

  let cautionHtml;
  if (caution) {
    cautionHtml = callout("caution", "Before you quote this", caution);
  } else {
    cautionHtml =
      '<div class="callout callout--note"><span class="callout__label">Missing caution</span>' +
      `No caution is attached to <b>${esc(label)}</b>. The value is shown because hiding ` +
      "it would make a bug invisible, but this is a defect in the pipeline, not in this page." +
      "</div>";
    state.uncautoned.push(label);
  }

  return `
    <div class="kpi ${tone ? "kpi--" + tone : ""}">
      <div class="kpi__label">${esc(label)}</div>
      <div class="kpi__value">${esc(value)}</div>
      ${unit ? `<div class="kpi__unit">${md(unit)}</div>` : ""}
    </div>
    ${cautionHtml}`;
}

function callout(kind, label, body) {
  if (!body) return "";
  return `<div class="callout callout--${kind}">
            <span class="callout__label">${esc(label)}</span>
            ${md(body)}
          </div>`;
}

function kpi(label, value, unit, tone = "", termKey = "", scaleKey = "") {
  return `
    <div class="kpi ${tone ? "kpi--" + tone : ""}">
      <div class="kpi__label">${termKey ? term(termKey) : esc(label)}</div>
      <div class="kpi__value">${esc(value)}</div>
      ${unit ? `<div class="kpi__unit">${md(unit)}</div>` : ""}
      ${scaleKey ? scaleLine(scaleKey) : ""}
    </div>`;
}

/** The answer to a question, as the largest text on the page.
 *
 *  Order is the design: question, then the answer, then how we know, then the
 *  caveat. A panel that shows data and leaves the reader to infer the point has
 *  the emphasis exactly backwards. */
function answerBlock(outcome, opts = {}) {
  const kind = KIND[outcome.kind] || KIND.positive;
  return `
    <div class="answer">
      <div class="answer__q">
        <span class="answer__rq">${esc(outcome.rq || "")}</span>
        <span>${esc(outcome.question || "")}</span>
      </div>
      <div class="answer__text">
        ${md(outcome.headline || "")}
        <span class="tag" style="background:${kind.colour}">${esc(kind.label)}</span>
      </div>
      ${outcome.detail ? `<div class="answer__body">${md(outcome.detail)}</div>` : ""}
      ${outcome.evidence ? callout("evidence", "How we know", outcome.evidence) : ""}
      ${outcome.caution ? callout("caution", "Before you quote this", outcome.caution) : ""}
    </div>`;
}

/** Embed a chart by name. The spec comes from Python.
 *
 *  A spec that fails to embed shows an explicit error rather than a blank
 *  rectangle. A blank rectangle in a presentation reads as a design choice,
 *  which is the one thing it must never be. */
function chart(name, title, sub, caption) {
  const spec = state.charts[name];
  const id = `chart-${name}`;
  let capHtml = "";
  if (caption) {
    const badge = spec && spec.dwm_caption
      ? `<span class="chart__caption-badge">${esc(spec.dwm_caption)}</span>` : "";
    capHtml = `<div class="chart__cap">${badge}${md(caption)}</div>`;
  }
  const body = spec && Object.keys(spec).length
    ? `<div class="chart__body" id="${id}"></div>`
    : `<div class="chart__error">This chart has no data for the current results. ` +
      `It is reported as unavailable rather than drawn empty, so "not measured" ` +
      `is never mistaken for "measured as zero".</div>`;

  return `
    <figure class="chart">
      <figcaption class="chart__head">
        <div class="chart__title">${esc(title)}</div>
        ${sub ? `<div class="chart__sub">${md(sub)}</div>` : ""}
      </figcaption>
      ${body}
      ${capHtml}
    </figure>`;
}

/** Mount the pending specs once the DOM is in place. */
function mountCharts() {
  for (const [name, spec] of Object.entries(state.charts)) {
    const host = document.getElementById(`chart-${name}`);
    if (!host || !spec || !Object.keys(spec).length) continue;
    if (!window.vegaEmbed) {
      host.innerHTML = '<div class="chart__error">The chart library did not load, ' +
        "so charts are unavailable. Every number is still on this page as text.</div>";
      continue;
    }
    vegaEmbed(host, spec, {
      actions: false,
      renderer: "svg",
      tooltip: { theme: state.theme === "dark" ? "dark" : "light" },
      config: { background: "transparent" },
    }).catch((error) => {
      host.innerHTML =
        `<div class="chart__error">This chart failed to render (${esc(error.message)}).</div>`;
    });
  }
}

/** Render into a container, replacing its *contents*.
 *
 *  The bug this exists to prevent: the results panels used to do
 *  `box.outerHTML = ...`, which deletes the element it is called on. The first
 *  click worked and every later one threw, because `$("#ex-results")` came back
 *  null — so every "Load" button in Explore worked exactly once and then went
 *  dead, which is what "unresponsive" looked like.
 *
 *  `innerHTML` replaces the children and leaves the container in place. */
function into(box, html) {
  if (!box) {
    toast("That panel is no longer on the page — reload and try again.");
    return;
  }
  box.innerHTML = html;
}

function table(columns, rows, opts = {}) {
  if (!rows || !rows.length) {
    return `<div class="empty">${esc(opts.empty || "Nothing to show.")}</div>`;
  }
  const head = columns.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("");
  const body = rows.map((row) => {
    const cells = columns.map((c) => {
      const raw = typeof c.get === "function" ? c.get(row) : row[c.key];
      const html = c.html ? c.html(row) : esc(raw);
      return `<td class="${c.num ? "num" : ""} ${c.dim ? "dim" : ""}">${html}</td>`;
    }).join("");
    return `<tr>${cells}</tr>`;
  }).join("");
  const caption = opts.caption
    ? `<div class="chart__cap">${md(opts.caption)}</div>` : "";
  return `<div class="tablewrap">${caption}<div class="tablescroll">
            <table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>
          </div></div>`;
}

/** Column definitions inferred from an OLAP result's own `columns` list.
 *
 *  The API already returns properly-named columns — `year_month`,
 *  `topic_name`, `headline_count`, `sensational_rate`, `sentiment_band`. The
 *  previous renderer threw them away and printed `JSON.stringify(row)` into a
 *  single column, which is why every operation arrived as a wall of JSON.
 *
 *  Formatting is chosen by column name, not by column position: a cube has
 *  headline_count, sensational_count, two sums and three surrogate group ids in
 *  one row, and a rule keyed on position gets it wrong the moment an operation
 *  gains a column. */
function olapColumns(rows, columns) {
  const raw = (columns && columns.length ? columns : Object.keys(rows[0] || {}));
  const hidden = new Set(["g_year", "g_topic", "g_band", "topic_key", "date_key"]);
  // Calendar fields are numbers but are not quantities. `year_no` formatted as
  // "2,015" is worse than unformatted, and `month_no` as "12" is right by luck.
  const dateish = new Set([
    "year_month", "full_date", "date", "year_no", "year", "month_no", "month",
  ]);
  const rateish = /(rate|share|pct|percent)/i;
  const moneyish = /^(sum_|mean_|measure_)/;

  return raw
    .filter((c) => !hidden.has(c))
    .map((key) => {
      const sample = rows.find((r) => r[key] !== null && r[key] !== undefined);
      const isNumber = typeof (sample ? sample[key] : null) === "number";
      // Named `numeric`, not `num`. A local `const num` shadows the global
      // formatter of the same name, and the closures below then call a boolean:
      // "num is not a function", for every integer column. The *property*
      // returned still has to be called `num`, because `table()` reads it as
      // the right-align flag.
      const numeric = isNumber && !dateish.has(key);
      let html = null;
      if (numeric && rateish.test(key)) {
        html = (r) => (r[key] == null ? "n/a" : pct(r[key], key.includes("share") ? 2 : 2));
      } else if (numeric && moneyish.test(key)) {
        // Not `num(r[key], 4)`: `num` takes one argument, so the precision was
        // silently dropped and a sentiment sum of -1234.5678 rendered as
        // -1,235. Formatting it here keeps the decimals a sum actually has.
        html = (r) => (r[key] == null ? "n/a" : Number(r[key]).toLocaleString("en-US", {
          minimumFractionDigits: 4, maximumFractionDigits: 4,
        }));
      } else if (numeric) {
        html = (r) => num(r[key]);
      }
      if (key === "is_trading_day") {
        html = (r) => (r[key] ? '<span class="pill-yes">trading</span>'
          : '<span class="pill-no">closed</span>');
      }
      if (key === "sentiment_band") {
        html = (r) => (r[key] == null ? '<span class="pill-no">no band</span>' : esc(r[key]));
      }
      return {
        key,
        label: key.replace(/_/g, " "),
        num: numeric,
        html,
        dim: !numeric && key !== "topic_name" && !dateish.has(key),
      };
    });
}

function tabs(names, active) {
  return `<div class="tabs" role="tablist">${names.map((name) =>
    `<button class="tab ${name === active ? "is-active" : ""}" role="tab"
             aria-selected="${name === active}" data-tab="${esc(name)}">${esc(name)}</button>`
  ).join("")}</div>`;
}

function sectionHead(title, sub) {
  return `<div class="sectionhead"><div><h2>${esc(title)}</h2>${sub ? `<p>${md(sub)}</p>` : ""}</div></div>`;
}

/* ── pages ─────────────────────────────────────────────────────────────── */

/* ── the plain layer ─────────────────────────────────────────────────── */

/** Glossary lookup, built once. `plain.glossary` is static text, so this is a
 *  dictionary rather than a search. */
let GLOSSARY = {};

function gloss(term) {
  return GLOSSARY[String(term).toLowerCase()] || null;
}

/** A glossary term, marked. The definition travels in the markup rather than in
 *  a tooltip, so it works without hovering and cannot be clipped off-screen. */
function term(name) {
  const definition = gloss(name);
  if (!definition) return esc(name);
  return `<abbr class="term" title="${esc(definition)}" tabindex="0">${esc(name)}</abbr>`;
}

function renderIntro() {
  const plain = state.summary.plain || {};
  const intro = plain.intro;
  if (!intro) return "";
  return `
    <section class="intro" aria-labelledby="intro-h">
      <h2 id="intro-h" class="intro__heading">${esc(intro.heading || "What this is")}</h2>
      ${(intro.paragraphs || []).map((p) => `<p class="intro__p">${md(p)}</p>`).join("")}
    </section>`;
}

/** The thirty-second summary: a claim, the number, and what it does not license.
 *
 *  `so_what` is the part that makes this worth reading. Every finding on this
 *  project has one, and they are the sentences that turn a measurement into a
 *  conclusion a reader can actually act on. */
function renderSummary() {
  const plain = state.summary.plain || {};
  const summary = plain.summary;
  if (!summary || !summary.items) return "";
  const cards = summary.items.map((item, index) => `
    <article class="finding" id="finding-${esc(item.id)}">
      <div class="finding__head">
        <span class="finding__n">${index + 1}</span>
        <span class="finding__rq">${esc(item.rq || "")}</span>
      </div>
      <h3 class="finding__claim">${md(item.claim)}</h3>
      <p class="finding__body">${md(item.body)}</p>
      ${item.so_what ? `<div class="finding__sowhat">
        <span class="finding__sowhatlabel">So what</span>
        ${md(item.so_what)}
      </div>` : ""}
      ${item.caution ? callout("caution", "Worth knowing", item.caution) : ""}
    </article>`).join("");

  return `
    <section class="summary" aria-labelledby="summary-h">
      <div class="sectionhead">
        <div>
          <h2 id="summary-h">${esc(summary.heading || "If you read nothing else")}</h2>
          <p>${md(summary.subtitle || "")}</p>
        </div>
      </div>
      <div class="summary__grid">${cards}</div>
      ${plain.why ? `<p class="summary__why">${md(plain.why)}</p>` : ""}
    </section>`;
}

/** A number with its scale attached. A figure with no range is decoration. */
function scaleLine(key) {
  const plain = state.summary.plain || {};
  const scales = plain.scales || {};
  const text = scales[key];
  if (!text) return "";
  return `<div class="scale">${esc(text)}</div>`;
}

function renderGlossary() {
  const plain = state.summary.plain || {};
  const glossary = plain.glossary;
  if (!glossary || !glossary.terms) return "";
  const items = glossary.terms.map((entry) => `
    <div class="gloss">
      <div class="gloss__term">${esc(entry.term)}</div>
      <div class="gloss__plain">${esc(entry.plain)}</div>
    </div>`).join("");
  return `
    <section class="glossary" aria-labelledby="glossary-h">
      <div class="sectionhead"><div>
        <h2 id="glossary-h">${esc(glossary.heading || "Words this project uses")}</h2>
        <p>${md(glossary.subtitle || "")}</p>
      </div></div>
      <div class="glossary__grid">${items}</div>
    </section>`;
}

function pageOverview() {
  const outcomes = (state.summary.outcomes || {});
  const list = outcomes.outcomes || [];
  const counts = outcomes.counts || {};
  const corpus = state.manifest.corpus || {};
  const rq7 = state.summary.rq7_classifier || {};
  const rq2 = state.summary.rq2_bursts || {};
  const accuracy = ((rq7.data || {}).metrics || {}).accuracy;

  const kpis = [
    kpi("Headlines analysed", num(corpus.headlines_in_window), "in the 5-year window",
        "", "", "corpus_size"),
    kpi("Questions with a result",
        `${(counts.positive || 0) + (counts.qualified || 0)}`,
        `of ${counts.total || 7} asked — the rest found nothing, which is a finding`,
        "caution"),
    kpi("Months spiking on volume", num(rq2.value), "none. the series is flat", "muted"),
    kpi("Accuracy", pct(accuracy),
        `vs ${pct(((rq7.data || {}).metrics || {}).majority_baseline_accuracy)} for always guessing "real"`,
        "positive", "classifier_accuracy", "classifier_accuracy"),
  ].join("");

  const cards = list.map((o) => {
    const kind = KIND[o.kind] || KIND.positive;
    return `
      <article class="outcome">
        <div class="outcome__top">
          <span class="answer__rq">${esc(o.rq)}</span>
          <span class="tag" style="background:${kind.colour}">${esc(kind.label)}</span>
        </div>
        <div class="outcome__headline">${md(o.headline)}</div>
        ${o.detail ? `<div class="outcome__body">${md(o.detail)}</div>` : ""}
        ${o.evidence ? callout("evidence", "How we know", o.evidence) : ""}
        ${o.caution ? callout("caution", "Before you quote this", o.caution) : ""}
      </article>`;
  }).join("");

  return `
    ${renderIntro()}

    <div class="hero">What 1.1 million Indian news headlines actually show</div>
    <p class="lede">${md(outcomes.headline || "")}</p>
    <div class="kpis">${kpis}</div>

    ${renderSummary()}

    ${sectionHead("The technical view",
      "Everything above, in full. Same numbers, with the method, the uncertainty and " +
      "the caveat attached.")}

    ${chart("topic_share", "What the corpus is made of",
      "Share of the whole window by topic. “a where, not a what” marks the city desks — " +
      "`Local` alone is 70% of the corpus.",
      "Places and subjects are separated because comparing a city desk against a " +
      "subject as if they matched would be meaningless.")}
    ${chart("rq1_topic_mix", "The topic mix over time",
      "Share of each year's headlines, within-year, because 2015 is a partial year and " +
      "raw counts are not comparable across years.",
      "The 2017 spike in one raw category is a filing artefact of the publisher, not a " +
      "change in what India was reading.")}

    ${cards}`;
}

function pageFindings(sub) {
  const question = QUESTIONS.find((q) => q.id === sub) || QUESTIONS[0];
  const outcomes = state.summary.outcomes || {};
  const outcome = (outcomes.outcomes || []).find((o) => o.id === question.id);
  const block = state.summary[SECTIONS_OF_QUESTION[question.id]] || {};
  const data = block.data || {};

  if (!outcome) {
    return `<div class="empty">No outcome recorded for ${esc(question.rq)}.</div>`;
  }

  // The plain reading of this one question, taken from the thirty-second
  // summary. Same measurement, same caveat, no method vocabulary — so a reader
  // can get the conclusion before meeting the technique that produced it.
  const plainItem = (((state.summary.plain || {}).summary || {}).items || [])
    .find((i) => i.id === question.id);

  let body = "";
  switch (question.id) {
    case "volume":
      body = `
        ${chart("rq2_event_z",
          "What the event months actually show",
          "A desk that a news-reactive archive would cover <em>more</em> of during these " +
          "events. Bars below zero mean coverage fell. Months are in date order.",
          "The listed events are calendar coincidences offered for judgement, not causes. " +
          "The desks predicted to rise fall instead in most months.")}
        ${sectionHead("Why no month is flagged as a spike")}
        ${table(
          [{ label: "Year", key: "year" },
           { label: "Within-year coefficient of variation", key: "cv", num: true, html: (r) =>
             r.cv === null || r.cv === undefined
               ? '<span class="pill-no">excluded — partial year</span>'
               : esc(Number(r.cv).toFixed(4)) }],
          Object.entries(data.per_year_cv || {}).map(([year, cv]) => ({ year, cv }))
        )}
        <p>Within-year volume varies by about 2–4%, so no threshold can fire on a series ` +
          `this flat. Lowering one until something appeared would be choosing a threshold ` +
          `to manufacture a result.</p>
        ${callout("caution", "What this cannot distinguish",
          data.counter_signal_note ||
          "A genuinely quota-driven newsroom and a sampling artefact of the publisher's " +
          "export produce the same signature in this data.")}`;
      break;

    case "mix":
      body = `
        ${chart("rq1_topic_mix", "The topic mix over time",
          "Within-year share of subject topics.",
          "Share of the window and share of a single year are different numbers answering " +
          "different questions, and the warehouse stores them separately.")}
        ${sectionHead("The filing artefact",
          "One raw category holds an implausible share of a single year, which means the " +
          "publisher changed how it filed content.")}
        ${table(
          [{ label: "Raw category", key: "raw_category" },
           { label: "Year", key: "year", num: true },
           { label: "Headlines", key: "count", num: true, html: (r) => num(r.count) },
           { label: "Share of that year", key: "share_of_year", num: true,
             html: (r) => pct(r.share_of_year) }],
          data.taxonomy_artefacts || []
        )}
        <p><code>business.international-business</code> is <b>11.1% of all 2017 headlines</b> ` +
          `and <b>0.2% of 2018</b>. An analysis that ignored this would report a news-industry ` +
          `event as a change in what India was reading.</p>
        ${sectionHead("Largest topics of the whole window")}
        ${table(
          [{ label: "Topic", key: "topic" },
           { label: "Group", key: "topic_group" },
           { label: "Share of window", key: "share_of_window", num: true, html: (r) => pct(r.share_of_window) }],
          ((data.window_shares || {}).topics || []).slice(0, 12)
        )}`;
      break;

    case "language":
      body = `
        <div class="kpis">${figure("Corpus risk-signal rate", block)}</div>
        ${chart("rq3_topic_rates", "Risk-signal rate by topic",
          "The bar is the point estimate; the whisker is the 95% interval, and the interval " +
          "is the honest part. Counts run to the tens of thousands, so a bare percentage " +
          "would imply a precision the measure does not have.",
          "The dashed line is the corpus rate. This is a rate of a <em>style</em> measure on " +
          "unlabelled headlines — never a fake-news rate.")}
        <div class="grid2">
          <div>
            ${chart("rq3_trend", "Trend across the window",
              "Risk-signal rate by year.",
              "A flat line is the finding here, not a failure to find anything.")}
          </div>
          <div>
            ${sectionHead("Is the ranking an artefact of the cut-off?",
              "The threshold is a judgement, so the report ships a sensitivity table across " +
              "eight of them.")}
            ${table(
              [{ label: "Threshold", key: "threshold", num: true,
                 html: (r) => r.threshold === r.is_chosen_threshold
                   ? `<b>${Number(r.threshold).toFixed(4)}</b>` : Number(r.threshold).toFixed(4) },
               { label: "Top topic", key: "top1" },
               { label: "Corpus rate", key: "corpus_rate", num: true, html: (r) => pct(r.corpus_rate) }],
              ((data.sensitivity || {}).rows || [])
            )}
            <p class="small muted">The top informative topic is
              <b>${((data.sensitivity || {}).ranking_is_stable) ? "stable" : "NOT stable"}</b>
              across this range. <code>Unknown</code> tops the raw ranking at some thresholds
              because it is a filing gap, not a subject.</p>
          </div>
        </div>`;
      break;

    case "clusters":
      body = `
        <div class="kpis">${figure("Silhouette", block, (v) => v == null ? "n/a" : Number(v).toFixed(4), "positive")}</div>
        ${chart("rq4_silhouette", "Choosing k",
          "Silhouette against the number of clusters, with the chosen value ringed and the " +
          "conventional 0.25 threshold drawn.",
          "The score and the shape must be read together. Silhouette rewards one large " +
          "cluster sitting far from a few small tight ones — which is exactly what the " +
          "next chart shows.")}
        ${chart("rq4_projection", "The clustering, as a picture",
          "Every sampled headline projected onto the first two components of the space " +
          "K-Means actually clustered — the same space the silhouette was measured on.",
          "The dominant cluster is thinned so the small groups stay visible, which makes it " +
          "over-represented here relative to its share. The cluster-size chart carries the " +
          "true proportions. The three small clusters sit apart from the cloud: that is the " +
          "separation the silhouette is scoring.")}
        ${chart("rq4_cluster_sizes", "Where the headlines actually went",
          "Share of the sample per cluster. The point of this chart is the shape, not the ranking.",
          "The small clusters find writing patterns — money terms, traffic deaths, ages — " +
          "rather than topics in the publisher's own taxonomy.")}
        ${renderClusterTable(data)}
        ${chart("rq4_cluster_topics", "Which publisher topics dominate each cluster",
          "The evidence that these clusters are not the publisher's desks.",
          "Every cluster is dominated by `Local`, because 70% of the corpus is. The clusters " +
          "separate on vocabulary, not on filing.")}
        ${callout("note", "The concentration caveat", data.concentration_note || "")}
        <details class="card">
          <summary style="cursor:pointer;font-weight:600">The correction this section replaces</summary>
          <div style="margin-top:14px">
            <p>An earlier run measured <b>0.068</b> and concluded the headlines had no
            recoverable topic structure. That was wrong, and the fault was in the feature
            engineering, not in the data.</p>
            <ol class="small">
              <li>scikit-learn's default token pattern admits pure numerals, so clusters were
                described by <code>000</code>, <code>102</code>, <code>1000</code>.</li>
              <li>With numerals removed they were described by <code>for</code>,
                <code>with</code>, <code>from</code> — mean TF-IDF inside a cluster is highest
                for function words, because they appear in a modest share of documents but a
                large share of any one cluster.</li>
              <li>Descriptions read off the K-Means centroid weights returned
                <code>aadhaar, aadmi, aap, aarti</code> for <em>every</em> cluster, because
                SVD components carry an arbitrary sign and alphabetically early features win
                systematically.</li>
            </ol>
            <p class="small muted">None of that is visible in a silhouette computed on a
            different feature space, which is why the first number looked like a property of
            the corpus. “The data has no structure” and “my features had no signal” are
            easy to confuse.</p>
          </div>
        </details>`;
      break;

    case "rules":
      body = `
        <div class="kpis">
          ${kpi("Rules mined", num(data.rule_count), "on identical input for both algorithms")}
          ${kpi("FP-Growth speedup", (data.fpgrowth_speedup || 0) + "×", "same rules, less time", "positive")}
          ${kpi("Tautologies", num(data.tautological_rules), "counted separately", "muted")}
        </div>
        ${chart("rq5_timing", "Two algorithms, one answer",
          "Both return the same rule set, so the only variable being measured is time.",
          "Apriori and FP-Growth search the same itemsets. A difference would mean one is " +
          "broken, not interesting.")}
        ${callout("note", "Agreement is asserted, not discovered", data.redundancy_note || "")}
        ${chart("rq5_lift", "The shape of the whole rule set",
          "Every mined rule, binned by lift. A ranked table shows the best rules; a " +
          "histogram shows whether the top of that list is a real effect or the tail of a " +
          "distribution.",
          data.lift_distribution_note || "")}
        ${sectionHead("The strongest non-trivial rules, by lift")}
        ${table(
          [{ label: "If", get: (r) => (r.antecedent || []).join(" + "), html: (r) => md((r.antecedent || []).join(" + ")) },
           { label: "Then", get: (r) => (r.consequent || []).join(" + "), html: (r) => md((r.consequent || []).join(" + ")) },
           { label: "Support", key: "support", num: true, html: (r) => Number(r.support).toFixed(4) },
           { label: "Confidence", key: "confidence", num: true, html: (r) => Number(r.confidence).toFixed(4) },
           { label: "Lift", key: "lift", num: true, html: (r) => Number(r.lift).toFixed(4) }],
          (data.top_rules || []).slice(0, 20)
        )}
        ${callout("caution", "What a lift means", data.lift_caveat || "")}
        <details class="card">
          <summary style="cursor:pointer;font-weight:600">Why attributes, and not the keyword vocabulary</summary>
          <div style="margin-top:14px">
            <p>Attribute transactions carry <b>${esc(data.mean_items)}</b> items each. The
            300-term keyword vocabulary yields only <b>${esc(((data.keyword_run) || {}).mean_items)}</b>
            items per headline, because its most frequent terms are
            <code>govt</code>, <code>held</code>, <code>get</code>, <code>man</code>, <code>police</code>.</p>
            <p>The keyword run produced <b>${esc(((data.keyword_run) || {}).rule_count)}</b> rules.
            That is reported as a result rather than an omission: the vocabulary carries almost
            no co-occurrence structure to mine.</p>
          </div>
        </details>`;
      break;

    case "market":
      body = `
        <div class="grid3">
          ${kpi("Paired trading days", num(data.trading_days_paired),
                "business headlines against the index")}
          ${kpi("Strongest r, returns", signed(block.value),
                "chance, given the tests run", "muted")}
          ${kpi("r against volatility",
                signed((((data.volatility) || {}).strongest || {}).pearson_r),
                "the real signal, sign unexplained", "caution")}
        </div>
        ${callout("caution", "Both of these are associations",
          "Headline volume and volatility both respond to the same underlying events. " +
          "Nothing in this data identifies a direction of effect, and a negative " +
          "correlation between two things that react to a common cause is not a mechanism.")}
        ${chart("rq6_volatility", "Headline volume against 20-day volatility",
          "One point per trading day for the topic the correlation was computed on. " +
          "The fitted line is the reported correlation, drawn on the same points.",
          "Busier headline days go with calmer markets. The sign is the interesting part, " +
          "and this data cannot explain it.")}
        ${chart("rq6_returns", "Headline volume against daily returns",
          "The same volume series against the index's daily return — the null result, drawn.",
          "This is what “no relationship” looks like: a full, unstructured cloud. A " +
          "negative result with no chart is indistinguishable from a section that failed, " +
          "so it is plotted.")}
        ${sectionHead("Against daily returns there is nothing")}
        ${table(
          [{ label: "Measure", get: (r) => r.measure },
           { label: "Lag (trading days)", key: "lag_trading_days", num: true },
           { label: "n", key: "n", num: true, html: (r) => num(r.n) },
           { label: "Pearson r", key: "pearson_r", num: true, html: (r) => signed(r.pearson_r) },
           { label: "p", key: "p_value", num: true,
             html: (r) => (r.p_value == null ? "n/a" : Number(r.p_value).toFixed(4)) },
           { label: "Significant", key: "significant_at_alpha", html: (r) => r.significant_at_alpha
              ? '<span class="pill-yes">yes</span>' : '<span class="pill-no">no</span>' }],
          Object.entries(data.measures || {}).flatMap(([name, m]) =>
            (m.by_lag || []).map((e) => ({ ...e, measure: name.replace(/_/g, " ") })))
        )}
        ${callout("note", "Reading one significant result out of many",
          (() => {
            const a = data.significance_accounting || {};
            const sig = (a.significant_results || []).length;
            return `${esc(a.tests_run)} lag tests were run and ${esc(sig)} is significant. ` +
              `At alpha = ${esc(a.alpha)}, that many tests produce about ` +
              `${esc(a.expected_false_positives)} false positives by chance, so the single ` +
              `significant result is not treated as a finding.`;
          })())}`;
      break;

    case "classifier":
      body = `
        <div class="kpis">
          ${kpi("Accuracy", pct((data.metrics || {}).accuracy), "on the held-out test split", "positive")}
          ${kpi("Majority baseline", pct((data.metrics || {}).majority_baseline_accuracy), "always answer “real”", "muted")}
          ${kpi("Lift", (((data.metrics || {}).lift_over_baseline_pp || 0) > 0 ? "+" : "") +
                Number((data.metrics || {}).lift_over_baseline_pp || 0).toFixed(1) + " pp", "over the baseline")}
          ${kpi("Fake recall", pct((data.metrics || {}).fake_recall), "the minority class", "caution")}
        </div>
        <p class="small muted"><code>${esc(data.chosen_model)}</code> was chosen on
        <b>training</b> cross-validated accuracy and the test split was scored once.
        Selecting on test accuracy and then reporting that number is exactly the optimism this
        study set out to avoid.</p>
        ${chart("rq7_confusion", "Confusion matrix",
          "Counts, not proportions, because the cell sizes are the evidence that the minority " +
          "class was not simply absorbed into the majority one.",
          ((((data.metrics || {}).confusion_matrix) || {}).reading) || "")}
        <div class="grid2">
          <div>
            ${sectionHead("Every model, against the same baseline",
              `The majority baseline is ${pct((data.metrics || {}).majority_baseline_accuracy)}. ` +
              "Accuracy alone would flatter a model that learned nothing, because always " +
              "answering “real” already scores it.")}
            ${table(
              [{ label: "Model", key: "model" },
               { label: "Accuracy", key: "accuracy", num: true, html: (r) => pct(r.accuracy) },
               { label: "Fake recall", key: "fake_recall", num: true, html: (r) => pct(r.fake_recall) },
               { label: "Chosen", key: "chosen", html: (r) => r.chosen
                  ? '<span class="pill-yes">yes</span>' : '<span class="pill-no">—</span>' }],
              Object.entries(data.models || {}).map(([model, m]) => ({
                model, ...m, chosen: model === data.chosen_model,
              }))
            )}
          </div>
          <div>
            ${sectionHead("What the model learned")}
            ${table(
              [{ label: "Toward FAKE", get: (r) => r.fake, html: (r) => md(r.fake) },
               { label: "Toward REAL", get: (r) => r.real, html: (r) => md(r.real) }],
              buildTermRows(data.top_terms || {})
            )}
          </div>
        </div>
        ${callout("caution", "This is an upper bound", data.upper_bound_caveat || "")}`;
      break;

    default:
      body = "";
  }

  const plainBlock = plainItem ? `
    <section class="plainread">
      <div class="plainread__label">In plain language</div>
      <p class="plainread__body">${md(plainItem.body)}</p>
      ${plainItem.so_what ? `<div class="finding__sowhat">
        <span class="finding__sowhatlabel">So what</span>
        ${md(plainItem.so_what)}
      </div>` : ""}
      ${plainItem.caution ? callout("caution", "Worth knowing", plainItem.caution) : ""}
    </section>` : "";

  const scales = (state.summary.plain || {}).scales || {};
  const scaleFor = { clusters: "silhouette", market: "volatility_correlation",
                     rules: "rule_count" }[question.id];

  return (
    plainBlock +
    answerBlock(outcome) +
    (scaleFor && scales[scaleFor]
      ? `<div class="callout callout--note scale-note">
           <span class="callout__label">How to read that number</span>
           ${esc(scales[scaleFor])}
         </div>`
      : "") +
    body
  );
}

/** Every cluster with its plain reading beside the raw words.
 *
 *  Without this the reader sees `rs crore, lakh, road, accident, killed, old,
 *  year old` and has to work out what it means. With it, "Indian rupee amounts"
 *  and "traffic and deaths" say it for them, and the raw words stay available for
 *  anyone who wants to check the claim. */
function renderClusterTable(data) {
  const clusters = (data.clusters || []);
  if (!clusters.length) return "";
  const glosses = (state.summary.plain || {}).cluster_terms || {};
  const rows = clusters.map((c) => {
    const g = glosses[String(c.cluster_id)] || {};
    return {
      id: c.cluster_id,
      size: c.size,
      share: c.share_of_sample,
      summary: g.summary || "—",
      raw: (g.raw || c.top_terms || []).slice(0, 8),
      note: g.note || "",
    };
  });
  return `
    ${sectionHead("What each cluster is, in plain language",
      "The distinguishing words translated. The raw vocabulary is kept beside it so " +
      "the translation can be checked rather than taken on trust.")}
    <div class="tablewrap"><div class="tablescroll">
      <table class="data">
        <thead><tr>
          <th>Cluster</th><th class="num">Headlines</th><th class="num">Share</th>
          <th>What distinguishes it</th><th>Distinguishing words</th>
        </tr></thead>
        <tbody>${rows.map((r) => `
          <tr>
            <td>${esc(r.id)}</td>
            <td class="num">${num(r.size)}</td>
            <td class="num">${pct(r.share)}</td>
            <td>
              <div style="font-weight:550">${esc(r.summary)}</div>
              ${r.note ? `<div class="small faint" style="margin-top:3px">${esc(r.note)}</div>` : ""}
            </td>
            <td class="dim">${r.raw.map((t) => `<code>${esc(t)}</code>`).join(" ")}</td>
          </tr>`).join("")}
        </tbody>
      </table>
    </div></div>`;
}

function buildTermRows(terms) {
  const fake = terms.toward_fake || [];
  const real = terms.toward_real || [];
  const n = Math.max(fake.length, real.length);
  const rows = [];
  for (let i = 0; i < n; i += 1) {
    rows.push({ fake: fake[i] || "", real: real[i] || "" });
  }
  return rows;
}

/* ── explore ───────────────────────────────────────────────────────────── */

function pageExplore() {
  const tab = state.route.tab || "Headlines";
  const body = { Headlines: exploreHeadlines, "Monthly series": exploreSeries,
                 Warehouse: exploreWarehouse, OLAP: exploreOlap,
                 Pipeline: explorePipeline }[tab] || exploreHeadlines;
  return tabs(["Headlines", "Monthly series", "Warehouse", "OLAP", "Pipeline"], tab) + body();
}

function exploreHeadlines() {
  const total = state.summary.corpus
    ? (state.summary.corpus.analysis_window || {}).headlines_in_window : null;
  const host = `<div id="explore-headlines">
    <div class="controls">
      <div class="field">
        <label class="field__label" for="ex-topic">Topic</label>
        <select id="ex-topic"><option value="">All topics</option></select>
      </div>
      <div class="field">
        <label class="field__label" for="ex-limit">Rows</label>
        <select id="ex-limit">
          <option>50</option><option>100</option><option>200</option><option>500</option>
        </select>
      </div>
      <div class="field">
        <label class="field__label" for="ex-offset">Offset</label>
        <input id="ex-offset" type="number" value="0" min="0" step="50">
      </div>
      <button class="btn btn--primary" id="ex-go">Load</button>
      <button class="btn" id="ex-csv">Download CSV</button>
    </div>
    <div id="ex-results" class="empty">Choose a topic and press Load.</div>
  </div>`;

  setTimeout(async () => {
    try {
      const { topics } = await api("/topics");
      const select = $("#ex-topic");
      if (select) {
        (topics || []).forEach((t) => {
          const option = document.createElement("option");
          option.value = t; option.textContent = t;
          select.appendChild(option);
        });
        const load = async () => {
          const topic = select.value || null;
          const limit = $("#ex-limit").value;
          const offset = $("#ex-offset").value || 0;
          const box = $("#ex-results");
          box.textContent = "Loading…";
          try {
            const data = await api("/headlines", { topic, limit, offset });
            window._lastHeadlines = data.rows || [];
            into(box, table(
              [{ label: "Date", key: "publish_date" },
               { label: "Topic", key: "topic", dim: true },
               { label: "Headline", key: "headline_text" },
               { label: "Style score", key: "sensational_score", num: true,
                 html: (r) => (r.sensational_score == null ? "n/a" : Number(r.sensational_score).toFixed(4)) },
               { label: "Sentiment", key: "sentiment_compound", num: true,
                 html: (r) => (r.sentiment_compound == null ? "n/a" : Number(r.sentiment_compound).toFixed(4)) },
               { label: "Risk signal", key: "is_risk_signal", html: (r) => r.is_risk_signal
                 ? '<span class="pill-yes">style flag</span>' : '<span class="pill-no">—</span>' }],
              data.rows || [],
              { empty: "No headlines matched.",
                caption: `${num(data.total)} in-window headlines` +
                  (topic ? ` for \`${topic}\`` : "") +
                  ` · showing ${num((data.rows || []).length)} from offset ${offset}` }
            ) + '<div style="padding:12px 16px 0">' +
              callout("caution", "What this flag means",
                "`is_risk_signal` is a **style** flag on unlabelled headlines. It is not " +
                "a fake-news determination; nothing in this corpus is labelled.") + "</div>");
          } catch (error) {
            into(box, `<div class="empty">Could not load: ${esc(error.message)}</div>`);
          }
        };
        $("#ex-go").addEventListener("click", load);
        // Changing the filter reloads straight away, so a demo does not need
        // the button. The button stays for the offset case.
        select.addEventListener("change", load);
        $("#ex-csv").addEventListener("click", () => {
          const rows = window._lastHeadlines || [];
          if (!rows.length) { toast("Nothing loaded to download yet."); return; }
          downloadCsv(rows, "headlines.csv");
          toast(`Downloaded ${rows.length} rows.`);
        });
        load();
      }
    } catch (error) {
      const box = $("#ex-results");
      if (box) box.textContent = `Could not load topics: ${error.message}`;
    }
  }, 0);

  return `
    <div class="lede">The same warehouse the analysis ran on, paged and filtered.
      ${total ? `${num(total)} in-window headlines.` : ""}</div>
    ${host}`;
}

function exploreSeries() {
  const host = `<div id="explore-series">
    <div class="controls">
      <div class="field">
        <label class="field__label" for="sr-topic">Topic</label>
        <select id="sr-topic"><option value="">All topics</option></select>
      </div>
      <button class="btn btn--primary" id="sr-go">Load</button>
      <button class="btn" id="sr-csv">Download CSV</button>
    </div>
    <div id="sr-results" class="empty">Loading…</div>
  </div>`;

  setTimeout(async () => {
    try {
      const { topics } = await api("/topics");
      const select = $("#sr-topic");
      (topics || []).forEach((t) => {
        const option = document.createElement("option");
        option.value = t; option.textContent = t;
        select.appendChild(option);
      });
      const load = async () => {
        const topic = select.value || null;
        const data = await api("/series/monthly", { topic });
        window._lastSeries = data.rows || [];
        const box = $("#sr-results");
        into(box, `
          ${chart("__series", `Monthly volume — ${topic || "all topics"}`,
            "Notice how flat this is. That flatness is the RQ2 finding, and it is why no " +
            "month registers as a volume outlier.",
            "Counts are headline totals for the selection; the rate is recomputed as " +
            "sum ÷ count from the cube's stored sums.")}
          ${table(
            [{ label: "Month", key: "year_month" },
             { label: "Headlines", key: "headline_count", num: true, html: (r) => num(r.headline_count) },
             { label: "Risk-signal rate", key: "risk_signal_rate", num: true, html: (r) => pct(r.risk_signal_rate) }],
            (data.rows || []).slice(0, 60)
          )}`);
        renderSeries(data.rows || [], "series", topic || "all topics");
      };
      $("#sr-go").addEventListener("click", load);
      select.addEventListener("change", load);
      $("#sr-csv").addEventListener("click", () => {
        const rows = window._lastSeries || [];
        if (!rows.length) { toast("Nothing loaded to download yet."); return; }
        downloadCsv(rows, "monthly_series.csv");
        toast(`Downloaded ${rows.length} rows.`);
      });
      load();
    } catch (error) {
      const box = $("#sr-results");
      if (box) box.textContent = `Could not load: ${error.message}`;
    }
  }, 0);

  return `<div class="lede">Monthly counts and rates straight from the cube.</div>${host}`;
}

/** Built here rather than in Python because it is a browse view, not a
 *  published figure: there is no claim here that needs to be reproducible, and
 *  routing it through the spec endpoint would mean a round trip per keystroke.
 *
 *  The colours are read from the CSS custom properties rather than hardcoded,
 *  so this chart cannot end up on a different palette from the other nine. */
function cssToken(name, fallback) {
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name).trim();
  return value || fallback;
}

function renderSeries(rows, id, title) {
  const host = document.getElementById(`chart-${id}`);
  if (!host || !rows.length || !window.vegaEmbed) return;
  const t = {
    grid: cssToken("--grid", "#E3E3E8"),
    axis: cssToken("--axis", "#C7C7CC"),
    muted: cssToken("--muted", "#5E5E63"),
    accent: cssToken("--accent", "#0062C4"),
  };
  // Months are ordinal in a series, so the axis is sorted by the label itself.
  // Left to itself Vega-Lite uses the order the rows arrive in.
  const months = Array.from(new Set(rows.map((r) => r.year_month))).sort();
  vegaEmbed(host, {
    $schema: "https://vega.github.io/schema/vega-lite/v6.json",
    width: "container",
    height: 280,
    data: { values: rows },
    mark: { type: "line", strokeWidth: 1.6, color: t.accent },
    encoding: {
      x: { field: "year_month", type: "nominal", sort: months,
           axis: { labelAngle: -45, labelFontSize: 10, tickCount: 12,
                   gridColor: null, domain: false, labelColor: t.muted } },
      y: { field: "headline_count", type: "quantitative",
           axis: { gridColor: t.grid, domain: false, tickColor: t.axis,
                   labelColor: t.muted, titleColor: t.muted } },
      tooltip: [
        { field: "year_month", type: "nominal" },
        { field: "headline_count", type: "quantitative" },
        { field: "risk_signal_rate", type: "quantitative", format: ".2%" },
      ],
    },
    config: {
      background: "transparent",
      view: { stroke: "transparent" },
      font: "system-ui, -apple-system, Segoe UI, sans-serif",
    },
  }, { actions: false, renderer: "svg" }).catch(() => {});
}

function exploreWarehouse() {
  return `<div class="lede">A star schema over three fact tables, with dimensions for date,
    topic, dataset, label, instrument and keyword, plus a bridge from headlines to keywords.</div>
    <div id="wh-tables"></div>`;
}

function exploreOlap() {
  return `<div class="lede">Ten whitelisted OLAP operations. Each one runs with its stored
    defaults on a single click, and comes back with a chart, a plain-language reading
    of the result, and the rows behind it. The operation is looked up by name, never
    interpolated into SQL, and the connection is read-only.</div>
    ${olapOperationCards()}`;
}

function explorePipeline() {
  return `<div class="lede">The ingest and ETL funnel, so every row count can be traced to a
    run.</div><div id="pipe-runs" class="empty">Loading…</div>`;
}

/* ── how it works ──────────────────────────────────────────────────────── */

const CORRECTIONS = [
  {
    area: "RQ4 · clustering",
    what: "Reported a silhouette of 0.068 as “these headlines have no recoverable topic structure”.",
    why: "scikit-learn's default token pattern admits pure numerals, so clusters were described by <code>000</code>, <code>102</code>, <code>1000</code>. With numerals removed they were described by <code>for</code>, <code>with</code>, <code>from</code>, because mean TF-IDF inside a cluster is highest for function words. And descriptions read off the K-Means centroid weights returned <code>aadhaar, aadmi, aap</code> for <em>every</em> cluster, because SVD components carry an arbitrary sign and alphabetically early features win systematically.",
    fix: "Alphabetic-only tokens, English stop words, and describing each cluster by the mean TF-IDF of its own members.",
    now: "0.2582, with the correction stated in the report itself rather than quietly replacing the number.",
  },
  {
    area: "RQ5 · association rules",
    what: "All 1,797 rules showed a lift of exactly 1.000, which reads as “every attribute is a tautology”.",
    why: "mlxtend orders its rule frame as <code>antecedents, consequents, antecedent support, consequent support, support, confidence, lift</code>. Reading it positionally was off by two, so the consequent support was labelled confidence and the confidence was labelled lift. Separately, <code>use_colnames</code> defaults to False, so rules came back as <code>5 =&gt; 4</code>.",
    fix: "Read columns by name, pass <code>use_colnames=True</code>, and drop <code>quarter</code> from the item set because year and quarter are redundant in both directions.",
    now: "644 readable rules, with both algorithms agreeing exactly.",
  },
  {
    area: "RQ6 · market association",
    what: "Reported the market question as “no relationship”, and nearly missed a real one.",
    why: "Only correlations against daily <em>returns</em> were reported. Against 20-day volatility, log business-headline volume correlates at r = −0.2827 with p &lt; 0.0001.",
    fix: "Report the volatility half separately, with the number of tests beside every p-value so that one significant lag out of fifteen is not read as a discovery.",
    now: "Both halves reported, and the direction is left explicitly unexplained.",
  },
  {
    area: "Reproducibility",
    what: "Claimed that fixed seeds reproduce every number. The claim was false.",
    why: "DuckDB's <code>SAMPLE reservoir(n ROWS) REPEATABLE(seed)</code> returns the wrong number of rows — 709 for 2,000 requested — and the sampling code topped up from the first headlines by id, which is a chronological sample of the oldest slice of the corpus. Separately, <code>silhouette_score</code> subsamples with no seed by default, so its score moved between identical runs.",
    fix: "Sort on <code>hash(column, seed)</code> and take the first n; pass an explicit <code>random_state</code> to the silhouette.",
    now: "Two mining runs agree on every number, and a test asserts it.",
  },
];

const DECISIONS = [
  ["The window is a flag, not a filter",
   "Filtering would throw away 2 million rows that are still queryable and make the ETL destructive. <code>in_window</code> is a boolean on the fact row, and the window is derived from <code>max(publish_date)</code> so it is never stale."],
  ["2015 is a partial year, and flagged as one",
   "The window starts 2015-06-30. Its wider spread made a flat series look like CV 0.39 when it was included in a whole-year statistic. Years below <code>full_year_months</code> are now excluded and named."],
  ["Headline grain is (date, text)",
   "37,629 texts recur across dates, and 111,146 (date, text) pairs are filed under more than one category. One row per category would multi-count a third of a million headlines in every topic analysis."],
  ["Cubes store sums, never averages",
   "A rounded average cannot be averaged again. Every mean in the report is recomputed as sum ÷ count at query time, and a test rejects any column named like a mean."],
  ["<code>Local</code> is separated from subjects",
   "It is 70% of the window and is a <em>where</em>, not a <em>what</em>. <code>dim_topic</code> carries a <code>topic_group</code> so a city desk is never compared against a subject as if they matched."],
  ["<code>Unknown</code> is excluded from the topic ranking",
   "It tops the raw sensationalism ranking at 17.29% because it is a filing gap, not a subject. Its score measures the absence of a filing decision."],
  ["The threshold is 0.2208, the measured 95th percentile",
   "The original 0.5 flagged 156 rows out of 3.15 million, which is not a threshold. Because any cut-off is a judgement, the report ships a sensitivity table across eight of them."],
  ["Dimension keys are looked up, never written",
   "<code>dim_dataset</code> numbers by sorted code, so <code>toi</code> is key 3. A hard-coded 1 pointed every headline at the wrong source while all row counts stayed correct — the most dangerous kind of bug, because nothing failed."],
  ["The API serves a copy of the warehouse",
   "DuckDB locks its file exclusively even for a read-only connection, so serving the live file blocked every CLI command. The copy is byte-exact and lets the dashboard and the CLI run at the same time."],
  ["The dashboard computes nothing",
   "Every figure was measured once by the mining stage and rendered from a template. A dashboard that recomputed anything would be a second source of truth, and the two would drift."],
];

function pageHow() {
  const tab = state.route.tab || "Plain guide";
  let body;
  if (tab === "Corrections") body = howCorrections();
  else if (tab === "Decisions") body = howDecisions();
  else if (tab === "Warehouse shape") body = howWarehouse();
  else if (tab === "Rules") body = howRules();
  else if (tab === "Guard rails") body = howGuards();
  else body = howPlainGuide();

  return `
    <div class="lede">The parts of this project that do not show up as a number.
    Each was found by checking the output rather than trusting it, and each had looked
    convincingly like a finding.</div>
    ${tabs(["Plain guide", "Guard rails", "Corrections", "Decisions", "Warehouse shape", "Rules"], tab)}
    ${body}`;
}

function howPlainGuide() {
  const plain = state.summary.plain || {};
  const scales = plain.scales || {};
  const scaleRows = Object.entries(scales).map(([key, text]) => `
    <div class="gloss">
      <div class="gloss__term">${esc(key.replace(/_/g, " "))}</div>
      <div class="gloss__plain">${esc(text)}</div>
    </div>`).join("");
  return `
    ${renderIntro()}
    ${sectionHead("Every headline number, with its range",
      "A figure with no scale is decoration. These are the ranges and comparisons that " +
      "make each one judgeable.")}
    <div class="glossary__grid">${scaleRows}</div>
    ${renderGlossary()}
    ${renderSummary()}`;
}

function howGuards() {
  const guards = state.summary.guards || {};
  const data = guards.data || guards;
  const checks = data.checks || [];
  return `
    ${sectionHead("The inference gate",
      "The report will not present a claim the warehouse cannot support, and the command exits " +
      "non-zero when a check fails. A pipeline cannot pass silently on numbers it cannot back.")}
    <div class="kpis">
      ${kpi("Checks passed", `${(data.checks || []).length - (data.failed || []).length}/${(data.checks || []).length}`,
            data.passed ? "gate open" : `${(data.failed || []).length} failed`,
            data.passed ? "positive" : "caution")}
      ${kpi("Stages that declined to run", String((data.failed || []).length),
            "a stage that did not run has not answered its question", "muted")}
    </div>
    ${table(
      [{ label: "", key: "passed", html: (r) => r.passed
          ? '<span class="tick">pass</span>' : '<span class="cross">FAIL</span>' },
       { label: "Check", key: "check" },
       { label: "What it looked at", key: "detail", dim: true }],
      checks
    )}
    <p class="small muted">A check that cannot be evaluated reports that rather than passing
    quietly. The report is still written when the gate fails, because “this could not be
    measured, and here is why” is more useful than no report.</p>`;
}

function howCorrections() {
  return `
    ${sectionHead("Four things this study got wrong",
      "This is the most useful part of the project. Each was found by checking the output " +
      "rather than trusting it, and each had looked convincingly like a finding.")}
    ${CORRECTIONS.map((c) => `
      <article class="correction">
        <div class="correction__area">${esc(c.area)}</div>
        <div class="correction__what">${md(c.what)}</div>
        <dl>
          <dt>Why it was wrong</dt><dd>${c.why}</dd>
          <dt>Fix</dt><dd>${c.fix}</dd>
          <dt>Now</dt><dd class="is-now">${c.now}</dd>
        </dl>
      </article>`).join("")}`;
}

function howDecisions() {
  return `
    ${sectionHead("Design decisions, and the reason for each",
      "Every one of these is a measured property of the data rather than a preference, and " +
      "several were learned the hard way.")}
    ${DECISIONS.map(([title, why]) => `
      <div class="decision">
        <div class="decision__title">${title}</div>
        <div class="decision__why">${why}</div>
      </div>`).join("")}`;
}

function howWarehouse() {
  return `
    <div class="lede">A star schema over three fact tables, with dimensions for date, topic,
    dataset, label, instrument and keyword, plus a bridge from headlines to keywords.</div>
    <div id="how-tables"></div>`;
}

function howRules() {
  const rules = state.manifest.honesty_rules || [];
  const datasets = state.manifest.datasets || {};
  const citations = Object.values(datasets)
    .map((d) => (d.citation || "").split("(")[0].trim()).filter(Boolean);
  return `
    ${sectionHead("Rules this study holds itself to",
      "These are enforced in code, not merely written down. Each has at least one test that " +
      "fails if it is broken.")}
    <ul class="rule-list">
      ${rules.map((r) => `<li>${md(r)}</li>`).join("")}
    </ul>
    ${sectionHead("Traceability")}
    <p><code>dwm mine</code> writes <code>mining.json</code>. <code>dwm report</code> writes
    <code>facts.json</code> and renders <code>report.md</code> from it <b>by template</b>.
    There is no language model anywhere in the reporting path, deliberately: a language model
    can produce a fluent sentence containing a number nobody computed.</p>
    <p>Every fact records the source it came from, and every fact with a unit carries a
    caution. This page walks the entire API payload at start-up looking for a figure that has
    a unit and no caution, and if it finds one, the warning appears in the top bar.</p>
    ${citations.length ? `
      ${sectionHead("Sources")}
      <ul class="rule-list">${citations.map((c) => `<li>${md(c)}</li>`).join("")}</ul>` : ""}`;
}

/* ── report ────────────────────────────────────────────────────────────── */

function pageReport() {
  return `<div class="lede">Rendered from <code>facts.json</code> by templates. No language
    model was involved. The same text is at <code>reports/report.md</code>.</div>
    <button class="btn mb-2" id="dl-report">Download report.md</button>
    <article class="report" id="report-body"><div class="loading">
      <div class="loading__bar"></div><p>Loading the report…</p></div></article>`;
}

function loadReport() {
  fetch("/report")
    .then((r) => r.text())
    .then((text) => {
      const body = $("#report-body");
      if (!body) return;
      body.innerHTML = window.marked
        ? marked.parse(text)
        : "<pre>" + esc(text) + "</pre>";
      const btn = $("#dl-report");
      if (btn) btn.addEventListener("click", () => {
        downloadBlob(new Blob([text], { type: "text/markdown" }), "report.md");
        toast("Downloaded report.md");
      });
    })
    .catch((error) => {
      const body = $("#report-body");
      if (body) body.innerHTML = `<div class="empty">Could not load the report: ${esc(error.message)}</div>`;
    });
}

/* ── download helpers ──────────────────────────────────────────────────── */

function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function downloadCsv(rows, name) {
  if (!rows.length) return;
  const columns = Object.keys(rows[0]);
  const escape = (v) => {
    if (v === null || v === undefined) return "";
    const s = String(v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const lines = [columns.join(",")];
  rows.forEach((r) => lines.push(columns.map((c) => escape(r[c])).join(",")));
  downloadBlob(new Blob([lines.join("\n")], { type: "text/csv" }), name);
}

/* ── navigation and routing ────────────────────────────────────────────── */

function renderNav() {
  // Grouped, so the sidebar reads as three parts of one study rather than
  // twelve undifferentiated links. The group heading is skipped for the first
  // group, which sits directly under the brand.
  const seen = new Set();
  $("#nav-main").innerHTML = SECTIONS.map((s) => {
    const active = state.route.page === s.id ? " is-active" : "";
    const tag = s.id === "findings" ? '<span class="navlink__tag">7</span>' : "";
    const heading = seen.has(s.group)
      ? ""
      : (seen.add(s.group), s.group === SECTIONS[0].group ? "" : `<div class="navgroup">${esc(s.group)}</div>`);
    return `${heading}<button class="navlink${active}" data-page="${s.id}">
      <span class="navlink__glyph">${s.glyph}</span>
      <span class="navlink__label">${esc(s.label)}</span>${tag}
    </button>`;
  }).join("");

  const showSub = state.route.page === "findings";
  $("#nav-subgroup").hidden = !showSub;
  $("#nav-sub").innerHTML = QUESTIONS.map((q) => {
    const active = state.route.sub === q.id ? " is-active" : "";
    return `<button class="navlink navlink--sub${active}" data-question="${q.id}">
      <span class="navlink__rq">${esc(q.rq)}</span>
      <span class="navlink__label">${esc(q.nav)}</span>
    </button>`;
  }).join("");

  const corpus = state.manifest.corpus || {};
  $("#nav-corpus").innerHTML =
    `${esc(num(corpus.headlines_in_window))} headlines<br>` +
    `2015-06-30 to ${esc(corpus.end || "—")}`;
}

function renderTopbar() {
  const page = SECTIONS.find((s) => s.id === state.route.page);
  $("#page-title").textContent = page ? page.label : "Overview";

  const PAGE_SUBTITLE = {
    mood: "Drill-across: two fact tables at different grains, joined on the conformed trading-date key.",
    stocks: "One company at a time. Prices and coverage, with the limits of both stated.",
    cube: "Sector by year, sliced and rolled up. Sums and counts, never stored averages.",
    events: "Cumulative abnormal return around a known date, with the excluded rows shown too.",
    mining: "Patterns across the whole window, each beside the thing that would undermine it.",
    checker: "Scores the writing, not the claim. A high style score is not evidence of falsehood.",
    quality: "What went into this warehouse, what was rejected, and what nobody has audited yet.",
  };
  let sub = state.manifest.subtitle || "";
  if (PAGE_SUBTITLE[state.route.page]) {
    sub = PAGE_SUBTITLE[state.route.page];
  } else if (state.route.page === "findings") {
    const q = QUESTIONS.find((x) => x.id === state.route.sub) || QUESTIONS[0];
    sub = `Each page states its answer first. The evidence is underneath, and the caveat sits ` +
          `with the number rather than in a footnote. — ${q.rq}, ${q.title}`;
  } else if (state.route.page === "explore") {
    sub = "The same warehouse the analysis ran on, paged and filtered.";
  } else if (state.route.page === "how") {
    sub = "The parts of this project that do not show up as a number.";
  } else if (state.route.page === "report") {
    sub = "Rendered from facts.json by templates. No language model was involved.";
  }
  $("#page-sub").textContent = sub;

  const guards = state.manifest.guards || {};
  const badge = $("#guard-badge");
  badge.classList.toggle("is-failed", !guards.passed);
  $("#guard-text").textContent = guards.passed
    ? `${guards.succeeded}/${guards.total} guard rails passed`
    : `${(guards.failed || []).length} guard rail(s) failed`;

  const p = state.manifest.provenance || {};
  $("#provenance").textContent =
    `run ${p.mining_run_id || "—"} · config v${p.config_version == null ? "?" : p.config_version}` +
    ` · ${shortDate(p.generated_at)}`;

  if (state.uncautoned.length) {
    $("#provenance").textContent += ` · ⚠ ${state.uncautoned.length} figure(s) missing a caution`;
    badge.classList.remove("is-failed");
    badge.classList.add("is-failed");
    $("#guard-text").textContent =
      `${state.uncautoned.length} figure(s) have a unit but no caution`;
  }
}

function renderFooter() {
  const p = state.manifest.provenance || {};
  const sources = Object.values(state.manifest.datasets || {})
    .map((d) => (d.citation || "").split("(")[0].trim()).filter(Boolean).join(" · ");
  $("#footer").innerHTML =
    `Every figure on this page is served unchanged from <strong>${esc(p.source || "facts.json")}</strong>, ` +
    `which the inference stage rendered from <strong>mining.json</strong> by template. ` +
    `No language model was involved, and this dashboard computes nothing.<br>` +
    (sources ? `Sources: ${esc(sources)}<br>` : "") +
    `Generated ${esc(shortDate(p.generated_at))} · run ${esc(p.mining_run_id || "—")}.`;
}

/** Read the hash into `state.route`.
 *
 *  The shape differs by page: findings carry a question *and* may carry a tab,
 *  while Explore and How it works carry only a tab. Reading the second segment
 *  unconditionally as `sub` meant the tab never landed in `tab`, and every tab
 *  in How it works silently rendered the first panel no matter what was
 *  clicked. */
function routeFromHash() {
  const raw = (window.location.hash || "#/overview").replace(/^#\/?/, "");
  const [page, second, third] = raw.split("/");
  const valid = SECTIONS.some((s) => s.id === page) ? page : "overview";
  const isFinding = valid === "findings";
  state.route = {
    page: valid,
    sub: isFinding ? (second || "volume") : null,
    tab: isFinding
      ? (third ? decodeURIComponent(third) : null)
      : (second ? decodeURIComponent(second) : null),
  };
}

function hashFor(route) {
  const parts = [route.page];
  if (route.page === "findings" && route.sub) parts.push(route.sub);
  if (route.tab) parts.push(encodeURIComponent(route.tab));
  return "#/" + parts.join("/");
}

function render() {
  routeFromHash();
  renderNav();
  renderTopbar();
  renderFooter();

  const view = $("#view");
  let html;
  // The v2 views live in v2.js so app.js does not double in size, and so a
  // syntax error in one file cannot stop the other booting. They are called
  // through DWM_V2 rather than imported, because there is no build step here.
  const V2 = window.DWM_V2;
  switch (state.route.page) {
    case "findings": html = pageFindings(state.route.sub); break;
    case "explore":  html = pageExplore(); break;
    case "how":      html = pageHow(); break;
    case "report":   html = pageReport(); break;
    default:         html = pageOverview();
  }
  // Overview carries the v2 Quick Brief and insights feed above the v1 summary,
  // because the brief is the most likely first question a visitor has.
  if (V2 && V2.pageOverview && state.route.page === "overview") {
    html = V2.pageOverview() + html;
  } else if (V2 && V2["page" + state.route.page.charAt(0).toUpperCase() + state.route.page.slice(1)]) {
    html = V2["page" + state.route.page.charAt(0).toUpperCase() + state.route.page.slice(1)]();
  }
  view.innerHTML = html;
  mountCharts();

  if (state.route.page === "report") loadReport();
  if (state.route.page === "explore") wireExplore();
  if (state.route.page === "how" && state.route.tab === "Warehouse shape") loadTableList("#how-tables", "How the warehouse is shaped");
  if (state.route.page === "explore" && state.route.tab === "Warehouse") {
    loadTableList("#wh-tables", "Every table, largest first");
  }
  document.title = `Indian News Warehouse — ${(SECTIONS.find((s) => s.id === state.route.page) || {}).label || "Overview"}`;
}

let tableListCache = null;

async function loadTableList(selector, caption) {
  const host = $(selector);
  if (!host) return;
  if (!tableListCache) {
    try {
      tableListCache = await api("/tables");
    } catch (error) {
      host.innerHTML = `<div class="empty">Could not load the table list: ${esc(error.message)}</div>`;
      return;
    }
  }
  const tables = (tableListCache.tables || []).slice().sort((a, b) => b.rows - a.rows);
  const groups = [
    ["Fact tables", tables.filter((t) => t.table.startsWith("fact_"))],
    ["Cubes", tables.filter((t) => t.table.startsWith("cube_"))],
    ["Dimensions", tables.filter((t) => t.table.startsWith("dim_"))],
    ["Staging, cleaning and the bridge",
      tables.filter((t) => !/^(fact_|cube_|dim_)/.test(t.table))],
  ];
  host.innerHTML = `
    <div class="kpis">
      ${kpi("Tables", String(tables.length), "in the warehouse")}
      ${kpi("Rows", num(tableListCache.total_rows), "across every table")}
      ${kpi("Fact tables", String(groups[0][1].length), "the measured grain")}
      ${kpi("Dimensions", String(groups[2][1].length), "looked up, never hard-coded", "muted")}
    </div>
    ${chart("warehouse_rows", "Largest tables by row count", "", caption)}
    ${groups.map(([label, rows]) => `
      ${sectionHead(label)}
      ${table([{ label: "Table", key: "table" },
               { label: "Rows", key: "rows", num: true, html: (r) => num(r.rows) }], rows)}`
    ).join("")}`;
  mountCharts();
}

/** Attach the behaviour of whichever Explore tab is showing.
 *
 *  Every tab that has listeners needs one wired here. `wireOlap` was defined and
 *  then, when the old OLAP form was removed, its call was dropped along with it
 *  — so the operation grid rendered and clicking a card did nothing at all. A
 *  defined-but-uncalled function is invisible to a syntax check and to every
 *  test, which is why `test_every_wired_function_is_called` now asserts against
 *  exactly that.
 */
async function wireExplore() {
  const tab = state.route.tab || "Headlines";
  if (tab === "OLAP") await wireOlap();
  if (tab === "Pipeline") await wirePipeline();
}

/** Every OLAP operation, as a clickable worked example.
 *
 *  The recommended shape: one click per operation runs it with its stored
 *  defaults and shows a chart, a plain-language reading of the result, and the
 *  full table. A form you have to fill in before anything appears is worse in a
 *  demonstration than a button that just works.
 *
 *  The topic and year fields stay as *overrides* — empty means "use the
 *  defaults", which is the worked example. */
function olapOperationCards() {
  const host = `<div id="ol-grid" class="olgrid"></div>
    <div id="ol-detail"></div>`;
  setTimeout(async () => {
    const grid = $("#ol-grid");
    if (!grid) return;
    try {
      const { operations } = await api("/operations");
      into(grid, (operations || []).map((o) => `
        <button class="olop" data-op="${esc(o.name)}" type="button">
          <span class="olop__name">${esc(o.name)}</span>
          <span class="olop__params">${Object.keys(o.default_params || {}).length
            ? esc(Object.entries(o.default_params).map(([k, v]) => `${k}=${v}`).join(", "))
            : "no parameters needed"}</span>
        </button>`).join(""));
    } catch (error) {
      into(grid, `<div class="empty">Could not load the operation list: ${esc(error.message)}</div>`);
    }
  }, 0);
  return host;
}

async function wireOlap() {
  const grid = $("#ol-grid");
  if (!grid) return;
  const detail = $("#ol-detail");

  grid.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-op]");
    if (!button) return;
    const name = button.dataset.op;
    $$(".olop", grid).forEach((b) => b.classList.toggle("is-active", b === button));
    into(detail, '<div class="loading"><div class="loading__bar"></div><p>Running…</p></div>');
    try {
      const result = await api(`/query/${name}`);
      into(detail, renderOlapResult(name, result));
      mountInline(result.chart, `olap-${name}`);
    } catch (error) {
      into(detail, `<div class="empty">Could not run <code>${esc(name)}</code>: ${esc(error.message)}</div>`);
    }
  });
}

/** One OLAP result: what it is, what it found, what it looks like, and the rows.
 *
 *  Order matters. The description says what the operation *does*, the reading
 *  says what this particular run *found*, the chart shows it and the table backs
 *  it up. Previously there was no reading and no chart — just the rows, printed
 *  as raw JSON, which is a correct cross-tabulation arriving as a wall of text. */
function renderOlapResult(name, result) {
  const rows = result.rows || [];
  const columns = olapColumns(rows, result.columns);

  const header = `
    <div class="sectionhead"><div>
      <h2><code>${esc(name)}</code></h2>
      <p>${md(result.description || "")}</p>
    </div></div>`;

  const reading = result.reading
    ? `<div class="callout callout--note olread">
         <span class="callout__label">What this run found</span>
         ${md(result.reading)}
       </div>`
    : "";

  const spec = result.chart || {};
  const chartBlock = spec.dwm_reason
    ? `<div class="chart__error">${md(spec.dwm_reason)}</div>`
    : (Object.keys(spec).length
      ? `<div class="chart__body" id="olap-${esc(name)}"></div>`
      : "");

  // Named `chartHtml`, not `chart`: the global `chart()` renders a spec, and a
  // local holding an HTML string under the same name is a trap for whoever
  // adds a chart call to this function later.
  const chartHtml = chartBlock ? `
    <figure class="chart">
      <figcaption class="chart__head">
        <div class="chart__title">${esc(name)} — the result</div>
        <div class="chart__sub">Chosen automatically from the shape of the result:
          a ranked bar, a time series or a stacked area. The rows below are the
          same numbers.</div>
      </figcaption>
      ${chartBlock}
      <div class="chart__cap">${num(rows.length)} rows ·
        ${(result.columns || []).length} columns ·
        produced by <code>GET /query/${esc(name)}</code> against a read-only
        connection.</div>
    </figure>` : "";

  return header + reading + chartHtml + table(columns, rows, {
    empty: "The operation returned no rows for these parameters.",
    caption: null,
  });
}

/** Embed a spec that is not part of the served chart set, such as an OLAP
 *  result. Kept separate from `mountCharts` because these arrive one at a time
 *  on user action rather than all at once on load. */
function mountInline(spec, id) {
  const host = document.getElementById(id);
  if (!host || !spec || spec.dwm_reason || !Object.keys(spec).length) return;
  if (!window.vegaEmbed) {
    into(host, '<div class="chart__error">The chart library did not load, so this '
      + "result is shown as a table only. Every number is still there.</div>");
    return;
  }
  vegaEmbed(host, spec, {
    actions: false,
    renderer: "svg",
    tooltip: { theme: state.theme === "dark" ? "dark" : "light" },
    config: { background: "transparent" },
  }).catch((error) => {
    into(host, `<div class="chart__error">This chart failed to render (${esc(error.message)}).</div>`);
  });
}

async function wirePipeline() {
  const host = $("#pipe-runs");
  if (!host) return;
  try {
    const { runs } = await api("/audit", { limit: 20 });
    into(host, table(
      [{ label: "Stage", key: "stage" },
       { label: "Dataset", key: "dataset", dim: true },
       { label: "Rows read", key: "rows_read", num: true, html: (r) => num(r.rows_read) },
       { label: "Rows loaded", key: "rows_loaded", num: true, html: (r) => num(r.rows_loaded) },
       { label: "Rejected", key: "rows_rejected", num: true, html: (r) => num(r.rows_rejected) },
       { label: "Finished", key: "finished_at", dim: true }],
      runs || [],
      { empty: "No pipeline runs recorded yet.",
        caption: "Every stage records what it read, what it loaded and what it rejected." }
    ));
  } catch (error) {
    host.textContent = `Could not load the audit log: ${error.message}`;
  }
}

/* ── theme ─────────────────────────────────────────────────────────────── */

function applyTheme(name) {
  state.theme = name;
  document.documentElement.dataset.theme = name;
  try { localStorage.setItem("dwm-theme", name); } catch (e) { /* private mode */ }
  $("#theme-label").textContent = name === "dark" ? "Light" : "Dark";
  mountCharts();
}

/** Charts are built per theme in Python, so switching means re-fetching.
 *  Re-painting the CSS alone would leave the charts on the old palette, which is
 *  the exact half-light/half-dark failure this dashboard was rewritten to fix. */
async function toggleTheme() {
  const next = state.theme === "dark" ? "light" : "dark";
  // v2 caches its specs per query, and every spec embeds the theme it was built
  // in. Without this the v2 pages would keep rendering light charts on a dark
  // page - the exact half-light/half-dark failure this dashboard was rewritten
  // to fix, reintroduced through a cache.
  if (window.DWM_V2 && window.DWM_V2.clearSpecCache) window.DWM_V2.clearSpecCache();
  applyTheme(next);
  try {
    const data = await api("/ui/charts", { theme: next });
    state.charts = data.charts || {};
    mountCharts();
  } catch (error) {
    toast(`Could not reload charts for the ${next} theme: ${error.message}`);
  }
}

/* ── events ────────────────────────────────────────────────────────────── */

function wireEvents() {
  document.addEventListener("click", (event) => {
    const pageBtn = event.target.closest("[data-page]");
    if (pageBtn) {
      state.route.tab = null;
      window.location.hash = hashFor({ page: pageBtn.dataset.page, sub: null, tab: null });
      return;
    }
    const qBtn = event.target.closest("[data-question]");
    if (qBtn) {
      window.location.hash = hashFor({ page: "findings", sub: qBtn.dataset.question, tab: null });
      return;
    }
    const tabBtn = event.target.closest("[data-tab]");
    if (tabBtn) {
      const current = state.route;
      window.location.hash = hashFor({
        page: current.page, sub: current.sub, tab: tabBtn.dataset.tab,
      });
      return;
    }
    if (event.target.closest("#theme-toggle")) toggleTheme();
  });

  window.addEventListener("hashchange", render);
  $("#theme-label").textContent = state.theme === "dark" ? "Light" : "Dark";
}

/* ── start ─────────────────────────────────────────────────────────────── */

async function boot() {
  wireEvents();
  const view = $("#view");

  try {
    const [manifest, summary, charts] = await Promise.all([
      api("/ui/manifest"),
      api("/summary"),
      api("/ui/charts", { theme: state.theme }),
    ]);
    state.manifest = manifest;
    state.summary = summary;
    state.charts = charts.charts || {};

    // Glossary lookup, built once. Terms are marked inline wherever they appear.
    const terms = (((summary.plain || {}).glossary) || {}).terms || [];
    GLOSSARY = {};
    for (const entry of terms) {
      GLOSSARY[entry.term.toLowerCase()] = entry.plain;
    }

    // The honesty check, run once over the whole payload.
    state.uncautoned = auditPayload(summary);

    render();
  } catch (error) {
    view.innerHTML = `
      <div class="hero">Cannot reach the backend</div>
      <p class="lede">${esc(error.message)}</p>
      ${callout("note", "Start it with",
        "<code>.\\\\.venv\\\\Scripts\\\\python.exe -m dwm serve</code> — the dashboard is " +
        "served by that same process, on this same address.")}`;
    $("#page-title").textContent = "Unavailable";
    renderFooter();
  }
}

document.addEventListener("DOMContentLoaded", boot);