import { useEffect, useState } from 'react'
import { fetchProjects } from './api'
import type { ProjectSummary } from './types'
import { MatrixView } from './components/MatrixView'
import './App.css'

export default function App() {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchProjects()
      .then((list) => {
        setProjects(list)
        if (list.length) setSelected(list[0].slug)
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  const active = projects.find((p) => p.slug === selected) ?? null

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">PR</span>
          <div>
            <strong>Procurement Review</strong>
            <span className="brand-sub">Compliance matrix</span>
          </div>
        </div>

        <div className="sidebar-section">
          <h2>Projects</h2>
          {loading && <p className="muted">Loading…</p>}
          {error && <p className="error-text">{error}</p>}
          {!loading && !error && projects.length === 0 && (
            <p className="muted">
              No projects yet. Create one in the Streamlit portal, run
              ingestion, then refresh.
            </p>
          )}
          <ul className="project-list">
            {projects.map((p) => (
              <li key={p.slug}>
                <button
                  type="button"
                  className={p.slug === selected ? 'active' : ''}
                  onClick={() => setSelected(p.slug)}
                >
                  <span className="project-name">{p.name}</span>
                  <span className="project-meta">
                    {p.vendors.length
                      ? `${p.vendors.length} vendors`
                      : 'no vendors'}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>

        <footer className="sidebar-footer">
          <p className="muted">
            Upload &amp; ingestion stay in Streamlit. This UI is read-only
            review.
          </p>
        </footer>
      </aside>

      <main className="main">
        {active ? (
          <MatrixView slug={active.slug} projectName={active.name} />
        ) : (
          <div className="panel empty">
            <h1>Select a project</h1>
            <p className="lead">
              Choose a project from the left to open its compliance comparison
              matrix.
            </p>
          </div>
        )}
      </main>
    </div>
  )
}
