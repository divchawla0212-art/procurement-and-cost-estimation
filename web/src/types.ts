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
 * The eight workflow stages, as `/api/workflow/stages` returns them. The union
 * exists for call sites that switch on a stage; `StageStrip` deliberately
 * takes `string[]` instead, so the server stays the single source of the
 * stage vocabulary and adding a stage server-side does not need a matching
 * edit here before the strip renders it.
 */
export type RfqStage =
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
  /** Each stage's reference in the client's process document, keyed by stage
   *  name. Sent by the server rather than computed here — see `StageStrip`'s
   *  docstring for why a position cannot produce these. */
  codes: Record<string, string>
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

/** A vendor the buyer had picked onto an item's draft that the new RFQ could
 *  not invite, and the server's own sentence saying why.
 *
 *  Adoption is not all-or-nothing — one suspended vendor must not stop the
 *  other twenty-four being invited — so the ones it could not take are named
 *  rather than dropped. Same shape as the per-vendor refusals the item screen
 *  renders beside the row that caused them. */
export interface SkippedPick {
  vendor_name: string
  reason: string
}

/** What `POST /rfqs` answers: the RFQ, plus what its draft adoption did. */
export interface RfqRaised extends Rfq {
  shortlist_adopted: number
  shortlist_skipped: SkippedPick[]
}

export interface RfqRoster {
  rfqs: Rfq[]
  stages: string[]
  stage_counts: Record<string, number>
  /** Each stage's process-document reference, sent alongside the counts for
   *  the same reason `client_approver` rides on the shortlist: the browser
   *  must not spell the process's own vocabulary. */
  stage_codes: Record<string, string>
}

export interface Attachment {
  doc_code: string
  title: string
  revision: string | null
}

/** The package a vendor bids against. It cannot be frozen while any attachment
 *  lacks a definite revision — "latest" is not something a vendor can quote —
 *  and once frozen it refuses every later edit. It is edited under Issued;
 *  freezing used to be the exit criterion of a Scoping stage that no longer
 *  exists. */
export interface TechnicalPackage {
  rfq_id: string
  revision: string
  basis_of_design: string
  attachments: Attachment[]
  /** Ids into `RfqDetail.documents` — the files the contractor issued. Rebuilt
   *  server-side from the records, so it cannot disagree with them. */
  documents: string[]
  frozen_at: string | null
  frozen_by: string | null
}

/** One real file stored against the RFQ.
 *
 *  `filename` is the leaf and `rel_path` is where it sat inside the folder or
 *  archive it arrived in — `enquiry/drawings/sld.dwg` against a leaf of
 *  `sld.dwg`. A plain single-file upload has the same text in both.
 *
 *  `submitted_by_vendor_id` is `null` for a document the contractor issued and
 *  a vendor id for one a bidder returned; one collection serves both halves of
 *  the enquiry. */
