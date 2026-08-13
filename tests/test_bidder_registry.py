"""The organisation-wide bidder registry in `WorkflowStore`.

The invariant these tests defend: **`_bidders` holds exactly the bidders nobody
has deleted, and no bidder is deleted while a shortlist entry references it.**

That second half is the third time this repository has needed the rule —
`delete_item` and `delete_project` are the other two — and the reason is the
same each time. `persistence.save` replaces the document wholesale, so a
shortlist entry pointing at a bidder that is gone does not merely look wrong in
memory: it survives the restart as a dangling reference.
"""
from datetime import date

import pytest

from workflow.store import WorkflowStore

TODAY = date(2026, 8, 13)


def a_store() -> WorkflowStore:
    return WorkflowStore()


def with_an_rfq(store: WorkflowStore):
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
        item_type="LV switchgear",
        description="LV switchboards and MCCs",
        qty=6,
        uom="ea",
        discipline="Electrical",
        estimated_value_aed=4_000_000,
    )
    return store.create_rfq(
        project_id=project.id,
        item_ids=[item.id],
        reference="ADP-RFQ-2026-014",
        package="LV switchgear",
        discipline="Electrical",
        value_estimate_aed=4_000_000,
    )


def a_bidder(store: WorkflowStore, **overrides):
    defaults = dict(
        name="Al Munara Switchgear LLC",
        country="United Arab Emirates",
        trade_categories=["Electrical"],
        prequal_status="Approved",
        prequal_expires_on=date(2027, 3, 31),
    )
    return store.create_bidder(**{**defaults, **overrides})


# -- creating and reading ----------------------------------------------------


def test_a_created_bidder_is_retrievable_by_id():
    store = a_store()
    bidder = a_bidder(store)
    assert store.get_bidder(bidder.id) == bidder
    assert store.list_bidders() == [bidder]


def test_an_unknown_bidder_reads_as_none_rather_than_raising():
    assert a_store().get_bidder("bdr_nope") is None


def test_ids_are_prefixed_and_distinct():
    store = a_store()
    first = a_bidder(store, name="Al Munara Switchgear LLC")
    second = a_bidder(store, name="Northwind Valve Works")
    assert first.id.startswith("bdr_")
    assert first.id != second.id


def test_the_registry_is_not_scoped_to_a_project():
    """A prequalification is an organisation-level fact. If the registry were
    per-project the same company would be re-entered and re-approved for every
    one of them, which is the thing this replaces."""
    store = a_store()
    with_an_rfq(store)
    bidder = a_bidder(store)
    assert store.list_bidders() == [bidder]


# -- updating ----------------------------------------------------------------


def test_an_update_leaves_absent_fields_alone():
    store = a_store()
    bidder = a_bidder(store, notes="Preferred on HAL")
    updated = store.update_bidder(bidder.id, {"prequal_status": "Suspended"})
    assert updated.prequal_status == "Suspended"
    assert updated.notes == "Preferred on HAL"
    assert updated.name == bidder.name


def test_an_update_validates_rather_than_storing_anything():
    """Rebuilt through the model, not `model_copy(update=...)` — which skips
    validation and would store a status no `PrequalStatus` allows."""
    store = a_store()
    bidder = a_bidder(store)
    with pytest.raises(Exception):
        store.update_bidder(bidder.id, {"prequal_status": "Sort of approved"})
    assert store.get_bidder(bidder.id).prequal_status == "Approved"


def test_identity_cannot_be_edited():
    store = a_store()
    bidder = a_bidder(store)
    with pytest.raises(ValueError, match="cannot be changed"):
        store.update_bidder(bidder.id, {"id": "bdr_elsewhere"})


def test_updating_an_unknown_bidder_raises():
    with pytest.raises(KeyError):
        a_store().update_bidder("bdr_nope", {"country": "Oman"})


# -- deleting ----------------------------------------------------------------


def test_an_unreferenced_bidder_can_be_deleted():
    store = a_store()
    bidder = a_bidder(store)
    store.delete_bidder(bidder.id)
    assert store.list_bidders() == []


def test_deleting_an_unknown_bidder_raises():
    with pytest.raises(KeyError):
        a_store().delete_bidder("bdr_nope")


def test_a_bidder_on_a_shortlist_cannot_be_deleted_and_the_reason_names_the_rfq():
    store = a_store()
    rfq = with_an_rfq(store)
    bidder = a_bidder(store)
    store.add_shortlist_entry(rfq.id, vendor_id=bidder.id, as_of=TODAY)

    with pytest.raises(ValueError) as excinfo:
        store.delete_bidder(bidder.id)

    # A refusal that does not say what would unblock it leaves the reader
    # nothing to act on.
    assert rfq.reference in str(excinfo.value)
    assert store.get_bidder(bidder.id) is not None


def test_removing_the_last_reference_releases_the_bidder():
    store = a_store()
    rfq = with_an_rfq(store)
    bidder = a_bidder(store)
    entry = store.add_shortlist_entry(rfq.id, vendor_id=bidder.id, as_of=TODAY)
    store.remove_shortlist_entry(rfq.id, entry.id)

    store.delete_bidder(bidder.id)
    assert store.get_bidder(bidder.id) is None


def test_a_free_text_entry_of_the_same_name_does_not_hold_a_bidder():
    """Entries bind by id, never by name. A vendor name is user-supplied text
    and two companies can share one."""
    store = a_store()
    rfq = with_an_rfq(store)
    bidder = a_bidder(store, name="Al Munara Switchgear LLC")
    store.add_shortlist_entry(
        rfq.id,
        vendor_name="Al Munara Switchgear LLC",
        prequal_status="Qualified",
        scope_code_fit=True,
        included=True,
    )
    store.delete_bidder(bidder.id)
    assert store.get_bidder(bidder.id) is None


def test_rfqs_inviting_names_every_referencing_rfq_once_and_in_order():
    store = a_store()
    first = with_an_rfq(store)
    second = store.create_rfq(
        project_id=first.project_id,
        item_ids=list(first.item_ids),
        reference="ADP-RFQ-2026-003",
        package="LV switchgear",
        discipline="Electrical",
        value_estimate_aed=1_000_000,
    )
    bidder = a_bidder(store)
    store.add_shortlist_entry(second.id, vendor_id=bidder.id, as_of=TODAY)
    store.add_shortlist_entry(first.id, vendor_id=bidder.id, as_of=TODAY)

    assert store.rfqs_inviting(bidder.id) == ["ADP-RFQ-2026-003", "ADP-RFQ-2026-014"]


def test_rfqs_inviting_an_unreferenced_bidder_is_empty():
    store = a_store()
    with_an_rfq(store)
    assert store.rfqs_inviting(a_bidder(store).id) == []
