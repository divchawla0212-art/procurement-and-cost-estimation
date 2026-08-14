import { useState } from 'react'
import type { JSX } from 'react'
import {
  addItemVendor,
  createRfq,
  extractRfqDoc,
  fetchAvailableBidders,
  fetchWorkflowProject,
  inviteRegisteredBidder,
  removeItemVendor,
  suggestItemVendors,
  updateWorkflowItem,
} from '../api'
import { useAsync } from '../useAsync'
import type { CoveringShortlist } from './covering-shortlists'
import { summarise, useCoveringShortlists } from './covering-shortlists'
import { ItemForm, RaiseRfqForm } from './forms'
import type {
  ItemVendorEntry,
  ItemVendorInput,
  Rfq,
  SuggestedVendor,
  VendorListSource,
  WorkflowItem,
  WorkflowItemInput,
} from '../types'
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
  // Bidder ids, never row indices — the table re-sorts under a search and
  // re-fetches under a chip, and an index would move a tick onto a different
  // company. Third instance of that rule on this screen.
  const [selected, setSelected] = useState<Set<string>>(new Set())
  // What the last batch did. Held rather than derived, because it describes an
  // action that has finished — the rows it refers to have already re-rendered.
  const [summary, setSummary] = useState<string | null>(null)
  // Which approvals a vendor must carry — AND, never OR, the same rule
  // `bidders.available` states. Null until the first response names them, so
  // the browser never spells an approver for itself; once known, all of them
  // are required, which is the list the server would have used anyway. The
  // card therefore opens showing exactly what it showed before the chips
  // existed, and only a deliberate untick changes it.
  const [required, setRequired] = useState<string[] | null>(null)
  const scoped = useAsync(
    () => fetchAvailableBidders(discipline, required ?? undefined),
    // Joined, not the array: it is rebuilt every render, and passing it here
    // would re-run the fetch forever.
    [discipline, (required ?? []).join(',')],
  )
  const whole = useAsync(
    () => fetchAvailableBidders(undefined, required ?? undefined),
    [(required ?? []).join(',')],
  )

  const selectable = scoped.data?.selectable_approvers ?? []
  const approvers = required ?? selectable

  function toggleApprover(org: string) {
    const next = approvers.includes(org)
      ? approvers.filter((o) => o !== org)
      : [...approvers, org]
    // Guarded here as well as by `disabled` on the control, so neither a
    // keyboard nor a test can reach the state the query has no expression for:
    // a HAVING count of zero matches nobody, and the server refuses it.
    if (next.length === 0) return
    setRequired(next)
  }

  // Read from the *target* RFQ's shortlist, not from all of them: a vendor
  // invited to one RFQ covering this item is still invitable to another.
  const invited = new Set(
    (shortlists?.find((s) => s.rfq.id === target)?.shortlist ?? [])
      .map((e) => e.vendor_id)
      .filter((id): id is string => id !== null),
  )

  /**
   * Invite everything ticked, one vendor at a time.
   *
   * There is no bulk endpoint and this deliberately does not add one: the
   * server's per-vendor guards — the override reason a blocked bidder demands,
   * the refusal of a duplicate invitation — are what make an invitation an
   * attributed act, and a bulk route would have to reimplement or bypass them.
   *
   * Sequential rather than `Promise.all`: every one of these is a
   * read-modify-write on the same document behind `locked_update`, so firing
   * them together only makes them queue on that lock with their errors
   * interleaved.
   *
   * Not all-or-nothing. One blocked vendor among fifty would otherwise block
   * the batch, and there is nothing to roll the successful writes back with —
   * they have already landed.
   */
  async function inviteSelected() {
    const failed = new Map<string, string>()
    let invited = 0
    for (const id of selected) {
      try {
        await inviteRegisteredBidder(target, { vendor_id: id })
        invited += 1
      } catch (err) {
        failed.set(id, (err as Error).message)
      }
    }
    setRowError((prev) => ({ ...prev, ...Object.fromEntries(failed) }))
    // Successes leave the selection; refusals stay ticked, so the reader fixes
    // the reason and retries exactly what failed rather than re-selecting.
    setSelected(new Set(failed.keys()))
    setSummary(
      `${invited} invited` + (failed.size ? ` · ${failed.size} refused` : ''),
    )
    // One reload for the whole batch, so the Invited marks and the RFQ summary
    // below move together instead of the table re-rendering under each call.
    onInvited()
  }

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

  // The rows actually drawn. `Select these N` adds only these, because the
  // table caps at BIDDER_CAP of up to 1 346 and an invitation has no undo —
  // what you tick has to be what you can see.
  const rendered = shown.slice(0, BIDDER_CAP)
  // Ticked but not currently drawn. Reported rather than pruned: a selection
  // the reader made is theirs to keep, and a count they cannot see is exactly
  // the thing worth saying out loud.
  const hidden = [...selected].filter(
    (id) => !rendered.some((b) => b.id === id),
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
            {/* The approvals required, not a choice between them. Both ticked
                is the list this card has always shown; unticking one widens it
                to the client's whole register or narrows it to ours. */}
            {selectable.map((org) => (
              <button
                key={org}
                type="button"
                className={`chip${approvers.includes(org) ? ' on' : ''}`}
                // The last one standing cannot be turned off — see
                // `toggleApprover`.
                disabled={approvers.length === 1 && approvers.includes(org)}
                title={
                  approvers.length === 1 && approvers.includes(org)
                    ? 'At least one approval is required.'
                    : undefined
                }
                onClick={() => toggleApprover(org)}
              >
                {org}
              </button>
            ))}
            {/* The disabled "From the internet" chip that used to sit here is
                gone rather than enabled. Nothing browses, so the chip promised
                a search no code performs; what replaced it is the Suggested
                vendors card, which says what it actually does. */}
          </div>

          {/* Which RFQ an invitation lands on. Only asked when the answer is
              not obvious — one covering RFQ needs no question, and none means
              there is nothing to invite anybody to. */}
          {covering.length > 1 && (
            // Not a `<label className="field">` wrapping the select: `.field`
            // is the input class, so the label took a border and padding of
            // its own and the select kept its — the nested-box defect the
            // search row above had. The label is plain text bound by `htmlFor`
            // and the class lives on the control it belongs to.
            <div className="fxrow">
              <label htmlFor="shortlist-target">Shortlist into</label>
              <select
                id="shortlist-target"
                className="input"
                value={target}
                onChange={(e) => onTarget(e.target.value)}
              >
                {covering.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.reference}
                  </option>
                ))}
              </select>
            </div>
          )}
          {covering.length === 0 && (
            <p className="muted">
              No RFQ covers this item, so there is nowhere to shortlist these
              vendors — raise an RFQ first.
            </p>
          )}

          <div className="fxrow">
            <button
              type="button"
              className="btn btn-sm"
              onClick={() =>
                setSelected(
                  (prev) => new Set([...prev, ...rendered.map((b) => b.id)]),
                )
              }
            >
              Select these {rendered.length}
            </button>
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={() => setSelected(new Set())}
            >
              Clear
            </button>
            {selected.size > 0 && (
              <span className="muted">
                {selected.size} selected
                {hidden.length > 0 && ` · ${hidden.length} not shown`}
              </span>
            )}
            {/* Absent when there is no RFQ to invite into — the same rule the
                per-row button follows. */}
            {target && selected.size > 0 && (
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => void inviteSelected()}
              >
                Shortlist selected ({selected.size})
              </button>
            )}
            {summary && (
              <span className="muted" role="status">
                {summary}
              </span>
            )}
          </div>

          <table className="table">
            <thead>
              <tr>
                <th scope="col">
                  <span className="sr-only">Select</span>
                </th>
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
              {rendered.map((b) => (
                <tr key={b.id}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Select ${b.name}`}
                      checked={selected.has(b.id)}
                      onChange={(e) =>
                        setSelected((prev) => {
                          const next = new Set(prev)
                          if (e.target.checked) next.add(b.id)
                          else next.delete(b.id)
                          return next
                        })
                      }
                    />
                  </td>
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
                        // The vendor's name belongs in the accessible name,
                        // not the visible label: in the label it wrapped to
                        // three lines and made every row 89px tall, while a
                        // screen reader needs it either way to tell one row's
                        // button from another's.
                        aria-label={`Shortlist ${b.name}`}
                        onClick={() => void invite(b.id)}
                      >
                        Shortlist
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

/** What each card is called, and what its empty state should say.
 *
 *  Four cards rather than one table with a source column, for the reason the
 *  first two were split: the lists answer different questions — who the client
 *  will accept, who we have qualified, who a buyer knows, who a model named —
 *  and a reader looking at one should not have to filter the others out by eye.
 *  Which of those a row belongs to is the thing that decides what may be done
 *  to it, so it is the heading rather than a column. */
const SOURCE_LABEL: Record<VendorListSource, string> = {
  Client: 'Client list',
  Astra: 'Astra list',
  Manual: 'Added by hand',
  Suggested: 'Suggested vendors',
}

/**
 * One of an item's vendor lists, as a card.
 *
 * Approvals come from the registry through `vendor_id`, never from the entry,
 * which is why an unlinked row shows a mark rather than empty pills: no
 * registry row means no finding either way, not "approved by nobody". Every
 * curated row is unlinked by construction — the server never looks a typed name
 * up, because a match would attach a real company's approvals to whatever
 * somebody typed.
 *
 * `onRemove` is passed only for the curated sources. The uploaded cards render
 * no remove control at all rather than one that always fails: an uploaded row
 * is part of a document, so correcting it means re-uploading the corrected
 * export, and a control that produced a 422 every time would read as broken
 * rather than as deliberate. The server refuses it either way — this matches
 * that refusal, it does not stand in for it.
 */
function VendorListCard({
  source,
  entries,
  discipline,
  onRemove,
  children,
}: {
  source: VendorListSource
  entries: ItemVendorEntry[]
  discipline: string
  /** Absent for `Client` and `Astra`. */
  onRemove?: (entryId: string) => Promise<void>
  /** How this list is added to, rendered inside the card so the control and
   *  the rows it appends to share one heading — and, because a card is a
   *  landmark, one accessible name for a reader navigating by region. Absent
   *  for the uploads, which are added to by re-uploading the export. */
  children?: JSX.Element
}): JSX.Element {
  const label = SOURCE_LABEL[source]
  const linked = entries.filter((e) => e.vendor_id !== null).length
  // Keyed by entry id, never by row index: the list re-renders after every
  // write, and a stale index would attach a refusal to a different company.
  // The same rule the available-vendor card keeps.
  const [rowError, setRowError] = useState<Record<string, string>>({})

  async function remove(entryId: string) {
    setRowError((prev) => {
      const { [entryId]: _gone, ...rest } = prev
      return rest
    })
    try {
      await onRemove!(entryId)
    } catch (err) {
      setRowError((prev) => ({ ...prev, [entryId]: (err as Error).message }))
    }
  }

  return (
    <Card title={label}>
      {/* Above the list, because on the two curated cards it is the thing the
          reader came here to do — and on an empty one it is the only thing
          there is to do. */}
      {children}
      {entries.length === 0 ? (
        <EmptyState title={`Nothing on the ${label.toLowerCase()} yet`}>
          {source === 'Client' || source === 'Astra' ? (
            <>
              Edit the item and add the{' '}
              {source === 'Client' ? 'client' : 'Astra'} Approved Vendor List.
              It is narrowed to this item's discipline on the way in, so only
              the vendors registered for {discipline || 'it'} are kept.
            </>
          ) : source === 'Manual' ? (
            <>
              Add a company below. Use this for a supplier who is on neither
              uploaded list — nothing here is checked against the registry, so
              the name is recorded exactly as you type it.
            </>
          ) : (
            <>
              Ask for suggestions below, then add the ones you want to keep.
              Each stays labelled as the model's, so a later reader can see
              where the name came from.
            </>
          )}
        </EmptyState>
      ) : (
        <>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Vendor</th>
                <th scope="col">Registry</th>
                <th scope="col">Product groups</th>
                <th scope="col">How it arrived</th>
                {onRemove && (
                  <th scope="col">
                    <span className="sr-only">Remove</span>
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id}>
                  {/* The name as the export wrote it, or as the buyer typed
                      it, so a row can be read back against its source. */}
                  <td>
                    {e.vendor_name}
                    {/* The refusal lives in the row that caused it. */}
                    {rowError[e.id] && (
                      <div className="banner banner--error" role="alert">
                        {rowError[e.id]}
                      </div>
                    )}
                  </td>
                  <td>
                    {e.vendor_id === null ? (
                      <span className="warn">Not in the registry</span>
                    ) : (
                      <span className="muted">Linked</span>
                    )}
                  </td>
                  <td className="muted">{e.trade_categories.join(' · ') || '—'}</td>
                  {/* `source_document` names the export for an upload and the
                      act for a curated row, so every row says how it got here
                      whichever door it came through. */}
                  <td className="muted">{e.source_document}</td>
                  {onRemove && (
                    <td>
                      <button
                        type="button"
                        className="btn btn-sm btn-ghost"
                        // The name goes in the accessible name, not the
                        // visible label — the rule the shortlist button
                        // follows, and here it is also what makes removal
                        // addressable in a list where two rows can share a
                        // trading name.
                        aria-label={`Remove ${e.vendor_name}`}
                        onClick={() => void remove(e.id)}
                      >
                        Remove
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted">
            {entries.length} on the {label.toLowerCase()} for{' '}
            {discipline || 'this item'} · {linked} in the registry
            {linked < entries.length && ` · ${entries.length - linked} not found`}
          </p>
        </>
      )}
    </Card>
  )
}

/**
 * Typing in a company nobody's list holds.
 *
 * Its own card rather than a row on the Manual list, because it is the only
 * control on this screen that creates a vendor record from nothing: the name is
 * stored exactly as typed and **never looked up in the registry**, since a
 * match would silently attach a real company's approvals to it.
 *
 * The blank guard is here as well as on the server. A round trip to be told the
 * obvious is a worse answer than not making it — and the server's refusal still
 * stands for anything that reaches it another way.
 */
function AddVendorByHand({
  onAdd,
}: {
  onAdd: (body: ItemVendorInput) => Promise<void>
}): JSX.Element {
  const [name, setName] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    const trimmed = name.trim()
    if (!trimmed) {
      setError('A vendor needs a name.')
      return
    }
    setError(null)
    try {
      await onAdd({
        vendor_name: trimmed,
        source: 'Manual',
        // Sent only when written. An empty note is not a note, and the server
        // would otherwise store a provenance sentence ending in a colon.
        ...(note.trim() ? { note: note.trim() } : {}),
      })
      setName('')
      setNote('')
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <>
      {/* Plain labels beside controls carrying `.input`, never a
          `<label className="field">` wrapping one: `.field` is the input
          class, and on a label it renders as a box inside a box. */}
      <div className="fxrow">
        <label htmlFor="hand-vendor-name">Vendor name</label>
        <input
          id="hand-vendor-name"
          className="input"
          value={name}
          placeholder="As you would write it on an enquiry"
          onChange={(e) => setName(e.target.value)}
        />
        <label htmlFor="hand-vendor-note">Note</label>
        <input
          id="hand-vendor-note"
          className="input"
          value={note}
          placeholder="Why this company (optional)"
          onChange={(e) => setNote(e.target.value)}
        />
        <button type="button" className="btn btn-sm" onClick={() => void submit()}>
          Add vendor
        </button>
      </div>
      {error && (
        <div className="banner banner--error" role="alert">
          {error}
        </div>
      )}
    </>
  )
}

/**
 * Companies a model believes supply this item.
 *
 * **Not a web search, and the caption says so.** `shared/llm/` wraps providers
 * that cannot browse, so what comes back is what the model recalls from
 * training: undated, unsourced, and capable of being confidently wrong by
 * inventing a plausible company name. Calling this a search on screen would put
 * a synthesised fact where a recorded one is expected — the rule this
 * repository keeps for the AVL import, the mock rounds and the RFQ extractor.
 *
 * **Nothing here is stored until a person adds it, one row at a time.** There
 * is deliberately no bulk accept: taking twenty unverified companies in one
 * click is precisely the act that needs friction. An accepted row keeps the
 * `Suggested` label rather than being promoted to `Manual`, because that the
 * name originated with a model is what a later reader would most want to know.
 *
 * A failed ask shows the failure. An outage and "no such companies exist" must
 * not look the same, which is the distinction the covering-RFQ summary already
 * keeps between `—` and `Nobody invited yet`.
 */
function SuggestVendors({
  discipline,
  onAsk,
  onAdd,
}: {
  discipline: string
  onAsk: () => Promise<SuggestedVendor[]>
  onAdd: (body: ItemVendorInput) => Promise<void>
}): JSX.Element {
  const [found, setFound] = useState<SuggestedVendor[] | null>(null)
  const [asking, setAsking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Keyed by name, which is what identifies an unstored candidate — these have
  // no id, precisely because nothing has recorded them.
  const [rowError, setRowError] = useState<Record<string, string>>({})
  const [added, setAdded] = useState<Set<string>>(new Set())

  async function ask() {
    setAsking(true)
    setError(null)
    try {
      setFound(await onAsk())
    } catch (err) {
      // The list is cleared, so a stale answer cannot sit under a failure
      // message and read as though it were this ask's result.
      setFound(null)
      setError((err as Error).message)
    } finally {
      setAsking(false)
    }
  }

  async function add(vendor: SuggestedVendor) {
    setRowError((prev) => {
      const { [vendor.name]: _gone, ...rest } = prev
      return rest
    })
    try {
      await onAdd({
        vendor_name: vendor.name,
        // Stays `Suggested` after a person accepts it — see the card's note.
        source: 'Suggested',
        // The model's own reason, carried onto the stored row's provenance so
        // a later reader sees why this name was put forward at all.
        ...(vendor.basis ? { note: vendor.basis } : {}),
      })
      setAdded((prev) => new Set(prev).add(vendor.name))
    } catch (err) {
      setRowError((prev) => ({ ...prev, [vendor.name]: (err as Error).message }))
    }
  }

  return (
    <>
      {/* Leads with what it is, before anything it produced. */}
      <p className="muted">
        Suggested by the model from its training data. Not a web search — verify
        each company before inviting them.
      </p>

      <div className="fxrow">
        <button
          type="button"
          className="btn btn-sm"
          disabled={asking}
          onClick={() => void ask()}
        >
          {asking ? 'Asking…' : 'Suggest vendors'}
        </button>
        <span className="muted">
          {discipline.trim()
            ? `For ${discipline}, excluding the companies already on this item's lists.`
            : "This item has no discipline, so the ask is only as good as its description."}
        </span>
      </div>

      {error && (
        <div className="banner banner--error" role="alert">
          {error}
        </div>
      )}

      {found !== null && found.length === 0 && !error && (
        <p className="muted">
          The model named nobody it was confident about. That is an answer, not
          a failure — it was asked to leave out any company it could not vouch
          for.
        </p>
      )}

      {found !== null && found.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Company</th>
              <th scope="col">Country</th>
              <th scope="col">Supplies</th>
              <th scope="col">Why the model says so</th>
              <th scope="col">
                <span className="sr-only">Add</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {found.map((v) => (
              <tr key={v.name}>
                <td>
                  {v.name}
                  {rowError[v.name] && (
                    <div className="banner banner--error" role="alert">
                      {rowError[v.name]}
                    </div>
                  )}
                </td>
                <td className="muted">{v.country ?? '—'}</td>
                <td className="muted">{v.supplies ?? '—'}</td>
                {/* Shown, not hidden behind a tooltip: it is the only thing
                    the reader has to judge an unverified name by. */}
                <td className="muted">{v.basis ?? '—'}</td>
                <td>
                  {added.has(v.name) ? (
                    <span className="muted">Added</span>
                  ) : (
                    <button
                      type="button"
                      className="btn btn-sm"
                      // One per row and no control that takes them all: see the
                      // card's note on friction.
                      aria-label={`Add ${v.name}`}
                      onClick={() => void add(v)}
                    >
                      Add
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
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
  onOpenRfq,
}: {
  projectId: string
  itemId: string
  /** Up one level, to the project that owns this item. */
  onBack: () => void
  /** Up two levels, to the project roster. */
  onHome: () => void
  /** Sideways, into one of the RFQs covering this item. A prop rather than a
   *  `useNavigate` here, so this screen stays renderable without a router —
   *  `ItemRoute` already holds one and passes it down. Optional because the
   *  control is the only thing that depends on it, and a caller with nowhere
   *  to send the reader should render no control rather than a dead one. */
  onOpenRfq?: (rfqId: string) => void
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
    // Every re-run here is a *refresh* of the same project, not a switch to a
    // different one, which is the case `useAsync`'s option exists for. Without
    // it the screen blanks after every write and unmounts the vendor card
    // mid-interaction — taking the batch's refusals, its summary and the
    // selection retained for a retry with it, which is everything the reader
    // needs in order to act on what just failed.
    { keepPreviousData: true },
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

  // Both writes re-read the whole project rather than patching local state, so
  // the four cards, the available-vendor card and the covering-RFQ summary
  // always agree — the server stays the one authority on what is on this
  // item's lists. Defined above the early returns for the same reason
  // `covering` is: they close over `tick`, which the cards below need.
  async function addVendor(body: ItemVendorInput) {
    await addItemVendor(projectId, itemId, body)
    setTick((t) => t + 1)
  }

  async function removeVendor(entryId: string) {
    await removeItemVendor(projectId, itemId, entryId)
    setTick((t) => t + 1)
  }

  // Only the *first* load blanks the screen. `keepPreviousData` keeps `data`
  // across a refresh but `loading` still goes true, so guarding on it alone
  // would unmount the subtree anyway and undo the option above.
  if (loading && !data) return <LoadingState label="Loading item…" />
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
            // Present here and absent on the project screen's create form:
            // an upload needs an item to attach to.
            projectId={projectId}
            itemId={itemId}
            onVendorListUploaded={() => setTick((t) => t + 1)}
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

      {/* Before the registry-wide card: these are the lists somebody actually
          supplied for this package, so they are the narrower and more
          authoritative answer to "who should bid".

          The two uploads first, then the two curated ones. `onRemove` is passed
          only to the curated pair — see `VendorListCard`. */}
      <VendorListCard
        source="Client"
        entries={data.item_vendor_lists?.[itemId]?.Client ?? []}
        discipline={item.discipline}
      />
      <VendorListCard
        source="Astra"
        entries={data.item_vendor_lists?.[itemId]?.Astra ?? []}
        discipline={item.discipline}
      />
      <VendorListCard
        source="Manual"
        entries={data.item_vendor_lists?.[itemId]?.Manual ?? []}
        discipline={item.discipline}
        onRemove={removeVendor}
      >
        <AddVendorByHand onAdd={addVendor} />
      </VendorListCard>
      <VendorListCard
        source="Suggested"
        entries={data.item_vendor_lists?.[itemId]?.Suggested ?? []}
        discipline={item.discipline}
        onRemove={removeVendor}
      >
        <SuggestVendors
          discipline={item.discipline}
          onAsk={async () =>
            (await suggestItemVendors(projectId, itemId)).vendors
          }
          onAdd={addVendor}
        />
      </VendorListCard>

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
                {onOpenRfq && (
                  <th scope="col">
                    <span className="sr-only">Open</span>
                  </th>
                )}
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
                    {onOpenRfq && (
                      <td>
                        <button
                          type="button"
                          className="btn btn-sm btn-ghost"
                          // The reference, not the glyph. `›` is the visible
                          // label because the column is one character wide;
                          // a screen reader and a test both need to know
                          // which RFQ this row's control opens, which is the
                          // same rule the Shortlist button follows.
                          aria-label={`Open ${r.reference}`}
                          onClick={() => onOpenRfq(r.id)}
                        >
                          ›
                        </button>
                      </td>
                    )}
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
