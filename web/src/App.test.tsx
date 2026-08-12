// BUG-001 (BUGS_TRACKER.md): the screens that read a stored extraction —
// `02 Overview`, `03 Compliance matrix` and `04 Comparative statement` —
// must be reachable only once a project's store holds an extraction to
// read. I2 (final-review report) corrected the gate from `status` to
// `has_results` directly — `status` can be `'failed'` while the store still
// holds a complete prior extraction (CLAUDE.md: "a failed extraction never
// blanks previously-good stored data") — so these tests drive `has_results`
// explicitly rather than inferring it from `status`.
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from './App'
import type { ProjectSetup, ProjectSummary } from './types'

// App now gates every screen behind a confirmed session. These tests are
// about the nav rail's reachability rules, not authentication, so they run
// as an already-signed-in admin: `ready` true and a `user` present, which is
// the state the rail renders in. The admin role keeps the role-filtered
// entry visible so a nav regression there cannot hide behind a reviewer's
// shorter list.
//
// The value is built once, outside the factory, and must stay that way:
// App passes `user` in the dependency list of the `useAsync` that loads the
// project roster, so a fresh object per `useAuth()` call is a new dependency
// on every render — refetch, re-render, refetch, until the worker dies of
// heap exhaustion rather than failing an assertion.
const auth = vi.hoisted(() => ({
  value: {
    user: { id: 'u1', email: 'admin@example.com', role: 'admin' as const },
    ready: true,
    login: async () => ({ ok: true }),
    signup: async () => ({ ok: true }),
    logout: async () => {},
  },
}))

vi.mock('./auth/context', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./auth/context')>()
  return { ...actual, useAuth: () => auth.value }
})

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>()
  return {
    ...actual,
    fetchProjects: vi.fn(),
    // Dashboard's ProjectCard fetches a per-project summary; keep it pending
    // forever so it never resolves or rejects during these tests, which only
    // assert on the nav rail.
    fetchSummary: vi.fn(() => new Promise<never>(() => {})),
    // Pending forever by default too: the I1 and dead-button tests below
    // navigate into the matrix/setup screens and only need a stable,
    // recognisable loading state to prove *which* screen is mounted, not
    // real data.
    fetchComplianceMatrix: vi.fn(() => new Promise<never>(() => {})),
    fetchSetup: vi.fn(() => new Promise<never>(() => {})),
  }
})

import { fetchComplianceMatrix, fetchProjects, fetchSetup } from './api'

const baseProject: ProjectSummary = {
  slug: 'acme-1',
  name: 'Acme project',
  vendors: ['Acme Co'],
  target_currency: 'USD',
  status: 'new',
  generation: 1,
  has_results: false,
}

