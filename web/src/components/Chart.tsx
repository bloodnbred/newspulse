import { useMemo } from 'react'
import ReactECharts from 'echarts-for-react'
import { echarts } from '../lib/echarts'
import { ChartCard } from './ChartCard'
import type { Meta } from '../types'

interface Props {
  title: string
  caption: string
  meta?: Meta
  loading?: boolean
  error?: string | null
  option: Record<string, unknown>
  height?: number
  /** Called when a data point is clicked. Omit for a read-only chart. */
  onEvent?: Record<string, (params: Record<string, unknown>) => void>
  className?: string
}

/** The shared axis and tooltip styling.
 *
 *  Exported rather than inlined so every chart on the site can be corrected in
 *  one place. The colours come from `SEMANTIC`, not from literals: `--news`
 *  teal is the news series everywhere and `--price` amber is the market
 *  everywhere, and a chart that hardcodes a hex is a chart that stops meaning
 *  what the legend says the moment the palette changes. */
export function baseOption(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    textStyle: {
      color: 'var(--muted)',
      fontFamily: "'Inter Variable', Inter, system-ui, sans-serif",
    },
    tooltip: {
      backgroundColor: 'rgba(11,18,32,.94)',
      borderColor: 'rgba(148,163,184,.25)',
      textStyle: { color: '#E6EDF7', fontSize: 12 },
      ...(over.tooltip as Record<string, unknown>),
    },
    ...over,
  }
}

export function Chart({
  title, caption, meta, loading, error, option, height = 300,
  onEvent, className,
}: Props) {
  // `notMerge` on every update: without it ECharts keeps the previous series
  // list, so changing the metric on the Time Machine draws the new line
  // *underneath* the old one and the chart silently shows two answers.
  //
  // The handler map is built rather than spread, because `echarts-for-react`
  // types `onEvents` as `Record<string, Function>` and an object holding
  // `{click: fn | undefined}` is not assignable to that - the index signature
  // admits no undefined. Building it conditionally keeps the type honest
  // rather than asserting around it.
  const handlers = useMemo(() => {
    const out: Record<string, (params: Record<string, unknown>) => void> = {}
    for (const [name, fn] of Object.entries(onEvent ?? {})) {
      if (fn) out[name] = fn
    }
    return out
  }, [onEvent])
  return (
    <ChartCard
      title={title}
      caption={caption}
      meta={meta}
      loading={loading}
      error={error ?? undefined}
      className={className}
    >
      <ReactECharts
        echarts={echarts}
        option={option}
        notMerge
        lazyUpdate
        style={{ height, width: '100%' }}
        onEvents={handlers}
        opts={{ renderer: 'canvas' }}
      />
    </ChartCard>
  )
}

export default Chart
