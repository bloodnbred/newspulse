/**
 * Clicks every control on every route in a real browser and reports what
 * actually happened.
 *
 * Screenshots show what a page looks like at rest. They cannot tell you
 * whether the Reset button resets anything, whether a second click on a treemap
 * node deselects it, or whether a filter that changes the URL also changes the
 * numbers. This drives the DOM instead: it enumerates every button, link,
 * input and tab on the page, operates each one, and fails the run if any of
 * them throws, logs an error, or produces a request the server rejected.
 *
 * Two things a naive version of this gets wrong, both fixed here:
 *
 *   - Clicking a link navigates. Mid-run, the page you are clicking through is
 *     no longer the page you enumerated. Links are therefore collected across
 *     every route and each distinct destination is visited once, in a separate
 *     pass, instead of being clicked in place.
 *   - An element can be visible and still be unreachable, behind the first-run
 *     tour. The tour is dismissed before enumeration.
 *
 * No dependencies. Node 22+ ships a global WebSocket and fetch, which is all
 * the DevTools protocol needs.
 *
 *   node scripts/uicheck.mjs [--base http://localhost:5173] [--cdp 9222]
 */

import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
};

const BASE = arg('base', 'http://localhost:5173').replace(/\/$/, '');
const PORT = Number(arg('cdp', '9222'));
const CHROME =
  process.env.CHROME_PATH ?? 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

const ROUTES = [
  '/',
  '/explore',
  // A URL no route matches, to prove the catch-all renders rather than
  // leaving the reader looking at an empty shell. '/sectors' and '/brief' are
  // API endpoints, not pages - they were in an earlier list of this and read
  // as a failure when the router correctly did not claim them.
  '/no-such-page',
  '/stocks/RELIANCE',
  '/events',
  '/events/1',
  '/mining',
  '/mining?tab=rules',
  '/mining?tab=models',
  '/mining?tab=network',
  '/headline',
  '/data',
  '/data?tab=etl',
  '/insights',
];

/** Controls operated in place. Links are handled separately - see the note
 *  above about why clicking one mid-run invalidates the enumeration. */
const IN_PLACE = [
  'button',
  'input[type="date"]',
  'input[type="checkbox"]',
  'input[type="range"]',
  'select',
  '[role="tab"]',
  'textarea',
].join(',');

const LINKS = 'a[href]';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------------------
// Chrome
// ---------------------------------------------------------------------------

async function launch() {
  const profile = await mkdtemp(join(tmpdir(), 'uicheck-'));
  const proc = spawn(
    CHROME,
    [
      '--headless=new',
      '--disable-gpu',
      '--hide-scrollbars',
      '--no-first-run',
      '--no-default-browser-check',
      '--window-size=1440,900',
      `--remote-debugging-port=${PORT}`,
      `--user-data-dir=${profile}`,
      'about:blank',
    ],
    { stdio: 'ignore', detached: false },
  );
  for (let i = 0; i < 60; i += 1) {
    await sleep(250);
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (r.ok) return { proc, profile };
    } catch {
      /* not up yet */
    }
  }
  proc.kill();
  throw new Error(`Chrome did not open a debugging port on ${PORT}`);
}

// ---------------------------------------------------------------------------
// DevTools protocol
// ---------------------------------------------------------------------------

class Cdp {
  #ws;
  #next = 1;
  #pending = new Map();
  #handlers = new Map();

  static async connect(url) {
    const c = new Cdp();
    c.#ws = new WebSocket(url);
    await new Promise((res, rej) => {
      c.#ws.addEventListener('open', res, { once: true });
      c.#ws.addEventListener('error', rej, { once: true });
    });
    c.#ws.addEventListener('message', (ev) => c.#onMessage(String(ev.data)));
    return c;
  }

