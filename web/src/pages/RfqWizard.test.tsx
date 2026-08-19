import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { EnquiryAudience } from '../types'
import { RfqWizard } from './RfqWizard'
import type { RfqDetail, ShortlistEntry } from '../types'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    fetchRfq: vi.fn(),
    transitionRfq: vi.fn(),
    addShortlistEntry: vi.fn(),
    removeShortlistEntry: vi.fn(),
    approveShortlist: vi.fn(),
    setTbeTemplate: vi.fn(),
    uploadRfqDocuments: vi.fn(),
    removeRfqDocument: vi.fn(),
    // ShortlistingStep now reads the organisation-wide contact directory on
    // every render. Mocked explicitly rather than left to `actual` so this
    // suite never makes a real network call.
    uploadVendorContacts: vi.fn(),
    fetchVendorContacts: vi.fn().mockResolvedValue({
      contacts: [],
      count: 0,
      uploaded_by: null,
      uploaded_at: null,
      source_document: null,
    }),
    // SendEnquiry reads and writes through these; mocked explicitly so this
    // suite never makes a real network call when the Issued step's card is
    // reached.
    previewEnquiry: vi.fn(),
    sendEnquiry: vi.fn(),
  }
})

import {
  addShortlistEntry,
  approveShortlist,
  fetchRfq,
  previewEnquiry,
  removeShortlistEntry,
  sendEnquiry,
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
    email: null,
    ...over,
  }
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
    ...over,
  }
}

async function show(data: RfqDetail) {
  vi.mocked(fetchRfq).mockResolvedValue(data)
  render(<RfqWizard rfqId="rfq_abc" stages={STAGES} onBack={() => {}} />)
  await waitFor(() => {
    expect(screen.queryByText(/Loading RFQ…/i)).not.toBeInTheDocument()
  })
}

