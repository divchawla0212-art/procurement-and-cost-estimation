export type Verdict = 'pass' | 'fail' | 'deviation' | 'unanswered' | 'review'
export type GroupKey = 'not_matched' | 'needs_human' | 'matched'

export interface ProjectSummary {
  slug: string
  name: string
  vendors: string[]
  target_currency: string
  status: string
  generation: number
  // Whether the store holds an extraction to read, independent of `status`
  // (I2, final-review report): `status` can be `"failed"` while the store
  // still holds a complete prior extraction — a second run that failed
  // outright does not blank a first run that succeeded (CLAUDE.md: "a
  // failed extraction never blanks previously-good stored data"). This is
  // the field `nav.ts`'s `reviewReachable` consults; `status` drives only
  // the partial/failed-run banner on the review screens.
  has_results: boolean
}

export interface MatrixCell {
  vendor: string
  verdict: Verdict | string
  group: GroupKey | string
  rationale: string
  fact_id: string | null
  doc_id: string | null
  candidate_fact_ids: string[]
  /** The file(s) behind the verdict. `doc_id` is a hash; these are filenames. */
  doc_name: string | null
  candidate_doc_names: string[]
}

export interface MatrixRow {
  req_id: string
  clause_ref: string
  text: string
  checkability: string
  parameter: string | null
  operator: string | null
  value: string | number | (string | number)[] | null
  unit: string | null
  cells: Record<string, MatrixCell>
}

export interface Coverage {
  auto_cells: number
  stated_cells: number
  by_verdict: Record<string, number>
  unanswered_silent: number
  unanswered_refused: number
}

export interface ComplianceMatrix {
  vendors: string[]
  rows: MatrixRow[]
  coverage: Coverage
  groups: Record<GroupKey, MatrixRow[]>
}

export interface ProjectDetail {
  slug: string
  name: string
  vendors: string[]
  target_currency: string
  generation: number
  requirement_count: number
  coverage: Coverage
  group_counts: Record<GroupKey, number>
  extraction: VendorExtractionRollup[]
}

// Mirrors procurement/coverage.py's DocumentStatus exactly. A document can be
// read by two extractors: `secondary_route`/`secondary_status`/`secondary_notes`
// carry the outcome of a *second* pass (Task 7b: an insufficiently-documented
// vendor's quotation is also fed to the technical extractor). `status` alone
// cannot say "primary ok, secondary failed" at once, so these are separate
// fields rather than folded into `status`/`notes`.
export interface DocumentStatus {
  doc_id: string
  filename: string
  path: string
  doc_class: string
  route: string | null
  status: 'ok' | 'failed' | 'skipped' | 'pending'
  notes: string | null
  text_source: string | null
  fact_count: number
  is_quotation: boolean
  superseded_by: string | null
  secondary_route: string | null
  secondary_status: 'ok' | 'failed' | null
  secondary_notes: string | null
}

// Mirrors procurement/coverage.py's VendorExtraction exactly.
export interface VendorExtraction {
  vendor: string
  documents: DocumentStatus[]
  extracted: number
  failed: number
  skipped: number
  fact_count: number
  has_commercial: boolean
  unanswered: number
  // Count of documents whose *second* extraction pass failed. Not evidence
  // the first pass succeeded: coverage.py increments this unconditionally on
  // the primary status, because run_secondary does not depend on it — a
  // document can fail both passes, and often will, since both hit the same
  // provider on the same text.
  secondary_failed: number
  // Stored technical facts whose doc_id matches no document of this vendor —
  // a breach of "a stored collection contains exactly the records of its
  // currently-live sources". `fact_count` is read off the stored collection,
  // so these are inside it and in no document row; the difference is reported
  // here rather than vanishing out of the vendor total.
  unattributed_facts: number
}

// Mirrors the `totals` dict procurement/coverage.py:163-172 actually emits.
// Kept as a named interface (not Record<string, number>) so a renamed or
// dropped key becomes a build error here rather than silently rendering as
// a measured-looking zero.
export interface ExtractionTotals {
  vendors: number
  documents: number
  extracted: number
  failed: number
  skipped: number
  facts: number
  unanswered: number
  secondary_failed: number
  unattributed_facts: number
}

export interface ExtractionStatus {
  vendors: VendorExtraction[]
  totals: ExtractionTotals
}

// the /summary roll-up: the same record minus the document list
export type VendorExtractionRollup = Omit<VendorExtraction, 'documents'>

