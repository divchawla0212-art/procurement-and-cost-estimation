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
