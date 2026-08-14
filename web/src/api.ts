import type {
  Addendum,
  AdminUser,
  BidderSummary,
  Candidate,
  ClarificationQuery,
  ComplianceMatrix,
  AvailableBidders,
  Discipline,
  ExtractionStatus,
  ProjectDetail,
  ProjectSetup,
  ProjectSummary,
  Attachment,
  Rfq,
  RfqDetail,
  RfqRoster,
  ShortlistEntry,
  TbeTemplate,
  TechnicalPackage,
  VdrlLine,
  Statement,
  RfqInput,
  WorkflowItemInput,
  WorkflowItemSaved,
  WorkflowProject,
  WorkflowProjectDetail,
  WorkflowProjectInput,
  WorkflowProjectSummary,
  ItemVendorEntry,
  ItemVendorInput,
  SuggestedVendor,
  VendorListSource,
  VendorListSummary,
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
  method: 'POST' | 'PUT' | 'PATCH',
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

/* ------------------------------------------------------------- bidders */
//
// The registry is organisation-wide, so this path does not hang off a project.
// `effective_prequal` and `invited_count` arrive computed; nothing here
// recomputes them.
//
// Reading the registry is all the front end does with it now that the
// standalone Bidders screen is gone — the single-bidder read and the
// create/update/delete calls went with it. The endpoints they spoke to are
// still served by `api/workflow_routes.py`; nothing in the browser calls them.

export function fetchBidders(): Promise<BidderSummary[]> {
  return getJson<{ bidders: BidderSummary[] }>('/api/workflow/bidders').then(
    (body) => body.bidders,
  )
}

/** The disciplines an item may be scoped to, with the export's product groups
 *  behind each. From the server, so adding a family is an edit to
 *  `workflow/disciplines.py` alone. */
export function fetchDisciplines(): Promise<Discipline[]> {
  return getJson<{ disciplines: Discipline[] }>('/api/workflow/disciplines').then(
    (body) => body.disciplines,
  )
}

/** The vendors that may actually be invited: on the client's Approved Vendor
 *  List *and* on ours. `discipline` narrows to one family; omit it for the whole
 *  list. Filtered and counted by the server — testing `approved_by` here would
 *  be a second definition of what "available" means. */
export function fetchAvailableBidders(
  discipline?: string,
  /** Which approvals a vendor must carry — AND, never OR. Omitted entirely
   *  means the server's own default, so the definition of "available" stays in
   *  one place rather than being something this module has to know. */
  approvers?: string[],
): Promise<AvailableBidders> {
  const q = new URLSearchParams()
  if (discipline) q.set('discipline', discipline)
  // Repeated rather than comma-joined: an approver's name could contain a
  // comma, and FastAPI reads repeats straight into a list.
  for (const a of approvers ?? []) q.append('approver', a)
  const s = q.toString()
  return getJson(`/api/workflow/bidders/available${s ? `?${s}` : ''}`)
}

export function fetchCandidates(rfqId: string): Promise<Candidate[]> {
  return getJson<{ candidates: Candidate[] }>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/candidates`,
  ).then((body) => body.candidates)
}

/* ------------------------------------------- workflow projects and items */
//
// The hierarchy the RFQ process hangs off: a project, its equipment items, and
// the RFQs raised against them. A different store from the ingestion projects
// above, reached by id rather than by slug — hence the `Workflow` prefix on
// every name here.

export function fetchWorkflowProjects(): Promise<WorkflowProjectSummary[]> {
  return getJson<{ projects: WorkflowProjectSummary[] }>(
    '/api/workflow/projects',
  ).then((body) => body.projects)
}

export function fetchWorkflowProject(
  projectId: string,
): Promise<WorkflowProjectDetail> {
  return getJson(`/api/workflow/projects/${encodeURIComponent(projectId)}`)
}

export function createWorkflowProject(
  body: WorkflowProjectInput,
): Promise<WorkflowProject> {
  return sendJson('/api/workflow/projects', 'POST', body)
}

/** Partial by contract: send only what changed. The server reads the body with
 *  `exclude_unset`, so a field absent here is left alone — spreading a whole
 *  object in would turn every edit into a full overwrite. */
export function updateWorkflowProject(
  projectId: string,
  changes: Partial<WorkflowProjectInput>,
): Promise<WorkflowProject> {
  return sendJson(
    `/api/workflow/projects/${encodeURIComponent(projectId)}`,
    'PATCH',
    changes,
  )
}

export function deleteWorkflowProject(projectId: string): Promise<void> {
  return fetch(`/api/workflow/projects/${encodeURIComponent(projectId)}`, {
    method: 'DELETE',
  }).then(expectNoContent)
}

export function createWorkflowItem(
  projectId: string,
  body: WorkflowItemInput,
): Promise<WorkflowItemSaved> {
  return sendJson(
    `/api/workflow/projects/${encodeURIComponent(projectId)}/items`,
    'POST',
    body,
  )
}

export function updateWorkflowItem(
  projectId: string,
  itemId: string,
  changes: Partial<WorkflowItemInput>,
): Promise<WorkflowItemSaved> {
  return sendJson(
    `/api/workflow/projects/${encodeURIComponent(projectId)}/items/${encodeURIComponent(itemId)}`,
    'PATCH',
    changes,
  )
}

/** A 409 here carries the server's own sentence naming the RFQ that blocks the
 *  delete, and `unwrap` raises it verbatim for the screen to show. */
export function deleteWorkflowItem(
  projectId: string,
  itemId: string,
): Promise<void> {
  return fetch(
    `/api/workflow/projects/${encodeURIComponent(projectId)}/items/${encodeURIComponent(itemId)}`,
    { method: 'DELETE' },
  ).then(expectNoContent)
}

export function createRfq(body: RfqInput): Promise<Rfq> {
  return sendJson('/api/workflow/rfqs', 'POST', body)
}

export function extractRfqDoc(file: File): Promise<Partial<RfqInput>> {
  return sendFile('/api/workflow/rfqs/extract', file)
}

/** Store an Approved Vendor List export as this item's list, for one source.
 *
 *  Unlike `extractRfqDoc`, this **stores**: an enquiry document fills a form
 *  the reader then submits, while a vendor list is itself the record. */
export function uploadItemVendorList(
  projectId: string,
  itemId: string,
  source: VendorListSource,
  file: File,
): Promise<{ entries: ItemVendorEntry[]; summary: VendorListSummary }> {
  return sendFile(
    `/api/workflow/projects/${encodeURIComponent(projectId)}/items/` +
      `${encodeURIComponent(itemId)}/vendor-list?source=${encodeURIComponent(source)}`,
    file,
  )
}

function itemVendorsPath(projectId: string, itemId: string): string {
  return (
    `/api/workflow/projects/${encodeURIComponent(projectId)}/items/` +
    `${encodeURIComponent(itemId)}/vendors`
  )
}

/** Put one vendor on this item's list, by hand or off the suggestion card.
 *
 *  One call per vendor, deliberately. There is no bulk accept: taking twenty
 *  unverified companies in a single click is precisely the act that needs
 *  friction, and every row lands attributed to whoever added it. */
export function addItemVendor(
  projectId: string,
  itemId: string,
  body: ItemVendorInput,
): Promise<ItemVendorEntry> {
  return sendJson(itemVendorsPath(projectId, itemId), 'POST', body)
}

/** Take one curated vendor off, **by id**. The server refuses an uploaded row:
 *  it is part of a document, so correcting it means re-uploading the corrected
 *  export. The screen does not offer the control there either. */
export function removeItemVendor(
  projectId: string,
  itemId: string,
  entryId: string,
): Promise<void> {
  return fetch(
    `${itemVendorsPath(projectId, itemId)}/${encodeURIComponent(entryId)}`,
    { method: 'DELETE' },
  ).then(expectNoContent)
}

/** Companies a model believes supply this item. **Stores nothing** — this is
 *  the same shape as `extractRfqDoc`: the answer goes to the screen, and only
 *  an explicit `addItemVendor` records one. */
export function suggestItemVendors(
  projectId: string,
  itemId: string,
): Promise<{ vendors: SuggestedVendor[] }> {
  return sendJson(
    `/api/workflow/projects/${encodeURIComponent(projectId)}/items/` +
      `${encodeURIComponent(itemId)}/vendor-suggestions`,
    'POST',
    {},
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

/* The wizard's writes. Each maps to one control in one step; the server owns
   every rule, so these deliberately carry no validation of their own. */

export function setTechnicalPackage(
  rfqId: string,
  body: { revision: string; basis_of_design: string; attachments: Attachment[] },
): Promise<TechnicalPackage> {
  return sendJson<TechnicalPackage>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/technical-package`,
    'PUT',
    body,
  )
}

