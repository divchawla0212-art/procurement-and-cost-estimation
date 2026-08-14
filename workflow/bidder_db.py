"""`<ROOT>/bidders.db`: the bidder registry, in SQLite.

The registry is the one collection in this system that is **reference data**.
It arrives whole from a client's Approved Vendor List export, it is ~1 300 rows
where every other collection is tens, it is read constantly and written almost
never, and it is queried by attributes — who approved them, what they are
registered for. Everything else in `workflow.json` is a handful of records that
only ever make sense together.

So it lives here instead, and the rest of the document does not. That split is
deliberate and is the whole design:

- **`workflow.json` no longer carries a `bidders` key.** The document holds
  exactly the entities the store holds, minus this one collection, which comes
  from beside it. `persistence` hydrates the store from this module on load and
  writes back through it on save, so `WorkflowStore` and every guard on it —
  `delete_bidder` refusing while a shortlist references a vendor, the shortlist
  snapshot read at invitation — are untouched and still work on plain dicts.
- **Reads that only want the registry skip the store entirely.**
  `client_approved_rows` is a query, not a scan of 1 346 objects in Python, and
  the index on the folded product group is what makes narrowing to a discipline
  cheap.

Three tables rather than JSON columns in one. `approved_by`,
`trade_categories` and `represented_manufacturers` are lists that get filtered
*on*, and a list in a text column can only be filtered with `LIKE '%…%'` — the
substring matching this repository has twice recorded as a defect. A child row
per value makes the match an equality test that an index can serve.

The folded key columns (`approver_key`, `product_group_key`) are stored, not
computed at query time, for the same reason: `WHERE product_group_key = ?` uses
the index and `WHERE lower(product_group) = ?` does not. They are written from
one function so the folding rule cannot drift between writer and reader.
"""
import os
import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from datetime import date
from typing import Iterator

from workflow import disciplines
from workflow.models.bidder import Bidder

SCHEMA = """
CREATE TABLE IF NOT EXISTS bidders (
    id                 TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    name_key           TEXT NOT NULL,
    country            TEXT,
    currency           TEXT NOT NULL,
    prequal_status     TEXT NOT NULL,
    prequal_expires_on TEXT,
    on_hold            INTEGER NOT NULL,
    hold_reason        TEXT,
    turnover_band      TEXT,
    performance_rating REAL,
    past_awards        INTEGER NOT NULL,
    notes              TEXT
);

CREATE TABLE IF NOT EXISTS bidder_approvals (
    bidder_id    TEXT NOT NULL REFERENCES bidders(id) ON DELETE CASCADE,
    approver     TEXT NOT NULL,
    approver_key TEXT NOT NULL,
    position     INTEGER NOT NULL,
    PRIMARY KEY (bidder_id, approver)
);

CREATE TABLE IF NOT EXISTS bidder_product_groups (
    bidder_id         TEXT NOT NULL REFERENCES bidders(id) ON DELETE CASCADE,
    product_group     TEXT NOT NULL,
    product_group_key TEXT NOT NULL,
    position          INTEGER NOT NULL,
    PRIMARY KEY (bidder_id, product_group)
);

CREATE TABLE IF NOT EXISTS bidder_manufacturers (
    bidder_id    TEXT NOT NULL REFERENCES bidders(id) ON DELETE CASCADE,
    manufacturer TEXT NOT NULL,
    position     INTEGER NOT NULL,
    PRIMARY KEY (bidder_id, manufacturer)
);

CREATE INDEX IF NOT EXISTS ix_approvals_key
    ON bidder_approvals (approver_key);
CREATE INDEX IF NOT EXISTS ix_product_groups_key
    ON bidder_product_groups (product_group_key);
CREATE INDEX IF NOT EXISTS ix_bidders_name_key
    ON bidders (name_key);
"""


def db_path(root: str) -> str:
    return os.path.join(root, "bidders.db")


# Re-exported rather than reimplemented. The key columns are written with the
# same function the Python matchers use, so a query and an in-memory filter
# cannot disagree about whether a label matches — see `disciplines.fold`.
fold = disciplines.fold


@contextmanager
def connect(root: str) -> Iterator[sqlite3.Connection]:
    """A connection with the schema in place.

    `os.makedirs` because the root may not exist yet on a first run — the same
    reading `persistence.load` gives a missing document: a first run, not an
    error.
    """
    os.makedirs(root, exist_ok=True)
    conn = sqlite3.connect(db_path(root))
    try:
        conn.row_factory = sqlite3.Row
        # Off by default in SQLite, and the cascade in `replace_all` depends on
        # it — without this, deleting a bidder would orphan its child rows.
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        yield conn
    finally:
        conn.close()


