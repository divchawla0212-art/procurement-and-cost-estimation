import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { JSX, KeyboardEvent } from 'react'
import { useNavigate } from 'react-router'
import {
  Search,
  FolderKanban,
  FileText,
  Users,
  ShieldCheck,
  Bookmark,
  BookmarkPlus,
  X,
  CornerDownLeft,
  ArrowUp,
  ArrowDown,
  Command,
} from 'lucide-react'
import type {
  BidderSummary,
  ProjectSummary,
  Rfq,
  RfqRoster,
  WorkflowProjectSummary,
} from '../types'
import {
  fetchBidders,
  fetchProjects,
  fetchRfqRoster,
  fetchWorkflowProjects,
} from '../api'

/** A single search result surfaced in the palette. */
type ResultKind = 'project' | 'workflow-project' | 'rfq' | 'vendor' | 'compliance'

interface Result {
  id: string
  kind: ResultKind
  title: string
  subtitle?: string
  meta?: string
  to: string
  score: number
}

/** A user's persisted filter — a name + query + scope. */
interface SavedFilter {
  id: string
  name: string
  query: string
  scope: ResultKind | 'all'
  createdAt: string
}

const SCOPES: { id: ResultKind | 'all'; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'project', label: 'Projects' },
  { id: 'workflow-project', label: 'Workflow' },
  { id: 'rfq', label: 'RFQs' },
  { id: 'vendor', label: 'Vendors' },
  { id: 'compliance', label: 'Compliance' },
]

const KIND_LABEL: Record<ResultKind, string> = {
  project: 'Bid set',
  'workflow-project': 'Workflow project',
  rfq: 'RFQ',
  vendor: 'Vendor',
  compliance: 'Compliance record',
}

const KIND_ICON: Record<ResultKind, typeof FolderKanban> = {
  project: FolderKanban,
  'workflow-project': FolderKanban,
  rfq: FileText,
  vendor: Users,
  compliance: ShieldCheck,
}

const STORAGE_KEY = 'bks:global-search:saved-filters'

/** Score a candidate label against a lowercased query. 0 means no match. */
function scoreOf(label: string, q: string): number {
  if (!q) return 1
  const l = label.toLowerCase()
  if (l === q) return 100
  if (l.startsWith(q)) return 80
  if (l.includes(` ${q}`)) return 60
  if (l.includes(q)) return 40
  // token match
  const tokens = q.split(/\s+/).filter(Boolean)
  if (tokens.every((t) => l.includes(t))) return 20
  return 0
}

function readSavedFilters(): SavedFilter[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((f) => f && typeof f.id === 'string')
  } catch {
    return []
  }
}

function writeSavedFilters(filters: SavedFilter[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(filters))
  } catch {
    /* storage full or blocked; the palette still works, filters are lost */
  }
}

/**
 * Global command palette (⌘K / Ctrl+K) that quick-searches across projects,
 * RFQs, vendors and compliance records, with saved filters per scope.
 *
 * Data is fetched lazily the first time the palette opens; subsequent opens
 * reuse the cached lists. Filtering, ordering and grouping happen client-side
 * against those lists — small enough (<10k rows) to keep responsive without a
 * dedicated /search endpoint.
 */
