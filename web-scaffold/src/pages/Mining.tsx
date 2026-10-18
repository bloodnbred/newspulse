import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, ArrowRight, Info } from 'lucide-react'
import { useApi } from '../lib/api'
import { fmtNum } from '../lib/format'
import Chart from '../components/Chart'
import { SECTOR_COLORS, SEMANTIC } from '../lib/echartsTheme'
import type {
  ClusterData, ClusterProfile, CoMentionData, MarketRule, ModelData, RuleData,
} from '../types'

/** Cluster hues.
 *
 *  A cluster is a category, so it takes a categorical palette - never a
 *  semantic one. `--news` is teal and this chart is about news-driven
 *  behaviour, so painting a cluster teal would read as "the news cluster".
 *  `--mining` violet is the project's own hue for mining output and is reserved
 *  for exactly that. */
const CLUSTER_HUE = ['#8B5CF6', '#F59E0B', '#38BDF8', '#F472B6', '#34D399', '#FB923C']

const clusterColour = (id: number | null, profiles: ClusterProfile[]): string => {
  if (id === null) return '#64748B'
  const index = profiles.findIndex((p) => p.cluster_id === id)
  return CLUSTER_HUE[index >= 0 ? index % CLUSTER_HUE.length : 0] ?? '#64748B'
}

/* ---------------------------------------------------------------------------
 * Tab 1: clusters
 * ------------------------------------------------------------------------ */

