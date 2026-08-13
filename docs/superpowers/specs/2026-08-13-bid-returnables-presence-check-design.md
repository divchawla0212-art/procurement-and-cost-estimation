# Bid returnables presence check — design

**Date:** 2026-08-13
**Branch:** `rfq-platform-phase-1`
**Follows:** [`2026-08-13-bidder-registry-and-shortlisting-design.md`](2026-08-13-bidder-registry-and-shortlisting-design.md)

---

## The problem

When bids arrive, somebody has to answer one question before anything else can
happen: **did this bidder send what we asked for?** Today that question has a
half-built answer and no consequence.

The half that works: [`vdrl_summary`](../../../workflow/store.py) counts
mandatory VDRL lines received, [`missing_vdrl_lines`](../../../workflow/store.py)
lists the ones that did not arrive, and both reach the screen as
`vdrl_received` / `vdrl_required` / `vdrl_missing`. The tally is already honest
— it counts only `state == "received"`, so an `unreadable` file reads as a gap
rather than a delivery.

Three things are missing:

1. **The flag decides nothing.** `select_bids` never consults the VDRL, and
   `_bids_received_exit` only asks whether *a* selection exists. A bid missing
   every mandatory returnable can be selected and taken to evaluation with no
   refusal recorded anywhere.
2. **What is required is retyped per RFQ.** VDRL lines are authored by hand
   (`TT-001`, `DS-002`, `WP-001`), so "must have" means whatever the buyer
   happened to type. A returnable nobody remembered to list cannot be missed.
3. **Bids cannot be received through the application at all.** `register_bid`,
   `record_vdrl_receipt` and `select_bids` have no HTTP routes; their only
   callers are [`seed_demo.py`](../../../workflow/seed_demo.py) and the tests.

## What this builds

A **fixed, organisation-wide set of returnables**, and a **hard refusal**: a
bid missing any must-have cannot be selected for evaluation. Plus the three
routes that let a real bid be recorded in the first place.

## What this deliberately does not build

**This stage checks presence, not content.** Whether a returned document is
*filled in* — a populated compliance sheet, a completed client datasheet — is
the evaluation stage's question, not this one. Two items from the original
checklist are therefore out of scope here:

- Compliance to MR/specification, and the deviation-list alternative when no
  compliance sheet was issued.
- The filled client datasheet.

Both are deferred to bid evaluation. This matters structurally: the deferred
compliance item is the only one whose requirement is *conditional* (required
only if a sheet was issued) and *disjunctive* (satisfiable by a deviation list
instead). `VdrlLine.mandatory` is a static bool and can express neither. By
deferring it, this design needs no change to the model at all.

There is also no override. A missing must-have is a refusal, full stop —
unlike [`add_shortlist_entry`](../../../workflow/store.py), where inviting a
blocked bidder is permitted with an attributed `override_reason`. The two
cases differ: inviting a bidder against a stale prequalification is a judgement
a buyer is entitled to make and defend, whereas evaluating a bid whose
commercial offer never arrived is not a judgement, it is an error.

The eight stages, `TRANSITIONS`, and every existing gate are otherwise
untouched.

---

## 1. `workflow/returnables.py` — the fixed set

A new pure module: no store, no I/O, no clock. The same shape as
[`workflow/bidders.py`](../../../workflow/bidders.py), and for the same reason
— the rule is a function of its inputs, so it should be testable without
building a store.

```python
class Returnable(BaseModel):
    doc_code: str
    title: str
    must_have: bool

ORG_RETURNABLES: tuple[Returnable, ...] = (
    Returnable(doc_code="TECH-OFFER",     title="Technical offer",       must_have=True),
    Returnable(doc_code="COMM-OFFER",     title="Commercial offer",      must_have=True),
    Returnable(doc_code="BID-EVAL",       title="Bid evaluation sheet",  must_have=True),
    Returnable(doc_code="TECH-DATASHEET", title="Technical datasheet",   must_have=False),
    Returnable(doc_code="DRAWINGS",       title="Drawings",              must_have=False),
    Returnable(doc_code="DOCUMENTS",      title="Supporting documents",  must_have=False),
    Returnable(doc_code="CATALOGUES",     title="Catalogues & brochures",must_have=False),
)
```

### The set is a constant, never copied into an RFQ

