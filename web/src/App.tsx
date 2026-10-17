import { Suspense, lazy } from 'react'
import { Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import Placeholder from './pages/Placeholder'

/** Home is eager: it is the landing page and the first-paint budget is spent
 *  here, so splitting it would trade the metric that matters for one that does
 *  not. Everything else is split. */
import Home from './pages/Home'

/* Lazy, with a named fallback rather than a blank frame. A page that resolves
 * in 40ms still shows a spinner for one frame, which reads as a glitch; the
 * fallback matches the card shape it replaces so the layout does not jump.
 *
 * This is also what keeps the heavy libraries out of the first load. React Flow
 * (schema diagram, U7), dnd-kit (Cube Studio field shelf, U4) and cmdk (the
 * command palette, U7) are large and are each used by exactly one route, so
 * eager imports would put all three on the critical path to see the homepage. */
const Stocks = lazy(() => import('./pages/Stocks'))
const Events = lazy(() => import('./pages/Events'))
const Mining = lazy(() => import('./pages/Mining'))
const Headline = lazy(() => import('./pages/Headline'))
const Data = lazy(() => import('./pages/Data'))
const Insights = lazy(() => import('./pages/Insights'))
const Explore = lazy(() => import('./pages/Explore'))

function PageFallback() {
  return (
    <div className="glass p-8 mt-4" role="status" aria-live="polite">
      <p className="text-sm" style={{ color: 'var(--muted)' }}>
        Loading…
      </p>
    </div>
  )
}

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Home />} />
        <Route path="explore" element={<Suspense fallback={<PageFallback />}><Explore /></Suspense>} />
        <Route path="stocks" element={<Suspense fallback={<PageFallback />}><Stocks /></Suspense>} />
        <Route path="stocks/:symbol" element={<Suspense fallback={<PageFallback />}><Stocks /></Suspense>} />
        <Route path="events" element={<Suspense fallback={<PageFallback />}><Events /></Suspense>} />
        <Route path="mining" element={<Suspense fallback={<PageFallback />}><Mining /></Suspense>} />
        <Route path="headline" element={<Suspense fallback={<PageFallback />}><Headline /></Suspense>} />
        <Route path="data" element={<Suspense fallback={<PageFallback />}><Data /></Suspense>} />
        <Route path="insights" element={<Suspense fallback={<PageFallback />}><Insights /></Suspense>} />
      </Route>
    </Routes>
  )
}

export { Placeholder, PageFallback }