  #onMessage(raw) {
    const msg = JSON.parse(raw);
    if (msg.id != null && this.#pending.has(msg.id)) {
      const { resolve, reject } = this.#pending.get(msg.id);
      this.#pending.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message));
      else resolve(msg.result);
      return;
    }
    const key = msg.sessionId ? `${msg.sessionId}:${msg.method}` : msg.method;
    for (const h of this.#handlers.get(key) ?? []) h(msg.params);
  }

  send(method, params = {}, sessionId) {
    const id = this.#next++;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    this.#ws.send(JSON.stringify(payload));
    return new Promise((resolve, reject) => {
      this.#pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.#pending.delete(id)) reject(new Error(`${method} timed out`));
      }, 30000);
    });
  }

  on(sessionId, method, fn) {
    const key = sessionId ? `${sessionId}:${method}` : method;
    const list = this.#handlers.get(key) ?? [];
    list.push(fn);
    this.#handlers.set(key, list);
  }

  close() {
    this.#ws.close();
  }
}

// ---------------------------------------------------------------------------
// In-page helpers, injected on every load
// ---------------------------------------------------------------------------

const HELPERS = `
window.__ui = {
  // React installs its own value setter on controlled inputs, so assigning
  // .value is silently ignored. Going through the prototype descriptor and
  // firing the events it listens for is what a keystroke looks like to React.
  setValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
      : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype
      : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  },

  // How a reader would name this control, with enough context to find it in
  // the source. Two controls with the same label on one page would otherwise
  // be indistinguishable in the report.
  describe(el) {
    const text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
    const label = el.getAttribute('aria-label') || el.getAttribute('title') || '';
    const kind = el.tagName.toLowerCase();
    const type = el.getAttribute('type') || '';
    const own = (label || text || el.getAttribute('name') || el.getAttribute('placeholder') || '').slice(0, 42);
    const section = el.closest('section, header, [class*="card"], [class*="glass"], nav');
    const head = section
      ? (section.querySelector('h1,h2,h3,[class*="title"]')?.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 30)
      : '';
    const state = el.disabled ? ' [disabled]'
      : el.getAttribute('aria-pressed') === 'true' ? ' [pressed]'
      : el.getAttribute('aria-selected') === 'true' ? ' [selected]'
      : el.type === 'checkbox' && el.checked ? ' [checked]' : '';
    return kind + (type ? ':' + type : '') + ' "' + own + '"' + (head ? ' in "' + head + '"' : '') + state;
  },

  // Visible is not the same as reachable: a control behind a dialog backdrop,
  // inside a collapsed accordion, or scrolled out of a virtualised list cannot
  // be clicked by a reader, and clicking it proves nothing.
  reachable(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 3 || r.height < 3) return false;
    const mid = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return mid === el || el.contains(mid) || (mid && mid.contains(el));
  },

  // Any overlay that would swallow a click aimed elsewhere.
  overlay() {
    const d = document.querySelector('[role="dialog"], dialog[open], [cmdk-root]');
    return !!d && d.getBoundingClientRect().width > 0;
  },
};
`;

// ---------------------------------------------------------------------------

