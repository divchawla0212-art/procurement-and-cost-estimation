import { useState } from 'react'
import {
  addShortlistEntry,
  approveShortlist,
  removeShortlistEntry,
  setTbeTemplate,
} from '../../api'
import { ApprovalPills } from '../../components/primitives'
import type { StepProps } from './types'

/**
 * Who is invited, and whether procurement has signed off on the list.
 *
 * Choosing who to invite no longer happens here — it happens on the item
 * screen, before an RFQ even exists: `AvailableVendorList` there is judged
 * against the four-source pool (both approvals, hand-added, model-suggested),
 * which is strictly more than the registry-only search this step used to
 * carry. What is left here is the record of who was invited, the approval
 * that gates issuance, and the one-off escape hatch for a vendor nobody
 * registered.
 *
 * The approval control leads the step rather than trailing it.
 * `_shortlisting_exit` (`workflow/gates.py`) checks an included vendor, then
 * approval, then the TBE template — in that order — and a control a reader
 * only meets after scrolling past the table it approves invites approving
 * without having looked at what is on it.
 */
export function ShortlistingStep({ data, run, busy }: StepProps) {
  const [criteria, setCriteria] = useState((data.tbe_template?.criteria ?? []).join('\n'))

  return (
    <>
      <p className={data.shortlist_approved ? 'muted' : 'warn'}>
        {data.shortlist_approved
          ? 'Shortlist approved.'
          : 'Shortlist not approved yet. Any change to the vendors re-opens approval.'}
      </p>
      <button
        type="button"
        className="btn btn--quiet"
        disabled={busy || data.shortlist_approved}
        onClick={() => run(() => approveShortlist(data.rfq.id))}
      >
        Approve shortlist
      </button>

      <h3>Invited bidders</h3>
      {data.shortlist.length === 0 ? (
        <p className="muted">Nobody invited yet.</p>
      ) : (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Vendor</th>
                <th scope="col">Approvals</th>
                <th scope="col">Prequalification</th>
                <th scope="col">Scope fit</th>
                <th scope="col">Recorded exception</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {data.shortlist.map((e) => (
                <tr key={e.id}>
                  <td>
                    {e.vendor_name}
                    {e.vendor_id ? null : (
                      <span className="muted"> · not in the registry</span>
                    )}
                  </td>
                  {/* Live, unlike every other column here: the server re-derives
                      both keys from the registry on each read, so correcting a
                      vendor's `approved_by` shows up without the shortlist being
                      touched.

                      Three states — pills, off the list, and not checked. The
                      last is a vendor typed in by hand, who has no registry row,
                      and saying "not on the list" for them would report a check
                      that never happened.

                      The pills and the warning are shown *together* rather than
                      as alternatives: a vendor we have qualified and the client
                      has not is both of those things at once, and the pill alone
                      would let our own approval read as clearance we do not
                      have. */}
                  <td>
                    {e.approved_by === null ? (
                      <span className="muted">Not checked</span>
                    ) : (
                      <>
                        <ApprovalPills approvers={e.approved_by} />
                        {e.client_approved === false && (
                          <span className="warn">
                            Not on the {data.client_approver} list
                          </span>
                        )}
                      </>
                    )}
                  </td>
                  {/* The status as it was when the invitation was issued, not as
                      it is today. That is what makes this row an audit trail. */}
                  <td>{e.prequal_status}</td>
                  <td>{e.scope_code_fit ? 'yes' : 'no'}</td>
                  <td>
                    {e.override_reason ? (
                      <>
                        {e.override_reason}
                        {e.override_by ? (
                          <span className="muted"> — {e.override_by}</span>
                        ) : null}
                      </>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="linkish"
                      disabled={busy}
                      onClick={() => run(() => removeShortlistEntry(data.rfq.id, e.id))}
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

      <details className="pcard-details">
        <summary>Add a vendor who is not in the registry</summary>
        <p className="muted">
          For a genuine one-off. Nothing is checked, because there is no
          registry record to check against — so the prequalification you type
          here is a claim, not a verified fact.
        </p>
        <UnregisteredVendorRow rfqId={data.rfq.id} run={run} busy={busy} />
      </details>

      <h3>TBE template</h3>
      <label className="field-label" htmlFor="tbe-criteria">
        One criterion per line
      </label>
      <textarea
        id="tbe-criteria"
        className="input"
        rows={4}
        value={criteria}
        onChange={(e) => setCriteria(e.target.value)}
      />
      <button
        type="button"
        className="btn btn--quiet"
        disabled={busy}
        onClick={() =>
          run(() =>
            setTbeTemplate(data.rfq.id, {
              criteria: criteria
                .split('\n')
                .map((c) => c.trim())
                .filter(Boolean),
            }),
          )
        }
      >
        Save TBE template
      </button>
    </>
  )
}

/** The old free-text row, unchanged in what it posts. Kept for the genuine
 *  one-off rather than deleted: forcing every urgent vendor through
 *  registration is how a shortlist ends up being kept somewhere else. */
function UnregisteredVendorRow({
  rfqId,
  run,
  busy,
}: {
  rfqId: string
  run: (action: () => Promise<unknown>) => Promise<void>
  busy: boolean
}) {
  const [vendor, setVendor] = useState('')
  const [prequal, setPrequal] = useState('Under review')
  const [scopeFit, setScopeFit] = useState(true)

  return (
    <div className="fxrow">
      <input
        className="input"
        aria-label="Vendor name"
        placeholder="Vendor"
        value={vendor}
        onChange={(e) => setVendor(e.target.value)}
      />
      <select
        className="input"
        aria-label="Prequalification status"
        value={prequal}
        onChange={(e) => setPrequal(e.target.value)}
      >
        <option>Approved</option>
        <option>Under review</option>
        <option>Suspended</option>
        <option>Not qualified</option>
      </select>
      <label>
        <input
          type="checkbox"
          checked={scopeFit}
          onChange={(e) => setScopeFit(e.target.checked)}
        />{' '}
        Scope fit
      </label>
      <button
        type="button"
        className="btn btn--quiet"
        disabled={busy || !vendor.trim()}
        onClick={() =>
          run(async () => {
            await addShortlistEntry(rfqId, {
              vendor_name: vendor.trim(),
              prequal_status: prequal,
              scope_code_fit: scopeFit,
              included: true,
            })
            setVendor('')
          })
        }
      >
        Add vendor
      </button>
    </div>
  )
}
