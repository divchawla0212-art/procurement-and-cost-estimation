# The Astra list as a subset of the client AVL — design

**Date:** 2026-08-13
**Branch:** `rfq-platform-phase-1`
**Follows:** [`2026-08-13-bidder-registry-and-shortlisting-design.md`](2026-08-13-bidder-registry-and-shortlisting-design.md)

---

## The problem

The bidder registry is a **master document**. It is imported once — from a
client's Approved Vendor List export — and then read by every project and every
RFQ in the platform. `WorkflowStore._bidders` is already organisation-wide for
exactly this reason: a prequalification is a fact about a company, and holding
it per project would mean re-entering and re-approving the same company for
every one of them. Nothing in this design changes that; it is written down here
because the rule below only makes sense against it. The flag is a property of a
company in the master registry, computed once and read identically by every
project's RFQ.

Within that master registry, Astra's own approved list is meant to be a
**subset** of the client's AVL: you draw from your own list first, but your own
list is drawn from theirs.

Today that subset relationship holds only by accident of construction, and
nothing detects a breach:

1. **It is true only where the AVL import made it true.**
   `avl_import.parse_avl` writes `approved_by = ["ADNOC"]` and appends
   `"Astra"`, so an imported bidder is `["ADNOC"]` or `["ADNOC", "Astra"]` and
   never Astra alone. The twelve invented demo companies hold the same property
   by hand. Neither is a rule — both are a habit that happens to have been kept.

2. **One live path breaks it right now.** The registry's **New bidder** form
   hardcodes `approved_by: ['Astra']`
   ([`Bidders.tsx:227`](../../../web/src/pages/Bidders.tsx)) — and
   correctly so, because a bidder somebody types in by hand is on *your* list,
   not the client's, and claiming an unverified ADNOC approval is the one thing
   that form must not do quietly. So every hand-created bidder is Astra-approved
   and not client-approved, and nothing anywhere says so.

3. **Nothing reads `approved_by` for meaning.** `workflow/bidders.py evaluate()`
   never looks at it. On the registry screen it is a badge row and a filter
   dropdown; in the RFQ Shortlisting step it is a badge row. A vendor the client
   has never approved is visually indistinguishable from one they have, at the
   moment somebody decides to invite them.

## What this builds

A single derived sentence — "this bidder is not on the client's Approved Vendor
List" — computed in one pure function and surfaced on the two screens where the
question arises: the registry, and RFQ shortlisting.

It is a **caution, not a blocker.** Nothing becomes ineligible, no
`override_reason` is demanded, and no vendor becomes unrecordable.

The eight stages, `TRANSITIONS`, every existing gate, and the shape of
`Bidder` itself are untouched.

---

## 1. The rule — `workflow/bidders.py`

```python
CLIENT_APPROVER = ADNOC


def missing_client_approval(bidder: Bidder) -> str | None:
    """None when the bidder is on the client's Approved Vendor List.

    Derived, never stored, for the same reason `PrequalStatus` has no
    "Expired" member: a stored copy is wrong the moment `approved_by` is
    edited, and keeping it honest needs a sweep job nobody has written.
    """
    if CLIENT_APPROVER in bidder.approved_by:
        return None
    held = ", ".join(bidder.approved_by)
    return (
        f"{bidder.name} is not on the {CLIENT_APPROVER} Approved Vendor List"
        + (f" — approved by {held} only." if held else " and has no approval recorded.")
    )
```

**One rule, not two.** The predicate is simply "the client approver is absent",
so it catches an Astra-only bidder *and* one carrying no approval at all. A
vendor nobody has approved is at least as worth flagging as one only Astra
approved, and a single rule cannot be sidestepped by leaving `approved_by`
empty.

**The tail says which case it is.** A shared rule that produced one sentence for
both states would make "we approved them, the client has not" read identically
to "nobody has approved them", and those call for different actions.

**Why this module.** `bidders.py` is the pure module — no store, no I/O, no
clock — and it is already where `effective_prequal` derives a value the screens
must not compute for themselves. This is the same kind of value and belongs
beside it.

**Why not the API boundary.** `_bidder_payload` and the candidates route would
each build the sentence, and two definitions of one rule drift the first time
the wording changes.

**Why not a field on `Bidder`.** Rejected for the reason quoted in the
docstring above.

## 2. Constants move — `workflow/models/bidder.py`

`ADNOC = "ADNOC"` and `ASTRA = "Astra"` move from `workflow/avl_import.py` to
`workflow/models/bidder.py`. `avl_import` imports them from there, so it
continues to export both names and every existing
`from workflow.avl_import import ADNOC, ASTRA` — in `workflow/seed_demo.py`,
`tests/test_avl_import.py` and `tests/test_seed_demo.py` — keeps working
unchanged.

This move is load-bearing, not tidying. `avl_import` imports `openpyxl`, and
`bidders.py`'s docstring promises a module with no I/O; importing the constants
from their current home would pull a spreadsheet library into it. Defining a
second `"ADNOC"` string literal in `bidders.py` instead would leave two
definitions of one organisation's name to drift apart.

## 3. Suitability — `workflow/bidders.py evaluate()`

