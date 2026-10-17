import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { BarChart3, CalendarClock, FileSearch, Users } from 'lucide-react'
import { SECTOR_COLORS } from '../lib/echartsTheme'
import Chart from './Chart'
import type { InsightCard, Meta, SectorHeat } from '../types'

/** Labels for the evidence keys `dwm/inference/insights.py` actually emits.
 *
 *  Without this the card prints the column name as a heading, and the first
 *  insight renders a bare "N" over the number 3 - which is not evidence a
 *  reader can check, it is a fragment of a SQL result. Keys not listed here
 *  fall back to their underscored form, which is ugly but legible. */
const EVIDENCE_LABELS: Record<string, string> = {
  n: 'sessions',
  event: 'event',
  event_type: 'type',
  event_date: 'date',
  sector: 'sector',
  n_stocks: 'stocks',
  t_statistic: 't',
  p_value: 'p',
  mean_car_0_5_pct: 'mean return 0 to 5 days (%)',
  mean_car_pct: 'mean abnormal return (%)',
  ci_low: 'CI low',
  ci_high: 'CI high',
  keywords: 'keywords',
  support: 'support',
  confidence: 'confidence',
  lift: 'lift',
  topics: 'topics',
}

/** The four evidence entries worth the space.
 *
 *  A dict of eight columns in a two-column grid pushes the card's own "Open"
 *  button below the fold of the panel, so the tail is dropped rather than
 *  wrapped. Empty values go first: a key that survived a join with nothing in
 *  it is the least informative thing on the card. */
function evidenceRows(
  evidence: Record<string, unknown> | undefined,
): { key: string; value: unknown }[] {
  return Object.entries(evidence ?? {})
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .slice(0, 4)
    .map(([key, value]) => ({ key, value }))
}

function formatEvidence(v: unknown): string {
  if (typeof v === 'number') return v.toLocaleString('en-IN', { maximumFractionDigits: 4 })
  if (Array.isArray(v)) return v.join(', ')
  return String(v)
}

/** The insight carousel.
 *
 *  Auto-advances and pauses on hover or when the tab is hidden. Auto-advancing
 *  something the reader is mid-way through reading is the fastest way to make
 *  them stop reading, so both conditions stop it, and `prefers-reduced-motion`
 *  stops it entirely. */