The alternative — seeding these seven as `VdrlLine`s on `create_rfq` — was
considered and rejected. It puts two kinds of line into `_vdrl` that are
indistinguishable by inspection, needs a new guard on `remove_vdrl_line` to
stop a must-have being deleted, and lets each RFQ's copies drift from the org
set over time.

Keeping it a constant means there is nothing to seed, nothing to forget,
nothing to remove, and no change to `to_document` / `from_document`. It also
closes the gap the hard refusal would otherwise leak through: a rule enforced
against a hand-authored list is only as strong as whoever authored the list,
and a buyer who forgets to add "Commercial offer" would get no refusal at all.

### Per-RFQ mandatory lines still count

The required set for one RFQ is `ORG_RETURNABLES` **union** its own
`VdrlLine`s. A per-RFQ line with `mandatory=True` is a must-have too, so a
project-specific returnable — a weld procedure, a type-test certificate —
hard-refuses exactly like `COMM-OFFER`. This is the existing mechanism
continuing to work, not a second one.

### Speaking codes, not `XX-NNN`

These codes appear verbatim in a refusal message a buyer has to act on.
"Missing COMM-OFFER" is actionable; "missing CO-001" is not — and `CO-002`
already means a coating datasheet in the demo seed, so numeric codes in this
namespace actively mislead. Per-RFQ lines keep whatever convention their author
prefers; only the org set is constrained.

---

## 2. `Responsiveness` — the flag, computed

```python
class Responsiveness(BaseModel):
    missing_must_haves: list[str]
    missing_optional: list[str]
    unreadable: list[str]
    complete: bool          # not missing_must_haves
```

Nothing is stored. There is no `flagged` or `missing_info` field on `Bid`, for
the reason [`bidders.py`](../../../workflow/bidders.py) gives for
`effective_prequal`: a stored verdict is wrong the moment the next receipt is
recorded, and would need a sweep nobody has written to stay true. The verdict
is derived from the receipts every time it is asked for.

### `unreadable` is reported separately

The model already distinguishes `unreadable` from `not_received`
([`bid.py`](../../../workflow/models/bid.py)) — a corrupt file is "a delivery
failure to chase, not a refusal to submit" — but `missing_vdrl_lines` collapses
them into one list. They are different jobs for the person reading the screen:
one is chased with an email, the other is not chaseable at all. Both still
block selection; only the remedy differs.

An `unreadable` must-have therefore appears in **both** `missing_must_haves`
and `unreadable`. The first is why the bid cannot be selected; the second is
what to do about it.

### It replaces `missing_vdrl_lines`

`store.missing_vdrl_lines` is subsumed and removed rather than left beside the
new computation. Two functions answering "what is missing?" against different
required sets — one org-aware, one not — is how the two disagree, and the
disagreement would surface as a bid that reads complete on screen and refuses
at selection.

### What `vdrl_required` counts

The tally's denominator becomes the **must-have** set: `ORG_RETURNABLES` where
`must_have` is true, union the RFQ's own `mandatory=True` lines. Optional
returnables are reported through `missing_optional` and stay out of the tally,
so `vdrl_received == vdrl_required` means exactly "this bid can be selected"
rather than "this bid sent everything anyone might have wanted".

---

## 3. The refusal — `store.select_bids`

The existing validations stay (RFQ exists, at least one bid, non-blank
rationale, every bid belongs to this RFQ). One is added, after them:

> For every bid named in the call, compute its `Responsiveness`. If any is
> incomplete, raise, naming each offending vendor and its missing codes.

### It refuses the whole call

Not a filtered subset. `select_bids` is one decision with one rationale and one
named author; silently narrowing it to the bids that happened to qualify would
attribute to that person a selection they did not make. The repository's
convention is that a blocked write leaves everything untouched —
`locked_update` writes only on a clean exit, so the raise leaves
`workflow.json` exactly as it was.

### The message names the whole criterion

Per the rule [`gates.py`](../../../workflow/gates.py) states and the phase 1
plan once broke: a refusal must say what would unblock it. Naming the vendor
without the codes, or the codes without the vendor, leaves the reader guessing
on a multi-bid RFQ.

    Northern Steel Fabrication LLC is missing COMM-OFFER, BID-EVAL.
    Gulf Coating Industries is missing BID-EVAL.
    These bids cannot be taken to evaluation until the documents arrive.

### The check lives in the store, not the route

It is a read that gates a write, so it belongs in the same critical section as
the write. This is the same rule as the auth store, `delete_item`,
`delete_project`, `delete_bidder` and `add_shortlist_entry` — the fifth time
this repository has needed it. The route runs the whole method inside
`persistence.locked_update`.

