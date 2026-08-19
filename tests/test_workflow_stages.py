"""The eight-stage RFQ state machine and the gates that guard its edges.

Two rules are load-bearing here and are each asserted directly: a transition
absent from `TRANSITIONS` is denied, and a blocked gate always carries a reason.
"""
from datetime import date

import pytest

from workflow.gates import check_gate
from workflow.models.rfq import Attachment
from workflow.stages import (
    STAGE_CODES,
    STAGE_ORDER,
    TRANSITIONS,
    Stage,
    is_allowed,
    is_backward,
)
from workflow.store import WorkflowStore


def test_eight_stages_in_process_order():
    assert STAGE_ORDER == [
        Stage.SHORTLISTING,
        Stage.ISSUED,
        Stage.CLARIFICATIONS,
        Stage.BIDS_RECEIVED,
        Stage.EVALUATION,
        Stage.NEGOTIATION,
        Stage.AWARDED,
        Stage.PO_ISSUED,
    ]


def test_scoping_is_not_a_stage():
    """An RFQ begins at Shortlisting. `Scoping` duplicated work the item screen
    had already done, and it is gone from the vocabulary rather than merely
    unused — a member nobody transitions to is still a member somebody can
    store."""
    assert "SCOPING" not in Stage.__members__
    assert "Scoping" not in {stage.value for stage in Stage}


def test_no_transition_targets_scoping():
    """Removing the key is half the job. An orphaned *target* is still a
    reachable edge, and `is_allowed` would happily answer yes to it."""
    reachable = {target.value for targets in TRANSITIONS.values() for target in targets}
    assert "Scoping" not in reachable
    assert "Scoping" not in {source.value for source in TRANSITIONS}


def test_each_stage_advances_to_its_successor():
    for current, following in zip(STAGE_ORDER, STAGE_ORDER[1:]):
        assert is_allowed(current, following), f"{current} should advance to {following}"


def test_skipping_a_stage_is_rejected():
    assert not is_allowed(Stage.SHORTLISTING, Stage.CLARIFICATIONS)
    assert not is_allowed(Stage.ISSUED, Stage.EVALUATION)


def test_retender_returns_evaluation_to_issued():
    assert is_allowed(Stage.EVALUATION, Stage.ISSUED)
    assert is_backward(Stage.EVALUATION, Stage.ISSUED)


def test_renegotiation_returns_to_evaluation():
    assert is_allowed(Stage.NEGOTIATION, Stage.EVALUATION)
    assert is_backward(Stage.NEGOTIATION, Stage.EVALUATION)


def test_forward_transitions_are_not_backward():
    assert not is_backward(Stage.SHORTLISTING, Stage.ISSUED)


def test_terminal_stage_has_no_successors():
    assert TRANSITIONS[Stage.PO_ISSUED] == frozenset()


def test_unlisted_transition_is_denied_by_default():
    assert not is_allowed(Stage.PO_ISSUED, Stage.SHORTLISTING)
    assert not is_allowed(Stage.AWARDED, Stage.SHORTLISTING)


def test_every_stage_has_a_transition_entry():
    """Deny-by-default is only safe if `is_allowed` cannot KeyError on a stage."""
    assert set(TRANSITIONS) == set(STAGE_ORDER)


def test_process_codes_are_the_client_document_s_own_and_not_a_position():
    """The codes are references into the client's process document, not an
    ordinal. Two of them cannot be computed from a position at all: bids arrive
    at RFQ-04A, and RFQ-06 covers both Negotiation and Awarded — terms are
    agreed at one and the approval chain completes at the other, under the same
    process step. Anything deriving a code from an index gets four of these
    eight wrong."""
    assert [STAGE_CODES[stage] for stage in STAGE_ORDER] == [
        "RFQ-02",   # Shortlisting
        "RFQ-03",   # Issued
        "RFQ-04",   # Clarifications
        "RFQ-04A",  # Bids Received
        "RFQ-05",   # Evaluation
        "RFQ-06",   # Negotiation
        "RFQ-06",   # Awarded — the same step
        "RFQ-07",   # PO Issued
    ]


def test_every_stage_has_a_process_code():
    """A stage with no code renders as a blank cell on the strip, so a stage
    added to `STAGE_ORDER` without one here is a defect the type system cannot
    catch."""
    assert set(STAGE_CODES) == set(STAGE_ORDER)


