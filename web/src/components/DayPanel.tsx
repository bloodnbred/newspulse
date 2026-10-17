import { useApi } from '../lib/api'
import type { Meta } from '../types'

interface Headline {
  headline_id: number
  publish_date: string
  headline_text: string
  sentiment_compound: number | null
  is_risk_signal: boolean
  is_market_report: boolean
}
interface DayPayload {
  date: string
  headline_count: number
  headlines: Headline[]
  market: { trade_date: string; close: number; return_pct: number } | null
  surprise: { sector: string; kind: string; news_heat_z: number; abs_return_z: number }[] | null
}

/** The panel the Time Machine's crosshair drives.
 *
 *  Shows one day: the index move, whether the day was unusual, and the
 *  headlines we hold for it. The "unusual" badge is the honest part - most
 *  dramatic-looking days in this corpus are flagged `quiet_news_move`, which
 *  says the price moved and *we* had little to say, not that the market was
 *  irrational.
 *
 *  `useApi` builds a query string, so the date goes in the PATH rather than as
 *  a parameter. The request is disabled until there is a date, or the panel
 *  would fire `/ui/day` on every render of a page nobody has hovered. */
export function DayPanel({ date }: { date: string | null }) {
  const { data, isFetching, error } = useApi<DayPayload>(
    date ? `/ui/day/${date}` : '/ui/day/',
    {},
    { enabled: Boolean(date), withFilters: false },
  )

  if (!date) {
    return (
      <div className="glass p-4 h-full flex flex-col justify-center">
        <div className="text-[11px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
          Day
        </div>
        <p className="text-sm mt-2" style={{ color: 'var(--muted)' }}>
          Hover or click the chart to open a day. Replay walks the whole window.
        </p>
      </div>
    )
  }

  if (isFetching) return <div className="glass p-4 skeleton h-64" aria-busy="true" />
  if (error || !data) {
    return (
      <div className="glass p-4">
        <p className="text-sm" style={{ color: 'var(--muted)' }}>
          Could not load {date}. It may fall outside the analysis window.
        </p>
      </div>
    )
  }

  const day = data.data
  const meta = data.meta as Meta | undefined
  if (!day) return null

  const unusual = day.surprise ?? []
  const allQuiet = unusual.length > 0 && unusual.every((s) => s.kind === 'quiet_news_move')

  return (
    <div className="glass p-4 h-full overflow-auto">
      <div className="flex items-baseline justify-between gap-2">
        <div className="text-[11px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
          Day
        </div>
        <span className="mono text-xs" style={{ color: 'var(--muted)' }}>
          {meta?.rows} rows · {meta?.ms} ms
        </span>
      </div>
      <div className="mono text-lg mt-1">{date}</div>

      {day.market ? (
        <div className="mt-2">
          <span
            className="mono text-xl"
            style={{ color: day.market.return_pct >= 0 ? 'var(--up)' : 'var(--down)' }}
          >
            {day.market.return_pct >= 0 ? '+' : ''}
            {day.market.return_pct.toFixed(2)}%
          </span>
          <span className="text-xs ml-2" style={{ color: 'var(--muted)' }}>
            Nifty{' '}
            {day.market.close.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
          </span>
        </div>
      ) : (
        <p className="text-xs mt-2" style={{ color: 'var(--muted)' }}>
          No index bar for this date — outside the window, or not a trading day.
        </p>
      )}

      {unusual.length > 0 && (
        <div className="mt-3">
          <span
            className="pill"
            style={{ borderColor: allQuiet ? 'var(--caution)' : 'var(--news)' }}
          >
            Unusual day? {unusual[0]!.kind === 'quiet_news_move' ? 'quiet news, big move' : 'news-driven'}
          </span>
          {allQuiet && (
            <p className="text-xs mt-2" style={{ color: 'var(--muted)', lineHeight: 1.5 }}>
              {unusual.length} sectors moved far enough to flag, and on every one of
              them this corpus had little to say. That is a fact about our coverage
              of one outlet — not evidence that the market was irrational.
            </p>
          )}
        </div>
      )}

      <div className="text-[11px] uppercase tracking-widest mt-4" style={{ color: 'var(--muted)' }}>
        Our headlines that day ({day.headline_count})
      </div>
      <ul className="mt-2 space-y-2">
        {day.headlines.slice(0, 8).map((h) => (
          <li key={h.headline_id} className="text-xs" style={{ lineHeight: 1.45 }}>
            <span style={{ color: 'var(--text)' }}>{h.headline_text}</span>
            <span className="ml-2">
              {h.is_market_report && (
                <span className="pill" style={{ marginRight: 4 }}>
                  market report
                </span>
              )}
              {h.is_risk_signal && (
                <span className="pill" style={{ borderColor: 'var(--caution)' }}>
                  risk signal
                </span>
              )}
            </span>
          </li>
        ))}
      </ul>
      {day.headlines.length > 8 && (
        <p className="text-xs mt-2" style={{ color: 'var(--muted)' }}>
          {day.headlines.length - 8} more, capped at 50 by the API.
        </p>
      )}
    </div>
  )
}

export default DayPanel
