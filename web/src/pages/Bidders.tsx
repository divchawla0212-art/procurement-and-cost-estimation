import { useState } from 'react'
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
 * Two rules it follows and does not bend:
 *
 * - **`effective_prequal` comes from the server and is never recomputed here.**
 *   Comparing `prequal_expires_on` to the browser's clock would be a second
 *   definition of "expired", and the two would drift the first time one of
 *   them learned about a grace period.
 * - **A destructive control is never pre-disabled.** Delete is offered, and a
 *   refusal shows the server's own sentence naming the RFQs that block it.
 *   A greyed-out button explains nothing — the same choice `RfqWizard` makes
 *   for a closed gate.
 */

const PREQUAL_STATES: PrequalStatus[] = [
  'Approved',
  'Under review',
  'Suspended',
  'Not qualified',
]

/** `Under review` → `under-review`, so a chip can carry a colour without the
 *  class name depending on how the label happens to be capitalised. */
function chipModifier(status: string): string {
  return status.toLowerCase().replace(/\s+/g, '-')
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
  const blocked = bidder.effective_prequal !== 'Approved' || bidder.on_hold
  return (
    <article className="pcard">
      <header className="pcard-head">
        <h3 className="pcard-title">{bidder.name}</h3>
        <span className="pcard-code mono">{bidder.country}</span>
      </header>

      <div className="pcard-facts">
        <span
          className={`pcard-status pcard-status--${chipModifier(
            bidder.effective_prequal,
          )}`}
        >
          {bidder.effective_prequal}
        </span>
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
        {bidder.trade_categories.length
          ? bidder.trade_categories.join(' · ')
          : 'No trade categories recorded — every RFQ will read as a scope mismatch.'}
      </p>

      {bidder.on_hold && (
        <p className="warn">On hold: {bidder.hold_reason ?? 'no reason recorded'}</p>
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
          <dd className="mono">{bidder.prequal_expires_on ?? 'open-ended'}</dd>

          <dt>Trade categories</dt>
          <dd>{bidder.trade_categories.join(', ') || '—'}</dd>

          <dt>Turnover band</dt>
          <dd>{bidder.turnover_band ?? '—'}</dd>

          <dt>Awards before this system</dt>
          <dd className="mono">{bidder.past_awards}</dd>

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
        <button
          type="button"
          className="linkish"
          disabled={busy}
          onClick={onDelete}
          title={
            blocked
              ? undefined
              : 'Refused while any shortlist still names this bidder'
          }
        >
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
          country: country.trim(),
          currency: 'AED',
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
        required
        value={country}
        onChange={(e) => setCountry(e.target.value)}
      />

      <label className="field-label" htmlFor="bidder-categories">
        Trade categories
      </label>
      <input
        id="bidder-categories"
        className="input"
        placeholder="Electrical, LV switchgear"
        value={categories}
        onChange={(e) => setCategories(e.target.value)}
      />
      <p className="muted">
        Comma separated. These are matched against an RFQ's discipline and
        package to work out scope fit.
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

  if (loading) return <LoadingState label="Loading bidders…" />
  if (error) return <ErrorState message={error} />

  const bidders = data ?? []

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
          prequalify, and their approvals will be checked for you every time a
          shortlist is drawn up.
        </EmptyState>
      ) : (
        <div className="project-grid">
          {bidders.map((b) => (
            <BidderCard
              key={b.id}
              bidder={b}
              busy={busy}
              // Partial by contract: only the field that changed is sent, so
              // an edit cannot quietly overwrite something a colleague set.
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
  )
}
