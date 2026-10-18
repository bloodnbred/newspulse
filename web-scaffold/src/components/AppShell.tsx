import type { ReactNode } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { CalendarClock, Database, FileSearch, Home, Layers, LineChart, Network, Sparkles } from 'lucide-react';
import { Ticker } from './Ticker';
import { CommandPalette, PALETTE_KEY } from './CommandPalette';
import { Tour } from './Tour';
import { FilterBar } from './FilterBar';
import { IS_MOCK, useApi } from '../lib/api';
import type { TapeItem } from '../types';

const NAV = [
  { to: '/', label: 'Home', icon: Home },
  { to: '/explore', label: 'Cube Studio', icon: Layers },
  { to: '/stocks', label: 'Stock 360', icon: LineChart },
  { to: '/events', label: 'Event Lab', icon: CalendarClock },
  { to: '/mining', label: 'Mining Lab', icon: Network },
  { to: '/headline', label: 'Headline Lab', icon: FileSearch },
  { to: '/data', label: 'Data & Schema', icon: Database },
  { to: '/insights', label: 'Insights', icon: Sparkles },
];

const linkClass = ({ isActive }: { isActive: boolean }) => `nav-link ${isActive ? 'active' : ''}`;

/** The global filter bar is rendered above every route except the placeholder
 *  pages, which have nothing to filter yet. `filterBar` remains a prop so a
 *  future shell-level chrome can override it. */
export function AppShell({ filterBar }: { filterBar?: ReactNode }) {
  const tape = useApi<TapeItem[]>('/ui/tape', {}, { withFilters: false });
  const location = useLocation();
  const showFilters = location.pathname === '/';
  return (
    <div className="min-h-full">
      <div className="mesh-bg" aria-hidden="true" />
      {IS_MOCK && <div className="mock-ribbon">MOCK DATA</div>}
      <Ticker items={tape.data?.data ?? []} />
      <div className="flex">
        <nav className="hidden md:flex flex-col gap-1 w-56 shrink-0 p-3 sticky top-0 h-screen" aria-label="Main">
          <div className="px-3 py-4 text-lg font-bold">
            NewsPulse <span style={{ color: 'var(--news)' }}>Nifty</span>
          </div>
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === '/'} className={linkClass}>
              <Icon size={18} aria-hidden="true" /> {label}
            </NavLink>
          ))}
          <kbd
            data-tour="palette"
            className="mt-3 text-[10px] px-2 py-1 rounded self-start"
            style={{ background: 'rgba(148,163,184,.12)', color: 'var(--muted)' }}
            title="Command palette"
          >
            Ctrl+{PALETTE_KEY.toUpperCase()}
          </kbd>
        </nav>
        <main className="flex-1 min-w-0 p-4 md:p-6 pb-24 md:pb-6">
          {filterBar ?? (showFilters ? <FilterBar /> : null)}
          <Outlet />
          <footer className="mt-8 text-xs" style={{ color: 'var(--muted)' }}>
            Educational project, not investment advice. Patterns shown are associations, not causes.
            Headline signals measure writing style, not truth.
          </footer>
        </main>
        {/* The palette is mounted once, here, so Ctrl-K works from every route
            rather than only on the pages that remembered to include it. */}
        <CommandPalette />
        <Tour />
      </div>
      <nav className="md:hidden fixed bottom-0 inset-x-0 glass flex justify-around py-2" aria-label="Mobile">
        {NAV.slice(0, 5).map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to} end={to === '/'} className={linkClass} aria-label={label}>
            <Icon size={20} aria-hidden="true" />
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
