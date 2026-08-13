# Bid returnables presence check — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A bid missing any organisation-wide must-have returnable cannot be selected for evaluation, and bids can be received through the API for the first time.

**Architecture:** A new pure module `workflow/returnables.py` holds the fixed org set and computes a `Responsiveness` verdict from an RFQ's VDRL lines plus one bid's receipts. `WorkflowStore` consults it in three places — the read-side tally, receipt validation, and a hard refusal inside `select_bids`. Three new routes wrap the existing store calls in `persistence.locked_update`. Nothing is stored that is derived.

**Tech Stack:** Python 3.12, pydantic v2, FastAPI, pytest. React + TypeScript + vitest for the web change.

**Spec:** [`2026-08-13-bid-returnables-presence-check-design.md`](../specs/2026-08-13-bid-returnables-presence-check-design.md)

## Global Constraints

- Presence only. No task inspects document *content*; "filled in" belongs to the evaluation stage.
- No override path. A missing must-have is a refusal, not a warning with an escape hatch.
- Nothing derived is stored. No `flagged` / `missing_info` field reaches `Bid` or `workflow.json`.
- Every decision that gates a write lives inside the store method the route runs within `persistence.locked_update` — never in the route.
- Refusal messages name the whole exit criterion: which vendor, and which codes.
- `workflow/returnables.py` stays pure — no store, no I/O, no clock — like `workflow/bidders.py`.
- Tests are key-free. Run with `python -m pytest` from the repo root; `npm test` under `web/`.

## A note on `PLAN-TEMPLATE.md` Rule 2

The template's nine required mutation rows describe the **procurement extraction
pipeline** — document revisions, prompt-version constants, LLM outages,
re-extraction. This subsystem has none of those: no model call, no documents on
disk, no re-extraction. Four of the nine transfer by analogy and appear in the
Task 8 matrix (a second statement about an already-recorded fact; a required
line removed after receipts exist; a failure after a success; a failure on both
runs). The other five are inapplicable, and are listed as such rather than
silently dropped.

The phase-specific rows matter more here, because `_receipts` is an
append-only accumulating collection whose *current state* is a derived reading
— exactly the C1 shape, and the reason the retraction defect exists at all.

---

## File structure

| File | Responsibility |
|---|---|
| `workflow/returnables.py` | **new.** The fixed org set; the pure verdict. |
| `workflow/store.py` | Adopts the verdict: tally, receipt validation, selection refusal. |
| `api/workflow_routes.py` | Three new routes; per-bid payload carries the verdict. |
| `workflow/seed_demo.py` | Fixture repair so the demo still builds. |
| `web/src/types.ts`, `web/src/pages/RfqDetail.tsx` | The flag on screen. |
| `tests/test_returnables.py` | **new.** Pure-module tests, no store. |
| `tests/test_workflow_store.py` | Store-side rules and the repaired fixtures. |
| `tests/test_workflow_endpoints.py` | The three routes. |
| `tests/test_workflow_persistence.py` | Repaired fixture; the mutation matrix. |
| `CLAUDE.md` | Both test-count rows. |

### Existing `select_bids` callers that this plan breaks

Every one of these selects a bid with no must-have receipts, so each raises the
moment Task 4 lands. They are repaired **in Task 4**, not left for Task 8:

| call site | repair |
|---|---|
| [`tests/test_workflow_store.py:471`](../../../tests/test_workflow_store.py) | `complete_receipts` helper before selecting |
| [`tests/test_workflow_store.py:486`](../../../tests/test_workflow_store.py) | same |
| [`tests/test_workflow_stages.py:222`](../../../tests/test_workflow_stages.py) | same |
| [`tests/test_workflow_persistence.py:66`](../../../tests/test_workflow_persistence.py) | fixture gains a second, complete bid — see Task 4 |
| [`workflow/seed_demo.py:492`](../../../workflow/seed_demo.py) | Task 6 |

Two other call sites must keep the refusal they already assert, which fixes the
**order** of validations in `select_bids`: rationale and belongs-to are checked
**before** completeness, so `test_selection_without_a_rationale_is_rejected`
(`match="rationale"`) and `test_selecting_a_bid_from_another_rfq_is_rejected`
(`match="does not belong"`) keep passing unchanged.

### A decision this plan makes that the spec did not cover

**An existing `BidShortlist` is never re-validated.** If a mandatory VDRL line
is added, or a receipt retracted, *after* a selection was recorded, the stored
selection stands. It is an attributed decision with an author, a rationale and a
timestamp; recomputing it later would retroactively unmake an act somebody
signed. A *new* `select_bids` call is refused on the new facts. Task 8's matrix
has a row for this.

---

## Task 1: The fixed set and the pure verdict

**Files:**
- Create: `workflow/returnables.py`
- Test: `tests/test_returnables.py`

**Interfaces:**
- Consumes: `VdrlLine` from `workflow.models.rfq`; `VdrlReceipt`, `ReceiptState` from `workflow.models.bid`
- Produces:
  - `ORG_RETURNABLES: tuple[Returnable, ...]`
  - `current_states(receipts: Sequence[VdrlReceipt]) -> dict[str, ReceiptState]`
  - `required(lines: Sequence[VdrlLine]) -> tuple[list[str], list[str]]`
  - `valid_codes(lines: Sequence[VdrlLine]) -> set[str]`
  - `evaluate(lines: Sequence[VdrlLine], receipts: Sequence[VdrlReceipt]) -> Responsiveness`
- **Store invariant owned:** none — this module is pure and touches no store. Per
  `PLAN-TEMPLATE.md`'s own constraint ("if it can be checked without loading a
  snapshot, it is not a store invariant"), claiming one here would be false. The
  invariants this module's logic serves are owned by Tasks 2, 3 and 4.

Two rules in this module are load-bearing and easy to get wrong.

**The org set wins over a per-RFQ line of the same code.** Without this, a buyer
adds `COMM-OFFER` with `mandatory=False` and defeats the entire fixed set —
which is precisely the leak the design exists to close. A per-RFQ line whose
code repeats an org code is ignored, not merged and not allowed to downgrade.

**Current state is the latest receipt, not the most favourable.** `current_states`
folds the append-only list in arrival order, so the last statement about a
`doc_code` wins. This is what makes retraction possible in Task 2.

