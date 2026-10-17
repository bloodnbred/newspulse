import { create } from 'zustand';
import type { Filters } from '../types';

export const DEFAULT_FILTERS: Filters = { from: '', to: '', sectors: [], topics: [], includeMarketReports: false };

function readUrl(): Filters {
  const p = new URLSearchParams(window.location.search);
  const list = (k: string) => (p.get(k) ?? '').split(',').filter(Boolean);
  return {
    from: p.get('from') ?? '', to: p.get('to') ?? '',
    sectors: list('sectors'), topics: list('topics'),
    includeMarketReports: p.get('mr') === '1',
  };
}

function writeUrl(f: Filters): void {
  const p = new URLSearchParams(window.location.search);
  const put = (k: string, v: string) => (v ? p.set(k, v) : p.delete(k));
  put('from', f.from); put('to', f.to);
  put('sectors', f.sectors.join(',')); put('topics', f.topics.join(','));
  put('mr', f.includeMarketReports ? '1' : '');
  const q = p.toString();
  // keep router state intact (window.history.state) so React Router is not confused
  window.history.replaceState(window.history.state, '', q ? `${window.location.pathname}?${q}` : window.location.pathname);
}

interface State {
  filters: Filters;
  set: (patch: Partial<Filters>) => void;
  toggleSector: (sector: string) => void;
  reset: () => void;
  activeCount: () => number;
}

export const useFilters = create<State>((set, get) => ({
  filters: readUrl(),
  set: (patch) => { const filters = { ...get().filters, ...patch }; writeUrl(filters); set({ filters }); },
  toggleSector: (sector) => {
    const cur = get().filters.sectors;
    get().set({ sectors: cur.includes(sector) ? cur.filter((s) => s !== sector) : [...cur, sector] });
  },
  reset: () => { writeUrl(DEFAULT_FILTERS); set({ filters: DEFAULT_FILTERS }); },
  activeCount: () => {
    const f = get().filters;
    return (f.from ? 1 : 0) + (f.to ? 1 : 0) + f.sectors.length + f.topics.length + (f.includeMarketReports ? 1 : 0);
  },
}));
