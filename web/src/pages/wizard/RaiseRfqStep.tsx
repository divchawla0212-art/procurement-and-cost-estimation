import { useState } from 'react'
import type { DragEvent, JSX } from 'react'
import { removeRfqDocument, setTbeTemplate, uploadRfqDocuments } from '../../api'
import { SendEnquiry } from './SendEnquiry'
import type { StepProps } from './types'

/**
 * One place to put the enquiry documents, and the list of what is there.
 *
 * This step was a technical-package editor: a revision, a basis of design, a
 * category picker, two upload buttons and Save/Freeze beneath them, with the
 * document table bolted underneath. Every one of those was a field standing
 * between a contractor and the only thing they came here to do. What is left
 * is a drop zone and a list.
 *
 * Freezing went with them, and that is a real loss stated once rather than
 * hidden: an addendum supersedes a package *revision*, so an RFQ raised from
 * here has nothing for one to supersede. A package frozen before this change
 * still refuses uploads server-side, which is why the frozen view survives —
 * the controls go and the reason is said out loud, rather than the upload
 * failing with a refusal nobody can explain.
 *
 * The eligibility checklist sits under the list, and it replaced a free-text
 * `TBE template` textarea. The nine returnables are `EligibilityCategory`,
 * built by `workflow/checklist.py` and **sent** on the RFQ payload — not
 * spelled out here. The same rows go into the enquiry mail, so what a buyer
 * reads on this screen and what a vendor is sent are one list; a copy in this
 * file would drift from both and from the gate that judges the returned bid.
 *
 * The nine are read-only because they are a fixed vocabulary: a control able
 * to edit them would be a control able to drop one. What a buyer may add is
 * *extra*, and lands in `TbeTemplate.items`.
 *
 * No gate reads any of this. `_issued_exit` asked for a template and is gone —
 * with the checklist fixed there is nothing left to forget, and keeping the
 * gate after removing its only editor would have stranded every RFQ at Issued.
 */