The sentence is appended to `cautions`. Never to `blockers`.

**Position is fixed, not incidental.** It goes after the prequalification
caution and before the scope-mismatch one, so the list always reads
prequalification → approval → scope. `cautions` is rendered in order and
asserted by index in the existing tests, so leaving the position to whichever
line happens to be written first is how a later edit silently reorders a
screen.

- `eligible` is `not blockers`, so it is **unchanged in every case**. This is
  the assertion that carries the whole decision and it is tested directly.
- `add_shortlist_entry` still demands `override_reason` only for a bidder with
  real blockers — a hold, or a prequalification that is lapsed, suspended,
  under review or declined. Inviting a non-client-approved vendor stays a
  one-click act.
- The candidates route's ordering (`api/workflow_routes.py`, blocked bidders
  sorted last) is untouched, because nothing new is blocked.

`ShortlistingStep.tsx:330` already renders `suitability.cautions` generically,
so this surface needs **no React change at all**.

## 4. Registry payload — `api/workflow_routes.py`

`_bidder_payload` gains one key:

```python
"approval_caution": missing_client_approval(bidder),   # str | None
```

It joins `effective_prequal` and `invited_count` as the third value the
docstring describes as one "a screen must not compute for itself" — and that
docstring says "the two values", so it is updated to say three rather than left
counting wrong. The name
matches the value's other home in `Suitability.cautions` on purpose: sameness of
name signals sameness of value, and the two must never be allowed to say
different things about one bidder.

It appears on both `GET /bidders` and `GET /bidders/{id}`, since both run
through `_bidder_payload`.

## 5. Registry screen — `web/src/pages/Bidders.tsx`

`BidderSummary` in `web/src/types.ts` gains `approval_caution: string | null`.

`BidderCard` renders it in the same `<p className="warn">` slot the `on_hold`
line uses, immediately below it, so a bidder that is both on hold and
unapproved shows both facts in reading order. It is rendered verbatim from the
server, never reconstructed in the browser — the rule the screen already keeps
for `effective_prequal`, and for the same reason: a second definition in the
client drifts from the first.

Nothing about the filters, the search, or the `RENDER_CAP` of 60 changes.

## 6. What deliberately does not change

- **The New bidder form keeps `approved_by: ['Astra']`.** It is correct, and it
  is now precisely the case this flag exists to catch, rather than a bug to fix.
- **Nothing enforces the subset.** An Astra-first vendor stays recordable — the
  store refuses nothing new. A vendor Astra qualified before the client listed
  them is a real state, and the remedy for it is a renewal conversation, not a
  rejected form.
- **`astra_approves` is untouched.** It remains the invented, deterministic
  demo subset documented in `avl_import.py`, and remains a subset by
  construction because it only ever runs over rows read from the client's
  sheet.
- **No per-project client mapping.** See the limitation below.

## 7. Known limitation, stated rather than solved

`CLIENT_APPROVER` is one constant, so "the client" is ADNOC for the whole
platform. `Bidder.approved_by`'s own comment observes that "which one matters
depends on whose project the RFQ is for", and `Bidders.tsx` deliberately builds
its approver filter from the data rather than hardcoding two names, so a
platform-wide constant does cut against that grain.

It is still the right first step. A per-project rule needs a client → approver
mapping that does not exist: `Project.client` is free text
(`"Al Dhafra Petroleum"`, `"Gulf Ports Authority"`), and inferring an approving
organisation from it would be guessing. Onboarding a second client's AVL is a
one-line change to this constant plus that mapping, not a redesign of the rule.

## 8. Testing

| file | what it covers |
|---|---|
| `tests/test_bidder_suitability.py` | The caution fires for an Astra-only bidder and for an empty `approved_by`, with the two different sentences; it is absent when the client approver is present; and **`eligible` stays `True` in every one of those cases.** That last assertion is the point of choosing caution over blocker, so it is asserted directly rather than inferred from an empty `blockers` list. |
| `tests/test_bidder_endpoints.py` | The key is present and correct on both `GET /bidders` and `GET /bidders/{id}`, and a bidder carrying it is still accepted onto a shortlist with no `override_reason`. |
| `tests/test_avl_import.py` | No bidder produced by `parse_avl` ever carries the caution — asserted over the in-memory `openpyxl` workbook so it runs in CI, and over the real export behind `needs_real_avl` where that file is present. |
| `tests/test_seed_demo.py` | None of the twelve invented companies carries it, so the demo registry shows the flag nowhere until somebody adds a bidder by hand. |
| `web/src/pages/Bidders.test.tsx` | The warn line renders when the server sends a sentence, and does not when it sends `null`. |

`ShortlistingStep` needs no new test for rendering — its existing cautions
assertion already covers the list — but `workflow-fixtures.ts` gains the field
so the type checks.

**Baselines.** `CLAUDE.md`'s two rows move. The workstation row is re-measured
by running `python -m pytest`, and the CI row is derived from it by the
subtraction that file documents, never edited independently. None of the new
Python tests touches a fixture directory or a provider key, so every one of them
lands in both rows. The web suite's count moves too and is stated separately.
