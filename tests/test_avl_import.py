"""Importing an ADNOC Approved Vendor List export into the bidder registry.

The export is one row per (product group, vendor, manufacturer), so the import
is a fold: 18 000 rows become roughly 1 300 bidders, each carrying every
product group they are listed against and every manufacturer they represent.

Two rules are load-bearing and asserted directly.

**Columns are found by header, never by position.** A re-export with a
reordered or inserted column would otherwise import manufacturer names as
vendor names, silently, and the registry would look plausible and be wrong.

**Nothing absent from the sheet is invented.** The export carries no
prequalification expiry, no hold, no turnover and no performance rating, so
those stay empty. Synthesising them would attach fabricated facts to real,
named companies — which is worse than a demo with fewer columns filled in.

Most tests here build a small workbook in memory, so they run in CI with no
fixture directory. The two that read the real export are guarded on it.
"""
import os

import openpyxl
import pytest

from workflow.avl_import import ASTRA, ADNOC, parse_avl, astra_approves
from workflow.bidders import missing_client_approval

HEADERS = [
    "Product Group Number",
    "Product Group Description",
    "Vendor Number",
    "Vendor Name",
    "Manufacture Number",
    "Manufacture name",
    "Manufacture Country",
    "Remarks",
]

ROWS = [
    ["122010", "A/C UNITS - SPLIT & WINDOW", "10000238", "SYSTEMS EQUIPMENT L.L.C",
     "20002132", "AXIS SOLUTIONS PVT LTD", "India", ""],
    ["122020", "A/C PACKAGES", "10000238", "SYSTEMS EQUIPMENT L.L.C",
     "20002132", "AXIS SOLUTIONS PVT LTD", "India", "Migrated"],
    ["122010", "A/C UNITS - SPLIT & WINDOW", "10000101", "U.T.S CARRIER LLC",
     "20007034", "CARRIER INTERNATIONAL CORPORATION", "United States of America", "Migrated"],
    ["131050", "BALL VALVES", "10000101", "U.T.S CARRIER LLC",
     "20005470", "CARRIER AIR CONDITIONING,USA", "United States of America", ""],
]


def workbook(tmp_path, headers=HEADERS, rows=ROWS, name="avl.xlsx") -> str:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    path = str(tmp_path / name)
    wb.save(path)
    return path


# -- the fold ----------------------------------------------------------------


def test_rows_fold_into_one_bidder_per_vendor_number(tmp_path):
    bidders = parse_avl(workbook(tmp_path))
    assert [b.name for b in bidders] == [
        "SYSTEMS EQUIPMENT L.L.C",
        "U.T.S CARRIER LLC",
    ]


def test_a_bidder_carries_every_product_group_it_is_listed_against(tmp_path):
    bidders = {b.name: b for b in parse_avl(workbook(tmp_path))}
    assert bidders["U.T.S CARRIER LLC"].trade_categories == [
        "A/C UNITS - SPLIT & WINDOW",
        "BALL VALVES",
    ]


def test_a_bidder_carries_every_manufacturer_it_represents_once(tmp_path):
    bidders = {b.name: b for b in parse_avl(workbook(tmp_path))}
    # Two rows, one manufacturer — the same OEM against two product groups is
    # one relationship, not two.
    assert bidders["SYSTEMS EQUIPMENT L.L.C"].represented_manufacturers == [
        "AXIS SOLUTIONS PVT LTD"
    ]


def test_the_id_is_the_vendor_number(tmp_path):
    """A real, stable key. Re-importing a later export updates the same bidder
    rather than creating a second one under a slightly different name."""
    bidders = {b.name: b for b in parse_avl(workbook(tmp_path))}
    assert bidders["U.T.S CARRIER LLC"].id == "bdr_10000101"


def test_every_imported_bidder_is_adnoc_approved(tmp_path):
    for bidder in parse_avl(workbook(tmp_path)):
        assert bidder.approved_by == [ADNOC]
        assert bidder.prequal_status == "Approved"


# -- nothing is invented -----------------------------------------------------


def test_fields_the_export_does_not_carry_are_left_empty(tmp_path):
    """The AVL has no expiry, no hold, no turnover and no rating. Filling them
    in would attach fabricated facts to a real company's name."""
    bidder = parse_avl(workbook(tmp_path))[0]
    assert bidder.prequal_expires_on is None
    assert bidder.on_hold is False
    assert bidder.hold_reason is None
    assert bidder.turnover_band is None
    assert bidder.performance_rating is None
    assert bidder.past_awards == 0


def test_country_is_left_unrecorded_because_the_export_has_none(tmp_path):
    """`Manufacture Country` is the OEM's, not the vendor's. Copying it across
    would say a UAE supplier is Indian because their principal is."""
    assert parse_avl(workbook(tmp_path))[0].country is None


# -- columns by header, not position -----------------------------------------


def test_a_reordered_export_still_imports_the_right_fields(tmp_path):
    shuffled = [HEADERS[3], HEADERS[1], HEADERS[5], HEADERS[2]]
    rows = [[r[3], r[1], r[5], r[2]] for r in ROWS]
    bidders = {b.name: b for b in parse_avl(workbook(tmp_path, shuffled, rows))}
    assert bidders["U.T.S CARRIER LLC"].id == "bdr_10000101"
    assert "BALL VALVES" in bidders["U.T.S CARRIER LLC"].trade_categories


def test_a_missing_required_column_is_refused_by_name(tmp_path):
    """Loudly, and naming what is missing. A silent skip would produce an empty
    registry that looks like an empty AVL."""
    without_vendor = [h for h in HEADERS if h != "Vendor Number"]
    rows = [[c for h, c in zip(HEADERS, r) if h != "Vendor Number"] for r in ROWS]
    with pytest.raises(ValueError, match="Vendor Number"):
        parse_avl(workbook(tmp_path, without_vendor, rows))


