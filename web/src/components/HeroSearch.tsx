import { useState } from 'react'
import { useApi, IS_MOCK } from '../lib/api'
import type { Meta } from '../types'

interface BriefNumbers {
  [key: string]: number | string | null
}
interface BriefPayload {
  kind: 'stock' | 'sector' | 'none'
  name?: string
  symbol?: string
  sector?: string | null
  match_quality?: number
  message?: string
  paragraphs?: string[]
  numbers?: BriefNumbers
  did_you_mean?: { name: string; similarity: number }[]
  caveats?: string[]
  recent_headlines?: { date: string; text: string; sentiment: number | null }[]
  constituents?: string[]
}

/** The hero search. The first thing a visitor is likely to try, so it answers
 *  in place rather than navigating away. */
export function HeroSearch() {
  const [q, setQ] = useState('')
  const [submitted, setSubmitted] = useState('')
  const { data, isFetching, error } = useApi<BriefPayload>(
    '/ui/brief',
    { q: submitted },
    { enabled: submitted.length > 0, withFilters: false },
  )
  const meta = data?.meta as Meta | undefined
  const brief = data?.data

  return (
    <div className="flex-1 min-w-0">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          const trimmed = q.trim()
          if (trimmed) setSubmitted(trimmed)
        }}
        className="flex gap-2"
      >
        <input
          className="field__input flex-1"
          style={{ padding: '12px 14px', fontSize: '1rem' }}
          placeholder="Search a company, sector or ticker…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search a company, sector or ticker"
          list="brief-options"
        />
        <datalist id="brief-options">
          {['Reliance', 'TCS', 'Infosys', 'HDFC Bank', 'Energy', 'Metals', 'Pharma'].map(
            (n) => (
              <option key={n} value={n} />
            ),
          )}
        </datalist>
        <button className="btn btn--primary" type="submit" style={{ padding: '0 22px' }}>
          {isFetching ? 'Looking…' : 'Brief me'}
        </button>
      </form>

      {submitted && (
        <div className="mt-4">
          {error && (
            <p className="text-sm" style={{ color: 'var(--down)' }}>
              Could not load a brief: {(error as { hint?: string }).hint ?? 'Is the API running?'}
            </p>
          )}
          {brief?.kind === 'none' && (
            <div className="glass p-4">
              <p className="text-sm" style={{ color: 'var(--muted)' }}>
                {brief.message}
              </p>
              {brief.did_you_mean && brief.did_you_mean.length > 0 && (
                <div className="flex flex-wrap gap-2 mt-3">
                  {brief.did_you_mean.map((alt) => (
                    <button
                      key={alt.name}
                      className="pill"
                      style={{ cursor: 'pointer' }}
                      onClick={() => {
                        setQ(alt.name)
                        setSubmitted(alt.name)
                      }}
                    >
                      {alt.name}
                      <span className="dim">{Math.round(alt.similarity * 100)}%</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {brief && brief.kind !== 'none' && (
            <div className="glass p-4">
              <div className="flex items-baseline gap-3 flex-wrap">
                <strong className="text-lg">{brief.name}</strong>
                {brief.symbol && <span className="pill">{brief.symbol}</span>}
                {brief.sector && <span className="pill">{brief.sector}</span>}
                {brief.match_quality !== undefined && brief.match_quality < 1 && (
                  <span className="pill" style={{ borderColor: 'var(--caution)' }}>
                    matched at {Math.round(brief.match_quality * 100)}% similarity
                  </span>
                )}
                {IS_MOCK && <span className="pill">MOCK</span>}
              </div>
              {brief.paragraphs?.map((p, i) => (
                <p key={i} className="text-sm mt-3" style={{ color: 'var(--text)', lineHeight: 1.6 }}>
                  {p.replace(/\*\*/g, '')}
                </p>
              ))}
              {brief.caveats && brief.caveats.length > 0 && (
                <details className="mt-3">
                  <summary className="text-xs" style={{ color: 'var(--muted)', cursor: 'pointer' }}>
                    What this is not ({brief.caveats.length})
                  </summary>
                  <ul className="bullets mt-2">
                    {brief.caveats.map((c, i) => (
                      <li key={i}>{c}</li>
                    ))}
                  </ul>
                </details>
              )}
              {meta?.concept && (
                <p className="mono text-xs mt-3" style={{ color: 'var(--faint, var(--muted))' }}>
                  {meta.concept} · {meta.rows} rows · {meta.ms} ms
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default HeroSearch
