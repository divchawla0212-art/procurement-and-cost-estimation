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
from datetime import datetime, timezone
from typing import Iterator

from procurement.store import layout
from workflow import bidder_db, contact_db
from workflow.models.bid import Bid, BidShortlist, VdrlReceipt
from workflow.models.bidder import Bidder
from workflow.models.clarification import Addendum, ClarificationQuery
from workflow.models.draft_shortlist import DraftShortlistEntry
from workflow.models.project import Item, ItemVendorEntry, Project
from workflow.models.rfq import (
    EnquirySend,
    RfqRecord,
    ShortlistEntry,
    StageTransition,
    TbeTemplate,
    TechnicalPackage,
    VdrlLine,
)
from workflow.models.rfq_document import RfqDocument
from workflow.stages import RETIRED_STAGES
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
        # No `bidders` key, and no `vendor_contacts` key. **Two** collections
        # live in `<ROOT>/bidders.db` — the registry and the vendor contact
        # directory; see `workflow/bidder_db.py` and `workflow/contact_db.py`
        # for why they are different — and `save` writes both there in the same
        # call that writes this document. A key here would be a second copy for
        # the first edit to disagree with.
        #
        # Both are therefore exempt from the rule that every field on
        # `WorkflowStore.__init__` needs a line in this function and in
        # `from_document`. That rule exists because a field without them
        # silently fails to survive a restart, so an exemption is only safe
        # while something else loads and saves it — `persistence.load` does,
        # and a test in `test_vendor_contact_store.py` says the document stays
        # clean.
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
        # An item's own vendor lists, both sources in one flat list — the
        # source is on each record. Only what is stored: whether an entry links
        # to the registry is `vendor_id`, and the approvals behind it are read
        # live, never written here.
        "item_vendor_lists": [
            e.model_dump(mode="json")
            for entries in store._item_vendor_lists.values()
            for e in entries
        ],
        # The vendors a buyer has picked against an item, before any RFQ covers
        # it. Flat, grouped back by `item_id` on the way in. Only what is
        # stored: `source` is where the buyer found them, and the approvals
        # behind a `vendor_id` are read live and never written here.
        "draft_shortlists": [
            e.model_dump(mode="json")
            for entries in store._draft_shortlists.values()
            for e in entries
        ],
        # The documents an RFQ carries, both halves of the enquiry in one flat
        # list — `submitted_by_vendor_id` on each record says which half. Only
        # what is stored: the bytes live under `<ROOT>/rfq-docs/`, addressed by
        # the digest and leaf name on the record, so no path is written here
        # for a moved root to invalidate.
        "rfq_documents": [
            d.model_dump(mode="json")
            for documents in store._rfq_documents.values()
            for d in documents
        ],
        # What actually went out, per RFQ. `to` is stored rather than derived
        # — see `EnquirySend`'s own docstring — so this list is the one place
        # re-uploading the vendor contact sheet cannot rewrite.
        "enquiry_sends": [
            r.model_dump(mode="json")
            for records in store._enquiry_sends.values()
            for r in records
        ],
    }


def _move_off_a_retired_stage(record: dict) -> dict:
    """An RFQ stored at a stage that no longer exists moves to its replacement,
    with one history entry recording the move.

    Two separate things happen to a document written before a stage was
    retired, and only one of them is this function's business:

    - Its **history** names the retired stage, in the entry recording the RFQ's
      own creation and possibly in a transition out of it. Those entries are
      left exactly as they are. History is append-only, and `StageTransition`
      keeps a retired label verbatim precisely so that they can be.
    - Its **stage** may still *be* the retired one, and that is what cannot
      stand: nothing can transition out of a stage `TRANSITIONS` has no key
      for, so the RFQ would be stuck. It moves, and the move is recorded.

    Idempotent because it keys on the stage, not on the history: once the RFQ
    has moved there is no retired stage left to find, and a second load appends
    nothing. Keying on "does the history mention Scoping" would append on every
    load forever, and only a two-run test tells the two apart —
    `test_loading_a_scoping_document_twice_appends_one_entry`.
    """
    retired = record.get("stage")
    replacement = RETIRED_STAGES.get(retired)
    if replacement is None:
        return record

    moved = StageTransition(
        # The literal retired label, not `None` and not an invented member.
        # `None` already means "the RFQ was created here", and this entry is
        # the opposite of that.
        from_stage=retired,
        to_stage=replacement,
        at=datetime.now(timezone.utc),
        by="system",
        reason=f"{retired} was removed from the process.",
    )
    return {
        **record,
        "stage": replacement.value,
        "history": [*record.get("history", []), moved.model_dump(mode="json")],
    }


