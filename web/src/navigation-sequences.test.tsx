/**
 * The navigation-sequence matrix.
 *
 * `docs/superpowers/PLAN-TEMPLATE.md`'s finding, applied to a front-end change:
 * every defect that survived phase 2's per-task TDD needed **two runs or two
 * modules to see**. This whole design is in that class, and one row is the
 * reason the file exists:
 *
 * > A guard that redirects with a *push* instead of a *replace* renders
 * > correctly, passes any single-navigation test, and passes a spy asserting
 * > `navigate` was called. It fails only when you arrive at the guarded route
 * > and **then press Back** — and it fails as a Back button that appears
 * > frozen, which reads as a bug in the button rather than in the guard.
 *
 * So every test here is a sequence, and the assertions are behavioural: where
 * did we end up, not which function was called.
 *
 * Rows covered elsewhere, deliberately not duplicated:
 * - forward-branch truncation, and back/forward across a long walk —
 *   `NavHistory.test.tsx`
 * - a reviewer deep-linking `/admin` — `routes.test.tsx`
 * - a refresh on a deep path — `tests/test_spa_fallback.py`
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router'
import App from './App'
import type { ProjectSummary } from './types'

const auth = vi.hoisted(() => ({
  value: {
    user: { id: 'u1', email: 'admin@example.com', role: 'admin' as const } as {
      id: string
      email: string
      role: 'admin' | 'reviewer'
    } | null,
    ready: true,
    login: async () => ({ ok: true }),
    signup: async () => ({ ok: true }),
    logout: async () => {},
  },
}))

// Built once and mutated in place, never replaced. `App` passes `user` into a
// `useAsync` dependency list, so a fresh object per call refetches on every
// render until the worker dies of heap exhaustion — which arrives as
// "Worker exited unexpectedly", not as a failed assertion (CLAUDE.md).
vi.mock('./auth/context', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./auth/context')>()
  return { ...actual, useAuth: () => auth.value }
})

// The Overview stands in for any screen that drills into another with a filter:
// its one control invokes the real `onOpenMatrix` the route wrapper handed it.
vi.mock('./pages/Overview', () => ({
  Overview: ({ onOpenMatrix }: { onOpenMatrix: (v?: string) => void }) => (
    <div>
      <h1>Overview screen</h1>
      <button type="button" onClick={() => onOpenMatrix('Alpha Systems')}>
        drill into Alpha Systems
      </button>
    </div>
  ),
}))
vi.mock('./pages/ComplianceMatrix', () => ({
  ComplianceMatrix: ({ initialVendor }: { initialVendor?: string }) => (
    <h1>Matrix screen for {initialVendor ?? 'all vendors'}</h1>
  ),
}))

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>()
  return {
    ...actual,
    fetchProjects: vi.fn(),
    fetchSummary: vi.fn(() => new Promise<never>(() => {})),
    fetchSetup: vi.fn(() => new Promise<never>(() => {})),
    fetchRfqRoster: vi.fn(() => new Promise<never>(() => {})),
    fetchWorkflowProjects: vi.fn(() => new Promise<never>(() => {})),
    fetchWorkflowProject: vi.fn(() => new Promise<never>(() => {})),
    fetchExtractionStatus: vi.fn(() => new Promise<never>(() => {})),
  }
})

import { fetchProjects, fetchWorkflowProject, fetchWorkflowProjects } from './api'
import { GENERATOR, HALIBA_ROW, detail } from './pages/workflow-fixtures'

const base: ProjectSummary = {
  slug: 'haliba',
  name: 'Haliba',
  vendors: ['Alpha'],
  target_currency: 'USD',
  status: 'done',
  generation: 2,
  has_results: true,
}
/** Created but never ingested — the review screens have nothing to read. */
const BAB: ProjectSummary = { ...base, slug: 'bab', name: 'Bab', has_results: false }

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="location">{loc.pathname + loc.search}</div>
}

/**
 * A push from inside the application.
 *
 * The rail's project switcher used to supply this — it was the one control that
 * could navigate to a screen the guard would then turn away. With the switcher
 * gone, no *current* control pushes into a guarded route: the Bid sets roster
 * computes the right destination, and the rail greys out what a project cannot
 * supply.
 *
 * That does not retire the invariant. `RequireResults` guards a URL, and a URL
 * can be arrived at by any future control as well as by a link somebody sends —
 * so the contract "a refused route is replaced, not pushed" is tested here at
 * the level it actually holds, independent of which control did the pushing.
 * Fabricating the entry through `initialEntries` instead would not do: an entry
 * the app never visited is one `history-stack.ts` deliberately treats as
 * unknown, which tests the reset rule rather than this one.
 */
