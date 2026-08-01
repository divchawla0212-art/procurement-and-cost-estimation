import type { ProjectSummary } from '../types'

export interface DashboardProps {
  projects: ProjectSummary[] | null
  loading: boolean
  error: string | null
  onOpen: (slug: string) => void
}

// Implemented by the Dashboard page subagent.
export function Dashboard(_props: DashboardProps) {
  return <div className="state">Dashboard placeholder</div>
}
