import { useState } from 'react'
import type { JSX } from 'react'
import { createWorkflowProject, fetchWorkflowProjects } from '../api'
import { useAsync } from '../useAsync'
import { ProjectForm } from './forms'
import { ProjectDetail } from './ProjectDetail'
import { ItemDetail } from './ItemDetail'
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
        <Card title="Projects">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Code</th>
                <th scope="col">Client</th>
                <th scope="col">Location</th>
                <th scope="col">Live period</th>
                <th scope="col">Status</th>
                <th scope="col">Items</th>
                <th scope="col">RFQs</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((p) => (
                <tr key={p.id}>
                  <td>
                    {/* A button, not a clickable row: a row with an onClick is
                        unreachable by keyboard and announces nothing. */}
                    <button
                      type="button"
                      className="linkish"
                      onClick={() => setOpenProjectId(p.id)}
                    >
                      {p.name}
                    </button>
                  </td>
                  <td className="mono">{p.code}</td>
                  <td>{p.client}</td>
                  <td>{p.location}</td>
                  <td className="mono">
                    {p.live_period_start} → {p.live_period_end}
                  </td>
                  <td>{p.status}</td>
                  <td className="mono">{p.item_count}</td>
                  <td className="mono">{p.rfq_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  )
}