export interface RfqDocument {
  id: string
  rfq_id: string
  filename: string
  rel_path: string
  sha256: string
  size_bytes: number
  content_type: string | null
  category: string | null
  uploaded_by: string
  uploaded_at: string
  submitted_by_vendor_id: string | null
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
  /** Every file stored against this RFQ, both halves of the enquiry. */
  documents: RfqDocument[]
  /** The eligibility vocabulary, from the server, so the upload card does not
   *  spell the categories itself. */
  document_categories: string[]
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
  /** The approvals actually **applied** to this answer — a vendor here carries
   *  every one of them, AND never OR. Follows what the caller asked for, so a
   *  narrowed list's caption names the narrowing rather than always claiming
   *  both. */
  approvers: string[]
  /** Every approval that may be asked for. From the server, so a screen builds
   *  its filter controls without spelling an approver's name and a second
   *  client's AVL does not mean editing the screens that render it. */
  selectable_approvers: string[]
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
/** Where an entry on an item's vendor list came from.
 *
 *  `Client` and `Astra` are the two uploads — the client's Approved Vendor List
 *  and Astra's subset of it. They arrive as whole documents and are corrected
 *  by re-uploading them, so the server refuses to add or remove one row of
 *  either. `Manual` is a company somebody typed in and `Suggested` is one a
 *  model named; both are built a vendor at a time and both can be removed.
 *
 *  A `Suggested` row stays labelled one even after a person accepts it: that
 *  the name originated with a model is the thing a later reader would most want
 *  to know. */
export type VendorListSource = 'Client' | 'Astra' | 'Manual' | 'Suggested'

/** The two sources built a vendor at a time. Used to decide which cards carry
 *  add and remove controls, so the screen matches the server's refusal rather
 *  than relying on it to explain itself after the click. */
export const CURATED_SOURCES: VendorListSource[] = ['Manual', 'Suggested']

/** One vendor on one item's list.
 *
 *  `vendor_id` is the export's own vendor number, and `null` means the export
 *  named a vendor the registry does not hold — a reportable state, and not the
 *  same as "unapproved". Approvals are deliberately absent: they are read from
 *  the registry through `vendor_id`, never copied here. */
export interface ItemVendorEntry {
  id: string
  item_id: string
  source: VendorListSource
  vendor_id: string | null
  vendor_name: string
  trade_categories: string[]
  uploaded_by: string
  uploaded_at: string
  source_document: string
}

/** One vendor a person is putting on an item's list.
 *
 *  There is no `vendor_id` here and the server never derives one: a name match
 *  would silently attach a real company's approvals to whatever somebody typed.
 *  `note` rides on the stored `source_document` rather than a field of its own,
 *  because a note here is *why this row exists*. */
export interface ItemVendorInput {
  vendor_name: string
  source: VendorListSource
  trade_categories?: string[]
  note?: string
}

/** One company a model named. Never a registry row and never a bidder — no
 *  approvals, no link, no prequalification, because it is precisely the
 *  companies the registry does not hold that are worth suggesting.
 *
 *  `basis` is the model's own reason, carried through unedited so the reader
 *  can judge it. It is evidence to weigh, not a verdict to act on. */
export interface SuggestedVendor {
  name: string
  country: string | null
  supplies: string | null
  basis: string | null
}

export interface VendorListSummary {
  /** Vendors in the uploaded export, before the discipline filter. */
  parsed: number
  /** How many survived it — the size of this item's list. */
  kept: number
  /** How many of those the registry holds. */
  linked: number
}

export interface WorkflowProjectDetail {
  project: WorkflowProject
  items: WorkflowItem[]
  rfqs: Rfq[]
  /** Keyed by item id, then by source. Sent with the project because the item
   *  screen already reads this payload, and a second fetch could disagree with
   *  the items rendered beside it. */
  item_vendor_lists: Record<string, Record<VendorListSource, ItemVendorEntry[]>>
  /** Keyed by item id. The vendors a buyer has picked against each item before
   *  any RFQ covers it. Flat per item rather than grouped by source — the buyer
   *  picked one basket, and `source` on each row says where each came from.
   *  Every item gets a key, so this is never `undefined` for a live item. */
  draft_shortlists: Record<string, DraftShortlistEntry[]>
}

/** The four chips on the item screen's vendor pool.
 *
 *  Deliberately not `VendorListSource`: that one's `Client` is the uploaded
 *  export's name for the client's own list, while this names the chip the buyer
 *  was looking at. The server refuses `Client` here with a 422. */
export type DraftShortlistSource = 'ADNOC' | 'Astra' | 'Manual' | 'Suggested'

/** One vendor picked against an item, before any RFQ covers it.
 *
 *  `vendor_id` is set for a registry row and `null` for a curated one, and the
 *  server never derives it from the name. There is no `approved_by` and no
 *  `prequal_status`: those are read live through `vendor_id`, so a copy here
 *  would be wrong the moment the registry is corrected. */
export interface DraftShortlistEntry {
  id: string
  item_id: string
  vendor_id: string | null
  vendor_name: string
  source: DraftShortlistSource
  added_by: string
  added_at: string
}

/** What the browser sends to pick a vendor. No `added_by` — attribution comes
 *  from the session, so a screen cannot name somebody else as the picker. */
export interface DraftShortlistInput {
  vendor_name: string
  source: DraftShortlistSource
  vendor_id?: string | null
}

export interface RfqInput {
  project_id: string
  item_ids: string[]
  reference: string
  package: string
  discipline: string
  value_estimate_aed: number
}
