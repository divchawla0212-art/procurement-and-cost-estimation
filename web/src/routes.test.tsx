import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import { AppRoutes } from './routes'
import type { ProjectSummary } from './types'
import type { User } from './auth/context'

/**
 * The route table's job is dispatch: given a URL, mount one screen and hand it
 * the parameters the URL carried. So the screens are mocked down to markers
 * that echo their props back. That keeps these tests about routing — they do
 * not break when a page's copy changes, and they say exactly which screen
 * mounted rather than inferring it from a heading.
 */
function marker(name: string) {
  return (props: Record<string, unknown>) => (
    <div data-testid={name} data-props={JSON.stringify(scalars(props))} />
  )
}

/** Props minus the callbacks and objects, which do not survive JSON and are not
 *  what these tests are asserting. `null` is kept deliberately: `typeof null`
 *  is `'object'`, and dropping it would hide the difference between "Setup was
 *  handed no slug" and "Setup was handed nothing at all" — which is the whole
 *  assertion on `/bid-sets/new`. */
function scalars(props: Record<string, unknown>) {
  return Object.fromEntries(
    Object.entries(props).filter(
      ([, v]) => typeof v !== 'function' && (v === null || typeof v !== 'object'),
    ),
  )
}

vi.mock('./pages/Projects', () => ({ Projects: marker('projects') }))
vi.mock('./pages/ProjectDetail', () => ({ ProjectDetail: marker('project-detail') }))
vi.mock('./pages/ItemDetail', () => ({ ItemDetail: marker('item-detail') }))
vi.mock('./pages/Bidders', () => ({ Bidders: marker('bidders') }))
vi.mock('./pages/RfqWorkflow', () => ({ RfqWorkflow: marker('rfq-workflow') }))
vi.mock('./pages/RfqWizard', () => ({ RfqWizard: marker('rfq-wizard') }))
vi.mock('./pages/RfqDetail', () => ({ RfqDetail: marker('rfq-detail') }))
vi.mock('./pages/Dashboard', () => ({ Dashboard: marker('dashboard') }))
vi.mock('./pages/Setup', () => ({ Setup: marker('setup') }))
vi.mock('./pages/Overview', () => ({ Overview: marker('overview') }))
vi.mock('./pages/ComplianceMatrix', () => ({ ComplianceMatrix: marker('matrix') }))
vi.mock('./pages/ComparativeStatement', () => ({ ComparativeStatement: marker('statement') }))
vi.mock('./pages/ExtractionStatus', () => ({ ExtractionStatus: marker('extraction') }))
vi.mock('./pages/Admin', () => ({ Admin: marker('admin') }))

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>()
  return { ...actual, fetchRfqRoster: vi.fn() }
})

import { fetchRfqRoster } from './api'

const HALIBA: ProjectSummary = {
  slug: 'haliba',
  name: 'Haliba',
  vendors: ['Alpha'],
  target_currency: 'USD',
  status: 'done',
  generation: 2,
  has_results: true,
}
// A project that has been created but never ingested — the review screens have
// nothing to read for it.
const BAB: ProjectSummary = { ...HALIBA, slug: 'bab', name: 'Bab', has_results: false }

const ADMIN: User = { id: 'usr_1', email: 'a@example.com', role: 'admin' }
const REVIEWER: User = { id: 'usr_2', email: 'r@example.com', role: 'reviewer' }

/** Reports the URL the router settled on, so a redirect is observable. */
function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="location">{loc.pathname + loc.search}</div>
}

function renderAt(
  path: string,
  opts: {
    projects?: ProjectSummary[] | null
    loading?: boolean
    error?: string | null
    user?: User
  } = {},
) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <LocationProbe />
      <AppRoutes
        projects={opts.projects === undefined ? [HALIBA, BAB] : opts.projects}
        loading={opts.loading ?? false}
        error={opts.error ?? null}
        reload={vi.fn()}
        user={opts.user ?? ADMIN}
      />
    </MemoryRouter>,
  )
}

function propsOf(testId: string) {
  return JSON.parse(screen.getByTestId(testId).getAttribute('data-props') ?? '{}')
}

const at = () => screen.getByTestId('location').textContent

