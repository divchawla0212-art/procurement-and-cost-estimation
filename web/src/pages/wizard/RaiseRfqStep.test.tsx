import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { RaiseRfqStep } from './RaiseRfqStep'
import { removeRfqDocument, setTbeTemplate, uploadRfqDocuments } from '../../api'
import type {
  ChecklistRow,
  RfqDetail,
  RfqDocument,
  TbeTemplate,
  TechnicalPackage,
} from '../../types'

vi.mock('../../api', () => ({
  removeRfqDocument: vi.fn().mockResolvedValue(undefined),
  setTbeTemplate: vi.fn().mockResolvedValue({}),
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
  eligibility_checklist: [],
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

const file = (name: string) => new File(['x'], name, { type: 'application/pdf' })

/** A `File` with the folder path a directory picker would have put on it. */
function fileAt(name: string, relPath?: string): File {
  const picked = file(name)
  Object.defineProperty(picked, 'webkitRelativePath', { value: relPath ?? '' })
  return picked
}

/**
 * The half of the drag-and-drop API jsdom does not implement.
 *
 * A drop carries `DataTransferItem`s, and `webkitGetAsEntry` is the only way to
 * ask whether one of them is a folder — `dataTransfer.files` flattens a dropped
 * folder to nothing at all. These fakes are the shape the browser hands over:
 * callback-style, and `readEntries` returning an empty batch to say it is done.
 */
type Entry = { isFile: boolean; isDirectory: boolean; name: string } & Record<string, unknown>

function fileEntry(name: string): Entry {
  return {
    isFile: true,
    isDirectory: false,
    name,
    file: (cb: (f: File) => void) => cb(file(name)),
  }
}

function dirEntry(name: string, children: Entry[]): Entry {
  return {
    isFile: false,
    isDirectory: true,
    name,
    createReader: () => {
      let sent = false
      return {
        readEntries: (cb: (batch: Entry[]) => void) => {
          cb(sent ? [] : children)
          sent = true
        },
      }
    },
  }
}

/** What `fireEvent.drop` needs to look like a real drop. */
function dropped(entries: Entry[] | null, files: File[] = []) {
  return {
    dataTransfer: {
      files,
      items: entries?.map((entry) => ({ kind: 'file', webkitGetAsEntry: () => entry })) ?? [],
      types: ['Files'],
    },
  }
}

/** The drop zone. Not addressable by role — it is a `<label>`, which is what
 *  makes a click anywhere on it reach the input without a line of JavaScript. */
const zone = () => document.querySelector('.dropzone') as HTMLElement

// Without this, `toHaveBeenCalledWith` matches a call an *earlier* test
// made — which is how a test asserting that no paths are sent passes while
// the component sends them.
beforeEach(() => vi.clearAllMocks())

describe('RaiseRfqStep', () => {
  it('has none of the technical-package editor back', () => {
    // The step was a technical-package editor with a document table bolted
    // under it: a revision, a basis of design, a category picker, two upload
    // buttons and two save/freeze actions. It is now one place to put files,
    // plus the TBE template that came over from Shortlisting — so this is no
    // longer "nothing else", and the named absences are what it asserts. This
    // is what fails if any of the editor grows back.
    const { container } = step()

    expect(screen.queryByLabelText('Package revision')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Basis of design')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Category')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save package' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Freeze package' })).not.toBeInTheDocument()
    expect(container.querySelectorAll('input[type="file"]')).toHaveLength(1)
    expect(container.querySelectorAll('.dropzone')).toHaveLength(1)
  })

  it('opens the file picker when the drop zone is clicked', () => {
    // The input is *inside* the label, which is the whole mechanism: the
    // browser forwards the click, so there is no handler to lose. Hidden
    // because the browser owns its own label — it reads "No files chosen" and
    // cannot be relabelled. Reparenting it outside the label would leave a
    // zone that looks like a control and does nothing.
    const { container } = step()
    const input = container.querySelector('#doc-files') as HTMLInputElement

    expect(zone().tagName).toBe('LABEL')
    expect(zone().contains(input)).toBe(true)
    expect(input).toHaveAttribute('multiple')
  })

  it('marks the zone while a drag is over it', () => {
    // `.drag` is what the stylesheet colours; the cursor is already busy
    // carrying the files, so the zone is the only thing that can acknowledge
    // them. Held on the same class the ingestion screen's zone uses.
    step()
    expect(zone().className).toBe('dropzone')

    fireEvent.dragOver(zone())
    expect(zone().className).toContain('drag')

    fireEvent.dragLeave(zone())
    expect(zone().className).toBe('dropzone')
  })

  it('uploads several picked files in one call, with no paths', () => {
    const { container } = step()

    fireEvent.change(container.querySelector('#doc-files') as HTMLInputElement, {
      target: { files: [fileAt('a.pdf'), fileAt('b.pdf')] },
    })

    expect(uploadRfqDocuments).toHaveBeenCalledWith(
      'rfq_1',
      [
        expect.objectContaining({ name: 'a.pdf' }),
        expect.objectContaining({ name: 'b.pdf' }),
      ],
      { paths: undefined, category: null },
    )
  })

  it('uploads files dropped on the zone', async () => {
    step()

    fireEvent.drop(zone(), dropped(null, [file('a.pdf')]))

    await waitFor(() =>
      expect(uploadRfqDocuments).toHaveBeenCalledWith(
        'rfq_1',
        [expect.objectContaining({ name: 'a.pdf' })],
        { paths: undefined, category: null },
      ),
    )
  })

  it('walks a dropped folder and sends its paths, positionally', async () => {
    // `dataTransfer.files` is empty for a dropped folder, so without the walk
    // this drop uploads nothing at all and reads on screen as ignored.
    step()

    fireEvent.drop(
      zone(),
      dropped([
        dirEntry('enquiry', [
          fileEntry('a.pdf'),
          dirEntry('drawings', [fileEntry('b.dwg')]),
        ]),
      ]),
    )

    await waitFor(() =>
      expect(uploadRfqDocuments).toHaveBeenCalledWith(
        'rfq_1',
        [
          expect.objectContaining({ name: 'a.pdf' }),
          expect.objectContaining({ name: 'b.dwg' }),
        ],
        expect.objectContaining({
          paths: ['enquiry/a.pdf', 'enquiry/drawings/b.dwg'],
        }),
      ),
    )
  })

  it('sends no paths at all when only some of the dropped files carry one', async () => {
    // They are positional on the server, so a partial list would attach a path
    // to the wrong file. Nothing is better than something wrong here.
    step()

    fireEvent.drop(
      zone(),
      dropped([dirEntry('enquiry', [fileEntry('a.pdf')]), fileEntry('loose.pdf')]),
    )

    await waitFor(() =>
      expect(uploadRfqDocuments).toHaveBeenCalledWith('rfq_1', expect.anything(), {
        paths: undefined,
        category: null,
      }),
    )
  })

  it('says what a zip and a folder will do before either is dropped', () => {
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

  it('does not repeat the file name as its own path', () => {
    step({ documents: [doc()] })
    expect(screen.getAllByText('datasheet.pdf')).toHaveLength(1)
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

  it('shows a frozen package without any way to change it', () => {
    // Nothing here freezes a package any more, but a package frozen before
    // this screen was simplified still refuses uploads server-side — so the
    // controls go and the reason is said out loud.
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
    expect(container.querySelector('.dropzone')).toBeNull()
    expect(
      screen.queryByRole('button', { name: 'Remove datasheet.pdf' }),
    ).not.toBeInTheDocument()
    expect(screen.getByText(/cannot be edited/)).toBeInTheDocument()
  })
})

// A free-text `TBE template` textarea stood here. It is gone: the nine
// returnables are a fixed vocabulary served on the payload, and only what a
// buyer adds *on top* is still editable.
describe('RaiseRfqStep eligibility checklist', () => {
  const tbe = (labels: string[]): TbeTemplate => ({
    rfq_id: 'rfq_1',
    items: labels.map((label, i) => ({ id: `cli_${i}`, label, mandatory: false })),
    source_rfq_reference: null,
  })

  const rows = (): ChecklistRow[] => [
    { letter: 'a', label: 'Technical offer', mandatory: true, note: null, item_id: null },
    {
      letter: 'c',
      label: 'Compliance / deviation sheet',
      mandatory: true,
      note: 'no compliance sheet is attached; send a deviation list',
      item_id: null,
    },
    { letter: 'g', label: 'Drawings', mandatory: false, note: null, item_id: null },
  ]

  it('renders every row the server sent, and does not build the list itself', () => {
    step({ eligibility_checklist: rows() })

    expect(
      screen.getByRole('heading', { name: 'Eligibility checklist' }),
    ).toBeInTheDocument()
    for (const row of rows()) {
      expect(screen.getByText(row.label)).toBeInTheDocument()
    }
  })

  it('has no TBE template editor any more', () => {
    step({ eligibility_checklist: rows() })

    expect(screen.queryByRole('heading', { name: 'TBE template' })).toBeNull()
    expect(screen.queryByLabelText('One criterion per line')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Save TBE template' })).toBeNull()
  })

  // The load-bearing one. The nine are a fixed vocabulary and the payload is
  // the only place they come from, so a row the server did not send must not
  // appear -- a hardcoded copy in the component would drift from the enquiry
  // mail and from the gate that judges the returned bid, and neither mismatch
  // is visible from either end.
  it('shows no returnable the server did not send', () => {
    step({ eligibility_checklist: [] })

    expect(screen.queryByText('Technical offer')).toBeNull()
    expect(screen.queryByText('Catalogues and brochures')).toBeNull()
  })

  it('marks the must-have rows and only those', () => {
    step({ eligibility_checklist: rows() })

    const drawings = screen.getByText('Drawings').closest('td')
    expect(drawings?.textContent).not.toContain('must have')
    const technical = screen.getByText('Technical offer').closest('td')
    expect(technical?.textContent).toContain('must have')
  })

  it("renders a row's note, which is where (c)'s two readings live", () => {
    step({ eligibility_checklist: rows() })

    expect(
      screen.getByText(/send a deviation list/),
    ).toBeInTheDocument()
  })

  it('prefills only the additions the buyer made, one per line', () => {
    step({
      eligibility_checklist: rows(),
      tbe_template: tbe(['Accuracy class', 'Turndown ratio']),
    })

    expect(screen.getByLabelText(/Add your own items/)).toHaveValue(
      'Accuracy class\nTurndown ratio',
    )
  })

  it('posts the non-blank added lines only, trimmed, as items', async () => {
    step({ eligibility_checklist: rows() })

    fireEvent.change(screen.getByLabelText(/Add your own items/), {
      target: { value: '  Plate grade  \n\n Weld procedure\n   \n' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save added items' }))

    await waitFor(() =>
      expect(setTbeTemplate).toHaveBeenCalledWith('rfq_1', {
        items: ['Plate grade', 'Weld procedure'],
      }),
    )
  })

  // A frozen package refuses a document upload, which is why this step returns
  // early for one -- but `set_tbe_template` has no freeze guard, because the
  // checklist is not part of the package. Dropping the card from that branch
  // would invent a lock the server does not have, and on a seeded RFQ that
  // branch is the only one a reader ever sees.
  it('renders against a frozen package too', () => {
    step({
      technical_package: pkg({
        frozen_at: '2026-08-16T10:00:00Z',
        frozen_by: 'lead@adp.ae',
      }),
      eligibility_checklist: rows(),
      tbe_template: tbe(['Conductor size']),
    })

    expect(
      screen.getByRole('heading', { name: 'Eligibility checklist' }),
    ).toBeInTheDocument()
    expect(screen.getByText('Technical offer')).toBeInTheDocument()
    expect(screen.getByLabelText(/Add your own items/)).toHaveValue('Conductor size')
    expect(
      screen.getByRole('button', { name: 'Save added items' }),
    ).toBeInTheDocument()
  })
})
