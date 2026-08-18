# Vendor Contact Directory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upload a `Vendor` / `Email` spreadsheet into an organisation-wide contact directory, and show each invited bidder's address on the RFQ wizard's Shortlisting step.

**Architecture:** A name-keyed directory in two new tables inside the existing `<ROOT>/bidders.db`, hydrated into `WorkflowStore` the way the bidder registry already is, and read back through one derived key on the shortlist payload. A pure parser, a pure store method, all I/O in `contact_db` and `persistence`.

**Tech Stack:** Python 3.12, pydantic v2, FastAPI, SQLite (stdlib `sqlite3`), `openpyxl`; React + TypeScript + vitest for the screen.

**Spec:** [`docs/superpowers/specs/2026-08-18-vendor-contact-directory-design.md`](../specs/2026-08-18-vendor-contact-directory-design.md)

## Global Constraints

Copied from the spec and from CLAUDE.md. Every task's requirements implicitly include this section.

- **No mail is sent by anything in this plan.** No `smtplib`, no `MAIL_*` config, no transport module. BD-9 is a separate phase.
- **The directory holds no `vendor_id` and nothing here resolves a name to a registry row** — except the informational `matched` count in the upload response, which is computed for that one response and stored nowhere.
- **Folding is `disciplines.fold` and nothing more** — case-folded, internal whitespace collapsed. Never strip punctuation or corporate suffixes.
- **Nothing here creates a bidder, grants an approval, or writes to the `bidders` table.**
- **`workflow.json` must gain no `vendor_contacts` key.** This is the second exemption from the "every `WorkflowStore.__init__` field needs a `to_document` / `from_document` line" rule, after `_bidders`, and for the same reason.
- **Every write, and every decision that gates one, happens inside `persistence.locked_update`.**
- **Tests are key-free.** No test may require `ANTHROPIC_API_KEY` or a fixture directory. Workbooks are built in memory with `openpyxl`, as `test_avl_import.py`'s non-gated tests do.
- Run Python tests from the repo root with `python -m pytest`. Run web tests with `npm test` under `web/`.
- Baselines before this plan: **workstation 1819 passed, 3 skipped**; **CI 1799 passed, 23 skipped**; **web 335 passed**. The AVL gate is **eleven** and must not move — no task here touches `test_avl_import.py`, `test_seed_demo.py` or `test_disciplines.py`.

> **Reference code below is intent, not paste-able.** It shows the shape and the reasoning, and it has not been executed. Read it, then write the implementation against the test. Where a part is load-bearing versus illustrative, the task says so.

---

## File structure

| file | responsibility |
|---|---|
| `workflow/models/vendor_contact.py` | the `VendorContact` record — name, addresses, provenance |
| `workflow/contact_import.py` | read a `Vendor` / `Email` workbook into `VendorContact`s. Pure: no store, no clock |
| `workflow/contact_db.py` | the two SQLite tables and their wholesale replacement. The only module that knows the directory is on disk |
| `workflow/store.py` | `_vendor_contacts`, `set_vendor_contacts`, `vendor_contacts`, `emails_for`. Knows nothing about disk |
| `workflow/persistence.py` | hydrate on load, write back on change, and **no document key** |
| `api/workflow_routes.py` | `POST` / `GET /vendor-contacts`, and one derived key on `_shortlist_payload` |
| `web/src/types.ts`, `web/src/api.ts` | the wire types and the two fetchers |
| `web/src/pages/wizard/ShortlistingStep.tsx` | the upload control, the caption, the Email column |

---

## Task 1: The record and the parser

**Files:**
- Create: `workflow/models/vendor_contact.py`
- Create: `workflow/contact_import.py`
- Test: `tests/test_contact_import.py`

**Interfaces:**
- Consumes: `workflow.disciplines.fold`
- Produces: `VendorContact(vendor_name: str, emails: list[str], source_document: str, uploaded_by: str, uploaded_at: datetime)`; `parse_contacts(path: str, *, uploaded_by: str, uploaded_at: datetime, source_document: str) -> list[VendorContact]`
- **Store invariant owned:** none, and that is deliberate rather than an omission — this module stores nothing, has no clock and takes no root. Fabricating one here would put a sentence about stored state on a task that cannot violate it. The invariants it *feeds* (V-A, V-B, V-E) are owned by Task 2.

The parser is the whole honesty argument in code. Columns are located by header, never by position, because a re-export with a column inserted would otherwise read addresses out of the wrong column and be wrong about every company in the file — the rule `avl_import` already states. Refusals name what is wrong, because a refusal that does not leaves the reader nothing to act on.

Three row rules, each with a reason that is not obvious: several addresses in one cell are kept because RFQ distribution routinely goes to more than one person at a vendor; a malformed address is **refused rather than skipped**, because a silently skipped row is a vendor who never receives the enquiry and nobody finds out until the bid is missing; a row with a vendor and no address is refused so that `emails` is never empty, which is what lets the read side have two states instead of three.

Duplicate rows **merge**. Once several addresses per vendor are supported, two rows for one vendor is not ambiguity — it is two contacts — so refusing the upload would make the common case an error.

Header normalisation reuses `disciplines.fold`: `" ".join(s.split()).casefold()` is the same transformation `avl_import._normalise` performs with a regex. Leave `avl_import` alone — refactoring it is not in this phase's scope.

- [ ] **Step 1: Write the failing test**

Create `tests/test_contact_import.py`:

```python
"""Reading a Vendor / Email sheet.

Workbooks are built in memory, so this runs in CI with no fixture directory.
The client's own sheet is one tab named `Vendors` with two columns; nothing
here depends on that tab name, because a re-export may rename it.
"""
from datetime import datetime, timezone

import openpyxl
import pytest

from workflow.contact_import import parse_contacts

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
        path, uploaded_by="buyer@example.com", uploaded_at=WHEN,
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
@pytest.mark.parametrize("email_header", ["Email", "Email ID", "E-mail", "EMAIL ADDRESS"])
def test_columns_are_found_by_header_whatever_it_is_called(
    tmp_path, vendor_header, email_header
):
    path = _book(
        [("DANWAY ABU DHABI L.L.C", "sales@danway.example")],
        tmp_path / f"{vendor_header}-{email_header}.xlsx",
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
    assert _parse(path)[0].emails == ["sales@danway.example", "bids@danway.example"]


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
    assert "3" in str(excinfo.value)          # header is row 1
    assert "not an address" in str(excinfo.value)


def test_a_vendor_with_no_address_is_refused(tmp_path):
    """`emails` is never empty, which is what gives the read side two states."""
    path = _book(
        [("DANWAY ABU DHABI L.L.C", "")], tmp_path / "empty.xlsx",
    )
    with pytest.raises(ValueError) as excinfo:
        _parse(path)
    assert "DANWAY ABU DHABI L.L.C" in str(excinfo.value)


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
    assert contacts[0].vendor_name == "DANWAY  ABU DHABI L.L.C"   # first spelling wins
    assert contacts[0].emails == ["sales@danway.example", "bids@danway.example"]


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_contact_import.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'workflow.contact_import'`

- [ ] **Step 3: Write minimal implementation**

`workflow/models/vendor_contact.py` — the model is load-bearing in exactly two ways: there is no `id` and no `vendor_id` (the folded name is the identity, and §1 of the spec is why there is no registry link), and `emails` is documented as never empty.

```python
from datetime import datetime

from pydantic import BaseModel


class VendorContact(BaseModel):
    """Where to send an enquiry, for one vendor.

    **No `vendor_id`, and no `id`.** The folded vendor name is the identity.
    The sheet this comes from carries no vendor number, so a registry link
    could only be made by comparing names — and a wrong match here mails a
    tender to the wrong company while rendering identically to a right one.
    See §1 of the design.

    `vendor_name` is kept as the sheet wrote it so a row can be read back
    against its source document. `emails` is **never empty**: a row with a
    vendor and no address is refused at parse, which is what lets the read
    side answer `None` or a list and never `[]`.
    """

    vendor_name: str
    emails: list[str]
    source_document: str
    uploaded_by: str
    uploaded_at: datetime
```

`workflow/contact_import.py` — the header sets and the row-number arithmetic are illustrative; the *load-bearing* parts are that columns are found by header, that a refusal names the offending value, and that the merge key is `fold`.

