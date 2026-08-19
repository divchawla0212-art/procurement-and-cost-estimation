import type { User } from './auth/context'

// The reachability rule for the screens that read a stored extraction —
// `02 Overview`, `03 Compliance matrix` and `04 Comparative statement`.
// Pulled out of App.tsx (see BUG-001 in
// BUGS_TRACKER.md) so the rule can be unit-tested independently of whether
// anything actually wires it into a `disabled` prop.
//
// Decision (design spec §1.1, corrected — see the correction note in that
// section): this predicate originally consulted `status`, on the theory that
// `new` and `failed` were the two states where the store holds no extraction
// to read. That was wrong for `failed`: CLAUDE.md's store invariant "a
// failed extraction never blanks previously-good stored data" means a
// project whose first run succeeded and whose *second* run failed outright
// (an expired key, a provider outage, or a forced re-run) still holds the
// first run's complete extraction, with `status` recomputed to `"failed"`
// per document (`procurement/pipeline.py`: `"failed" if extracted == 0`).
//
// The right predicate is "does the store hold results to read", and
// `has_results` (`ProjectSummary.has_results`, computed in
// `api/main.py::_project_summary` the same way `_setup_state` already does —
// `procurement.pipeline.has_results`) answers exactly that. A project with
// results is reviewable whatever its `status`; a project without is not.
// `status` now drives only the partial/failed-run banner on the review
// screens, not this gate.
export function reviewReachable(hasResults: boolean | null | undefined): boolean {
  return hasResults === true
}

// `needsReview` marks the screens that read a stored extraction (BUG-001,
// BUGS_TRACKER.md): they need not just a selected project but one whose
// `has_results` passes `reviewReachable` (I2, final-review report —
// `status` alone is not the right gate: see `nav.ts`). `Overview` carries it
// too: it renders from the compliance matrix and statement, so it is as
// unreachable without an extraction as the two screens it links into.
// `Extraction status` stays `needsProject` only — it is the screen that
// reports why the review screens are unreachable, so it must stay open
// regardless of status.
//
// These two flags now only grey out a rail button. The screens themselves are
// guarded by `RequireResults` in `routes.tsx`, because a URL can be typed and a
// disabled button cannot stop that. Keeping both is not duplication: one tells
// the reader a screen is unavailable, the other makes it so.
//
// `roles` is omitted for every screen both roles can reach; the one entry that
// carries it is hidden from a reviewer's nav. This is presentation only — the
// server enforces the actual boundary, and every `/api/admin/*` route refuses
// a reviewer whether or not this list ever mentioned it.
//
// Grouped, because the two halves of this app answer different questions. The
// RFQ process is where a package is scoped, issued and awarded; bid evaluation
// is the ingestion pipeline and the screens that read what it extracted. They
// share a project but not a workflow, and a single flat list of eight entries
// invited the reading that ingestion *is* the process.
export type NavGroup = 'RFQ process' | 'Bid evaluation' | 'Administration'

export interface NavEntry {
  index: string
  label: string
  group: NavGroup
  /**
   * Where the entry goes. Returns `null` when it needs a project and there is
   * none — which is also what disables it, so a rail button can never point at
   * `/bid-sets/null/overview`.
   */
  to: (slug: string | null) => string | null
  /** Whether the current path belongs to this entry, for the active highlight. */
  matches: (pathname: string) => boolean
  needsReview: boolean
  roles?: Array<User['role']>
}

