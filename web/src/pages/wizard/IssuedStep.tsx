import { useState } from 'react'
import { addVdrlLine, removeVdrlLine } from '../../api'
import { RaiseRfqStep } from './RaiseRfqStep'
import type { StepProps } from './types'

/**
 * What the enquiry is, and what has to come back.
 *
 * Two halves. The technical package comes first, because it is what vendors
 * bid against and it is the thing that gets frozen; it arrived here from the
 * Scoping step when that stage was removed from the process, and BD-6 turned
 * it from a register of document codes into the documents themselves —
 * `RaiseRfqStep`. The VDRL follows: the documents each vendor must return with
 * their bid, which is a list of requirements rather than a list of files and
 * so stays a register.
 */
export function IssuedStep({ data, run, busy }: Omit<StepProps, 'tick'>) {
  const [code, setCode] = useState('')
  const [title, setTitle] = useState('')
  const [docType, setDocType] = useState('Datasheet')
  const [mandatory, setMandatory] = useState(true)

  return (
    <>
      <h3>Technical package</h3>
      <RaiseRfqStep data={data} run={run} busy={busy} />

      <h3>Documents required with the bid</h3>
      {data.vdrl.length === 0 ? (
        <p className="muted">No documents required yet.</p>
      ) : (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Code</th>
                <th scope="col">Title</th>
                <th scope="col">Type</th>
                <th scope="col">Mandatory</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {data.vdrl.map((line) => (
                <tr key={line.id}>
                  <td className="mono">{line.doc_code}</td>
                  <td>{line.title}</td>
                  <td>{line.doc_type}</td>
                  <td>{line.mandatory ? 'yes' : 'no'}</td>
                  <td>
                    <button
                      type="button"
                      className="linkish"
                      disabled={busy}
                      onClick={() => run(() => removeVdrlLine(data.rfq.id, line.id))}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="fxrow">
        <input
          className="input"
          aria-label="Document code"
          placeholder="Doc code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
        <input
          className="input"
          aria-label="Document title"
          placeholder="Title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <select
          className="input"
          aria-label="Document type"
          value={docType}
          onChange={(e) => setDocType(e.target.value)}
        >
          <option>Datasheet</option>
          <option>GA Drawing</option>
          <option>Test Procedure</option>
          <option>Certificate</option>
        </select>
        <label>
          <input
            type="checkbox"
            checked={mandatory}
            onChange={(e) => setMandatory(e.target.checked)}
          />{' '}
          Mandatory
        </label>
        <button
          type="button"
          className="btn btn--quiet"
          disabled={busy || !code.trim()}
          onClick={() =>
            run(async () => {
              await addVdrlLine(data.rfq.id, {
                doc_code: code.trim(),
                title: title.trim(),
                doc_type: docType,
                mandatory,
              })
              setCode('')
              setTitle('')
            })
          }
        >
          Add document
        </button>
      </div>
    </>
  )
}