```python
"""Reading a Vendor / Email sheet into contacts.

Pure: no store, no clock, no root. The uploader, the timestamp and the file
name are passed in, the same shape `item_vendor_lists.entries_for` is written
in and for the same reason — the rule can be tested without building a store.

**Columns are located by header, never by position.** A re-export with a
column inserted would otherwise read addresses out of the wrong column, and
be wrong about every company in the file. Header text is normalised with
`disciplines.fold`, which is the same collapse-and-casefold `avl_import`
performs with a regex; that older copy is left alone rather than refactored
here.
"""
import re
from datetime import datetime

import openpyxl

from workflow.disciplines import fold
from workflow.models.vendor_contact import VendorContact

_VENDOR_HEADERS = {"vendor", "vendor name", "name"}
_EMAIL_HEADERS = {"email", "email id", "e-mail", "email address"}

# Deliberately shallow: one `@`, no whitespace, something either side. A strict
# RFC 5322 validator rejects addresses that work, and the cost of a false
# refusal here is an upload nobody can complete.
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
    """The sheet's contacts, in sheet order, duplicates merged.

    Raises `ValueError` naming what is wrong: a missing column and what was
    found instead, a malformed address and its row number, or a vendor with no
    address at all. A refusal that does not say what is wrong with the file
    leaves the reader nothing to act on.
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

        found = {fold(str(cell)): index for index, cell in enumerate(header) if cell}
        vendor_at = _column(found, _VENDOR_HEADERS)
        email_at = _column(found, _EMAIL_HEADERS)
        if vendor_at is None or email_at is None:
            missing = "Vendor" if vendor_at is None else "Email"
            columns = ", ".join(str(cell) for cell in header if cell) or "none"
            raise ValueError(
                f"{source_document} has no {missing} column. Columns found: {columns}."
            )

        merged: dict[str, VendorContact] = {}
        for number, row in enumerate(rows, start=2):
            name = _text(row, vendor_at)
            addresses = _addresses(_text(row, email_at), number)
            if not name and not addresses:
                continue                     # a trailing blank row, not a vendor
            if not name:
                raise ValueError(f"Row {number} has an address and no vendor name.")
            if not addresses:
                raise ValueError(f"{name} (row {number}) has no email address.")
            key = fold(name)
            existing = merged.get(key)
            if existing is None:
                merged[key] = VendorContact(
                    vendor_name=name, emails=addresses,
                    source_document=source_document,
                    uploaded_by=uploaded_by, uploaded_at=uploaded_at,
                )
            else:
                # Two rows for one vendor are two contacts. The first spelling
                # of the name wins; the addresses union, order preserved.
                existing.emails.extend(a for a in addresses if a not in existing.emails)
        if not merged:
            raise ValueError(f"{source_document} holds no vendors.")
        return list(merged.values())
    finally:
        workbook.close()


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_contact_import.py -v`
Expected: PASS, all cases.

- [ ] **Step 5: Commit**

```bash
git add workflow/models/vendor_contact.py workflow/contact_import.py tests/test_contact_import.py
git commit -m "feat: a vendor's addresses are read from a sheet, by header and never by position"
```

---

## Task 2: The tables

**Files:**
- Create: `workflow/contact_db.py`
- Test: `tests/test_contact_db.py`

**Interfaces:**
- Consumes: `VendorContact` (Task 1); `workflow.bidder_db.db_path`, `workflow.disciplines.fold`
- Produces: `contact_db.replace_all(root: str, contacts: Iterable[VendorContact]) -> int`; `contact_db.list_all(root: str) -> list[VendorContact]`; `contact_db.count(root: str) -> int`; `contact_db.fold` (re-export)
- **Store invariant owned:**
  - **V-A.** `vendor_contacts` holds exactly the vendors of the most recent upload — no more.
  - **V-B.** `vendor_contact_emails` holds exactly the addresses of the rows in `vendor_contacts`; no address outlives its vendor.
  - **V-D.** Reloading the registry (`bidder_db.replace_all`) leaves both tables exactly as they were.
  - **V-E.** Every stored `name_key` equals `fold(vendor_name)` of its own row.

V-D is the one that needs saying out loud. The directory shares a file with the registry, and `bidder_db.replace_all` does `DELETE FROM bidders` and nothing else — so contacts survive an AVL reload today. A future `DROP TABLE`-and-recreate in either module would take the other's rows with it and nothing would notice until somebody tried to send an enquiry. The test is what makes that a rule rather than a coincidence.

V-E exists because when the key written and the key queried disagree the symptom is silent: a vendor invisible to the very lookup their own row satisfies. `fold` is re-exported from `disciplines`, never reimplemented, exactly as `bidder_db` does it.

`connect` is a second copy of `bidder_db.connect`'s shape, deliberately: each module owns its own tables and runs its own `CREATE TABLE IF NOT EXISTS`, so neither module's schema becomes a dependency of the other's. `db_path` is *not* duplicated — it is imported, so the file's location has one definition.

- [ ] **Step 1: Write the failing test**

Create `tests/test_contact_db.py`:

```python
"""The vendor contact directory in SQLite.

It shares `bidders.db` with the registry — same kind of thing, reference data
that arrives whole from an export — but its own tables. The two facts worth
asserting are that a contact survives the round trip whole, and that reloading
the registry does not touch it.
"""
from datetime import datetime, timezone

from workflow import bidder_db, contact_db
from workflow.models.bidder import ADNOC, Bidder
from workflow.models.vendor_contact import VendorContact

WHEN = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)


def a_contact(**overrides) -> VendorContact:
    defaults = dict(
        vendor_name="DANWAY ABU DHABI L.L.C",
        emails=["sales@danway.example", "bids@danway.example"],
        source_document="vendors.xlsx",
        uploaded_by="buyer@example.com",
        uploaded_at=WHEN,
    )
    return VendorContact(**{**defaults, **overrides})


def test_a_missing_database_reads_as_an_empty_directory(tmp_path):
    assert contact_db.list_all(str(tmp_path)) == []
    assert contact_db.count(str(tmp_path)) == 0


def test_a_contact_survives_the_round_trip_whole(tmp_path):
    root = str(tmp_path)
    contact_db.replace_all(root, [a_contact()])
    (stored,) = contact_db.list_all(root)
    assert stored.vendor_name == "DANWAY ABU DHABI L.L.C"
    assert stored.emails == ["sales@danway.example", "bids@danway.example"]
    assert stored.source_document == "vendors.xlsx"
    assert stored.uploaded_by == "buyer@example.com"
    assert stored.uploaded_at == WHEN


def test_the_addresses_keep_the_order_they_were_given_in(tmp_path):
    """`position`, not insertion luck: the first address is the one a reader
    treats as the main contact."""
    root = str(tmp_path)
    contact_db.replace_all(root, [a_contact(emails=["z@x.example", "a@x.example"])])
    assert contact_db.list_all(root)[0].emails == ["z@x.example", "a@x.example"]


def test_a_later_upload_replaces_the_directory_wholesale(tmp_path):
    """V-A. A vendor of the previous upload cannot survive a later one — the
    same invariant `persistence.save` has for the document."""
    root = str(tmp_path)
    contact_db.replace_all(root, [a_contact(), a_contact(vendor_name="BIN SARI")])
    contact_db.replace_all(root, [a_contact(vendor_name="BIN SARI")])
    assert [c.vendor_name for c in contact_db.list_all(root)] == ["BIN SARI"]


def test_no_address_outlives_its_vendor(tmp_path):
    """V-B. The cascade, asserted through the read rather than the table."""
    root = str(tmp_path)
    contact_db.replace_all(root, [a_contact()])
    contact_db.replace_all(root, [a_contact(vendor_name="BIN SARI", emails=["a@x.example"])])
    with contact_db.connect(root) as conn:
        rows = conn.execute("SELECT count(*) FROM vendor_contact_emails").fetchone()[0]
    assert rows == 1


def test_every_stored_key_is_the_fold_of_its_own_name(tmp_path):
    """V-E. When the key written and the key queried disagree, the symptom is a
    vendor invisible to the very lookup their own row satisfies."""
    root = str(tmp_path)
    contact_db.replace_all(root, [a_contact(vendor_name="DANWAY  Abu Dhabi L.L.C")])
    with contact_db.connect(root) as conn:
        key, name = conn.execute(
            "SELECT name_key, vendor_name FROM vendor_contacts"
        ).fetchone()
    assert key == contact_db.fold(name)
    assert key == "danway abu dhabi l.l.c"


def test_two_names_folding_alike_cannot_both_be_stored(tmp_path):
    """The primary key makes ambiguity structurally impossible: no query has to
    decide which of two rows for one folded name wins."""
    root = str(tmp_path)
    contact_db.replace_all(root, [
        a_contact(vendor_name="Danway Abu Dhabi L.L.C", emails=["a@x.example"]),
        a_contact(vendor_name="DANWAY  ABU DHABI L.L.C", emails=["b@x.example"]),
    ])
    assert contact_db.count(root) == 1


def test_reloading_the_registry_leaves_the_directory_alone(tmp_path):
    """V-D. The two live in one file. `bidder_db.replace_all` deletes from
    `bidders` and nothing else, and this is what keeps it that way."""
    root = str(tmp_path)
    contact_db.replace_all(root, [a_contact()])
    bidder_db.replace_all(root, [
        Bidder(id="bdr_1", name="Al Munara Switchgear LLC", approved_by=[ADNOC]),
    ])
    (stored,) = contact_db.list_all(root)
    assert stored.emails == ["sales@danway.example", "bids@danway.example"]


def test_replacing_the_directory_leaves_the_registry_alone(tmp_path):
    """V-D, pointing the other way — the copy nobody thinks to write."""
    root = str(tmp_path)
    bidder_db.replace_all(root, [Bidder(id="bdr_1", name="Al Munara Switchgear LLC")])
    contact_db.replace_all(root, [a_contact()])
    assert bidder_db.count(root) == 1


def test_the_directory_is_read_back_in_folded_name_order(tmp_path):
    root = str(tmp_path)
    contact_db.replace_all(root, [
        a_contact(vendor_name="zeta", emails=["z@x.example"]),
        a_contact(vendor_name="Alpha", emails=["a@x.example"]),
    ])
    assert [c.vendor_name for c in contact_db.list_all(root)] == ["Alpha", "zeta"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_contact_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'workflow.contact_db'`

