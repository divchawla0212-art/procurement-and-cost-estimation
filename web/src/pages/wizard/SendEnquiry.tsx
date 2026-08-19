import { useState } from 'react'
import type { JSX } from 'react'
import { previewEnquiry, sendEnquiry } from '../../api'
import type { EnquiryAudience } from '../../types'
import type { StepProps } from './types'

/** What each audience is called on screen. The browser spells the labels; the
 *  server owns the values, which is why the picker's `value`s come from
 *  `EnquiryAudience` and never from a string typed here. */
const AUDIENCE_LABELS: Record<EnquiryAudience, string> = {
  unsent: 'vendors not yet sent to',
  outdated: 'vendors without the latest documents',
  all: 'everyone on the shortlist',
}

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
 * **There is no send control for nobody.** A preview that reaches zero
 * vendors used to render a live `Send to 0 vendors` button under
 * `This sends real email to 0 vendors.` — a control whose only honest
 * outcome is nothing happening, sitting where the action goes, on exactly
 * the screen a buyer reaches when every vendor has already been sent. The
 * zero case now says so in a sentence and offers the only thing that would
 * change it: sending the enquiry again to the whole shortlist.
 *
 * **That resend is one press, and it is the one place a send happens without
 * the addresses being on screen first.** Under `unsent` an already-sent
 * vendor's row shows the reason they are out of scope, not the mailbox they
 * would be written to, so a contact sheet re-uploaded since the first send
 * changes where the mail goes without this table showing it. Chosen
 * deliberately over widening-then-previewing: the vendors are the same
 * vendors, listed above, and the second press was the thing being removed.
 * The send always re-previews afterwards, so the table is never left
 * describing a state that has since changed.
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
  // Narrowest by default: sending a tender again is an act somebody chooses
  // each time, never a setting that stays on from an earlier decision.
  const [audience, setAudience] = useState<EnquiryAudience>('unsent')

  const reachable = preview?.recipients.filter((r) => r.to !== null).length ?? 0

  async function loadPreview(pick: EnquiryAudience) {
    setPreviewLoading(true)
    setPreviewError(null)
    try {
      setPreview(await previewEnquiry(data.rfq.id, pick))
    } catch (e) {
      setPreviewError(e instanceof Error ? e.message : String(e))
    } finally {
      setPreviewLoading(false)
    }
  }

  /** Send, then re-read who is left.
   *
   *  `run` swallows the server's refusal into the wizard's banner, so it
   *  cannot be asked whether the send landed — the flag is set beside the
   *  `setResult` that only a successful call reaches. Without it a refused
   *  send would move the picker to an audience nothing was sent to. */
  async function send(pick: EnquiryAudience) {
    let sent = false
    await run(async () => {
      setResult(await sendEnquiry(data.rfq.id, pick))
      sent = true
    })
    if (!sent) return
    setAudience(pick)
    await loadPreview(pick)
  }

  /* The control renders in **both** branches, and that is the whole point.
     It used to live only before the preview, so the moment a buyer discovered
     everybody had already been sent — which is exactly when they need to widen
     the audience — the option had vanished and only a page reload brought it
     back. Changing it here re-previews immediately, so the table on screen is
     always the list that would go out. */
  const picker = (
    <label className="fxrow">
      <span>Send to</span>
      <select
        className="input"
        value={audience}
        disabled={busy || previewLoading}
        onChange={(e) => {
          const pick = e.target.value as EnquiryAudience
          setAudience(pick)
          if (preview !== null) void loadPreview(pick)
        }}
      >
        {(Object.keys(AUDIENCE_LABELS) as EnquiryAudience[]).map((value) => (
          <option key={value} value={value}>
            {AUDIENCE_LABELS[value]}
          </option>
        ))}
      </select>
    </label>
  )

  // Nobody in scope, but somebody is on the shortlist and the audience is
  // narrower than everyone: widening is the one action that would change the
  // answer, so it is the only one offered. With `all` already on screen there
  // is nothing left to widen to — every remaining row is a missing address or
  // an oversized package, and neither is fixed from this card.
  const canResendToEveryone =
    preview !== null &&
    reachable === 0 &&
    preview.recipients.length > 0 &&
    preview.audience !== 'all'

  return (
    <>
      <h3>Send the enquiry</h3>
      {preview === null ? (
        <>
          {picker}
          <button
            type="button"
            className="btn"
            disabled={busy || previewLoading}
            onClick={() => void loadPreview(audience)}
          >
            Preview recipients
          </button>
          {previewError ? <p className="warn">{previewError}</p> : null}
        </>
      ) : (
        <>
          {picker}
          {previewError ? <p className="warn">{previewError}</p> : null}
          {reachable === 0 ? (
            <>
              <p className="muted">
                {preview.recipients.length === 0
                  ? 'Nobody is on the shortlist for this enquiry.'
                  : `Nobody is in scope for “${AUDIENCE_LABELS[preview.audience]}” — ` +
                    'every vendor below has a reason beside them.'}
              </p>
              {canResendToEveryone && preview.transport === 'smtp' ? (
                <p className="warn">
                  Sending again writes real email to every vendor below with an
                  address on file.
                </p>
              ) : null}
              {canResendToEveryone && preview.transport !== 'smtp' ? (
                <p className="muted">This writes to the outbox. Nobody receives anything.</p>
              ) : null}
            </>
          ) : preview.transport === 'smtp' ? (
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
          {reachable === 0 ? (
            canResendToEveryone ? (
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => void send('all')}
              >
                Send again to everyone on the shortlist
              </button>
            ) : null
          ) : (
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => void send(preview.audience)}
            >
              {preview.audience === 'unsent' ? 'Send to' : 'Send again to'} {reachable} vendors
            </button>
          )}
        </>
      )}
      {result ? (
        <p className="muted">
          Sent to {result.sent.length} vendor{result.sent.length === 1 ? '' : 's'}.
        </p>
      ) : null}
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
