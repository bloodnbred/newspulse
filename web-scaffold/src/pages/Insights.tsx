import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Download, Filter } from 'lucide-react'
import { useApi } from '../lib/api'
import type { InsightCard, InsightsPayload } from '../types'

/** Each concept appears once. Several cards share "text mining" and "cluster
 *  analysis", and a filter that shows six cards labelled identically helps
 *  nobody choose. */
function conceptsOf(cards: InsightCard[]): { id: string; n: number }[] {
  const counts = new Map<string, number>()
  for (const c of cards) counts.set(c.concept, (counts.get(c.concept) ?? 0) + 1)
  return [...counts.entries()]
    .map(([id, n]) => ({ id, n }))
    .sort((a, b) => b.n - a.n || a.id.localeCompare(b.id))
}

/** Evidence is shown as `key: value`, and values are NOT assumed to be scalars.
 *
 *  `surprise_days` carries `days` as a list of objects and `z_thresholds` as an
 *  object. The first version read them as strings, so three cards printed
 *  "[object Object]" beside a key that looked like a number - which is worse
 *  than printing nothing, because it looks like a figure that failed to format.
 */
function evidenceOf(card: InsightCard): { key: string; value: string }[] {
  return Object.entries(card.evidence ?? {})
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .slice(0, 4)
    .map(([k, v]) => ({ key: k.replace(/_/g, ' '), value: renderValue(v) }))
}

function renderValue(v: unknown): string {
  if (typeof v === 'number') {
    return Number.isInteger(v)
      ? v.toLocaleString('en-IN')
      : v.toLocaleString('en-IN', { maximumFractionDigits: 3 })
  }
  if (typeof v === 'string') return v
  if (Array.isArray(v)) {
    // A list of rows: the count is the fact, and one row is shown so the reader
    // can see what kind of thing is in it.
    const first = v[0]
    if (v.length === 0) return 'none'
    if (first !== null && typeof first === 'object') {
      const keys = Object.keys(first as Record<string, unknown>).slice(0, 3)
      return `${v.length} rows, e.g. ${keys.map((kk) => `${kk} ${String((first as Record<string, unknown>)[kk])}`).join(', ')}…`
    }
    return `${v.length}: ${v.slice(0, 3).map(String).join(', ')}${v.length > 3 ? '…' : ''}`
  }
  if (v && typeof v === 'object') {
    const entries = Object.entries(v as Record<string, unknown>).slice(0, 3)
    return entries.map(([kk, vv]) => `${kk} ${String(vv)}`).join(', ')
  }
  return String(v)
}

/** Where a card's "Open" goes. `page` comes from the insight builder and is a
 *  route, not a label, so it is used as a link and not matched against a map. */
const PAGE_LABEL: Record<string, string> = {
  explore: 'Open in Cube Studio',
  mining: 'Open in Mining Lab',
  events: 'Open in Event Lab',
  stocks: 'Open in Stock 360',
  headline: 'Open in Headline Lab',
}