- [ ] **Step 1: Write the failing test**

```python
"""The fixed organisation-wide returnables, and whether one bid sent them.

Pure-module tests: no store, no I/O. The boundary cases that matter are an
unreadable must-have (a gap that is also a chase), a per-RFQ mandatory line
alongside the org set, and a per-RFQ line that tries to downgrade an org
must-have.
"""
import pytest

from workflow import returnables
from workflow.models.bid import VdrlReceipt
from workflow.models.rfq import VdrlLine


def line(doc_code: str, mandatory: bool = True) -> VdrlLine:
    return VdrlLine(rfq_id="rfq_x", doc_code=doc_code, title=doc_code,
                    doc_type="Doc", mandatory=mandatory)


def receipt(doc_code: str, state: str) -> VdrlReceipt:
    return VdrlReceipt(bid_id="bid_x", doc_code=doc_code, state=state)


def all_must_haves() -> list[VdrlReceipt]:
    must, _ = returnables.required([])
    return [receipt(code, "received") for code in must]


def test_the_org_set_has_exactly_three_must_haves():
    must, optional = returnables.required([])
    assert must == ["TECH-OFFER", "COMM-OFFER", "BID-EVAL"]
    assert optional == ["TECH-DATASHEET", "DRAWINGS", "DOCUMENTS", "CATALOGUES"]


def test_a_bid_with_every_must_have_is_complete():
    verdict = returnables.evaluate([], all_must_haves())
    assert verdict.complete is True
    assert verdict.missing_must_haves == []


def test_a_missing_must_have_is_reported_and_blocks():
    verdict = returnables.evaluate([], all_must_haves()[:2])
    assert verdict.complete is False
    assert verdict.missing_must_haves == ["BID-EVAL"]


def test_an_unreadable_must_have_is_both_a_gap_and_a_chase():
    """A corrupt file is a delivery failure to chase, not a delivery. It blocks
    selection like an absent one, but the remedy differs, so it is reported
    twice — once as why, once as what to do."""
    receipts = all_must_haves()[:2] + [receipt("BID-EVAL", "unreadable")]
    verdict = returnables.evaluate([], receipts)
    assert verdict.complete is False
    assert verdict.missing_must_haves == ["BID-EVAL"]
    assert verdict.unreadable == ["BID-EVAL"]


def test_a_per_rfq_mandatory_line_is_a_must_have_too():
    verdict = returnables.evaluate([line("WP-001")], all_must_haves())
    assert verdict.complete is False
    assert verdict.missing_must_haves == ["WP-001"]


def test_a_per_rfq_optional_line_never_blocks():
    verdict = returnables.evaluate([line("NICE-001", mandatory=False)], all_must_haves())
    assert verdict.complete is True
    assert verdict.missing_optional == [
        "TECH-DATASHEET", "DRAWINGS", "DOCUMENTS", "CATALOGUES", "NICE-001",
    ]


def test_a_per_rfq_line_cannot_downgrade_an_org_must_have():
    """The whole point of a fixed set: adding COMM-OFFER as optional must not
    make it optional, or the refusal is defeated by a typo in the VDRL screen."""
    must, _ = returnables.required([line("COMM-OFFER", mandatory=False)])
    assert must == ["TECH-OFFER", "COMM-OFFER", "BID-EVAL"]
    assert must.count("COMM-OFFER") == 1


def test_the_latest_receipt_wins_not_the_most_favourable():
    """Retraction. A document ticked received by mistake must be un-tickable,
    because under the new rule it gates award."""
    receipts = all_must_haves() + [receipt("TECH-OFFER", "not_received")]
    verdict = returnables.evaluate([], receipts)
    assert verdict.complete is False
    assert verdict.missing_must_haves == ["TECH-OFFER"]


def test_a_retraction_can_itself_be_reversed():
    receipts = all_must_haves() + [
        receipt("TECH-OFFER", "not_received"),
        receipt("TECH-OFFER", "received"),
    ]
    assert returnables.evaluate([], receipts).complete is True


def test_valid_codes_span_the_org_set_and_the_rfq():
    codes = returnables.valid_codes([line("WP-001")])
    assert "COMM-OFFER" in codes
    assert "WP-001" in codes
    assert "NOT-A-CODE" not in codes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_returnables.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'workflow.returnables'`

- [ ] **Step 3: Write minimal implementation**

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the
> implementation against the test.
>
> **Load-bearing:** the org-set-wins rule in `required`; last-write-wins in
> `current_states`; `unreadable` appearing in both `missing_must_haves` and
> `unreadable`. **Illustrative:** the exact titles, and whether `Returnable` is
> a pydantic model or a `NamedTuple`.

