# RFQ clarifications — query register and addenda — design

**Date:** 2026-08-13
**Branch:** `rfq-platform-phase-1`
**Follows:** [`2026-08-13-bidder-registry-and-shortlisting-design.md`](2026-08-13-bidder-registry-and-shortlisting-design.md)

---

## The problem

`Stage.CLARIFICATIONS` (RFQ-04) exists as a stage code and nothing else.

- `TRANSITIONS` routes `ISSUED → CLARIFICATIONS → BIDS_RECEIVED`, and neither
  edge appears in `_GATES`, so `check_gate` falls through to `_passed()` on
  both. Entering and leaving the stage is unconditional.
- There is no model, no store collection, no route. Grepping `api/` for
  "clarif" returns nothing.
- The wizard step renders one sentence: *"Nothing to capture here yet —
  technical queries arrive with the agent work."*
- `workflow/seed_demo.py` parks `rfq_ruu02` here with the transition reason
  `"Two technical queries raised on the IO list"`. That string is the entire
  substance of the stage today: an audit line asserting that two queries exist,
  with nowhere for them to be.

So an RFQ sits at Clarifications while the query round happens in email, and a
buyer clicks through to Bids Received whenever they judge it done. The two
facts that decide whether a tender was run fairly — what each bidder asked, and
whether every bidder got the same answer — are not in the system.

## What this builds

A **clarification query register** and an **addendum** record, both owned by one
RFQ, plus the exit gate that makes them load-bearing.

The eight stages and `TRANSITIONS` are untouched. One new entry appears in
`_GATES`. `set_technical_package`'s refusal of a frozen package is untouched;
`issue_addendum` becomes the one sanctioned door through it.

### Explicitly out of scope

- **Setting an RFQ's original bid due date.** That belongs to the Issued stage.
  This spec derives the *operative* due date from issued addenda and adds no
  field to `RfqRecord` — see §7. An RFQ with no addendum has no due date, and
  the screen says so rather than inventing one.
- **Notifying bidders.** Circulation is a recorded property of an answer, not a
  message this system sends. There is no mail transport in this repository and
  inventing one here would put a side effect inside `locked_update`.
- **Parsing MOM/clarification documents into queries.** The extraction pipeline
  already classifies `mom` documents in `procurement/`; wiring that to this
  register is a later phase and is named in the roadmap.

---

## 1. Where the code lives

The registry phase established the shape and this reuses it exactly.

| file | role |
|---|---|
| `workflow/models/clarification.py` | `ClarificationQuery`, `Addendum` — data only |
| `workflow/clarifications.py` | **pure**: no store, no I/O, no clock |
| `workflow/store.py` | the guarded writes |
| `workflow/gates.py` | `_clarifications_exit` |
| `workflow/persistence.py` | two collections, in three places each |
| `api/workflow_routes.py` | seven routes |
| `web/src/pages/wizard/` | the four wizard steps, extracted |

`workflow/clarifications.py` is pure for the same reason `workflow/bidders.py`
is: the state of a query is a function of its fields, both boundaries have to
be assertable from both sides, and a helper that reaches for a store cannot be
tested without building one.

---

## 2. `ClarificationQuery` — `workflow/models/clarification.py`

```python
QueryCategory = Literal["Technical", "Commercial"]

class ClarificationQuery(BaseModel):
    id: str                          # clq_xxxxxxxx
    rfq_id: str
    number: str                      # "TQ-001" / "CQ-001"
    raised_by_entry_id: str          # ShortlistEntry.id
    raised_by_name: str              # snapshot
    raised_on: date
    category: QueryCategory
    question: str
    answer: str | None = None
    answered_by: str | None = None
    answered_at: datetime | None = None
    restricted_reason: str | None = None
    withdrawn_reason: str | None = None
    withdrawn_by: str | None = None
    withdrawn_at: datetime | None = None
```

### State is computed, not stored

There is no `status` field. State is derived in `workflow/clarifications.py`:

```
withdrawn_at is not None  → "Withdrawn"
answer is not None        → "Answered"
otherwise                 → "Open"
```

