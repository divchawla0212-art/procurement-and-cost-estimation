import { useState } from 'react'
import {
  answerQuery,
  deleteAddendum,
  draftAddendum,
  issueAddendum,
  raiseQuery,
  withdrawQuery,
} from '../../api'
import type { Addendum, Attachment, ClarificationQuery } from '../../types'
import { AttachmentTable } from './AttachmentTable'
import type { StepProps } from './types'

/**
 * The bid-period query round.
 *
 * Two ideas, and the relationship between them is the point of the screen:
 *
 * - **A query** is one invited bidder's question. Answering it circulates the
 *   answer to the whole included shortlist; withholding it costs a recorded
 *   reason, because an answer given to one bidder and not the others is the
 *   classic tender-fairness failure.
 * - **An addendum** is what an answer sometimes forces. It is the one
 *   sanctioned way a frozen technical package moves, and issuing it supersedes
 *   that package at a new revision.
 *
 * Both halves must be settled before bids are opened, and the gate says so.
 *
 * The raiser is picked from the included shortlist rather than typed — the same
 * lesson the registry taught the Shortlisting step. Withdrawal is a reason
 * *and* a button, never a bare button: the server refuses without a reason, and
 * a control that cannot succeed is worse than no control.
 */

/** Open first, then answered, then withdrawn — what still needs doing leads.
 *  An explicit rank rather than array order, which is not a contract. */
const STATE_RANK: Record<string, number> = { Open: 0, Answered: 1, Withdrawn: 2 }

export function ClarificationsStep({ data, run, busy }: StepProps) {
  const ordered = [...data.queries].sort(
    (a, b) =>
      STATE_RANK[a.state] - STATE_RANK[b.state] || a.number.localeCompare(b.number),
  )
  const openCount = data.queries.filter((q) => q.state === 'Open').length
  const draftCount = data.addenda.filter((a) => a.draft).length

  return (
    <>
      {data.bid_due_date ? (
        <p className="banner">
          Bids are due <b>{data.bid_due_date}</b>, as set by the latest issued
          addendum.
        </p>
      ) : (
        <p className="muted">
          No addendum has moved the bid due date.
        </p>
      )}

      <h3>Query register</h3>
      {ordered.length === 0 ? (
        <p className="muted">
          No clarifications raised yet. A query recorded here is answered to the
          whole shortlist unless you record a reason not to.
        </p>
      ) : (
        <ul className="candidatelist">
          {ordered.map((query) => (
            <QueryRow
              key={query.id}
              query={query}
              rfqId={data.rfq.id}
              run={run}
              busy={busy}
            />
          ))}
        </ul>
      )}

      <RaiseQueryForm data={data} run={run} busy={busy} />

      <h3>Addenda</h3>
      {data.addenda.length === 0 ? (
        <p className="muted">No addenda. The package stands as frozen.</p>
      ) : (
        <ul className="candidatelist">
          {data.addenda.map((addendum) => (
            <AddendumRow
              key={addendum.id}
              addendum={addendum}
              rfqId={data.rfq.id}
              run={run}
              busy={busy}
            />
          ))}
        </ul>
      )}

      <DraftAddendumForm data={data} run={run} busy={busy} />

      <p className={openCount || draftCount ? 'warn' : 'muted'}>
        {openCount || draftCount
          ? `Bids cannot be opened yet: ${openCount} open ${
              openCount === 1 ? 'query' : 'queries'
            }, ${draftCount} draft ${draftCount === 1 ? 'addendum' : 'addenda'}.`
          : 'Nothing outstanding. Bids can be opened.'}
      </p>
    </>
  )
}

/* -------------------------------------------------------------- one query */

