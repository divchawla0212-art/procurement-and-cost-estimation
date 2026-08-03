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

    Both refusals raise before the transaction opens, so a rejected note
    leaves the store untouched rather than rolled back. They raise different
    types because callers must tell them apart: a blank reason is the caller's
    input error, an unknown vendor is a missing addressee, and an HTTP front
    end owes those two different status codes. String-matching one message to
    decide would break the next time the wording changes.
    """
    if not (reason or "").strip():
        raise ValueError("a feedback note needs a reason")
    facts = snapshots.load_facts(root, slug, vendor)
    if facts is None:
        raise LookupError(f"no facts stored for {vendor}")
    with snapshots.transaction(root, slug):
        facts.technical_feedback = text
        snapshots.save_facts(root, slug, facts)
        events.append_event(root, slug, Event(
            at=_now(), run_id=actor, actor=actor,
            action="facts.feedback_edited", target=vendor,
            detail={"reason": reason.strip(), "text": text}))
