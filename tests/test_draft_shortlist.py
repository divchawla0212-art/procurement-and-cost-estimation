"""The draft shortlist an **item** owns, before any RFQ exists.

A buyer assembles a vendor selection on the item screen and it has to still be
there after a refresh, a sign-out and a restart — a twenty-five vendor
selection lost to a reload is the failure this collection exists to prevent.
So it is stored, and the two rules about what is stored are what this file
defends:

**I-A** — `workflow.json` holds exactly the draft entries whose item still
exists. `delete_item` pops the draft, and `delete_project` reaches through the
item cascade to take its items' drafts with it. The disk half of that rule is
asserted in `tests/test_workflow_persistence.py`; here it is asserted against
the store.

**I-B** — an item's draft holds exactly one entry per vendor, keyed on
`vendor_id` where there is one and on `vendor_name` where there is not. A
second add of the same vendor is a **no-op, not a second row**, and the check
lives in the store method rather than in the route: a check in the caller and a
write in the store are two critical sections, so two concurrent adds would each
read "not there yet" and both write. Every test below calls the store directly,
which is exactly what a route-level check would fail.

Key-free and fixture-free: nothing here reads an export or calls a provider.
"""
from datetime import date

import pytest
from pydantic import ValidationError

from workflow.models.draft_shortlist import DraftShortlistEntry
from workflow.store import WorkflowStore

ADDED_AT = "2026-08-15T09:00:00+00:00"


def store_with_an_item(discipline: str = "Cables"):
    store = WorkflowStore()
    project = store.create_project(
        name="Haliba", code="HAL", client="Al Dhafra Petroleum", location="UAE",
        live_period_start=date(2026, 1, 1), live_period_end=date(2029, 12, 31),
    )
    item = store.create_item(
        project_id=project.id, item_type="HV cable", description="11 kV",
        qty=1, uom="m", discipline=discipline, estimated_value_aed=900_000,
    )
    return store, project, item


def an_entry(item_id: str, name: str, *, vendor_id=None, source="Manual"):
    return DraftShortlistEntry(
        item_id=item_id,
        vendor_id=vendor_id,
        vendor_name=name,
        source=source,
        added_by="buyer@example.com",
        added_at=ADDED_AT,
    )


# -- the record ---------------------------------------------------------------


def test_a_draft_entry_carries_its_own_id_and_the_source_it_came_from():
    """`source` records *where the buyer found them*, which is a different fact
    from who approved them and is not derivable later — a registry vendor's
    approvals can change and a curated row can be deleted from the item's list.
    Recording it at selection time is the only way to keep it true."""
    entry = an_entry("itm_1", "Al Munara Cables LLC", vendor_id="bdr_1", source="ADNOC")

    assert entry.id.startswith("dse_")
    assert entry.vendor_id == "bdr_1"
    assert entry.source == "ADNOC"
    assert entry.added_by == "buyer@example.com"


def test_two_draft_entries_never_share_an_id():
    first = an_entry("itm_1", "A")
    second = an_entry("itm_1", "A")

    assert first.id != second.id


def test_the_source_is_one_of_the_four_pool_sources_and_nothing_else():
    """A literal rather than free text, for the reason `VendorListSource` is
    one: a typo would quietly create a fifth source that no screen renders and
    no reader can interpret. `Client` is the *uploaded* list's name for ADNOC
    and is deliberately not a member here — this field names a pool chip."""
    with pytest.raises(ValidationError):
        an_entry("itm_1", "X", source="Client")


def test_a_draft_entry_copies_no_approval_and_no_prequalification():
    """Approvals are read live through `vendor_id`, the same rule
    `client_approved` keeps on a shortlist row. A copy would be wrong the moment
    the registry is corrected, and correcting it is the common case."""
    fields = set(DraftShortlistEntry.model_fields)

    assert "approved_by" not in fields
    assert "client_approved" not in fields
    assert "prequal_status" not in fields


# -- add, read, remove --------------------------------------------------------


def test_a_vendor_is_added_to_the_draft_and_read_back():
    store, _project, item = store_with_an_item()

    stored = store.add_draft_shortlist_entry(
        item.id, an_entry(item.id, "Al Munara Cables LLC", vendor_id="bdr_1", source="ADNOC")
    )

    draft = store.draft_shortlist(item.id)
    assert [e.vendor_name for e in draft] == ["Al Munara Cables LLC"]
    assert draft[0].id == stored.id