function PushTo({ to }: { to: string }) {
  const navigate = useNavigate()
  return (
    <button type="button" onClick={() => navigate(to)}>
      push to {to}
    </button>
  )
}

function renderApp(entries: string[], index?: number, push?: string) {
  return render(
    <MemoryRouter initialEntries={entries} initialIndex={index}>
      <LocationProbe />
      {push && <PushTo to={push} />}
      <App />
    </MemoryRouter>,
  )
}

const pushButton = (to: string) =>
  screen.getByRole('button', { name: `push to ${to}` })

const at = () => screen.getByTestId('location').textContent
const back = () => screen.getByRole('button', { name: 'Go back' })
const forward = () => screen.getByRole('button', { name: 'Go forward' })
const navButton = (name: RegExp) =>
  within(screen.getByRole('complementary')).getByRole('button', { name })

beforeEach(() => {
  vi.clearAllMocks()
  auth.value.user = { id: 'u1', email: 'admin@example.com', role: 'admin' }
  vi.mocked(fetchProjects).mockResolvedValue([base, BAB])
})

describe('a guard redirect stays out of the history', () => {
  // The row this file exists for. Arriving at a guarded route must leave the
  // history exactly one entry longer, with the redirecting URL not in it.
  //
  // See `PushTo` above for why the push comes from a harness rather than a
  // rail control.
  it('goes back past the route that redirected, not onto it', async () => {
    renderApp(['/bid-sets/haliba/overview'], undefined, '/bid-sets/bab/overview')
    await screen.findByText('Overview screen')

    fireEvent.click(pushButton('/bid-sets/bab/overview'))

    // The guard replaced it with setup, because Bab holds no extraction.
    await waitFor(() => expect(at()).toBe('/bid-sets/bab/setup'))

    fireEvent.click(back())
    await waitFor(() => expect(at()).toBe('/bid-sets/haliba/overview'))
  })

  // The stronger half, and the one a spy on `navigate` cannot reach: after
  // going back, Forward must return to the *redirect target*. If the redirect
  // had pushed, `/bid-sets/bab/overview` would still be sitting in the history
  // and Forward would land on it — where the guard would bounce again, so the
  // user would see Forward skip a step it never asked for.
  it('does not leave the refused URL sitting in the forward direction', async () => {
    renderApp(['/bid-sets/haliba/overview'], undefined, '/bid-sets/bab/overview')
    await screen.findByText('Overview screen')

    fireEvent.click(pushButton('/bid-sets/bab/overview'))
    await waitFor(() => expect(at()).toBe('/bid-sets/bab/setup'))

    fireEvent.click(back())
    await waitFor(() => expect(at()).toBe('/bid-sets/haliba/overview'))

    fireEvent.click(forward())
    await waitFor(() => expect(at()).toBe('/bid-sets/bab/setup'))
  })

  it('offers no way back at all from a deep-linked guarded route', async () => {
    renderApp(['/bid-sets/bab/overview'])

    await waitFor(() => expect(at()).toBe('/bid-sets/bab/setup'))
    // One entry, replaced in place. A pushing redirect would leave two and
    // light this button up, offering a Back that only returns to a URL the
    // guard immediately refuses again.
    expect(back()).toBeDisabled()
  })
})

describe('a rail click returns a section to its top level', () => {
  // What `navEpoch` used to buy, now bought by the drill-down having an
  // address. Three navigations to see it.
  it('clicking 01 while inside an item returns to the project roster', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([HALIBA_ROW])
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())

    renderApp(['/projects'])

    fireEvent.click(await screen.findByRole('button', { name: HALIBA_ROW.name }))
    await waitFor(() => expect(at()).toBe(`/projects/${HALIBA_ROW.id}`))

    fireEvent.click(await screen.findByRole('button', { name: GENERATOR.item_type }))
    await waitFor(() =>
      expect(at()).toBe(`/projects/${HALIBA_ROW.id}/items/${GENERATOR.id}`),
    )

    fireEvent.click(navButton(/Projects & items/))
    await waitFor(() => expect(at()).toBe('/projects'))
  })

  it('leaves the drill-down reachable by Back afterwards', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([HALIBA_ROW])
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())

    renderApp(['/projects'])
    fireEvent.click(await screen.findByRole('button', { name: HALIBA_ROW.name }))
    await waitFor(() => expect(at()).toBe(`/projects/${HALIBA_ROW.id}`))

    fireEvent.click(navButton(/Projects & items/))
    await waitFor(() => expect(at()).toBe('/projects'))

    // The rail click was a navigation, not a reset — so the project is still
    // behind us. Under `navEpoch` there was nothing to go back to.
    fireEvent.click(back())
    await waitFor(() => expect(at()).toBe(`/projects/${HALIBA_ROW.id}`))
  })
})

