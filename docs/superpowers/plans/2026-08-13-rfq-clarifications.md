# RFQ clarifications — query register and addenda — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `Stage.CLARIFICATIONS` a clarification query register and an addendum record, and an exit gate that reads both, so what each bidder asked and whether every bidder got the same answer are facts the system holds.

**Architecture:** Two new collections on `WorkflowStore`, keyed by `rfq_id`, with their derived state computed in a pure module the way `workflow/bidders.py` computes prequalification expiry. Every guard is a read that gates a write and therefore lives in the store method, which the route runs inside `persistence.locked_update`. The addendum becomes the one sanctioned door through the frozen-package invariant.

**Tech Stack:** Python 3.12, pydantic v2, FastAPI, pytest. React 18 + TypeScript + vitest under `web/`.

**Spec:** [`docs/superpowers/specs/2026-08-13-rfq-clarifications-design.md`](../specs/2026-08-13-rfq-clarifications-design.md)

## Global Constraints

- **No new infrastructure.** No database, no ORM, no migrations. `workflow.json` only.
- **`VERSION` does not move.** `from_document` reads both new collections with `.get(key, [])`; a document written before this phase loads as an RFQ with no queries and no addenda. That is the correct reading of a missing key, not a migration.
- **Every write, and every decision that gates one, happens inside `persistence.locked_update`.** A check in the route and a write in the store are two critical sections, not one.
- **A gate never returns a bare `False`.** `GateResult` carries a reason, and the reason names the **whole** exit criterion, not the nearer half of it.
- **Only forward transitions are gated.** `check_gate` short-circuits on `is_backward` before reaching `_GATES`; nothing in this plan changes that.
- **State is derived, never stored.** No `status` field on either model. Same call the registry made in refusing an `"Expired"` member of `PrequalStatus`.
- **Tests are key-free.** Nothing added here may require `ANTHROPIC_API_KEY` or any fixture directory.
- **Stage codes and `TRANSITIONS` are untouched.** This plan adds one entry to `_GATES` and nothing else to `workflow/stages.py`.
- **Nothing is deleted out from under a live reference.** Fourth instance of the rule after `delete_item`, `delete_project`, `delete_bidder`.
- Run the Python suite from the repo root with `python -m pytest`; the web suite with `npm test` under `web/`.

> **Reference code below is intent, not paste-able.** It shows the shape and the reasoning, and it has not been executed. Read it, then write the implementation against the test.
>
> **Load-bearing** throughout: the derived-state rule (no `status` field), the `max + 1` numbering rule, the four `issue_addendum` checks and the fact that all four sit inside the store method, the blank-reason refusals, and the `.get(key, [])` defaults in `from_document`.
> **Illustrative:** exact wording of refusal sentences, the zero-padding widths, CSS class names, and the precise ordering of fields in a payload dict.

---

## File structure

| file | responsibility | task |
|---|---|---|
| `workflow/models/clarification.py` | `ClarificationQuery`, `Addendum` — data only | 1 |
| `workflow/clarifications.py` | pure: state, circulation, next number | 1 |
| `workflow/store.py` | query writes and their guards | 2, 3 |
| `workflow/store.py` | addendum writes and their guards | 4 |
| `workflow/persistence.py` | both collections, three places each | 5 |
| `workflow/gates.py` | `_clarifications_exit` | 6 |
| `api/workflow_routes.py` | seven routes, `get_rfq` payload | 7 |
| `web/src/pages/wizard/*.tsx` | the four steps, extracted | 8, 9 |
| `web/src/pages/RfqDetail.tsx` | the register, read-only | 10 |
| `workflow/seed_demo.py` | a demo whose reason string and data agree | 11 |

---

### Task 1: Models and the pure module

**Files:**
- Create: `workflow/models/clarification.py`
- Create: `workflow/clarifications.py`
- Test: `tests/test_clarifications.py`

**Interfaces:**
- Consumes: `workflow.models.rfq.Attachment`
- Produces:
  - `ClarificationQuery`, `Addendum` (pydantic models), `QueryCategory`
  - `new_query_id() -> str`, `new_addendum_id() -> str`
  - `clarifications.state(query) -> str` — `"Open" | "Answered" | "Withdrawn"`
  - `clarifications.is_open(query) -> bool`
  - `clarifications.is_circulated(query) -> bool`
  - `clarifications.is_draft(addendum) -> bool`
  - `clarifications.next_query_number(existing, category) -> str`
  - `clarifications.next_addendum_number(existing) -> str`
- **Store invariant owned:** none — this task touches no stored state. Said out loud rather than left blank: a pure module has no snapshot to assert over, and inventing an invariant here would leave a real collection unclaimed in a later task. `_queries` is claimed by Task 2, `_addenda` by Task 4.

`next_query_number` takes the existing queries and returns one past the **highest** number of that category, not one past the count. Withdrawn queries keep their numbers and stay in the register, so counting would reissue a number already quoted to a bidder in writing. It must survive a list containing a malformed number without crashing — parse defensively and ignore what does not parse, because a number that cannot be read is not evidence that the sequence restarted.

`state` checks `withdrawn_at` **before** `answer`. A query answered and then withdrawn reads as Withdrawn: withdrawal is the later act and the one that takes it out of the gate's count.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_clarifications.py
"""The clarification register's pure logic.

No store, no I/O, no clock — the same shape as `tests/test_bidder_suitability.py`
against `workflow/bidders.py`. State is derived from three fields rather than
stored in a fourth, so these assertions are the only place that rule is checked
without building a store.
"""
from datetime import date, datetime, timezone

import pytest

from workflow import clarifications
from workflow.models.clarification import Addendum, ClarificationQuery
from workflow.models.rfq import Attachment


def a_query(**overrides) -> ClarificationQuery:
    defaults = dict(
        rfq_id="rfq_1",
        number="TQ-001",
        raised_by_entry_id="sle_1",
        raised_by_name="Al Munara Switchgear LLC",
        raised_on=date(2026, 8, 13),
        category="Technical",
        question="Confirm the IO list revision the transmitters are counted against.",
    )
    return ClarificationQuery(**{**defaults, **overrides})


def an_addendum(**overrides) -> Addendum:
    defaults = dict(
        rfq_id="rfq_1",
        number="ADD-01",
        supersedes_revision="Rev. A",
        revision="Rev. B",
        summary="IO list corrected; two transmitters added.",
        attachments=[Attachment(doc_code="RUU-IO-411", title="IO list", revision="Rev. B")],
    )
    return Addendum(**{**defaults, **overrides})


def test_a_query_with_no_answer_is_open():
    assert clarifications.state(a_query()) == "Open"
    assert clarifications.is_open(a_query()) is True


def test_a_query_with_an_answer_is_answered():
    q = a_query(answer="Rev. A is the issued revision.",
                answered_by="buyer@example.com",
                answered_at=datetime(2026, 8, 14, tzinfo=timezone.utc))
    assert clarifications.state(q) == "Answered"
    assert clarifications.is_open(q) is False


def test_withdrawal_wins_over_an_answer_already_given():
    """Withdrawal is the later act. A query answered and then withdrawn is out
    of the round, and the gate must not still be counting it as answered."""
    q = a_query(answer="Rev. A.", answered_by="buyer@example.com",
                answered_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
                withdrawn_reason="Raised in error; duplicate of TQ-001.",
                withdrawn_by="buyer@example.com",
                withdrawn_at=datetime(2026, 8, 15, tzinfo=timezone.utc))
    assert clarifications.state(q) == "Withdrawn"
    assert clarifications.is_open(q) is False


def test_silence_means_circulated_and_a_reason_means_restricted():
    assert clarifications.is_circulated(a_query(answer="Yes.")) is True
    assert clarifications.is_circulated(
        a_query(answer="Yes.", restricted_reason="Reveals the bidder's own layout.")
    ) is False


def test_the_next_number_is_one_past_the_highest_not_one_past_the_count():
    """A withdrawn query keeps its number. Counting would hand TQ-002 to a
    second bidder after the first TQ-002 was already quoted in writing."""
    existing = [
        a_query(number="TQ-001"),
        a_query(number="TQ-002", withdrawn_reason="r", withdrawn_by="b",
                withdrawn_at=datetime(2026, 8, 15, tzinfo=timezone.utc)),
    ]
    assert clarifications.next_query_number(existing, "Technical") == "TQ-003"


def test_the_two_category_sequences_are_independent():
    existing = [a_query(number="TQ-001"), a_query(number="TQ-002")]
    assert clarifications.next_query_number(existing, "Commercial") == "CQ-001"
    assert clarifications.next_query_number([], "Technical") == "TQ-001"


def test_an_unparseable_number_does_not_restart_the_sequence():
    existing = [a_query(number="TQ-001"), a_query(number="TQ-legacy")]
    assert clarifications.next_query_number(existing, "Technical") == "TQ-002"


def test_addendum_numbers_run_on_their_own_two_digit_sequence():
    assert clarifications.next_addendum_number([]) == "ADD-01"
    assert clarifications.next_addendum_number([an_addendum(number="ADD-01")]) == "ADD-02"


def test_an_addendum_is_a_draft_until_it_is_issued():
    assert clarifications.is_draft(an_addendum()) is True
    issued = an_addendum(issued_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
                         issued_by="buyer@example.com")
    assert clarifications.is_draft(issued) is False


def test_a_query_carries_no_status_field():
    """The rule, asserted rather than assumed: a stored status is a fourth
    thing that has to agree with three fields that already say it."""
    assert "status" not in ClarificationQuery.model_fields
    assert "status" not in Addendum.model_fields
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_clarifications.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'workflow.clarifications'`

- [ ] **Step 3: Write minimal implementation**

```python
# workflow/models/clarification.py
"""A bidder's question, and the amendment an answer sometimes forces.

Neither model carries a `status` field. State is a function of the timestamps
below and is computed in `workflow/clarifications.py`, for the same reason
`PrequalStatus` has no `"Expired"` member: a stored status is a fourth thing
that has to agree with three fields that already say it, and the first write
that updates one and not the other makes the register lie with nothing to
sweep it.
"""
from datetime import date, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from workflow.models.rfq import Attachment

QueryCategory = Literal["Technical", "Commercial"]


def new_query_id() -> str:
    return f"clq_{uuid4().hex[:8]}"


def new_addendum_id() -> str:
    return f"add_{uuid4().hex[:8]}"


class ClarificationQuery(BaseModel):
    """One question from one invited bidder, and what was answered.

    `raised_by_entry_id` addresses a `ShortlistEntry`, not a registry bidder:
    a query is raised against *this* RFQ by somebody invited to *this* RFQ,
    and a `vendor_id` would also name companies who were never invited.

    `raised_by_name` beside it is a **snapshot**, for the identical reason
    `ShortlistEntry` snapshots `vendor_name` — the register records who asked
    at the time they asked, and a later rename does not rewrite it.

    `restricted_reason` is the whole circulation model. `None` means the
    answer went to the entire included shortlist. A reason means it did not,
    and who decided that. There is deliberately no `circulate: bool`: a
    boolean makes a restricted answer indistinguishable from an oversight.
    """

    id: str = Field(default_factory=new_query_id)
    rfq_id: str
    number: str
    raised_by_entry_id: str
    raised_by_name: str
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


class Addendum(BaseModel):
    """A numbered amendment to an issued RFQ.

    `supersedes_revision` is recorded by the store at draft time, not supplied
    by the caller — a caller-supplied "what I am superseding" is a claim, and
    the store already knows the answer. Together with `revision` it makes the
    addenda list the package's revision trail, so no new history mechanism is
    needed and `RfqRecord.history` stays what it is: stage transitions.

    `arising_from_query_ids` may be empty. A buyer-initiated addendum — a
    client change, a corrected datasheet — is legitimate, and requiring a
    query to justify one would only produce fabricated queries.
    """

    id: str = Field(default_factory=new_addendum_id)
    rfq_id: str
    number: str
    supersedes_revision: str
    revision: str
    summary: str
    attachments: list[Attachment] = Field(default_factory=list)
    arising_from_query_ids: list[str] = Field(default_factory=list)
    bid_due_date: date | None = None
    issued_at: datetime | None = None
    issued_by: str | None = None
```

```python
# workflow/clarifications.py
"""What state a clarification query is in, and what number the next one gets.

A pure module: no store, no I/O, no clock. The same arrangement as
`workflow/bidders.py`, and for the same reason — the answer is a function of
the record's own fields, so it is assertable from both sides without building
a store or freezing a clock.
"""
import re

from workflow.models.clarification import Addendum, ClarificationQuery, QueryCategory

_CATEGORY_PREFIX: dict[str, str] = {"Technical": "TQ", "Commercial": "CQ"}
_QUERY_WIDTH = 3
_ADDENDUM_WIDTH = 2


def state(query: ClarificationQuery) -> str:
    """`withdrawn_at` is checked first on purpose. Withdrawal is the later act
    and the one that takes a query out of the round, so a query answered and
    then withdrawn reads as Withdrawn rather than as Answered."""
    if query.withdrawn_at is not None:
        return "Withdrawn"
    if query.answer is not None:
        return "Answered"
    return "Open"


def is_open(query: ClarificationQuery) -> bool:
    return state(query) == "Open"


def is_circulated(query: ClarificationQuery) -> bool:
    """Silence means circulated. There is no third state."""
    return query.restricted_reason is None


def is_draft(addendum: Addendum) -> bool:
    return addendum.issued_at is None


def _highest(numbers: list[str], prefix: str) -> int:
    """One past the highest, never one past the count.

    Numbers are never reused: a withdrawn query keeps its number and stays in
    the register, so a count-based sequence would hand a bidder a number that
    another bidder was already quoted in writing.

    A number that does not parse is ignored rather than fatal. It is not
    evidence that the sequence restarted, and a register that cannot be added
    to because one legacy row is odd is worse than one that skips it.
    """
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    found = [int(m.group(1)) for n in numbers if (m := pattern.match(n))]
    return max(found, default=0)


