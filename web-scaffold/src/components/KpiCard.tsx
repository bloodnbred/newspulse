import { motion } from 'framer-motion';
import { useCountUp } from '../hooks/useCountUp';
import { fmtNum, fmtPct } from '../lib/format';
import type { Kpi } from '../types';

function Sparkline({ values, color }: { values: number[]; color: string }) {
  if (values.length < 2) return <div className="h-9" />;
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

interface Props { kpi: Kpi; index?: number; accent?: string; onClick?: () => void }

export function KpiCard({ kpi, index = 0, accent = 'var(--news)', onClick }: Props) {
  const shown = useCountUp(kpi.value);
  const clickable = Boolean(onClick);
  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: index * 0.05, ease: [0.22, 1, 0.36, 1] }}
      className={`glass lift p-4 ${clickable ? 'cursor-pointer' : ''}`}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      onClick={onClick}
      onKeyDown={(e) => { if (clickable && (e.key === 'Enter' || e.key === ' ')) onClick?.(); }}
    >
      <div className="text-[11px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>{kpi.label}</div>
      <div className="mono text-3xl font-semibold mt-1">
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