def test_an_item_with_no_draft_reads_as_an_empty_one():
    """Absent is empty, not an error — the item screen asks before the buyer
    has picked anybody."""
    store, _project, item = store_with_an_item()

    assert store.draft_shortlist(item.id) == []


def test_vendors_accumulate_in_the_order_they_were_picked():
    store, _project, item = store_with_an_item()
    store.add_draft_shortlist_entry(item.id, an_entry(item.id, "First Co"))
    store.add_draft_shortlist_entry(item.id, an_entry(item.id, "Second Co"))

    assert [e.vendor_name for e in store.draft_shortlist(item.id)] == [
        "First Co",
        "Second Co",
    ]


def test_a_vendor_is_removed_from_the_draft_by_id():
    """By id, never by name or position — the same rule
    `remove_shortlist_entry` and `remove_item_vendor_entry` keep."""
    store, _project, item = store_with_an_item()
    first = store.add_draft_shortlist_entry(
        item.id, an_entry(item.id, "Same Name", vendor_id="bdr_1")
    )
    second = store.add_draft_shortlist_entry(
        item.id, an_entry(item.id, "Same Name", vendor_id="bdr_2")
    )

    store.remove_draft_shortlist_entry(item.id, first.id)

    assert [e.id for e in store.draft_shortlist(item.id)] == [second.id]


def test_removing_an_unknown_draft_entry_raises():
    """The route turns this into a 404. Silently succeeding would report a
    removal that never happened."""
    store, _project, item = store_with_an_item()
    store.add_draft_shortlist_entry(item.id, an_entry(item.id, "First Co"))

    with pytest.raises(KeyError, match="dse_missing"):
        store.remove_draft_shortlist_entry(item.id, "dse_missing")

    assert len(store.draft_shortlist(item.id)) == 1


def test_removing_an_entry_from_the_wrong_items_draft_raises():
    """Drafts are per item, so an id that exists elsewhere is still not this
    item's — otherwise one item's screen could empty another's basket."""
    store, project, item = store_with_an_item()
    other = store.create_item(
        project_id=project.id, item_type="Transformer", description="11/0.415 kV",
        qty=1, uom="no", discipline="Cables", estimated_value_aed=1,
    )
    entry = store.add_draft_shortlist_entry(item.id, an_entry(item.id, "First Co"))

    with pytest.raises(KeyError):
        store.remove_draft_shortlist_entry(other.id, entry.id)

    assert [e.id for e in store.draft_shortlist(item.id)] == [entry.id]


def test_adding_to_an_unknown_item_raises():
    store, _project, _item = store_with_an_item()

    with pytest.raises(KeyError, match="itm_missing"):
        store.add_draft_shortlist_entry("itm_missing", an_entry("itm_missing", "X"))


def test_clearing_a_draft_empties_it_and_leaves_another_items_alone():
    store, project, item = store_with_an_item()
    other = store.create_item(
        project_id=project.id, item_type="Transformer", description="11/0.415 kV",
        qty=1, uom="no", discipline="Cables", estimated_value_aed=1,
    )
    store.add_draft_shortlist_entry(item.id, an_entry(item.id, "First Co"))
    store.add_draft_shortlist_entry(other.id, an_entry(other.id, "Other Co"))

    store.clear_draft_shortlist(item.id)

    assert store.draft_shortlist(item.id) == []
    assert [e.vendor_name for e in store.draft_shortlist(other.id)] == ["Other Co"]


# -- I-B: exactly one entry per vendor ----------------------------------------
#
# Every test here calls the store directly. A duplicate check written in the
# route would leave all of them red, which is the point: the decision that gates
# the write has to sit in the same critical section as the write.


def test_adding_a_registry_vendor_twice_is_one_row_not_two():
    store, _project, item = store_with_an_item()
    first = store.add_draft_shortlist_entry(
        item.id, an_entry(item.id, "Al Munara Cables LLC", vendor_id="bdr_1", source="ADNOC")
    )

    again = store.add_draft_shortlist_entry(
        item.id, an_entry(item.id, "Al Munara Cables LLC", vendor_id="bdr_1", source="Astra")
    )

    draft = store.draft_shortlist(item.id)
    assert len(draft) == 1
    # The no-op hands back the row that is already there, so a caller that
    # stores the returned id addresses a row that exists.
    assert again.id == first.id
    assert draft[0].source == "ADNOC"


