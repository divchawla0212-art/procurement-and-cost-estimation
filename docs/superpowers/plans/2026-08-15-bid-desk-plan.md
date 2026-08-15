# Bid Desk — implementation plan

Executes [`../specs/2026-08-15-bid-desk-design.md`](../specs/2026-08-15-bid-desk-design.md)
against the requirements in [`feature-request.md`](../../../feature-request.md).

Written under [`../PLAN-TEMPLATE.md`](../PLAN-TEMPLATE.md). Its three rules are
not ceremony: phase 2 shipped seven defects that survived per-task TDD and seven
honest reviews, and every one of them needed **two runs or two modules** to see.
Bid Desk introduces four accumulating collections — draft shortlist entries, RFQ
documents, MR passages, mail messages — so it is more exposed than phase 2, not
less.

> **Reference code below is intent, not paste-able.** It shows shape and
> reasoning and has not been executed. Read it, then write the implementation
> against the test. Seven of phase 2's reference samples were wrong and two
> became shipped defects.

**Baselines to hold at every commit:** `python -m pytest` → 1694 passed, 3
skipped on a workstation (CI derives to 1674/23); `npm test` under `web/` →
green. No new test may require a provider key.

---

## Store invariants — the register

Every invariant is owned by exactly one task. An invariant nobody claims is the
next C1.

| # | invariant | owner |
|---|---|---|
| **I-A** | `workflow.json` holds exactly the draft shortlist entries whose item still exists | T2 |
| **I-B** | An item's draft holds **exactly one** entry per (vendor_id) or (vendor_name where vendor_id is null) — never two | T2 |
| **I-C** | No RFQ in `workflow.json` is stored at stage `Scoping`, and every migrated RFQ has exactly one appended history entry recording the move | T4 |
| **I-D** | `<ROOT>/rfq-docs/<rfq_id>/` contains exactly the blobs referenced by live `RfqDocument` records — no orphan blobs, no dangling records | T6 |
| **I-E** | `workflow.json` holds exactly the `RfqDocument` records whose RFQ still exists | T6 |
| **I-F** | No eligibility verdict is stored anywhere — not on `RfqDocument`, not on `ShortlistEntry`, not as a key in `workflow.json` | T7 |
| **I-G** | `mr-index.db` holds passages for exactly the currently-issued document revision of each RFQ — a superseded MR contributes no passage | T8 |
| **I-H** | `workflow.json` holds exactly the mail messages whose clarification query still exists | T9 |

I-F is the odd one: it asserts an **absence**, so it passes the moment it is
written. It must be verified the way `test_no_shortlist_entry_stores_the_approvals_it_reports`
was — by adding the field and watching it go red. An absence-assertion nobody has
watched fail is not known to be wired to anything.

---

## T1 — the four-source vendor pool (BD-1)

**Files:** Modify `web/src/pages/ItemDetail.tsx`. Test `web/src/pages/ItemDetail.test.tsx`.

**Interfaces:**
- Consumes: `AvailableBidders` from `fetchAvailableBidders`; `ItemVendorEntry[]` already on the project payload.
- Produces: `PoolRow` union rendered in one table; four toggle chips.
- **Store invariant owned:** none — this task writes nothing. Deliberately first, so the riskiest UI change lands with no persistence to get wrong.

Front-end only. No route changes, no server changes. The registry query is
unchanged; curated rows are filtered client-side from data already fetched.

**Load-bearing, not illustrative:**
- When both registry chips are unticked, `fetchAvailableBidders` is **not called**. It 422s on an empty approver list, and that refusal is right — the browser must contribute no registry rows rather than ask for none.
- Registry chips AND among themselves; curated chips append. These are different operators on purpose.
- No dedupe by name. Two rows for one company, each source-tagged.

- [ ] **Step 1** Failing tests: four chips render; unticking both registry chips issues no request and shows only curated rows; a name present in both halves renders twice; the last chip cannot be unticked; search spans the union.
- [ ] **Step 2** Run — verify they fail for the missing feature, not a typo.
- [ ] **Step 3** Implement.
- [ ] **Step 4** `npm test`; `npm run build`.
- [ ] **Step 5** Browser check — chip row does not wrap into a second line at 1280px, and a curated row's product groups stay capped at two (the 170px-row defect, third instance).
- [ ] **Step 6** Commit.

---

## T2 — the item-owned draft shortlist (BD-2)

**Files:** Create `workflow/models/draft_shortlist.py`. Modify `workflow/store.py`, `workflow/persistence.py`, `api/workflow_routes.py`. Test `tests/test_draft_shortlist.py`, `tests/test_workflow_persistence.py`.