def next_query_number(
    existing: list[ClarificationQuery], category: QueryCategory
) -> str:
    prefix = _CATEGORY_PREFIX[category]
    same = [q.number for q in existing if q.category == category]
    return f"{prefix}-{_highest(same, prefix) + 1:0{_QUERY_WIDTH}d}"


def next_addendum_number(existing: list[Addendum]) -> str:
    """Two digits rather than three: a tender that runs to a hundred addenda
    has a problem no padding fixes, and the narrower field is what buyers
    write on the documents."""
    highest = _highest([a.number for a in existing], "ADD")
    return f"ADD-{highest + 1:0{_ADDENDUM_WIDTH}d}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_clarifications.py -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Commit**

```bash
git add workflow/models/clarification.py workflow/clarifications.py tests/test_clarifications.py
git commit -m "feat: a query whose state is read off its own timestamps"
```

---

### Task 2: Raising, answering and withdrawing a query

**Files:**
- Modify: `workflow/store.py` — new `_queries` collection in `__init__`, new methods after the VDRL block
- Test: `tests/test_clarifications.py` (append a `-- the store --` section)

**Interfaces:**
- Consumes: `clarifications.next_query_number`, `clarifications.state`, `ClarificationQuery`, `_with_id`
- Produces:
  - `WorkflowStore.raise_query(rfq_id, entry_id, question, category, raised_on, query_id=None) -> ClarificationQuery`
  - `WorkflowStore.answer_query(rfq_id, query_id, answer, by, restricted_reason=None) -> ClarificationQuery`
  - `WorkflowStore.withdraw_query(rfq_id, query_id, reason, by) -> ClarificationQuery`
  - `WorkflowStore.queries_for(rfq_id) -> list[ClarificationQuery]`
- **Store invariant owned:** `_queries[rfq_id]` contains **exactly** the queries raised against that RFQ by a currently-included shortlist entry of that same RFQ, and within one category no two of them share a `number` — including numbers belonging to withdrawn queries.

Every refusal here is a read that gates a write. They live in the store method, not in the route, because the route runs the whole method inside `locked_update` and a check made outside the lock is a second critical section. This is the same rule the auth store, `delete_item` and `add_shortlist_entry` each state.

Re-answering is deliberately allowed: a buyer revising an answer is normal practice and the revision goes to the same audience. It overwrites, re-stamps `answered_by`/`answered_at`, and may change the restriction in either direction — including clearing it by passing `restricted_reason=None`, which circulates a previously restricted answer. Answering a **withdrawn** query is refused: it would put a live answer under a dead question.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_clarifications.py — append

from workflow.store import WorkflowStore


def store_with_a_shortlisted_rfq() -> tuple[WorkflowStore, str, str]:
    store = WorkflowStore()
    project = store.create_project(
        name="Ruwais Upgrade", code="RUU", client="ADNOC Refining",
        location="Ruwais, UAE",
        live_period_start=date(2026, 1, 1), live_period_end=date(2029, 12, 31),
    )
    item = store.create_item(
        project_id=project.id, item_type="Transmitters", description="Field transmitters",
        qty=40, uom="no", discipline="Instrumentation", estimated_value_aed=4_750_000,
    )
    rfq = store.create_rfq(
        project_id=project.id, item_ids=[item.id], reference="RUU-RFQ-2026-006",
        package="Field transmitters", discipline="Instrumentation",
        value_estimate_aed=4_750_000,
    )
    entry = store.add_shortlist_entry(
        rfq.id, vendor_name="Al Munara Switchgear LLC", prequal_status="Approved",
        scope_code_fit=True, included=True,
    )
    return store, rfq.id, entry.id


def test_a_raised_query_is_numbered_and_snapshots_the_vendor_name():
    store, rfq_id, entry_id = store_with_a_shortlisted_rfq()
    q = store.raise_query(rfq_id, entry_id, question="Which IO list revision?",
                          category="Technical", raised_on=date(2026, 8, 13))
    assert q.number == "TQ-001"
    assert q.raised_by_name == "Al Munara Switchgear LLC"
    assert clarifications.state(q) == "Open"


def test_a_query_from_a_vendor_who_was_never_invited_is_refused():
    """The record this register exists to make impossible."""
    store, rfq_id, _entry_id = store_with_a_shortlisted_rfq()
    with pytest.raises(KeyError):
        store.raise_query(rfq_id, "sle_nobody", question="q", category="Technical",
                          raised_on=date(2026, 8, 13))


def test_a_query_from_an_entry_of_a_different_rfq_is_refused():
    store, rfq_id, entry_id = store_with_a_shortlisted_rfq()
    other = store.create_rfq(
        project_id=store.list_projects()[0].id,
        item_ids=[store.list_rfqs()[0].item_ids[0]], reference="RUU-RFQ-2026-007",
        package="Cable", discipline="Electrical", value_estimate_aed=1,
    )
    with pytest.raises(ValueError, match="does not belong"):
        store.raise_query(other.id, entry_id, question="q", category="Technical",
                          raised_on=date(2026, 8, 13))


def test_a_query_from_an_excluded_entry_is_refused():
    store, rfq_id, _entry_id = store_with_a_shortlisted_rfq()
    excluded = store.add_shortlist_entry(
        rfq_id, vendor_name="Northwind Valve Works", prequal_status="Approved",
        scope_code_fit=True, included=False,
    )
    with pytest.raises(ValueError, match="not included"):
        store.raise_query(rfq_id, excluded.id, question="q", category="Technical",
                          raised_on=date(2026, 8, 13))


def test_a_blank_question_is_refused():
    store, rfq_id, entry_id = store_with_a_shortlisted_rfq()
    with pytest.raises(ValueError, match="question"):
        store.raise_query(rfq_id, entry_id, question="   ", category="Technical",
                          raised_on=date(2026, 8, 13))


def test_answering_circulates_by_default():
    store, rfq_id, entry_id = store_with_a_shortlisted_rfq()
    q = store.raise_query(rfq_id, entry_id, question="Which revision?",
                          category="Technical", raised_on=date(2026, 8, 13))
    answered = store.answer_query(rfq_id, q.id, answer="Rev. A.", by="buyer@example.com")
    assert clarifications.is_circulated(answered) is True
    assert answered.answered_by == "buyer@example.com"
    assert answered.answered_at is not None


def test_restricting_an_answer_without_a_reason_is_refused():
    """The circulation invariant. Withholding an answer from the rest of the
    shortlist is legal, and legal only as a recorded, attributed act."""
    store, rfq_id, entry_id = store_with_a_shortlisted_rfq()
    q = store.raise_query(rfq_id, entry_id, question="q", category="Technical",
                          raised_on=date(2026, 8, 13))
    with pytest.raises(ValueError, match="reason"):
        store.answer_query(rfq_id, q.id, answer="Yes.", by="buyer@example.com",
                           restricted_reason="   ")


def test_a_blank_answer_is_refused():
    store, rfq_id, entry_id = store_with_a_shortlisted_rfq()
    q = store.raise_query(rfq_id, entry_id, question="q", category="Technical",
                          raised_on=date(2026, 8, 13))
    with pytest.raises(ValueError, match="answer"):
        store.answer_query(rfq_id, q.id, answer="  ", by="buyer@example.com")


def test_re_answering_replaces_the_answer_and_can_lift_a_restriction():
    store, rfq_id, entry_id = store_with_a_shortlisted_rfq()
    q = store.raise_query(rfq_id, entry_id, question="q", category="Technical",
                          raised_on=date(2026, 8, 13))
    store.answer_query(rfq_id, q.id, answer="Provisionally yes.", by="buyer@example.com",
                       restricted_reason="Commercially sensitive while under review.")
    revised = store.answer_query(rfq_id, q.id, answer="Confirmed: yes.",
                                 by="lead@example.com")
    assert revised.answer == "Confirmed: yes."
    assert revised.answered_by == "lead@example.com"
    assert clarifications.is_circulated(revised) is True
    assert len(store.queries_for(rfq_id)) == 1


def test_answering_a_withdrawn_query_is_refused():
    store, rfq_id, entry_id = store_with_a_shortlisted_rfq()
    q = store.raise_query(rfq_id, entry_id, question="q", category="Technical",
                          raised_on=date(2026, 8, 13))
    store.withdraw_query(rfq_id, q.id, reason="Duplicate.", by="buyer@example.com")
    with pytest.raises(ValueError, match="withdrawn"):
        store.answer_query(rfq_id, q.id, answer="Yes.", by="buyer@example.com")


def test_withdrawing_without_a_reason_or_twice_is_refused():
    store, rfq_id, entry_id = store_with_a_shortlisted_rfq()
    q = store.raise_query(rfq_id, entry_id, question="q", category="Technical",
                          raised_on=date(2026, 8, 13))
    with pytest.raises(ValueError, match="reason"):
        store.withdraw_query(rfq_id, q.id, reason="", by="buyer@example.com")
    store.withdraw_query(rfq_id, q.id, reason="Duplicate.", by="buyer@example.com")
    with pytest.raises(ValueError, match="already withdrawn"):
        store.withdraw_query(rfq_id, q.id, reason="Again.", by="buyer@example.com")


def test_a_withdrawn_number_is_never_handed_out_again():
    """The invariant this task owns, asserted over the collection."""
    store, rfq_id, entry_id = store_with_a_shortlisted_rfq()
    first = store.raise_query(rfq_id, entry_id, question="a", category="Technical",
                              raised_on=date(2026, 8, 13))
    store.withdraw_query(rfq_id, first.id, reason="Raised in error.", by="b@example.com")
    second = store.raise_query(rfq_id, entry_id, question="b", category="Technical",
                               raised_on=date(2026, 8, 14))
    numbers = [q.number for q in store.queries_for(rfq_id)]
    assert second.number == "TQ-002"
    assert len(numbers) == len(set(numbers))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_clarifications.py -v`
Expected: FAIL — `AttributeError: 'WorkflowStore' object has no attribute 'raise_query'`

- [ ] **Step 3: Write minimal implementation**

Add to `WorkflowStore.__init__`, after `self._bid_shortlists`:

```python
        self._queries: dict[str, list[ClarificationQuery]] = {}