def test_the_same_registry_vendor_under_a_different_name_is_still_one_row():
    """Keyed on `vendor_id` where there is one: the registry row was renamed
    between the two picks, and it is still the same company."""
    store, _project, item = store_with_an_item()
    store.add_draft_shortlist_entry(
        item.id, an_entry(item.id, "Al Munara Switchgear LLC", vendor_id="bdr_1", source="ADNOC")
    )

    store.add_draft_shortlist_entry(
        item.id,
        an_entry(item.id, "Al Munara Electrical Industries LLC", vendor_id="bdr_1", source="ADNOC"),
    )

    assert [e.vendor_name for e in store.draft_shortlist(item.id)] == [
        "Al Munara Switchgear LLC"
    ]


def test_adding_a_curated_vendor_twice_by_name_is_one_row_not_two():
    """A curated row has no id, so the name is the only key there is."""
    store, _project, item = store_with_an_item()
    store.add_draft_shortlist_entry(
        item.id, an_entry(item.id, "Typed By Hand LLC", source="Manual")
    )

    store.add_draft_shortlist_entry(
        item.id, an_entry(item.id, "Typed By Hand LLC", source="Suggested")
    )

    draft = store.draft_shortlist(item.id)
    assert len(draft) == 1
    assert draft[0].source == "Manual"


def test_a_registry_row_and_a_hand_typed_one_of_the_same_name_are_two_rows():
    """Not a defect to be tidied later. This repository has recorded name
    matching as a shipped defect twice, and collapsing a hand-typed string into
    a real approved vendor is that defect wearing a different hat — the buyer
    picked two things and gets two rows, each tagged with its source."""
    store, _project, item = store_with_an_item()
    store.add_draft_shortlist_entry(
        item.id, an_entry(item.id, "Al Munara Cables LLC", vendor_id="bdr_1", source="ADNOC")
    )

    store.add_draft_shortlist_entry(
        item.id, an_entry(item.id, "Al Munara Cables LLC", source="Manual")
    )

    assert [e.source for e in store.draft_shortlist(item.id)] == ["ADNOC", "Manual"]


def test_two_registry_vendors_sharing_a_trading_name_both_survive():
    """Two suppliers can share a trading name, so the id is what separates
    them — the reason removal addresses by id in the first place."""
    store, _project, item = store_with_an_item()
    store.add_draft_shortlist_entry(
        item.id, an_entry(item.id, "Same Name", vendor_id="bdr_1", source="ADNOC")
    )

    store.add_draft_shortlist_entry(
        item.id, an_entry(item.id, "Same Name", vendor_id="bdr_2", source="ADNOC")
    )

    assert [e.vendor_id for e in store.draft_shortlist(item.id)] == ["bdr_1", "bdr_2"]


def test_the_same_vendor_on_two_items_is_one_row_on_each():
    """The draft is owned by the item, so the guard is per item — not a global
    "this vendor is already somewhere" check."""
    store, project, item = store_with_an_item()
    other = store.create_item(
        project_id=project.id, item_type="Transformer", description="11/0.415 kV",
        qty=1, uom="no", discipline="Cables", estimated_value_aed=1,
    )
    store.add_draft_shortlist_entry(
        item.id, an_entry(item.id, "Shared Co", vendor_id="bdr_1", source="ADNOC")
    )
    store.add_draft_shortlist_entry(
        other.id, an_entry(other.id, "Shared Co", vendor_id="bdr_1", source="ADNOC")
    )

    assert len(store.draft_shortlist(item.id)) == 1
    assert len(store.draft_shortlist(other.id)) == 1


def test_a_removed_vendor_can_be_added_again():
    """The guard holds and releases. A dedupe that remembered removed rows
    would leave a buyer unable to undo a mis-click."""
    store, _project, item = store_with_an_item()
    first = store.add_draft_shortlist_entry(
        item.id, an_entry(item.id, "Al Munara Cables LLC", vendor_id="bdr_1", source="ADNOC")
    )
    store.remove_draft_shortlist_entry(item.id, first.id)

    second = store.add_draft_shortlist_entry(
        item.id, an_entry(item.id, "Al Munara Cables LLC", vendor_id="bdr_1", source="ADNOC")
    )

    assert [e.id for e in store.draft_shortlist(item.id)] == [second.id]
    assert second.id != first.id


# -- I-A: the cascade ---------------------------------------------------------


def test_deleting_an_item_takes_its_draft():
    """A draft pointing at an item that is gone is an orphan, and `save`
    replaces the document wholesale — so it survives the restart."""
    store, _project, item = store_with_an_item()
    store.add_draft_shortlist_entry(item.id, an_entry(item.id, "First Co"))

    store.delete_item(item.id)

    assert store.draft_shortlist(item.id) == []