async function renderWithProject(status: string, hasResults: boolean) {
  vi.mocked(fetchProjects).mockResolvedValue([
    { ...baseProject, status, has_results: hasResults },
  ])
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

describe('review screens gate on has_results, not status (BUG-001, I2)', () => {
  it('disables the matrix and statement for a project with no run yet, but leaves extraction status open', async () => {
    await renderWithProject('new', false)

    await waitFor(() => {
      expect(navButton(/Compliance matrix/)).toBeDisabled()
      expect(navButton(/Comparative statement/)).toBeDisabled()
      expect(navButton(/Extraction status/)).toBeEnabled()
    })
  })

  it('disables the matrix and statement for a run that extracted nothing and left no prior results', async () => {
    await renderWithProject('failed', false)

    await waitFor(() => {
      expect(navButton(/Compliance matrix/)).toBeDisabled()
      expect(navButton(/Comparative statement/)).toBeDisabled()
      expect(navButton(/Extraction status/)).toBeEnabled()
    })
  })

  it('enables all three screens for a clean run', async () => {
    await renderWithProject('done', true)

    await waitFor(() => {
      expect(navButton(/Compliance matrix/)).toBeEnabled()
      expect(navButton(/Comparative statement/)).toBeEnabled()
      expect(navButton(/Extraction status/)).toBeEnabled()
    })
  })

  it('enables all three screens, with the matrix and statement reachable, for a run with some documents failed', async () => {
    await renderWithProject('done_with_failures', true)

    await waitFor(() => {
      expect(navButton(/Compliance matrix/)).toBeEnabled()
      expect(navButton(/Comparative statement/)).toBeEnabled()
      expect(navButton(/Extraction status/)).toBeEnabled()
    })
  })

  it('I2: keeps the matrix and statement reachable when the latest run failed outright but the store still holds an earlier run\'s results', async () => {
    // The exact scenario the original `status`-only predicate got wrong: a
    // project ingested cleanly, then a second run (expired key, provider
    // outage, a forced re-run) failed every document. `status` recomputes to
    // `'failed'`, but CLAUDE.md's invariant means the first run's extraction
    // is still sitting in the store, so `has_results` is `true`.
    await renderWithProject('failed', true)

    await waitFor(() => {
      expect(navButton(/Compliance matrix/)).toBeEnabled()
      expect(navButton(/Comparative statement/)).toBeEnabled()
      expect(navButton(/Extraction status/)).toBeEnabled()
    })
  })
})

describe('the rail switcher does not leave a review screen mounted for an unreachable project (I1)', () => {
  it('sends the view back to setup when switching to a project with no results', async () => {
    const doneProject: ProjectSummary = {
      ...baseProject,
      slug: 'done-1',
      name: 'Done project',
      status: 'done',
      has_results: true,
    }
    const newProject: ProjectSummary = {
      ...baseProject,
      slug: 'new-1',
      name: 'New project',
      status: 'new',
      has_results: false,
    }
    vi.mocked(fetchProjects).mockResolvedValue([doneProject, newProject])
    render(<App />)
    // Wait for the button to actually be *enabled*, not just for the option
    // to exist: the project list resolving and the auto-select effect that
    // sets `slug` (and so un-disables this button) are two separate render
    // passes. Clicking as soon as the option appears races that effect — if
    // `slug` is still null the button is disabled and the click is a no-op,
    // which is exactly the flake this caused.
    await waitFor(() => {
      expect(navButton(/Compliance matrix/)).toBeEnabled()
    })

    // Open the matrix for the reachable project.
    navButton(/Compliance matrix/).click()
    await waitFor(() => {
      expect(screen.getByText('Building compliance matrix…')).toBeInTheDocument()
    })

    // Switch the rail's project switcher to the unreachable one. Before the
    // fix, `onChange` only called `setSlug` — `view` stayed `'matrix'`, so
    // the compliance matrix stayed mounted and refetched for the
    // never-ingested project, the exact state BUG-001 reports.
    fireEvent.change(screen.getByLabelText('Active project'), {
      target: { value: newProject.slug },
    })

    await waitFor(() => {
      expect(
        screen.queryByText('Building compliance matrix…'),
      ).not.toBeInTheDocument()
      // Setup is where an unreachable project's view now lands.
      expect(screen.getByText('Loading project…')).toBeInTheDocument()
    })
  })

  it('leaves the view alone when switching to another reachable project', async () => {
    const doneProject: ProjectSummary = {
      ...baseProject,
      slug: 'done-1',
      name: 'Done project',
      status: 'done',
      has_results: true,
    }
    const otherDoneProject: ProjectSummary = {
      ...baseProject,
      slug: 'done-2',
      name: 'Other done project',
      status: 'done',
      has_results: true,
    }
    vi.mocked(fetchProjects).mockResolvedValue([doneProject, otherDoneProject])
    render(<App />)
    // See the comment in the previous test: wait for the button to be
    // enabled, not just for the option to exist, or the click can race the
    // auto-select effect that sets `slug`.
    await waitFor(() => {
      expect(navButton(/Compliance matrix/)).toBeEnabled()
    })

    navButton(/Compliance matrix/).click()
    await waitFor(() => {
      expect(screen.getByText('Building compliance matrix…')).toBeInTheDocument()
    })

    fireEvent.change(screen.getByLabelText('Active project'), {
      target: { value: otherDoneProject.slug },
    })

    await waitFor(() => {
      expect(vi.mocked(fetchComplianceMatrix)).toHaveBeenCalledWith(otherDoneProject.slug)
    })
    expect(screen.getByText('Building compliance matrix…')).toBeInTheDocument()
  })
})

describe('the dead "Review compliance matrix" button (I2 leftover)', () => {
  it('routes off the setup screen for a failed run that still holds results, instead of doing nothing', async () => {
    // Pre-I2, this button called `onOpen(slug)`, which routed through the
    // same `status`-only `reviewReachable` predicate as the nav rail. For a
    // `status: 'failed'` project that still had `has_results: true`, that
    // predicate said "not reachable", so the button sent the user right
    // back to the setup screen they were already on — indistinguishable
    // from the button doing nothing.
    const failedWithResults: ProjectSummary = {
      ...baseProject,
      slug: 'failed-1',
      name: 'Failed but has results',
      status: 'failed',
      has_results: true,
    }
    const setup: ProjectSetup = {
      slug: failedWithResults.slug,
      name: failedWithResults.name,
      target_currency: 'USD',
      requirements_file: 'spec.pdf',
      vendors: [{ name: 'ACME', file_count: 1, quote: 'quote.txt' }],
      fx_rates: {},
      status: 'failed',
      generation: 2,
      has_results: true,
      provider: {
        provider: 'mock',
        needs_key: null,
        ready: true,
        catalog: [{ id: 'mock', needs_key: null, ready: true }],
      },
    }
    vi.mocked(fetchProjects).mockResolvedValue([failedWithResults])
    vi.mocked(fetchSetup).mockResolvedValue(setup)
    render(<App />)
    await waitFor(() => {
      expect(
        screen.getByRole('option', { name: failedWithResults.name }),
      ).toBeInTheDocument()
    })

    navButton(/Set up & ingest/).click()
    const reviewButton = await screen.findByRole('button', {
      name: 'Review compliance matrix',
    })
    reviewButton.click()

    await waitFor(() => {
      expect(screen.getByText('Building compliance matrix…')).toBeInTheDocument()
    })
  })
})
