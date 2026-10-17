import { useMemo } from 'react'
import { useFilters } from '../lib/filters'
import { useApi } from '../lib/api'

/** The global filter bar: date range, sectors, and the market-report toggle.
 *
 *  Every control writes to the Zustand store, which writes to the URL. So the
 *  filter state is shareable and survives a reload, and a treemap click made
 *  three steps ago is still reflected here. The active count is shown because
 *  "why is the calendar showing four months?" is otherwise unanswerable. */
export function FilterBar() {
  const filters = useFilters((s) => s.filters)
  const set = useFilters((s) => s.set)
  const reset = useFilters((s) => s.reset)
  const toggleSector = useFilters((s) => s.toggleSector)
  const activeCount = useFilters((s) => s.activeCount)()

  const { data } = useApi<{ sector_name: string }[]>('/ui/sectors', {}, { withFilters: false })
  const sectors = useMemo(() => (data?.data ?? []).map((s) => s.sector_name), [data])

  return (
    <div className="glass px-4 py-3 mb-4 sticky top-0 z-20 flex flex-wrap items-end gap-3">
      <label className="field">
        <span className="field__label">From</span>
        <input
          className="field__input"
          type="date"
          value={filters.from}
          onChange={(e) => set({ from: e.target.value })}
        />
      </label>
      <label className="field">
        <span className="field__label">To</span>
        <input
          className="field__input"
          type="date"
          value={filters.to}
          onChange={(e) => set({ to: e.target.value })}
        />
      </label>

      <div className="field" style={{ minWidth: 260 }}>
        <span className="field__label">Sectors</span>
        <div className="flex flex-wrap gap-1">
          {sectors.map((s) => (
            <button
              key={s}
              className="pill"
              aria-pressed={filters.sectors.includes(s)}
              style={{
                cursor: 'pointer',
                borderColor: filters.sectors.includes(s) ? 'var(--news)' : undefined,
                color: filters.sectors.includes(s) ? 'var(--news)' : undefined,
              }}
              onClick={() => toggleSector(s)}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      <label className="field">
        <span className="field__label">Market-report headlines</span>
        <div className="flex items-center gap-2" style={{ minHeight: 30 }}>
          <input
            type="checkbox"
            checked={filters.includeMarketReports}
            onChange={(e) => set({ includeMarketReports: e.target.checked })}
          />
          <span className="text-xs" style={{ color: 'var(--muted)' }}>
            off by default — they describe a move that already happened
          </span>
        </div>
      </label>

      <div className="ml-auto flex items-center gap-3">
        {activeCount > 0 && (
          <span className="pill" style={{ borderColor: 'var(--news)' }}>
            {activeCount} filter{activeCount === 1 ? '' : 's'} active
          </span>
        )}
        <button className="btn" onClick={reset} disabled={activeCount === 0}>
          Reset
        </button>
      </div>
    </div>
  )
}

export default FilterBar