export function GlobalSearch({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}): JSX.Element | null {
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const [query, setQuery] = useState('')
  const [scope, setScope] = useState<ResultKind | 'all'>('all')
  const [activeIndex, setActiveIndex] = useState(0)

  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [workflowProjects, setWorkflowProjects] = useState<WorkflowProjectSummary[]>([])
  const [rfqs, setRfqs] = useState<Rfq[]>([])
  const [vendors, setVendors] = useState<BidderSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [saved, setSaved] = useState<SavedFilter[]>(() => readSavedFilters())

  // Load the corpus once, the first time the palette opens with data missing.
  useEffect(() => {
    if (!open) return
    if (projects.length || workflowProjects.length || rfqs.length || vendors.length) return
    setLoading(true)
    setLoadError(null)
    Promise.allSettled([
      fetchProjects(),
      fetchWorkflowProjects(),
      fetchRfqRoster(null).then((r: RfqRoster) => r.rfqs),
      fetchBidders(),
    ])
      .then(([p, wp, rq, vd]) => {
        if (p.status === 'fulfilled') setProjects(p.value)
        if (wp.status === 'fulfilled') setWorkflowProjects(wp.value)
        if (rq.status === 'fulfilled') setRfqs(rq.value)
        if (vd.status === 'fulfilled') setVendors(vd.value)
        const failed = [p, wp, rq, vd].filter((r) => r.status === 'rejected')
        if (failed.length && failed.length === 4) {
          setLoadError('Could not reach the workspace. Try again.')
        }
      })
      .finally(() => setLoading(false))
  }, [open, projects.length, workflowProjects.length, rfqs.length, vendors.length])

  // Focus the input when the palette opens; reset selection on query change.
  useEffect(() => {
    if (open) {
      // Delay a tick so the transition doesn't steal focus.
      const t = window.setTimeout(() => inputRef.current?.focus(), 20)
      return () => window.clearTimeout(t)
    }
    return
  }, [open])

  useEffect(() => {
    setActiveIndex(0)
  }, [query, scope])

  // Global keyboard: Escape closes, ⌘K / Ctrl+K toggles from anywhere.
  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key === 'Escape' && open) {
        e.preventDefault()
        onClose()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const results = useMemo<Result[]>(() => {
    const q = query.trim().toLowerCase()
    const out: Result[] = []

    const push = (r: Result) => {
      if (scope !== 'all' && r.kind !== scope) return
      if (r.score > 0) out.push(r)
    }

    for (const p of projects) {
      const label = `${p.name} ${p.slug} ${p.vendors.join(' ')}`
      push({
        id: `project:${p.slug}`,
        kind: 'project',
        title: p.name,
        subtitle: `${p.vendors.length} vendor${p.vendors.length === 1 ? '' : 's'} · ${p.target_currency}`,
        meta: p.slug,
        to: `/bid-sets/${encodeURIComponent(p.slug)}/overview`,
        score: scoreOf(label, q),
      })
      // Also surface each vendor of a bid set as a Vendor row — the user's
      // request explicitly names "vendors" alongside projects.
      for (const v of p.vendors) {
        push({
          id: `vendor:${p.slug}:${v}`,
          kind: 'vendor',
          title: v,
          subtitle: `on ${p.name}`,
          meta: p.slug,
          to: `/bid-sets/${encodeURIComponent(p.slug)}/overview`,
          score: scoreOf(`${v} ${p.name}`, q),
        })
      }
    }

    for (const wp of workflowProjects) {
      const label = `${wp.name} ${wp.code} ${wp.client} ${wp.location}`
      push({
        id: `wp:${wp.id}`,
        kind: 'workflow-project',
        title: wp.name,
        subtitle: `${wp.client} · ${wp.location}`,
        meta: `${wp.code} · ${wp.rfq_count} RFQ${wp.rfq_count === 1 ? '' : 's'}`,
        to: `/projects`,
        score: scoreOf(label, q),
      })
    }

    for (const r of rfqs) {
      const label = `${r.reference} ${r.package} ${r.discipline}`
      push({
        id: `rfq:${r.id}`,
        kind: 'rfq',
        title: r.reference,
        subtitle: `${r.discipline} · ${r.package}`,
        meta: r.stage,
        to: `/rfqs/${encodeURIComponent(r.id)}`,
        score: scoreOf(label, q),
      })
    }

    for (const b of vendors) {
      const trade = b.trade_categories.join(' ')
      const label = `${b.name} ${trade} ${b.effective_prequal} ${b.approved_by.join(' ')}`
      push({
        id: `bidder:${b.id}`,
        kind: 'vendor',
        title: b.name,
        subtitle: b.trade_categories[0] ?? undefined,
        meta: b.effective_prequal,
        to: `/rfqs`,
        score: scoreOf(label, q),
      })
    }

    // Compliance is a per-project concept; surface a compliance-matrix entry
    // per project so the palette can route straight to it.
    for (const p of projects) {
      const label = `${p.name} compliance matrix ${p.slug}`
      push({
        id: `compliance:${p.slug}`,
        kind: 'compliance',
        title: `Compliance · ${p.name}`,
        subtitle: 'Compliance matrix',
        meta: p.slug,
        to: `/bid-sets/${encodeURIComponent(p.slug)}/compliance-matrix`,
        score: scoreOf(label, q) * 0.9, // secondary surface for the same project
      })
    }

    out.sort((a, b) => b.score - a.score || a.title.localeCompare(b.title))
    return out.slice(0, 40)
  }, [projects, workflowProjects, rfqs, vendors, query, scope])

  const groups = useMemo(() => {
    const acc: Record<ResultKind, Result[]> = {
      project: [],
      'workflow-project': [],
      rfq: [],
      vendor: [],
      compliance: [],
    }
    for (const r of results) acc[r.kind].push(r)
    return (Object.keys(acc) as ResultKind[])
      .map((k) => ({ kind: k, items: acc[k] }))
      .filter((g) => g.items.length > 0)
  }, [results])

  const flatOrder = useMemo(() => groups.flatMap((g) => g.items), [groups])

  const commit = useCallback(
    (r: Result | undefined) => {
      if (!r) return
      onClose()
      navigate(r.to)
    },
    [navigate, onClose],
  )

  const onInputKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex((i) => Math.min(i + 1, Math.max(flatOrder.length - 1, 0)))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      commit(flatOrder[activeIndex])
    }
  }

  const saveCurrent = () => {
    if (!query.trim()) return
    const name = query.trim().slice(0, 40)
    const filter: SavedFilter = {
      id: `f_${Math.random().toString(36).slice(2, 8)}${Date.now().toString(36).slice(-4)}`,
      name,
      query: query.trim(),
      scope,
      createdAt: new Date().toISOString(),
    }
    const next = [filter, ...saved.filter((f) => !(f.query === filter.query && f.scope === filter.scope))].slice(0, 12)
    setSaved(next)
    writeSavedFilters(next)
  }

  const applyFilter = (f: SavedFilter) => {
    setQuery(f.query)
    setScope(f.scope)
    inputRef.current?.focus()
  }

  const removeFilter = (id: string) => {
    const next = saved.filter((f) => f.id !== id)
    setSaved(next)
    writeSavedFilters(next)
  }

  if (!open) return null

  return (
    <div className="gs-scrim" role="dialog" aria-label="Global search" data-testid="global-search">
      <button
        type="button"
        className="gs-scrim-close"
        aria-label="Close search"
        data-testid="global-search-scrim"
        onClick={onClose}
      />
      <div className="gs-panel" role="combobox" aria-expanded="true" aria-haspopup="listbox">
        <div className="gs-input-row">
          <Search size={16} aria-hidden className="gs-input-icon" />
          <input
            ref={inputRef}
            className="gs-input"
            type="search"
            placeholder="Search projects, RFQs, vendors, compliance…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onInputKey}
            spellCheck={false}
            aria-controls="global-search-results"
            data-testid="global-search-input"
          />
          <kbd className="gs-kbd" aria-hidden>ESC</kbd>
        </div>

        <div className="gs-scope" role="tablist" aria-label="Search scope" data-testid="global-search-scopes">
          {SCOPES.map((s) => (
            <button
              key={s.id}
              type="button"
              role="tab"
              aria-selected={scope === s.id}
              className={scope === s.id ? 'gs-chip on' : 'gs-chip'}
              onClick={() => setScope(s.id)}
              data-testid={`global-search-scope-${s.id}`}
            >
              {s.label}
            </button>
          ))}
          <span className="gs-scope-spacer" />
          <button
            type="button"
            className="gs-save"
            onClick={saveCurrent}
            disabled={!query.trim()}
            title={query.trim() ? 'Save this search' : 'Type a search to save'}
            data-testid="global-search-save"
          >
            <BookmarkPlus size={13} /> Save filter
          </button>
        </div>

        {saved.length > 0 && (
          <div className="gs-saved" data-testid="global-search-saved">
            <div className="gs-saved-label">Saved filters</div>
            <div className="gs-saved-list">
              {saved.map((f) => (
                <span key={f.id} className="gs-saved-chip" data-testid={`saved-filter-${f.id}`}>
                  <button
                    type="button"
                    className="gs-saved-chip-apply"
                    onClick={() => applyFilter(f)}
                    title={`Apply “${f.name}” · ${f.scope}`}
                  >
                    <Bookmark size={11} /> {f.name}
                    <span className="gs-saved-scope">{f.scope}</span>
                  </button>
                  <button
                    type="button"
                    className="gs-saved-chip-x"
                    aria-label={`Remove ${f.name}`}
                    onClick={() => removeFilter(f.id)}
                  >
                    <X size={11} />
                  </button>
                </span>
              ))}
            </div>
          </div>
        )}

        <div
          ref={listRef}
          id="global-search-results"
          role="listbox"
          className="gs-results"
          data-testid="global-search-results"
        >
          {loading && (
            <div className="gs-state" data-testid="global-search-loading">Loading workspace…</div>
          )}
          {!loading && loadError && (
            <div className="gs-state gs-state-error" data-testid="global-search-error">{loadError}</div>
          )}
          {!loading && !loadError && groups.length === 0 && (
            <div className="gs-state" data-testid="global-search-empty">
              {query.trim()
                ? 'No matches. Try a different word, or clear the scope.'
                : 'Start typing — or pick a scope to browse everything in that surface.'}
            </div>
          )}
          {!loading && !loadError && groups.map((g) => (
            <div key={g.kind} className="gs-group">
              <div className="gs-group-label">{KIND_LABEL[g.kind]}s</div>
              <ul className="gs-list">
                {g.items.map((r) => {
                  const Icon = KIND_ICON[r.kind]
                  const idx = flatOrder.indexOf(r)
                  const active = idx === activeIndex
                  return (
                    <li key={r.id}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={active}
                        className={active ? 'gs-item on' : 'gs-item'}
                        onMouseEnter={() => setActiveIndex(idx)}
                        onClick={() => commit(r)}
                        data-testid={`global-search-result-${r.id}`}
                      >
                        <span className="gs-item-icon"><Icon size={14} aria-hidden /></span>
                        <span className="gs-item-body">
                          <span className="gs-item-title">{r.title}</span>
                          {r.subtitle && <span className="gs-item-sub">{r.subtitle}</span>}
                        </span>
                        {r.meta && <span className="gs-item-meta">{r.meta}</span>}
                      </button>
                    </li>
                  )
                })}
              </ul>
            </div>
          ))}
        </div>

        <div className="gs-foot">
          <span className="gs-foot-item"><ArrowUp size={11} /><ArrowDown size={11} /> Navigate</span>
          <span className="gs-foot-item"><CornerDownLeft size={11} /> Open</span>
          <span className="gs-foot-item"><Command size={11} /> K anywhere</span>
          <span className="gs-foot-spacer" />
          <span className="gs-foot-item gs-foot-count">{results.length} result{results.length === 1 ? '' : 's'}</span>
        </div>
      </div>
    </div>
  )
}
