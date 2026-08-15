import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { RaiseRfqStep } from './RaiseRfqStep'
import {
  freezeTechnicalPackage,
  removeRfqDocument,
  setTechnicalPackage,
  uploadRfqDocuments,
} from '../../api'
import type { RfqDetail, RfqDocument, TechnicalPackage } from '../../types'

vi.mock('../../api', () => ({
  freezeTechnicalPackage: vi.fn().mockResolvedValue({}),
  removeRfqDocument: vi.fn().mockResolvedValue(undefined),
  setTechnicalPackage: vi.fn().mockResolvedValue({}),
  uploadRfqDocuments: vi.fn().mockResolvedValue({ documents: [] }),
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
  document_categories: ['Technical offer', 'Drawings'],
  tbe_template: null,
  vdrl: [],
  bids: [],
  bid_selection: null,
  queries: [],
  addenda: [],
  bid_due_date: null,
}

const doc = (over: Partial<RfqDocument> = {}): RfqDocument => ({
  id: 'rdoc_1',
  rfq_id: 'rfq_1',
  filename: 'datasheet.pdf',
  rel_path: 'datasheet.pdf',
  sha256: 'a'.repeat(64),
  size_bytes: 2048,
  content_type: 'application/pdf',
  category: null,
  uploaded_by: 'buyer@adp.ae',
  uploaded_at: '2026-08-16T09:00:00Z',
  submitted_by_vendor_id: null,
  ...over,
})

const pkg = (over: Partial<TechnicalPackage> = {}): TechnicalPackage => ({
  rfq_id: 'rfq_1',
  revision: 'Rev. B',
  basis_of_design: 'basis',
  attachments: [],
  documents: [],
  frozen_at: null,
  frozen_by: null,
  ...over,
})

/** `run` is the wizard's own wrapper. Awaiting the action here rather than
 *  stubbing it away keeps the assertion on what was *sent*, which is the only
 *  thing this component decides. */
const run = (action: () => Promise<unknown>) => action().then(() => undefined)

function step(over: Partial<RfqDetail> = {}) {
  return render(
    <RaiseRfqStep data={{ ...BASE, ...over }} run={vi.fn(run)} busy={false} />,
  )
}

/** A `File` with the folder path a directory picker would have put on it. */
function fileAt(name: string, relPath?: string): File {
  const file = new File(['x'], name, { type: 'application/pdf' })
  Object.defineProperty(file, 'webkitRelativePath', { value: relPath ?? '' })
  return file
}

// Without this, `toHaveBeenCalledWith` matches a call an *earlier* test
// made — which is how a test asserting that no paths are sent passes while
// the component sends them.
beforeEach(() => vi.clearAllMocks())

describe('RaiseRfqStep', () => {
  it('offers a folder picker, not just a file picker', () => {
    // jsdom neither opens a picker nor honours `webkitdirectory`, so the
    // attribute is the only thing a test here can see. That it is *set at all*
    // is the whole difference between "add a folder" and a button that quietly
    // adds one file, and React's typings do not carry the attribute — so this
    // is the assertion that catches the line going missing.
    const { container } = step()
    const folder = container.querySelector('#doc-folder')
    expect(folder).toHaveAttribute('webkitdirectory')
    expect(container.querySelector('#doc-files')).toHaveAttribute('multiple')
  })

  it('uploads several files in one call, with no paths', () => {
    const { container } = step()
    const input = container.querySelector('#doc-files') as HTMLInputElement

    fireEvent.change(input, {
      target: { files: [fileAt('a.pdf'), fileAt('b.pdf')] },
    })

    expect(uploadRfqDocuments).toHaveBeenCalledWith(
      'rfq_1',
      [expect.objectContaining({ name: 'a.pdf' }), expect.objectContaining({ name: 'b.pdf' })],
      { paths: undefined, category: null },
    )
  })

  it("sends a folder's relative paths, positionally", () => {
    const { container } = step()
    const input = container.querySelector('#doc-folder') as HTMLInputElement

    fireEvent.change(input, {
      target: {
        files: [
          fileAt('a.pdf', 'enquiry/datasheets/a.pdf'),
          fileAt('b.dwg', 'enquiry/drawings/b.dwg'),
        ],
      },
    })

    expect(uploadRfqDocuments).toHaveBeenCalledWith(
      'rfq_1',
      expect.anything(),
      expect.objectContaining({
        paths: ['enquiry/datasheets/a.pdf', 'enquiry/drawings/b.dwg'],
      }),
    )
  })

  it('sends no paths at all when only some files carry one', () => {
    // They are positional on the server, so a partial list would attach a path
    // to the wrong file. Nothing is better than something wrong here.
    const { container } = step()
    const input = container.querySelector('#doc-folder') as HTMLInputElement

    fireEvent.change(input, {
      target: { files: [fileAt('a.pdf', 'enquiry/a.pdf'), fileAt('b.pdf')] },
    })

    expect(uploadRfqDocuments).toHaveBeenCalledWith('rfq_1', expect.anything(), {
      paths: undefined,
      category: null,
    })
  })

  it('sends the chosen category with the files', () => {
    const { container } = step()
    fireEvent.change(screen.getByLabelText('Category'), {
      target: { value: 'Drawings' },
    })

    fireEvent.change(container.querySelector('#doc-files') as HTMLInputElement, {
      target: { files: [fileAt('a.pdf')] },
    })

    expect(uploadRfqDocuments).toHaveBeenCalledWith(
      'rfq_1',
      expect.anything(),
      expect.objectContaining({ category: 'Drawings' }),
    )
  })

  it('says what a zip and a folder will do before either is picked', () => {
    // The label matches the mechanism: a reader who drops a zip in expecting
    // one row gets one row per member, and finding that out afterwards is
    // finding it out too late.
    step()
    expect(screen.getByText(/\.zip is expanded/)).toBeInTheDocument()
  })

  it('lists an uploaded document', () => {
    step({ documents: [doc({ rel_path: 'enquiry/datasheet.pdf' })] })

    expect(screen.getByText('datasheet.pdf')).toBeInTheDocument()
    expect(screen.getByText('enquiry/datasheet.pdf')).toBeInTheDocument()
    expect(screen.getByText('2 KB')).toBeInTheDocument()
  })

  it("does not list a vendor's submission as an enquiry document", () => {
    // One collection serves both halves of the enquiry. This card is the
    // contractor's half, and showing a bid document among the files vendors
    // are bidding against would misreport what was issued.
    step({
      documents: [
        doc({ id: 'rdoc_1', filename: 'ours.pdf' }),
        doc({ id: 'rdoc_2', filename: 'theirs.pdf', submitted_by_vendor_id: 'bdr_1' }),
      ],
    })

    expect(screen.getByText('ours.pdf')).toBeInTheDocument()
    expect(screen.queryByText('theirs.pdf')).not.toBeInTheDocument()
  })

  it('removes a document by id', () => {
    step({ documents: [doc({ id: 'rdoc_7' })] })

    fireEvent.click(screen.getByRole('button', { name: 'Remove datasheet.pdf' }))

    expect(removeRfqDocument).toHaveBeenCalledWith('rfq_1', 'rdoc_7')
  })

  it('keeps the attachment register a package already carries', () => {
    // This step no longer edits the register — an addendum does — so saving
    // has to send back what is there. Sending `[]` would delete the lines an
    // addendum set, from a control whose subject is the revision and the
    // basis of design.
    step({
      technical_package: pkg({
        attachments: [{ doc_code: 'HAL-PID-001', title: 'P&ID', revision: 'Rev. C' }],
      }),
    })

    fireEvent.click(screen.getByRole('button', { name: 'Save package' }))

    expect(setTechnicalPackage).toHaveBeenCalledWith(
      'rfq_1',
      expect.objectContaining({
        attachments: [{ doc_code: 'HAL-PID-001', title: 'P&ID', revision: 'Rev. C' }],
      }),
    )
  })

  it('freezes the package', () => {
    step({ technical_package: pkg() })

    fireEvent.click(screen.getByRole('button', { name: 'Freeze package' }))

    expect(freezeTechnicalPackage).toHaveBeenCalledWith('rfq_1')
  })

  it('shows a frozen package without any way to change it', () => {
    const { container } = step({
      technical_package: pkg({
        frozen_at: '2026-08-16T10:00:00Z',
        frozen_by: 'lead@adp.ae',
      }),
      documents: [doc()],
    })

    expect(screen.getByText(/frozen by lead@adp\.ae/)).toBeInTheDocument()
    // The documents are still shown — they are what vendors are bidding
    // against, and hiding them would leave the reader unable to see what was
    // issued.
    expect(screen.getByText('datasheet.pdf')).toBeInTheDocument()
    expect(container.querySelector('#doc-files')).toBeNull()
    expect(container.querySelector('#doc-folder')).toBeNull()
    expect(
      screen.queryByRole('button', { name: 'Remove datasheet.pdf' }),
    ).not.toBeInTheDocument()
    expect(screen.getByText(/cannot be edited/)).toBeInTheDocument()
  })
})