**Interfaces:**
- Consumes: `item_id`, a `DraftShortlistIn` body.
- Produces: `add_draft_shortlist_entry`, `remove_draft_shortlist_entry`, `draft_shortlist`, `clear_draft_shortlist`.
- **Store invariant owned:** **I-A** and **I-B**.

I-B's duplicate check runs **inside `locked_update`**, never in the route. Two
concurrent adds of the same vendor each read "not there yet" and both write. This
is the `api/auth/store.py::grant` shape, and it is the fifth time this repository
has needed it.

I-A needs `delete_item` extended. The cascade materialises its id list **before**
the first deletion — dropping that is a shipped orphan, and `delete_project`
already shows the pattern.

- [ ] **Step 1** Failing tests: add/read/remove by id; a duplicate add is a no-op not a second row; removing by name is impossible (no such method); deleting the item takes its draft; a draft round-trips a save/load; a draft entry for a deleted item never survives a reload.
- [ ] **Step 2** Run — verify.
- [ ] **Step 3** Implement. Persistence: flat array `draft_shortlists`, regrouped by `item_id` on load, `.get` with a default, `VERSION` does **not** move.
- [ ] **Step 4** `python -m pytest`.
- [ ] **Step 5** Commit.

---

## T3 — the item screen sends selections to the draft (BD-2 front end)

**Files:** Modify `web/src/pages/ItemDetail.tsx`, `web/src/api.ts`, `web/src/routes.tsx`. Test `ItemDetail.test.tsx`.

**Interfaces:**
- Consumes: the `selected` Set already in `AvailableVendorList`.
- Produces: `Shortlist selected (N)` posting one entry per vendor, then navigating to the shortlisting screen.
- **Store invariant owned:** none — T2 owns the writes.

Sequential calls, one per vendor, **not** a bulk route. The per-vendor guards are
what make an invitation an attributed act, and this repository already refused a
bulk invitation endpoint for that reason. One refusal fails alone, renders beside
its row, and stays ticked for retry.

- [ ] **Step 1** Failing tests: the control appears with no covering RFQ (today it requires one); a refusal keeps that vendor ticked and the rest succeed; the draft survives a simulated reload — stub the refetch to resolve on a **macrotask**, or the loading state and the resolution batch into one commit and the test passes against the defect.
- [ ] **Step 2–6** as T1.

---

## T4 — remove Scoping (BD-4)

**Files:** Modify `workflow/stages.py`, `workflow/gates.py`, `workflow/models/rfq.py`, `workflow/store.py`, `workflow/persistence.py`, `web/src/pages/RfqWizard.tsx`, `web/src/routes.tsx`. Test `tests/test_workflow_stages.py`, `tests/test_workflow_persistence.py`, `web/src/pages/RfqWizard.test.tsx`, `web/src/routes.test.tsx`.

**Interfaces:**
- Consumes: a stored document possibly containing `"stage": "Scoping"`.
- Produces: an eight-member `Stage`; RFQs defaulting to `SHORTLISTING`.
- **Store invariant owned:** **I-C**.

The migration is **on load, idempotent, append-only**. An RFQ at `Scoping` loads
at `Shortlisting` with one appended history entry. Running load twice must not
append twice — that is the assertion that matters, and it is a two-run test.

`_scoping_exit` is deleted; `_shortlisting_exit` becomes the first gate.
`TRANSITIONS` loses its `Scoping` key **and** `Scoping` as a target — an
orphaned target is still a reachable edge.

- [ ] **Step 1** Failing tests: eight stages in order; `Scoping` is not a member; a new RFQ starts at Shortlisting; a stored Scoping RFQ loads at Shortlisting with exactly one appended entry; loading twice appends once; no transition targets Scoping.
- [ ] **Step 2–5** as T2, plus `npm test`.

---

## T5 — rework the shortlisting step (BD-5) and drop the covering card (BD-3)

**Files:** Modify `web/src/pages/wizard/ShortlistingStep.tsx`, `web/src/pages/ItemDetail.tsx`. Test `ShortlistingStep.test.tsx`, `ItemDetail.test.tsx`.

**Interfaces:**
- Consumes: `RfqDetail`, and the item's draft shortlist.
- Produces: a step whose first block is the approval control.
- **Store invariant owned:** none.

The Registry section and `CandidateList` come out. `UnregisteredVendorRow` stays
behind its disclosure — it is the escape hatch for a vendor nobody registered.
`Approve shortlist` moves **above** the invited-bidders table together with its
status line.

`RFQs covering this item` and its `Raise RFQ` button come off the item screen.
The plain statement of which RFQs cover the item stays; the form does not.