```python
"""What every bidder must return, and whether one bid returned it.

Pure: no store, no I/O, no clock — the same shape as `workflow/bidders.py`, and
for the same reason. The rule is a function of its inputs, so every boundary is
assertable without building a store.

**This module answers presence, never content.** Whether a returned document is
filled in is the evaluation stage's question; conflating the two is what would
force `VdrlLine.mandatory` to grow conditions it cannot express.
"""
from typing import Sequence

from pydantic import BaseModel

from workflow.models.bid import ReceiptState, VdrlReceipt
from workflow.models.rfq import VdrlLine


class Returnable(BaseModel):
    doc_code: str
    title: str
    must_have: bool


# Fixed organisation-wide. Deliberately a constant rather than seeded copies on
# each RFQ: a rule enforced against a hand-authored list is only as strong as
# whoever authored the list, and there is no refusal at all for a returnable
# nobody remembered to add.
#
# Speaking codes, because these appear verbatim in a refusal a buyer has to act
# on — and because `CO-002` already means a coating datasheet in the demo seed.
ORG_RETURNABLES: tuple[Returnable, ...] = (
    Returnable(doc_code="TECH-OFFER", title="Technical offer", must_have=True),
    Returnable(doc_code="COMM-OFFER", title="Commercial offer", must_have=True),
    Returnable(doc_code="BID-EVAL", title="Bid evaluation sheet", must_have=True),
    Returnable(doc_code="TECH-DATASHEET", title="Technical datasheet", must_have=False),
    Returnable(doc_code="DRAWINGS", title="Drawings", must_have=False),
    Returnable(doc_code="DOCUMENTS", title="Supporting documents", must_have=False),
    Returnable(doc_code="CATALOGUES", title="Catalogues & brochures", must_have=False),
)

_ORG_CODES = {r.doc_code for r in ORG_RETURNABLES}


class Responsiveness(BaseModel):
    """`complete` is `not missing_must_haves`, spelled out so a caller does not
    have to know that — the same courtesy `Suitability.eligible` extends."""

    missing_must_haves: list[str]
    missing_optional: list[str]
    unreadable: list[str]
    complete: bool


def current_states(receipts: Sequence[VdrlReceipt]) -> dict[str, ReceiptState]:
    """The most recent statement about each doc_code — not the most favourable.

    Receipts are append-only history, which is the audit trail of who said what
    and when. This collapses that history to current state so a mis-tick can be
    corrected by recording the correction, rather than being permanent.
    """
    states: dict[str, ReceiptState] = {}
    for receipt in receipts:
        states[receipt.doc_code] = receipt.state
    return states


def required(lines: Sequence[VdrlLine]) -> tuple[list[str], list[str]]:
    """`(must_have_codes, optional_codes)` for one RFQ.

    The org set first, then the RFQ's own lines. **A per-RFQ line repeating an
    org code is ignored**: it must not duplicate the code, and above all it must
    not downgrade a must-have to optional, which would defeat the fixed set from
    the VDRL screen.
    """
    must = [r.doc_code for r in ORG_RETURNABLES if r.must_have]
    optional = [r.doc_code for r in ORG_RETURNABLES if not r.must_have]
    for line in lines:
        if line.doc_code in _ORG_CODES:
            continue
        (must if line.mandatory else optional).append(line.doc_code)
    return must, optional


def valid_codes(lines: Sequence[VdrlLine]) -> set[str]:
    """Every code a receipt may name for this RFQ."""
    must, optional = required(lines)
    return set(must) | set(optional)


def evaluate(
    lines: Sequence[VdrlLine], receipts: Sequence[VdrlReceipt]
) -> Responsiveness:
    must, optional = required(lines)
    states = current_states(receipts)

    missing_must = [code for code in must if states.get(code) != "received"]
    # An unreadable must-have appears here *and* below: the first says why the
    # bid cannot be selected, the second says what to do about it.
    unreadable = [code for code, state in states.items() if state == "unreadable"]

    return Responsiveness(
        missing_must_haves=missing_must,
        missing_optional=[code for code in optional if states.get(code) != "received"],
        unreadable=sorted(unreadable),
        complete=not missing_must,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_returnables.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Commit**

```bash
git add workflow/returnables.py tests/test_returnables.py
git commit -m "feat: a fixed set of returnables nobody has to remember to ask for"
```

---

## Task 2: The store reads the verdict

**Files:**
- Modify: `workflow/store.py` — `_received_codes` (delete), `vdrl_summary`, `missing_vdrl_lines` (delete), new `responsiveness_for`
- Test: `tests/test_workflow_store.py`

**Interfaces:**
- Consumes: `returnables.evaluate`, `returnables.required` from Task 1
- Produces: `WorkflowStore.responsiveness_for(bid_id: str) -> Responsiveness`; `vdrl_summary(bid_id) -> tuple[int, int]` unchanged in signature, changed in denominator
- **Store invariant owned:** a bid's received set contains exactly the doc_codes
  whose **most recent** receipt is `received` — no code whose latest statement
  is `not_received` or `unreadable`, and no code with no receipt at all.

`missing_vdrl_lines` is deleted rather than left beside `responsiveness_for`.
Two functions answering "what is missing?" against different required sets —
one org-aware, one not — is how they come to disagree, and the disagreement
would surface as a bid that reads complete on screen and refuses at selection.

The tally's denominator changes: `vdrl_required` becomes the **must-have** count
(org must-haves ∪ the RFQ's `mandatory=True` lines). Optional returnables report
through `missing_optional` and stay out of it, so `received == required` means
exactly "this bid can be selected".

Two existing tests assert the old denominator and must be updated, not deleted:
`test_an_unreadable_document_is_not_a_received_one` and
`test_optional_vdrl_lines_do_not_count_against_a_bid`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_workflow_store.py`, and update the two named above:

```python
def complete_receipts(store, rfq_id: str, bid_id: str) -> None:
    """Record every must-have of this RFQ as received, for one bid.

    A helper rather than four lines in each test: after the returnables rule
    lands, every test that reaches a selection needs this, and a test that is
    about something else should not read as though it were about receipts.

    Takes `rfq_id` explicitly rather than looking it up through `store._bids`,
    so the helper never reaches into a private collection.
    """
    from workflow import returnables
    must, _ = returnables.required(store.vdrl_for(rfq_id))
    for code in must:
        store.record_vdrl_receipt(bid_id, doc_code=code, state="received")


def test_a_receipt_can_be_retracted():
    """The defect this fixes: a set over appended receipts made any `received`
    permanent, so a mis-tick could not be undone — and it now gates award."""
    store = make_store()
    rfq = seed_rfq(store)
    bid = store.register_bid(rfq.id, vendor_name="Galfar", headline_price_aed=1)
    store.record_vdrl_receipt(bid.id, doc_code="TECH-OFFER", state="received")
    assert "TECH-OFFER" not in store.responsiveness_for(bid.id).missing_must_haves

    store.record_vdrl_receipt(bid.id, doc_code="TECH-OFFER", state="not_received")

    assert "TECH-OFFER" in store.responsiveness_for(bid.id).missing_must_haves
    assert len(store.receipts_for(bid.id)) == 2, "history is append-only"


def test_the_tally_counts_must_haves_only():
    store = make_store()
    rfq = seed_rfq(store)
    store.add_vdrl_line(rfq.id, doc_code="WP-001", title="WPS",
                        doc_type="Doc", mandatory=True)
    store.add_vdrl_line(rfq.id, doc_code="NICE-001", title="Nice",
                        doc_type="Doc", mandatory=False)
    bid = store.register_bid(rfq.id, vendor_name="Galfar", headline_price_aed=1)
    complete_receipts(store, rfq.id, bid.id)

    assert store.vdrl_summary(bid.id) == (4, 4), "3 org must-haves + WP-001"


def test_an_unreadable_document_is_not_a_received_one():
    store = make_store()
    rfq = seed_rfq(store)
    store.add_vdrl_line(rfq.id, doc_code="GA-001", title="GA",
                        doc_type="Doc", mandatory=True)
    bid = store.register_bid(rfq.id, vendor_name="Petrofac", headline_price_aed=1)
    store.record_vdrl_receipt(bid.id, doc_code="GA-001", state="unreadable")

    verdict = store.responsiveness_for(bid.id)
    assert "GA-001" in verdict.missing_must_haves
    assert verdict.unreadable == ["GA-001"]
    assert [r.state for r in store.receipts_for(bid.id)] == ["unreadable"]


def test_optional_vdrl_lines_do_not_count_against_a_bid():
    store = make_store()
    rfq = seed_rfq(store)
    store.add_vdrl_line(rfq.id, doc_code="OPT-001", title="Nice to have",
                        doc_type="Doc", mandatory=False)
    bid = store.register_bid(rfq.id, vendor_name="Petrofac", headline_price_aed=1)
    complete_receipts(store, rfq.id, bid.id)

    assert store.vdrl_summary(bid.id) == (3, 3)
    assert store.responsiveness_for(bid.id).complete is True
    assert "OPT-001" in store.responsiveness_for(bid.id).missing_optional
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_workflow_store.py -v -k "retracted or tally or unreadable or optional_vdrl"`
Expected: FAIL — `AttributeError: 'WorkflowStore' object has no attribute 'responsiveness_for'`

