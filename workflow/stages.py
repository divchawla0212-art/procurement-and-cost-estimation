from enum import Enum


class Stage(str, Enum):
    """The eight process stages. RFQ-06 spans two states — terms are agreed
    at NEGOTIATION and the approval chain completes at AWARDED — so there are
    nine enum members."""

    SCOPING = "Scoping"                # RFQ-01
    SHORTLISTING = "Shortlisting"      # RFQ-02
    ISSUED = "Issued"                  # RFQ-03
    CLARIFICATIONS = "Clarifications"  # RFQ-04
    BIDS_RECEIVED = "Bids Received"    # RFQ-04A
    EVALUATION = "Evaluation"          # RFQ-05
    NEGOTIATION = "Negotiation"        # RFQ-06
    AWARDED = "Awarded"                # RFQ-06
    PO_ISSUED = "PO Issued"            # RFQ-07


STAGE_ORDER: list[Stage] = [
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

# Deny by default: a transition absent from this table is rejected.
# Backward edges are the documented recoveries — retender and renegotiate.
# Every stage needs an entry, including the terminal one, so that `is_allowed`
# answers "no" rather than raising.
TRANSITIONS: dict[Stage, frozenset[Stage]] = {
    Stage.SCOPING: frozenset({Stage.SHORTLISTING}),
    Stage.SHORTLISTING: frozenset({Stage.ISSUED}),
    Stage.ISSUED: frozenset({Stage.CLARIFICATIONS}),
    Stage.CLARIFICATIONS: frozenset({Stage.BIDS_RECEIVED}),
    Stage.BIDS_RECEIVED: frozenset({Stage.EVALUATION}),
    Stage.EVALUATION: frozenset({Stage.NEGOTIATION, Stage.ISSUED}),
    Stage.NEGOTIATION: frozenset({Stage.AWARDED, Stage.EVALUATION}),
    Stage.AWARDED: frozenset({Stage.PO_ISSUED}),
    Stage.PO_ISSUED: frozenset(),
}


def is_allowed(source: Stage, target: Stage) -> bool:
    return target in TRANSITIONS[source]


def is_backward(source: Stage, target: Stage) -> bool:
    return STAGE_ORDER.index(target) < STAGE_ORDER.index(source)
