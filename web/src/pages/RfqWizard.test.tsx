import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { RfqWizard } from './RfqWizard'
import type { Candidate, RfqDetail, ShortlistEntry } from '../types'
import { APPROVED_BIDDER, EXPIRED_BIDDER } from './workflow-fixtures'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    fetchRfq: vi.fn(),
    transitionRfq: vi.fn(),
    freezeTechnicalPackage: vi.fn(),
    addShortlistEntry: vi.fn(),
    inviteRegisteredBidder: vi.fn(),
    fetchCandidates: vi.fn(),
    removeShortlistEntry: vi.fn(),
    approveShortlist: vi.fn(),
    setTbeTemplate: vi.fn(),
    addVdrlLine: vi.fn(),
    removeVdrlLine: vi.fn(),
  }
})

import {
  addShortlistEntry,
  approveShortlist,
  fetchCandidates,
  fetchRfq,
  freezeTechnicalPackage,
  inviteRegisteredBidder,
  removeShortlistEntry,
  transitionRfq,
} from '../api'

/** A shortlist entry as the server returns it once a registry bidder has been
 *  invited: the name and prequal status are the registry's, not the caller's. */
function entry(over: Partial<ShortlistEntry> = {}): ShortlistEntry {
  return {
    id: 'sle_1',
    rfq_id: 'rfq_abc',
    vendor_id: 'bdr_almunara',
    vendor_name: 'Al Munara Switchgear LLC',
    prequal_status: 'Approved',
    scope_code_fit: true,
    included: true,
    client_approved: true,
    approved_by: ['ADNOC', 'Astra'],
    override_by: null,
    override_reason: null,
    ...over,
  }
}

function candidate(over: Partial<Candidate> = {}): Candidate {
  return {
    bidder: APPROVED_BIDDER,
    suitability: {
      eligible: true,
      scope_fit: true,
      effective_prequal: 'Approved',
      blockers: [],
      cautions: [],
    },
    shortlisted: false,
    ...over,
  }
}

/** Blocked by a lapsed prequalification, but *in* scope — so it survives the
 *  candidate list's default "only those registered for this discipline"
 *  filter, and each test below is about one thing. */
const BLOCKED_CANDIDATE: Candidate = {
  bidder: EXPIRED_BIDDER,
  suitability: {
    eligible: false,
    scope_fit: true,
    effective_prequal: 'Expired',
    blockers: [
      'Prequalification lapsed on 2026-05-09 and must be renewed before Sandstone Piping Industries can be invited.',
    ],
    cautions: [],
  },
  shortlisted: false,
}

/** Approved, but not listed for this RFQ's discipline. A caution, never a
 *  blocker — and hidden by the default filter until it is unticked. */
const OUT_OF_SCOPE_CANDIDATE: Candidate = {
  bidder: { ...APPROVED_BIDDER, id: 'bdr_offscope', name: 'Blue Harbour Marine Services' },
  suitability: {
    eligible: true,
    scope_fit: false,
    effective_prequal: 'Approved',
    blockers: [],
    cautions: ['Not registered for Mechanical — inviting them records a scope mismatch.'],
  },
  shortlisted: false,
}

const STAGES = [
  'Shortlisting',
  'Issued',
  'Clarifications',
  'Bids Received',
  'Evaluation',
  'Negotiation',
  'Awarded',
  'PO Issued',
]

function detail(over: Partial<RfqDetail> = {}): RfqDetail {
  return {
    rfq: {
      id: 'rfq_abc',
      reference: 'ADP-RFQ-2026-014',
      project_id: 'prj_1',
      item_ids: ['itm_1'],
      package: 'Wellhead tie-in materials',
      discipline: 'Mechanical',
      value_estimate_aed: 46200000,
      stage: 'Shortlisting',
      history: [
        { from_stage: null, to_stage: 'Shortlisting', at: '2026-08-13T09:00:00Z', by: 'system', reason: 'RFQ created' },
      ],
    },
    gate: { passed: false, reason: 'The shortlist contains no included vendors.' },
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
    ...over,
  }
}

async function show(data: RfqDetail, candidates: Candidate[] = []) {
  vi.mocked(fetchRfq).mockResolvedValue(data)
  vi.mocked(fetchCandidates).mockResolvedValue(candidates)
  render(<RfqWizard rfqId="rfq_abc" stages={STAGES} onBack={() => {}} />)
  await waitFor(() => {
    expect(screen.queryByText(/Loading RFQ…/i)).not.toBeInTheDocument()
  })
}

