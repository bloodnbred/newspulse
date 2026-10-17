import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { AlertTriangle, ArrowLeft, Layers, TrendingDown, TrendingUp } from 'lucide-react'
import { useApi } from '../lib/api'
import { fmtNum } from '../lib/format'
import Chart from '../components/Chart'
import type {
  EventReaction, Meta, RadarAxis, Stock360, StockHeadline, StockSession,
} from '../types'
import { SEMANTIC } from '../lib/echartsTheme'

/* ---------------------------------------------------------------------------
 * The price chart
 * ------------------------------------------------------------------------ */

/** Candlestick + volume + mention markers, or the rebased pair.
 *
 *  Two grids rather than one: price on top, volume beneath, sharing an x axis
 *  and a single `dataZoom` so dragging either one moves both. Two separate
 *  charts with two brushes would let a reader compare a zoomed price against an
 *  unzoomed volume, which is how a "volume spiked" impression gets invented.
 */
function PriceChart({
  series, rebase, mentionCap,
}: {
  series: StockSession[]
  rebase: boolean
  /** The busiest day in the window, used to size the marker. */
  mentionCap: number
}) {
  const option = useMemo(() => {
    const dates = series.map((p) => p.date)
    // ECharts candlestick order is [open, close, low, high].
    const candles = series.map((p) => [p.open, p.close, p.low, p.high])
    const volumes = series.map((p, i) => ({
      value: p.volume,
      itemStyle: { color: p.close >= p.open ? 'rgba(16,185,129,.55)' : 'rgba(239,68,68,.55)' },
      name: dates[i],
    }))
    // Mention markers ride on the price series at the bar's high, so they sit
    // above the candle rather than on top of it.
    const mentionPoints = series
      .map((p, i) => ({ p, i }))
      .filter(({ p }) => (p.mentions ?? 0) > 0)
      .map(({ p, i }) => ({
        name: dates[i] ?? '',
        coord: [i, rebase ? p.stock_rebased : p.high],
        value: p.mentions,
      }))

    return {
      animation: false,
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        backgroundColor: 'rgba(11,18,32,.94)',
        borderColor: 'rgba(148,163,184,.25)',
        textStyle: { color: '#E6EDF7', fontSize: 12 },
      },
      grid: [
        { left: 8, right: 56, top: 12, height: rebase ? 250 : 210 },
        { left: 8, right: 56, top: rebase ? 292 : 252, height: 58 },
      ],
      xAxis: [
        {
          type: 'category',
          data: dates,
          gridIndex: 0,
          axisLabel: { show: false },
          axisLine: { lineStyle: { color: 'rgba(148,163,184,.25)' } },
        },
        {
          type: 'category',
          data: dates,
          gridIndex: 1,
          axisLabel: { color: 'var(--muted)', fontSize: 9 },
          axisLine: { lineStyle: { color: 'rgba(148,163,184,.25)' } },
        },
      ],
      yAxis: [
        {
          scale: true,
          gridIndex: 0,
          axisLabel: {
            color: 'var(--muted)',
            fontSize: 10,
            formatter: (v: number) => (rebase ? v.toFixed(0) : v.toFixed(0)),
          },
          splitLine: { lineStyle: { color: 'rgba(148,163,184,.10)' } },
        },
        {
          scale: true,
          gridIndex: 1,
          splitNumber: 2,
          axisLabel: { color: 'var(--muted)', fontSize: 9, formatter: fmtCompactVolume },
          splitLine: { show: false },
        },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
        {
          type: 'slider',
          xAxisIndex: [0, 1],
          bottom: 4,
          height: 20,
          start: 0,
          end: 100,
          borderColor: 'rgba(148,163,184,.2)',
          fillerColor: 'rgba(20,184,166,.12)',
          handleStyle: { color: SEMANTIC.news },
          textStyle: { color: 'var(--muted)', fontSize: 9 },
        },
      ],
      series: rebase
        ? [
            {
              name: 'Stock, rebased to 100',
              type: 'line',
              xAxisIndex: 0,
              yAxisIndex: 0,
              data: series.map((p) => p.stock_rebased),
              showSymbol: false,
              lineStyle: { width: 2, color: SEMANTIC.news },
              itemStyle: { color: SEMANTIC.news },
            },
            {
              name: 'Nifty 50, rebased to 100',
              type: 'line',
              xAxisIndex: 0,
              yAxisIndex: 0,
              data: series.map((p) => p.nifty_rebased),
              showSymbol: false,
              lineStyle: { width: 2, color: SEMANTIC.price },
              itemStyle: { color: SEMANTIC.price },
            },
            {
              name: 'Volume',
              type: 'bar',
              xAxisIndex: 1,
              yAxisIndex: 1,
              data: volumes,
            },
          ]
        : [
            {
              name: 'Price',
              type: 'candlestick',
              xAxisIndex: 0,
              yAxisIndex: 0,
              data: candles,
              itemStyle: {
                color: 'rgba(16,185,129,.85)',
                color0: 'rgba(239,68,68,.85)',
                borderColor: 'rgba(16,185,129,1)',
                borderColor0: 'rgba(239,68,68,1)',
              },
              markPoint: mentionPoints.length
                ? {
                    symbol: 'circle',
                    symbolSize: (d: { value: number }) =>
                      4 + Math.min(6, Math.sqrt((d.value ?? 0) / Math.max(1, mentionCap)) * 6),
                    itemStyle: { color: SEMANTIC.mining, opacity: 0.85 },
                    label: { show: false },
                    data: mentionPoints,
                    tooltip: {
                      formatter: (p: { name: string; value: number }) =>
                        `${p.name}<br/>${p.value} headline(s) named this company`,
                    },
                  }
                : undefined,
            },
            { name: 'Volume', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volumes },
          ],
    }
  }, [series, rebase, mentionCap])

  return <Chart option={option} height={340} title="" caption="" />
}

