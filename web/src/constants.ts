import type { MatrixRow, Verdict } from './types'

export const VERDICTS: Verdict[] = ['pass', 'deviation', 'fail', 'review', 'unanswered']

export const VERDICT_LABEL: Record<string, string> = {
  pass: 'Pass',
  fail: 'Fail',
  deviation: 'Deviation',
  unanswered: 'Unanswered',
  review: 'Review',
}

export const GROUP_LABEL: Record<string, string> = {
  not_matched: 'Not matched',
  needs_human: 'Needs a human',
  matched: 'Matched',
}

/** The requirement's machine-checkable bound, or its clause text. */
export function bound(row: MatrixRow): string {
  if (row.checkability !== 'auto') return row.text
  const value = Array.isArray(row.value)
    ? row.value.map(String).join('..')
    : row.value
  return `${row.parameter ?? ''} ${row.operator ?? ''} ${value ?? ''} ${row.unit ?? ''}`
    .replace(/\s+/g, ' ')
    .trim()
}

export function pct(part: number, whole: number): string {
  if (!whole) return '0%'
  return `${Math.round((part / whole) * 100)}%`
}

export function formatMoney(value: number | null, currency: string): string {
  if (value == null) return '—'
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency || 'USD',
      maximumFractionDigits: 2,
    }).format(value)
  } catch {
    return `${value.toLocaleString()} ${currency}`.trim()
  }
}