export interface ProviderOption {
  id: string
  needs_key: string | null
  ready: boolean
}

export interface ProviderState {
  // null when no LLM_PROVIDER is set in the API environment (BUG-004): there
  // is no implicit default any more, so an unconfigured deployment must be
  // representable as "nothing is selected", not coerced into a string.
  provider: string | null
  needs_key: string | null
  ready: boolean
  catalog: ProviderOption[]
}

export interface VendorSetup {
  name: string
  file_count: number
  quote: string | null
}

export interface ProjectSetup {
  slug: string
  name: string
  target_currency: string
  requirements_file: string | null
  vendors: VendorSetup[]
  fx_rates: Record<string, number>
  status: string
  generation: number
  has_results: boolean
  provider: ProviderState
}

export interface StatementCell {
  qty: number | null
  unit_price: number | null
  total: number | null
  text: string | null
  note: string | null
}

export interface StatementRow {
  key: string
  label: string
  kind: 'priced' | 'value' | 'text'
  cells: Record<string, StatementCell>
}

export interface Statement {
  project: string
  currency: string
  generation: number
  vendors: string[]
  revisions: Record<string, string | null>
  currencies: Record<string, string | null>
  statuses: Record<string, 'ok' | 'failed' | 'missing' | string>
  rows: StatementRow[]
}

/**
 * A row of `GET /api/admin/users`. There is deliberately no `password_hash`
 * here and none in the server's `User` model either — the digest is reachable
 * only through `store.password_hash_for`, never through a response body.
 *
 * `grants` is empty for an admin and carries no meaning there: admins see
 * every project regardless of what the grants table says.
 */
export interface AdminUser {
  id: string
  email: string
  role: 'admin' | 'reviewer'
  created_at: string
  grants: string[]
}

/**
 * The nine workflow stages, as `/api/workflow/stages` returns them. The union
 * exists for call sites that switch on a stage; `StageStrip` deliberately
 * takes `string[]` instead, so the server stays the single source of the
 * stage vocabulary and adding a stage server-side does not need a matching
 * edit here before the strip renders it.
 */
export type RfqStage =
  | 'Scoping'
  | 'Shortlisting'
  | 'Issued'
  | 'Clarifications'
  | 'Bids Received'
  | 'Evaluation'
  | 'Negotiation'
  | 'Awarded'
  | 'PO Issued'

export interface StageStripProps {
  stages: string[]
  counts: Record<string, number>
  /** The stage one RFQ sits at. Omit on a roster view. */
  current?: string | null
}

/** One entry in an RFQ's stage history. Append-only: a backward transition
 *  adds an entry, it never rewrites an earlier one, so this is the audit
 *  trail of a retender as much as of the first pass. */
export interface StageTransition {
  from_stage: string | null
  to_stage: string
  at: string
  by: string
  reason: string | null
}

export interface Rfq {
  id: string
  reference: string
  project_id: string
  item_ids: string[]
  package: string
  discipline: string
  value_estimate_aed: number
  stage: string
  history: StageTransition[]
}

export interface RfqRoster {
  rfqs: Rfq[]
  stages: string[]
  stage_counts: Record<string, number>
}

export interface Attachment {
  doc_code: string
  title: string
  revision: string | null
}

/** The package a vendor bids against. Freezing it is what closes the Scoping
 *  gate, and it cannot be frozen while any attachment lacks a definite
 *  revision — "latest" is not something a vendor can quote. */
export interface TechnicalPackage {
  rfq_id: string
  revision: string
  basis_of_design: string
  attachments: Attachment[]
  frozen_at: string | null
  frozen_by: string | null
}

export interface ShortlistEntry {
  id: string
  rfq_id: string
  /** Set when the vendor came from the registry, null for a genuine one-off
   *  typed in by hand. The three fields below are a snapshot taken when the
   *  entry was added, not a live mirror of the registry — a later rename or
   *  suspension does not rewrite the record of what was decided. */
  vendor_id: string | null
  vendor_name: string
  prequal_status: string
  scope_code_fit: boolean
  included: boolean
  /** Whether the client has approved this vendor — derived by the server from
   *  the registry on every read, so correcting `approved_by` is the only edit
   *  needed. Unlike the three snapshot fields above it is deliberately *not*
   *  frozen at invitation: it answers who is approved now, not what was known
   *  then. `null` means unknown rather than unapproved — a vendor typed in by
   *  hand has no registry row to check, and so no finding either way. */
  client_approved: boolean | null
  /** Every organisation that has approved this vendor, live from the registry
   *  on each read — the identity behind `client_approved`'s verdict. `null` is
   *  the same third state that key uses: no registry row, so no finding either
   *  way. `[]` is a row that exists and carries no approval. Sent as well as
   *  the boolean because Astra approval is not a function of it. */
  approved_by: string[] | null
  override_by: string | null
  override_reason: string | null
}

