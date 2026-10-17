import type { TapeItem } from '../types';

export function Ticker({ items }: { items: TapeItem[] }) {
  if (!items.length) return <div className="h-8" aria-hidden="true" />;
  const loop = [...items, ...items]; // duplicated for a seamless loop
  return (
    <div className="overflow-hidden border-b" style={{ borderColor: 'var(--border)' }} aria-label="Latest sector moves">
      <div className="tape py-1.5 text-xs mono">
        {loop.map((t, i) => (
          <span key={i} className="flex gap-2 items-center">
            <span style={{ color: 'var(--muted)' }}>{t.label}</span>
            {/* `value` is the move over the whole window; `change` is the last
                session. Showing both is the point of a tape - a level and a
                daily delta. The pack's version printed `value` as the change
                AND then printed the change again beside the arrow, so every
                item read "Telecom +4.12% up 4.12%": the same number twice, which
                looks like a rendering fault. */}
            <span>{t.value}</span>
            <span style={{ color: t.change >= 0 ? 'var(--up)' : 'var(--down)' }}>
              {t.change >= 0 ? '▲' : '▼'} {Math.abs(t.change).toFixed(2)}%
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}
