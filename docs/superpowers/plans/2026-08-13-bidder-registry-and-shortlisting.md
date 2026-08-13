# Bidder registry and shortlisting — implementation plan

**Spec:** [`2026-08-13-bidder-registry-and-shortlisting-design.md`](../specs/2026-08-13-bidder-registry-and-shortlisting-design.md)
**Template:** written against [`PLAN-TEMPLATE.md`](../PLAN-TEMPLATE.md) — every task
names the store invariant it owns, the integration task carries a two-run
mutation matrix, and reference code is intent rather than paste-able.

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the
> implementation against the test.
>
> Load-bearing in every block: the *failure convention* (a refusal names the
> whole criterion), the *id-stability rule* (`vendor_id`, never a name), and
> *where the lock sits* (a read that gates a write is inside `locked_update`).
> Illustrative: exact wording of messages, exact field ordering, exact CSS
> class names.

A note on the template's nine required matrix rows: they are stated in terms of
the extraction pipeline (superseded documents, LLM outages, prompt-version
bumps). This feature touches none of that — it adds one accumulating collection
to the workflow store. The matrix in Task 10 therefore carries the *shape* the
rule requires — a mutation between two runs, an invariant at risk, an assertion
— translated to this collection, plus the rows the existing eight-row matrix in
`test_workflow_persistence.py` already holds.

---

### Task 1: The `Bidder` model and the suitability rules

**Files:**
- Create: `workflow/models/bidder.py`, `workflow/bidders.py`
- Test: `tests/test_bidder_suitability.py`

**Interfaces:**
- Consumes: `RfqRecord` (for `discipline` and `package`)
- Produces: `Bidder`, `PrequalStatus`, `Suitability`,
  `evaluate(bidder, rfq, as_of) -> Suitability`,
  `effective_prequal(bidder, as_of) -> str`
- **Store invariant owned:** none — this task stores nothing. `workflow/bidders.py`
  is a pure module with no store dependency and no I/O, and the absence of a
  stored eligibility *is* the design: `Expired` exists only as a computed value,
  so no sweep job is needed to keep the store honest.

The one decision worth stating: `as_of` is a parameter, not a `date.today()`
call inside the function. Both expiry boundaries have to be asserted from both
sides, and a function that reads the clock cannot be tested at a boundary
without freezing it.

- [ ] **Step 1: Write the failing test.** One case per row of the spec's §2
      table, plus four boundary cases: expiring exactly on `as_of` (not yet
      expired), the day after (expired), exactly 30 days out (caution), 31 days
      out (no caution). Plus: a blocker sentence names the criterion, not the
      half of it — assert the hold reason and the lapse date appear in the text.
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Write the implementation.**

  ```python
  # Intent. The classification split is load-bearing; the wording is not.
  def evaluate(bidder, rfq, as_of):
      blockers, cautions = [], []
      if bidder.on_hold:
          blockers.append(f"{bidder.name} is on hold: {bidder.hold_reason or 'no reason recorded'}.")
      status = effective_prequal(bidder, as_of)
      if status == "Expired":
          blockers.append(
              f"Prequalification lapsed on {bidder.prequal_expires_on.isoformat()} "
              f"and must be renewed before {bidder.name} can be invited."
          )
      elif status != "Approved":
          blockers.append(f"Prequalification is {status.lower()}, not approved.")
      # Exact whole-string match, case-folded. Substring matching is how
      # `classify._RULES` shipped false matches; see PLAN-TEMPLATE Rule 3.
      wanted = {rfq.discipline.casefold(), rfq.package.casefold()}
      scope_fit = any(c.casefold() in wanted for c in bidder.trade_categories)
      if not scope_fit:
          cautions.append(...)
      ...
      return Suitability(eligible=not blockers, scope_fit=scope_fit, ...)
  ```

- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Commit.**

---

### Task 2: The registry in `WorkflowStore`

**Files:**
- Modify: `workflow/store.py`
- Test: `tests/test_bidder_registry.py`

**Interfaces:**
- Produces: `create_bidder`, `get_bidder`, `list_bidders`, `update_bidder`,
  `delete_bidder`, `rfqs_inviting(bidder_id) -> list[str]`
- **Store invariant owned:** `_bidders` holds exactly the bidders nobody has
  deleted, and **no bidder is deleted while any `ShortlistEntry` references
  it** — so the store never holds a shortlist entry whose `vendor_id` names a
  bidder that is gone.

Third instance of the repository's "nothing is deleted out from under a live
reference" rule, and the reason is identical: `persistence.save` replaces the
document wholesale, so a dangling reference survives the restart. Follow
`delete_project`'s shape exactly, including materialising any list before
iterating and naming the offending references in the refusal.

