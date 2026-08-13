import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { fetchProjects } from './api'
import type { ProjectSummary } from './types'
import { useAsync } from './useAsync'
import { reviewReachable } from './nav'
import { useAuth } from './auth/context'
import type { User } from './auth/context'
import { Auth } from './pages/Auth'
import { AppRoutes } from './routes'
import { BackForwardControls, NavHistoryProvider } from './NavHistory'

// `needsReview` marks the screens that read a stored extraction (BUG-001,
// BUGS_TRACKER.md): they need not just a selected project but one whose
// `has_results` passes `reviewReachable` (I2, final-review report —
// `status` alone is not the right gate: see `nav.ts`). `Overview` carries it
// too: it renders from the compliance matrix and statement, so it is as
// unreachable without an extraction as the two screens it links into.
// `Extraction status` stays `needsProject` only — it is the screen that
// reports why the review screens are unreachable, so it must stay open
// regardless of status.
//
// These two flags now only grey out a rail button. The screens themselves are
// guarded by `RequireResults` in `routes.tsx`, because a URL can be typed and a
// disabled button cannot stop that. Keeping both is not duplication: one tells
// the reader a screen is unavailable, the other makes it so.
//
// `roles` is omitted for every screen both roles can reach; the one entry that
// carries it is hidden from a reviewer's nav. This is presentation only — the
// server enforces the actual boundary, and every `/api/admin/*` route refuses
// a reviewer whether or not this list ever mentioned it.
//
// Grouped, because the two halves of this app answer different questions. The
// RFQ process is where a package is scoped, issued and awarded; bid evaluation
// is the ingestion pipeline and the screens that read what it extracted. They
// share a project but not a workflow, and a single flat list of eight entries
// invited the reading that ingestion *is* the process.
type NavGroup = 'RFQ process' | 'Bid evaluation' | 'Administration'

interface NavEntry {
  index: string
  label: string
  group: NavGroup
  /**
   * Where the entry goes. Returns `null` when it needs a project and there is
   * none — which is also what disables it, so a rail button can never point at
   * `/bid-sets/null/overview`.
   */
  to: (slug: string | null) => string | null
  /** Whether the current path belongs to this entry, for the active highlight. */
  matches: (pathname: string) => boolean
  needsReview: boolean
  roles?: Array<User['role']>
}

