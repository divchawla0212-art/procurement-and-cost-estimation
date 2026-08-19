import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ItemDetail } from './ItemDetail'
import {
  APPROVED_BIDDER,
  CABLE,
  GENERATOR,
  HALIBA_PROJECT,
  RFQ_DETAIL,
  detail,
  draftPick,
  rfq,
  shortlistEntry,
  suggestion,
  vendorEntry,
  vendorLists,
} from './workflow-fixtures'
import type {
  AvailableBidders,
  ItemVendorEntry,
  VendorListSource,
  WorkflowProjectDetail,
} from '../types'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    fetchWorkflowProject: vi.fn(),
    updateWorkflowItem: vi.fn(),
    fetchAvailableBidders: vi.fn(),
    // A curated row has no `vendor_id`, so it is shortlisted by name through
    // the free-text path — never through `inviteRegisteredBidder`.
    addShortlistEntry: vi.fn(),
    // The Edit item form's Discipline field is a picker over the vocabulary.
    fetchDisciplines: vi.fn(),
    // One read per covering RFQ: the project payload carries no shortlist.
    fetchRfq: vi.fn(),
    inviteRegisteredBidder: vi.fn(),
    uploadItemVendorList: vi.fn(),
    addItemVendor: vi.fn(),
    removeItemVendor: vi.fn(),
    suggestItemVendors: vi.fn(),
    // The item's own shortlist draft — one call per vendor, no bulk route.
    addDraftShortlistPick: vi.fn(),
    removeDraftShortlistPick: vi.fn(),
  }
})

import {
  addDraftShortlistPick,
  addItemVendor,
  addShortlistEntry,
  removeDraftShortlistPick,
  fetchAvailableBidders,
  fetchDisciplines,
  fetchRfq,
  fetchWorkflowProject,
  inviteRegisteredBidder,
  removeItemVendor,
  suggestItemVendors,
  updateWorkflowItem,
  uploadItemVendorList,
} from '../api'

function availableList(over: Partial<AvailableBidders> = {}): AvailableBidders {
  const bidders = over.bidders ?? []
  return {
    approvers: ['ADNOC', 'Astra'],
    selectable_approvers: ['ADNOC', 'Astra'],
    discipline: null,
    total: bidders.length,
    ...over,
    bidders,
  }
}

function renderItem(itemId = 'itm_1', onBack = vi.fn(), onHome = vi.fn()) {
  render(
    <ItemDetail
      projectId="prj_1"
      itemId={itemId}
      onBack={onBack}
      onHome={onHome}
    />,
  )
  return { onBack, onHome }
}