### `_bids_received_exit` is unchanged

It still asks only whether a selection exists. It does not need to re-check
completeness, because a selection can now only exist if every bid in it was
complete. Two gates asserting the same fact is how they drift apart.

---

## 4. Two defects the refusal makes consequential

Both exist today. Both are tolerable while the tally is advisory and are not
once it gates award.

### A receipt cannot be retracted

[`_received_codes`](../../../workflow/store.py) is a set comprehension over
appended receipts, filtered on `state == "received"`. Tick `TECH-OFFER`
received by mistake, then record it `not_received`, and the set still contains
it: the bid reads complete forever, and now selects on that basis.

**Fix:** current state is the **latest** receipt per `doc_code`, not any
receipt ever. Receipts stay append-only — the history of what was recorded when
is preserved, matching the rule stage history follows — but the answer to "did
this arrive?" is the most recent statement, not the most favourable one.

### Receipts are not validated against the required set

`record_vdrl_receipt` checks only that the bid exists. A receipt for a
`doc_code` that is neither an org returnable nor a line on that bid's RFQ is
stored and silently never counts, so a typo is indistinguishable on screen from
a document that genuinely never arrived.

**Fix:** reject a `doc_code` outside `ORG_RETURNABLES ∪ vdrl_for(rfq_id)`,
naming the codes that are valid.

---

## 5. Routes — `api/workflow_routes.py`

| Method | Path | Store call |
|---|---|---|
| `POST` | `/rfqs/{rfq_id}/bids` | `register_bid` |
| `POST` | `/rfqs/{rfq_id}/bids/{bid_id}/receipts` | `record_vdrl_receipt` |
| `POST` | `/rfqs/{rfq_id}/bids/select` | `select_bids` |

Each wraps its store call in `persistence.locked_update`. `KeyError` → 404;
`ValueError` → 409, carrying the refusal sentence as `detail`, which is how
every other gated write on this router already reports a refusal.

None is added to `middleware.PUBLIC_PATHS`. `test_auth_middleware.py`'s route
sweep walks every registered route, so the new `bid_id` path parameter must be
added to that test's probe substitutions — the assertion exists to force
exactly that.

### The read side

`get_rfq`'s per-bid payload replaces `vdrl_missing` with the four fields of
`Responsiveness`. `vdrl_received` / `vdrl_required` stay: the tally answers
"how far along is this bid", which the verdict alone does not.

---

## 6. Blast radius

### The demo seed

Two changes, both in [`seed_demo.py`](../../../workflow/seed_demo.py):

1. **`CO-002` becomes `mandatory=False`.** JAT-02 deliberately selects both
   bids while the second is `not_received` on `CO-002`, which is declared
   mandatory — under the new rule that call raises and `build_demo_store` fails
   outright. Flipping the flag keeps the visible tally gap the seed's own
   comment exists to demonstrate, keeps both bids advancing, and keeps the
   recorded rationale about pricing the coating deviation at TBE accurate.
2. **JAT-02's two bids gain the three must-have receipts.** Without them the
   seed cannot select, for the same reason.

JAT-01 is left incomplete on purpose. It never calls `select_bids`, so it
survives the new rule untouched and becomes the demo's worked example of a
flagged bid.

### The web app

`BidsCard` in [`RfqDetail.tsx`](../../../web/src/pages/RfqDetail.tsx) and the
matching types. A flagged bid reads as flagged, with its missing codes; an
unreadable document reads as a chase rather than a gap.

### `CLAUDE.md`

Both test-count rows move. Per that file's own instruction the workstation row
is **measured** and the CI row derived from it by the documented subtraction —
not edited independently.

---

## 7. Testing

New pure-module tests for `returnables.py` need no store at all, which is the
point of keeping it pure: the boundary cases — an `unreadable` must-have, a
per-RFQ mandatory line alongside the org set, an optional line missing — are
assertable directly.

Store tests cover the refusal from both sides, the retraction fix across two
receipts, and the `doc_code` validation. Endpoint tests cover 404 / 409 and the
auth sweep. The integration task carries a two-run mutation matrix per
[`PLAN-TEMPLATE.md`](../PLAN-TEMPLATE.md), because the retraction fix is
exactly the class of defect a single run cannot see: it needs a second receipt
recorded after the first.