describe('a filter drilled into is a place you can come back from', () => {
  it('carries the vendor into the matrix and back out again', async () => {
    renderApp(['/bid-sets/haliba/overview'])

    await screen.findByText('Overview screen')
    fireEvent.click(screen.getByRole('button', { name: /drill into Alpha Systems/i }))

    await waitFor(() => expect(at()).toBe('/bid-sets/haliba/matrix?vendor=Alpha%20Systems'))
    expect(screen.getByText(/Matrix screen for Alpha Systems/)).toBeInTheDocument()

    fireEvent.click(back())

    // Back to the Overview, not to an unfiltered matrix: the filter travelled
    // as part of the location rather than as state the history never saw.
    await waitFor(() => expect(at()).toBe('/bid-sets/haliba/overview'))
    expect(screen.getByText('Overview screen')).toBeInTheDocument()
  })

  it('goes forward into the filtered matrix again', async () => {
    renderApp(['/bid-sets/haliba/overview'])

    await screen.findByText('Overview screen')
    fireEvent.click(screen.getByRole('button', { name: /drill into Alpha Systems/i }))
    await waitFor(() => expect(at()).toContain('vendor=Alpha'))

    fireEvent.click(back())
    await waitFor(() => expect(at()).toBe('/bid-sets/haliba/overview'))

    fireEvent.click(forward())
    await waitFor(() => expect(at()).toBe('/bid-sets/haliba/matrix?vendor=Alpha%20Systems'))
    expect(screen.getByText(/Matrix screen for Alpha Systems/)).toBeInTheDocument()
  })
})

// I1 was reported as a switcher bug — switching project left a review screen
// mounted and refetching for a project that had never been ingested. The
// switcher is gone, but the rule it violated is a property of *arriving* at a
// review screen, not of how you arrived, so it still has to hold.
describe('arriving at a review screen the bid set cannot supply (I1)', () => {
  it('lands on that bid set’s setup, and Back returns to where you were', async () => {
    renderApp(['/bid-sets/haliba/matrix'], undefined, '/bid-sets/bab/matrix')
    await screen.findByText(/Matrix screen/)

    fireEvent.click(pushButton('/bid-sets/bab/matrix'))

    // The guard bounces it to setup — one entry, because the bounce replaces.
    await waitFor(() => expect(at()).toBe('/bid-sets/bab/setup'))
    // And it is not still mounted, refetching for a project with nothing to
    // read, which is what BUG-001 actually reported.
    expect(screen.queryByText(/Matrix screen/)).toBeNull()

    fireEvent.click(back())
    await waitFor(() => expect(at()).toBe('/bid-sets/haliba/matrix'))
  })

  it('stays on the screen when the bid set can supply it', async () => {
    const other: ProjectSummary = { ...base, slug: 'other', name: 'Other', has_results: true }
    vi.mocked(fetchProjects).mockResolvedValue([base, other])
    renderApp(['/bid-sets/haliba/matrix'], undefined, '/bid-sets/other/matrix')

    await screen.findByText(/Matrix screen/)
    fireEvent.click(pushButton('/bid-sets/other/matrix'))

    await waitFor(() => expect(at()).toBe('/bid-sets/other/matrix'))
  })
})

describe('the sign-in gate', () => {
  // The URL has to survive signing in, which it does by `App` swapping the
  // shell for <Auth/> without navigating anywhere. Stated as the two halves
  // that are actually observable, rather than by faking a session ending
  // mid-render: the address is untouched while signed out, and the same
  // address renders its screen once signed in.
  it('leaves the address alone while signed out', () => {
    auth.value.user = null
    renderApp(['/bid-sets/haliba/matrix'])

    expect(screen.getByRole('heading', { name: /sign in/i })).toBeInTheDocument()
    // No redirect to a login path, and nothing rewritten to the roster — so
    // there is an address left to return to.
    expect(at()).toBe('/bid-sets/haliba/matrix')
  })

  it('renders that same address once a session exists', async () => {
    renderApp(['/bid-sets/haliba/matrix'])

    expect(await screen.findByText(/Matrix screen/)).toBeInTheDocument()
    expect(at()).toBe('/bid-sets/haliba/matrix')
  })
})
