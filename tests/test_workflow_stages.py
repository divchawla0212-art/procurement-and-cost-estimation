"""The eight-stage RFQ state machine and the gates that guard its edges.

Two rules are load-bearing here and are each asserted directly: a transition
absent from `TRANSITIONS` is denied, and a blocked gate always carries a reason.
"""
from datetime import date

import pytest

from workflow.gates import check_gate
from workflow.models.rfq import Attachment
from workflow.stages import (
    STAGE_ORDER,
    TRANSITIONS,
    Stage,
    is_allowed,
    is_backward,
)
from workflow.store import WorkflowStore


def test_nine_stages_in_process_order():
    assert STAGE_ORDER == [
        Stage.SCOPING,
        Stage.SHORTLISTING,
        Stage.ISSUED,
        Stage.CLARIFICATIONS,
        Stage.BIDS_RECEIVED,
        Stage.EVALUATION,
        Stage.NEGOTIATION,
        Stage.AWARDED,
        Stage.PO_ISSUED,
    ]


def test_each_stage_advances_to_its_successor():
    for current, following in zip(STAGE_ORDER, STAGE_ORDER[1:]):
        assert is_allowed(current, following), f"{current} should advance to {following}"


def test_skipping_a_stage_is_rejected():
    assert not is_allowed(Stage.SCOPING, Stage.ISSUED)
    assert not is_allowed(Stage.ISSUED, Stage.EVALUATION)


def test_retender_returns_evaluation_to_issued():
    assert is_allowed(Stage.EVALUATION, Stage.ISSUED)
    assert is_backward(Stage.EVALUATION, Stage.ISSUED)


def test_renegotiation_returns_to_evaluation():
    assert is_allowed(Stage.NEGOTIATION, Stage.EVALUATION)
    assert is_backward(Stage.NEGOTIATION, Stage.EVALUATION)


def test_forward_transitions_are_not_backward():
    assert not is_backward(Stage.SCOPING, Stage.SHORTLISTING)


def test_terminal_stage_has_no_successors():
    assert TRANSITIONS[Stage.PO_ISSUED] == frozenset()


def test_unlisted_transition_is_denied_by_default():
    assert not is_allowed(Stage.PO_ISSUED, Stage.SCOPING)
    assert not is_allowed(Stage.AWARDED, Stage.SHORTLISTING)


def test_every_stage_has_a_transition_entry():
    """Deny-by-default is only safe if `is_allowed` cannot KeyError on a stage."""
    assert set(TRANSITIONS) == set(STAGE_ORDER)


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


def test_scoping_gate_blocks_until_the_package_is_frozen():
    store, rfq_id = gated_store()
    result = check_gate(store, rfq_id, Stage.SCOPING, Stage.SHORTLISTING)
    assert result.passed is False
    assert "frozen" in result.reason.lower()


def test_scoping_gate_passes_once_the_package_is_frozen():
    store, rfq_id = gated_store()
    freeze_the_package(store, rfq_id)
    assert check_gate(store, rfq_id, Stage.SCOPING, Stage.SHORTLISTING).passed is True


def test_shortlisting_gate_blocks_until_the_shortlist_is_approved():
    store, rfq_id = gated_store()
    freeze_the_package(store, rfq_id)
    store.transition(rfq_id, Stage.SHORTLISTING, by="amal@example.com")
    store.add_shortlist_entry(rfq_id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)

    result = check_gate(store, rfq_id, Stage.SHORTLISTING, Stage.ISSUED)
    assert result.passed is False
    assert "approved" in result.reason.lower()


def test_shortlisting_gate_blocks_when_no_vendor_is_included():
    store, rfq_id = gated_store()
    freeze_the_package(store, rfq_id)
    store.transition(rfq_id, Stage.SHORTLISTING, by="amal@example.com")
    store.add_shortlist_entry(rfq_id, vendor_name="OQC", prequal_status="Under review",
                              scope_code_fit=False, included=False)

    result = check_gate(store, rfq_id, Stage.SHORTLISTING, Stage.ISSUED)
    assert result.passed is False
    assert "no included vendors" in result.reason.lower()


def test_issuance_gate_blocks_without_a_tbe_template():
    store, rfq_id = gated_store()
    freeze_the_package(store, rfq_id)
    store.transition(rfq_id, Stage.SHORTLISTING, by="amal@example.com")
    store.add_shortlist_entry(rfq_id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)
    store.approve_shortlist(rfq_id, by="procurement@example.com")

    result = check_gate(store, rfq_id, Stage.SHORTLISTING, Stage.ISSUED)
    assert result.passed is False
    assert "tbe" in result.reason.lower()


def test_issuance_gate_passes_with_shortlist_approved_and_tbe_present():
    store, rfq_id = gated_store()
    freeze_the_package(store, rfq_id)
    store.transition(rfq_id, Stage.SHORTLISTING, by="amal@example.com")
    store.add_shortlist_entry(rfq_id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)
    store.approve_shortlist(rfq_id, by="procurement@example.com")
    store.set_tbe_template(rfq_id, criteria=["Throughput", "Materials"])

    assert check_gate(store, rfq_id, Stage.SHORTLISTING, Stage.ISSUED).passed is True


def test_a_blocked_gate_always_carries_a_reason():
    """No bare False: a blocked transition must say what is blocking it."""
    store, rfq_id = gated_store()
    result = check_gate(store, rfq_id, Stage.SCOPING, Stage.SHORTLISTING)
    assert result.passed is False
    assert result.reason


def test_ungated_transitions_pass_without_a_reason():
    store, rfq_id = gated_store()
    result = check_gate(store, rfq_id, Stage.ISSUED, Stage.CLARIFICATIONS)
    assert result.passed is True
    assert result.reason is None


def test_transition_raises_with_the_gate_reason():
    store, rfq_id = gated_store()
    with pytest.raises(ValueError, match="frozen"):
        store.transition(rfq_id, Stage.SHORTLISTING, by="amal@example.com")


def test_a_blocked_transition_leaves_the_stage_and_history_untouched():
    store, rfq_id = gated_store()
    before = store.get_rfq(rfq_id)
    with pytest.raises(ValueError):
        store.transition(rfq_id, Stage.SHORTLISTING, by="amal@example.com")
    after = store.get_rfq(rfq_id)
    assert after.stage is Stage.SCOPING
    assert len(after.history) == len(before.history)


def test_backward_transitions_are_not_gated():
    """A retender must not be blocked by the gate that guards going forward."""
    store, rfq_id = gated_store()
    freeze_the_package(store, rfq_id)
    store.transition(rfq_id, Stage.SHORTLISTING, by="amal@example.com")
    store.add_shortlist_entry(rfq_id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)
    store.approve_shortlist(rfq_id, by="procurement@example.com")
    store.set_tbe_template(rfq_id, criteria=["Throughput"])
    for target in [Stage.ISSUED, Stage.CLARIFICATIONS, Stage.BIDS_RECEIVED, Stage.EVALUATION]:
        store.transition(rfq_id, target, by="amal@example.com")

    retendered = store.transition(rfq_id, Stage.ISSUED, by="amal@example.com", reason="retender")
    assert retendered.stage is Stage.ISSUED