def _row_to_bidder(row: sqlite3.Row, children: dict[str, dict[str, list[str]]]) -> Bidder:
    child = children.get(row["id"], {})
    expires = row["prequal_expires_on"]
    return Bidder(
        id=row["id"],
        name=row["name"],
        country=row["country"],
        currency=row["currency"],
        approved_by=child.get("approvals", []),
        trade_categories=child.get("product_groups", []),
        prequal_status=row["prequal_status"],
        prequal_expires_on=date.fromisoformat(expires) if expires else None,
        on_hold=bool(row["on_hold"]),
        hold_reason=row["hold_reason"],
        turnover_band=row["turnover_band"],
        performance_rating=row["performance_rating"],
        past_awards=row["past_awards"],
        represented_manufacturers=child.get("manufacturers", []),
        notes=row["notes"],
    )


def _children(conn: sqlite3.Connection, ids: Sequence[str]) -> dict[str, dict[str, list[str]]]:
    """Every child row for `ids`, in three queries rather than three per bidder.

    Ordered by the stored `position`, so a bidder comes back with its lists in
    the order it went in with. The AVL importer sorts them before it ever gets
    here, so this changes nothing for imported rows — it is for the ones a
    person creates, where reordering a field nobody asked to reorder is the
    kind of quiet edit that makes a store hard to trust.

    The N+1 this avoids is not theoretical: the registry is 1 346 rows, and a
    query per bidder per list would be four thousand round trips to render one
    screen.
    """
    out: dict[str, dict[str, list[str]]] = {}
    if not ids:
        return out
    marks = ",".join("?" * len(ids))
    queries = (
        ("approvals", f"SELECT bidder_id, approver AS v FROM bidder_approvals "
                      f"WHERE bidder_id IN ({marks}) ORDER BY bidder_id, position"),
        ("product_groups", f"SELECT bidder_id, product_group AS v FROM bidder_product_groups "
                           f"WHERE bidder_id IN ({marks}) ORDER BY bidder_id, position"),
        ("manufacturers", f"SELECT bidder_id, manufacturer AS v FROM bidder_manufacturers "
                          f"WHERE bidder_id IN ({marks}) ORDER BY bidder_id, position"),
    )
    for key, sql in queries:
        for row in conn.execute(sql, tuple(ids)):
            out.setdefault(row["bidder_id"], {}).setdefault(key, []).append(row["v"])
    return out


def _hydrate(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[Bidder]:
    children = _children(conn, [r["id"] for r in rows])
    return [_row_to_bidder(r, children) for r in rows]


def replace_all(root: str, bidders: Iterable[Bidder]) -> int:
    """Make the table hold exactly `bidders`, in one transaction.

    Wholesale replacement rather than a diff, and the same invariant
    `persistence.save` has for the document: a bidder removed in memory cannot
    survive on disk. A diff would need change tracking on a model that has
    none, and the table is small enough that rewriting it is cheaper than
    getting that wrong.
    """
    with connect(root) as conn:
        with conn:  # one transaction; rolls back whole on any failure
            conn.execute("DELETE FROM bidders")
            for b in bidders:
                conn.execute(
                    "INSERT INTO bidders (id, name, name_key, country, currency,"
                    " prequal_status, prequal_expires_on, on_hold, hold_reason,"
                    " turnover_band, performance_rating, past_awards, notes)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        b.id, b.name, fold(b.name), b.country, b.currency,
                        b.prequal_status,
                        b.prequal_expires_on.isoformat() if b.prequal_expires_on else None,
                        int(b.on_hold), b.hold_reason, b.turnover_band,
                        b.performance_rating, b.past_awards, b.notes,
                    ),
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO bidder_approvals"
                    " (bidder_id, approver, approver_key, position) VALUES (?,?,?,?)",
                    [(b.id, a, fold(a), i) for i, a in enumerate(b.approved_by)],
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO bidder_product_groups"
                    " (bidder_id, product_group, product_group_key, position)"
                    " VALUES (?,?,?,?)",
                    [(b.id, c, fold(c), i) for i, c in enumerate(b.trade_categories)],
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO bidder_manufacturers"
                    " (bidder_id, manufacturer, position) VALUES (?,?,?)",
                    [(b.id, m, i) for i, m in enumerate(b.represented_manufacturers)],
                )
        return conn.execute("SELECT count(*) FROM bidders").fetchone()[0]


def list_all(root: str) -> list[Bidder]:
    """Every bidder, by name. What `persistence.load` hydrates the store from."""
    with connect(root) as conn:
        rows = conn.execute("SELECT * FROM bidders ORDER BY name_key").fetchall()
        return _hydrate(conn, rows)