describe('AppRoutes — dispatch', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchRfqRoster).mockResolvedValue({
      stages: ['Scoping', 'Shortlisting'],
      stage_counts: {},
      rfqs: [{ id: 'rfq_1', reference: 'RFQ-1', package: 'P', discipline: 'D', stage: 'Scoping' }],
    } as never)
  })

  it('sends the root to the project roster', async () => {
    renderAt('/')
    await screen.findByTestId('projects')
    expect(at()).toBe('/projects')
  })

  // Not a 404 page: there is nowhere in this application a stray URL is more
  // usefully pointed than the roster it starts from.
  it('sends an unknown path to the project roster', async () => {
    renderAt('/nowhere/at/all')
    await screen.findByTestId('projects')
    expect(at()).toBe('/projects')
  })

  it('mounts the project roster at /projects', async () => {
    renderAt('/projects')
    expect(screen.getByTestId('projects')).toBeInTheDocument()
  })

  it('carries the project id into the project detail', async () => {
    renderAt('/projects/prj_5049beff')
    expect(propsOf('project-detail').projectId).toBe('prj_5049beff')
  })

  it('carries both ids into the item detail', async () => {
    renderAt('/projects/prj_5049beff/items/itm_7')
    expect(propsOf('item-detail')).toMatchObject({
      projectId: 'prj_5049beff',
      itemId: 'itm_7',
    })
  })

  it('mounts the bidder registry at /bidders', () => {
    renderAt('/bidders')
    expect(screen.getByTestId('bidders')).toBeInTheDocument()
  })

  it('mounts the RFQ roster at /rfqs', () => {
    renderAt('/rfqs')
    expect(screen.getByTestId('rfq-workflow')).toBeInTheDocument()
  })

  // The wizard covers the stages that have editors behind them; anything past
  // Clarifications gets the read-only view. A deep link arrives with no roster
  // loaded, so the route has to fetch one to make that choice.
  it('opens an early-stage RFQ in the wizard', async () => {
    renderAt('/rfqs/rfq_1')
    await screen.findByTestId('rfq-wizard')
    expect(propsOf('rfq-wizard').rfqId).toBe('rfq_1')
  })

  it('opens a late-stage RFQ in the read-only view', async () => {
    vi.mocked(fetchRfqRoster).mockResolvedValue({
      stages: ['Scoping'],
      stage_counts: {},
      rfqs: [{ id: 'rfq_9', reference: 'RFQ-9', package: 'P', discipline: 'D', stage: 'Awarded' }],
    } as never)
    renderAt('/rfqs/rfq_9')
    await screen.findByTestId('rfq-detail')
  })

  it('mounts the bid-set roster at /bid-sets', () => {
    renderAt('/bid-sets')
    expect(screen.getByTestId('dashboard')).toBeInTheDocument()
  })

  it('opens setup with no slug at /bid-sets/new', () => {
    renderAt('/bid-sets/new')
    expect(propsOf('setup').slug).toBe(null)
  })

  it.each([
    ['setup', 'setup'],
    ['extraction', 'extraction'],
    ['overview', 'overview'],
    ['matrix', 'matrix'],
    ['statement', 'statement'],
  ])('carries the slug into /bid-sets/:slug/%s', (segment, testId) => {
    renderAt(`/bid-sets/haliba/${segment}`)
    expect(propsOf(testId).slug).toBe('haliba')
  })

  // The one piece of navigation state that is a filter rather than a location,
  // which is exactly what a query string is for. It also makes the Overview's
  // vendor drill-down a link somebody can send.
  it('reads the matrix vendor filter off the query string', () => {
    renderAt('/bid-sets/haliba/matrix?vendor=Alpha%20Systems')
    expect(propsOf('matrix').initialVendor).toBe('Alpha Systems')
  })

  it('leaves the vendor filter unset when the query string carries none', () => {
    renderAt('/bid-sets/haliba/matrix')
    expect(propsOf('matrix').initialVendor).toBeUndefined()
  })

  it('mounts the admin screen at /admin for an admin', () => {
    renderAt('/admin', { user: ADMIN })
    expect(screen.getByTestId('admin')).toBeInTheDocument()
  })
})

describe('AppRoutes — guards', () => {
  beforeEach(() => vi.clearAllMocks())

  it.each(['overview', 'matrix', 'statement'])(
    'sends /%s to setup when the project holds no extraction',
    async (segment) => {
      renderAt(`/bid-sets/bab/${segment}`)
      await waitFor(() => expect(at()).toBe('/bid-sets/bab/setup'))
      expect(screen.queryByTestId(segment === 'matrix' ? 'matrix' : segment)).toBeNull()
    },
  )

  it('admits a review screen when the project does hold an extraction', () => {
    renderAt('/bid-sets/haliba/overview')
    expect(screen.getByTestId('overview')).toBeInTheDocument()
  })

  // Extraction status is the screen that reports *why* the review screens are
  // unreachable, so it must stay open for a project with no results.
  it('lets extraction status through without an extraction', () => {
    renderAt('/bid-sets/bab/extraction')
    expect(screen.getByTestId('extraction')).toBeInTheDocument()
  })

  it('sends an unknown slug back to the bid-set roster', async () => {
    renderAt('/bid-sets/not-a-project/overview')
    await waitFor(() => expect(at()).toBe('/bid-sets'))
  })

  // The roster is still in flight on a deep link. Deciding now would bounce
  // every bookmarked review screen to setup before its project had loaded.
  it('waits for the roster rather than redirecting while it loads', () => {
    renderAt('/bid-sets/haliba/overview', { projects: null, loading: true })
    expect(at()).toBe('/bid-sets/haliba/overview')
    expect(screen.queryByTestId('setup')).toBeNull()
  })

  it('keeps a reviewer out of the admin screen', async () => {
    renderAt('/admin', { user: REVIEWER })
    await waitFor(() => expect(at()).toBe('/projects'))
    // Not merely redirected — the screen must never mount. Its calls would 403
    // anyway; it should not appear at all.
    expect(screen.queryByTestId('admin')).toBeNull()
  })
})
