const compact = new Intl.NumberFormat('en-IN', { notation: 'compact', maximumFractionDigits: 1 });
export const fmtCompact = (n: number): string => compact.format(n);
export const fmtNum = (n: number, d = 0): string => n.toLocaleString('en-IN', { maximumFractionDigits: d, minimumFractionDigits: d });
export const fmtPct = (n: number, d = 1): string => `${n >= 0 ? '+' : ''}${n.toFixed(d)}%`;