def test_stage_values_are_human_readable_strings():
    assert Stage.BIDS_RECEIVED.value == "Bids Received"
    assert Stage.PO_ISSUED.value == "PO Issued"


def gated_store() -> tuple[WorkflowStore, str]:
    store = WorkflowStore()
    project = store.create_project(
        name="Haliba Field Development",
        code="HAL",
        client="Al Dhafra Petroleum",
        location="Haliba field, UAE",
        live_period_start=date(2026, 1, 1),
        live_period_end=date(2029, 12, 31),
    )
    item = store.create_item(
        project_id=project.id,
        item_type="Wellhead tie-in materials",
        description="Wellhead & CGF tie-in materials",
        qty=1,
        uom="lot",
        discipline="Mechanical / piping",
        estimated_value_aed=46_200_000,
    )
    rfq = store.create_rfq(
        project_id=project.id,
        item_ids=[item.id],
        reference="ADP-RFQ-2026-014",
        package="Wellhead & CGF tie-in materials",
        discipline="Mechanical / piping",
        value_estimate_aed=46_200_000,
    )
    return store, rfq.id


def freeze_the_package(store: WorkflowStore, rfq_id: str) -> None:
    store.set_technical_package(
        rfq_id,
        revision="Rev. B",
        basis_of_design="129 wellhead tie-ins, CGF, 16in export line to ASAB",
        attachments=[Attachment(doc_code="HAL-PID-001", title="P&ID", revision="Rev. C")],
    )
    store.freeze_package(rfq_id, by="lead.engineer@example.com")


def issued_store() -> tuple[WorkflowStore, str]:
    """An RFQ that has actually reached Issued, with no TBE template on it.

    Built by transitioning rather than by setting `stage` directly, so the
    fixture proves the edge it depends on is open — an RFQ that cannot be
    issued without a template would make every assertion below vacuous.
    """
    store, rfq_id = gated_store()
    freeze_the_package(store, rfq_id)
    store.add_shortlist_entry(rfq_id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)
    store.approve_shortlist(rfq_id, by="procurement@example.com")
    store.transition(rfq_id, Stage.ISSUED, by="amal@example.com")
    return store, rfq_id


# The two tests that stood here — `test_scoping_gate_blocks_until_the_package_is_frozen`
# and `test_scoping_gate_passes_once_the_package_is_frozen` — are deleted rather
# than updated: the rule they asserted no longer exists. `Scoping` is not a
# stage, so there is no Scoping → Shortlisting edge for a gate to guard, and
# `_shortlisting_exit` is the first gate now. The freeze rule itself survives
# untouched in `test_workflow_store.py` (a frozen package refuses a later edit)
# and moved on screen into the Issued step; what went is the *gate* that made
# freezing a precondition of shortlisting.


def test_shortlisting_gate_blocks_until_the_shortlist_is_approved():
    store, rfq_id = gated_store()
    freeze_the_package(store, rfq_id)
    store.add_shortlist_entry(rfq_id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)

    result = check_gate(store, rfq_id, Stage.SHORTLISTING, Stage.ISSUED)
    assert result.passed is False
    assert "approved" in result.reason.lower()


def test_shortlisting_gate_blocks_when_no_vendor_is_included():
    store, rfq_id = gated_store()
    freeze_the_package(store, rfq_id)
    store.add_shortlist_entry(rfq_id, vendor_name="OQC", prequal_status="Under review",
                              scope_code_fit=False, included=False)

    result = check_gate(store, rfq_id, Stage.SHORTLISTING, Stage.ISSUED)
    assert result.passed is False
    assert "no included vendors" in result.reason.lower()


def test_the_shortlisting_gate_does_not_ask_for_a_tbe_template():
    """It used to, and the deliberate cost of moving it is recorded here: an
    RFQ can now be **issued to vendors** with nobody having settled what they
    must return. The editor sits on the Issued step, so asking for it on the
    way *in* meant refusing a reader on the strength of a control they had not
    reached; the check moved to that step's own exit instead.
    """
    store, rfq_id = gated_store()
    freeze_the_package(store, rfq_id)
    store.add_shortlist_entry(rfq_id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)
    store.approve_shortlist(rfq_id, by="procurement@example.com")

    assert store.get_tbe_template(rfq_id) is None
    assert check_gate(store, rfq_id, Stage.SHORTLISTING, Stage.ISSUED).passed is True


