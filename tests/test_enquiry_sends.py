import pytest

from workflow.models.mail import SentRef
from workflow.store import WorkflowStore


def a_store():
    """One project, one item, one RFQ, one shortlisted vendor."""
    store = WorkflowStore()
    project = store.create_project(
        name="Haliba", code="HAL", client="Al Dhafra Petroleum",
        location="Haliba field, UAE",
        live_period_start=__import__("datetime").date(2026, 1, 1),
        live_period_end=__import__("datetime").date(2029, 12, 31),
    )
    item = store.create_item(
        project_id=project.id, item_type="Cable", description="HV cable",
        qty=1, uom="lot", discipline="Electrical", estimated_value_aed=1_000,
    )
    rfq = store.create_rfq(
        project_id=project.id, item_ids=[item.id], reference="ADP-RFQ-2026-014",
        package="HV cable", discipline="Electrical", value_estimate_aed=1_000,
    )
    entry = store.add_shortlist_entry(
        rfq.id, vendor_name="Galfar", prequal_status="Qualified",
        scope_code_fit=True, included=True,
    )
    return store, rfq.id, entry.id


def a_ref(message_id="<a@example.com>"):
    return SentRef(message_id=message_id, transport="outbox", location="/tmp/a.eml")


def test_a_send_is_recorded_against_its_rfq_and_shortlist_entry():
    store, rfq_id, entry_id = a_store()

    record = store.record_enquiry_send(
        rfq_id, shortlist_entry_id=entry_id, vendor_name="Galfar",
        to=["sales@example.com"], ref=a_ref(), by="buyer@adp.ae",
    )

    assert record.rfq_id == rfq_id
    assert record.shortlist_entry_id == entry_id
    assert record.to == ["sales@example.com"]
    assert record.transport == "outbox"
    assert [r.id for r in store.enquiry_sends_for(rfq_id)] == [record.id]


def test_a_send_against_an_unknown_rfq_is_refused():
    store, _, entry_id = a_store()
    with pytest.raises(KeyError, match="Unknown RFQ"):
        store.record_enquiry_send(
            "rfq_nope", shortlist_entry_id=entry_id, vendor_name="Galfar",
            to=["sales@example.com"], ref=a_ref(), by="buyer@adp.ae",
        )


def test_the_same_shortlist_entry_cannot_be_sent_to_twice():
    """The duplicate guard is in the store, not the route: it is a read that
    gates a write, and splitting them makes two critical sections."""
    store, rfq_id, entry_id = a_store()
    store.record_enquiry_send(
        rfq_id, shortlist_entry_id=entry_id, vendor_name="Galfar",
        to=["sales@example.com"], ref=a_ref(), by="buyer@adp.ae",
    )

    with pytest.raises(ValueError, match="already"):
        store.record_enquiry_send(
            rfq_id, shortlist_entry_id=entry_id, vendor_name="Galfar",
            to=["sales@example.com"], ref=a_ref("<b@example.com>"), by="buyer@adp.ae",
        )


def test_already_sent_entry_ids_answers_what_the_dispatch_will_skip():
    store, rfq_id, entry_id = a_store()
    assert store.already_sent_entry_ids(rfq_id) == set()

    store.record_enquiry_send(
        rfq_id, shortlist_entry_id=entry_id, vendor_name="Galfar",
        to=["sales@example.com"], ref=a_ref(), by="buyer@adp.ae",
    )
    assert store.already_sent_entry_ids(rfq_id) == {entry_id}


def test_removing_the_shortlist_entry_leaves_its_send_record():
    """The mail was sent. Deleting the record would falsify that — the
    append-only rule the stage history keeps, in a second place."""
    store, rfq_id, entry_id = a_store()
    store.record_enquiry_send(
        rfq_id, shortlist_entry_id=entry_id, vendor_name="Galfar",
        to=["sales@example.com"], ref=a_ref(), by="buyer@adp.ae",
    )

    store.remove_shortlist_entry(rfq_id, entry_id)

    assert len(store.enquiry_sends_for(rfq_id)) == 1


# -- sending the same enquiry a second time, deliberately ----------------------


def _send(store, rfq_id, entry_id, message_id, **over):
    return store.record_enquiry_send(
        rfq_id, shortlist_entry_id=entry_id, vendor_name="Galfar",
        to=["sales@example.com"], ref=a_ref(message_id), by="buyer@adp.ae",
        **over,
    )


def test_a_second_send_to_the_same_entry_is_still_refused_by_default():
    """The accident guard is unchanged. Re-sending a tender is a deliberate
    act, so it must not be something a stray second click can do."""
    store, rfq_id, entry_id = a_store()
    _send(store, rfq_id, entry_id, "<1@x>")

    with pytest.raises(ValueError) as exc:
        _send(store, rfq_id, entry_id, "<2@x>")
    assert "already been sent" in str(exc.value)


def test_a_deliberate_resend_is_recorded_as_a_second_record():
    """**The invariant this changes.** The collection held at most one record
    per shortlist entry; it now holds one per *send*.

    Refusing the second record would have left a mail that really went out
    unrecorded, and overwriting the first would have destroyed the fact that
    the vendor was written to on the earlier date. Both are falsifications of
    a log whose whole purpose is to say what actually happened.
    """
    store, rfq_id, entry_id = a_store()
    first = _send(store, rfq_id, entry_id, "<1@x>")
    second = _send(store, rfq_id, entry_id, "<2@x>", resend=True)

    records = store.enquiry_sends_for(rfq_id)
    assert len(records) == 2
    assert [r.id for r in records] == [first.id, second.id]
    assert [r.message_id for r in records] == ["<1@x>", "<2@x>"]


def test_a_resend_does_not_rewrite_the_first_record():
    """Append-only, still. The earlier send keeps its own timestamp and its own
    message id — a reader asking "when did this vendor first get the enquiry?"
    must not be answered with the resend's date."""
    store, rfq_id, entry_id = a_store()
    first = _send(store, rfq_id, entry_id, "<1@x>")
    sent_at, message_id = first.sent_at, first.message_id

    _send(store, rfq_id, entry_id, "<2@x>", resend=True)

    reread = store.enquiry_sends_for(rfq_id)[0]
    assert reread.sent_at == sent_at
    assert reread.message_id == message_id


def test_an_entry_sent_twice_is_still_reported_once_as_already_sent():
    """`already_sent_entry_ids` answers *whether*, not *how many times*. It is
    a set, and a vendor sent twice is no more 'already sent' than one sent
    once — the skip reason would read identically either way."""
    store, rfq_id, entry_id = a_store()
    _send(store, rfq_id, entry_id, "<1@x>")
    _send(store, rfq_id, entry_id, "<2@x>", resend=True)

    assert store.already_sent_entry_ids(rfq_id) == {entry_id}


def test_a_resend_to_an_entry_never_sent_is_allowed_and_is_just_a_send():
    """`resend` widens what is permitted; it does not demand a prior send.
    A dispatch asked to re-send everybody covers the vendors added since,
    rather than refusing them for having no earlier record."""
    store, rfq_id, entry_id = a_store()

    record = _send(store, rfq_id, entry_id, "<1@x>", resend=True)

    assert record.shortlist_entry_id == entry_id
    assert len(store.enquiry_sends_for(rfq_id)) == 1
