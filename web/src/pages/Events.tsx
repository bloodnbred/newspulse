import { useCallback, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useSafeBack } from '../hooks/useSafeBack'
import { AlertTriangle, ArrowLeft, Columns2, Layers } from 'lucide-react'
import { useApi } from '../lib/api'
import { fmtNum } from '../lib/format'
import Chart from '../components/Chart'
import type { EventDetail, EventSummary, Meta, SectorReaction } from '../types'
import { SEMANTIC } from '../lib/echartsTheme'

/** Event types, and the hue each gets on the timeline.
 *
 *  These are categorical - a budget is not "more" than a crisis - so they take
 *  hues from the categorical palette and never from `--news`, `--price`,
 *  `--up` or `--down`. The one exception is `--up`/`--down` on the CAR bars,
 *  where the sign is the meaning. */
const TYPE_COLOR: Record<string, string> = {
  budget: '#3B82F6',
  policy: '#A78BFA',
  shock: '#F97316',
  crisis: '#EF4444',
  global: '#22D3EE',
  election: '#FACC15',
  default: '#94A3B8',
}

const typeColor = (t: string): string => TYPE_COLOR[t] ?? TYPE_COLOR.default ?? '#94A3B8'

/** The horizontal timeline.
 *
 *  A real `<button>` per node, in DOM order, so Tab and the arrow keys work
 *  without a keydown handler on a div. Roving tabindex would be tidier; it
 *  would also be the only thing on the page where keyboard order differs from
 *  reading order, which is a poor trade for thirteen buttons. */
function Timeline({
  events, selected, onSelect,
}: {
  events: EventSummary[]
  selected: number | null
  onSelect: (key: number) => void
}) {
  if (events.length === 0) {
    return <p className="text-sm" style={{ color: 'var(--muted)' }}>No events in the window.</p>
  }
  const types = [...new Set(events.map((e) => e.type))]
  return (
    <div>
      <div className="relative overflow-x-auto pb-2">
        <div className="flex items-start gap-0 min-w-max px-2">
          {events.map((e) => {
            const on = e.event_key === selected
            return (
              <button
                key={e.event_key}
                type="button"
                onClick={() => onSelect(e.event_key)}
                aria-pressed={on}
                className="flex flex-col items-center gap-2 px-1"
                style={{ width: 96 }}
                title={`${e.label} — ${e.stocks_studied} stocks studied`}
              >
                <span
                  className="rounded-full"
                  style={{
                    width: on ? 15 : 11,
                    height: on ? 15 : 11,
                    marginTop: on ? 0 : 2,
                    background: typeColor(e.type),
                    boxShadow: on ? `0 0 0 4px ${typeColor(e.type)}33` : 'none',
                    transition: 'all .2s',
                  }}
                />
                <span
                  className="text-[10px] text-center leading-tight"
                  style={{ color: on ? 'var(--text)' : 'var(--muted)', fontWeight: on ? 600 : 400 }}
                >
                  {/* Wide enough for "Union Budget 2020" on one line. At 78px
                      the labels truncated to "Union Budget 20…", which read as
                      the same event twice - four budgets in a row, all ending
                      in an ellipsis. */}
                  {e.label}
                </span>
                <span className="mono text-[9px]" style={{ color: 'var(--faint)' }}>
                  {e.date.slice(0, 7)}
                </span>
              </button>
            )
          })}
        </div>
        <div
          className="absolute left-0 right-0 pointer-events-none"
          style={{ top: 26, height: 1, background: 'rgba(148,163,184,.25)' }}
        />
      </div>
      <div className="flex flex-wrap gap-3 mt-3 pt-3" style={{ borderTop: '1px solid rgba(148,163,184,.12)' }}>
        {types.map((t) => (
          <span key={t} className="text-[10px] inline-flex items-center gap-1" style={{ color: 'var(--muted)' }}>
            <span style={{ width: 8, height: 8, borderRadius: 999, background: typeColor(t) }} />
            {t}
          </span>
        ))}
      </div>
    </div>
  )
}

/** Mean abnormal return by sector, as signed bars.
 *
 *  The count of stocks behind each bar is on the axis label. A sector average
 *  over 3 stocks and one over 12 are not the same claim, and a reader cannot
 *  tell them apart from the bar alone. */
