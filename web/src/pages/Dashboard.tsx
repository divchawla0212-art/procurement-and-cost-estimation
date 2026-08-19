import type { JSX } from 'react'
import type { GroupKey, ProjectSummary } from '../types'
import { fetchSummary } from '../api'
import { useAsync } from '../useAsync'
import { GROUP_LABEL } from '../constants'
import {
  CoverageInstrument,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from '../components/primitives'

export interface DashboardProps {
  projects: ProjectSummary[] | null
  loading: boolean
  error: string | null
  onOpen: (slug: string) => void
  onNew: () => void
}

const GROUP_ORDER: GroupKey[] = ['not_matched', 'needs_human', 'matched']

export function Dashboard(props: DashboardProps): JSX.Element {
  const { projects, loading, error, onOpen, onNew } = props

  return (
    <>
      <PageHeader
        eyebrow="Bid sets"
        title="Bid sets"
        sub="Every tender you are evaluating. Open one to review its compliance matrix and comparative statement, or start a new one."
        actions={
          <button type="button" className="btn btn-primary" data-testid="new-bid-set-button" onClick={onNew}>
            New project
          </button>
        }
      />
      {loading ? (
        <LoadingState label="Loading projects…" />
      ) : error ? (
        <ErrorState message={error} />
      ) : !projects || projects.length === 0 ? (
        <EmptyState glyph="∅" title="No projects yet">
          Start a project, attach the requirements document, upload vendor bids,
          and run ingestion — then the matrix and statement appear here.
          <span style={{ display: 'block', marginTop: '1rem' }}>
            <button type="button" className="btn btn-primary" onClick={onNew}>
              New project
            </button>
          </span>
        </EmptyState>
      ) : (
        <div className="grid-cards" data-testid="bid-set-list">
          {projects.map((project) => (
            <ProjectCard key={project.slug} project={project} onOpen={onOpen} />
          ))}
        </div>
      )}
    </>
  )
}

function ProjectCard({
  project,
  onOpen,
}: {
  project: ProjectSummary
  onOpen: (slug: string) => void
}): JSX.Element {
  const { data, loading } = useAsync(
    () => fetchSummary(project.slug),
    [project.slug],
  )

  return (
    <button
      type="button"
      className="pcard"
      data-testid={`bid-set-card-${project.slug}`}
      onClick={() => onOpen(project.slug)}
    >
      <div className="title">{project.name}</div>

      <div className="meta">
        {loading ? (
          <span className="skeleton" style={{ height: 14, width: 180 }} />
        ) : data ? (
          <>
            <span>gen {data.generation}</span>
            <span>{data.target_currency}</span>
            <span>{data.requirement_count} requirements</span>
          </>
        ) : (
          <span>gen {project.generation}</span>
        )}
      </div>

      <div className="vendors">
        {project.vendors.length > 0 ? (
          project.vendors.map((vendor) => (
            <span className="vpill" key={vendor}>
              {vendor}
            </span>
          ))
        ) : (
          <span className="muted mono" style={{ fontSize: '0.72rem' }}>
            no vendors
          </span>
        )}
      </div>

      {loading ? (
        <div className="skeleton" style={{ height: 14 }} aria-hidden />
      ) : data ? (
        <CoverageInstrument coverage={data.coverage} compact />
      ) : (
        <p className="muted mono" style={{ fontSize: '0.78rem', margin: 0 }}>
          coverage unavailable
        </p>
      )}

      {data && (
        <div
          className="meta"
          style={{ gap: '1rem' }}
          aria-label="Requirement groups"
        >
          {GROUP_ORDER.map((group) => (
            <span key={group}>
              {GROUP_LABEL[group]} {data.group_counts[group]}
            </span>
          ))}
        </div>
      )}

      {data?.extraction && data.extraction.length > 0 && (
        <div className="meta" aria-label="Extraction status">
          {data.extraction.map((v) => {
            // the single most useful signal on the page: a vendor with zero
            // technical facts, or one where a second read of a document
            // silently failed, both change an award decision.
            const flagged = v.fact_count === 0 || v.secondary_failed > 0
            return (
              <span key={v.vendor} className={flagged ? 'warn' : undefined}>
                {v.vendor} {v.extracted}/{v.extracted + v.failed + v.skipped} read
                {v.fact_count === 0 && ' · no facts'}
                {v.secondary_failed > 0 &&
                  ` · ${v.secondary_failed} second pass failed`}
              </span>
            )
          })}
        </div>
      )}
    </button>
  )
}
