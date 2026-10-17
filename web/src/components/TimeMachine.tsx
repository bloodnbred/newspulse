import { useEffect, useMemo, useState } from 'react'
import { Play, Pause } from 'lucide-react'
import { SEMANTIC } from '../lib/echartsTheme'
import { useFilters } from '../lib/filters'
import Chart from './Chart'
import type { Meta } from '../types'

interface Point {
  date: string
  headlines: number
  sentiment: number
  sensational: number
  nifty: number | null
}
interface EventMark {
  date: string
  label: string
  type: string
}

const METRICS = [
  { key: 'headlines', label: 'News volume' },
  { key: 'sentiment', label: 'Sentiment' },
  { key: 'sensational', label: 'Sensationalism' },
] as const
type MetricKey = (typeof METRICS)[number]['key']

interface Props {
  points: Point[]
  events: EventMark[]
  meta?: Meta
  loading?: boolean
  error?: string | null
  focusDate: string | null
  onFocus: (date: string | null) => void
  metric: MetricKey
}

/** The Time Machine: news against the Nifty, with a draggable window.
 *
 *  The `dataZoom` brush is wired to the GLOBAL filter store, so dragging it
 *  re-filters the calendar, the treemap and every KPI on the page. That is the
 *  blueprint's cross-filtering requirement, and it is why the brush is a filter
 *  and not a local zoom: a local zoom would leave the page showing numbers for
 *  a period the reader has just hidden. */