function QueryRow({
  query,
  rfqId,
  run,
  busy,
}: {
  query: ClarificationQuery
  rfqId: string
  run: (action: () => Promise<unknown>) => Promise<void>
  busy: boolean
}) {
  const [answer, setAnswer] = useState('')
  // Progressive disclosure, the same shape `CandidateRow` uses for
  // `override_reason`: the honest default costs nothing, the exception costs a
  // sentence.
  const [restricted, setRestricted] = useState(false)
  const [reason, setReason] = useState('')
  const [withdrawReason, setWithdrawReason] = useState('')

  return (
    <li className="candidate">
      <div className="candidate-head">
        <b className="mono">{query.number}</b>
        <span className={`pcard-status pcard-status--${query.state.toLowerCase()}`}>
          {query.state}
        </span>
        <span className="muted">
          {query.category} · {query.raised_by_name} · raised {query.raised_on}
        </span>
      </div>

      <p>{query.question}</p>

      {query.answer ? (
        <>
          <p>
            <b>Answer:</b> {query.answer}
            {query.answered_by ? (
              <span className="muted"> — {query.answered_by}</span>
            ) : null}
          </p>
          {query.circulated ? (
            <p className="muted">Circulated to the whole shortlist.</p>
          ) : (
            <p className="warn">
              Not circulated: {query.restricted_reason}
            </p>
          )}
        </>
      ) : null}

      {query.state === 'Withdrawn' ? (
        <p className="muted">
          Withdrawn: {query.withdrawn_reason}
          {query.withdrawn_by ? <> — {query.withdrawn_by}</> : null}
        </p>
      ) : null}

      {query.state === 'Open' ? (
        <>
          <label className="field-label" htmlFor={`answer-${query.id}`}>
            Answer to {query.number}
          </label>
          <textarea
            id={`answer-${query.id}`}
            className="input"
            rows={2}
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
          />
          <label>
            <input
              type="checkbox"
              checked={restricted}
              onChange={(e) => setRestricted(e.target.checked)}
            />{' '}
            Do not circulate this answer to the other bidders
          </label>
          {restricted ? (
            <>
              <label className="field-label" htmlFor={`restrict-${query.id}`}>
                Why this answer is not circulated
              </label>
              <input
                id={`restrict-${query.id}`}
                className="input"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </>
          ) : null}

          <div className="wizactions">
            <button
              type="button"
              className="btn btn-sm"
              disabled={busy || !answer.trim()}
              onClick={() =>
                run(() =>
                  answerQuery(rfqId, query.id, {
                    answer: answer.trim(),
                    // Omitted entirely when circulated, so an unrestricted
                    // answer is never recorded as restricted with no text.
                    ...(restricted ? { restricted_reason: reason.trim() } : {}),
                  }),
                )
              }
            >
              Answer {query.number}
            </button>
          </div>

          <label className="field-label" htmlFor={`withdraw-${query.id}`}>
            Reason for withdrawing {query.number}
          </label>
          <div className="fxrow">
            <input
              id={`withdraw-${query.id}`}
              className="input"
              value={withdrawReason}
              onChange={(e) => setWithdrawReason(e.target.value)}
            />
            <button
              type="button"
              className="btn btn--quiet btn-sm"
              disabled={busy || !withdrawReason.trim()}
              onClick={() =>
                run(() => withdrawQuery(rfqId, query.id, withdrawReason.trim()))
              }
            >
              Withdraw {query.number}
            </button>
          </div>
        </>
      ) : null}
    </li>
  )
}

/* ------------------------------------------------------------ raise a query */

function RaiseQueryForm({ data, run, busy }: Omit<StepProps, 'tick'>) {
  // Only an included bidder may raise one, and the server refuses anybody else.
  // Offering them here would be offering a control that cannot succeed.
  const invited = data.shortlist.filter((e) => e.included)
  const [entryId, setEntryId] = useState(invited[0]?.id ?? '')
  const [category, setCategory] = useState<'Technical' | 'Commercial'>('Technical')
  const [question, setQuestion] = useState('')
  const [raisedOn, setRaisedOn] = useState(() => new Date().toISOString().slice(0, 10))

  if (invited.length === 0) {
    return (
      <p className="muted">
        Nobody is included on the shortlist, so there is nobody who could raise a
        clarification.
      </p>
    )
  }

  return (
    <>
      <h3>Record a query</h3>
      <div className="fxrow">
        <label className="field-label" htmlFor="query-raiser">
          Raised by
        </label>
        <select
          id="query-raiser"
          className="input"
          value={entryId}
          onChange={(e) => setEntryId(e.target.value)}
        >
          {invited.map((e) => (
            <option key={e.id} value={e.id}>
              {e.vendor_name}
            </option>
          ))}
        </select>

        <label className="field-label" htmlFor="query-category">
          Category
        </label>
        <select
          id="query-category"
          className="input"
          value={category}
          onChange={(e) => setCategory(e.target.value as 'Technical' | 'Commercial')}
        >
          <option value="Technical">Technical</option>
          <option value="Commercial">Commercial</option>
        </select>

        <label className="field-label" htmlFor="query-raised-on">
          Raised on
        </label>
        <input
          id="query-raised-on"
          className="input"
          type="date"
          value={raisedOn}
          onChange={(e) => setRaisedOn(e.target.value)}
        />
      </div>

      <label className="field-label" htmlFor="query-question">
        Question
      </label>
      <textarea
        id="query-question"
        className="input"
        rows={3}
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
      />
      <button
        type="button"
        className="btn"
        disabled={busy || !question.trim() || !entryId}
        onClick={() =>
          run(async () => {
            await raiseQuery(data.rfq.id, {
              raised_by_entry_id: entryId,
              category,
              question: question.trim(),
              raised_on: raisedOn,
            })
            setQuestion('')
          })
        }
      >
        Record query
      </button>
    </>
  )
}

