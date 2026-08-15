import { useState } from 'react'
import { freezeTechnicalPackage, setTechnicalPackage } from '../../api'
import type { Attachment } from '../../types'
import { AttachmentTable } from './AttachmentTable'
import type { StepProps } from './types'

/**
 * Define the package and freeze it. Vendors bid against this revision.
 *
 * This was the whole of the Scoping step until Scoping was removed from the
 * process. It moved rather than went: freezing is still what makes a package
 * something a vendor can bid against, and `DraftAddendumForm` still refuses
 * while the package is unfrozen — deleting the editor with the stage would
 * have left addenda with nothing to supersede and no way to freeze anything.
 *
 * The one thing editing cannot undo is a frozen package: the server refuses,
 * and this shows the frozen state rather than hiding the control, so the reason
 * is visible rather than mysterious.
 */
export function TechnicalPackageEditor({ data, run, busy }: Omit<StepProps, 'tick'>) {
  const pkg = data.technical_package
  const frozen = Boolean(pkg?.frozen_at)
  const [revision, setRevision] = useState(pkg?.revision ?? '')
  const [basis, setBasis] = useState(pkg?.basis_of_design ?? '')
  const [attachments, setAttachments] = useState<Attachment[]>(pkg?.attachments ?? [])
  const [docCode, setDocCode] = useState('')
  const [title, setTitle] = useState('')
  const [attRevision, setAttRevision] = useState('')

  const save = () =>
    run(() =>
      setTechnicalPackage(data.rfq.id, {
        revision,
        basis_of_design: basis,
        attachments,
      }),
    )

  if (frozen) {
    return (
      <>
        <p>
          <b>{pkg!.revision}</b>{' '}
          <span className="muted">frozen by {pkg!.frozen_by}</span>
        </p>
        <p className="muted">{pkg!.basis_of_design}</p>
        <AttachmentTable attachments={pkg!.attachments} />
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

      <AttachmentTable
        attachments={attachments}
        onRemove={(code) =>
          setAttachments((list) => list.filter((a) => a.doc_code !== code))
        }
      />

      <div className="fxrow">
        <input
          className="input"
          aria-label="Attachment document code"
          placeholder="Doc code"
          value={docCode}
          onChange={(e) => setDocCode(e.target.value)}
        />
        <input
          className="input"
          aria-label="Attachment title"
          placeholder="Title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <input
          className="input"
          aria-label="Attachment revision"
          placeholder="Revision"
          value={attRevision}
          onChange={(e) => setAttRevision(e.target.value)}
        />
        <button
          type="button"
          className="btn btn--quiet"
          disabled={!docCode.trim()}
          onClick={() => {
            setAttachments((list) => [
              ...list,
              {
                doc_code: docCode.trim(),
                title: title.trim(),
                // Empty means "no definite revision", which is what blocks the
                // freeze — so it is stored as null rather than as "".
                revision: attRevision.trim() || null,
              },
            ])
            setDocCode('')
            setTitle('')
            setAttRevision('')
          }}
        >
          Add attachment
        </button>
      </div>

      <div className="wizactions">
        <button type="button" className="btn" disabled={busy} onClick={save}>
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
