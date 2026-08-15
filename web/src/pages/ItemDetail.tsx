import { useState } from 'react'
import type { JSX } from 'react'
import {
  addDraftShortlistPick,
  addItemVendor,
  addShortlistEntry,
  createRfq,
  extractRfqDoc,
  fetchAvailableBidders,
  fetchWorkflowProject,
  inviteRegisteredBidder,
  removeDraftShortlistPick,
  removeItemVendor,
  suggestItemVendors,
  updateWorkflowItem,
} from '../api'
import { useAsync } from '../useAsync'
import type { CoveringShortlist } from './covering-shortlists'
import { summarise, useCoveringShortlists } from './covering-shortlists'
import { ItemForm, RaiseRfqForm } from './forms'
import type {
  BidderSummary,
  DraftShortlistEntry,
  DraftShortlistInput,
  DraftShortlistSource,
  ItemVendorEntry,
  ItemVendorInput,
  Rfq,
  SuggestedVendor,
  VendorListSource,
  WorkflowItem,
  WorkflowItemInput,
} from '../types'
import { CURATED_SOURCES } from '../types'
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

/** The two pool sources that come from the registry. Named here so the check
 *  below is a narrowing rather than a cast — a bidder approved by some third
 *  client carries an approver that is not a pool source, and it must not become
 *  one by being written straight through. */
const REGISTRY_SOURCES: DraftShortlistSource[] = ['ADNOC', 'Astra']

/**
 * What has been shortlisted against this item so far.
 *
 * Rendered because the alternative is a button that appears to do nothing —
 * the same class of defect as curated rows falling past the pool's row cap,
 * found the same way, by using the screen.
 *
 * This is a **draft**. Nobody here has been invited to anything: an invitation
 * is an attributed act with its own per-vendor guards, and it happens when an
 * RFQ is raised over this shortlist.
 */
