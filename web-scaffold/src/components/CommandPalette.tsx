import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Command } from 'cmdk'
import { CornerDownLeft, Search } from 'lucide-react'
import { getEnvelope } from '../lib/api'
import type { SectorHit, StockHit } from '../types'

/** Ctrl-K / Cmd-K from anywhere. */
export const PALETTE_KEY = 'k'

interface PaletteItem {
  id: string;
  label: string;
  sub?: string | null;
  group: string;
  href: string;
}

/** The static half: every page and the operations worth doing.
 *
 *  A palette that only searches companies is a search box with extra steps. The
 *  pages and actions are the things a returning reader wants most, and they cost
 *  nothing to list.
 */
const STATIC_ITEMS: PaletteItem[] = [
  { id: 'nav-home', label: 'Home', group: 'Go to', href: '/' },
  { id: 'nav-cube', label: 'Cube Studio', sub: 'slice the warehouse', group: 'Go to', href: '/explore' },
  { id: 'nav-stocks', label: 'Stock 360', sub: 'one company end to end', group: 'Go to', href: '/stocks/RELIANCE' },
  { id: 'nav-events', label: 'Event Lab', sub: 'reaction to dated events', group: 'Go to', href: '/events' },
  { id: 'nav-mining', label: 'Mining Lab', sub: 'clusters, rules, models', group: 'Go to', href: '/mining' },
  { id: 'nav-models', label: 'Mining Lab → Models', sub: 'the honest verdict', group: 'Go to', href: '/mining?tab=models' },
  { id: 'nav-headline', label: 'Headline Lab', sub: 'score a headline', group: 'Go to', href: '/headline' },
  { id: 'nav-data', label: 'Data & Schema', sub: 'what is in the warehouse', group: 'Go to', href: '/data' },
  { id: 'nav-insights', label: 'Insights', sub: 'what the mining found', group: 'Go to', href: '/insights' },
]

export function CommandPalette() {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<{ stocks: StockHit[]; sectors: SectorHit[] }>({
    stocks: [],
    sectors: [],
  })
  const navigate = useNavigate()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // `metaKey` for macOS, `ctrlKey` elsewhere. Both, because a reader on the
      // wrong one of those is used to the other.
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === PALETTE_KEY) {
        e.preventDefault()
        setOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Search the server, debounced, and only once the query is long enough to be
  // a query rather than a keystroke.
  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) {
      setHits({ stocks: [], sectors: [] })
      return
    }
    const controller = new AbortController()
    const id = window.setTimeout(async () => {
      try {
        const env = await getEnvelope<{ stocks: StockHit[]; sectors: SectorHit[] }>(
          '/ui/search', { q }, controller.signal,
        )
        setHits(env.data)
      } catch {
        // A failed search shows the static items rather than an error; the
        // palette is a shortcut, not a place to surface failures.
        setHits({ stocks: [], sectors: [] })
      }
    }, 250)
    return () => {
      window.clearTimeout(id)
      controller.abort()
    }
  }, [query])

  const items = useMemo<PaletteItem[]>(
    () => [
      ...STATIC_ITEMS,
      ...hits.stocks.map((s) => ({
        id: `stock-${s.symbol}`,
        label: s.company_name,
        sub: s.symbol,
        group: 'Companies',
        href: `/stocks/${s.symbol}`,
      })),
      ...hits.sectors.map((s) => ({
        id: `sector-${s.sector_name}`,
        label: s.sector_name,
        sub: 'sector',
        group: 'Sectors',
        href: `/explore?cube=market&rows=sector&cols=year&vals=mean_return`,
      })),
    ],
    [hits],
  )

  return (
    <Command.Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o)
        if (!o) setQuery('')
      }}
      label="Jump to a page, company or sector"
      // A dark, opaque surface: a command palette over a translucent glass
      // shell is unreadable, because the page behind it shows through the list.
      className="w-[min(560px,92vw)] rounded-2xl border p-0"
      overlayClassName="fixed inset-0 z-50"
      contentClassName="bg-[#111A2E] border-[rgba(148,163,184,.25)]"
    >
      <div
        className="flex items-center gap-2 px-4 py-3 border-b"
        style={{ borderColor: 'rgba(148,163,184,.2)' }}
      >
        <Search size={15} style={{ color: 'var(--muted)' }} />
        <Command.Input
          value={query}
          onValueChange={setQuery}
          placeholder="Jump to a page, company or sector…"
          className="flex-1 bg-transparent outline-none text-sm"
          style={{ color: 'var(--text)' }}
        />
        <kbd
          className="text-[10px] px-1.5 py-0.5 rounded"
          style={{ background: 'rgba(148,163,184,.14)', color: 'var(--muted)' }}
        >
          Esc
        </kbd>
      </div>
      <Command.List
        className="max-h-80 overflow-auto p-2"
        style={{ scrollbarWidth: 'thin' }}
      >
        <Command.Empty className="px-3 py-6 text-center text-sm" style={{ color: 'var(--muted)' }}>
          Nothing matched “{query}”.
        </Command.Empty>
        {['Go to', 'Companies', 'Sectors'].map((group) => {
          const groupItems = items.filter((i) => i.group === group)
          if (groupItems.length === 0) return null
          return (
            <Command.Group
              key={group}
              heading={group}
              className="text-[10px] uppercase tracking-widest px-2 pt-2 pb-1"
              style={{ color: 'var(--muted)' }}
            >
              {groupItems.map((item) => (
                <Command.Item
                  key={item.id}
                  value={`${item.group} ${item.label} ${item.sub ?? ''}`}
                  onSelect={() => {
                    navigate(item.href)
                    setOpen(false)
                  }}
                  className="flex items-center justify-between gap-3 px-2 py-2 rounded-lg text-sm cursor-pointer data-[selected=true]:bg-[rgba(20,184,166,.14)]"
                >
                  <span className="truncate">{item.label}</span>
                  <span className="flex items-center gap-2 shrink-0">
                    {item.sub && (
                      <span className="mono text-[11px]" style={{ color: 'var(--muted)' }}>
                        {item.sub}
                      </span>
                    )}
                    <CornerDownLeft size={11} style={{ color: 'var(--faint)' }} />
                  </span>
                </Command.Item>
              ))}
            </Command.Group>
          )
        })}
      </Command.List>
    </Command.Dialog>
  )
}
