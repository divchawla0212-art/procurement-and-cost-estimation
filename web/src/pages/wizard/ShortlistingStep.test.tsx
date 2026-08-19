import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ShortlistingStep } from './ShortlistingStep'
import { fetchVendorContacts, uploadVendorContacts } from '../../api'
import type { RfqDetail, ShortlistEntry } from '../../types'

vi.mock('../../api', () => ({
  addShortlistEntry: vi.fn().mockResolvedValue({}),
  approveShortlist: vi.fn().mockResolvedValue({}),
  removeShortlistEntry: vi.fn().mockResolvedValue({}),
  setTbeTemplate: vi.fn().mockResolvedValue({}),
  // Every render of this step now calls `fetchVendorContacts`, so every
  // pre-existing test in this file goes through it too — a bare `vi.fn()`
  // resolves to `undefined` and `useAsync` calls `.then` on that. The
  // 'vendor contact directory' describe block below overrides this with its
  // own fixture per test.
  uploadVendorContacts: vi.fn(),
  fetchVendorContacts: vi.fn().mockResolvedValue({
    contacts: [],
    count: 0,
    uploaded_by: null,
    uploaded_at: null,
    source_document: null,
  }),
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
  email: null,
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

  // The editor moved to the Issued step, beside the enquiry documents whose
  // returnables it lists. `_shortlisting_exit` still reads the template on the
  // way out of *this* step, so the control now sits after the gate that
  // requires it — reachable because the wizard opens any step early, and named
  // in the refusal so a blocked reader is told where to go. This is the
  // assertion that fails if it grows back here.
  it('has no TBE template editor', () => {
    step([entry()])

    expect(
      screen.queryByRole('heading', { name: 'TBE template' }),
    ).not.toBeInTheDocument()
    expect(screen.queryByLabelText('One criterion per line')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Save TBE template' }),
    ).not.toBeInTheDocument()
  })
})

// The Scope fit column came out of the invited-bidders table: it is a snapshot
// of a whole-string discipline match taken at invitation, and on a real
// shortlist it reads `no` for most rows without saying what did not match —
// a verdict nobody can act on sitting beside the approvals that can be.
// `scope_code_fit` is untouched on the record and still posted by the escape
// hatch below; only the column is gone.
describe('ShortlistingStep invited bidders', () => {
  it('has no Scope fit column', () => {
    step([entry()])

    expect(
      screen.queryByRole('columnheader', { name: /scope fit/i }),
    ).not.toBeInTheDocument()
  })

  it('still shows the approvals, prequalification and exception columns', () => {
    step([entry()])

    for (const name of [/vendor/i, /approvals/i, /prequalification/i, /recorded exception/i]) {
      expect(screen.getByRole('columnheader', { name })).toBeInTheDocument()
    }
  })
})

// Task 5: the vendor contact directory. `email` on a shortlist row is derived
// server-side against the organisation-wide directory, keyed on the vendor's
// folded name — see `docs/superpowers/specs/2026-08-18-vendor-contact-directory-design.md`
// §7-8. This step both shows it (the Email column) and is the one place that
// can replace the whole directory (the upload control), so the tests below
// cover both directions.
const DIRECTORY = {
  contacts: [],
  count: 8,
  uploaded_by: 'buyer@example.com',
  uploaded_at: '2026-08-18T09:00:00+00:00',
  source_document: 'vendors.xlsx',
}

describe('vendor contact directory', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchVendorContacts).mockResolvedValue(DIRECTORY)
  })

  it('shows an invited bidder’s address', async () => {
    render(
      <ShortlistingStep
        data={{ ...BASE, shortlist: [entry({ email: ['sales@danway.example'] })] }}
        run={vi.fn()} busy={false} tick={0}
      />,
    )
    expect(await screen.findByText('sales@danway.example')).toBeInTheDocument()
  })

  it('shows every address a vendor holds, not just the first', async () => {
    render(
      <ShortlistingStep
        data={{ ...BASE, shortlist: [entry({
          email: ['sales@danway.example', 'bids@danway.example'],
        })] }}
        run={vi.fn()} busy={false} tick={0}
      />,
    )
    expect(await screen.findByText('bids@danway.example')).toBeInTheDocument()
  })

  it('says no address is on file rather than rendering an empty cell', async () => {
    render(
      <ShortlistingStep
        data={{ ...BASE, shortlist: [entry({ email: null })] }}
        run={vi.fn()} busy={false} tick={0}
      />,
    )
    expect(await screen.findByText(/no address on file/i)).toBeInTheDocument()
  })

  it('says the directory is organisation-wide, not this RFQ’s', async () => {
    // The control sits inside one RFQ and writes a shared directory. Without
    // this caption a buyer overwrites everyone else's addresses believing they
    // are editing their own enquiry.
    render(<ShortlistingStep data={BASE} run={vi.fn()} busy={false} tick={0} />)
    const caption = await screen.findByText(/organisation-wide/i)
    expect(caption).toHaveTextContent('8')
    expect(caption).toHaveTextContent('buyer@example.com')
  })

  it('uploads the chosen sheet through run, so the wizard reloads', async () => {
    vi.mocked(uploadVendorContacts).mockResolvedValue({
      contacts: [],
      summary: { parsed: 9, stored: 8, addresses: 8, matched: 6 },
    })
    const run = vi.fn(async (action: () => Promise<unknown>) => {
      await action()
    })
    render(<ShortlistingStep data={BASE} run={run} busy={false} tick={0} />)
    const file = new File(['x'], 'vendors.xlsx')
    fireEvent.change(screen.getByLabelText(/add vendor email list/i), {
      target: { files: [file] },
    })
    await waitFor(() => expect(uploadVendorContacts).toHaveBeenCalledWith(file))
    expect(run).toHaveBeenCalled()
  })

  it('reads the counts back rather than showing a bare tick', async () => {
    // An upload that matched nobody is a spelling problem, not a success, and
    // only the numbers say which.
    vi.mocked(uploadVendorContacts).mockResolvedValue({
      contacts: [],
      summary: { parsed: 9, stored: 8, addresses: 8, matched: 6 },
    })
    const run = vi.fn(async (action: () => Promise<unknown>) => {
      await action()
    })
    render(<ShortlistingStep data={BASE} run={run} busy={false} tick={0} />)
    fireEvent.change(screen.getByLabelText(/add vendor email list/i), {
      target: { files: [new File(['x'], 'vendors.xlsx')] },
    })
    const line = await screen.findByText(/8 of 9/i)
    expect(line).toHaveTextContent('6')
  })

  it('keeps the email column inside a horizontally scrolling wrapper', async () => {
    const { container } = render(
      <ShortlistingStep
        data={{ ...BASE, shortlist: [entry()] }}
        run={vi.fn()} busy={false} tick={0}
      />,
    )
    await screen.findByText('Al Munara Switchgear LLC')
    const wrapper = container.querySelector('.table-scroll .table')
    expect(wrapper).toBeTruthy()
    // Not just "a table sits in a scroll wrapper" — the sixth column is in
    // it, since that is the one this task added.
    expect(within(wrapper as HTMLElement).getByRole('columnheader', { name: 'Email' })).toBeInTheDocument()
  })
})