function DraftShortlistCard({
  projectId,
  itemId,
  picks,
  onChanged,
}: {
  projectId: string
  itemId: string
  picks: DraftShortlistEntry[]
  onChanged: () => void
}): JSX.Element {
  const [rowError, setRowError] = useState<Record<string, string>>({})

  async function remove(entryId: string) {
    setRowError(({ [entryId]: _gone, ...rest }) => rest)
    try {
      await removeDraftShortlistPick(projectId, itemId, entryId)
      onChanged()
    } catch (err) {
      setRowError((prev) => ({ ...prev, [entryId]: (err as Error).message }))
    }
  }

  return (
    <Card title="Shortlist draft">
      {picks.length === 0 ? (
        <EmptyState title="Nobody shortlisted yet">
          Tick vendors in the list above and shortlist them. They stay here
          until an RFQ is raised over them, and nobody is contacted meanwhile.
        </EmptyState>
      ) : (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Vendor</th>
                {/* Where the buyer found them, which is not derivable later: a
                    registry vendor's approvals can be corrected and a curated row
                    can be deleted from the item's list entirely. */}
                <th scope="col">Found on</th>
                <th scope="col">Shortlisted by</th>
                <th scope="col">
                  <span className="sr-only">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {picks.map((pick) => (
                <tr key={pick.id}>
                  <td>
                    {pick.vendor_name}
                    {rowError[pick.id] && (
                      <div className="banner banner--error" role="alert">
                        {rowError[pick.id]}
                      </div>
                    )}
                  </td>
                  <td className="muted">{pick.source}</td>
                  <td className="muted">{pick.added_by}</td>
                  <td>
                    {/* The name rides on `aria-label`, not in the visible label:
                        a per-row button carrying a vendor name wrapped to three
                        lines and made every row 89px tall. A screen reader still
                        gets the name. */}
                    <button
                      type="button"
                      className="linkish"
                      aria-label={`Remove ${pick.vendor_name}`}
                      onClick={() => void remove(pick.id)}
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
    </Card>
  )
}

/**
 * One row of the pool, tagged with the universe it came from.
 *
 * The discriminant is not decoration: a registry row is invited by
 * `vendor_id` and a curated row by `vendor_name` with no id, and those are two
 * different calls. Modelling the row as a union is what stops the wrong one
 * being made — a shared shape with an optional id would let a curated row send
 * `vendor_id: undefined` and silently take the registry path.
 */
type PoolRow =
  | { kind: 'registry'; bidder: BidderSummary }
  | { kind: 'curated'; entry: ItemVendorEntry }

/**
 * What identifies a row for selection and for a per-row refusal.
 *
 * A bidder id and a vendor-list entry id are different id spaces, so the kind
 * has to be part of the key: keyed on the bare id, a registry row and a
 * curated row that happened to share one would be a single tick. Never the row
 * index — the table re-sorts under a search and re-fetches under a chip.
 */
function rowKey(row: PoolRow): string {
  return row.kind === 'registry'
    ? `registry:${row.bidder.id}`
    : `curated:${row.entry.id}`
}

function rowName(row: PoolRow): string {
  return row.kind === 'registry' ? row.bidder.name : row.entry.vendor_name
}

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
  projectId,
  itemId,
  discipline,
  curated,
  covering,
  shortlists,
  target,
  onTarget,
  onInvited,
}: {
  /** The item the picks are written against. A shortlist is assembled here,
   *  before any RFQ exists — which is the whole point of the draft. */
  projectId: string
  itemId: string
  discipline: string
  /** This item's curated vendor lists — the `Manual` and `Suggested` entries,
   *  already on the page as part of the project payload. Filtering them is a
   *  `.filter()`, never a request: they are item-scoped rows in
   *  `workflow.json`, not registry approvals, and folding them into
   *  `GET /bidders/available` as a fifth approver would be a lie, because
   *  nobody approved anything. */
  curated: ItemVendorEntry[]
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
  // Keyed by `rowKey`, never by row index: the table re-sorts under a search,
  // and a stale index would attach a refusal to a different company. The same
  // rule the project screen keeps for a refused delete.
  const [rowError, setRowError] = useState<Record<string, string>>({})
  // Row keys, never row indices — the table re-sorts under a search and
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
  // Which of this item's own lists are appended. Empty by default, so the card
  // opens on exactly the ADNOC ∩ Astra list it showed before these two chips
  // existed and only a deliberate tick widens it.
  const [sources, setSources] = useState<VendorListSource[]>([])
  // Remembered rather than read off the latest response, because there may not
  // be one: with both registry chips unticked nothing is fetched, and the
  // chips still have to be there to tick back on.
  const [selectable, setSelectable] = useState<string[]>([])

  // Both registry chips off. The endpoint refuses an empty approver list with
  // a 422 and that refusal is correct — a HAVING count of zero matches nobody
  // — so the browser contributes no registry rows rather than asking for none.
  const registryOff = required !== null && required.length === 0

  /** Keep the approver vocabulary the server named, without re-rendering on
   *  every response that repeats it. */
  function remember<T extends { selectable_approvers: string[] }>(answer: T): T {
    setSelectable((prev) =>
      prev.join(',') === answer.selectable_approvers.join(',')
        ? prev
        : answer.selectable_approvers,
    )
    return answer
  }

  const scoped = useAsync(
    () =>
      registryOff
        ? Promise.resolve(null)
        : fetchAvailableBidders(discipline, required ?? undefined).then(remember),
    // Joined, not the array: it is rebuilt every render, and passing it here
    // would re-run the fetch forever. `registryOff` rides along because an
    // empty `required` and an absent one join to the same empty string.
    [discipline, (required ?? []).join(','), registryOff],
  )
  const whole = useAsync(
    () =>
      registryOff
        ? Promise.resolve(null)
        : fetchAvailableBidders(undefined, required ?? undefined).then(remember),
    [(required ?? []).join(','), registryOff],
  )

  const approvers = required ?? selectable
  // Chips of any kind, not approvals: four unticked is not a query, it is an
  // empty screen that reads as "no vendor qualifies".
  const ticked = approvers.length + sources.length

  function toggleApprover(org: string) {
    const next = approvers.includes(org)
      ? approvers.filter((o) => o !== org)
      : [...approvers, org]
    // Guarded here as well as by `disabled` on the control, so neither a
    // keyboard nor a test can reach the state with nothing ticked at all.
    if (next.length + sources.length === 0) return
    setRequired(next)
  }

  function toggleSource(source: VendorListSource) {
    const next = sources.includes(source)
      ? sources.filter((s) => s !== source)
      : [...sources, source]
    if (next.length + approvers.length === 0) return
    setSources(next)
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
  /**
   * Put one row on the target RFQ's shortlist, through the call its kind
   * demands.
   *
   * Two calls rather than one with an optional id. A registry row sends
   * `vendor_id` and nothing else — the name, prequalification and scope fit are
   * the registry's, snapshotted server-side at the moment of the decision, and
   * a screen that sent them would be claiming they were its to decide. A
   * curated row has no registry row at all, so it goes through the free-text
   * path with the same defaults the wizard's hand-typed row posts. Sending a
   * `vendor_id` for it would attach a real company's approvals to a name
   * somebody typed, which is the defect this repository has recorded twice.
   */
  async function shortlist(row: PoolRow): Promise<void> {
    if (row.kind === 'registry') {
      await inviteRegisteredBidder(target, { vendor_id: row.bidder.id })
      return
    }
    await addShortlistEntry(target, {
      vendor_name: row.entry.vendor_name,
      prequal_status: 'Under review',
      scope_code_fit: true,
      included: true,
    })
  }

  /**
   * Put one row on the **item's** shortlist draft.
   *
   * Not on an RFQ. A buyer assembles a selection before raising anything, so
   * the write is owned by the item and outlives the browser — this API restarts
   * on every code change and clears sessions when it does, which is exactly
   * what carrying the basket in navigation state would not survive.
   *
   * The discriminated union decides what is sent: a registry row goes by
   * `vendor_id`, a curated one by name with `vendor_id: null`. Deriving an id
   * from a name would attach a real company's approvals to whatever somebody
   * typed, which is the defect this repository has recorded twice.
   */
  function pickBody(row: PoolRow): DraftShortlistInput {
    if (row.kind === 'registry') {
      // A registry row satisfies *every* ticked approval, because that half is
      // ANDed — so "which chip surfaced them" is genuinely ambiguous. The
      // client's approval is the more informative of the two to record, and
      // Astra is the fallback rather than a guess: with both required, a row
      // that reached this table carries at least one of them.
      const found = row.bidder.approved_by.find((a): a is DraftShortlistSource =>
        REGISTRY_SOURCES.includes(a as DraftShortlistSource),
      )
      return {
        vendor_id: row.bidder.id,
        vendor_name: row.bidder.name,
        source: found ?? 'Astra',
      }
    }
    return {
      vendor_id: null,
      vendor_name: row.entry.vendor_name,
      source: row.entry.source === 'Suggested' ? 'Suggested' : 'Manual',
    }
  }

  async function shortlistSelected() {
    const failed = new Map<string, string>()
    let picked = 0
    for (const key of selected) {
      const row = pool.find((r) => rowKey(r) === key)
      if (!row) continue
      try {
        await addDraftShortlistPick(projectId, itemId, pickBody(row))
        picked += 1
      } catch (err) {
        failed.set(key, (err as Error).message)
      }
    }
    setRowError((prev) => ({ ...prev, ...Object.fromEntries(failed) }))
    // Successes leave the selection; refusals stay ticked, so the reader fixes
    // what went wrong and retries exactly that rather than re-selecting.
    setSelected(new Set(failed.keys()))
    setSummary(
      `${picked} shortlisted` + (failed.size ? ` · ${failed.size} refused` : ''),
    )
    onInvited()
  }

  // There was a batch *invitation* here until BD-2. It went with the rule it
  // implemented: the batch control now fills the item's shortlist draft, and
  // inviting is what happens when an RFQ is raised over that draft. The
  // per-vendor path below stays, and goes with the covering-RFQ card.

  async function invite(row: PoolRow) {
    const key = rowKey(row)
    setRowError((prev) => {
      const { [key]: _gone, ...rest } = prev
      return rest
    })
    try {
      // Nothing here checks eligibility — the server owns that, and its
      // refusal is what the reader sees.
      await shortlist(row)
      onInvited()
    } catch (err) {
      setRowError((prev) => ({ ...prev, [key]: (err as Error).message }))
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

  // The registry half is only in flight, and only capable of failing, while it
  // is being asked at all.
  const registryPending = !registryOff && loading
  const registryError = registryOff || loading
    ? null
    : (error ?? (!data ? 'No bidder data was returned.' : null))

  // The two halves of the pool, concatenated and never merged. A company the
  // registry holds *and* somebody typed in appears twice, once per source:
  // silently folding them together would attach a real company's approvals to
  // a hand-typed string, which is name matching — the defect this repository
  // has recorded twice.
  const registryRows: PoolRow[] = (registryOff ? [] : (data?.bidders ?? [])).map(
    (bidder) => ({ kind: 'registry', bidder }),
  )
  const curatedRows: PoolRow[] = curated
    .filter((entry) => sources.includes(entry.source))
    .map((entry) => ({ kind: 'curated', entry }))
  // Curated rows first, and that ordering is load-bearing rather than a taste.
  // The table draws BIDDER_CAP rows of a registry list that runs to 79 for one
  // discipline and 1 346 unnarrowed; appended after it, an item's one or two
  // hand-added companies fall past the cap and are never drawn, so ticking the
  // chip reads as doing nothing at all. They are also the few deliberate ones —
  // somebody typed them against this item. Found by ticking the chip in a
  // browser on a real 79-vendor list; jsdom sees no cap it was not given.
  const pool: PoolRow[] = [...curatedRows, ...registryRows]

  // Client-side, over a list already in memory: 1 300 rows filter in well under
  // a frame, and a round trip per keystroke would be slower and no more
  // correct. Name and represented manufacturers both, because a buyer as often
  // knows the principal ("Rosemount") as the local agent. Applied to the whole
  // union, not the registry half — a needle that matches nothing on a curated
  // row has to take that row away too, or the search reads as broken on
  // exactly the rows it did not reach.
  const needle = query.trim().toLowerCase()
  const matches = (row: PoolRow): boolean => {
    if (!needle) return true
    if (row.kind === 'curated') {
      const e = row.entry
      return (
        e.vendor_name.toLowerCase().includes(needle) ||
        e.trade_categories.some((c) => c.toLowerCase().includes(needle))
      )
    }
    const b = row.bidder
    return (
      b.name.toLowerCase().includes(needle) ||
      b.trade_categories.some((c) => c.toLowerCase().includes(needle)) ||
      b.represented_manufacturers.some((m) => m.toLowerCase().includes(needle))
    )
  }
  const shown = pool.filter(matches)

  // The rows actually drawn. `Select these N` adds only these, because the
  // table caps at BIDDER_CAP of up to 1 346 and an invitation has no undo —
  // what you tick has to be what you can see.
  const rendered = shown.slice(0, BIDDER_CAP)
  // Ticked but not currently drawn. Reported rather than pruned: a selection
  // the reader made is theirs to keep, and a count they cannot see is exactly
  // the thing worth saying out loud.
  const drawn = new Set(rendered.map(rowKey))
  const hidden = [...selected].filter((key) => !drawn.has(key))

  // The registry's own total, which is not `registryRows.length` once the
  // server caps what it sends; plus the curated rows, which are all here.
  const registryTotal = registryOff ? 0 : (data?.total ?? 0)
  const poolTotal = registryTotal + curatedRows.length

  return (
    <Card title={title}>
      {/* Only worth saying when the fallback actually produced something —
          beside an empty registry it would explain a narrowing that made no
          difference. */}
      {!registryPending && !registryOff && !registryError && !narrowed && registryTotal > 0 && (
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
          this is now a filter row.

          The chips are always drawn, even when the half below them is
          empty or failed: they are the only way back to a list, and an
          empty state that hid them would be a dead end. */}
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
            to the client's whole register or narrows it to ours. AND
            across these two, and only these two — a curated chip is not
            an approval and is never ANDed with one. */}
        {selectable.map((org) => (
          <button
            key={org}
            type="button"
            className={`chip${approvers.includes(org) ? ' on' : ''}`}
            // The last chip standing, of any kind, cannot be turned off —
            // see `toggleApprover`.
            disabled={ticked === 1 && approvers.includes(org)}
            title={
              ticked === 1 && approvers.includes(org)
                ? 'At least one source is required.'
                : undefined
            }
            onClick={() => toggleApprover(org)}
          >
            {org}
          </button>
        ))}
        {/* This item's own lists, appended rather than intersected. A
            hand-added company has no registry row to approve it, so ANDing
            these with the two above would empty the table every time.

            `chip--asis` because `.chip` capitalises every word, which is
            right for the single-word verdict chips it was written for and
            renders "Added by hand" as "Added By Hand". */}
        {CURATED_SOURCES.map((source) => (
          <button
            key={source}
            type="button"
            className={`chip chip--asis${sources.includes(source) ? ' on' : ''}`}
            disabled={ticked === 1 && sources.includes(source)}
            title={
              ticked === 1 && sources.includes(source)
                ? 'At least one source is required.'
                : undefined
            }
            onClick={() => toggleSource(source)}
          >
            {SOURCE_LABEL[source]}
          </button>
        ))}
        {/* The disabled "From the internet" chip that used to sit here is
            gone rather than enabled. Nothing browses, so the chip promised
            a search no code performs; what replaced it is the Suggested
            vendors card, which says what it actually does. */}
      </div>

      {registryPending ? (
        <p className="muted">Loading the vendor list…</p>
      ) : registryError ? (
        <p className="warn">{registryError}</p>
      ) : pool.length === 0 ? (
        !registryOff && sources.length === 0 ? (
          <EmptyState title="No vendor carries both approvals">
            A vendor is available once it is on the client's Approved Vendor
            List and on ours. Load both lists and the vendors carrying each
            appear here.
          </EmptyState>
        ) : (
          <EmptyState title="Nothing on the sources you have ticked">
            Tick another chip above. The two approvals narrow the registry;
            the other two append this item's own lists.
          </EmptyState>
        )
      ) : (
        <>
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
              Tick the vendors you want and shortlist them against this item. No
              RFQ is needed yet — one is raised over the shortlist afterwards.
            </p>
          )}

          <div className="fxrow">
            <button
              type="button"
              className="btn btn-sm"
              onClick={() =>
                setSelected(
                  (prev) => new Set([...prev, ...rendered.map(rowKey)]),
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
            {/* Offered whether or not an RFQ covers the item: the draft is
                owned by the item, and assembling it before raising anything is
                the point. The per-row control beside it still invites into a
                covering RFQ, and goes when that card does. */}
            {selected.size > 0 && (
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => void shortlistSelected()}
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

          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">
                    <span className="sr-only">Select</span>
                  </th>
                  {/* The source rides under the name in this column rather than
                      in one of its own. It was a column first, and a column cost
                      61px of an 902px table that the three wrapping columns
                      beside it were already short of: the worst rows went from
                      146px to 194px tall and the whole table 16% longer.
                      Measured in a browser, both ways, on the real 79-vendor
                      Cables list — jsdom applies no stylesheet and does no
                      layout, so it sees none of this. Under the name it is back
                      to 146px, and it reads better besides: the provenance
                      belongs to the company, not to a column beside it. */}
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
                {rendered.map((row) => {
                  const key = rowKey(row)
                  const name = rowName(row)
                  // Registry rows only. A curated row has no `vendor_id`, so
                  // there is nothing to match a shortlist entry on — and
                  // matching on the name would be the very lookup this screen
                  // refuses to make.
                  const already =
                    row.kind === 'registry' && invited.has(row.bidder.id)
                  const groups =
                    row.kind === 'registry'
                      ? row.bidder.trade_categories
                      : row.entry.trade_categories
                  return (
                    <tr key={key}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Select ${name}`}
                          checked={selected.has(key)}
                          onChange={(e) =>
                            setSelected((prev) => {
                              const next = new Set(prev)
                              if (e.target.checked) next.add(key)
                              else next.delete(key)
                              return next
                            })
                          }
                        />
                      </td>
                      <td>
                        {/* Wrapped, so the name is still an element of its own:
                            with the source line beside it as a bare text node
                            the cell's text would read "Al Munara Switchgear
                            LLCRegistry" and nothing could match the name alone. */}
                        <span>{name}</span>
                        {/* Every row says which universe it came from, because
                            two rows can carry the same company name and which of
                            the two this is decides what may be done to it. */}
                        <div className="muted">
                          {row.kind === 'registry'
                            ? 'Registry'
                            : SOURCE_LABEL[row.entry.source]}
                        </div>
                        {/* The refusal lives in the row that caused it. */}
                        {rowError[key] && (
                          <div className="banner banner--error" role="alert">
                            {rowError[key]}
                          </div>
                        )}
                      </td>
                      {/* "Not checked" rather than empty pills or a dash: a
                          curated row has no registry row, so there is no finding
                          either way — the `null` vs `false` distinction the
                          shortlist's client-approval column keeps. */}
                      <td>
                        {row.kind === 'registry' ? (
                          <ApprovalPills approvers={row.bidder.approved_by} />
                        ) : (
                          <span className="muted">Not checked</span>
                        )}
                      </td>
                      {/* Capped at two: an eleven-group discipline expansion
                          rendered in full ran this cell to 595px and made every
                          row 170px tall. */}
                      <td className="muted">
                        {groups.slice(0, 2).join(' · ') || '—'}
                        {groups.length > 2 && ` · +${groups.length - 2} more`}
                      </td>
                      <td className="muted">
                        {row.kind === 'registry'
                          ? row.bidder.represented_manufacturers
                              .slice(0, 2)
                              .join(' · ') || '—'
                          : '—'}
                      </td>
                      {/* The derived status, not `prequal_status`: a lapsed
                          approval is still stored as "Approved". */}
                      <td>
                        {row.kind === 'registry' ? (
                          row.bidder.effective_prequal
                        ) : (
                          <span className="muted">Not checked</span>
                        )}
                      </td>
                      <td>
                        {already ? (
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
                            aria-label={`Shortlist ${name}`}
                            onClick={() => void invite(row)}
                          >
                            Shortlist
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <p className="muted">
            {needle
              ? `${shown.length} of ${poolTotal} vendors match "${query.trim()}".`
              : registryOff
                ? `${curatedRows.length} from this item's own lists.`
                : narrowed
                  ? `${registryTotal} available ${registryTotal === 1 ? 'vendor is' : 'vendors are'} registered for ${discipline}.`
                  : `${registryTotal} vendors approved by ${(data?.approvers ?? []).join(' and ')}.`}
            {!needle && !registryOff && curatedRows.length > 0 && (
              <>
                {' '}
                {curatedRows.length} more from this item's own lists.
              </>
            )}
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
  // The registry column and its tally belong to the uploads only. A curated
  // row is unlinked *by construction* — the server never looks a typed name up
  // — so "Not in the registry" there would report a check nobody ran, and the
  // "not found" count would tally the same non-lookup as a miss. That is the
  // `null` vs `false` distinction the shortlist's client-approval column
  // keeps, and here the honest rendering of `null` is no column at all.
  const uploaded = source === 'Client' || source === 'Astra'
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
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Vendor</th>
                  {uploaded && <th scope="col">Registry</th>}
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
                    {uploaded && (
                      <td>
                        {e.vendor_id === null ? (
                          <span className="warn">Not in the registry</span>
                        ) : (
                          <span className="muted">Linked</span>
                        )}
                      </td>
                    )}
                    {/* Capped at two, the same shape the available-vendor card
                        beside it uses. A curated row carries the item's whole
                        discipline expansion — eleven groups for Cables — and
                        rendered in full that cell ran to 595px, crushed the
                        vendor name to 67px and made every row 170px tall. Only
                        measurable in a browser: jsdom applies no stylesheet and
                        does no layout. Second instance on this screen, after the
                        89px shortlist button. */}
                    <td className="muted">
                      {e.trade_categories.slice(0, 2).join(' · ') || '—'}
                      {e.trade_categories.length > 2 &&
                        ` · +${e.trade_categories.length - 2} more`}
                    </td>
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
          </div>
          <p className="muted">
            {entries.length} on the {label.toLowerCase()} for{' '}
            {discipline || 'this item'}
            {uploaded && (
              <>
                {' '}
                · {linked} in the registry
                {linked < entries.length &&
                  ` · ${entries.length - linked} not found`}
              </>
            )}
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
        <div className="table-scroll">
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
        </div>
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
        projectId={projectId}
        itemId={itemId}
        discipline={item.discipline}
        // The two curated lists, from the payload this screen already read.
        // Passed rather than fetched: a second read could disagree with the
        // two cards rendered above, and there is nothing to ask the server
        // that it has not already answered.
        curated={[
          ...(data.item_vendor_lists?.[itemId]?.Manual ?? []),
          ...(data.item_vendor_lists?.[itemId]?.Suggested ?? []),
        ]}
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

      {/* Directly under the pool it is filled from, so shortlisting something
          and seeing it land are one glance apart. */}
      <DraftShortlistCard
        projectId={projectId}
        itemId={itemId}
        picks={data.draft_shortlists?.[itemId] ?? []}
        onChanged={() => setTick((t) => t + 1)}
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
          <div className="table-scroll">
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
          </div>
        )}
      </Card>
    </>
  )
}
