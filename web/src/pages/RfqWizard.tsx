import { useState } from 'react'
import type { JSX } from 'react'
import {
  addShortlistEntry,
  addVdrlLine,
  approveShortlist,
  fetchCandidates,
  fetchRfq,
  freezeTechnicalPackage,
  inviteRegisteredBidder,
  removeShortlistEntry,
  removeVdrlLine,
  setTbeTemplate,
  setTechnicalPackage,
  transitionRfq,
} from '../api'
import type { Attachment, Candidate, RfqDetail } from '../types'
import { useAsync } from '../useAsync'
import { Card, ErrorState, LoadingState } from '../components/primitives'

/**
 * The RFQ, walked one step at a time.
 *
 * Two ideas kept separate on purpose:
 *
 * - **Where the RFQ actually is** — `rfq.stage`, which only ever moves through
 *   a gated transition on the server.
 * - **Which step you are looking at** — local state. You can open any step you
 *   have already passed and keep editing it, because a shortlist that cannot be
 *   corrected after issuance is a shortlist that gets corrected in a spreadsheet
 *   instead.
 *
 * The one thing editing cannot undo is a frozen technical package: the server
 * refuses, and this shows that refusal rather than hiding the control, so the
 * reason is visible rather than mysterious.
 *
 * Ticking a step off is `transitionRfq`. The button is not disabled when the
 * gate is closed — a disabled button explains nothing. It is enabled, and a
 * refusal surfaces the gate's own sentence.
 */

/** The steps this phase covers. Bids and evaluation arrive with the ingestion
 *  work, so the wizard stops at Clarifications rather than showing four steps
 *  with nothing behind them. */
const WIZARD_STAGES = ['Scoping', 'Shortlisting', 'Issued', 'Clarifications'] as const

const STEP_BLURB: Record<string, string> = {
  Scoping: 'Define the package and freeze it. Vendors bid against this revision.',
  Shortlisting:
    'Invite bidders from the registry, get the list approved, and attach the TBE template.',
  Issued: 'List the documents each vendor must return with their bid.',
  Clarifications: 'Technical queries are handled here before bids are opened.',
}