export function TimeMachine({
  points, events, meta, loading, error, focusDate, onFocus, metric,
}: Props) {
  const setFilters = useFilters((s) => s.set)
  const [playing, setPlaying] = useState(false)

  const dates = useMemo(() => points.map((p) => p.date), [points])

  const option = useMemo(() => {
    // The news series is drawn as an AREA in `--news` teal because news is the
    // subject of this project. The Nifty is a thin amber LINE because price is
    // context. Two series, two y-axes: `--price` is deliberately recessive, and
    // the caution under the chart says the two scales are not comparable.
    const newsSeries = {
      name: METRICS.find((m) => m.key === metric)?.label ?? 'News',
      type: 'line' as const,
      smooth: 0.25,
      symbol: 'none',
      yAxisIndex: 0,
      lineStyle: { color: SEMANTIC.news, width: 1.4 },
      areaStyle: { color: 'rgba(20,184,166,.18)' },
      data: points.map((p) => p[metric] ?? null),
    }
    const niftySeries = {
      name: 'Nifty',
      type: 'line' as const,
      smooth: false,
      symbol: 'none',
      yAxisIndex: 1,
      lineStyle: { color: SEMANTIC.price, width: 1.6 },
      data: points.map((p) => p.nifty),
    }
    return {
      animationDuration: 600,
      grid: { left: 52, right: 56, top: 18, bottom: 64 },
      legend: { show: false },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(11,18,32,.94)',
        borderColor: 'rgba(148,163,184,.25)',
        textStyle: { color: '#E6EDF7', fontSize: 12 },
      },
      xAxis: {
        type: 'category',
        data: dates,
        axisLabel: { color: 'var(--muted)', fontSize: 10, hideOverlap: true },
        axisLine: { lineStyle: { color: 'rgba(148,163,184,.3)' } },
      },
      yAxis: [
        {
          type: 'value',
          name: 'News',
          nameTextStyle: { color: SEMANTIC.news, fontSize: 10 },
          axisLabel: { color: 'var(--muted)', fontSize: 10 },
          splitLine: { lineStyle: { color: 'rgba(148,163,184,.12)' } },
        },
        {
          type: 'value',
          name: 'Nifty',
          nameTextStyle: { color: SEMANTIC.price, fontSize: 10 },
          axisLabel: { color: 'var(--muted)', fontSize: 10 },
          splitLine: { show: false },
          scale: true,
        },
      ],
      // Event flags as labelled vertical markers. `markPoint` is used rather
      // than a second series because an event is a point in time, not a
      // quantity, and giving it a y-value would invent one.
      series: [
        {
          ...newsSeries,
          // Event flags as labelled vertical markers.
          //
          // `value` is a NUMBER, not the label. The pack's version set it to
          // `e.label`, and a string in `value` feeds into the y-axis extent
          // calculation - which pushed the news axis to 25,000 against monthly
          // values of roughly 700 to 2,000, flattening the series to a sliver at
          // the bottom of the chart. The label goes in `name`, which is where
          // ECharts reads a mark point's text from.
          markPoint: {
            symbol: 'pin',
            symbolSize: 24,
            label: { show: false },
            itemStyle: { color: SEMANTIC.mining },
            data: events.map((e) => ({
              name: e.label,
              coord: [e.date, 0],
              value: 0,
            })),
          },
        },
        niftySeries,
      ],
      dataZoom: [
        { type: 'inside', start: 0, end: 100 },
        {
          type: 'slider',
          height: 26,
          bottom: 12,
          borderColor: 'rgba(148,163,184,.2)',
          fillerColor: 'rgba(20,184,166,.14)',
          handleStyle: { color: SEMANTIC.news },
          textStyle: { color: 'var(--muted)', fontSize: 10 },
          labelFormatter: (i: number) => dates[i] ?? '',
        },
      ],
    }
  }, [points, dates, events, metric])

  /** Replay: walk the focus date forward across the window. */
  useEffect(() => {
    if (!playing || dates.length === 0) return
    const id = window.setInterval(() => {
      const current = focusDate ? dates.indexOf(focusDate) : -1
      const next = current + 1
      if (next >= dates.length) {
        setPlaying(false)
        onFocus(null)
        return
      }
      onFocus(dates[next] ?? null)
    }, 140)
    return () => window.clearInterval(id)
  }, [playing, dates, focusDate, onFocus])

  return (
    <Chart
      title="Time Machine"
      caption="Accent area: this month's news volume. Amber line: the Nifty, on its own scale. Drag the slider to re-filter the whole page; hover to open a day."
      meta={meta}
      loading={loading}
      error={error}
      height={340}
      className="lg:col-span-8"
      option={option}
      onEvent={{
        click: (p) => {
          const idx = typeof p.dataIndex === 'number' ? p.dataIndex : -1
          const date = dates[idx]
          if (date) onFocus(date)
        },
        datazoom: (p) => {
          // ECharts reports the brush as percentages of the whole series, so
          // the indices are resolved against `dates` before the filter is
          // written. Without this the drag would be a local zoom and the rest
          // of the page would keep showing the full window.
          const z = p as { start?: number; end?: number }
          if (z.start === undefined || z.end === undefined) return
          const lo = Math.max(0, Math.floor((z.start / 100) * dates.length))
          const hi = Math.min(dates.length - 1, Math.ceil((z.end / 100) * dates.length))
          const from = dates[lo]
          const to = dates[hi]
          if (from && to) setFilters({ from, to })
        },
      }}
    />
  )
}

export function TimeMachineControls({
  metric, onMetric, playing, onPlay,
}: {
  metric: MetricKey
  onMetric: (m: MetricKey) => void
  playing: boolean
  onPlay: () => void
}) {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <div className="flex gap-1" role="group" aria-label="Metric">
        {METRICS.map((m) => (
          <button
            key={m.key}
            className="pill"
            aria-pressed={metric === m.key}
            style={{
              cursor: 'pointer',
              borderColor: metric === m.key ? SEMANTIC.news : undefined,
              color: metric === m.key ? SEMANTIC.news : undefined,
            }}
            onClick={() => onMetric(m.key)}
          >
            {m.label}
          </button>
        ))}
      </div>
      <button className="btn" onClick={onPlay} style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
        {playing ? <Pause size={14} aria-hidden /> : <Play size={14} aria-hidden />}
        {playing ? 'Pause replay' : 'Replay'}
      </button>
    </div>
  )
}

export default TimeMachine