Note the ordering bug this fixes as a side effect: `_shortlisting_exit` requires
included vendor → approval → TBE template, but the old screen rendered approval
**before** the TBE editor, so a top-to-bottom user approved and then edited.

- [ ] **Step 1** Failing tests: no Registry heading; no candidate search; approve control precedes the bidder table in DOM order; the disclosure survives; the item screen has no `Raise RFQ` control and no covering-RFQ form; ~16 existing tests in `RfqWizard.test.tsx` that drive the candidate list must be **deleted, not skipped**, and the deletion recorded in `CLAUDE.md`'s count.
- [ ] **Step 2–6** as T1.

---

## T6 — real RFQ documents (BD-6)

**Files:** Create `workflow/doc_store.py`, `workflow/models/rfq_document.py`, `workflow/safe_extract.py`. Modify `workflow/store.py`, `workflow/persistence.py`, `api/workflow_routes.py`, `web/src/pages/wizard/` (new `RaiseRfqStep.tsx`). Test `tests/test_rfq_documents.py`, `tests/test_safe_extract.py`.

**Interfaces:**
- Consumes: `list[UploadFile]`, optional relative paths, optional category.
- Produces: `RfqDocument` records and blobs on disk.
- **Store invariant owned:** **I-D** and **I-E**.

`workflow/safe_extract.py` is the traversal guard **extracted from**
`procurement/project.py:141-143`, and that call site is changed to use it. A
second copy of a security check is a second thing to forget to fix.

**Load-bearing:** content addressing means two records may share one blob.
Deleting a record deletes the blob **only when no other record shares the hash**.
Deleting unconditionally is data loss that a test with one document cannot see —
it needs two.

**Illustrative:** the exact fan-out depth, the leaf-name convention.

- [ ] **Step 1** Failing tests: multi-file upload; zip expansion; a zip member escaping its directory is refused by name; a folder's relative paths survive; the same bytes twice write one blob and two records; deleting one of those two records leaves the blob; deleting both removes it; deleting an RFQ takes its documents and blobs.
- [ ] **Step 2–6** as T2, plus a browser check that the multi-select and folder inputs actually accept what they claim.

---

## T7 — the eligibility checklist (BD-7)

**Files:** Create `workflow/eligibility.py`. Modify `api/workflow_routes.py`. Test `tests/test_eligibility.py`.

**Interfaces:**
- Consumes: issued documents, submitted documents.
- Produces: `assess(issued, submitted) -> EligibilityVerdict`.
- **Store invariant owned:** **I-F** — no verdict is stored anywhere.

Pure module: no store, no I/O, no clock, like `workflow/bidders.py`.

**Load-bearing:** `missing` lists **every** absent mandatory category, not the
first. And category (c) is required either way — a returned compliance sheet if
one was issued, a deviation list if not — so the reason names *which* is
expected.

- [ ] **Step 1** Failing tests: all three mandatories present → admissible; each one missing individually → refused naming it; two missing → **both** named; (c) with a compliance sheet issued demands the sheet; (c) with none issued demands a deviation list; optional categories never block; **I-F verified by adding a `verdict` field to `RfqDocument` and watching the absence test go red**.
- [ ] **Step 2–5** as T2.

---

## T8 — MR retrieval and grounded answers (BD-8)

**Files:** Create `workflow/passages.py`, `workflow/mr_index.py`, `workflow/clarification_answers.py`, `shared/llm/prompts/mr_answer_v1.txt`. Test `tests/test_mr_index.py`, `tests/test_grounded_answers.py`.

**Interfaces:**
- Consumes: issued `RfqDocument`s; an incoming question.
- Produces: `search(rfq_id, query, k) -> list[Hit]`; `draft_answer(...) -> GroundedAnswer`.
- **Store invariant owned:** **I-G**.

BM25, pure Python, no provider key — CI requires it and Anthropic has no
embedding endpoint, so "reuse our provider" is not on the table.

**Load-bearing:**
- `supported = bool(cited) and set(cited) <= retrieved_ids`. An empty or
  hallucinated citation is **not answered**, however confident the prose.
- The prompt is a versioned file, and a test reads it for verdict-requesting
  words — the guard `test_the_model_is_never_asked_for_a_verdict` already applies
  to vendor suggestions.
- A provider failure escalates to the contractor. It never becomes "no answer
  found in the MR" — an outage and an absence must not look the same.

**Illustrative:** the BM25 constants, the passage length, `k`.

- [ ] **Step 1** Failing tests: a passage answering a question is retrieved; an uncited answer escalates; a fabricated citation escalates; a provider failure escalates and says so; re-issuing the MR at a new revision leaves **no** passage of the superseded one (I-G, a two-run test); the prompt file contains no verdict language.
- [ ] **Step 2–5** as T2.

