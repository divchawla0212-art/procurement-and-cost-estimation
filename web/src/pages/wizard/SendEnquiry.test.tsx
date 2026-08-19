import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SendEnquiry } from './SendEnquiry'
import type { EnquiryPreview } from '../../types'
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
  eligibility_checklist: [],
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
      audience: 'unsent',
      audiences: ['unsent', 'outdated', 'all'],
    })
    render(<SendEnquiry {...props()} />)

    fireEvent.click(screen.getByRole('button', { name: /Preview recipients/ }))

    expect(await screen.findByText('sales@galfar.example')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Send/ })).toBeInTheDocument()
  })

  it('says the outbox and real mail in different words', async () => {
    // The two must not read identically — that is how a real tender goes out
    // during a demo.
    vi.mocked(previewEnquiry).mockResolvedValue({ transport: 'smtp', recipients: [] , audience: 'unsent', audiences: ['unsent', 'outdated', 'all'] })
    render(<SendEnquiry {...props()} />)
    fireEvent.click(screen.getByRole('button', { name: /Preview recipients/ }))
    expect(await screen.findByText(/real email/i)).toBeInTheDocument()
  })

  it('says the outbox in different words from real mail', async () => {
    vi.mocked(previewEnquiry).mockResolvedValue({ transport: 'outbox', recipients: [], audience: 'unsent', audiences: ['unsent', 'outdated', 'all'] })
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
      audience: 'unsent',
      audiences: ['unsent', 'outdated', 'all'],
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
    vi.mocked(previewEnquiry).mockResolvedValue({ transport: 'outbox', recipients: [], audience: 'unsent', audiences: ['unsent', 'outdated', 'all'] })
    vi.mocked(sendEnquiry).mockResolvedValue({ sent: [], skipped: [] })
    render(<SendEnquiry {...props()} />)

    fireEvent.click(screen.getByRole('button', { name: /Preview recipients/ }))
    // Both routes now carry the resend flag, and both default to `false` --
    // asserted explicitly rather than loosely, so a default flipping to `true`
    // fails here instead of quietly re-mailing a shortlist.
    await waitFor(() => expect(previewEnquiry).toHaveBeenCalledWith('rfq_1', 'unsent'))

    fireEvent.click(await screen.findByRole('button', { name: /^Send/ }))
    await waitFor(() => expect(sendEnquiry).toHaveBeenCalledWith('rfq_1', 'unsent'))
  })
})

describe('SendEnquiry audience', () => {
  const recipient = {
    shortlist_entry_id: 'sle_1',
    vendor_name: 'Galfar',
    to: ['sales@galfar.example'],
    skip_reason: null,
  }

  const preview = (over: Partial<EnquiryPreview> = {}): EnquiryPreview => ({
    transport: 'outbox',
    recipients: [recipient],
    audience: 'unsent',
    audiences: ['unsent', 'outdated', 'all'],
    ...over,
  })

  it('previews the vendors not yet sent to by default', async () => {
    vi.mocked(previewEnquiry).mockResolvedValue(preview({ recipients: [] }))
    render(<SendEnquiry {...props()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Preview recipients' }))

    await waitFor(() => expect(previewEnquiry).toHaveBeenCalledWith('rfq_1', 'unsent'))
  })

  it('offers the three audiences without spelling them itself', () => {
    render(<SendEnquiry {...props()} />)

    const picker = screen.getByRole('combobox', { name: /Send to/ })
    expect([...picker.querySelectorAll('option')].map((o) => o.value)).toEqual([
      'unsent',
      'outdated',
      'all',
    ])
  })

  // **The load-bearing one, and it is a regression test.** The control used to
  // render only before a preview, so the moment a buyer saw "everyone has
  // already been sent" -- exactly when they need to widen the audience -- the
  // option had gone and only a page reload brought it back.
  it('keeps the audience control on screen once a preview is up', async () => {
    vi.mocked(previewEnquiry).mockResolvedValue(preview())
    render(<SendEnquiry {...props()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Preview recipients' }))
    await screen.findByRole('button', { name: /Send to 1 vendors/ })

    expect(screen.getByRole('combobox', { name: /Send to/ })).toBeInTheDocument()
  })

  it('re-previews immediately when the audience changes', async () => {
    vi.mocked(previewEnquiry).mockResolvedValue(preview())
    render(<SendEnquiry {...props()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Preview recipients' }))
    await screen.findByRole('button', { name: /Send to 1 vendors/ })

    vi.mocked(previewEnquiry).mockResolvedValue(preview({ audience: 'outdated' }))
    fireEvent.change(screen.getByRole('combobox', { name: /Send to/ }), {
      target: { value: 'outdated' },
    })

    await waitFor(() =>
      expect(previewEnquiry).toHaveBeenLastCalledWith('rfq_1', 'outdated'),
    )
  })

  // The table on screen and the mail that goes out must describe one action.
  it('sends the audience the preview on screen was built with', async () => {
    vi.mocked(previewEnquiry).mockResolvedValue(preview({ audience: 'all' }))
    render(<SendEnquiry {...props()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Preview recipients' }))
    fireEvent.click(await screen.findByRole('button', { name: /Send again to 1 vendors/ }))

    await waitFor(() => expect(sendEnquiry).toHaveBeenCalledWith('rfq_1', 'all'))
  })

  it('says "Send again" only when the audience is wider than unsent', async () => {
    vi.mocked(previewEnquiry).mockResolvedValue(preview())
    render(<SendEnquiry {...props()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Preview recipients' }))

    expect(await screen.findByRole('button', { name: /^Send to 1 vendors/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Send again/ })).toBeNull()
  })
})
