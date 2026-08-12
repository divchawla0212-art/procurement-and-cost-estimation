// Vitest setup file (registered via `test.setupFiles` in vitest.config.ts).
//
// - `@testing-library/jest-dom/vitest` extends Vitest's `expect` with the
//   jest-dom DOM matchers (`toBeDisabled`, `toHaveTextContent`, ...) and
//   augments Vitest's `Assertion` type so they type-check too.
// - `cleanup()` unmounts anything rendered by `@testing-library/react` after
//   each test so tests don't leak DOM state into one another.
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

afterEach(() => {
  cleanup()
})
