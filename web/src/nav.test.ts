// Harness smoke test for Task 3 (front-end test runner setup).
//
// This is NOT a test of real nav logic — `nav.ts` does not exist yet.
// Its only job is to prove `npm test` actually runs Vitest, once, and exits
// non-zero on failure. Task 4 owns `src/nav.ts` and replaces this file's
// contents with real tests against it.
import { describe, expect, it } from 'vitest'

describe('harness smoke test (placeholder for Task 4)', () => {
  it('runs a trivial assertion under vitest', () => {
    expect(1 + 1).toBe(2)
  })
})
