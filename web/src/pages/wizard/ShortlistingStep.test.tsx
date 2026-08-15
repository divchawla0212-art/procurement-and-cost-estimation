import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { ShortlistingStep } from './ShortlistingStep'
import type { RfqDetail, ShortlistEntry } from '../../types'

vi.mock('../../api', () => ({
  addShortlistEntry: vi.fn().mockResolvedValue({}),
  approveShortlist: vi.fn().mockResolvedValue({}),
  inviteRegisteredBidder: vi.fn().mockResolvedValue({}),
  removeShortlistEntry: vi.fn().mockResolvedValue({}),
  setTbeTemplate: vi.fn().mockResolvedValue({}),
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
    stage: 'Shortlisting',
    history: [],
  },
  gate: { passed: true, reason: null },
  technical_package: null,
  shortlist: [],
  shortlist_approved: false,
  client_approver: 'ADNOC',
  tbe_template: null,
  vdrl: [],
  bids: [],
  bid_selection: null,
  queries: [],
  addenda: [],
  bid_due_date: null,
}

const entry = (over: Partial<ShortlistEntry> = {}): ShortlistEntry => ({
  id: 'sle_1',
  rfq_id: 'rfq_1',
  vendor_id: 'bdr_1',
  vendor_name: 'Al Munara Switchgear LLC',
  prequal_status: 'Approved',
  scope_code_fit: true,
  included: true,
  client_approved: true,
  approved_by: ['ADNOC', 'Astra'],
  override_by: null,
  override_reason: null,
  ...over,
})

function step(shortlist: ShortlistEntry[]) {
  return render(
    <ShortlistingStep
      data={{ ...BASE, shortlist }}
      run={vi.fn()}
      busy={false}
      tick={0}
    />,
  )
}

describe('ShortlistingStep approvals column', () => {
  it('shows a pill for each approver on the row', async () => {
    step([entry()])

    expect(await screen.findByText('ADNOC')).toHaveClass('approval-badge--adnoc')
    expect(screen.getByText('Astra')).toHaveClass('approval-badge--astra')
  })

  it('shows our own approval and the client gap at the same time', async () => {
    // The load-bearing case. Both facts are true, and the pill alone would let
    // our own qualification read as clearance we do not have.
    step([entry({ approved_by: ['Astra'], client_approved: false })])

    const row = await screen.findByRole('row', { name: /Al Munara/ })
    expect(within(row).getByText('Astra')).toHaveClass('approval-badge--astra')
    expect(within(row).getByText(/Not on the ADNOC list/i)).toBeInTheDocument()
  })

  it('reports a hand-typed vendor as unchecked, not as unapproved', async () => {
    // `null` is "we cannot tell": there is no registry row, so rendering "not
    // on the list" would report a check nobody ran.
    step([entry({ vendor_id: null, approved_by: null, client_approved: null })])

    const row = await screen.findByRole('row', { name: /Al Munara/ })
    expect(within(row).getByText(/Not checked/i)).toBeInTheDocument()
    expect(within(row).queryByText(/Not on the ADNOC list/i)).toBeNull()
  })

  it('reports a registry vendor nobody approved as off the list', async () => {
    // `[]` is a real answer, unlike `null`: the row exists and carries no
    // approval.
    step([entry({ approved_by: [], client_approved: false })])

    const row = await screen.findByRole('row', { name: /Al Munara/ })
    expect(within(row).getByText(/Not on the ADNOC list/i)).toBeInTheDocument()
  })
})

// BD-5: who is invited now comes from the item's own draft shortlist, filled
// on the item screen before an RFQ exists at all — see `AvailableVendorList`
// in `ItemDetail.tsx`. The Registry search-and-invite affordance this step
// used to carry duplicated that, on a narrower list (the registry alone,
// never the four-source pool), so it comes out rather than staying as a
// second door to the same act.
describe('ShortlistingStep vendor selection', () => {
  it('has no Registry section', () => {
    step([entry()])

    expect(screen.queryByRole('heading', { name: 'Registry' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Search candidates')).not.toBeInTheDocument()
    expect(
      screen.queryByLabelText(/Only those registered for/),
    ).not.toBeInTheDocument()
  })

  it('keeps the escape hatch for a vendor nobody registered', () => {
    step([entry()])

    expect(
      screen.getByText(/Add a vendor who is not in the registry/i),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Vendor name')).toBeInTheDocument()
  })

  // `_shortlisting_exit` (workflow/gates.py) checks included vendor, then
  // approval, then the TBE template, in that order. The approval control used
  // to render after the whole registry section, at the bottom of the vendor
  // half of the step; it now leads the step so a top-to-bottom reader meets it
  // before the table it approves.
  it('renders the approval control before the invited-bidders table', () => {
    step([entry()])

    const button = screen.getByRole('button', { name: 'Approve shortlist' })
    const table = screen.getByRole('table')
    expect(
      button.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })
})
