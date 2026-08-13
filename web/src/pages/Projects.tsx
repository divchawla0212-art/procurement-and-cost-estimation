import { useState } from 'react'
import type { JSX } from 'react'
import { createWorkflowProject, fetchWorkflowProjects } from '../api'
import { useAsync } from '../useAsync'
import { ProjectForm } from './forms'
import { ProjectDetail } from './ProjectDetail'
import { ItemDetail } from './ItemDetail'
import type { WorkflowProjectSummary } from '../types'
import {
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from '../components/primitives'

/**
 * The project → item hierarchy, and the way into it.
 *
 * A project is the top-level container the client gives us — Haliba, say. Its
 * equipment are items, and an RFQ is raised against one or more of them. This
 * screen owns the drill-down state and hands off to one of two detail screens,
 * the way `RfqWorkflow` owns `openRfqId` and hands off to the wizard. `App`
 * therefore learns one new view and nothing about items.
 *
 * Note these are the *workflow* store's projects, not the ingestion projects in
 * the rail's switcher. The two are deliberately separate for now.
 */
/**
 * One project, as a card.
 *
 * The detail sits inside a native `<details>` rather than a JS-toggled div:
 * the disclosure is keyboard-operable and announced as expanded/collapsed
 * without any of that being written here, and the card carries no state of its
 * own, so a roster reload cannot collapse a card the reader had just opened.
 *
 * Closed, the card shows only what tells two projects apart at a glance — the
 * name, the code, how much work is under it, and whether it is live. Everything
 * else is one click away rather than on screen at all times.
 */
function ProjectCard({
  project,
  onOpen,
}: {
  project: WorkflowProjectSummary
  onOpen: () => void
}): JSX.Element {
  return (
    <article className="pcard">
      <header className="pcard-head">
        <h3 className="pcard-title">
          {/* A button, not a clickable card: a div with an onClick is
              unreachable by keyboard and announces nothing. */}
          <button type="button" className="linkish" onClick={onOpen}>
            {project.name}
          </button>
        </h3>
        <span className="pcard-code mono">{project.code}</span>
      </header>

      <div className="pcard-facts">
        <span className={`pcard-status pcard-status--${statusModifier(project.status)}`}>
          {project.status}
        </span>
        <span className="pcard-count">
          <b>{project.item_count}</b>{' '}
          {project.item_count === 1 ? 'item' : 'items'}
        </span>
        <span className="pcard-count">
          <b>{project.rfq_count}</b> {project.rfq_count === 1 ? 'RFQ' : 'RFQs'}
        </span>
      </div>

      <details className="pcard-details">
        <summary>Details</summary>
        <dl className="pcard-kv">
          <dt>Project code</dt>
          <dd className="mono">{project.code}</dd>

          <dt>Client</dt>
          <dd>{project.client}</dd>

          <dt>Location</dt>
          <dd>{project.location}</dd>

          <dt>Live period</dt>
          <dd className="mono">
            {project.live_period_start} → {project.live_period_end}
          </dd>

          <dt>Currency</dt>
          <dd className="mono">{project.currency}</dd>

          <dt>Status</dt>
          <dd>{project.status}</dd>

          <dt>Items</dt>
          <dd className="mono">{project.item_count}</dd>

          <dt>RFQs raised</dt>
          <dd className="mono">{project.rfq_count}</dd>
        </dl>
      </details>

      <button type="button" className="btn btn-sm pcard-open" onClick={onOpen}>
        Open project
      </button>
    </article>
  )
}

/** `On Hold` → `on-hold`, so the status can carry a colour without the class
 *  name depending on how the label happens to be capitalised. */
function statusModifier(status: WorkflowProjectSummary['status']): string {
  return status.toLowerCase().replace(/\s+/g, '-')
}

export function Projects(): JSX.Element {
  const [tick, setTick] = useState(0)
  const [openProjectId, setOpenProjectId] = useState<string | null>(null)
  const [openItemId, setOpenItemId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const { data, error, loading } = useAsync(() => fetchWorkflowProjects(), [tick])

  const reload = () => setTick((t) => t + 1)

  // The drill-down returns before the roster renders, exactly as RfqWorkflow
  // returns its wizard before its table.
  if (openProjectId && openItemId) {
    return (
      <ItemDetail
        projectId={openProjectId}
        itemId={openItemId}
        onBack={() => setOpenItemId(null)}
      />
    )
  }
  if (openProjectId) {
    return (
      <ProjectDetail
        projectId={openProjectId}
        onOpenItem={setOpenItemId}
        onBack={() => {
          setOpenProjectId(null)
          // Counts on the roster have to account for anything added or deleted
          // while the user was inside.
          reload()
        }}
      />
    )
  }

  if (loading) return <LoadingState label="Loading projects…" />
  if (error) return <ErrorState message={error} />

  const projects = data ?? []

  return (
    <>
      <PageHeader
        eyebrow="01 · RFQ process"
        title="Projects & items"
        sub="Every project, the equipment items under it, and the RFQs raised against them."
        actions={
          !creating && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setCreating(true)}
            >
              New project
            </button>
          )
        }
      />

      {creating && (
        <Card title="New project">
          <ProjectForm
            submitLabel="Create project"
            onCancel={() => setCreating(false)}
            onSubmit={async (body) => {
              await createWorkflowProject(body)
              setCreating(false)
              reload()
            }}
          />
        </Card>
      )}

      {projects.length === 0 ? (
        <EmptyState title="No projects yet">
          A project is the container a client gives you — create one, then add
          the equipment items that will be tendered under it.
        </EmptyState>
      ) : (
        /* One card per project, not one row per project. A table puts eight
           columns of a project's detail on a single line, and at four or five
           projects that is a wall of text with no shape to it — the reader
           scans horizontally to find a field, then vertically to find the
           project. A card names the project once, keeps the two numbers that
           distinguish it at a glance, and folds the rest away. */
        <div className="project-grid">
          {projects.map((p) => (
            <ProjectCard
              key={p.id}
              project={p}
              onOpen={() => setOpenProjectId(p.id)}
            />
          ))}
        </div>
      )}
    </>
  )
}
