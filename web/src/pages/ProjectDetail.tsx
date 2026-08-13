import type { JSX } from 'react'
import { fetchWorkflowProject } from '../api'
import { useAsync } from '../useAsync'
import { ErrorState, LoadingState, PageHeader } from '../components/primitives'

export function ProjectDetail({
  projectId,
  onBack,
}: {
  projectId: string
  onOpenItem: (itemId: string) => void
  onBack: () => void
}): JSX.Element {
  const { data, error, loading } = useAsync(
    () => fetchWorkflowProject(projectId),
    [projectId],
  )

  if (loading) return <LoadingState label="Loading project…" />
  if (error) return <ErrorState message={error} />
  if (!data) return <ErrorState message="No project data was returned." />

  return (
    <>
      <PageHeader
        eyebrow="01 · RFQ process"
        title={data.project.name}
        actions={
          <button type="button" className="btn" onClick={onBack}>
            Back to projects
          </button>
        }
      />
    </>
  )
}
