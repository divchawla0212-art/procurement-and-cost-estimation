import type { JSX, ReactElement } from 'react'
import { Navigate, Route, Routes, useNavigate, useParams, useSearchParams } from 'react-router'
import { fetchRfqRoster } from './api'
import { useAsync } from './useAsync'
import { reviewReachable } from './nav'
import type { ProjectSummary } from './types'
import type { User } from './auth/context'
import { ErrorState, LoadingState } from './components/primitives'
import { Projects } from './pages/Projects'
import { ProjectDetail } from './pages/ProjectDetail'
import { ItemDetail } from './pages/ItemDetail'
import { Bidders } from './pages/Bidders'
import { RfqWorkflow } from './pages/RfqWorkflow'
import { RfqWizard } from './pages/RfqWizard'
import { RfqDetail } from './pages/RfqDetail'
import { Dashboard } from './pages/Dashboard'
import { Setup } from './pages/Setup'
import { Overview } from './pages/Overview'
import { ComplianceMatrix } from './pages/ComplianceMatrix'
import { ComparativeStatement } from './pages/ComparativeStatement'
import { ExtractionStatus } from './pages/ExtractionStatus'
import { Admin } from './pages/Admin'

/**
 * Every address this application has.
 *
 * Routing lives here and nowhere else, so "where can this go?" is one file to
 * read rather than a grep for `useNavigate` across fourteen screens. The screens
 * themselves keep the prop interfaces they already had — `projectId`, `slug`,
 * `onBack` — and the thin wrappers below adapt URL parameters to those props.
 * That is what keeps each screen testable on its own, without a router.
 *
 * Two identities share this table and are deliberately not merged: `/projects`
 * addresses the *workflow* store by id (`prj_…`), and `/bid-sets` addresses the
 * *ingestion* store by slug. They are different entities in different stores;
 * unifying them is phase 2, and until then the paths say so.
 */
export interface AppRoutesProps {
  /** The ingestion project roster, from `App`'s loader. `null` while in flight. */
  projects: ProjectSummary[] | null
  loading: boolean
  error: string | null
  reload: () => void
  user: User
}

export function AppRoutes(props: AppRoutesProps): JSX.Element {
  return (
    <Routes>
      {/* `replace` on every redirect in this table. A redirect that pushes puts
          the redirecting URL into the history, so Back lands on it and is
          immediately thrown forward again — which the user reads as a Back
          button that does not work. */}
      <Route path="/" element={<Navigate to="/projects" replace />} />

      {/* ---- RFQ process: the workflow store, addressed by id ---- */}
      <Route path="/projects" element={<Projects />} />
      <Route path="/projects/:projectId" element={<ProjectDetailRoute />} />
      <Route path="/projects/:projectId/items/:itemId" element={<ItemDetailRoute />} />
      <Route path="/bidders" element={<Bidders />} />
      <Route path="/rfqs" element={<RfqWorkflow />} />
      <Route path="/rfqs/:rfqId" element={<RfqRoute />} />

      {/* ---- Bid evaluation: the ingestion store, addressed by slug ---- */}
      <Route path="/bid-sets" element={<DashboardRoute {...props} />} />
      <Route path="/bid-sets/new" element={<SetupRoute {...props} />} />
      <Route path="/bid-sets/:slug/setup" element={<SetupRoute {...props} />} />
      <Route
        path="/bid-sets/:slug/extraction"
        element={
          <RequireProject {...props}>
            {(project) => (
              <ExtractionStatus slug={project.slug} projectName={project.name} />
            )}
          </RequireProject>
        }
      />
      <Route
        path="/bid-sets/:slug/overview"
        element={
          <RequireResults {...props}>
            {(project) => <OverviewRoute project={project} />}
          </RequireResults>
        }
      />
      <Route
        path="/bid-sets/:slug/matrix"
        element={
          <RequireResults {...props}>
            {(project) => <MatrixRoute project={project} />}
          </RequireResults>
        }
      />
      <Route
        path="/bid-sets/:slug/statement"
        element={
          <RequireResults {...props}>
            {(project) => (
              <ComparativeStatement
                slug={project.slug}
                projectName={project.name}
                status={project.status}
              />
            )}
          </RequireResults>
        }
      />

      <Route
        path="/admin"
        element={
          <RequireAdmin user={props.user}>
            <Admin projects={props.projects ?? []} meId={props.user.id} />
          </RequireAdmin>
        }
      />

      {/* A stray URL has nowhere more useful to be than the roster the
          application starts from, so there is no 404 screen. */}
      <Route path="*" element={<Navigate to="/projects" replace />} />
    </Routes>
  )
}

/* ------------------------------------------------------------------ guards */

/**
 * Resolves `:slug` to a project, or redirects.
 *
 * The load check comes first and is load-bearing: on a deep link the roster is
 * still in flight, and deciding before it lands would bounce every bookmarked
 * bid-evaluation URL to the roster.
 */
function RequireProject({
  projects,
  loading,
  error,
  children,
}: AppRoutesProps & { children: (project: ProjectSummary) => ReactElement }) {
  const { slug } = useParams<{ slug: string }>()
  if (loading || projects === null) return <LoadingState label="Loading project…" />
  if (error) return <ErrorState message={error} />
  const project = projects.find((p) => p.slug === slug)
  if (!project) return <Navigate to="/bid-sets" replace />
  return children(project)
}

