import type {
  ComplianceMatrix,
  ProjectDetail,
  ProjectSetup,
  ProjectSummary,
  Statement,
} from './types'

async function unwrap<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      /* non-JSON error body; keep status text */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

function getJson<T>(path: string): Promise<T> {
  return fetch(path).then((res) => unwrap<T>(res))
}

function sendJson<T>(
  path: string,
  method: 'POST' | 'PUT',
  body: unknown,
): Promise<T> {
  return fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then((res) => unwrap<T>(res))
}

function sendFile<T>(path: string, file: File): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  return fetch(path, { method: 'POST', body: form }).then((res) => unwrap<T>(res))
}

export function fetchProjects(): Promise<ProjectSummary[]> {
  return getJson('/api/projects')
}

export function fetchSummary(slug: string): Promise<ProjectDetail> {
  return getJson(`/api/projects/${encodeURIComponent(slug)}/summary`)
}

export function fetchComplianceMatrix(slug: string): Promise<ComplianceMatrix> {
  return getJson(`/api/projects/${encodeURIComponent(slug)}/compliance-matrix`)
}

export function fetchStatement(slug: string): Promise<Statement> {
  return getJson(`/api/projects/${encodeURIComponent(slug)}/statement`)
}

// --- setup / ingestion (mutations) ---------------------------------------

export function fetchSetup(slug: string): Promise<ProjectSetup> {
  return getJson(`/api/projects/${encodeURIComponent(slug)}/setup`)
}

export function createProject(
  name: string,
  targetCurrency: string,
): Promise<ProjectSetup> {
  return sendJson('/api/projects', 'POST', {
    name,
    target_currency: targetCurrency,
  })
}

export function uploadRequirements(
  slug: string,
  file: File,
): Promise<ProjectSetup> {
  return sendFile(`/api/projects/${encodeURIComponent(slug)}/requirements`, file)
}

export function uploadVendors(slug: string, file: File): Promise<ProjectSetup> {
  return sendFile(`/api/projects/${encodeURIComponent(slug)}/vendors`, file)
}

export function saveFxRates(
  slug: string,
  rates: Record<string, number>,
): Promise<ProjectSetup> {
  return sendJson(`/api/projects/${encodeURIComponent(slug)}/fx-rates`, 'PUT', {
    rates,
  })
}

export function runIngestion(
  slug: string,
  provider?: string,
): Promise<ProjectSetup> {
  return sendJson<ProjectSetup>(
    `/api/projects/${encodeURIComponent(slug)}/ingest`,
    'POST',
    provider ? { provider } : {},
  )
}
