import { useEffect, useState } from 'react'
import { fetchProjects } from './api'
import type { ProjectSummary } from './types'
import { useAsync } from './useAsync'
import { reviewReachable } from './nav'
import { useAuth } from './auth/context'
import type { User } from './auth/context'
import { Auth } from './pages/Auth'
import { Dashboard } from './pages/Dashboard'
import { Overview } from './pages/Overview'
import { ComplianceMatrix } from './pages/ComplianceMatrix'
import { ComparativeStatement } from './pages/ComparativeStatement'
import { ExtractionStatus } from './pages/ExtractionStatus'
import { Setup } from './pages/Setup'
import { Admin } from './pages/Admin'
import { RfqWorkflow } from './pages/RfqWorkflow'
import { Projects } from './pages/Projects'

type View =
  | 'projects'
  | 'dashboard'
  | 'setup'
  | 'overview'
  | 'matrix'
  | 'statement'
  | 'extraction'
  | 'workflow'
  | 'admin'

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
// `roles` is omitted for every screen both roles can reach; the one entry that
// carries it is hidden from a reviewer's nav. This is presentation only — the
// server enforces the actual boundary, and every `/api/admin/*` route refuses
// a reviewer whether or not this list ever mentioned it.
// Grouped, because the two halves of this app answer different questions. The
// RFQ process is where a package is scoped, issued and awarded; bid evaluation
// is the ingestion pipeline and the screens that read what it extracted. They
// share a project but not a workflow, and a single flat list of eight entries
// invited the reading that ingestion *is* the process.
type NavGroup = 'RFQ process' | 'Bid evaluation' | 'Administration'

const NAV: {
  view: View
  index: string
  label: string
  group: NavGroup
  needsProject: boolean
  needsReview: boolean
  roles?: Array<User['role']>
}[] = [
  // Both RFQ-process screens are project-agnostic in the sense these two gates
  // mean it: their projects are the workflow store's own, not the ingestion
  // `slug`s in the switcher above, so neither gate applies. The Bid-evaluation
  // entry is called "Bid sets" rather than "Projects" for the same reason —
  // two rail entries reading "Projects" over two different stores is the first
  // confusion a reader hits. Unifying the two identities is phase 2.
  { view: 'projects', index: '01', label: 'Projects & items', group: 'RFQ process', needsProject: false, needsReview: false },
  { view: 'workflow', index: '02', label: 'RFQ workflow', group: 'RFQ process', needsProject: false, needsReview: false },
  { view: 'dashboard', index: '03', label: 'Bid sets', group: 'Bid evaluation', needsProject: false, needsReview: false },
  { view: 'setup', index: '04', label: 'Set up & ingest', group: 'Bid evaluation', needsProject: false, needsReview: false },
  { view: 'extraction', index: '05', label: 'Extraction status', group: 'Bid evaluation', needsProject: true, needsReview: false },
  { view: 'overview', index: '06', label: 'Overview', group: 'Bid evaluation', needsProject: true, needsReview: true },
  { view: 'matrix', index: '07', label: 'Compliance matrix', group: 'Bid evaluation', needsProject: true, needsReview: true },
  { view: 'statement', index: '08', label: 'Comparative statement', group: 'Bid evaluation', needsProject: true, needsReview: true },
  {
    view: 'admin',
    index: '09',
    label: 'Users and access',
    group: 'Administration',
    needsProject: false,
    needsReview: false,
    roles: ['admin'],
  },
]

const NAV_GROUPS: NavGroup[] = ['RFQ process', 'Bid evaluation', 'Administration']