```

Add the import at the top of `workflow/store.py`:

```python
from workflow import clarifications
from workflow.models.clarification import ClarificationQuery, QueryCategory
```

Add the methods at the end of the class:

```python
    # -- clarifications ---------------------------------------------------
    #
    # Every refusal below is a read that gates a write, so it lives here and
    # not in the route: the route runs the whole method inside
    # `persistence.locked_update`, which makes the check and the write one
    # critical section. The same rule as the auth store and `delete_item`.

    def _query_or_raise(self, rfq_id: str, query_id: str) -> ClarificationQuery:
        for query in self._queries.get(rfq_id, []):
            if query.id == query_id:
                return query
        raise KeyError(f"Unknown clarification query: {query_id}")

    def _replace_query(self, rfq_id: str, updated: ClarificationQuery) -> ClarificationQuery:
        """Replace in place, by id. Addressing by position would rewrite the
        wrong row the moment a list was reordered — the same rule the
        procurement store states as "by id, never by index"."""
        self._queries[rfq_id] = [
            updated if q.id == updated.id else q for q in self._queries.get(rfq_id, [])
        ]
        return updated

    def raise_query(
        self,
        rfq_id: str,
        entry_id: str,
        question: str,
        category: QueryCategory,
        raised_on: date,
        query_id: str | None = None,
    ) -> ClarificationQuery:
        """A query is raised against this RFQ by somebody invited to it.

        `raised_by_name` is snapshotted here for the same reason
        `ShortlistEntry` snapshots `vendor_name`: the register records who
        asked at the time they asked.
        """
        if rfq_id not in self._rfqs:
            raise KeyError(f"Unknown RFQ: {rfq_id}")
        entries = {e.id: e for e in self._shortlists.get(rfq_id, [])}
        entry = entries.get(entry_id)
        if entry is None:
            # Deliberately a KeyError even when the id exists on another RFQ's
            # shortlist: from this RFQ's point of view there is no such entry.
            if not any(
                e.id == entry_id
                for entries_ in self._shortlists.values()
                for e in entries_
            ):
                raise KeyError(f"Unknown shortlist entry: {entry_id}")
            raise ValueError(
                f"Shortlist entry {entry_id} does not belong to RFQ {rfq_id}."
            )
        if not entry.included:
            raise ValueError(
                f"{entry.vendor_name} is not included on this shortlist, so they "
                f"cannot raise a clarification against it."
            )
        if not question.strip():
            raise ValueError("A clarification needs a question.")

        query = ClarificationQuery(
            **_with_id(query_id),
            rfq_id=rfq_id,
            number=clarifications.next_query_number(
                self._queries.get(rfq_id, []), category
            ),
            raised_by_entry_id=entry.id,
            raised_by_name=entry.vendor_name,
            raised_on=raised_on,
            category=category,
            question=question.strip(),
        )
        self._queries.setdefault(rfq_id, []).append(query)
        return query

    def answer_query(
        self,
        rfq_id: str,
        query_id: str,
        answer: str,
        by: str,
        restricted_reason: str | None = None,
    ) -> ClarificationQuery:
        """Answering circulates to the whole included shortlist. Withholding
        requires an attributed reason — the shape `override_reason` and
        `rationale` already use, and the reason a boolean was refused: it
        would make a restricted answer indistinguishable from an oversight.

        Re-answering is allowed and overwrites; a revised answer goes to the
        same audience. Answering a withdrawn query is not: it would put a live
        answer under a dead question.
        """
        query = self._query_or_raise(rfq_id, query_id)
        if query.withdrawn_at is not None:
            raise ValueError(
                f"{query.number} was withdrawn and can no longer be answered."
            )
        if not answer.strip():
            raise ValueError("An answer cannot be empty.")
        if restricted_reason is not None and not restricted_reason.strip():
            raise ValueError(
                "Withholding an answer from the rest of the shortlist needs a "
                "recorded reason. Leave it unset to circulate the answer."
            )
        return self._replace_query(rfq_id, query.model_copy(update={
            "answer": answer.strip(),
            "answered_by": by,
            "answered_at": datetime.now(timezone.utc),
            "restricted_reason": restricted_reason.strip() if restricted_reason else None,
        }))

    def withdraw_query(
        self, rfq_id: str, query_id: str, reason: str, by: str
    ) -> ClarificationQuery:
        query = self._query_or_raise(rfq_id, query_id)
        if query.withdrawn_at is not None:
            raise ValueError(f"{query.number} is already withdrawn.")
        if not reason.strip():
            raise ValueError("Withdrawing a clarification needs a recorded reason.")
        return self._replace_query(rfq_id, query.model_copy(update={
            "withdrawn_reason": reason.strip(),
            "withdrawn_by": by,
            "withdrawn_at": datetime.now(timezone.utc),
        }))

    def queries_for(self, rfq_id: str) -> list[ClarificationQuery]:
        return list(self._queries.get(rfq_id, []))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_clarifications.py -v`
Expected: PASS, 22 tests

- [ ] **Step 5: Commit**

```bash
git add workflow/store.py tests/test_clarifications.py
git commit -m "feat: a query register where the default is that everyone hears the answer"
```

---

### Task 3: A bidder who asked cannot be erased from the register

**Files:**
- Modify: `workflow/store.py` — `remove_shortlist_entry`
- Test: `tests/test_clarifications.py` (append)

**Interfaces:**
- Consumes: `WorkflowStore.queries_for`
- Produces: `remove_shortlist_entry` unchanged in signature; refuses with `ValueError` when the entry has raised any query
- **Store invariant owned:** every `ClarificationQuery.raised_by_entry_id` in `_queries` resolves to a live `ShortlistEntry` of the same RFQ — **exactly**, with no dangling id and no query orphaned by a removal.

Instance four of "nothing is deleted out from under a live reference", after `delete_item`, `delete_project` and `delete_bidder`. The reason is identical: `persistence.save` replaces the document wholesale, so a query left pointing at a removed entry is not merely wrong in memory — it survives the restart as a dangling reference.

**Answered and withdrawn queries block removal too, not only open ones.** A bidder who took part in the clarification round is part of its record; if they decline to bid they remain on the shortlist as a non-bidder, which is the true fact. Erasing them would make the register read as though they had never asked.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_clarifications.py — append


def test_a_bidder_who_raised_a_query_cannot_be_removed_from_the_shortlist():
    store, rfq_id, entry_id = store_with_a_shortlisted_rfq()
    q = store.raise_query(rfq_id, entry_id, question="Which revision?",
                          category="Technical", raised_on=date(2026, 8, 13))
    with pytest.raises(ValueError, match=q.number):
        store.remove_shortlist_entry(rfq_id, entry_id)
    assert len(store.shortlist_for(rfq_id)) == 1


def test_the_guard_holds_for_answered_and_withdrawn_queries_too():
    """A bidder who took part in the round is part of its record. Erasing them
    would make the register read as though they had never asked."""
    store, rfq_id, entry_id = store_with_a_shortlisted_rfq()
    answered = store.raise_query(rfq_id, entry_id, question="a", category="Technical",
                                 raised_on=date(2026, 8, 13))
    store.answer_query(rfq_id, answered.id, answer="Rev. A.", by="buyer@example.com")
    withdrawn = store.raise_query(rfq_id, entry_id, question="b", category="Commercial",
                                  raised_on=date(2026, 8, 13))
    store.withdraw_query(rfq_id, withdrawn.id, reason="Duplicate.", by="buyer@example.com")

    with pytest.raises(ValueError) as exc:
        store.remove_shortlist_entry(rfq_id, entry_id)
    assert "TQ-001" in str(exc.value) and "CQ-001" in str(exc.value)


def test_a_bidder_who_raised_nothing_is_still_removable():
    """The guard has to release as well as hold."""
    store, rfq_id, entry_id = store_with_a_shortlisted_rfq()
    quiet = store.add_shortlist_entry(
        rfq_id, vendor_name="Northwind Valve Works", prequal_status="Approved",
        scope_code_fit=True, included=True,
    )
    store.raise_query(rfq_id, entry_id, question="a", category="Technical",
                      raised_on=date(2026, 8, 13))
    store.remove_shortlist_entry(rfq_id, quiet.id)
    assert [e.id for e in store.shortlist_for(rfq_id)] == [entry_id]


def test_no_query_ever_points_at_an_entry_that_is_gone():
    """The invariant this task owns, asserted over the whole store."""
    store, rfq_id, entry_id = store_with_a_shortlisted_rfq()
    quiet = store.add_shortlist_entry(
        rfq_id, vendor_name="Northwind Valve Works", prequal_status="Approved",
        scope_code_fit=True, included=True,
    )
    store.raise_query(rfq_id, entry_id, question="a", category="Technical",
                      raised_on=date(2026, 8, 13))
    store.remove_shortlist_entry(rfq_id, quiet.id)

    live = {e.id for e in store.shortlist_for(rfq_id)}
    assert all(q.raised_by_entry_id in live for q in store.queries_for(rfq_id))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_clarifications.py -k "removed or removable or points_at" -v`
Expected: FAIL — `DID NOT RAISE <class 'ValueError'>`

- [ ] **Step 3: Write minimal implementation**

Replace `remove_shortlist_entry` in `workflow/store.py`:

```python
    def remove_shortlist_entry(self, rfq_id: str, entry_id: str) -> None:
        """Refused while the entry has raised any clarification.

        Instance four of the rule `delete_item`, `delete_project` and
        `delete_bidder` each state, and the reason is identical:
        `persistence.save` replaces the document wholesale, so a query left
        pointing at a removed entry survives the restart as a dangling
        reference.

        Answered and withdrawn queries hold it too, not only open ones. A
        bidder who took part in the clarification round is part of its record;
        if they decline to bid they stay on the shortlist as a non-bidder,
        which is the true fact.
        """
        entries = self._shortlists.get(rfq_id, [])
        remaining = [e for e in entries if e.id != entry_id]
        if len(remaining) == len(entries):
            raise KeyError(f"Unknown shortlist entry: {entry_id}")

        raised = sorted(
            q.number for q in self._queries.get(rfq_id, [])
            if q.raised_by_entry_id == entry_id
        )
        if raised:
            vendor = next(e.vendor_name for e in entries if e.id == entry_id)
            raise ValueError(
                f"{vendor} cannot be removed from the shortlist: they raised "
                f"{', '.join(raised)}. The clarification register is the record "
                f"of who took part."
            )

        self._shortlists[rfq_id] = remaining
        self._revoke_shortlist_approval(rfq_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_clarifications.py tests/test_shortlist_linking.py tests/test_workflow_store.py -v`
Expected: PASS — the existing shortlist tests remove entries that raised nothing, so none of them regress.

- [ ] **Step 5: Commit**

```bash
git add workflow/store.py tests/test_clarifications.py
git commit -m "feat: a shortlist that will not forget who asked a question"
```

---

### Task 4: Addenda — the one door through a frozen package

**Files:**
- Modify: `workflow/store.py` — `_addenda` in `__init__`, methods after the clarification block
- Test: `tests/test_clarifications.py` (append)

**Interfaces:**
- Consumes: `clarifications.next_addendum_number`, `clarifications.is_draft`, `Addendum`, `Attachment`, `WorkflowStore.get_technical_package`
- Produces:
  - `draft_addendum(rfq_id, revision, summary, attachments, arising_from_query_ids=None, bid_due_date=None, addendum_id=None) -> Addendum`
  - `update_addendum(rfq_id, addendum_id, changes: dict) -> Addendum`
  - `issue_addendum(rfq_id, addendum_id, by) -> Addendum`
  - `delete_addendum(rfq_id, addendum_id) -> None`
  - `addenda_for(rfq_id) -> list[Addendum]`
  - `current_bid_due_date(rfq_id) -> date | None`
- **Store invariant owned:** the stored `TechnicalPackage.revision` for an RFQ **exactly** equals the `revision` of that RFQ's most recently issued addendum, or the originally frozen revision when it has none — and every issued addendum's `supersedes_revision` is the `revision` of the one issued before it.

`set_technical_package` is untouched and keeps refusing a frozen package. `issue_addendum` is the one path that supersedes it, exactly as retender and renegotiate are the only documented backward edges: the invariant is not weakened, it is given one exit that says who used it and why.

The four checks in `issue_addendum` are load-bearing, and check 2 is the one that would actually be lost by moving any of them into the route: two concurrent issues of two drafts, both reading "current revision is B" outside the lock, would both write.

`current_bid_due_date` orders by `issued_at`, not by list position or by number. Drafts do not count — a due date nobody has been told about is not a due date.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_clarifications.py — append


def frozen_rfq() -> tuple[WorkflowStore, str, str]:
    store, rfq_id, entry_id = store_with_a_shortlisted_rfq()
    store.set_technical_package(
        rfq_id, revision="Rev. A", basis_of_design="Battery-limit transmitters.",
        attachments=[Attachment(doc_code="RUU-IO-411", title="IO list", revision="Rev. A")],
    )
    store.freeze_package(rfq_id, by="lead@example.com")
    return store, rfq_id, entry_id


def a_draft(store: WorkflowStore, rfq_id: str, **overrides) -> Addendum:
    defaults = dict(
        revision="Rev. B",
        summary="IO list corrected; two transmitters added.",
        attachments=[Attachment(doc_code="RUU-IO-411", title="IO list", revision="Rev. B")],
    )
    return store.draft_addendum(rfq_id, **{**defaults, **overrides})


def test_a_draft_records_what_it_supersedes_from_the_package_not_the_caller():
    store, rfq_id, _entry_id = frozen_rfq()
    draft = a_draft(store, rfq_id)
    assert draft.number == "ADD-01"
    assert draft.supersedes_revision == "Rev. A"
    assert clarifications.is_draft(draft) is True
    # Nothing has moved yet: the package is still what vendors hold.
    assert store.get_technical_package(rfq_id).revision == "Rev. A"


def test_an_addendum_cannot_be_drafted_against_an_unfrozen_package():
    store, rfq_id, _entry_id = store_with_a_shortlisted_rfq()
    store.set_technical_package(rfq_id, revision="Rev. A", basis_of_design="d",
                                attachments=[])
    with pytest.raises(ValueError, match="frozen"):
        a_draft(store, rfq_id)


def test_a_draft_that_does_not_move_the_revision_is_refused():
    store, rfq_id, _entry_id = frozen_rfq()
    with pytest.raises(ValueError, match="revision"):
        a_draft(store, rfq_id, revision="Rev. A")


def test_a_draft_naming_a_query_of_another_rfq_is_refused():
    store, rfq_id, entry_id = frozen_rfq()
    with pytest.raises(KeyError):
        a_draft(store, rfq_id, arising_from_query_ids=["clq_nobody"])


def test_issuing_supersedes_the_package_and_re_freezes_at_the_new_revision():
    store, rfq_id, _entry_id = frozen_rfq()
    draft = a_draft(store, rfq_id)
    issued = store.issue_addendum(rfq_id, draft.id, by="buyer@example.com")

    assert clarifications.is_draft(issued) is False
    assert issued.issued_by == "buyer@example.com"
    package = store.get_technical_package(rfq_id)
    assert package.revision == "Rev. B"
    assert package.frozen_at is not None
    assert package.frozen_by == "buyer@example.com"
    assert [a.revision for a in package.attachments] == ["Rev. B"]


def test_the_frozen_package_still_refuses_an_ordinary_edit_after_an_addendum():
    """The invariant keeps its teeth. The addendum is a door, not a bypass."""
    store, rfq_id, _entry_id = frozen_rfq()
    store.issue_addendum(rfq_id, a_draft(store, rfq_id).id, by="buyer@example.com")
    with pytest.raises(ValueError, match="frozen"):
        store.set_technical_package(rfq_id, revision="Rev. C", basis_of_design="d",
                                    attachments=[])


def test_a_stale_draft_is_refused_rather_than_rolling_the_package_back():
    """Check 2, and the reason all four live inside the store method: two
    drafts cut against Rev. A, both reading "current is Rev. A" outside a lock,
    would both write and the second would undo the first."""
    store, rfq_id, _entry_id = frozen_rfq()
    first = a_draft(store, rfq_id, revision="Rev. B")
    second = a_draft(store, rfq_id, revision="Rev. B2")
    store.issue_addendum(rfq_id, first.id, by="buyer@example.com")

    with pytest.raises(ValueError, match="Rev. B"):
        store.issue_addendum(rfq_id, second.id, by="buyer@example.com")
    assert store.get_technical_package(rfq_id).revision == "Rev. B"


def test_issuing_refuses_an_attachment_with_no_definite_revision():
    """Issuing re-freezes, and `freeze_package` already refuses to freeze
    without one — the same rule, reused rather than restated."""
    store, rfq_id, _entry_id = frozen_rfq()
    draft = a_draft(store, rfq_id, attachments=[
        Attachment(doc_code="RUU-IO-411", title="IO list", revision=None),
    ])
    with pytest.raises(ValueError, match="RUU-IO-411"):
        store.issue_addendum(rfq_id, draft.id, by="buyer@example.com")
    assert store.get_technical_package(rfq_id).revision == "Rev. A"


def test_an_issued_addendum_can_be_neither_edited_nor_deleted():
    store, rfq_id, _entry_id = frozen_rfq()
    draft = a_draft(store, rfq_id)
    store.issue_addendum(rfq_id, draft.id, by="buyer@example.com")

    with pytest.raises(ValueError, match="issued"):
        store.update_addendum(rfq_id, draft.id, {"summary": "Reworded."})
    with pytest.raises(ValueError, match="issued"):
        store.delete_addendum(rfq_id, draft.id)
    with pytest.raises(ValueError, match="issued"):
        store.issue_addendum(rfq_id, draft.id, by="buyer@example.com")


def test_a_draft_is_editable_partially_and_deletable():
    store, rfq_id, _entry_id = frozen_rfq()
    draft = a_draft(store, rfq_id)
    edited = store.update_addendum(rfq_id, draft.id, {"summary": "Reworded."})
    assert edited.summary == "Reworded."
    assert edited.revision == "Rev. B"        # absent from changes, so untouched
    assert edited.number == "ADD-01"

    store.delete_addendum(rfq_id, draft.id)
    assert store.addenda_for(rfq_id) == []


