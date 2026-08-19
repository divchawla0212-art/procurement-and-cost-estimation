// I2 (final-review report): `reviewReachable` used to consult `status`
// (`'done'` / `'done_with_failures'` admitted, `'new'` / `'failed'` did not).
// That was wrong for `'failed'` — CLAUDE.md's store invariant means a
// project whose most recent run failed outright can still hold an earlier
// run's complete extraction. The predicate now consults `has_results`
// directly: reachable whenever the store holds results, whatever `status`
// says.
import { describe, expect, it } from 'vitest'
import { nextPage, reviewReachable } from './nav'

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

/** The end-of-page control's destination.
 *
 *  Every case here is a `NAV` position rather than a hand-written pair, which
 *  is the point: if the table is reordered these move with it, and a second
 *  list of destinations would not. */
describe('nextPage', () => {
  const opts = (over: Partial<Parameters<typeof nextPage>[1]> = {}) => ({
    slug: 'coverage-floor-t7',
    hasResults: true,
    role: 'admin' as const,
    ...over,
  })

  it('walks the rail in order', () => {
    expect(nextPage('/projects', opts())).toEqual({ to: '/rfqs', label: 'RFQ workflow' })
    expect(nextPage('/rfqs', opts())).toEqual({ to: '/bid-sets', label: 'Bid sets' })
  })

  it('follows a screen into its own detail pages', () => {
    // `01` matches `/projects/*`, so an item screen's rail position is 01 and
    // its next is 02 -- the same answer the rail's highlight gives.
    expect(nextPage('/projects/prj_1/items/itm_1', opts())?.to).toBe('/rfqs')
  })

  it('builds the next URL from the slug rather than naming it', () => {
    expect(nextPage('/bid-sets/x/extraction', opts({ slug: 'x' }))).toEqual({
      to: '/bid-sets/x/overview',
      label: 'Overview',
    })
  })

  // The whole reason this returns a nullable rather than a best guess: a large
  // primary that cannot be pressed is the `Send to 0 vendors` defect again.
  it('offers nothing when the next screen needs an extraction there is none of', () => {
    expect(nextPage('/bid-sets/x/extraction', opts({ slug: 'x', hasResults: false }))).toBeNull()
    expect(nextPage('/bid-sets/x/extraction', opts({ slug: 'x', hasResults: null }))).toBeNull()
  })

  it('offers nothing when the next screen needs a bid set and there is none', () => {
    // `04` resolves to `/bid-sets/new` with no slug, so `03` still has a next;
    // `05` onwards genuinely cannot be addressed, and `04` is where that shows.
    expect(nextPage('/bid-sets', opts({ slug: null }))?.to).toBe('/bid-sets/new')
    expect(nextPage('/bid-sets/new', opts({ slug: null }))).toBeNull()
  })

  it('stops at the end of the rail', () => {
    expect(nextPage('/admin', opts())).toBeNull()
  })

  // 09 is the only role-gated entry and it is also the last, so a reviewer's
  // chain ends one screen earlier than an admin's.
  it('ends at 08 for a reviewer and at 09 for an admin', () => {
    expect(nextPage('/bid-sets/x/statement', opts({ slug: 'x', role: 'reviewer' }))).toBeNull()
    expect(nextPage('/bid-sets/x/statement', opts({ slug: 'x' }))).toEqual({
      to: '/admin',
      label: 'Users and access',
    })
  })

  it('offers nothing for a path the rail does not know', () => {
    expect(nextPage('/nowhere', opts())).toBeNull()
  })
})
