import { useMemo, useState } from 'react'
import type { JSX } from 'react'
import { createBidder, deleteBidder, fetchBidders, updateBidder } from '../api'
import { useAsync } from '../useAsync'
import type { BidderInput, BidderSummary, PrequalStatus } from '../types'
import {
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from '../components/primitives'

/**
 * The bidder registry — who may be invited, held once for the organisation.
 *
 * This screen exists because a vendor used to come into being at the moment
 * somebody typed their name into an RFQ. Prequalification is a fact about a
 * company with a validity period, and asserting it per RFQ, by hand, from
 * memory, is how a suspended vendor ends up on a shortlist.
 *
 * Three rules it follows and does not bend:
 *
 * - **`effective_prequal` comes from the server and is never recomputed here.**
 *   Comparing `prequal_expires_on` to the browser's clock would be a second
 *   definition of "expired", and the two would drift the first time one of
 *   them learned about a grace period.
 * - **A destructive control is never pre-disabled.** Delete is offered, and a
 *   refusal shows the server's own sentence naming the RFQs that block it.
 *   A greyed-out button explains nothing — the same choice `RfqWizard` makes
 *   for a closed gate.
 * - **Nothing renders 1 300 cards.** An imported ADNOC AVL is that big, so the
 *   list is filtered first and capped second, and the cap says so rather than
 *   quietly truncating. A screen that silently shows the first fifty of
 *   thirteen hundred is a screen that lies about what is in the registry.
 */

const PREQUAL_STATES: PrequalStatus[] = [
  'Approved',
  'Under review',
  'Suspended',
  'Not qualified',
]

/** Enough to scroll through and judge, few enough to render instantly. */
const RENDER_CAP = 60

/** `Under review` → `under-review`, so a chip can carry a colour without the
 *  class name depending on how the label happens to be capitalised. */
function chipModifier(status: string): string {
  return status.toLowerCase().replace(/\s+/g, '-')
}

function matches(bidder: BidderSummary, query: string): boolean {
  if (!query) return true
  const needle = query.toLowerCase()
  // Manufacturers are searched too: "who can supply Rosemount" is the question
  // a buyer actually arrives with, and the answer is in that list, not in the
  // vendor's own name.
  return (
    bidder.name.toLowerCase().includes(needle) ||
    bidder.trade_categories.some((c) => c.toLowerCase().includes(needle)) ||
    bidder.represented_manufacturers.some((m) => m.toLowerCase().includes(needle))
  )
}

function ApprovalBadges({ approvedBy }: { approvedBy: string[] }): JSX.Element {
  if (approvedBy.length === 0) {
    return <span className="muted">No approval recorded</span>
  }
  return (
    <>
      {approvedBy.map((org) => (
        <span key={org} className="approval-badge">
          {org}
        </span>
      ))}
    </>
  )
}

function BidderCard({
  bidder,
  busy,
  onSuspend,
  onReinstate,
  onDelete,
}: {
  bidder: BidderSummary
  busy: boolean
  onSuspend: () => void
  onReinstate: () => void
  onDelete: () => void
}): JSX.Element {
  return (
    <article className="pcard">
      <header className="pcard-head">
        <h3 className="pcard-title">{bidder.name}</h3>
        <span className="pcard-code mono">{bidder.country ?? '—'}</span>
      </header>

      <div className="pcard-facts">
        <span
          className={`pcard-status pcard-status--${chipModifier(
            bidder.effective_prequal,
          )}`}
        >
          {bidder.effective_prequal}
        </span>
        <ApprovalBadges approvedBy={bidder.approved_by} />
        <span className="pcard-count">
          <b>{bidder.invited_count}</b>{' '}
          {bidder.invited_count === 1 ? 'RFQ' : 'RFQs'}
        </span>
        {bidder.performance_rating !== null && (
          <span className="pcard-count">
            <b>{bidder.performance_rating.toFixed(1)}</b> / 5
          </span>
        )}
      </div>

      <p className="muted">
        {bidder.trade_categories.length === 0
          ? 'No trade categories recorded — every RFQ will read as a scope mismatch.'
          : bidder.trade_categories.slice(0, 3).join(' · ') +
            (bidder.trade_categories.length > 3
              ? ` · +${bidder.trade_categories.length - 3} more`
              : '')}
      </p>

      {bidder.on_hold && (
        <p className="warn">On hold: {bidder.hold_reason ?? 'no reason recorded'}</p>
      )}

      {/* Below the hold, so a bidder who is both shows both facts in reading
          order. Rendered verbatim: the sentence is the server's, and testing
          `approved_by` for the client's name here would be a second
          definition of the rule. */}
      {bidder.approval_caution && (
        <p className="warn">{bidder.approval_caution}</p>
      )}

      <details className="pcard-details">
        <summary>Details</summary>
        <dl className="pcard-kv">
          <dt>Prequalification</dt>
          <dd>
            {bidder.prequal_status}
            {bidder.effective_prequal !== bidder.prequal_status && (
              <> — {bidder.effective_prequal.toLowerCase()} as of today</>
            )}
          </dd>

          <dt>Valid until</dt>
          <dd className="mono">{bidder.prequal_expires_on ?? 'not recorded'}</dd>

          <dt>Approved by</dt>
          <dd>{bidder.approved_by.join(', ') || 'not recorded'}</dd>

          <dt>Trade categories ({bidder.trade_categories.length})</dt>
          <dd>{bidder.trade_categories.join(', ') || '—'}</dd>

          <dt>Represents ({bidder.represented_manufacturers.length})</dt>
          <dd>{bidder.represented_manufacturers.join(', ') || '—'}</dd>

          <dt>Turnover band</dt>
          <dd>{bidder.turnover_band ?? 'not recorded'}</dd>

          <dt>RFQs invited here</dt>
          <dd className="mono">{bidder.invited_count}</dd>

          {bidder.notes && (
            <>
              <dt>Notes</dt>
              <dd>{bidder.notes}</dd>
            </>
          )}
        </dl>
      </details>

      <div className="wizactions">
        {bidder.prequal_status === 'Suspended' ? (
          <button
            type="button"
            className="btn btn-sm"
            disabled={busy}
            onClick={onReinstate}
          >
            Reinstate
          </button>
        ) : (
          <button
            type="button"
            className="btn btn-sm btn--quiet"
            disabled={busy}
            onClick={onSuspend}
          >
            Suspend
          </button>
        )}
        <button type="button" className="linkish" disabled={busy} onClick={onDelete}>
          Delete
        </button>
      </div>
    </article>
  )
}

function BidderForm({
  onSubmit,
  onCancel,
}: {
  onSubmit: (body: BidderInput) => Promise<void>
  onCancel: () => void
}): JSX.Element {
  const [name, setName] = useState('')
  const [country, setCountry] = useState('')
  const [categories, setCategories] = useState('')
  const [status, setStatus] = useState<PrequalStatus>('Under review')
  const [expires, setExpires] = useState('')
  const [band, setBand] = useState('')
  const [notes, setNotes] = useState('')

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        void onSubmit({
          name: name.trim(),
          country: country.trim() || null,
          currency: 'AED',
          // A bidder added by hand here is on your own list, not the client's.
          // Claiming an ADNOC approval nobody has checked is the one thing
          // this form must not do quietly.
          approved_by: ['Astra'],
          // Split and trimmed here rather than stored raw: a category of
          // " Piping" never matches an RFQ's discipline, and the mismatch
          // would show up as a scope caution nobody can explain.
          trade_categories: categories
            .split(',')
            .map((c) => c.trim())
            .filter(Boolean),
          prequal_status: status,
          prequal_expires_on: expires || null,
          on_hold: false,
          hold_reason: null,
          turnover_band: band.trim() || null,
          performance_rating: null,
          past_awards: 0,
          represented_manufacturers: [],
          notes: notes.trim() || null,
        })
      }}
    >
      <label className="field-label" htmlFor="bidder-name">
        Name
      </label>
      <input
        id="bidder-name"
        className="input"
        required
        value={name}
        onChange={(e) => setName(e.target.value)}
      />

      <label className="field-label" htmlFor="bidder-country">
        Country
      </label>
      <input
        id="bidder-country"
        className="input"
        value={country}
        onChange={(e) => setCountry(e.target.value)}
      />

      <label className="field-label" htmlFor="bidder-categories">
        Trade categories
      </label>
      <input
        id="bidder-categories"
        className="input"
        placeholder="SWITCHGEARS - LV -415V"
        value={categories}
        onChange={(e) => setCategories(e.target.value)}
      />
      <p className="muted">
        Comma separated, and matched whole against an RFQ's discipline. On a
        registry imported from an ADNOC AVL these are the product group
        descriptions, so copy the wording exactly.
      </p>

      <label className="field-label" htmlFor="bidder-status">
        Prequalification
      </label>
      <select
        id="bidder-status"
        className="input"
        value={status}
        onChange={(e) => setStatus(e.target.value as PrequalStatus)}
      >
        {PREQUAL_STATES.map((s) => (
          <option key={s}>{s}</option>
        ))}
      </select>

      <label className="field-label" htmlFor="bidder-expires">
        Valid until
      </label>
      <input
        id="bidder-expires"
        className="input"
        type="date"
        value={expires}
        onChange={(e) => setExpires(e.target.value)}
      />
      <p className="muted">
        Leave empty for an open-ended approval. A date in the past reads as
        expired everywhere, without anything having to be re-saved.
      </p>

      <label className="field-label" htmlFor="bidder-band">
        Turnover band
      </label>
      <input
        id="bidder-band"
        className="input"
        placeholder="AED 50–100m"
        value={band}
        onChange={(e) => setBand(e.target.value)}
      />

      <label className="field-label" htmlFor="bidder-notes">
        Notes
      </label>
      <textarea
        id="bidder-notes"
        className="input"
        rows={2}
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
      />

      <div className="wizactions">
        <button type="submit" className="btn btn-primary">
          Create bidder
        </button>
        <button type="button" className="btn btn--quiet" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}