export function RaiseRfqStep({ data, run, busy }: Omit<StepProps, 'tick'>): JSX.Element {
  const pkg = data.technical_package
  const frozen = Boolean(pkg?.frozen_at)
  const [over, setOver] = useState(false)
  // The buyer's **own additions** only. The nine fixed returnables are never
  // in here — they are not stored per RFQ at all, so there is nothing to
  // round-trip and no way for this box to drop one.
  const [extras, setExtras] = useState(
    (data.tbe_template?.items ?? []).map((item) => item.label).join('\n'),
  )

  /** The contractor's own documents. A vendor's submission arrives against the
   *  same RFQ through the bid door and is not part of the enquiry package. */
  const ours = data.documents.filter((d) => d.submitted_by_vendor_id === null)

  function send(picked: Picked[]): void {
    if (picked.length === 0) return
    // `paths` is positional server-side, so a partial list would attach a path
    // to the wrong file, and inventing paths for a flat selection would put a
    // folder structure on the record that the uploader never had. Sent only
    // when every file carries one.
    const paths = picked.map((p) => p.path)
    void run(() =>
      uploadRfqDocuments(
        data.rfq.id,
        picked.map((p) => p.file),
        { paths: paths.every((p) => p.length > 0) ? paths : undefined, category: null },
      ),
    )
  }

  function onDrop(event: DragEvent<HTMLLabelElement>): void {
    event.preventDefault()
    setOver(false)
    void pickedFromDrop(event.dataTransfer).then(send)
  }

  const documentList =
    ours.length === 0 ? (
      <p className="muted">No documents uploaded yet.</p>
    ) : (
      <div className="table-scroll">
        <table className="grid">
          <thead>
            <tr>
              <th scope="col">File</th>
              <th scope="col">Size</th>
              <th scope="col">Uploaded by</th>
              {frozen ? null : <th scope="col" />}
            </tr>
          </thead>
          <tbody>
            {ours.map((doc) => (
              <tr key={doc.id}>
                <td>
                  {doc.filename}
                  {/* The leaf repeated under itself says nothing, so the path
                      shows only where a folder upload actually put one. */}
                  {doc.rel_path === doc.filename ? null : (
                    <div className="muted filepath">{doc.rel_path}</div>
                  )}
                </td>
                <td className="mono">{formatSize(doc.size_bytes)}</td>
                <td className="muted">{doc.uploaded_by}</td>
                {frozen ? null : (
                  <td>
                    <button
                      type="button"
                      className="linkish"
                      aria-label={`Remove ${doc.filename}`}
                      disabled={busy}
                      onClick={() => run(() => removeRfqDocument(data.rfq.id, doc.id))}
                    >
                      Remove
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )

  /* Rendered in both branches, from one local so the two cannot drift apart.
     A frozen package refuses an upload, which is why the branch below exists —
     but `store.set_tbe_template` has no freeze guard, because the eligibility
     checklist is not part of the package. Withholding this editor there would
     invent a lock the server does not have, and a frozen RFQ is the only view
     a seeded one ever shows. */
  const tbeEditor = (
    <>
      <h3>Eligibility checklist</h3>
      <p className="muted">
        What every bidder must return against this enquiry. The lettered items
        are fixed and go out with the enquiry mail; lines marked{' '}
        <b>must have</b> block a bid that omits them.
      </p>
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Returnable</th>
            </tr>
          </thead>
          <tbody>
            {data.eligibility_checklist.map((row) => (
              <tr key={row.letter}>
                <td>{row.letter}.</td>
                <td>
                  {row.label}
                  {row.mandatory ? <b> (must have)</b> : null}
                  {row.note ? <div className="muted">{row.note}</div> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <label className="field-label" htmlFor="tbe-criteria">
        Add your own items — one per line
      </label>
      <textarea
        id="tbe-criteria"
        className="input"
        rows={3}
        value={extras}
        onChange={(e) => setExtras(e.target.value)}
      />
      <button
        type="button"
        className="btn btn--quiet"
        disabled={busy}
        onClick={() =>
          run(() =>
            setTbeTemplate(data.rfq.id, {
              items: extras
                .split('\n')
                .map((c: string) => c.trim())
                .filter(Boolean),
            }),
          )
        }
      >
        Save added items
      </button>
    </>
  )

  if (frozen) {
    return (
      <>
        <p>
          <b>{pkg!.revision}</b> <span className="muted">frozen by {pkg!.frozen_by}</span>
        </p>
        {documentList}
        <p className="muted">
          A frozen package cannot be edited — that is what makes it something a
          vendor can bid against.
        </p>
        {tbeEditor}
        <SendEnquiry data={data} run={run} busy={busy} />
      </>
    )
  }

  return (
    <>
      {/* The ingestion screen's zone, reused rather than reinvented: a label
          wrapping an `.sr-only` input, so clicking anywhere on it opens the
          picker with no JavaScript in the way, and `.drag` while a drag is
          over it. The input is hidden because the browser owns its label — it
          reads "No files chosen" and cannot be relabelled. */}
      <label
        className={over ? 'dropzone drag' : 'dropzone'}
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
      >
        <input
          id="doc-files"
          className="sr-only"
          type="file"
          multiple
          disabled={busy}
          onChange={(e) => {
            // Materialised before the input is cleared: `e.target.files` is
            // live, so reading it afterwards gives nothing. Cleared at all so
            // that picking the same files twice uploads twice — the retry
            // after a refusal is the case that needs it.
            const picked = e.target.files ? Array.from(e.target.files) : []
            e.target.value = ''
            send(picked.map((file) => ({ file, path: file.webkitRelativePath ?? '' })))
          }}
        />
        <span className="dz-glyph" aria-hidden>
          ↥
        </span>
        <span className="dz-title">Add documents</span>
        <span className="hint">
          {busy ? 'Uploading…' : 'Drag files or folders here, or click to choose'}
        </span>
      </label>
      <p className="muted">
        A .zip is expanded and each file inside it stored on its own. A folder
        keeps the paths the files sat under.
      </p>

      {documentList}
      {tbeEditor}
      <SendEnquiry data={data} run={run} busy={busy} />
    </>
  )
}

/** One file on its way up, with the folder path it sat under — empty string
 *  for a file that came on its own. */
type Picked = { file: File; path: string }

/**
 * What was dropped, walked.
 *
 * `dataTransfer.files` is empty for a dropped folder, so a handler reading only
 * that ignores the drop entirely. `webkitGetAsEntry` is the one way to ask
 * whether an item is a directory; it is prefixed, unstandardised, and supported
 * everywhere this runs. A browser that does not offer it falls back to the flat
 * file list rather than dropping the upload on the floor.
 */
async function pickedFromDrop(transfer: DataTransfer): Promise<Picked[]> {
  const entries = Array.from(transfer.items ?? [])
    .map((item) =>
      typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null,
    )
    .filter((entry): entry is FileSystemEntry => entry !== null)

  if (entries.length === 0) {
    return Array.from(transfer.files ?? []).map((file) => ({ file, path: '' }))
  }
  const picked: Picked[] = []
  for (const entry of entries) await walk(entry, '', picked)
  return picked
}

/** Depth-first, prefixing each file with the folders above it. A file dropped
 *  on its own gets no path at all — the same answer a plain multi-select
 *  gives, so the two doors record the same thing. */
async function walk(entry: FileSystemEntry, prefix: string, out: Picked[]): Promise<void> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) =>
      (entry as FileSystemFileEntry).file(resolve, reject),
    )
    out.push({ file, path: prefix ? `${prefix}${entry.name}` : '' })
    return
  }
  // `readEntries` hands back at most 100 at a time and signals the end with an
  // empty batch, so one call is not the whole folder.
  const reader = (entry as FileSystemDirectoryEntry).createReader()
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
      reader.readEntries(resolve, reject),
    )
    if (batch.length === 0) return
    for (const child of batch) await walk(child, `${prefix}${entry.name}/`, out)
  }
}

/** Bytes as something a person reads. Binary units, because that is what a
 *  file manager shows and a mismatch reads as a wrong number. */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