`update_bidder` follows `update_item`: partial via the caller's
`exclude_unset` dict, rebuilt through the model rather than
`model_copy(update=...)` — which skips validation and would happily store a
`prequal_status` no `PrequalStatus` allows — and `_BIDDER_IMMUTABLE = {"id"}`
enforced in the store as well as by the route's request model.

- [ ] **Step 1: Write the failing test** — create/get/list; a partial update
      leaves absent fields alone; `id` in `changes` raises; delete of an
      unreferenced bidder succeeds; delete of a referenced one raises with the
      RFQ reference in the message; `rfqs_inviting` returns references, sorted.
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Write the implementation.**
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Commit.**

---

### Task 3: Linking a shortlist entry to a registry bidder

**Files:**
- Modify: `workflow/models/rfq.py` (`ShortlistEntry.vendor_id`), `workflow/store.py`
- Test: `tests/test_bidder_registry.py` (new class)

**Interfaces:**
- Consumes: `bidders.evaluate`
- Produces: `add_shortlist_entry(..., vendor_id: str | None = None, as_of: date | None = None)`
- **Store invariant owned:** every `ShortlistEntry` carrying a `vendor_id`
  holds exactly the `vendor_name`, `prequal_status` and `scope_code_fit` that
  the registry and the RFQ implied at the moment it was added — none of the
  three is ever taken from the caller when a `vendor_id` is present.

And its companion rule: **an entry linked to a bidder with blockers exists only
with an `override_reason`.** Both are checks that gate a write, so both live in
the store method, which the route runs inside `locked_update`.

Free-text entries — no `vendor_id` — are untouched. Eligibility can only be
judged against a registry record, so the new rules attach to the link, not to
the call. Every existing test of `add_shortlist_entry` must still pass
unmodified; if one needs editing, the change is wrong.

`_revoke_shortlist_approval` still fires on every add and remove. A linked add
is still an edit to the approved set.

- [ ] **Step 1: Write the failing test** — a linked add copies name, prequal and
      scope fit from the registry and *ignores* contradicting values in the
      call; an unknown `vendor_id` raises `KeyError`; a blocked bidder with no
      override raises `ValueError` naming the blocker; the same add with an
      override succeeds and records it; a linked add revokes approval. Plus one
      test asserting the free-text path is byte-for-byte unchanged.
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Write the implementation.**
- [ ] **Step 4: Run to verify it passes** — and run the whole existing
      `test_workflow_*` set, which must be green without edits.
- [ ] **Step 5: Commit.**

---

### Task 4: Persistence

**Files:**
- Modify: `workflow/persistence.py`
- Test: `tests/test_workflow_persistence.py`

**Interfaces:**
- Produces: `"bidders"` in `to_document`, the matching rebuild in `from_document`
- **Store invariant owned:** `workflow.json` holds exactly the bidders the store
  holds — a bidder deleted in memory does not survive the restart, and a bidder
  created in memory does.

CLAUDE.md names this file's exact failure mode: a field added to
`WorkflowStore.__init__` without a line in *both* functions silently fails to
survive a restart. `VERSION` stays 1 — a document written before this change
has no `"bidders"` key and `from_document` reads it as an empty registry, which
is the correct reading, not a migration.

- [ ] **Step 1: Write the failing test** — round-trip a store with bidders;
      a pre-existing document with no `"bidders"` key loads as an empty
      registry; a `ShortlistEntry.vendor_id` survives the round-trip.
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Write the implementation.**
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Commit.**

---

### Task 5: Registry routes

**Files:**
- Modify: `api/workflow_routes.py`, `tests/test_auth_middleware.py`
- Test: `tests/test_bidder_endpoints.py`

**Interfaces:**
- Produces: `GET/POST /api/workflow/bidders`,
  `GET/PATCH/DELETE /api/workflow/bidders/{bidder_id}`
- **Store invariant owned:** none new — this task must not add one. Every
  decision belongs to Task 2's store methods; the route maps exceptions to
  status codes and decides nothing. A guard that appears here rather than there
  is a read outside the lock, which is the defect class CLAUDE.md records this
  repository shipping twice.

Status mapping, matching the module: 404 unknown id, 409 referenced-bidder
delete, 422 an immutable field in a PATCH. `effective_prequal` and
`invited_count` are computed in the roster response, exactly as `list_projects`
computes its counts, so no screen re-derives them.

