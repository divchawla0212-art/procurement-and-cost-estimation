import { describe, expect, it } from 'vitest'
import { reviewReachable } from './nav'

describe('reviewReachable', () => {
  it('admits a project whose run finished clean', () => {
    expect(reviewReachable('done')).toBe(true)
  })

  it('admits a project whose run finished with some documents failed', () => {
    expect(reviewReachable('done_with_failures')).toBe(true)
  })

  it('rejects a project with no run yet', () => {
    expect(reviewReachable('new')).toBe(false)
  })

  it('rejects a project whose run extracted nothing', () => {
    expect(reviewReachable('failed')).toBe(false)
  })

  it('rejects null', () => {
    expect(reviewReachable(null)).toBe(false)
  })

  it('rejects undefined', () => {
    expect(reviewReachable(undefined)).toBe(false)
  })

  it('rejects the empty string', () => {
    expect(reviewReachable('')).toBe(false)
  })

  it('rejects an unrecognised status, defaulting to the honest screen', () => {
    expect(reviewReachable('archived')).toBe(false)
  })
})
