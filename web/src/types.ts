export type Verdict = 'pass' | 'fail' | 'deviation' | 'unanswered' | 'review'
export type GroupKey = 'not_matched' | 'needs_human' | 'matched'

export interface ProjectSummary {
  slug: string
  name: string
  vendors: string[]
  target_currency: string
  status: string
  generation: number
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