def test_a_refused_item_delete_leaves_the_draft_alone():
    """The RFQ guard raises before the cascade runs, so nothing half-lands."""
    store, project, item = store_with_an_item()
    entry = store.add_draft_shortlist_entry(item.id, an_entry(item.id, "First Co"))
    store.create_rfq(
        project_id=project.id, item_ids=[item.id], reference="ADP-RFQ-2026-014",
        package="Cables", discipline="Cables", value_estimate_aed=1,
    )

    with pytest.raises(ValueError, match="ADP-RFQ-2026-014"):
        store.delete_item(item.id)

    assert [e.id for e in store.draft_shortlist(item.id)] == [entry.id]


def test_deleting_a_project_takes_its_items_drafts():
    """Reaches through the item cascade: a draft goes with its item, whichever
    door deleted it. The id list is materialised before the first deletion —
    the pattern `delete_project` already keeps for the vendor lists."""
    store, project, item = store_with_an_item()
    second = store.create_item(
        project_id=project.id, item_type="Transformer", description="11/0.415 kV",
        qty=1, uom="no", discipline="Cables", estimated_value_aed=1,
    )
    store.add_draft_shortlist_entry(item.id, an_entry(item.id, "First Co"))
    store.add_draft_shortlist_entry(second.id, an_entry(second.id, "Second Co"))

    store.delete_project(project.id)

    assert store.draft_shortlist(item.id) == []
    assert store.draft_shortlist(second.id) == []


def test_deleting_one_project_leaves_another_projects_draft_alone():
    """A cascade that forgot to filter by `project_id` would empty both."""
    store, project, item = store_with_an_item()
    kept_project = store.create_project(
        name="Bab", code="BAB", client="ADNOC", location="UAE",
        live_period_start=date(2026, 1, 1), live_period_end=date(2029, 12, 31),
    )
    kept_item = store.create_item(
        project_id=kept_project.id, item_type="HV cable", description="11 kV",
        qty=1, uom="m", discipline="Cables", estimated_value_aed=1,
    )
    store.add_draft_shortlist_entry(item.id, an_entry(item.id, "Doomed Co"))
    store.add_draft_shortlist_entry(kept_item.id, an_entry(kept_item.id, "Kept Co"))

    store.delete_project(project.id)

    assert store.draft_shortlist(item.id) == []
    assert [e.vendor_name for e in store.draft_shortlist(kept_item.id)] == ["Kept Co"]


# -- adoption -----------------------------------------------------------------
#
# This is where the draft stops being a draft. `test_there_is_no_adoption_yet`
# stood here until BD-6: it marked `adopt_draft_shortlist` as belonging to the
# task where the RFQ-raising screen needs it, which is the task that replaced
# it with the cases below.


def a_registry_bidder(store, name="Al Munara Cables LLC", **changes):
    # A real product group, not the discipline label: scope matching expands
    # "Cables" through the vocabulary and no vendor is registered for a
    # category literally called that.
    # Approved unless a case says otherwise: the registry's default is "Under
    # review", which `evaluate` reports as a *blocker*, and a fixture that
    # blocks by accident would make every adoption case below assert the
    # skipping path.
    changes.setdefault("prequal_status", "Approved")
    return store.create_bidder(
        name=name, trade_categories=["CABLES - FIBER OPTICS"], **changes
    )


def an_rfq_over(store, project, item, **changes):
    return store.create_rfq(
        project_id=project.id, item_ids=[item.id], reference="ADP-RFQ-2026-014",
        package="HV cable", discipline="Cables", value_estimate_aed=900_000,
        **changes,
    )


def test_adoption_copies_the_draft_onto_the_rfq():
    store, project, item = store_with_an_item()
    store.add_draft_shortlist_entry(item.id, an_entry(item.id, "Ducab LLC"))
    rfq = an_rfq_over(store, project, item)

    added = store.adopt_draft_shortlist(rfq.id, item.id)

    assert added == 1
    assert [e.vendor_name for e in store.shortlist_for(rfq.id)] == ["Ducab LLC"]


def test_a_registry_pick_adopts_through_the_vendor_id_path():
    """The snapshot is the registry's, derived at adoption time — not copied
    from the draft, which holds only a name. That is the existing rule in
    `add_shortlist_entry`, reused rather than reimplemented."""
    store, project, item = store_with_an_item()
    bidder = a_registry_bidder(store, prequal_status="Approved")
    store.add_draft_shortlist_entry(
        item.id,
        # A stale name, deliberately: the registry's is the one that must land.
        an_entry(item.id, "Al Munara Cables (old name)", vendor_id=bidder.id,
                 source="ADNOC"),
    )
    rfq = an_rfq_over(store, project, item)

    store.adopt_draft_shortlist(rfq.id, item.id)

    [entry] = store.shortlist_for(rfq.id)
    assert entry.vendor_id == bidder.id
    assert entry.vendor_name == "Al Munara Cables LLC"
    assert entry.prequal_status == "Approved"
    assert entry.scope_code_fit is True