This is the same call the bidder registry made when it refused an `"Expired"`
member of `PrequalStatus`. A stored status is a fourth thing that has to agree
with three fields that already say it; the first write that updates one and not
the other makes the register lie, and nothing sweeps it. Deriving it means
`state()` cannot disagree with the record it reads.

The order matters and is asserted: a query that was answered and *then*
withdrawn reads as Withdrawn. Withdrawal is the later act and the one that
takes it out of the count.

### `raised_by_entry_id` links to the shortlist, not to the registry

A query is raised against *this* RFQ by somebody invited to *this* RFQ, and
`ShortlistEntry.id` is what that means. A `vendor_id` would also name bidders
who were never invited, and a free-text name would let a query be attributed to
a company that is not bidding.

`raised_by_name` beside it is a **snapshot**, for the identical reason
`ShortlistEntry` snapshots `vendor_name`: the register records who asked at the
time they asked. A later rename in the registry does not rewrite it.

### Numbering: `max + 1`, per RFQ, per category

`TQ-` for Technical, `CQ-` for Commercial, independent sequences, zero-padded
to three digits. Addenda number `ADD-01` on the same rule with two digits — a
tender that runs to a hundred addenda has a problem no padding fixes, and the
narrower field is what buyers write on the documents.

The next number is one past the **highest** existing number of that category,
not one past the count. Withdrawn queries keep their numbers and stay in the
register, so counting would reissue a number that has already been quoted to a
bidder in writing. Numbers are never reused and never renumbered.

---

## 3. The circulation invariant

**Answering a query circulates it to the whole included shortlist. Withholding
it requires an attributed reason.**

`restricted_reason is None` means circulated. There is no third state and no
`circulate: bool` — a boolean makes a restricted answer indistinguishable from
an oversight, and the register stops being evidence of anything.

`answer_query` refuses a `restricted_reason` that is empty or whitespace. This
is the shape `override_reason` on `ShortlistEntry` and `rationale` on
`select_bids` already use: the deviation from the fair default is legal, and it
is legal only as a recorded, attributed act.

An answer to one bidder that the others never see is the classic tender-fairness
failure. Making the honest path the default and the exception cost a sentence is
the only arrangement that survives contact with a deadline.

### Re-answering is allowed; answering a withdrawn query is not

A buyer revising an answer is normal practice — the revision is issued to the
same audience. `answer_query` on an already-answered query overwrites the answer
and re-stamps `answered_by`/`answered_at`, and may change the restriction in
either direction (subject to the reason rule above).

`answer_query` on a withdrawn query is refused. The query is out of the round;
answering it would put a live answer under a dead question.

---

## 4. `Addendum` — the sanctioned exception to a frozen package

```python
class Addendum(BaseModel):
    id: str                          # add_xxxxxxxx
    rfq_id: str
    number: str                      # "ADD-01"
    supersedes_revision: str
    revision: str
    summary: str
    attachments: list[Attachment]
    arising_from_query_ids: list[str] = []
    bid_due_date: date | None = None
    issued_at: datetime | None = None
    issued_by: str | None = None
```

Draft and issued are derived the same way state is: `issued_at is None` is a
draft. No `status` field.

`arising_from_query_ids` may be empty. A buyer-initiated addendum — a client
change, a corrected datasheet — is legitimate and requiring a query to justify
it would only produce fabricated queries.

### The invariant keeps its teeth and gains one named door

CLAUDE.md states: *a frozen technical package is immutable.*
`set_technical_package` keeps refusing one, unchanged. `issue_addendum` is the
only path that supersedes it, exactly as retender and renegotiate are the only
documented backward edges — the rule is not weakened, it is given one exit that
says who used it and why.

`issue_addendum` performs four reads before it writes, and refuses on any:

1. **The package must exist and be frozen.** There is nothing to supersede
   otherwise, and an addendum to an unfrozen package is an edit with extra steps.
2. **`supersedes_revision` must equal the package's current revision.** A draft
   cut against Rev. B, when another addendum has since taken the package to
   Rev. C, is stale; issuing it would silently roll the package back and its
   summary would describe changes against a revision no bidder holds. The
   refusal names both revisions.
