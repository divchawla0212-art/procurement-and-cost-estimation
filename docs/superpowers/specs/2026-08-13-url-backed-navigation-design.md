# URL-backed navigation — design

**Date:** 2026-08-13
**Branch:** `rfq-platform-phase-1`
**Status:** approved, ready to plan

The application has ten screens and three levels of drill-down, and not one of
them has an address. Every screen is a value in a React `useState`, so the
browser's Back button does not go back a screen — it leaves the application
entirely. This design gives every screen a URL, which is what makes Back,
Forward, refresh and a pasted link all mean something.

---

## 1. What exists today

Navigation state is spread across three components that do not know about each
other:

| holder | state | what it selects |
| --- | --- | --- |
| `App.tsx` | `view` | which of ten rail screens renders |
| `App.tsx` | `slug` | which ingestion project the Bid-evaluation screens read |
| `App.tsx` | `matrixVendor` | the vendor filter the Overview drills into |
| `App.tsx` | `creating` | whether Setup is creating or editing |
| `App.tsx` | `navEpoch` | a remount counter — see below |
| `Projects.tsx` | `openProjectId`, `openItemId` | project → item drill-down |
| `RfqWorkflow.tsx` | `openRfqId` | RFQ drill-down |

Two consequences follow from this, and both are the reason for the work:

- **The browser's Back button exits the application.** There is no history to
  walk, because nothing was ever pushed onto one.
- **`navEpoch` exists only to paper over the split.** `App.tsx` cannot see
  `Projects`'s `openProjectId`, so clicking "01 Projects & items" while three
  levels deep set `view` to a value it already held — a no-op that left the
  drill-down mounted and made the rail button read as dead. The fix was a
  counter used as a React `key` to force a remount. Once a drill-down is a URL,
  that click is a real navigation and the counter is unnecessary.

Single-hop escapes already exist and are not the gap: `ProjectDetail` and
`ItemDetail` both render a `Breadcrumb` and a `← Back to projects` button. Those
answer "go **up** to my parent". Nothing answers "go **back** to where I just
was", and nothing at all answers "go forward again".

## 2. Approach

**React Router v7 in declarative mode** — `BrowserRouter`, `Routes`, `Route`.
Chosen over a hand-rolled History API hook and over an in-memory-only stack.

The trade-off was put explicitly and decided: a hand-rolled hook would have kept
the web app's dependency list at exactly `react` + `react-dom`, and react-router
costs a dependency and a larger diff. It buys the conventional, well-understood
answer — one that a developer joining this codebase already knows, and that does
not require them to trust ~100 lines of bespoke routing. An in-memory stack was
rejected outright: it leaves the browser's own Back button still exiting the
app, which is the control a user actually reaches for.

v7 ships the web APIs in the base `react-router` package; `react-router-dom` is
a deprecated re-export and is not used.

## 3. Route table

| Path | Screen | Guard |
| --- | --- | --- |
| `/` | → `/projects` (replace) | |
| `/projects` | Projects roster | |
| `/projects/:projectId` | ProjectDetail | |
| `/projects/:projectId/items/:itemId` | ItemDetail | |
| `/bidders` | Bidders | |
| `/rfqs` | RFQ workflow roster | |
| `/rfqs/:rfqId` | RfqWizard *or* RfqDetail, by stage | |
| `/bid-sets` | Dashboard | |
| `/bid-sets/new` | Setup, creating | |
| `/bid-sets/:slug/setup` | Setup | |
| `/bid-sets/:slug/extraction` | ExtractionStatus | needs project |
| `/bid-sets/:slug/overview` | Overview | needs results |
| `/bid-sets/:slug/matrix` | ComplianceMatrix | needs results |
| `/bid-sets/:slug/statement` | ComparativeStatement | needs results |
| `/admin` | Admin | admin role |
| `*` | → `/projects` (replace) | |

Two path decisions worth stating:

- **`/rfqs`, not `/workflow`.** The screen is a roster of RFQs and its rows are
  RFQs; `/rfqs/:rfqId` reads as what it is. "Workflow" is the label, not the
  resource.