// Without this, `toHaveBeenCalledTimes` on a mock this file shares across
// every test (`fetchRfq` above all) counts calls earlier tests made too —
// this file had no such guard until the enquiry-reload tests below needed to
// count `fetchRfq` calls precisely.
beforeEach(() => vi.clearAllMocks())

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

  // The blocked reason used to render here, ahead of any attempt. It was asked
  // for removed, and the sentence is not lost with it: `run` catches the
  // server's 409 and the error banner shows the same words, which
  // `surfaces the server’s refusal when a closed gate rejects the step` below
  // is what holds. That test is the safety net — removing it too would leave a
  // refusal with nowhere to appear at all.
  it('does not state the blocking reason before the step is attempted', async () => {
    await show(detail())

    expect(
      screen.queryByText(/The shortlist contains no included vendors/),
    ).not.toBeInTheDocument()
    // The button stays, and stays enabled: its refusal is what surfaces the
    // reason now, so disabling it would make the gate unexplainable.
    expect(
      screen.getByRole('button', { name: /Mark Shortlisting complete/ }),
    ).toBeEnabled()
  })

  // Pins the scope of that removal. Only the blocked variant went; the passing
  // one is the card's whole content when a gate is open, and dropping the
  // paragraph outright rather than just its blocked branch takes this with it.
  it('still says the step is ready when the gate passes', async () => {
    await show(detail({ gate: { passed: true, reason: null } }))

    expect(
      screen.getByText(/Everything Shortlisting needs is in place/),
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
          documents: [],
          frozen_at: '2026-08-13T10:00:00Z',
          frozen_by: 'lead@adp.ae',
        },
        shortlist: [entry()],
      }),
    )

    fireEvent.click(screen.getByRole('button', { name: /Shortlisting/ }))

    expect(screen.getByText(/This step is complete\. You can still edit it/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove' })).toBeInTheDocument()
  })

  // BD-5: who to invite is chosen on the item screen, before an RFQ exists —
  // `AvailableVendorList` there is judged against the four-source pool, which
  // is strictly more than the registry-only search this step used to carry.
  // The seven tests that drove that search were deleted here, not skipped:
  // they exercised the Invite-from-a-candidate-row control, the scope-fit
  // filter, the candidate search box, an override reason typed against a
  // blocked candidate, and the already-invited mark inside that list — none
  // of which exists any more. `ShortlistingStep.test.tsx` covers what
  // replaced it: the Registry section is gone and the escape hatch for an
  // unregistered vendor survives.

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
    )

    const row = screen.getByRole('row', { name: /Galfar/ })
    expect(within(row).queryByText(/Not on the ADNOC list/)).toBeNull()
    expect(within(row).getByText('Not checked')).toBeInTheDocument()
  })

  it('removes a shortlisted vendor by id', async () => {
    vi.mocked(removeShortlistEntry).mockResolvedValue(undefined)
    await show(
      detail({ rfq: { ...detail().rfq, stage: 'Shortlisting' }, shortlist: [entry()] }),
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

  // Three tests stood here and are deleted rather than skipped: they drove the
  // package editor's revision field, its Freeze button and its attachment
  // register, none of which exists. The Issued step is one upload control and
  // the list of what has been uploaded; `RaiseRfqStep.test.tsx` covers it, and
  // what this file still owns is that the step is reachable at all.
  it('uploads enquiry documents under Issued', async () => {
    await show(detail({ rfq: { ...detail().rfq, stage: 'Issued' } }))
    expect(screen.getByText('Add documents')).toBeInTheDocument()
    expect(screen.queryByLabelText('Package revision')).not.toBeInTheDocument()
  })

  it('shows a frozen package read-only, with nothing to press', async () => {
    await show(
      detail({
        rfq: { ...detail().rfq, stage: 'Issued' },
        technical_package: {
          rfq_id: 'rfq_abc', revision: 'Rev. B', basis_of_design: 'basis',
          attachments: [{ doc_code: 'HAL-PID-001', title: 'P&ID', revision: 'Rev. C' }],
          documents: [],
          frozen_at: '2026-08-13T10:00:00Z', frozen_by: 'lead@adp.ae',
        },
      }),
    )
    expect(screen.queryByText('Add documents')).not.toBeInTheDocument()
    expect(screen.getByText(/frozen by lead@adp\.ae/)).toBeInTheDocument()
    expect(screen.getByText(/cannot be edited/)).toBeInTheDocument()
  })

  it('keeps the whole stage history visible while stepping', async () => {
    await show(detail())
    expect(screen.getByText(/RFQ created/)).toBeInTheDocument()
  })

  // Each step's blurb is the only sentence telling a reader what the step is
  // for, so it is the first thing to go stale when a control moves between two
  // of them. The free-text TBE template editor is gone entirely, replaced by
  // the fixed eligibility checklist; a blurb still promising a template sends
  // the reader to look for a control that no longer exists anywhere, and
  // nothing else in this suite reads these strings.
  it('promises the eligibility checklist on Issued and no TBE template anywhere', async () => {
    await show(detail())

    // Asserted on the whole step card rather than the blurb's wording, so it
    // covers the editor and the sentence describing it at once — either one
    // left behind here is the failure.
    expect(screen.getByRole('region', { name: /Shortlisting/ })).not.toHaveTextContent(
      /TBE|eligibility checklist/i,
    )

    // Scoped to the stepper: "Issued" also names the transition button at the
    // bottom of the screen.
    const steps = screen.getByRole('list', { name: 'RFQ steps' })
    fireEvent.click(within(steps).getByRole('button', { name: /Issued/ }))
    const issued = screen.getByRole('region', { name: /Issued/ })
    expect(issued).toHaveTextContent(/eligibility checklist/i)
    // The retired name must not survive anywhere on the step it used to own.
    expect(issued).not.toHaveTextContent(/TBE template/)
  })
})

// Regression coverage for the Critical defect found round-tripping this
// screen in a real browser: clicking "Preview recipients" showed the
// recipient table for an instant and then reverted to the button, with no
// way to ever reach Send. `SendEnquiry` stubbed `run` in its own test file to
// something that just awaits the action — nothing there reloads or unmounts,
// so those tests could not see this. Only rendering through the real
// `RfqWizard`, whose `run` genuinely reloads the RFQ, exercises the path that
// broke.
describe('RfqWizard enquiry send — surviving a reload', () => {
  const withRecipient = {
    transport: 'outbox' as const,
    recipients: [
      { shortlist_entry_id: 'sle_1', vendor_name: 'Galfar', to: ['sales@galfar.example'], skip_reason: null },
    ],
    audience: 'unsent' as const,
    audiences: ['unsent', 'outdated', 'all'] as EnquiryAudience[],
  }

  it('does not reload the RFQ merely to preview who it would reach', async () => {
    // The mechanism: `previewEnquiry` stores nothing, so routing it through
    // `run` reloads the whole RFQ for no reason — and that reload is exactly
    // what used to unmount `SendEnquiry` mid-preview. If a later change routes
    // the preview back through `run`, `fetchRfq` fires a second time and this
    // goes red, independent of any timing.
    vi.mocked(fetchRfq).mockResolvedValue(
      detail({ rfq: { ...detail().rfq, stage: 'Issued' } }),
    )
    vi.mocked(previewEnquiry).mockResolvedValue(withRecipient)

    render(<RfqWizard rfqId="rfq_abc" stages={STAGES} onBack={() => {}} />)
    await waitFor(() =>
      expect(screen.queryByText(/Loading RFQ…/i)).not.toBeInTheDocument(),
    )

    fireEvent.click(screen.getByRole('button', { name: /Preview recipients/ }))

    expect(await screen.findByText('sales@galfar.example')).toBeInTheDocument()
    // A macrotask beat, so a reload that *did* fire (bug reinstated) has time
    // to land before this assertion runs.
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.getByText('sales@galfar.example')).toBeInTheDocument()
    expect(fetchRfq).toHaveBeenCalledTimes(1)
  })

  it('keeps the preview and the skipped list on screen after sending reloads the RFQ', async () => {
    // Sending genuinely writes, so it does go through `run`, and `run` does
    // reload. The second `fetchRfq` call resolves on a macrotask rather than
    // immediately: resolved immediately, the loading state and the resolution
    // batch into one commit and the subtree never unmounts, which is the trap
    // this repository has already recorded three times on the item screen —
    // the test would pass whether or not `RfqWizard` guards its loading branch
    // correctly.
    const rfq = detail({ rfq: { ...detail().rfq, stage: 'Issued' } })
    let calls = 0
    vi.mocked(fetchRfq).mockImplementation(() => {
      calls += 1
      return calls === 1
        ? Promise.resolve(rfq)
        : new Promise((resolve) => setTimeout(() => resolve(rfq), 0))
    })
    vi.mocked(previewEnquiry).mockResolvedValue(withRecipient)
    vi.mocked(sendEnquiry).mockResolvedValue({
      sent: [],
      skipped: [{ vendor_name: 'Nowhere Trading', reason: 'No address on file for Nowhere Trading.' }],
    })

    render(<RfqWizard rfqId="rfq_abc" stages={STAGES} onBack={() => {}} />)
    await waitFor(() =>
      expect(screen.queryByText(/Loading RFQ…/i)).not.toBeInTheDocument(),
    )

    fireEvent.click(screen.getByRole('button', { name: /Preview recipients/ }))
    await screen.findByText('sales@galfar.example')

    fireEvent.click(screen.getByRole('button', { name: /^Send/ }))

    // The reload is in flight now (the macrotask has not fired yet). The
    // whole point of `keepPreviousData` is that the screen does not blank
    // during it.
    expect(screen.queryByText(/Loading RFQ…/i)).not.toBeInTheDocument()

    expect(
      await screen.findByText(/No address on file for Nowhere Trading/),
    ).toBeInTheDocument()
    expect(fetchRfq).toHaveBeenCalledTimes(2)
  })
})
