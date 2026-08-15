import { useState } from 'react'
import {
  addShortlistEntry,
  approveShortlist,
  fetchCandidates,
  inviteRegisteredBidder,
  removeShortlistEntry,
  setTbeTemplate,
} from '../../api'
import type { Candidate } from '../../types'
import { useAsync } from '../../useAsync'
import { ApprovalPills } from '../../components/primitives'
import type { StepProps } from './types'

/**
 * Who is invited, chosen from the registry rather than typed from memory.
 *
 * The candidate list is the whole registry judged against *this* RFQ, so the
 * reader decides with the same information the server will decide with. A
 * blocked bidder keeps their Invite control — it is the override reason beside
 * it that makes including them a recorded, attributed act, and hiding the
 * control would only send the decision back to a spreadsheet.
 *
 * Inviting sends `vendor_id` and nothing else. The name, prequal status and
 * scope fit are the registry's, snapshotted server-side at the moment of the
 * decision; a screen that sent them would be claiming they were its to decide.
 */
export function ShortlistingStep({ data, run, busy, tick }: StepProps) {
  const [criteria, setCriteria] = useState((data.tbe_template?.criteria ?? []).join('\n'))
  // Keyed on the wizard's tick as well as the RFQ, so inviting somebody
  // refreshes who is still available rather than leaving a stale list.
  const candidates = useAsync(() => fetchCandidates(data.rfq.id), [data.rfq.id, tick])

  return (
    <>
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

      <h3>Registry</h3>
      {candidates.loading ? (
        <p className="muted">Loading bidders…</p>
      ) : candidates.error ? (
        <p className="warn">{candidates.error}</p>
      ) : (candidates.data ?? []).length === 0 ? (
        <p className="muted">
          The bidder registry is empty. Import an Approved Vendor List, and its
          vendors will be offered here with their prequalification checked for
          you.
        </p>
      ) : (
        <CandidateList
          candidates={candidates.data ?? []}
          discipline={data.rfq.discipline}
          rfqId={data.rfq.id}
          run={run}
          busy={busy}
        />
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

/** How many candidates to render at once.
 *
 *  On a registry imported from an ADNOC AVL a common product group has over a
 *  hundred approved vendors, and rendering all of them is both slow and
 *  useless — nobody shortlists by scrolling a hundred cards. The list leads
 *  with those who fit (the server orders it that way), and says plainly how
 *  many it is holding back rather than truncating in silence.
 */
const CANDIDATE_CAP = 25

function CandidateList({
  candidates,
  discipline,
  rfqId,
  run,
  busy,
}: {
  candidates: Candidate[]
  discipline: string
  rfqId: string
  run: (action: () => Promise<unknown>) => Promise<void>
  busy: boolean
}) {
  const [query, setQuery] = useState('')
  // Default on: the registry is the client's whole approved list, and the
  // question in front of the reader is almost always "who of ours can do
  // this". The toggle is right there when the answer is nobody.
  const [fittingOnly, setFittingOnly] = useState(true)

  const needle = query.trim().toLowerCase()
  const shown = candidates.filter((c) => {
    if (fittingOnly && !c.suitability.scope_fit && !c.shortlisted) return false
    if (!needle) return true
    return (
      c.bidder.name.toLowerCase().includes(needle) ||
      c.bidder.represented_manufacturers.some((m) =>
        m.toLowerCase().includes(needle),
      )
    )
  })

  return (
    <>
      <div className="fxrow">
        <input
          className="input"
          aria-label="Search candidates"
          placeholder="Search by vendor or manufacturer"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <label>
          <input
            type="checkbox"
            checked={fittingOnly}
            onChange={(e) => setFittingOnly(e.target.checked)}
          />{' '}
          Only those registered for {discipline}
        </label>
      </div>

      <p className="muted">
        {shown.length} of {candidates.length} in the registry.
        {shown.length > CANDIDATE_CAP && (
          <> Showing the first {CANDIDATE_CAP} — search to narrow it.</>
        )}
      </p>

      {shown.length === 0 ? (
        <p className="muted">
          Nobody in the registry is listed for {discipline}. Untick the filter
          to invite somebody anyway — it will be recorded as a scope mismatch.
        </p>
      ) : (
        <ul className="candidatelist">
          {shown.slice(0, CANDIDATE_CAP).map((c) => (
            <CandidateRow
              key={c.bidder.id}
              candidate={c}
              rfqId={rfqId}
              run={run}
              busy={busy}
            />
          ))}
        </ul>
      )}
    </>
  )
}

function CandidateRow({
  candidate,
  rfqId,
  run,
  busy,
}: {
  candidate: Candidate
  rfqId: string
  run: (action: () => Promise<unknown>) => Promise<void>
  busy: boolean
}) {
  const [reason, setReason] = useState('')
  const { bidder, suitability } = candidate
  const shortName = bidder.name.split(/\s+/)[0]

  return (
    <li className="candidate">
      <div className="candidate-head">
        <b>{bidder.name}</b>
        <span
          className={`pcard-status pcard-status--${suitability.effective_prequal
            .toLowerCase()
            .replace(/\s+/g, '-')}`}
        >
          {suitability.effective_prequal}
        </span>
        {/* Whose list they are on. On a client's imported AVL every candidate
            is approved, so this is the distinction that actually separates
            them — which is why the pills are coloured rather than uniform. */}
        <ApprovalPills approvers={bidder.approved_by} />
        {candidate.shortlisted ? (
          <span className="muted">Invited</span>
        ) : (
          <button
            type="button"
            className="btn btn-sm"
            disabled={busy}
            onClick={() =>
              run(() =>
                inviteRegisteredBidder(rfqId, {
                  vendor_id: bidder.id,
                  // Omitted entirely when empty, so an eligible bidder is not
                  // recorded as carrying an exception with no text.
                  ...(reason.trim() ? { override_reason: reason.trim() } : {}),
                }),
              )
            }
          >
            Invite {bidder.name}
          </button>
        )}
      </div>

      <p className="muted">
        {bidder.trade_categories.length === 0
          ? 'No trade categories recorded'
          : bidder.trade_categories.slice(0, 2).join(' · ') +
            (bidder.trade_categories.length > 2
              ? ` · +${bidder.trade_categories.length - 2} more`
              : '')}
        {bidder.represented_manufacturers.length > 0 && (
          <> — represents {bidder.represented_manufacturers.slice(0, 3).join(', ')}</>
        )}
      </p>

      {suitability.blockers.map((b) => (
        <p className="warn" key={b}>
          {b}
        </p>
      ))}
      {suitability.cautions.map((c) => (
        <p className="muted" key={c}>
          {c}
        </p>
      ))}

      {!suitability.eligible && !candidate.shortlisted ? (
        <>
          <label className="field-label" htmlFor={`override-${bidder.id}`}>
            Reason for inviting {shortName} anyway
          </label>
          <input
            id={`override-${bidder.id}`}
            className="input"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </>
      ) : null}
    </li>
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