---

## T9 — mail transport (BD-9) and the mock exchange (BD-10)

**Files:** Create `workflow/mail/transport.py`, `workflow/mail/outbox.py`, `workflow/mail/smtp_imap.py`, `workflow/models/mail.py`, `tools/mock_mail_thread.py`. Test `tests/test_mail_transport.py`.

**Interfaces:**
- Consumes: an `OutboundMail`; a polled mailbox.
- Produces: `send`, `poll`, and a threaded message record per query.
- **Store invariant owned:** **I-H**.

**Load-bearing, and the safety property of the whole task:** `OutboxTransport`
is the default. `SmtpImapTransport` is constructed only when `MAIL_TRANSPORT=smtp`
is explicitly set — absent, empty, `0`, `false` all leave the outbox in place,
read the same way `AUTH_DISABLED` is. **No test, seed or default configuration
can send mail to a real address.**

Mock parties are invented and say so. An invented vendor may carry `Astra` and
**never** `ADNOC` — `test_no_invented_vendor_claims_the_clients_approval` already
guards that, and this is a new door into it.

- [ ] **Step 1** Failing tests: the default transport writes a file and opens no socket; `MAIL_TRANSPORT` unset/empty/`0`/`false` all yield the outbox; a reply threads to its query by `In-Reply-To`; a message whose query is deleted does not survive a reload (I-H); the seeded mock exchange claims no client approval.
- [ ] **Step 2–5** as T2.

---

## T10 — the Vendor List and continuous adequacy (BD-11) — **integration task**

**Files:** Modify `api/workflow_routes.py`, create `web/src/pages/wizard/VendorListStep.tsx`. Test `tests/test_vendor_list.py`, `tests/test_bid_desk_integration.py`.

**Interfaces:**
- Consumes: issued and submitted `RfqDocument`s per shortlisted bidder.
- Produces: `GET /rfqs/{id}/vendor-list`.
- **Store invariant owned:** none new — it defends **I-F** at the boundary.

The column is labelled **`Vendor List`**, asserted as a literal string, because
it is a client requirement and it reads worse to a developer than "Bidders" —
which is exactly how it would get renamed.

### Step 4b — two-run mutation matrix

Every row names an invariant. A row defending nothing is decoration; an
invariant with no row is untested. **Verify by reinstating each defect and
confirming the intended row fails, and that nothing else does.**

| mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|
| a bidder judged inadmissible uploads the missing document | I-F | status flips to admissible with nothing rewritten |
| an admissible bidder's document is deleted | I-F | status flips back; no stale verdict survives |
| the same document is uploaded twice | I-D | one blob, two records |
| one of two records sharing a blob is deleted | I-D | the blob survives |
| an RFQ is deleted between runs | I-E, I-D | no record and no blob outlive it |
| an item is deleted while its draft holds entries | I-A | no draft entry survives |
| the same vendor is added to a draft twice | I-B | one row |
| a draft is adopted by two RFQs | I-B | each RFQ gets one shortlist row, draft intact |
| the MR is re-issued at a new revision | I-G | no passage of the superseded revision is retrievable |
| a clarification query is deleted with mail against it | I-H | no message survives |
| a stored `Scoping` RFQ is loaded twice | I-C | exactly one appended history entry |
| an LLM call fails on run 1, succeeds on run 2 | I-G | run 1 escalates, run 2 answers; the failure was not cached as an answer |
| an LLM call fails on both runs | I-G | a diagnostic is recorded, not silent retry |
| the model returns a response omitting an optional array | I-G | read as "no results", never as failure |

The last three are `PLAN-TEMPLATE`'s required rows (I1, I4, I5) in this phase's
terms. The four before them are this phase's own accumulating collections.

- [ ] **Steps 1–4** as T2.
- [ ] **Step 4b** the matrix above.
- [ ] **Step 5** Update `CLAUDE.md`: both baselines, the new invariants, the removed Scoping stage, and the deleted candidate-list tests.
- [ ] **Step 6** Commit.

---

## Checklist before this plan is considered approved

- [x] Every task's `Interfaces` block has a **Store invariant owned** bullet (or states plainly that it writes nothing).
- [x] No invariant is claimed twice; every new collection is claimed.
- [x] The integration task has a mutation matrix including the required I1/I4/I5 rows plus a row per new accumulating collection.
- [x] Every matrix row names an invariant; every invariant has a row.
- [x] The Rule 3 banner appears above the first reference block.
- [x] The plan states which reference parts are load-bearing and which are illustrative.
