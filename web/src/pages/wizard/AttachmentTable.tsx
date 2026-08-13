import type { JSX } from 'react'
import type { Attachment } from '../../types'

/** The package's attachment list. Shared by Scoping and by the addendum draft
 *  form, which replaces the list wholesale when it is issued. */
export function AttachmentTable({
  attachments,
  onRemove,
}: {
  attachments: Attachment[]
  onRemove?: (docCode: string) => void
}): JSX.Element {
  if (attachments.length === 0) return <p className="muted">No attachments yet.</p>
  return (
    <table className="table">
      <thead>
        <tr>
          <th scope="col">Document</th>
          <th scope="col">Title</th>
          <th scope="col">Revision</th>
          {onRemove ? <th scope="col" /> : null}
        </tr>
      </thead>
      <tbody>
        {attachments.map((a) => (
          <tr key={a.doc_code}>
            <td className="mono">{a.doc_code}</td>
            <td>{a.title}</td>
            <td>{a.revision ?? <span className="warn">none</span>}</td>
            {onRemove ? (
              <td>
                <button
                  type="button"
                  className="linkish"
                  onClick={() => onRemove(a.doc_code)}
                >
                  Remove
                </button>
              </td>
            ) : null}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
