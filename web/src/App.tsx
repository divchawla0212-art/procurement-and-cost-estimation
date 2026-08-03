import { useEffect, useState } from 'react'
import { fetchProjects } from './api'
import type { ProjectSummary } from './types'
import { useAsync } from './useAsync'
import { Dashboard } from './pages/Dashboard'
import { Overview } from './pages/Overview'
import { ComplianceMatrix } from './pages/ComplianceMatrix'
import { ComparativeStatement } from './pages/ComparativeStatement'
import { ExtractionStatus } from './pages/ExtractionStatus'
import { Setup } from './pages/Setup'

type View = 'dashboard' | 'setup' | 'overview' | 'matrix' | 'statement' | 'extraction'

const NAV: { view: View; index: string; label: string; needsProject: boolean }[] = [
  { view: 'dashboard', index: '00', label: 'Dashboard', needsProject: false },
  { view: 'setup', index: '01', label: 'Set up & ingest', needsProject: false },
  { view: 'overview', index: '02', label: 'Overview', needsProject: true },
  { view: 'matrix', index: '03', label: 'Compliance matrix', needsProject: true },
  { view: 'statement', index: '04', label: 'Comparative statement', needsProject: true },
  { view: 'extraction', index: '05', label: 'Extraction status', needsProject: true },
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
  // A vendor to pre-select when the matrix is opened from the Overview, so a
  // click on a vendor there lands on that vendor's rows rather than "all".
  const [matrixVendor, setMatrixVendor] = useState<string | null>(null)

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
    setView('overview')
  }

  function openMatrix(vendor?: string) {
    setMatrixVendor(vendor ?? null)
    setView('matrix')
  }

  function navigate(next: View) {
    // A plain nav click to the matrix shows every vendor; only an Overview
    // drill-down carries a vendor filter into it.
    if (next === 'matrix') setMatrixVendor(null)
    setView(next)
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
              onChange={(e) => setSlug(e.target.value || null)}
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
              const disabled = item.needsProject && !slug
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
                initialVendor={matrixVendor ?? undefined}
              />
            )}
            {view === 'statement' && active && (
              <ComparativeStatement slug={active.slug} projectName={active.name} />
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
