import ReactECharts from 'echarts-for-react'
import { echarts } from '../lib/echarts'
import { SEMANTIC } from '../lib/echartsTheme'

interface Props {
  /** 0-100 percentile of recent news heat against the corpus's own history. */
  score: number
  label?: string
}

const BANDS = [
  { max: 33, label: 'Calm', colour: SEMANTIC.up },
  { max: 66, label: 'Active', colour: SEMANTIC.price },
  { max: 101, label: 'Heated', colour: SEMANTIC.down },
]

/** Market Mood: how busy the news is, against its own five-year history.
 *
 *  A percentile and not an index. "News volume is 340" is not a thing a reader
 *  can place; "busier than 92% of the days in this corpus" is. The band labels
 *  are deliberately coarse - a gauge claiming to distinguish 61 from 63 would
 *  be claiming precision the data does not have. */
export function MoodGauge({ score }: Props) {
  const value = Math.max(0, Math.min(100, Number.isFinite(score) ? score : 50))
  const band = BANDS.find((b) => value < b.max) ?? BANDS[BANDS.length - 1]!
  const reduced =
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

  const option = {
    animationDuration: 900,
    series: [
      {
        type: 'gauge',
        min: 0,
        max: 100,
        startAngle: 205,
        endAngle: -25,
        radius: '92%',
        center: ['50%', '62%'],
        pointer: { length: '58%', width: 4, itemStyle: { color: band.colour } },
        axisLine: {
          lineStyle: {
            width: 10,
            color: [
              [0.33, 'rgba(34,197,94,.22)'],
              [0.66, 'rgba(245,158,11,.22)'],
              [1, 'rgba(239,68,68,.22)'],
            ],
          },
        },
        axisTick: { show: false },
        splitLine: { length: 10, lineStyle: { width: 1, color: 'rgba(148,163,184,.35)' } },
        axisLabel: { show: false },
        anchor: { show: false },
        title: { show: false },
        detail: {
          valueAnimation: true,
          offsetCenter: [0, '-4%'],
          formatter: (v: number) => `${Math.round(v)}th`,
          color: '#E6EDF7',
          fontSize: 26,
          fontFamily: "'JetBrains Mono Variable', monospace",
        },
        // `value` must be the number itself. The pack passed a bare number here
        // too, but with `valueAnimation` on, a headless render can capture the
        // gauge mid-animation at 0 — so the animation is skipped when the reader
        // has asked for reduced motion, which is also when a static number is
        // the right answer anyway.
        animation: !reduced,
        data: [{ value }],
      },
    ],
  }

  return (
    <div className="glass p-4 flex flex-col items-center justify-center h-full" data-tour="mood">
      <div className="text-[11px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
        Market mood
      </div>
      <ReactECharts
        echarts={echarts}
        option={option}
        style={{ height: 132, width: '100%' }}
        opts={{ renderer: 'canvas' }}
      />
      <div className="text-sm font-semibold" style={{ color: band.colour }}>
        {band.label}
      </div>
      <p className="text-xs text-center mt-1" style={{ color: 'var(--muted)', maxWidth: 240 }}>
        How busy this sector's news is against its own history — a percentile, not
        an index. News volume is not sentiment.
      </p>
    </div>
  )
}

export default MoodGauge
