import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import { Projects } from './Projects'
import { HALIBA_ROW as HALIBA } from './workflow-fixtures'

const BAB = {
  ...HALIBA,
  id: 'prj_2',
  name: 'Bab Compression',
  code: 'BAB',
  item_count: 0,
  rfq_count: 0,
}

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    fetchWorkflowProjects: vi.fn(),
    fetchWorkflowProject: vi.fn(),
    createWorkflowProject: vi.fn(),
  }
})

import { createWorkflowProject, fetchWorkflowProjects } from '../api'

/** The roster navigates rather than swapping in a detail subtree, so it needs a
 *  router around it and a way to read where a click sent us. */
function LocationProbe() {
  return <div data-testid="location">{useLocation().pathname}</div>
}

function renderProjects() {
  return render(
    <MemoryRouter initialEntries={['/projects']}>
      <LocationProbe />
      <Projects />
    </MemoryRouter>,
  )
}

const at = () => screen.getByTestId('location').textContent

function fillProjectForm() {
  const type = (label: string, value: string) =>
    fireEvent.change(screen.getByLabelText(label), { target: { value } })
  type('Name', 'Haliba Field Development')
  type('Code', 'HAL')
  type('Client', 'Al Dhafra Petroleum')
  type('Location', 'Haliba field, UAE')
  type('Live period start', '2026-01-01')
  type('Live period end', '2029-12-31')
}

