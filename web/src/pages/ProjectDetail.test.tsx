import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ProjectDetail } from './ProjectDetail'
import {
  CABLE,
  GENERATOR,
  HALIBA_PROJECT,
  detail,
  rfq,
} from './workflow-fixtures'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    fetchWorkflowProject: vi.fn(),
    createRfq: vi.fn(),
    updateWorkflowProject: vi.fn(),
    deleteWorkflowProject: vi.fn(),
    createWorkflowItem: vi.fn(),
    deleteWorkflowItem: vi.fn(),
  }
})

import {
  createRfq,
  createWorkflowItem,
  deleteWorkflowItem,
  deleteWorkflowProject,
  fetchWorkflowProject,
  updateWorkflowProject,
} from '../api'

function renderDetail(onOpenItem = vi.fn(), onBack = vi.fn()) {
  render(
    <ProjectDetail projectId="prj_1" onOpenItem={onOpenItem} onBack={onBack} />,
  )
  return { onOpenItem, onBack }
}

function fillItemForm() {
  const type = (label: string, value: string) =>
    fireEvent.change(screen.getByLabelText(label), { target: { value } })
  type('Item type', 'Gas generator')
  type('Description', '2 x 5 MW containerised')
  type('Quantity', '2')
  type('Unit of measure', 'no')
  type('Discipline', 'Electrical')
  type('Estimated value (AED)', '18000000')
}