def test_shortlisting_gate_passes_once_the_shortlist_is_approved():
    store, rfq_id = gated_store()
    freeze_the_package(store, rfq_id)
    store.add_shortlist_entry(rfq_id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)
    store.approve_shortlist(rfq_id, by="procurement@example.com")

    assert check_gate(store, rfq_id, Stage.SHORTLISTING, Stage.ISSUED).passed is True


def test_the_issued_gate_is_open_and_asks_for_no_tbe_template():
    """`_issued_exit` is gone, and this is what replaced the two tests that
    drove it.

    It asked whether somebody had settled what bidders must return. The
    eligibility checklist is now `EligibilityCategory` — nine fixed returnables
    that apply to every RFQ and are stored nowhere — so that question is
    answered by construction and there is nothing for a buyer to forget.

    The removal was forced rather than chosen: the free-text editor was the only
    control that could set a template and it was deliberately taken off the
    Issued step, so keeping the gate would have stranded every RFQ at Issued
    with nothing able to unblock it. This test is the assertion that says the
    edge is open, and it fails if the check is ever quietly reinstated.
    """
    store, rfq_id = issued_store()

    assert store.get_tbe_template(rfq_id) is None
    result = check_gate(store, rfq_id, Stage.ISSUED, Stage.CLARIFICATIONS)
    assert result.passed is True
    assert result.reason is None


def test_a_buyers_own_checklist_additions_never_gate_the_rfq():
    """What a buyer adds on top of the nine is *extra*. Blocking an RFQ for
    want of an optional addition would refuse it for something that is not a
    requirement, so attaching one changes nothing about the edge."""
    store, rfq_id = issued_store()
    store.set_tbe_template(rfq_id, items=["Drum lengths"])

    result = check_gate(store, rfq_id, Stage.ISSUED, Stage.CLARIFICATIONS)
    assert result.passed is True


def test_a_blocked_gate_always_carries_a_reason():
    """No bare False: a blocked transition must say what is blocking it."""
    store, rfq_id = gated_store()
    result = check_gate(store, rfq_id, Stage.SHORTLISTING, Stage.ISSUED)
    assert result.passed is False
    assert result.reason


def test_ungated_transitions_pass_without_a_reason():
    # Issued → Clarifications was the example here until the TBE check moved
    # onto it. Awarded → PO Issued carries no gate and is the case this test is
    # actually about: an edge absent from `_GATES` answers "yes", with no reason.
    store, rfq_id = gated_store()
    result = check_gate(store, rfq_id, Stage.AWARDED, Stage.PO_ISSUED)
    assert result.passed is True
    assert result.reason is None


def test_transition_raises_with_the_gate_reason():
    store, rfq_id = gated_store()
    with pytest.raises(ValueError, match="no included vendors"):
        store.transition(rfq_id, Stage.ISSUED, by="amal@example.com")


def test_a_blocked_transition_leaves_the_stage_and_history_untouched():
    store, rfq_id = gated_store()
    before = store.get_rfq(rfq_id)
    with pytest.raises(ValueError):
        store.transition(rfq_id, Stage.ISSUED, by="amal@example.com")
    after = store.get_rfq(rfq_id)
    assert after.stage is Stage.SHORTLISTING
    assert len(after.history) == len(before.history)


def test_backward_transitions_are_not_gated():
    """A retender must not be blocked by the gate that guards going forward."""
    store, rfq_id = gated_store()
    freeze_the_package(store, rfq_id)
    store.add_shortlist_entry(rfq_id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)
    store.approve_shortlist(rfq_id, by="procurement@example.com")
    store.set_tbe_template(rfq_id, items=["Throughput"])
    bid = store.register_bid(rfq_id, vendor_name="Galfar", headline_price_aed=46_200_000)
    store.select_bids(rfq_id, [bid.id], by="client@example.com", rationale="Only compliant bid")
    for target in [Stage.ISSUED, Stage.CLARIFICATIONS, Stage.BIDS_RECEIVED, Stage.EVALUATION]:
        store.transition(rfq_id, target, by="amal@example.com")

    retendered = store.transition(rfq_id, Stage.ISSUED, by="amal@example.com", reason="retender")
    assert retendered.stage is Stage.ISSUED


# -- the Clarifications exit gate --------------------------------------------


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
    store.set_tbe_template(rfq.id, items=["Accuracy class"])
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
    """The lesson the deleted Scoping gate left behind: a reader told only the
    nearer half fixes it, retries, and is refused again for a reason nobody
    mentioned."""
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