export function freezeTechnicalPackage(rfqId: string): Promise<TechnicalPackage> {
  return sendJson<TechnicalPackage>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/technical-package/freeze`,
    'POST',
    {},
  )
}

/** Two shapes, one route. With a `vendor_id` the server takes the vendor's
 *  name, prequal status and scope fit from the registry and ignores anything
 *  sent alongside — so this deliberately does not send them, rather than
 *  sending values it has no business deciding. */
export function inviteRegisteredBidder(
  rfqId: string,
  body: { vendor_id: string; included?: boolean; override_reason?: string | null },
): Promise<ShortlistEntry> {
  return sendJson<ShortlistEntry>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/shortlist`,
    'POST',
    body,
  )
}

export function addShortlistEntry(
  rfqId: string,
  body: {
    vendor_name: string
    prequal_status: string
    scope_code_fit: boolean
    included: boolean
    override_reason?: string | null
  },
): Promise<ShortlistEntry> {
  return sendJson<ShortlistEntry>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/shortlist`,
    'POST',
    body,
  )
}

export function removeShortlistEntry(rfqId: string, entryId: string): Promise<void> {
  return fetch(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/shortlist/${encodeURIComponent(entryId)}`,
    { method: 'DELETE' },
  ).then(expectNoContent)
}

export function approveShortlist(rfqId: string): Promise<{ approved_by: string }> {
  return sendJson<{ approved_by: string }>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/shortlist/approve`,
    'POST',
    {},
  )
}

export function setTbeTemplate(
  rfqId: string,
  body: { criteria: string[]; source_rfq_reference?: string | null },
): Promise<TbeTemplate> {
  return sendJson<TbeTemplate>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/tbe-template`,
    'PUT',
    body,
  )
}

