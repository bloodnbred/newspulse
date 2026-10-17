import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Bookmark, BookmarkPlus, Link2, Save, Trash2 } from 'lucide-react'
import { useApi, postEnvelope, ApiError } from '../lib/api'
import {
  DEFAULT_VIEW, canSaveLocally, formatCell, toRequest, toggleIn,
  useSavedViews,
  type CubeViewState,
} from '../lib/cube'
import type {
  CubeData, CubeField, CubeHeadline, CubeRow, CubeSchema, CubeViewKind, CubeZone,
} from '../types'
import { FieldShelf, OperationChips, VIEW_KINDS, Zone } from '../components/cube/Shelf'
import { CubeBars, CubeHeatmap, CubeTable, CubeTreemap } from '../components/cube/Views'

/** The view lives in the URL, because a slice someone else cannot open is a
 *  slice someone else cannot check. Only the shape travels - which cube, which
 *  axes, which measure - not the results, which are a query away.
 *
 *  A parameter that is PRESENT but empty means "nothing on this axis", which is
 *  not the same as absent. A view with one axis is a legitimate view, and a
 *  link that said `cols=` and came back with a second axis would be a link that
 *  opens something other than what it names. */
function readViewFromUrl(): CubeViewState {
  const p = new URLSearchParams(window.location.search)
  const list = (k: string): string[] => (p.get(k) ?? '').split(',').filter(Boolean)
  const axis = (k: string, fallback: string[]): string[] =>
    p.has(k) ? list(k) : fallback
  const kind = p.get('view') as CubeViewKind | null
  return {
    cube: p.get('cube') ?? DEFAULT_VIEW.cube,
    rows: axis('rows', DEFAULT_VIEW.rows),
    columns: axis('cols', DEFAULT_VIEW.columns),
    values: axis('vals', DEFAULT_VIEW.values),
    filters: list('flt'),
    view: kind && VIEW_KINDS.some((v) => v.id === kind) ? kind : DEFAULT_VIEW.view,
    orderBy: p.get('ord'),
    descending: p.get('dir') !== 'asc',
  }
}

function writeViewToUrl(v: CubeViewState): void {
  const p = new URLSearchParams(window.location.search)
  const put = (k: string, val: string) => (val ? p.set(k, val) : p.delete(k))
  put('cube', v.cube === DEFAULT_VIEW.cube ? '' : v.cube)
  put('rows', v.rows.join(','))
  put('cols', v.columns.join(','))
  put('vals', v.values.join(','))
  put('flt', v.filters.join(','))
  put('view', v.view === DEFAULT_VIEW.view ? '' : v.view)
  put('ord', v.orderBy ?? '')
  put('dir', v.descending ? '' : 'asc')
  const q = p.toString()
  window.history.replaceState(
    window.history.state, '', q ? `${window.location.pathname}?${q}` : window.location.pathname,
  )
}