export function Bidders(): JSX.Element {
  const [tick, setTick] = useState(0)
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [approver, setApprover] = useState<string>('')
  const { data, error, loading } = useAsync(() => fetchBidders(), [tick])

  const reload = () => setTick((t) => t + 1)

  /** One place owns the busy flag and the server's refusal sentence, so no
   *  individual control has to remember to show it. */
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

  const bidders = useMemo(() => data ?? [], [data])

  /** Built from the data rather than hardcoded to ADNOC and Astra: a second
   *  client's AVL should show up as a filter without an edit here. */
  const approvers = useMemo(
    () => [...new Set(bidders.flatMap((b) => b.approved_by))].sort(),
    [bidders],
  )

  const shown = useMemo(
    () =>
      bidders.filter(
        (b) => (!approver || b.approved_by.includes(approver)) && matches(b, query),
      ),
    [bidders, approver, query],
  )

  if (loading) return <LoadingState label="Loading bidders…" />
  if (error) return <ErrorState message={error} />

  return (
    <>
      <PageHeader
        eyebrow="02 · RFQ process"
        title="Bidders"
        sub="Who may be invited to bid, and whether their prequalification still stands."
        actions={
          !creating && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setCreating(true)}
            >
              New bidder
            </button>
          )
        }
      />

      {actionError && <div className="banner banner--error">{actionError}</div>}

      {creating && (
        <Card title="New bidder">
          <BidderForm
            onCancel={() => setCreating(false)}
            onSubmit={async (body) => {
              await run(() => createBidder(body))
              setCreating(false)
            }}
          />
        </Card>
      )}

      {bidders.length === 0 ? (
        <EmptyState title="No bidders yet">
          The registry is who your RFQs can be issued to. Add the companies you
          prequalify, or import a client's approved vendor list, and their
          approvals will be checked for you every time a shortlist is drawn up.
        </EmptyState>
      ) : (
        <>
          <div className="fxrow">
            <input
              className="input"
              aria-label="Search bidders"
              placeholder="Search by name, trade category or manufacturer"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <select
              className="input"
              aria-label="Approved by"
              value={approver}
              onChange={(e) => setApprover(e.target.value)}
            >
              <option value="">Any approval</option>
              {approvers.map((org) => (
                <option key={org} value={org}>
                  Approved by {org}
                </option>
              ))}
            </select>
          </div>

          <p className="muted">
            {shown.length === bidders.length
              ? `${bidders.length} bidders.`
              : `${shown.length} of ${bidders.length} bidders match.`}
            {shown.length > RENDER_CAP && (
              <>
                {' '}
                Showing the first {RENDER_CAP} — narrow the search to see the
                rest.
              </>
            )}
          </p>

          {shown.length === 0 ? (
            <EmptyState title="Nothing matches that search">
              Try a product group — the trade categories on an imported ADNOC
              list are its product group descriptions, like "VALVES - BALL".
            </EmptyState>
          ) : (
            <div className="project-grid">
              {shown.slice(0, RENDER_CAP).map((b) => (
                <BidderCard
                  key={b.id}
                  bidder={b}
                  busy={busy}
                  // Partial by contract: only the field that changed is sent,
                  // so an edit cannot quietly overwrite something else.
                  onSuspend={() =>
                    run(() => updateBidder(b.id, { prequal_status: 'Suspended' }))
                  }
                  onReinstate={() =>
                    run(() => updateBidder(b.id, { prequal_status: 'Approved' }))
                  }
                  onDelete={() => run(() => deleteBidder(b.id))}
                />
              ))}
            </div>
          )}
        </>
      )}
    </>
  )
}
