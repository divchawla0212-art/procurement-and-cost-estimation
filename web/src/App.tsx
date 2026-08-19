import { useEffect, useState } from 'react'
import { Route, Routes, useLocation, useNavigate } from 'react-router'
import { fetchProjects } from './api'
import type { ProjectSummary } from './types'
import { useAsync } from './useAsync'
import { NAV, NAV_GROUPS, bidSetSlug, nextPage, reviewReachable } from './nav'
import { useAuth } from './auth/context'
import { Auth } from './pages/Auth'
import { Landing } from './pages/Landing'
import { AppRoutes } from './routes'
import { BackForwardControls, NavHistoryProvider } from './NavHistory'
import { ShieldMark, BRAND_NAME } from './components/ShieldMark'



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

  // The URL is the authority on which bid set the evaluation screens are
  // showing, and `03 Bid sets` is where you choose one — opening a project
  // there navigates to `/bid-sets/:slug/…`, which is what makes that choice an
  // address rather than a hidden selection.
  //
  // `remembered` answers only the question the URL cannot: which bid set the
  // rail's evaluation entries and the status bar should point at while we are
  // on `/projects` or `/rfqs`, where no slug is in the path. Deriving in
  // this order is what stops the two from ever disagreeing.
  const pathSlug = bidSetSlug(location.pathname)
  const [remembered, setRemembered] = useState<string | null>(null)
  // Derived during render, not defaulted from an effect. An effect would leave
  // one commit where the roster has arrived but `slug` is still null — and in
  // that commit "Set up & ingest" points at `/bid-sets/new`, so a fast click
  // lands on the create form instead of the bid set the rail is about to point
  // at. Falling through to the first project here closes that window rather
  // than narrowing it.
  const slug = pathSlug ?? remembered ?? projects?.[0]?.slug ?? null

  useEffect(() => {
    if (pathSlug) setRemembered(pathSlug)
  }, [pathSlug])

  const active = projects?.find((p) => p.slug === slug) ?? null
  const reload = () => setTick((t) => t + 1)

  // Every hook above must run on every render regardless of auth state, so
  // these gates come after them rather than before.
  if (!ready) return <div className="boot" />

  if (!user) {
    return (
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/login" element={<Auth />} />
        {/* Every other address stays in the bar so signing in returns here. */}
        <Route path="*" element={<Auth />} />
      </Routes>
    )
  }

  const visibleNav = NAV.filter((item) => !item.roles || item.roles.includes(user.role))

  return (
    <NavHistoryProvider>
      <div className="shell">
        <aside className="rail">
          <div className="rail-brand">
            <ShieldMark size={28} tone="light" />
            <div>
              <strong>{BRAND_NAME}</strong>
              <span>Procurement workspace</span>
            </div>
          </div>

          <div className="rail-scroll">

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
                next={nextPage(location.pathname, {
                  slug,
                  hasResults: active?.has_results,
                  role: user.role,
                })}
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