- **`/bid-sets/:slug/…`, with the slug in the path.** The Bid-evaluation screens
  are meaningless without a project, and putting the slug in component state
  while the screen name is in the URL would produce a link that opens a
  different project for whoever clicks it. The rail's label is already "Bid
  sets" rather than "Projects", for the same reason the path is.

The Overview's vendor drill-down becomes `?vendor=…` on the matrix route,
replacing the `matrixVendor` state. This is the one piece of navigation state
that is genuinely a filter rather than a location, and a query string is exactly
that distinction.

## 4. What the components stop owning

`App.tsx` loses `view`, `navEpoch`, `matrixVendor` and `creating`.
`Projects.tsx` loses `openProjectId` and `openItemId` and both of its early
returns. `RfqWorkflow.tsx` loses `openRfqId`. All three become screens that
render what the URL asks for.

`slug` survives, renamed `activeSlug`, because the rail's project switcher and
the status bar need an active project on `/projects` and `/bidders` where the
path carries none. It syncs *from* the path whenever a `/bid-sets/:slug/…` route
is active, so the switcher and the URL can never disagree.

**`selectProject`'s special case disappears into a guard.** `App.tsx` currently
carries a rule (the I1 fix) that switching project while sitting on a review
screen must bounce to `setup` when the newly selected project has no extraction.
Under routing, switching navigates to the same screen under the new slug and the
*needs results* guard makes that decision on its own. Same behaviour, one rule
instead of two code paths — and it now also covers the case the original could
not: a pasted link to a review screen of a project that has no extraction.

## 5. Leaf components keep their props

`ProjectDetail`, `ItemDetail`, `RfqWizard`, `RfqDetail`, `Overview`,
`ComplianceMatrix`, `ComparativeStatement`, `ExtractionStatus`, `Setup`,
`Dashboard` and `Admin` keep their current prop interfaces — `projectId`,
`onBack`, `onOpenItem`, `slug` and the rest — unchanged.

A new `web/src/routes.tsx` holds thin wrappers that read `useParams` /
`useSearchParams` and call `useNavigate`, adapting the URL to those props. Two
reasons, and the second is the load-bearing one:

- Every leaf stays testable in isolation, without a router, exactly as its
  existing test file already renders it. Most of the 141-test web suite survives
  untouched.
- **Routing stays in one file.** A reader asking "where can this application
  go?" reads `routes.tsx` and is done, rather than grepping `useNavigate` across
  fourteen screens.

`RfqRoute` is the one wrapper that must fetch: `RfqWorkflow` picks wizard versus
read-only view from the roster's stage data, and a deep link arrives with no
roster loaded. It calls `fetchRfqRoster` itself. That is a real extra request on
a deep link, and it is the cost of the route being addressable at all.

## 6. Back and forward availability — `web/src/history-stack.ts`

The History API cannot answer either question. There is no `canGoForward` at
all, and `window.history.length` counts entries from before the application
loaded and never shrinks. React Router does not expose it either.

So the model is ours, as a pure reducer unit-tested standalone the way `nav.ts`
is — no router, no DOM, no hooks:

```ts
type HistoryModel = { keys: string[]; index: number }
visit(model, key, type: 'PUSH' | 'POP' | 'REPLACE'): HistoryModel
canGoBack(model): boolean
canGoForward(model): boolean
```

- **PUSH** truncates every entry after `index`, appends the key, points `index`
  at it. Truncation is what makes Forward stop being available after you go back
  and then navigate somewhere new.
- **REPLACE** overwrites `keys[index]` and moves nothing. This is what keeps a
  guard redirect from lengthening the history.
- **POP** re-indexes to the position of the incoming key, which covers Back,
  Forward and a multi-step jump from the browser's own history menu with one
  branch.
- **A POP to a key we have never seen resets the model** to that single entry.
  It means the entry predates this application session, and we genuinely cannot
  know what lies on either side of it. Both buttons disable. Reporting "no"
  when the answer is unknown is honest; guessing produces a button that does
  nothing when pressed.

