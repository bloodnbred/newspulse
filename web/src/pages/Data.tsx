import { useCallback, useMemo, useState } from 'react'
import {
  Background, Controls, Handle, Position, ReactFlow, type Edge, type Node,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { AlertTriangle, Database, GitBranch, Table2 } from 'lucide-react'
import { useApi } from '../lib/api'
import { fmtNum } from '../lib/format'
import Chart from '../components/Chart'
import { SEMANTIC } from '../lib/echartsTheme'
import type { EtlStep, SchemaTable } from '../types'

/** Fact tables are the subject; everything else hangs off one.
 *
 *  The distinction is a grain statement, not a styling choice: a fact table has
 *  one row per event, a dimension has one row per attribute value, and joining
 *  two facts is where a count starts meaning something it did not before.
 */
const FACT_TABLES = new Set([
  'fact_headline', 'fact_statement', 'fact_stock_daily', 'fact_market_daily',
  'fact_news_sector_daily', 'fact_news_stock_mention',
])

const KEY_COLUMNS = /_key$|^date_key$|^topic_key$|^sector_key$|^stock_key$/

/** Mining and audit tables. Results rather than part of the constellation. */
const RESULT_TABLES = /^(mining_|etl_audit)/

interface TableNodeData extends Record<string, unknown> {
  table: SchemaTable;
  onSelect: (name: string) => void;
  selected: boolean;
}

function TableNode({ data }: { data: TableNodeData }) {
  const { table, onSelect, selected } = data
  const isFact = FACT_TABLES.has(table.name)
  const accent = isFact ? 'var(--news)' : 'var(--muted)'
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect(table.name)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') onSelect(table.name)
      }}
      className="rounded-xl px-3 py-2 cursor-pointer min-w-[170px]"
      style={{
        background: 'var(--surface-solid)',
        border: `1px solid ${selected ? accent : 'rgba(148,163,184,.22)'}`,
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: accent, opacity: 0.5 }} />
      <div className="flex items-center gap-1.5">
        {isFact ? <Database size={11} style={{ color: accent }} /> : <Table2 size={11} style={{ color: accent }} />}
        <span className="text-[11px] font-semibold">{table.name}</span>
      </div>
      <div className="mono text-[10px] mt-0.5" style={{ color: 'var(--muted)' }}>
        {fmtNum(table.rows)} rows · {table.column_count} cols
      </div>
      <Handle type="source" position={Position.Right} style={{ background: accent, opacity: 0.5 }} />
    </div>
  )
}

const NODE_TYPES = { table: TableNode }

/** Lay the constellation out in columns by role.
 *
 *  A deterministic layered layout rather than a force simulation: the point of
 *  this diagram is that a reader can see the shape at a glance, and a force
 *  layout moves the same nodes differently on every load.
 */