- [ ] **Step 3: Write minimal implementation**

> **Reference code below is intent, not paste-able.**
> **Load-bearing:** deleting `_received_codes` and `missing_vdrl_lines` outright;
> the denominator being must-haves only. **Illustrative:** the exact arithmetic
> in `vdrl_summary`.

```python
from workflow import returnables

    def responsiveness_for(self, bid_id: str) -> Responsiveness:
        """Computed on every call, never stored. A stored verdict is wrong the
        moment the next receipt lands — the reasoning `effective_prequal`
        already follows for expiry."""
        bid = self._bids.get(bid_id)
        if bid is None:
            raise KeyError(f"Unknown bid: {bid_id}")
        return returnables.evaluate(self.vdrl_for(bid.rfq_id), self.receipts_for(bid_id))

    def vdrl_summary(self, bid_id: str) -> tuple[int, int]:
        """Returns (received, required) over the **must-have** set, so
        `received == required` means exactly "this bid can be selected"."""
        bid = self._bids.get(bid_id)
        if bid is None:
            raise KeyError(f"Unknown bid: {bid_id}")
        must, _ = returnables.required(self.vdrl_for(bid.rfq_id))
        missing = self.responsiveness_for(bid_id).missing_must_haves
        return len(must) - len(missing), len(must)
```

Delete `_received_codes` and `missing_vdrl_lines` entirely.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_workflow_store.py -v`
Expected: PASS. `test_selection_*` still fail — Task 4 repairs them.

- [ ] **Step 5: Commit**

```bash
git add workflow/store.py tests/test_workflow_store.py
git commit -m "fix: the latest receipt decides, not the most favourable one"
```

---

## Task 3: A receipt must name a real returnable

**Files:**
- Modify: `workflow/store.py` — `record_vdrl_receipt`
- Test: `tests/test_workflow_store.py`

**Interfaces:**
- Consumes: `returnables.valid_codes` from Task 1
- Produces: `record_vdrl_receipt` unchanged in signature; raises `ValueError` on an unknown code
- **Store invariant owned:** `_receipts[bid_id]` contains exactly receipts whose
  `doc_code` is a returnable required of that bid's RFQ at the time it was
  recorded — no receipt naming a code that was never asked for.

Today a typo is stored and silently never counts, so on screen it is
indistinguishable from a document that genuinely never arrived. Harmless while
the tally was advisory; not harmless now that it gates award.

- [ ] **Step 1: Write the failing test**

```python
def test_a_receipt_for_an_unknown_code_is_rejected():
    store = make_store()
    rfq = seed_rfq(store)
    bid = store.register_bid(rfq.id, vendor_name="Galfar", headline_price_aed=1)
    with pytest.raises(ValueError, match="TECH-OFFERR"):
        store.record_vdrl_receipt(bid.id, doc_code="TECH-OFFERR", state="received")
    assert store.receipts_for(bid.id) == []


def test_a_receipt_may_name_a_per_rfq_line():
    store = make_store()
    rfq = seed_rfq(store)
    store.add_vdrl_line(rfq.id, doc_code="WP-001", title="WPS",
                        doc_type="Doc", mandatory=True)
    bid = store.register_bid(rfq.id, vendor_name="Galfar", headline_price_aed=1)
    store.record_vdrl_receipt(bid.id, doc_code="WP-001", state="received")
    assert [r.doc_code for r in store.receipts_for(bid.id)] == ["WP-001"]