def count(root: str) -> int:
    with connect(root) as conn:
        return conn.execute("SELECT count(*) FROM bidders").fetchone()[0]


def approved_by_all(
    root: str, approvers: Sequence[str], product_groups: Sequence[str] | None = None
) -> list[Bidder]:
    """Vendors carrying **every** approval in `approvers`.

    The "available" list: ADNOC's approval says the client will accept them,
    Astra's says we will, and a vendor is only invitable when both hold. AND,
    not OR — an `IN` clause would return everyone with *either*, which is the
    whole registry plus nothing useful.

    Expressed as a group-and-count rather than one join per approver so the
    number of approvers is data, not code shape.
    """
    if not approvers:
        return []
    marks = ",".join("?" * len(approvers))
    sql = [
        "SELECT b.* FROM bidders b",
        f"JOIN bidder_approvals a ON a.bidder_id = b.id AND a.approver_key IN ({marks})",
    ]
    params: list[str] = [fold(a) for a in approvers]
    if product_groups is not None:
        group_marks = ",".join("?" * len(product_groups)) or "NULL"
        sql.append(
            f"JOIN bidder_product_groups g ON g.bidder_id = b.id"
            f" AND g.product_group_key IN ({group_marks})"
        )
        params.extend(fold(g) for g in product_groups)
    # COUNT(DISTINCT) because the product-group join multiplies the approval
    # rows: a vendor registered for four cable groups repeats each approval
    # four times, and a plain count would let one approval satisfy the test.
    sql.append("GROUP BY b.id HAVING count(DISTINCT a.approver_key) = ?")
    params.append(len(approvers))
    sql.append("ORDER BY b.name_key")

    with connect(root) as conn:
        rows = conn.execute(" ".join(sql), tuple(params)).fetchall()
        return _hydrate(conn, rows)


def add_approval(root: str, vendor_ids: Sequence[str], approver: str) -> int:
    """Record `approver` against each bidder that exists, and say how many.

    Additive: a vendor's other approvals are untouched, and running it twice
    changes nothing. Ids with no bidder behind them are skipped rather than
    creating one — this file says who approved whom, not who exists, and
    inventing a nameless vendor from a number in a second export is exactly the
    embellishment `avl_import` refuses to do.
    """
    with connect(root) as conn:
        with conn:
            known = {
                r["id"]
                for r in conn.execute("SELECT id FROM bidders")
            }
            wanted = [v for v in vendor_ids if v in known]
            conn.executemany(
                "INSERT OR IGNORE INTO bidder_approvals"
                " (bidder_id, approver, approver_key, position)"
                # Appended after whatever the vendor already carries, so the
                # client's own approval keeps its place at the front.
                " SELECT ?, ?, ?, coalesce(max(position), -1) + 1"
                " FROM bidder_approvals WHERE bidder_id = ?",
                [(v, approver, fold(approver), v) for v in wanted],
            )
        return len(wanted)


def client_approved_rows(
    root: str, approver: str, product_groups: Sequence[str] | None = None
) -> list[Bidder]:
    """The approver's list, optionally narrowed to a set of product groups.

    The query `client_approved` does in Python, done in SQL — same answer, same
    ordering, without loading the registry to filter it. `product_groups` is
    the *expanded* family: this module knows nothing about disciplines, which
    keeps the vocabulary in one place that has no database in it.

    `DISTINCT` because a vendor registered for four of a discipline's groups is
    still one vendor.
    """
    sql = [
        "SELECT DISTINCT b.* FROM bidders b",
        "JOIN bidder_approvals a ON a.bidder_id = b.id AND a.approver_key = ?",
    ]
    params: list[str] = [fold(approver)]
    if product_groups is not None:
        marks = ",".join("?" * len(product_groups)) or "NULL"
        sql.append(
            f"JOIN bidder_product_groups g ON g.bidder_id = b.id"
            f" AND g.product_group_key IN ({marks})"
        )
        params.extend(fold(g) for g in product_groups)
    sql.append("ORDER BY b.name_key")

    with connect(root) as conn:
        rows = conn.execute(" ".join(sql), tuple(params)).fetchall()
        return _hydrate(conn, rows)


def product_group_names(root: str) -> list[str]:
    """Every distinct product group in the registry, by name.

    The export's vocabulary as the registry actually holds it — which is what a
    discipline's product groups have to be checked against.
    """
    with connect(root) as conn:
        return [
            r["product_group"]
            for r in conn.execute(
                "SELECT DISTINCT product_group FROM bidder_product_groups"
                " ORDER BY product_group"
            )
        ]