def test_headers_are_matched_case_and_space_insensitively(tmp_path):
    relaxed = ["  product group number ", "PRODUCT GROUP DESCRIPTION", "vendor number",
               "Vendor  Name", "Manufacture Number", "manufacture name",
               "Manufacture Country", "Remarks"]
    assert len(parse_avl(workbook(tmp_path, relaxed))) == 2


# -- rows that cannot become a bidder ----------------------------------------


def test_a_row_with_no_vendor_number_is_skipped(tmp_path):
    rows = ROWS + [["999", "SOMETHING", None, "A NAMELESS ROW", "", "", "", ""]]
    assert len(parse_avl(workbook(tmp_path, rows=rows))) == 2


def test_a_row_with_no_vendor_name_is_skipped(tmp_path):
    rows = ROWS + [["999", "SOMETHING", "10009999", None, "", "", "", ""]]
    assert len(parse_avl(workbook(tmp_path, rows=rows))) == 2


def test_a_local_manufacture_placeholder_is_not_recorded_as_a_principal(tmp_path):
    """`( LOCAL MANUFACTURE )` appears where there is no third-party OEM. It is
    a marker, not a company, and listing it would imply a relationship."""
    rows = ROWS + [["131050", "BALL VALVES", "10070167", "STOLWAY MANUFACTURING",
                    "", "( LOCAL MANUFACTURE )", "", "Ref SQM# WS191062592"]]
    stolway = {b.name: b for b in parse_avl(workbook(tmp_path, rows=rows))}[
        "STOLWAY MANUFACTURING"
    ]
    assert stolway.represented_manufacturers == []
    assert stolway.trade_categories == ["BALL VALVES"]


def test_whitespace_around_every_value_is_stripped(tmp_path):
    rows = [["  122010 ", "  BALL VALVES  ", " 10000101 ", "  U.T.S CARRIER LLC ",
             "", "  CARRIER INTERNATIONAL  ", "", ""]]
    bidder = parse_avl(workbook(tmp_path, rows=rows))[0]
    assert bidder.name == "U.T.S CARRIER LLC"
    assert bidder.id == "bdr_10000101"
    assert bidder.trade_categories == ["BALL VALVES"]
    assert bidder.represented_manufacturers == ["CARRIER INTERNATIONAL"]


# -- the Astra subset --------------------------------------------------------


def test_the_astra_subset_is_marked_on_top_of_the_adnoc_approval(tmp_path):
    """Astra approval is additional, not alternative: everybody here is on the
    ADNOC list, and some are also on Astra's."""
    bidders = parse_avl(workbook(tmp_path), astra_subset=True)
    for bidder in bidders:
        assert bidder.approved_by[0] == ADNOC
        assert set(bidder.approved_by) <= {ADNOC, ASTRA}


def test_the_astra_subset_is_stable_across_runs(tmp_path):
    """Derived from the vendor number, so a reseed does not reshuffle who is
    approved — a demo that changes its own answer between runs is unusable."""
    first = {b.id: b.approved_by for b in parse_avl(workbook(tmp_path), astra_subset=True)}
    second = {b.id: b.approved_by for b in parse_avl(workbook(tmp_path), astra_subset=True)}
    assert first == second


def test_the_astra_subset_is_off_unless_asked_for(tmp_path):
    assert all(b.approved_by == [ADNOC] for b in parse_avl(workbook(tmp_path)))


def test_astra_approval_favours_vendors_listed_across_more_product_groups():
    """The rule is invented, and it is invented to be explicable: an internal
    AVL skews towards suppliers you have used across several packages."""
    broad = sum(astra_approves(f"1000{n:04d}", group_count=40) for n in range(400))
    narrow = sum(astra_approves(f"1000{n:04d}", group_count=1) for n in range(400))
    assert broad > narrow


def test_the_astra_subset_is_a_real_subset_not_everybody_or_nobody():
    approved = sum(astra_approves(f"1000{n:04d}", group_count=3) for n in range(600))
    assert 0 < approved < 600


# -- the client-approval gap is unreachable through the importer --------------


def test_no_imported_bidder_is_ever_off_the_client_list(tmp_path):
    """Every AVL row is by definition on the client's list, so the caution the
    registry computes must be unreachable through this path — with or without
    the Astra subset, which adds an approval rather than replacing one."""
    for astra_subset in (False, True):
        for bidder in parse_avl(workbook(tmp_path), astra_subset=astra_subset):
            assert ADNOC in bidder.approved_by
            assert missing_client_approval(bidder) is None


# -- the real export ---------------------------------------------------------

REAL_AVL = os.path.join(
    "data", "bidders_details", "ADNOC Approved Vendor List as of 10.12.2025.xlsx"
)
needs_real_avl = pytest.mark.skipif(
    not os.path.exists(REAL_AVL),
    reason="the ADNOC export is untracked; present on a workstation, absent in CI",
)


@needs_real_avl
def test_the_real_export_folds_into_a_registry_of_plausible_size():
    bidders = parse_avl(REAL_AVL)
    assert 1000 < len(bidders) < 2000
    assert len({b.id for b in bidders}) == len(bidders)
    assert all(b.name for b in bidders)
    assert all(b.trade_categories for b in bidders)


@needs_real_avl
def test_the_real_export_yields_an_astra_subset_smaller_than_the_whole():
    bidders = parse_avl(REAL_AVL, astra_subset=True)
    astra = [b for b in bidders if ASTRA in b.approved_by]
    assert 0 < len(astra) < len(bidders)