describe('RfqWizard', () => {
  it('shows the three pre-bid steps and opens the RFQ’s current one', async () => {
    await show(detail())
    const steps = screen.getAllByRole('listitem').map((li) => li.textContent)
    expect(steps.some((t) => t?.includes('Shortlisting'))).toBe(true)
    expect(steps.some((t) => t?.includes('Clarifications'))).toBe(true)
    // Scoping was removed from the process; it must not come back as a step.
    expect(steps.some((t) => t?.includes('Scoping'))).toBe(false)
    // The Shortlisting editor is the one on screen — an RFQ starts here now.
    expect(screen.getByRole('button', { name: 'Approve shortlist' })).toBeInTheDocument()
  })

  it('offers a web vendor search that is not built yet', async () => {
    await show(detail())

    const button = screen.getByRole('button', {
      name: /search the web for similar vendors/i,
    })
    // Disabled *and* captioned. This screen's rule is that a disabled control
    // states its reason — the stage button is enabled precisely because its
    // refusal is information, and there is nothing behind this one at all.
    expect(button).toBeDisabled()
    expect(screen.getByText('Not built yet.')).toBeInTheDocument()
  })

  it('says what is blocking the step, in the gate’s own words', async () => {
    await show(detail())
    expect(
      screen.getByText(/The shortlist contains no included vendors/),
    ).toBeInTheDocument()
  })

  it('ticks a step off by transitioning to the next stage', async () => {
    vi.mocked(transitionRfq).mockResolvedValue(detail().rfq)
    await show(detail({ gate: { passed: true, reason: null } }))

    fireEvent.click(screen.getByRole('button', { name: /Mark Shortlisting complete/ }))

    await waitFor(() => {
      expect(transitionRfq).toHaveBeenCalledWith('rfq_abc', 'Issued')
    })
  })

  it('surfaces the server’s refusal when a closed gate rejects the step', async () => {
    // The button stays enabled on a closed gate on purpose: a disabled control
    // explains nothing, and the gate's sentence is the explanation.
    vi.mocked(transitionRfq).mockRejectedValue(
      new Error('The shortlist must be approved by procurement before issuance.'),
    )
    await show(detail())

    fireEvent.click(screen.getByRole('button', { name: /Mark Shortlisting complete/ }))

    await waitFor(() => {
      expect(
        screen.getByText(/The shortlist must be approved by procurement/),
      ).toBeInTheDocument()
    })
  })

  it('lets you open an earlier step and keep editing it', async () => {
    await show(
      detail({
        rfq: { ...detail().rfq, stage: 'Issued' },
        technical_package: {
          rfq_id: 'rfq_abc',
          revision: 'Rev. B',
          basis_of_design: 'basis',
          attachments: [],
          frozen_at: '2026-08-13T10:00:00Z',
          frozen_by: 'lead@adp.ae',
        },
        shortlist: [entry()],
      }),
      [candidate({ shortlisted: true })],
    )

    fireEvent.click(screen.getByRole('button', { name: /Shortlisting/ }))

    expect(screen.getByText(/This step is complete\. You can still edit it/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove' })).toBeInTheDocument()
  })

  it('invites a bidder from the registry, sending only the id', async () => {
    // Not the name, prequal status or scope fit: those come from the registry,
    // and a screen that sent them would be claiming they were its to decide.
    vi.mocked(inviteRegisteredBidder).mockResolvedValue(entry())
    await show(detail({ rfq: { ...detail().rfq, stage: 'Shortlisting' } }), [
      candidate(),
    ])

    fireEvent.click(await screen.findByRole('button', { name: /Invite Al Munara/ }))

    await waitFor(() =>
      expect(inviteRegisteredBidder).toHaveBeenCalledWith('rfq_abc', {
        vendor_id: 'bdr_almunara',
      }),
    )
  })

  it('shows a blocked candidate\u2019s blockers rather than hiding the control', async () => {
    await show(detail({ rfq: { ...detail().rfq, stage: 'Shortlisting' } }), [
      BLOCKED_CANDIDATE,
    ])

    expect(
      await screen.findByText(/Prequalification lapsed on 2026-05-09/),
    ).toBeInTheDocument()
    // Still invitable — with a reason. Hiding the control would move the
    // decision to a spreadsheet rather than prevent it.
    expect(screen.getByRole('button', { name: /Invite Sandstone/ })).toBeInTheDocument()
  })

  it('hides out-of-scope candidates behind a filter that says what it is doing', async () => {
    // A client's imported AVL is the whole approved list; on a common product
    // group most of it is irrelevant to this package. The filter is on by
    // default and the count says how much it is holding back.
    await show(detail({ rfq: { ...detail().rfq, stage: 'Shortlisting' } }), [
      candidate(),
      OUT_OF_SCOPE_CANDIDATE,
    ])

    // A candidate's name appears twice — as the heading and inside its Invite
    // button — so these count matches rather than expecting exactly one.
    await screen.findAllByText(/Al Munara Switchgear LLC/)
    expect(screen.queryAllByText(/Blue Harbour Marine Services/)).toHaveLength(0)
    expect(screen.getByText(/1 of 2 in the registry/)).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText(/Only those registered for/))

    expect(screen.getAllByText(/Blue Harbour Marine Services/).length).toBeGreaterThan(0)
    expect(screen.getByText(/records a scope mismatch/)).toBeInTheDocument()
  })

  it('searches candidates by the manufacturers they represent', async () => {
    await show(detail({ rfq: { ...detail().rfq, stage: 'Shortlisting' } }), [
      candidate(),
      { ...candidate(), bidder: { ...APPROVED_BIDDER, id: 'bdr_other', name: 'Another Vendor', represented_manufacturers: ['ABB'] } },
    ])

    await screen.findAllByText(/Al Munara Switchgear LLC/)
    fireEvent.change(screen.getByLabelText('Search candidates'), {
      target: { value: 'ABB' },
    })

    expect(screen.getAllByText(/Another Vendor/).length).toBeGreaterThan(0)
    expect(screen.queryAllByText(/Al Munara Switchgear LLC/)).toHaveLength(0)
  })

  it('sends the override reason typed against a blocked candidate', async () => {
    vi.mocked(inviteRegisteredBidder).mockResolvedValue(
      entry({ vendor_id: 'bdr_sandstone', override_reason: 'Sole source' }),
    )
    await show(detail({ rfq: { ...detail().rfq, stage: 'Shortlisting' } }), [
      BLOCKED_CANDIDATE,
    ])

    fireEvent.change(await screen.findByLabelText(/Reason for inviting Sandstone/), {
      target: { value: 'Sole source for the 16-inch jig' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Invite Sandstone/ }))

    await waitFor(() =>
      expect(inviteRegisteredBidder).toHaveBeenCalledWith('rfq_abc', {
        vendor_id: 'bdr_sandstone',
        override_reason: 'Sole source for the 16-inch jig',
      }),
    )
  })

  it('surfaces the refusal when a blocked bidder is invited with no reason', async () => {
    vi.mocked(inviteRegisteredBidder).mockRejectedValue(
      new Error(
        'Prequalification lapsed on 2026-05-09. Record a reason to invite them anyway.',
      ),
    )
    await show(detail({ rfq: { ...detail().rfq, stage: 'Shortlisting' } }), [
      BLOCKED_CANDIDATE,
    ])

    fireEvent.click(await screen.findByRole('button', { name: /Invite Sandstone/ }))

    await waitFor(() =>
      expect(
        screen.getByText(/Record a reason to invite them anyway/),
      ).toBeInTheDocument(),
    )
  })

  it('marks a candidate already invited rather than offering them twice', async () => {
    await show(
      detail({ rfq: { ...detail().rfq, stage: 'Shortlisting' }, shortlist: [entry()] }),
      [candidate({ shortlisted: true })],
    )

    expect(await screen.findByText('Invited')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Invite Al Munara/ })).toBeNull()
  })

  // The card above the item screen's RFQ list answers "who may bid"; this
  // column answers "is who we invited still on that list". They read the same
  // registry, so a vendor cannot be approved on one screen and not the other.
  it('says on the shortlist who the client has approved', async () => {
    await show(
      detail({
        rfq: { ...detail().rfq, stage: 'Shortlisting' },
        shortlist: [
          entry(),
          entry({
            id: 'sle_2',
            vendor_name: 'Silverdune Process Systems',
            client_approved: false,
            // On our list and not the client's — the two keys stay coherent,
            // because the boolean is derived from this list server-side.
            approved_by: ['Astra'],
          }),
        ],
      }),
      [candidate({ shortlisted: true })],
    )

    const approved = screen.getByRole('row', { name: /Al Munara/ })
    expect(within(approved).getByText('ADNOC')).toBeInTheDocument()
    const off = screen.getByRole('row', { name: /Silverdune/ })
    expect(within(off).getByText(/Not on the ADNOC list/)).toBeInTheDocument()
  })

  it('reports an unregistered vendor as unknown, not as unapproved', async () => {
    // `null` is "we cannot tell", and the column has to keep saying that. A
    // hand-typed vendor rendered as "Not on the ADNOC list" would be a finding
    // nobody made — nothing was checked, because there was nothing to check.
    await show(
      detail({
        rfq: { ...detail().rfq, stage: 'Shortlisting' },
        shortlist: [
          entry({
            vendor_id: null,
            vendor_name: 'Galfar',
            client_approved: null,
            approved_by: null,
          }),
        ],
      }),
      [],
    )

    const row = screen.getByRole('row', { name: /Galfar/ })
    expect(within(row).queryByText(/Not on the ADNOC list/)).toBeNull()
    expect(within(row).getByText('Not checked')).toBeInTheDocument()
  })

  it('removes a shortlisted vendor by id', async () => {
    vi.mocked(removeShortlistEntry).mockResolvedValue(undefined)
    await show(
      detail({ rfq: { ...detail().rfq, stage: 'Shortlisting' }, shortlist: [entry()] }),
      [candidate({ shortlisted: true })],
    )

    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))

    await waitFor(() =>
      expect(removeShortlistEntry).toHaveBeenCalledWith('rfq_abc', 'sle_1'),
    )
  })

  it('still takes a one-off vendor by hand, behind a disclosure', async () => {
    vi.mocked(addShortlistEntry).mockResolvedValue(
      entry({ id: 'sle_2', vendor_id: null, vendor_name: 'A one-off fabricator' }),
    )
    await show(detail({ rfq: { ...detail().rfq, stage: 'Shortlisting' } }))

    fireEvent.change(await screen.findByLabelText('Vendor name'), {
      target: { value: 'A one-off fabricator' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add vendor' }))

    await waitFor(() =>
      expect(addShortlistEntry).toHaveBeenCalledWith(
        'rfq_abc',
        expect.objectContaining({
          vendor_name: 'A one-off fabricator',
          included: true,
        }),
      ),
    )
  })

  it('warns that changing vendors re-opens approval', async () => {
    await show(detail({ rfq: { ...detail().rfq, stage: 'Shortlisting' } }))
    expect(
      screen.getByText(/Any change to the vendors re-opens approval/),
    ).toBeInTheDocument()
  })

  it('approves the shortlist', async () => {
    vi.mocked(approveShortlist).mockResolvedValue({ approved_by: 'admin@gmail.com' })
    await show(detail({ rfq: { ...detail().rfq, stage: 'Shortlisting' } }))

    fireEvent.click(screen.getByRole('button', { name: 'Approve shortlist' }))

    await waitFor(() => expect(approveShortlist).toHaveBeenCalledWith('rfq_abc'))
  })

  // The technical package moved into the Issued step when Scoping was removed
  // from the process. It is the same editor and the same freeze rule; what
  // changed is which step you find it under, so these three drive it there.
  it('edits the technical package under Issued', async () => {
    await show(detail({ rfq: { ...detail().rfq, stage: 'Issued' } }))
    expect(screen.getByLabelText('Package revision')).toBeInTheDocument()
    expect(screen.getByLabelText('Document code')).toBeInTheDocument()
  })

  it('freezes the package', async () => {
    vi.mocked(freezeTechnicalPackage).mockResolvedValue({
      rfq_id: 'rfq_abc', revision: 'Rev. B', basis_of_design: 'b',
      attachments: [], frozen_at: '2026-08-13T10:00:00Z', frozen_by: 'admin@gmail.com',
    })
    await show(
      detail({
        rfq: { ...detail().rfq, stage: 'Issued' },
        technical_package: {
          rfq_id: 'rfq_abc', revision: 'Rev. B', basis_of_design: 'basis',
          attachments: [], frozen_at: null, frozen_by: null,
        },
      }),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Freeze package' }))

    await waitFor(() => expect(freezeTechnicalPackage).toHaveBeenCalledWith('rfq_abc'))
  })

  it('replaces the package editor with a read-only view once frozen', async () => {
    await show(
      detail({
        rfq: { ...detail().rfq, stage: 'Issued' },
        technical_package: {
          rfq_id: 'rfq_abc', revision: 'Rev. B', basis_of_design: 'basis',
          attachments: [{ doc_code: 'HAL-PID-001', title: 'P&ID', revision: 'Rev. C' }],
          frozen_at: '2026-08-13T10:00:00Z', frozen_by: 'lead@adp.ae',
        },
      }),
    )
    expect(screen.queryByLabelText('Package revision')).not.toBeInTheDocument()
    expect(screen.getByText(/frozen by lead@adp\.ae/)).toBeInTheDocument()
    expect(screen.getByText(/cannot be edited/)).toBeInTheDocument()
  })

  it('flags an attachment with no definite revision, since that blocks the freeze', async () => {
    await show(
      detail({
        rfq: { ...detail().rfq, stage: 'Issued' },
        technical_package: {
          rfq_id: 'rfq_abc', revision: 'Rev. B', basis_of_design: 'basis',
          attachments: [{ doc_code: 'HAL-PID-001', title: 'P&ID', revision: null }],
          frozen_at: null, frozen_by: null,
        },
      }),
    )
    expect(screen.getByText('none')).toBeInTheDocument()
  })

  it('keeps the whole stage history visible while stepping', async () => {
    await show(detail())
    expect(screen.getByText(/RFQ created/)).toBeInTheDocument()
  })
})
