import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SendEnquiry } from './SendEnquiry'
import { previewEnquiry, sendEnquiry } from '../../api'
import type { RfqDetail } from '../../types'

vi.mock('../../api', () => ({
  previewEnquiry: vi.fn(),
  sendEnquiry: vi.fn(),
}))

const BASE: RfqDetail = {
  rfq: {
    id: 'rfq_1',
    reference: 'ADP-RFQ-2026-014',
    project_id: 'prj_1',
    item_ids: [],
    package: 'LV switchgear',
    discipline: 'Electrical',
    value_estimate_aed: 4_000_000,
    stage: 'Issued',
    history: [],
  },
  gate: { passed: true, reason: null },
  technical_package: null,
  shortlist: [],
  shortlist_approved: false,
  client_approver: 'ADNOC',
  documents: [],
  document_categories: [],
  tbe_template: null,
  vdrl: [],
  bids: [],
  bid_selection: null,
  queries: [],
  addenda: [],
  bid_due_date: null,
}

/** `run` is the wizard's own wrapper. Awaiting the action here rather than
 *  stubbing it away keeps the assertion on what was *sent*, which is the only
 *  thing this component decides. */
const run = (action: () => Promise<unknown>) => action().then(() => undefined)

function props() {
  return { data: BASE, run: vi.fn(run), busy: false }
}

// Without this, `toHaveBeenCalledWith` (or a resolved value from an earlier
// test) can leak into a later assertion — the exact trap this repository has
// already shipped a broken component behind once.
beforeEach(() => vi.clearAllMocks())

describe('SendEnquiry', () => {
  it('offers no send control before a preview has been fetched', () => {
    render(<SendEnquiry {...props()} />)
    expect(screen.queryByRole('button', { name: /^Send/ })).not.toBeInTheDocument()
  })

  it('lists every resolved address before sending', async () => {
    vi.mocked(previewEnquiry).mockResolvedValue({
      transport: 'outbox',
      recipients: [
        { shortlist_entry_id: 'sle_1', vendor_name: 'Galfar', to: ['sales@galfar.example'], skip_reason: null },
      ],
    })
    render(<SendEnquiry {...props()} />)

    fireEvent.click(screen.getByRole('button', { name: /Preview recipients/ }))

    expect(await screen.findByText('sales@galfar.example')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Send/ })).toBeInTheDocument()
  })

  it('says the outbox and real mail in different words', async () => {
    // The two must not read identically — that is how a real tender goes out
    // during a demo.
    vi.mocked(previewEnquiry).mockResolvedValue({ transport: 'smtp', recipients: [] })
    render(<SendEnquiry {...props()} />)
    fireEvent.click(screen.getByRole('button', { name: /Preview recipients/ }))
    expect(await screen.findByText(/real email/i)).toBeInTheDocument()
  })

  it('says the outbox in different words from real mail', async () => {
    vi.mocked(previewEnquiry).mockResolvedValue({ transport: 'outbox', recipients: [] })
    render(<SendEnquiry {...props()} />)
    fireEvent.click(screen.getByRole('button', { name: /Preview recipients/ }))
    expect(await screen.findByText(/outbox/i)).toBeInTheDocument()
    expect(screen.queryByText(/real email/i)).not.toBeInTheDocument()
  })

  it('renders every skipped vendor with its reason, not a count', async () => {
    vi.mocked(previewEnquiry).mockResolvedValue({
      transport: 'outbox',
      recipients: [
        { shortlist_entry_id: 'sle_1', vendor_name: 'Nowhere Trading', to: null, skip_reason: 'No address on file for Nowhere Trading.' },
      ],
    })
    vi.mocked(sendEnquiry).mockResolvedValue({
      sent: [],
      skipped: [{ vendor_name: 'Nowhere Trading', reason: 'No address on file for Nowhere Trading.' }],
    })
    render(<SendEnquiry {...props()} />)

    fireEvent.click(screen.getByRole('button', { name: /Preview recipients/ }))
    await screen.findByText('Nowhere Trading')
    fireEvent.click(screen.getByRole('button', { name: /^Send/ }))

    expect(await screen.findByText(/No address on file for Nowhere Trading/)).toBeInTheDocument()
  })

  it('calls the two routes with the RFQ id', async () => {
    vi.mocked(previewEnquiry).mockResolvedValue({ transport: 'outbox', recipients: [] })
    vi.mocked(sendEnquiry).mockResolvedValue({ sent: [], skipped: [] })
    render(<SendEnquiry {...props()} />)

    fireEvent.click(screen.getByRole('button', { name: /Preview recipients/ }))
    await waitFor(() => expect(previewEnquiry).toHaveBeenCalledWith('rfq_1'))

    fireEvent.click(await screen.findByRole('button', { name: /^Send/ }))
    await waitFor(() => expect(sendEnquiry).toHaveBeenCalledWith('rfq_1'))
  })
})
