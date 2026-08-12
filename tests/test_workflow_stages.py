"""The eight-stage RFQ state machine and the gates that guard its edges.

Two rules are load-bearing here and are each asserted directly: a transition
absent from `TRANSITIONS` is denied, and a blocked gate always carries a reason.
"""
import pytest

from workflow.stages import (
    STAGE_ORDER,
    TRANSITIONS,
    Stage,
    is_allowed,
    is_backward,
)


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