function SectorBars({ rows }: { rows: SectorReaction[] }) {
  const option = useMemo(() => {
    if (rows.length === 0) return null
    const sorted = [...rows].sort((a, b) => a.mean_car_0_5 - b.mean_car_0_5)
    return {
      animationDuration: 550,
      grid: { left: 8, right: 48, top: 8, bottom: 24, containLabel: true },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(11,18,32,.94)',
        borderColor: 'rgba(148,163,184,.25)',
        textStyle: { color: '#E6EDF7', fontSize: 12 },
        formatter: (ps: { name: string; data: number }[]) => {
          const p = ps[0]
          const r = sorted.find((x) => `${x.sector} (${x.n_stocks})` === p?.name)
          if (!r) return ''
          return `<b>${r.sector}</b><br/>${r.n_stocks} stock(s) with a fitted model<br/>mean CAR 0 to 5 sessions: ${r.mean_car_0_5.toFixed(2)}%<br/>0 to 1 session: ${r.mean_car_0_1.toFixed(2)}%<br/>market-model beta: ${r.mean_beta.toFixed(2)}`
        },
      },
      xAxis: {
        type: 'value',
        axisLabel: { color: 'var(--muted)', fontSize: 10, formatter: (v: number) => `${v.toFixed(0)}%` },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,.10)' } },
      },
      yAxis: {
        type: 'category',
        data: sorted.map((r) => `${r.sector} (${r.n_stocks})`),
        axisLabel: { color: 'var(--muted)', fontSize: 10 },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      series: [
        {
          type: 'bar',
          barMaxWidth: 14,
          data: sorted.map((r) => ({
            value: r.mean_car_0_5,
            itemStyle: {
              color: r.mean_car_0_5 >= 0 ? SEMANTIC.up : SEMANTIC.down,
              borderRadius: 3,
            },
          })),
          label: {
            show: true,
            position: 'right',
            fontSize: 10,
            color: 'var(--muted)',
            // `p.data` is the item OBJECT here, because each bar carries an
            // `itemStyle`; `Number({})` is NaN, and the first render printed
            // "NaN%" on every bar.
            formatter: (p: { value: number }) => `${Number(p.value).toFixed(1)}%`,
          },
        },
      ],
    }
  }, [rows])

  if (!option) {
    return (
      <p className="text-sm py-8 text-center" style={{ color: 'var(--muted)' }}>
        No sector had a fitted market model for this event.
      </p>
    )
  }
  return <Chart option={option} height={Math.max(260, rows.length * 30)} title="" caption="" />
}

/** Headline volume in the ten sessions either side of the event.
 *
 *  The event session is marked, not coloured by value: the question this chart
 *  answers is "was there more news around the event", and a colour scale on the
 *  bars would answer a different one while hiding whether the event day itself
 *  was busier than the days around it. */
function NewsAround({ rows, eventDate }: { rows: EventDetail['news_around']; eventDate: string | null }) {
  const option = useMemo(() => {
    if (rows.length === 0) return null
    return {
      animationDuration: 400,
      grid: { left: 8, right: 16, top: 18, bottom: 24, containLabel: true },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(11,18,32,.94)',
        borderColor: 'rgba(148,163,184,.25)',
        textStyle: { color: '#E6EDF7', fontSize: 12 },
        formatter: (ps: { name: string; data: number; dataIndex: number }[]) => {
          const p = ps[0]
          if (!p) return ''
          const off = rows[p.dataIndex]?.offset ?? 0
          const label = off === 0 ? 'the event session' : `${Math.abs(off)} session(s) ${off < 0 ? 'before' : 'after'}`
          return `<b>${p.name}</b><br/>${label}<br/>${fmtNum(p.data)} headlines, market reports excluded`
        },
      },
      xAxis: {
        type: 'category',
        data: rows.map((r) => r.offset),
        axisLabel: {
          color: 'var(--muted)',
          fontSize: 9,
          formatter: (v: string) => (Number(v) === 0 ? 'event' : Number(v) > 0 ? `+${v}` : `${v}`),
        },
        axisLine: { lineStyle: { color: 'rgba(148,163,184,.25)' } },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: 'var(--muted)', fontSize: 10 },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,.10)' } },
      },
      series: [
        {
          type: 'bar',
          barMaxWidth: 18,
          data: rows.map((r) => ({
            value: r.news,
            itemStyle: {
              color: r.offset === 0 ? SEMANTIC.news : 'rgba(20,184,166,.38)',
              borderRadius: 3,
            },
          })),
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { color: 'rgba(245,158,11,.6)', type: 'dashed' },
            label: {
              formatter: 'mean of the 20 sessions',
              color: 'var(--caution)',
              fontSize: 9,
              position: 'insideEndTop',
            },
            data: [
              {
                yAxis: Math.round(
                  rows.reduce((s, r) => s + r.news, 0) / Math.max(1, rows.length),
                ),
              },
            ],
          },
        },
      ],
    }
  }, [rows])

  void eventDate
  if (!option) {
    return <p className="text-sm py-8 text-center" style={{ color: 'var(--muted)' }}>No sessions to show.</p>
  }
  return <Chart option={option} height={220} title="" caption="" />
}