`{bidder_id}` joins the probe substitutions in
`test_auth_middleware.py::test_every_api_route_outside_the_allowlist_requires_a_session`.
That test exists to fail when a path parameter is added; adding it there is the
documented extension point, and no new path joins `PUBLIC_PATHS`.

- [ ] **Step 1: Write the failing test** — one per route, plus: the roster
      carries a derived `Expired`; a PATCH with `id` is 422; a DELETE of a
      referenced bidder is 409 and the detail names the RFQ.
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Write the implementation.**
- [ ] **Step 4: Run to verify it passes**, including the auth sweep.
- [ ] **Step 5: Commit.**

---

### Task 6: Candidates, and `vendor_id` on the shortlist route

**Files:**
- Modify: `api/workflow_routes.py`
- Test: `tests/test_bidder_endpoints.py`

**Interfaces:**
- Produces: `GET /api/workflow/rfqs/{rfq_id}/candidates`;
  `ShortlistEntryIn.vendor_id`
- **Store invariant owned:** none new, for Task 5's reason. The eligibility
  check that authorises the shortlist write is Task 3's, inside the store,
  inside the lock.

`as_of` is resolved to today at the route boundary and passed down, keeping
`workflow/bidders.py` pure. `ShortlistEntryIn` keeps `vendor_name`,
`prequal_status` and `scope_code_fit` as fields — they are what the free-text
path posts — and the store is what ignores them when `vendor_id` is present.
The refusal for an ineligible bidder is a 409: the request is well-formed and
both entities exist; the workflow says no.

- [ ] **Step 1: Write the failing test** — candidates lists every bidder with
      suitability and a `shortlisted` flag; an already-shortlisted bidder is
      flagged; posting a `vendor_id` for a blocked bidder is 409 and the detail
      is the blocker sentence; with an override it is 201 and `override_by` is
      the session user.
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Write the implementation.**
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Commit.**

---

### Task 7: The Bidders screen

**Files:**
- Create: `web/src/pages/Bidders.tsx`, `web/src/pages/Bidders.test.tsx`
- Modify: `web/src/App.tsx` (nav), `web/src/api.ts`, `web/src/types.ts`, `web/src/theme.css`

**Interfaces:**
- Produces: the registry screen, and `fetchBidders` / `createBidder` /
  `updateBidder` / `deleteBidder` in `api.ts`
- **Store invariant owned:** none — the browser holds no store. The screen's
  own rule: it renders `effective_prequal` from the server and never recomputes
  expiry locally, so one definition of "expired" exists.

The nav entry goes between `Projects & items` and `RFQ workflow`, in the RFQ
process group; indices below shift by one. Delete surfaces the server's refusal
verbatim rather than pre-disabling the control — the choice `RfqWizard` already
makes for a closed gate, and for the same reason: a disabled control explains
nothing.

Any component test rendering `App` must mock `auth/context`'s `useAuth` with a
**stable** object built once via `vi.hoisted`. CLAUDE.md records what a fresh
literal per call does: the roster refetch loop kills the vitest worker with a
V8 fatal error rather than a failed assertion.

- [ ] **Step 1: Write the failing test** — roster renders; an expired bidder
      shows the derived chip; a refused delete shows the server's sentence.
- [ ] **Step 2: Run `npm test` to verify it fails.**
- [ ] **Step 3: Write the implementation.**
- [ ] **Step 4: Run `npm test` and `npm run build`** — the build type-checks the
      test files, since `tsconfig.app.json` includes `src`.
- [ ] **Step 5: Commit.**

---

### Task 8: Shortlisting becomes a picker

**Files:**
- Modify: `web/src/pages/RfqWizard.tsx`, `web/src/pages/RfqWizard.test.tsx`,
  `web/src/api.ts`, `web/src/types.ts`
- Test: as above

**Interfaces:**
- Consumes: `GET /rfqs/{id}/candidates`
- Produces: the candidate list, the override field, the unregistered-vendor
  disclosure
- **Store invariant owned:** none. The screen's rule: it never sends
  `prequal_status` or `scope_code_fit` alongside a `vendor_id` — those come
  from the registry, and a screen that sent them would imply they were its to
  decide.

- [ ] **Step 1: Write the failing test** — candidates render with blockers; an
      invite for a blocked bidder surfaces the 409; typing an override reason
      lets it through; the free-text disclosure still posts the old shape.
- [ ] **Step 2: Run `npm test` to verify it fails.**
- [ ] **Step 3: Write the implementation.**
- [ ] **Step 4: Run `npm test` and `npm run build`.**
- [ ] **Step 5: Commit.**

---

### Task 9: The demo seed

**Files:**
- Create: `workflow/seed_demo.py`, `tests/test_seed_demo.py`
- Test: as above