const fmtCompactVolume = (v: number): string => {
  const abs = Math.abs(v)
  if (abs >= 1e7) return `${(v / 1e7).toFixed(1)}Cr`
  if (abs >= 1e5) return `${(v / 1e5).toFixed(1)}L`
  return String(Math.round(v))
}

/* ---------------------------------------------------------------------------
 * The peer radar
 * ------------------------------------------------------------------------ */

/** This stock against the average of its cluster, on six axes.
 *
 *  Each arm is scaled to that axis's own spread across all 47 stocks, so a
 *  point at the edge means "the most extreme value in this study" on every arm
 *  alike. Scaling all six against one shared min/max - the obvious approach -
 *  collapses four of them onto the centre, because mention rate runs to 330
 *  while news sensitivity sits near zero, and the polygon becomes a vertical
 *  spike that looks like a shape and means nothing. The percentile under each
 *  arm is the honest reading of a point's distance from the centre.
 */
function PeerRadar({ axes }: { axes: RadarAxis[] }) {
  const option = useMemo(() => {
    const usable = axes.filter(
      (a) => a.value !== null && a.cluster_mean !== null && a.pop_min !== null && a.pop_max !== null,
    )
    if (usable.length === 0) return null
    // Each axis against its own population bounds, clamped: the stock itself is
    // in the population, so its value can never fall outside, but a cluster
    // mean could in principle if the population shifted under it.
    const norm = (v: number, a: RadarAxis) => {
      const lo = a.pop_min as number
      const hi = a.pop_max as number
      const span = hi - lo
      if (span <= 0) return 0.5
      return Math.max(0, Math.min(1, (v - lo) / span))
    }
    return {
      animationDuration: 500,
      tooltip: {
        backgroundColor: 'rgba(11,18,32,.94)',
        borderColor: 'rgba(148,163,184,.25)',
        textStyle: { color: '#E6EDF7', fontSize: 12 },
      },
      legend: {
        bottom: 0,
        textStyle: { color: 'var(--muted)', fontSize: 10 },
        itemWidth: 10,
        itemHeight: 10,
      },
      radar: {
        indicator: usable.map((a) => ({ name: shortAxis(a.label), max: 1 })),
        radius: '60%',
        axisName: { color: 'var(--muted)', fontSize: 9 },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,.14)' } },
        splitArea: { areaStyle: { color: ['rgba(255,255,255,.015)', 'transparent'] } },
        axisLine: { lineStyle: { color: 'rgba(148,163,184,.14)' } },
      },
      series: [
        {
          type: 'radar',
          data: [
            {
              name: 'This stock',
              value: usable.map((a) => norm(a.value as number, a)),
              lineStyle: { color: SEMANTIC.news, width: 2 },
              itemStyle: { color: SEMANTIC.news },
              areaStyle: { color: 'rgba(20,184,166,.20)' },
            },
            {
              name: 'Cluster average',
              value: usable.map((a) => norm(a.cluster_mean as number, a)),
              lineStyle: { color: SEMANTIC.price, width: 2, type: 'dashed' },
              itemStyle: { color: SEMANTIC.price },
              areaStyle: { color: 'rgba(245,158,11,.10)' },
            },
          ],
        },
      ],
    }
  }, [axes])

  if (!option) {
    return (
      <p className="text-sm py-6 text-center" style={{ color: 'var(--muted)' }}>
        This stock is not in the clustering. Run <code>python -m dwm mine --op stockclusters</code>.
      </p>
    )
  }

  return <Chart option={option} height={280} title="" caption="" />
}

