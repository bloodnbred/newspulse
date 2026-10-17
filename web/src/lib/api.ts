import { useQuery } from '@tanstack/react-query';
import { useFilters } from './filters';
import type { Envelope, Filters } from '../types';

const BASE: string = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000';
/** Dev-only: ?mock=1 reads public/mock/ui/*.json. The shell shows a red MOCK DATA ribbon. */
export const IS_MOCK: boolean = new URLSearchParams(window.location.search).has('mock');

/** A failed request, carrying the server's own hint.
 *
 *  Written with explicit fields rather than the shorthand
 *  `constructor(public status: number, ...)` the pack used. TypeScript
 *  "parameter properties" are not *erasable syntax* - they require a
 *  transformation step to remove - and the Vite template's tsconfig sets
 *  `erasableSyntaxOnly: true`, which makes `tsc` reject them outright. The
 *  behaviour is identical and the class survives a switch to plain JS output. */
export class ApiError extends Error {
  readonly status: number;
  readonly hint: string | undefined;

  constructor(status: number, message: string, hint?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.hint = hint;
  }
}

function qs(params: Record<string, unknown>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    if (Array.isArray(v)) { if (v.length) u.set(k, v.join(',')); } else u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : '';
}

export function filterParams(f: Filters): Record<string, unknown> {
  return { from: f.from, to: f.to, sectors: f.sectors, topics: f.topics, include_market: f.includeMarketReports ? 1 : 0 };
}

async function parse<T>(res: Response): Promise<Envelope<T>> {
  if (!res.ok) {
    let hint: string | undefined;
    try { hint = (await res.json()).detail; } catch { /* body is not JSON */ }
    throw new ApiError(res.status, `Request failed (${res.status})`, hint);
  }
  return res.json() as Promise<Envelope<T>>;
}

export async function getEnvelope<T>(path: string, params: Record<string, unknown> = {}, signal?: AbortSignal): Promise<Envelope<T>> {
  const url = IS_MOCK ? `/mock${path}.json` : `${BASE}${path}${qs(params)}`;
  return parse<T>(await fetch(url, { signal }));
}

export async function postEnvelope<T>(path: string, body: unknown, signal?: AbortSignal): Promise<Envelope<T>> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  });
  return parse<T>(res);
}

/** GET with the global filters merged in (set withFilters:false for filter-independent calls). */
export function useApi<T>(
  path: string,
  extra: Record<string, unknown> = {},
  opts: { withFilters?: boolean; enabled?: boolean } = {},
) {
  const { withFilters = true, enabled = true } = opts;
  const filters = useFilters((s) => s.filters);
  const params = { ...(withFilters ? filterParams(filters) : {}), ...extra };
  return useQuery({
    queryKey: [path, params],
    queryFn: ({ signal }) => getEnvelope<T>(path, params, signal),
    enabled,
    staleTime: 5 * 60_000,
  });
}
