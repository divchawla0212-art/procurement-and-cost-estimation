import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ItemDetail } from './ItemDetail'
import { CABLE, GENERATOR, HALIBA_PROJECT, detail, rfq } from './workflow-fixtures'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    fetchWorkflowProject: vi.fn(),
    updateWorkflowItem: vi.fn(),
  }
})

import { fetchWorkflowProject, updateWorkflowItem } from '../api'

function renderItem(itemId = 'itm_1', onBack = vi.fn()) {
  render(<ItemDetail projectId="prj_1" itemId={itemId} onBack={onBack} />)
  return { onBack }
}

describe('ItemDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows the item and only the RFQs covering it', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({
        items: [GENERATOR, CABLE],
        rfqs: [
          rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1']),
          rfq('rfq_2', 'ADP-RFQ-2026-015', ['itm_2']),
        ],
      }),
    )

    renderItem()

    expect(await screen.findByText('Gas generator')).toBeInTheDocument()
    expect(screen.getByText('ADP-RFQ-2026-014')).toBeInTheDocument()
    // The second RFQ covers the cable, not this item.
    expect(screen.queryByText('ADP-RFQ-2026-015')).not.toBeInTheDocument()
  })

  it('counts an RFQ that spans several items as covering each of them', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({
        items: [GENERATOR, CABLE],
        rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1', 'itm_2'])],
      }),
    )

    renderItem('itm_2')

    expect(await screen.findByText('ADP-RFQ-2026-014')).toBeInTheDocument()
  })

  it('says so when no RFQ covers the item', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    renderItem()
    expect(
      await screen.findByText(/No RFQ covers this item yet/i),
    ).toBeInTheDocument()
  })

  it('handles a stale item id without crashing', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [] }))
    renderItem('itm_missing')
    expect(await screen.findByText(/could not be found/i)).toBeInTheDocument()
  })

  it('sends only the fields an edit actually changed', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    vi.mocked(updateWorkflowItem).mockResolvedValue({
      ...GENERATOR,
      qty: 3,
      live_period_warning: null,
    })

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /edit item/i }))
    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '3' } })
    fireEvent.click(screen.getByRole('button', { name: /save item/i }))

    await waitFor(() =>
      expect(updateWorkflowItem).toHaveBeenCalledWith('prj_1', 'itm_1', { qty: 3 }),
    )
  })

  it('shows the live-period caution returned by an edit', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    vi.mocked(updateWorkflowItem).mockResolvedValue({
      ...GENERATOR,
      required_on_site: '2030-06-01',
      live_period_warning:
        '2030-06-01 falls outside the project live period (2026-01-01 to 2029-12-31)',
    })

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /edit item/i }))
    fireEvent.change(screen.getByLabelText('Required on site'), {
      target: { value: '2030-06-01' },
    })
    fireEvent.click(screen.getByRole('button', { name: /save item/i }))

    expect(
      await screen.findByText(/falls outside the project live period/i),
    ).toBeInTheDocument()
  })

  it('names the project it belongs to', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    renderItem()
    expect(
      await screen.findByText(new RegExp(HALIBA_PROJECT.name)),
    ).toBeInTheDocument()
  })
})
