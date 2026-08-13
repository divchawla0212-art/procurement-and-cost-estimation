import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Projects } from './Projects'
import { HALIBA_ROW as HALIBA } from './workflow-fixtures'

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

  it('lists projects with their item and RFQ counts', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([HALIBA])

    render(<Projects />)

    const name = await screen.findByText('Haliba Field Development')
    const row = name.closest('tr')!
    expect(row).toHaveTextContent('HAL')
    expect(row).toHaveTextContent('4')
    expect(row).toHaveTextContent('1')
  })

  it('tells the user what an empty roster means', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([])
    render(<Projects />)
    expect(await screen.findByText(/No projects yet/i)).toBeInTheDocument()
  })

  it('creates a project and shows it in the roster', async () => {
    vi.mocked(fetchWorkflowProjects)
      .mockResolvedValueOnce([])
      .mockResolvedValue([HALIBA])
    vi.mocked(createWorkflowProject).mockResolvedValue(HALIBA)

    render(<Projects />)
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

    render(<Projects />)
    fireEvent.click(await screen.findByRole('button', { name: /new project/i }))
    fillProjectForm()
    fireEvent.click(screen.getByRole('button', { name: /create project/i }))

    // Verbatim. A rewritten "Something went wrong" throws away the only part
    // of the answer the reader can act on.
    expect(await screen.findByText(/already in use/i)).toBeInTheDocument()
  })

  it('opens a project when its name is clicked', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([HALIBA])
    const { fetchWorkflowProject } = await import('../api')
    vi.mocked(fetchWorkflowProject).mockResolvedValue({
      project: HALIBA,
      items: [],
      rfqs: [],
    })

    render(<Projects />)
    fireEvent.click(await screen.findByText('Haliba Field Development'))

    expect(
      await screen.findByRole('button', { name: /back to projects/i }),
    ).toBeInTheDocument()
  })
})