describe('ProjectDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("lists the project's items", async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())

    renderDetail()

    expect(await screen.findByText('Gas generator')).toBeInTheDocument()
    expect(screen.getByText('2 x 5 MW containerised')).toBeInTheDocument()
    expect(screen.getByText('Electrical')).toBeInTheDocument()
  })

  it('opens an item when its type is clicked', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    const { onOpenItem } = renderDetail()

    fireEvent.click(await screen.findByText('Gas generator'))

    expect(onOpenItem).toHaveBeenCalledWith('itm_1')
  })

  it('lists the RFQs raised against the project', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
    )

    renderDetail()

    expect(await screen.findByText('ADP-RFQ-2026-014')).toBeInTheDocument()
  })

  it('adds an item and reloads', async () => {
    vi.mocked(fetchWorkflowProject)
      .mockResolvedValueOnce(detail({ items: [] }))
      .mockResolvedValue(detail())
    vi.mocked(createWorkflowItem).mockResolvedValue({
      ...GENERATOR,
      live_period_warning: null,
    })

    renderDetail()
    fireEvent.click(await screen.findByRole('button', { name: /add item/i }))
    fillItemForm()
    fireEvent.click(screen.getByRole('button', { name: /save item/i }))

    await waitFor(() =>
      expect(createWorkflowItem).toHaveBeenCalledWith(
        'prj_1',
        expect.objectContaining({ item_type: 'Gas generator', qty: 2 }),
      ),
    )
    expect(await screen.findByText('Gas generator')).toBeInTheDocument()
  })

  it('shows the live-period caution returned with a saved item', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [] }))
    vi.mocked(createWorkflowItem).mockResolvedValue({
      ...GENERATOR,
      required_on_site: '2030-06-01',
      live_period_warning:
        '2030-06-01 falls outside the project live period (2026-01-01 to 2029-12-31)',
    })

    renderDetail()
    fireEvent.click(await screen.findByRole('button', { name: /add item/i }))
    fillItemForm()
    fireEvent.click(screen.getByRole('button', { name: /save item/i }))

    // Advisory, so the item was still saved — the caution is shown, not raised.
    expect(
      await screen.findByText(/falls outside the project live period/i),
    ).toBeInTheDocument()
  })

  it('sends only the fields an edit actually changed', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    vi.mocked(updateWorkflowProject).mockResolvedValue({
      ...HALIBA_PROJECT,
      status: 'On Hold',
    })

    renderDetail()
    fireEvent.click(await screen.findByRole('button', { name: /edit project/i }))
    fireEvent.change(screen.getByLabelText('Status'), {
      target: { value: 'On Hold' },
    })
    fireEvent.click(screen.getByRole('button', { name: /save project/i }))

    // Not the whole object: the server reads the body with `exclude_unset`.
    await waitFor(() =>
      expect(updateWorkflowProject).toHaveBeenCalledWith('prj_1', {
        status: 'On Hold',
      }),
    )
  })

  it('renders a refused item delete next to the item that refused it', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR, CABLE] }),
    )
    vi.mocked(deleteWorkflowItem).mockRejectedValue(
      new Error(
        'This item cannot be deleted: it is covered by ADP-RFQ-2026-014. Amend or retender first.',
      ),
    )

    renderDetail()
    fireEvent.click(
      await screen.findByRole('button', { name: 'Delete Gas generator' }),
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Confirm delete of Gas generator' }),
    )

    const row = (await screen.findByText('Gas generator')).closest('tr')!
    expect(within(row).getByText(/ADP-RFQ-2026-014/)).toBeInTheDocument()
    // And nothing was removed from the screen.
    expect(screen.getByText('Gas generator')).toBeInTheDocument()
    // The other row is unaffected — the error is keyed by item id, not shared.
    const other = screen.getByText('HV cable').closest('tr')!
    expect(within(other).queryByText(/ADP-RFQ-2026-014/)).not.toBeInTheDocument()
  })

  it('needs a confirmation before it deletes an item', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    vi.mocked(deleteWorkflowItem).mockResolvedValue(undefined)

    renderDetail()
    fireEvent.click(
      await screen.findByRole('button', { name: 'Delete Gas generator' }),
    )

    expect(deleteWorkflowItem).not.toHaveBeenCalled()

    fireEvent.click(
      screen.getByRole('button', { name: 'Confirm delete of Gas generator' }),
    )
    await waitFor(() =>
      expect(deleteWorkflowItem).toHaveBeenCalledWith('prj_1', 'itm_1'),
    )
  })

  it('states how many items a project delete would take with it', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR, CABLE] }),
    )

    renderDetail()
    fireEvent.click(await screen.findByRole('button', { name: /delete project/i }))

    expect(screen.getByText(/and its 2 items/i)).toBeInTheDocument()
  })

  it('surfaces a refused project delete and stays on the page', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    vi.mocked(deleteWorkflowProject).mockRejectedValue(
      new Error(
        'This project cannot be deleted: it holds ADP-RFQ-2026-014. Delete or retender first.',
      ),
    )
    const { onBack } = renderDetail()

    fireEvent.click(await screen.findByRole('button', { name: /delete project/i }))
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Confirm delete of Haliba Field Development',
      }),
    )

    expect(await screen.findByText(/it holds ADP-RFQ-2026-014/)).toBeInTheDocument()
    expect(onBack).not.toHaveBeenCalled()
  })

  it('returns to the roster after a successful project delete', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [] }))
    vi.mocked(deleteWorkflowProject).mockResolvedValue(undefined)
    const { onBack } = renderDetail()

    fireEvent.click(await screen.findByRole('button', { name: /delete project/i }))
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Confirm delete of Haliba Field Development',
      }),
    )

    await waitFor(() => expect(onBack).toHaveBeenCalled())
  })
  it('raises an RFQ covering the ticked items', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR, CABLE] }),
    )
    vi.mocked(createRfq).mockResolvedValue(rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1']))

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))

    const type = (label: string, value: string) =>
      fireEvent.change(screen.getByLabelText(label), { target: { value } })
    type('Reference', 'ADP-RFQ-2026-014')
    type('Package', 'Power generation')
    type('Discipline', 'Electrical')
    type('Value estimate (AED)', '18000000')
    fireEvent.click(screen.getByRole('button', { name: /create rfq/i }))

    await waitFor(() =>
      expect(createRfq).toHaveBeenCalledWith({
        project_id: 'prj_1',
        item_ids: ['itm_1'],
        reference: 'ADP-RFQ-2026-014',
        package: 'Power generation',
        discipline: 'Electrical',
        value_estimate_aed: 18000000,
      }),
    )
  })

  it('can raise one RFQ spanning several items', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR, CABLE] }),
    )
    vi.mocked(createRfq).mockResolvedValue(
      rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1', 'itm_2']),
    )

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select HV cable' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))

    expect(screen.getByText(/covering 2 items/i)).toBeInTheDocument()
  })

  it('cannot raise an RFQ with nothing selected', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    renderDetail()
    expect(await screen.findByRole('button', { name: /raise rfq/i })).toBeDisabled()
  })

  it("surfaces the server's refusal when an RFQ cannot be raised", async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    vi.mocked(createRfq).mockRejectedValue(
      new Error('Item itm_1 does not belong to project prj_1'),
    )

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))

    const type = (label: string, value: string) =>
      fireEvent.change(screen.getByLabelText(label), { target: { value } })
    type('Reference', 'ADP-RFQ-2026-014')
    type('Package', 'Power generation')
    type('Discipline', 'Electrical')
    type('Value estimate (AED)', '18000000')
    fireEvent.click(screen.getByRole('button', { name: /create rfq/i }))

    expect(await screen.findByText(/does not belong to project/i)).toBeInTheDocument()
  })
  it('clears the live-period caution once the item it described is deleted', async () => {
    // Found by walking the running app: the caution outlived its item and sat
    // there describing something no longer on screen.
    vi.mocked(fetchWorkflowProject)
      .mockResolvedValueOnce(detail({ items: [] }))
      .mockResolvedValue(detail())
    vi.mocked(createWorkflowItem).mockResolvedValue({
      ...GENERATOR,
      required_on_site: '2030-06-01',
      live_period_warning:
        '2030-06-01 falls outside the project live period (2026-01-01 to 2029-12-31)',
    })
    vi.mocked(deleteWorkflowItem).mockResolvedValue(undefined)

    renderDetail()
    fireEvent.click(await screen.findByRole('button', { name: /add item/i }))
    fillItemForm()
    fireEvent.click(screen.getByRole('button', { name: /save item/i }))
    await screen.findByText(/falls outside the project live period/i)

    fireEvent.click(
      await screen.findByRole('button', { name: 'Delete Gas generator' }),
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Confirm delete of Gas generator' }),
    )

    await waitFor(() =>
      expect(
        screen.queryByText(/falls outside the project live period/i),
      ).not.toBeInTheDocument(),
    )
  })
})
