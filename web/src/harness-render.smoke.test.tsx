// Harness smoke test for Task 3 (front-end test runner setup).
//
// Proves the jsdom environment, `@testing-library/react`, and the jest-dom
// matchers (wired up in `src/test-setup.ts`) actually work end to end. It
// renders an inline element, not any real app component, so it has nothing
// for a later task to inherit — delete it once real component tests exist.
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'

describe('testing-library harness smoke test (placeholder)', () => {
  it('renders into jsdom and jest-dom matchers evaluate DOM state', () => {
    render(<button disabled>Click</button>)

    expect(screen.getByRole('button', { name: 'Click' })).toBeDisabled()
  })
})
