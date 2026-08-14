import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ProjectDetail } from './ProjectDetail'
import {
  CABLE,
  GENERATOR,
  HALIBA_PROJECT,
  detail,
  rfq,
} from './workflow-fixtures'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    fetchWorkflowProject: vi.fn(),
    createRfq: vi.fn(),
    updateWorkflowProject: vi.fn(),
    deleteWorkflowProject: vi.fn(),
    createWorkflowItem: vi.fn(),
    deleteWorkflowItem: vi.fn(),
    // ItemForm's Discipline field is a picker over the served vocabulary.
    fetchDisciplines: vi.fn(),
    extractRfqDoc: vi.fn(),
  }
})

import {
  createRfq,
  extractRfqDoc,
  fetchDisciplines,
  createWorkflowItem,
  deleteWorkflowItem,
  deleteWorkflowProject,
  fetchWorkflowProject,
  updateWorkflowProject,
} from '../api'

function renderDetail(onOpenItem = vi.fn(), onBack = vi.fn()) {
  render(
    <ProjectDetail projectId="prj_1" onOpenItem={onOpenItem} onBack={onBack} />,
  )
  return { onOpenItem, onBack }
}

async function fillItemForm() {
  const type = (label: string, value: string) =>
    fireEvent.change(screen.getByLabelText(label), { target: { value } })
  // Discipline is a select over the served vocabulary. Changing it before the
  // options land silently leaves it empty — a `<select>` ignores a value it
  // has no option for — so wait for the option itself, not just the field.
  await screen.findByRole('option', { name: 'Generators' })
  type('Item type', 'Gas generator')
  type('Description', '2 x 5 MW containerised')
  type('Quantity', '2')
  type('Unit of measure', 'no')
  // A select, not a text box: free text never matched the export's product
  // groups, so the discipline is chosen from the served vocabulary.
  type('Discipline', 'Generators')
  type('Estimated value (AED)', '18000000')
}

