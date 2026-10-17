import { useMemo } from 'react'
import { SEMANTIC } from '../lib/echartsTheme'
import Chart from './Chart'
import type { DayCell, Meta } from '../types'

/** Calendar heatmap: one cell per in-window day.
 *
 *  Carries `is_trading_day` through to the tooltip, which matters more than it
 *  looks: 32.4% of in-window headlines fall on days the market was closed, so a
 *  calendar without that flag shows closed days and holidays as quiet market
 *  days. That is the opposite of what they are. */
export function CalendarHeatmap({
  rows, events, meta, loading, error, onPick,
}: {
  rows: DayCell[]
  events: { date: string; label: string }[]
  meta?: Meta
  loading?: boolean
  error?: string | null
  onPick: (date: string) => void
}) {
  const eventDays = useMemo(
    () => new Set(events.map((e) => e.date.slice(0, 10))),
    [events],
  )

  /* The cell size and the card height are derived from the span, not fixed.
   *
   * A calendar needs `7 * years` rows. At a fixed 13px cell and a fixed 330px
   * card, six years ask for 546px and get 330 - ECharts clips silently, and
   * every month and year label is drawn outside the visible area. So the cell
   * shrinks as the window grows and the height follows from it, which is the
   * only way the labels survive at both one year and six. */
  const { cell, height, years } = useMemo(() => {
    const first = rows.length > 0 ? Number(rows[0]!.date.slice(0, 4)) : 2015
    const last = rows.length > 0 ? Number(rows[rows.length - 1]!.date.slice(0, 4)) : 2020
    const span = Math.max(1, last - first + 1)
    const size = span > 5 ? 12 : span > 3 ? 14 : span > 1 ? 18 : 22
    return { cell: size, height: span * 7 * size + 74, years: span }
  }, [rows])

  const option = useMemo(() => {
    const values: [string, number][] = rows.map((r) => [r.date, r.value ?? 0])
    return {
      animationDuration: 400,
      tooltip: {
        backgroundColor: 'rgba(11,18,32,.94)',
        borderColor: 'rgba(148,163,184,.25)',
        textStyle: { color: '#E6EDF7', fontSize: 12 },
        formatter: (p: Record<string, unknown>) => {
          const date = String((p.value as [string, number])[0])
          const v = Number((p.value as [string, number])[1])
          const row = rows.find((r) => r.date === date)
          const ev = events.find((e) => e.date.slice(0, 10) === date)
          return [
            `<strong>${date}</strong>`,
            `${Math.round(v).toLocaleString('en-IN')} headlines`,
            row?.is_trading_day ? 'trading day' : 'market closed',
            ev ? `event: ${ev.label}` : '',
          ]
            .filter(Boolean)
            .join('<br/>')
        },
      },
      visualMap: {
        min: 0,
        max: Math.max(1, ...values.map((v) => v[1])),
        calculable: false,
        orient: 'horizontal',
        left: 'center',
        bottom: 0,
        itemHeight: 60,
        textStyle: { color: 'var(--muted)', fontSize: 10 },
        inRange: { color: ['rgba(20,184,166,.08)', SEMANTIC.news] },
      },
      calendar: {
        top: 30,
        left: 40,
        right: 12,
        bottom: 74,
        cellSize: cell,
        range: (() => {
          // A two-element ARRAY. The pack's version built the string
          // "2015-2020", which ECharts does not parse as a range - it read as
          // a single year called "2015-2020", so the calendar rendered no grid
          // at all and only the colour bar was visible.
          if (rows.length === 0) return ['2015', '2020']
          return [
            rows[0]!.date.slice(0, 4),
            rows[rows.length - 1]!.date.slice(0, 4),
          ]
        })(),
        itemStyle: { color: 'transparent', borderWidth: 0 },
        splitLine: { show: false },
        yearLabel: {
          show: true,
          position: 'left',
          color: 'var(--muted)',
          fontSize: 11,
          margin: 12,
        },
        // Only January is labelled, and the names are supplied explicitly rather
        // than via `nameMap: 'en'`. The locale build returns "January", so a
        // formatter testing for "Jan" matched nothing and every month label
        // came out empty - a calendar with no months on it.
        monthLabel: {
          show: true,
          color: 'var(--muted)',
          fontSize: 9,
          nameMap: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
          formatter: (p: string) => (p.startsWith('Jan') ? 'Jan' : ''),
        },
        dayLabel: {
          show: true,
          color: 'var(--muted)',
          fontSize: 9,
          firstDay: 1,
          nameMap: ['', 'M', '', 'W', '', 'F', ''],
        },
      },
      series: [
        {
          type: 'heatmap',
          coordinateSystem: 'calendar',
          data: values,
          itemStyle: { borderRadius: 2 },
          // Event days get a ring so a known date is findable on a five-year
          // grid where a single cell is a few pixels wide.
          markPoint: {
            symbol: 'circle',
            symbolSize: 7,
            itemStyle: { color: SEMANTIC.mining },
            label: { show: false },
            data: [...eventDays].map((d) => ({ coord: [d, 1] })),
          },
        },
      ],
    }
  }, [rows, events, eventDays, cell])

  return (
    <Chart
      title="Calendar heatmap"
      caption={`One cell per day in the window, ${years} years. Teal depth is headline volume; a violet ring marks an event day. Click a day to open it in the Time Machine.`}
      meta={meta}
      loading={loading}
      error={error}
      height={height}
      className="lg:col-span-8"
      option={option}
      onEvent={{
        click: (p) => {
          const v = p.value as [string, number] | undefined
          if (v?.[0]) onPick(String(v[0]).slice(0, 10))
        },
      }}
    />
  )
}

export default CalendarHeatmap
