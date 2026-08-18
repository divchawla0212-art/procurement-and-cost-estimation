"""The vendor contact directory in `<ROOT>/bidders.db`.

Same file as the registry, its own tables. The four reasons CLAUDE.md gives for
the registry living in SQLite apply here verbatim: it is reference data, it
arrives whole from an export, it is replaced wholesale, and it is queried by
key rather than read whole. It is the same *kind* of thing — vendor reference
data — which is why it shares the file.

**Its own tables, and not a column on `bidders`.** A column would make the
directory a property of the registry, which would mean an AVL reload destroys
it and a vendor absent from the registry could hold no address. Neither is
wanted: the sheet this reads names companies the registry may not hold, and
the registry is refreshed from a client export on its own schedule.

`name_key` is the primary key, so two rows for one folded name are impossible
rather than guarded against — no query has to decide which one wins.
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
# function every Python comparison uses. `bidder_db` re-exports it for the same
# reason, and the failure when two copies drift is silent: a vendor invisible
# to the very lookup their own row satisfies. See `disciplines.fold`.
fold = disciplines.fold

# Re-exported rather than recomputed, so the file's location has one
# definition. The directory and the registry share `bidders.db`.
db_path = db_path


@contextmanager
def connect(root: str) -> Iterator[sqlite3.Connection]:
    """A connection with this module's tables in place.

    Deliberately *not* shared with `bidder_db.connect`: each module runs its
    own `CREATE TABLE IF NOT EXISTS`, so neither one's schema becomes a
    dependency of the other's. `db_path` is shared, because the file's location
    is one fact.

    `os.makedirs` because the root may not exist on a first run — the same
    reading `persistence.load` gives a missing document.
    """
    os.makedirs(root, exist_ok=True)
    conn = sqlite3.connect(db_path(root))
    try:
        conn.row_factory = sqlite3.Row
        # Off by default in SQLite, and the cascade in `replace_all` depends on
        # it — without this, replacing the directory would orphan every address.
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        yield conn
    finally:
        conn.close()


def replace_all(root: str, contacts: Iterable[VendorContact]) -> int:
    """Make the directory hold exactly `contacts`, in one transaction.

    Wholesale, the same invariant `bidder_db.replace_all` and
    `persistence.save` both have: a vendor removed in memory must not survive
    on disk. An upload is a document, and a vendor dropped from the sheet is a
    vendor the sheet no longer names.

    **Deletes from `vendor_contacts` only** — never `DROP`, and never from
    `bidders`, which shares this file. That is the invariant
    `test_replacing_the_directory_leaves_the_registry_alone` holds.
    """
    with connect(root) as conn:
        with conn:  # one transaction; rolls back whole on any failure
            conn.execute("DELETE FROM vendor_contacts")
            for contact in contacts:
                key = fold(contact.vendor_name)
                conn.execute(
                    "INSERT OR REPLACE INTO vendor_contacts"
                    " (name_key, vendor_name, source_document, uploaded_by,"
                    "  uploaded_at) VALUES (?,?,?,?,?)",
                    (
                        key, contact.vendor_name, contact.source_document,
                        contact.uploaded_by, contact.uploaded_at.isoformat(),
                    ),
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
            "SELECT name_key, email FROM vendor_contact_emails"
            " ORDER BY name_key, position"
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
