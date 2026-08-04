// BUG-001 (BUGS_TRACKER.md): screens `02 Compliance matrix` and
// `03 Comparative statement` must be reachable only once a project's store
// holds an extraction to read — i.e. `status` is `done` or
// `done_with_failures`. Before the fix, the only gate was `!slug`, so a
// freshly created project with `status: 'new'` rendered both buttons
// enabled. This is the test that fails without the fix.
import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import App from './App'
import type { ProjectSummary } from './types'

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>()
  return {
    ...actual,
    fetchProjects: vi.fn(),
    // Dashboard's ProjectCard fetches a per-project summary; keep it pending
    // forever so it never resolves or rejects during these tests, which only
    // assert on the nav rail.
    fetchSummary: vi.fn(() => new Promise<never>(() => {})),
  }
})

import { fetchProjects } from './api'

const baseProject: ProjectSummary = {
  slug: 'acme-1',
  name: 'Acme project',
  vendors: ['Acme Co'],
  target_currency: 'USD',
  status: 'new',
  generation: 1,
}

async function renderWithStatus(status: string) {
  vi.mocked(fetchProjects).mockResolvedValue([{ ...baseProject, status }])
  render(<App />)
  // Wait for the project list to load and the app to auto-select it — both
  // happen asynchronously, so any nav assertion made before this settles
  // would be racing the fetch.
  await waitFor(() => {
    expect(
      screen.getByRole('option', { name: baseProject.name }),
    ).toBeInTheDocument()
  })
}

function navButton(name: RegExp) {
  return screen.getByRole('button', { name })
}

describe('review screens gate on project status (BUG-001)', () => {
  it('disables the matrix and statement for a project with no run yet, but leaves extraction status open', async () => {
    await renderWithStatus('new')

    await waitFor(() => {
      expect(navButton(/Compliance matrix/)).toBeDisabled()
      expect(navButton(/Comparative statement/)).toBeDisabled()
      expect(navButton(/Extraction status/)).toBeEnabled()
    })
  })

  it('disables the matrix and statement for a run that extracted nothing', async () => {
    await renderWithStatus('failed')

    await waitFor(() => {
      expect(navButton(/Compliance matrix/)).toBeDisabled()
      expect(navButton(/Comparative statement/)).toBeDisabled()
      expect(navButton(/Extraction status/)).toBeEnabled()
    })
  })

  it('enables all three screens for a clean run', async () => {
    await renderWithStatus('done')

    await waitFor(() => {
      expect(navButton(/Compliance matrix/)).toBeEnabled()
      expect(navButton(/Comparative statement/)).toBeEnabled()
      expect(navButton(/Extraction status/)).toBeEnabled()
    })
  })

  it('enables all three screens, with the matrix and statement reachable, for a run with some documents failed', async () => {
    await renderWithStatus('done_with_failures')

    await waitFor(() => {
      expect(navButton(/Compliance matrix/)).toBeEnabled()
      expect(navButton(/Comparative statement/)).toBeEnabled()
      expect(navButton(/Extraction status/)).toBeEnabled()
    })
  })
})