3. **`revision` must differ from `supersedes_revision`.** An addendum that does
   not move the revision gives bidders no way to tell which package they hold.
4. **Every attachment must name a definite revision.** Issuing re-freezes, and
   `freeze_package` already refuses to freeze without one — the same check,
   reused rather than restated, because two copies of a rule diverge.

All four are reads that gate a write, so all four live in the store method. The
route runs that whole method inside `persistence.locked_update`, which makes
them one critical section with the write. This is the same rule the auth store,
`delete_item` and `add_shortlist_entry` each state, and check 2 is the one that
would actually be lost by splitting them: two concurrent issues of two drafts,
both reading "current revision is B" outside the lock, would both write.

On success `issue_addendum`:

- replaces the `TechnicalPackage` with one at `revision`, carrying the
  addendum's attachment list, `frozen_at` set to now and `frozen_by` to the
  issuer;
- stamps `issued_at` and `issued_by` on the addendum.

The superseded revision stays on record as the addendum's own
`supersedes_revision`, so the addenda list *is* the revision trail. No new
history mechanism, and `RfqRecord.history` stays what it is — a record of stage
transitions, which an addendum is not.

### Drafts are editable; issued addenda are not

`update_addendum` is partial (`model_dump(exclude_unset=True)`, rebuilt through
the model rather than `model_copy(update=...)`, which skips validation) and
refuses `id`, `rfq_id`, `number`, `issued_at` and `issued_by`. It refuses
outright once issued.

`delete_addendum` works only on a draft. An issued addendum went to the bidders;
deleting it would erase a document they hold.

---

## 5. The exit gate

`_GATES` gains its fourth entry: `(CLARIFICATIONS, BIDS_RECEIVED)`.

Blocked when any query is Open, **or** any addendum is in draft.

```
Bids cannot be opened while there are queries still open (TQ-002, CQ-001) and
addenda still in draft (ADD-02). Answer or withdraw each query, and issue or
delete each draft addendum.
```

Both halves appear when both are true. `_scoping_exit`'s docstring records that
naming only the nearer half is the mistake this codebase already shipped: a
reader told "queries are open" fixes those, retries, and is refused again for a
reason nobody mentioned. The sentence names the whole exit criterion.

Opening bids while a bidder is still waiting on an answer, or against a package
change that was drafted and never issued, are the two failures this prevents.
Both are recoverable in principle and unrecoverable in practice: the bids are
already open.

Only the forward edge is gated. `check_gate` short-circuits on `is_backward`
first, so a retender out of Evaluation is unaffected — a forward gate that
blocked a recovery would leave a stuck RFQ with no way out.

---

## 6. Nothing deleted out from under a live reference — instance four

`remove_shortlist_entry` is refused while that entry has raised **any** query,
answered or withdrawn, and the refusal names the numbers:

```
This vendor cannot be removed from the shortlist: they raised TQ-003, TQ-007.
The clarification register is the record of who took part.
```

`delete_item`, `delete_project` and `delete_bidder` are instances one to three,
and the reason is identical: `persistence.save` replaces the document wholesale,
so a query left pointing at a removed entry is not merely wrong in memory — it
survives the restart as a dangling reference.

Answered and withdrawn queries block removal too, not only open ones. A bidder
who took part in the clarification round is part of its record; if they decline
to bid they remain on the shortlist as a non-bidder, which is the true fact.
Erasing them would make the register read as though they had never asked.

`raise_query` is the matching guard on the other side: the entry must exist,
belong to this RFQ, and be `included`. A query attributed to a vendor who was
never invited is the record this register exists to make impossible.

---

## 7. Bid due date — derived, not stored

`store.current_bid_due_date(rfq_id)` returns the `bid_due_date` of the most
recently issued addendum that names one, or `None`.

No field is added to `RfqRecord`. The original due date is set at Issued, which
this phase does not touch (see *Out of scope*), so a field here would be
half-owned: written by addenda, never written by the stage that actually decides
it, and wrong for every RFQ that has no addendum. Deriving it means the value is
either a real recorded extension or honestly absent.

"Most recently issued" is by `issued_at`, not by list position or by number.
Drafts do not count — a due date nobody has been told about is not a due date.

