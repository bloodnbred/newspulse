import { useCallback, useMemo, useState } from 'react'
import type {
  CubeField, CubeRow, CubeSchema, CubeShelfEntry, CubeViewKind, CubeZone,
} from '../types'

/** What the four zones hold, plus how the result should be drawn.
 *
 *  `filters` is a list of field ids rather than a map, because the chosen values
 *  live in a picker that has not been opened yet. A dimension on the Filters
 *  zone is "this view is sliced by Topic"; what it is sliced to is separate
 *  state, because that is a value, not a shape. */
export interface CubeViewState {
  cube: string
  rows: string[]
  columns: string[]
  values: string[]
  filters: string[]
  view: CubeViewKind
  orderBy: string | null
  descending: boolean
}

export const DEFAULT_VIEW: CubeViewState = {
  cube: 'news',
  // Topic by Year with headline volume is the view that answers "which subjects
  // did this outlet spend the window on", which is the first question anyone
  // opens an OLAP tool to ask. It is also 92 cells, which fits on a screen.
  rows: ['topic'],
  columns: ['year'],
  values: ['news_count'],
  filters: [],
  view: 'table',
  orderBy: null,
  descending: true,
}

/** Everything the shelf and the zones need to know about the current cube. */
export function useCubeView(schema: CubeSchema | undefined, view: CubeViewState) {
  const entry: CubeShelfEntry | undefined = useMemo(
    () => schema?.cubes.find((c) => c.id === view.cube),
    [schema, view.cube],
  )
  const all: CubeField[] = useMemo(
    () => (entry ? [...entry.dimensions, ...entry.measures] : []),
    [entry],
  )
  const byId = useMemo(() => {
    const m = new Map<string, CubeField>()
    for (const f of all) m.set(f.id, f)
    return m
  }, [all])
  return { entry, all, byId }
}

/** Add or remove a field from a zone.
 *
 *  Returns the next state rather than mutating, so the caller can decide whether
 *  the change is worth a query. Removing a field from Filters while it also sits
 *  on an axis is allowed and intentional - the axis groups, the filter cuts. */
export function toggleIn(list: string[], id: string, limit = Infinity): string[] {
  if (list.includes(id)) return list.filter((x) => x !== id)
  if (list.length >= limit) return list
  return [...list, id]
}

export const ROW_LIMIT = 1
export const COLUMN_LIMIT = 1
export const VALUE_LIMIT = 3
export const FILTER_LIMIT = 3
export const AXIS_LIMIT = ROW_LIMIT + COLUMN_LIMIT

export interface SavedView {
  name: string
  state: CubeViewState
  savedAt: string
}

const SAVED_KEY = 'newspulse.cube.views.v1'

/** Named views in localStorage.
 *
 *  `localStorage` can throw - Safari's private mode throws on write, and a
 *  quota error is not worth taking the page down for. So every access is
 *  guarded and a failure degrades to "saving is unavailable", which the page
 *  states rather than swallowing. */
export function useSavedViews() {
  const [views, setViews] = useState<SavedView[]>(() => {
    try {
      const raw = window.localStorage.getItem(SAVED_KEY)
      const parsed: unknown = raw ? JSON.parse(raw) : []
      return Array.isArray(parsed) ? (parsed as SavedView[]) : []
    } catch {
      return []
    }
  })

  const persist = useCallback((next: SavedView[]) => {
    setViews(next)
    try {
      window.localStorage.setItem(SAVED_KEY, JSON.stringify(next))
    } catch {
      /* reported by the caller through `canSave` */
    }
  }, [])

  const save = useCallback(
    (name: string, state: CubeViewState) => {
      const next = [...views.filter((v) => v.name !== name), { name, state, savedAt: new Date().toISOString() }]
      persist(next)
    },
    [views, persist],
  )
  const remove = useCallback((name: string) => persist(views.filter((v) => v.name !== name)), [views, persist])

  return { views, save, remove }
}

export function canSaveLocally(): boolean {
  try {
    const probe = '__np_probe__'
    window.localStorage.setItem(probe, '1')
    window.localStorage.removeItem(probe)
    return true
  } catch {
    return false
  }
}

/** The request body, in the shape `POST /ui/cube/query` expects. */
export function toRequest(
  view: CubeViewState,
  choices: Record<string, string[]>,
): Record<string, unknown> {
  const filters: Record<string, string[]> = {}
  for (const id of view.filters) {
    const picked = choices[id]
    if (picked && picked.length) filters[id] = picked
  }
  return {
    cube: view.cube,
    rows: view.rows,
    columns: view.columns,
    measures: view.values,
    filters,
    orderBy: view.orderBy,
    descending: view.descending,
  }
}

/** Format a measure by its unit.
 *
 *  The unit is carried from the server precisely so this is not a guess: a
 *  ratio shown as `0.038` and a count shown the same way are indistinguishable,
 *  and a reader who has to remember which is which will eventually read one as
 *  the other. */
export function formatCell(value: string | number | null, field: CubeField | undefined): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value
  switch (field?.unit) {
    case 'count':
      return Math.round(value).toLocaleString('en-IN')
    case 'ratio':
      return `${(value * 100).toFixed(1)}%`
    case 'pct':
      return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
    case 'score':
      return value.toFixed(3)
    case 'points':
      return value.toFixed(1)
    case 'year':
      return String(Math.round(value))
    default:
      return value.toLocaleString('en-IN', { maximumFractionDigits: 3 })
  }
}

/** The number to colour a cell by, without the unit getting in the way.
 *
 *  A measure whose values straddle zero is centred on zero: a return of +0.1%
 *  and a return of -0.1% are both small moves, and a scale that stretched zero
 *  to the middle of the range would paint them as opposites. An unsigned
 *  measure is scaled from its own minimum, because a share or a count has no
 *  meaningful zero to centre on. */
export function cellScale(
  rows: CubeRow[],
  measure: string,
  field: CubeField | undefined,
): { min: number; max: number; signed: boolean } {
  const values = rows
    .map((r) => r[measure])
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
  if (values.length === 0) return { min: 0, max: 1, signed: false }
  const min = Math.min(...values)
  const max = Math.max(...values)
  // A unit is the honest signal here: a percentage can be negative, a count and
  // a share cannot. Relying on "does this result happen to straddle zero" would
  // silently re-centre a percentage whose every cell happened to be positive,
  // which is a different colour scale for the same measure depending on the
  // filters - the exact thing a reader cannot see.
  const signed = field?.unit === 'pct' || (min < 0 && max > 0)
  return { min: signed ? Math.min(min, 0) : min, max, signed }
}

/** Axis order that reads as time.
 *
 *  A year is a number and a quarter is a number, so a default ascending sort
 *  puts 10 after 9 and Quarter 4 before Quarter 3. On a time axis that is not a
 *  detail - the reader sees a dip that is not there. */
export function axisOrder(values: string[]): string[] {
  const numeric = values.every((v) => v !== '' && !Number.isNaN(Number(v)))
  if (!numeric) return values
  return [...values].sort((a, b) => Number(a) - Number(b))
}

export const ZONE_LABELS: Record<CubeZone, string> = {
  rows: 'Rows',
  columns: 'Columns',
  values: 'Values',
  filters: 'Filters',
}

export const ZONE_HINTS: Record<CubeZone, string> = {
  rows: 'What each row is',
  columns: 'What each column is',
  values: 'The numbers to compute',
  filters: 'Dimensions to cut by',
}

export const ZONE_LIMITS = {
  rows: ROW_LIMIT,
  columns: COLUMN_LIMIT,
  values: VALUE_LIMIT,
  filters: FILTER_LIMIT,
} as const
