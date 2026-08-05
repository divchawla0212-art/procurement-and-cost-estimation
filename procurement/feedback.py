"""The one write on the comparative statement: a reviewer's technical note.

This lived in `portal/views/statement.py` while Streamlit was the only front
end. It is domain logic, not presentation — it opens a store transaction and
appends an audit event — so it belongs beside the rest of the store writers,
where every front end can reach it and the store invariants are reviewed.
"""
from procurement.pipeline import _now
from procurement.store import events, snapshots
from procurement.store.models import Event


def save_feedback(root: str, slug: str, vendor: str, text: str, reason: str,
                  actor: str = "api") -> None:
    """Record `text` as `vendor`'s technical feedback (INV-S5).

    One transaction, so `generation` bumps exactly once, and the reason lands
    in the event rather than on the field: `VendorFacts` has no per-field
    metadata slot, and the History screen is where a rationale is read.

    The blank-reason refusal raises before the transaction opens: it is a
    caller input error that never needed the vendor lock. The unknown-vendor
    refusal raises *inside* the transaction, from `update_facts`'s
    `create=False` default -- a check at this call site would be a TOCTOU
    (the vendor could be pruned between the check and the lock acquire). It
    is still safe: the body raising withholds the generation bump and
    `update_facts` writes nothing, so a rejected note leaves the store
    untouched rather than rolled back. They raise different types because
    callers must tell them apart: a blank reason is the caller's input error,
    an unknown vendor is a missing addressee, and an HTTP front end owes
    those two different status codes. String-matching one message to decide
    would break the next time the wording changes.
    """
    if not (reason or "").strip():
        raise ValueError("a feedback note needs a reason")
    with snapshots.transaction(root, slug):
        # Only this field. Everything else is the run's or renormalize's, and
        # writing it back from a copy read a moment ago is BUG-010.
        with snapshots.update_facts(root, slug, vendor) as facts:
            facts.technical_feedback = text
        events.append_event(root, slug, Event(
            at=_now(), run_id=actor, actor=actor,
            action="facts.feedback_edited", target=vendor,
            detail={"reason": reason.strip(), "text": text}))
