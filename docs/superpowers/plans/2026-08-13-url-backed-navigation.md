# URL-backed navigation — implementation plan

**Spec:** [`2026-08-13-url-backed-navigation-design.md`](../specs/2026-08-13-url-backed-navigation-design.md)
**Branch:** `rfq-platform-phase-1`

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the
> implementation against the test.

## Applying PLAN-TEMPLATE.md to a front-end change

[`PLAN-TEMPLATE.md`](../PLAN-TEMPLATE.md) is written for the extraction
pipeline's stores, and this change writes to no store. Its three rules are not
about stores, though — they are about **defects that need two runs or two
modules to see**, and this design is squarely in that class. The adaptation:

| template | here |
| --- | --- |
| **Store invariant owned** — a sentence about stored state | **Navigation invariant owned** — a sentence about the history stack after a sequence, not about what a function returned |
| **Two-run mutation matrix** | **Navigation-sequence matrix** — every row is two or more navigations, because no single navigation can show these defects |
| **Reference code is intent** | unchanged |

The load-bearing example, and the reason the matrix is not optional: a guard
that redirects with a *push* instead of a *replace* renders correctly, passes
any single-navigation test, and passes a spy asserting `navigate` was called. It
only fails when you arrive at the guarded route and **then press Back** — and it
fails as a Back button that appears frozen, which reads as a bug in the button
rather than in the guard.

---

### Task 1: The history model

**Files:**
- Create: `web/src/history-stack.ts`
- Test: `web/src/history-stack.test.ts`

**Interfaces:**
- Consumes: a location key and a navigation type, nothing else — no router, no DOM, no hooks
- Produces: `visit(model, key, type) -> HistoryModel`, `canGoBack(model) -> boolean`, `canGoForward(model) -> boolean`
- **Navigation invariant owned:** after any sequence of visits, `keys` contains
  **exactly** the entries reachable from the current position — `index` entries
  behind and `keys.length - index - 1` ahead — and no entry that a PUSH
  truncated.

Pure and standalone, the way `nav.ts` is, so the rule can be tested without
mounting anything. "Exactly" is the word that matters: the defect this shape
invites is a PUSH that appends without truncating, which leaves Forward enabled
pointing at a branch the user abandoned.

- [ ] **Step 1: Write the failing test.** Cover: PUSH truncates a forward tail;
      REPLACE overwrites in place and does not lengthen; POP re-indexes for
      back, forward, and a multi-step jump; an unknown key resets to a single
      entry with both directions disabled; `canGoBack`/`canGoForward` at both
      ends.
- [ ] **Step 2: Run to verify it fails**
- [ ] **Step 3: Implement**

      Load-bearing: the truncation in PUSH, and the unknown-key reset. The exact
      shape of `HistoryModel` is illustrative.

      ```ts
      // intent, not paste-able
      export function visit(m: HistoryModel, key: string, type: NavType): HistoryModel {
        if (type === 'REPLACE') {
          const keys = m.keys.slice(); keys[m.index] = key
          return { keys, index: m.index }
        }
        if (type === 'POP') {
          const found = m.keys.indexOf(key)
          // Unknown: the entry predates this session. We cannot know what is on
          // either side of it, so say so rather than guess.
          return found === -1 ? { keys: [key], index: 0 } : { keys: m.keys, index: found }
        }
        const keys = [...m.keys.slice(0, m.index + 1), key]   // truncate, then append
        return { keys, index: keys.length - 1 }
      }
      ```
- [ ] **Step 4: Run to verify it passes**
- [ ] **Step 5: Commit**

---

### Task 2: The route table

**Files:**
- Modify: `web/package.json` (add `react-router`), `web/src/main.tsx`
- Create: `web/src/routes.tsx`
- Test: `web/src/routes.test.tsx`

**Interfaces:**
- Consumes: the URL
- Produces: `<AppRoutes />` — the route table of spec §3, plus the param→prop wrappers
- **Navigation invariant owned:** every path in spec §3 renders exactly one
  screen, and every path outside the table lands on `/projects` — there is no
  URL that renders nothing.

Leaf components keep their existing prop interfaces; the wrappers adapt
`useParams` / `useSearchParams` to those props. Routing lives in this one file so
"where can this application go?" has a single answer.

`RfqRoute` is the exception that must fetch — `RfqWorkflow` chooses wizard versus
read-only from roster stage data, and a deep link arrives with no roster loaded.

