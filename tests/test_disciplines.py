"""The item-discipline vocabulary, and its join to the export's product groups.

The bug this exists to close: an item's discipline was free text, the vendor
list speaks 1 141 quoted product group descriptions, and the two never met — so
a correctly-imported registry of 1 346 approved vendors answered every item
with nobody.

Two of these tests assert against the *real* export and are therefore gated on
it. They are the ones that would catch the failure that actually matters: a
product group name in `disciplines.py` that no longer exists in the sheet, or
was mistyped when it was copied in. The rest run everywhere.
"""
import os

import pytest

from workflow import disciplines
from workflow.avl_import import parse_avl

AVL = "data/bidders_details/ADNOC Approved Vendor List as of 10.12.2025(client).xlsx"

needs_real_avl = pytest.mark.skipif(
    not os.path.exists(AVL),
    reason="needs the real ADNOC Approved Vendor List export",
)


def test_the_two_disciplines_are_offered_in_order():
    assert disciplines.names() == ["Cables", "Generators"]


def test_a_discipline_expands_to_the_export_s_own_product_groups():
    groups = disciplines.product_groups("Cables")
    assert "CABLES - MV (UP TO 33KV)POWER TRANSMISSION" in groups
    assert len(groups) == 11


def test_a_discipline_is_recognised_whatever_its_capitalisation():
    """It arrives from a stored item or a query string, neither of which is
    guaranteed to have kept the picker's capitalisation."""
    for spelling in ("Cables", "cables", "  CABLES  "):
        assert disciplines.is_known(spelling), spelling
        assert disciplines.product_groups(spelling) == disciplines.DISCIPLINES["Cables"]


def test_an_unknown_discipline_resolves_to_itself():
    """The fallback that keeps a directly-scoped item working: the export's own
    product group descriptions are valid disciplines even without an entry
    here."""
    assert not disciplines.is_known("STEEL STRUCTURE FABRICATED")
    assert disciplines.product_groups("STEEL STRUCTURE FABRICATED") == (
        "STEEL STRUCTURE FABRICATED",
    )


def test_free_text_that_is_neither_matches_nothing():
    assert not disciplines.is_known("Electrical")
    assert not disciplines.covering("Electrical", ["CABLES - FIBER OPTICS"])


def test_is_known_tolerates_none_and_blank():
    assert not disciplines.is_known(None)
    assert not disciplines.is_known("")
    assert not disciplines.is_known("   ")


def test_covering_matches_any_group_in_the_family():
    assert disciplines.covering("Cables", ["UMBILICAL CABLE"])
    assert disciplines.covering("Generators", ["GENERATOR POWER-OTHERS", "PUMPS"])


def test_covering_matches_the_whole_label_not_a_substring():
    """Substring matching would let a cable-tray fabricator answer a cable
    enquiry — the accessory groups are deliberately outside the family."""
    assert not disciplines.covering("Cables", ["TRAYS, LADDERS & TRUNKING (GRP) - (CABLE FITTINGS)"])
    assert not disciplines.covering("Cables", ["CABLES"])


def test_accessories_are_not_folded_into_their_parent_discipline():
    """Recorded as a decision, not an oversight: these are real product groups
    in the export and they are outside both families on purpose."""
    outside = [
        "CABLE ACCESSORIES (LUGS, MULTI TRANSITS, TIES ETC)",
        "GLAND -CABLE",
        "JOINTINGS & TERMINATION KITS - (CABLE FITTINGS)",
        "ELECTRICAL ACCESSORIES FOR GENERATOR SUCH AS AVR/ EXCITATION",
    ]
    for group in outside:
        assert not disciplines.covering("Cables", [group]), group
        assert not disciplines.covering("Generators", [group]), group


@needs_real_avl
def test_every_product_group_named_here_exists_in_the_export():
    """The test that catches a typo. A name that matches nothing silently
    shrinks a discipline, and no other test would notice — the family would
    still expand, just to a label no vendor carries."""
    in_sheet = {
        c.strip().casefold()
        for bidder in parse_avl(AVL)
        for c in bidder.trade_categories
    }
    missing = [
        group
        for family in disciplines.DISCIPLINES.values()
        for group in family
        if group.strip().casefold() not in in_sheet
    ]
    assert missing == []


@needs_real_avl
def test_both_disciplines_actually_resolve_to_approved_vendors():
    """A discipline nobody is registered for is a discipline that empties the
    screen, which is the whole defect this vocabulary exists to fix."""
    from workflow.bidders import client_approved

    roster = parse_avl(AVL)
    assert len(client_approved(roster, "Cables")) > 20
    assert len(client_approved(roster, "Generators")) > 20


def test_matching_collapses_doubled_spaces_inside_a_label():
    """The client's own exports disagree with themselves: the full list spells
    it `CABLES - LV POWER DISTRIBUTION` and a subset exported from the same
    system spells it with two spaces. A rule that only stripped the ends would
    read those as two trades and split the discipline in half."""
    assert disciplines.covering("Cables", ["CABLES - LV  POWER DISTRIBUTION"])
    assert disciplines.fold("CABLES - LV  POWER DISTRIBUTION") == disciplines.fold(
        "CABLES - LV POWER DISTRIBUTION"
    )


def test_the_database_folds_labels_the_same_way(tmp_path):
    """One definition, asserted across the boundary: `bidder_db` re-exports
    this function rather than having its own, and a vendor stored with the
    doubled-space spelling is found by the single-space one."""
    from workflow import bidder_db
    from workflow.models.bidder import ADNOC, Bidder

    assert bidder_db.fold is disciplines.fold
    root = str(tmp_path)
    bidder_db.replace_all(root, [
        Bidder(id="bdr_1", name="Ras Dana", approved_by=[ADNOC],
               trade_categories=["CABLES - LV  POWER DISTRIBUTION"]),
    ])
    found = bidder_db.client_approved_rows(
        root, ADNOC, list(disciplines.product_groups("Cables"))
    )
    assert [b.name for b in found] == ["Ras Dana"]