- [ ] **Step 3: Write minimal implementation**

Create `workflow/contact_db.py`. Load-bearing: the `name_key` primary key, `PRAGMA foreign_keys = ON` (the cascade in `replace_all` depends on it, exactly as it does in `bidder_db`), and `fold` being a re-export. The column order and the `ORDER BY` are illustrative.

```python
"""The vendor contact directory in `<ROOT>/bidders.db`.

Same file as the registry, its own tables. The four reasons CLAUDE.md gives
for the registry living in SQLite apply here verbatim: reference data, arrives
whole from an export, replaced wholesale, queried by key rather than read
whole. It is the same *kind* of thing, which is why it shares the file — and
its own tables, because a column on `bidders` would mean an AVL reload
destroys the directory and a vendor absent from the registry could hold no
address.

`name_key` is the primary key, so two rows for one folded name are impossible
rather than guarded against: no query has to decide which one wins.
"""
import os
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime

from workflow import disciplines
from workflow.bidder_db import db_path
from workflow.models.vendor_contact import VendorContact

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendor_contacts (
    name_key        TEXT PRIMARY KEY,
    vendor_name     TEXT NOT NULL,
    source_document TEXT NOT NULL,
    uploaded_by     TEXT NOT NULL,
    uploaded_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vendor_contact_emails (
    name_key TEXT NOT NULL REFERENCES vendor_contacts(name_key) ON DELETE CASCADE,
    email    TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (name_key, email)
);
"""

# Re-exported, never reimplemented — the key column is written with the same
# function every Python comparison uses. See `disciplines.fold`, and
# `bidder_db`, which re-exports it for the same reason.
fold = disciplines.fold


@contextmanager
def connect(root: str) -> Iterator[sqlite3.Connection]:
    """A connection with this module's tables in place.

    Deliberately not shared with `bidder_db.connect`: each module runs its own
    `CREATE TABLE IF NOT EXISTS`, so neither one's schema becomes a dependency
    of the other's. `db_path` *is* shared, so the file has one definition.
    """
    os.makedirs(root, exist_ok=True)
    conn = sqlite3.connect(db_path(root))
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")   # the cascade below needs it
        conn.executescript(SCHEMA)
        yield conn
    finally:
        conn.close()


def replace_all(root: str, contacts: Iterable[VendorContact]) -> int:
    """Make the directory hold exactly `contacts`, in one transaction.

    Wholesale, the same invariant `bidder_db.replace_all` and
    `persistence.save` both have: a vendor removed in memory must not survive
    on disk. **Deletes from `vendor_contacts` only** — never `DROP`, and never
    from `bidders`, which shares this file.
    """
    with connect(root) as conn:
        with conn:
            conn.execute("DELETE FROM vendor_contacts")
            for contact in contacts:
                key = fold(contact.vendor_name)
                conn.execute(
                    "INSERT OR REPLACE INTO vendor_contacts"
                    " (name_key, vendor_name, source_document, uploaded_by, uploaded_at)"
                    " VALUES (?,?,?,?,?)",
                    (key, contact.vendor_name, contact.source_document,
                     contact.uploaded_by, contact.uploaded_at.isoformat()),
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO vendor_contact_emails"
                    " (name_key, email, position) VALUES (?,?,?)",
                    [(key, e, i) for i, e in enumerate(contact.emails)],
                )
        return conn.execute("SELECT count(*) FROM vendor_contacts").fetchone()[0]


def list_all(root: str) -> list[VendorContact]:
    """The whole directory, by folded name. What `persistence.load` hydrates
    the store from."""
    with connect(root) as conn:
        rows = conn.execute(
            "SELECT * FROM vendor_contacts ORDER BY name_key"
        ).fetchall()
        emails: dict[str, list[str]] = {}
        for row in conn.execute(
            "SELECT name_key, email FROM vendor_contact_emails ORDER BY name_key, position"
        ):
            emails.setdefault(row["name_key"], []).append(row["email"])
        return [
            VendorContact(
                vendor_name=row["vendor_name"],
                emails=emails.get(row["name_key"], []),
                source_document=row["source_document"],
                uploaded_by=row["uploaded_by"],
                uploaded_at=datetime.fromisoformat(row["uploaded_at"]),
            )
            for row in rows
        ]


def count(root: str) -> int:
    with connect(root) as conn:
        return conn.execute("SELECT count(*) FROM vendor_contacts").fetchone()[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_contact_db.py -v`
Expected: PASS, all cases.

- [ ] **Step 5: Commit**

```bash
git add workflow/contact_db.py tests/test_contact_db.py
git commit -m "feat: the contact directory shares bidders.db and survives an AVL reload"
```

---

## Task 3: The store field, and the document key that must not appear

**Files:**
- Modify: `workflow/store.py` — `__init__` (around line 161, after `_rfq_documents`), plus three methods
- Modify: `workflow/persistence.py` — `load` (line 248), `save` (line 269), `locked_update` (line 290)
- Test: `tests/test_vendor_contact_store.py`

**Interfaces:**
- Consumes: `contact_db.replace_all`, `contact_db.list_all`, `contact_db.fold` (Task 2)
- Produces: `WorkflowStore.set_vendor_contacts(contacts: Iterable[VendorContact]) -> int`; `WorkflowStore.vendor_contacts() -> list[VendorContact]`; `WorkflowStore.emails_for(vendor_name: str) -> list[str] | None`; `persistence.save(root, store, *, registry: bool = True, contacts: bool = True)`
- **Store invariant owned:**
  - **V-C.** `workflow.json` has **no** `vendor_contacts` key, in any document this phase writes.

This is the second field exempt from the rule that every `WorkflowStore.__init__` field needs a matching line in `to_document` and `from_document`. That rule exists because a field without those lines silently fails to survive a restart — so an exemption is dangerous, and the only thing that makes it safe is that the field is loaded and saved somewhere *else*, and that a test says so. `_bidders` is the first. Write the comment in `__init__` next to the field, not only here.

A key in the document would be a second copy for the first edit to disagree with, which is the argument that moved the registry out in the first place.

`locked_update` compares against a shallow copy for the same reason the registry does: `set_vendor_contacts` rebuilds the dict rather than mutating stored models in place. Note that `parse_contacts` returns fresh objects each time, so this holds — but `contact.emails.extend(...)` inside the parser mutates a model the store has never seen, which is fine and is why the merge lives in the parser rather than the store.

- [ ] **Step 1: Write the failing test**

Create `tests/test_vendor_contact_store.py`:

