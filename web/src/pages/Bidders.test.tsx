import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { Bidders } from './Bidders'
import { APPROVED_BIDDER, EXPIRED_BIDDER, SUSPENDED_BIDDER } from './workflow-fixtures'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    fetchBidders: vi.fn(),
    createBidder: vi.fn(),
    updateBidder: vi.fn(),
    deleteBidder: vi.fn(),
  }
})

import { createBidder, deleteBidder, fetchBidders, updateBidder } from '../api'

describe('Bidders', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('gives each bidder a card headed by their name', async () => {
    vi.mocked(fetchBidders).mockResolvedValue([APPROVED_BIDDER, EXPIRED_BIDDER])

    render(<Bidders />)

    await screen.findByText('Al Munara Switchgear LLC')
    const headings = screen.getAllByRole('heading', { level: 3 })
    expect(headings.map((h) => h.textContent)).toEqual([
      'Al Munara Switchgear LLC',
      'Sandstone Piping Industries',
    ])
  })

  it('shows the derived status, not the stored one', async () => {
    // The server says the stored status is still "Approved" and the effective
    // one is "Expired". Showing the stored one would tell the reader a lapsed
    // bidder is fine to invite.
    vi.mocked(fetchBidders).mockResolvedValue([EXPIRED_BIDDER])

    render(<Bidders />)

    const card = (await screen.findByText('Sandstone Piping Industries')).closest(
      'article',
    )!
    expect(within(card).getByText('Expired')).toBeTruthy()
    expect(within(card).queryByText('Approved')).toBeNull()
  })

  it('shows the hold reason next to a bidder who is on hold', async () => {
    vi.mocked(fetchBidders).mockResolvedValue([
      { ...SUSPENDED_BIDDER, on_hold: true, hold_reason: 'Dispute on HAL-19' },
    ])

    render(<Bidders />)

    expect(await screen.findByText(/Dispute on HAL-19/)).toBeTruthy()
  })

  it('says how many RFQs have invited a bidder', async () => {
    vi.mocked(fetchBidders).mockResolvedValue([
      { ...APPROVED_BIDDER, invited_count: 3 },
    ])

    render(<Bidders />)

    // The number sits in its own <b> so it reads at a glance, which splits the
    // text node — hence matching on the card rather than on a string.
    const card = (await screen.findByText('Al Munara Switchgear LLC')).closest(
      'article',
    )!
    expect(card.textContent).toContain('3')
    expect(card.textContent).toContain('RFQs')
  })

  it('offers an empty state rather than a bare table when the registry is new', async () => {
    vi.mocked(fetchBidders).mockResolvedValue([])

    render(<Bidders />)

    expect(await screen.findByText(/No bidders yet/)).toBeTruthy()
  })

  it('creates a bidder from the form and reloads the roster', async () => {
    vi.mocked(fetchBidders).mockResolvedValue([])
    vi.mocked(createBidder).mockResolvedValue({ ...APPROVED_BIDDER })

    render(<Bidders />)

    fireEvent.click(await screen.findByRole('button', { name: 'New bidder' }))
    fireEvent.change(screen.getByLabelText('Name'), {
      target: { value: 'Northwind Valve Works' },
    })
    fireEvent.change(screen.getByLabelText('Country'), { target: { value: 'Oman' } })
    fireEvent.change(screen.getByLabelText('Trade categories'), {
      target: { value: 'Valves, Piping' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create bidder' }))

    await waitFor(() => expect(createBidder).toHaveBeenCalled())
    expect(vi.mocked(createBidder).mock.calls[0][0]).toMatchObject({
      name: 'Northwind Valve Works',
      country: 'Oman',
      // Split and trimmed, so the form's comma-separated field does not store
      // " Piping" as a category that will never match an RFQ's discipline.
      trade_categories: ['Valves', 'Piping'],
    })
    // The roster refetches, so a bidder added here is visible without a
    // reload — the counts on it are server-computed and cannot be patched in.
    await waitFor(() => expect(fetchBidders).toHaveBeenCalledTimes(2))
  })

  it('sends only what changed when a prequalification is updated', async () => {
    vi.mocked(fetchBidders).mockResolvedValue([APPROVED_BIDDER])
    vi.mocked(updateBidder).mockResolvedValue({ ...APPROVED_BIDDER })

    render(<Bidders />)

    fireEvent.click(await screen.findByRole('button', { name: /Suspend/ }))

    await waitFor(() => expect(updateBidder).toHaveBeenCalled())
    expect(vi.mocked(updateBidder).mock.calls[0][1]).toEqual({
      prequal_status: 'Suspended',
    })
  })

  it("surfaces the server's refusal when a shortlisted bidder cannot be deleted", async () => {
    vi.mocked(fetchBidders).mockResolvedValue([APPROVED_BIDDER])
    vi.mocked(deleteBidder).mockRejectedValue(
      new Error(
        'This bidder cannot be deleted: they are shortlisted on HAL-RFQ-2026-002.',
      ),
    )

    render(<Bidders />)

    fireEvent.click(await screen.findByRole('button', { name: /Delete/ }))

    // The control is not pre-disabled. A disabled button explains nothing;
    // the server's own sentence names the RFQ that blocks it.
    expect(
      await screen.findByText(/shortlisted on HAL-RFQ-2026-002/),
    ).toBeTruthy()
  })
})