export function addVdrlLine(
  rfqId: string,
  body: { doc_code: string; title: string; doc_type: string; mandatory: boolean },
): Promise<VdrlLine> {
  return sendJson<VdrlLine>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/vdrl`,
    'POST',
    body,
  )
}

export function removeVdrlLine(rfqId: string, lineId: string): Promise<void> {
  return fetch(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/vdrl/${encodeURIComponent(lineId)}`,
    { method: 'DELETE' },
  ).then(expectNoContent)
}

/** Tick a step off: advance the RFQ. A closed gate answers 409 and `unwrap`
 *  raises the gate's own sentence, which the wizard shows verbatim. */
export function transitionRfq(
  rfqId: string,
  target: string,
  reason?: string,
): Promise<Rfq> {
  return sendJson<Rfq>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/transition`,
    'POST',
    { target, reason: reason ?? null },
  )
}

/* The Clarifications step's writes. The server owns every rule, so these carry
   no validation of their own - including the circulation rule: `answerQuery`
   omits `restricted_reason` entirely rather than sending an empty string, so a
   circulated answer is never recorded as restricted with no text. */

export function raiseQuery(
  rfqId: string,
  body: {
    raised_by_entry_id: string
    category: "Technical" | "Commercial"
    question: string
    raised_on: string
  },
): Promise<ClarificationQuery> {
  return sendJson<ClarificationQuery>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/queries`,
    "POST",
    body,
  )
}

export function answerQuery(
  rfqId: string,
  queryId: string,
  body: { answer: string; restricted_reason?: string },
): Promise<ClarificationQuery> {
  return sendJson<ClarificationQuery>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/queries/${encodeURIComponent(queryId)}/answer`,
    "POST",
    body,
  )
}

export function withdrawQuery(
  rfqId: string,
  queryId: string,
  reason: string,
): Promise<ClarificationQuery> {
  return sendJson<ClarificationQuery>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/queries/${encodeURIComponent(queryId)}/withdraw`,
    "POST",
    { reason },
  )
}

export function draftAddendum(
  rfqId: string,
  body: {
    revision: string
    summary: string
    attachments: Attachment[]
    arising_from_query_ids?: string[]
    bid_due_date?: string | null
  },
): Promise<Addendum> {
  return sendJson<Addendum>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/addenda`,
    "POST",
    body,
  )
}

export function updateAddendum(
  rfqId: string,
  addendumId: string,
  body: Partial<{
    revision: string
    summary: string
    attachments: Attachment[]
    arising_from_query_ids: string[]
    bid_due_date: string | null
  }>,
): Promise<Addendum> {
  return sendJson<Addendum>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/addenda/${encodeURIComponent(addendumId)}`,
    "PATCH",
    body,
  )
}

/** The one sanctioned door through the frozen-package rule. Who issued it comes
 *  from the session, so the body is deliberately empty. */
export function issueAddendum(rfqId: string, addendumId: string): Promise<Addendum> {
  return sendJson<Addendum>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/addenda/${encodeURIComponent(addendumId)}/issue`,
    "POST",
    {},
  )
}

export function deleteAddendum(rfqId: string, addendumId: string): Promise<void> {
  return fetch(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/addenda/${encodeURIComponent(addendumId)}`,
    { method: "DELETE" },
  ).then(expectNoContent)
}
