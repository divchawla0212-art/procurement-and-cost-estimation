import type {
  AdminUser,
  ComplianceMatrix,
  ExtractionStatus,
  ProjectDetail,
  ProjectSetup,
  ProjectSummary,
  RfqDetail,
  RfqRoster,
  Statement,
} from './types'

/** The server's `detail` if it sent one, else the status line. */
async function failure(res: Response): Promise<Error> {
  let detail = `${res.status} ${res.statusText}`
  try {
    const body = await res.json()
    if (body?.detail) detail = body.detail
  } catch {
    /* non-JSON error body; keep status text */
  }
  return new Error(detail)
}

async function unwrap<T>(res: Response): Promise<T> {
  if (!res.ok) throw await failure(res)
  return res.json() as Promise<T>
}

/**
 * For routes that answer 204 with no body — parsing one as JSON would throw
 * on success, which is the opposite of what an error path should do.
 */
async function expectNoContent(res: Response): Promise<void> {
  if (!res.ok) throw await failure(res)
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

export function fetchExtractionStatus(slug: string): Promise<ExtractionStatus> {
  return getJson(`/api/projects/${encodeURIComponent(slug)}/extraction-status`)
}

// --- downloads -------------------------------------------------------------
// URLs rather than fetchers: these are handed to an <a download>, so the
// browser performs the request and the server's Content-Disposition names the
// file. Nothing buffers the workbook in JS. Append `?format=xlsx|csv`.

export function statementExportUrl(slug: string): string {
  return `/api/projects/${encodeURIComponent(slug)}/statement/export`
}

export function complianceMatrixExportUrl(slug: string): string {
  return `/api/projects/${encodeURIComponent(slug)}/compliance-matrix/export`
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

// --- admin: users and project access -------------------------------------
// Every one of these is refused for a reviewer by `require_admin` on the
// server. The admin-only nav entry that leads here is convenience, not a
// control — these calls would 403 just the same if a reviewer reached them.

export function fetchUsers(): Promise<AdminUser[]> {
  return getJson('/api/admin/users')
}

export function createUser(
  email: string,
  password: string,
  role: 'admin' | 'reviewer',
): Promise<AdminUser> {
  return sendJson('/api/admin/users', 'POST', { email, password, role })
}

export function deleteUser(userId: string): Promise<void> {
  return fetch(`/api/admin/users/${encodeURIComponent(userId)}`, {
    method: 'DELETE',
  }).then(expectNoContent)
}

export function grantProject(userId: string, slug: string): Promise<void> {
  return sendJson<unknown>(
    `/api/admin/users/${encodeURIComponent(userId)}/grants`,
    'POST',
    { slug },
  ).then(() => undefined)
}

export function revokeProject(userId: string, slug: string): Promise<void> {
  return fetch(
    `/api/admin/users/${encodeURIComponent(userId)}/grants/${encodeURIComponent(slug)}`,
    { method: 'DELETE' },
  ).then(expectNoContent)
}

export function runIngestion(
  slug: string,
  provider?: string,
  force?: boolean,
): Promise<ProjectSetup> {
  const body: { provider?: string; force?: boolean } = {}
  if (provider) body.provider = provider
  // Omitted rather than sent as `false`: the payload field defaults to false
  // server-side (design spec §1.2, guard 1), so a plain ingestion run makes
  // the same request it always has.
  if (force) body.force = true
  return sendJson<ProjectSetup>(
    `/api/projects/${encodeURIComponent(slug)}/ingest`,
    'POST',
    body,
  )
}

/* ------------------------------------------------------------------ workflow */

export function fetchRfqRoster(projectId?: string | null): Promise<RfqRoster> {
  const q = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
  return getJson<RfqRoster>(`/api/workflow/rfqs${q}`)
}


export function fetchRfq(rfqId: string): Promise<RfqDetail> {
  return getJson<RfqDetail>(`/api/workflow/rfqs/${encodeURIComponent(rfqId)}`)
}
