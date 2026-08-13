"""Linking a shortlist entry to a registry bidder.

Two invariants are defended here.

**A linked entry's snapshot comes from the registry, never from the caller.**
When `vendor_id` is present, `vendor_name`, `prequal_status` and
`scope_code_fit` are derived at the moment of adding and any contradicting
values in the call are ignored. Without this the registry is decoration: a
caller could link an entry to a suspended bidder, label it "Qualified", and the
shortlist would read as clean.

**Inviting a blocked bidder requires an override reason.** `ShortlistEntry`'s
docstring has always claimed that including a vendor against the prequal signal
is "a positive, attributed act". Nothing enforced it until now.

Both are reads that gate a write, so both live in the store method — which the
route runs inside `locked_update`. A guard in the route would be a second
critical section, which is the defect class CLAUDE.md records this repository
shipping twice.

The free-text path — no `vendor_id` — is unchanged, and the last test here says
so. Eligibility can only be judged against a registry record, so the new rules
attach to the link, not to the call.
"""
from datetime import date

import pytest

from workflow.store import WorkflowStore

TODAY = date(2026, 8, 13)


def a_store_with_an_rfq() -> tuple[WorkflowStore, str]:
    store = WorkflowStore()
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
    rfq = store.create_rfq(
        project_id=project.id,
        item_ids=[item.id],
        reference="ADP-RFQ-2026-014",
        package="LV switchgear",
        discipline="Electrical",
        value_estimate_aed=4_000_000,
    )
    return store, rfq.id


def a_bidder(store: WorkflowStore, **overrides):
    defaults = dict(
        name="Al Munara Switchgear LLC",
        country="United Arab Emirates",
        trade_categories=["Electrical"],
        prequal_status="Approved",
        prequal_expires_on=date(2027, 3, 31),
    )
    return store.create_bidder(**{**defaults, **overrides})


# -- the snapshot comes from the registry ------------------------------------


def test_a_linked_entry_takes_its_name_and_prequal_from_the_registry():
    store, rfq_id = a_store_with_an_rfq()
    bidder = a_bidder(store)
    entry = store.add_shortlist_entry(rfq_id, vendor_id=bidder.id, as_of=TODAY)

    assert entry.vendor_id == bidder.id
    assert entry.vendor_name == "Al Munara Switchgear LLC"
    assert entry.prequal_status == "Approved"
    assert entry.scope_code_fit is True
    assert entry.included is True


def test_a_contradicting_prequal_in_the_call_is_ignored_not_stored():
    """The whole point of the registry. A caller cannot label a suspended
    bidder as qualified by saying so."""
    store, rfq_id = a_store_with_an_rfq()
    bidder = a_bidder(store, prequal_status="Suspended")
    entry = store.add_shortlist_entry(
        rfq_id,
        vendor_id=bidder.id,
        vendor_name="Something Else Entirely",
        prequal_status="Qualified",
        scope_code_fit=True,
        override_reason="Sole source for this switchgear frame size",
        as_of=TODAY,
    )
    assert entry.vendor_name == "Al Munara Switchgear LLC"
    assert entry.prequal_status == "Suspended"


def test_a_linked_entry_records_the_derived_scope_mismatch():
    store, rfq_id = a_store_with_an_rfq()
    bidder = a_bidder(store, trade_categories=["Civil"])
    entry = store.add_shortlist_entry(
        rfq_id, vendor_id=bidder.id, scope_code_fit=True, as_of=TODAY
    )
    assert entry.scope_code_fit is False


def test_an_expired_prequalification_is_snapshotted_as_expired():
    """`Expired` is not a stored `PrequalStatus`, but it is the true state at
    the moment of the decision, and the entry records decisions."""
    store, rfq_id = a_store_with_an_rfq()
    bidder = a_bidder(store, prequal_expires_on=date(2026, 4, 30))
    entry = store.add_shortlist_entry(
        rfq_id, vendor_id=bidder.id, override_reason="Renewal in progress", as_of=TODAY
    )
    assert entry.prequal_status == "Expired"


def test_an_unknown_vendor_id_raises():
    store, rfq_id = a_store_with_an_rfq()
    with pytest.raises(KeyError, match="bdr_nope"):
        store.add_shortlist_entry(rfq_id, vendor_id="bdr_nope", as_of=TODAY)


# -- an override is required for a blocked bidder ----------------------------