```python
"""The contact directory in the store, and the key it must never write.

`_vendor_contacts` is the second `WorkflowStore` field with no line in
`to_document` / `from_document`, after `_bidders`. That rule exists because a
field without those lines silently fails to survive a restart, so the
exemption is only safe while something else loads and saves it — and while a
test says the document stays clean.
"""
import json
import os
from datetime import datetime, timezone

from workflow import contact_db, persistence
from workflow.models.vendor_contact import VendorContact
from workflow.store import WorkflowStore

WHEN = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)


def a_contact(name="DANWAY ABU DHABI L.L.C", emails=None) -> VendorContact:
    return VendorContact(
        vendor_name=name,
        emails=emails if emails is not None else ["sales@danway.example"],
        source_document="vendors.xlsx",
        uploaded_by="buyer@example.com",
        uploaded_at=WHEN,
    )


def test_an_address_is_found_by_a_name_folded(tmp_path):
    store = WorkflowStore()
    store.set_vendor_contacts([a_contact()])
    assert store.emails_for("danway  abu dhabi l.l.c") == ["sales@danway.example"]


def test_a_vendor_with_no_row_answers_none_and_never_an_empty_list(tmp_path):
    """Two states, not three. `None` is 'no contact held'; there is no `[]`,
    because a row with no address is refused at parse."""
    store = WorkflowStore()
    store.set_vendor_contacts([a_contact()])
    assert store.emails_for("BIN SARI SPECIALIZED TECHNOLOGIES") is None


def test_the_answer_is_a_copy_so_a_caller_cannot_edit_the_directory(tmp_path):
    store = WorkflowStore()
    store.set_vendor_contacts([a_contact()])
    store.emails_for("DANWAY ABU DHABI L.L.C").append("oops@x.example")
    assert store.emails_for("DANWAY ABU DHABI L.L.C") == ["sales@danway.example"]


def test_setting_the_directory_replaces_it_wholesale(tmp_path):
    store = WorkflowStore()
    store.set_vendor_contacts([a_contact(), a_contact(name="BIN SARI")])
    store.set_vendor_contacts([a_contact(name="BIN SARI")])
    assert store.emails_for("DANWAY ABU DHABI L.L.C") is None


def test_the_directory_survives_a_save_and_a_reload(tmp_path):
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([a_contact()])
    reloaded = persistence.load(root)
    assert reloaded.emails_for("DANWAY ABU DHABI L.L.C") == ["sales@danway.example"]


def test_the_document_carries_no_vendor_contacts_key(tmp_path):
    """V-C. A key here would be a second copy for the first edit to disagree
    with — the argument that moved the registry out."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([a_contact()])
    with open(persistence.workflow_path(root), encoding="utf-8") as handle:
        document = json.load(handle)
    assert "vendor_contacts" not in document
    assert not any("contact" in key for key in document)


def test_a_document_that_somehow_carries_the_key_does_not_hydrate_from_it(tmp_path):
    """The database is the only source. A hand-edited document must not be able
    to introduce a contact the directory does not hold."""
    root = str(tmp_path)
    os.makedirs(root, exist_ok=True)
    with open(persistence.workflow_path(root), "w", encoding="utf-8") as handle:
        json.dump({"vendor_contacts": [{"vendor_name": "X", "emails": ["x@x.example"]}]}, handle)
    assert persistence.load(root).emails_for("X") is None


def test_a_transition_that_touches_no_contact_does_not_rewrite_the_directory(tmp_path):
    """The same economy `locked_update` applies to the registry: a wholesale
    rewrite on every write would cost more than most writes do."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([a_contact()])
    stamp = os.path.getmtime(contact_db.db_path(root))
    with persistence.locked_update(root):
        pass
    assert os.path.getmtime(contact_db.db_path(root)) == stamp


def test_an_empty_directory_reads_back_as_no_contacts(tmp_path):
    assert persistence.load(str(tmp_path)).vendor_contacts() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vendor_contact_store.py -v`
Expected: FAIL — `AttributeError: 'WorkflowStore' object has no attribute 'set_vendor_contacts'`

- [ ] **Step 3: Write minimal implementation**

In `workflow/store.py`, add the field at the end of `__init__` (after `self._rfq_documents`) with the comment that makes the exemption visible:

```python
        # Keyed by `fold(vendor_name)`. **The second field with no line in
        # `to_document` / `from_document`, after `_bidders`, and for the same
        # reason** — it lives in `bidders.db`, hydrated by `persistence.load`
        # and written by `contact_db.replace_all`. A `vendor_contacts` key in
        # the document would be a second copy for the first edit to disagree
        # with. There is no `vendor_id` on any of these: see the design's §1.
        self._vendor_contacts: dict[str, VendorContact] = {}
```

with `from workflow.disciplines import fold` and `from workflow.models.vendor_contact import VendorContact` at the top, and three methods beside the bidder ones:

```python
    def set_vendor_contacts(self, contacts: Iterable[VendorContact]) -> int:
        """Replace the directory wholesale, returning how many it now holds.

        Wholesale because an upload is a document: a vendor dropped from the
        sheet must not survive in the directory. Rebuilds the dict rather than
        mutating it, which is what lets `locked_update` notice the change by
        comparing against a shallow copy.
        """
        self._vendor_contacts = {fold(c.vendor_name): c for c in contacts}
        return len(self._vendor_contacts)

    def vendor_contacts(self) -> list[VendorContact]:
        return sorted(self._vendor_contacts.values(), key=lambda c: fold(c.vendor_name))

    def emails_for(self, vendor_name: str) -> list[str] | None:
        """Where to send this vendor's enquiry, or `None` if nothing is held.

        Keyed on the **name**, folded — there is no `vendor_id` in the
        directory, so a hand-typed shortlist row resolves exactly as a
        registry-linked one does. Two states and never three: `[]` cannot
        occur, because a contact with no address is refused at parse.

        A copy, so a caller cannot edit the directory through the answer.
        """
        contact = self._vendor_contacts.get(fold(vendor_name))
        return list(contact.emails) if contact else None
```

In `workflow/persistence.py`:

```python
# in load(), after the registry hydration
    store._vendor_contacts = {
        contact_db.fold(c.vendor_name): c for c in contact_db.list_all(root)
    }
    return store


# save() gains a second flag, defaulting True for the same reason `registry`
# does: a direct caller must not be able to drop an edit by omission.
def save(root: str, store: WorkflowStore, *, registry: bool = True,
         contacts: bool = True) -> None:
    if registry:
        bidder_db.replace_all(root, store._bidders.values())
    if contacts:
        contact_db.replace_all(root, store._vendor_contacts.values())
    layout.atomic_write_json(workflow_path(root), to_document(store))


# in locked_update(), beside registry_before
        contacts_before = dict(store._vendor_contacts)
        yield store
        save(
            root, store,
            registry=store._bidders != registry_before,
            contacts=store._vendor_contacts != contacts_before,
        )
```

`to_document` and `from_document` are **not** touched. Add a line to `to_document`'s existing comment about `bidders` naming `_vendor_contacts` as the second collection that lives elsewhere.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_vendor_contact_store.py tests/test_workflow_persistence.py -v`
Expected: PASS. The existing persistence suite must stay green — `save`'s new keyword is defaulted, so every existing caller is unchanged.

- [ ] **Step 5: Commit**

```bash
git add workflow/store.py workflow/persistence.py tests/test_vendor_contact_store.py
git commit -m "feat: the directory hydrates from bidders.db and writes no document key"
```

---

## Task 4: The routes, and an address on every shortlist row

**Files:**
- Modify: `api/workflow_routes.py` — two new routes, and `_shortlist_payload` (line 815)
- Test: `tests/test_vendor_contact_endpoints.py`

**Interfaces:**
- Consumes: `parse_contacts` (Task 1), `contact_db` (Task 2), `store.set_vendor_contacts` / `store.emails_for` (Task 3)
- Produces: `POST /api/workflow/vendor-contacts` → `{"summary": {"parsed": int, "stored": int, "addresses": int, "matched": int}, "contacts": [...]}`; `GET /api/workflow/vendor-contacts` → `{"contacts": [...], "count": int, "uploaded_by": str | null, "uploaded_at": str | null, "source_document": str | null}`; `_shortlist_payload` gains `"email": list[str] | None`
- **Store invariant owned:**
  - **V-F.** No `ShortlistEntry` stores the address it reports.

Parse **outside** the lock and write **inside** it — the shape `/vendor-list` already uses, and the reason is the same: parsing is pure CPU over a file, and holding the store lock through it blocks every other writer. The temporary file is removed either way. The workbook itself is not kept; the entries are the record and `source_document` holds the filename.

The summary is counts rather than a tick, for the reason the vendor-list upload gives its own: an empty or half-size result is a spelling problem, not a failure, and only the numbers say which. `matched` is computed inside the lock against `bidders.name_key` and **stored nowhere** — nothing else in the system consults it and no stored field is written from it.

`_shortlist_payload` gains one derived key. This is the third instance on that record of "derived on read, stored nowhere", after `client_approved` and `approved_by`, and the reason is unchanged: a copy taken at invitation is wrong the moment the directory is corrected, and correcting it is the common case.

Route ordering is not at risk here — `/vendor-contacts` is a literal path with no `{param}` sibling that could swallow it, unlike `/bidders/approved` and `/rfqs/extract`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_vendor_contact_endpoints.py`:

```python
"""The contact directory over HTTP, and the address on a shortlist row.

Workbooks are built in memory, so this runs in CI with no fixture directory.
What matters most here is the negative space: the upload changes nothing about
the registry, and no shortlist entry stores the address it reports.
"""
import json

import openpyxl
import pytest
from fastapi.testclient import TestClient

from tests.auth_helpers import ADMIN_EMAIL, signed_in_admin
from workflow import bidder_db, persistence
from workflow.models.bidder import ADNOC, ASTRA, Bidder


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return signed_in_admin(TestClient(api_main.app), tmp_path)


