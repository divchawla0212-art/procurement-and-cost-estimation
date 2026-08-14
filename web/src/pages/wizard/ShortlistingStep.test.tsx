import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { ShortlistingStep } from './ShortlistingStep'
import type { RfqDetail, ShortlistEntry } from '../../types'

vi.mock('../../api', () => ({
  addShortlistEntry: vi.fn().mockResolvedValue({}),
  approveShortlist: vi.fn().mockResolvedValue({}),
  // The step loads the candidate list on mount; every test here is about the
  // invited table above it, so an empty registry keeps that half quiet.
  fetchCandidates: vi.fn().mockResolvedValue([]),
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