- [ ] **Step 1: Write the failing test** — one `MemoryRouter` case per row of the
      §3 table, plus an unknown path landing on `/projects`.
- [ ] **Step 2: Run to verify it fails**
- [ ] **Step 3: Implement.** Install `react-router` (v7, base package — not
      `react-router-dom`). Wrap `main.tsx` in `<BrowserRouter>`.
- [ ] **Step 4: Run to verify it passes**
- [ ] **Step 5: Commit**

---

### Task 3: The guards

**Files:**
- Modify: `web/src/routes.tsx`
- Test: `web/src/routes.test.tsx`

**Interfaces:**
- Consumes: the active project summary, the signed-in user's role
- Produces: `<RequireResults>`, `<RequireAdmin>`
- **Navigation invariant owned:** a guard redirect leaves the history **exactly
  one entry longer than before the navigation that triggered it** — the
  redirecting URL is not an entry. Pressing Back from a redirect target must
  never return to the redirect.

`RequireResults` reuses `reviewReachable` from `nav.ts` unchanged — that
predicate is already the tested answer to "does the store hold results to read",
and its correction note explains why `status` is the wrong gate. `RequireAdmin`
repeats the role check that `App.tsx:372` already makes; the server enforces the
real boundary either way.

- [ ] **Step 1: Write the failing test.** The `replace` assertion is
      **behavioural, not a spy**: navigate to a guarded route, confirm the
      redirect target rendered, press Back, assert we did *not* land on the
      guarded URL again. A spy on `navigate` passes against a redirect that
      pushes, which is the defect.
- [ ] **Step 2: Run to verify it fails**
- [ ] **Step 3: Implement.** Load-bearing: `replace` on every `<Navigate>`.
- [ ] **Step 4: Run to verify it passes**
- [ ] **Step 5: Commit**

---

### Task 4: `App.tsx` gives up navigation state

**Files:**
- Modify: `web/src/App.tsx`
- Test: `web/src/App.test.tsx`

**Interfaces:**
- Consumes: `useLocation`, `useNavigate`
- Produces: the shell — rail, switcher, status bar — with `<AppRoutes/>` in the canvas
- **Navigation invariant owned:** the rail's highlighted entry and the project
  switcher's selection are **derived from the URL**, never held independently —
  so no navigation can leave them disagreeing with what is on screen.

Deletes `view`, `navEpoch`, `matrixVendor` and `creating`. `slug` becomes
`activeSlug`, syncing from the path whenever a `/bid-sets/:slug/…` route is
active. `selectProject`'s I1 special case is deleted — Task 3's `RequireResults`
now makes that decision, and covers the pasted-link case the original could not.

`navEpoch` going is the point, not a side effect: it existed only because
`App.tsx` could not see `Projects`'s drill-down state.

- [ ] **Step 1: Write the failing test** — rail highlight follows the URL;
      switching project on a review screen whose target has no results lands on
      setup; `App` renders under a router. Keep the `vi.hoisted` stable `useAuth`
      mock (CLAUDE.md — a fresh object per call OOMs the worker).
- [ ] **Step 2: Run to verify it fails**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run to verify it passes**
- [ ] **Step 5: Commit**

---

### Task 5: The rosters stop drilling down in place

**Files:**
- Modify: `web/src/pages/Projects.tsx`, `web/src/pages/RfqWorkflow.tsx`
- Test: `web/src/pages/Projects.test.tsx`, `web/src/pages/RfqWorkflow.test.tsx`

**Interfaces:**
- Consumes: nothing new
- Produces: rosters that navigate
- **Navigation invariant owned:** neither component holds any state that selects
  a screen — after this task, `openProjectId`, `openItemId` and `openRfqId` do
  not exist anywhere in the codebase.

That invariant is assertable by grep, and it is the one that keeps this change
from being half-done: a surviving drill-down field would be a screen with no
address, silently outside the history.

- [ ] **Step 1: Write the failing test** — clicking a project row navigates to
      `/projects/:id` rather than swapping the subtree in place.
- [ ] **Step 2: Run to verify it fails**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run to verify it passes**
- [ ] **Step 5: Commit**

---

### Task 6: The ← → controls

**Files:**
- Create: `web/src/NavHistory.tsx`
- Modify: `web/src/App.tsx`, `web/src/theme.css`
- Test: `web/src/routes.test.tsx`