function shortAxis(label: string): string {
  return label
    .replace('Annualised volatility', 'Volatility')
    .replace('Beta vs the Nifty', 'Beta')
    .replace('Max drawdown', 'Drawdown')
    .replace('Mention rate', 'Mentions')
    .replace('Mention sentiment', 'Sentiment')
    .replace('News sensitivity', 'News sens.')
}

/* ---------------------------------------------------------------------------
 * The page
 * ------------------------------------------------------------------------ */

export default function Stocks() {
  const { symbol } = useParams<{ symbol: string }>()
  const navigate = useNavigate()
  const [rebase, setRebase] = useState(false)

  const sym = (symbol ?? 'RELIANCE').toUpperCase()
  const stock = useApi<Stock360>(`/ui/stocks/${sym}`, {}, { withFilters: false })
  const headlines = useApi<StockHeadline[]>(`/ui/stocks/${sym}/headlines`, { limit: 50 }, { withFilters: false })

  const data = stock.data?.data
  const profile = data?.profile
  const last = data?.last ?? null
  const meta: Meta | undefined = stock.data?.meta

  const mentionCap = useMemo(
    () => Math.max(1, ...(data?.series ?? []).map((p) => p.mentions ?? 0)),
    [data],
  )

  const shown = headlines.data?.data ?? []

  return (
    <div className="flex flex-col gap-4">
      {/* ------------------------------------------------ header ----------- */}
      <header className="glass p-4">
        <button type="button" className="btn mb-3" onClick={() => navigate(-1)}>
          <ArrowLeft size={13} /> Back
        </button>
        {stock.isLoading && <div className="skeleton h-24 rounded-xl" />}
        {stock.error && (
          <p className="text-sm p-3 rounded-lg" style={{ color: 'var(--caution)', background: 'rgba(239,68,68,.08)' }}>
            {(stock.error as { hint?: string }).hint ?? 'Could not load this company.'}
          </p>
        )}
        {profile && (
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <h1 className="text-xl font-semibold">{profile.company_name}</h1>
              <div className="flex flex-wrap items-center gap-2 mt-2">
                <span className="mono text-xs px-2 py-0.5 rounded" style={{ background: 'rgba(148,163,184,.14)' }}>
                  {profile.symbol}
                </span>
                {profile.sector && (
                  <span className="text-xs px-2 py-0.5 rounded" style={{ background: 'rgba(20,184,166,.14)', color: 'var(--news)' }}>
                    {profile.sector}
                  </span>
                )}
                {data?.cluster.cluster_id !== null && data?.cluster.cluster_id !== undefined && (
                  <span
                    className="text-xs px-2 py-0.5 rounded inline-flex items-center gap-1"
                    style={{ background: 'rgba(139,92,246,.16)', color: 'var(--mining)' }}
                    title="Behavioural cluster from a PCA over volatility, beta, drawdown and news measures. Similar trading, not similar business."
                  >
                    <Layers size={11} /> cluster {data.cluster.cluster_id}
                  </span>
                )}
                {profile.isin && (
                  <span className="mono text-xs" style={{ color: 'var(--muted)' }}>{profile.isin}</span>
                )}
              </div>
              {!profile.is_survivor && (
                <p className="text-xs mt-2 flex items-center gap-1" style={{ color: 'var(--caution)' }}>
                  <AlertTriangle size={12} />
                  Listed after the window opened, so most of the period has no price for it.
                </p>
              )}
            </div>
            {last && (
              <div className="text-right">
                <div className="mono text-2xl font-semibold">
                  {fmtNum(last.close, 2)}
                </div>
                <div
                  className="mono text-sm inline-flex items-center gap-1"
                  style={{ color: (last.return_pct ?? 0) >= 0 ? 'var(--up)' : 'var(--down)' }}
                >
                  {(last.return_pct ?? 0) >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                  {(last.return_pct ?? 0) >= 0 ? '+' : ''}
                  {(last.return_pct ?? 0).toFixed(2)}%
                  {data?.change_abs !== null && data?.change_abs !== undefined && (
                    <span style={{ color: 'var(--muted)' }}>
                      ({data.change_abs >= 0 ? '+' : ''}{data.change_abs.toFixed(2)})
                    </span>
                  )}
                </div>
                <div className="text-[11px]" style={{ color: 'var(--muted)' }}>
                  close on {last.date}
                </div>
              </div>
            )}
          </div>
        )}
      </header>

      {/* ------------------------------------------------ main ------------ */}
      <div className="grid gap-4 lg:grid-cols-12">
        <div className="lg:col-span-8 glass p-4 flex flex-col">
          <div className="flex items-center justify-between gap-2 mb-2">
            <h2 className="text-sm font-semibold">
              {rebase ? 'Rebased against the Nifty' : 'Price and volume'}
            </h2>
            <button
              type="button"
              onClick={() => setRebase((r) => !r)}
              aria-pressed={rebase}
              className="text-xs px-2.5 py-1 rounded-full border"
              style={{
                borderColor: rebase ? 'var(--news)' : 'rgba(148,163,184,.22)',
                background: rebase ? 'rgba(20,184,166,.12)' : 'transparent',
                color: rebase ? 'var(--news)' : 'var(--muted)',
              }}
            >
              Rebased vs Nifty
            </button>
          </div>
          {stock.isLoading && <div className="skeleton h-80 rounded-xl" />}
          {data && data.series.length > 0 && (
            <PriceChart series={data.series} rebase={rebase} mentionCap={mentionCap} />
          )}
          {data && data.series.length === 0 && (
            <p className="text-sm py-10 text-center" style={{ color: 'var(--muted)' }}>
              No sessions for this company inside the window.
            </p>
          )}
          <p className="text-xs mt-2" style={{ color: 'var(--muted)', lineHeight: 1.55 }}>
            {rebase
              ? 'Both series start at 100 on the first session of the window, so this shows how the stock moved against the index — not what it is worth. The gap between the lines at any date is the relative move.'
              : 'Candles are daily open/close with the high and low as the wick. A violet dot marks a session where headlines named this company; a bigger dot means more headlines. Volume is on the lower grid and shares the brush, so the two are always the same window.'}
          </p>
          {meta?.note && (
            <p className="text-xs mt-2" style={{ color: 'var(--caution)', lineHeight: 1.55 }}>
              {meta.note}
            </p>
          )}
        </div>

        {/* ------------------------------------------ headlines ---------- */}
        <div className="lg:col-span-4 glass p-4 flex flex-col">
          <div className="flex items-baseline justify-between gap-2">
            <h2 className="text-sm font-semibold">Headlines naming it</h2>
            {data && (
              <span className="text-xs" style={{ color: 'var(--muted)' }}>
                {fmtNum(data.totals.mentions)} in window
              </span>
            )}
          </div>
          <p className="text-xs mt-1 mb-2" style={{ color: 'var(--muted)' }}>
            The {shown.length} most recent, capped at 50 by the API. Named by name — a mention
            is not a story about the company.
          </p>
          {headlines.isLoading && <div className="skeleton h-64 rounded-xl" />}
          <ul className="flex flex-col gap-2 overflow-auto" style={{ maxHeight: 420 }}>
            {(headlines.data?.data ?? []).map((h) => (
              <li key={h.headline_id} className="text-sm flex gap-2">
                <span
                  title={`sentiment ${h.sentiment_compound?.toFixed(2)}`}
                  style={{
                    width: 6, borderRadius: 3, flexShrink: 0, marginTop: 5,
                    background:
                      (h.sentiment_compound ?? 0) > 0.05 ? 'var(--up)'
                        : (h.sentiment_compound ?? 0) < -0.05 ? 'var(--down)' : 'var(--muted)',
                  }}
                />
                <span>
                  <span className="mono text-[10px] mr-2" style={{ color: 'var(--muted)' }}>
                    {h.publish_date}
                  </span>
                  {h.headline_text}
                  {h.is_ambiguous_alias && (
                    <span
                      className="ml-2 text-[10px] px-1.5 py-0.5 rounded"
                      style={{ background: 'rgba(245,158,11,.14)', color: 'var(--caution)' }}
                      title={`Matched on the alias "${h.matched_alias}", which also names other companies.`}
                    >
                      ambiguous alias
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
          <Link to="/headline" className="text-xs mt-3" style={{ color: 'var(--news)' }}>
            Analyse a headline of your own →
          </Link>
        </div>
      </div>

      {/* ------------------------------------------------ below ----------- */}
      <div className="grid gap-4 lg:grid-cols-12">
        <div className="lg:col-span-4 glass p-4">
          <h2 className="text-sm font-semibold">Against its cluster</h2>
          <p className="text-xs mt-1 mb-2" style={{ color: 'var(--muted)' }}>
            Each arm is scaled to its own spread across all 47 stocks, so the edge means
            "most extreme here" on every arm alike. Behaviour, not business.
          </p>
          <PeerRadar axes={data?.cluster.axes ?? []} />
          {data && data.cluster.axes.length > 0 && (
            <table className="w-full text-[11px] mt-2">
              <thead>
                <tr style={{ color: 'var(--muted)' }}>
                  <th className="text-left font-normal">axis</th>
                  <th className="text-right font-normal">this</th>
                  <th className="text-right font-normal">cluster</th>
                  <th className="text-right font-normal">percentile</th>
                </tr>
              </thead>
              <tbody className="mono">
                {data.cluster.axes.map((a) => (
                  <tr key={a.key} title={a.description}>
                    <td style={{ color: 'var(--muted)' }}>{a.label}</td>
                    <td className="text-right">{a.value?.toFixed(2) ?? '—'}</td>
                    <td className="text-right" style={{ color: 'var(--muted)' }}>
                      {a.cluster_mean?.toFixed(2) ?? '—'}
                    </td>
                    <td className="text-right" style={{ color: 'var(--muted)' }}>
                      {a.percentile === null || a.percentile === undefined
                        ? '—'
                        : `${Math.round(a.percentile)}th`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="lg:col-span-5 glass p-4">
          <h2 className="text-sm font-semibold">Around each studied event</h2>
          <p className="text-xs mt-1 mb-2" style={{ color: 'var(--muted)' }}>
            Cumulative abnormal return over the five sessions after the event.
          </p>
          <EventReactions reactions={data?.reactions ?? []} meta={meta} />
        </div>

        <div className="lg:col-span-3 glass p-4">
          <h2 className="text-sm font-semibold">In its cluster</h2>
          <p className="text-xs mt-1 mb-3" style={{ color: 'var(--muted)' }}>
            Similar trading over the window. Open one to compare.
          </p>
          {data && data.peers.length > 0 ? (
            <ul className="flex flex-col gap-1">
              {data.peers.map((p) => (
                <li key={p.symbol}>
                  <Link
                    to={`/stocks/${p.symbol}`}
                    className="flex items-center justify-between gap-2 text-sm px-2 py-1.5 rounded-lg"
                    style={{ background: 'rgba(255,255,255,.03)' }}
                  >
                    <span>
                      <span className="mono">{p.symbol}</span>
                      <span className="ml-2 text-[11px]" style={{ color: 'var(--muted)' }}>
                        {p.sector}
                      </span>
                    </span>
                    <span className="mono text-[11px]" style={{ color: 'var(--muted)' }}>
                      β {p.beta?.toFixed(2)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs" style={{ color: 'var(--muted)' }}>
              Its cluster has no other members with prices in the window.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

/** CAR for every studied event, as a bar chart plus a table.
 *
 *  Direction is the only thing that gets `--up` / `--down`. These are abnormal
 *  returns, not "good news" and "bad news", and colouring them by anything else
 *  would invite exactly that reading. */
function EventReactions({ reactions, meta }: { reactions: EventReaction[]; meta?: Meta }) {
  const option = useMemo(() => {
    if (reactions.length === 0) return null
    const sorted = [...reactions].sort((a, b) => a.car_0_5 - b.car_0_5)
    return {
      animationDuration: 450,
      grid: { left: 8, right: 16, top: 8, bottom: 44, containLabel: true },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(11,18,32,.94)',
        borderColor: 'rgba(148,163,184,.25)',
        textStyle: { color: '#E6EDF7', fontSize: 12 },
        formatter: (ps: { name: string; data: number; value: number }[]) => {
          const p = ps[0]
          if (!p) return ''
          const r = sorted.find((x) => x.label === p.name)
          return `<b>${p.name}</b><br/>${r?.date ?? ''}<br/>CAR 0 to 5 sessions: ${
            typeof p.data === 'number' ? p.data.toFixed(2) : ''
          }%<br/>market-model beta: ${r?.beta?.toFixed(2) ?? '—'}`
        },
      },
      xAxis: {
        type: 'value',
        axisLabel: { color: 'var(--muted)', fontSize: 10, formatter: (v: number) => `${v.toFixed(0)}%` },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,.10)' } },
      },
      yAxis: {
        type: 'category',
        data: sorted.map((r) => r.label),
        axisLabel: { color: 'var(--muted)', fontSize: 9, width: 130, overflow: 'truncate' },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      series: [
        {
          type: 'bar',
          barMaxWidth: 12,
          data: sorted.map((r) => ({
            value: r.car_0_5,
            itemStyle: { color: r.car_0_5 >= 0 ? SEMANTIC.up : SEMANTIC.down, borderRadius: 3 },
          })),
          label: {
            show: true,
            position: 'right',
            fontSize: 9,
            color: 'var(--muted)',
            // `p.data` is the item OBJECT here, because each bar carries an
            // `itemStyle`; `Number({})` is NaN, and the first render printed
            // "NaN%" on every bar.
            formatter: (p: { value: number }) => `${Number(p.value).toFixed(1)}%`,
          },
        },
      ],
    }
  }, [reactions])

  if (reactions.length === 0) {
    return (
      <p className="text-sm py-8 text-center" style={{ color: 'var(--muted)' }}>
        No valid event study for this company — it may have listed after the events in the
        window, or lacked the 100 pre-event sessions the model needs.
      </p>
    )
  }

  return (
    <>
      <Chart option={option as Record<string, unknown>} height={Math.max(240, reactions.length * 26)} title="" caption="" />
      {meta?.note && (
        <p className="text-xs mt-2" style={{ color: 'var(--caution)', lineHeight: 1.55 }}>
          {meta.note}
        </p>
      )}
    </>
  )
}