def test_an_update_cannot_forge_identity_or_issuance():
    store, rfq_id, _entry_id = frozen_rfq()
    draft = a_draft(store, rfq_id)
    for field in ["id", "rfq_id", "number", "issued_at", "issued_by"]:
        with pytest.raises(ValueError, match="cannot be changed"):
            store.update_addendum(rfq_id, draft.id, {field: "forged"})


def test_the_bid_due_date_comes_from_the_latest_issued_addendum_only():
    store, rfq_id, _entry_id = frozen_rfq()
    assert store.current_bid_due_date(rfq_id) is None

    first = a_draft(store, rfq_id, revision="Rev. B", bid_due_date=date(2026, 9, 15))
    store.issue_addendum(rfq_id, first.id, by="buyer@example.com")
    assert store.current_bid_due_date(rfq_id) == date(2026, 9, 15)

    # A draft carrying a later date does not count — nobody has been told.
    a_draft(store, rfq_id, revision="Rev. C", bid_due_date=date(2026, 10, 31))
    assert store.current_bid_due_date(rfq_id) == date(2026, 9, 15)


def test_an_issued_addendum_that_names_no_date_leaves_the_last_one_standing():
    store, rfq_id, _entry_id = frozen_rfq()
    first = a_draft(store, rfq_id, revision="Rev. B", bid_due_date=date(2026, 9, 15))
    store.issue_addendum(rfq_id, first.id, by="buyer@example.com")
    second = a_draft(store, rfq_id, revision="Rev. C")
    store.issue_addendum(rfq_id, second.id, by="buyer@example.com")
    assert store.current_bid_due_date(rfq_id) == date(2026, 9, 15)


def test_the_addenda_chain_records_an_unbroken_revision_trail():
    """The invariant this task owns."""
    store, rfq_id, _entry_id = frozen_rfq()
    for revision in ["Rev. B", "Rev. C", "Rev. D"]:
        draft = a_draft(store, rfq_id, revision=revision)
        store.issue_addendum(rfq_id, draft.id, by="buyer@example.com")

    issued = [a for a in store.addenda_for(rfq_id) if not clarifications.is_draft(a)]
    issued.sort(key=lambda a: a.issued_at)
    assert [a.number for a in issued] == ["ADD-01", "ADD-02", "ADD-03"]
    assert [a.supersedes_revision for a in issued] == ["Rev. A", "Rev. B", "Rev. C"]
    assert store.get_technical_package(rfq_id).revision == issued[-1].revision
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_clarifications.py -k addend -v`
Expected: FAIL — `AttributeError: 'WorkflowStore' object has no attribute 'draft_addendum'`

- [ ] **Step 3: Write minimal implementation**

Add to `WorkflowStore.__init__`:

```python
        self._addenda: dict[str, list[Addendum]] = {}
```

Extend the import: `from workflow.models.clarification import Addendum, ClarificationQuery, QueryCategory`

Add a module-level constant beside `_BIDDER_IMMUTABLE`:

```python
# Identity, parentage, the number already quoted on a document, and the two
# fields that record issuance. An update that could set `issued_at` would let a
# draft claim it went to bidders without the package ever moving.
_ADDENDUM_IMMUTABLE = frozenset({"id", "rfq_id", "number", "issued_at", "issued_by"})
```

Add the methods:

```python
    # -- addenda ----------------------------------------------------------

    def _addendum_or_raise(self, rfq_id: str, addendum_id: str) -> Addendum:
        for addendum in self._addenda.get(rfq_id, []):
            if addendum.id == addendum_id:
                return addendum
        raise KeyError(f"Unknown addendum: {addendum_id}")

    def _frozen_package_or_raise(self, rfq_id: str) -> TechnicalPackage:
        package = self._packages.get(rfq_id)
        if package is None:
            raise ValueError(
                "This RFQ has no technical package, so there is nothing for an "
                "addendum to supersede."
            )
        if package.frozen_at is None:
            raise ValueError(
                "The technical package is not frozen yet. Edit it directly — an "
                "addendum amends what vendors were already given."
            )
        return package

    def draft_addendum(
        self,
        rfq_id: str,
        revision: str,
        summary: str,
        attachments: list[Attachment],
        arising_from_query_ids: list[str] | None = None,
        bid_due_date: date | None = None,
        addendum_id: str | None = None,
    ) -> Addendum:
        """`supersedes_revision` is read from the package, never supplied.

        A caller-supplied "what I am superseding" is a claim; the store already
        knows the answer, and the answer is what makes the addenda list a
        revision trail rather than a pile of assertions.
        """
        if rfq_id not in self._rfqs:
            raise KeyError(f"Unknown RFQ: {rfq_id}")
        package = self._frozen_package_or_raise(rfq_id)
        if not summary.strip():
            raise ValueError("An addendum needs a summary of what changed.")
        if revision.strip() == package.revision:
            raise ValueError(
                f"An addendum must move the revision. The package is already at "
                f"{package.revision}, so bidders would have no way to tell which "
                f"one they hold."
            )
        known = {q.id for q in self._queries.get(rfq_id, [])}
        for query_id in arising_from_query_ids or []:
            if query_id not in known:
                raise KeyError(
                    f"Unknown clarification query for this RFQ: {query_id}"
                )

        addendum = Addendum(
            **_with_id(addendum_id),
            rfq_id=rfq_id,
            number=clarifications.next_addendum_number(self._addenda.get(rfq_id, [])),
            supersedes_revision=package.revision,
            revision=revision.strip(),
            summary=summary.strip(),
            attachments=list(attachments),
            arising_from_query_ids=list(arising_from_query_ids or []),
            bid_due_date=bid_due_date,
        )
        self._addenda.setdefault(rfq_id, []).append(addendum)
        return addendum

    def update_addendum(self, rfq_id: str, addendum_id: str, changes: dict) -> Addendum:
        """Partial, and validating, for the same two reasons as
        `update_project`: an absent field is left alone rather than cleared,
        and rebuilding through the model rather than `model_copy(update=...)`
        is what stops an invalid value reaching the store."""
        addendum = self._addendum_or_raise(rfq_id, addendum_id)
        if addendum.issued_at is not None:
            raise ValueError(
                f"{addendum.number} has been issued and can no longer be edited. "
                f"Bidders hold it."
            )
        _reject_immutable(changes, _ADDENDUM_IMMUTABLE)
        updated = Addendum(**{**addendum.model_dump(), **changes})
        self._addenda[rfq_id] = [
            updated if a.id == addendum_id else a for a in self._addenda.get(rfq_id, [])
        ]
        return updated

    def issue_addendum(self, rfq_id: str, addendum_id: str, by: str) -> Addendum:
        """Supersede the frozen package. The one sanctioned door through the
        immutability rule, exactly as retender and renegotiate are the only
        documented backward edges.

        Four reads gate this write, and all four are here rather than in the
        route because the route runs this whole method inside `locked_update`.
        Check 3 is the one that would actually be lost by splitting them: two
        concurrent issues, each reading "current revision is B" outside the
        lock, would both write and the second would undo the first.
        """
        addendum = self._addendum_or_raise(rfq_id, addendum_id)
        if addendum.issued_at is not None:
            raise ValueError(f"{addendum.number} has already been issued.")

        package = self._frozen_package_or_raise(rfq_id)           # 1
        if addendum.supersedes_revision != package.revision:      # 2
            raise ValueError(
                f"{addendum.number} was drafted against "
                f"{addendum.supersedes_revision}, but the package is now at "
                f"{package.revision}. Redraft it against the current revision."
            )
        if addendum.revision == package.revision:                 # 3
            raise ValueError(
                f"{addendum.number} does not move the revision away from "
                f"{package.revision}."
            )
        missing = [a.doc_code for a in addendum.attachments if not a.revision]  # 4
        if missing:
            raise ValueError(
                f"Cannot issue {addendum.number}: attachments without a definite "
                f"revision: {', '.join(missing)}"
            )

        at = datetime.now(timezone.utc)
        self._packages[rfq_id] = TechnicalPackage(
            rfq_id=rfq_id,
            revision=addendum.revision,
            basis_of_design=package.basis_of_design,
            attachments=list(addendum.attachments),
            frozen_at=at,
            frozen_by=by,
        )
        issued = addendum.model_copy(update={"issued_at": at, "issued_by": by})
        self._addenda[rfq_id] = [
            issued if a.id == addendum_id else a for a in self._addenda.get(rfq_id, [])
        ]
        return issued

    def delete_addendum(self, rfq_id: str, addendum_id: str) -> None:
        addendum = self._addendum_or_raise(rfq_id, addendum_id)
        if addendum.issued_at is not None:
            raise ValueError(
                f"{addendum.number} has been issued and cannot be deleted. "
                f"Bidders hold it."
            )
        self._addenda[rfq_id] = [
            a for a in self._addenda.get(rfq_id, []) if a.id != addendum_id
        ]

    def addenda_for(self, rfq_id: str) -> list[Addendum]:
        return list(self._addenda.get(rfq_id, []))

    def current_bid_due_date(self, rfq_id: str) -> date | None:
        """The operative due date: the latest *issued* addendum that names one.

        Ordered by `issued_at`, not by list position or by number. A draft does
        not count — a due date nobody has been told about is not a due date.
        No field is added to `RfqRecord`: the original due date is set at
        Issued, which this phase does not touch, so a stored field here would
        be half-owned and wrong for every RFQ that has no addendum.
        """
        dated = [
            a for a in self._addenda.get(rfq_id, [])
            if a.issued_at is not None and a.bid_due_date is not None
        ]
        if not dated:
            return None
        return max(dated, key=lambda a: a.issued_at).bid_due_date
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_clarifications.py -v`
Expected: PASS, 40 tests

- [ ] **Step 5: Commit**

```bash
git add workflow/store.py tests/test_clarifications.py
git commit -m "feat: an addendum, which is the only way a frozen package moves"
```

---

### Task 5: Persistence

**Files:**
- Modify: `workflow/persistence.py` — imports, `to_document`, `from_document`
- Test: `tests/test_workflow_persistence.py` (append, before the mutation-matrix sections)

**Interfaces:**
- Consumes: `WorkflowStore._queries`, `WorkflowStore._addenda`
- Produces: `to_document(store)["queries"]`, `to_document(store)["addenda"]`; `from_document` rebuilds both grouped on `rfq_id`
- **Store invariant owned:** `workflow.json` holds **exactly** the queries and addenda the store holds — a round trip neither drops one nor invents one, and every id survives it unchanged.

CLAUDE.md names this file's exact failure mode: a field added to `WorkflowStore.__init__` without a matching line in **both** `to_document` and `from_document` silently fails to survive a restart. Two new fields land here, so both directions are asserted directly.

Ids surviving the round trip is not decoration: `answer_query`, `withdraw_query`, `issue_addendum` and `delete_addendum` all address by id, so a regenerated id would make every "answer this one" hit a different row after a restart.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_persistence.py — append after the registry round-trip tests

# -- clarifications ----------------------------------------------------------


def rfq_with_a_query(root: str):
    """Run 1 for the rows below: a saved store holding one frozen RFQ, one
    invited bidder, one answered query and one issued addendum."""
    with persistence.locked_update(root) as store:
        project, generator, _cable = project_and_items(store)
        rfq = cover_with_rfq(store, project.id, [generator.id])
        store.set_technical_package(
            rfq.id, revision="Rev. A", basis_of_design="2 x 5 MW",
            attachments=[Attachment(doc_code="GEN-DS-01", title="Datasheet",
                                    revision="Rev. A")],
        )
        store.freeze_package(rfq.id, by="lead@example.com")
        entry = store.add_shortlist_entry(
            rfq.id, vendor_name="Al Munara Switchgear LLC", prequal_status="Approved",
            scope_code_fit=True, included=True,
        )
        query = store.raise_query(
            rfq.id, entry.id, question="Confirm the frame size.",
            category="Technical", raised_on=date(2026, 8, 13),
        )
        store.answer_query(rfq.id, query.id, answer="Frame 6.", by="buyer@example.com")
    return rfq.id, entry.id, query.id


def test_a_query_survives_a_round_trip_with_every_field(tmp_path):
    """Compared against the in-memory record, not against a second load — two
    loads of the same broken document agree with each other."""
    store, rfq_id = populated_store()
    entry = store.shortlist_for(rfq_id)[0]
    query = store.raise_query(rfq_id, entry.id, question="Confirm the frame size.",
                              category="Technical", raised_on=date(2026, 8, 13))
    store.answer_query(rfq_id, query.id, answer="Frame 6.", by="buyer@example.com")
    before = store.queries_for(rfq_id)
    persistence.save(str(tmp_path), store)

    assert persistence.load(str(tmp_path)).queries_for(rfq_id) == before
    assert [q.number for q in before] == ["TQ-001"]


def test_an_addendum_survives_a_round_trip_with_every_field(tmp_path):
    root = str(tmp_path)
    rfq_id, _entry_id, _query_id = rfq_with_a_query(root)
    with persistence.locked_update(root) as store:
        draft = store.draft_addendum(
            rfq_id, revision="Rev. B", summary="Frame size corrected.",
            attachments=[Attachment(doc_code="GEN-DS-01", title="Datasheet",
                                    revision="Rev. B")],
            bid_due_date=date(2026, 10, 15),
        )
        store.issue_addendum(rfq_id, draft.id, by="buyer@example.com")

    reloaded = persistence.load(root)
    [addendum] = reloaded.addenda_for(rfq_id)
    assert addendum.number == "ADD-01"
    assert addendum.supersedes_revision == "Rev. A"
    assert addendum.issued_by == "buyer@example.com"
    assert reloaded.current_bid_due_date(rfq_id) == date(2026, 10, 15)
    # The superseded package went with it, so the reloaded store agrees.
    assert reloaded.get_technical_package(rfq_id).revision == "Rev. B"


def test_the_document_carries_both_new_collections(tmp_path):
    root = str(tmp_path)
    rfq_with_a_query(root)
    doc = json.loads(Path(persistence.workflow_path(root)).read_text(encoding="utf-8"))
    assert {"queries", "addenda"} <= set(doc)
    assert doc["queries"][0]["number"] == "TQ-001"
    # Derived, never stored — the load has nothing to keep honest.
    assert "status" not in doc["queries"][0]


def test_a_document_written_before_clarifications_loads_as_an_empty_one(tmp_path):
    """No version bump and no migration: a document with no `queries` key means
    no queries, which is the correct reading of it."""
    root = str(tmp_path)
    rfq_id, _entry_id, _query_id = rfq_with_a_query(root)
    path = Path(persistence.workflow_path(root))
    doc = json.loads(path.read_text(encoding="utf-8"))
    del doc["queries"]
    del doc["addenda"]
    path.write_text(json.dumps(doc), encoding="utf-8")

    loaded = persistence.load(root)
    assert loaded.queries_for(rfq_id) == []
    assert loaded.addenda_for(rfq_id) == []
    assert loaded.list_projects()          # everything else still loaded
    # No migration, so the version never moved to accommodate the new keys.
    assert doc["version"] == 1


def test_clarification_ids_survive_a_round_trip(tmp_path):
    """Every write addresses by id. A regenerated id would make "answer this
    one" hit a different row after a restart — this file's silent failure."""
    root = str(tmp_path)
    rfq_id, _entry_id, query_id = rfq_with_a_query(root)
    with persistence.locked_update(root) as store:
        store.draft_addendum(rfq_id, revision="Rev. B", summary="s", attachments=[])
    before = [a.id for a in persistence.load(root).addenda_for(rfq_id)]

    reloaded = persistence.load(root)
    assert [q.id for q in reloaded.queries_for(rfq_id)] == [query_id]
    assert [a.id for a in reloaded.addenda_for(rfq_id)] == before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_workflow_persistence.py -k "query or addend or collections" -v`