describe('Projects', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('gives each project its own card, titled with the project name', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([HALIBA, BAB])

    renderProjects()

    await screen.findByText('Haliba Field Development')
    // One card per project, and the name is the card's heading — not a cell in
    // a shared row, which is what made four projects unreadable.
    const headings = screen.getAllByRole('heading', { level: 3 })
    expect(headings.map((h) => h.textContent)).toEqual([
      'Haliba Field Development',
      'Bab Compression',
    ])
  })

  it('keeps the counts and status visible on the collapsed card', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([HALIBA])

    renderProjects()

    const card = (await screen.findByText('Haliba Field Development')).closest(
      'article',
    )!
    expect(card).toHaveTextContent('HAL')
    expect(card).toHaveTextContent('4 items')
    expect(card).toHaveTextContent('1 RFQ')
    expect(card).toHaveTextContent('Active')
  })

  it('singularises the counts so a card never reads "1 items"', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([
      { ...HALIBA, item_count: 1, rfq_count: 1 },
    ])

    renderProjects()

    const card = (await screen.findByText('Haliba Field Development')).closest(
      'article',
    )!
    expect(card).toHaveTextContent('1 item')
    expect(card).not.toHaveTextContent('1 items')
    expect(card).not.toHaveTextContent('1 RFQs')
  })

  it('folds the rest of the detail behind a closed disclosure', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([HALIBA])

    renderProjects()
    const card = (await screen.findByText('Haliba Field Development')).closest(
      'article',
    )!
    const details = card.querySelector('details')!

    // Closed by default — that is the whole point. The fields are in the DOM
    // (so they are findable and printable) but not on screen.
    expect(details.open).toBe(false)
    expect(within(card).getByText('Project code')).toBeInTheDocument()
  })

  it('shows every detail field once the disclosure is opened', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([HALIBA])

    renderProjects()
    const card = (await screen.findByText('Haliba Field Development')).closest(
      'article',
    )!
    fireEvent.click(within(card).getByText('Details'))

    const labels = [...card.querySelectorAll('dt')].map((d) => d.textContent)
    expect(labels).toEqual([
      'Project code',
      'Client',
      'Location',
      'Live period',
      'Currency',
      'Status',
      'Items',
      'RFQs raised',
    ])
    expect(within(card).getByText('Al Dhafra Petroleum')).toBeInTheDocument()
    expect(within(card).getByText('Haliba field, UAE')).toBeInTheDocument()
    expect(
      within(card).getByText('2026-01-01 → 2029-12-31'),
    ).toBeInTheDocument()
  })

  it('opens one card without opening the others', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([HALIBA, BAB])

    renderProjects()
    const first = (await screen.findByText('Haliba Field Development')).closest(
      'article',
    )!
    const second = screen.getByText('Bab Compression').closest('article')!
    fireEvent.click(within(first).getByText('Details'))

    expect(first.querySelector('details')!.open).toBe(true)
    expect(second.querySelector('details')!.open).toBe(false)
  })

  it('carries the status as a class as well as a word', async () => {
    // Colour is never the only signal, but the class is what lets On Hold read
    // differently from Active at a glance.
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([
      { ...HALIBA, status: 'On Hold' },
    ])

    renderProjects()
    const card = (await screen.findByText('Haliba Field Development')).closest(
      'article',
    )!

    expect(card.querySelector('.pcard-status--on-hold')).toBeTruthy()
    expect(card.querySelector('.pcard-status')!.textContent).toBe('On Hold')
    // Twice on purpose: the pill is the at-a-glance signal on the collapsed
    // card, and the Status row is part of the full record inside the details.
    expect(within(card).getAllByText('On Hold')).toHaveLength(2)
  })

  it('tells the user what an empty roster means', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([])
    renderProjects()
    expect(await screen.findByText(/No projects yet/i)).toBeInTheDocument()
  })

  it('creates a project and shows it in the roster', async () => {
    vi.mocked(fetchWorkflowProjects)
      .mockResolvedValueOnce([])
      .mockResolvedValue([HALIBA])
    vi.mocked(createWorkflowProject).mockResolvedValue(HALIBA)

    renderProjects()
    await screen.findByText(/No projects yet/i)

    fireEvent.click(screen.getByRole('button', { name: /new project/i }))
    fillProjectForm()
    fireEvent.click(screen.getByRole('button', { name: /create project/i }))

    await waitFor(() =>
      expect(createWorkflowProject).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Haliba Field Development',
          code: 'HAL',
          client: 'Al Dhafra Petroleum',
          currency: 'AED',
        }),
      ),
    )
    expect(await screen.findByText('Haliba Field Development')).toBeInTheDocument()
  })

  it("shows the server's own message when creation is refused", async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([])
    vi.mocked(createWorkflowProject).mockRejectedValue(
      new Error('Project code HAL is already in use.'),
    )

    renderProjects()
    fireEvent.click(await screen.findByRole('button', { name: /new project/i }))
    fillProjectForm()
    fireEvent.click(screen.getByRole('button', { name: /create project/i }))

    // Verbatim. A rewritten "Something went wrong" throws away the only part
    // of the answer the reader can act on.
    expect(await screen.findByText(/already in use/i)).toBeInTheDocument()
  })

  // The roster no longer swaps a detail subtree in beneath itself — it
  // navigates, so the project it opened has an address that survives a refresh
  // and can be handed to somebody else.
  it('navigates to a project when its name is clicked', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([HALIBA])

    renderProjects()
    fireEvent.click(await screen.findByText('Haliba Field Development'))

    await waitFor(() => expect(at()).toBe(`/projects/${HALIBA.id}`))
  })

  it('navigates from the Open project button too', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([HALIBA])

    renderProjects()
    fireEvent.click(await screen.findByRole('button', { name: /open project/i }))

    await waitFor(() => expect(at()).toBe(`/projects/${HALIBA.id}`))
  })

  // Assertable by grep as well as by test: no field in this component selects a
  // screen. A surviving drill-down would be a screen with no address, silently
  // outside the history.
  it('holds no drill-down state of its own', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([HALIBA])
    renderProjects()
    fireEvent.click(await screen.findByText('Haliba Field Development'))

    // Still the roster underneath — the click changed the URL, not this tree.
    expect(screen.getByText('Haliba Field Development')).toBeInTheDocument()
  })
})
