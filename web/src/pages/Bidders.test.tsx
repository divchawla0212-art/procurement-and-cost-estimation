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
      target: { value: 'VALVES - BALL, FLANGES FOR PIPES ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create bidder' }))

    await waitFor(() => expect(createBidder).toHaveBeenCalled())
    expect(vi.mocked(createBidder).mock.calls[0][0]).toMatchObject({
      name: 'Northwind Valve Works',
      country: 'Oman',
      // Split and trimmed, so the form's comma-separated field does not store
      // " FLANGES" as a category that will never match an RFQ's discipline.
      trade_categories: ['VALVES - BALL', 'FLANGES FOR PIPES'],
      // A bidder added by hand is on our own list, never claimed as the
      // client's.
      approved_by: ['Astra'],
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

  it('shows which organisations have approved a bidder', async () => {
    vi.mocked(fetchBidders).mockResolvedValue([APPROVED_BIDDER, EXPIRED_BIDDER])

    render(<Bidders />)

    const munara = (await screen.findByText('Al Munara Switchgear LLC')).closest(
      'article',
    )!
    expect(within(munara).getByText('ADNOC')).toBeTruthy()
    expect(within(munara).getByText('Astra')).toBeTruthy()

    const sandstone = screen.getByText('Sandstone Piping Industries').closest(
      'article',
    )!
    expect(within(sandstone).queryByText('Astra')).toBeNull()
  })

  it('filters the registry to one approving organisation', async () => {
    vi.mocked(fetchBidders).mockResolvedValue([APPROVED_BIDDER, EXPIRED_BIDDER])

    render(<Bidders />)
    await screen.findByText('Al Munara Switchgear LLC')

    fireEvent.change(screen.getByLabelText('Approved by'), {
      target: { value: 'Astra' },
    })

    expect(screen.getByText('Al Munara Switchgear LLC')).toBeTruthy()
    expect(screen.queryByText('Sandstone Piping Industries')).toBeNull()
    expect(screen.getByText(/1 of 2 bidders match/)).toBeTruthy()
  })

  it('searches names, trade categories and the manufacturers represented', async () => {
    vi.mocked(fetchBidders).mockResolvedValue([APPROVED_BIDDER, EXPIRED_BIDDER])

    render(<Bidders />)
    await screen.findByText('Al Munara Switchgear LLC')
    const search = screen.getByLabelText('Search bidders')

    // A product group the reader would type from the RFQ in front of them.
    fireEvent.change(search, { target: { value: 'FLANGES' } })
    expect(screen.queryByText('Al Munara Switchgear LLC')).toBeNull()
    expect(screen.getByText('Sandstone Piping Industries')).toBeTruthy()

    // "Who can supply Schneider" is the question a buyer actually arrives
    // with, and the answer is in the manufacturer list, not the vendor name.
    fireEvent.change(search, { target: { value: 'schneider' } })
    expect(screen.getByText('Al Munara Switchgear LLC')).toBeTruthy()
    expect(screen.queryByText('Sandstone Piping Industries')).toBeNull()
  })

  it('caps how many cards it renders and says that it has', async () => {
    // An imported ADNOC AVL is about 1 300 bidders. Rendering all of them is
    // slow; rendering the first 60 silently would misrepresent the registry.
    vi.mocked(fetchBidders).mockResolvedValue(
      Array.from({ length: 80 }, (_, i) => ({
        ...APPROVED_BIDDER,
        id: `bdr_${i}`,
        name: `Vendor ${String(i).padStart(3, '0')}`,
      })),
    )

    render(<Bidders />)

    await screen.findByText('Vendor 000')
    expect(screen.getByText(/Showing the first 60/)).toBeTruthy()
    expect(screen.queryByText('Vendor 070')).toBeNull()
  })

  it('offers a way forward when a search matches nothing', async () => {
    vi.mocked(fetchBidders).mockResolvedValue([APPROVED_BIDDER])

    render(<Bidders />)
    await screen.findByText('Al Munara Switchgear LLC')

    fireEvent.change(screen.getByLabelText('Search bidders'), {
      target: { value: 'zzzz' },
    })

    expect(screen.getByText(/Nothing matches that search/)).toBeTruthy()
  })

  it('renders the server sentence when a bidder is off the client list', async () => {
    // Rendered verbatim. Rebuilding the sentence here from `approved_by` would
    // be a second definition of the rule, and the two would drift.
    vi.mocked(fetchBidders).mockResolvedValue([
      {
        ...APPROVED_BIDDER,
        approved_by: ['Astra'],
        approval_caution:
          'Al Munara Switchgear LLC is not on the ADNOC Approved Vendor List — approved by Astra only.',
      },
    ])

    render(<Bidders />)

    expect(
      await screen.findByText(/not on the ADNOC Approved Vendor List/),
    ).toBeTruthy()
  })

  it('says nothing when the bidder is on the client list', async () => {
    vi.mocked(fetchBidders).mockResolvedValue([APPROVED_BIDDER])

    render(<Bidders />)

    await screen.findByText('Al Munara Switchgear LLC')
    expect(screen.queryByText(/Approved Vendor List/)).toBeNull()
  })

  it('reports a country the registry does not hold rather than inventing one', async () => {
    // AVL-imported bidders have no country: the export only carries the
    // manufacturer's.
    vi.mocked(fetchBidders).mockResolvedValue([{ ...APPROVED_BIDDER, country: null }])

    render(<Bidders />)

    const card = (await screen.findByText('Al Munara Switchgear LLC')).closest(
      'article',
    )!
    expect(within(card).getByText('—')).toBeTruthy()
  })
})