def _tbe_template(record: dict) -> TbeTemplate:
    record = dict(record)
    legacy = record.pop("criteria", None)
    if legacy and not record.get("items"):
        record["items"] = [{"label": line} for line in legacy]
    return TbeTemplate(**record)


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
    # A `bidders` key means a document written before the registry moved to
    # SQLite. Read it, so an old document still loads with its registry: `load`
    # hands the result to `bidder_db` and the next `save` drops the key. Absent
    # is the normal case now, and `load` fills the registry from the database.
    store._bidders = {b["id"]: Bidder(**b) for b in doc.get("bidders", [])}
    store._rfqs = {
        r["id"]: RfqRecord(**_move_off_a_retired_stage(r)) for r in doc.get("rfqs", [])
    }
    store._packages = {p["rfq_id"]: TechnicalPackage(**p) for p in doc.get("packages", [])}
    # A document written before the checklist existed carries `criteria`, a
    # list of free-text lines. They are read as **non-mandatory** items,
    # because that is exactly what a line of prose was: something the buyer
    # wanted, with no power to block a bid. Read rather than migrated -- the
    # correct reading of an older key, which is why `VERSION` does not move.
    store._tbe = {t["rfq_id"]: _tbe_template(t) for t in doc.get("tbe", [])}
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
    # `.get` with a default, so a document written before per-item vendor lists
    # existed loads as items with none. The correct reading of a missing key,
    # not a migration — which is why `VERSION` does not move.
    for record in doc.get("item_vendor_lists", []):
        entry = ItemVendorEntry(**record)
        store._item_vendor_lists.setdefault(entry.item_id, []).append(entry)
    # Same `.get` default, same reason: a document written before drafts existed
    # loads as items with none, and `VERSION` stays where it is.
    for record in doc.get("draft_shortlists", []):
        pick = DraftShortlistEntry(**record)
        store._draft_shortlists.setdefault(pick.item_id, []).append(pick)
    # I-E: the document holds exactly the `RfqDocument` records whose RFQ still
    # exists. Nothing in this repository deletes an RFQ — deleting a project is
    # refused while it holds one — so the invariant is held at both ends
    # instead of by a cascade: `add_rfq_document` refuses an RFQ that is not
    # there, and a record whose RFQ has gone from the document is dropped here
    # rather than loaded as a row pointing at nothing. Dropping rather than
    # tolerating, because the blob behind such a record is unreachable: no
    # screen can show it and no delete path can reach it, so keeping the row
    # would only make the orphan harder to see.
    for record in doc.get("rfq_documents", []):
        document = RfqDocument(**record)
        if document.rfq_id not in store._rfqs:
            continue
        store._rfq_documents.setdefault(document.rfq_id, []).append(document)
    # Same shape as the loop above, one entity type down: a record whose RFQ
    # has gone is dropped rather than loaded as a row pointing at nothing.
    for record in doc.get("enquiry_sends", []):
        send = EnquirySend(**record)
        if send.rfq_id not in store._rfqs:
            continue
        store._enquiry_sends.setdefault(send.rfq_id, []).append(send)
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
    is indistinguishable from data loss at the point it matters.

    Two sources, one store: the document for everything the workflow owns, and
    `bidders.db` for the registry. The store the caller gets back is the same
    plain-dict object it always was, so nothing downstream knows or cares that
    one collection came from a different file.
    """
    doc = layout.read_json(workflow_path(root), default=None)
    store = WorkflowStore() if doc is None else from_document(doc)
    # A document still carrying a `bidders` key predates the move. Its registry
    # wins so that the first load after the move does not read as an emptied
    # one; the next `save` writes it to the database and drops the key. Once
    # that has happened the key is gone and this branch never runs again.
    if not store._bidders:
        store._bidders = {b.id: b for b in bidder_db.list_all(root)}
    # The database is the *only* source for the contact directory. Unlike
    # `bidders` there is no document key to migrate from and never was, so
    # this is unconditional — a `vendor_contacts` key in a hand-edited
    # document must not be able to introduce a contact the directory does not
    # hold.
    store._vendor_contacts = {
        contact_db.fold(c.vendor_name): c for c in contact_db.list_all(root)
    }
    return store


def save(
    root: str, store: WorkflowStore, *, registry: bool = True, contacts: bool = True
) -> None:
    """Write both halves.

    The registry goes first: it is its own transaction, and a document written
    against a registry that failed to save would be the inconsistency this
    module exists to prevent. `replace_all` is wholesale for the same reason
    `atomic_write_json` is — a bidder removed in memory must not survive on
    disk.

    `registry=False` skips it, and only `locked_update` passes that, having
    compared the collection against the one it loaded. Rewriting 1 346 rows
    costs ~130 ms, which is most of the time an RFQ transition would otherwise
    take — and a transition does not touch the registry. The default is `True`
    so that a direct caller cannot silently drop a registry edit by omission.
    """
    if registry:
        bidder_db.replace_all(root, store._bidders.values())
    if contacts:
        contact_db.replace_all(root, store._vendor_contacts.values())
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
        # A shallow copy is enough to notice every registry edit the store can
        # make: `create_bidder` and `delete_bidder` change the keys, and
        # `update_bidder` rebuilds through the model and rebinds the value
        # rather than mutating it in place (CLAUDE.md: updates rebuild through
        # the model, which is also what makes them validate). An in-place
        # mutation of a stored `Bidder` would defeat this — there is none, and
        # this comment is why there must not be one.
        registry_before = dict(store._bidders)
        # Same shallow copy, same reasoning: `set_vendor_contacts` rebuilds the
        # dict rather than mutating stored models in place, so a rebind is the
        # only way the directory can change and `!=` sees all of them.
        contacts_before = dict(store._vendor_contacts)
        yield store
        save(
            root, store,
            registry=store._bidders != registry_before,
            contacts=store._vendor_contacts != contacts_before,
        )
