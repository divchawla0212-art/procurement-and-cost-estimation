from enum import Enum


class Stage(str, Enum):
    """The seven process stages. RFQ-06 spans two states — terms are agreed
    at NEGOTIATION and the approval chain completes at AWARDED — so there are
    eight enum members.

    An RFQ begins at SHORTLISTING. The stage that used to come before it is in
    `RETIRED_STAGES`, not here: a member nobody transitions to is still a
    member somebody can store.

    Each member's process code lives in `STAGE_CODES`, not in a comment here —
    the codes are served to the browser, and a comment cannot be."""

    SHORTLISTING = "Shortlisting"
    ISSUED = "Issued"
    CLARIFICATIONS = "Clarifications"
    BIDS_RECEIVED = "Bids Received"
    EVALUATION = "Evaluation"
    NEGOTIATION = "Negotiation"
    AWARDED = "Awarded"
    PO_ISSUED = "PO Issued"


STAGE_ORDER: list[Stage] = [
    Stage.SHORTLISTING,
    Stage.ISSUED,
    Stage.CLARIFICATIONS,
    Stage.BIDS_RECEIVED,
    Stage.EVALUATION,
    Stage.NEGOTIATION,
    Stage.AWARDED,
    Stage.PO_ISSUED,
]

# Where each stage sits in the client's process document.
#
# **These are references, not ordinals.** Nothing here can be computed from a
# stage's position, and two members say so plainly: bids arrive at RFQ-04A,
# which no counter produces, and RFQ-06 covers both NEGOTIATION and AWARDED
# because agreeing terms and completing the approval chain are two states of
# one process step. A screen that numbered the stages it was sent would have
# been wrong about four of these eight even before `Scoping` left — removing it
# only moved the first error to the top of the strip, where it showed.
#
# Served by `/api/workflow/stages` and by the RFQ roster, for the same reason
# `selectable_approvers` and `client_approver` are: the browser must not spell
# the process's own vocabulary, or a re-lettered step becomes an edit in two
# repositories.
STAGE_CODES: dict[Stage, str] = {
    Stage.SHORTLISTING: "RFQ-02",
    Stage.ISSUED: "RFQ-03",
    Stage.CLARIFICATIONS: "RFQ-04",
    Stage.BIDS_RECEIVED: "RFQ-04A",
    Stage.EVALUATION: "RFQ-05",
    Stage.NEGOTIATION: "RFQ-06",
    Stage.AWARDED: "RFQ-06",
    Stage.PO_ISSUED: "RFQ-07",
}

# Deny by default: a transition absent from this table is rejected.
# Backward edges are the documented recoveries — retender and renegotiate.
# Every stage needs an entry, including the terminal one, so that `is_allowed`
# answers "no" rather than raising.
TRANSITIONS: dict[Stage, frozenset[Stage]] = {
    Stage.SHORTLISTING: frozenset({Stage.ISSUED}),
    Stage.ISSUED: frozenset({Stage.CLARIFICATIONS}),
    Stage.CLARIFICATIONS: frozenset({Stage.BIDS_RECEIVED}),
    Stage.BIDS_RECEIVED: frozenset({Stage.EVALUATION}),
    Stage.EVALUATION: frozenset({Stage.NEGOTIATION, Stage.ISSUED}),
    Stage.NEGOTIATION: frozenset({Stage.AWARDED, Stage.EVALUATION}),
    Stage.AWARDED: frozenset({Stage.PO_ISSUED}),
    Stage.PO_ISSUED: frozenset(),
}


# Stages that were once part of the process and still appear in stored history.
#
# History is append-only, so an entry naming `Scoping` is a record of something
# that happened and must not be restated as something else. `Stage` is the
# vocabulary of the process *now*; this is the vocabulary an old document may
# still be written in. `StageTransition` admits a plain string only when it is
# in here — every other unknown stage name is still a validation error, which is
# what keeps a typo from silently loading as a stage nobody can transition out
# of.
#
# Nothing may be added here to keep a live stage working. A member belongs here
# only after it has been removed from `Stage`.
RETIRED_STAGES: dict[str, Stage] = {
    # Removed with Bid Desk's BD-4: it duplicated work the item screen had
    # already done. `persistence.load` moves an RFQ still sitting here.
    "Scoping": Stage.SHORTLISTING,
}


def is_allowed(source: Stage, target: Stage) -> bool:
    return target in TRANSITIONS[source]


def is_backward(source: Stage, target: Stage) -> bool:
    return STAGE_ORDER.index(target) < STAGE_ORDER.index(source)