Expected: FAIL — `KeyError: 'queries'` from `test_the_document_carries_both_new_collections`

- [ ] **Step 3: Write minimal implementation**

In `workflow/persistence.py`, extend the import block:

```python
from workflow.models.clarification import Addendum, ClarificationQuery
```

In `to_document`, after `"bid_shortlists"`:

```python
        "queries": [
            q.model_dump(mode="json")
            for queries in store._queries.values()
            for q in queries
        ],
        "addenda": [
            a.model_dump(mode="json")
            for addenda in store._addenda.values()
            for a in addenda
        ],
```

In `from_document`, beside the other grouped rebuilds:

```python
    # `.get` with a default, so a document written before clarifications
    # existed loads as an RFQ with no queries. The correct reading of a missing
    # key, not a migration — which is why `VERSION` does not move.
    for record in doc.get("queries", []):
        query = ClarificationQuery(**record)
        store._queries.setdefault(query.rfq_id, []).append(query)
    for record in doc.get("addenda", []):
        addendum = Addendum(**record)
        store._addenda.setdefault(addendum.rfq_id, []).append(addendum)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_workflow_persistence.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add workflow/persistence.py tests/test_workflow_persistence.py
git commit -m "feat: a register that is still there after a restart"
```

---

### Task 6: The exit gate

**Files:**
- Modify: `workflow/gates.py` — `_clarifications_exit`, one new `_GATES` entry
- Test: `tests/test_workflow_stages.py` (append)

**Interfaces:**
- Consumes: `WorkflowStore.queries_for`, `WorkflowStore.addenda_for`, `clarifications.is_open`, `clarifications.is_draft`
- Produces: `_GATES[(Stage.CLARIFICATIONS, Stage.BIDS_RECEIVED)] = _clarifications_exit`
- **Store invariant owned:** no `RfqRecord` whose `stage` is `BIDS_RECEIVED` or later reached it through a forward transition while any of its queries was Open or any of its addenda was in draft.

The reason names **both** halves when both are true. `_scoping_exit`'s docstring records that naming only the nearer half is the mistake this codebase already shipped: a reader told "queries are open" fixes those, retries, and is refused again for a reason nobody mentioned.

Only the forward edge is gated. `check_gate` short-circuits on `is_backward` before reaching `_GATES`, so a retender out of Evaluation is unaffected — a forward gate that blocked a recovery would leave a stuck RFQ with no way out.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_stages.py — append

from datetime import date

from workflow.gates import check_gate
from workflow.models.rfq import Attachment
from workflow.stages import Stage
from workflow.store import WorkflowStore


def rfq_at_clarifications() -> tuple[WorkflowStore, str, str]:
    store = WorkflowStore()
    project = store.create_project(
        name="Ruwais", code="RUU", client="ADNOC Refining", location="Ruwais",
        live_period_start=date(2026, 1, 1), live_period_end=date(2029, 12, 31),
    )
    item = store.create_item(
        project_id=project.id, item_type="Transmitters", description="d", qty=1,
        uom="lot", discipline="Instrumentation", estimated_value_aed=1,
    )
    rfq = store.create_rfq(
        project_id=project.id, item_ids=[item.id], reference="RUU-RFQ-2026-006",
        package="Field transmitters", discipline="Instrumentation",
        value_estimate_aed=1,
    )
    store.set_technical_package(
        rfq.id, revision="Rev. A", basis_of_design="d",
        attachments=[Attachment(doc_code="IO-411", title="IO list", revision="Rev. A")],
    )
    store.freeze_package(rfq.id, by="lead@example.com")
    entry = store.add_shortlist_entry(
        rfq.id, vendor_name="Al Munara Switchgear LLC", prequal_status="Approved",
        scope_code_fit=True, included=True,
    )
    store.approve_shortlist(rfq.id, by="procurement@example.com")
    store.set_tbe_template(rfq.id, criteria=["Accuracy class"])
    store.transition(rfq.id, Stage.SHORTLISTING, by="buyer@example.com")
    store.transition(rfq.id, Stage.ISSUED, by="buyer@example.com")
    store.transition(rfq.id, Stage.CLARIFICATIONS, by="buyer@example.com")
    return store, rfq.id, entry.id


def test_clarifications_with_nothing_outstanding_passes():
    store, rfq_id, _entry_id = rfq_at_clarifications()
    gate = check_gate(store, rfq_id, Stage.CLARIFICATIONS, Stage.BIDS_RECEIVED)
    assert gate.passed is True


def test_an_open_query_blocks_bids_from_being_opened():
    store, rfq_id, entry_id = rfq_at_clarifications()
    store.raise_query(rfq_id, entry_id, question="Which revision?",
                      category="Technical", raised_on=date(2026, 8, 13))
    gate = check_gate(store, rfq_id, Stage.CLARIFICATIONS, Stage.BIDS_RECEIVED)
    assert gate.passed is False
    assert "TQ-001" in gate.reason


def test_answering_or_withdrawing_every_query_clears_the_gate():
    store, rfq_id, entry_id = rfq_at_clarifications()
    a = store.raise_query(rfq_id, entry_id, question="a", category="Technical",
                          raised_on=date(2026, 8, 13))
    b = store.raise_query(rfq_id, entry_id, question="b", category="Commercial",
                          raised_on=date(2026, 8, 13))
    store.answer_query(rfq_id, a.id, answer="Rev. A.", by="buyer@example.com")
    store.withdraw_query(rfq_id, b.id, reason="Duplicate.", by="buyer@example.com")
    assert check_gate(store, rfq_id, Stage.CLARIFICATIONS, Stage.BIDS_RECEIVED).passed


def test_a_draft_addendum_blocks_bids_from_being_opened():
    store, rfq_id, _entry_id = rfq_at_clarifications()
    store.draft_addendum(rfq_id, revision="Rev. B", summary="s", attachments=[])
    gate = check_gate(store, rfq_id, Stage.CLARIFICATIONS, Stage.BIDS_RECEIVED)
    assert gate.passed is False
    assert "ADD-01" in gate.reason


def test_the_reason_names_both_halves_when_both_are_outstanding():
    """`_scoping_exit`'s lesson: a reader told only the nearer half fixes it,
    retries, and is refused again for a reason nobody mentioned."""
    store, rfq_id, entry_id = rfq_at_clarifications()
    store.raise_query(rfq_id, entry_id, question="a", category="Technical",
                      raised_on=date(2026, 8, 13))
    store.draft_addendum(rfq_id, revision="Rev. B", summary="s", attachments=[])
    reason = check_gate(store, rfq_id, Stage.CLARIFICATIONS, Stage.BIDS_RECEIVED).reason
    assert "TQ-001" in reason and "ADD-01" in reason


def test_the_transition_itself_is_refused_with_the_gates_own_sentence():
    store, rfq_id, entry_id = rfq_at_clarifications()
    store.raise_query(rfq_id, entry_id, question="a", category="Technical",
                      raised_on=date(2026, 8, 13))
    with pytest.raises(ValueError, match="TQ-001"):
        store.transition(rfq_id, Stage.BIDS_RECEIVED, by="buyer@example.com")
    assert store.get_rfq(rfq_id).stage is Stage.CLARIFICATIONS


def test_a_backward_transition_is_never_blocked_by_the_new_gate():
    """Retender is a recovery. A forward gate that blocked one would leave a
    stuck RFQ with no way out."""
    store, rfq_id, entry_id = rfq_at_clarifications()
    store.transition(rfq_id, Stage.BIDS_RECEIVED, by="buyer@example.com")
    bid = store.register_bid(rfq_id, vendor_name="Al Munara", headline_price_aed=1)
    store.select_bids(rfq_id, [bid.id], by="client@example.com", rationale="Only bid")
    store.transition(rfq_id, Stage.EVALUATION, by="buyer@example.com")
    store.raise_query(rfq_id, entry_id, question="late query", category="Technical",
                      raised_on=date(2026, 8, 20))

    store.transition(rfq_id, Stage.ISSUED, by="buyer@example.com", reason="retender")
    assert store.get_rfq(rfq_id).stage is Stage.ISSUED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_workflow_stages.py -k "clarification or query or addendum or halves" -v`
Expected: FAIL — `assert True is False` on `test_an_open_query_blocks_bids_from_being_opened`, because no gate exists for the edge

- [ ] **Step 3: Write minimal implementation**

In `workflow/gates.py`, add the import and the gate, and register it:

```python
from workflow import clarifications


def _clarifications_exit(store: "WorkflowStore", rfq_id: str) -> GateResult:
    """Nothing outstanding before the bids are opened.

    Both halves appear in the reason when both are true. `_scoping_exit`
    records the rule and the mistake behind it: a reader told only the nearer
    half fixes that, retries, and is refused again for something nobody
    mentioned.

    The two failures this prevents are recoverable in principle and
    unrecoverable in practice — once the bids are open, a bidder who never got
    their answer cannot be given one, and a package change that was drafted and
    never issued cannot be issued.
    """
    outstanding = []
    open_queries = sorted(
        q.number for q in store.queries_for(rfq_id) if clarifications.is_open(q)
    )
    if open_queries:
        outstanding.append(f"queries still open ({', '.join(open_queries)})")
    drafts = sorted(
        a.number for a in store.addenda_for(rfq_id) if clarifications.is_draft(a)
    )
    if drafts:
        outstanding.append(f"addenda still in draft ({', '.join(drafts)})")

    if not outstanding:
        return _passed()
    return _blocked(
        "Bids cannot be opened while there are "
        + " and ".join(outstanding)
        + ". Answer or withdraw each query, and issue or delete each draft addendum."
    )