export function InsightsCarousel({ cards }: { cards: InsightCard[] }) {
  const [index, setIndex] = useState(0)
  const [paused, setPaused] = useState(false)
  const navigate = useNavigate()

  const reduced =
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

  useEffect(() => {
    if (paused || reduced || cards.length < 2) return
    const id = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        setIndex((i) => (i + 1) % cards.length)
      }
    }, 7000)
    return () => window.clearInterval(id)
  }, [paused, reduced, cards.length])

  if (cards.length === 0) {
    return (
      <div className="glass p-4">
        <div className="text-sm font-semibold">Insights</div>
        <p className="text-sm mt-2" style={{ color: 'var(--muted)' }}>
          None cleared their evidence floor. Run <code>python -m dwm mine all</code>.
        </p>
      </div>
    )
  }
  const card = cards[index]!
  return (
    <div
      className="glass p-4 flex flex-col"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <div className="flex items-center justify-between gap-3">
        <span className="pill" style={{ borderColor: 'var(--mining)' }}>
          {card.concept}
        </span>
        <span className="mono text-xs" style={{ color: 'var(--muted)' }}>
          {index + 1} / {cards.length}
        </span>
      </div>
      <h3 className="text-base font-semibold mt-3" style={{ lineHeight: 1.35 }}>
        {card.title}
      </h3>
      <p className="text-sm mt-2" style={{ color: 'var(--muted)', lineHeight: 1.55 }}>
        {card.text}
      </p>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 mt-4">
        {evidenceRows(card.evidence).map(({ key, value }) => (
          <div key={key}>
            <dt className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--muted)' }}>
              {EVIDENCE_LABELS[key] ?? key.replace(/_/g, ' ')}
            </dt>
            <dd className="mono text-sm">{formatEvidence(value)}</dd>
          </div>
        ))}
      </dl>
      <div className="flex gap-2 mt-4">
        <button className="btn" onClick={() => navigate(`/${card.page}`)}>
          Open
        </button>
        <div className="flex gap-1 ml-auto" role="group" aria-label="Insight navigation">
          {cards.map((c, i) => (
            <button
              key={c.id}
              aria-label={`Insight ${i + 1}: ${c.title}`}
              aria-current={i === index}
              onClick={() => setIndex(i)}
              style={{
                width: 8, height: 8, borderRadius: 999, cursor: 'pointer',
                border: 'none', padding: 0,
                background: i === index ? 'var(--news)' : 'rgba(148,163,184,.35)',
              }}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

/** Sector race: sectors ranked by news volume, per month.
 *
 *  Reuses the headline series already fetched for the Time Machine rather than
 *  fetching a second aggregate for the same numbers - one series, two views. */
export function SectorRace({ rows, meta }: { rows: SectorHeat[]; meta?: Meta }) {
  const top = rows.slice(0, 8)
  const option = useMemo(
    () => ({
      animationDuration: 600,
      grid: { left: 96, right: 24, top: 12, bottom: 28 },
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(11,18,32,.94)',
        borderColor: 'rgba(148,163,184,.25)',
        textStyle: { color: '#E6EDF7', fontSize: 12 },
        valueFormatter: (v: unknown) => Number(v).toLocaleString('en-IN'),
      },
      xAxis: {
        type: 'value',
        axisLabel: { color: 'var(--muted)', fontSize: 10, formatter: (v: number) => `${(v / 1000).toFixed(0)}k` },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,.12)' } },
      },
      yAxis: {
        type: 'category',
        data: top.map((r) => r.sector).reverse(),
        axisLabel: { color: 'var(--muted)', fontSize: 11 },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      series: [
        {
          type: 'bar',
          barWidth: '62%',
          // A flat number with a per-item colour, rather than an object whose
          // `value` carries the label work. The pack's version passed
          // `{value, itemStyle}` and then read `p.value` as if it were the
          // number, which it was not once the object form is used.
          data: top
            .map((r, i) => ({
              value: r.news_count,
              itemStyle: {
                // Rank order is the message, so colour follows position rather
                // than sector identity: a sector that moves from 4th to 6th
                // changes colour, which is the point of a race chart.
                color: SECTOR_COLORS[(top.length - 1 - i) % SECTOR_COLORS.length],
                borderRadius: [0, 4, 4, 0],
              },
            }))
            .reverse(),
          label: {
            show: true,
            position: 'right',
            color: 'var(--muted)',
            fontSize: 10,
            formatter: (p: { value: number }) => Number(p.value).toLocaleString('en-IN'),
          },
        },
      ],
    }),
    [top],
  )

  return (
    <Chart
      title="Sector race"
      caption="Headline-days per sector across the window. Tile colour is a categorical palette, never the news or price hue."
      meta={meta}
      height={280}
      className="lg:col-span-4"
      option={option}
    />
  )
}

const ACTIONS = [
  { to: '/explore', label: 'Build an OLAP view', icon: BarChart3, blurb: 'Slice, dice and drill the sector cube.' },
  { to: '/events', label: 'Study an event', icon: CalendarClock, blurb: 'Abnormal returns around a known date.' },
  { to: '/headline', label: 'Check a headline', icon: FileSearch, blurb: 'Score its writing against our corpus.' },
  { to: '/stocks', label: 'Find similar stocks', icon: Users, blurb: 'Behavioural clusters, not business.' },
]

/** Four routes in, with a hover tilt so they read as buttons. */
export function ActionTiles() {
  const navigate = useNavigate()
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {ACTIONS.map(({ to, label, blurb, icon: Icon }) => (
        <button
          key={to}
          className="glass lift p-4 text-left"
          style={{ cursor: 'pointer' }}
          onClick={() => navigate(to)}
        >
          <Icon size={20} style={{ color: 'var(--news)' }} aria-hidden />
          <div className="text-sm font-semibold mt-3">{label}</div>
          <p className="text-xs mt-1" style={{ color: 'var(--muted)' }}>
            {blurb}
          </p>
        </button>
      ))}
    </div>
  )
}

export default InsightsCarousel
