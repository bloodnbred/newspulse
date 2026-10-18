import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, Loader2, Search } from 'lucide-react'
import { postEnvelope, useApi } from '../lib/api'
import { SEMANTIC } from '../lib/echartsTheme'
import type { CheckResult, SampleSet } from '../types'

/** The debounce, in milliseconds.
 *
 *  Long enough that typing a sentence issues one request rather than thirty, and
 *  short enough that the panel updates before the reader has finished deciding
 *  whether to wait. The request is also aborted on every keystroke, so a slow
 *  early response cannot overwrite a fast later one.
 */
const DEBOUNCE_MS = 400

export default function Headline() {
  const [text, setText] = useState('')
  const [result, setResult] = useState<CheckResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const abort = useRef<AbortController | null>(null)

  const samples = useApi<SampleSet>('/ui/check/samples', {}, { withFilters: false })

  const analyse = useCallback((headline: string) => {
    abort.current?.abort()
    if (headline.trim().length < 3) {
      setResult(null)
      setError(null)
      return
    }
    const controller = new AbortController()
    abort.current = controller
    setPending(true)
    postEnvelope<CheckResult>('/ui/check', { headline }, controller.signal)
      .then((envelope) => {
        setResult(envelope.data)
        setError(null)
      })
      .catch((err: unknown) => {
        if ((err as { name?: string })?.name === 'AbortError') return
        setResult(null)
        setError(
          (err as { hint?: string })?.hint ?? 'Could not score that headline. Is the API running?',
        )
      })
      .finally(() => {
        if (!controller.signal.aborted) setPending(false)
      })
  }, [])

  useEffect(() => {
    const id = window.setTimeout(() => analyse(text), DEBOUNCE_MS)
    return () => window.clearTimeout(id)
  }, [text, analyse])

  const percentile = result?.percentile ?? null
  const rank = percentile?.percentile ?? null
  const band = useMemo(() => {
    if (rank === null) return { label: '—', colour: 'var(--muted)' }
    if (rank >= 90) return { label: 'top decile', colour: 'var(--caution)' }
    if (rank >= 50) return { label: 'above the median', colour: SEMANTIC.news }
    return { label: 'below the median', colour: 'var(--muted)' }
  }, [rank])

  return (
    <div className="flex flex-col gap-4">
      <header className="glass p-4">
        <h1 className="text-lg font-semibold">Headline Lab</h1>
        <p className="text-sm mt-1" style={{ color: 'var(--muted)', lineHeight: 1.55, maxWidth: '72ch' }}>
          Paste a headline and see what this project&apos;s scorer makes of its{' '}
          <b>writing</b>. It measures style — shouting, superlatives, urgency, punctuation —
          and nothing else. A sensational headline can be true and a plain one can be false.
        </p>
        <div
          className="mt-3 p-2.5 rounded-lg flex items-start gap-2 text-xs"
          style={{ background: 'rgba(245,158,11,.10)', color: 'var(--caution)', lineHeight: 1.5 }}
        >
          <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />
          <span>
            This is a style signal, not a fact-check. Nothing here tells you whether a claim
            is true, and a high score is not evidence of a lie — it is evidence of writing.
          </span>
        </div>
      </header>

      <div className="grid gap-4 lg:grid-cols-12">
        <div className="lg:col-span-7 glass p-4 flex flex-col">
          <label htmlFor="headline-input" className="text-sm font-semibold">
            A headline
          </label>
          <textarea
            id="headline-input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={5}
            placeholder="Paste or type a headline from any Indian outlet…"
            className="mt-2 w-full rounded-xl p-3 text-sm"
            style={{
              background: 'rgba(255,255,255,.04)',
              border: '1px solid rgba(148,163,184,.2)',
              color: 'var(--text)',
              resize: 'vertical',
            }}
          />
          <div className="flex items-center gap-2 mt-2 min-h-5">
            {pending && (
              <span className="text-xs inline-flex items-center gap-1" style={{ color: 'var(--muted)' }}>
                <Loader2 size={12} className="animate-spin" /> scoring…
              </span>
            )}
            {text.trim().length >= 3 && !pending && result && (
              <span className="text-xs" style={{ color: 'var(--faint)' }}>
                scored {result.metric_name}
                {result.truncated ? ' (first 2000 characters only)' : ''}
              </span>
            )}
          </div>

          {samples.data && (
            <div className="mt-4">
              <p className="text-[11px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
                Try a real headline from this corpus
              </p>
              <div className="flex flex-col gap-2 mt-2">
                {[...(samples.data.data.hype ?? []), ...(samples.data.data.plain ?? [])].map((s, i) => (
                  <button
                    key={`${s.publish_date}-${i}`}
                    type="button"
                    onClick={() => setText(s.headline)}
                    className="text-left text-xs px-2.5 py-2 rounded-lg"
                    style={{ background: 'rgba(255,255,255,.03)' }}
                  >
                    <span
                      className="mr-2 px-1.5 py-0.5 rounded text-[9px] uppercase"
                      style={{
                        background: i < 3 ? 'rgba(239,68,68,.14)' : 'rgba(20,184,166,.14)',
                        color: i < 3 ? 'var(--caution)' : 'var(--news)',
                      }}
                    >
                      {i < 3 ? 'most sensational' : 'plainest'}
                    </span>
                    {s.headline}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="lg:col-span-5 flex flex-col gap-4">
          {error && (
            <div className="glass p-4">
              <p className="text-sm" style={{ color: 'var(--caution)' }}>{error}</p>
            </div>
          )}

          {!result && !error && (
            <div className="glass p-4">
              <p className="text-sm" style={{ color: 'var(--muted)' }}>
                {text.trim().length < 3
                  ? 'Nothing to score yet. Type at least a few characters, or pick one of the real headlines on the left.'
                  : 'Scoring…'}
              </p>
            </div>
          )}

          {result && (
            <>
              <div className="glass p-4">
                <div className="flex items-baseline justify-between">
                  <h2 className="text-sm font-semibold">Where it sits</h2>
                  {result.is_verbatim_in_corpus && (
                    <span
                      className="text-[10px] px-2 py-0.5 rounded"
                      style={{ background: 'rgba(20,184,166,.14)', color: 'var(--news)' }}
                      title="This exact sentence is in the corpus, so its percentile is its real rank and not an estimate."
                    >
                      verbatim from the corpus
                    </span>
                  )}
                </div>
                {percentile !== null && rank !== null ? (
                  <>
                    <div className="flex items-baseline gap-2 mt-2">
                      <span className="mono text-3xl font-semibold" style={{ color: band.colour }}>
                        {rank.toFixed(1)}
                      </span>
                      <span className="text-sm" style={{ color: band.label }}>
                        {rank.toFixed(0)}th percentile
                      </span>
                    </div>
                    {/* The sentence the server wrote. It knows the corpus size
                        and the distribution's shape; a phrase assembled here
                        from the rank alone would not. */}
                    <p className="text-xs mt-1" style={{ color: 'var(--muted)' }}>
                      {percentile.in_words}
                    </p>
                    <p className="text-[10px] mt-1" style={{ color: 'var(--faint)' }}>
                      Scored against {percentile.n_corpus.toLocaleString('en-IN')} headlines
                      from this outlet — mean {percentile.corpus_mean.toFixed(3)}, sd{' '}
                      {percentile.corpus_sd.toFixed(3)}.
                    </p>
                    <div
                      className="mt-3 h-2 rounded-full relative"
                      style={{ background: 'rgba(148,163,184,.16)' }}
                    >
                      <div
                        className="h-2 rounded-full absolute left-0"
                        style={{
                          width: `${Math.max(1, rank)}%`,
                          background: band.colour,
                        }}
                      />
                    </div>
                  </>
                ) : (
                  <p className="text-sm mt-2" style={{ color: 'var(--muted)' }}>
                    No percentile for this one — the scorer could not rank it against the corpus.
                  </p>
                )}
              </div>

              {result.topic && (
                <div className="glass p-4">
                  <h2 className="text-sm font-semibold mb-2">Topic</h2>
                  <span
                    className="text-xs px-2 py-1 rounded"
                    style={{ background: 'rgba(20,184,166,.14)', color: 'var(--news)' }}
                  >
                    {result.topic.name ?? 'unclassified'}
                    {result.topic.confidence !== null && (
                      <span className="mono ml-2" style={{ color: 'var(--muted)' }}>
                        {(result.topic.confidence * 100).toFixed(0)}%
                      </span>
                    )}
                  </span>
                </div>
              )}

              {(result.tags.stocks.length > 0 || result.tags.sectors.length > 0) && (
                <div className="glass p-4">
                  <h2 className="text-sm font-semibold mb-2">Named in it</h2>
                  <div className="flex flex-wrap gap-1">
                    {result.tags.sectors.map((t) => (
                      <span key={`s-${t.label}`} className="text-[11px] px-2 py-0.5 rounded" style={{ background: 'rgba(148,163,184,.14)' }}>
                        {t.label} <span style={{ color: 'var(--muted)' }}>sector</span>
                      </span>
                    ))}
                    {result.tags.stocks.map((t) => (
                      <Link
                        key={`k-${t.label}`}
                        to={`/stocks/${t.label}`}
                        className="text-[11px] px-2 py-0.5 rounded"
                        style={{ background: 'rgba(20,184,166,.14)', color: 'var(--news)' }}
                      >
                        {t.label} <span style={{ color: 'var(--muted)' }}>→</span>
                      </Link>
                    ))}
                  </div>
                  <p className="text-[10px] mt-2" style={{ color: 'var(--muted)', lineHeight: 1.5 }}>
                    A name match, not a judgement that the story is about them — and the
                    precision of that matching has not been measured.
                  </p>
                </div>
              )}

              {Object.keys(result.style ?? {}).length > 0 && (
                <div className="glass p-4">
                  <h2 className="text-sm font-semibold mb-2">What the scorer saw</h2>
                  <ul className="flex flex-col gap-1.5">
                    {Object.entries(result.style)
                      .filter(([, v]) => typeof v === 'number' && v !== 0)
                      .sort(([, a], [, b]) => Math.abs(b) - Math.abs(a))
                      .slice(0, 8)
                      .map(([k, v]) => (
                        <li key={k} className="flex items-center gap-2 text-xs">
                          <span className="w-32 shrink-0" style={{ color: 'var(--muted)' }}>
                            {k.replace(/_/g, ' ')}
                          </span>
                          <span className="mono">{typeof v === 'number' ? v.toFixed(3) : String(v)}</span>
                        </li>
                      ))}
                  </ul>
                </div>
              )}

              {result.neighbours.length > 0 && (
                <div className="glass p-4">
                  <h2 className="text-sm font-semibold mb-1">Most similar past headlines</h2>
                  <p className="text-[10px] mb-2" style={{ color: 'var(--muted)' }}>
                    By word overlap with this corpus&apos;s own writing.
                  </p>
                  <ul className="flex flex-col gap-2">
                    {result.neighbours.slice(0, 5).map((n, i) => (
                      <li key={`${i}-${n.date}`} className="text-xs">
                        <span className="mono text-[10px] mr-2" style={{ color: 'var(--muted)' }}>
                          {n.date}
                        </span>
                        {n.text}
                        <span className="mono text-[10px] ml-2" style={{ color: 'var(--faint)' }}>
                          {n.similarity.toFixed(2)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {result?.disclaimer && (
        <p className="text-xs px-1" style={{ color: 'var(--muted)' }}>
          <Search size={11} style={{ verticalAlign: -1, marginRight: 4 }} />
          {result.disclaimer}
        </p>
      )}
    </div>
  )
}
