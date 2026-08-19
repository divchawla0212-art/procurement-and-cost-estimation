import { useState } from 'react'
import type { JSX } from 'react'
import { previewEnquiry, sendEnquiry } from '../../api'
import type { StepProps } from './types'

/**
 * Preview the recipients, then send — and the send control does not exist
 * until a preview is on screen.
 *
 * Rendered only inside the post-preview branch, not disabled outside it:
 * a disabled button some other state change could enable is one refactor
 * away from being reachable without a preview, and the preview is the only
 * thing standing between a wrong vendor-name match and a real company
 * receiving someone else's tender.
 *
 * The transport sentence is two separate strings in two branches, not one
 * string with an interpolated word — "writes to the outbox" and "sends real
 * email" must not read alike, or a real tender goes out during a demo and
 * nobody notices.
 *
 * **The preview does not go through `run`.** `run`'s own docstring says
 * "every write goes through here" — a preview stores nothing (asserted
 * server-side, byte-for-byte), so it is not a write, and routing it through
 * the wizard's write path reloads the whole RFQ for no reason. That reload
 * used to unmount this component mid-preview: `RfqWizard` blanks its screen
 * while `loading` is true, `SendEnquiry` and its `preview` state went with
 * it, and the card silently reset to the button the moment the fetch that
 * had just succeeded came back. The preview gets its own local loading and
 * error state instead, and a failed preview renders in this card rather than
 * the wizard's banner.
 */
export function SendEnquiry({ data, run, busy }: Omit<StepProps, 'tick'>): JSX.Element {
  const [preview, setPreview] = useState<Awaited<ReturnType<typeof previewEnquiry>> | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [result, setResult] = useState<Awaited<ReturnType<typeof sendEnquiry>> | null>(null)

  const reachable = preview?.recipients.filter((r) => r.to !== null).length ?? 0

  async function loadPreview() {
    setPreviewLoading(true)
    setPreviewError(null)
    try {
      setPreview(await previewEnquiry(data.rfq.id))
    } catch (e) {
      setPreviewError(e instanceof Error ? e.message : String(e))
    } finally {
      setPreviewLoading(false)
    }
  }

  return (
    <>
      <h3>Send the enquiry</h3>
      {preview === null ? (
        <>
          <button
            type="button"
            className="btn"
            disabled={busy || previewLoading}
            onClick={() => void loadPreview()}
          >
            Preview recipients
          </button>
          {previewError ? <p className="warn">{previewError}</p> : null}
        </>
      ) : (
        <>
          {preview.transport === 'smtp' ? (
            <p className="warn">This sends real email to {reachable} vendors.</p>
          ) : (
            <p className="muted">This writes to the outbox. Nobody receives anything.</p>
          )}
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Vendor</th>
                  <th scope="col">Goes to</th>
                </tr>
              </thead>
              <tbody>
                {preview.recipients.map((r) => (
                  <tr key={r.shortlist_entry_id}>
                    <td>{r.vendor_name}</td>
                    <td>
                      {r.to === null ? (
                        <span className="warn">{r.skip_reason}</span>
                      ) : (
                        r.to.map((address) => <div key={address}>{address}</div>)
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={() => run(async () => setResult(await sendEnquiry(data.rfq.id)))}
          >
            Send to {reachable} vendors
          </button>
        </>
      )}
      {result?.skipped.length ? (
        <ul>
          {result.skipped.map((s) => (
            <li key={s.vendor_name} className="warn">
              {s.reason}
            </li>
          ))}
        </ul>
      ) : null}
    </>
  )
}
