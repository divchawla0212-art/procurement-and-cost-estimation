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
}

export interface ProviderOption {
  id: string
  needs_key: string | null
  ready: boolean
}

export interface ProviderState {
  provider: string
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
