import type { MatrixRow, Verdict } from './types'

export const VERDICTS: Verdict[] = [
  'pass',
  'fail',
  'deviation',
  'unanswered',
  'review',
]

export const GROUP_LABELS: Record<string, string> = {
  not_matched: 'Not matched',
  needs_human: 'Needs a human',
  matched: 'Matched',
}

export function bound(row: MatrixRow): string {
  if (row.checkability !== 'auto') return row.text
  const value = Array.isArray(row.value)
    ? row.value.map(String).join('..')
    : row.value
  return `${row.parameter ?? ''} ${row.operator ?? ''} ${value ?? ''} ${row.unit ?? ''}`.trim()
}

export function rowMatchesQuery(row: MatrixRow, query: string): boolean {
  if (!query.trim()) return true
  const q = query.trim().toLowerCase()
  const hay = [
    row.clause_ref,
    row.text,
    bound(row),
    row.parameter ?? '',
    ...Object.values(row.cells).flatMap((c) => [c.verdict, c.rationale]),
  ]
    .join(' ')
    .toLowerCase()
  return hay.includes(q)
}

export function rowHasVerdict(
  row: MatrixRow,
  verdicts: Set<string>,
  vendorFilter: string | null,
): boolean {
  if (verdicts.size === 0) return true
  const cells = vendorFilter
    ? [row.cells[vendorFilter]].filter(Boolean)
    : Object.values(row.cells)
  return cells.some((c) => verdicts.has(c.verdict))
}

export function pct(part: number, whole: number): string {
  if (!whole) return '0%'
  return `${Math.round((part / whole) * 100)}%`
}
