import { useState } from 'react'
import type { JSX } from 'react'
import { fetchWorkflowProject, updateWorkflowItem } from '../api'
import { useAsync } from '../useAsync'
import { ItemForm } from './forms'
import type { WorkflowItem, WorkflowItemInput } from '../types'
import {
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  Metric,
  PageHeader,
} from '../components/primitives'

/**
 * One item, and the RFQs covering it.
 *
 * Deliberately thin. It exists because "which RFQs cover this cable" has no
 * other answer in the product, and because a delete refused for exactly that
 * reason needs somewhere to send the reader.
 *
 * It reads the project's own response rather than adding a per-item route: the
 * covering RFQs have to be filtered out of the project's RFQs anyway, so a
 * dedicated route would have to return them too — and then the item and its
 * RFQs could arrive from two generations of the document.
 */

function toInput(item: WorkflowItem): WorkflowItemInput {
  const { id: _id, project_id: _projectId, ...rest } = item
  return rest
}

function changed(
  before: WorkflowItemInput,
  after: WorkflowItemInput,
): Partial<WorkflowItemInput> {
  const out: Partial<WorkflowItemInput> = {}
  for (const key of Object.keys(after) as (keyof WorkflowItemInput)[]) {
    if (after[key] !== before[key]) {
      out[key] = after[key] as never
    }
  }
  return out
}

export function ItemDetail({
  projectId,
  itemId,
  onBack,
}: {
  projectId: string
  itemId: string
  onBack: () => void
}): JSX.Element {
  const [tick, setTick] = useState(0)
  const [editing, setEditing] = useState(false)
  const [warning, setWarning] = useState<string | null>(null)
  const { data, error, loading } = useAsync(
    () => fetchWorkflowProject(projectId),
    [projectId, tick],
  )

  if (loading) return <LoadingState label="Loading item…" />
  if (error) return <ErrorState message={error} />
  if (!data) return <ErrorState message="No project data was returned." />

  const item = data.items.find((i) => i.id === itemId)
  if (!item) {
    // Not a crash: the id can be stale if the item was deleted in another tab,
    // or if the user came back to a page that had been open a while.
    return (
      <>
        <PageHeader
          eyebrow="01 · RFQ process"
          title="Item not found"
          actions={
            <button type="button" className="btn" onClick={onBack}>
              Back to project
            </button>
          }
        />
        <EmptyState title="That item could not be found">
          It may have been deleted since this page was opened.
        </EmptyState>
      </>
    )
  }

  // An RFQ may cover several items, so this is a filter over `item_ids`, not a
  // find — and it matches by id, never by name.
  const covering = data.rfqs.filter((r) => r.item_ids.includes(itemId))

  return (
    <>
      <PageHeader
        eyebrow="01 · RFQ process"
        title={item.item_type}
        sub={`${item.description} · ${data.project.name}`}
        actions={
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button type="button" className="btn" onClick={onBack}>
              Back to project
            </button>
            {!editing && (
              <button
                type="button"
                className="btn"
                onClick={() => setEditing(true)}
              >
                Edit item
              </button>
            )}
          </div>
        }
      />

      {warning && (
        <div className="banner banner--warn" role="alert">
          {warning}
        </div>
      )}

      {editing ? (
        <Card title="Edit item">
          <ItemForm
            initial={toInput(item)}
            submitLabel="Save item"
            onCancel={() => setEditing(false)}
            onSubmit={async (body) => {
              const saved = await updateWorkflowItem(
                projectId,
                itemId,
                changed(toInput(item), body),
              )
              setWarning(saved.live_period_warning)
              setEditing(false)
              setTick((t) => t + 1)
            }}
          />
        </Card>
      ) : (
        <Card title="Item detail">
          <div className="metrics">
            <Metric k="Quantity" v={`${item.qty} ${item.uom}`} />
            <Metric k="Discipline" v={item.discipline} />
            <Metric
              k="Estimated value"
              v={item.estimated_value_aed.toLocaleString('en-AE')}
              sub="AED"
            />
            <Metric
              k="Required on site"
              v={item.required_on_site ?? '—'}
              sub={item.is_long_lead ? 'long lead' : undefined}
            />
          </div>
        </Card>
      )}

      <Card title="RFQs covering this item">
        {covering.length === 0 ? (
          <EmptyState title="No RFQ covers this item yet">
            Select it on the project screen to raise one.
          </EmptyState>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Reference</th>
                <th scope="col">Package</th>
                <th scope="col">Discipline</th>
                <th scope="col">Stage</th>
              </tr>
            </thead>
            <tbody>
              {covering.map((r) => (
                <tr key={r.id}>
                  <td className="mono">{r.reference}</td>
                  <td>{r.package}</td>
                  <td>{r.discipline}</td>
                  <td>{r.stage}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  )
}