function useConstellation(
  tables: SchemaTable[],
  selected: string | null,
  onSelect: (n: string) => void,
  showResults: boolean,
) {
  return useMemo(() => {
    const byName = new Map(tables.map((t) => [t.name, t]))
    const facts = tables.filter((t) => FACT_TABLES.has(t.name))
    const dims = tables.filter((t) => t.name.startsWith('dim_'))
    const bridges = tables.filter(
      (t) =>
        !FACT_TABLES.has(t.name) &&
        !t.name.startsWith('dim_') &&
        !RESULT_TABLES.test(t.name),
    )
    const cubes = tables.filter((t) => t.name.startsWith('cube_'))
    const results = showResults ? tables.filter((t) => RESULT_TABLES.test(t.name)) : []

    const nodes: Node[] = []
    const edges: Edge[] = []
    const place = (list: SchemaTable[], x: number, column: number) => {
      list.forEach((t, i) => {
        nodes.push({
          id: t.name,
          type: 'table',
          position: { x, y: i * 74 },
          data: { table: t, onSelect, selected: t.name === selected } satisfies TableNodeData,
        })
      })
      return column
    }

    let column = 0
    column = place(dims, 0, column)
    column += 1
    place(facts, 240, column)
    column += 1
    place(bridges, 480, column)
    place(cubes, 480, column + 12)
    // Mining and audit tables are OUTPUTS, not part of the constellation, and
    // they hold no `_key` to anything. Shown in their own column and behind a
    // toggle: with them inline they took a third of the diagram and pushed the
    // fact-to-dimension relationships the page exists to show off screen.
    place(results, 720, column + 20)

    // Edges only where the star-schema naming actually declares a relationship:
    // A holds a column called `<dim>_key` for dimension table `dim_<dim>`.
    //
    // Matching on any shared `_key` column instead produced several hundred
    // edges across 43 tables - a hairball in which no individual relationship is
    // readable, which is the opposite of what a schema diagram is for. The
    // declared form is also the honest one: it draws the keys the project
    // actually names, rather than every column pair that happens to share a
    // suffix.
    for (const t of tables) {
      for (const col of t.columns) {
        if (!KEY_COLUMNS.test(col.name)) continue
        // `topic_key` -> `dim_topic`; `effective_trading_date_key` -> `dim_date`.
        const referenced = col.name
          .replace(/_key$/, '')
          .replace(/^effective_trading_date$/, 'date');
        const targetName = `dim_${referenced}`;
        const target = byName.get(targetName);
        if (!target || target === t) continue;
        const from = nodes.find((n) => n.id === t.name);
        const to = nodes.find((n) => n.id === targetName);
        if (!from || !to) continue;
        const key = `${t.name}->${targetName}:${col.name}`;
        if (edges.some((e) => e.id === key)) continue;
        edges.push({
          id: key,
          source: t.name,
          target: targetName,
          style: {
            stroke: FACT_TABLES.has(t.name) ? 'rgba(20,184,166,.35)' : 'rgba(148,163,184,.25)',
            strokeWidth: 1,
          },
          label: col.name,
          labelStyle: { fill: 'var(--faint)', fontSize: 8 },
          labelBgStyle: { fill: 'transparent' },
        });
      }
    }
    return { nodes, edges, resultCount: tables.filter((t) => RESULT_TABLES.test(t.name)).length }
  }, [tables, selected, onSelect, showResults])
}

