import { useState } from 'react'
import type { JSX } from 'react'
import {
  createRfq,
  extractRfqDoc,
  fetchAvailableBidders,
  fetchWorkflowProject,
  inviteRegisteredBidder,
  updateWorkflowItem,
} from '../api'
import { useAsync } from '../useAsync'
import type { CoveringShortlist } from './covering-shortlists'
import { summarise, useCoveringShortlists } from './covering-shortlists'
import { ItemForm, RaiseRfqForm } from './forms'
import type { Rfq, WorkflowItem, WorkflowItemInput } from '../types'
import {
  ApprovalPills,
  Breadcrumb,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  Metric,
  PageHeader,
} from '../components/primitives'

/**
 * One item, and the RFQs covering it.
 *
 * Deliberately thin. It exists because "which RFQs cover this cable" has no
 * other answer in the product, and because a delete refused for exactly that
 * reason needs somewhere to send the reader.
 *
 * It reads the project's own response rather than adding a per-item route: the
 * covering RFQs have to be filtered out of the project's RFQs anyway, so a
 * dedicated route would have to return them too — and then the item and its
 * RFQs could arrive from two generations of the document.
 */

/**
 * How many vendors are drawn before the card starts counting instead.
 *
 * The unnarrowed list can run to the size of the registry, so something has to
 * bound the DOM. Search narrows it; the count below the table always reports
 * the real total, so a capped view never reads as a complete one. The same
 * reasoning, and the same wording, as the registry screen that used to carry
 * this list.
 */
const BIDDER_CAP = 25

/**
 * The vendors that may be invited, narrowed to this item's discipline.
 *
 * "Available" is both approvals at once: the client's Approved Vendor List says
 * they may be used on an ADNOC project, ours says procurement has qualified
 * them. Either alone is not enough to put a vendor in front of a buyer, and one
 * alone is the common case — the client's list runs to 1 346 and ours to a
 * hundred or so.
 *
 * `discipline` is passed through to the server, which expands it into the
 * export's own product groups — "Cables" reaches the eleven cable groups the
 * sheet names, because no vendor is registered for a category called "Cables".
 * An item still carrying free text from before the vocabulary existed narrows
 * to nothing, so the card falls back to the whole list and says that it has:
 * an empty table would report the item as uncoverable when the truth is that
 * it is unscoped.
 */