```

```python
_GATES = {
    (Stage.SCOPING, Stage.SHORTLISTING): _scoping_exit,
    (Stage.SHORTLISTING, Stage.ISSUED): _shortlisting_exit,
    (Stage.CLARIFICATIONS, Stage.BIDS_RECEIVED): _clarifications_exit,
    (Stage.BIDS_RECEIVED, Stage.EVALUATION): _bids_received_exit,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_workflow_stages.py tests/test_workflow_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add workflow/gates.py tests/test_workflow_stages.py
git commit -m "feat: a gate that will not open bids over an unanswered question"
```

---

### Task 7: Routes

**Files:**
- Modify: `api/workflow_routes.py` — request models, seven routes, `get_rfq` payload
- Modify: `tests/test_auth_middleware.py` — two probe substitutions
- Test: `tests/test_clarification_endpoints.py` (create)

**Interfaces:**
- Consumes: every store method from Tasks 2 and 4, `persistence.locked_update`, `current_user`
- Produces: the seven routes listed below; `get_rfq`'s payload gains `queries`, `addenda`, `bid_due_date`
- **Store invariant owned:** a refused write route leaves `workflow.json` **byte-identical** — every route runs its whole store method inside `locked_update`, which writes only on a clean exit, so no refusal half-lands.

There is deliberately **no GET** for either collection. `get_rfq` already returns every artifact its gates read, and the gate now reads both.

The auth sweep in `test_auth_middleware.py` asserts `"{" not in probe`, so the two new path parameters make that test fail until they are added. That failure is the point of the assertion — leave it to fail once, then add them.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_clarification_endpoints.py
"""The clarification routes.

Each mirrors one control on the wizard's Clarifications step. Every rule lives
in the store; these assertions are about the HTTP contract — status codes,
attribution to the session user, and the fact that a refusal is a 409 carrying
the store's own sentence rather than a 500.
"""
from datetime import date

import pytest

from tests.test_workflow_endpoints import _client, create_rfq


def frozen_and_shortlisted(client) -> tuple[str, str]:
    rfq_id = create_rfq(client)
    client.put(f"/api/workflow/rfqs/{rfq_id}/technical-package", json={
        "revision": "Rev. A",
        "basis_of_design": "Battery-limit transmitters.",
        "attachments": [{"doc_code": "IO-411", "title": "IO list", "revision": "Rev. A"}],
    })
    client.post(f"/api/workflow/rfqs/{rfq_id}/technical-package/freeze", json={})
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={
        "vendor_name": "Al Munara Switchgear LLC", "prequal_status": "Approved",
        "scope_code_fit": True, "included": True,
    })
    return rfq_id, r.json()["id"]


def test_a_raised_query_comes_back_numbered(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, entry_id = frozen_and_shortlisted(client)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/queries", json={
        "raised_by_entry_id": entry_id, "category": "Technical",
        "question": "Which IO list revision?", "raised_on": "2026-08-13",
    })
    assert r.status_code == 201
    assert r.json()["number"] == "TQ-001"
    assert r.json()["state"] == "Open"


def test_a_query_from_an_unknown_entry_is_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, _entry_id = frozen_and_shortlisted(client)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/queries", json={
        "raised_by_entry_id": "sle_nobody", "category": "Technical",
        "question": "q", "raised_on": "2026-08-13",
    })
    assert r.status_code == 404


def test_answering_attributes_to_the_session_user(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, entry_id = frozen_and_shortlisted(client)
    query_id = client.post(f"/api/workflow/rfqs/{rfq_id}/queries", json={
        "raised_by_entry_id": entry_id, "category": "Technical",
        "question": "q", "raised_on": "2026-08-13",
    }).json()["id"]

    r = client.post(f"/api/workflow/rfqs/{rfq_id}/queries/{query_id}/answer",
                    json={"answer": "Rev. A."})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "Answered"
    assert body["circulated"] is True
    assert body["answered_by"]          # whoever the test client is signed in as


def test_restricting_without_a_reason_is_409(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, entry_id = frozen_and_shortlisted(client)
    query_id = client.post(f"/api/workflow/rfqs/{rfq_id}/queries", json={
        "raised_by_entry_id": entry_id, "category": "Technical",
        "question": "q", "raised_on": "2026-08-13",
    }).json()["id"]

    r = client.post(f"/api/workflow/rfqs/{rfq_id}/queries/{query_id}/answer",
                    json={"answer": "Yes.", "restricted_reason": "   "})
    assert r.status_code == 409
    assert "reason" in r.json()["detail"]


def test_withdrawing_needs_a_reason_and_reports_the_state(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, entry_id = frozen_and_shortlisted(client)
    query_id = client.post(f"/api/workflow/rfqs/{rfq_id}/queries", json={
        "raised_by_entry_id": entry_id, "category": "Technical",
        "question": "q", "raised_on": "2026-08-13",
    }).json()["id"]

    assert client.post(f"/api/workflow/rfqs/{rfq_id}/queries/{query_id}/withdraw",
                       json={"reason": ""}).status_code == 409
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/queries/{query_id}/withdraw",
                    json={"reason": "Duplicate of TQ-001."})
    assert r.status_code == 200
    assert r.json()["state"] == "Withdrawn"


def test_the_rfq_payload_carries_the_register_and_the_due_date(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, entry_id = frozen_and_shortlisted(client)
    client.post(f"/api/workflow/rfqs/{rfq_id}/queries", json={
        "raised_by_entry_id": entry_id, "category": "Technical",
        "question": "q", "raised_on": "2026-08-13",
    })
    body = client.get(f"/api/workflow/rfqs/{rfq_id}").json()
    assert [q["number"] for q in body["queries"]] == ["TQ-001"]
    assert body["addenda"] == []
    assert body["bid_due_date"] is None


def test_an_addendum_is_drafted_issued_and_then_immutable(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, _entry_id = frozen_and_shortlisted(client)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/addenda", json={
        "revision": "Rev. B", "summary": "IO list corrected.",
        "attachments": [{"doc_code": "IO-411", "title": "IO list", "revision": "Rev. B"}],
        "bid_due_date": "2026-10-15",
    })
    assert r.status_code == 201
    addendum_id = r.json()["id"]
    assert r.json()["number"] == "ADD-01"
    assert r.json()["supersedes_revision"] == "Rev. A"
    assert r.json()["draft"] is True

    assert client.patch(f"/api/workflow/rfqs/{rfq_id}/addenda/{addendum_id}",
                        json={"summary": "Reworded."}).status_code == 200
    issued = client.post(f"/api/workflow/rfqs/{rfq_id}/addenda/{addendum_id}/issue",
                         json={})
    assert issued.status_code == 200
    assert issued.json()["draft"] is False

    assert client.patch(f"/api/workflow/rfqs/{rfq_id}/addenda/{addendum_id}",
                        json={"summary": "Again."}).status_code == 409
    assert client.delete(
        f"/api/workflow/rfqs/{rfq_id}/addenda/{addendum_id}").status_code == 409

    body = client.get(f"/api/workflow/rfqs/{rfq_id}").json()
    assert body["technical_package"]["revision"] == "Rev. B"
    assert body["bid_due_date"] == "2026-10-15"


def test_a_draft_addendum_can_be_deleted(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, _entry_id = frozen_and_shortlisted(client)
    addendum_id = client.post(f"/api/workflow/rfqs/{rfq_id}/addenda", json={
        "revision": "Rev. B", "summary": "s", "attachments": [],
    }).json()["id"]

    assert client.delete(
        f"/api/workflow/rfqs/{rfq_id}/addenda/{addendum_id}").status_code == 204
    assert client.get(f"/api/workflow/rfqs/{rfq_id}").json()["addenda"] == []


def test_the_transition_route_surfaces_the_gate_sentence(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, entry_id = frozen_and_shortlisted(client)
    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist/approve", json={})
    client.put(f"/api/workflow/rfqs/{rfq_id}/tbe-template",
               json={"criteria": ["Accuracy class"]})
    for target in ["Shortlisting", "Issued", "Clarifications"]:
        assert client.post(f"/api/workflow/rfqs/{rfq_id}/transition",
                           json={"target": target}).status_code == 200
    client.post(f"/api/workflow/rfqs/{rfq_id}/queries", json={
        "raised_by_entry_id": entry_id, "category": "Technical",
        "question": "q", "raised_on": "2026-08-13",
    })

    r = client.post(f"/api/workflow/rfqs/{rfq_id}/transition",
                    json={"target": "Bids Received"})
    assert r.status_code == 409
    assert "TQ-001" in r.json()["detail"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_clarification_endpoints.py -v`
Expected: FAIL — every POST answers 404 (no such route)

Also run: `python -m pytest tests/test_auth_middleware.py -v` — expected to still pass at this point, and to fail once the routes exist.

- [ ] **Step 3: Write minimal implementation**

Request models, beside the existing ones in `api/workflow_routes.py`:

```python
class QueryIn(BaseModel):
    raised_by_entry_id: str
    category: QueryCategory
    question: str
    raised_on: date


class AnswerIn(BaseModel):
    answer: str
    # Absent means circulate. The screen omits the field rather than sending
    # an empty string, so an eligible answer is never recorded as restricted
    # with no text — the same shape the invite control uses for override_reason.
    restricted_reason: str | None = None


class WithdrawIn(BaseModel):
    reason: str


class AddendumIn(BaseModel):
    revision: str
    summary: str
    attachments: list[Attachment] = []
    arising_from_query_ids: list[str] = []
    bid_due_date: date | None = None


class AddendumPatch(BaseModel):
    """Partial: `model_dump(exclude_unset=True)` is what makes an absent field
    mean "leave it alone" rather than "clear it". The immutable fields are not
    declared here at all, so naming one is a 422 before the store ever sees it."""
    revision: str | None = None
    summary: str | None = None
    attachments: list[Attachment] | None = None
    arising_from_query_ids: list[str] | None = None
    bid_due_date: date | None = None


class IssueIn(BaseModel):
    pass
```

A shared payload helper, near `_bidder_payload`:

```python
def _query_payload(query: ClarificationQuery) -> dict:
    """`state` and `circulated` are computed here rather than stored, so the
    screen never re-derives that withdrawal beats an answer."""
    return {
        **query.model_dump(mode="json"),
        "state": clarifications.state(query),
        "circulated": clarifications.is_circulated(query),
    }


def _addendum_payload(addendum: Addendum) -> dict:
    return {**addendum.model_dump(mode="json"), "draft": clarifications.is_draft(addendum)}
```

The seven routes:

```python
# -- clarifications ----------------------------------------------------------
#
# Same shape as every other write in this module: the whole store method runs
# inside `locked_update`, so the reads that gate it and the write itself are one
# critical section, and a refusal leaves the document untouched.


@router.post("/rfqs/{rfq_id}/queries", status_code=201)
def raise_query(rfq_id: str, body: QueryIn) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            query = store.raise_query(
                rfq_id,
                entry_id=body.raised_by_entry_id,
                question=body.question,
                category=body.category,
                raised_on=body.raised_on,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _query_payload(query)


@router.post("/rfqs/{rfq_id}/queries/{query_id}/answer")
def answer_query(
    rfq_id: str, query_id: str, body: AnswerIn, user: User = Depends(current_user)
) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            query = store.answer_query(
                rfq_id, query_id, answer=body.answer, by=user.email,
                restricted_reason=body.restricted_reason,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _query_payload(query)


@router.post("/rfqs/{rfq_id}/queries/{query_id}/withdraw")
def withdraw_query(
    rfq_id: str, query_id: str, body: WithdrawIn, user: User = Depends(current_user)
) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            query = store.withdraw_query(
                rfq_id, query_id, reason=body.reason, by=user.email
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _query_payload(query)


@router.post("/rfqs/{rfq_id}/addenda", status_code=201)
def draft_addendum(rfq_id: str, body: AddendumIn) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            addendum = store.draft_addendum(
                rfq_id,
                revision=body.revision,
                summary=body.summary,
                attachments=body.attachments,
                arising_from_query_ids=body.arising_from_query_ids,
                bid_due_date=body.bid_due_date,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _addendum_payload(addendum)


@router.patch("/rfqs/{rfq_id}/addenda/{addendum_id}")
def patch_addendum(rfq_id: str, addendum_id: str, body: AddendumPatch) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            addendum = store.update_addendum(
                rfq_id, addendum_id, body.model_dump(exclude_unset=True)
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _addendum_payload(addendum)


@router.post("/rfqs/{rfq_id}/addenda/{addendum_id}/issue")
def issue_addendum(
    rfq_id: str, addendum_id: str, body: IssueIn, user: User = Depends(current_user)
) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            addendum = store.issue_addendum(rfq_id, addendum_id, by=user.email)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _addendum_payload(addendum)


@router.delete("/rfqs/{rfq_id}/addenda/{addendum_id}", status_code=204)
def delete_addendum(rfq_id: str, addendum_id: str) -> None:
    try:
        with persistence.locked_update(_root()) as store:
            store.delete_addendum(rfq_id, addendum_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
```

And in `get_rfq`'s returned dict, after `"bid_selection"`:

```python
        "queries": [_query_payload(q) for q in store.queries_for(rfq_id)],
        "addenda": [_addendum_payload(a) for a in store.addenda_for(rfq_id)],
        # Derived from the latest issued addendum, never stored: see
        # `WorkflowStore.current_bid_due_date`.
        "bid_due_date": (
            d.isoformat() if (d := store.current_bid_due_date(rfq_id)) else None
        ),
```

In `tests/test_auth_middleware.py`, extend the probe:

```python
                .replace("{bidder_id}", "any")
                .replace("{query_id}", "any")
                .replace("{addendum_id}", "any")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_clarification_endpoints.py tests/test_auth_middleware.py tests/test_workflow_endpoints.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/workflow_routes.py tests/test_clarification_endpoints.py tests/test_auth_middleware.py
git commit -m "feat: seven routes for a round that used to happen in email"
```

---

### Task 8: Extract the wizard steps

**Files:**
- Create: `web/src/pages/wizard/types.ts`, `AttachmentTable.tsx`, `ScopingStep.tsx`, `ShortlistingStep.tsx`, `IssuedStep.tsx`
- Modify: `web/src/pages/RfqWizard.tsx` — keep the shell, import the steps
- Test: `web/src/pages/RfqWizard.test.tsx` (unchanged assertions; it must still pass)

**Interfaces:**
- Consumes: nothing new
- Produces: `StepProps` from `wizard/types.ts`; `ScopingStep`, `ShortlistingStep`, `IssuedStep`, `AttachmentTable` as named exports
- **Store invariant owned:** none — this task moves front-end code and touches no stored state. Stated rather than left blank, per the plan template's rule that an unclaimed invariant is the next silent defect: every collection this phase adds is claimed by Tasks 2, 4 and 5.

**This is a pure move.** No behaviour change, no renamed props, no reworded copy. `RfqWizard.tsx` is 902 lines holding four steps; a real Clarifications step takes it past 1 100, and the extraction is what stops the new step landing at the bottom of a file nobody can hold in their head. `AttachmentTable` moves out because the addendum form in Task 9 needs it too.

The proof that it is a pure move is that `RfqWizard.test.tsx` passes untouched. If it needs editing, something changed that should not have.

- [ ] **Step 1: Run the existing test to record the baseline**

Run (from `web/`): `npm test -- RfqWizard`
Expected: PASS. Note the count; it must be identical after the move.

- [ ] **Step 2: Create `wizard/types.ts` and move `AttachmentTable`**

```tsx
// web/src/pages/wizard/types.ts
import type { RfqDetail } from '../../types'

/** What every wizard step is handed. `run` owns the busy flag and the server's
 *  refusal message, so no step re-implements either. */
export type StepProps = {
  data: RfqDetail
  run: (action: () => Promise<unknown>) => Promise<void>
  busy: boolean
  /** The wizard's reload counter. Only steps that load a *second* resource
   *  need it — that resource changes when a write here succeeds. */
  tick: number
}
```

```tsx
// web/src/pages/wizard/AttachmentTable.tsx
import type { JSX } from 'react'
import type { Attachment } from '../../types'

export function AttachmentTable({
  attachments,
  onRemove,
}: {
  attachments: Attachment[]
  onRemove?: (docCode: string) => void
}): JSX.Element {
  // …body moved verbatim from RfqWizard.tsx…
}
```

- [ ] **Step 3: Move the three steps**

Move `ScopingStep` into `wizard/ScopingStep.tsx`, `ShortlistingStep` (with `CANDIDATE_CAP`, `CandidateList`, `CandidateRow`, `UnregisteredVendorRow`) into `wizard/ShortlistingStep.tsx`, and `IssuedStep` into `wizard/IssuedStep.tsx`. Each becomes a named export; each imports `StepProps` from `./types`, its API calls from `../../api`, and its types from `../../types`. Copy every docstring comment across unchanged — they carry the reasoning, and leaving them behind is how the reasoning gets lost.

`RfqWizard.tsx` keeps: `WIZARD_STAGES`, `STEP_BLURB`, the component's state and `run`, the stepper, the banners, the step `<Card>`, the stage history and the "Complete this step" card. Its imports narrow to `fetchRfq`, `transitionRfq`, the primitives, and the four step components.

- [ ] **Step 4: Run the test to verify nothing changed**

Run (from `web/`): `npm test` then `npm run build`
Expected: PASS at the same count as Step 1; `npm run build` type-checks the test files too, so a missed import surfaces there.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/wizard web/src/pages/RfqWizard.tsx
git commit -m "refactor: four steps in four files, so a fifth has somewhere to go"
```

---

### Task 9: The Clarifications step

**Files:**
- Create: `web/src/pages/wizard/ClarificationsStep.tsx`
- Modify: `web/src/types.ts` — `ClarificationQuery`, `Addendum`, three fields on `RfqDetail`
- Modify: `web/src/api.ts` — seven fetchers
- Modify: `web/src/pages/RfqWizard.tsx` — render the step
- Test: `web/src/pages/wizard/ClarificationsStep.test.tsx` (create)

**Interfaces:**
- Consumes: `RfqDetail.queries`, `RfqDetail.addenda`, `RfqDetail.bid_due_date`
- Produces: `raiseQuery`, `answerQuery`, `withdrawQuery`, `draftAddendum`, `updateAddendum`, `issueAddendum`, `deleteAddendum` in `api.ts`; `ClarificationsStep` component
- **Store invariant owned:** none — front-end. See Task 8.

The bidder who raised a query is **picked from the included shortlist**, never typed: the same lesson the registry taught the Shortlisting step. Restricting an answer uses progressive disclosure — a tick that reveals a required reason input — the shape `CandidateRow` already uses for `override_reason`, and the reason field is omitted from the request entirely when empty so a circulated answer is never recorded as restricted with no text.

Withdrawal is a reason input **and** a button, not a bare button. The store refuses without a reason, and a control that cannot succeed is worse than no control.

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/pages/wizard/ClarificationsStep.test.tsx
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ClarificationsStep } from './ClarificationsStep'
import type { RfqDetail } from '../../types'

vi.mock('../../api', () => ({
  raiseQuery: vi.fn().mockResolvedValue({}),
  answerQuery: vi.fn().mockResolvedValue({}),
  withdrawQuery: vi.fn().mockResolvedValue({}),
  draftAddendum: vi.fn().mockResolvedValue({}),
  updateAddendum: vi.fn().mockResolvedValue({}),
  issueAddendum: vi.fn().mockResolvedValue({}),
  deleteAddendum: vi.fn().mockResolvedValue({}),
}))

import { answerQuery, raiseQuery } from '../../api'

const BASE: RfqDetail = {
  rfq: {
    id: 'rfq_1', reference: 'RUU-RFQ-2026-006', project_id: 'prj_1', item_ids: [],
    package: 'Field transmitters', discipline: 'Instrumentation',
    value_estimate_aed: 4_750_000, stage: 'Clarifications', history: [],
  },
  gate: { passed: true, reason: null },
  technical_package: {
    rfq_id: 'rfq_1', revision: 'Rev. A', basis_of_design: 'd',
    attachments: [], frozen_at: '2026-08-01T00:00:00Z', frozen_by: 'lead@example.com',
  },
  shortlist: [{
    id: 'sle_1', rfq_id: 'rfq_1', vendor_id: 'bdr_1',
    vendor_name: 'Al Munara Switchgear LLC', prequal_status: 'Approved',
    scope_code_fit: true, included: true, override_by: null, override_reason: null,
  }],
  shortlist_approved: true,
  tbe_template: null,
  vdrl: [],
  bids: [],
  bid_selection: null,
  queries: [],
  addenda: [],
  bid_due_date: null,
}

const noop = async (action: () => Promise<unknown>) => { await action() }

beforeEach(() => vi.clearAllMocks())

describe('ClarificationsStep', () => {
  it('raises a query against a bidder chosen from the shortlist', async () => {
    render(<ClarificationsStep data={BASE} run={noop} busy={false} tick={0} />)

    fireEvent.change(screen.getByLabelText(/raised by/i), { target: { value: 'sle_1' } })
    fireEvent.change(screen.getByLabelText(/question/i), {
      target: { value: 'Which IO list revision?' },
    })
    fireEvent.click(screen.getByRole('button', { name: /record query/i }))

    expect(raiseQuery).toHaveBeenCalledWith('rfq_1', expect.objectContaining({
      raised_by_entry_id: 'sle_1',
      question: 'Which IO list revision?',
    }))
  })

  it('offers only included bidders as the raiser', () => {
    const data = {
      ...BASE,
      shortlist: [
        ...BASE.shortlist,
        { ...BASE.shortlist[0], id: 'sle_2', vendor_name: 'Excluded Co', included: false },
      ],
    }
    render(<ClarificationsStep data={data} run={noop} busy={false} tick={0} />)
    const options = screen.getAllByRole('option').map((o) => o.textContent)
    expect(options).toContain('Al Munara Switchgear LLC')
    expect(options).not.toContain('Excluded Co')
  })

  it('sends no restricted_reason unless the answer is restricted', async () => {
    const data = {
      ...BASE,
      queries: [{
        id: 'clq_1', rfq_id: 'rfq_1', number: 'TQ-001', raised_by_entry_id: 'sle_1',
        raised_by_name: 'Al Munara Switchgear LLC', raised_on: '2026-08-13',
        category: 'Technical' as const, question: 'Which revision?',
        answer: null, answered_by: null, answered_at: null, restricted_reason: null,
        withdrawn_reason: null, withdrawn_by: null, withdrawn_at: null,
        state: 'Open' as const, circulated: true,
      }],
    }
    render(<ClarificationsStep data={data} run={noop} busy={false} tick={0} />)

    fireEvent.change(screen.getByLabelText(/answer to TQ-001/i), {
      target: { value: 'Rev. A is the issued revision.' },
    })
    fireEvent.click(screen.getByRole('button', { name: /^answer TQ-001$/i }))

    expect(answerQuery).toHaveBeenCalledWith('rfq_1', 'clq_1', {
      answer: 'Rev. A is the issued revision.',
    })
  })

  it('reveals a required reason input only when the answer is restricted', () => {
    const data = {
      ...BASE,
      queries: [{
        id: 'clq_1', rfq_id: 'rfq_1', number: 'TQ-001', raised_by_entry_id: 'sle_1',
        raised_by_name: 'Al Munara Switchgear LLC', raised_on: '2026-08-13',
        category: 'Technical' as const, question: 'Which revision?',
        answer: null, answered_by: null, answered_at: null, restricted_reason: null,
        withdrawn_reason: null, withdrawn_by: null, withdrawn_at: null,
        state: 'Open' as const, circulated: true,
      }],
    }
    render(<ClarificationsStep data={data} run={noop} busy={false} tick={0} />)

    expect(screen.queryByLabelText(/why this answer is not circulated/i)).toBeNull()
    fireEvent.click(screen.getByLabelText(/do not circulate/i))
    expect(screen.getByLabelText(/why this answer is not circulated/i)).toBeTruthy()
  })

  it('shows an issued addendum as read-only and a draft with its controls', () => {
    const common = {
      rfq_id: 'rfq_1', supersedes_revision: 'Rev. A', attachments: [],
      arising_from_query_ids: [], bid_due_date: null,
    }
    const data = {
      ...BASE,
      addenda: [
        { ...common, id: 'add_1', number: 'ADD-01', revision: 'Rev. B',
          summary: 'IO list corrected.', issued_at: '2026-08-16T00:00:00Z',
          issued_by: 'buyer@example.com', draft: false },
        { ...common, id: 'add_2', number: 'ADD-02', revision: 'Rev. C',
          summary: 'Due date extended.', issued_at: null, issued_by: null, draft: true },
      ],
    }
    render(<ClarificationsStep data={data} run={noop} busy={false} tick={0} />)

    expect(screen.getByRole('button', { name: /issue ADD-02/i })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /issue ADD-01/i })).toBeNull()
    expect(screen.getByText(/buyer@example.com/)).toBeTruthy()
  })

  it('states the operative bid due date when an addendum has moved it', () => {
    render(
      <ClarificationsStep
        data={{ ...BASE, bid_due_date: '2026-10-15' }}
        run={noop} busy={false} tick={0}
      />,
    )
    expect(screen.getByText(/2026-10-15/)).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `web/`): `npm test -- ClarificationsStep`
Expected: FAIL — cannot resolve `./ClarificationsStep`

- [ ] **Step 3: Write minimal implementation**

Add to `web/src/types.ts`:

```ts
/** A bidder's question and what was answered.
 *
 *  `state` and `circulated` are computed server-side and sent alongside the
 *  record, never re-derived here: withdrawal beats an answer, and that rule
 *  belongs in one place. `restricted_reason` null means the answer went to the
 *  whole included shortlist — there is deliberately no `circulate` boolean. */
export interface ClarificationQuery {
  id: string
  rfq_id: string
  number: string
  raised_by_entry_id: string
  raised_by_name: string
  raised_on: string
  category: 'Technical' | 'Commercial'
  question: string
  answer: string | null
  answered_by: string | null
  answered_at: string | null
  restricted_reason: string | null
  withdrawn_reason: string | null
  withdrawn_by: string | null
  withdrawn_at: string | null
  state: 'Open' | 'Answered' | 'Withdrawn'
  circulated: boolean
}

/** A numbered amendment to an issued RFQ. `supersedes_revision` and `revision`
 *  together make the addenda list the package's revision trail. `draft` is
 *  computed from `issued_at`; an issued addendum can be neither edited nor
 *  deleted, because bidders hold it. */
export interface Addendum {
  id: string
  rfq_id: string
  number: string
  supersedes_revision: string
  revision: string
  summary: string
  attachments: Attachment[]
  arising_from_query_ids: string[]
  bid_due_date: string | null
  issued_at: string | null
  issued_by: string | null
  draft: boolean
}
```

and three fields on `RfqDetail`:

```ts
  queries: ClarificationQuery[]
  addenda: Addendum[]
  /** The latest issued addendum's due date, or null. Derived server-side; an
   *  RFQ with no addendum has no due date here, and the screen says so rather
   *  than inventing one. */
  bid_due_date: string | null
```

Add to `web/src/api.ts`:

```ts
/* The Clarifications step's writes. The server owns every rule, so these carry
   no validation of their own — including the circulation rule: `answerQuery`
   omits `restricted_reason` entirely rather than sending an empty string, so a
   circulated answer is never recorded as restricted with no text. */

export function raiseQuery(
  rfqId: string,
  body: {
    raised_by_entry_id: string
    category: 'Technical' | 'Commercial'
    question: string
    raised_on: string
  },
): Promise<ClarificationQuery> {
  return sendJson<ClarificationQuery>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/queries`, 'POST', body,
  )
}

export function answerQuery(
  rfqId: string,
  queryId: string,
  body: { answer: string; restricted_reason?: string },
): Promise<ClarificationQuery> {
  return sendJson<ClarificationQuery>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/queries/${encodeURIComponent(queryId)}/answer`,
    'POST', body,
  )
}

