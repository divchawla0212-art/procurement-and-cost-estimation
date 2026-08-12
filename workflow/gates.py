from typing import TYPE_CHECKING

from pydantic import BaseModel

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


def _scoping_exit(store: "WorkflowStore", rfq_id: str) -> GateResult:
    package = store.get_technical_package(rfq_id)
    if package is None:
        # Name the whole exit criterion, not just the nearer half of it. A user
        # told only "no package" has to guess that attaching one is not enough.
        return _blocked(
            "No technical package has been attached to this RFQ; one must be "
            "attached and frozen before shortlisting can begin."
        )
    if package.frozen_at is None:
        return _blocked("The technical package must be frozen before shortlisting can begin.")
    return _passed()


def _shortlisting_exit(store: "WorkflowStore", rfq_id: str) -> GateResult:
    entries = store.shortlist_for(rfq_id)
    if not any(e.included for e in entries):
        return _blocked("The shortlist contains no included vendors.")
    if not store.is_shortlist_approved(rfq_id):
        return _blocked("The shortlist must be approved by procurement before issuance.")
    if store.get_tbe_template(rfq_id) is None:
        return _blocked("A TBE template must be attached before the RFQ can be issued.")
    return _passed()


# Only forward transitions are gated. Backward transitions (retender,
# renegotiate) are recoveries and must not be blocked by a forward gate.
_GATES = {
    (Stage.SCOPING, Stage.SHORTLISTING): _scoping_exit,
    (Stage.SHORTLISTING, Stage.ISSUED): _shortlisting_exit,
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