def test_the_refusal_names_the_valid_codes():
    """A refusal that does not say what would unblock it leaves the reader
    nothing to act on — the rule `gates.py` states."""
    store = make_store()
    rfq = seed_rfq(store)
    bid = store.register_bid(rfq.id, vendor_name="Galfar", headline_price_aed=1)
    with pytest.raises(ValueError, match="COMM-OFFER"):
        store.record_vdrl_receipt(bid.id, doc_code="WRONG", state="received")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_workflow_store.py -v -k "unknown_code or per_rfq_line or valid_codes"`
Expected: FAIL — `DID NOT RAISE <class 'ValueError'>`

- [ ] **Step 3: Write minimal implementation**

> **Reference code below is intent, not paste-able.**
> **Load-bearing:** the check precedes the append, so a rejected receipt leaves
> `_receipts` untouched. **Illustrative:** the message wording.

```python
    def record_vdrl_receipt(
        self, bid_id: str, doc_code: str, state: ReceiptState, revision: str | None = None
    ) -> VdrlReceipt:
        bid = self._bids.get(bid_id)
        if bid is None:
            raise KeyError(f"Unknown bid: {bid_id}")
        valid = returnables.valid_codes(self.vdrl_for(bid.rfq_id))
        if doc_code not in valid:
            raise ValueError(
                f"{doc_code} is not a returnable required of this RFQ. "
                f"Valid codes: {', '.join(sorted(valid))}."
            )
        receipt = VdrlReceipt(bid_id=bid_id, doc_code=doc_code, state=state, revision=revision)
        self._receipts.setdefault(bid_id, []).append(receipt)
        return receipt
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_workflow_store.py -v -k "unknown_code or per_rfq_line or valid_codes"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add workflow/store.py tests/test_workflow_store.py
git commit -m "fix: a typo'd doc code no longer reads as a missing document"
```

---

## Task 4: The refusal

**Files:**
- Modify: `workflow/store.py` — `select_bids`
- Modify: `tests/test_workflow_store.py`, `tests/test_workflow_stages.py`, `tests/test_workflow_persistence.py` — repair the five broken call sites
- Test: `tests/test_workflow_store.py`

**Interfaces:**
- Consumes: `WorkflowStore.responsiveness_for` from Task 2
- Produces: `select_bids` unchanged in signature; raises `ValueError` naming each incomplete vendor and its missing codes
- **Store invariant owned:** `_bid_shortlists` contains exactly selections in
  which every named bid had every must-have received **at the moment of
  selection** — no selection recorded over an incomplete bid.

The check goes **after** the existing validations, so the two tests that assert
a different refusal keep getting it. It refuses the whole call rather than
filtering to the bids that qualify: `select_bids` is one decision with one
rationale and one named author, and silently narrowing it would attribute to
that person a selection they did not make.

`_bids_received_exit` is deliberately **not** changed. It still asks only
whether a selection exists, because a selection can now only exist if every bid
in it was complete. Two gates asserting the same fact is how they drift apart.

- [ ] **Step 1: Write the failing test**

```python
def test_a_bid_missing_a_must_have_cannot_be_selected():
    store = make_store()
    rfq = seed_rfq(store)
    bid = store.register_bid(rfq.id, vendor_name="Galfar", headline_price_aed=1)
    store.record_vdrl_receipt(bid.id, doc_code="TECH-OFFER", state="received")

    with pytest.raises(ValueError, match="COMM-OFFER"):
        store.select_bids(rfq.id, [bid.id], by="p@example.com", rationale="Cheapest")

    assert store.get_bid_shortlist(rfq.id) is None, "a refused write lands nothing"


def test_the_refusal_names_every_offending_vendor_and_code():
    store = make_store()
    rfq = seed_rfq(store)
    a = store.register_bid(rfq.id, vendor_name="Galfar", headline_price_aed=1)
    b = store.register_bid(rfq.id, vendor_name="Petrofac", headline_price_aed=2)
    complete_receipts(store, rfq.id, a.id)
    store.record_vdrl_receipt(b.id, doc_code="TECH-OFFER", state="received")

    with pytest.raises(ValueError) as exc:
        store.select_bids(rfq.id, [a.id, b.id], by="p@example.com", rationale="Both")

    message = str(exc.value)
    assert "Petrofac" in message
    assert "COMM-OFFER" in message and "BID-EVAL" in message
    assert "Galfar" not in message, "only the bids that block are named"


def test_one_incomplete_bid_refuses_the_whole_call():
    """Not a filtered subset: the rationale and the author belong to the
    selection as made, so narrowing it would attribute an unmade decision."""
    store = make_store()
    rfq = seed_rfq(store)
    a = store.register_bid(rfq.id, vendor_name="Galfar", headline_price_aed=1)
    b = store.register_bid(rfq.id, vendor_name="Petrofac", headline_price_aed=2)
    complete_receipts(store, rfq.id, a.id)

    with pytest.raises(ValueError):
        store.select_bids(rfq.id, [a.id, b.id], by="p@example.com", rationale="Both")

    assert store.get_bid_shortlist(rfq.id) is None


def test_an_unreadable_must_have_blocks_selection_too():
    store = make_store()
    rfq = seed_rfq(store)
    bid = store.register_bid(rfq.id, vendor_name="Galfar", headline_price_aed=1)
    complete_receipts(store, rfq.id, bid.id)
    store.record_vdrl_receipt(bid.id, doc_code="BID-EVAL", state="unreadable")

    with pytest.raises(ValueError, match="BID-EVAL"):
        store.select_bids(rfq.id, [bid.id], by="p@example.com", rationale="Cheapest")


def test_a_complete_bid_still_selects():
    store = make_store()
    rfq = seed_rfq(store)
    bid = store.register_bid(rfq.id, vendor_name="Galfar", headline_price_aed=1)
    complete_receipts(store, rfq.id, bid.id)

    store.select_bids(rfq.id, [bid.id], by="p@example.com", rationale="Lowest compliant")

    assert store.get_bid_shortlist(rfq.id).selected_bid_ids == [bid.id]
