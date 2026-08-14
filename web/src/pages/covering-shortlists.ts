import { fetchRfq } from '../api'
import { useAsync } from '../useAsync'
import type { AsyncState } from '../useAsync'
import type { Rfq, ShortlistEntry } from '../types'

/**
 * Every covering RFQ's shortlist, in one read.
 *
 * The project payload carries no shortlist, so this is one `fetchRfq` per
 * covering RFQ. That cost is deliberate: for the one-to-three RFQs an item
 * typically has, N reads of an existing endpoint beat a new aggregate route
 * that would exist for one column. If an item ever covers enough RFQs for that
 * to hurt, the fix is that route — never a cap, which would under-report.
 *
 * Shared by the summary column and the Shortlist control rather than fetched
 * twice, because the screen must not offer to invite somebody it is
 * simultaneously counting as invited two cards below.
 */
export interface CoveringShortlist {
  rfq: Rfq
  /** `null` when this RFQ's read failed — distinct from `[]`, which is an RFQ
   *  with nobody invited. An unanswered question and an empty shortlist are
   *  different facts, and the column renders them differently. */
  shortlist: ShortlistEntry[] | null
  /** From the same payload, so the summary can order itself by the client's
   *  approver without the browser naming that organisation. `null` when the
   *  read failed, alongside `shortlist`. */
  clientApprover: string | null
}

export function useCoveringShortlists(
  covering: Rfq[],
  tick: number,
): AsyncState<CoveringShortlist[]> {
  return useAsync<CoveringShortlist[]>(
    () =>
      Promise.all(
        covering.map((r) =>
          fetchRfq(r.id)
            // Caught per RFQ, not per batch: one unreadable RFQ leaves its own
            // row blank and the others intact, where a rejected batch would
            // blank a column that is mostly correct.
            .then((d) => ({
              rfq: r,
              shortlist: d.shortlist,
              clientApprover: d.client_approver,
            }))
            .catch(() => ({ rfq: r, shortlist: null, clientApprover: null })),
        ),
      ),
    // The ids joined, never the array itself: `covering` is a fresh array on
    // every render of the item screen, so passing it here would re-run the
    // fetch forever. That is the same failure CLAUDE.md records for a `useAuth`
    // returning a fresh object, and it arrives the same way — a vitest worker
    // dying of heap exhaustion rather than a failed assertion.
    [covering.map((r) => r.id).join(','), tick],
  )
}

/**
 * One RFQ's shortlist as a sentence: how many were invited, and how many carry
 * each approval.
 *
 * The approvers counted are **whichever ones these rows actually carry**, not
 * a list this module names. That is not a second definition of who may approve
 * a vendor — it is just the set being counted — and it means a second client's
 * AVL appears here with no edit. `clientApprover` comes from the server and is
 * used only to order the sentence, so the column reads the same way on every
 * row and no screen spells that name for itself.
 *
 * An approver contributing zero is omitted rather than rendered as `0 Astra`:
 * a row of zeroes invites the reader to scan for a number that is never
 * meaningful. `not checked` is reported separately and never folded into an
 * approver's count, because a vendor with no registry row is not a vendor
 * anyone found unapproved.
 */
export function summarise(rows: ShortlistEntry[], clientApprover: string): string {
  const seen: string[] = []
  for (const row of rows) {
    for (const org of row.approved_by ?? []) {
      if (!seen.includes(org)) seen.push(org)
    }
  }
  const approvers = [
    ...(seen.includes(clientApprover) ? [clientApprover] : []),
    ...seen.filter((org) => org !== clientApprover),
  ]

  const parts = [`${rows.length} invited`]
  for (const org of approvers) {
    const n = rows.filter((e) => e.approved_by?.includes(org)).length
    if (n > 0) parts.push(`${n} ${org}`)
  }
  const unchecked = rows.filter((e) => e.approved_by === null).length
  if (unchecked > 0) parts.push(`${unchecked} not checked`)
  return parts.join(' · ')
}
