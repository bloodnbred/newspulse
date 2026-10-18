import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useLocation } from 'react-router-dom'
import { ChevronLeft, ChevronRight, X } from 'lucide-react'

/** One step: which element to ring, and what to say about it.
 *
 *  `selector` is a CSS selector rather than a ref because the targets live in
 *  different components and a tour that cannot survive a layout change is a tour
 *  that highlights the wrong box. A missing target is skipped rather than
 *  pointing at the centre of the screen. */
export interface TourStep {
  selector: string;
  title: string;
  body: string;
}

export const TOUR_STEPS: TourStep[] = [
  {
    selector: '[data-tour="search"]',
    title: 'Ask about any company',
    body: 'Type a company or ticker and get a brief built from what this corpus actually says about it — and what it does not cover.',
  },
  {
    selector: '[data-tour="mood"]',
    title: 'How heated is the news?',
    body: 'The last twenty sessions of headline volume, ranked against five years of this outlet’s own history. A percentile, not an index, and not a measure of sentiment.',
  },
  {
    selector: '[data-tour="timemachine"]',
    title: 'Travel through time',
    body: 'News against the Nifty across the whole window. Drag the slider to re-filter every panel on the page; hover or click to open a single day.',
  },
  {
    selector: '[data-tour="treemap"]',
    title: 'Click a sector to filter everything',
    body: 'Tile size is news volume; tile colour is that sector’s mean return over the window. Clicking one puts it into the global filters, and the URL with it.',
  },
  {
    selector: '[data-tour="surprise"]',
    title: 'Days when news and prices disagreed',
    body: 'Sessions where both measures were unusual for that sector. Most are quiet-news moves — the price moved while this corpus said nothing, which is a fact about one outlet’s coverage.',
  },
  {
    selector: '[data-tour="sql"]',
    title: 'Every figure has a statement behind it',
    body: 'The </> button on any card opens the SQL that produced it and names the DWM concept. Nothing on this site is hard-coded.',
  },
  {
    selector: '[data-tour="palette"]',
    title: 'Ctrl-K from anywhere',
    body: 'Jump to a page, a company or a sector without taking your eyes off what you were reading.',
  },
]

const STORAGE_KEY = 'newspulse.tour.seen.v1'

export function Tour() {
  const [running, setRunning] = useState(false)
  const [index, setIndex] = useState(0)
  const location = useLocation()

  useEffect(() => {
    // Home only. The steps all describe elements on the landing page, and a tour
    // that opened over Data & Schema would be pointing at targets that are not
    // there - a dialog that says "click a sector to filter everything" while
    // the reader is looking at a schema diagram.
    if (location.pathname !== '/') return
    // Offered once. A tour that reappears on every visit is an obstacle, and a
    // reader who skipped it once has said they do not want it.
    try {
      if (window.localStorage.getItem(STORAGE_KEY)) return
    } catch {
      /* storage blocked; offer it anyway rather than hide a feature */
    }
    const id = window.setTimeout(() => setRunning(true), 1400)
    return () => window.clearTimeout(id)
  }, [location.pathname])

  const seen = (v: boolean) => {
    if (v) {
      try {
        window.localStorage.setItem(STORAGE_KEY, '1')
      } catch {
        /* nothing to do; it will be offered again next time */
      }
    }
  }

  const go = (delta: number) => {
    const next = index + delta
    if (next < 0) return
    if (next >= TOUR_STEPS.length) {
      seen(true)
      setRunning(false)
      return
    }
    setIndex(next)
  }

  useEffect(() => {
    if (running && location.pathname !== '/') setRunning(false)
  }, [running, location.pathname])

  if (!running) return null
  const step = TOUR_STEPS[index]
  if (!step) return null

  return (
    <>
      <motion.div
        key={step.selector}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={() => {
          seen(true)
          setRunning(false)
        }}
        className="fixed inset-0 z-40"
        style={{ background: 'rgba(4,8,18,.62)' }}
        role="button"
        tabIndex={0}
        aria-label="End the tour"
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            seen(true)
            setRunning(false)
          }
        }}
      />
      <AnimatePresence mode="wait">
        <motion.div
          key={index}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 8 }}
          transition={{ duration: 0.22 }}
          className="fixed z-50 w-[min(380px,90vw)] p-4 rounded-2xl"
          style={{
            background: 'var(--surface-solid)',
            border: '1px solid rgba(20,184,166,.35)',
            left: '50%',
            top: '72%',
            transform: 'translateX(-50%)',
          }}
          role="dialog"
          aria-label={`Tour step ${index + 1} of ${TOUR_STEPS.length}`}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-2 text-[10px] uppercase tracking-widest" style={{ color: 'var(--news)' }}>
              <span>{index + 1} / {TOUR_STEPS.length}</span>
            </div>
            <button
              type="button"
              aria-label="Close the tour"
              onClick={() => {
                seen(true)
                setRunning(false)
              }}
              style={{ color: 'var(--muted)' }}
            >
              <X size={14} />
            </button>
          </div>
          <h3 className="text-sm font-semibold mt-1">{step.title}</h3>
          <p className="text-xs mt-1" style={{ color: 'var(--muted)', lineHeight: 1.6 }}>
            {step.body}
          </p>
          <div className="flex items-center justify-between mt-3">
            <div className="flex gap-1">
              {TOUR_STEPS.map((s, i) => (
                <span
                  key={s.selector}
                  style={{
                    width: 6, height: 6, borderRadius: 999,
                    background: i === index ? 'var(--news)' : 'rgba(148,163,184,.35)',
                  }}
                />
              ))}
            </div>
            <div className="flex items-center gap-1">
              {index > 0 && (
                <button type="button" className="icon-btn" aria-label="Previous step" onClick={() => go(-1)}>
                  <ChevronLeft size={14} />
                </button>
              )}
              <button type="button" className="btn" onClick={() => go(1)}>
                {index === TOUR_STEPS.length - 1 ? 'Done' : 'Next'}
                <ChevronRight size={13} />
              </button>
            </div>
          </div>
        </motion.div>
      </AnimatePresence>
    </>
  )
}
