import { KpiCard } from '../components/KpiCard';
import { useApi } from '../lib/api';
import type { HomeSummary } from '../types';

/** Starting point. Phase U3 builds the full homepage (see docs/UI-BLUEPRINT.md section 6.1). */
export default function Home() {
  const { data, isLoading, error } = useApi<HomeSummary>('/ui/home');
  const home = data?.data;
  return (
    <div className="space-y-6">
      <section className="glass p-6 md:p-8">
        <h1 className="text-3xl md:text-4xl font-bold">Watch India&rsquo;s news and the Nifty move together.</h1>
        <p className="mt-2" style={{ color: 'var(--muted)' }}>
          {home ? `${home.window.start} to ${home.window.end}` : 'Loading the warehouse...'}
        </p>
      </section>

      {error && (
        <div className="glass p-4 text-sm">
          Could not load the dashboard. {(error as { hint?: string }).hint ?? 'Is the API running? Try `python -m dwm serve`.'}
        </div>
      )}

      <section className="grid grid-cols-2 lg:grid-cols-6 gap-4">
        {isLoading && Array.from({ length: 6 }).map((_, i) => <div key={i} className="skeleton h-28 rounded-2xl" />)}
        {home?.kpis.map((k, i) => <KpiCard key={k.id} kpi={k} index={i} />)}
      </section>
    </div>
  );
}
