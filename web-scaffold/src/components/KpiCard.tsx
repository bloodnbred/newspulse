import { motion } from 'framer-motion';
import { useCountUp } from '../hooks/useCountUp';
import { fmtNum, fmtPct } from '../lib/format';
import type { Kpi } from '../types';

function Sparkline({ values, color }: { values: number[]; color: string }) {
  /* No placeholder gap when there is no series.
   *
   * A single count - 47 stocks, 331 surprise days - has no trend, and the pack
   * reserved a fixed `h-9` for it anyway. That renders as a blank strip under
   * the number, which reads as a chart that failed to load rather than as a
   * figure that has no history to draw. Cards in the same row stretch to the
   * tallest, so dropping the strip costs nothing in alignment. */
  if (values.length < 2) return null;
  const w = 120, h = 34;
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const pts = values
    .map((v, i) => `${(i / (values.length - 1)) * w},${h - ((v - min) / span) * (h - 4) - 2}`)
    .join(' ');
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-9" aria-hidden="true" preserveAspectRatio="none">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

interface Props {
  kpi: Kpi;
  index?: number;
  accent?: string;
  onClick?: () => void;
  /** Whether a filter is currently narrowing the page. Drives the
   *  "not affected" marker - see below for why it is not shown otherwise. */
  filtered?: boolean;
}

export function KpiCard({ kpi, index = 0, accent = 'var(--news)', onClick, filtered = false }: Props) {
  const shown = useCountUp(kpi.value);
  const clickable = Boolean(onClick);
  /* A figure that ignores the filter says so, but only while a filter is on.
   * With nothing selected every card would carry a marker explaining that it is
   * not being filtered, which is noise that trains a reader to ignore markers -
   * and then the one that matters is the one they skim past. */
  const inert =
    filtered && kpi.respondsTo !== undefined && kpi.respondsTo.length === 0;
  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: index * 0.05, ease: [0.22, 1, 0.36, 1] }}
      className={`glass lift p-4 relative ${clickable ? 'cursor-pointer' : ''}`}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      onClick={onClick}
      onKeyDown={(e) => { if (clickable && (e.key === 'Enter' || e.key === ' ')) onClick?.(); }}
      title={kpi.unfilteredBecause}
    >
      {inert && (
        <span
          className="absolute top-2 right-2 text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded"
          style={{
            color: 'var(--caution)',
            background: 'color-mix(in srgb, var(--caution) 14%, transparent)',
            border: '1px solid color-mix(in srgb, var(--caution) 32%, transparent)',
          }}
        >
          not filtered
        </span>
      )}
      <div className="text-[11px] uppercase tracking-widest pr-16" style={{ color: 'var(--muted)' }}>{kpi.label}</div>
      {/* Three sizes, and never a wrap. "11,13,427" is eleven characters: at a
          fixed 3xl it overflowed the card and clipped to "11,13,4", and with
          `break-all` it wrapped as "11,13,42 / 7" - a number split across two
          lines, which is worse than either. It shrinks to fit instead. */}
      <div className="mono text-[20px] leading-tight sm:text-[24px] 2xl:text-3xl font-semibold mt-1 whitespace-nowrap">
        {fmtNum(shown, kpi.decimals ?? 0)}
        {kpi.unit && <span className="text-base ml-1" style={{ color: 'var(--muted)' }}>{kpi.unit}</span>}
      </div>
      {kpi.deltaPct !== undefined && (
        <div className="mono text-xs" style={{ color: kpi.deltaPct >= 0 ? 'var(--up)' : 'var(--down)' }}>
          {fmtPct(kpi.deltaPct)}
        </div>
      )}
      <Sparkline values={kpi.spark} color={accent} />
    </motion.div>
  );
}
