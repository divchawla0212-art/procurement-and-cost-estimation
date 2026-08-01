import type { ComplianceMatrix, ProjectSummary } from './types'

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) {
    const detail = await res.text()
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

export function fetchProjects(): Promise<ProjectSummary[]> {
  return getJson('/api/projects')
}

export function fetchComplianceMatrix(slug: string): Promise<ComplianceMatrix> {
  return getJson(`/api/projects/${encodeURIComponent(slug)}/compliance-matrix`)
}
