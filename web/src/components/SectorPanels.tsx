import { useMemo } from 'react'
import { SECTOR_COLORS, SEMANTIC } from '../lib/echartsTheme'
import { useFilters } from '../lib/filters'
import Chart from './Chart'
import type { Meta, SectorHeat, SurpriseDay } from '../types'

/** Sector heat as a treemap: area is news volume, colour is the mean return.
 *
 *  Clicking a tile toggles that sector in the global filters, which is the
 *  cross-filtering the blueprint asks for. A treemap is the right encoding here
 *  precisely because area already carries a magnitude; a pie would too, but
 *  treemap sorts and labels better at ten categories.
 *
 *  The colour scale is diverging around zero and is clamped, because a sector
 *  mean return is small and one outlier sector would otherwise flatten every
 *  other tile to the same near-white. The clamp is stated in the caption
 *  rather than left for the reader to infer from the legend. */
export function SectorTreemap({
  rows, meta, loading, error,
}: {
  rows: SectorHeat[]
  meta?: Meta
  loading?: boolean
  error?: string | null
}) {
  const toggleSector = useFilters((s) => s.toggleSector)
  const active = useFilters((s) => s.filters.sectors)

  const option = useMemo(() => {
    const clamp = 2.0
    const bounded = rows.map((r) => Math.max(-clamp, Math.min(clamp, r.avg_return_pct ?? 0)))
    const extent = Math.max(0.25, ...bounded.map(Math.abs))
    return {
      animationDuration: 600,
      tooltip: {
        backgroundColor: 'rgba(11,18,32,.94)',
        borderColor: 'rgba(148,163,184,.25)',
        textStyle: { color: '#E6EDF7', fontSize: 12 },
        formatter: (p: Record<string, unknown>) => {
          /* ECharts fires this for its own internal treemap nodes as well as
           * for the sectors, and those carry no `data`. Reading `p.data.sector`
           * without checking threw on hover and took the tooltip down with it -
           * invisible to a screenshot, invisible to the type checker (the cast
           * asserts what it hopes for), and only reproducible by actually
           * pointing at the chart. */
          const d = p.data as
            | { name?: string; value?: unknown[]; sector?: SectorHeat }
            | undefined;
          const s = d.sector;
          const news = Number(Array.isArray(d.value) ? d.value[0] : 0);
          const ret = s?.avg_return_pct ?? 0;
          return [
            `<strong>${s?.sector ?? d.name ?? ''}</strong>`,
            `${news.toLocaleString('en-IN')} headline-days`,
            `mean return ${ret >= 0 ? '+' : ''}${ret.toFixed(2)}%`,
            '',
            '<span style="opacity:.7">Click to filter the page by this sector</span>',
          ].join('<br/>')
        },
      },
      series: [
        {
          type: 'treemap',
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          width: '100%',
          height: '100%',
          itemStyle: { borderColor: 'rgba(11,18,32,.9)', borderWidth: 2, gapWidth: 2 },
          label: {
            show: true,
            color: '#E6EDF7',
            fontSize: 12,
            formatter: '{b}',
          },
          upperLabel: { show: true, height: 20, color: '#93A4BF', fontSize: 10 },
          data: rows.map((r) => {
            const pct = Math.max(-1, Math.min(1, (r.avg_return_pct ?? 0) / extent))
            // Diverging: a negative return leans red, positive leans green, and
            // the midpoint is the surface colour rather than a pale hue, so a
            // sector that did not move reads as "nothing to say".
            const colour =
              pct >= 0
                ? `rgba(34,197,94,${(0.25 + 0.6 * pct).toFixed(2)})`
                : `rgba(239,68,68,${(0.25 + 0.6 * -pct).toFixed(2)})`
            return {
              name: r.sector,
              value: [r.news_count, r.avg_return_pct ?? 0],
              sector: r,
              itemStyle: {
                color: colour,
                borderColor: active.includes(r.sector)
                  ? SEMANTIC.news
                  : 'rgba(11,18,32,.9)',
                borderWidth: active.includes(r.sector) ? 3 : 2,
              },
            }
          }),
        },
      ],
    }
  }, [rows, active])

  return (
    <Chart
      title="Sector heat"
      caption="Tile area is news volume; tile colour is the sector's mean return over the window, scaled and clipped at ±2% so one sector cannot flatten the rest. Click a tile to filter the page."
      meta={meta}
      loading={loading}
      error={error}
      height={300}
      className="lg:col-span-6"
      option={option}
      onEvent={{
        click: (p) => {
          const d = p.data as { sector?: SectorHeat } | undefined
          const sector = d?.sector?.sector
          if (sector) toggleSector(sector)
        },
      }}
    />
  )
}