export interface TbeTemplate {
  rfq_id: string
  criteria: string[]
  source_rfq_reference: string | null
}

export interface VdrlLine {
  id: string
  rfq_id: string
  doc_code: string
  title: string
  doc_type: string
  mandatory: boolean
}

/** A bid, with its VDRL tally computed server-side. `vdrl_received` counts
 *  only documents actually received: an unreadable file is a gap, not a
 *  delivery, and that rule lives on the server so no screen re-derives it. */
export interface BidWithVdrl {
  id: string
  rfq_id: string
  vendor_name: string
  received_at: string
  headline_price_aed: number
  currency: string
  vdrl_received: number
  vdrl_required: number
  vdrl_missing: string[]
  selected: boolean
}

export interface BidSelection {
  rfq_id: string
  selected_bid_ids: string[]
  selected_by: string
  rationale: string
  at: string
}

/** Whether the next forward stage is reachable, and if not, why not. A blocked
 *  gate always carries a `reason` — the server never sends a bare `false`. */
export interface Gate {
  passed: boolean
  reason: string | null
}

export interface RfqDetail {
  rfq: Rfq
  gate: Gate
  technical_package: TechnicalPackage | null
  shortlist: ShortlistEntry[]
  shortlist_approved: boolean
  /** Whose approval `client_approved` on each entry refers to. From the server
   *  so the shortlist table does not spell the client's name itself. */
  client_approver: string
  tbe_template: TbeTemplate | null
  vdrl: VdrlLine[]
  bids: BidWithVdrl[]
  bid_selection: BidSelection | null
  queries: ClarificationQuery[]
  addenda: Addendum[]
  /** The latest issued addendum's due date, or null. Derived server-side; an
   *  RFQ with no addendum has no due date here, and the screen says so rather
   *  than inventing one. */
  bid_due_date: string | null
}

/* ------------------------------------------------------- clarifications */

/** A bidder's question and what was answered.
 *
 *  `state` and `circulated` are computed server-side and sent alongside the
 *  record, never re-derived here: withdrawal beats an answer, and that rule
 *  belongs in one place.
 *
 *  `restricted_reason` null means the answer went to the whole included
 *  shortlist. There is deliberately no `circulate` boolean — one would make a
 *  restricted answer indistinguishable from an oversight. */
export interface ClarificationQuery {
  id: string
  rfq_id: string
  number: string
  /** The `ShortlistEntry` who asked. `raised_by_name` beside it is a snapshot
   *  of what they were called at the time, not a live mirror of the registry. */
  raised_by_entry_id: string
  raised_by_name: string
  raised_on: string
  category: 'Technical' | 'Commercial'
  question: string
  answer: string | null
  answered_by: string | null
  answered_at: string | null
  restricted_reason: string | null
  withdrawn_reason: string | null
  withdrawn_by: string | null
  withdrawn_at: string | null
  state: 'Open' | 'Answered' | 'Withdrawn'
  circulated: boolean
}

/** A numbered amendment to an issued RFQ.
 *
 *  `supersedes_revision` and `revision` together make the addenda list the
 *  package's revision trail. `draft` is computed from `issued_at`; an issued
 *  addendum can be neither edited nor deleted, because bidders hold it. */
export interface Addendum {
  id: string
  rfq_id: string
  number: string
  supersedes_revision: string
  revision: string
  summary: string
  attachments: Attachment[]
  arising_from_query_ids: string[]
  bid_due_date: string | null
  issued_at: string | null
  issued_by: string | null
  draft: boolean
}

/* ------------------------------------------------------------- bidders */

export type PrequalStatus =
  | 'Approved'
  | 'Under review'
  | 'Suspended'
  | 'Not qualified'