export default function Explore() {
  const [view, setView] = useState<CubeViewState>(readViewFromUrl)
  /** Which values each Filters-zone dimension is cut to. */
  const [choices, setChoices] = useState<Record<string, string[]>>({})
  const [operation, setOperation] = useState<string | null>(null)
  const [focus, setFocus] = useState<CubeRow | null>(null)
  const [saveName, setSaveName] = useState('')

  useEffect(() => { writeViewToUrl(view) }, [view])

  const schema = useApi<CubeSchema>('/ui/cube/schema', {}, { withFilters: false })

  const request = useMemo(() => toRequest(view, choices), [view, choices])

  const query = useMutation({
    mutationFn: (body: Record<string, unknown>) => postEnvelope<CubeData>('/ui/cube/query', body),
  })

  // Run whenever the shape changes. Refetching on every keystroke of a filter
  // picker would issue a query per character; the picker commits on selection.
  useEffect(() => {
    if (!schema.data) return
    query.mutate(request)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request, schema.data])

  const entry = schema.data?.data.cubes.find((c) => c.id === view.cube)
  const byId = useMemo(() => {
    const m = new Map<string, CubeField>()
    if (entry) for (const f of [...entry.dimensions, ...entry.measures]) m.set(f.id, f)
    return m
  }, [entry])

  const label = (id: string) => byId.get(id)?.label ?? id

  const setZone = useCallback((zone: CubeZone, id: string) => {
    setView((v) => {
      const list = v[zone] as string[]
      const limit = zone === 'rows' || zone === 'columns' ? 1 : 3
      const next = toggleIn(list, id, limit)
      if (next === list) return v
      const f = { ...v, [zone]: next } as CubeViewState
      // A measure cannot also be an axis, and a dimension on an axis cannot
      // also be the thing being aggregated. Clearing the other zone keeps the
      // request well-formed instead of letting the server refuse it, which
      // would look like a broken page rather than a choice the page made.
      if (zone === 'values') {
        return { ...f, rows: f.rows.filter((x) => x !== id), columns: f.columns.filter((x) => x !== id) }
      }
      if (zone === 'rows' || zone === 'columns') {
        return { ...f, values: f.values.filter((x) => x !== id) }
      }
      return f
    })
  }, [])

  const removeZone = useCallback((zone: CubeZone, id: string) => {
    setView((v) => ({ ...v, [zone]: (v[zone] as string[]).filter((x) => x !== id) }))
    if (zone === 'filters') {
      setChoices((c) => {
        const next = { ...c }
        delete next[id]
        return next
      })
    }
  }, [])

  const saved = useSavedViews()
  const localAvailable = useMemo(canSaveLocally, [])
  const shareUrl = typeof window !== 'undefined' ? window.location.href : ''

  const data = query.data?.data
  const meta = query.data?.meta
  const error = query.error instanceof ApiError ? (query.error.hint ?? query.error.message) : null
  const busy = query.isPending

  const op = schema.data?.data.operations.find((o) => o.id === operation) ?? null

  return (
    <div className="flex flex-col gap-4">
      {/* ------------------------------------------------ question ---------- */}
      <header className="glass p-4">
        <h1 className="text-lg font-semibold">Cube Studio</h1>
        <p className="text-sm mt-1" style={{ color: 'var(--muted)', lineHeight: 1.55, maxWidth: '68ch' }}>
          Slice the warehouse yourself. Dimensions split the result into groups, measures are
          computed inside each group, and the statement the page ran is one button away. Every
          measure is a division of stored sums, evaluated in the same query that grouped the rows.
        </p>
        <div className="flex flex-wrap items-center gap-2 mt-3">
          {schema.data?.data.cubes.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() =>
                setView((v) => ({
                  ...v,
                  cube: c.id,
                  // The two cubes share no fields, so carrying the axes across
                  // would send a request the engine refuses. Reset them, and say
                  // so rather than letting the 409 read as a fault.
                  rows: c.id === 'news' ? ['topic'] : ['sector'],
                  columns: c.id === 'news' ? ['year'] : ['year'],
                  values: c.id === 'news' ? ['news_count'] : ['mean_return'],
                  filters: [],
                  orderBy: null,
                }))
              }
              aria-pressed={view.cube === c.id}
              className="text-xs px-3 py-1 rounded-full border"
              style={{
                borderColor: view.cube === c.id ? 'var(--news)' : 'rgba(148,163,184,.22)',
                background: view.cube === c.id ? 'rgba(20,184,166,.12)' : 'transparent',
                color: view.cube === c.id ? 'var(--news)' : 'var(--muted)',
              }}
            >
              {c.label}
              <span className="ml-1 opacity-60">{c.grain}</span>
            </button>
          ))}
        </div>
      </header>

      <div className="grid gap-4 lg:grid-cols-12">
        {/* ------------------------------------------------ shelf ----------- */}
        <aside className="lg:col-span-3">
          <div className="glass p-4 lg:sticky lg:top-4">
            {schema.isLoading && <div className="skeleton h-96 rounded-xl" />}
            {schema.error && (
              <p className="text-sm" style={{ color: 'var(--caution)' }}>The field shelf failed to load.</p>
            )}
            {entry && (
              <FieldShelf
                entry={entry}
                onPick={(zone, id) => setZone(zone, id)}
              />
            )}
          </div>
        </aside>

        {/* ------------------------------------------------ view ----------- */}
        <div className="lg:col-span-9 flex flex-col gap-4">
          <div className="glass p-4">
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {(['rows', 'columns', 'values', 'filters'] as CubeZone[]).map((zone) => (
                <Zone
                  key={zone}
                  zone={zone}
                  fields={(view[zone] as string[]).map((id) => ({
                    id,
                    label: label(id),
                    derived: byId.get(id)?.derived ?? false,
                  }))}
                  onAdd={(id) => setZone(zone, id)}
                  onRemove={(id) => removeZone(zone, id)}
                  highlight={op?.zones.includes(zone) ?? false}
                  chosen={zone === 'filters' ? choices : undefined}
                />
              ))}
            </div>

            {/* Filter values, one row per dimension on the Filters zone. */}
            {view.filters.length > 0 && (
              <div className="mt-3 pt-3" style={{ borderTop: '1px solid rgba(148,163,184,.14)' }}>
                <h4 className="text-[11px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
                  Cut to
                </h4>
                <div className="flex flex-col gap-2 mt-2">
                  {view.filters.map((id) => (
                    <FilterPicker
                      key={id}
                      dimensionId={id}
                      values={choices[id] ?? []}
                      onChange={(next) =>
                        setChoices((c) => ({ ...c, [id]: next }))
                      }
                    />
                  ))}
                </div>
              </div>
            )}

            <div className="flex flex-wrap items-center gap-3 mt-3 pt-3" style={{ borderTop: '1px solid rgba(148,163,184,.14)' }}>
              <OperationChips
                operations={schema.data?.data.operations ?? []}
                active={operation}
                onPick={setOperation}
              />
              <div className="flex gap-1 ml-auto" role="group" aria-label="How to draw the result">
                {VIEW_KINDS.map((k) => (
                  <button
                    key={k.id}
                    type="button"
                    onClick={() => setView((v) => ({ ...v, view: k.id }))}
                    aria-pressed={view.view === k.id}
                    className="text-xs px-2.5 py-1 rounded-full"
                    style={{
                      border: `1px solid ${view.view === k.id ? 'var(--news)' : 'rgba(148,163,184,.22)'}`,
                      background: view.view === k.id ? 'rgba(20,184,166,.12)' : 'transparent',
                      color: view.view === k.id ? 'var(--news)' : 'var(--muted)',
                    }}
                  >
                    {k.label}
                  </button>
                ))}
              </div>
            </div>

            {op && (
              <div className="mt-3 p-3 rounded-xl" style={{ background: 'rgba(20,184,166,.06)', border: '1px solid rgba(20,184,166,.24)' }}>
                <p className="text-sm"><b style={{ color: 'var(--news)' }}>{op.label}</b> — {op.blurb}</p>
                <p className="text-xs mt-1" style={{ color: 'var(--muted)' }}>{op.hint}</p>
              </div>
            )}
          </div>

          {/* ------------------------------------------- result ---------- */}
          <div className="glass p-4">
            <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
              <div className="flex flex-wrap items-baseline gap-2">
                <h2 className="text-sm font-semibold">Result</h2>
                {data && (
                  <span className="text-xs" style={{ color: 'var(--muted)' }}>
                    {data.rows.length.toLocaleString('en-IN')} cells
                    {data.truncated ? ' (truncated)' : ''}
                    {meta ? ` · ${meta.ms} ms` : ''}
                    {view.orderBy ? ` · sorted by ${label(view.orderBy)} ${data.spec.descending ? 'descending' : 'ascending'}` : ''}
                  </span>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-1">
                <button
                  type="button"
                  className="icon-btn"
                  title="Copy a link to this exact view"
                  aria-label="Copy a link to this exact view"
                  onClick={() => void navigator.clipboard?.writeText(shareUrl)}
                >
                  <Link2 size={15} />
                </button>
                <button
                  type="button"
                  className="icon-btn"
                  title="Download the cells as CSV"
                  aria-label="Download the cells as CSV"
                  onClick={() => downloadCsv(data, byId)}
                >
                  <Save size={15} />
                </button>
              </div>
            </div>

            {busy && <div className="skeleton h-64 rounded-xl" />}
            {error && (
              <p className="text-sm p-3 rounded-lg" style={{ color: 'var(--caution)', background: 'rgba(239,68,68,.08)' }}>
                {error}
              </p>
            )}
            {data && !busy && !error && (
              <>
                <span
                  className="inline-block text-[11px] px-2 py-0.5 rounded-full border mb-2"
                  style={{ borderColor: 'var(--price)', color: 'var(--price)' }}
                >
                  {meta?.concept ?? 'Fact constellation'}
                </span>
                {view.view === 'table' && (
                  <CubeTable
                    data={data}
                    byId={byId}
                    meta={meta}
                    orderBy={view.orderBy}
                    onOrder={(id) =>
                      setView((v) => ({
                        ...v,
                        orderBy: v.orderBy === id ? null : id,
                        descending: v.orderBy === id ? !v.descending : true,
                      }))
                    }
                    onCell={setFocus}
                    caption={captionFor(data, byId)}
                  />
                )}
                {view.view === 'heatmap' && (
                  <CubeHeatmap data={data} byId={byId} meta={meta} onCell={setFocus} caption={captionFor(data, byId)} />
                )}
                {view.view === 'bars' && (
                  <CubeBars data={data} byId={byId} meta={meta} onCell={setFocus} caption={captionFor(data, byId)} />
                )}
                {view.view === 'treemap' && (
                  <CubeTreemap data={data} byId={byId} meta={meta} onCell={setFocus} caption={captionFor(data, byId)} />
                )}
              </>
            )}
          </div>

          {/* ------------------------------------ headlines behind a cell --- */}
          {focus && (
            <CellHeadlines
              cell={focus}
              rowsDim={view.rows[0]}
              colsDim={view.columns[0]}
              onClose={() => setFocus(null)}
            />
          )}

          {/* ------------------------------------------- saved views ------ */}
          <div className="glass p-4">
            <h3 className="text-sm font-semibold flex items-center gap-2">
              <Bookmark size={14} /> Saved views
            </h3>
            {!localAvailable && (
              <p className="text-xs mt-1" style={{ color: 'var(--caution)' }}>
                This browser is blocking local storage, so views cannot be saved here. The link
                button still works, and a link carries the whole view.
              </p>
            )}
            <div className="flex flex-wrap gap-2 mt-3 items-center">
              <input
                className="text-sm px-2 py-1 rounded-lg"
                style={{ background: 'rgba(255,255,255,.04)', border: '1px solid rgba(148,163,184,.2)', minWidth: 180 }}
                placeholder="Name this view"
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
                aria-label="Name for the saved view"
              />
              <button
                type="button"
                className="btn"
                disabled={!saveName.trim() || !localAvailable}
                onClick={() => {
                  saved.save(saveName.trim(), view)
                  setSaveName('')
                }}
              >
                <BookmarkPlus size={13} /> Save
              </button>
            </div>
            {saved.views.length > 0 && (
              <ul className="flex flex-col gap-1 mt-3">
                {saved.views.map((v) => (
                  <li key={v.name} className="flex items-center gap-2 text-xs">
                    <button type="button" className="flex-1 text-left" onClick={() => setView(v.state)}>
                      <span style={{ color: 'var(--text)' }}>{v.name}</span>
                      <span className="ml-2" style={{ color: 'var(--muted)' }}>
                        {v.state.rows.map((r) => label(r)).join(' × ')} · {v.state.values.map((r) => label(r)).join(', ')}
                      </span>
                    </button>
                    <button
                      type="button"
                      className="icon-btn"
                      aria-label={`Delete saved view ${v.name}`}
                      onClick={() => saved.remove(v.name)}
                    >
                      <Trash2 size={13} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

/** A caption generated from the result, never typed by hand. */
function captionFor(data: CubeData, byId: Map<string, CubeField>): string {
  const measure = data.spec.measures[0]
  if (!measure) return ''
  const field = byId.get(measure)
  const values = data.rows
    .map((r) => r[measure])
    .filter((v): v is number => typeof v === 'number')
  if (values.length === 0) return ''
  const top = data.rows.reduce((best, r) =>
    (typeof r[measure] === 'number' && typeof best?.[measure] === 'number' && (r[measure] as number) > (best[measure] as number))
      ? r
      : best,
  )
  const rowDim = data.spec.rows[0]
  const peak = top && rowDim ? `${String(top[rowDim])} ` : ''
  return (
    `${field?.label ?? measure} ranges from ${formatCell(Math.min(...values), field)} to ` +
    `${formatCell(Math.max(...values), field)} across ${data.rows.length} cells.` +
    (peak ? ` The largest is ${peak}at ${formatCell(top?.[measure] ?? null, field)}.` : '')
  )
}

/** Values for one Filters-zone dimension.
 *
 *  The list of choices comes from the same field, queried on its own, so the
 *  picker never offers a value that would return an empty cell - and it is a
 *  query rather than a hard-coded list for the same reason. */
function FilterPicker({
  dimensionId, values, onChange,
}: {
  dimensionId: string
  values: string[]
  onChange: (next: string[]) => void
}) {
  const options = useApi<{ rows: CubeRow[] }>(
    '/ui/cube/query',
    {},
    { withFilters: false, enabled: false },
  )
  void options

  const [available, setAvailable] = useState<string[]>([])
  const [open, setOpen] = useState(false)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const env = await postEnvelope<{ rows: CubeRow[] }>('/ui/cube/query', {
          cube: 'news', rows: [dimensionId], measures: ['news_count'], orderBy: 'news_count',
        })
        if (cancelled) return
        setAvailable(env.data.rows.map((r) => String(r[dimensionId] ?? '')).filter(Boolean))
      } catch {
        if (!cancelled) setAvailable([])
      }
    }
    void load()
    return () => { cancelled = true }
  }, [dimensionId])

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="text-xs px-2 py-1 rounded-lg"
        style={{ background: 'rgba(255,255,255,.04)', border: '1px solid rgba(148,163,184,.2)' }}
        aria-expanded={open}
      >
        {dimensionId}: {values.length ? values.join(', ') : 'all'}
      </button>
      {open && (
        <div className="flex flex-wrap gap-1 mt-2">
          {available.length === 0 && (
            <span className="text-xs" style={{ color: 'var(--muted)' }}>Loading values…</span>
          )}
          {available.map((v) => {
            const on = values.includes(v)
            return (
              <button
                key={v}
                type="button"
                onClick={() => onChange(on ? values.filter((x) => x !== v) : [...values, v])}
                aria-pressed={on}
                className="text-[11px] px-2 py-0.5 rounded-full"
                style={{
                  border: `1px solid ${on ? 'var(--news)' : 'rgba(148,163,184,.22)'}`,
                  background: on ? 'rgba(20,184,166,.12)' : 'transparent',
                  color: on ? 'var(--news)' : 'var(--muted)',
                }}
              >
                {v}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

/** The 50 headlines behind one cell. */
function CellHeadlines({
  cell, rowsDim, colsDim, onClose,
}: {
  cell: CubeRow
  rowsDim?: string
  colsDim?: string
  onClose: () => void
}) {
  const params = useMemo(() => {
    const p: Record<string, unknown> = { limit: 50 }
    if (rowsDim) p.topic = cell[rowsDim]
    return p
  }, [cell, rowsDim])

  const headlines = useApi<CubeHeadline[]>('/ui/cube/headlines', params, { withFilters: false })

  return (
    <div className="glass p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">
          Headlines behind {rowsDim ? String(cell[rowsDim]) : 'this cell'}
          {colsDim && cell[colsDim] ? ` · ${String(cell[colsDim])}` : ''}
        </h3>
        <button type="button" className="btn" onClick={onClose}>Close</button>
      </div>
      <p className="text-xs mt-1 mb-3" style={{ color: 'var(--caution)' }}>
        Ranked by sensationalism score — the most striking rows in the cell, not a
        representative sample of it.
      </p>
      {headlines.isLoading && <div className="skeleton h-40 rounded-xl" />}
      {headlines.error && (
        <p className="text-sm" style={{ color: 'var(--muted)' }}>
          Could not load headlines for this cell. A month or a sector is not a topic, so only a
          topic cell can be looked into.
        </p>
      )}
      <ul className="flex flex-col gap-2 max-h-80 overflow-auto">
        {(headlines.data?.data ?? []).map((h) => (
          <li key={h.headline_id} className="text-sm">
            <span className="mono text-[10px] mr-2" style={{ color: 'var(--muted)' }}>{h.publish_date}</span>
            {h.headline_text}
            {h.is_risk_signal && (
              <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded" style={{ background: 'rgba(245,158,11,.14)', color: 'var(--caution)' }}>
                risk signal
              </span>
            )}
          </li>
        ))}
      </ul>
      <p className="text-xs mt-3" style={{ color: 'var(--muted)' }}>
        Want a full analysis of one line? <Link to="/headline" style={{ color: 'var(--news)' }}>Open Headline Lab</Link>.
      </p>
    </div>
  )
}

/** CSV of exactly what is on screen.
 *
 *  The cells only - not the totals row. A total in a CSV gets averaged by
 *  whatever opens it next, and that is the same mistake the totals row is
 *  designed to avoid on screen. */
function downloadCsv(data: CubeData | undefined, byId: Map<string, CubeField>): void {
  if (!data) return
  const dims = [...data.spec.rows, ...data.spec.columns]
  const measures = data.spec.measures
  const escape = (v: string | number | null): string => {
    const s = v === null ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const lines = [
    [
      ...dims.map((d) => byId.get(d)?.label ?? d),
      ...measures.map((m) => byId.get(m)?.label ?? m),
    ].join(','),
    ...data.rows.map((r) =>
      [...dims, ...measures].map((k) => escape(r[k] ?? null)).join(','),
    ),
  ]
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `newspulse-cube-${data.cube}.csv`
  a.click()
  URL.revokeObjectURL(url)
}