export default function App() {
  const { user, ready, logout } = useAuth()
  const [tick, setTick] = useState(0)
  // Nothing is fetched until a session is confirmed: while signed out (or
  // still booting) the loader resolves to an empty list without hitting the
  // network, and the moment `user` changes — sign-in, sign-out — this re-runs
  // because `user` is a dependency, so the roster is never left showing a
  // stale or unauthenticated fetch's result.
  const { data: projects, error, loading } = useAsync<ProjectSummary[]>(
    () => (user ? fetchProjects() : Promise.resolve([])),
    [tick, user],
  )
  // The project is the top-level container the client gives us, so signing in
  // lands on the project roster. Phase 1 landed on the RFQ workflow on the
  // reasoning that ingestion is not the process; that still holds — this is
  // one step further up the same half of the app, not a move back to the
  // other one.
  const [view, setView] = useState<View>('projects')
  const [slug, setSlug] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  // A vendor to pre-select when the matrix is opened from the Overview, so a
  // click on a vendor there lands on that vendor's rows rather than "all".
  const [matrixVendor, setMatrixVendor] = useState<string | null>(null)
  // Incremented by every rail click. Used as the `key` of the two sections that
  // hold their own drill-down state, so clicking the section you are already in
  // returns you to its top level. See `navigate`.
  const [navEpoch, setNavEpoch] = useState(0)

  useEffect(() => {
    if (projects && projects.length && slug === null) {
      setSlug(projects[0].slug)
    }
  }, [projects, slug])

  const active = projects?.find((p) => p.slug === slug) ?? null
  const reload = () => setTick((t) => t + 1)

  function open(nextSlug: string) {
    setSlug(nextSlug)
    setMatrixVendor(null)
    // Overview is where an opened project lands, but only once there is an
    // extraction behind it (BUG-001) — otherwise it would mount and fetch a
    // matrix and statement that do not exist yet.
    const project = projects?.find((p) => p.slug === nextSlug)
    setView(reviewReachable(project?.has_results) ? 'overview' : 'setup')
  }

  function openMatrix(vendor?: string) {
    setMatrixVendor(vendor ?? null)
    setView('matrix')
  }

  // The setup screen's "Review compliance matrix" button lands on the matrix
  // rather than the Overview `open()` routes to, because that is what its
  // label promises. It only renders when `has_results` is true, so the
  // reachability gate is already satisfied at the call site.
  function openMatrixFor(nextSlug: string) {
    setSlug(nextSlug)
    openMatrix()
  }

  // I1 (final-review report): the rail's project switcher used to call
  // `setSlug` directly and never touch `view`, so switching away from a
  // `done` project while sitting on a review screen left that screen
  // mounted — refetching for the newly selected project even though its nav
  // button just greyed out. `open()` already computes the right destination
  // for a freshly *opened* project; this reuses the same rule, but only
  // overrides `view` when the current screen actually needs review and the
  // new project can't supply it — a switch onto the dashboard, setup or
  // extraction-status screen (none of which need a completed review) must
  // not be yanked around.
  function selectProject(nextSlug: string | null) {
    setSlug(nextSlug)
    const project = nextSlug ? projects?.find((p) => p.slug === nextSlug) ?? null : null
    setView((v) => {
      const item = NAV.find((n) => n.view === v)
      return item?.needsReview && !reviewReachable(project?.has_results) ? 'setup' : v
    })
  }

  function navigate(next: View) {
    // A plain nav click to the matrix shows every vendor; only an Overview
    // drill-down carries a vendor filter into it.
    if (next === 'matrix') setMatrixVendor(null)
    setView(next)
    // Bump on every rail click, which is what makes a click on the section you
    // are *already* in mean something.
    //
    // `Projects` and `RfqWorkflow` each own their drill-down state
    // (`openProjectId` / `openItemId`, `openRfqId`). Without this, clicking
    // "01 Projects & items" while three levels deep inside an item ran
    // `setView('projects')` against a view that was already `'projects'` — a
    // no-op — so the component never re-rendered, kept its drill-down state,
    // and the rail button read as dead. That is what "these two pages are not
    // working" was: not a missing back control, a nav entry that did nothing.
    //
    // Used as a React `key` below, so the section remounts and its drill-down
    // state goes with it. Remounting also refetches, which is correct here:
    // returning to a roster should show what is there now, not what was there
    // when you drilled in.
    setNavEpoch((n) => n + 1)
  }

  function startNew() {
    setCreating(true)
    setView('setup')
  }

  function onCreated(nextSlug: string) {
    setSlug(nextSlug)
    setCreating(false)
    reload()
  }

  // Every hook above must run on every render regardless of auth state, so
  // these gates come after them rather than before.
  if (!ready) return <div className="boot" />
  if (!user) return <Auth />

  const visibleNav = NAV.filter((item) => !item.roles || item.roles.includes(user.role))

  return (
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
                    const disabled =
                      (item.needsProject && !slug) ||
                      (item.needsReview && !reviewReachable(active?.has_results))
                    return (
                      <li key={item.view}>
                        <button
                          type="button"
                          className={view === item.view ? 'active' : ''}
                          disabled={disabled}
                          style={disabled ? { opacity: 0.4, cursor: 'not-allowed' } : undefined}
                          onClick={() => navigate(item.view)}
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
            {view === 'dashboard' && (
              <Dashboard
                projects={projects}
                loading={loading}
                error={error}
                onOpen={open}
                onNew={startNew}
              />
            )}
            {view === 'setup' && (
              <Setup
                slug={creating ? null : active?.slug ?? null}
                onCreated={onCreated}
                onNew={startNew}
                reload={reload}
                onOpen={openMatrixFor}
              />
            )}
            {view === 'overview' && active && (
              <Overview
                slug={active.slug}
                projectName={active.name}
                onOpenMatrix={openMatrix}
              />
            )}
            {view === 'matrix' && active && (
              <ComplianceMatrix
                slug={active.slug}
                projectName={active.name}
                status={active.status}
                initialVendor={matrixVendor ?? undefined}
              />
            )}
            {view === 'statement' && active && (
              <ComparativeStatement
                slug={active.slug}
                projectName={active.name}
                status={active.status}
              />
            )}
            {view === 'extraction' && active && (
              <ExtractionStatus slug={active.slug} projectName={active.name} />
            )}
            {view === 'projects' && <Projects key={navEpoch} />}
            {view === 'workflow' && <RfqWorkflow key={navEpoch} />}
            {view === 'admin' && user.role === 'admin' && (
              // The role check is repeated here rather than trusted from the
              // nav filter: `view` is component state, so a stale 'admin' left
              // over from a previous session would otherwise render the screen
              // for whoever signs in next. Its calls would 403, but the screen
              // should not appear at all.
              <Admin projects={projects ?? []} meId={user.id} />
            )}
          </div>
        </main>
      </div>
    </div>
  )
}
