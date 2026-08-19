from typing import TYPE_CHECKING

from pydantic import BaseModel

from workflow import clarifications
from workflow.stages import Stage, is_backward

if TYPE_CHECKING:  # avoids a circular import at runtime
    from workflow.store import WorkflowStore


class GateResult(BaseModel):
    """A gate never returns a bare False — a blocked transition must say what
    is blocking it, so the caller can show the user something actionable."""

    passed: bool
    reason: str | None = None


def _passed() -> GateResult:
    return GateResult(passed=True)


def _blocked(reason: str) -> GateResult:
    return GateResult(passed=False, reason=reason)


# `_scoping_exit` stood here and required a frozen technical package before an
# RFQ could be shortlisted. `Scoping` is no longer a stage, so there is no edge
# left for it to guard. The freeze rule itself is unchanged — a frozen package
# still refuses a later edit, and an addendum is still the one sanctioned way
# it moves — but freezing is no longer a precondition of anything, and the
# editor that does it now lives in the Issued step.


def _shortlisting_exit(store: "WorkflowStore", rfq_id: str) -> GateResult:
    entries = store.shortlist_for(rfq_id)
    if not any(e.included for e in entries):
        return _blocked("The shortlist contains no included vendors.")
    if not store.is_shortlist_approved(rfq_id):
        return _blocked("The shortlist must be approved by procurement before issuance.")
    # The TBE template was asked for here and no longer is. Its editor is on the
    # Issued step, so this gate refused a reader on the strength of a control
    # they had not reached; the check moved to `_issued_exit`, which guards the
    # edge out of the step that holds it. The deliberate cost is that an RFQ can
    # be issued to vendors before anyone has settled what they must return.
    return _passed()


# `_issued_exit` stood here and asked for a TBE template. It is gone, and
# `Issued → Clarifications` is now ungated.
#
# The check asked whether somebody had settled what bidders must return. That
# question is now answered by construction: the eligibility checklist is
# `EligibilityCategory` — nine fixed returnables that apply to every RFQ,
# stored nowhere and impossible to omit — so there is nothing left for a buyer
# to forget and nothing for a gate to catch. What a buyer may still add on top
# (`TbeTemplate.items`) is *extra*, and blocking an RFQ for want of an optional
# addition would refuse it for a reason that is not a requirement.
#
# Removing it was forced rather than chosen. The free-text editor was the only
# control anywhere that could set a template, and it was deliberately removed
# from the Issued step; leaving the gate would have stranded every RFQ at
# Issued with nothing able to unblock it. That is exactly the trap BD-4
# recorded when the Scoping gate and the only screen that could satisfy it were
# both up for deletion — the difference is that there the check moved, and here
# the thing it checked for stopped being optional at all.


def _clarifications_exit(store: "WorkflowStore", rfq_id: str) -> GateResult:
    """Nothing outstanding before the bids are opened.

    Both halves appear in the reason when both are true. The deleted Scoping
    gate is where that rule came from, and the mistake behind it: a reader told
    only the nearer half fixes that, retries, and is refused again for
    something nobody mentioned.

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


def _bids_received_exit(store: "WorkflowStore", rfq_id: str) -> GateResult:
    if store.get_bid_shortlist(rfq_id) is None:
        return _blocked(
            "Bids must be selected for evaluation before evaluation can begin."
        )
    return _passed()


# Only forward transitions are gated. Backward transitions (retender,
# renegotiate) are recoveries and must not be blocked by a forward gate.
_GATES = {
    (Stage.SHORTLISTING, Stage.ISSUED): _shortlisting_exit,
    (Stage.CLARIFICATIONS, Stage.BIDS_RECEIVED): _clarifications_exit,
    (Stage.BIDS_RECEIVED, Stage.EVALUATION): _bids_received_exit,
}


def check_gate(
    store: "WorkflowStore", rfq_id: str, source: Stage, target: Stage
) -> GateResult:
    if is_backward(source, target):
        return _passed()
    gate = _GATES.get((source, target))
    if gate is None:
        return _passed()
    return gate(store, rfq_id)
