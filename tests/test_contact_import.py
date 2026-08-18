"""Reading a Vendor / Email sheet.

Workbooks are built in memory, so this runs in CI with no fixture directory.
The client's own sheet is one tab named `Vendors` with two columns; nothing
here depends on that tab name, because a re-export may rename it.
"""
from datetime import datetime, timezone

import openpyxl
import pytest

from workflow.contact_import import parse_contacts, parse_contacts_counted

WHEN = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)


def _book(rows, path, headers=("Vendor", "Email")):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Vendors"
    ws.append(list(headers))
    for row in rows:
        ws.append(list(row))
    wb.save(path)
    return str(path)


def _parse(path):
    return parse_contacts(
        path,
        uploaded_by="buyer@example.com",
        uploaded_at=WHEN,
        source_document="vendors.xlsx",
    )


def test_a_sheet_reads_one_contact_per_row(tmp_path):
    path = _book(
        [("DANWAY ABU DHABI L.L.C", "sales@danway.example"),
         ("BIN SARI SPECIALIZED TECHNOLOGIES", "bids@binsari.example")],
        tmp_path / "vendors.xlsx",
    )
    contacts = _parse(path)
    assert [c.vendor_name for c in contacts] == [
        "DANWAY ABU DHABI L.L.C", "BIN SARI SPECIALIZED TECHNOLOGIES",
    ]
    assert contacts[0].emails == ["sales@danway.example"]
    assert contacts[0].uploaded_by == "buyer@example.com"
    assert contacts[0].uploaded_at == WHEN
    assert contacts[0].source_document == "vendors.xlsx"


@pytest.mark.parametrize("vendor_header", ["Vendor", "Vendor Name", " name "])
@pytest.mark.parametrize(
    "email_header", ["Email", "Email ID", "E-mail", "EMAIL ADDRESS"]
)
def test_columns_are_found_by_header_whatever_it_is_called(
    tmp_path, vendor_header, email_header
):
    path = _book(
        [("DANWAY ABU DHABI L.L.C", "sales@danway.example")],
        tmp_path / f"{vendor_header.strip()}-{email_header}.xlsx",
        headers=(vendor_header, email_header),
    )
    assert _parse(path)[0].emails == ["sales@danway.example"]


def test_columns_are_found_by_header_and_not_by_position(tmp_path):
    """A re-export with a column inserted must not read addresses out of the
    wrong column — the whole file would be wrong about every company in it."""
    path = _book(
        [("320603", "DANWAY ABU DHABI L.L.C", "sales@danway.example")],
        tmp_path / "reordered.xlsx",
        headers=("Product Group Number", "Vendor", "Email"),
    )
    contacts = _parse(path)
    assert contacts[0].vendor_name == "DANWAY ABU DHABI L.L.C"
    assert contacts[0].emails == ["sales@danway.example"]


def test_a_missing_column_is_refused_naming_it_and_what_was_found(tmp_path):
    path = _book(
        [("DANWAY ABU DHABI L.L.C", "Abu Dhabi")],
        tmp_path / "no-email.xlsx",
        headers=("Vendor", "City"),
    )
    with pytest.raises(ValueError) as excinfo:
        _parse(path)
    message = str(excinfo.value)
    assert "Email" in message
    assert "City" in message


def test_one_cell_may_carry_several_addresses(tmp_path):
    """RFQ distribution routinely goes to more than one person at a vendor."""
    path = _book(
        [("DANWAY ABU DHABI L.L.C", "sales@danway.example; bids@danway.example")],
        tmp_path / "multi.xlsx",
    )
    assert _parse(path)[0].emails == [
        "sales@danway.example", "bids@danway.example",
    ]


def test_a_malformed_address_is_refused_naming_its_row(tmp_path):
    """Refused, never skipped. A dropped row is a vendor who never receives the
    enquiry, and nobody finds out until the bid is missing."""
    path = _book(
        [("DANWAY ABU DHABI L.L.C", "sales@danway.example"),
         ("BIN SARI SPECIALIZED TECHNOLOGIES", "not an address")],
        tmp_path / "bad.xlsx",
    )
    with pytest.raises(ValueError) as excinfo:
        _parse(path)
    assert "3" in str(excinfo.value)          # the header is row 1
    assert "not an address" in str(excinfo.value)


