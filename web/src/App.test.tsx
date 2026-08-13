// BUG-001 (BUGS_TRACKER.md): the screens that read a stored extraction —
// `02 Overview`, `03 Compliance matrix` and `04 Comparative statement` —
// must be reachable only once a project's store holds an extraction to
// read. I2 (final-review report) corrected the gate from `status` to
// `has_results` directly — `status` can be `'failed'` while the store still
// holds a complete prior extraction (CLAUDE.md: "a failed extraction never
// blanks previously-good stored data") — so these tests drive `has_results`
// explicitly rather than inferring it from `status`.
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
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
    // Signing in now lands on the RFQ workflow, so its roster fetch fires on
    // every render here. Pending forever, like the others: these tests assert
    // on the nav rail, not on workflow data.
    fetchRfqRoster: vi.fn(() => new Promise<never>(() => {})),
    // Unlike the others these two must resolve with real shapes: the BUG-016
    // test drills roster -> project -> item before touching the rail.
    fetchWorkflowProjects: vi.fn(() => new Promise<never>(() => {})),
    fetchWorkflowProject: vi.fn(() => new Promise<never>(() => {})),
  }
})

import {
  fetchComplianceMatrix,
  fetchProjects,
  fetchSetup,
  fetchWorkflowProject,
  fetchWorkflowProjects,
} from './api'
import { GENERATOR, HALIBA_ROW, detail } from './pages/workflow-fixtures'

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
  renderApp()
  // Wait for the project list to load and the app to settle on one — both
  // happen asynchronously, so any nav assertion made before this settles would
  // be racing the fetch. The status bar names the active bid set, which is
  // independent of the nav gating these tests then assert on.
  await screen.findByText(baseProject.name)
}

/** `App` reads the URL for its active highlight and its bid-set slug, so it
 *  cannot mount without a router. Entries start at the root, which the route
 *  table redirects to the project roster — the same landing this suite has
 *  always asserted. */
function renderApp(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

function navButton(name: RegExp) {
  // Scoped to the rail. The breadcrumb inside a drill-down carries a
  // "Projects & items" button too, and an unscoped query matches both.
  return within(screen.getByRole('complementary')).getByRole('button', { name })
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

// Reported against the rail's project switcher, which no longer exists. The
// rule it broke is not about that control though — a review screen must never
// be mounted for a bid set with nothing to read, however you arrived at it — so
// it is asserted here on arrival, which is where `RequireResults` enforces it.
describe('a review screen is never mounted for an unreachable bid set (I1)', () => {
  it('sends a review URL to setup when the bid set has no results', async () => {
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
    renderApp(`/bid-sets/${newProject.slug}/matrix`)

    await waitFor(() => {
      // Never mounted — the matrix must not be sitting there refetching for a
      // project that was never ingested, which is the state BUG-001 reports.
      expect(
        screen.queryByText('Building compliance matrix…'),
      ).not.toBeInTheDocument()
      // Setup is where it lands instead; Setup's own loading state proves it
      // mounted rather than the guard merely rendering nothing.
      expect(screen.getByText('Loading project…')).toBeInTheDocument()
    })
  })

  it('mounts the review screen when the bid set does hold results', async () => {
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
    renderApp(`/bid-sets/${otherDoneProject.slug}/matrix`)

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
    renderApp()
    await screen.findByText(failedWithResults.name)

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

describe('signing in lands on the RFQ process, not on bid evaluation', () => {
  it('mounts the projects screen on first render', async () => {
    // The project is the top-level container the client gives us, so the front
    // door is the project roster. This replaces the phase-1 assertion that the
    // RFQ workflow mounted first: the landing screen moved one step up the
    // same half of the app, deliberately.
    await renderWithProject('done', true)

    expect(navButton(/Projects & items/)).toHaveClass('active')
    expect(screen.queryByText(/Loading RFQ workflow/i)).not.toBeInTheDocument()
  })

  it('names the two project concepts differently in the rail', async () => {
    // Two entries reading "Projects" over two different stores is the first
    // confusion a reader hits, so the bid-evaluation one is "Bid sets".
    await renderWithProject('done', true)

    expect(navButton(/Projects & items/)).toBeInTheDocument()
    expect(navButton(/Bid sets/)).toBeInTheDocument()
    // Exactly one rail entry says "Projects" — the workflow one. The
    // bid-evaluation entry used to say it too, which is what this guards.
    // (Nav names carry their index prefix, so these are unanchored.)
    expect(screen.getAllByRole('button', { name: /Projects/ })).toHaveLength(1)
  })

  it('still reaches the RFQ workflow from the rail', async () => {
    await renderWithProject('done', true)

    fireEvent.click(navButton(/RFQ workflow/))

    // fetchRfqRoster is mocked pending-forever, so the workflow screen's own
    // loading state is the stable proof of *which* screen mounted.
    expect(screen.getByText(/Loading RFQ workflow/i)).toBeInTheDocument()
    expect(navButton(/RFQ workflow/)).toHaveClass('active')
  })

  it('groups the rail so ingestion reads as part of bid evaluation', async () => {
    await renderWithProject('done', true)

    expect(screen.getByText('RFQ process')).toBeInTheDocument()
    expect(screen.getByText('Bid evaluation')).toBeInTheDocument()
    // The ingestion and review screens belong to the second group, not the first.
    for (const label of [/Set up & ingest/, /Extraction status/, /Compliance matrix/]) {
      expect(navButton(label)).toBeInTheDocument()
    }
  })

  it('still lets you leave the RFQ process for a bid-evaluation screen', async () => {
    await renderWithProject('done', true)

    fireEvent.click(navButton(/Compliance matrix/))

    expect(screen.queryByText(/Loading RFQ workflow/i)).not.toBeInTheDocument()
    expect(navButton(/Compliance matrix/)).toHaveClass('active')
  })
})

describe('a rail click returns a section to its top level (BUG-016)', () => {
  // This used to need `navEpoch`: the drill-down lived inside `Projects`, so
  // clicking the rail entry for the section you were already in ran `setView`
  // against the value it already held — a no-op that left the drill-down
  // mounted and made the button read as dead. The counter forced a remount.
  //
  // With the drill-down in the URL the click is an ordinary navigation from
  // `/projects/:id/items/:itemId` to `/projects`, and the counter is gone.
  it('clicking 01 while inside an item returns to the project roster', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([HALIBA_ROW])
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())

    renderApp()

    // Drill in: roster -> project -> item.
    fireEvent.click(await screen.findByRole('button', { name: HALIBA_ROW.name }))
    fireEvent.click(await screen.findByRole('button', { name: GENERATOR.item_type }))
    expect(await screen.findByRole('heading', { name: GENERATOR.item_type })).toBeInTheDocument()

    fireEvent.click(navButton(/Projects & items/))

    // Back at the roster, not still on the item.
    expect(await screen.findByText(/Every project, the equipment items/i)).toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: GENERATOR.item_type }),
    ).not.toBeInTheDocument()
  })
})