def test_adoption_does_not_clear_the_draft():
    """The item may be covered by a second RFQ later, and silently emptying a
    buyer's basket because one RFQ consumed it is a surprise."""
    store, project, item = store_with_an_item()
    store.add_draft_shortlist_entry(item.id, an_entry(item.id, "Ducab LLC"))
    rfq = an_rfq_over(store, project, item)

    store.adopt_draft_shortlist(rfq.id, item.id)

    assert [e.vendor_name for e in store.draft_shortlist(item.id)] == ["Ducab LLC"]


def test_adoption_is_idempotent():
    store, project, item = store_with_an_item()
    store.add_draft_shortlist_entry(item.id, an_entry(item.id, "Ducab LLC"))
    rfq = an_rfq_over(store, project, item)

    first = store.adopt_draft_shortlist(rfq.id, item.id)
    second = store.adopt_draft_shortlist(rfq.id, item.id)

    assert (first, second) == (1, 0)
    assert len(store.shortlist_for(rfq.id)) == 1


def test_a_vendor_already_invited_by_hand_is_not_duplicated():
    store, project, item = store_with_an_item()
    bidder = a_registry_bidder(store)
    store.add_draft_shortlist_entry(
        item.id, an_entry(item.id, bidder.name, vendor_id=bidder.id, source="ADNOC")
    )
    rfq = an_rfq_over(store, project, item)
    store.add_shortlist_entry(rfq.id, vendor_id=bidder.id)

    added = store.adopt_draft_shortlist(rfq.id, item.id)

    assert added == 0
    assert len(store.shortlist_for(rfq.id)) == 1


def test_a_registry_pick_and_a_hand_typed_one_of_the_same_name_both_adopt():
    """The same two-space rule the draft itself keeps: keyed on the registry id
    where there is one and the name where there is not, so collapsing them
    would attach a real company's approvals to a string somebody typed."""
    store, project, item = store_with_an_item()
    bidder = a_registry_bidder(store, name="Ducab LLC")
    store.add_draft_shortlist_entry(
        item.id, an_entry(item.id, "Ducab LLC", vendor_id=bidder.id, source="ADNOC")
    )
    store.add_draft_shortlist_entry(item.id, an_entry(item.id, "Ducab LLC"))
    rfq = an_rfq_over(store, project, item)

    added = store.adopt_draft_shortlist(rfq.id, item.id)

    assert added == 2
    assert [e.vendor_id for e in store.shortlist_for(rfq.id)] == [bidder.id, None]


def test_a_blocked_bidder_is_skipped_rather_than_invited_unattributed():
    """Inviting a blocked bidder needs a recorded reason, and adoption has
    nobody to attribute one to. Skipping leaves the buyer to invite them
    deliberately, which is the whole point of that guard."""
    store, project, item = store_with_an_item()
    blocked = a_registry_bidder(store, name="Suspended Cables LLC",
                                prequal_status="Suspended")
    store.add_draft_shortlist_entry(
        item.id, an_entry(item.id, blocked.name, vendor_id=blocked.id, source="ADNOC")
    )
    store.add_draft_shortlist_entry(item.id, an_entry(item.id, "Ducab LLC"))
    rfq = an_rfq_over(store, project, item)

    added = store.adopt_draft_shortlist(rfq.id, item.id)

    assert added == 1
    assert [e.vendor_name for e in store.shortlist_for(rfq.id)] == ["Ducab LLC"]


def test_adopting_an_empty_draft_adds_nothing():
    store, project, item = store_with_an_item()
    rfq = an_rfq_over(store, project, item)

    assert store.adopt_draft_shortlist(rfq.id, item.id) == 0
    assert store.shortlist_for(rfq.id) == []


def test_adoption_against_an_unknown_rfq_or_item_raises():
    store, project, item = store_with_an_item()
    rfq = an_rfq_over(store, project, item)

    with pytest.raises(KeyError):
        store.adopt_draft_shortlist("rfq_nope", item.id)
    with pytest.raises(KeyError):
        store.adopt_draft_shortlist(rfq.id, "itm_nope")