export default function Data() {
  const schema = useApi<{ tables: SchemaTable[] }>('/ui/schema', {}, { withFilters: false })
  const etl = useApi<EtlStep[]>('/ui/etl', {}, { withFilters: false })
  // In the URL, like the Mining tabs: "here is the ETL funnel" is a thing
  // people say to each other, and a local-only tab cannot be linked to.
  const [tab, setTab] = useState<'schema' | 'etl'>(() => {
    const q = new URLSearchParams(window.location.search).get('tab')
    return q === 'etl' ? 'etl' : 'schema'
  })
  const chooseTab = (next: 'schema' | 'etl') => {
    setTab(next)
    const p = new URLSearchParams(window.location.search)
    if (next === 'schema') p.delete('tab')
    else p.set('tab', next)
    const q = p.toString()
    window.history.replaceState(
      window.history.state, '', q ? `${window.location.pathname}?${q}` : window.location.pathname,
    )
  }
  const [selected, setSelected] = useState<string | null>(null)

  const tables = schema.data?.data.tables ?? []
  const onSelect = useCallback((name: string) => setSelected((s) => (s === name ? null : name)), [])
  const [showResults, setShowResults] = useState(false)
  const { nodes, edges, resultCount } = useConstellation(tables, selected, onSelect, showResults)
  const chosen = tables.find((t) => t.name === selected) ?? null

  return (
    <div className="flex flex-col gap-4">
      <header className="glass p-4">
        <h1 className="text-lg font-semibold">Data &amp; Schema</h1>
        <p className="text-sm mt-1" style={{ color: 'var(--muted)', lineHeight: 1.55, maxWidth: '74ch' }}>
          What is actually in the warehouse, read from DuckDB&apos;s own catalogue rather than
          from a document that can drift. Fact tables carry one row per event; dimensions
          carry one row per attribute value. Row counts are live.
        </p>
        {schema.data?.meta.note && (
          <p className="text-xs mt-2" style={{ color: 'var(--caution)', lineHeight: 1.55 }}>
            {schema.data.meta.note}
          </p>
        )}
      </header>

      <div className="flex gap-1.5" role="tablist" aria-label="Data views">
        {([['schema', 'Schema'], ['etl', 'ETL funnel']] as const).map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            onClick={() => chooseTab(id)}
            className="text-xs px-3 py-1.5 rounded-full"
            style={{
              border: `1px solid ${tab === id ? 'var(--news)' : 'rgba(148,163,184,.22)'}`,
              background: tab === id ? 'rgba(20,184,166,.12)' : 'transparent',
              color: tab === id ? 'var(--news)' : 'var(--muted)',
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'schema' && (
        <div className="grid gap-4 lg:grid-cols-12">
          <div className="lg:col-span-8 glass p-2 relative" style={{ height: 560 }}>
            {resultCount > 0 && (
              <button
                type="button"
                onClick={() => setShowResults((s) => !s)}
                aria-pressed={showResults}
                className="absolute z-10 top-2 right-2 text-[11px] px-2.5 py-1 rounded-full"
                style={{
                  background: 'var(--surface-solid)',
                  border: `1px solid ${showResults ? 'var(--news)' : 'rgba(148,163,184,.25)'}`,
                  color: showResults ? 'var(--news)' : 'var(--muted)',
                }}
                title="Mining and audit tables. They are results, not part of the fact constellation, and hold no key to anything."
              >
                {showResults ? 'hide' : 'show'} {resultCount} result tables
              </button>
            )}
            {schema.isLoading && <div className="skeleton h-full rounded-xl" />}
            {nodes.length > 0 && (
              <ReactFlow
                nodes={nodes}
                edges={edges}
                nodeTypes={NODE_TYPES}
                fitView
                fitViewOptions={{ padding: 0.12 }}
                nodesDraggable
                nodesConnectable={false}
                elementsSelectable
                proOptions={{ hideAttribution: true }}
                style={{ background: 'transparent' }}
              >
                <Background color="rgba(148,163,184,.12)" gap={22} size={1} />
                <Controls
                  showInteractive={false}
                  style={{ background: 'var(--surface-solid)', borderColor: 'rgba(148,163,184,.2)' }}
                />
              </ReactFlow>
            )}
          </div>
          <div className="lg:col-span-4">
            <div className="glass p-4 lg:sticky lg:top-4">
              {chosen ? (
                <>
                  <div className="flex items-center justify-between gap-2">
                    <h2 className="text-sm font-semibold mono">{chosen.name}</h2>
                    <span
                      className="text-[10px] px-2 py-0.5 rounded"
                      style={{
                        background: FACT_TABLES.has(chosen.name) ? 'rgba(20,184,166,.14)' : 'rgba(148,163,184,.14)',
                        color: FACT_TABLES.has(chosen.name) ? 'var(--news)' : 'var(--muted)',
                      }}
                    >
                      {FACT_TABLES.has(chosen.name) ? 'fact' : chosen.name.startsWith('dim_') ? 'dimension' : 'other'}
                    </span>
                  </div>
                  <p className="mono text-xs mt-1" style={{ color: 'var(--muted)' }}>
                    {fmtNum(chosen.rows)} rows · {chosen.column_count} columns
                  </p>
                  <table className="w-full text-[11px] mt-3">
                    <tbody className="mono">
                      {chosen.columns.map((c) => (
                        <tr key={c.name} style={{ borderTop: '1px solid rgba(148,163,184,.08)' }}>
                          <td className="py-1">{c.name}</td>
                          <td className="text-right" style={{ color: 'var(--muted)' }}>{c.type}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              ) : (
                <>
                  <h2 className="text-sm font-semibold">Pick a table</h2>
                  <p className="text-xs mt-1" style={{ color: 'var(--muted)', lineHeight: 1.55 }}>
                    Click any node for its grain, row count and columns. Edges are drawn only
                    where one table actually holds a key column the other owns — inferred
                    join keys would be a guess presented as metadata.
                  </p>
                  <ul className="mt-3 flex flex-col gap-1">
                    {[
                      ['fact_headline', 'one row per headline'],
                      ['fact_stock_daily', 'one row per stock-session'],
                      ['cube_day_topic', 'one row per (day, topic)'],
                      ['mining_event_study', 'one row per (event, stock)'],
                    ].map(([name, grain]) => (
                      <li key={name} className="text-[11px]">
                        <span className="mono" style={{ color: 'var(--news)' }}>{name}</span>
                        <span className="ml-2" style={{ color: 'var(--muted)' }}>{grain}</span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {tab === 'etl' && <EtlFunnel steps={etl.data?.data ?? []} meta={etl.data?.meta} loading={etl.isLoading} />}
    </div>
  )
}

/** Read, rejected, loaded — per ETL step.
 *
 *  Rejections are the interesting number. A funnel that only shows what
 *  succeeded hides the part of the pipeline that decided something was not
 *  usable, which is usually where a reader's question lives.
 */
function EtlFunnel({
  steps, meta, loading,
}: {
  steps: EtlStep[]
  meta?: { note?: string; concept?: string; rows?: number }
  loading: boolean
}) {
  const option = useMemo(() => {
    if (steps.length === 0) return null
    const read = steps.reduce((s, x) => s + (x.rows_read ?? 0), 0)
    const loaded = steps.reduce((s, x) => s + (x.rows_loaded ?? 0), 0)
    const rejected = steps.reduce((s, x) => s + (x.rows_rejected ?? 0), 0)
    const data = [
      { name: 'rows read', value: read },
      { name: 'rows loaded', value: loaded },
      { name: 'rows rejected', value: rejected },
    ].filter((d) => d.value > 0)
    if (data.length < 2) return null
    return {
      animationDuration: 500,
      tooltip: {
        backgroundColor: 'rgba(11,18,32,.94)',
        borderColor: 'rgba(148,163,184,.25)',
        textStyle: { color: '#E6EDF7', fontSize: 12 },
        valueFormatter: (v: number) => fmtNum(v),
      },
      series: [
        {
          type: 'funnel',
          left: '6%',
          width: '88%',
          minSize: '18%',
          sort: 'descending',
          gap: 3,
          label: {
            show: true,
            position: 'inside',
            fontSize: 11,
            color: '#04120F',
            formatter: (p: { name: string; value: number }) =>
              `${p.name}: ${fmtNum(p.value)}`,
          },
          itemStyle: { borderColor: 'transparent', borderWidth: 0 },
          data: data.map((d, i) => ({
            ...d,
            itemStyle: { color: [SEMANTIC.news, 'rgba(20,184,166,.55)', 'rgba(239,68,68,.6)'][i] ?? SEMANTIC.news },
          })),
        },
      ],
    }
  }, [steps])

  if (loading) return <div className="skeleton h-72 rounded-xl" />

  return (
    <div className="flex flex-col gap-4">
      {meta?.note && (
        <div
          className="glass p-3 flex items-start gap-2 text-xs"
          style={{ background: 'rgba(245,158,11,.08)', border: '1px solid rgba(245,158,11,.22)', color: 'var(--caution)', lineHeight: 1.55 }}
        >
          <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />
          <span>{meta.note}</span>
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-12">
        <div className="lg:col-span-5 glass p-4">
          <h2 className="text-sm font-semibold mb-1">Rows through the pipeline</h2>
          <p className="text-xs mb-2" style={{ color: 'var(--muted)' }}>
            Across every step in the last run.
          </p>
          {option ? (
            <Chart
              option={option}
              height={200}
              title=""
              caption="Rejected rows are shown, not folded away: a funnel that only reports success hides where the pipeline decided something was unusable."
            />
          ) : (
            <p className="text-sm py-8 text-center" style={{ color: 'var(--muted)' }}>
              This run recorded no row counts.
            </p>
          )}
        </div>
        <div className="lg:col-span-7 glass p-4">
          <h2 className="text-sm font-semibold mb-2 flex items-center gap-2">
            <GitBranch size={14} /> Every step
          </h2>
          <div className="overflow-auto" style={{ maxHeight: 380 }}>
            <table className="w-full text-[11px]">
              <thead>
                <tr style={{ color: 'var(--muted)' }}>
                  <th className="text-left py-1">step</th>
                  <th className="text-left">status</th>
                  <th className="text-right">read</th>
                  <th className="text-right">loaded</th>
                  <th className="text-right">rejected</th>
                </tr>
              </thead>
              <tbody className="mono">
                {steps.map((s) => (
                  <tr key={s.run_id + s.step} style={{ borderTop: '1px solid rgba(148,163,184,.08)' }}>
                    <td className="py-1 pr-2">{s.step}</td>
                    <td style={{ color: s.status === 'ok' ? 'var(--up)' : 'var(--caution)' }}>{s.status}</td>
                    <td className="text-right">{(s.rows_read ?? 0).toLocaleString('en-IN')}</td>
                    <td className="text-right">{(s.rows_loaded ?? 0).toLocaleString('en-IN')}</td>
                    <td className="text-right" style={{ color: (s.rows_rejected ?? 0) > 0 ? 'var(--caution)' : 'var(--muted)' }}>
                      {(s.rows_rejected ?? 0).toLocaleString('en-IN')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}