// Written out per entry rather than derived from a prefix. `/bid-sets` and
// `/bid-sets/:slug/setup` both start with `/bid-sets`, so a prefix rule lights
// up two entries at once; being explicit costs a line each and cannot go wrong
// quietly.
const NAV: NavEntry[] = [
  // Both RFQ-process screens are project-agnostic in the sense the review gate
  // means it: their projects are the workflow store's own, not the ingestion
  // `slug`s in the switcher above. The Bid-evaluation entry is called "Bid
  // sets" rather than "Projects" for the same reason — two rail entries reading
  // "Projects" over two different stores is the first confusion a reader hits.
  // Unifying the two identities is phase 2.
  // Bidders sits between the two because that is the order the work happens
  // in: you have projects and their items, you have a list of who may bid, and
  // only then is there an RFQ to raise against both.
  {
    index: '01',
    label: 'Projects & items',
    group: 'RFQ process',
    to: () => '/projects',
    matches: (p) => p === '/projects' || p.startsWith('/projects/'),
    needsReview: false,
  },
  {
    index: '02',
    label: 'Bidders',
    group: 'RFQ process',
    to: () => '/bidders',
    matches: (p) => p === '/bidders',
    needsReview: false,
  },
  {
    index: '03',
    label: 'RFQ workflow',
    group: 'RFQ process',
    to: () => '/rfqs',
    matches: (p) => p === '/rfqs' || p.startsWith('/rfqs/'),
    needsReview: false,
  },
  {
    index: '04',
    label: 'Bid sets',
    group: 'Bid evaluation',
    to: () => '/bid-sets',
    matches: (p) => p === '/bid-sets',
    needsReview: false,
  },
  {
    index: '05',
    label: 'Set up & ingest',
    group: 'Bid evaluation',
    // With no project selected this is where you create one, which is exactly
    // what the screen does with a null slug.
    to: (slug) => (slug ? `/bid-sets/${slug}/setup` : '/bid-sets/new'),
    matches: (p) => p === '/bid-sets/new' || /^\/bid-sets\/[^/]+\/setup$/.test(p),
    needsReview: false,
  },
  {
    index: '06',
    label: 'Extraction status',
    group: 'Bid evaluation',
    to: (slug) => (slug ? `/bid-sets/${slug}/extraction` : null),
    matches: (p) => /^\/bid-sets\/[^/]+\/extraction$/.test(p),
    needsReview: false,
  },
  {
    index: '07',
    label: 'Overview',
    group: 'Bid evaluation',
    to: (slug) => (slug ? `/bid-sets/${slug}/overview` : null),
    matches: (p) => /^\/bid-sets\/[^/]+\/overview$/.test(p),
    needsReview: true,
  },
  {
    index: '08',
    label: 'Compliance matrix',
    group: 'Bid evaluation',
    to: (slug) => (slug ? `/bid-sets/${slug}/matrix` : null),
    matches: (p) => /^\/bid-sets\/[^/]+\/matrix$/.test(p),
    needsReview: true,
  },
  {
    index: '09',
    label: 'Comparative statement',
    group: 'Bid evaluation',
    to: (slug) => (slug ? `/bid-sets/${slug}/statement` : null),
    matches: (p) => /^\/bid-sets\/[^/]+\/statement$/.test(p),
    needsReview: true,
  },
  {
    index: '10',
    label: 'Users and access',
    group: 'Administration',
    to: () => '/admin',
    matches: (p) => p === '/admin',
    needsReview: false,
    roles: ['admin'],
  },
]

const NAV_GROUPS: NavGroup[] = ['RFQ process', 'Bid evaluation', 'Administration']

/** The `:slug` of a bid-evaluation URL, or `null` when the path carries none. */
function bidSetSlug(pathname: string): string | null {
  const m = /^\/bid-sets\/([^/]+)\//.exec(pathname)
  // `/bid-sets/new` is the create screen, not a project called "new".
  return m && m[1] !== 'new' ? m[1] : null
}

