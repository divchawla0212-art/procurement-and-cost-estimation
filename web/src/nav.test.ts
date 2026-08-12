// I2 (final-review report): `reviewReachable` used to consult `status`
// (`'done'` / `'done_with_failures'` admitted, `'new'` / `'failed'` did not).
// That was wrong for `'failed'` — CLAUDE.md's store invariant means a
// project whose most recent run failed outright can still hold an earlier
// run's complete extraction. The predicate now consults `has_results`
// directly: reachable whenever the store holds results, whatever `status`
// says.
import { describe, expect, it } from 'vitest'
import { reviewReachable } from './nav'

describe('reviewReachable', () => {
  it('admits a project whose store holds results', () => {
    expect(reviewReachable(true)).toBe(true)
  })

  it('rejects a project whose store holds no results', () => {
    expect(reviewReachable(false)).toBe(false)
  })

  it('rejects null', () => {
    expect(reviewReachable(null)).toBe(false)
  })

  it('rejects undefined', () => {
    expect(reviewReachable(undefined)).toBe(false)
  })

  it('admits a run that failed outright but still holds an earlier run\'s results (I2)', () => {
    // `status: 'failed'` (extracted === 0 this run) with `has_results: true`
    // is exactly the case the original `status`-only predicate got wrong:
    // CLAUDE.md — "a failed extraction never blanks previously-good stored
    // data" — so the store can hold a complete prior extraction under a
    // `'failed'` status. The predicate must not know or care about `status`
    // at all; it is passed `has_results` directly.
    expect(reviewReachable(true)).toBe(true)
  })
})
