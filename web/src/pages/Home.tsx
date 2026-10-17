import { useState } from 'react'
import { useApi } from '../lib/api'
import { KpiCard } from '../components/KpiCard'
import { HeroSearch } from '../components/HeroSearch'
import { MoodGauge } from '../components/MoodGauge'
import { TimeMachine, TimeMachineControls } from '../components/TimeMachine'
import { DayPanel } from '../components/DayPanel'
import { SectorTreemap, SurpriseCards } from '../components/SectorPanels'
import { CalendarHeatmap } from '../components/CalendarHeatmap'
import { InsightsCarousel, SectorRace, ActionTiles } from '../components/Panels'
import type {
  DayCell, InsightCard, Kpi, Meta, SectorHeat, SeriesPointFull, SurpriseDay,
} from '../types'

interface HomeSummary {
  devSample: boolean
  window: { start: string; end: string }
  kpis: Kpi[]
  mood: { score: number; label: string; percentile: number }
  main: SeriesPointFull[]
  events: { date: string; label: string; type: string }[]
  sectorHeat: SectorHeat[]
  surprise: SurpriseDay[]
  insights: InsightCard[]
}

/** The homepage, per docs/UI-BLUEPRINT.md §6.1.
 *
 *  Order is the design. Hero and KPIs first because they are the three-second
 *  answer; the Time Machine next because it is the thing worth playing with;
 *  the cross-filtering panels after that; insights and routes last.
 *
 *  Two fetches, not eight: `/ui/home` carries the KPIs, series, events, sector
 *  heat, surprise days and insights in one response because the contract says
 *  it must, and `/ui/calendar` is separate because it is the one payload big
 *  enough to be worth deferring. The day panel fetches only once the reader
 *  hovers something. */
export default function Home() {
  const { data, isLoading, error } = useApi<HomeSummary>('/ui/home')
  const calendar = useApi<DayCell[]>('/ui/calendar', {}, { enabled: !isLoading })
  const [focusDate, setFocusDate] = useState<string | null>(null)
  const [metric, setMetric] = useState<'headlines' | 'sentiment' | 'sensational'>('headlines')
  const [playing, setPlaying] = useState(false)

  const home = data?.data
  const meta = data?.meta as Meta | undefined
  const calMeta = calendar.data?.meta as Meta | undefined

  if (error) {
    return (
      <div className="glass p-8 mt-4">
        <h1 className="text-xl font-semibold">The dashboard could not load</h1>
        <p className="text-sm mt-2" style={{ color: 'var(--muted)' }}>
          {(error as { hint?: string }).hint ??
            'Is the API running? Start it with `python -m dwm serve`, then reload.'}
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* 1. Hero: promise, search, mood. */}
      <section className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        <div className="glass p-6 lg:col-span-8 flex flex-col justify-center">
          <h1 className="text-3xl md:text-4xl font-bold" style={{ lineHeight: 1.15 }}>
            Watch India&rsquo;s news and the Nifty move together.
          </h1>
          <p className="text-sm mt-2 mb-5" style={{ color: 'var(--muted)' }}>
            {home
              ? `${home.window.start} to ${home.window.end} · ${home.kpis.length} figures · every one from a query`
              : 'Reading the warehouse…'}
          </p>
          <HeroSearch />
        </div>
        <div className="lg:col-span-4">
          {home ? (
            <MoodGauge score={home.mood.percentile} label={home.mood.label} />
          ) : (
            <div className="skeleton h-full rounded-2xl" aria-busy="true" />
          )}
        </div>
      </section>

      {/* 2. KPI row. */}
      <section className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        {isLoading
          ? Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="skeleton h-28 rounded-2xl" aria-busy="true" />
            ))
          : home?.kpis.map((k, i) => <KpiCard key={k.id} kpi={k} index={i} />)}
      </section>

      {/* 3. Time Machine and the day it drives. */}
      <section>
        <div className="flex items-center justify-between gap-3 flex-wrap mb-3">
          <div>
            <h2 className="text-lg font-semibold">Time Machine</h2>
            <p className="text-xs" style={{ color: 'var(--muted)' }}>
              News against the Nifty across the window. The slider filters the whole
              page; the crosshair opens a day.
            </p>
          </div>
          <TimeMachineControls
            metric={metric}
            onMetric={setMetric}
            playing={playing}
            onPlay={() => {
              setPlaying((p) => !p)
              setFocusDate(null)
            }}
          />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
          <TimeMachine
            metric={metric}
            points={home?.main ?? []}
            events={home?.events ?? []}
            meta={meta}
            loading={isLoading}
            onFocus={setFocusDate}
            focusDate={focusDate}
          />
          <div className="lg:col-span-4">
            <DayPanel date={focusDate} />
          </div>
        </div>
      </section>

      {/* 4. Sector heat and surprise days, both cross-filtering. */}
      <section className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        <SectorTreemap rows={home?.sectorHeat ?? []} meta={meta} loading={isLoading} />
        <SurpriseCards days={home?.surprise ?? []} onPick={setFocusDate} />
      </section>

      {/* 5. Calendar and the sector race. */}
      <section className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        <CalendarHeatmap
          rows={calendar.data?.data ?? []}
          events={home?.events ?? []}
          meta={calMeta}
          loading={calendar.isLoading}
          error={calendar.error ? 'Could not load the calendar.' : null}
          onPick={setFocusDate}
        />
        <SectorRace rows={home?.sectorHeat ?? []} meta={meta} />
      </section>

      {/* 6. Insights and the routes onward. */}
      <section className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        <div className="lg:col-span-4">
          <InsightsCarousel cards={home?.insights ?? []} />
        </div>
        <div className="lg:col-span-8">
          <ActionTiles />
        </div>
      </section>
    </div>
  )
}