def test_a_blocked_bidder_is_refused_and_the_blocker_is_the_reason():
    store, rfq_id = a_store_with_an_rfq()
    bidder = a_bidder(store, on_hold=True, hold_reason="Unresolved dispute on HAL-19")

    with pytest.raises(ValueError) as excinfo:
        store.add_shortlist_entry(rfq_id, vendor_id=bidder.id, as_of=TODAY)

    assert "Unresolved dispute on HAL-19" in str(excinfo.value)
    assert store.shortlist_for(rfq_id) == []


def test_an_override_lets_a_blocked_bidder_in_and_is_recorded():
    store, rfq_id = a_store_with_an_rfq()
    bidder = a_bidder(store, prequal_status="Suspended")
    entry = store.add_shortlist_entry(
        rfq_id,
        vendor_id=bidder.id,
        override_by="procurement@example.com",
        override_reason="Sole source for this frame size; suspension is administrative",
        as_of=TODAY,
    )
    assert entry.override_by == "procurement@example.com"
    assert "Sole source" in entry.override_reason


def test_a_blank_override_reason_is_not_an_override():
    store, rfq_id = a_store_with_an_rfq()
    bidder = a_bidder(store, prequal_status="Suspended")
    with pytest.raises(ValueError):
        store.add_shortlist_entry(
            rfq_id, vendor_id=bidder.id, override_reason="   ", as_of=TODAY
        )


def test_a_scope_mismatch_alone_needs_no_override():
    """A caution is not a refusal. Requiring an override here would make the
    common case of a bidder expanding their trade feel like a violation."""
    store, rfq_id = a_store_with_an_rfq()
    bidder = a_bidder(store, trade_categories=["Civil"])
    entry = store.add_shortlist_entry(rfq_id, vendor_id=bidder.id, as_of=TODAY)
    assert entry.override_reason is None


# -- interaction with the existing rules -------------------------------------


def test_a_linked_add_revokes_shortlist_approval():
    """Approval is of a specific set of vendors and cannot outlive an edit to
    it. Linking does not change that."""
    store, rfq_id = a_store_with_an_rfq()
    first = a_bidder(store)
    store.add_shortlist_entry(rfq_id, vendor_id=first.id, as_of=TODAY)
    store.approve_shortlist(rfq_id, by="procurement@example.com")
    assert store.is_shortlist_approved(rfq_id)

    second = a_bidder(store, name="Northwind Valve Works")
    store.add_shortlist_entry(rfq_id, vendor_id=second.id, as_of=TODAY)
    assert not store.is_shortlist_approved(rfq_id)


def test_the_same_bidder_may_be_added_twice_and_the_store_does_not_dedupe():
    """A duplicate is a user error the screen can show. Silently collapsing it
    here would make a removal ambiguous — which of the two did you mean?"""
    store, rfq_id = a_store_with_an_rfq()
    bidder = a_bidder(store)
    first = store.add_shortlist_entry(rfq_id, vendor_id=bidder.id, as_of=TODAY)
    second = store.add_shortlist_entry(rfq_id, vendor_id=bidder.id, as_of=TODAY)
    assert first.id != second.id
    assert len(store.shortlist_for(rfq_id)) == 2


def test_an_entry_survives_the_bidder_being_renamed_and_keeps_its_snapshot():
    store, rfq_id = a_store_with_an_rfq()
    bidder = a_bidder(store, name="Al Munara Switchgear LLC")
    entry = store.add_shortlist_entry(rfq_id, vendor_id=bidder.id, as_of=TODAY)
    store.update_bidder(bidder.id, {"name": "Al Munara Electrical Industries LLC"})

    stored = store.shortlist_for(rfq_id)[0]
    # Bound by id, so it still resolves; the recorded name is the one that was
    # true when the decision was made, which is what an audit trail is for.
    assert stored.vendor_id == bidder.id
    assert stored.vendor_name == "Al Munara Switchgear LLC"


# -- the free-text path is untouched -----------------------------------------


def test_a_free_text_entry_behaves_exactly_as_before():
    store, rfq_id = a_store_with_an_rfq()
    entry = store.add_shortlist_entry(
        rfq_id,
        vendor_name="A one-off fabricator",
        prequal_status="Not qualified",
        scope_code_fit=False,
        included=True,
    )
    # No registry record exists to judge against, so nothing is checked and
    # nothing is overwritten — including a status the registry would block.
    assert entry.vendor_id is None
    assert entry.vendor_name == "A one-off fabricator"
    assert entry.prequal_status == "Not qualified"
    assert entry.override_reason is None