export interface Bidder {
  id: string
  name: string
  /** Null for bidders imported from an ADNOC AVL export: the only country in
   *  that sheet is the manufacturer's, and copying it across would record a
   *  UAE supplier as Indian because their principal is. */
  country: string | null
  currency: string
  /** The organisations that have approved this bidder — "ADNOC", "Astra", or
   *  both. A list, because the same company is commonly on several and which
   *  one matters depends on whose project the RFQ is for. */
  approved_by: string[]
  trade_categories: string[]
  prequal_status: PrequalStatus
  prequal_expires_on: string | null
  on_hold: boolean
  hold_reason: string | null
  turnover_band: string | null
  performance_rating: number | null
  past_awards: number
  /** The OEMs this vendor is listed as representing — often the real
   *  difference between two suppliers of the same product group. */
  represented_manufacturers: string[]
  notes: string | null
}

export type BidderInput = Omit<Bidder, 'id'>

/** A roster row. `effective_prequal` may read `Expired`, which no stored
 *  `prequal_status` ever does — expiry is derived server-side against today's
 *  date, and this screen must never recompute it, or there would be two
 *  definitions of "expired" to keep in step. */
export interface BidderSummary extends Bidder {
  effective_prequal: string
  /** The server's sentence when this bidder is not on the client's Approved
   *  Vendor List, or `null` when they are. Rendered verbatim — deriving it here
   *  from `approved_by` would be a second definition of the rule. */
  approval_caution: string | null
  invited_count: number
}

export interface BidderDetail extends BidderSummary {
  invited_by: string[]
}

/** A discipline an item can be scoped to, and the export's product group
 *  descriptions it stands for. */
export interface Discipline {
  name: string
  product_groups: string[]
}

/** The vendors that may be invited: approved by the client and by us. */
export interface AvailableBidders {
  /** The approvals a vendor must carry to be here — the client's and ours.
   *  From the server, so a second client's AVL does not mean editing the
   *  screens that render it. */
  approvers: string[]
  /** The product group the list was narrowed to, or null for the whole list. */
  discipline: string | null
  /** The size of the answer, which is not `bidders.length` once a screen caps
   *  what it draws. */
  total: number
  bidders: BidderSummary[]
}

/** Why a bidder may or may not be invited to one specific RFQ. A blocker is
 *  somebody's explicit refusal and needs an override reason to get past; a
 *  caution decides nothing and is shown so the decision is informed. */
export interface Suitability {
  eligible: boolean
  scope_fit: boolean
  effective_prequal: string
  blockers: string[]
  cautions: string[]
}

export interface Candidate {
  bidder: BidderSummary
  suitability: Suitability
  shortlisted: boolean
}

/* ------------------------------------------- workflow projects and items */
//
// The `Workflow` prefix is not decoration. `ProjectSummary` and `ProjectDetail`
// above belong to the *ingestion* project — a different entity, in a different
// store, reached by `slug` rather than by id. Two types called `ProjectDetail`
// in one file is precisely how the two identities get confused in code.

export interface WorkflowProject {
  id: string
  name: string
  code: string
  client: string
  location: string
  live_period_start: string
  live_period_end: string
  currency: string
  status: 'Active' | 'On Hold' | 'Closed'
}

/** What a create form supplies. `id` is server-generated; `status` defaults to
 *  Active and no route ever changes it after creation. */
export type WorkflowProjectInput = Omit<WorkflowProject, 'id' | 'status'> &
  Partial<Pick<WorkflowProject, 'status'>>

/** A roster row. The counts are computed server-side, so no screen re-derives
 *  them from a list it only partly holds. */
export interface WorkflowProjectSummary extends WorkflowProject {
  item_count: number
  rfq_count: number
}

export interface WorkflowItem {
  id: string
  project_id: string
  item_type: string
  description: string
  qty: number
  uom: string
  discipline: string
  estimated_value_aed: number
  required_on_site: string | null
  is_long_lead: boolean
}

export type WorkflowItemInput = Omit<WorkflowItem, 'id' | 'project_id'>

/** What create and patch return: the item, plus an advisory caution when its
 *  delivery date falls outside the project's live period. Advisory — the item
 *  is stored either way, because needing something after a live period closes
 *  is unusual rather than impossible. */
export interface WorkflowItemSaved extends WorkflowItem {
  live_period_warning: string | null
}

/** One project with everything the detail screen renders. One response rather
 *  than three, so the three lists cannot come from three generations of the
 *  document. */
export interface WorkflowProjectDetail {
  project: WorkflowProject
  items: WorkflowItem[]
  rfqs: Rfq[]
}

export interface RfqInput {
  project_id: string
  item_ids: string[]
  reference: string
  package: string
  discipline: string
  value_estimate_aed: number
}
