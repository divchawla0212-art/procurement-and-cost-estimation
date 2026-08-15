import { useEffect, useRef, useState } from 'react'
import type { JSX } from 'react'
import {
  freezeTechnicalPackage,
  removeRfqDocument,
  setTechnicalPackage,
  uploadRfqDocuments,
} from '../../api'
import { AttachmentTable } from './AttachmentTable'
import type { StepProps } from './types'

/**
 * The enquiry package: what it is, what is in it, and freezing it.
 *
 * This replaces the register-line editor that stood here. That editor let a
 * buyer *describe* documents — a code, a title, a revision — while the files
 * themselves lived in somebody's mail. Now the files are uploaded: several at
 * once, a whole folder, or a zip the server expands member by member.
 *
 * `Attachment` is not gone and this still renders it: an addendum supersedes a
 * package by describing what it replaces, so the register survives on packages
 * that carry one. What it no longer is, is the only record of the package.
 *
 * The freeze rule is unchanged and now covers the documents too, because they
 * are what vendors bid against. A frozen package shows its contents and no
 * controls — the server refuses either way, and hiding the refusal behind a
 * missing button would leave the reason mysterious.
 */
export function RaiseRfqStep({ data, run, busy }: Omit<StepProps, 'tick'>): JSX.Element {
  const pkg = data.technical_package
  const frozen = Boolean(pkg?.frozen_at)
  const [revision, setRevision] = useState(pkg?.revision ?? '')
  const [basis, setBasis] = useState(pkg?.basis_of_design ?? '')
  const [category, setCategory] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const folderRef = useRef<HTMLInputElement>(null)

  // `webkitdirectory` is what turns a file picker into a folder picker, and
  // React's typings do not carry it — it is a prefixed attribute, not a
  // standard one. Set on the element rather than spread through `any`, so the
  // escape hatch is one line and says what it is for.
  useEffect(() => {
    folderRef.current?.setAttribute('webkitdirectory', '')
  }, [])

  /** The contractor's own documents. A vendor's submission arrives against the
   *  same RFQ through the bid door and is not part of the enquiry package. */
  const ours = data.documents.filter((d) => d.submitted_by_vendor_id === null)

  function send(files: File[]): void {
    if (files.length === 0) return
    // `webkitRelativePath` is the path within the chosen folder, and empty
    // string for a plain multi-select. Sent only when every file carries one:
    // the list is positional server-side, so a partial one would attach a path
    // to the wrong file, and inventing paths for a flat selection would put a
    // folder structure on the record that the uploader never had.
    const paths = files.map((f) => f.webkitRelativePath ?? '')
    void run(() =>
      uploadRfqDocuments(data.rfq.id, files, {
        paths: paths.every((p) => p.length > 0) ? paths : undefined,
        category: category || null,
      }),
    )
  }

  const documentTable = (
    <>
      {ours.length === 0 ? (
        <p className="muted">No documents uploaded yet.</p>
      ) : (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col">Path</th>
                <th scope="col">Category</th>
                <th scope="col">Size</th>
                <th scope="col">Uploaded by</th>
                {frozen ? null : <th scope="col" />}
              </tr>
            </thead>
            <tbody>
              {ours.map((doc) => (
                <tr key={doc.id}>
                  <td>{doc.filename}</td>
                  {/* The leaf repeated in both columns says nothing, so a
                      single file shows a dash rather than its own name. */}
                  <td className="muted">
                    {doc.rel_path === doc.filename ? '—' : doc.rel_path}
                  </td>
                  <td>{doc.category ?? <span className="muted">Not categorised</span>}</td>
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
      )}
    </>
  )

  if (frozen) {
    return (
      <>
        <p>
          <b>{pkg!.revision}</b>{' '}
          <span className="muted">frozen by {pkg!.frozen_by}</span>
        </p>
        <p className="muted">{pkg!.basis_of_design}</p>
        {documentTable}
        {pkg!.attachments.length > 0 ? (
          <AttachmentTable attachments={pkg!.attachments} />
        ) : null}
        <p className="muted">
          A frozen package cannot be edited — that is what makes it something a
          vendor can bid against.
        </p>
      </>
    )
  }

  return (
    <>
      <label className="field-label" htmlFor="pkg-revision">
        Package revision
      </label>
      <input
        id="pkg-revision"
        className="input"
        value={revision}
        onChange={(e) => setRevision(e.target.value)}
      />

      <label className="field-label" htmlFor="pkg-basis">
        Basis of design
      </label>
      <textarea
        id="pkg-basis"
        className="input"
        rows={3}
        value={basis}
        onChange={(e) => setBasis(e.target.value)}
      />

      <h4>Enquiry documents</h4>
      {documentTable}

      <div className="fxrow">
        <label className="field-label field-label--inline" htmlFor="doc-category">
          Category
        </label>
        <select
          id="doc-category"
          className="input"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
        >
          {/* Blank is a real answer, not a prompt to be corrected: a buyer
              dragging in forty files has not classified them, and a category
              chosen on their behalf would look the same on screen as one
              somebody picked. */}
          <option value="">Not categorised</option>
          {data.document_categories.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>

        {/* Hidden rather than removed, the same reason the enquiry-document
            picker on the RFQ form states: the browser owns this control's
            label, it reads "No files chosen", and it cannot be relabelled. */}
        <input
          id="doc-files"
          className="sr-only"
          ref={fileRef}
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
            send(picked)
          }}
        />
        <button
          type="button"
          className="btn btn--quiet"
          disabled={busy}
          onClick={() => fileRef.current?.click()}
        >
          Add files
        </button>

        <input
          id="doc-folder"
          className="sr-only"
          ref={folderRef}
          type="file"
          multiple
          disabled={busy}
          onChange={(e) => {
            const picked = e.target.files ? Array.from(e.target.files) : []
            e.target.value = ''
            send(picked)
          }}
        />
        <button
          type="button"
          className="btn btn--quiet"
          disabled={busy}
          onClick={() => folderRef.current?.click()}
        >
          Add a folder
        </button>
      </div>
      <p className="muted">
        A .zip is expanded and each file inside it stored on its own. A folder
        keeps the paths the files sat under.
      </p>

      {pkg && pkg.attachments.length > 0 ? (
        <>
          <h4>Attachment register</h4>
          <AttachmentTable attachments={pkg.attachments} />
        </>
      ) : null}

      <div className="wizactions">
        <button
          type="button"
          className="btn"
          disabled={busy}
          onClick={() =>
            run(() =>
              setTechnicalPackage(data.rfq.id, {
                revision,
                basis_of_design: basis,
                // Sent as they are: this step no longer edits the register, and
                // sending [] would silently drop the lines an addendum set.
                attachments: pkg?.attachments ?? [],
              }),
            )
          }
        >
          Save package
        </button>
        <button
          type="button"
          className="btn btn--quiet"
          disabled={busy || !pkg}
          onClick={() => run(() => freezeTechnicalPackage(data.rfq.id))}
        >
          Freeze package
        </button>
      </div>
    </>
  )
}

/** Bytes as something a person reads. Binary units, because that is what a
 *  file manager shows and a mismatch reads as a wrong number. */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