describe('ItemDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Every test renders the bidder card, so every test needs this fetcher
    // resolved. Left unmocked it reaches the real one and the suite fails on a
    // network call rather than on the assertion being made.
    // The card asks twice: narrowed to the item's discipline, and for the
    // whole list it falls back to. Both need an answer in every test.
    vi.mocked(fetchAvailableBidders).mockResolvedValue(availableList())
    // Any test whose project has covering RFQs reads each one's shortlist.
    vi.mocked(fetchRfq).mockResolvedValue(RFQ_DETAIL)
    vi.mocked(fetchDisciplines).mockResolvedValue([
      { name: 'Cables', product_groups: ['CABLES - LV POWER DISTRIBUTION'] },
      { name: 'Generators', product_groups: ['GENERATOR POWER-OTHERS'] },
    ])
  })

  it("lists the client's vendors for this item", async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({
        items: [GENERATOR],
        item_vendor_lists: { itm_1: vendorLists({ Client: [vendorEntry()] }) },
      }),
    )

    renderItem()

    const card = await screen.findByRole('region', { name: /client list/i })
    // An upload holding rows starts folded, and `getByText` finds an element
    // inside a `hidden` body just as happily as a visible one — so this asks
    // for the card to be open first, or it would pass over a list that never
    // renders at all.
    fireEvent.click(within(card).getByRole('button', { name: /show/i }))

    expect(within(card).getByText('AL MUNARA SWITCHGEAR LLC')).toBeInTheDocument()
  })

  it('marks a vendor the registry does not hold', async () => {
    // `null` is "the export named them and we cannot find them", which is not
    // the same as unapproved — the same three-state rule the shortlist keeps.
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({
        items: [GENERATOR],
        item_vendor_lists: {
          itm_1: vendorLists({
            Client: [vendorEntry({ vendor_id: null, vendor_name: 'STRANGER LLC' })],
          }),
        },
      }),
    )

    renderItem()

    const card = await screen.findByRole('region', { name: /client list/i })
    fireEvent.click(within(card).getByRole('button', { name: /show/i }))

    expect(within(card).getByText(/not in the registry/i)).toBeInTheDocument()
  })

  it('says when a list has not been uploaded', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], item_vendor_lists: { itm_1: vendorLists() } }),
    )

    renderItem()

    const card = await screen.findByRole('region', { name: /astra list/i })
    expect(
      within(card).getByText(/nothing on the astra list yet/i),
    ).toBeInTheDocument()
  })

  it('offers both vendor list uploads when editing an item', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /edit item/i }))

    expect(screen.getByLabelText(/add client list/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/add astra list/i)).toBeInTheDocument()
  })

  it('uploads the client list against this item', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(uploadItemVendorList).mockResolvedValue({
      entries: [vendorEntry()],
      summary: { parsed: 2, kept: 1, linked: 1 },
    })

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /edit item/i }))
    const file = new File(['x'], 'avl.xlsx')
    fireEvent.change(screen.getByLabelText(/add client list/i), {
      target: { files: [file] },
    })

    // The source is the browser's to send: the Astra list is a subset of the
    // client's and carries nothing in it saying whose list it is.
    await waitFor(() =>
      expect(uploadItemVendorList).toHaveBeenCalledWith('prj_1', 'itm_1', 'Client', file),
    )
    expect(await screen.findByText(/1 of 2 kept · 1 in the registry/i)).toBeInTheDocument()
  })

  it("surfaces the server's refusal of a bad workbook", async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(uploadItemVendorList).mockRejectedValue(
      new Error('avl.xlsx has no "Vendor Name" column.'),
    )

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /edit item/i }))
    fireEvent.change(screen.getByLabelText(/add client list/i), {
      target: { files: [new File(['x'], 'avl.xlsx')] },
    })

    expect(await screen.findByText(/has no "Vendor Name" column/i)).toBeInTheDocument()
  })

  it('renders the RFQ chooser as one field, not a box inside a box', async () => {
    // The same defect the search row had, in the control added beside it: a
    // `<label className="field">` takes an input's border and padding and the
    // control inside it keeps its own, so the pair renders as nested boxes.
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({
        items: [GENERATOR],
        rfqs: [
          rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1']),
          rfq('rfq_2', 'ADP-RFQ-2026-021', ['itm_1']),
        ],
      }),
    )
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )

    renderItem()

    const chooser = await screen.findByLabelText(/shortlist into/i)
    expect(chooser.closest('label.field')).toBeNull()
  })

  it('keeps the row button short while still naming its vendor', async () => {
    // The vendor name in the visible label made every button wrap to three
    // lines and every row 89px tall. The name belongs in the accessible name,
    // which is what a screen reader and these tests read.
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
    )
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )
    vi.mocked(fetchRfq).mockResolvedValue({ ...RFQ_DETAIL, shortlist: [] })

    renderItem()

    const button = await screen.findByRole('button', { name: /shortlist al munara/i })
    expect(button).toHaveTextContent(/^Shortlist$/)
  })

  it('renders the vendor search as one field, not a box inside a box', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )

    renderItem()

    // `.field` and `.input` are input classes. Put on the wrapping label it
    // gave the label an input's border and padding and left the real input
    // with the browser's raw user-agent chrome — two nested boxes.
    const search = await screen.findByLabelText('Search vendors')
    expect(search).toHaveClass('input')
    expect(search.closest('label')).toBeNull()
  })

  it('shortlists every selected vendor, one call each', async () => {
    // The batch targets the **item's** draft, not an RFQ — BD-2 moved it there
    // so a selection can be assembled before anything is raised. Still one call
    // per vendor and still sequential: there is no bulk route, deliberately.
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
    )
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({
        bidders: [
          APPROVED_BIDDER,
          { ...APPROVED_BIDDER, id: 'bdr_other', name: 'Gulf Crescent Fabricators' },
        ],
      }),
    )
    vi.mocked(fetchRfq).mockResolvedValue({ ...RFQ_DETAIL, shortlist: [] })
    vi.mocked(addDraftShortlistPick).mockImplementation(async (_p, _i, body) =>
      draftPick({ ...body, vendor_id: body.vendor_id ?? null }),
    )

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /select these 2/i }))
    fireEvent.click(screen.getByRole('button', { name: /shortlist selected \(2\)/i }))

    await waitFor(() => expect(addDraftShortlistPick).toHaveBeenCalledTimes(2))
    expect(addDraftShortlistPick).toHaveBeenCalledWith('prj_1', 'itm_1', {
      vendor_id: 'bdr_almunara',
      vendor_name: 'Al Munara Switchgear LLC',
      source: 'ADNOC',
    })
    expect(addDraftShortlistPick).toHaveBeenCalledWith('prj_1', 'itm_1', {
      vendor_id: 'bdr_other',
      vendor_name: 'Gulf Crescent Fabricators',
      source: 'ADNOC',
    })
    // Nothing is invited by shortlisting. An invitation is an attributed act
    // that happens when an RFQ is raised over the draft.
    expect(inviteRegisteredBidder).not.toHaveBeenCalled()
  })

  it('still shows the refusals after the batch reloads the screen', async () => {
    // The batch ends with a re-read of the project. If that re-read blanks the
    // screen, the card unmounts and takes the refusals, the summary and the
    // retained selection with it — everything the reader needs to act on. The
    // reload here resolves on a macrotask so the loading state actually
    // commits; resolved immediately it batches into one render and this passes
    // whether or not the bug is present.
    const project = detail({
      items: [GENERATOR],
      rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])],
    })
    let calls = 0
    vi.mocked(fetchWorkflowProject).mockImplementation(() => {
      calls += 1
      return calls === 1
        ? Promise.resolve(project)
        : new Promise((resolve) => setTimeout(() => resolve(project), 0))
    })
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({
        bidders: [
          APPROVED_BIDDER,
          { ...APPROVED_BIDDER, id: 'bdr_other', name: 'Gulf Crescent Fabricators' },
        ],
      }),
    )
    vi.mocked(fetchRfq).mockResolvedValue({ ...RFQ_DETAIL, shortlist: [] })
    vi.mocked(addDraftShortlistPick).mockImplementation(async (_p, _i, body) => {
      if (body.vendor_id === 'bdr_other') {
        throw new Error('Gulf Crescent Fabricators is on hold.')
      }
      return draftPick({ ...body, vendor_id: body.vendor_id ?? null })
    })

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /select these 2/i }))
    fireEvent.click(screen.getByRole('button', { name: /shortlist selected \(2\)/i }))

    await waitFor(() => expect(fetchWorkflowProject).toHaveBeenCalledTimes(2))
    // After the reload has actually landed.
    await screen.findByText('Gulf Crescent Fabricators')

    expect(screen.getByText(/1 shortlisted · 1 refused/i)).toBeInTheDocument()
    const row = screen.getByText('Gulf Crescent Fabricators').closest('tr')!
    expect(within(row).getByText(/is on hold/i)).toBeInTheDocument()
    expect(screen.getByText(/^1 selected/)).toBeInTheDocument()
  })

  // `has no bulk control when no RFQ covers the item` was deleted here, not
  // skipped: BD-2 inverts the rule it asserted. The batch now writes to the
  // item's own draft, so it is offered whether or not an RFQ covers the item —
  // `shortlists the selected vendors with no RFQ covering the item`, in the
  // draft suite at the foot of this file, is the assertion that replaced it.

  it('selects the vendors that are actually on screen', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({
        bidders: [
          APPROVED_BIDDER,
          { ...APPROVED_BIDDER, id: 'bdr_other', name: 'Gulf Crescent Fabricators' },
        ],
      }),
    )

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /select these 2/i }))

    expect(screen.getByText(/2 selected/)).toBeInTheDocument()
  })

  it('ticks a vendor by id, not by row', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )

    renderItem()
    fireEvent.click(await screen.findByRole('checkbox', { name: /select al munara/i }))

    expect(screen.getByText(/1 selected/)).toBeInTheDocument()
  })

  it('keeps a selection the search has hidden, and says so', async () => {
    // Pruning on every keystroke throws away the reader's work; dropping the
    // count lets them shortlist a vendor they can no longer see.
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({
        bidders: [
          APPROVED_BIDDER,
          { ...APPROVED_BIDDER, id: 'bdr_other', name: 'Gulf Crescent Fabricators' },
        ],
      }),
    )

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /select these 2/i }))
    fireEvent.change(screen.getByLabelText('Search vendors'), {
      target: { value: 'Gulf' },
    })

    expect(screen.getByText(/2 selected · 1 not shown/)).toBeInTheDocument()
  })

  it('clears the selection', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /select these 1/i }))
    fireEvent.click(screen.getByRole('button', { name: /^clear$/i }))

    expect(screen.queryByText(/selected/)).toBeNull()
  })

  it('opens with every approval required, so the list is unchanged', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )

    renderItem()

    expect(await screen.findByRole('button', { name: 'ADNOC' })).toHaveClass('on')
    expect(screen.getByRole('button', { name: 'Astra' })).toHaveClass('on')
  })

  it('asks the server again with one approval when a chip is unticked', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: 'Astra' }))

    // `toHaveBeenCalledWith`, not `LastCalledWith`: the card makes two reads —
    // the scoped one and the whole-list fallback — and the fallback is the one
    // that happens to settle last.
    await waitFor(() =>
      expect(fetchAvailableBidders).toHaveBeenCalledWith('Electrical', ['ADNOC']),
    )
  })

  it('will not let the last approval be unticked', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: 'Astra' }))

    // Zero required approvals matches nobody in the HAVING query — the server
    // refuses it, and the control should not be able to ask.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'ADNOC' })).toBeDisabled(),
    )
  })

  it('shows an approval pill against each available vendor', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )

    renderItem()

    // Scoped to the row: "ADNOC" now names a filter chip as well as a pill,
    // and an unscoped query matches both.
    const row = (await screen.findByText('Al Munara Switchgear LLC')).closest('tr')!
    expect(within(row).getByText('ADNOC')).toHaveClass('approval-badge--adnoc')
    expect(within(row).getByText('Astra')).toHaveClass('approval-badge--astra')
  })

  it('invites a vendor into the one RFQ covering this item', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
    )
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )
    vi.mocked(fetchRfq).mockResolvedValue({ ...RFQ_DETAIL, shortlist: [] })
    vi.mocked(inviteRegisteredBidder).mockResolvedValue(shortlistEntry())

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /shortlist al munara/i }))

    // `vendor_id` and nothing else: the name, prequalification and scope fit
    // are the registry's, snapshotted server-side.
    await waitFor(() =>
      expect(inviteRegisteredBidder).toHaveBeenCalledWith('rfq_1', {
        vendor_id: 'bdr_almunara',
      }),
    )
  })

  it('has nowhere to invite a vendor when no RFQ covers the item', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], rfqs: [] }),
    )
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )

    renderItem()
    await screen.findByText('Al Munara Switchgear LLC')

    // The *per-row* control is still absent — it invites into a covering RFQ,
    // and there is none. What changed is the copy beside it: shortlisting is
    // now something you can do here first, so telling the reader to raise an
    // RFQ before picking anybody would send them the wrong way round.
    expect(screen.queryByRole('button', { name: /shortlist al munara/i })).toBeNull()
    expect(screen.getByText(/No RFQ is needed yet/i)).toBeInTheDocument()
  })

  it('asks which RFQ when several cover the item', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({
        items: [GENERATOR],
        rfqs: [
          rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1']),
          rfq('rfq_2', 'ADP-RFQ-2026-021', ['itm_1']),
        ],
      }),
    )
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )
    vi.mocked(fetchRfq).mockResolvedValue({ ...RFQ_DETAIL, shortlist: [] })
    vi.mocked(inviteRegisteredBidder).mockResolvedValue(shortlistEntry())

    renderItem()
    fireEvent.change(await screen.findByLabelText(/shortlist into/i), {
      target: { value: 'rfq_2' },
    })
    fireEvent.click(screen.getByRole('button', { name: /shortlist al munara/i }))

    await waitFor(() =>
      expect(inviteRegisteredBidder).toHaveBeenCalledWith('rfq_2', {
        vendor_id: 'bdr_almunara',
      }),
    )
  })

  it('keeps the chosen RFQ after an invitation reloads the screen', async () => {
    // The hazard: a successful invitation re-reads the project, which unmounts
    // and remounts the vendor card. If the chosen target lived in that card it
    // would reset to the first RFQ, and the *next* vendor would be invited to
    // an RFQ nobody selected — silently, since the control would look right.
    const project = detail({
      items: [GENERATOR],
      rfqs: [
        rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1']),
        rfq('rfq_2', 'ADP-RFQ-2026-021', ['itm_1']),
      ],
    })
    // The reload resolves on a macrotask, so the screen's loading state
    // actually commits and the vendor card actually unmounts. Resolved
    // immediately it batches into one render, the card never unmounts, and
    // this test would pass with the target held in the card — which is the
    // bug it exists to catch.
    let calls = 0
    vi.mocked(fetchWorkflowProject).mockImplementation(() => {
      calls += 1
      return calls === 1
        ? Promise.resolve(project)
        : new Promise((resolve) => setTimeout(() => resolve(project), 0))
    })
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({
        bidders: [
          APPROVED_BIDDER,
          { ...APPROVED_BIDDER, id: 'bdr_other', name: 'Gulf Crescent Fabricators' },
        ],
      }),
    )
    vi.mocked(fetchRfq).mockResolvedValue({ ...RFQ_DETAIL, shortlist: [] })
    vi.mocked(inviteRegisteredBidder).mockResolvedValue(shortlistEntry())

    renderItem()
    fireEvent.change(await screen.findByLabelText(/shortlist into/i), {
      target: { value: 'rfq_2' },
    })
    fireEvent.click(screen.getByRole('button', { name: /shortlist al munara/i }))
    // Wait for the *reload*, not merely for the call: the invitation resolves
    // before `onInvited` re-reads the project, so asserting on the call count
    // would let the second click land on the pre-reload render and the test
    // would pass without the reload ever having happened.
    await waitFor(() => expect(fetchWorkflowProject).toHaveBeenCalledTimes(2))
    await screen.findByLabelText(/shortlist into/i)

    // Second invitation, after the reload, with nobody having touched the
    // select in between.
    fireEvent.click(
      await screen.findByRole('button', { name: /shortlist gulf crescent/i }),
    )

    await waitFor(() =>
      expect(inviteRegisteredBidder).toHaveBeenLastCalledWith('rfq_2', {
        vendor_id: 'bdr_other',
      }),
    )
  })

  it("renders the server's refusal against the vendor it refused", async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
    )
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({
        bidders: [
          APPROVED_BIDDER,
          { ...APPROVED_BIDDER, id: 'bdr_other', name: 'Gulf Crescent Fabricators' },
        ],
      }),
    )
    vi.mocked(fetchRfq).mockResolvedValue({ ...RFQ_DETAIL, shortlist: [] })
    vi.mocked(inviteRegisteredBidder).mockRejectedValue(
      new Error('Al Munara Switchgear LLC is on hold — an override reason is required.'),
    )

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /shortlist al munara/i }))

    // Beside the row that caused it, keyed by bidder id — not a page banner,
    // and not against the other vendor. The table re-sorts under a search, so
    // a row index would attach the refusal to a different company.
    const row = (await screen.findByText('Al Munara Switchgear LLC')).closest('tr')!
    expect(within(row).getByText(/an override reason is required/i)).toBeInTheDocument()
    const other = screen.getByText('Gulf Crescent Fabricators').closest('tr')!
    expect(within(other).queryByText(/override reason/i)).toBeNull()
  })

  it('marks a vendor already on the target RFQ as invited', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
    )
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )
    vi.mocked(fetchRfq).mockResolvedValue({
      ...RFQ_DETAIL,
      shortlist: [shortlistEntry({ vendor_id: 'bdr_almunara' })],
    })

    renderItem()
    const row = (await screen.findByText('Al Munara Switchgear LLC')).closest('tr')!

    // The screen must not offer to invite somebody it is counting as invited
    // in the card below.
    await waitFor(() => expect(within(row).getByText('Invited')).toBeInTheDocument())
    expect(within(row).queryByRole('button', { name: /shortlist/i })).toBeNull()
  })

  it("counts each covering RFQ's approvals", async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
    )
    vi.mocked(fetchRfq).mockResolvedValue({
      ...RFQ_DETAIL,
      shortlist: [
        shortlistEntry({ id: 'a', approved_by: ['ADNOC', 'Astra'] }),
        shortlistEntry({ id: 'b', approved_by: ['ADNOC'] }),
        shortlistEntry({ id: 'c', vendor_id: null, approved_by: null, client_approved: null }),
      ],
    })

    renderItem()

    // "not checked" is reported, never folded into an approver's count: a
    // vendor typed in by hand has no registry row, so there is no finding
    // either way.
    expect(
      await screen.findByText('3 invited · 2 ADNOC · 1 Astra · 1 not checked'),
    ).toBeInTheDocument()
  })

  it('says nobody is invited rather than showing a row of zeroes', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
    )
    vi.mocked(fetchRfq).mockResolvedValue({ ...RFQ_DETAIL, shortlist: [] })

    renderItem()

    expect(await screen.findByText(/nobody invited yet/i)).toBeInTheDocument()
  })

  it('shows a dash when a shortlist could not be read', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
    )
    vi.mocked(fetchRfq).mockRejectedValue(new Error('nope'))

    renderItem()

    // An unanswered question and an empty shortlist are different facts, and
    // "0 invited" would report the second when the truth is the first.
    const row = (await screen.findByText('ADP-RFQ-2026-014')).closest('tr')!
    expect(within(row).getByText('—')).toBeInTheDocument()
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

    // The heading, not just any occurrence: the breadcrumb names the item too.
    expect(
      await screen.findByRole('heading', { name: 'Gas generator' }),
    ).toBeInTheDocument()
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

  // BD-3: raising an RFQ from this screen came off. An item's shortlist is
  // now assembled in the draft above this card, before any RFQ exists — the
  // door into actually raising one is the project screen, which is where the
  // selection this form used to skip (`itemIds={[itemId]}`) has to be ticked
  // for more than one item anyway. The card still says which RFQs cover the
  // item; only the affordance to create one here is gone.
  it('has no Raise RFQ control on the covering-RFQ card', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({
        items: [GENERATOR],
        rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])],
      }),
    )

    renderItem()

    const card = await screen.findByRole('region', { name: /RFQs covering/i })
    expect(
      within(card).queryByRole('button', { name: /raise rfq/i }),
    ).not.toBeInTheDocument()
    expect(within(card).queryByLabelText('Reference')).not.toBeInTheDocument()
  })

  it('has no Raise RFQ control when the item has no covering RFQ either', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())

    renderItem()

    const card = await screen.findByRole('region', { name: /RFQs covering/i })
    expect(
      within(card).queryByRole('button', { name: /raise rfq/i }),
    ).not.toBeInTheDocument()
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
    // Twice now, and both are wanted: the header sub-line for context, and the
    // breadcrumb as the way back up. Assert on the count so neither can vanish
    // silently.
    expect(
      await screen.findAllByText(new RegExp(HALIBA_PROJECT.name)),
    ).toHaveLength(2)
  })

  describe('getting back out (BUG-016)', () => {
    it('offers a breadcrumb to the project and to the roster', async () => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({ items: [GENERATOR, CABLE] }),
      )
      renderItem()

      const crumbs = await screen.findByRole('navigation', { name: /breadcrumb/i })
      expect(crumbs).toHaveTextContent('Projects & items')
      expect(crumbs).toHaveTextContent(HALIBA_PROJECT.name)
      expect(crumbs).toHaveTextContent(GENERATOR.item_type)
    })

    it('goes up one level to the project from the breadcrumb', async () => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
      const { onBack } = renderItem()

      const crumbs = await screen.findByRole('navigation', { name: /breadcrumb/i })
      fireEvent.click(within(crumbs).getByRole('button', { name: HALIBA_PROJECT.name }))

      expect(onBack).toHaveBeenCalledTimes(1)
    })

    it('goes up two levels to the roster from the breadcrumb', async () => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
      const { onHome, onBack } = renderItem()

      const crumbs = await screen.findByRole('navigation', { name: /breadcrumb/i })
      fireEvent.click(within(crumbs).getByRole('button', { name: /projects & items/i }))

      expect(onHome).toHaveBeenCalledTimes(1)
      expect(onBack).not.toHaveBeenCalled()
    })

    it('does not make the current item a link', async () => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
      renderItem()

      const crumbs = await screen.findByRole('navigation', { name: /breadcrumb/i })
      // The last crumb is where you already are, so it must not be a control.
      expect(
        within(crumbs).queryByRole('button', { name: GENERATOR.item_type }),
      ).not.toBeInTheDocument()
      expect(within(crumbs).getByText(GENERATOR.item_type)).toHaveAttribute(
        'aria-current',
        'page',
      )
    })

    it('still offers the explicit back button, marked as going back', async () => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
      const { onBack } = renderItem()

      fireEvent.click(await screen.findByRole('button', { name: /back to project/i }))

      expect(onBack).toHaveBeenCalledTimes(1)
    })

    it('can still get out when the item is gone', async () => {
      // The stale-id branch renders its own header, and used to be the one
      // place with no trail at all.
      vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [] }))
      const { onHome } = renderItem('itm_missing')

      const crumbs = await screen.findByRole('navigation', { name: /breadcrumb/i })
      fireEvent.click(within(crumbs).getByRole('button', { name: /projects & items/i }))

      expect(onHome).toHaveBeenCalledTimes(1)
    })
  })
  // BD-3: `the raise-RFQ form` describe block came out with it — its five
  // tests drove `RaiseRfqForm` reached through this screen's own Raise RFQ
  // button, which no longer exists here. `ProjectDetail.test.tsx` already
  // exercises the same form (the discipline picker, its product groups, the
  // budget field, the package placeholder) on the door it now belongs to
  // exclusively, so nothing here loses coverage.

  describe('the available vendor list', () => {
    /** Answers the scoped call and the master-list call separately. */
    function serve(byDiscipline: Record<string, AvailableBidders>, whole: AvailableBidders) {
      vi.mocked(fetchAvailableBidders).mockImplementation((discipline?: string) =>
        Promise.resolve(
          discipline ? (byDiscipline[discipline] ?? availableList()) : whole,
        ),
      )
    }

    const RAS_DANA = {
      ...APPROVED_BIDDER,
      id: 'bdr_rasdana',
      name: 'Ras Dana Cables & Conductors',
      trade_categories: ['CABLES - MV (UP TO 33KV)POWER TRANSMISSION'],
    }

    it('narrows to the discipline the item carries', async () => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({ items: [{ ...GENERATOR, discipline: 'Cables' }] }),
      )
      serve(
        { Cables: availableList({ bidders: [RAS_DANA], discipline: 'Cables' }) },
        availableList({ bidders: [APPROVED_BIDDER, RAS_DANA] }),
      )

      renderItem()

      const card = await screen.findByRole('region', { name: /available vendor/i })
      expect(
        await within(card).findByText('Ras Dana Cables & Conductors'),
      ).toBeInTheDocument()
      // The heading names the discipline it narrowed to.
      expect(
        within(card).getByRole('heading', { name: /Available vendors for Cables/ }),
      ).toBeInTheDocument()
      // The second argument is the approvals required. `undefined` on the
      // first read means "the server's own default" — the card does not send
      // a list until the reader unticks a chip.
      expect(fetchAvailableBidders).toHaveBeenCalledWith('Cables', undefined)
      expect(
        within(card).queryByText('Al Munara Switchgear LLC'),
      ).not.toBeInTheDocument()
    })

    it('falls back to the whole list when the discipline matches nobody', async () => {
      // The items that predate the vocabulary carry free text like "1". An
      // empty table would report the item as uncoverable; it is unscoped.
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({ items: [{ ...GENERATOR, discipline: '1' }] }),
      )
      serve({}, availableList({ bidders: [APPROVED_BIDDER, RAS_DANA] }))

      renderItem()

      const card = await screen.findByRole('region', { name: /available vendor/i })
      expect(
        await within(card).findByText(/No available vendor is registered for "1"/),
      ).toBeInTheDocument()
      expect(within(card).getByText('Al Munara Switchgear LLC')).toBeInTheDocument()
      expect(within(card).getByText('Ras Dana Cables & Conductors')).toBeInTheDocument()
    })

    it('says so plainly when the item has no discipline at all', async () => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({ items: [{ ...GENERATOR, discipline: '' }] }),
      )
      serve({}, availableList({ bidders: [APPROVED_BIDDER] }))

      renderItem()

      const card = await screen.findByRole('region', { name: /available vendor/i })
      expect(
        await within(card).findByText(/no discipline, so every available vendor is shown/i),
      ).toBeInTheDocument()
    })

    it('caps the rows and says the real total', async () => {
      const many = Array.from({ length: 40 }, (_, i) => ({
        ...APPROVED_BIDDER,
        id: `bdr_${i}`,
        name: `Vendor ${String(i).padStart(3, '0')}`,
      }))
      vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
      serve({}, availableList({ bidders: many, total: 1346 }))

      renderItem()

      const card = await screen.findByRole('region', { name: /available vendor/i })
      expect(await within(card).findByText('Vendor 000')).toBeInTheDocument()
      expect(within(card).queryByText('Vendor 030')).not.toBeInTheDocument()
      // The count is the server's total, never the number of rows drawn.
      expect(
        within(card).getByText(/1346 vendors approved by ADNOC and Astra/),
      ).toBeInTheDocument()
      expect(within(card).getByText(/Showing the first 25/)).toBeInTheDocument()
    })

    it('filters on vendor, product group or manufacturer', async () => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
      serve(
        {},
        availableList({
          bidders: [
            APPROVED_BIDDER,
            {
              ...APPROVED_BIDDER,
              id: 'bdr_2',
              name: 'Marjan Industrial Development LLC',
              represented_manufacturers: ['ROSEMOUNT'],
            },
          ],
        }),
      )

      renderItem()

      const card = await screen.findByRole('region', { name: /available vendor/i })
      await within(card).findByText('Al Munara Switchgear LLC')
      fireEvent.change(within(card).getByLabelText('Search vendors'), {
        target: { value: 'rosemount' },
      })

      expect(
        within(card).getByText('Marjan Industrial Development LLC'),
      ).toBeInTheDocument()
      expect(
        within(card).queryByText('Al Munara Switchgear LLC'),
      ).not.toBeInTheDocument()
      expect(within(card).getByText(/1 of 2 vendors match/)).toBeInTheDocument()
    })

    it('says the list is empty rather than showing a blank table', async () => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
      serve({}, availableList({ bidders: [] }))

      renderItem()

      const card = await screen.findByRole('region', { name: /available vendor/i })
      // Names the reason, not just the emptiness: one approval is not enough.
      expect(
        await within(card).findByText(/carries both approvals/i),
      ).toBeInTheDocument()
    })
  })

  // BD-3: raising an RFQ from this screen came off — see `has no Raise RFQ
  // control on the covering-RFQ card` above. The two tests that drove the
  // form's presence here (`covers exactly this item, without a selection
  // step` and `shows the new RFQ in the covering list once it is created`)
  // were deleted, not skipped: `RaiseRfqForm` is no longer reachable from
  // this screen at all, and `ProjectDetail.test.tsx` already covers the form
  // itself on the door it now belongs to exclusively.

  /* ------------------------------------------------- adding one by hand */
  //
  // The two curated lists carry add and remove controls; the two uploads carry
  // neither. That mirrors the server's refusal rather than relying on it: an
  // uploaded row is part of a document, so a control that produced a 422 every
  // time would read as broken rather than as deliberate.

  describe('a vendor added by hand', () => {
    function openManual() {
      return screen.findByRole('region', { name: /added by hand/i })
    }

    it('sends the name typed into its own card', async () => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({ items: [GENERATOR], item_vendor_lists: { itm_1: vendorLists() } }),
      )
      vi.mocked(addItemVendor).mockResolvedValue(
        vendorEntry({ source: 'Manual', vendor_name: 'Gulf Cable Works LLC' }),
      )

      renderItem()
      const card = await openManual()
      fireEvent.change(within(card).getByLabelText(/vendor name/i), {
        target: { value: 'Gulf Cable Works LLC' },
      })
      fireEvent.click(within(card).getByRole('button', { name: /^add vendor$/i }))

      await waitFor(() =>
        expect(addItemVendor).toHaveBeenCalledWith('prj_1', 'itm_1', {
          vendor_name: 'Gulf Cable Works LLC',
          source: 'Manual',
        }),
      )
    })

    it('refuses a blank name without asking the server', async () => {
      // Guarded here as well as on the server. A round trip to be told the
      // obvious is a worse answer than not making it, and the server's refusal
      // still stands for anything that reaches it another way.
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({ items: [GENERATOR], item_vendor_lists: { itm_1: vendorLists() } }),
      )

      renderItem()
      const card = await openManual()
      fireEvent.change(within(card).getByLabelText(/vendor name/i), {
        target: { value: '   ' },
      })
      fireEvent.click(within(card).getByRole('button', { name: /^add vendor$/i }))

      expect(addItemVendor).not.toHaveBeenCalled()
      expect(await within(card).findByRole('alert')).toHaveTextContent(/name/i)
    })

    it('shows the server sentence when the add is refused', async () => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({ items: [GENERATOR], item_vendor_lists: { itm_1: vendorLists() } }),
      )
      vi.mocked(addItemVendor).mockRejectedValue(
        new Error('Client is an uploaded list - re-upload the corrected export.'),
      )

      renderItem()
      const card = await openManual()
      fireEvent.change(within(card).getByLabelText(/vendor name/i), {
        target: { value: 'Gulf Cable Works LLC' },
      })
      fireEvent.click(within(card).getByRole('button', { name: /^add vendor$/i }))

      expect(await within(card).findByRole('alert')).toHaveTextContent(
        /re-upload the corrected export/i,
      )
    })

    it('removes one by id and re-reads the screen', async () => {
      const kept = vendorEntry({ id: 'ive_keep', source: 'Manual', vendor_name: 'Same Name' })
      const going = vendorEntry({ id: 'ive_go', source: 'Manual', vendor_name: 'Same Name' })
      vi.mocked(fetchWorkflowProject)
        .mockResolvedValueOnce(
          detail({
            items: [GENERATOR],
            item_vendor_lists: { itm_1: vendorLists({ Manual: [going, kept] }) },
          }),
        )
        .mockResolvedValue(
          detail({
            items: [GENERATOR],
            item_vendor_lists: { itm_1: vendorLists({ Manual: [kept] }) },
          }),
        )
      vi.mocked(removeItemVendor).mockResolvedValue(undefined)

      renderItem()
      const card = await openManual()
      // By id, never by name or position - two suppliers can share a trading
      // name, which is why both fixture rows carry the same one.
      fireEvent.click(
        (await within(card).findAllByRole('button', { name: /^remove Same Name$/i }))[0],
      )

      await waitFor(() =>
        expect(removeItemVendor).toHaveBeenCalledWith('prj_1', 'itm_1', 'ive_go'),
      )
      await waitFor(() =>
        expect(within(card).getAllByText('Same Name')).toHaveLength(1),
      )
    })

    it('reports no registry finding, because nobody looked one up', async () => {
      // A curated row is unlinked *by construction* — the server never looks a
      // typed name up, because a match would attach a real company's approvals
      // to it. Rendering "Not in the registry" here would report a check
      // nobody ran, which is the `null` vs `false` distinction the shortlist's
      // client-approval column already keeps. The column is absent instead.
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({
          items: [GENERATOR],
          item_vendor_lists: {
            itm_1: vendorLists({
              Manual: [vendorEntry({ source: 'Manual', vendor_id: null, vendor_name: 'Hand Co' })],
            }),
          },
        }),
      )

      renderItem()
      const card = await openManual()

      expect(within(card).queryByText(/not in the registry/i)).not.toBeInTheDocument()
      expect(
        within(card).queryByRole('columnheader', { name: /registry/i }),
      ).not.toBeInTheDocument()
      // And no tally that counts the same non-lookup as a miss.
      expect(within(card).queryByText(/not found/i)).not.toBeInTheDocument()
    })

    it('does not let a discipline expansion take over the row', async () => {
      // Measured in a real browser, which is the only place this is visible:
      // eleven cable product groups rendered in full made the cell 595px wide,
      // crushed the vendor name to 67px and every row to 170px tall. jsdom
      // applies no stylesheet and does no layout, so what is asserted here is
      // the structure that caused it — the same shape the available-vendor
      // card already uses, and the second instance of this defect on this
      // screen after the 89px shortlist button.
      const many = Array.from({ length: 11 }, (_, i) => `CABLES - GROUP ${i + 1}`)
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({
          items: [GENERATOR],
          item_vendor_lists: {
            itm_1: vendorLists({
              Manual: [
                vendorEntry({ source: 'Manual', vendor_name: 'Hand Co', trade_categories: many }),
              ],
            }),
          },
        }),
      )

      renderItem()
      const card = await openManual()
      const row = (await within(card).findByText('Hand Co')).closest('tr')!

      expect(within(row).getByText(/\+9 more/)).toBeInTheDocument()
      expect(within(row).queryByText(/CABLES - GROUP 11/)).not.toBeInTheDocument()
    })

    it('shows a refused removal beside the row it refused', async () => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({
          items: [GENERATOR],
          item_vendor_lists: {
            itm_1: vendorLists({
              Manual: [vendorEntry({ id: 'ive_1', source: 'Manual', vendor_name: 'Kept Co' })],
            }),
          },
        }),
      )
      vi.mocked(removeItemVendor).mockRejectedValue(new Error('Nope, still here.'))

      renderItem()
      const card = await openManual()
      fireEvent.click(within(card).getByRole('button', { name: /^remove Kept Co$/i }))

      const row = (await within(card).findByText('Kept Co')).closest('tr')!
      expect(within(row).getByRole('alert')).toHaveTextContent('Nope, still here.')
      // The row is still there: a refused removal removes nothing.
      expect(within(card).getByText('Kept Co')).toBeInTheDocument()
    })
  })

  describe('the uploaded cards', () => {
    it('carry no add or remove control', async () => {
      // Matching the store's refusal rather than relying on it. Both uploads,
      // because the guard is per source and a card that only got it right for
      // the client's list would look identical on screen.
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({
          items: [GENERATOR],
          item_vendor_lists: {
            itm_1: vendorLists({
              Client: [vendorEntry({ vendor_name: 'CLIENT CO' })],
              Astra: [vendorEntry({ id: 'ive_2', source: 'Astra', vendor_name: 'ASTRA CO' })],
            }),
          },
        }),
      )

      renderItem()

      for (const name of [/client list/i, /astra list/i]) {
        const card = await screen.findByRole('region', { name })
        // Opened first, and this one matters more than most: an upload holding
        // rows starts folded, and a `hidden` body answers "not in the document"
        // to every role query. Left folded, this test would pass just as
        // happily against a card that *had* grown a Remove control.
        fireEvent.click(within(card).getByRole('button', { name: /show/i }))
        expect(within(card).getByRole('table')).toBeInTheDocument()

        expect(
          within(card).queryByRole('button', { name: /^remove /i }),
        ).not.toBeInTheDocument()
        expect(
          within(card).queryByRole('button', { name: /^add vendor$/i }),
        ).not.toBeInTheDocument()
      }
    })
  })

  /* --------------------------------------------------- collapsing a list */

  describe('collapsing a vendor list', () => {
    it('offers the control on all four lists and on the registry card', async () => {
      // Per card rather than one control over the lot: a buyer folding the
      // client's 79-row export away is usually doing it to see the two curated
      // cards under it, so collapsing all five together would defeat the point.
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({ items: [GENERATOR] }),
      )

      renderItem()

      for (const name of [
        /client list/i,
        /astra list/i,
        /added by hand/i,
        /suggested vendors/i,
        /available vendors/i,
      ]) {
        const card = await screen.findByRole('region', { name })
        // Either label: which way a card starts is the next test's business,
        // and asserting "Hide" here would tie this one to that default.
        expect(
          within(card).getByRole('button', { name: /hide|show/i }),
        ).toBeInTheDocument()
      }
    })

    it('takes the rows away and leaves the heading', async () => {
      // Driven through a curated card, which is the one that starts open.
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({
          items: [GENERATOR],
          item_vendor_lists: {
            itm_1: vendorLists({
              Manual: [vendorEntry({ source: 'Manual', vendor_id: null })],
            }),
          },
        }),
      )

      renderItem()

      const card = await screen.findByRole('region', { name: /added by hand/i })
      expect(within(card).getByRole('table')).toBeInTheDocument()

      fireEvent.click(within(card).getByRole('button', { name: /hide/i }))

      expect(within(card).queryByRole('table')).not.toBeInTheDocument()
      // Still findable by name, so the card announces itself collapsed rather
      // than disappearing — and the count is what it says while folded away.
      expect(
        await screen.findByRole('region', { name: /added by hand/i }),
      ).toBeInTheDocument()
    })

    it('starts an uploaded list folded once it holds rows', async () => {
      // The two exports are what push everything under them off the screen —
      // 52 rows each for one discipline on the corpus this was measured on.
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({
          items: [GENERATOR],
          item_vendor_lists: {
            itm_1: vendorLists({
              Client: [vendorEntry()],
              Astra: [
                vendorEntry({ id: 'ive_2', source: 'Astra', vendor_name: 'ASTRA CO' }),
              ],
              Manual: [
                vendorEntry({ id: 'ive_3', source: 'Manual', vendor_id: null }),
              ],
            }),
          },
        }),
      )

      renderItem()

      for (const name of [/client list/i, /astra list/i]) {
        const card = await screen.findByRole('region', { name })
        expect(within(card).queryByRole('table')).not.toBeInTheDocument()
        expect(within(card).getByRole('button', { name: /show/i })).toBeInTheDocument()
      }

      // The curated cards are where the work happens, so they stay open. The
      // exports are folded *so that* these are the first thing under the item.
      const manual = await screen.findByRole('region', { name: /added by hand/i })
      expect(within(manual).getByRole('table')).toBeInTheDocument()
    })

    it('leaves an empty upload open, because its body is the instructions', async () => {
      // An empty card's body is the sentence telling the reader to edit the
      // item and load the export. Folding that away leaves a card saying only
      // that it is empty, which is the one state where the fold costs the
      // reader the thing they needed.
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({ items: [GENERATOR], item_vendor_lists: { itm_1: vendorLists() } }),
      )

      renderItem()

      const card = await screen.findByRole('region', { name: /client list/i })
      expect(within(card).getByRole('button', { name: /hide/i })).toBeInTheDocument()
      expect(
        within(card).getByRole('heading', { name: /nothing on the client list yet/i }),
      ).toBeInTheDocument()
    })

    it('opens a folded upload again', async () => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({
          items: [GENERATOR],
          item_vendor_lists: {
            itm_1: vendorLists({ Client: [vendorEntry({ vendor_name: 'CLIENT CO' })] }),
          },
        }),
      )

      renderItem()

      const card = await screen.findByRole('region', { name: /client list/i })
      fireEvent.click(within(card).getByRole('button', { name: /show/i }))

      expect(within(card).getByRole('table')).toBeInTheDocument()
      expect(within(card).getByText('CLIENT CO')).toBeInTheDocument()
    })

    it('counts the rows in the heading, so a collapsed list still says how big it is', async () => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({
          items: [GENERATOR],
          item_vendor_lists: {
            itm_1: vendorLists({
              Client: [
                vendorEntry(),
                vendorEntry({ id: 'ive_2', vendor_name: 'SECOND CO' }),
              ],
            }),
          },
        }),
      )

      renderItem()

      const card = await screen.findByRole('region', { name: /client list/i })
      expect(within(card).getByRole('heading')).toHaveTextContent(/client list\s*2/i)
    })

    it('folds and unfolds each list on its own', async () => {
      // Both exports start folded, so unfolding one has to leave the other
      // where it was: a buyer opens the client's list to read it *against* the
      // Astra one, and a single shared flag would open both.
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({
          items: [GENERATOR],
          item_vendor_lists: {
            itm_1: vendorLists({
              Client: [vendorEntry({ vendor_name: 'CLIENT CO' })],
              Astra: [
                vendorEntry({ id: 'ive_2', source: 'Astra', vendor_name: 'ASTRA CO' }),
              ],
            }),
          },
        }),
      )

      renderItem()

      const client = await screen.findByRole('region', { name: /client list/i })
      fireEvent.click(within(client).getByRole('button', { name: /show/i }))

      expect(within(client).getByRole('table')).toBeInTheDocument()
      const astra = await screen.findByRole('region', { name: /astra list/i })
      expect(within(astra).queryByRole('table')).not.toBeInTheDocument()
    })
  })

  /* ------------------------------------------------- the suggestion card */

  describe('the suggested vendors card', () => {
    function openSuggested() {
      return screen.findByRole('region', { name: /suggested vendors/i })
    }

    beforeEach(() => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({ items: [GENERATOR], item_vendor_lists: { itm_1: vendorLists() } }),
      )
    })

    it('says it is the model and not a web search', async () => {
      // The honest-label rule. These providers cannot browse, so a caption
      // claiming a search would put a synthesised fact where a recorded one is
      // expected - the failure the AVL import, the mock rounds and the RFQ
      // extractor all guard against.
      renderItem()

      const card = await openSuggested()
      expect(within(card).getByText(/training data/i)).toBeInTheDocument()
      expect(within(card).getByText(/not a web search/i)).toBeInTheDocument()
      expect(within(card).getByText(/verify each company/i)).toBeInTheDocument()
    })

    it('has no "From the internet" chip left anywhere on the screen', async () => {
      // Replaced rather than enabled. It promised a search nothing performs.
      renderItem()
      await openSuggested()

      expect(screen.queryByText(/from the internet/i)).not.toBeInTheDocument()
      expect(
        screen.queryByText(/searching the web for vendors is not built yet/i),
      ).not.toBeInTheDocument()
    })

    it('lists what the model named, with its reason', async () => {
      vi.mocked(suggestItemVendors).mockResolvedValue({
        vendors: [suggestion({ name: 'DUCAB HV CABLE', basis: 'A UAE cable maker.' })],
      })

      renderItem()
      const card = await openSuggested()
      fireEvent.click(within(card).getByRole('button', { name: /suggest vendors/i }))

      expect(await within(card).findByText('DUCAB HV CABLE')).toBeInTheDocument()
      expect(within(card).getByText(/A UAE cable maker\./)).toBeInTheDocument()
    })

    it('adds one at a time and offers no bulk accept', async () => {
      // Accepting twenty unverified companies in one click is precisely the
      // act that needs friction, so each row has its own Add and there is no
      // control that takes them all.
      vi.mocked(suggestItemVendors).mockResolvedValue({
        vendors: [
          suggestion({ name: 'DUCAB HV CABLE', basis: 'A UAE cable maker.' }),
          suggestion({ name: 'JEDDAH CABLES', basis: 'A Saudi cable maker.' }),
        ],
      })
      vi.mocked(addItemVendor).mockResolvedValue(
        vendorEntry({ source: 'Suggested', vendor_name: 'DUCAB HV CABLE' }),
      )

      renderItem()
      const card = await openSuggested()
      fireEvent.click(within(card).getByRole('button', { name: /suggest vendors/i }))

      expect(
        await within(card).findAllByRole('button', { name: /^add /i }),
      ).toHaveLength(2)
      expect(
        within(card).queryByRole('button', { name: /add all|add selected|add these/i }),
      ).not.toBeInTheDocument()

      fireEvent.click(within(card).getByRole('button', { name: /^add DUCAB HV CABLE$/i }))

      await waitFor(() =>
        expect(addItemVendor).toHaveBeenCalledWith('prj_1', 'itm_1', {
          vendor_name: 'DUCAB HV CABLE',
          source: 'Suggested',
          note: 'A UAE cable maker.',
        }),
      )
    })

    it('shows a failed ask rather than an empty list', async () => {
      // An outage and "no such companies exist" must not look the same - the
      // distinction the covering-RFQ summary keeps between a dash and "Nobody
      // invited yet".
      vi.mocked(suggestItemVendors).mockRejectedValue(
        new Error('The model could not be asked for vendors: provider is down'),
      )

      renderItem()
      const card = await openSuggested()
      fireEvent.click(within(card).getByRole('button', { name: /suggest vendors/i }))

      expect(await within(card).findByRole('alert')).toHaveTextContent(
        /provider is down/i,
      )
      expect(within(card).queryByText(/named nobody/i)).not.toBeInTheDocument()
    })

    it('says so plainly when the model names nobody', async () => {
      vi.mocked(suggestItemVendors).mockResolvedValue({ vendors: [] })

      renderItem()
      const card = await openSuggested()
      fireEvent.click(within(card).getByRole('button', { name: /suggest vendors/i }))

      expect(await within(card).findByText(/named nobody/i)).toBeInTheDocument()
      expect(within(card).queryByRole('alert')).not.toBeInTheDocument()
    })
  })

  /* ------------------------------------------ the four-source vendor pool */

  describe('the four-source vendor pool', () => {
    /** A company somebody typed in. No `vendor_id`, ever: the server never
     *  looks a curated name up, because a match would attach a real company's
     *  approvals to whatever was typed. */
    const GULF = vendorEntry({
      id: 'ive_gulf',
      source: 'Manual',
      vendor_id: null,
      vendor_name: 'Gulf Crescent Fabricators',
      trade_categories: ['STRUCTURAL STEEL FABRICATION'],
      source_document: 'added by hand by buyer@example.com',
    })

    /** A company a model named, kept labelled as one after a person accepted
     *  it — that it originated with a model is the thing a later reader would
     *  most want to know. */
    const MODELLED = vendorEntry({
      id: 'ive_ducab',
      source: 'Suggested',
      vendor_id: null,
      vendor_name: 'DUCAB HV CABLE',
      trade_categories: ['CABLES - HV POWER TRANSMISSION'],
      source_document: 'suggested by the model',
    })

    function serveItem(
      lists: Partial<Record<VendorListSource, ItemVendorEntry[]>> = {},
      over: Partial<WorkflowProjectDetail> = {},
    ) {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({
          items: [GENERATOR],
          item_vendor_lists: { itm_1: vendorLists(lists) },
          ...over,
        }),
      )
      vi.mocked(fetchAvailableBidders).mockResolvedValue(
        availableList({ bidders: [APPROVED_BIDDER] }),
      )
    }

    /** The available-vendor card, once its first registry read has landed. */
    async function pool(): Promise<HTMLElement> {
      renderItem()
      const card = await screen.findByRole('region', { name: /available vendor/i })
      await within(card).findByText('Al Munara Switchgear LLC')
      return card
    }

    const chip = (card: HTMLElement, name: string) =>
      within(card).getByRole('button', { name })

    it('offers a chip for each of the four sources', async () => {
      serveItem()
      const card = await pool()

      // Two registry approvals and two curated lists. Different universes, and
      // the chips are the only place they meet.
      for (const label of ['ADNOC', 'Astra', 'Added by hand', 'Suggested vendors']) {
        expect(chip(card, label)).toBeInTheDocument()
      }
    })

    it('spells the curated chips as written rather than in title case', async () => {
      // `.chip` carries `text-transform: capitalize`, which is right for the
      // single-word verdict chips it was written for and renders "Added by
      // hand" as "Added By Hand". jsdom applies no stylesheet, so the class is
      // the only stand-in a test has for the rendering.
      serveItem()
      const card = await pool()

      expect(chip(card, 'Added by hand')).toHaveClass('chip--asis')
      expect(chip(card, 'Suggested vendors')).toHaveClass('chip--asis')
    })

    it('opens with the curated chips off, so the registry list is unchanged', async () => {
      serveItem({ Manual: [GULF] })
      const card = await pool()

      expect(chip(card, 'Added by hand')).not.toHaveClass('on')
      expect(
        within(card).queryByText('Gulf Crescent Fabricators'),
      ).not.toBeInTheDocument()
    })

    it('appends a curated list without asking the server for anything', async () => {
      // Additive, not ANDed: a hand-added company has no registry row to
      // approve it, and the rows are already on the page — filtering them is a
      // `.filter()`, never a request.
      serveItem({ Manual: [GULF] })
      const card = await pool()
      const before = vi.mocked(fetchAvailableBidders).mock.calls.length

      fireEvent.click(chip(card, 'Added by hand'))

      expect(
        await within(card).findByText('Gulf Crescent Fabricators'),
      ).toBeInTheDocument()
      expect(within(card).getByText('Al Munara Switchgear LLC')).toBeInTheDocument()
      expect(vi.mocked(fetchAvailableBidders).mock.calls.length).toBe(before)

      fireEvent.click(chip(card, 'Added by hand'))
      expect(
        within(card).queryByText('Gulf Crescent Fabricators'),
      ).not.toBeInTheDocument()
    })

    it('asks the server for nothing when both registry chips are unticked', async () => {
      serveItem({ Manual: [GULF] })
      const card = await pool()

      fireEvent.click(chip(card, 'Added by hand'))
      fireEvent.click(chip(card, 'ADNOC'))
      await waitFor(() =>
        expect(fetchAvailableBidders).toHaveBeenCalledWith('Electrical', ['Astra']),
      )
      vi.mocked(fetchAvailableBidders).mockClear()

      fireEvent.click(chip(card, 'Astra'))

      // The endpoint 422s on an empty approver list and that refusal is right:
      // the browser contributes no registry rows rather than asking for none.
      expect(
        await within(card).findByText('Gulf Crescent Fabricators'),
      ).toBeInTheDocument()
      await waitFor(() =>
        expect(
          within(card).queryByText('Al Munara Switchgear LLC'),
        ).not.toBeInTheDocument(),
      )
      expect(fetchAvailableBidders).not.toHaveBeenCalled()
    })

    it('shows a company held by the registry and by hand twice, once per source', async () => {
      // No deduplication by name, ever. This repository has recorded name
      // matching as a shipped defect twice, and merging the two rows in the
      // view is that defect wearing a different hat.
      serveItem({
        Manual: [
          vendorEntry({
            id: 'ive_twin',
            source: 'Manual',
            vendor_id: null,
            vendor_name: 'Al Munara Switchgear LLC',
          }),
        ],
      })
      const card = await pool()

      fireEvent.click(chip(card, 'Added by hand'))

      const both = await within(card).findAllByText('Al Munara Switchgear LLC')
      expect(both).toHaveLength(2)
      // As a set, not positionally. Which half is drawn first is a separate
      // rule with its own test below, and pinning it here as well would make
      // one change break two tests for two unrelated reasons.
      const labels = both.map(
        (cell) =>
          within(cell.closest('tr')!).getByText(
            /^(Registry|Added by hand|Suggested vendors)$/,
          ).textContent,
      )
      expect([...labels].sort()).toEqual(['Added by hand', 'Registry'])
    })

    it('draws a curated row even when the registry fills the cap', async () => {
      // The ordering rule, and the reason it is not a matter of taste. The
      // table draws 25 rows of a registry list that runs to 79 for one
      // discipline and 1 346 unnarrowed. Appended *after* that, an item's one
      // or two hand-added companies fall past the cap and are never drawn — so
      // ticking the chip reads as doing nothing at all.
      //
      // Found by ticking the chip in a browser against a real 79-vendor list.
      // jsdom sees no cap it was not handed, which is exactly why this test
      // hands it one: with the default single-bidder fixture, curated-last
      // passes and the defect ships.
      serveItem({ Manual: [GULF] })
      vi.mocked(fetchAvailableBidders).mockResolvedValue(
        availableList({
          bidders: Array.from({ length: 40 }, (_, i) => ({
            ...APPROVED_BIDDER,
            id: `bdr_${i}`,
            name: `Registry Vendor ${i}`,
          })),
        }),
      )
      renderItem()
      const card = await screen.findByRole('region', { name: /available vendor/i })
      await within(card).findByText('Registry Vendor 0')

      fireEvent.click(chip(card, 'Added by hand'))

      expect(
        await within(card).findByText('Gulf Crescent Fabricators'),
      ).toBeInTheDocument()
    })

    it('states the source of every row', async () => {
      serveItem({ Manual: [GULF], Suggested: [MODELLED] })
      const card = await pool()

      fireEvent.click(chip(card, 'Added by hand'))
      fireEvent.click(chip(card, 'Suggested vendors'))

      const gulf = (
        await within(card).findByText('Gulf Crescent Fabricators')
      ).closest('tr')!
      expect(within(gulf).getByText('Added by hand')).toBeInTheDocument()
      const ducab = within(card).getByText('DUCAB HV CABLE').closest('tr')!
      expect(within(ducab).getByText('Suggested vendors')).toBeInTheDocument()
      const registry = within(card)
        .getByText('Al Munara Switchgear LLC')
        .closest('tr')!
      expect(within(registry).getByText('Registry')).toBeInTheDocument()
    })

    it('will not let the last chip of any kind be unticked', async () => {
      // Four unticked chips is not a query — it is an empty screen that reads
      // as "no vendor qualifies".
      serveItem({ Manual: [GULF] })
      const card = await pool()

      fireEvent.click(chip(card, 'Added by hand'))
      fireEvent.click(chip(card, 'ADNOC'))
      fireEvent.click(chip(card, 'Astra'))

      await waitFor(() => expect(chip(card, 'Added by hand')).toBeDisabled())
      expect(chip(card, 'Added by hand')).toHaveAttribute(
        'title',
        expect.stringMatching(/at least one/i),
      )
    })

    it('searches across the union, not just the registry half', async () => {
      serveItem({ Manual: [GULF] })
      const card = await pool()

      fireEvent.click(chip(card, 'Added by hand'))
      await within(card).findByText('Gulf Crescent Fabricators')
      fireEvent.change(within(card).getByLabelText('Search vendors'), {
        target: { value: 'Munara' },
      })

      // The load-bearing direction: a needle matching the registry row must
      // take the curated row away. A search applied to the registry half alone
      // would leave it sitting there.
      expect(
        within(card).queryByText('Gulf Crescent Fabricators'),
      ).not.toBeInTheDocument()
      expect(within(card).getByText('Al Munara Switchgear LLC')).toBeInTheDocument()
    })

    it('shortlists a curated vendor by name, never by id', async () => {
      serveItem({ Manual: [GULF] }, { rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] })
      vi.mocked(fetchRfq).mockResolvedValue({ ...RFQ_DETAIL, shortlist: [] })
      vi.mocked(addShortlistEntry).mockResolvedValue(
        shortlistEntry({ vendor_id: null, vendor_name: 'Gulf Crescent Fabricators' }),
      )
      const card = await pool()

      fireEvent.click(chip(card, 'Added by hand'))
      fireEvent.click(
        await within(card).findByRole('button', { name: /shortlist gulf crescent/i }),
      )

      await waitFor(() =>
        expect(addShortlistEntry).toHaveBeenCalledWith(
          'rfq_1',
          expect.objectContaining({
            vendor_name: 'Gulf Crescent Fabricators',
            included: true,
          }),
        ),
      )
      // `inviteRegisteredBidder` sends a `vendor_id`, and a curated row has
      // none — sending one would attach a real company's approvals to a name
      // somebody typed.
      expect(inviteRegisteredBidder).not.toHaveBeenCalled()
    })

    it('sends each row of a mixed batch with the right shape for its kind', async () => {
      // The discriminated union, asserted through the batch. A registry row
      // carries its `vendor_id`; a curated one carries `null` and its name.
      // Deriving an id for the curated row would attach a real company's
      // approvals to whatever somebody typed.
      serveItem({ Manual: [GULF] }, { rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] })
      vi.mocked(fetchRfq).mockResolvedValue({ ...RFQ_DETAIL, shortlist: [] })
      vi.mocked(addDraftShortlistPick).mockImplementation(async (_p, _i, body) =>
        draftPick({ ...body, vendor_id: body.vendor_id ?? null }),
      )
      const card = await pool()

      fireEvent.click(chip(card, 'Added by hand'))
      await within(card).findByText('Gulf Crescent Fabricators')
      fireEvent.click(within(card).getByRole('button', { name: /select these 2/i }))
      fireEvent.click(
        within(card).getByRole('button', { name: /shortlist selected \(2\)/i }),
      )

      await waitFor(() =>
        expect(addDraftShortlistPick).toHaveBeenCalledWith('prj_1', 'itm_1', {
          vendor_id: 'bdr_almunara',
          vendor_name: 'Al Munara Switchgear LLC',
          source: 'ADNOC',
        }),
      )
      expect(addDraftShortlistPick).toHaveBeenCalledWith('prj_1', 'itm_1', {
        vendor_id: null,
        vendor_name: 'Gulf Crescent Fabricators',
        source: 'Manual',
      })
    })

    it('keys a selection by source as well as by id', async () => {
      // A bidder id and a vendor-list entry id are different id spaces. Keyed
      // on the bare id these two rows would be one selection.
      serveItem({
        Manual: [
          vendorEntry({
            id: 'bdr_almunara',
            source: 'Manual',
            vendor_id: null,
            vendor_name: 'Gulf Crescent Fabricators',
          }),
        ],
      })
      const card = await pool()

      fireEvent.click(chip(card, 'Added by hand'))
      fireEvent.click(
        await within(card).findByRole('checkbox', {
          name: 'Select Gulf Crescent Fabricators',
        }),
      )
      fireEvent.click(
        within(card).getByRole('checkbox', { name: 'Select Al Munara Switchgear LLC' }),
      )

      expect(within(card).getByText(/2 selected/)).toBeInTheDocument()
    })

    it("caps a curated row's product groups at two", async () => {
      // Rendered in full, an eleven-group discipline expansion ran that cell to
      // 595px, crushed the vendor name to 67px and made every row 170px tall.
      // jsdom sees none of that; the structure is what a test can hold.
      serveItem({
        Manual: [
          vendorEntry({
            id: 'ive_wide',
            source: 'Manual',
            vendor_id: null,
            vendor_name: 'Wide Load Trading',
            trade_categories: Array.from({ length: 11 }, (_, i) => `GROUP ${i}`),
          }),
        ],
      })
      const card = await pool()

      fireEvent.click(chip(card, 'Added by hand'))

      const row = (await within(card).findByText('Wide Load Trading')).closest('tr')!
      expect(
        within(row).getByText('GROUP 0 · GROUP 1 · +9 more'),
      ).toBeInTheDocument()
    })
  })

  /* ------------------------------------------------- opening a covering RFQ */

  describe('the covering RFQ table', () => {
    it('opens a covering RFQ from the control at the end of the page', async () => {
      // The accessible name is the reference, not the glyph - the same rule the
      // vendor row button follows: the chevron is decoration and the name is
      // what a screen reader and a test read. It used to be a one-character
      // control inside the row; it is the same act from the foot of the page.
      const onOpenRfq = vi.fn()
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({
          items: [GENERATOR],
          rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])],
          item_vendor_lists: { itm_1: vendorLists() },
        }),
      )

      render(
        <ItemDetail
          projectId="prj_1"
          itemId="itm_1"
          onBack={vi.fn()}
          onHome={vi.fn()}
          onOpenRfq={onOpenRfq}
        />,
      )

      fireEvent.click(
        await screen.findByRole('button', { name: /open ADP-RFQ-2026-014/i }),
      )

      expect(onOpenRfq).toHaveBeenCalledWith('rfq_1')
    })

    // Two covering RFQs are two next steps, and nothing on this screen could
    // pick between them without inventing a rule the reader cannot see.
    it('draws one control per covering RFQ', async () => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({
          items: [GENERATOR],
          rfqs: [
            rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1']),
            rfq('rfq_2', 'ADP-RFQ-2026-015', ['itm_1']),
          ],
          item_vendor_lists: { itm_1: vendorLists() },
        }),
      )

      render(
        <ItemDetail projectId="prj_1" itemId="itm_1" onBack={vi.fn()} onHome={vi.fn()}
                    onOpenRfq={vi.fn()} />,
      )

      expect(await screen.findByRole('button', { name: /open ADP-RFQ-2026-014/i })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /open ADP-RFQ-2026-015/i })).toBeInTheDocument()
    })

    // The control moved rather than being added beside the old one. Two
    // buttons opening the same RFQ is not a second way in, it is the same way
    // in twice -- and it would break every `getByRole` query for that name.
    it('leaves no open control inside the table rows', async () => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({
          items: [GENERATOR],
          rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])],
          item_vendor_lists: { itm_1: vendorLists() },
        }),
      )

      render(
        <ItemDetail projectId="prj_1" itemId="itm_1" onBack={vi.fn()} onHome={vi.fn()}
                    onOpenRfq={vi.fn()} />,
      )

      const opener = await screen.findByRole('button', { name: /open ADP-RFQ-2026-014/i })
      expect(opener.closest('table')).toBeNull()
      expect(opener.closest('.page-next')).not.toBeNull()
    })

    it('offers no way onward when no RFQ covers the item', async () => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({ items: [GENERATOR], rfqs: [], item_vendor_lists: { itm_1: vendorLists() } }),
      )

      render(
        <ItemDetail projectId="prj_1" itemId="itm_1" onBack={vi.fn()} onHome={vi.fn()}
                    onOpenRfq={vi.fn()} />,
      )

      await screen.findByRole('region', { name: /RFQs covering/i })
      expect(screen.queryByRole('button', { name: /^open /i })).toBeNull()
    })
  })
})