def test_a_vendor_with_no_address_is_refused(tmp_path):
    """`emails` is never empty, which is what gives the read side two states."""
    path = _book([("DANWAY ABU DHABI L.L.C", "")], tmp_path / "empty.xlsx")
    with pytest.raises(ValueError) as excinfo:
        _parse(path)
    assert "DANWAY ABU DHABI L.L.C" in str(excinfo.value)


def test_an_address_with_no_vendor_is_refused(tmp_path):
    """The mirror of the case above. A row nobody can attribute is not a
    contact, and storing it under the empty string would key every one of them
    the same way."""
    path = _book([("", "orphan@example.com")], tmp_path / "orphan.xlsx")
    with pytest.raises(ValueError) as excinfo:
        _parse(path)
    assert "2" in str(excinfo.value)


def test_two_rows_for_one_vendor_merge_rather_than_being_refused(tmp_path):
    """Two contacts, not an ambiguity. Folding differs only in case and
    internal whitespace, so these are the same vendor."""
    path = _book(
        [("DANWAY  ABU DHABI L.L.C", "sales@danway.example"),
         ("danway abu dhabi l.l.c", "bids@danway.example")],
        tmp_path / "dupe.xlsx",
    )
    contacts = _parse(path)
    assert len(contacts) == 1
    assert contacts[0].vendor_name == "DANWAY  ABU DHABI L.L.C"  # first spelling wins
    assert contacts[0].emails == [
        "sales@danway.example", "bids@danway.example",
    ]


def test_the_counted_form_reports_rows_read_separately_from_contacts_stored(
    tmp_path,
):
    """`len(parse_contacts(...))` is already post-merge, so a route that only
    had that number could never tell "8 rows became 8 contacts" apart from
    "9 rows became 8 because two folded together" — exactly the case a
    summary line exists to report. Two rows, one vendor: `rows_read` counts
    the rows, not the merged contacts."""
    path = _book(
        [("DANWAY  ABU DHABI L.L.C", "sales@danway.example"),
         ("danway abu dhabi l.l.c", "bids@danway.example")],
        tmp_path / "dupe.xlsx",
    )
    contacts, rows_read = parse_contacts_counted(
        path,
        uploaded_by="buyer@example.com",
        uploaded_at=WHEN,
        source_document="vendors.xlsx",
    )
    assert rows_read == 2
    assert len(contacts) == 1


def test_a_repeated_address_for_one_vendor_is_held_once(tmp_path):
    path = _book(
        [("DANWAY ABU DHABI L.L.C", "sales@danway.example"),
         ("DANWAY ABU DHABI L.L.C", "sales@danway.example")],
        tmp_path / "same.xlsx",
    )
    assert _parse(path)[0].emails == ["sales@danway.example"]


def test_a_blank_row_is_skipped_rather_than_refused(tmp_path):
    """Trailing blank rows are what a spreadsheet editor leaves behind; they
    are not a vendor anybody failed to give an address for."""
    path = _book(
        [("DANWAY ABU DHABI L.L.C", "sales@danway.example"), (None, None)],
        tmp_path / "trailing.xlsx",
    )
    assert len(_parse(path)) == 1


def test_an_empty_sheet_is_refused_rather_than_read_as_no_vendors(tmp_path):
    wb = openpyxl.Workbook()
    wb.save(tmp_path / "empty-book.xlsx")
    with pytest.raises(ValueError):
        _parse(str(tmp_path / "empty-book.xlsx"))


def test_a_sheet_of_headers_and_nothing_else_is_refused(tmp_path):
    """An upload that stored nobody would empty the directory silently, and
    the reader would have no way to tell that from a file that parsed."""
    path = _book([], tmp_path / "headers-only.xlsx")
    with pytest.raises(ValueError) as excinfo:
        _parse(path)
    assert "vendors.xlsx" in str(excinfo.value)