/* ----------------------------------------------------------- one addendum */

function AddendumRow({
  addendum,
  rfqId,
  run,
  busy,
}: {
  addendum: Addendum
  rfqId: string
  run: (action: () => Promise<unknown>) => Promise<void>
  busy: boolean
}) {
  return (
    <li className="candidate">
      <div className="candidate-head">
        <b className="mono">{addendum.number}</b>
        <span
          className={`pcard-status pcard-status--${addendum.draft ? 'draft' : 'issued'}`}
        >
          {addendum.draft ? 'Draft' : 'Issued'}
        </span>
        <span className="muted">
          {addendum.supersedes_revision} → {addendum.revision}
        </span>
        {addendum.draft ? (
          <>
            <button
              type="button"
              className="btn btn-sm"
              disabled={busy}
              onClick={() => run(() => issueAddendum(rfqId, addendum.id))}
            >
              Issue {addendum.number}
            </button>
            <button
              type="button"
              className="linkish"
              disabled={busy}
              onClick={() => run(() => deleteAddendum(rfqId, addendum.id))}
            >
              Delete {addendum.number}
            </button>
          </>
        ) : (
          // No controls at all once issued: bidders hold it, and the server
          // refuses both an edit and a delete.
          <span className="muted">issued by {addendum.issued_by}</span>
        )}
      </div>

      <p>{addendum.summary}</p>
      {addendum.bid_due_date ? (
        <p className="muted">Moves the bid due date to {addendum.bid_due_date}.</p>
      ) : null}
      <AttachmentTable attachments={addendum.attachments} />
    </li>
  )
}

/* --------------------------------------------------------- draft an addendum */

function DraftAddendumForm({ data, run, busy }: Omit<StepProps, 'tick'>) {
  const pkg = data.technical_package
  // Seeded from the package: an addendum replaces the attachment list
  // wholesale, so starting from what vendors already hold is the honest
  // default. Issuing refuses any attachment with no definite revision, the
  // same rule freezing follows.
  const [attachments, setAttachments] = useState<Attachment[]>(pkg?.attachments ?? [])
  const [revision, setRevision] = useState('')
  const [summary, setSummary] = useState('')
  const [dueDate, setDueDate] = useState('')
  const [docCode, setDocCode] = useState('')
  const [title, setTitle] = useState('')
  const [attRevision, setAttRevision] = useState('')

  if (!pkg?.frozen_at) {
    return (
      <p className="muted">
        The technical package is not frozen, so there is nothing for an addendum
        to supersede. Edit it directly under Scoping.
      </p>
    )
  }

  return (
    <details className="pcard-details">
      <summary>Draft an addendum</summary>
      <p className="muted">
        An addendum is the one way a frozen package moves. Issuing it supersedes{' '}
        {pkg.revision} and re-freezes at the revision you give here, so every
        bidder can tell which package they hold.
      </p>

      <div className="fxrow">
        <label className="field-label" htmlFor="addendum-revision">
          New revision
        </label>
        <input
          id="addendum-revision"
          className="input"
          value={revision}
          onChange={(e) => setRevision(e.target.value)}
        />
        <label className="field-label" htmlFor="addendum-due">
          New bid due date (optional)
        </label>
        <input
          id="addendum-due"
          className="input"
          type="date"
          value={dueDate}
          onChange={(e) => setDueDate(e.target.value)}
        />
      </div>

      <label className="field-label" htmlFor="addendum-summary">
        What changed
      </label>
      <textarea
        id="addendum-summary"
        className="input"
        rows={3}
        value={summary}
        onChange={(e) => setSummary(e.target.value)}
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
          aria-label="Addendum attachment document code"
          placeholder="Doc code"
          value={docCode}
          onChange={(e) => setDocCode(e.target.value)}
        />
        <input
          className="input"
          aria-label="Addendum attachment title"
          placeholder="Title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <input
          className="input"
          aria-label="Addendum attachment revision"
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

      <button
        type="button"
        className="btn"
        disabled={busy || !revision.trim() || !summary.trim()}
        onClick={() =>
          run(async () => {
            await draftAddendum(data.rfq.id, {
              revision: revision.trim(),
              summary: summary.trim(),
              attachments,
              // Omitted rather than sent empty: an addendum that does not move
              // the date should not claim to.
              ...(dueDate ? { bid_due_date: dueDate } : {}),
            })
            setRevision('')
            setSummary('')
            setDueDate('')
          })
        }
      >
        Save draft addendum
      </button>
    </details>
  )
}