**Interfaces:**
- Consumes: `useLocation().key`, `useNavigationType()`, Task 1's reducer
- Produces: `useNavHistory() -> { canBack, canForward }`, and the two buttons
- **Navigation invariant owned:** a disabled direction is never navigable and a
  navigable direction is never disabled — the buttons report the model in Task
  1, and the model is the only source of that answer.

`window.history.length` is not usable: it counts entries from before the app
loaded, never shrinks, and there is no `canGoForward` at all. Worth a comment at
the call site, because reaching for it is the obvious wrong instinct.

- [ ] **Step 1: Write the failing test** — enable/disable across a real
      sequence: fresh load (both off), navigate (back on, forward off), back
      (both on), navigate somewhere new (forward off again — the truncation).
- [ ] **Step 2: Run to verify it fails**
- [ ] **Step 3: Implement.** `aria-label`led "Go back" / "Go forward".
- [ ] **Step 4: Run to verify it passes**
- [ ] **Step 5: Commit**

---

### Task 7: SPA fallback

**Files:**
- Modify: `api/main.py`
- Test: `tests/test_spa_fallback.py`

**Interfaces:**
- Consumes: a request path
- Produces: `index.html` for any non-`/api` path with no file behind it
- **Navigation invariant owned:** every URL the route table can produce survives
  a refresh — and **no `/api` path is ever answered with HTML**, whether it
  exists or not.

The second half is the trap. A catch-all mounted at `/` that returns
`index.html` for anything unmatched will happily answer a misspelled API route
with a 200 and a page, turning a 404 a developer could read into a JSON parse
error they cannot. `/api/*` must still 404 as JSON.

Vite's dev server already does this fallback, so **the bug is invisible on a
workstation and appears only in a container** — which is exactly why it gets a
test rather than a manual check.

- [ ] **Step 1: Write the failing test** — a deep path returns `index.html`; a
      real asset still returns the asset; `/api/nope` still 404s and is not
      HTML; the app still starts with no `web/dist` present.
- [ ] **Step 2: Run to verify it fails**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run to verify it passes**
- [ ] **Step 5: Commit**

---

### Task 8: Integration

**Files:**
- Test: `web/src/routes.test.tsx`
- Modify: `CLAUDE.md`

- [ ] **Step 1–4:** as usual, then:

- [ ] **Step 4b: Navigation-sequence matrix.** One test per row. Every row is two
      or more navigations, because none of these defects is visible in one.

| sequence | invariant at risk | assert |
|---|---|---|
| rail → drill down → rail entry already active | Task 5 (no screen-selecting state) | lands on the roster, not the stale drill-down — the defect `navEpoch` was papering over |
| navigate into a guarded route → Back | Task 3 (redirect adds one entry) | lands before the guarded route, not on it |
| deep-link a guarded route directly → Back | Task 3 | leaves the app or lands on the roster — never bounces on the redirect |
| A → B → Back → C | Task 1 (exact reachable set) | Forward is disabled at C; B is unreachable |
| A → B → Back → Forward | Task 1 | lands on B, both directions correct at each step |
| switch project on a review screen, target has no results | Task 4 (I1 preserved) | lands on that project's setup, and Back returns to the original screen |
| Overview → vendor drill-down → Back | §3 (`?vendor=` is a location) | returns to Overview, not to an unfiltered matrix |
| sign out → sign in | Task 2 | the URL is preserved across the `<Auth/>` gate |
| a reviewer deep-links `/admin` | Task 3 | redirected, and the Admin screen never mounts |
| refresh on a deep path | Task 7 | serves the app, not a 404 |

- [ ] **Step 4c: Verify the matrix is real.** Reintroduce each defect one at a
      time — change one `<Navigate replace>` to a push, drop the truncation in
      `visit`, restore `openProjectId` — and confirm the intended row fails and
      nothing else does. A row that still passes with its defect reinstated is
      not testing what it claims.

- [ ] **Step 5: Counts.** Run `python -m pytest` and `npm test` under `web/`.
      **Measure the workstation row**, then derive the CI row by the documented
      subtraction (4 corpus-coverage + 3 `data/` + 2 `pdftotext` + 9 AVL turning
      into skips). Update CLAUDE.md's two rows and the web-suite figure together.

- [ ] **Step 6: Commit**