export function withdrawQuery(
  rfqId: string, queryId: string, reason: string,
): Promise<ClarificationQuery> {
  return sendJson<ClarificationQuery>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/queries/${encodeURIComponent(queryId)}/withdraw`,
    'POST', { reason },
  )
}

export function draftAddendum(
  rfqId: string,
  body: {
    revision: string
    summary: string
    attachments: Attachment[]
    arising_from_query_ids?: string[]
    bid_due_date?: string | null
  },
): Promise<Addendum> {
  return sendJson<Addendum>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/addenda`, 'POST', body,
  )
}

export function updateAddendum(
  rfqId: string,
  addendumId: string,
  body: Partial<{
    revision: string
    summary: string
    attachments: Attachment[]
    arising_from_query_ids: string[]
    bid_due_date: string | null
  }>,
): Promise<Addendum> {
  return sendJson<Addendum>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/addenda/${encodeURIComponent(addendumId)}`,
    'PATCH', body,
  )
}

export function issueAddendum(rfqId: string, addendumId: string): Promise<Addendum> {
  return sendJson<Addendum>(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/addenda/${encodeURIComponent(addendumId)}/issue`,
    'POST', {},
  )
}

export function deleteAddendum(rfqId: string, addendumId: string): Promise<void> {
  return fetch(
    `/api/workflow/rfqs/${encodeURIComponent(rfqId)}/addenda/${encodeURIComponent(addendumId)}`,
    { method: 'DELETE' },
  ).then(expectNoContent)
}
```

`ClarificationsStep.tsx` renders, in order: the operative bid due date when set; the register (open first, then answered, then withdrawn) with per-open-query answer and withdraw controls; the raise-a-query form whose raiser `<select>` lists only `included` shortlist entries; and the addenda list with a draft form. Sort with an explicit rank rather than relying on array order:

```tsx
const STATE_RANK: Record<string, number> = { Open: 0, Answered: 1, Withdrawn: 2 }
const ordered = [...data.queries].sort(
  (a, b) => STATE_RANK[a.state] - STATE_RANK[b.state] || a.number.localeCompare(b.number),
)
```

The answer control's restriction is local state, and the request omits the field when unticked:

```tsx
run(() =>
  answerQuery(data.rfq.id, query.id, {
    answer: answer.trim(),
    // Omitted entirely when circulated, so an unrestricted answer is never
    // recorded as restricted with no text — the same rule the invite control
    // follows for override_reason.
    ...(restricted ? { restricted_reason: reason.trim() } : {}),
  }),
)
```

Finally, in `RfqWizard.tsx`, replace the placeholder paragraph:

```tsx
        {step === 'Clarifications' ? (
          <ClarificationsStep data={data} run={run} busy={busy} tick={tick} />
        ) : null}
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `web/`): `npm test` then `npm run build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/wizard web/src/pages/RfqWizard.tsx web/src/types.ts web/src/api.ts
git commit -m "feat: a step where a query is asked, answered and circulated"
```

