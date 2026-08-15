import { useState } from 'react'
import type { JSX } from 'react'
import {
  createRfq,
  createWorkflowItem,
  deleteWorkflowItem,
  deleteWorkflowProject,
  extractRfqDoc,
  fetchWorkflowProject,
  updateWorkflowProject,
} from '../api'
import { useAsync } from '../useAsync'
import { ItemForm, ProjectForm, RaiseRfqForm } from './forms'
import type { WorkflowProject, WorkflowProjectInput } from '../types'
import {
  Breadcrumb,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  Metric,
  PageHeader,
} from '../components/primitives'

/**
 * One project: its own detail, the equipment items under it, and the RFQs
 * raised against them.
 *
 * The rule this screen exists to keep: **a refused delete renders its reason
 * beside the row that refused it.** Not a toast, not a page-level banner. The
 * server's 409 names the RFQ that blocks the delete, which is the only
 * actionable thing on the screen at that moment — the same rule the stage
 * gates already follow.
 */

/** A project as the edit form wants it: everything except its identity. */
function toInput(project: WorkflowProject): WorkflowProjectInput {
  const { id: _id, ...rest } = project
  return rest
}

/**
 * Only what actually changed.
 *
 * The server reads a PATCH body with `exclude_unset`, so sending the whole
 * object would turn every edit into a full overwrite — and would silently
 * re-send fields another user may have changed in between.
 */
function changed(
  before: WorkflowProjectInput,
  after: WorkflowProjectInput,
): Partial<WorkflowProjectInput> {
  const out: Partial<WorkflowProjectInput> = {}
  for (const key of Object.keys(after) as (keyof WorkflowProjectInput)[]) {
    if (after[key] !== before[key]) {
      out[key] = after[key] as never
    }
  }
  return out
}