describe('the shortlist draft an item owns', () => {
  // BD-2 front end. Ticking vendors in the pool and shortlisting them writes
  // against the **item**, not against an RFQ — the buyer assembles a selection
  // before raising anything, and it has to survive a reload.
  beforeEach(() => {
    vi.mocked(fetchDisciplines).mockResolvedValue([
      { name: 'Cables', product_groups: ['CABLES - LV POWER DISTRIBUTION'] },
    ])
    vi.mocked(addDraftShortlistPick).mockImplementation(async (_p, _i, body) =>
      draftPick({ ...body, vendor_id: body.vendor_id ?? null }),
    )
    vi.mocked(removeDraftShortlistPick).mockResolvedValue(undefined)
  })

  function serve(over: Partial<WorkflowProjectDetail> = {}, bidders = [APPROVED_BIDDER]) {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], item_vendor_lists: { itm_1: vendorLists() }, ...over }),
    )
    vi.mocked(fetchAvailableBidders).mockResolvedValue(availableList({ bidders }))
  }

  async function poolCard(): Promise<HTMLElement> {
    renderItem()
    const card = await screen.findByRole('region', { name: /available vendor/i })
    await within(card).findByText('Al Munara Switchgear LLC')
    return card
  }

  it('shortlists the selected vendors with no RFQ covering the item', async () => {
    // The requirement in one test: shortlisting happens *before* an RFQ exists.
    // The card used to withhold the control entirely without a covering RFQ.
    serve({ rfqs: [] })
    const card = await poolCard()

    fireEvent.click(within(card).getByLabelText('Select Al Munara Switchgear LLC'))
    fireEvent.click(within(card).getByRole('button', { name: /Shortlist selected/ }))

    await waitFor(() => expect(addDraftShortlistPick).toHaveBeenCalledTimes(1))
    expect(addDraftShortlistPick).toHaveBeenCalledWith('prj_1', 'itm_1', {
      vendor_id: 'bdr_almunara',
      vendor_name: 'Al Munara Switchgear LLC',
      source: 'ADNOC',
    })
  })

  it('sends a curated pick by name, with no registry id', async () => {
    // A hand-added company has no registry row. Sending a `vendor_id` for it
    // would attach a real company's approvals to a name somebody typed.
    serve({
      item_vendor_lists: {
        itm_1: vendorLists({
          Manual: [
            vendorEntry({
              id: 'ive_gulf',
              source: 'Manual',
              vendor_id: null,
              vendor_name: 'Gulf Crescent Fabricators',
            }),
          ],
        }),
      },
    })
    const card = await poolCard()
    fireEvent.click(within(card).getByRole('button', { name: 'Added by hand' }))

    fireEvent.click(
      await within(card).findByLabelText('Select Gulf Crescent Fabricators'),
    )
    fireEvent.click(within(card).getByRole('button', { name: /Shortlist selected/ }))

    await waitFor(() => expect(addDraftShortlistPick).toHaveBeenCalled())
    expect(addDraftShortlistPick).toHaveBeenCalledWith('prj_1', 'itm_1', {
      vendor_id: null,
      vendor_name: 'Gulf Crescent Fabricators',
      source: 'Manual',
    })
  })

  it('keeps a refused vendor ticked and shortlists the rest', async () => {
    // Not all-or-nothing, and there is nothing to roll the successes back with
    // — those writes have already landed through `locked_update`.
    const second = { ...APPROVED_BIDDER, id: 'bdr_two', name: 'Second Co' }
    serve({}, [APPROVED_BIDDER, second])
    vi.mocked(addDraftShortlistPick).mockImplementation(async (_p, _i, body) => {
      if (body.vendor_id === 'bdr_two') throw new Error('That vendor is on hold.')
      return draftPick({ ...body, vendor_id: body.vendor_id ?? null })
    })
    const card = await poolCard()

    fireEvent.click(within(card).getByLabelText('Select Al Munara Switchgear LLC'))
    fireEvent.click(within(card).getByLabelText('Select Second Co'))
    fireEvent.click(within(card).getByRole('button', { name: /Shortlist selected/ }))

    expect(await within(card).findByText('That vendor is on hold.')).toBeInTheDocument()
    expect(within(card).getByLabelText('Select Second Co')).toBeChecked()
    expect(
      within(card).getByLabelText('Select Al Munara Switchgear LLC'),
    ).not.toBeChecked()
  })

  it('shows what has been shortlisted so far in its own card', async () => {
    // Without this the button appears to do nothing — the same class of defect
    // as curated rows falling past the row cap.
    serve({ draft_shortlists: { itm_1: [draftPick()] } })
    renderItem()

    const card = await screen.findByRole('region', { name: /shortlist draft/i })
    expect(within(card).getByText('Al Munara Switchgear LLC')).toBeInTheDocument()
  })

  it('says the draft is empty rather than showing a blank table', async () => {
    serve({ draft_shortlists: { itm_1: [] } })
    renderItem()

    const card = await screen.findByRole('region', { name: /shortlist draft/i })
    expect(within(card).getByText(/nobody shortlisted yet/i)).toBeInTheDocument()
  })

  it('removes a pick by id', async () => {
    serve({ draft_shortlists: { itm_1: [draftPick({ id: 'dse_keep' })] } })
    renderItem()
    const card = await screen.findByRole('region', { name: /shortlist draft/i })

    fireEvent.click(
      within(card).getByRole('button', { name: /Remove Al Munara Switchgear LLC/i }),
    )

    await waitFor(() =>
      expect(removeDraftShortlistPick).toHaveBeenCalledWith('prj_1', 'itm_1', 'dse_keep'),
    )
  })

  it('states where each pick came from', async () => {
    // `source` is the fact that cannot be re-derived later — a registry
    // vendor's approvals can be corrected and a curated row can be deleted.
    serve({
      draft_shortlists: {
        itm_1: [
          draftPick({ id: 'dse_a', source: 'ADNOC' }),
          draftPick({
            id: 'dse_b',
            source: 'Suggested',
            vendor_id: null,
            vendor_name: 'DUCAB HV CABLE',
          }),
        ],
      },
    })
    renderItem()
    const card = await screen.findByRole('region', { name: /shortlist draft/i })

    const ducab = within(card).getByText('DUCAB HV CABLE').closest('tr')!
    expect(within(ducab).getByText('Suggested')).toBeInTheDocument()
  })

  it('keeps the draft on screen after a shortlisting reloads it', async () => {
    // The second project read resolves on a **macrotask**, on purpose. Resolved
    // immediately, the loading state and the resolution batch into one commit,
    // the card never unmounts, and this passes whether or not the guard is
    // there. The same trap as `keeps the chosen RFQ after an invitation`.
    const before = detail({
      items: [GENERATOR],
      item_vendor_lists: { itm_1: vendorLists() },
      draft_shortlists: { itm_1: [] },
    })
    const after = detail({
      items: [GENERATOR],
      item_vendor_lists: { itm_1: vendorLists() },
      draft_shortlists: { itm_1: [draftPick()] },
    })
    let call = 0
    vi.mocked(fetchWorkflowProject).mockImplementation(() => {
      call += 1
      return call === 1
        ? Promise.resolve(before)
        : new Promise((resolve) => setTimeout(() => resolve(after), 0))
    })
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )
    const card = await poolCard()

    fireEvent.click(within(card).getByLabelText('Select Al Munara Switchgear LLC'))
    fireEvent.click(within(card).getByRole('button', { name: /Shortlist selected/ }))

    const draft = await screen.findByRole('region', { name: /shortlist draft/i })
    expect(await within(draft).findByText('Al Munara Switchgear LLC')).toBeInTheDocument()
  })
})
