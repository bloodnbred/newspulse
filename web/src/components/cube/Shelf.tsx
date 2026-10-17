import { useState } from 'react'
import { GripVertical, HelpCircle } from 'lucide-react'
import type { CubeField, CubeShelfEntry, CubeViewKind, CubeZone } from '../../types'
import {
  ZONE_HINTS, ZONE_LABELS, ZONE_LIMITS,
} from '../../lib/cube'

/** The field shelf.
 *
 *  Click-to-add is the primary interaction and drag is the accelerator, not the
 *  other way round. A reader who has never seen a pivot table cannot drag
 *  something they do not understand, but they can certainly click a thing that
 *  says what it does. Drag is wired natively (no dnd-kit) so the zone layout
 *  below needs no library and the payload is a field id in `dataTransfer`. */
export function FieldShelf({
  entry, onPick,
}: {
  entry: CubeShelfEntry
  onPick: (zone: CubeZone, id: string) => void
}) {
  const [open, setOpen] = useState<string | null>(null)

  const groups: { kind: CubeField['kind']; title: string; blurb: string }[] = [
    {
      kind: 'dimension',
      title: 'Dimensions',
      blurb: 'Groups of rows. Drop one on an axis to split the view by it, or on Filters to cut it.',
    },
    {
      kind: 'measure',
      title: 'Measures',
      blurb: 'Numbers computed over each group. They are divisions of stored sums, computed in the query.',
    },
  ]

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h3 className="text-sm font-semibold">{entry.label}</h3>
        <p className="text-xs mono" style={{ color: 'var(--muted)' }}>{entry.grain}</p>
      </div>

      {groups.map((group) => {
        const fields = [...entry.dimensions, ...entry.measures].filter(
          (f) => f.kind === group.kind,
        )
        return (
          <section key={group.kind}>
            <h4 className="text-[11px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
              {group.title}
            </h4>
            <p className="text-xs mt-1 mb-2" style={{ color: 'var(--muted)', lineHeight: 1.5 }}>
              {group.blurb}
            </p>
            <ul className="flex flex-col gap-1">
              {fields.map((field) => {
                const showing = open === field.id
                return (
                  <li key={field.id}>
                    <div className="flex items-stretch gap-1">
                      <button
                        type="button"
                        draggable
                        onDragStart={(e) => {
                          e.dataTransfer.setData('text/cube-field', field.id)
                          e.dataTransfer.effectAllowed = 'copy'
                        }}
                        onClick={() => setOpen(showing ? null : field.id)}
                        aria-expanded={showing}
                        className="flex items-center gap-2 flex-1 text-left px-2 py-1.5 rounded-lg"
                        style={{
                          background: showing ? 'rgba(20,184,166,.10)' : 'rgba(255,255,255,.03)',
                          border: '1px solid rgba(148,163,184,.14)',
                          cursor: 'grab',
                        }}
                      >
                        <GripVertical size={12} style={{ color: 'var(--faint)', flexShrink: 0 }} />
                        <span className="text-xs">{field.label}</span>
                        {field.derived && (
                          <span
                            className="text-[9px] uppercase px-1 rounded"
                            style={{ background: 'rgba(20,184,166,.16)', color: 'var(--news)' }}
                            title="Computed from a measure rather than read from a dimension"
                          >
                            derived
                          </span>
                        )}
                        <HelpCircle size={12} style={{ color: 'var(--faint)', marginLeft: 'auto', flexShrink: 0 }} />
                      </button>
                    </div>

                    {showing && (
                      <>
                        <p className="text-xs mt-1 ml-6" style={{ color: 'var(--muted)', lineHeight: 1.5 }}>
                          {field.description}
                        </p>
                        <div className="flex flex-wrap gap-1 mt-1 ml-6">
                          {(field.kind === 'measure'
                            ? (['values'] as CubeZone[])
                            : (['rows', 'columns', 'filters'] as CubeZone[])
                          ).map((zone) => (
                            <button
                              key={zone}
                              type="button"
                              className="text-[10px] px-2 py-0.5 rounded-full border"
                              style={{ borderColor: 'rgba(148,163,184,.3)', color: 'var(--muted)' }}
                              onClick={() => onPick(zone, field.id)}
                            >
                              add to {ZONE_LABELS[zone]}
                            </button>
                          ))}
                        </div>
                      </>
                    )}
                  </li>
                )
              })}
            </ul>
          </section>
        )
      })}

      <section>
        <h4 className="text-[11px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
          About this cube
        </h4>
        <ul className="mt-1 flex flex-col gap-1">
          {entry.notes.map((note) => (
            <li key={note} className="text-xs" style={{ color: 'var(--muted)', lineHeight: 1.5 }}>
              {note}
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}

/** One drop zone.
 *
 *  The limit is enforced here as well as in the engine: a zone that silently
 *  ignored a fourth field would look like a bug, and one that accepted it and
 *  then failed the query would look like a broken page. It says no instead. */
export function Zone({
  zone, fields, onAdd, onRemove, highlight, chosen,
}: {
  zone: CubeZone
  fields: { id: string; label: string; derived: boolean }[]
  onAdd: (id: string) => void
  onRemove: (id: string) => void
  highlight: boolean
  chosen?: Record<string, string[]>
}) {
  const [over, setOver] = useState(false)
  const limit = ZONE_LIMITS[zone]
  const full = fields.length >= limit

  return (
    <div
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes('text/cube-field')) return
        e.preventDefault()
        e.dataTransfer.dropEffect = 'copy'
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        const id = e.dataTransfer.getData('text/cube-field')
        if (id) onAdd(id)
      }}
      className="rounded-xl p-3"
      style={{
        border: `1px solid ${
          over ? 'var(--news)' : highlight ? 'rgba(20,184,166,.45)' : 'rgba(148,163,184,.16)'
        }`,
        background: over
          ? 'rgba(20,184,166,.10)'
          : highlight
            ? 'rgba(20,184,166,.05)'
            : 'rgba(255,255,255,.02)',
      }}
    >
      <div className="flex items-baseline justify-between gap-2">
        <h4 className="text-[11px] uppercase tracking-widest">{ZONE_LABELS[zone]}</h4>
        <span className="text-[10px]" style={{ color: 'var(--muted)' }}>
          {ZONE_HINTS[zone]} · {fields.length}/{limit}
        </span>
      </div>

      {fields.length === 0 ? (
        <p className="text-xs mt-2" style={{ color: 'var(--faint)' }}>
          {zone === 'values'
            ? 'Nothing to compute yet'
            : zone === 'filters'
              ? 'No cut applied'
              : 'Empty — drop a dimension here'}
        </p>
      ) : (
        <ul className="flex flex-wrap gap-1 mt-2">
          {fields.map((f) => (
            <li
              key={f.id}
              className="flex items-center gap-1 text-xs px-2 py-1 rounded-lg"
              style={{ background: 'rgba(255,255,255,.05)' }}
            >
              <span>{f.label}</span>
              {f.derived && (
                <span className="text-[9px]" style={{ color: 'var(--news)' }} title="Derived from a measure">◇</span>
              )}
              <button
                type="button"
                onClick={() => onRemove(f.id)}
                aria-label={`Remove ${f.label} from ${ZONE_LABELS[zone]}`}
                className="ml-1"
                style={{ color: 'var(--muted)' }}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      {chosen && Object.keys(chosen).length > 0 && (
        <ul className="mt-2 flex flex-col gap-1">
          {Object.entries(chosen).map(([id, values]) => (
            <li key={id} className="text-[10px]" style={{ color: 'var(--muted)' }}>
              {id}: {values.join(', ')}
            </li>
          ))}
        </ul>
      )}

      {over && full && (
        <p className="text-[10px] mt-1" style={{ color: 'var(--caution)' }}>
          {ZONE_LABELS[zone]} holds {limit}. Remove one first.
        </p>
      )}
    </div>
  )
}

/** The six operations, each in one line, each naming the zones it touches. */
export function OperationChips({
  operations, active, onPick,
}: {
  operations: { id: string; label: string; blurb: string; zones: string[]; hint: string }[]
  active: string | null
  onPick: (id: string) => void
}) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="OLAP operations">
      {operations.map((op) => {
        const on = active === op.id
        return (
          <button
            key={op.id}
            type="button"
            onClick={() => onPick(on ? '' : op.id)}
            aria-pressed={on}
            title={op.hint}
            className="text-xs px-2.5 py-1 rounded-full"
            style={{
              border: `1px solid ${on ? 'var(--news)' : 'rgba(148,163,184,.22)'}`,
              background: on ? 'rgba(20,184,166,.12)' : 'transparent',
              color: on ? 'var(--news)' : 'var(--muted)',
            }}
          >
            {op.label}
          </button>
        )
      })}
    </div>
  )
}

export const VIEW_KINDS: { id: CubeViewKind; label: string }[] = [
  { id: 'table', label: 'Table' },
  { id: 'heatmap', label: 'Heatmap' },
  { id: 'bars', label: 'Bars' },
  { id: 'treemap', label: 'Treemap' },
]
