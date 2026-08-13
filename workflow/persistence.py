"""`<ROOT>/workflow.json`: durability for the RFQ workflow.

Deliberately the same shape as `api/auth/store.py`'s `auth.json` — one
document, one lock, one atomic write — and for the same reason. Projects,
items, RFQs and every artifact reference each other by id, so a write that
landed one collection but not another would leave a shortlist pointing at an
RFQ that does not exist. One document makes that unrepresentable.

The invariant: **the document holds exactly the entities the store holds.**
`save` replaces the document wholesale rather than merging into it, so an
entity removed in memory cannot survive on disk.

This is not the database of the persistence phase; it is the infrastructure-free
equivalent, and it uses the same `layout.atomic_write_json` the snapshot store
does. Swapping it for a real database is a change to this module only —
`WorkflowStore` stays a pure in-memory model and knows nothing about disk.
"""
import os
import threading
from contextlib import contextmanager
from typing import Iterator

from procurement.store import layout
from workflow.models.bid import Bid, BidShortlist, VdrlReceipt
from workflow.models.bidder import Bidder
from workflow.models.clarification import Addendum, ClarificationQuery
from workflow.models.project import Item, Project
from workflow.models.rfq import (
    RfqRecord,
    ShortlistEntry,
    TbeTemplate,
    TechnicalPackage,
    VdrlLine,
)
from workflow.store import WorkflowStore

# One process, one lock — the same reasoning as `api/auth/store._LOCK`, and the
# same caveat: the shipped deployment is a single uvicorn worker. Multiple
# workers or containers over a shared volume would need a cross-process lock.
_LOCK = threading.Lock()

VERSION = 1


def workflow_path(root: str) -> str:
    return os.path.join(root, "workflow.json")


def to_document(store: WorkflowStore) -> dict:
    return {
        "version": VERSION,
        "projects": [p.model_dump(mode="json") for p in store._projects.values()],
        "items": [i.model_dump(mode="json") for i in store._items.values()],
        # Only what is stored. `effective_prequal` is computed from
        # `prequal_expires_on` and the date it is asked on, so writing it here
        # would put a value in the document that is wrong the next morning.
        "bidders": [b.model_dump(mode="json") for b in store._bidders.values()],
        "rfqs": [r.model_dump(mode="json") for r in store._rfqs.values()],
        "packages": [p.model_dump(mode="json") for p in store._packages.values()],
        "shortlists": [
            e.model_dump(mode="json")
            for entries in store._shortlists.values()
            for e in entries
        ],
        "shortlist_approvals": dict(store._shortlist_approvals),
        "tbe": [t.model_dump(mode="json") for t in store._tbe.values()],
        "vdrl": [
            line.model_dump(mode="json")
            for lines in store._vdrl.values()
            for line in lines
        ],
        "bids": [b.model_dump(mode="json") for b in store._bids.values()],
        "receipts": [
            r.model_dump(mode="json")
            for receipts in store._receipts.values()
            for r in receipts
        ],
        "bid_shortlists": [s.model_dump(mode="json") for s in store._bid_shortlists.values()],
        # Only what is stored. A query's state and an addendum's draft flag are
        # computed from the timestamps beside them, so writing them here would
        # put a second copy in the document for the first update to disagree
        # with — the same reasoning as `effective_prequal` above.
        "queries": [
            q.model_dump(mode="json")
            for queries in store._queries.values()
            for q in queries
        ],
        "addenda": [
            a.model_dump(mode="json")
            for addenda in store._addenda.values()
            for a in addenda
        ],
    }


def from_document(doc: dict) -> WorkflowStore:
    """Rebuild a store from a document.

    Collections keyed by owner are rebuilt by grouping on the owning id that
    each record already carries, rather than being stored pre-grouped. One
    flat list per entity type keeps the document readable and makes the
    grouping key impossible to disagree with the record it groups.
    """
    store = WorkflowStore()
    store._projects = {p["id"]: Project(**p) for p in doc.get("projects", [])}
    store._items = {i["id"]: Item(**i) for i in doc.get("items", [])}
    # `.get` with a default, so a document written before the registry existed
    # loads as an empty one. That is the correct reading of a missing key, not
    # a migration, which is why `VERSION` does not move.
    store._bidders = {b["id"]: Bidder(**b) for b in doc.get("bidders", [])}
    store._rfqs = {r["id"]: RfqRecord(**r) for r in doc.get("rfqs", [])}
    store._packages = {p["rfq_id"]: TechnicalPackage(**p) for p in doc.get("packages", [])}
    store._tbe = {t["rfq_id"]: TbeTemplate(**t) for t in doc.get("tbe", [])}
    store._bids = {b["id"]: Bid(**b) for b in doc.get("bids", [])}
    store._bid_shortlists = {
        s["rfq_id"]: BidShortlist(**s) for s in doc.get("bid_shortlists", [])
    }
    store._shortlist_approvals = dict(doc.get("shortlist_approvals", {}))

    for record in doc.get("shortlists", []):
        entry = ShortlistEntry(**record)
        store._shortlists.setdefault(entry.rfq_id, []).append(entry)
    for record in doc.get("vdrl", []):
        line = VdrlLine(**record)
        store._vdrl.setdefault(line.rfq_id, []).append(line)
    for record in doc.get("receipts", []):
        receipt = VdrlReceipt(**record)
        store._receipts.setdefault(receipt.bid_id, []).append(receipt)
    # `.get` with a default, so a document written before clarifications existed
    # loads as an RFQ with no queries and no addenda. The correct reading of a
    # missing key, not a migration — which is why `VERSION` does not move.
    for record in doc.get("queries", []):
        query = ClarificationQuery(**record)
        store._queries.setdefault(query.rfq_id, []).append(query)
    for record in doc.get("addenda", []):
        addendum = Addendum(**record)
        store._addenda.setdefault(addendum.rfq_id, []).append(addendum)

    return store


def load(root: str) -> WorkflowStore:
    """Read the store from disk. A missing document is a first run, not an
    error; a corrupt one raises, because reading a truncated file as "no RFQs"
    is indistinguishable from data loss at the point it matters."""
    doc = layout.read_json(workflow_path(root), default=None)
    if doc is None:
        return WorkflowStore()
    return from_document(doc)


def save(root: str, store: WorkflowStore) -> None:
    layout.atomic_write_json(workflow_path(root), to_document(store))


@contextmanager
def locked_update(root: str) -> Iterator[WorkflowStore]:
    """Read, mutate, write — as one unit, with no other writer interleaving.

    Every decision that gates a write belongs *inside* this block, not in the
    caller. A check made outside the lock and a write made inside it are two
    critical sections, not one, and the workflow is full of such checks: a gate
    reads the technical package, the shortlist and the TBE template before
    allowing a transition. Two concurrent transitions that each read "gate
    open" outside the lock would both write.

    The write happens only on a clean exit, so a caller that raises — a closed
    gate raises `ValueError` — leaves the document exactly as it was.
    """
    with _LOCK:
        store = load(root)
        yield store
        save(root, store)