function AvailableVendorList({
  discipline,
  covering,
  shortlists,
  target,
  onTarget,
  onInvited,
}: {
  discipline: string
  /** The RFQs covering this item, passed rather than fetched: the screen has
   *  already filtered them out of the project payload, and a second read could
   *  disagree with the table rendered below this card. */
  covering: Rfq[]
  /** Their shortlists, from the one read `useCoveringShortlists` makes. Shared
   *  with the summary column so this card cannot offer to invite somebody that
   *  column is counting as invited. `null` while it is in flight. */
  shortlists: CoveringShortlist[] | null
  /** Which RFQ an invitation lands on, held by the screen rather than by this
   *  card. A successful invitation re-reads the project, and while that read
   *  is in flight the screen renders its loading state — which unmounts this
   *  card and would reset a target held here to the first RFQ. The next vendor
   *  would then be invited to an RFQ nobody chose, with the control still
   *  looking correct. */
  target: string
  onTarget: (rfqId: string) => void
  /** Re-read the screen after a successful invitation, so the Invited marks
   *  and the summary below move together. */
  onInvited: () => void
}): JSX.Element {
  const [query, setQuery] = useState('')
  // Keyed by bidder id, never by row index: the table re-sorts under a search,
  // and a stale index would attach a refusal to a different company. The same
  // rule the project screen keeps for a refused delete.
  const [rowError, setRowError] = useState<Record<string, string>>({})
  const scoped = useAsync(() => fetchAvailableBidders(discipline), [discipline])
  const whole = useAsync(() => fetchAvailableBidders(), [])

  // Read from the *target* RFQ's shortlist, not from all of them: a vendor
  // invited to one RFQ covering this item is still invitable to another.
  const invited = new Set(
    (shortlists?.find((s) => s.rfq.id === target)?.shortlist ?? [])
      .map((e) => e.vendor_id)
      .filter((id): id is string => id !== null),
  )

  async function invite(bidderId: string) {
    setRowError((prev) => {
      const { [bidderId]: _gone, ...rest } = prev
      return rest
    })
    try {
      // `vendor_id` and nothing else. The name, prequalification and scope fit
      // are the registry's, snapshotted server-side at the moment of the
      // decision; a screen that sent them would be claiming they were its to
      // decide. Nothing here checks eligibility either — the server owns that,
      // and its refusal is what the reader sees.
      await inviteRegisteredBidder(target, { vendor_id: bidderId })
      onInvited()
    } catch (err) {
      setRowError((prev) => ({ ...prev, [bidderId]: (err as Error).message }))
    }
  }

  // The fallback is decided on the scoped answer being *empty*, not on the
  // discipline's spelling: the browser does not hold the vocabulary, and
  // guessing at it here would be a second definition of what a discipline is.
  //
  // The blank case has to be excluded explicitly, though. An empty discipline
  // sends no filter, so the "scoped" answer *is* the whole list — non-empty,
  // and it would otherwise read as a successful narrowing to nothing.
  const narrowed =
    discipline.trim() !== '' && !!scoped.data && scoped.data.total > 0
  const { data, error, loading } = narrowed ? scoped : whole

  // The narrowed title names the discipline, so it waits for the fetch. The
  // unnarrowed one is the card's own name and needs nothing from the server.
  const title = data && narrowed
    ? `Available vendors for ${discipline}`
    : 'Available vendors list'

  // Client-side, over a list already in memory: 1 300 rows filter in well under
  // a frame, and a round trip per keystroke would be slower and no more
  // correct. Name and represented manufacturers both, because a buyer as often
  // knows the principal ("Rosemount") as the local agent.
  const needle = query.trim().toLowerCase()
  const shown = !data
    ? []
    : !needle
      ? data.bidders
      : data.bidders.filter(
          (b) =>
            b.name.toLowerCase().includes(needle) ||
            b.trade_categories.some((c) => c.toLowerCase().includes(needle)) ||
            b.represented_manufacturers.some((m) =>
              m.toLowerCase().includes(needle),
            ),
        )

  return (
    <Card title={title}>
      {loading ? (
        <p className="muted">Loading the vendor list…</p>
      ) : error ? (
        <p className="warn">{error}</p>
      ) : !data ? (
        <p className="warn">No bidder data was returned.</p>
      ) : data.total === 0 ? (
        <EmptyState title="No vendor carries both approvals">
          A vendor is available once it is on the client's Approved Vendor List
          and on ours. Load both lists and the vendors carrying each appear
          here.
        </EmptyState>
      ) : (
        <>
          {!narrowed && (
            <p className="muted">
              {discipline.trim() === ''
                ? 'This item has no discipline, so every available vendor is shown.'
                : `No available vendor is registered for "${discipline}", so every one is shown. Set the item's discipline to one of the listed ones to narrow it.`}
            </p>
          )}

          {/* One field, not a label wearing an input's clothes. `.field` is
              the input class; on the wrapping label it gave the label a
              border and padding and left the real input with the browser's
              raw user-agent chrome, so the control read as two nested boxes.
              `.input` is the inline variant the wizard's filter rows use, and
              this is now a filter row. */}
          <div className="fxrow">
            <input
              className="input"
              type="search"
              aria-label="Search vendors"
              value={query}
              placeholder="Vendor, product group or manufacturer"
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          {/* Which RFQ an invitation lands on. Only asked when the answer is
              not obvious — one covering RFQ needs no question, and none means
              there is nothing to invite anybody to. */}
          {covering.length > 1 && (
            <label className="field">
              <span>Shortlist into</span>
              <select value={target} onChange={(e) => onTarget(e.target.value)}>
                {covering.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.reference}
                  </option>
                ))}
              </select>
            </label>
          )}
          {covering.length === 0 && (
            <p className="muted">
              No RFQ covers this item, so there is nowhere to shortlist these
              vendors — raise an RFQ first.
            </p>
          )}

          <table className="table">
            <thead>
              <tr>
                <th scope="col">Vendor</th>
                <th scope="col">Approvals</th>
                <th scope="col">Product groups</th>
                <th scope="col">Represents</th>
                <th scope="col">Prequalification</th>
                <th scope="col">
                  <span className="sr-only">Shortlist</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {shown.slice(0, BIDDER_CAP).map((b) => (
                <tr key={b.id}>
                  <td>
                    {b.name}
                    {/* The refusal lives in the row that caused it. */}
                    {rowError[b.id] && (
                      <div className="banner banner--error" role="alert">
                        {rowError[b.id]}
                      </div>
                    )}
                  </td>
                  <td>
                    <ApprovalPills approvers={b.approved_by} />
                  </td>
                  <td className="muted">
                    {b.trade_categories.slice(0, 2).join(' · ') || '—'}
                    {b.trade_categories.length > 2 &&
                      ` · +${b.trade_categories.length - 2} more`}
                  </td>
                  <td className="muted">
                    {b.represented_manufacturers.slice(0, 2).join(' · ') || '—'}
                  </td>
                  {/* The derived status, not `prequal_status`: a lapsed
                      approval is still stored as "Approved". */}
                  <td>{b.effective_prequal}</td>
                  <td>
                    {invited.has(b.id) ? (
                      <span className="muted">Invited</span>
                    ) : target ? (
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={() => void invite(b.id)}
                      >
                        Shortlist {b.name}
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <p className="muted">
            {needle
              ? `${shown.length} of ${data.total} vendors match "${query.trim()}".`
              : narrowed
                ? `${data.total} available ${data.total === 1 ? 'vendor is' : 'vendors are'} registered for ${discipline}.`
                : `${data.total} vendors approved by ${data.approvers.join(' and ')}.`}
            {shown.length > BIDDER_CAP && (
              <>
                {' '}
                Showing the first {BIDDER_CAP} — search to narrow the rest.
              </>
            )}
          </p>
        </>
      )}
    </Card>
  )
}

function toInput(item: WorkflowItem): WorkflowItemInput {
  const { id: _id, project_id: _projectId, ...rest } = item
  return rest
}

function changed(
  before: WorkflowItemInput,
  after: WorkflowItemInput,
): Partial<WorkflowItemInput> {
  const out: Partial<WorkflowItemInput> = {}
  for (const key of Object.keys(after) as (keyof WorkflowItemInput)[]) {
    if (after[key] !== before[key]) {
      out[key] = after[key] as never
    }
  }
  return out
}

export function ItemDetail({
  projectId,
  itemId,
  onBack,
  onHome,
}: {
  projectId: string
  itemId: string
  /** Up one level, to the project that owns this item. */
  onBack: () => void
  /** Up two levels, to the project roster. */
  onHome: () => void
}): JSX.Element {
  const [tick, setTick] = useState(0)
  const [editing, setEditing] = useState(false)
  const [raising, setRaising] = useState(false)
  const [warning, setWarning] = useState<string | null>(null)
  // Held here rather than in the vendor card, which unmounts while a reload is
  // in flight. Empty until the covering RFQs are known; the fallback below
  // resolves it to the first one.
  const [target, setTarget] = useState('')
  const { data, error, loading } = useAsync(
    () => fetchWorkflowProject(projectId),
    [projectId, tick],
  )

  // Both of these sit above the early returns because the second is a hook,
  // and a hook cannot be called conditionally. While `data` is null `covering`
  // is empty, which fetches nothing — the read starts when the project lands
  // and the joined ids in its dependency key change.
  //
  // An RFQ may cover several items, so this is a filter over `item_ids`, not a
  // find — and it matches by id, never by name.
  const covering = (data?.rfqs ?? []).filter((r) => r.item_ids.includes(itemId))
  const shortlists = useCoveringShortlists(covering, tick)

  if (loading) return <LoadingState label="Loading item…" />
  if (error) return <ErrorState message={error} />
  if (!data) return <ErrorState message="No project data was returned." />

  const item = data.items.find((i) => i.id === itemId)
  if (!item) {
    // Not a crash: the id can be stale if the item was deleted in another tab,
    // or if the user came back to a page that had been open a while.
    return (
      <>
        <Breadcrumb
          trail={[
            { label: 'Projects & items', onClick: onHome },
            { label: data.project.name, onClick: onBack },
            { label: 'Item not found' },
          ]}
        />
        <PageHeader
          eyebrow="01 · RFQ process"
          title="Item not found"
          actions={
            <button type="button" className="btn" onClick={onBack}>
              ← Back to project
            </button>
          }
        />
        <EmptyState title="That item could not be found">
          It may have been deleted since this page was opened.
        </EmptyState>
      </>
    )
  }

  return (
    <>
      {/* Both ancestors are reachable, so switching to a sibling item is
          "project, then the item" rather than a trip back through the roster. */}
      <Breadcrumb
        trail={[
          { label: 'Projects & items', onClick: onHome },
          { label: data.project.name, onClick: onBack },
          { label: item.item_type },
        ]}
      />
      <PageHeader
        eyebrow="01 · RFQ process"
        title={item.item_type}
        sub={`${item.description} · ${data.project.name}`}
        actions={
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button type="button" className="btn" onClick={onBack}>
              ← Back to project
            </button>
            {!editing && (
              <button
                type="button"
                className="btn"
                onClick={() => setEditing(true)}
              >
                Edit item
              </button>
            )}
          </div>
        }
      />

      {warning && (
        <div className="banner banner--warn" role="alert">
          {warning}
        </div>
      )}

      {editing ? (
        <Card title="Edit item">
          <ItemForm
            initial={toInput(item)}
            submitLabel="Save item"
            onCancel={() => setEditing(false)}
            onSubmit={async (body) => {
              const saved = await updateWorkflowItem(
                projectId,
                itemId,
                changed(toInput(item), body),
              )
              setWarning(saved.live_period_warning)
              setEditing(false)
              setTick((t) => t + 1)
            }}
          />
        </Card>
      ) : (
        <Card title="Item detail">
          <div className="metrics">
            <Metric k="Quantity" v={`${item.qty} ${item.uom}`} />
            <Metric k="Discipline" v={item.discipline} />
            <Metric
              k="Estimated value"
              v={item.estimated_value_aed.toLocaleString('en-AE')}
              sub="AED"
            />
            <Metric
              k="Required on site"
              v={item.required_on_site ?? '—'}
              sub={item.is_long_lead ? 'long lead' : undefined}
            />
          </div>
        </Card>
      )}

      {/* Above the RFQ list on purpose: who could bid is the question you have
          before an RFQ exists, and after one does the RFQ list is where you
          go next. */}
      <AvailableVendorList
        discipline={item.discipline}
        covering={covering}
        shortlists={shortlists.data}
        // Falls back to the first covering RFQ rather than being seeded by an
        // effect: a chosen target that no longer covers this item has to
        // resolve to something real, and computing it on render keeps the two
        // cases in one expression.
        target={
          covering.some((r) => r.id === target) ? target : covering[0]?.id ?? ''
        }
        onTarget={setTarget}
        onInvited={() => setTick((t) => t + 1)}
      />

      <Card
        title="RFQs covering this item"
        actions={
          !raising && (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setRaising(true)}
            >
              Raise RFQ
            </button>
          )
        }
      >
        {/* No selection step, unlike the project screen: there the RFQ is
            raised over whichever items you ticked, and here the item you are
            looking at *is* the selection. An RFQ raised from this door covers
            this item and nothing else — widen it from the project screen. */}
        {raising && (
          <RaiseRfqForm
            projectId={projectId}
            itemIds={[itemId]}
            onExtract={extractRfqDoc}
            onCancel={() => setRaising(false)}
            onSubmit={async (body) => {
              await createRfq(body)
              setRaising(false)
              // Re-read rather than push the new RFQ into local state:
              // `covering` is filtered out of the project payload, so the
              // server stays the one authority on which RFQs cover this item.
              setTick((t) => t + 1)
            }}
          />
        )}

        {covering.length === 0 && !raising ? (
          <EmptyState title="No RFQ covers this item yet">
            Raise one here to cover this item alone, or tick it alongside others
            on the project screen to cover several at once.
          </EmptyState>
        ) : covering.length === 0 ? null : (
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Reference</th>
                <th scope="col">Package</th>
                <th scope="col">Discipline</th>
                <th scope="col">Stage</th>
                <th scope="col">Shortlist</th>
              </tr>
            </thead>
            <tbody>
              {covering.map((r) => {
                const found = shortlists.data?.find((s) => s.rfq.id === r.id)
                return (
                  <tr key={r.id}>
                    <td className="mono">{r.reference}</td>
                    <td>{r.package}</td>
                    <td>{r.discipline}</td>
                    <td>{r.stage}</td>
                    {/* Three states, and the first two must stay apart: a
                        shortlist that could not be read is not a shortlist
                        with nobody on it, and rendering "0 invited" for the
                        former would report a fact nobody established. */}
                    <td>
                      {!found || found.shortlist === null ? (
                        <span className="muted">—</span>
                      ) : found.shortlist.length === 0 ? (
                        <span className="muted">Nobody invited yet</span>
                      ) : (
                        summarise(found.shortlist, found.clientApprover ?? '')
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </Card>
    </>
  )
}
