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
from workflow.store import WorkflowStore


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


# -- the store ---------------------------------------------------------------
#
# Every refusal below is a read that gates a write, and lives in the store
# method rather than the route: the route runs the whole method inside
# `persistence.locked_update`, which makes the check and the write one critical
# section. The same rule the auth store and `delete_item` each state.


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
    original = store.get_rfq(rfq_id)
    other = store.create_rfq(
        project_id=original.project_id, item_ids=list(original.item_ids),
        reference="RUU-RFQ-2026-007", package="Cable", discipline="Electrical",
        value_estimate_aed=1,
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