---

## 8. Store methods

```python
raise_query(rfq_id, entry_id, question, category, raised_on, query_id=None)
answer_query(rfq_id, query_id, answer, by, restricted_reason=None)
withdraw_query(rfq_id, query_id, reason, by)
queries_for(rfq_id) -> list[ClarificationQuery]

draft_addendum(rfq_id, revision, summary, attachments,
               arising_from_query_ids=None, bid_due_date=None, addendum_id=None)
update_addendum(rfq_id, addendum_id, changes)
issue_addendum(rfq_id, addendum_id, by)
delete_addendum(rfq_id, addendum_id)
addenda_for(rfq_id) -> list[Addendum]
current_bid_due_date(rfq_id) -> date | None
```

`query_id` and `addendum_id` are optional pinned ids through `_with_id`, for the
same single reason every other entity has them: `workflow/seed_demo` is
rehearsed and reseeded, and a demo whose queries change identity between builds
makes "answer this one" hit a different row each time.

Refusals, all of them reads inside the method that the route runs inside
`locked_update`:

| method | refuses when |
|---|---|
| `raise_query` | RFQ unknown; entry unknown, not this RFQ's, or not `included`; blank question |
| `answer_query` | query unknown or not this RFQ's; blank answer; withdrawn; `restricted_reason` present but blank |
| `withdraw_query` | query unknown; blank reason; already withdrawn |
| `draft_addendum` | RFQ unknown; no package, or package not frozen; blank summary; `revision` equal to the package's current revision; a named `arising_from_query_ids` member that is not this RFQ's |
| `update_addendum` | already issued; immutable field named |
| `issue_addendum` | already issued; the four checks of §4 |
| `delete_addendum` | already issued |
| `remove_shortlist_entry` | the entry raised any query (§6) |

`draft_addendum` records `supersedes_revision` from the package at draft time —
the caller does not supply it, because a caller-supplied "what I am superseding"
is a claim, and the store already knows the answer.

---

## 9. Persistence

`WorkflowStore.__init__` gains

```python
self._queries: dict[str, list[ClarificationQuery]] = {}   # keyed by rfq_id
self._addenda: dict[str, list[Addendum]] = {}             # keyed by rfq_id
```

and `to_document`/`from_document` each gain one line, flat lists regrouped on
the `rfq_id` every record already carries — the existing pattern, which keeps
the grouping key impossible to disagree with the record it groups.

`from_document` reads both with `.get(key, [])`, so a document written before
this phase loads as an RFQ with no queries and no addenda. That is the correct
reading of a missing key rather than a migration, which is why `VERSION` does
not move — the same call the registry made.

CLAUDE.md's warning applies directly: a field added to `__init__` without a
matching line in **both** directions silently fails to survive a restart. The
mutation matrix in §11 is what catches it.

---

## 10. Routes and web

### Seven routes

```
POST   /api/workflow/rfqs/{rfq_id}/queries                            201
POST   /api/workflow/rfqs/{rfq_id}/queries/{query_id}/answer
POST   /api/workflow/rfqs/{rfq_id}/queries/{query_id}/withdraw
POST   /api/workflow/rfqs/{rfq_id}/addenda                            201
PATCH  /api/workflow/rfqs/{rfq_id}/addenda/{addendum_id}
POST   /api/workflow/rfqs/{rfq_id}/addenda/{addendum_id}/issue
DELETE /api/workflow/rfqs/{rfq_id}/addenda/{addendum_id}              204
```

`answer`, `withdraw` and `issue` take `user: User = Depends(current_user)` and
attribute to `user.email`, like `approve_shortlist` and
`freeze_technical_package`. `KeyError → 404`, `ValueError → 409`, malformed
body → 422 by the request model.

There is **no GET for either collection.** `get_rfq` already returns every
artifact its gates read, and the gate now reads both, so its payload gains
`queries`, `addenda` and `bid_due_date`. One fetch, which is how the wizard
already works.

These paths are not on `middleware.PUBLIC_PATHS` and must not be.
`test_auth_middleware.py`'s route sweep needs `{query_id}` and `{addendum_id}`
added to its probe substitutions — CLAUDE.md records that the sweep's assertion
exists precisely to force that addition.

