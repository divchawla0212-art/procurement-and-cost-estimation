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


def test_a_withdrawn_query_keeps_its_number_and_the_next_one_follows_it():
    existing = [
        a_query(number="TQ-001"),
        a_query(number="TQ-002", withdrawn_reason="r", withdrawn_by="b",
                withdrawn_at=datetime(2026, 8, 15, tzinfo=timezone.utc)),
    ]
    assert clarifications.next_query_number(existing, "Technical") == "TQ-003"


def test_a_gap_in_the_sequence_does_not_reissue_a_number_already_handed_out():
    """The case that separates `max + 1` from `count + 1`, and the only one
    that does: while the sequence is dense the two agree, so a test over
    TQ-001 and TQ-002 would pass under either and prove nothing.

    A gap is reachable. `workflow.json` is documented as human-readable and
    gets hand-inspected, `from_document` loads whatever numbers it finds, and
    `_highest` deliberately tolerates rows it cannot parse. Counting here would
    return TQ-003 — a number a bidder is already holding in writing.
    """
    existing = [a_query(number="TQ-001"), a_query(number="TQ-003")]
    assert clarifications.next_query_number(existing, "Technical") == "TQ-004"


def test_a_gap_in_the_addendum_sequence_is_treated_the_same_way():
    existing = [an_addendum(number="ADD-01"), an_addendum(number="ADD-04")]
    assert clarifications.next_addendum_number(existing) == "ADD-05"


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


# -- nothing deleted out from under a live reference, instance four -----------


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


# -- addenda: the one sanctioned door through a frozen package ----------------


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
    store, rfq_id, _entry_id = frozen_rfq()
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