// Written out per entry rather than derived from a prefix. `/bid-sets` and
// `/bid-sets/:slug/setup` both start with `/bid-sets`, so a prefix rule lights
// up two entries at once; being explicit costs a line each and cannot go wrong
// quietly.
export const NAV: NavEntry[] = [
  // Both RFQ-process screens are project-agnostic in the sense the review gate
  // means it: their projects are the workflow store's own, not the ingestion
  // `slug`s the Bid-evaluation entries below are built from. That entry is
  // called "Bid sets" rather than "Projects" for the same reason — two rail
  // entries reading "Projects" over two different stores is the first confusion
  // a reader hits. Unifying the two identities is phase 2.
  //
  // There is no rail entry for the bidder registry. It is not a place you go;
  // it is the list the Shortlisting step of an RFQ draws candidates from, and
  // that is the only screen that reads it.
  {
    index: '01',
    label: 'Projects & items',
    group: 'RFQ process',
    to: () => '/projects',
    matches: (p) => p === '/projects' || p.startsWith('/projects/'),
    needsReview: false,
  },
  {
    index: '02',
    label: 'RFQ workflow',
    group: 'RFQ process',
    to: () => '/rfqs',
    matches: (p) => p === '/rfqs' || p.startsWith('/rfqs/'),
    needsReview: false,
  },
  {
    index: '03',
    label: 'Bid sets',
    group: 'Bid evaluation',
    to: () => '/bid-sets',
    matches: (p) => p === '/bid-sets',
    needsReview: false,
  },
  {
    index: '04',
    label: 'Set up & ingest',
    group: 'Bid evaluation',
    // With no project selected this is where you create one, which is exactly
    // what the screen does with a null slug.
    to: (slug) => (slug ? `/bid-sets/${slug}/setup` : '/bid-sets/new'),
    matches: (p) => p === '/bid-sets/new' || /^\/bid-sets\/[^/]+\/setup$/.test(p),
    needsReview: false,
  },
  {
    index: '05',
    label: 'Extraction status',
    group: 'Bid evaluation',
    to: (slug) => (slug ? `/bid-sets/${slug}/extraction` : null),
    matches: (p) => /^\/bid-sets\/[^/]+\/extraction$/.test(p),
    needsReview: false,
  },
  {
    index: '06',
    label: 'Overview',
    group: 'Bid evaluation',
    to: (slug) => (slug ? `/bid-sets/${slug}/overview` : null),
    matches: (p) => /^\/bid-sets\/[^/]+\/overview$/.test(p),
    needsReview: true,
  },
  {
    index: '07',
    label: 'Compliance matrix',
    group: 'Bid evaluation',
    to: (slug) => (slug ? `/bid-sets/${slug}/matrix` : null),
    matches: (p) => /^\/bid-sets\/[^/]+\/matrix$/.test(p),
    needsReview: true,
  },
  {
    index: '08',
    label: 'Comparative statement',
    group: 'Bid evaluation',
    to: (slug) => (slug ? `/bid-sets/${slug}/statement` : null),
    matches: (p) => /^\/bid-sets\/[^/]+\/statement$/.test(p),
    needsReview: true,
  },
  {
    index: '09',
    label: 'Users and access',
    group: 'Administration',
    to: () => '/admin',
    matches: (p) => p === '/admin',
    needsReview: false,
    roles: ['admin'],
  },
]

export const NAV_GROUPS: NavGroup[] = ['RFQ process', 'Bid evaluation', 'Administration']

/** The `:slug` of a bid-evaluation URL, or `null` when the path carries none. */
export function bidSetSlug(pathname: string): string | null {
  const m = /^\/bid-sets\/([^/]+)\//.exec(pathname)
  // `/bid-sets/new` is the create screen, not a project called "new".
  return m && m[1] !== 'new' ? m[1] : null
}

/* ------------------------------------------------------- the next page */

/** Where the end-of-page control goes from `pathname`, or `null` for no
 *  control at all.
 *
 *  Derived from `NAV` rather than from a second table of destinations. The
 *  rail and this button answer the same question — what comes after this
 *  screen — and a hand-written list beside an ordered one is a list that
 *  disagrees with it after the first insertion.
 *
 *  Three ways it answers `null`, and the first is the important one:
 *
 *  - **The next page is not reachable yet.** No slug for an entry that needs
 *    one, or a `needsReview` entry with no stored extraction. The button is
 *    then *absent*, never rendered disabled — a large primary that cannot be
 *    pressed is the `Send to 0 vendors` defect wearing different clothes.
 *    Reachability is the rail's own rule, unchanged and read from here.
 *  - **There is no next page**: `09` is last, and `08` is last for a reviewer.
 *  - **The path is not a rail entry at all** — nothing outside `NAV` gets a
 *    button by accident.
 *
 *  An entry the role cannot see is skipped rather than refused, so a hidden
 *  admin screen in the middle of the table would not sever the chain for a
 *  reviewer. An *unreachable* one stops the walk instead: skipping past it
 *  would land the reader two screens further on than the rail's own next, on
 *  a page they have no way to have prepared for.
 */
export function nextPage(
  pathname: string,
  opts: { slug: string | null; hasResults: boolean | null | undefined; role: User['role'] },
): { to: string; label: string } | null {
  const here = NAV.findIndex((entry) => entry.matches(pathname))
  if (here < 0) return null
  for (const entry of NAV.slice(here + 1)) {
    if (entry.roles && !entry.roles.includes(opts.role)) continue
    const to = entry.to(opts.slug)
    if (to === null) return null
    if (entry.needsReview && !reviewReachable(opts.hasResults)) return null
    return { to, label: entry.label }
  }
  return null
}
