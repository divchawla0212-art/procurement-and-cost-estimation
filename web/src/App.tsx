import { useEffect, useState } from 'react'
import { fetchProjects } from './api'
import type { ProjectSummary } from './types'
import { useAsync } from './useAsync'
import { reviewReachable } from './nav'
import { Dashboard } from './pages/Dashboard'
import { ComplianceMatrix } from './pages/ComplianceMatrix'
import { ComparativeStatement } from './pages/ComparativeStatement'
import { ExtractionStatus } from './pages/ExtractionStatus'
import { Setup } from './pages/Setup'

type View = 'dashboard' | 'setup' | 'matrix' | 'statement' | 'extraction'

// `needsReview` marks the two screens that read a stored extraction (BUG-001,
// BUGS_TRACKER.md): they need not just a selected project but one whose
// `has_results` passes `reviewReachable` (I2, final-review report —
// `status` alone is not the right gate: see `nav.ts`). `04 Extraction
// status` stays `needsProject` only — it is the screen that reports why
// 02/03 are unreachable, so it must stay open regardless of status.
const NAV: {
  view: View
  index: string
  label: string
  needsProject: boolean
  needsReview: boolean
}[] = [
  { view: 'dashboard', index: '00', label: 'Dashboard', needsProject: false, needsReview: false },
  { view: 'setup', index: '01', label: 'Set up & ingest', needsProject: false, needsReview: false },
  { view: 'matrix', index: '02', label: 'Compliance matrix', needsProject: true, needsReview: true },
  { view: 'statement', index: '03', label: 'Comparative statement', needsProject: true, needsReview: true },
  { view: 'extraction', index: '04', label: 'Extraction status', needsProject: true, needsReview: false },
]

export default function App() {
  const [tick, setTick] = useState(0)
  const { data: projects, error, loading } = useAsync<ProjectSummary[]>(
    fetchProjects,
    [tick],
  )
  const [view, setView] = useState<View>('dashboard')
  const [slug, setSlug] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    if (projects && projects.length && slug === null) {
      setSlug(projects[0].slug)
    }
  }, [projects, slug])

  const active = projects?.find((p) => p.slug === slug) ?? null
  const reload = () => setTick((t) => t + 1)

  function open(nextSlug: string) {
    setSlug(nextSlug)
    const project = projects?.find((p) => p.slug === nextSlug)
    setView(reviewReachable(project?.has_results) ? 'matrix' : 'setup')
  }

  // I1 (final-review report): the rail's project switcher used to call
  // `setSlug` directly and never touch `view`, so switching away from a
  // `done` project while sitting on `02`/`03` left that screen mounted —
  // refetching for the newly selected project even though its nav button
  // just greyed out. `open()` already computes the right destination for a
  // freshly *opened* project; this reuses the same rule, but only overrides
  // `view` when the current screen actually needs review and the new
  // project can't supply it — a switch onto the dashboard, setup or
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

  function startNew() {
    setCreating(true)
    setView('setup')
  }

  function onCreated(nextSlug: string) {
    setSlug(nextSlug)
    setCreating(false)
    reload()
  }

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

          <div className="rail-label">Screens</div>
          <ul className="rail-nav">
            {NAV.map((item) => {
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
                    onClick={() => setView(item.view)}
                  >
                    <span className="nav-index">{item.index}</span>
                    {item.label}
                  </button>
                </li>
              )
            })}
          </ul>
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
                onOpen={open}
              />
            )}
            {view === 'matrix' && active && (
              <ComplianceMatrix
                slug={active.slug}
                projectName={active.name}
                status={active.status}
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
          </div>
        </main>
      </div>
    </div>
  )
}