/** Surprise days, as cards.
 *
 *  Both kinds are shown, and the ratio between them is called out, because
 *  most of them are `quiet_news_move`: the price moved far and this corpus had
 *  little to say. Presenting only the news-driven ones would be cherry-picking
 *  toward the conclusion the study does not support. */
export function SurpriseCards({
  days, onPick,
}: {
  days: SurpriseDay[]
  onPick: (date: string) => void
}) {
  if (days.length === 0) {
    return (
      <div className="glass p-4 lg:col-span-6">
        <div className="text-sm font-semibold">Surprise days</div>
        <p className="text-sm mt-2" style={{ color: 'var(--muted)' }}>
          None found. Run <code>python -m dwm mine --op surprise</code>.
        </p>
      </div>
    )
  }
  const quiet = days.filter((d) => d.kind === 'quiet_news_move').length
  const quietShare = Math.round((100 * quiet) / days.length)

  return (
    <div className="glass p-4 lg:col-span-6 flex flex-col">
      <div className="flex items-baseline justify-between gap-2">
        <div className="text-sm font-semibold">Surprise days</div>
        <span className="mono text-xs" style={{ color: 'var(--muted)' }}>
          {quietShare}% are quiet-news moves
        </span>
      </div>
      <div className="max-h-64 overflow-auto mt-3 space-y-2 pr-1">
        {days.map((d) => {
          const driven = d.kind === 'news_driven'
          return (
            <button
              key={`${d.sector}-${d.session}`}
              className="w-full text-left glass p-3"
              style={{ cursor: 'pointer' }}
              onClick={() => onPick(String(d.session).slice(0, 10))}
            >
              <div className="flex items-center gap-2 flex-wrap">
                <span
                  className="pill"
                  style={{ borderColor: driven ? SEMANTIC.news : 'var(--caution)' }}
                >
                  {driven ? 'News-driven' : 'Quiet-news move'}
                </span>
                <span className="text-xs" style={{ color: 'var(--muted)' }}>
                  {d.sector}
                </span>
                <span className="mono text-xs ml-auto" style={{ color: 'var(--muted)' }}>
                  {String(d.session).slice(0, 10)}
                </span>
              </div>
              <div className="mono text-xs mt-2" style={{ color: 'var(--muted)' }}>
                news z {(d.news_heat_z ?? 0).toFixed(2)} · move z{' '}
                {(d.abs_return_z ?? 0).toFixed(2)} ·{' '}
                <span
                  style={{
                    color:
                      (d.avg_return ?? 0) >= 0 ? 'var(--up)' : 'var(--down)',
                  }}
                >
                  {(d.avg_return ?? 0) >= 0 ? '+' : ''}
                  {(d.avg_return ?? 0).toFixed(2)}%
                </span>
              </div>
            </button>
          )
        })}
      </div>
      <p className="text-xs mt-3" style={{ color: 'var(--muted)', lineHeight: 1.5 }}>
        Scored against each sector's own preceding 60 sessions. A
        <span className="mono"> quiet_news_move</span> is the price moving far
        while this corpus stayed quiet — a fact about our coverage of one outlet.
      </p>
    </div>
  )
}

export { SECTOR_COLORS }
export default SectorTreemap