export default function App() {
  const { user, ready, logout } = useAuth()
  const [tick, setTick] = useState(0)
  const location = useLocation()
  const navigate = useNavigate()
  // Nothing is fetched until a session is confirmed: while signed out (or
  // still booting) the loader resolves to an empty list without hitting the
  // network, and the moment `user` changes — sign-in, sign-out — this re-runs
  // because `user` is a dependency, so the roster is never left showing a
  // stale or unauthenticated fetch's result.
  const { data: projects, error, loading } = useAsync<ProjectSummary[]>(
    () => (user ? fetchProjects() : Promise.resolve([])),
    [tick, user],
  )

  // The URL is the authority on which project a bid-evaluation screen is
  // showing. `remembered` only answers the question the URL cannot: which
  // project the switcher and the status bar should name while we are on
  // `/projects` or `/bidders`, where no slug is in the path. Deriving in this
  // order is what stops the two from ever disagreeing.
  const pathSlug = bidSetSlug(location.pathname)
  const [remembered, setRemembered] = useState<string | null>(null)
  // Derived during render, not defaulted from an effect. An effect would leave
  // one commit where the roster has arrived but `slug` is still null — and in
  // that commit "Set up & ingest" points at `/bid-sets/new`, so a fast click
  // lands on the create form instead of the project that is right there in the
  // switcher. Falling through to the first project here closes that window
  // rather than narrowing it.
  const slug = pathSlug ?? remembered ?? projects?.[0]?.slug ?? null

  useEffect(() => {
    if (pathSlug) setRemembered(pathSlug)
  }, [pathSlug])

  const active = projects?.find((p) => p.slug === slug) ?? null
  const reload = () => setTick((t) => t + 1)

  /**
   * The rail's project switcher.
   *
   * On a bid-evaluation screen this stays on the same screen for the newly
   * chosen project. It no longer carries the I1 special case — the rule that
   * switching off a review screen onto a project with no extraction must land
   * on setup. `RequireResults` makes that decision now, for this path and for a
   * pasted link equally.
   */
  function selectProject(next: string | null) {
    setRemembered(next)
    if (next && pathSlug) {
      navigate(location.pathname.replace(`/bid-sets/${pathSlug}/`, `/bid-sets/${next}/`))
    }
  }

  // Every hook above must run on every render regardless of auth state, so
  // these gates come after them rather than before.
  if (!ready) return <div className="boot" />
  // No redirect here: the URL is left exactly as it was, so signing in returns
  // the user to the address they arrived on rather than to the roster.
  if (!user) return <Auth />

  const visibleNav = NAV.filter((item) => !item.roles || item.roles.includes(user.role))

  return (
    <NavHistoryProvider>
      <div className="shell">
        <aside className="rail">
          <div className="rail-brand">
            <div className="rail-mark">TE</div>
            <div>
              <strong>Tender Eval</strong>
              <span>procurement review</span>
            </div>
          </div>

          <div className="rail-scroll">
            <div className="rail-label">Project</div>
            <div className="switcher">
              <label className="sr-only" htmlFor="project-switch">
                Active project
              </label>
              <select
                id="project-switch"
                value={slug ?? ''}
                onChange={(e) => selectProject(e.target.value || null)}
                disabled={!projects || projects.length === 0}
              >
                {(!projects || projects.length === 0) && (
                  <option value="">No projects</option>
                )}
                {projects?.map((p) => (
                  <option key={p.slug} value={p.slug}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>

            {NAV_GROUPS.map((group) => {
              const items = visibleNav.filter((item) => item.group === group)
              if (items.length === 0) return null
              return (
                <div key={group}>
                  <div className="rail-label">{group}</div>
                  <ul className="rail-nav">
                    {items.map((item) => {
                      const href = item.to(slug)
                      const disabled =
                        href === null ||
                        (item.needsReview && !reviewReachable(active?.has_results))
                      return (
                        <li key={item.index}>
                          <button
                            type="button"
                            // Derived from the URL, never held alongside it —
                            // so no navigation can leave the highlight and the
                            // screen disagreeing.
                            className={item.matches(location.pathname) ? 'active' : ''}
                            disabled={disabled}
                            style={disabled ? { opacity: 0.4, cursor: 'not-allowed' } : undefined}
                            onClick={() => href && navigate(href)}
                          >
                            <span className="nav-index">{item.index}</span>
                            {item.label}
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                </div>
              )
            })}
          </div>

          <div className="rail-account">
            <div className="rail-who">
              <span className="rail-email">{user.email}</span>
              <span className="rail-role">{user.role}</span>
            </div>
            <button type="button" className="rail-signout" onClick={() => logout()}>
              Sign out
            </button>
          </div>

          <div className="rail-foot">
            Set up a project, ingest vendor bids, then review the results — all
            here.
          </div>
        </aside>

        <div className="canvas">
          <div className="statusbar">
            <BackForwardControls />
            {active ? (
              <>
                <span className="sb-item">
                  <span className="sb-dot" />
                  <b>{active.name}</b>
                </span>
                <span className="sb-item">
                  gen <b>{active.generation}</b>
                </span>
                <span className="sb-item">
                  currency <b>{active.target_currency}</b>
                </span>
                <span className="sb-item">
                  vendors <b>{active.vendors.length}</b>
                </span>
              </>
            ) : (
              <span className="sb-item">
                <span className="sb-dot" />
                <b>{projects?.length ?? 0}</b> projects
              </span>
            )}
            <span className="sb-spacer" />
            <span className="sb-item">API :8000</span>
          </div>

          <main className="canvas-scroll">
            <div className="wrap">
              <AppRoutes
                projects={projects}
                loading={loading}
                error={error}
                reload={reload}
                user={user}
              />
            </div>
          </main>
        </div>
      </div>
    </NavHistoryProvider>
  )
}
