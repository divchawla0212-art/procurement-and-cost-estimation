"""Fold an ADNOC Approved Vendor List export into `Bidder` records.

The export is one row per (product group, vendor, manufacturer): about 18 000
rows describing about 1 300 vendors. This groups them by vendor number, so a
vendor listed against fifty product groups is one bidder with fifty trade
categories rather than fifty bidders.

Two rules the parser holds to.

**Columns are located by header, never by position.** A re-export with a
column inserted or moved would otherwise load manufacturer names into
`vendor_name`, and the registry would look entirely plausible while being
wrong about every company in it. A missing required header raises, naming
itself, rather than yielding an empty registry that reads as an empty AVL.

**Nothing the sheet does not say is invented.** There is no prequalification
expiry in it, no hold, no turnover band and no performance rating, so those
stay empty on every imported bidder. These are real, named companies:
synthesising a suspension or a rating against one of them would be
manufacturing a record about a real business, and a demo with fewer columns
filled in is the cheaper problem by a wide margin.

The one invented thing here is the **Astra subset**, and it is invented
deliberately and marked as such — see `astra_approves`.
"""
import hashlib
import re
from typing import Iterable

import openpyxl

from workflow.models.bidder import ADNOC, ASTRA, Bidder

# Canonical header -> attribute. The first four are required; without them
# there is no bidder to build.
_VENDOR_NUMBER = "vendor number"
_VENDOR_NAME = "vendor name"
_PRODUCT_GROUP = "product group description"
_MANUFACTURER = "manufacture name"

_REQUIRED = (_VENDOR_NUMBER, _VENDOR_NAME, _PRODUCT_GROUP)

# Appears in `Manufacture name` where the vendor makes the item themselves.
# It is a marker, not a company, and recording it would imply a principal that
# does not exist.
_NOT_A_MANUFACTURER = re.compile(r"^\(?\s*local manufacture\s*\)?$", re.IGNORECASE)


def _normalise(header) -> str:
    """Case-folded, with runs of whitespace collapsed, so a re-export that
    capitalises differently or pads a cell still matches."""
    return re.sub(r"\s+", " ", str(header or "")).strip().casefold()


def _column_map(header_row: Iterable) -> dict[str, int]:
    found = {}
    for index, cell in enumerate(header_row):
        name = _normalise(cell)
        if name and name not in found:
            found[name] = index
    missing = [h for h in _REQUIRED if h not in found]
    if missing:
        raise ValueError(
            "This does not look like an Approved Vendor List export — no "
            f"column named {', '.join(h.title() for h in missing)}. Columns "
            f"found: {', '.join(sorted(found)) or 'none'}."
        )
    return found


def _text(row, columns: dict[str, int], key: str) -> str:
    index = columns.get(key)
    if index is None or index >= len(row):
        return ""
    return re.sub(r"\s+", " ", str(row[index] or "")).strip()


def astra_approves(vendor_number: str, group_count: int) -> bool:
    """Whether this vendor is also on Astra's internal approved list.

    **This is invented.** There is no Astra approval in the ADNOC export, and
    no real Astra list has been supplied — this exists so a demo can show the
    difference between a client's AVL and an internal subset of it. It must
    not be read as a record of who Astra has actually approved.

    Two properties it does have, because a fabricated answer that is unstable
    or arbitrary is worse than useless:

    - **Stable.** Derived from the vendor number alone, so reseeding does not
      reshuffle the answer and a rehearsed demo says the same thing twice.
    - **Explicable.** Weighted towards vendors listed against more product
      groups, because an internal list does skew towards suppliers you have
      used across several packages.
    """
    digest = hashlib.sha256(f"astra:{vendor_number}".encode("utf-8")).digest()
    # First two bytes as a fraction of the space: deterministic, uniform, and
    # not dependent on Python's hash randomisation.
    draw = int.from_bytes(digest[:2], "big") / 0xFFFF
    threshold = 0.12 + 0.02 * min(group_count, 12)
    return draw < threshold


def parse_avl(path: str, astra_subset: bool = False) -> list[Bidder]:
    """Read the export at `path` and return one `Bidder` per vendor number.

    Order follows first appearance in the sheet, which keeps a diff between
    two exports readable. Trade categories and manufacturers are sorted, since
    their order in the sheet carries no meaning.
    """
    # Closed in `finally`, not left to the garbage collector: `read_only=True`
    # holds the underlying zip open, so a caller that wants to delete the file
    # afterwards — the upload route, which parses a temporary copy — cannot, and
    # on Windows the unlink raises outright. The CLI never noticed because the
    # process exits.
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = None
    try:
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)

        try:
            columns = _column_map(next(rows))
        except StopIteration:
            raise ValueError(
                f"{path} is empty — there is not even a header row."
            ) from None

        collected: dict[str, dict] = {}
        for row in rows:
            number = _text(row, columns, _VENDOR_NUMBER)
            name = _text(row, columns, _VENDOR_NAME)
            # A row missing either is not a vendor. Skipped rather than guessed
            # at: a bidder with no name cannot be invited and a bidder with no
            # number cannot be matched to the next export.
            if not number or not name:
                continue

            record = collected.setdefault(
                number, {"name": name, "groups": set(), "manufacturers": set()}
            )
            group = _text(row, columns, _PRODUCT_GROUP)
            if group:
                record["groups"].add(group)
            manufacturer = _text(row, columns, _MANUFACTURER)
            if manufacturer and not _NOT_A_MANUFACTURER.match(manufacturer):
                record["manufacturers"].add(manufacturer)
    finally:
        # The generator too, not just the workbook. A missing header raises
        # while `rows` is still suspended, and a suspended read-only row
        # iterator holds its own handle into the archive — so closing only the
        # workbook leaves the file locked on exactly the error path.
        if rows is not None:
            rows.close()
        workbook.close()

    bidders = []
    for number, record in collected.items():
        approved_by = [ADNOC]
        if astra_subset and astra_approves(number, len(record["groups"])):
            approved_by.append(ASTRA)
        bidders.append(
            Bidder(
                # The vendor number is a real, stable key, so re-importing a
                # later export updates the same bidder rather than creating a
                # near-duplicate under a slightly different spelling.
                id=f"bdr_{number}",
                name=record["name"],
                approved_by=approved_by,
                trade_categories=sorted(record["groups"]),
                represented_manufacturers=sorted(record["manufacturers"]),
                # On the Approved Vendor List, so approved. Everything the
                # sheet does not say is left unsaid — see the module docstring.
                prequal_status="Approved",
            )
        )
    return bidders