export function RfqWizard({
  rfqId,
  stages,
  onBack,
}: {
  rfqId: string
  stages: string[]
  onBack: () => void
}): JSX.Element {
  const [tick, setTick] = useState(0)
  const { data, error, loading } = useAsync(() => fetchRfq(rfqId), [rfqId, tick])
  const [openStep, setOpenStep] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const reload = () => setTick((t) => t + 1)

  /** Every write goes through here so one place owns the busy flag and the
   *  server's refusal message. */
  async function run(action: () => Promise<unknown>) {
    setBusy(true)
    setActionError(null)
    try {
      await action()
      reload()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <LoadingState label="Loading RFQ…" />
  if (error) return <ErrorState message={error} />
  if (!data) return <ErrorState message="No RFQ was returned." />

  const { rfq, gate } = data
  const stageIndex = stages.indexOf(rfq.stage)
  const step = openStep ?? (WIZARD_STAGES.includes(rfq.stage as never) ? rfq.stage : WIZARD_STAGES[0])
  const stepIndex = stages.indexOf(step)
  const isCurrentStep = step === rfq.stage
  const nextStage = stages[stageIndex + 1]

  return (
    <>
      <header className="page-head">
        <p className="eyebrow">
          <button type="button" className="linkback" onClick={onBack}>
            ← All RFQs
          </button>
        </p>
        <h1>{rfq.reference}</h1>
        <p className="sub">
          {rfq.package} · {rfq.discipline} · est.{' '}
          {rfq.value_estimate_aed.toLocaleString()} AED
        </p>
      </header>

      <ol className="wizsteps" aria-label="RFQ steps">
        {WIZARD_STAGES.map((name) => {
          const i = stages.indexOf(name)
          const done = i < stageIndex
          const current = name === rfq.stage
          const cls = [
            'wizstep',
            done && 'is-done',
            current && 'is-current',
            name === step && 'is-open',
          ]
            .filter(Boolean)
            .join(' ')
          return (
            <li key={name}>
              <button
                type="button"
                className={cls}
                aria-current={current ? 'step' : undefined}
                onClick={() => setOpenStep(name)}
              >
                <span className="wizstep-mark" aria-hidden="true">
                  {done ? '✓' : i + 1}
                </span>
                <span className="wizstep-name">{name}</span>
              </button>
            </li>
          )
        })}
      </ol>

      {actionError ? <div className="banner banner--error">{actionError}</div> : null}

      {!isCurrentStep ? (
        <div className="banner">
          {stepIndex < stageIndex
            ? `This step is complete. You can still edit it — the RFQ stays at ${rfq.stage}.`
            : `Not reached yet. The RFQ is at ${rfq.stage}.`}
        </div>
      ) : null}

      <Card title={step}>
        <p className="muted">{STEP_BLURB[step]}</p>
        {step === 'Scoping' ? <ScopingStep data={data} run={run} busy={busy} /> : null}
        {step === 'Shortlisting' ? (
          <ShortlistingStep data={data} run={run} busy={busy} tick={tick} />
        ) : null}
        {step === 'Issued' ? <IssuedStep data={data} run={run} busy={busy} /> : null}
        {step === 'Clarifications' ? (
          <p className="muted">
            Nothing to capture here yet — technical queries arrive with the
            agent work.
          </p>
        ) : null}
      </Card>

      <Card title="Stage history">
        <ol className="historylist">
          {rfq.history.map((entry, i) => (
            // Index is part of the key on purpose: a retender walks the same
            // edge twice, so `to_stage` alone is not unique within one history.
            <li key={`${entry.to_stage}-${i}`}>
              <span className="mono">{entry.at.slice(0, 10)}</span>{' '}
              <b>
                {entry.from_stage ? `${entry.from_stage} → ` : ''}
                {entry.to_stage}
              </b>{' '}
              <span className="muted">by {entry.by}</span>
              {entry.reason ? <div className="muted">{entry.reason}</div> : null}
            </li>
          ))}
        </ol>
      </Card>

      {isCurrentStep && nextStage ? (
        <Card title="Complete this step">
          <p className={gate.passed ? 'muted' : 'warn'}>
            {gate.passed
              ? `Everything ${rfq.stage} needs is in place.`
              : gate.reason}
          </p>
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={() => run(() => transitionRfq(rfqId, nextStage))}
          >
            Mark {rfq.stage} complete → {nextStage}
          </button>
        </Card>
      ) : null}
    </>
  )
}

type StepProps = {
  data: RfqDetail
  run: (action: () => Promise<unknown>) => Promise<void>
  busy: boolean
  /** The wizard's reload counter. Only the Shortlisting step needs it: it
   *  loads a second resource, and that resource changes when a write here
   *  succeeds. */
  tick: number
}

/* ------------------------------------------------------------------ Scoping */

function ScopingStep({ data, run, busy }: Omit<StepProps, 'tick'>) {
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

function AttachmentTable({
  attachments,
  onRemove,
}: {
  attachments: Attachment[]
  onRemove?: (docCode: string) => void
}) {
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

/* ------------------------------------------------------------- Shortlisting */

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
function ShortlistingStep({ data, run, busy, tick }: StepProps) {
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
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Vendor</th>
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
      )}

      <h3>Registry</h3>
      {candidates.loading ? (
        <p className="muted">Loading bidders…</p>
      ) : candidates.error ? (
        <p className="warn">{candidates.error}</p>
      ) : (candidates.data ?? []).length === 0 ? (
        <p className="muted">
          The bidder registry is empty. Add bidders under Bidders, and they will
          be offered here with their prequalification checked for you.
        </p>
      ) : (
        <ul className="candidatelist">
          {(candidates.data ?? []).map((c) => (
            <CandidateRow key={c.bidder.id} candidate={c} rfqId={data.rfq.id} run={run} busy={busy} />
          ))}
        </ul>
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
        {bidder.trade_categories.join(' · ') || 'No trade categories recorded'}
        {' · '}
        {bidder.country}
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

/* ------------------------------------------------------------------- Issued */

function IssuedStep({ data, run, busy }: Omit<StepProps, 'tick'>) {
  const [code, setCode] = useState('')
  const [title, setTitle] = useState('')
  const [docType, setDocType] = useState('Datasheet')
  const [mandatory, setMandatory] = useState(true)

  return (
    <>
      {data.vdrl.length === 0 ? (
        <p className="muted">No documents required yet.</p>
      ) : (
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