### Web

`web/src/pages/RfqWizard.tsx` is 902 lines holding four steps. A real
Clarifications step takes it past 1 100, so the steps move first, mechanically
and without behaviour change, into `web/src/pages/wizard/`:
`ScopingStep.tsx`, `ShortlistingStep.tsx`, `IssuedStep.tsx`, and the shared
`AttachmentTable`. `RfqWizard.tsx` keeps the shell — stepper, step blurbs, gate
card, stage history — and `ClarificationsStep.tsx` lands beside its siblings
rather than at the bottom of a file nobody can hold in their head.

The step renders:

- **Register**, open first, then answered, then withdrawn. Columns: number,
  bidder, category, raised, question, answer, circulation. A restricted answer
  shows its reason and who restricted it; a circulated one shows "circulated".
- **Raise a query** against a bidder **picked from the included shortlist** —
  a `<select>`, not a text field. The same lesson as the registry: a shortlist
  chosen from a list rather than typed from memory.
- **Answer**, per open query: a textarea, plus a "restrict this answer" tick
  that reveals a required reason input. Progressive disclosure, the same shape
  `CandidateRow` uses for `override_reason`.
- **Withdraw**, per open query: a reason input and a button. Not a bare button —
  the store refuses without a reason and a control that cannot succeed is worse
  than no control.
- **Addenda**: number, revision `from → to`, summary, due date, status. Drafts
  get Edit / Issue / Delete; issued rows are read-only and say who issued them.
- **The operative bid due date**, when one exists, with the addendum that set it.

`RfqDetail.tsx` — the read-only screen for an RFQ past Clarifications — gains
the register read-only. It is what Evaluation argues from.

The transition button stays enabled when the gate is closed, and surfaces the
gate's own sentence on refusal. That is the existing rule in `RfqWizard`'s
docstring and this step does not become the exception to it.

---

## 11. Testing

| file | covers |
|---|---|
| `tests/test_clarifications.py` | the pure module and the store guards: state derivation including answered-then-withdrawn, `max+1` numbering across a withdrawal, both circulation boundaries, all four `issue_addendum` checks including the stale-revision race, draft-only edit and delete, `current_bid_due_date` ordering by `issued_at` |
| `tests/test_clarification_endpoints.py` | the seven routes: status codes, attribution to the session user, 404/409/422 mapping |
| `tests/test_workflow_stages.py` | `_clarifications_exit` open/draft/both/clear, and that the backward edges stay ungated |
| `tests/test_workflow_persistence.py` | seven new rows on the two-run mutation matrix — raise, answer, withdraw, draft, update, issue, delete |
| `tests/test_auth_middleware.py` | the two new path-parameter substitutions |
| `tests/test_seed_demo.py` | the seeded register |
| `web/src/pages/wizard/ClarificationsStep.test.tsx` | the register, the restrict-reason disclosure, the draft/issued split |

The mutation matrix is the one that matters most. Every defect this repository
has shipped in this subsystem needed two runs or two modules to see, and a
collection added to `__init__` without both persistence lines is exactly that
shape of bug: every single-run test passes.

### Seed demo

`rfq_ruu02`'s transition reason already claims *"Two technical queries raised on
the IO list"*. It will now create them — one answered and circulated, one still
open — so the claim and the data agree. A second RFQ gets an issued addendum
(so the revision trail is visible) and a third a draft one (so the closed gate
is visible on screen rather than only in a test).

This is invented demo data and is free to be, unlike the imported AVL: the
constraint CLAUDE.md places on `workflow/avl_import.py` is that facts about
**real named companies** are never embellished. Queries attributed to those
companies inside an obviously fictional project are demo scaffolding of the same
kind as the projects and RFQs already there.

### Baselines

Both CLAUDE.md rows are updated once at the end: the workstation row
**measured** on this branch with `data/`, `pdftotext` and an ingested
`projects/` present, and the CI row **derived** from it by the documented
subtraction — `1379 = 1397 - 4 - 3 - 2 - 9`. The two rows are never edited
independently; that is how they drifted apart before.