```

Repair the four test call sites by inserting `complete_receipts(store, rfq.id, bid.id)`
before each `select_bids`. `tests/test_workflow_stages.py` and
`tests/test_workflow_persistence.py` each need their own copy of the helper —
there is no `tests/conftest.py` in this repository and each test file defines
its own helpers.

`tests/test_workflow_persistence.py::populated_store` needs more than a helper
call: its assertion at line 104 requires the first bid to carry exactly one
`unreadable` receipt. Give it **two** bids — the existing flagged one, and a
second complete one that the selection names:

```python
    bid = store.register_bid(rfq.id, vendor_name="Petrofac", headline_price_aed=51_400_000)
    store.record_vdrl_receipt(bid.id, doc_code="GA-001", state="unreadable")
    # A second, complete bid — the selection needs one, and round-tripping a
    # flagged bid beside a selected one is worth more than either alone.
    selected = store.register_bid(rfq.id, vendor_name="Galfar", headline_price_aed=46_200_000)
    store.record_vdrl_receipt(selected.id, doc_code="GA-001", state="received", revision="Rev. A")
    complete_receipts(store, rfq.id, selected.id)
    store.select_bids(rfq.id, [selected.id], by="client@example.com",
                      rationale="Only compliant bid")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_workflow_store.py -v -k "must_have or offending or whole_call"`
Expected: FAIL — `DID NOT RAISE <class 'ValueError'>`

- [ ] **Step 3: Write minimal implementation**

> **Reference code below is intent, not paste-able.**
> **Load-bearing:** the block goes *after* the rationale and belongs-to checks,
> and the raise happens before any mutation. **Illustrative:** the sentence
> wording and the joining.

```python
        # A read that gates a write, so it lives here rather than in the route:
        # the route runs this whole method inside `locked_update`, making them
        # one critical section. The same rule as `add_shortlist_entry`,
        # `delete_item`, `delete_project` and `delete_bidder`.
        flagged = []
        for bid_id in bid_ids:
            verdict = self.responsiveness_for(bid_id)
            if not verdict.complete:
                flagged.append(
                    f"{self._bids[bid_id].vendor_name} is missing "
                    f"{', '.join(verdict.missing_must_haves)}."
                )
        if flagged:
            raise ValueError(
                " ".join(flagged)
                + " These bids cannot be taken to evaluation until the "
                "documents arrive."
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_workflow_store.py tests/test_workflow_stages.py tests/test_workflow_persistence.py -v`
Expected: PASS, all three files

- [ ] **Step 5: Commit**

```bash
git add workflow/store.py tests/
git commit -m "feat: an incomplete bid cannot be taken to evaluation"
```

---

## Task 5: Bid intake routes

**Files:**
- Modify: `api/workflow_routes.py` — three routes, plus the per-bid payload in `get_rfq`
- Modify: `tests/test_auth_middleware.py` — probe substitution for `bid_id`
- Test: `tests/test_workflow_endpoints.py`

**Interfaces:**
- Consumes: `register_bid`, `record_vdrl_receipt`, `select_bids`, `responsiveness_for`
- Produces: `POST /rfqs/{rfq_id}/bids`, `POST /rfqs/{rfq_id}/bids/{bid_id}/receipts`, `POST /rfqs/{rfq_id}/bids/select`
- **Store invariant owned:** none new — every write these routes make is a store
  method that already owns its invariant, and the routes add no decision of
  their own. That is the point: a route that gated anything would be a check
  outside the lock.

`KeyError` → 404, `ValueError` → 409 carrying the refusal sentence as `detail`,
matching every other gated write on this router.

None of the three is added to `middleware.PUBLIC_PATHS`.
`test_auth_middleware.py`'s route sweep walks every registered route and
substitutes a probe value per path parameter; `bid_id` is new and must be added
there, which is exactly what that assertion exists to force.

- [ ] **Step 1: Write the failing test**

```python
def test_a_bid_can_be_registered(client, rfq_id):
    response = client.post(f"/api/workflow/rfqs/{rfq_id}/bids",
                           json={"vendor_name": "Galfar", "headline_price_aed": 46_200_000})
    assert response.status_code == 201
    assert response.json()["vendor_name"] == "Galfar"


def test_a_receipt_can_be_recorded(client, rfq_id):
    bid = client.post(f"/api/workflow/rfqs/{rfq_id}/bids",
                      json={"vendor_name": "Galfar", "headline_price_aed": 1}).json()
    response = client.post(
        f"/api/workflow/rfqs/{rfq_id}/bids/{bid['id']}/receipts",
        json={"doc_code": "TECH-OFFER", "state": "received", "revision": "Rev. 0"},
    )
    assert response.status_code == 201
    assert response.json()["state"] == "received"


def test_a_receipt_for_an_unknown_code_is_a_409(client, rfq_id):
    bid = client.post(f"/api/workflow/rfqs/{rfq_id}/bids",
                      json={"vendor_name": "Galfar", "headline_price_aed": 1}).json()
    response = client.post(
        f"/api/workflow/rfqs/{rfq_id}/bids/{bid['id']}/receipts",
        json={"doc_code": "NOPE", "state": "received"},
    )
    assert response.status_code == 409
    assert "NOPE" in response.json()["detail"]


def test_selecting_an_incomplete_bid_is_a_409_naming_the_codes(client, rfq_id):
    bid = client.post(f"/api/workflow/rfqs/{rfq_id}/bids",
                      json={"vendor_name": "Galfar", "headline_price_aed": 1}).json()
    response = client.post(
        f"/api/workflow/rfqs/{rfq_id}/bids/select",
        json={"bid_ids": [bid["id"]], "by": "p@example.com", "rationale": "Cheapest"},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "Galfar" in detail and "COMM-OFFER" in detail


def test_a_complete_bid_selects_and_the_payload_reports_it(client, rfq_id):
    bid = client.post(f"/api/workflow/rfqs/{rfq_id}/bids",
                      json={"vendor_name": "Galfar", "headline_price_aed": 1}).json()
    for code in ("TECH-OFFER", "COMM-OFFER", "BID-EVAL"):
        client.post(f"/api/workflow/rfqs/{rfq_id}/bids/{bid['id']}/receipts",
                    json={"doc_code": code, "state": "received"})

    selected = client.post(
        f"/api/workflow/rfqs/{rfq_id}/bids/select",
        json={"bid_ids": [bid["id"]], "by": "p@example.com", "rationale": "Lowest"},
    )
    assert selected.status_code == 201

    detail = client.get(f"/api/workflow/rfqs/{rfq_id}").json()
    row = detail["bids"][0]
    assert row["selected"] is True
    assert row["missing_must_haves"] == []
    assert row["complete"] is True
    # Relative, not absolute: the denominator is 3 only if this fixture's RFQ
    # adds no mandatory VDRL line of its own. Asserting the relationship is
    # what the tally actually promises — "received == required" means
    # selectable.
    assert row["vdrl_received"] == row["vdrl_required"]


def test_the_payload_flags_an_incomplete_bid(client, rfq_id):
    bid = client.post(f"/api/workflow/rfqs/{rfq_id}/bids",
                      json={"vendor_name": "Galfar", "headline_price_aed": 1}).json()
    client.post(f"/api/workflow/rfqs/{rfq_id}/bids/{bid['id']}/receipts",
                json={"doc_code": "BID-EVAL", "state": "unreadable"})

    row = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["bids"][0]
    assert row["complete"] is False
    assert "BID-EVAL" in row["missing_must_haves"]
    assert row["unreadable"] == ["BID-EVAL"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_workflow_endpoints.py -v -k "bid or receipt or select"`
Expected: FAIL — 404 Not Found, the routes do not exist

- [ ] **Step 3: Write minimal implementation**

> **Reference code below is intent, not paste-able.**
> **Load-bearing:** `ValueError` → 409 and `KeyError` → 404; the whole store
> call inside `locked_update`. **Illustrative:** the request model field names,
> which should match the store's parameter names.

```python
class BidIn(BaseModel):
    vendor_name: str
    headline_price_aed: int
    currency: str = "AED"


class ReceiptIn(BaseModel):
    doc_code: str
    state: ReceiptState
    revision: str | None = None


class BidSelectionIn(BaseModel):
    bid_ids: list[str]
    by: str
    rationale: str


@router.post("/rfqs/{rfq_id}/bids", status_code=201)
def register_bid(rfq_id: str, body: BidIn) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            bid = store.register_bid(rfq_id, **body.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return bid.model_dump(mode="json")


@router.post("/rfqs/{rfq_id}/bids/{bid_id}/receipts", status_code=201)
def record_receipt(rfq_id: str, bid_id: str, body: ReceiptIn) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            receipt = store.record_vdrl_receipt(bid_id, **body.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return receipt.model_dump(mode="json")


@router.post("/rfqs/{rfq_id}/bids/select", status_code=201)
def select_bids(rfq_id: str, body: BidSelectionIn) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            selection = store.select_bids(
                rfq_id, body.bid_ids, by=body.by, rationale=body.rationale
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return selection.model_dump(mode="json")
```

And in `get_rfq`'s bid loop, replacing `"vdrl_missing"`:

```python
        verdict = store.responsiveness_for(bid.id)
        bids.append({
            **bid.model_dump(mode="json"),
            "vdrl_received": received,
            "vdrl_required": required,
            "missing_must_haves": verdict.missing_must_haves,
            "missing_optional": verdict.missing_optional,
            "unreadable": verdict.unreadable,
            "complete": verdict.complete,
            "selected": bid.id in selected,
        })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_workflow_endpoints.py tests/test_auth_middleware.py -v`
Expected: PASS both files

- [ ] **Step 5: Commit**

```bash
git add api/workflow_routes.py tests/test_workflow_endpoints.py tests/test_auth_middleware.py
git commit -m "feat: bids can be received through the application"
```

---

## Task 6: The demo seed

**Files:**
- Modify: `workflow/seed_demo.py`
- Test: `tests/test_seed_demo.py`

**Interfaces:**
- Consumes: everything above
- Produces: `build_demo_store` still builds
- **Store invariant owned:** none new — the seed asserts the invariants of Tasks
  2–4 against a realistic corpus rather than owning one of its own.

Two changes, and one deliberate non-change.

`CO-002` becomes `mandatory=False`. JAT-02 selects both bids while the second is
`not_received` on it; under Task 4 that call now raises and `build_demo_store`
fails outright. Flipping the flag keeps the visible tally gap the seed's own
comment exists to demonstrate, keeps both bids advancing, and keeps the recorded
rationale about pricing the coating deviation at TBE accurate.

JAT-02's two bids gain the three org must-haves as `received`, for the same
reason.

**JAT-01 is left incomplete on purpose.** It never calls `select_bids`, so it
survives untouched and becomes the demo's worked example of a flagged bid — one
`unreadable` type-test certificate, and three org must-haves nobody sent.

- [ ] **Step 1: Write the failing test**

```python
def test_the_demo_still_builds():
    store = build_demo_store(as_of=date(2026, 8, 13))
    assert store.get_bid_shortlist("rfq_jat02") is not None


def test_jat01_demonstrates_a_flagged_bid():
    store = build_demo_store(as_of=date(2026, 8, 13))
    bids = store.bids_for("rfq_jat01")
    assert bids, "JAT-01 has bids"
    assert all(not store.responsiveness_for(b.id).complete for b in bids)


def test_jat02_bids_are_all_selectable():
    store = build_demo_store(as_of=date(2026, 8, 13))
    for bid in store.bids_for("rfq_jat02"):
        assert store.responsiveness_for(bid.id).complete is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_seed_demo.py -v`
Expected: FAIL — `ValueError: … is missing TECH-OFFER, COMM-OFFER, BID-EVAL.`

- [ ] **Step 3: Write minimal implementation**

Flip `CO-002` to `mandatory=False` at [`seed_demo.py:473`](../../../workflow/seed_demo.py),
and record the three org must-haves for each JAT-02 bid inside the existing loop.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_seed_demo.py -v`
Expected: PASS. Nine tests skip without the ADNOC export — that is the documented
`needs_real_avl` gate, not a failure.

- [ ] **Step 5: Commit**

```bash
git add workflow/seed_demo.py tests/test_seed_demo.py
git commit -m "fix: the demo seed under a rule it predates"
```

---

## Task 7: The flag on screen

**Files:**
- Modify: `web/src/types.ts`, `web/src/pages/RfqDetail.tsx`
- Test: `web/src/pages/RfqDetail.test.tsx`

**Interfaces:**
- Consumes: the payload fields from Task 5
- Produces: `BidRow` gains `missing_must_haves`, `missing_optional`, `unreadable`, `complete`; loses `vdrl_missing`
- **Store invariant owned:** none — this is a view.

A flagged bid must read as flagged, and an unreadable document must read as a
chase rather than a gap. Those are different remedies and the screen is where
the difference is acted on.

Component tests that render `App` or `Setup` must mock `auth/context`'s
`useAuth` with a **stable** object built once via `vi.hoisted` — a fresh literal
per call refetches until the vitest worker dies of heap exhaustion, which
arrives as `Worker exited unexpectedly`, not as a failed assertion.

- [ ] **Step 1: Write the failing test**

```tsx
it('flags a bid that is missing a must-have', async () => {
  renderDetail({
    bids: [{ ...bidFixture, complete: false, missing_must_haves: ['COMM-OFFER'], unreadable: [] }],
  })
  expect(await screen.findByText(/missing info/i)).toBeInTheDocument()
  expect(screen.getByText(/COMM-OFFER/)).toBeInTheDocument()
})

it('separates an unreadable document from an absent one', async () => {
  renderDetail({
    bids: [{ ...bidFixture, complete: false,
             missing_must_haves: ['BID-EVAL'], unreadable: ['BID-EVAL'] }],
  })
  expect(await screen.findByText(/unreadable/i)).toBeInTheDocument()
})

it('shows a complete bid as complete', async () => {
  renderDetail({ bids: [{ ...bidFixture, complete: true, missing_must_haves: [], unreadable: [] }] })
  expect(screen.queryByText(/missing info/i)).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- RfqDetail` under `web/`
Expected: FAIL — the text is not rendered

- [ ] **Step 3: Write minimal implementation**

Replace the `vdrl_missing` cell in `BidsCard` with a status cell driven by
`complete`, listing `missing_must_haves`, and a separate line for `unreadable`
worded as a chase. Update `BidRow` in `types.ts` to match.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test` and `npm run build` under `web/`
Expected: PASS; the build type-checks the test files too, since
`web/tsconfig.app.json` includes `src`

- [ ] **Step 5: Commit**

```bash
git add web/src
git commit -m "feat: a flagged bid reads as flagged"
```

---

## Task 8: Integration — the mutation matrix

**Files:**
- Test: `tests/test_workflow_persistence.py`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: every task above
- Produces: nothing new
- **Store invariant owned:** `workflow.json` holds exactly the entities the
  store holds, across a write–reload–mutate–write cycle — including the full
  append-only receipt history, whose *current* reading is derived rather than
  stored.

### Which of `PLAN-TEMPLATE.md`'s nine rows apply

| template row | here |
|---|---|
| a newer revision of an already-extracted document arrives | **transfers** — a second receipt for a code already recorded |
| a document is deleted from its source folder | **transfers** — a VDRL line removed after receipts named it |
| a re-extraction fails after a successful one | **transfers** — a refused `select_bids` after a successful one |
| an LLM call fails on both runs | **transfers** — a bid that stays incomplete across both runs |
| a sibling revision arrives on a second upload | inapplicable — no lineage in this subsystem |
| each prompt-version constant is bumped | inapplicable — no prompts |
| an LLM call fails on run 1, succeeds on run 2 | folded into the retraction row |
| an entity's only source document is unrecognised | inapplicable — no classification |
| the model returns a response omitting an optional array | inapplicable — no model |

- [ ] **Step 1: Write the two-run harness**

Every row below reloads between run 1 and run 2, so the mutation crosses a real
serialization boundary rather than mutating a store that never left memory. This
harness is what makes that cheap:

```python
def reload(tmp_path, store) -> WorkflowStore:
    """Run 1 ends, run 2 begins. A mutation that only ever touches an in-memory
    store proves nothing about `workflow.json`, and the receipt history is
    exactly the collection a round trip could silently flatten."""
    persistence.save(str(tmp_path), store)
    return persistence.load(str(tmp_path))


def complete_receipts(store, rfq_id: str, bid_id: str) -> None:
    from workflow import returnables
    must, _ = returnables.required(store.vdrl_for(rfq_id))
    for code in must:
        store.record_vdrl_receipt(bid_id, doc_code=code, state="received")
```

- [ ] **Step 2: Run it to verify the harness itself round-trips**

Run: `python -m pytest tests/test_workflow_persistence.py -v -k "round_trip"`
Expected: PASS — the existing round-trip tests still pass against the repaired
fixture from Task 4, before a single matrix row is written.

- [ ] **Step 3: Write the matrix, one test per row** — see the table below. No
      production code is expected in this task; a row that needs some is a
      defect Tasks 1–7 missed, and is fixed **there**, not here.

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest`
Expected: PASS, no failures. Record the counts for Step 5.

- [ ] **Step 4b: Two-run mutation matrix**

Each row is one test, using `reload` between run 1 and run 2.

| mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|
| a must-have receipt recorded `received`, then `not_received` after reload | Task 2 — received set is the latest statement | the bid is incomplete after reload; both receipts survive in history |
| the retraction is itself reversed on a third write | Task 2 | complete again; three receipts in history |
| a per-RFQ `mandatory=True` line added after a complete selection | Task 4 — a selection is never re-validated | the stored `BidShortlist` is unchanged; a **new** `select_bids` refuses |
| a VDRL line removed after receipts named it | Task 3 — receipts name a required returnable | the orphaned receipt survives in history but no longer counts; the bid's verdict does not silently improve |
| `select_bids` refused after a successful one | Task 4 — a refused write lands nothing | the earlier selection survives byte-identical; `workflow.json` is unchanged |
| a bid registered after a selection was recorded | Task 4 | it is absent from `selected_bid_ids` and is not retro-selected |
| a bid incomplete on both runs, selection attempted both times | Task 4 | refused twice, same message, nothing written either time |
| a receipt for an org code recorded before any VDRL line exists | Task 3 — the org set is always valid | accepted; the org set does not depend on per-RFQ authoring |
| a per-RFQ line added with an org code and `mandatory=False` | Task 1 — the org set wins | `COMM-OFFER` is still a must-have after reload |

Verify the matrix is real: reintroduce each defect one at a time and confirm the
intended row fails, and that nothing else does. A row that still passes with its
defect reinstated is not testing what it claims.

- [ ] **Step 5: Measure the test counts and update `CLAUDE.md`**

Run `python -m pytest` on the workstation — with `pdftotext`, `data/` including
the ADNOC export, and an ingested multi-vendor `projects/` all present — and
record the **measured** workstation row. Derive the CI row from it by the
documented subtraction (`-4 -3 -2 -9` passes, `+4 +3 +2 +9` skips). Do not edit
the two rows independently; that is how they drift apart. Add a line for the web
suite's new count.

- [ ] **Step 6: Commit**

```bash
git add tests/test_workflow_persistence.py CLAUDE.md
git commit -m "test: the two-run matrix for receipts, and measured counts"
```

---

## Self-review against the spec

| spec section | task |
|---|---|
| 1 — `returnables.py`, the constant, org-set-wins, speaking codes | 1 |
| 2 — `Responsiveness`, computed, `unreadable` split, replaces `missing_vdrl_lines`, `vdrl_required` denominator | 1, 2 |
| 3 — the refusal, whole call, message, in the store, gate unchanged | 4 |
| 4 — retraction, receipt validation | 2, 3 |
| 5 — three routes, auth sweep, read side | 5 |
| 6 — seed, web, `CLAUDE.md` | 6, 7, 8 |
| 7 — testing, pure tests, mutation matrix | 1, 8 |
