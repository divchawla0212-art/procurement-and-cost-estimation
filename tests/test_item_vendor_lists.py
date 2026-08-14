"""The per-item vendor lists: the record, and the rule that narrows an upload.

Both uploads are Approved Vendor List exports in the same format, so nothing
here parses a new one — `avl_import.parse_avl` reads them. What is worth testing
is the narrowing: the client export is 1 346 vendors and an item is one line of
equipment, so a rule that fails open would attach the whole register to it.

Key-free and fixture-free. Nothing here reads the real export.
"""
from datetime import datetime

import pytest
from pydantic import ValidationError

from workflow.item_vendor_lists import entries_for
from workflow.models.bidder import Bidder
from workflow.models.project import ItemVendorEntry

NOW = datetime(2026, 8, 14, 9, 0)

# Real product group descriptions, quoted exactly, so the discipline expansion
# resolves against the same vocabulary the export uses.
LV_CABLE = "CABLES - LV POWER DISTRIBUTION"
BALL_VALVE = 'VALVES - BALL - API 6D - UP TO 12"'


def _bidder(bidder_id: str, name: str, *groups: str) -> Bidder:
    return Bidder(id=bidder_id, name=name, trade_categories=list(groups))


def _entries(bidders, discipline="Cables", known_ids=None, source="Client"):
    return entries_for(
        bidders,
        item_id="itm_1",
        discipline=discipline,
        source=source,
        known_ids={b.id for b in bidders} if known_ids is None else known_ids,
        uploaded_by="buyer@example.com",
        uploaded_at=NOW,
        source_document="avl.xlsx",
    )


# -- the record ---------------------------------------------------------------


def test_an_entry_records_the_export_name_and_the_registry_link():
    entry = ItemVendorEntry(
        item_id="itm_1",
        source="Client",
        vendor_id="bdr_10007739",
        vendor_name="ABU DHABI CABLE FACTORY",
        trade_categories=[LV_CABLE],
        uploaded_by="buyer@example.com",
        uploaded_at=NOW,
        source_document="avl.xlsx",
    )

    assert entry.id.startswith("ive_")
    assert entry.vendor_id == "bdr_10007739"


def test_the_source_is_one_of_two_and_nothing_else():
    """`Contractor` was the first draft's name for the second list. The real
    second document is Astra's subset, and the literal is what stops a stale
    name reaching the store."""
    with pytest.raises(ValidationError):
        ItemVendorEntry(
            item_id="itm_1",
            source="Contractor",
            vendor_id=None,
            vendor_name="X",
            uploaded_by="a@b.c",
            uploaded_at=NOW,
            source_document="x.xlsx",
        )


def test_an_entry_copies_no_approval_from_the_registry():
    """Approvals are read live through `vendor_id`, the same rule
    `client_approved` keeps on a shortlist row. A copied one is wrong the moment
    the registry is corrected."""
    fields = set(ItemVendorEntry.model_fields)

    assert "approved_by" not in fields
    assert "prequal_status" not in fields


# -- the narrowing rule -------------------------------------------------------


def test_only_vendors_registered_for_the_item_discipline_are_kept():
    """The whole point: the client export is 1 346 vendors and an item is one
    line of equipment."""
    kept = _entries([
        _bidder("bdr_1", "Cable Co", LV_CABLE),
        _bidder("bdr_2", "Valve Co", BALL_VALVE),
    ])

    assert [e.vendor_name for e in kept] == ["Cable Co"]


def test_an_entry_carries_only_the_item_relevant_groups():
    """A cable supplier who also sells valves is on this item's list *for
    cables*; the valves would be noise on this screen."""
    kept = _entries([_bidder("bdr_1", "Both Co", LV_CABLE, BALL_VALVE)])

    assert kept[0].trade_categories == [LV_CABLE]


def test_a_vendor_the_registry_does_not_hold_is_kept_and_marked():
    """Kept, because the export named them; unlinked, because there are no
    approvals to show. Never created as a bidder — the registry arrives whole
    from its own import."""
    kept = _entries([_bidder("bdr_9", "Unknown Co", LV_CABLE)], known_ids=set())

    assert kept[0].vendor_id is None
    assert kept[0].vendor_name == "Unknown Co"


def test_a_discipline_matching_nothing_yields_nothing_rather_than_everything():
    """Items predating the vocabulary carry free text like "1". Falling back to
    the whole upload would attach a 1 346-vendor export to one item, which is
    the opposite of what this list is for."""
    assert _entries([_bidder("bdr_1", "Cable Co", LV_CABLE)], discipline="1") == []


def test_a_product_group_matches_whole_string_never_as_a_substring():
    """A plausible near-miss would put vendors on an item's list who do not do
    the work, and look identical on screen to a list that matches. This
    repository has recorded substring matching as a shipped defect twice."""
    kept = _entries(
        [_bidder("bdr_1", "Nearly Co", "CABLES - LV POWER DISTRIBUTION AND MORE")],
        discipline="Cables",
    )

    assert kept == []


def test_the_source_and_the_document_ride_on_every_entry():
    """A row has to be traceable back to the upload that produced it."""
    kept = _entries([_bidder("bdr_1", "Cable Co", LV_CABLE)], source="Astra")

    assert kept[0].source == "Astra"
    assert kept[0].source_document == "avl.xlsx"
    assert kept[0].uploaded_by == "buyer@example.com"