describe('ProjectDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchDisciplines).mockResolvedValue([
      { name: 'Cables', product_groups: ['CABLES - LV POWER DISTRIBUTION'] },
      { name: 'Generators', product_groups: ['GENERATOR POWER-OTHERS'] },
    ])
  })

  it("lists the project's items", async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())

    renderDetail()

    expect(await screen.findByText('Gas generator')).toBeInTheDocument()
    expect(screen.getByText('2 x 5 MW containerised')).toBeInTheDocument()
    expect(screen.getByText('Electrical')).toBeInTheDocument()
  })

  it('opens an item when its type is clicked', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    const { onOpenItem } = renderDetail()

    fireEvent.click(await screen.findByText('Gas generator'))

    expect(onOpenItem).toHaveBeenCalledWith('itm_1')
  })

  it('lists the RFQs raised against the project', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
    )

    renderDetail()

    expect(await screen.findByText('ADP-RFQ-2026-014')).toBeInTheDocument()
  })

  it('adds an item and reloads', async () => {
    vi.mocked(fetchWorkflowProject)
      .mockResolvedValueOnce(detail({ items: [] }))
      .mockResolvedValue(detail())
    vi.mocked(createWorkflowItem).mockResolvedValue({
      ...GENERATOR,
      live_period_warning: null,
    })

    renderDetail()
    fireEvent.click(await screen.findByRole('button', { name: /add item/i }))
    await fillItemForm()
    fireEvent.click(screen.getByRole('button', { name: /save item/i }))

    await waitFor(() =>
      expect(createWorkflowItem).toHaveBeenCalledWith(
        'prj_1',
        expect.objectContaining({ item_type: 'Gas generator', qty: 2 }),
      ),
    )
    expect(await screen.findByText('Gas generator')).toBeInTheDocument()
  })

  it('shows the live-period caution returned with a saved item', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [] }))
    vi.mocked(createWorkflowItem).mockResolvedValue({
      ...GENERATOR,
      required_on_site: '2030-06-01',
      live_period_warning:
        '2030-06-01 falls outside the project live period (2026-01-01 to 2029-12-31)',
    })

    renderDetail()
    fireEvent.click(await screen.findByRole('button', { name: /add item/i }))
    await fillItemForm()
    fireEvent.click(screen.getByRole('button', { name: /save item/i }))

    // Advisory, so the item was still saved — the caution is shown, not raised.
    expect(
      await screen.findByText(/falls outside the project live period/i),
    ).toBeInTheDocument()
  })

  it('sends only the fields an edit actually changed', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    vi.mocked(updateWorkflowProject).mockResolvedValue({
      ...HALIBA_PROJECT,
      status: 'On Hold',
    })

    renderDetail()
    fireEvent.click(await screen.findByRole('button', { name: /edit project/i }))
    fireEvent.change(screen.getByLabelText('Status'), {
      target: { value: 'On Hold' },
    })
    fireEvent.click(screen.getByRole('button', { name: /save project/i }))

    // Not the whole object: the server reads the body with `exclude_unset`.
    await waitFor(() =>
      expect(updateWorkflowProject).toHaveBeenCalledWith('prj_1', {
        status: 'On Hold',
      }),
    )
  })

  it('renders a refused item delete next to the item that refused it', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR, CABLE] }),
    )
    vi.mocked(deleteWorkflowItem).mockRejectedValue(
      new Error(
        'This item cannot be deleted: it is covered by ADP-RFQ-2026-014. Amend or retender first.',
      ),
    )

    renderDetail()
    fireEvent.click(
      await screen.findByRole('button', { name: 'Delete Gas generator' }),
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Confirm delete of Gas generator' }),
    )

    const row = (await screen.findByText('Gas generator')).closest('tr')!
    expect(within(row).getByText(/ADP-RFQ-2026-014/)).toBeInTheDocument()
    // And nothing was removed from the screen.
    expect(screen.getByText('Gas generator')).toBeInTheDocument()
    // The other row is unaffected — the error is keyed by item id, not shared.
    const other = screen.getByText('HV cable').closest('tr')!
    expect(within(other).queryByText(/ADP-RFQ-2026-014/)).not.toBeInTheDocument()
  })

  it('needs a confirmation before it deletes an item', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    vi.mocked(deleteWorkflowItem).mockResolvedValue(undefined)

    renderDetail()
    fireEvent.click(
      await screen.findByRole('button', { name: 'Delete Gas generator' }),
    )

    expect(deleteWorkflowItem).not.toHaveBeenCalled()

    fireEvent.click(
      screen.getByRole('button', { name: 'Confirm delete of Gas generator' }),
    )
    await waitFor(() =>
      expect(deleteWorkflowItem).toHaveBeenCalledWith('prj_1', 'itm_1'),
    )
  })

  it('states how many items a project delete would take with it', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR, CABLE] }),
    )

    renderDetail()
    fireEvent.click(await screen.findByRole('button', { name: /delete project/i }))

    expect(screen.getByText(/and its 2 items/i)).toBeInTheDocument()
  })

  it('surfaces a refused project delete and stays on the page', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    vi.mocked(deleteWorkflowProject).mockRejectedValue(
      new Error(
        'This project cannot be deleted: it holds ADP-RFQ-2026-014. Delete or retender first.',
      ),
    )
    const { onBack } = renderDetail()

    fireEvent.click(await screen.findByRole('button', { name: /delete project/i }))
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Confirm delete of Haliba Field Development',
      }),
    )

    expect(await screen.findByText(/it holds ADP-RFQ-2026-014/)).toBeInTheDocument()
    expect(onBack).not.toHaveBeenCalled()
  })

  it('returns to the roster after a successful project delete', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [] }))
    vi.mocked(deleteWorkflowProject).mockResolvedValue(undefined)
    const { onBack } = renderDetail()

    fireEvent.click(await screen.findByRole('button', { name: /delete project/i }))
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Confirm delete of Haliba Field Development',
      }),
    )

    await waitFor(() => expect(onBack).toHaveBeenCalled())
  })
  it('raises an RFQ covering the ticked items', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR, CABLE] }),
    )
    vi.mocked(createRfq).mockResolvedValue(rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1']))

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))

    const type = (label: string, value: string) =>
      fireEvent.change(screen.getByLabelText(label), { target: { value } })
    // The Discipline select is populated from the served vocabulary; a
    // `<select>` ignores a value it has no option for, so wait for the option.
    await screen.findByRole('option', { name: /^Cables/ })
    type('Reference', 'ADP-RFQ-2026-014')
    type('Package', 'Power generation')
    type('Discipline', 'Cables')
    type('Estimated budget (AED)', '18000000')
    fireEvent.click(screen.getByRole('button', { name: /create rfq/i }))

    await waitFor(() =>
      expect(createRfq).toHaveBeenCalledWith({
        project_id: 'prj_1',
        item_ids: ['itm_1'],
        reference: 'ADP-RFQ-2026-014',
        package: 'Power generation',
        // The RFQ form's Discipline is still free text — it is the RFQ's own
        // scope line, not an item's, and nothing matches vendors on it.
        discipline: 'Cables',
        value_estimate_aed: 18000000,
      }),
    )
  })

  it('takes the estimated budget as text, so the scroll wheel cannot alter it', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))

    // A focused number input steps on wheel scroll in Chrome and Edge, so
    // scrolling a long form past a filled-in budget silently rewrote it. A
    // text input has no stepping behaviour to suppress.
    expect(screen.getByLabelText('Estimated budget (AED)')).toHaveAttribute('type', 'text')
  })

  it('takes the estimated value as text too', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())

    renderDetail()
    fireEvent.click(await screen.findByRole('button', { name: /add item/i }))

    expect(screen.getByLabelText('Estimated value (AED)')).toHaveAttribute('type', 'text')
  })

  it('refuses a budget that is not a number rather than sending NaN', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))

    const type = (label: string, value: string) =>
      fireEvent.change(screen.getByLabelText(label), { target: { value } })
    await screen.findByRole('option', { name: /^Cables/ })
    type('Reference', 'ADP-RFQ-2026-014')
    type('Package', 'Power generation')
    type('Discipline', 'Cables')
    // Grouping separators are what a reader types into a money field, and
    // `Number('1,800,000')` is NaN — which serialises to null and comes back
    // as a validation error naming a field they did not think they touched.
    type('Estimated budget (AED)', '1,800,000')
    fireEvent.click(screen.getByRole('button', { name: /create rfq/i }))

    expect(
      await screen.findByText('Estimated budget must be a number.'),
    ).toBeInTheDocument()
    expect(createRfq).not.toHaveBeenCalled()
  })

  it('fills the RFQ form in from an uploaded enquiry document', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR, CABLE] }),
    )
    vi.mocked(extractRfqDoc).mockResolvedValue({
      reference: 'MOCK-RFQ-1234',
      package: 'Gas genset package',
      // A product group the served vocabulary actually offers. A value outside
      // it lands in the picker marked "not a listed discipline", which is what
      // the extractor's own test exists to stop.
      discipline: 'GENERATOR POWER-OTHERS',
      value_estimate_aed: 500000,
    })

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))

    await screen.findByRole('option', { name: /^Cables/ })
    const file = new File(['%PDF-1.4'], 'enquiry.pdf', { type: 'application/pdf' })
    fireEvent.change(screen.getByLabelText(/fill in from the enquiry document/i), {
      target: { files: [file] },
    })

    await waitFor(() =>
      expect(screen.getByLabelText('Reference')).toHaveValue('MOCK-RFQ-1234'),
    )
    expect(extractRfqDoc).toHaveBeenCalledWith(file)
    expect(screen.getByLabelText('Package')).toHaveValue('Gas genset package')
    expect(screen.getByLabelText('Discipline')).toHaveValue('GENERATOR POWER-OTHERS')
    // A string, because the field is a text input — see the scroll-wheel test
    // above. This is a real behaviour change, stated rather than loosened to
    // accept either.
    expect(screen.getByLabelText('Estimated budget (AED)')).toHaveValue('500000')
  })

  it('names the document it read, rather than reporting no file chosen', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    vi.mocked(extractRfqDoc).mockResolvedValue({ reference: 'MOCK-RFQ-1234' })

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))
    await screen.findByRole('option', { name: /^Cables/ })

    fireEvent.change(screen.getByLabelText(/fill in from the enquiry document/i), {
      target: { files: [new File(['%PDF-1.4'], 'haliba-enquiry.pdf')] },
    })

    // The input clears itself so the same file can be picked twice — which is
    // what reset its native label to "No file chosen" after a successful read.
    // This is the state that has to survive that clear.
    expect(await screen.findByText('haliba-enquiry.pdf')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /view haliba-enquiry\.pdf/i }),
    ).toBeInTheDocument()
  })

  it('still offers the document when reading it failed', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    vi.mocked(extractRfqDoc).mockRejectedValue(new Error('The upload has no file name.'))

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))
    await screen.findByRole('option', { name: /^Cables/ })

    fireEvent.change(screen.getByLabelText(/fill in from the enquiry document/i), {
      target: { files: [new File([''], 'unreadable.pdf')] },
    })

    // Being able to open the file that failed is more useful than being able
    // to open only the ones that worked.
    expect(await screen.findByText(/has no file name/i)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /view unreadable\.pdf/i }),
    ).toBeInTheDocument()
  })

  it('opens the picked document in a new tab', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    vi.mocked(extractRfqDoc).mockResolvedValue({})
    // jsdom implements neither, so both are installed and asserted directly.
    const createObjectURL = vi.fn().mockReturnValue('blob:enquiry')
    const revokeObjectURL = vi.fn()
    URL.createObjectURL = createObjectURL
    URL.revokeObjectURL = revokeObjectURL
    const open = vi.fn()
    vi.stubGlobal('open', open)

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))
    await screen.findByRole('option', { name: /^Cables/ })
    const file = new File(['%PDF-1.4'], 'haliba-enquiry.pdf')
    fireEvent.change(screen.getByLabelText(/fill in from the enquiry document/i), {
      target: { files: [file] },
    })

    fireEvent.click(await screen.findByRole('button', { name: /view haliba-enquiry\.pdf/i }))

    expect(createObjectURL).toHaveBeenCalledWith(file)
    expect(open).toHaveBeenCalledWith('blob:enquiry', '_blank', 'noopener')
    vi.unstubAllGlobals()
  })

  it('leaves the fields alone when the document cannot be read', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR, CABLE] }),
    )
    vi.mocked(extractRfqDoc).mockRejectedValue(new Error('The upload has no file name.'))

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))

    await screen.findByRole('option', { name: /^Cables/ })
    fireEvent.change(screen.getByLabelText('Reference'), {
      target: { value: 'ADP-RFQ-2026-014' },
    })
    fireEvent.change(screen.getByLabelText(/fill in from the enquiry document/i), {
      target: { files: [new File([''], 'enquiry.pdf')] },
    })

    // The server's own sentence, and the reference the reader had already
    // typed still there to submit.
    expect(await screen.findByText(/has no file name/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Reference')).toHaveValue('ADP-RFQ-2026-014')
  })

  it('can raise one RFQ spanning several items', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR, CABLE] }),
    )
    vi.mocked(createRfq).mockResolvedValue(
      rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1', 'itm_2']),
    )

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select HV cable' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))

    expect(screen.getByText(/covering 2 items/i)).toBeInTheDocument()
  })

  it('cannot raise an RFQ with nothing selected', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    renderDetail()
    expect(await screen.findByRole('button', { name: /raise rfq/i })).toBeDisabled()
  })

  it("surfaces the server's refusal when an RFQ cannot be raised", async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    vi.mocked(createRfq).mockRejectedValue(
      new Error('Item itm_1 does not belong to project prj_1'),
    )

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))

    const type = (label: string, value: string) =>
      fireEvent.change(screen.getByLabelText(label), { target: { value } })
    // The Discipline select is populated from the served vocabulary; a
    // `<select>` ignores a value it has no option for, so wait for the option.
    await screen.findByRole('option', { name: /^Cables/ })
    type('Reference', 'ADP-RFQ-2026-014')
    type('Package', 'Power generation')
    type('Discipline', 'Cables')
    type('Estimated budget (AED)', '18000000')
    fireEvent.click(screen.getByRole('button', { name: /create rfq/i }))

    expect(await screen.findByText(/does not belong to project/i)).toBeInTheDocument()
  })
  it('clears the live-period caution once the item it described is deleted', async () => {
    // Found by walking the running app: the caution outlived its item and sat
    // there describing something no longer on screen.
    vi.mocked(fetchWorkflowProject)
      .mockResolvedValueOnce(detail({ items: [] }))
      .mockResolvedValue(detail())
    vi.mocked(createWorkflowItem).mockResolvedValue({
      ...GENERATOR,
      required_on_site: '2030-06-01',
      live_period_warning:
        '2030-06-01 falls outside the project live period (2026-01-01 to 2029-12-31)',
    })
    vi.mocked(deleteWorkflowItem).mockResolvedValue(undefined)

    renderDetail()
    fireEvent.click(await screen.findByRole('button', { name: /add item/i }))
    await fillItemForm()
    fireEvent.click(screen.getByRole('button', { name: /save item/i }))
    await screen.findByText(/falls outside the project live period/i)

    fireEvent.click(
      await screen.findByRole('button', { name: 'Delete Gas generator' }),
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Confirm delete of Gas generator' }),
    )

    await waitFor(() =>
      expect(
        screen.queryByText(/falls outside the project live period/i),
      ).not.toBeInTheDocument(),
    )
  })
})