function ClusterTab() {
  const data = useApi<ClusterData>('/ui/mining/clusters', {}, { withFilters: false })
  const d = data.data?.data
  const meta = data.data?.meta
  const [hidden, setHidden] = useState<number[]>([])
  const [picked, setPicked] = useState<string[]>([])

  const option = useMemo(() => {
    if (!d) return null
    const shown = d.points.filter((p) => !hidden.includes(p.cluster_id ?? -1))
    return {
      animationDuration: 400,
      tooltip: {
        backgroundColor: 'rgba(11,18,32,.94)',
        borderColor: 'rgba(148,163,184,.25)',
        textStyle: { color: '#E6EDF7', fontSize: 12 },
        formatter: (p: { data: { value: number[] } }) => {
          const pt = shown.find((s) => s.symbol === String(p.data.value[2]))
          if (!pt) return ''
          const name = d.profiles.find((x) => x.cluster_id === pt.cluster_id)?.name ?? 'unclustered'
          return `<b>${pt.symbol}</b><br/>${pt.sector ?? 'unclassified'}<br/>${name}<br/>annualised volatility ${pt.ann_volatility}%<br/>beta ${pt.beta}`
        },
      },
      grid: { left: 8, right: 16, top: 8, bottom: 44, containLabel: true },
      xAxis: {
        type: 'value',
        name: 'PC1',
        nameLocation: 'middle',
        nameGap: 26,
        nameTextStyle: { color: 'var(--muted)', fontSize: 10 },
        axisLabel: { color: 'var(--muted)', fontSize: 9 },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,.10)' } },
      },
      yAxis: {
        type: 'value',
        name: 'PC2',
        nameTextStyle: { color: 'var(--muted)', fontSize: 10 },
        axisLabel: { color: 'var(--muted)', fontSize: 9 },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,.10)' } },
      },
      series: d.profiles.map((profile) => ({
        name: profile.name,
        type: 'scatter',
        symbolSize: 11,
        itemStyle: {
          color: clusterColour(profile.cluster_id, d.profiles),
          opacity: hidden.includes(profile.cluster_id) ? 0.15 : 0.9,
        },
        label: {
          show: false,
          formatter: (p: { data: number[] }) => String(p.data[2]),
          position: 'right',
          color: 'var(--muted)',
          fontSize: 9,
        },
        emphasis: { label: { show: true, color: '#E6EDF7' } },
        data: d.points
          .filter((p) => p.cluster_id === profile.cluster_id)
          .map((p) => [p.pc1, p.pc2, p.symbol]),
      })),
    }
  }, [d, hidden])

  if (data.isLoading) return <div className="skeleton h-96 rounded-xl" />
  if (!d || !option) return <p className="text-sm" style={{ color: 'var(--muted)' }}>No clustering yet.</p>

  return (
    <div className="flex flex-col gap-4">
      {/* The score and its shape together. 0.26 is a respectable-looking number
          until you know one cluster holds 94.9% of the sample. */}
      <div
        className="p-3 rounded-xl flex flex-wrap items-center gap-x-6 gap-y-2"
        style={{ background: 'rgba(245,158,11,.08)', border: '1px solid rgba(245,158,11,.22)' }}
      >
        <div>
          <div className="text-[10px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>silhouette</div>
          <div className="mono text-lg">{d.silhouette?.toFixed(3) ?? '—'}</div>
        </div>
        {d.dominant_cluster_share !== null && (
          <div>
            <div className="text-[10px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
              largest cluster's share
            </div>
            <div className="mono text-lg">{(d.dominant_cluster_share * 100).toFixed(1)}%</div>
          </div>
        )}
        <p className="text-xs flex-1" style={{ color: 'var(--muted)', lineHeight: 1.55 }}>
          <AlertTriangle size={12} style={{ verticalAlign: -1, marginRight: 4, color: 'var(--caution)' }} />
          {d.concentration_note}
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-12">
        <div className="lg:col-span-7 glass p-4">
          <h3 className="text-sm font-semibold">First two principal components</h3>
          <p className="text-xs mt-1 mb-2" style={{ color: 'var(--muted)' }}>
            Each dot is one stock, positioned by how it trades. Click a legend entry to
            hide a cluster; click a dot to add it to the list.
          </p>
          <Chart
            option={option}
            height={340}
            title=""
            caption={d.cluster_note ?? ''}
            onEvent={{
              // Clicking a dot is the tab's promise: it puts that symbol in the
              // picked list with a link to its own page. Without this the
              // scatter was a picture and nothing else.
              click: (p) => {
                const symbol = Array.isArray(p.data) ? String((p.data as unknown[])[2] ?? '') : ''
                if (!symbol) return
                setPicked((list) => (list.includes(symbol) ? list : [...list, symbol]))
              },
            }}
          />
        </div>

        <div className="lg:col-span-5 flex flex-col gap-3">
          <div className="glass p-4">
            <h3 className="text-sm font-semibold mb-2">The clusters</h3>
            <div className="flex flex-wrap gap-2 mb-3">
              {d.profiles.map((p) => {
                const on = !hidden.includes(p.cluster_id)
                return (
                  <button
                    key={p.cluster_id}
                    type="button"
                    onClick={() =>
                      setHidden((h) => (h.includes(p.cluster_id) ? h.filter((x) => x !== p.cluster_id) : [...h, p.cluster_id]))
                    }
                    aria-pressed={on}
                    className="text-[11px] px-2 py-1 rounded-full inline-flex items-center gap-1.5"
                    style={{
                      border: `1px solid ${on ? clusterColour(p.cluster_id, d.profiles) : 'rgba(148,163,184,.2)'}`,
                      background: on ? 'rgba(255,255,255,.03)' : 'transparent',
                      color: on ? 'var(--text)' : 'var(--muted)',
                      opacity: on ? 1 : 0.55,
                    }}
                  >
                    <span style={{ width: 8, height: 8, borderRadius: 999, background: clusterColour(p.cluster_id, d.profiles) }} />
                    {p.name} ({p.n_stocks})
                  </button>
                )
              })}
            </div>
            <div className="flex flex-col gap-2">
              {d.profiles.map((p) => (
                <div key={p.cluster_id} className="p-2 rounded-lg" style={{ background: 'rgba(255,255,255,.025)' }}>
                  <div className="text-xs font-semibold">{p.name}</div>
                  <div className="text-[10px] mt-0.5" style={{ color: 'var(--muted)' }}>{p.sectors}</div>
                  <div className="mono text-[10px] mt-1" style={{ color: 'var(--muted)' }}>
                    vol {p.median_volatility} · β {p.median_beta} · drawdown {p.median_drawdown} ·{' '}
                    {p.median_mentions} mentions/1k days
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="glass p-4">
            <h3 className="text-sm font-semibold mb-2">
              Picked stocks {picked.length > 0 && <span className="mono text-xs" style={{ color: 'var(--news)' }}>({picked.length})</span>}
            </h3>
            {picked.length === 0 ? (
              <p className="text-xs" style={{ color: 'var(--muted)' }}>
                Nothing picked yet. Each picked symbol links to its own page.
              </p>
            ) : (
              <ul className="flex flex-wrap gap-1">
                {picked.map((symbol) => (
                  <li key={symbol}>
                    <Link
                      to={`/stocks/${symbol}`}
                      className="text-[11px] px-2 py-1 rounded-lg inline-flex items-center gap-1"
                      style={{ background: 'rgba(20,184,166,.12)', color: 'var(--news)' }}
                    >
                      {symbol} <ArrowRight size={10} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
      {meta?.note && <p className="text-xs" style={{ color: 'var(--caution)', lineHeight: 1.55 }}>{meta.note}</p>}
    </div>
  )
}

/* ---------------------------------------------------------------------------
 * Tab 2: rules
 * ------------------------------------------------------------------------ */

/** The antecedent and consequent as English, rather than as a pattern string.
 *
 *  `sector_name=Telecom -> sentiment=neutral, vol_regime=low` is the rule as the
 *  miner wrote it. This is the same rule said in a sentence, and the two are
 *  derived from the same fields so they cannot drift.
 */
function plainRule(rule: MarketRule): string {
  const read = (part: string) =>
    part
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => {
        const [key, value] = s.split('=')
        if (!key || !value) return s
        const k = key.trim().replace(/_/g, ' ')
        const v = value.trim().replace(/_/g, ' ')
        return `${v} ${k}`
      })
      .join(' and ')
  return `When ${read(rule.antecedents)}, ${read(rule.consequents)} also tended to hold.`
}

function RulesTab() {
  const [minLift, setMinLift] = useState(1.0)
  const [minConfidence, setMinConfidence] = useState(0.0)
  const data = useApi<RuleData>(
    '/ui/mining/rules',
    { min_lift: minLift, min_confidence: minConfidence },
    { withFilters: false },
  )
  const d = data.data?.data
  const meta = data.data?.meta

  const top = useMemo(() => (d ? d.rules.slice(0, 3) : []), [d])
  const liftBars = useMemo(() => {
    if (!d) return null
    const rows = d.rules.slice(0, 12).reverse()
    return {
      animationDuration: 400,
      grid: { left: 8, right: 16, top: 8, bottom: 56, containLabel: true },
      tooltip: {
        backgroundColor: 'rgba(11,18,32,.94)',
        borderColor: 'rgba(148,163,184,.25)',
        textStyle: { color: '#E6EDF7', fontSize: 12 },
      },
      xAxis: {
        type: 'value',
        name: 'lift',
        nameLocation: 'middle',
        nameGap: 26,
        nameTextStyle: { color: 'var(--muted)', fontSize: 10 },
        axisLabel: { color: 'var(--muted)', fontSize: 9 },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,.10)' } },
      },
      yAxis: {
        type: 'category',
        data: rows.map((r) => r.antecedents.slice(0, 34)),
        axisLabel: { color: 'var(--muted)', fontSize: 9 },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      series: [
        {
          type: 'bar',
          barMaxWidth: 14,
          data: rows.map((r) => ({ value: r.lift, itemStyle: { color: SEMANTIC.mining, borderRadius: 3 } })),
        },
      ],
    }
  }, [d])

  if (data.isLoading) return <div className="skeleton h-96 rounded-xl" />
  if (!d) return <p className="text-sm" style={{ color: 'var(--muted)' }}>No rules yet.</p>

  return (
    <div className="flex flex-col gap-4">
      <div className="glass p-4">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <label className="text-xs flex items-center gap-2">
            <span style={{ color: 'var(--muted)' }}>min lift</span>
            <input
              type="range" min={1} max={2} step={0.05} value={minLift}
              onChange={(e) => setMinLift(Number(e.target.value))}
              className="w-32"
              aria-label="Minimum lift"
            />
            <span className="mono" style={{ color: 'var(--news)' }}>{minLift.toFixed(2)}</span>
          </label>
          <label className="text-xs flex items-center gap-2">
            <span style={{ color: 'var(--muted)' }}>min confidence</span>
            <input
              type="range" min={0} max={1} step={0.05} value={minConfidence}
              onChange={(e) => setMinConfidence(Number(e.target.value))}
              className="w-32"
              aria-label="Minimum confidence"
            />
            <span className="mono" style={{ color: 'var(--news)' }}>{minConfidence.toFixed(2)}</span>
          </label>
          <span className="text-xs ml-auto mono" style={{ color: 'var(--muted)' }}>
            {d.matched} of {d.total} rules pass
          </span>
        </div>
        <p className="text-xs mt-2" style={{ color: 'var(--muted)', lineHeight: 1.55 }}>
          <Info size={11} style={{ verticalAlign: -1, marginRight: 4 }} />
          Lift above 1 is the only threshold that means "more than chance".
          Confidence alone will happily return a pattern whose consequent is simply
          the most common thing in the corpus.
        </p>
      </div>

      {top.length > 0 && (
        <div className="glass p-4">
          <h3 className="text-sm font-semibold">The three strongest, in English</h3>
          <ol className="flex flex-col gap-2 mt-2">
            {top.map((r, i) => (
              <li key={r.rule_id} className="text-sm flex gap-2">
                <span className="mono" style={{ color: 'var(--mining)' }}>{i + 1}.</span>
                <span>
                  {plainRule(r)}{' '}
                  <span className="mono text-xs" style={{ color: 'var(--muted)' }}>
                    (lift {r.lift.toFixed(2)}, {r.support_count} cases)
                  </span>
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-12">
        <div className="lg:col-span-5 glass p-4">
          <h3 className="text-sm font-semibold mb-2">Lift by rule</h3>
          {liftBars && (
            <Chart
              option={liftBars}
              height={Math.max(240, Math.min(12, d.rules.length) * 30)}
              title=""
              caption={`The ${Math.min(12, d.rules.length)} highest-lift rules currently passing your thresholds.`}
            />
          )}
        </div>
        <div className="lg:col-span-7 glass p-4">
          <h3 className="text-sm font-semibold mb-2">All {d.matched} matching rules</h3>
          <div className="overflow-auto" style={{ maxHeight: 400 }}>
            <table className="w-full text-[11px]">
              <thead>
                <tr style={{ color: 'var(--muted)' }}>
                  <th className="text-left py-1">if</th>
                  <th className="text-left">then</th>
                  <th className="text-right">supp</th>
                  <th className="text-right">conf</th>
                  <th className="text-right">lift</th>
                </tr>
              </thead>
              <tbody className="mono">
                {d.rules.map((r) => (
                  <tr key={r.rule_id} style={{ borderTop: '1px solid rgba(148,163,184,.08)' }}>
                    <td className="py-1 pr-2">{r.antecedents}</td>
                    <td className="pr-2" style={{ color: 'var(--muted)' }}>{r.consequents}</td>
                    <td className="text-right">{(r.support * 100).toFixed(2)}%</td>
                    <td className="text-right">{(r.confidence * 100).toFixed(1)}%</td>
                    <td className="text-right" style={{ color: r.lift > 1 ? 'var(--mining)' : 'var(--muted)' }}>
                      {r.lift.toFixed(3)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
      {meta?.note && <p className="text-xs" style={{ color: 'var(--caution)', lineHeight: 1.55 }}>{meta.note}</p>}
    </div>
  )
}

/* ---------------------------------------------------------------------------
 * Tab 3: models
 * ------------------------------------------------------------------------ */

function ModelsTab() {
  const data = useApi<ModelData>('/ui/mining/models', {}, { withFilters: false })
  const d = data.data?.data
  const meta = data.data?.meta

  const test = useMemo(
    () => (d ? d.direction.filter((r) => r.split === 'test') : []),
    [d],
  )
  const cm = d?.classifier ?? null
  const chosen = cm ? (cm.models[cm.chosen_on_train_cv] ?? null) : null

  const accuracyChart = useMemo(() => {
    if (test.length === 0) return null
    const baseline = test[0]!.baseline_majority * 100
    return {
      animationDuration: 500,
      legend: { bottom: 0, textStyle: { color: 'var(--muted)', fontSize: 10 }, itemHeight: 10, itemWidth: 10 },
      grid: { left: 8, right: 16, top: 12, bottom: 48, containLabel: true },
      tooltip: {
        backgroundColor: 'rgba(11,18,32,.94)',
        borderColor: 'rgba(148,163,184,.25)',
        textStyle: { color: '#E6EDF7', fontSize: 12 },
        valueFormatter: (v: number) => `${v?.toFixed(2)}%`,
      },
      xAxis: {
        type: 'category',
        data: test.map((r) => r.model),
        axisLabel: { color: 'var(--muted)', fontSize: 10 },
        axisLine: { lineStyle: { color: 'rgba(148,163,184,.25)' } },
      },
      yAxis: {
        type: 'value',
        // Zoomed to the band the numbers actually occupy. From 0, a 0.4%
        // difference between the model and its baseline is invisible - which is
        // precisely the comparison this chart exists to make.
        min: Math.floor(Math.min(baseline, ...test.map((r) => r.accuracy * 100)) - 1),
        max: Math.ceil(Math.max(baseline, ...test.map((r) => r.accuracy * 100)) + 1),
        axisLabel: { color: 'var(--muted)', fontSize: 10, formatter: '{value}%' },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,.10)' } },
      },
      series: [
        {
          type: 'bar',
          name: 'model accuracy',
          barMaxWidth: 42,
          // Set at series level as well as per datum, because ECharts draws the
          // legend swatch from the series colour - and a blue swatch beside
          // grey bars tells the reader the grey means something else.
          itemStyle: { color: 'rgba(148,163,184,.55)', borderRadius: 4 },
          data: test.map((r) => ({
            value: r.accuracy * 100,
            itemStyle: {
              color: r.beats_baseline ? SEMANTIC.up : 'rgba(148,163,184,.55)',
              borderRadius: 4,
            },
          })),
        },
        {
          type: 'bar',
          name: 'majority baseline',
          barMaxWidth: 42,
          data: test.map(() => baseline),
          itemStyle: { color: 'rgba(245,158,11,.5)', borderRadius: 4 },
        },
      ],
    }
  }, [test])

  const cmOption = useMemo(() => {
    if (!chosen) return null
    const matrix = chosen.metrics.confusion_matrix
    return {
      animationDuration: 300,
      grid: { left: 8, right: 40, top: 8, bottom: 28, containLabel: true },
      tooltip: {
        backgroundColor: 'rgba(11,18,32,.94)',
        borderColor: 'rgba(148,163,184,.25)',
        textStyle: { color: '#E6EDF7', fontSize: 12 },
      },
      xAxis: {
        type: 'category',
        data: matrix.labels,
        name: 'predicted',
        nameLocation: 'middle',
        nameGap: 22,
        nameTextStyle: { color: 'var(--muted)', fontSize: 9 },
        axisLabel: { color: 'var(--muted)', fontSize: 10 },
        splitArea: { show: true },
      },
      yAxis: {
        type: 'category',
        data: matrix.rows_are_actual,
        name: 'actual',
        nameTextStyle: { color: 'var(--muted)', fontSize: 9 },
        axisLabel: { color: 'var(--muted)', fontSize: 10 },
        inverse: true,
      },
      visualMap: {
        min: 0,
        max: Math.max(...matrix.matrix.flat()),
        calculable: false,
        orient: 'horizontal',
        left: 'center',
        bottom: 0,
        itemHeight: 60,
        textStyle: { color: 'var(--muted)', fontSize: 9 },
        inRange: { color: ['rgba(20,184,166,.08)', SEMANTIC.news] },
      },
      series: [
        {
          type: 'heatmap',
          data: matrix.matrix.flatMap((row, i) => row.map((v, j) => [j, i, v])),
          label: {
            show: true,
            fontSize: 11,
            color: '#E6EDF7',
            formatter: (p: { value: number[] }) => fmtNum(Number(p.value[2])),
          },
        },
      ],
    }
  }, [chosen])

  // Every hook has run by now. An early return above a `useMemo` changes the
  // hook count between renders, and React throws rather than rendering
  // something subtly wrong - which is how this tab first appeared as a blank
  // page.
  if (data.isLoading) return <div className="skeleton h-96 rounded-xl" />
  if (!d) return <p className="text-sm" style={{ color: 'var(--muted)' }}>No models yet.</p>

  const beats = test.some((r) => r.beats_baseline)

  return (
    <div className="flex flex-col gap-4">
      {/* The honesty banner. `beats_baseline` is arithmetic computed on the
          server from the numbers beside it, so this cannot be softened from the
          client - a page that computes its own verdict can decide to show the
          good one. */}
      <div
        className="p-4 rounded-xl"
        style={{
          background: beats ? 'rgba(16,185,129,.07)' : 'rgba(239,68,68,.08)',
          border: `1px solid ${beats ? 'rgba(16,185,129,.26)' : 'rgba(239,68,68,.28)'}`,
        }}
      >
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <AlertTriangle size={14} style={{ color: beats ? 'var(--up)' : 'var(--caution)' }} />
          Predicting tomorrow&apos;s direction: {beats ? 'above the baseline' : 'no skill over the baseline'}
        </h3>
        <p className="text-xs mt-2" style={{ color: 'var(--muted)', lineHeight: 1.6 }}>
          {beats
            ? 'The news features carried information the baselines did not have.'
            : `Best test accuracy ${(Math.max(...test.map((r) => r.accuracy)) * 100).toFixed(2)}% against a majority baseline of ${(test[0]!.baseline_majority * 100).toFixed(2)}%, p=${test[0]?.p_vs_baseline?.toFixed(3) ?? '—'}. Always predicting the majority class would have scored as well or better. This project's news features did not predict tomorrow's direction, and the figure is reported rather than dropped.`}
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-12">
        <div className="lg:col-span-6 glass p-4">
          <h3 className="text-sm font-semibold">Direction model, held-out test split</h3>
          <p className="text-xs mt-1 mb-2" style={{ color: 'var(--muted)' }}>
            Chronological split, no shuffling. Both baselines are shown because a model
            that beats only one of them has not shown much.
          </p>
          {accuracyChart && (
            <Chart
              option={accuracyChart}
              height={260}
              title=""
              caption={`Test accuracy per model against the majority-class baseline of ${(test[0]!.baseline_majority * 100).toFixed(2)}%.`}
            />
          )}
          <table className="w-full text-[11px] mt-3">
            <thead>
              <tr style={{ color: 'var(--muted)' }}>
                <th className="text-left">model</th>
                <th className="text-right">acc</th>
                <th className="text-right">maj</th>
                <th className="text-right">yest</th>
                <th className="text-right">p</th>
              </tr>
            </thead>
            <tbody className="mono">
              {test.map((r) => (
                <tr key={r.model} style={{ borderTop: '1px solid rgba(148,163,184,.08)' }}>
                  <td className="py-1">{r.model}</td>
                  <td className="text-right">{r.beats_baseline ? '' : ''}{(r.accuracy * 100).toFixed(2)}%</td>
                  <td className="text-right" style={{ color: 'var(--muted)' }}>{(r.baseline_majority * 100).toFixed(2)}%</td>
                  <td className="text-right" style={{ color: 'var(--muted)' }}>
                    {r.baseline_yesterday === null ? '—' : `${(r.baseline_yesterday * 100).toFixed(2)}%`}
                  </td>
                  <td className="text-right" style={{ color: 'var(--muted)' }}>
                    {r.p_vs_baseline?.toFixed(3) ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!d.tree_rules_available && (
            <p className="text-xs mt-3" style={{ color: 'var(--muted)', lineHeight: 1.55 }}>
              The fitted tree&apos;s rule text is printed by{' '}
              <code>python -m dwm mine --op direction</code> but is not persisted between
              runs, so this page cannot show it. Only the metrics are stored, in{' '}
              <code>mining_direction_results</code>.
            </p>
          )}
        </div>

        <div className="lg:col-span-6 glass p-4">
          <h3 className="text-sm font-semibold">Statement classifier: REAL vs FAKE</h3>
          {!cm && <p className="text-xs mt-2" style={{ color: 'var(--muted)' }}>No classifier report.</p>}
          {cm && chosen && (
            <>
              <p className="text-xs mt-1 mb-2" style={{ color: 'var(--muted)', lineHeight: 1.55 }}>
                {cm.honesty_note} {fmtNum(cm.labelled_statements)} labelled IFND statements,
                {fmtNum(cm.test_count)} held out.
              </p>
              <div className="flex flex-wrap gap-4 mb-3">
                <div>
                  <div className="text-[10px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>accuracy</div>
                  <div className="mono text-lg" style={{ color: 'var(--up)' }}>
                    {(chosen.metrics.accuracy * 100).toFixed(2)}%
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>baseline</div>
                  <div className="mono text-lg" style={{ color: 'var(--muted)' }}>
                    {(cm.majority_baseline_accuracy * 100).toFixed(2)}%
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>lift</div>
                  <div className="mono text-lg">{chosen.metrics.lift_over_baseline_pp.toFixed(1)} pp</div>
                </div>
              </div>
              {cmOption && (
                <Chart
                  option={cmOption}
                  height={200}
                  title=""
                  caption={`Confusion matrix for ${cm.chosen_on_train_cv}. Rows are what the statement was, columns are what the model said.`}
                />
              )}
              <p className="text-xs mt-2" style={{ color: 'var(--caution)', lineHeight: 1.55 }}>
                {cm.upper_bound_caveat}
              </p>
            </>
          )}
        </div>
      </div>
      {meta?.note && <p className="text-xs" style={{ color: 'var(--caution)', lineHeight: 1.55 }}>{meta.note}</p>}
    </div>
  )
}

/* ---------------------------------------------------------------------------
 * Tab 4: co-mentions
 * ------------------------------------------------------------------------ */

function CoMentionTab() {
  const data = useApi<CoMentionData>('/ui/mining/co-mentions', {}, { withFilters: false })
  const d = data.data?.data
  const meta = data.data?.meta

  const option = useMemo(() => {
    if (!d) return null
    const mentioned = new Set<string>()
    for (const l of d.links) { mentioned.add(l.a_symbol); mentioned.add(l.b_symbol) }
    return {
      animationDuration: 500,
      tooltip: {
        backgroundColor: 'rgba(11,18,32,.94)',
        borderColor: 'rgba(148,163,184,.25)',
        textStyle: { color: '#E6EDF7', fontSize: 12 },
        formatter: (p: { dataType: string; data: Record<string, unknown> }) =>
          p.dataType === 'edge'
            ? `<b>${String(p.data.source)}</b> and <b>${String(p.data.target)}</b><br/>${String(p.data.value)} shared headlines`
            : `<b>${String(p.data.name)}</b><br/>${String(p.data.sector ?? 'unclassified')}`,
      },
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          categories: [...new Set(d.nodes.map((n) => n.sector ?? 'Unclassified'))].map((name, i) => ({
            name,
            itemStyle: { color: SECTOR_COLORS[i % SECTOR_COLORS.length] },
          })),
          force: { repulsion: 320, edgeLength: 70, gravity: 0.08 },
          label: { show: true, fontSize: 9, color: 'var(--muted)' },
          edgeSymbol: ['none', 'none'],
          // Edge width carries the count, so a strong pair looks strong rather
          // than merely being listed above a weak one.
          lineStyle: { color: 'rgba(148,163,184,.35)', width: 0, curveness: 0.1 },
          data: d.nodes
            .filter((n) => mentioned.has(n.symbol))
            .map((n) => ({
              name: n.symbol,
              category: d.nodes.findIndex((x) => (x.sector ?? 'Unclassified') === (n.sector ?? 'Unclassified')),
              value: n.mentions,
              sector: n.sector,
              symbolSize: Math.max(12, Math.min(44, 10 + Math.sqrt(n.mentions))),
            })),
          links: d.links.map((l) => ({
            source: l.a_symbol,
            target: l.b_symbol,
            value: l.together,
            lineStyle: { width: Math.min(6, 1 + Math.log2(l.together)) },
          })),
        },
      ],
    }
  }, [d])

  if (data.isLoading) return <div className="skeleton h-96 rounded-xl" />
  if (!d || !option) return <p className="text-sm" style={{ color: 'var(--muted)' }}>No co-mentions.</p>

  return (
    <div className="flex flex-col gap-4">
      <div className="glass p-4">
        <h3 className="text-sm font-semibold">Stocks named in the same headline</h3>
        <p className="text-xs mt-1" style={{ color: 'var(--muted)', lineHeight: 1.55 }}>
          {d.links.length} pairs above the threshold, out of {d.total_pairs} that exist at
          all across five years of one outlet&apos;s archive. That number is the honest
          headline: <b>stock-level mentions are sparse</b>, and a graph this thin is a fact
          about name matching across 47 tickers, not about the companies.
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-12">
        <div className="lg:col-span-8 glass p-4">
          <Chart
            option={option}
            height={420}
            title=""
            caption="Node size is total mentions; edge width is how many headlines named both. Drag to rearrange, scroll to zoom."
          />
        </div>
        <div className="lg:col-span-4 glass p-4">
          <h3 className="text-sm font-semibold mb-2">Strongest pairs</h3>
          <ul className="flex flex-col gap-1 overflow-auto" style={{ maxHeight: 400 }}>
            {d.links.map((l) => (
              <li key={`${l.a}-${l.b}`} className="text-xs flex items-center justify-between gap-2 px-2 py-1 rounded" style={{ background: 'rgba(255,255,255,.03)' }}>
                <span>
                  <Link to={`/stocks/${l.a_symbol}`} style={{ color: 'var(--news)' }}>{l.a_symbol}</Link>
                  <span className="mx-1" style={{ color: 'var(--faint)' }}>+</span>
                  <Link to={`/stocks/${l.b_symbol}`} style={{ color: 'var(--news)' }}>{l.b_symbol}</Link>
                </span>
                <span className="mono" style={{ color: 'var(--muted)' }}>{l.together}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
      {meta?.note && <p className="text-xs" style={{ color: 'var(--caution)', lineHeight: 1.55 }}>{meta.note}</p>}
    </div>
  )
}

/* ---------------------------------------------------------------------------
 * the page
 * ------------------------------------------------------------------------ */

const TABS = [
  { id: 'clusters', label: 'Stock clusters', render: () => <ClusterTab /> },
  { id: 'rules', label: 'Rules', render: () => <RulesTab /> },
  { id: 'models', label: 'Models', render: () => <ModelsTab /> },
  { id: 'network', label: 'Co-mentions', render: () => <CoMentionTab /> },
] as const

export default function Mining() {
  // The tab lives in the URL. A result that is only reachable by clicking
  // through a menu is a result nobody can send to a colleague, and the Models
  // tab in particular is the one worth quoting.
  const [tab, setTab] = useState<(typeof TABS)[number]['id']>(() => {
    const q = new URLSearchParams(window.location.search).get('tab')
    return TABS.some((t) => t.id === q) ? (q as (typeof TABS)[number]['id']) : 'clusters'
  })

  const choose = (id: (typeof TABS)[number]['id']) => {
    setTab(id)
    const p = new URLSearchParams(window.location.search)
    if (id === 'clusters') p.delete('tab')
    else p.set('tab', id)
    const q = p.toString()
    window.history.replaceState(
      window.history.state, '', q ? `${window.location.pathname}?${q}` : window.location.pathname,
    )
  }

  const current = TABS.find((t) => t.id === tab) ?? TABS[0]
  return (
    <div className="flex flex-col gap-4">
      <header className="glass p-4">
        <h1 className="text-lg font-semibold">Mining Lab</h1>
        <p className="text-sm mt-1" style={{ color: 'var(--muted)', lineHeight: 1.55, maxWidth: '70ch' }}>
          Four analyses of the same corpus. Two of them found structure; one of those two
          found none, and is reported as it came out.
        </p>
      </header>
      <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Mining analyses">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => choose(t.id)}
            className="text-xs px-3 py-1.5 rounded-full"
            style={{
              border: `1px solid ${tab === t.id ? 'var(--mining)' : 'rgba(148,163,184,.22)'}`,
              background: tab === t.id ? 'rgba(139,92,246,.14)' : 'transparent',
              color: tab === t.id ? 'var(--mining)' : 'var(--muted)',
            }}
          >
            {t.label}
          </button>
        ))}
      </div>
      {current.render()}
    </div>
  )
}