def _sheet(tmp_path, rows, name="vendors.xlsx", headers=("Vendor", "Email")):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(headers))
    for row in rows:
        ws.append(list(row))
    path = tmp_path / name
    wb.save(path)
    return path


def _upload(client, path):
    with open(path, "rb") as handle:
        return client.post(
            "/api/workflow/vendor-contacts",
            files={"file": (path.name, handle,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )


def _an_rfq_with_one_invited_vendor(client, tmp_path, vendor_name, vendor_id=None):
    """A project, an item, an RFQ and one shortlist entry — the shortest path
    to a row that can carry an address."""
    project = client.post("/api/workflow/projects", json={
        "name": "Habshan", "client": "ADNOC",
    }).json()
    item = client.post(f"/api/workflow/projects/{project['id']}/items", json={
        "item_type": "cable", "description": "LV power cable", "qty": 1,
        "uom": "lot", "discipline": "CABLES - LV POWER DISTRIBUTION",
        "estimated_value_aed": 100000,
    }).json()
    rfq = client.post("/api/workflow/rfqs", json={
        "project_id": project["id"], "item_ids": [item["id"]],
        "package": "LV cable", "discipline": "CABLES - LV POWER DISTRIBUTION",
        "value_estimate_aed": 100000,
    }).json()
    rfq_id = rfq["rfq"]["id"] if "rfq" in rfq else rfq["id"]
    body = {"vendor_name": vendor_name}
    if vendor_id:
        body["vendor_id"] = vendor_id
    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json=body)
    return rfq_id


def test_an_upload_reports_what_it_read(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    bidder_db.replace_all(str(tmp_path), [
        Bidder(id="bdr_1", name="DANWAY ABU DHABI L.L.C", approved_by=[ADNOC]),
    ])
    path = _sheet(tmp_path, [
        ("DANWAY ABU DHABI L.L.C", "sales@danway.example; bids@danway.example"),
        ("BIN SARI SPECIALIZED TECHNOLOGIES", "bids@binsari.example"),
    ])
    response = _upload(client, path)
    assert response.status_code == 200
    assert response.json()["summary"] == {
        "parsed": 2, "stored": 2, "addresses": 3, "matched": 1,
    }


def test_a_workbook_with_no_email_column_is_refused_with_the_parsers_sentence(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, monkeypatch)
    path = _sheet(tmp_path, [("DANWAY ABU DHABI L.L.C", "Abu Dhabi")],
                  headers=("Vendor", "City"))
    response = _upload(client, path)
    assert response.status_code == 422
    assert "Email" in response.json()["detail"]


def test_the_upload_changes_nothing_about_the_registry(tmp_path, monkeypatch):
    """The registry is organisation-wide and arrives whole from its own import.
    A sheet of addresses must not enrol a company or grant an approval."""
    client = _client(tmp_path, monkeypatch)
    bidder_db.replace_all(str(tmp_path), [
        Bidder(id="bdr_1", name="DANWAY ABU DHABI L.L.C", approved_by=[ASTRA]),
    ])
    before = [b.model_dump(mode="json") for b in bidder_db.list_all(str(tmp_path))]
    _upload(client, _sheet(tmp_path, [
        ("DANWAY ABU DHABI L.L.C", "sales@danway.example"),
        ("A COMPANY NOBODY HAS REGISTERED", "hello@nobody.example"),
    ]))
    after = [b.model_dump(mode="json") for b in bidder_db.list_all(str(tmp_path))]
    assert after == before


def test_reading_the_directory_names_who_loaded_it(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _upload(client, _sheet(tmp_path, [("DANWAY ABU DHABI L.L.C", "sales@danway.example")]))
    body = client.get("/api/workflow/vendor-contacts").json()
    assert body["count"] == 1
    assert body["uploaded_by"] == ADMIN_EMAIL
    assert body["source_document"] == "vendors.xlsx"
    assert body["contacts"][0]["emails"] == ["sales@danway.example"]


def test_an_empty_directory_reads_as_a_count_of_zero_and_no_uploader(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, monkeypatch)
    body = client.get("/api/workflow/vendor-contacts").json()
    assert body["count"] == 0
    assert body["uploaded_by"] is None


def test_a_second_upload_replaces_the_directory(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _upload(client, _sheet(tmp_path, [
        ("DANWAY ABU DHABI L.L.C", "sales@danway.example"),
        ("BIN SARI SPECIALIZED TECHNOLOGIES", "bids@binsari.example"),
    ], name="first.xlsx"))
    _upload(client, _sheet(tmp_path, [
        ("BIN SARI SPECIALIZED TECHNOLOGIES", "bids@binsari.example"),
    ], name="second.xlsx"))
    names = [c["vendor_name"] for c in
             client.get("/api/workflow/vendor-contacts").json()["contacts"]]
    assert names == ["BIN SARI SPECIALIZED TECHNOLOGIES"]


def test_a_shortlist_row_carries_the_address_held_for_its_vendor(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, monkeypatch)
    rfq_id = _an_rfq_with_one_invited_vendor(client, tmp_path, "DANWAY ABU DHABI L.L.C")
    _upload(client, _sheet(tmp_path, [
        ("danway  abu dhabi l.l.c", "sales@danway.example"),
    ]))
    (row,) = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["shortlist"]
    assert row["email"] == ["sales@danway.example"]


def test_a_vendor_with_no_row_reports_no_address_rather_than_an_empty_list(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, monkeypatch)
    rfq_id = _an_rfq_with_one_invited_vendor(client, tmp_path, "BIN SARI SPECIALIZED TECHNOLOGIES")
    _upload(client, _sheet(tmp_path, [("DANWAY ABU DHABI L.L.C", "sales@danway.example")]))
    (row,) = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["shortlist"]
    assert row["email"] is None


def test_a_spelling_the_fold_does_not_reach_reports_no_address(tmp_path, monkeypatch):
    """`L.L.C` against `LLC` is a miss, and that is the correct outcome.
    Widening the fold is how an enquiry reaches the wrong company."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = _an_rfq_with_one_invited_vendor(client, tmp_path, "DANWAY ABU DHABI L.L.C")
    _upload(client, _sheet(tmp_path, [("Danway Abu Dhabi LLC", "sales@danway.example")]))
    (row,) = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["shortlist"]
    assert row["email"] is None


def test_a_hand_typed_vendor_gets_an_address_too(tmp_path, monkeypatch):
    """The upside of name-keying: a vendor on nobody's register still resolves.
    An id-keyed directory could never reach them."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = _an_rfq_with_one_invited_vendor(client, tmp_path, "A COMPANY NOBODY HAS REGISTERED")
    _upload(client, _sheet(tmp_path, [("A COMPANY NOBODY HAS REGISTERED", "hello@nobody.example")]))
    (row,) = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["shortlist"]
    assert row["vendor_id"] is None
    assert row["email"] == ["hello@nobody.example"]


def test_no_shortlist_entry_stores_the_address_it_reports(tmp_path, monkeypatch):
    """V-F. An absence assertion, so it passes the moment it is written —
    verify it by adding `email` to `ShortlistEntry` and watching it go red.
    Third instance of this discipline in this repository."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = _an_rfq_with_one_invited_vendor(client, tmp_path, "DANWAY ABU DHABI L.L.C")
    _upload(client, _sheet(tmp_path, [("DANWAY ABU DHABI L.L.C", "sales@danway.example")]))
    client.get(f"/api/workflow/rfqs/{rfq_id}")
    with open(persistence.workflow_path(str(tmp_path)), encoding="utf-8") as handle:
        raw = handle.read()
    assert "sales@danway.example" not in raw
    document = json.loads(raw)
    for entry in document["shortlists"][rfq_id] if isinstance(
        document.get("shortlists"), dict
    ) else document.get("shortlists", []):
        assert "email" not in entry


def test_correcting_the_sheet_corrects_every_shortlist_with_nothing_rewritten(
    tmp_path, monkeypatch
):
    """The whole argument for deriving on read: re-upload, and every shortlist
    in the system is right."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = _an_rfq_with_one_invited_vendor(client, tmp_path, "DANWAY ABU DHABI L.L.C")
    _upload(client, _sheet(tmp_path, [("DANWAY ABU DHABI L.L.C", "wrong@danway.example")],
                           name="first.xlsx"))
    _upload(client, _sheet(tmp_path, [("DANWAY ABU DHABI L.L.C", "right@danway.example")],
                           name="second.xlsx"))
    (row,) = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["shortlist"]
    assert row["email"] == ["right@danway.example"]


def test_the_upload_needs_a_session(tmp_path, monkeypatch):
    """Workflow routes are not on PUBLIC_PATHS and must not be."""
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    anonymous = TestClient(api_main.app)
    assert anonymous.get("/api/workflow/vendor-contacts").status_code == 401
```

Note on `test_no_shortlist_entry_stores_the_address_it_reports`: read how `test_workflow_persistence.py` addresses the shortlist collection in the document and match it, rather than keeping the defensive branch above. The substring assertion on the raw file is the load-bearing half and works regardless of shape.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vendor_contact_endpoints.py -v`
Expected: FAIL — 404 on `/api/workflow/vendor-contacts`.

- [ ] **Step 3: Write minimal implementation**

In `api/workflow_routes.py`, add the imports (`from workflow import contact_db`, `from workflow.contact_import import parse_contacts`) and the two routes. The temp-file handling mirrors `upload_item_vendor_list` (line ~490) — read that and follow it rather than inventing a second pattern.

```python
@router.post("/vendor-contacts")
def upload_vendor_contacts(
    file: UploadFile = File(...),
    user: User = Depends(current_user),
) -> dict:
    """Replace the organisation-wide vendor contact directory from a sheet.

    **Organisation-wide, not per-RFQ**, even though the control that calls this
    sits on one RFQ's Shortlisting step. The screen says so; this docstring is
    the other half of saying it.

    Parse outside the lock, write inside it — parsing is pure CPU over a file
    and holding the store lock through it blocks every other writer. The
    workbook is not kept: the entries are the record and `source_document`
    holds the file name, the rule `/vendor-list` and `/rfqs/extract` keep.

    **Nothing here touches the registry.** A sheet of addresses must not enrol
    a company or grant an approval; `approved_by` moves only through
    `python -m workflow.avl_db`.
    """
    if not file.filename:
        raise HTTPException(status_code=422, detail="The upload has no file name.")

    suffix = os.path.splitext(file.filename)[1] or ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as scratch:
        scratch.write(file.file.read())
        scratch_path = scratch.name
    try:
        parsed = parse_contacts(
            scratch_path,
            uploaded_by=user.email,
            uploaded_at=datetime.now(timezone.utc),
            source_document=file.filename,
        )
    except ValueError as exc:
        # The parser's own sentence, which names the missing column or the
        # offending row. A refusal that does not say what is wrong with the
        # file leaves the reader nothing to act on.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        os.unlink(scratch_path)

    with persistence.locked_update(_root()) as store:
        stored = store.set_vendor_contacts(parsed)
        # Informational only, computed here and kept nowhere: nothing else in
        # the system consults it and no stored field is written from it. An
        # upload that matches nobody is almost always a spelling problem, and
        # a bare "8 rows stored" hides that.
        registered = {contact_db.fold(b.name) for b in store.list_bidders()}
        matched = sum(
            1 for c in store.vendor_contacts()
            if contact_db.fold(c.vendor_name) in registered
        )
        contacts = [c.model_dump(mode="json") for c in store.vendor_contacts()]
        addresses = sum(len(c.emails) for c in store.vendor_contacts())

    return {
        "contacts": contacts,
        "summary": {
            "parsed": len(parsed), "stored": stored,
            "addresses": addresses, "matched": matched,
        },
    }


@router.get("/vendor-contacts")
def read_vendor_contacts() -> dict:
    """The whole directory, plus who loaded it and when.

    The provenance rides alongside so the screen can say *organisation-wide,
    uploaded by … on …* — a control that sits inside one RFQ while writing a
    shared directory has to say which it is.
    """
    store = _read()
    contacts = store.vendor_contacts()
    latest = max(contacts, key=lambda c: c.uploaded_at, default=None)
    return {
        "contacts": [c.model_dump(mode="json") for c in contacts],
        "count": len(contacts),
        "uploaded_by": latest.uploaded_by if latest else None,
        "uploaded_at": latest.uploaded_at.isoformat() if latest else None,
        "source_document": latest.source_document if latest else None,
    }
```

And one key on `_shortlist_payload`, with the docstring extended to name the third derived field:

```python
        # Derived on read, like the two keys above and for the same reason: a
        # copy taken at invitation is wrong the moment the directory is
        # corrected, and correcting it is the common case. Keyed on the
        # **name**, not `vendor_id` — the directory holds no registry link, so
        # a hand-typed row resolves exactly as a registry-linked one does.
        # Two states: a list, or `None` for no contact held. Never `[]`.
        "email": store.emails_for(entry.vendor_name),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_vendor_contact_endpoints.py tests/test_auth_middleware.py -v`
Expected: PASS. `test_auth_middleware.py`'s route sweep covers every workflow path; if the new routes need a probe substitution, add it there — that assertion exists to force exactly this.

- [ ] **Step 5: Verify V-F is wired to something**

Add `email: list[str] = []` to `ShortlistEntry` in `workflow/models/rfq.py`, re-run `test_no_shortlist_entry_stores_the_address_it_reports`, and confirm it **fails**. Then revert. An absence assertion nobody has watched fail is not known to be wired to anything — this is the third time this repository has needed that check.

- [ ] **Step 6: Commit**

```bash
git add api/workflow_routes.py tests/test_vendor_contact_endpoints.py
git commit -m "feat: an invited bidder's address is derived on read, never stored"
```

---

## Task 5: The screen

**Files:**
- Modify: `web/src/types.ts` — `ShortlistEntry` gains `email`; add `VendorContact`, `VendorContactDirectory`, `VendorContactSummary`
- Modify: `web/src/api.ts` — `uploadVendorContacts`, `fetchVendorContacts`
- Modify: `web/src/pages/wizard/ShortlistingStep.tsx`
- Test: `web/src/pages/wizard/ShortlistingStep.test.tsx`

**Interfaces:**
- Consumes: the two routes from Task 4
- Produces: `uploadVendorContacts(file: File): Promise<{ contacts: VendorContact[]; summary: VendorContactSummary }>`; `fetchVendorContacts(): Promise<VendorContactDirectory>`
- **Store invariant owned:** none — this task writes nothing to any store. Fabricating one would be decoration; the invariants it *displays* are owned by Tasks 2–4.

The upload control reuses the `.sr-only` input behind a `<button>` that `VendorListUpload` in `forms.tsx:283` uses — the browser's "No file chosen" text is unrelabelable, so it is hidden and one status line reports what was read. Follow that component; do not write a new `.dropzone`, which is a mistake this repository has already made once by shipping a same-named CSS block that silently restyled another screen.

The caption is load-bearing. The control sits inside one RFQ but writes a shared directory, and a control that looks per-RFQ while behaving globally invites a buyer to overwrite everyone else's addresses believing they are editing their own enquiry.

`run` owns the busy flag and the server's refusal sentence, so the upload goes through it — but `run` returns `void`, so the summary is captured inside the closure into local state. `run` also ticks the wizard, which refetches the RFQ *and* any `useAsync` keyed on `tick`, so the Email column and the caption both refresh from one call.

- [ ] **Step 1: Write the failing test**

Append to `web/src/pages/wizard/ShortlistingStep.test.tsx`. The `vi.mock('../../api', …)` factory at the top of the file must gain `uploadVendorContacts` and `fetchVendorContacts`, and `BASE`'s `entry()` helper must gain `email: null`:

```tsx
import { fireEvent, waitFor } from '@testing-library/react'
import { fetchVendorContacts, uploadVendorContacts } from '../../api'

// in the vi.mock factory:
//   uploadVendorContacts: vi.fn(),
//   fetchVendorContacts: vi.fn(),

const DIRECTORY = {
  contacts: [],
  count: 8,
  uploaded_by: 'buyer@example.com',
  uploaded_at: '2026-08-18T09:00:00+00:00',
  source_document: 'vendors.xlsx',
}

describe('vendor contact directory', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchVendorContacts).mockResolvedValue(DIRECTORY)
  })

  it('shows an invited bidder’s address', async () => {
    render(
      <ShortlistingStep
        data={{ ...BASE, shortlist: [entry({ email: ['sales@danway.example'] })] }}
        run={vi.fn()} busy={false} tick={0}
      />,
    )
    expect(await screen.findByText('sales@danway.example')).toBeInTheDocument()
  })

  it('shows every address a vendor holds, not just the first', async () => {
    render(
      <ShortlistingStep
        data={{ ...BASE, shortlist: [entry({
          email: ['sales@danway.example', 'bids@danway.example'],
        })] }}
        run={vi.fn()} busy={false} tick={0}
      />,
    )
    expect(await screen.findByText('bids@danway.example')).toBeInTheDocument()
  })

  it('says no address is on file rather than rendering an empty cell', async () => {
    render(
      <ShortlistingStep
        data={{ ...BASE, shortlist: [entry({ email: null })] }}
        run={vi.fn()} busy={false} tick={0}
      />,
    )
    expect(await screen.findByText(/no address on file/i)).toBeInTheDocument()
  })

  it('says the directory is organisation-wide, not this RFQ’s', async () => {
    // The control sits inside one RFQ and writes a shared directory. Without
    // this caption a buyer overwrites everyone else's addresses believing they
    // are editing their own enquiry.
    render(<ShortlistingStep data={BASE} run={vi.fn()} busy={false} tick={0} />)
    const caption = await screen.findByText(/organisation-wide/i)
    expect(caption).toHaveTextContent('8')
    expect(caption).toHaveTextContent('buyer@example.com')
  })

  it('uploads the chosen sheet through run, so the wizard reloads', async () => {
    vi.mocked(uploadVendorContacts).mockResolvedValue({
      contacts: [],
      summary: { parsed: 9, stored: 8, addresses: 8, matched: 6 },
    })
    const run = vi.fn(async (action: () => Promise<unknown>) => {
      await action()
    })
    render(<ShortlistingStep data={BASE} run={run} busy={false} tick={0} />)
    const file = new File(['x'], 'vendors.xlsx')
    fireEvent.change(screen.getByLabelText(/add vendor email list/i), {
      target: { files: [file] },
    })
    await waitFor(() => expect(uploadVendorContacts).toHaveBeenCalledWith(file))
    expect(run).toHaveBeenCalled()
  })

  it('reads the counts back rather than showing a bare tick', async () => {
    // An upload that matched nobody is a spelling problem, not a success, and
    // only the numbers say which.
    vi.mocked(uploadVendorContacts).mockResolvedValue({
      contacts: [],
      summary: { parsed: 9, stored: 8, addresses: 8, matched: 6 },
    })
    const run = vi.fn(async (action: () => Promise<unknown>) => {
      await action()
    })
    render(<ShortlistingStep data={BASE} run={run} busy={false} tick={0} />)
    fireEvent.change(screen.getByLabelText(/add vendor email list/i), {
      target: { files: [new File(['x'], 'vendors.xlsx')] },
    })
    const line = await screen.findByText(/8 of 9/i)
    expect(line).toHaveTextContent('6')
  })

  it('keeps the email column inside a horizontally scrolling wrapper', async () => {
    const { container } = render(
      <ShortlistingStep
        data={{ ...BASE, shortlist: [entry()] }}
        run={vi.fn()} busy={false} tick={0}
      />,
    )
    await screen.findByText('Al Munara Switchgear LLC')
    expect(container.querySelector('.table-scroll .table')).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `web/`): `npm test -- ShortlistingStep`
Expected: FAIL — `fetchVendorContacts` is not exported, and there is no Email column.

- [ ] **Step 3: Write minimal implementation**

`web/src/types.ts` — one field on `ShortlistEntry`, documented as the third derived one:

```ts
  /** Where this vendor's enquiry goes, from the organisation-wide contact
   *  directory, derived by the server on every read like `client_approved`
   *  above it. `null` means no contact is held for this vendor's name — never
   *  an empty array, because a contact with no address is refused at upload.
   *  Matched on the **name**, folded: the directory holds no registry link. */
  email: string[] | null
```

```ts
export interface VendorContact {
  vendor_name: string
  emails: string[]
  source_document: string
  uploaded_by: string
  uploaded_at: string
}

/** The directory, plus who loaded it — the screen says *organisation-wide*
 *  rather than letting a control inside one RFQ read as that RFQ's own. */
export interface VendorContactDirectory {
  contacts: VendorContact[]
  count: number
  uploaded_by: string | null
  uploaded_at: string | null
  source_document: string | null
}

export interface VendorContactSummary {
  parsed: number
  stored: number
  addresses: number
  matched: number
}
```

`web/src/api.ts`:

```ts
/** Replace the organisation-wide vendor contact directory.
 *
 *  Shared, despite the control living on one RFQ's Shortlisting step: this
 *  overwrites the directory every RFQ reads. */
export function uploadVendorContacts(
  file: File,
): Promise<{ contacts: VendorContact[]; summary: VendorContactSummary }> {
  return sendFile('/api/workflow/vendor-contacts', file)
}

export function fetchVendorContacts(): Promise<VendorContactDirectory> {
  return getJson('/api/workflow/vendor-contacts')
}
```

`ShortlistingStep.tsx` — the directory is a *second* resource, so it is keyed on `tick`:

```tsx
  const { data: directory } = useAsync(() => fetchVendorContacts(), [tick])
  const [summary, setSummary] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
```

above the Invited bidders table:

```tsx
      <h3>Invited bidders</h3>
      {/* The directory is shared by every RFQ. This caption is the only thing
          that says so, and without it a buyer overwrites everyone else's
          addresses believing they are editing their own enquiry. */}
      <p className="muted">
        Vendor addresses are organisation-wide
        {directory ? ` · ${directory.count} vendors` : ''}
        {directory?.uploaded_by
          ? ` · uploaded by ${directory.uploaded_by}`
          : ''}
      </p>
      <input
        id="vendor-contacts"
        className="sr-only"
        ref={fileRef}
        type="file"
        accept=".xlsx"
        aria-label="Add vendor email list"
        disabled={busy}
        onChange={(e) => {
          const file = e.target.files?.[0]
          e.target.value = ''      // so picking the same file twice reads it twice
          if (!file) return
          void run(async () => {
            const { summary: s } = await uploadVendorContacts(file)
            setSummary(
              `${s.stored} of ${s.parsed} stored · ${s.addresses} addresses · ` +
              `${s.matched} in the registry`,
            )
          })
        }}
      />
      <button type="button" className="btn btn-sm" disabled={busy}
              onClick={() => fileRef.current?.click()}>
        Add vendor email list
      </button>
      {summary && <p className="muted">{summary}</p>}
```

and one column in the table, header and cell:

```tsx
                <th scope="col">Email</th>
```

```tsx
                  {/* Live, like Approvals beside it: re-uploading the sheet
                      corrects every shortlist with nothing rewritten. `null`
                      is 'no contact held', which is a real finding — the
                      directory was consulted. */}
                  <td>
                    {e.email === null ? (
                      <span className="muted">No address on file</span>
                    ) : (
                      e.email.map((address) => <div key={address}>{address}</div>)
                    )}
                  </td>
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `web/`): `npm test` then `npm run build`
Expected: PASS, and the build type-checks the test files (`tsconfig.app.json` includes `src`). Every existing fixture that builds a `ShortlistEntry` needs `email`, including `web/src/pages/workflow-fixtures.ts` — the build is what finds them.

- [ ] **Step 5: Verify the geometry in a browser**

jsdom applies no stylesheet and does no layout, so none of the tests above can see a column that overruns its card — a defect this repository has shipped four times. Launch with `.\run.ps1`, open an RFQ at Shortlisting, and measure:

- `getBoundingClientRect().width` on the table versus its card, at a 940px viewport. It now has six columns; if it overruns, the `.table-scroll` wrapper is what must be scrolling, not the document.
- A vendor with two addresses, to confirm the stacked `<div>`s do not blow the row height out the way the eleven-product-group cell did.

Set `style.transition = 'none'` before reading any transitioned property — with the preview pane hidden, `document.visibilityState` is `"hidden"` and transitions never advance, which has already cost one false alarm here.

- [ ] **Step 6: Commit**

```bash
git add web/src/types.ts web/src/api.ts web/src/pages/wizard/ShortlistingStep.tsx web/src/pages/wizard/ShortlistingStep.test.tsx web/src/pages/workflow-fixtures.ts
git commit -m "feat: the shortlist says where each enquiry goes, and where the list came from"
```

---

## Task 6: The two-run matrix, and the baselines

**Files:**
- Create: `tests/test_vendor_contact_lifecycle.py`
- Modify: `CLAUDE.md`
- Test: the file above, plus a full-suite measurement

**Interfaces:**
- Consumes: everything above
- Produces: no new interface — this task defends the invariants the others own
- **Store invariant owned:** none newly; this task is the defending matrix for **V-A, V-B, V-C, V-D, V-E, V-F**, each of which is owned by an earlier task.

Every defect this repository has shipped and not caught needed **two runs or two modules** to see. Single-run tests structurally cannot find them. Each row below names the invariant it defends; a row defending nothing is decoration, and an invariant with no row is untested.

**Five of PLAN-TEMPLATE's nine required rows have no analogue here and are deliberately absent rather than fabricated.** This subsystem calls no model, so the four rows about LLM failure, prompt-version bumps and an omitted optional array have nothing to mutate; and there is no revision lineage, so the superseded-sibling row has no door to knock on. Do not invent them.

| mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|
| a second sheet is uploaded, dropping a vendor run 1 held | V-A | the dropped vendor is gone, not merged |
| a second sheet is uploaded, dropping one address of a vendor it keeps | V-B | the dropped address is gone |
| the store is saved and reloaded between the upload and the lookup | V-E | the folded lookup still resolves — the key came off disk, not out of the call that wrote it |
| the ADNOC export is reloaded between runs | V-D | every contact and address survives |
| a shortlist entry is added on run 2 for a vendor uploaded on run 1 | V-F | the address appears with nothing having rewritten the shortlist |
| a vendor's `approved_by` is edited between runs | V-F | the address is unchanged and still derived |
| a document written on run 1 is loaded on run 2 | V-C | no `vendor_contacts` key, before or after |

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vendor_contact_lifecycle.py`. Each test is one matrix row and says which in its docstring. The load-bearing shape is that **run 2 reads state that came off disk**, never out of the call that wrote it:

```python
"""The contact directory across two runs.

Every defect this repository has shipped and not caught needed two runs or two
modules to see. These are the two-run tests; the per-module ones live in
`test_contact_db.py`, `test_vendor_contact_store.py` and
`test_vendor_contact_endpoints.py`.
"""
import json
from datetime import datetime, timezone

from workflow import bidder_db, contact_db, persistence
from workflow.models.bidder import ADNOC, ASTRA, Bidder
from workflow.models.vendor_contact import VendorContact

WHEN = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)


def a_contact(name, emails) -> VendorContact:
    return VendorContact(
        vendor_name=name, emails=emails, source_document="vendors.xlsx",
        uploaded_by="buyer@example.com", uploaded_at=WHEN,
    )


def test_a_second_upload_does_not_leave_the_first_uploads_vendor_behind(tmp_path):
    """V-A. Run 2 replaces; it does not merge."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([
            a_contact("DANWAY ABU DHABI L.L.C", ["sales@danway.example"]),
            a_contact("BIN SARI", ["bids@binsari.example"]),
        ])
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([a_contact("BIN SARI", ["bids@binsari.example"])])
    reloaded = persistence.load(root)
    assert reloaded.emails_for("DANWAY ABU DHABI L.L.C") is None
    assert reloaded.emails_for("BIN SARI") == ["bids@binsari.example"]


def test_a_second_upload_does_not_leave_a_dropped_address_behind(tmp_path):
    """V-B. The vendor stays and one of their addresses goes."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([
            a_contact("DANWAY ABU DHABI L.L.C", ["sales@danway.example", "old@danway.example"]),
        ])
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([
            a_contact("DANWAY ABU DHABI L.L.C", ["sales@danway.example"]),
        ])
    assert persistence.load(root).emails_for("DANWAY ABU DHABI L.L.C") == [
        "sales@danway.example",
    ]


