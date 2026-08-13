import type { JSX } from 'react'
import { fetchWorkflowProject } from '../api'
import { useAsync } from '../useAsync'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from '../components/primitives'

export function ItemDetail({
  projectId,
  itemId,
  onBack,
}: {
  projectId: string
  itemId: string
  onBack: () => void
}): JSX.Element {
  const { data, error, loading } = useAsync(
    () => fetchWorkflowProject(projectId),
    [projectId],
  )

  if (loading) return <LoadingState label="Loading item…" />
  if (error) return <ErrorState message={error} />
  if (!data) return <ErrorState message="No project data was returned." />

  const item = data.items.find((i) => i.id === itemId)
  if (!item) {
    return (
      <EmptyState title="That item could not be found">
        It may have been deleted since this page was opened.
      </EmptyState>
    )
  }

  return (
    <>
      <PageHeader
        eyebrow="01 · RFQ process"
        title={item.item_type}
        actions={
          <button type="button" className="btn" onClick={onBack}>
            Back to project
          </button>
        }
      />
    </>
  )
}