/* ---------------------------------------------------------------------------
 * The page
 * ------------------------------------------------------------------------ */

export default function Events() {
  const { eventKey } = useParams<{ eventKey?: string }>()
  const navigate = useNavigate()
  // `navigate(-1)` alone leaves the app entirely when the page was opened
  // from a shared link rather than from inside it.
  const goBack = useSafeBack('/')
  const [selected, setSelected] = useState<number | null>(eventKey ? Number(eventKey) : null)
  const [compareWith, setCompareWith] = useState<number | null>(null)
  const [sortDesc, setSortDesc] = useState(true)

  const timeline = useApi<{ events: EventSummary[] }>('/ui/events', {}, { withFilters: false })
  const events = timeline.data?.data.events ?? []

  // Arrive with nothing selected and the page is a strip of dots; pick the
  // biggest study so the first thing on screen is a real result.
  const active = selected ?? events[0]?.event_key ?? null
  const detail = useApi<EventDetail>(`/ui/events/${active}`, {}, {
    withFilters: false,
    enabled: active !== null,
  })
  const compare = useApi<EventDetail>(`/ui/events/${compareWith}`, {}, {
    withFilters: false,
    enabled: compareWith !== null,
  })

  const choose = useCallback((key: number) => {
    setSelected(key)
    setCompareWith(null)
    navigate(`/events/${key}`, { replace: true })
  }, [navigate])

  const d = detail.data?.data
  const meta: Meta | undefined = detail.data?.meta
  const sortedStocks = useMemo(() => {
    const rows = [...(d?.by_stock ?? [])]
    rows.sort((a, b) => (sortDesc ? b.car_0_5 - a.car_0_5 : a.car_0_5 - b.car_0_5))
    return rows
  }, [d, sortDesc])

  return (
    <div className="flex flex-col gap-4">
      <header className="glass p-4">
        <button type="button" className="btn mb-3" onClick={goBack}>
          <ArrowLeft size={13} /> Back
        </button>
        <h1 className="text-lg font-semibold">Event Lab</h1>
        <p className="text-sm mt-1" style={{ color: 'var(--muted)', lineHeight: 1.55, maxWidth: '70ch' }}>
          Thirteen dated events, and how the market moved around each one. Abnormal return is the
          move beyond what the market model explains — it <b>coincides with</b> the event and does
          not show the event caused it. Every other thing that happened that day is inside the
          same number.
        </p>
      </header>

      <section className="glass p-4">
        <div className="flex items-center justify-between gap-2 mb-3">
          <h2 className="text-sm font-semibold">Timeline</h2>
          {d && (
            <button
              type="button"
              className="text-xs px-2.5 py-1 rounded-full border inline-flex items-center gap-1"
              style={{
                borderColor: compareWith ? 'var(--mining)' : 'rgba(148,163,184,.22)',
                background: compareWith ? 'rgba(139,92,246,.14)' : 'transparent',
                color: compareWith ? 'var(--mining)' : 'var(--muted)',
              }}
              onClick={() => {
                const other = events.find((e) => e.event_key !== active)
                setCompareWith((c) => (c ? null : other?.event_key ?? null))
              }}
              disabled={events.length < 2}
            >
              <Columns2 size={12} /> Compare with {compareWith ? '…' : 'another event'}
            </button>
          )}
        </div>
        {timeline.isLoading && <div className="skeleton h-24 rounded-xl" />}
        {timeline.data && (
          <Timeline events={events} selected={active} onSelect={choose} />
        )}
      </section>

      {compareWith && compare.data && (
        <ComparePanel
          left={d}
          right={compare.data.data}
          leftLabel={d?.event.label ?? ''}
          rightLabel={compare.data.data.event.label}
        />
      )}

      <div className="grid gap-4 lg:grid-cols-12">
        <div className="lg:col-span-6 glass p-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-sm font-semibold">{d?.event.label ?? 'Select an event'}</h2>
            {d && (
              <span className="text-xs px-2 py-0.5 rounded" style={{ background: `${typeColor(d.event.type)}22`, color: typeColor(d.event.type) }}>
                {d.event.type}
              </span>
            )}
          </div>
          {d?.event.session && (
            <p className="text-xs mt-1" style={{ color: 'var(--muted)' }}>
              announced {d.event.date}, measured from the trading session on {d.event.session}
              {d.event.session !== d.event.date ? ' (the next session, since the date was not a trading day)' : ''}
            </p>
          )}
          {d && d.nearby_events.length > 0 && (
            <p
              className="text-xs mt-2 p-2 rounded-lg flex items-start gap-1.5"
              style={{ background: 'rgba(245,158,11,.10)', color: 'var(--caution)' }}
            >
              <AlertTriangle size={12} style={{ flexShrink: 0, marginTop: 2 }} />
              <span>
                {d.nearby_events.map((n) => `${n.label} (${n.days_apart}d)`).join(', ')}{' '}
                {d.nearby_events.length === 1 ? 'falls' : 'fall'} inside the five-session window.
                Two events this close cannot be told apart in the number below — the move is
                theirs together, not this one's.
              </span>
            </p>
          )}
          <div className="mt-3">
            <h3 className="text-xs uppercase tracking-widest mb-1" style={{ color: 'var(--muted)' }}>
              Mean abnormal return by sector
            </h3>
            {detail.isLoading && <div className="skeleton h-64 rounded-xl" />}
            <SectorBars rows={d?.by_sector ?? []} />
          </div>
          {meta?.note && (
            <p className="text-xs mt-2" style={{ color: 'var(--caution)', lineHeight: 1.55 }}>
              {meta.note}
            </p>
          )}
        </div>

        <div className="lg:col-span-6 flex flex-col gap-4">
          <div className="glass p-4">
            <h3 className="text-xs uppercase tracking-widest mb-1" style={{ color: 'var(--muted)' }}>
              News volume around it
            </h3>
            <NewsAround rows={d?.news_around ?? []} eventDate={d?.event.session ?? null} />
          </div>

          <div className="glass p-4">
            <h3 className="text-xs uppercase tracking-widest mb-2" style={{ color: 'var(--muted)' }}>
              Headlines on the session
            </h3>
            <p className="text-xs mb-2" style={{ color: 'var(--caution)' }}>
              The 50 most sensational of the day, not a representative set — and not necessarily
              about the event.
            </p>
            <ul className="flex flex-col gap-1.5 overflow-auto" style={{ maxHeight: 220 }}>
              {(d?.headlines ?? []).map((h) => (
                <li key={h.headline_id} className="text-sm">
                  <span className="text-[10px] px-1.5 py-0.5 rounded mr-2" style={{ background: 'rgba(148,163,184,.14)', color: 'var(--muted)' }}>
                    {h.topic}
                  </span>
                  {h.headline_text}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>

      {/* ------------------------------------------ leaderboard ----------- */}
      <section className="glass p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2 mb-2">
          <h2 className="text-sm font-semibold">
            Every stock with a fitted model ({sortedStocks.length})
          </h2>
          <button
            type="button"
            className="text-xs px-2.5 py-1 rounded-full border"
            style={{
              borderColor: 'rgba(148,163,184,.22)',
              color: 'var(--muted)',
            }}
            onClick={() => setSortDesc((s) => !s)}
          >
            {sortDesc ? 'Biggest fall first ▲' : 'Biggest rise first ▼'}
          </button>
        </div>
        <p className="text-xs mb-3" style={{ color: 'var(--muted)' }}>
          The whole valid set, not just the extremes — a leaderboard of only the movers cannot
          show that most stocks did not move.
          {d && d.excluded.length > 0 && (
            <>
              {' '}
              <span style={{ color: 'var(--caution)' }}>
                {d.excluded.length} stock(s) were excluded for too little pre-event history:
                {d.excluded.slice(0, 3).map((x) => `${x.symbol} (${x.reason})`).join('; ')}
                {d.excluded.length > 3 ? '…' : ''}
              </span>
            </>
          )}
        </p>
        <div className="overflow-auto" style={{ maxHeight: 420 }}>
          <table className="w-full text-xs">
            <thead>
              <tr style={{ color: 'var(--muted)' }}>
                <th className="text-left py-1.5">stock</th>
                <th className="text-left">sector</th>
                <th className="text-right">CAR 0→5</th>
                <th className="text-right">CAR 0→1</th>
                <th className="text-right">β</th>
                <th className="text-right">R²</th>
              </tr>
            </thead>
            <tbody className="mono">
              {sortedStocks.map((r) => (
                <tr key={r.symbol} style={{ borderTop: '1px solid rgba(148,163,184,.08)' }}>
                  <td className="py-1.5">
                    <Link to={`/stocks/${r.symbol}`} style={{ color: 'var(--news)' }}>{r.symbol}</Link>
                  </td>
                  <td style={{ color: 'var(--muted)' }}>{r.sector}</td>
                  <td className="text-right" style={{ color: r.car_0_5 >= 0 ? 'var(--up)' : 'var(--down)' }}>
                    {r.car_0_5 >= 0 ? '+' : ''}{r.car_0_5.toFixed(2)}%
                  </td>
                  <td className="text-right" style={{ color: 'var(--muted)' }}>
                    {r.car_0_1 >= 0 ? '+' : ''}{r.car_0_1.toFixed(2)}%
                  </td>
                  <td className="text-right" style={{ color: 'var(--muted)' }}>{r.beta.toFixed(2)}</td>
                  <td className="text-right" style={{ color: 'var(--muted)' }}>{r.r_squared.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}

/** Two events side by side.
 *
 *  The two panels are drawn from two separate studies of two separate events.
 *  There is no test of whether their differences are significant, so nothing
 *  here claims that one produced a bigger reaction than the other — the reader
 *  is shown two numbers and told what each one is. */
function ComparePanel({
  left, right, leftLabel, rightLabel,
}: {
  left: EventDetail | undefined
  right: EventDetail
  leftLabel: string
  rightLabel: string
}) {
  return (
    <section className="glass p-4">
      <h2 className="text-sm font-semibold flex items-center gap-2">
        <Layers size={14} /> Side by side
      </h2>
      <p className="text-xs mt-1 mb-3" style={{ color: 'var(--caution)' }}>
        Two independent studies of two different events. Nothing here tests whether the two
        reactions differ by more than chance — the bars are the numbers, not a verdict.
      </p>
      <div className="grid gap-4 md:grid-cols-2">
        {([[left, leftLabel], [right, rightLabel]] as const).map(([d, label]) => (
          <div key={label} className="rounded-xl p-3" style={{ background: 'rgba(255,255,255,.02)' }}>
            <h3 className="text-sm font-semibold">{label}</h3>
            <p className="text-xs mb-2" style={{ color: 'var(--muted)' }}>
              {d?.event.date} · {d?.by_stock.length ?? 0} stocks with a fitted model
            </p>
            <table className="w-full text-[11px]">
              <tbody className="mono">
                <tr>
                  <td style={{ color: 'var(--muted)' }}>sectors down most</td>
                  <td className="text-right">
                    {[...(d?.by_sector ?? [])]
                      .sort((a, b) => a.mean_car_0_5 - b.mean_car_0_5)
                      .slice(0, 1)
                      .map((r) => `${r.sector} ${r.mean_car_0_5.toFixed(1)}%`)
                      .join('') || '—'}
                  </td>
                </tr>
                <tr>
                  <td style={{ color: 'var(--muted)' }}>sectors up most</td>
                  <td className="text-right">
                    {[...(d?.by_sector ?? [])]
                      .sort((a, b) => b.mean_car_0_5 - a.mean_car_0_5)
                      .slice(0, 1)
                      .map((r) => `${r.sector} ${r.mean_car_0_5 >= 0 ? '+' : ''}${r.mean_car_0_5.toFixed(1)}%`)
                      .join('') || '—'}
                  </td>
                </tr>
                <tr>
                  <td style={{ color: 'var(--muted)' }}>worst single stock</td>
                  <td className="text-right">
                    {[...(d?.by_stock ?? [])]
                      .sort((a, b) => a.car_0_5 - b.car_0_5)
                      .slice(0, 1)
                      .map((r) => `${r.symbol} ${r.car_0_5.toFixed(1)}%`)
                      .join('') || '—'}
                  </td>
                </tr>
                <tr>
                  <td style={{ color: 'var(--muted)' }}>best single stock</td>
                  <td className="text-right">
                    {[...(d?.by_stock ?? [])]
                      .sort((a, b) => b.car_0_5 - a.car_0_5)
                      .slice(0, 1)
                      .map((r) => `${r.symbol} ${r.car_0_5 >= 0 ? '+' : ''}${r.car_0_5.toFixed(1)}%`)
                      .join('') || '—'}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </section>
  )
}
