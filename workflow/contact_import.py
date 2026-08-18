"""Reading a Vendor / Email sheet into contacts.

Pure: no store, no clock, no root. The uploader, the timestamp and the file
name are passed in, the same shape `item_vendor_lists.entries_for` is written
in and for the same reason — the rule can then be tested without building a
store.

**Columns are located by header, never by position.** A re-export with a
column inserted would otherwise read addresses out of the wrong column and be
wrong about every company in the file; that is the rule `avl_import` already
states for the much larger export beside this one. Header text is normalised
with `disciplines.fold`, which performs the same collapse-and-casefold that
`avl_import._normalise` does with a regex — the older copy is left alone
rather than refactored from here.

Nothing in this module creates a bidder, grants an approval or touches the
registry. It reads a spreadsheet and returns records.
"""
import re
from datetime import datetime

import openpyxl

from workflow.disciplines import fold
from workflow.models.vendor_contact import VendorContact

#: Folded, because the header is normalised before it is looked up here.
_VENDOR_HEADERS = {"vendor", "vendor name", "name"}
_EMAIL_HEADERS = {"email", "email id", "e-mail", "email address"}

# Deliberately shallow: one `@`, no whitespace, something either side. A strict
# RFC 5322 validator rejects addresses that work in practice, and the cost of a
# false refusal here is an upload nobody can complete.
_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+$")

# One cell, several people. Both separators, because a sheet uses whichever the
# person typing it reached for.
_SEPARATORS = re.compile(r"[;,]")


def parse_contacts(
    path: str,
    *,
    uploaded_by: str,
    uploaded_at: datetime,
    source_document: str,
) -> list[VendorContact]:
    """The sheet's contacts, in sheet order, with duplicate vendors merged.

    Raises `ValueError` naming what is wrong: a missing column and the columns
    found instead, a malformed address and its row number, or a vendor with no
    address at all. A refusal that does not say what is wrong with the file
    leaves the reader nothing to act on.

    **A malformed address is refused rather than skipped.** A silently dropped
    row is a vendor who never receives the enquiry, and nobody finds out until
    the bid is missing.

    **Two rows for one vendor merge**, keyed by `fold`. Once several addresses
    per vendor are supported, a second row for the same company is not an
    ambiguity — it is a second contact — so refusing it would make the common
    case an error. The first spelling of the name wins, and the addresses
    union with their order preserved.
    """
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            raise ValueError(
                f"{source_document} is empty — there is not even a header row."
            ) from None

        vendor_at, email_at = _columns(header, source_document)

        merged: dict[str, VendorContact] = {}
        for number, row in enumerate(rows, start=2):
            name = _text(row, vendor_at)
            addresses = _addresses(_text(row, email_at), number)
            if not name and not addresses:
                # A trailing blank row is what a spreadsheet editor leaves
                # behind, not a vendor somebody failed to give an address for.
                continue
            if not name:
                raise ValueError(
                    f"Row {number} has an address and no vendor name."
                )
            if not addresses:
                raise ValueError(
                    f"{name} (row {number}) has no email address."
                )
            key = fold(name)
            existing = merged.get(key)
            if existing is None:
                merged[key] = VendorContact(
                    vendor_name=name,
                    emails=addresses,
                    source_document=source_document,
                    uploaded_by=uploaded_by,
                    uploaded_at=uploaded_at,
                )
            else:
                existing.emails.extend(
                    a for a in addresses if a not in existing.emails
                )
        if not merged:
            # An upload that stored nobody would empty the directory silently,
            # and a reader could not tell that from a file that parsed.
            raise ValueError(f"{source_document} holds no vendors.")
        return list(merged.values())
    finally:
        workbook.close()


def _columns(header, source_document: str) -> tuple[int, int]:
    """Where the two columns are, by header text. Raises naming the missing
    one and what was found, so a wrong file can be corrected rather than
    guessed at."""
    found: dict[str, int] = {}
    for index, cell in enumerate(header):
        if cell is None or not str(cell).strip():
            continue
        found.setdefault(fold(str(cell)), index)

    vendor_at = _column(found, _VENDOR_HEADERS)
    email_at = _column(found, _EMAIL_HEADERS)
    if vendor_at is None or email_at is None:
        missing = "Vendor" if vendor_at is None else "Email"
        columns = ", ".join(
            str(cell) for cell in header if cell is not None and str(cell).strip()
        )
        raise ValueError(
            f"{source_document} has no {missing} column. "
            f"Columns found: {columns or 'none'}."
        )
    return vendor_at, email_at


def _column(found: dict[str, int], accepted: set[str]) -> int | None:
    for name, index in found.items():
        if name in accepted:
            return index
    return None


def _text(row, index: int) -> str:
    if index >= len(row):
        return ""
    return str(row[index] or "").strip()


def _addresses(cell: str, row_number: int) -> list[str]:
    addresses: list[str] = []
    for part in _SEPARATORS.split(cell):
        address = part.strip()
        if not address:
            continue
        if not _ADDRESS.match(address):
            raise ValueError(
                f"Row {row_number} has an address that is not one: {address!r}."
            )
        if address not in addresses:
            addresses.append(address)
    return addresses