A `NavHistory` provider drives it from `useLocation().key` and
`useNavigationType()`.

**Invariant: every guard redirect uses `replace`, never a push.** A redirect
that pushes puts the redirecting URL in the history, so Back lands on it and is
immediately thrown forward again. The user sees a Back button that appears
frozen. This is the one defect this design is most exposed to, because each
guard is written separately and only the composite behaviour shows it.

## 7. Status bar controls

A `← →` pair at the left of the existing `.statusbar`, before the project chip:
above every screen, always in the same place, `aria-label`led ("Go back" / "Go
forward"), and `disabled` per the model in §6.

The existing per-page `← Back to projects` buttons and breadcrumbs stay. They
mean "up to my parent", which is a different question from "back to where I
was", and on a drill-down reached from a rail click the two have different
answers. Removing them would also silently change what
`ProjectDetail.test.tsx` and `ItemDetail.test.tsx` cover.

No keyboard shortcuts are added: <kbd>Alt</kbd>+<kbd>←</kbd> and
<kbd>Alt</kbd>+<kbd>→</kbd> are already browser back and forward, and they will
now work correctly for the first time.

## 8. Server-side SPA fallback

`api/main.py` mounts `StaticFiles(directory=_WEB_DIST, html=True)` last. With
`html=True` Starlette serves `index.html` for *directory* requests and 404s
everything else, so `/projects/prj_5049beff` — a URL this design makes it
possible to bookmark — returns 404 on refresh in a container.

The mount is replaced by a catch-all that returns `index.html` for any path that
is not under `/api` and does not resolve to a real file on disk. It stays
mounted after every `/api` route so it cannot shadow one, and it must not
swallow a genuinely missing API path into an HTML page.

Vite's dev server already performs this fallback, so **this bug is invisible on
a workstation and only appears in a container**. That is precisely why it gets a
Python test rather than a manual check.

## 9. Testing

| what | where |
| --- | --- |
| the reducer, all three navigation types, the unknown-key reset | `web/src/history-stack.test.ts` (new) |
| deep link renders the right screen for every route | `web/src/routes.test.tsx` (new) |
| both guards redirect, and redirect with `replace` | `web/src/routes.test.tsx` |
| back/forward buttons enable and disable across a real navigation sequence | `web/src/routes.test.tsx` |
| rosters navigate instead of drilling down in place | `Projects.test.tsx`, `RfqWorkflow.test.tsx` (reworked) |
| the shell renders under a router | `App.test.tsx` (wrapped) |
| SPA fallback serves `index.html`, and does not swallow `/api` | `tests/test_spa_fallback.py` (new) |

Route tests use `MemoryRouter` with `initialEntries`. `App.test.tsx` keeps the
stable `useAuth` mock built once via `vi.hoisted` — a fresh object per call puts
`user` in a `useAsync` dependency list and refetches until the vitest worker
dies of heap exhaustion, which surfaces as `Worker exited unexpectedly` rather
than a failed assertion.

The `replace`-on-redirect assertion is deliberately behavioural rather than a
spy on `navigate`: it navigates into a guarded route, then goes back, and
asserts it did not land on the redirecting URL. A spy would pass against a
redirect that pushes.

## 10. Counts

Both suites move. `python -m pytest` gains the SPA fallback test; the web suite
gains two files and reworks three. CLAUDE.md's two baseline rows are updated by
**measuring the workstation row and deriving the CI row** by the documented
subtraction — four corpus-coverage, three `data/`, two `pdftotext` and nine AVL
tests turning into skips — rather than editing the two rows independently.

## 11. Out of scope

- **Unifying the workflow project and the ingestion project.** `/projects/:id`
  and `/bid-sets/:slug` address two different stores, and this design keeps them
  visibly separate rather than papering over the split with a shared path. That
  unification is phase 2.
- **Route-level data loaders.** Declarative mode only; components keep fetching
  through `useAsync`. Moving to a data router is a separate change with its own
  payoff and its own risk.
- **Persisting scroll position** across navigations.
- **A `basename`.** The application is served from the origin root.