def test_the_folded_lookup_still_resolves_after_a_reload(tmp_path):
    """V-E. Run 2's key came off disk, not out of the call that wrote it. A
    fold applied on write and not on read passes run 1 and fails here."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([
            a_contact("DANWAY  Abu Dhabi L.L.C", ["sales@danway.example"]),
        ])
    reloaded = persistence.load(root)
    assert reloaded.emails_for("danway abu dhabi l.l.c") == ["sales@danway.example"]


def test_reloading_the_registry_between_runs_leaves_every_address(tmp_path):
    """V-D. Two modules, one file. This is the row a single-module test cannot
    reach."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([
            a_contact("DANWAY ABU DHABI L.L.C", ["sales@danway.example", "bids@danway.example"]),
        ])
    bidder_db.replace_all(root, [
        Bidder(id="bdr_1", name="DANWAY ABU DHABI L.L.C", approved_by=[ADNOC]),
    ])
    assert persistence.load(root).emails_for("DANWAY ABU DHABI L.L.C") == [
        "sales@danway.example", "bids@danway.example",
    ]


def test_a_shortlist_added_after_the_upload_still_gets_the_address(tmp_path, monkeypatch):
    """V-F. Derived on read — the entry did not exist when the sheet landed."""
    # Build the RFQ through the API the way `test_vendor_contact_endpoints.py`
    # does, upload on run 1, invite on run 2, then read.
    ...