**Interfaces:**
- Produces: `build_demo_store(as_of) -> WorkflowStore`, `main(argv) -> int`
- **Store invariant owned:** the store the seed builds holds exactly the
  entities the demo describes and **no RFQ in a stage its own gates would have
  refused** — every seeded RFQ's artifacts satisfy every gate on the forward
  path its history records.

`build_demo_store` is separate from `main` so the test never touches argv or a
subprocess. `main` refuses a store holding any entity unless `--force`: a demo
loader that silently replaces real work is a data-loss bug with a friendly
name. Writing goes through `persistence.save`, so the seed cannot emit a
document the loader would reject.

Ids are fixed literals; dates are computed from `--as-of`. Fixed ids keep a
demo link and a screenshot valid across reseeds; relative dates keep "expiring
in three weeks" true whenever it is run. Names are invented — no real company
appears.

- [ ] **Step 1: Write the failing test** — for every seeded RFQ, replay its
      forward history and assert `check_gate` passes at each edge; `main` on a
      non-empty store returns non-zero and writes nothing; `--force` overwrites;
      ids are identical across two builds; the expired bidder is expired
      relative to `as_of` and the expiring one is inside thirty days.
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Write the implementation.**
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Commit.**

---

### Task 10: Integration — the mutation matrix, and the baselines

**Files:**
- Modify: `tests/test_workflow_persistence.py`, `CLAUDE.md`
- Test: as above

**Interfaces:**
- **Store invariant owned:** across a save/load cycle and a second round of
  mutations, the document holds exactly the registry and shortlist state the
  store holds — no bidder, entry or link outlives its deletion, and no derived
  value is found stored.

- [ ] **Step 1: Write the failing test** — the matrix below, one test per row.
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Fix what it catches.**
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 4b: Two-run mutation matrix.**

| mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|
| a bidder is created, saved, then deleted and saved again | Task 4 — the document holds exactly the store's bidders | the reloaded store has no such bidder |
| a bidder referenced by a shortlist entry is deleted | Task 2 — no delete under a live reference | refused, reason names the RFQ; after reload the entry and bidder both still exist |
| the referencing entry is removed, then the bidder is deleted | Task 2 | succeeds; after reload neither exists |
| a bidder's `prequal_expires_on` passes between run 1 and run 2 | Task 1 — `Expired` is never stored | the stored record is byte-identical; only `effective_prequal` differs, and only because `as_of` moved |
| a bidder is suspended after being shortlisted | Task 3 — the entry's snapshot is from the moment of adding | the existing entry keeps its recorded `prequal_status`; a *new* add of the same bidder is refused without an override |
| a bidder's `trade_categories` is edited to no longer match the RFQ | Task 3 | the existing entry's `scope_code_fit` is unchanged; the candidates view reports the caution |
| a linked entry is added after approval | Task 3 + the existing approval rule | approval is revoked; the reloaded store agrees |
| a bidder is renamed | Task 3 — entries bind by `vendor_id`, never by name | the entry still resolves to the bidder; its recorded `vendor_name` is the name at the time of adding |
| a document written before this change is loaded, mutated, and saved | Task 4 — no migration needed | loads as an empty registry, saves with a `"bidders"` key |
| two adds of the same bidder to the same RFQ | Task 3 | both stored — a duplicate is a user error the screen shows, not a store error; the store does not silently dedupe by name |

      Verify the matrix is real: reintroduce each defect one at a time and
      confirm the intended row fails and nothing else does.

- [ ] **Step 5: Re-measure both baselines.** Run `python -m pytest` on the
      workstation and record the measured row in CLAUDE.md; derive the CI row
      by the documented subtraction rather than editing it independently. Run
      `npm test` under `web/` and update the web count. Add a short section to
      CLAUDE.md for the registry's invariants, next to the workflow ones.
- [ ] **Step 6: Commit.**

---

## Checklist before this plan is approved

- [x] Every task's `Interfaces` block has a **Store invariant owned** bullet
      (Tasks 1, 5, 6, 7 and 8 declare *no* invariant, with the reason — a route
      or a screen that owned one would be holding a guard outside the lock).
- [x] No invariant is claimed twice; the one new stored collection (`_bidders`)
      and the one new field (`ShortlistEntry.vendor_id`) are both claimed.
- [x] The integration task has a mutation matrix; the template's nine rows are
      translated to this feature's collection, as explained at the top.
- [x] Every matrix row names an invariant; every invariant has a row.
- [x] The Rule 3 banner appears above the first reference block.
- [x] The plan states which reference parts are load-bearing and which are
      illustrative.