async function run() {
  const { proc, profile } = await launch();
  const problems = [];
  const seen = new Set();
  const record = (route, control, note) => {
    const key = `${route}|${control}|${note}`;
    if (seen.has(key)) return;
    seen.add(key);
    problems.push({ route, control, note });
  };

  let operated = 0;
  const allLinks = new Map(); // href -> label

  try {
    const version = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
    const cdp = await Cdp.connect(version.webSocketDebuggerUrl);
    const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });

    await cdp.send('Page.enable', {}, sessionId);
    await cdp.send('Runtime.enable', {}, sessionId);
    await cdp.send('Log.enable', {}, sessionId);

    let noise = [];
    cdp.on(sessionId, 'Runtime.exceptionThrown', (p) => {
      const d = p.exceptionDetails;
      noise.push({ kind: 'exception', text: (d.exception?.description ?? d.text ?? '').split('\n')[0] });
    });
    cdp.on(sessionId, 'Runtime.consoleAPICalled', (p) => {
      if (p.type !== 'error' && p.type !== 'warning') return;
      const text = (p.args ?? [])
        .map((a) => a.value ?? a.description ?? a.unserializableValue ?? '')
        .join(' ')
        .trim();
      if (text) noise.push({ kind: p.type, text: text.split('\n')[0].slice(0, 260) });
    });
    cdp.on(sessionId, 'Log.entryAdded', (p) => {
      if (p.entry.level !== 'error') return;
      noise.push({ kind: 'network', text: `${p.entry.text} ${p.entry.url ?? ''}`.trim().slice(0, 260) });
    });

    const evaluate = async (expression) => {
      const r = await cdp.send(
        'Runtime.evaluate',
        { expression, awaitPromise: true, returnByValue: true },
        sessionId,
      );
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
      return r.result.value;
    };

    const goto = async (route) => {
      noise = [];
      await cdp.send('Page.navigate', { url: `${BASE}${route}` }, sessionId);
      await sleep(2600); // lazy chunk + first query
      await evaluate(HELPERS);
    };

    // The tour is offered once and covers the page. A reader who has already
    // seen it should never see it again; setting the key up front makes this
    // run behave like a returning visit.
    await goto('/');
    await evaluate(`try{localStorage.setItem('newspulse.tour.seen.v1','1')}catch(e){};true`);

    // ---- pass 1: controls that do not navigate ----------------------------
    for (const route of ROUTES) {
      await goto(route);
      const mounted = await evaluate(`document.querySelector('#root')?.innerText?.trim().length ?? 0`);
      if (mounted < 60) {
        record(route, '(page load)', `rendered only ${mounted} chars of text`);
        console.log(`  ${route.padEnd(24)} DID NOT MOUNT (${mounted} chars)`);
        continue;
      }
      for (const n of noise) record(route, '(page load)', `${n.kind}: ${n.text}`);

      const links = await evaluate(
        `Array.from(document.querySelectorAll(${JSON.stringify(LINKS)})).map(a => [a.getAttribute('href'), (a.innerText||'').trim().split('\\n')[0]])`,
      );
      for (const [href, label] of links) {
        if (href && !href.startsWith('http') && !href.startsWith('#')) allLinks.set(href, label);
      }

      const controls = await evaluate(`(() => {
        const seen = new Set(); const out = [];
        for (const el of document.querySelectorAll(${JSON.stringify(IN_PLACE)})) {
          if (!window.__ui.reachable(el)) continue;
          const d = window.__ui.describe(el);
          if (seen.has(d)) continue;
          seen.add(d); out.push({ d, type: el.getAttribute('type') || '' });
        }
        return out;
      })()`);

      let done = 0;
      for (const c of controls) {
        const mark = noise.length;
        const outcome = await evaluate(`(() => {
          const els = Array.from(document.querySelectorAll(${JSON.stringify(IN_PLACE)}))
            .filter(e => window.__ui.reachable(e));
          const el = els.find(e => window.__ui.describe(e) === ${JSON.stringify(c.d)});
          if (!el) return 'gone';
          if (el.disabled) return 'disabled';
          if (window.__ui.overlay()) return 'blocked';
          if (el.tagName === 'SELECT') {
            const opt = el.options[1] || el.options[0];
            if (!opt || el.selectedIndex === 0) return 'single-option';
            window.__ui.setValue(el, opt.value); return 'set';
          }
          if (el.type === 'checkbox') { window.__ui.setValue(el, el.checked ? 'false' : 'true'); return 'set'; }
          if (el.type === 'date') { window.__ui.setValue(el, el.min || '2020-01-01'); return 'set'; }
          if (el.type === 'range') { window.__ui.setValue(el, el.value); return 'set'; }
          el.click(); return 'clicked';
        })()`);
        if (outcome === 'gone' || outcome === 'blocked' || outcome === 'single-option') continue;
        done += 1;
        operated += 1;
        await sleep(320);
        if (await evaluate('window.__ui.overlay()')) {
          await evaluate(
            `for (const t of [document, window]) t.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));`,
          );
          await sleep(300);
        }
        for (const n of noise.slice(mark)) record(route, c.d, `${n.kind}: ${n.text}`);
      }

      // ---- hover sweep ------------------------------------------------
      // Every chart in this app draws its tooltip only on hover, and a tooltip
      // formatter that throws is invisible to a screenshot, to the type checker
      // (the cast asserts what it hopes for) and to a click sweep. The treemap
      // one shipped broken for six phases because nothing ever pointed at the
      // chart.
      //
      // The events go through CDP's `Input` domain rather than
      // `dispatchEvent`, because synthetic DOM events are untrusted and ECharts
      // listens on the canvas through zrender, which ignores them. An earlier
      // version of this sweep used `dispatchEvent`, reported "no console
      // errors", and was still catching a deliberately reintroduced bug - so
      // the check has to fire real input or it is decoration.
      const mark = noise.length;
      const [vw, vh] = await evaluate(
        `[window.innerWidth, window.innerHeight, document.body.scrollHeight]`,
      );
      // Scroll as well as sweep: `Input.dispatchMouseEvent` addresses viewport
      // coordinates, so a chart below the fold is simply not there to be
      // hovered. The treemap that started this lives about two screens down,
      // and a sweep of the first screen reported a clean run while the bug sat
      // in it unchanged.
      const step = Math.max(120, Math.floor(vh * 0.8));
      for (let offset = 0; offset < Math.min(vh * 3, 6000); offset += step) {
        await evaluate(`window.scrollTo(0, ${offset}); true`);
        await sleep(120);
        for (let y = 40; y < vh - 20; y += 55) {
          for (let x = 30; x < vw; x += 85) {
            await cdp.send(
              'Input.dispatchMouseEvent',
              { type: 'mouseMoved', x, y, button: 'none', buttons: 0 },
              sessionId,
            );
          }
        }
      }
      await evaluate('window.scrollTo(0, 0); true');
      await sleep(400);
      for (const n of noise.slice(mark)) record(route, '(hover sweep)', `${n.kind}: ${n.text}`);

      const count = problems.filter((p) => p.route === route).length;
      console.log(
        `  ${route.padEnd(24)} ${String(controls.length).padStart(3)} controls, ${String(done).padStart(3)} operated, ${count} problem(s)`,
      );
    }

    // ---- pass 2: every distinct destination the UI links to --------------
    console.log(`\n  ${allLinks.size} distinct link destinations`);
    for (const [href, label] of allLinks) {
      const path = href.split('?')[0].split('#')[0];
      if (ROUTES.includes(href) || ROUTES.includes(path)) continue; // already walked
      noise = [];
      await cdp.send('Page.navigate', { url: `${BASE}${href}` }, sessionId);
      await sleep(2400);
      await evaluate(HELPERS);
      const mounted = await evaluate(`document.querySelector('#root')?.innerText?.trim().length ?? 0`);
      if (mounted < 60) record(href, `(link "${label}")`, 'destination rendered no content');
      else for (const n of noise) record(href, `(link "${label}")`, `${n.kind}: ${n.text}`);
    }

    cdp.close();
  } finally {
    proc.kill();
    await sleep(800); // let Chrome release the profile before removing it
    await rm(profile, { recursive: true, force: true, maxRetries: 5 }).catch(() => {});
  }

  console.log(`\n${operated} controls operated, ${allLinks.size} destinations visited.`);
  if (!problems.length) {
    console.log('No console errors, exceptions or failed requests.');
    return 0;
  }
  console.log(`\n${problems.length} distinct problem(s):\n`);
  for (const p of problems) {
    console.log(`  ${p.route}\n    control: ${p.control}\n    ${p.note}\n`);
  }
  return 1;
}

process.exit(await run());