def test_editing_a_vendors_approvals_does_not_disturb_their_address(tmp_path):
    """V-F. The two derived keys on that row are independent."""
    ...


def test_a_document_written_on_run_one_still_carries_no_contact_key(tmp_path):
    """V-C. Written by run 1, loaded and rewritten by run 2 — the key must be
    absent both times, since a `from_document` that invented one would only
    show up on the second write."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([a_contact("DANWAY", ["sales@danway.example"])])
    first = json.loads(open(persistence.workflow_path(root), encoding="utf-8").read())
    with persistence.locked_update(root):
        pass
    second = json.loads(open(persistence.workflow_path(root), encoding="utf-8").read())
    assert "vendor_contacts" not in first
    assert "vendor_contacts" not in second
```

Fill in the two elided bodies against `test_vendor_contact_endpoints.py`'s `_client` and `_an_rfq_with_one_invited_vendor` helpers — import them rather than copying, so a copy in this file cannot agree with a wrong original. That is the rule `test_mock_round_fixtures.py` records for `_already_done`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_vendor_contact_lifecycle.py -v`
Expected: FAIL on the elided tests until their bodies are written; the rest should pass if Tasks 1–4 are correct. A row that passes before its implementation exists is a row asserting nothing — check it.

- [ ] **Step 3: Verify each row is real, by reinstating its defect**

One at a time, reintroduce the defect and confirm **the intended row fails and nothing else does**. A row that still passes with its defect reinstated is not testing what it claims.

| defect to reinstate | expected to fail |
|---|---|
| `set_vendor_contacts` updates the dict instead of rebinding it | the V-A row |
| `contact_db.replace_all` deletes from `vendor_contacts` but not the child table (drop the cascade / `PRAGMA`) | the V-B row |
| `emails_for` looks up `vendor_name` unfolded | the V-E row |
| `contact_db.replace_all` runs `DROP TABLE IF EXISTS bidders` before its own schema | the V-D row |
| `to_document` gains a `vendor_contacts` key | the V-C row |
| `email` becomes a field on `ShortlistEntry` | `test_no_shortlist_entry_stores_the_address_it_reports` (Task 4) |

- [ ] **Step 4: Measure both baselines**

Run: `python -m pytest` from the repo root, and `npm test` from `web/`.

**Measure the workstation row; do not derive it.** Then derive the CI row from it by the documented subtraction (four corpus-coverage, three `data/`, two `pdftotext`, eleven AVL). The AVL gate stays at **eleven** and is **not re-measured** — no task here touched `test_avl_import.py`, `test_seed_demo.py` or `test_disciplines.py`, which is the condition CLAUDE.md sets for re-measuring it. Say so in the CLAUDE.md paragraph rather than leaving a reader to wonder.

- [ ] **Step 5: Update CLAUDE.md**

Add a paragraph in the counts narrative naming this phase's contribution, and add to the RFQ workflow invariants section:

- the directory's storage split (`bidders.db`, its own tables, and why not a column on `bidders`);
- `_vendor_contacts` as the **second** exemption from the `to_document` / `from_document` rule, stated as loudly as `_bidders`;
- name-keying, the fold's deliberate narrowness, and that nothing here resolves a name to a registry row;
- `email` on a shortlist row as the third derived key on that record, with `None` and never `[]`;
- that **no mail is sent** and the sender is recorded but unused.

- [ ] **Step 6: Commit**

```bash
git add tests/test_vendor_contact_lifecycle.py CLAUDE.md
git commit -m "test: the directory holds across two runs, and the baselines move"
```

---

## Self-review

**Spec coverage.** §1 name-keying → Tasks 1, 3, 4 (and the fold-miss test in Task 4). §2 storage → Task 2. §3 model → Task 1. §4 parser → Task 1. §5 store and persistence → Task 3. §6 routes → Task 4. §7 derived read → Task 4. §8 screen → Task 5. §9 invariants → owned across Tasks 2, 3, 4 with V-A/V-B/V-D/V-E on Task 2, V-C on Task 3, V-F on Task 4; none owned twice, none unclaimed. §10 testing → every task's test step plus Task 6. §11 next phase → out of scope by design.

**Placeholders.** Two test bodies in Task 6 Step 1 are elided with `...` and an explicit instruction naming the helpers to import and the file to import them from. That is a deliberate pointer to existing code rather than a "fill in details" — every other step carries the code it needs.

**Type consistency.** `parse_contacts(path, *, uploaded_by, uploaded_at, source_document)` is used with those keywords in Task 4. `contact_db.replace_all/list_all/count/fold/connect/db_path` are used as declared. `store.set_vendor_contacts` returns `int` and is used as the `stored` count. `store.vendor_contacts()` returns a list and is used for `addresses`, `matched` and the payload. `emails_for` returns `list[str] | None`, matching `ShortlistEntry.email: string[] | null` in TypeScript. `save(root, store, *, registry, contacts)` — both keywords defaulted `True`, so existing callers are unchanged.

One thing to watch during execution: `contact_db.db_path` is used in Task 3's mtime test but is an *import* from `bidder_db` — re-export it explicitly (`db_path = bidder_db.db_path` or a plain `from ... import db_path`, which already makes it an attribute of the module).