---

### Task 10: The register on the read-only screen

**Files:**
- Modify: `web/src/pages/RfqDetail.tsx`
- Test: `web/src/pages/RfqDetail.test.tsx` (append)

**Interfaces:**
- Consumes: `RfqDetail.queries`, `RfqDetail.addenda`
- Produces: nothing new
- **Store invariant owned:** none — front-end. See Task 8.

`RfqWorkflow` routes an RFQ past Clarifications to `RfqDetail` rather than the wizard. The register is what Evaluation argues from, so it has to be visible there — read-only, with the circulation of each answer stated, because "was this answer given to everyone" is exactly the question a disputed award turns on.

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/pages/RfqDetail.test.tsx — append inside the existing describe

  it('shows the clarification register read-only, with circulation stated', async () => {
    // …extend the mocked fetchRfq payload with one answered-and-circulated
    // query and one restricted one, then:
    expect(await screen.findByText('TQ-001')).toBeTruthy()
    expect(screen.getByText(/circulated/i)).toBeTruthy()
    expect(screen.getByText(/reveals the bidder's own layout/i)).toBeTruthy()
    // Read-only: none of the wizard's controls appear here.
    expect(screen.queryByRole('button', { name: /answer/i })).toBeNull()
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `web/`): `npm test -- RfqDetail`
Expected: FAIL — `Unable to find an element with the text: TQ-001`

- [ ] **Step 3: Write minimal implementation**

Add a `<Card title="Clarification register">` to `RfqDetail.tsx` listing number, bidder, category, question, answer, and circulation — where circulation renders as `circulated` or the `restricted_reason` with `restricted_by` beside it. Add a second card for addenda showing number, `supersedes_revision → revision`, summary and who issued it. Render nothing but a muted "No clarifications were raised." when both are empty, rather than two empty tables.

- [ ] **Step 4: Run test to verify it passes**

Run (from `web/`): `npm test` then `npm run build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/RfqDetail.tsx web/src/pages/RfqDetail.test.tsx
git commit -m "feat: the register stays visible after the round closes"
```

---

### Task 11: A demo whose reason string and data agree

**Files:**
- Modify: `workflow/seed_demo.py`
- Test: `tests/test_seed_demo.py` (append)

**Interfaces:**
- Consumes: `raise_query`, `answer_query`, `draft_addendum`, `issue_addendum`
- Produces: seeded queries and addenda on three RFQs
- **Store invariant owned:** the seeded store satisfies every invariant Tasks 2–6 own — the demo is not a special case, and `test_seed_demo.py` asserts that directly rather than trusting it.

`rfq_ruu02`'s transition reason already claims *"Two technical queries raised on the IO list"*. Seed them, so the claim and the data agree. Give a second RFQ an issued addendum so the revision trail is visible, and a third a draft one so a closed gate is visible on screen rather than only in a test.

Ids are pinned (`query_id=`, `addendum_id=`) for the same reason every other seeded entity's is: a demo that is rehearsed and reseeded needs "answer this one" to hit the same row across builds.

This is invented demo data and is free to be. The constraint CLAUDE.md places on `workflow/avl_import.py` is that facts about **real named companies** are never embellished; queries attributed to those companies inside an obviously fictional project are demo scaffolding of the same kind as the projects and RFQs already there.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_seed_demo.py — append


def test_the_clarifications_rfq_actually_holds_the_queries_it_claims(seeded_store):
    """The transition reason says two technical queries were raised. Before
    this phase that sentence was the only evidence they existed."""
    queries = seeded_store.queries_for("rfq_ruu02")
    assert [q.number for q in queries] == ["TQ-001", "TQ-002"]
    assert [clarifications.state(q) for q in queries] == ["Answered", "Open"]
    assert clarifications.is_circulated(queries[0]) is True


def test_the_seeded_demo_has_a_visibly_closed_clarifications_gate(seeded_store):
    gate = check_gate(seeded_store, "rfq_ruu02", Stage.CLARIFICATIONS,
                      Stage.BIDS_RECEIVED)
    assert gate.passed is False
    assert "TQ-002" in gate.reason


def test_an_issued_addendum_leaves_a_readable_revision_trail(seeded_store):
    issued = [a for a in seeded_store.addenda_for("rfq_ruu01")
              if not clarifications.is_draft(a)]
    assert [a.number for a in issued] == ["ADD-01"]
    package = seeded_store.get_technical_package("rfq_ruu01")
    assert package.revision == issued[0].revision
    assert issued[0].supersedes_revision == "Rev. B"


def test_every_seeded_query_points_at_a_live_shortlist_entry(seeded_store):
    """The invariant Task 3 owns, asserted across the whole seeded store."""
    for rfq in seeded_store.list_rfqs():
        live = {e.id for e in seeded_store.shortlist_for(rfq.id)}
        for query in seeded_store.queries_for(rfq.id):
            assert query.raised_by_entry_id in live
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_seed_demo.py -k "clarification or addendum or query" -v`
Expected: FAIL — `assert [] == ['TQ-001', 'TQ-002']`

- [ ] **Step 3: Write minimal implementation**

In `workflow/seed_demo.py`, after the `rfq_ruu02` transition to `CLARIFICATIONS`, seed the two queries the reason string names. Take the raiser from the shortlist that `_invite` just built rather than hard-coding an entry id, because `_invite` picks from the imported registry and the ids depend on it:

```python
    # The two queries the transition reason above names. Before this they were
    # a sentence with nothing behind it.
    _ruu02_bidders = store.shortlist_for("rfq_ruu02")
    answered = store.raise_query(
        "rfq_ruu02", _ruu02_bidders[0].id, query_id="clq_ruu02_01",
        question=(
            "The IO list shows 42 transmitters and the instrument index shows 40. "
            "Which governs for pricing?"
        ),
        category="Technical", raised_on=date(2026, 8, 10),
    )
    store.answer_query(
        "rfq_ruu02", answered.id,
        answer="The instrument index at Rev. A governs. 40 transmitters.",
        by=BUYER,
    )
    store.raise_query(
        "rfq_ruu02", _ruu02_bidders[min(1, len(_ruu02_bidders) - 1)].id,
        query_id="clq_ruu02_02",
        question=(
            "Confirm whether the cyber-security compliance statement is required "
            "at bid stage or at award."
        ),
        category="Technical", raised_on=date(2026, 8, 12),
    )
```

Give `rfq_ruu01` an issued addendum (it is at Issued with a frozen Rev. B package), and one other RFQ a draft one so a closed gate is visible on screen. Both use pinned ids.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_seed_demo.py -v`
Expected: PASS. Seven of these tests carry `needs_real_avl` and skip without `data/bidders_details/…xlsx`; the new ones must not depend on it — assert on `rfq_ruu02`'s queries via the shortlist the seed built, whatever registry it came from.

- [ ] **Step 5: Commit**

```bash
git add workflow/seed_demo.py tests/test_seed_demo.py
git commit -m "feat: a demo whose two technical queries actually exist"
```

---

### Task 12: The two-run mutation matrix, and the baselines

**Files:**
- Modify: `tests/test_workflow_persistence.py` — a new matrix section
- Modify: `CLAUDE.md` — the invariant list and both baseline rows
- Test: the whole suite, both languages

**Interfaces:**
- Consumes: everything above
- Produces: no new production code
- **Store invariant owned:** the composite — `workflow.json` equals the store after **any** sequence of clarification writes, and no query or addendum survives a mutation that should have removed it.

PLAN-TEMPLATE.md's nine required rows all mutate the extraction pipeline — a newer document revision, a prompt-version bump, an LLM call failing between runs. None of that exists in `workflow.json`, and the existing matrix in this file says so in a comment. What carries across is their **shape**: each catches state that should have left the store and did not. The rows below reproduce that shape against the entities this phase adds.

| mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|
| a query is raised, then its bidder's removal is refused | every `raised_by_entry_id` resolves (Task 3) | the entry is still on disk and the refusal wrote nothing |
| a query is withdrawn, then a new one raised | numbers are never reused (Task 2) | the new number is `TQ-002`, and both rows are on disk |
| an answer is restricted, then re-answered without a reason | circulation is derived from one field (Task 2) | `restricted_reason` is `null` on disk, not `""` |
| an addendum is drafted, then issued | the package revision equals the latest issued addendum's (Task 4) | the reloaded package is at the new revision and frozen |
| a second addendum drafted against the old revision is issued | the stale-draft check runs inside the lock (Task 4) | the refusal leaves the file byte-identical |
| a draft addendum is deleted | the document holds exactly the store's addenda (Task 5) | it is absent from disk, and the issued one is not |
| an RFQ transitions with an open query | no RFQ passes the gate with one outstanding (Task 6) | the stage on disk is still `Clarifications` |
| create → answer → withdraw → draft → issue | the document equals the store (Task 5) | `to_document(load(root))` equals the file byte-for-byte |

Every row reads run 2 **from disk**, never from the store object that made the change. That is the whole point: a single-run assertion passes while the document is already wrong.

- [ ] **Step 1: Write the eight matrix tests**

Follow the existing rows' shape exactly — `with persistence.locked_update(root) as store:` for each run, `persistence.load(root)` for the assertion, and `Path(persistence.workflow_path(root)).read_bytes()` for the byte-identical rows. For example, the stale-draft row:

```python
def test_a_refused_stale_issue_leaves_the_document_untouched(tmp_path):
    """Row 5. The check that would be lost by moving it into the route: two
    drafts cut against the same revision, both reading "current is Rev. A"
    outside a lock, would both write and the second would undo the first."""
    root = str(tmp_path)
    rfq_id, _entry_id, _query_id = rfq_with_a_query(root)
    with persistence.locked_update(root) as store:
        first = store.draft_addendum(rfq_id, revision="Rev. B", summary="s",
                                     attachments=[])
        second = store.draft_addendum(rfq_id, revision="Rev. B2", summary="s",
                                      attachments=[])
        store.issue_addendum(rfq_id, first.id, by="buyer@example.com")

    before = Path(persistence.workflow_path(root)).read_bytes()
    with pytest.raises(ValueError, match="Rev. B"):
        with persistence.locked_update(root) as store:
            store.issue_addendum(rfq_id, second.id, by="buyer@example.com")

    assert Path(persistence.workflow_path(root)).read_bytes() == before
    assert persistence.load(root).get_technical_package(rfq_id).revision == "Rev. B"
```

- [ ] **Step 2: Verify the matrix is real, not decorative**

Reintroduce each defect one at a time and confirm the intended row fails **and that nothing else does**. A row that still passes with its defect reinstated is not testing what it claims. The eight defects to reinstate, one per row:

1. drop the `raised` guard from `remove_shortlist_entry`
2. change `_highest` to `len(same)` in `next_query_number`
3. store `restricted_reason` as `""` rather than `None` when cleared
4. skip the `self._packages[rfq_id] = …` write in `issue_addendum`
5. delete check 2 from `issue_addendum`
6. drop the `"addenda"` line from `to_document`
7. remove the `(CLARIFICATIONS, BIDS_RECEIVED)` entry from `_GATES`
8. drop the `"queries"` line from `from_document`

Record the result in the commit message. A throwaway script that patches, runs, and reverts each one is the cheapest way; the phase-2 fix wave did exactly this.

- [ ] **Step 3: Run both suites in full**

Run: `python -m pytest`
Run (from `web/`): `npm test` and `npm run build`

- [ ] **Step 4: Update `CLAUDE.md`**

Add to the RFQ workflow invariants section, after the frozen-package bullet:

> - **A clarification's state is computed, not stored, and circulation is the
>   default.** There is no `status` field on `ClarificationQuery`: Open,
>   Answered and Withdrawn are derived from three timestamps in
>   `workflow/clarifications.py`, and withdrawal beats an answer. A blank
>   `restricted_reason` is refused, so withholding an answer from the rest of
>   the shortlist is always a recorded, attributed act — the same shape as
>   `override_reason`. **An addendum is the one sanctioned door through the
>   frozen-package rule**: `set_technical_package` still refuses, and
>   `issue_addendum` supersedes at a new revision after four reads that all
>   sit inside the store method. A bidder who raised any query cannot be
>   removed from the shortlist — the fourth instance of "nothing is deleted out
>   from under a live reference", after `delete_item`, `delete_project` and
>   `delete_bidder`.

Then update both baseline rows: **measure** the workstation row with `python -m pytest`, and **derive** the CI row from it by the documented subtraction (`- 4 - 3 - 2 - 9` passes become skips). Do not edit the two rows independently — that is how they drifted apart before. Update the web count from `npm test` as well, and the sentence naming the number of test files.

- [ ] **Step 5: Commit**

```bash
git add tests/test_workflow_persistence.py CLAUDE.md
git commit -m "test: eight mutations, each verified by reinstating its defect"
```

---

## Self-review

**Spec coverage.** Every numbered section of the spec maps to a task: §1 → 1, §2 → 1+2, §3 → 2, §4 → 4, §5 → 6, §6 → 3, §7 → 4, §8 → 2+4, §9 → 5, §10 → 7+8+9+10, §11 → 11+12. The two out-of-scope items (setting an original bid due date, notifying bidders) have no task by design and are stated as gaps in the spec.

**Invariant ownership.** `_queries` → Task 2; the `raised_by_entry_id` reference → Task 3; `_addenda` and the package-revision chain → Task 4; the document → Task 5; the gate's stage guarantee → Task 6; refusals writing nothing → Task 7; the seeded store → Task 11; the composite → Task 12. No invariant is claimed twice. Tasks 8, 9 and 10 are front-end and say so explicitly rather than leaving the bullet blank.

**Type consistency.** `raise_query` takes `entry_id` and the model field is `raised_by_entry_id`; the route model `QueryIn.raised_by_entry_id` maps between them, and `_query_payload` emits the model's name. `state`/`is_open`/`is_circulated`/`is_draft` are the only four derived helpers and are named identically in the store, the gate, the payload builders and the TypeScript interfaces (`state`, `circulated`, `draft`). `current_bid_due_date` is the store method; `bid_due_date` is the payload key and the `Addendum` field — different things, deliberately, and the payload comment says so.