export default function Insights() {
  const insights = useApi<InsightsPayload>('/ui/insights', {}, { withFilters: false })
  // An object, not a list: `/ui/insights` wraps the cards with a count and a
  // list of anything it withheld. The first version read it as an array and
  // the page rendered blank.
  const cards = insights.data?.data.cards ?? []
  const meta = insights.data?.meta
  const [concept, setConcept] = useState<string | null>(null)

  const concepts = useMemo(() => conceptsOf(cards), [cards])
  const shown = useMemo(
    () => (concept ? cards.filter((c) => c.concept === concept) : cards),
    [cards, concept],
  )

  return (
    <div className="flex flex-col gap-4">
      <header className="glass p-4">
        <h1 className="text-lg font-semibold">Insights</h1>
        <p className="text-sm mt-1" style={{ color: 'var(--muted)', lineHeight: 1.55, maxWidth: '74ch' }}>
          What the mining stages found, with the evidence numbers attached. Every figure here is
          in the report and every report figure is a query — click through to the page that
          produced it and check.
        </p>
      </header>

      {insights.isLoading && <div className="skeleton h-72 rounded-xl" />}
      {insights.error && (
        <p className="text-sm p-3 rounded-lg" style={{ color: 'var(--caution)', background: 'rgba(239,68,68,.08)' }}>
          {(insights.error as { hint?: string }).hint ?? 'Could not load the insights.'}
        </p>
      )}

      {concepts.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <Filter size={12} style={{ color: 'var(--muted)' }} />
          <button
            type="button"
            onClick={() => setConcept(null)}
            aria-pressed={concept === null}
            className="text-xs px-2.5 py-1 rounded-full"
            style={{
              border: `1px solid ${concept === null ? 'var(--news)' : 'rgba(148,163,184,.22)'}`,
              background: concept === null ? 'rgba(20,184,166,.12)' : 'transparent',
              color: concept === null ? 'var(--news)' : 'var(--muted)',
            }}
          >
            All ({cards.length})
          </button>
          {concepts.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => setConcept(concept === c.id ? null : c.id)}
              aria-pressed={concept === c.id}
              className="text-xs px-2.5 py-1 rounded-full"
              style={{
                border: `1px solid ${concept === c.id ? 'var(--news)' : 'rgba(148,163,184,.22)'}`,
                background: concept === c.id ? 'rgba(20,184,166,.12)' : 'transparent',
                color: concept === c.id ? 'var(--news)' : 'var(--muted)',
              }}
            >
              {c.id} ({c.n})
            </button>
          ))}
        </div>
      )}

      {shown.length === 0 && !insights.isLoading && (
        <div className="glass p-6 text-center">
          <p className="text-sm" style={{ color: 'var(--muted)' }}>
            No insights yet. They are produced by the mining stage:
          </p>
          <code className="text-xs mt-2 inline-block px-2 py-1 rounded" style={{ background: 'rgba(0,0,0,.25)', color: 'var(--news)' }}>
            python -m dwm mine all
          </code>
        </div>
      )}

      {/* A CSS masonry: `columns` reflows without a layout library, and the
          cards here are text of genuinely different lengths. */}
      <div className="columns-1 md:columns-2 xl:columns-3 gap-4">
        {shown.map((card) => {
          const evidence = evidenceOf(card)
          return (
            <article
              key={card.id}
              className="glass p-4 mb-4 break-inside-avoid flex flex-col"
              style={{ display: 'inline-block', width: '100%' }}
            >
              <div className="flex items-start justify-between gap-2">
                <span
                  className="text-[10px] px-2 py-0.5 rounded-full border shrink-0"
                  style={{ borderColor: 'var(--price)', color: 'var(--price)' }}
                >
                  {card.concept}
                </span>
                <span
                  className="text-[10px] shrink-0"
                  style={{ color: 'var(--muted)' }}
                  title="The DWM concept this question belongs to"
                >
                  {card.id}
                </span>
              </div>
              <h2 className="text-sm font-semibold mt-2" style={{ lineHeight: 1.4 }}>
                {card.title}
              </h2>
              {/* `caution`, not `text`: the builder writes the card's whole
                  explanation into that field, and it is a caveat - what the
                  figure does not show. Rendering it in body colour and calling
                  it prose would be the one dishonest styling choice on the page. */}
              <p
                className="text-xs mt-2 p-2 rounded-lg"
                style={{ color: 'var(--caution)', background: 'rgba(245,158,11,.07)', lineHeight: 1.6 }}
              >
                {card.caution}
              </p>
              {evidence.length > 0 && (
                <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 mt-3">
                  {evidence.map((e) => (
                    <div key={e.key}>
                      <dt className="text-[9px] uppercase tracking-wider" style={{ color: 'var(--faint)' }}>
                        {e.key}
                      </dt>
                      <dd className="mono text-xs">{e.value}</dd>
                    </div>
                  ))}
                </dl>
              )}
              <div className="flex items-center gap-2 mt-4">
                <Link to={`/${card.page}`} className="btn text-xs">
                  {PAGE_LABEL[card.page] ?? 'Open'} <ArrowRight size={12} />
                </Link>
                <DownloadCard card={card} />
              </div>
            </article>
          )
        })}
      </div>

      {meta?.note && (
        <p className="text-xs" style={{ color: 'var(--caution)', lineHeight: 1.55 }}>
          {meta.note}
        </p>
      )}
    </div>
  )
}

/** Download one card as a standalone PNG.
 *
 *  Built with an SVG foreignObject and a canvas rather than a screenshot
 *  library: the card is text, and a rasteriser that cannot lay out text would
 *  produce an image of the wrong card. If the browser refuses (Safari blocks
 *  canvas tainting in some configurations) the button says so instead of
 *  silently doing nothing.
 */
function DownloadCard({ card }: { card: InsightCard }) {
  const [failed, setFailed] = useState(false)

  const download = async () => {
    try {
      const evidence = evidenceOf(card)
      const rows = evidence
        .map((e) => `<div style="font:11px monospace;color:#94A3B8">${e.key}</div>` +
                    `<div style="font:13px monospace">${e.value}</div>`)
        .join('')
      const html = `<div xmlns="http://www.w3.org/1999/xhtml" style="font:14px system-ui;color:#E6EDF7;background:#111A2E;padding:20px;width:440px;border-radius:12px">
        <div style="font:10px sans-serif;color:#F59E0B;letter-spacing:.08em;text-transform:uppercase">${escapeXml(card.concept)}</div>
        <div style="font:600 15px system-ui;margin:8px 0 6px;line-height:1.4">${escapeXml(card.title)}</div>
        <div style="font:12px system-ui;color:#F59E0B;line-height:1.6">${escapeXml(card.caution)}</div>
        <div style="display:flex;gap:16px;margin-top:12px">${rows}</div>
        <div style="font:10px system-ui;color:#64748B;margin-top:14px">NewsPulse Nifty — associations, not causes. Educational project.</div>
      </div>`
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="480" height="360"><foreignObject width="100%" height="100%">${html}</foreignObject></svg>`
      const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const img = new Image()
      await new Promise<void>((resolve, reject) => {
        img.onload = () => resolve()
        img.onerror = () => reject(new Error('svg did not load'))
        img.src = url
      })
      const canvas = document.createElement('canvas')
      canvas.width = 960
      canvas.height = 720
      const ctx = canvas.getContext('2d')
      if (!ctx) throw new Error('no 2d context')
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
      URL.revokeObjectURL(url)
      const a = document.createElement('a')
      a.href = canvas.toDataURL('image/png')
      a.download = `newspulse-${card.id}.png`
      a.click()
    } catch {
      setFailed(true)
    }
  }

  return (
    <button
      type="button"
      className="icon-btn"
      onClick={() => void download()}
      aria-label={`Download the ${card.id} card as a PNG`}
      title={failed ? 'This browser blocked the canvas export' : 'Download this card as a PNG'}
    >
      <Download size={14} />
    </button>
  )
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}
