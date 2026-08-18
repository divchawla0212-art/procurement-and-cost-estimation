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