export function ProjectDetail({
  projectId,
  onOpenItem,
  onBack,
}: {
  projectId: string
  onOpenItem: (itemId: string) => void
  onBack: () => void
}): JSX.Element {
  const [tick, setTick] = useState(0)
  const [editing, setEditing] = useState(false)
  const [adding, setAdding] = useState(false)
  const [confirmingProject, setConfirmingProject] = useState(false)
  const [confirmingItem, setConfirmingItem] = useState<string | null>(null)
  const [projectError, setProjectError] = useState<string | null>(null)
  // Keyed by item id, never by position: the row order is not a contract, and
  // a re-fetch can reorder the list under a stale index.
  const [rowError, setRowError] = useState<Record<string, string>>({})
  const [warning, setWarning] = useState<string | null>(null)
  // Item ids, not row indices — the set has to survive a re-fetch that
  // reorders the table.
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [raising, setRaising] = useState(false)

  const { data, error, loading } = useAsync(
    () => fetchWorkflowProject(projectId),
    [projectId, tick],
  )

  const reload = () => setTick((t) => t + 1)

  if (loading) return <LoadingState label="Loading project…" />
  if (error) return <ErrorState message={error} />
  if (!data) return <ErrorState message="No project data was returned." />

  const { project, items, rfqs } = data

  async function removeItem(itemId: string) {
    setConfirmingItem(null)
    setRowError((prev) => {
      const { [itemId]: _gone, ...rest } = prev
      return rest
    })
    try {
      await deleteWorkflowItem(projectId, itemId)
      // The caution belongs to the item that was last saved. Once an item is
      // gone, a caution about its delivery date is describing something the
      // reader can no longer see — worse than showing nothing.
      setWarning(null)
      reload()
    } catch (err) {
      setRowError((prev) => ({ ...prev, [itemId]: (err as Error).message }))
    }
  }

  async function removeProject() {
    setConfirmingProject(false)
    setProjectError(null)
    try {
      await deleteWorkflowProject(projectId)
      onBack()
    } catch (err) {
      setProjectError((err as Error).message)
    }
  }

  return (
    <>
      <Breadcrumb
        trail={[
          { label: 'Projects & items', onClick: onBack },
          { label: project.name },
        ]}
      />
      <PageHeader
        eyebrow="01 · RFQ process"
        title={project.name}
        sub={`${project.code} · ${project.client} · ${project.location}`}
        actions={
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button type="button" className="btn" onClick={onBack}>
              ← Back to projects
            </button>
            {!editing && (
              <button
                type="button"
                className="btn"
                onClick={() => setEditing(true)}
              >
                Edit project
              </button>
            )}
            <button
              type="button"
              className="btn btn-danger"
              onClick={() => setConfirmingProject(true)}
            >
              Delete project
            </button>
          </div>
        }
      />

      {projectError && (
        <div className="banner banner--error" role="alert">
          {projectError}
        </div>
      )}

      {confirmingProject && (
        <div className="banner banner--warn" role="alert">
          {/* The count is stated because the cascade is not obvious from the
              button. The server is still the only authority on whether the
              delete is allowed — this only stops it being an accident. */}
          Delete {project.name} and its {items.length}{' '}
          {items.length === 1 ? 'item' : 'items'}?{' '}
          <button
            type="button"
            className="btn btn-sm btn-danger"
            aria-label={`Confirm delete of ${project.name}`}
            onClick={removeProject}
          >
            Delete
          </button>{' '}
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={() => setConfirmingProject(false)}
          >
            Cancel
          </button>
        </div>
      )}

      {editing ? (
        <Card title="Edit project">
          <ProjectForm
            initial={toInput(project)}
            submitLabel="Save project"
            onCancel={() => setEditing(false)}
            onSubmit={async (body) => {
              await updateWorkflowProject(projectId, changed(toInput(project), body))
              setEditing(false)
              reload()
            }}
          />
        </Card>
      ) : (
        <Card title="Project detail">
          <div className="metrics">
            <Metric
              k="Live period"
              v={`${project.live_period_start} → ${project.live_period_end}`}
            />
            <Metric k="Currency" v={project.currency} />
            <Metric k="Status" v={project.status} />
            <Metric k="Items" v={items.length} sub={`${rfqs.length} RFQs`} />
          </div>
        </Card>
      )}

      <Card
        title="Items"
        actions={
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            {!raising && (
              <button
                type="button"
                className="btn btn-sm"
                // An RFQ covering no item is meaningless, and the server
                // refuses it anyway — so the control says so before the click.
                disabled={selected.size === 0}
                onClick={() => setRaising(true)}
              >
                Raise RFQ
              </button>
            )}
            {!adding && (
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => setAdding(true)}
              >
                Add item
              </button>
            )}
          </div>
        }
      >
        {warning && (
          <div className="banner banner--warn" role="alert">
            {warning}
          </div>
        )}

        {raising && (
          <RaiseRfqForm
            projectId={projectId}
            itemIds={[...selected]}
            onExtract={extractRfqDoc}
            onCancel={() => setRaising(false)}
            onSubmit={async (body) => {
              const raised = await createRfq(body)
              // The items' draft picks became invitations server-side. Any the
              // server refused are named here rather than dropped: the buyer
              // chose them, and a shortlist silently one row short is the
              // failure this reports. The same banner the live-period warning
              // uses — this is a caution, not a failed creation.
              setWarning(
                raised.shortlist_skipped.length
                  ? `${raised.shortlist_adopted} of the vendors you picked were ` +
                    `shortlisted. ` +
                    raised.shortlist_skipped
                      .map((s) => `${s.vendor_name}: ${s.reason}`)
                      .join(' ')
                  : null,
              )
              setRaising(false)
              setSelected(new Set())
              // The user stays on the project — the new RFQ appears in the
              // table below, and they click into the wizard from there.
              reload()
            }}
          />
        )}

        {adding && (
          <ItemForm
            submitLabel="Save item"
            onCancel={() => setAdding(false)}
            onSubmit={async (body) => {
              const saved = await createWorkflowItem(projectId, body)
              // Advisory, not a refusal: the item is stored either way, so the
              // form closes and the caution stays on screen beside the table.
              setWarning(saved.live_period_warning)
              setAdding(false)
              reload()
            }}
          />
        )}

        {items.length === 0 && !adding ? (
          <EmptyState title="No items yet">
            Add the equipment this project will tender — a generator, a cable
            run, a transformer.
          </EmptyState>
        ) : (
          items.length > 0 && (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">
                      <span className="sr-only">Select</span>
                    </th>
                    <th scope="col">Type</th>
                    <th scope="col">Description</th>
                    <th scope="col">Qty</th>
                    <th scope="col">UOM</th>
                    <th scope="col">Discipline</th>
                    <th scope="col">Est. value (AED)</th>
                    <th scope="col">On site</th>
                    <th scope="col">Long lead</th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Select ${item.item_type}`}
                          checked={selected.has(item.id)}
                          onChange={(e) =>
                            setSelected((prev) => {
                              const next = new Set(prev)
                              if (e.target.checked) next.add(item.id)
                              else next.delete(item.id)
                              return next
                            })
                          }
                        />
                      </td>
                      <td>
                        <button
                          type="button"
                          className="linkish"
                          onClick={() => onOpenItem(item.id)}
                        >
                          {item.item_type}
                        </button>
                        {/* The refusal lives in the row that refused it. */}
                        {rowError[item.id] && (
                          <div className="banner banner--error" role="alert">
                            {rowError[item.id]}
                          </div>
                        )}
                      </td>
                      <td>{item.description}</td>
                      <td className="mono">{item.qty}</td>
                      <td>{item.uom}</td>
                      <td>{item.discipline}</td>
                      <td className="mono">
                        {item.estimated_value_aed.toLocaleString('en-AE')}
                      </td>
                      <td className="mono">{item.required_on_site ?? '—'}</td>
                      <td>{item.is_long_lead ? 'Yes' : '—'}</td>
                      <td>
                        {confirmingItem === item.id ? (
                          <>
                            <button
                              type="button"
                              className="btn btn-sm btn-danger"
                              aria-label={`Confirm delete of ${item.item_type}`}
                              onClick={() => removeItem(item.id)}
                            >
                              Delete
                            </button>{' '}
                            <button
                              type="button"
                              className="btn btn-sm btn-ghost"
                              onClick={() => setConfirmingItem(null)}
                            >
                              Cancel
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            className="btn btn-sm btn-danger"
                            aria-label={`Delete ${item.item_type}`}
                            onClick={() => setConfirmingItem(item.id)}
                          >
                            Delete
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}
      </Card>

      <Card title="RFQs">
        {rfqs.length === 0 ? (
          <EmptyState title="No RFQs yet">
            An RFQ is raised against one or more of this project's items.
          </EmptyState>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Reference</th>
                  <th scope="col">Package</th>
                  <th scope="col">Discipline</th>
                  <th scope="col">Items</th>
                  <th scope="col">Stage</th>
                </tr>
              </thead>
              <tbody>
                {rfqs.map((rfq) => (
                  <tr key={rfq.id}>
                    <td className="mono">{rfq.reference}</td>
                    <td>{rfq.package}</td>
                    <td>{rfq.discipline}</td>
                    <td className="mono">{rfq.item_ids.length}</td>
                    <td>{rfq.stage}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}
