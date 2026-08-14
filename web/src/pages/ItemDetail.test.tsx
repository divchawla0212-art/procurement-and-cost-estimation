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
  rfq,
  shortlistEntry,
} from './workflow-fixtures'
import type { AvailableBidders } from '../types'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    fetchWorkflowProject: vi.fn(),
    updateWorkflowItem: vi.fn(),
    fetchAvailableBidders: vi.fn(),
    createRfq: vi.fn(),
    // The Raise-RFQ form's Discipline field is a picker over the vocabulary.
    fetchDisciplines: vi.fn(),
    // One read per covering RFQ: the project payload carries no shortlist.
    fetchRfq: vi.fn(),
    inviteRegisteredBidder: vi.fn(),
  }
})

import {
  createRfq,
  fetchAvailableBidders,
  fetchDisciplines,
  fetchRfq,
  fetchWorkflowProject,
  inviteRegisteredBidder,
  updateWorkflowItem,
} from '../api'

function availableList(over: Partial<AvailableBidders> = {}): AvailableBidders {
  const bidders = over.bidders ?? []
  return {
    approvers: ['ADNOC', 'Astra'],
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
  describe('the raise-RFQ form', () => {
    async function openIt() {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
      renderItem()
      fireEvent.click(await screen.findByRole('button', { name: /raise rfq/i }))
      return screen.getByLabelText('Discipline')
    }

    it('offers the disciplines and the product groups behind them', async () => {
      // Both levels: an item is scoped to a family, but an RFQ is commonly cut
      // narrower — one cable type rather than all eleven.
      const select = await openIt()
      await screen.findByRole('option', { name: /^Cables/ })

      expect(select.tagName).toBe('SELECT')
      expect(
        within(select).getByRole('option', { name: /Cables — all 1 product groups/ }),
      ).toBeInTheDocument()
      expect(
        within(select).getByRole('option', { name: 'CABLES - LV POWER DISTRIBUTION' }),
      ).toBeInTheDocument()
      expect(
        within(select).getByRole('option', { name: 'GENERATOR POWER-OTHERS' }),
      ).toBeInTheDocument()
    })

    it('groups each product group under its discipline', async () => {
      const select = await openIt()
      await screen.findByRole('option', { name: /^Cables/ })

      const groups = select.querySelectorAll('optgroup')
      expect([...groups].map((g) => g.getAttribute('label'))).toEqual([
        'Cables',
        'Generators',
      ])
    })

    it('sends the product group chosen, not the family', async () => {
      const select = await openIt()
      await screen.findByRole('option', { name: /^Cables/ })
      fireEvent.change(select, {
        target: { value: 'CABLES - LV POWER DISTRIBUTION' },
      })
      expect((select as HTMLSelectElement).value).toBe(
        'CABLES - LV POWER DISTRIBUTION',
      )
    })

    it('shows an example of a package rather than explaining one', async () => {
      await openIt()
      expect(screen.getByLabelText('Package')).toHaveAttribute(
        'placeholder',
        expect.stringContaining('Wellhead tie-in ball valves'),
      )
    })

    it('calls the budget field a budget', async () => {
      await openIt()
      expect(screen.getByLabelText('Estimated budget (AED)')).toBeInTheDocument()
    })
  })

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
      expect(fetchAvailableBidders).toHaveBeenCalledWith('Cables')
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
      fireEvent.change(within(card).getByLabelText('Search'), {
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

  describe('raising an RFQ', () => {
    // The project screen raises one over the items you ticked there. Here the
    // item *is* the selection, so there is nothing to tick — which is the whole
    // reason this door exists: you arrive at an item, read who could bid for it
    // in the card above, and raise the RFQ without going back up a level.
    it('covers exactly this item, without a selection step', async () => {
      vi.mocked(fetchWorkflowProject).mockResolvedValue(
        detail({ items: [GENERATOR, CABLE] }),
      )
      vi.mocked(createRfq).mockResolvedValue(
        rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1']),
      )

      renderItem()

      const card = await screen.findByRole('region', { name: /RFQs covering/i })
      fireEvent.click(within(card).getByRole('button', { name: /raise rfq/i }))

      const type = (label: string, value: string) =>
        fireEvent.change(within(card).getByLabelText(label), {
          target: { value },
        })
      // The Discipline select is populated from the served vocabulary; a
    // `<select>` ignores a value it has no option for, so wait for the option.
    await screen.findByRole('option', { name: /^Cables/ })
    type('Reference', 'ADP-RFQ-2026-014')
      type('Package', 'Power generation')
      type('Discipline', 'Generators')
      type('Estimated budget (AED)', '18000000')
      fireEvent.click(within(card).getByRole('button', { name: /create rfq/i }))

      await waitFor(() =>
        expect(createRfq).toHaveBeenCalledWith({
          project_id: 'prj_1',
          // `itm_1` alone, and never the project's other items — CABLE is in
          // the fixture precisely so a regression that sent them all is caught.
          item_ids: ['itm_1'],
          reference: 'ADP-RFQ-2026-014',
          package: 'Power generation',
          discipline: 'Generators',
          value_estimate_aed: 18000000,
        }),
      )
    })

    it('shows the new RFQ in the covering list once it is created', async () => {
      // The new RFQ is read back from the server rather than pushed into
      // local state: `covering` is filtered from the project payload, so a
      // screen that did not reload would show a stale list next to a form that
      // had just succeeded.
      vi.mocked(fetchWorkflowProject)
        .mockResolvedValueOnce(detail())
        .mockResolvedValue(
          detail({ rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
        )
      vi.mocked(createRfq).mockResolvedValue(
        rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1']),
      )

      renderItem()

      const card = await screen.findByRole('region', { name: /RFQs covering/i })
      expect(
        within(card).getByText(/No RFQ covers this item yet/i),
      ).toBeInTheDocument()

      fireEvent.click(within(card).getByRole('button', { name: /raise rfq/i }))
      const type = (label: string, value: string) =>
        fireEvent.change(within(card).getByLabelText(label), {
          target: { value },
        })
      // The Discipline select is populated from the served vocabulary; a
    // `<select>` ignores a value it has no option for, so wait for the option.
    await screen.findByRole('option', { name: /^Cables/ })
    type('Reference', 'ADP-RFQ-2026-014')
      type('Package', 'Power generation')
      type('Discipline', 'Generators')
      type('Estimated budget (AED)', '18000000')
      fireEvent.click(within(card).getByRole('button', { name: /create rfq/i }))

      expect(await screen.findByText('ADP-RFQ-2026-014')).toBeInTheDocument()
      // The form closed on success, so the card is a list again.
      await waitFor(() =>
        expect(
          screen.queryByRole('button', { name: /create rfq/i }),
        ).not.toBeInTheDocument(),
      )
    })
  })
})