/**
 * The three screens that read a stored extraction.
 *
 * `reviewReachable` is `nav.ts`'s tested predicate and is reused unchanged —
 * `status` is not the right gate, because a project whose second run failed
 * still holds the first run's complete extraction (CLAUDE.md: a failed
 * extraction never blanks previously-good stored data).
 *
 * This one guard replaces `App`'s old `selectProject` special case, and covers
 * a case that rule could not: a pasted link straight to a review screen of a
 * project nobody has ingested.
 */
function RequireResults(
  props: AppRoutesProps & { children: (project: ProjectSummary) => ReactElement },
) {
  return (
    <RequireProject {...props}>
      {(project) =>
        reviewReachable(project.has_results) ? (
          props.children(project)
        ) : (
          <Navigate to={`/bid-sets/${project.slug}/setup`} replace />
        )
      }
    </RequireProject>
  )
}

/**
 * Repeated here rather than trusted from the rail's filtered nav: `/admin` is a
 * URL anyone can type. The server refuses every `/api/admin/*` call from a
 * reviewer regardless — this stops the screen from mounting at all.
 */
function RequireAdmin({ user, children }: { user: User; children: ReactElement }) {
  if (user.role !== 'admin') return <Navigate to="/projects" replace />
  return children
}

/* ---------------------------------------------------------------- wrappers */

function ProjectDetailRoute() {
  const { projectId = '' } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  return (
    <ProjectDetail
      projectId={projectId}
      onOpenItem={(itemId) => navigate(`/projects/${projectId}/items/${itemId}`)}
      onBack={() => navigate('/projects')}
    />
  )
}

function ItemDetailRoute() {
  const { projectId = '', itemId = '' } = useParams<{ projectId: string; itemId: string }>()
  const navigate = useNavigate()
  return (
    <ItemDetail
      projectId={projectId}
      itemId={itemId}
      onBack={() => navigate(`/projects/${projectId}`)}
      onHome={() => navigate('/projects')}
    />
  )
}

/**
 * The one wrapper that fetches.
 *
 * Which screen an RFQ opens in depends on its stage, and a deep link arrives
 * with no roster loaded — so the choice `RfqWorkflow` used to make from data it
 * already had has to be made from data this route fetches. That is a real extra
 * request on a deep link, and the cost of the RFQ being addressable at all.
 */
const WIZARD_RANGE = ['Scoping', 'Shortlisting', 'Issued', 'Clarifications']

function RfqRoute() {
  const { rfqId = '' } = useParams<{ rfqId: string }>()
  const navigate = useNavigate()
  const { data, error, loading } = useAsync(() => fetchRfqRoster(), [])

  if (loading) return <LoadingState label="Loading RFQ…" />
  if (error) return <ErrorState message={error} />
  if (!data) return <ErrorState message="No workflow data was returned." />

  const open = data.rfqs.find((r) => r.id === rfqId)
  // An RFQ we cannot find a stage for gets the wizard, matching what
  // `RfqWorkflow` did with the same unknown.
  const Screen = !open || WIZARD_RANGE.includes(open.stage) ? RfqWizard : RfqDetail
  return <Screen rfqId={rfqId} stages={data.stages} onBack={() => navigate('/rfqs')} />
}

function DashboardRoute({ projects, loading, error }: AppRoutesProps) {
  const navigate = useNavigate()
  return (
    <Dashboard
      projects={projects}
      loading={loading}
      error={error}
      // Where an opened bid set lands: the Overview once there is an extraction
      // behind it, setup otherwise. `RequireResults` would bounce it anyway —
      // sending it straight to the right place keeps the redirect off the
      // history rather than relying on the guard to clean up after us.
      onOpen={(slug) => {
        const project = projects?.find((p) => p.slug === slug)
        navigate(
          reviewReachable(project?.has_results)
            ? `/bid-sets/${slug}/overview`
            : `/bid-sets/${slug}/setup`,
        )
      }}
      onNew={() => navigate('/bid-sets/new')}
    />
  )
}

/**
 * Serves both `/bid-sets/new` and `/bid-sets/:slug/setup`. `Setup` already
 * branches on `slug === null` between creating and configuring, so the two
 * routes are one component with the parameter present or absent.
 */
function SetupRoute({ reload }: AppRoutesProps) {
  const { slug } = useParams<{ slug: string }>()
  const navigate = useNavigate()
  return (
    <Setup
      slug={slug ?? null}
      onCreated={(nextSlug) => {
        reload()
        // `replace`: the freshly created project's setup screen takes the place
        // of the empty create form, so Back does not offer to create it again.
        navigate(`/bid-sets/${nextSlug}/setup`, { replace: true })
      }}
      onNew={() => navigate('/bid-sets/new')}
      onOpen={(nextSlug) => navigate(`/bid-sets/${nextSlug}/matrix`)}
      reload={reload}
    />
  )
}

function OverviewRoute({ project }: { project: ProjectSummary }) {
  const navigate = useNavigate()
  return (
    <Overview
      slug={project.slug}
      projectName={project.name}
      onOpenMatrix={(vendor) =>
        navigate(
          `/bid-sets/${project.slug}/matrix` +
            (vendor ? `?vendor=${encodeURIComponent(vendor)}` : ''),
        )
      }
    />
  )
}

function MatrixRoute({ project }: { project: ProjectSummary }) {
  const [params] = useSearchParams()
  // A filter, not a location — which is exactly the distinction a query string
  // draws, and what makes the Overview's vendor drill-down a link somebody can
  // send rather than a state nobody else can reach.
  const vendor = params.get('vendor')
  return (
    <ComplianceMatrix
      slug={project.slug}
      projectName={project.name}
      status={project.status}
      initialVendor={vendor ?? undefined}
    />
  )
}
