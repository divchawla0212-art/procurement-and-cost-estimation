from collections.abc import Iterable
from datetime import date, datetime, timezone
from typing import NamedTuple

from workflow import clarifications
from workflow.bidders import evaluate
from workflow.disciplines import fold
from workflow.models.bid import Bid, BidShortlist, ReceiptState, VdrlReceipt
from workflow.models.clarification import (
    Addendum,
    ClarificationQuery,
    QueryCategory,
)
from workflow.models.bidder import Bidder, PrequalStatus
from workflow.models.draft_shortlist import DraftShortlistEntry
from workflow.models.project import (
    CURATED_SOURCES,
    UPLOADED_SOURCES,
    Item,
    ItemVendorEntry,
    Project,
    VendorListSource,
)
from workflow.models.rfq import (
    Attachment,
    RfqRecord,
    ShortlistEntry,
    StageTransition,
    TbeTemplate,
    TechnicalPackage,
    VdrlLine,
)
from workflow.models.rfq_document import RfqDocument
from workflow.models.vendor_contact import VendorContact
from workflow.gates import check_gate
from workflow.stages import Stage, is_allowed


# Fields no update may write. `id` is identity. `project_id` is parentage, and
# moving an item between projects would change which project's RFQs are allowed
# to cover it without either project's RFQ records changing — so the honest
# operation is a delete and a re-add. Enforced here as well as by the route's
# request model, so a caller that bypasses the route cannot do it either.
_PROJECT_IMMUTABLE = frozenset({"id"})
_ITEM_IMMUTABLE = frozenset({"id", "project_id"})
_BIDDER_IMMUTABLE = frozenset({"id"})
# Identity, parentage, the number already quoted on a document, and the two
# fields that record issuance. An update that could set `issued_at` would let a
# draft claim it went to bidders without the package ever moving.
_ADDENDUM_IMMUTABLE = frozenset({"id", "rfq_id", "number", "issued_at", "issued_by"})


class BlockedBidder(ValueError):
    """A registry bidder whose own record refuses the invitation until somebody
    records a reason.

    A distinct type for the same reason `IncompleteShortlistEntry` is one: a
    caller that has to tell this refusal apart from every other `ValueError`
    `add_shortlist_entry` can raise would otherwise have to match on the
    wording, and the wording is the part most likely to improve.

    It stays a `ValueError` subclass so the routes' existing 409 mapping is
    unchanged — this widens what a caller *can* distinguish, not what the
    refusal is.
    """


class SkippedPick(NamedTuple):
    """One draft pick adoption did not invite, and the sentence that says why.

    The reason is the refusal's own text, never a summary of it: it is what a
    buyer needs in order to act, and it is already written in the one place
    that knows the rule.
    """

    vendor_name: str
    reason: str


class DraftAdoption(NamedTuple):
    """What `adopt_draft_shortlist` did.

    Not a bare count. A count says how many arrived and is silent about the
    ones that did not, and this repository's rule is that a refusal is never
    silent — `CLAUDE.md`: a gate never returns a bare `False`. A buyer who
    picked a suspended vendor and then raised the RFQ would otherwise get a
    shortlist quietly one row short, with nothing on any screen saying so.
    """

    added: int
    skipped: list[SkippedPick]


class IncompleteShortlistEntry(ValueError):
    """A shortlist add that names neither a registry bidder nor a complete
    free-text vendor.

    A distinct type rather than a distinguishing message, because the route has
    to map it to 422 while every other refusal here is a 409, and a route that
    told them apart by matching on wording would break the next time the
    wording improved.
    """


def _with_id(supplied: str | None) -> dict:
    """Let a caller pin an id, or leave the model's factory to mint one.

    The one caller that pins them is `workflow.seed_demo`: a demo that is
    rehearsed and reseeded needs the same RFQ to keep the same identity across
    builds, and removal addresses a shortlist entry by id, so an unstable one
    would make "remove this row" hit a different row each time. A supplied id
    is validated exactly like a generated one and is immutable afterwards, so
    this widens who may choose an id, not what an id is.
    """
    return {"id": supplied} if supplied is not None else {}


def _reject_immutable(changes: dict, immutable: frozenset[str]) -> None:
    blocked = immutable & set(changes)
    if blocked:
        raise ValueError(f"These fields cannot be changed: {', '.join(sorted(blocked))}")


class WorkflowStore:
    """In-memory store for workflow entities.

    Durability lives in `workflow/persistence.py`, which is the one module
    allowed to read and write the dicts below directly — it serializes the
    whole store to `<ROOT>/workflow.json` and rebuilds it. Keeping that there
    rather than here leaves this class a pure model with no notion of disk, so
    replacing the file with a database is a change to that module alone. Any
    field added here needs a matching line in both `to_document` and
    `from_document`, or it silently fails to survive a restart.
    """

    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}
        self._items: dict[str, Item] = {}
        self._bidders: dict[str, Bidder] = {}
        self._rfqs: dict[str, RfqRecord] = {}
        self._packages: dict[str, TechnicalPackage] = {}
        self._shortlists: dict[str, list[ShortlistEntry]] = {}
        self._shortlist_approvals: dict[str, str] = {}
        self._tbe: dict[str, TbeTemplate] = {}
        self._vdrl: dict[str, list[VdrlLine]] = {}
        self._bids: dict[str, Bid] = {}
        self._receipts: dict[str, list[VdrlReceipt]] = {}
        self._bid_shortlists: dict[str, BidShortlist] = {}
        self._queries: dict[str, list[ClarificationQuery]] = {}
        self._addenda: dict[str, list[Addendum]] = {}
        # Keyed by item id, holding both sources' entries in one list — the
        # source is on each record, so grouping by it here would be a second
        # place for the key to disagree with what it groups.
        self._item_vendor_lists: dict[str, list[ItemVendorEntry]] = {}
        # Keyed by item id. The grouping key is not stored on the way out —
        # `persistence` flattens this to one array and regroups on the way in,
        # the same shape as `_item_vendor_lists` above.
        self._draft_shortlists: dict[str, list[DraftShortlistEntry]] = {}
        # Keyed by RFQ id, holding both halves of the enquiry: the documents
        # the contractor issued and the ones bidders return. Which is which is
        # `submitted_by_vendor_id` on each record, so grouping by it here would
        # be a second place for the key to disagree with what it groups.
        self._rfq_documents: dict[str, list[RfqDocument]] = {}
        # Keyed by `fold(vendor_name)`. **The second field with no line in
        # `to_document` / `from_document`, after `_bidders`, and for the same
        # reason** — it lives in `bidders.db`, hydrated by `persistence.load`
        # and written by `contact_db.replace_all`. A `vendor_contacts` key in
        # the document would be a second copy for the first edit to disagree
        # with, which is the argument that moved the registry out.
        #
        # No `vendor_id` on any of these, deliberately: the sheet they come
        # from carries no vendor number, so a registry link could only be made
        # by comparing names.
        self._vendor_contacts: dict[str, VendorContact] = {}

    # -- projects ---------------------------------------------------------

    def create_project(
        self,
        name: str,
        code: str,
        client: str,
        location: str,
        live_period_start: date,
        live_period_end: date,
        currency: str = "AED",
        project_id: str | None = None,
    ) -> Project:
        project = Project(
            **_with_id(project_id),
            name=name,
            code=code,
            client=client,
            location=location,
            live_period_start=live_period_start,
            live_period_end=live_period_end,
            currency=currency,
        )
        self._projects[project.id] = project
        return project

    def get_project(self, project_id: str) -> Project | None:
        return self._projects.get(project_id)

    def list_projects(self) -> list[Project]:
        return list(self._projects.values())

    def update_project(self, project_id: str, changes: dict) -> Project:
        """A partial update: a field absent from `changes` is left alone, never
        cleared. That is what makes the route's PATCH partial.

        Rebuilt through the model rather than `model_copy(update=...)`, because
        `model_copy` skips validation outright — it would happily store a
        `status` no `ProjectStatus` allows.
        """
        project = self._projects.get(project_id)
        if project is None:
            raise KeyError(f"Unknown project: {project_id}")
        _reject_immutable(changes, _PROJECT_IMMUTABLE)
        updated = Project(**{**project.model_dump(), **changes})
        self._projects[project_id] = updated
        return updated

    # -- items ------------------------------------------------------------

    def create_item(
        self,
        project_id: str,
        item_type: str,
        description: str,
        qty: float,
        uom: str,
        discipline: str,
        estimated_value_aed: int,
        required_on_site: date | None = None,
        is_long_lead: bool = False,
        item_id: str | None = None,
    ) -> Item:
        if project_id not in self._projects:
            raise KeyError(f"Unknown project: {project_id}")
        item = Item(
            **_with_id(item_id),
            project_id=project_id,
            item_type=item_type,
            description=description,
            qty=qty,
            uom=uom,
            discipline=discipline,
            estimated_value_aed=estimated_value_aed,
            required_on_site=required_on_site,
            is_long_lead=is_long_lead,
        )
        self._items[item.id] = item
        return item

    def get_item(self, item_id: str) -> Item | None:
        return self._items.get(item_id)

    def items_for_project(self, project_id: str) -> list[Item]:
        return [i for i in self._items.values() if i.project_id == project_id]

    def update_item(self, item_id: str, changes: dict) -> Item:
        """Partial, and validating, for the same two reasons as
        `update_project`. `project_id` is refused: see `_ITEM_IMMUTABLE`."""
        item = self._items.get(item_id)
        if item is None:
            raise KeyError(f"Unknown item: {item_id}")
        _reject_immutable(changes, _ITEM_IMMUTABLE)
        updated = Item(**{**item.model_dump(), **changes})
        self._items[item_id] = updated
        return updated

    def delete_item(self, item_id: str) -> None:
        """Refused while any RFQ covers the item.

        An RFQ's `item_ids` is what defines its scope, so removing a member
        silently would change what vendors were invited to bid on after the
        fact. The reason names the RFQs, because a refusal that does not say
        what would unblock it leaves the reader nothing to act on.
        """
        if item_id not in self._items:
            raise KeyError(f"Unknown item: {item_id}")
        covering = sorted(r.reference for r in self._rfqs.values() if item_id in r.item_ids)
        if covering:
            raise ValueError(
                f"This item cannot be deleted: it is covered by "
                f"{', '.join(covering)}. Amend or retender first."
            )
        del self._items[item_id]
        # In the same call, for the reason `delete_project` states: `save`
        # replaces the document wholesale, so an entry left here is an entry
        # pointing at an item that is gone — and it survives the restart.
        self._item_vendor_lists.pop(item_id, None)
        # Same rule, same call: a draft pointing at an item that is gone is an
        # orphan, and it survives the restart.
        self._draft_shortlists.pop(item_id, None)

    # -- the draft shortlist an item owns ---------------------------------

    @staticmethod
    def _draft_key(entry: DraftShortlistEntry) -> tuple[str, str]:
        """What makes two picks the same vendor.

        The registry id where there is one, so a company renamed between two
        picks is still one row; the name where there is not, because a curated
        entry has no id and the name is the only key there is.

        The two spaces are kept apart by the tag rather than merged: a registry
        row and a hand-typed one sharing a trading name are **two** rows, and
        collapsing them would attach a real company's approvals to a string
        somebody typed. Exact match, never folded — case-insensitive matching
        of company names is the defect this repository has recorded twice, and
        it does not become safe because the scope is one item.
        """
        if entry.vendor_id:
            return ("id", entry.vendor_id)
        return ("name", entry.vendor_name)

    def add_draft_shortlist_entry(
        self, item_id: str, entry: DraftShortlistEntry
    ) -> DraftShortlistEntry:
        """Pick a vendor against this item. Adding one twice is a no-op.

        The duplicate check is **here**, not in the route, and that is the whole
        reason this method exists rather than a bare append. A check made by the
        caller and a write made by the store are two critical sections: two
        concurrent picks of the same vendor would each read "not there yet" and
        both write. The route runs this inside `persistence.locked_update`, so
        the read and the write are one. Fifth instance of that rule in this
        repository — `api/auth/store.py::grant` is where it is stated.

        The existing row is returned rather than the rejected one, so a caller
        that stores the returned id addresses a row that exists.
        """
        if item_id not in self._items:
            raise KeyError(f"Unknown item: {item_id}")
        draft = self._draft_shortlists.setdefault(item_id, [])
        key = self._draft_key(entry)
        for existing in draft:
            if self._draft_key(existing) == key:
                return existing
        draft.append(entry)
        return entry

    def remove_draft_shortlist_entry(self, item_id: str, entry_id: str) -> None:
        """Unpick a vendor, addressed by **id** — never by name or position.

        Two suppliers can share a trading name, and the list re-sorts under the
        pool's search, so both of the other two addresses are wrong. The same
        rule `remove_shortlist_entry` and `remove_item_vendor_entry` keep.

        An id belonging to another item's draft is not found here: drafts are
        per item, and resolving it globally would let one item's screen empty
        another's basket.
        """
        draft = self._draft_shortlists.get(item_id, [])
        kept = [e for e in draft if e.id != entry_id]
        if len(kept) == len(draft):
            raise KeyError(f"Unknown draft shortlist entry: {entry_id}")
        self._draft_shortlists[item_id] = kept

    def draft_shortlist(self, item_id: str) -> list[DraftShortlistEntry]:
        """This item's picks, in the order they were made. Absent reads as
        empty — the screen asks before the buyer has picked anybody."""
        return list(self._draft_shortlists.get(item_id, []))

    def clear_draft_shortlist(self, item_id: str) -> None:
        self._draft_shortlists.pop(item_id, None)

    # -- an item's own vendor lists ---------------------------------------

    def set_item_vendor_list(
        self,
        item_id: str,
        source: VendorListSource,
        entries: Iterable[ItemVendorEntry],
    ) -> list[ItemVendorEntry]:
        """Replace this item's entries **for one source**, leaving the other's.

        Wholesale within the source, for the same reason `persistence.save`
        replaces the document: a second upload is a correction, and appending
        would leave the previous export's vendors present with nothing to mark
        them stale — a vendor dropped from a revised list would be
        indistinguishable from one still on it.

        Nothing here touches the registry. These uploads are genuine Approved
        Vendor List exports, so the objection is not that the evidence is weak;
        it is that a write attached to one item must not decide what a company
        is approved for across every project. `approved_by` moves only through
        `python -m workflow.avl_db`.
        """
        if item_id not in self._items:
            raise KeyError(f"Unknown item: {item_id}")
        if source not in UPLOADED_SOURCES:
            # Replacing a curated list wholesale would throw away work somebody
            # did a vendor at a time, with nothing to undo it.
            raise ValueError(
                f"{source} is built one vendor at a time — add and remove its "
                "vendors individually rather than replacing the whole list."
            )
        kept = [e for e in self._item_vendor_lists.get(item_id, []) if e.source != source]
        self._item_vendor_lists[item_id] = kept + list(entries)
        return self.item_vendor_list(item_id, source)

    def add_item_vendor_entry(
        self, item_id: str, entry: ItemVendorEntry
    ) -> ItemVendorEntry:
        """Append one vendor, for the curated sources only.

        `Client` and `Astra` are refused because they are documents: appending
        to one would leave it no longer matching the export it came from, so
        the screen would show a list that no re-upload reproduces.
        """
        if item_id not in self._items:
            raise KeyError(f"Unknown item: {item_id}")
        if entry.source not in CURATED_SOURCES:
            raise ValueError(
                f"{entry.source} is an uploaded list — re-upload the corrected "
                "export rather than adding vendors to it one at a time."
            )
        self._item_vendor_lists.setdefault(item_id, []).append(entry)
        return entry

    def remove_item_vendor_entry(self, item_id: str, entry_id: str) -> None:
        """Remove one vendor **by id**, for the curated sources only.

        By id and never by name, because two suppliers can share a trading
        name — the same rule `remove_shortlist_entry` keeps.
        """
        entries = self._item_vendor_lists.get(item_id, [])
        found = next((e for e in entries if e.id == entry_id), None)
        if found is None:
            raise KeyError(f"Unknown vendor list entry: {entry_id}")
        if found.source not in CURATED_SOURCES:
            raise ValueError(
                f"{found.source} is an uploaded list — re-upload the corrected "
                "export rather than removing vendors from it one at a time."
            )
        self._item_vendor_lists[item_id] = [e for e in entries if e.id != entry_id]

    def item_vendor_list(
        self, item_id: str, source: VendorListSource | None = None
    ) -> list[ItemVendorEntry]:
        """This item's entries, optionally for one source only."""
        entries = self._item_vendor_lists.get(item_id, [])
        return [e for e in entries if source is None or e.source == source]

    def delete_project(self, project_id: str) -> None:
        """Refused while the project holds any RFQ; otherwise the project and
        every one of its items go together, in this one call.

        The cascade is not a convenience. `persistence.save` replaces the
        document wholesale, so an item left behind here is an item pointing at
        a project that no longer exists — and it survives the restart.
        """
        if project_id not in self._projects:
            raise KeyError(f"Unknown project: {project_id}")
        held = sorted(r.reference for r in self._rfqs.values() if r.project_id == project_id)
        if held:
            raise ValueError(
                f"This project cannot be deleted: it holds "
                f"{', '.join(held)}. Delete or retender first."
            )
        # Materialised before the first deletion: mutating a dict while
        # iterating its values raises.
        doomed = [i.id for i in self._items.values() if i.project_id == project_id]
        for item_id in doomed:
            del self._items[item_id]
            # Reaches through the item cascade: an item's vendor lists go with
            # the item, whichever door deleted it.
            self._item_vendor_lists.pop(item_id, None)
            self._draft_shortlists.pop(item_id, None)
        del self._projects[project_id]

    def validate_against_live_period(self, project_id: str, when: date) -> str | None:
        """Returns None when `when` is in range, otherwise the reason it is not.
        A reason string rather than a bare False, so a caller can show the user
        something actionable."""
        project = self._projects.get(project_id)
        if project is None:
            raise KeyError(f"Unknown project: {project_id}")
        if when < project.live_period_start or when > project.live_period_end:
            return (
                f"{when.isoformat()} falls outside the project live period "
                f"({project.live_period_start.isoformat()} to "
                f"{project.live_period_end.isoformat()})"
            )
        return None

    # -- vendor contacts ---------------------------------------------------
    #
    # Where an enquiry goes, keyed by folded vendor name. Organisation-wide,
    # like the registry beside it and for the same reason — an address is a
    # fact about a company, not about one RFQ. There is **no registry link**:
    # the sheet these come from carries no vendor number, so a `vendor_id`
    # here could only come from comparing names, which this repository has
    # recorded as a shipped defect twice.

    def set_vendor_contacts(self, contacts: Iterable[VendorContact]) -> int:
        """Replace the directory wholesale, returning how many it now holds.

        Wholesale because an upload is a document: a vendor dropped from the
        sheet must not survive in the directory. Rebuilds the dict rather than
        mutating it, which is what lets `locked_update` notice the change by
        comparing against a shallow copy taken at load — the same mechanism,
        and the same requirement, as the registry.
        """
        self._vendor_contacts = {fold(c.vendor_name): c for c in contacts}
        return len(self._vendor_contacts)

    def vendor_contacts(self) -> list[VendorContact]:
        return sorted(self._vendor_contacts.values(), key=lambda c: fold(c.vendor_name))

    def emails_for(self, vendor_name: str) -> list[str] | None:
        """Where to send this vendor's enquiry, or `None` if nothing is held.

        Keyed on the **name**, folded — there is no `vendor_id` in the
        directory, so a hand-typed shortlist row resolves exactly as a
        registry-linked one does. That is the upside of name-keying: a vendor
        on nobody's register still has an address.

        Folding is `disciplines.fold` and nothing more, so `L.L.C` against
        `LLC` is a miss. Widening it to strip punctuation is how an enquiry
        reaches the wrong company, and the failure would be invisible.

        Two states and never three: `[]` cannot occur, because a contact with
        no address is refused at parse.

        A copy, so a caller cannot edit the directory through the answer.
        """
        contact = self._vendor_contacts.get(fold(vendor_name))
        return list(contact.emails) if contact else None

    # -- bidders ----------------------------------------------------------
    #
    # The registry is organisation-wide, not per project: a prequalification is
    # a fact about a company, and holding it per project would mean re-entering
    # and re-approving the same company for every one of them.

    def create_bidder(
        self,
        name: str,
        country: str | None = None,
        currency: str = "AED",
        approved_by: list[str] | None = None,
        trade_categories: list[str] | None = None,
        prequal_status: PrequalStatus = "Under review",
        prequal_expires_on: date | None = None,
        on_hold: bool = False,
        hold_reason: str | None = None,
        turnover_band: str | None = None,
        performance_rating: float | None = None,
        past_awards: int = 0,
        represented_manufacturers: list[str] | None = None,
        notes: str | None = None,
        bidder_id: str | None = None,
    ) -> Bidder:
        bidder = Bidder(
            **_with_id(bidder_id),
            name=name,
            country=country,
            currency=currency,
            approved_by=list(approved_by or []),
            trade_categories=list(trade_categories or []),
            prequal_status=prequal_status,
            prequal_expires_on=prequal_expires_on,
            on_hold=on_hold,
            hold_reason=hold_reason,
            turnover_band=turnover_band,
            performance_rating=performance_rating,
            past_awards=past_awards,
            represented_manufacturers=list(represented_manufacturers or []),
            notes=notes,
        )
        self._bidders[bidder.id] = bidder
        return bidder

    def get_bidder(self, bidder_id: str) -> Bidder | None:
        return self._bidders.get(bidder_id)

    def list_bidders(self) -> list[Bidder]:
        return list(self._bidders.values())

    def update_bidder(self, bidder_id: str, changes: dict) -> Bidder:
        """Partial, and validating, for the same two reasons as
        `update_project`: an absent field is left alone rather than cleared,
        and rebuilding through the model rather than `model_copy(update=...)`
        is what stops a `prequal_status` no `PrequalStatus` allows reaching
        the store."""
        bidder = self._bidders.get(bidder_id)
        if bidder is None:
            raise KeyError(f"Unknown bidder: {bidder_id}")
        _reject_immutable(changes, _BIDDER_IMMUTABLE)
        updated = Bidder(**{**bidder.model_dump(), **changes})
        self._bidders[bidder_id] = updated
        return updated

    def rfqs_inviting(self, bidder_id: str) -> list[str]:
        """The references of every RFQ whose shortlist names this bidder.

        By `vendor_id`, never by name: a name is user-supplied text and two
        companies can share one, so matching on it would both hold the wrong
        bidder and release the right one.
        """
        references = {
            rfq.reference
            for rfq_id, entries in self._shortlists.items()
            for entry in entries
            if entry.vendor_id == bidder_id
            for rfq in [self._rfqs.get(rfq_id)]
            if rfq is not None
        }
        return sorted(references)

    def delete_bidder(self, bidder_id: str) -> None:
        """Refused while any shortlist references the bidder.

        The third place this repository has needed the rule, and the reason is
        the same as for `delete_item` and `delete_project`:
        `persistence.save` replaces the document wholesale, so an entry left
        pointing at a deleted bidder is not merely wrong in memory — it
        survives the restart as a dangling reference. The refusal names the
        RFQs, because one that does not say what would unblock it leaves the
        reader nothing to act on.
        """
        if bidder_id not in self._bidders:
            raise KeyError(f"Unknown bidder: {bidder_id}")
        inviting = self.rfqs_inviting(bidder_id)
        if inviting:
            raise ValueError(
                f"This bidder cannot be deleted: they are shortlisted on "
                f"{', '.join(inviting)}. Remove them from those shortlists first."
            )
        del self._bidders[bidder_id]

    # -- rfqs -------------------------------------------------------------

    def create_rfq(
        self,
        project_id: str,
        item_ids: list[str],
        reference: str,
        package: str,
        discipline: str,
        value_estimate_aed: int,
        rfq_id: str | None = None,
    ) -> RfqRecord:
        if project_id not in self._projects:
            raise KeyError(f"Unknown project: {project_id}")
        for item_id in item_ids:
            item = self._items.get(item_id)
            if item is None:
                raise KeyError(f"Unknown item: {item_id}")
            if item.project_id != project_id:
                raise ValueError(f"Item {item_id} does not belong to project {project_id}")

        rfq = RfqRecord(
            **_with_id(rfq_id),
            reference=reference,
            project_id=project_id,
            item_ids=list(item_ids),
            package=package,
            discipline=discipline,
            value_estimate_aed=value_estimate_aed,
            history=[
                StageTransition(
                    from_stage=None,
                    to_stage=Stage.SHORTLISTING,
                    at=datetime.now(timezone.utc),
                    by="system",
                    reason="RFQ created",
                )
            ],
        )
        self._rfqs[rfq.id] = rfq
        return rfq

    def get_rfq(self, rfq_id: str) -> RfqRecord | None:
        return self._rfqs.get(rfq_id)

    def list_rfqs(self, project_id: str | None = None) -> list[RfqRecord]:
        rfqs = list(self._rfqs.values())
        if project_id is None:
            return rfqs
        return [r for r in rfqs if r.project_id == project_id]

    def transition(
        self,
        rfq_id: str,
        target: Stage,
        by: str,
        reason: str | None = None,
    ) -> RfqRecord:
        rfq = self._rfqs.get(rfq_id)
        if rfq is None:
            raise KeyError(f"Unknown RFQ: {rfq_id}")
        if not is_allowed(rfq.stage, target):
            raise ValueError(f"Transition {rfq.stage.value} -> {target.value} is not allowed")
        gate = check_gate(self, rfq_id, rfq.stage, target)
        if not gate.passed:
            raise ValueError(gate.reason)

        entry = StageTransition(
            from_stage=rfq.stage,
            to_stage=target,
            at=datetime.now(timezone.utc),
            by=by,
            reason=reason,
        )
        updated = rfq.model_copy(update={"stage": target, "history": [*rfq.history, entry]})
        self._rfqs[rfq_id] = updated
        return updated

    # -- artifacts --------------------------------------------------------

    def set_technical_package(
        self,
        rfq_id: str,
        revision: str,
        basis_of_design: str,
        attachments: list[Attachment],
    ) -> TechnicalPackage:
        if rfq_id not in self._rfqs:
            raise KeyError(f"Unknown RFQ: {rfq_id}")
        self._refuse_a_frozen_package(rfq_id)
        package = TechnicalPackage(
            rfq_id=rfq_id,
            revision=revision,
            basis_of_design=basis_of_design,
            attachments=list(attachments),
            # Rebuilt from the records rather than carried in the request, so a
            # package created after its documents were uploaded still lists
            # them and a caller cannot name a document that is not there.
            documents=self._contractor_document_ids(rfq_id),
        )
        self._packages[rfq_id] = package
        return package

    def _refuse_a_frozen_package(self, rfq_id: str) -> None:
        """Freezing exists precisely because vendors bid against a fixed
        revision. Replacing it afterwards moves the goalposts under bids
        already invited against the old one. (It used to be the Scoping exit
        criterion as well; that stage is gone, the refusal is not.)

        One function, because the rule now guards two collections — the
        package's own fields and the documents behind `documents` — and two
        copies of it would be two sentences to keep in step.
        """
        existing = self._packages.get(rfq_id)
        if existing is not None and existing.frozen_at is not None:
            raise ValueError(
                "The technical package is frozen and can no longer be edited."
            )

    def get_technical_package(self, rfq_id: str) -> TechnicalPackage | None:
        return self._packages.get(rfq_id)

    def freeze_package(self, rfq_id: str, by: str) -> TechnicalPackage:
        """A package can only be frozen when every attachment names a definite
        revision — "latest" is not a revision a vendor can bid against."""
        package = self._packages.get(rfq_id)
        if package is None:
            raise KeyError(f"No technical package for RFQ: {rfq_id}")
        if package.frozen_at is not None:
            raise ValueError(
                f"The technical package is already frozen, by {package.frozen_by}."
            )
        missing = [a.doc_code for a in package.attachments if not a.revision]
        if missing:
            raise ValueError(
                f"Cannot freeze: attachments without a definite revision: {', '.join(missing)}"
            )
        frozen = package.model_copy(
            update={"frozen_at": datetime.now(timezone.utc), "frozen_by": by}
        )
        self._packages[rfq_id] = frozen
        return frozen

    # -- the documents an RFQ carries -------------------------------------

    @staticmethod
    def _blob_key(document: RfqDocument) -> tuple[str, str]:
        """What makes two records point at one blob.

        The digest **and** the leaf name, because the blob path is built from
        both: the same bytes uploaded twice under one name are one file, and
        the same bytes under two names are two files that differ before the
        leaf. Keying on the digest alone would leave the second file on disk
        with no record naming it, which is the orphan I-D forbids.
        """
        return (document.sha256, document.filename)

    def _contractor_document_ids(self, rfq_id: str) -> list[str]:
        """The ids the technical package lists: the contractor's own documents,
        never a vendor's submission. One collection serves both halves of the
        enquiry, and this is the one function that says which half is which."""
        return [
            d.id
            for d in self._rfq_documents.get(rfq_id, [])
            if d.submitted_by_vendor_id is None
        ]

    def _relist_package_documents(self, rfq_id: str) -> None:
        """Rebuild `documents` from the records, so the list on the package
        cannot disagree with the collection it points into."""
        package = self._packages.get(rfq_id)
        if package is None:
            return
        self._packages[rfq_id] = package.model_copy(
            update={"documents": self._contractor_document_ids(rfq_id)}
        )

    def add_rfq_document(self, document: RfqDocument) -> RfqDocument:
        """Record one uploaded file against its RFQ.

        Two reads gate this write, and both are here rather than in the route
        for the reason this store states everywhere else: the route runs the
        whole method inside `locked_update`, so a check made outside it would
        be a second critical section.

        The RFQ must exist — half of I-E, and the half that can be enforced,
        since nothing in this repository deletes an RFQ.

        A **frozen package refuses a contractor document**, because that list
        is part of what vendors bid against. A vendor's submission is not
        refused: bids arrive after the freeze, and blocking them would make the
        rule that protects the enquiry destroy the responses to it.
        """
        if document.rfq_id not in self._rfqs:
            raise KeyError(f"Unknown RFQ: {document.rfq_id}")
        if document.submitted_by_vendor_id is None:
            self._refuse_a_frozen_package(document.rfq_id)
        self._rfq_documents.setdefault(document.rfq_id, []).append(document)
        self._relist_package_documents(document.rfq_id)
        return document

    def rfq_documents(self, rfq_id: str) -> list[RfqDocument]:
        """This RFQ's documents, in the order they arrived. Absent reads as
        empty — the screen asks before anybody has uploaded anything."""
        return list(self._rfq_documents.get(rfq_id, []))

    def remove_rfq_document(
        self, rfq_id: str, document_id: str
    ) -> tuple[RfqDocument, bool]:
        """Drop one record, and say whether its blob is now unreferenced.

        The second half of the answer is the whole of I-D. Content addressing
        means two records legitimately point at one blob, so a caller that
        deleted the file unconditionally would destroy a document somebody
        else's record still names — and a test with one document cannot tell
        the two behaviours apart.

        It is computed **here**, after the removal and against what is left,
        because it is a read that gates a write to the filesystem: deciding it
        in the route would be the two-critical-sections mistake this store has
        needed to avoid six times now.
        """
        entries = self._rfq_documents.get(rfq_id, [])
        removed = next((e for e in entries if e.id == document_id), None)
        if removed is None:
            raise KeyError(f"Unknown document: {document_id}")
        if removed.submitted_by_vendor_id is None:
            self._refuse_a_frozen_package(rfq_id)

        kept = [e for e in entries if e.id != document_id]
        self._rfq_documents[rfq_id] = kept
        self._relist_package_documents(rfq_id)
        key = self._blob_key(removed)
        return removed, not any(self._blob_key(e) == key for e in kept)

    def add_shortlist_entry(
        self,
        rfq_id: str,
        vendor_name: str | None = None,
        prequal_status: str | None = None,
        scope_code_fit: bool | None = None,
        included: bool = True,
        override_by: str | None = None,
        override_reason: str | None = None,
        vendor_id: str | None = None,
        as_of: date | None = None,
        entry_id: str | None = None,
    ) -> ShortlistEntry:
        """Invite a vendor, either from the registry (`vendor_id`) or by name.

        **A linked entry's snapshot comes from the registry, never from the
        caller.** With a `vendor_id`, `vendor_name`, `prequal_status` and
        `scope_code_fit` are derived here and any contradicting values in the
        call are ignored. Otherwise the registry would be decoration: a caller
        could link a suspended bidder and label the entry "Qualified".

        **Inviting a blocked bidder requires an override reason** — the
        positive, attributed act `ShortlistEntry`'s docstring has always
        claimed and nothing previously enforced. A caution (a scope mismatch,
        an approaching expiry) is not a refusal and needs no override; it is
        recorded in `scope_code_fit` and shown on the screen.

        Both of those are reads that gate this write, which is why they are
        here rather than in the route: the route runs this whole method inside
        `locked_update`, so they are one critical section with the write.

        The free-text path — no `vendor_id` — is unchanged. Eligibility can
        only be judged against a registry record, so the rules attach to the
        link rather than to the call.
        """
        if rfq_id not in self._rfqs:
            raise KeyError(f"Unknown RFQ: {rfq_id}")

        if vendor_id is not None:
            bidder = self._bidders.get(vendor_id)
            if bidder is None:
                raise KeyError(f"Unknown bidder: {vendor_id}")
            suitability = evaluate(bidder, self._rfqs[rfq_id], as_of or date.today())
            if suitability.blockers and not (override_reason or "").strip():
                raise BlockedBidder(
                    " ".join(suitability.blockers)
                    + " Record a reason to invite them anyway."
                )
            vendor_name = bidder.name
            prequal_status = suitability.effective_prequal
            scope_code_fit = suitability.scope_fit
        elif vendor_name is None or prequal_status is None or scope_code_fit is None:
            # Not coerced to a passing default: an unnamed vendor with an
            # assumed "Qualified" is exactly the record this feature exists to
            # stop being created.
            raise IncompleteShortlistEntry(
                "A shortlist entry needs either a vendor_id from the registry, "
                "or a vendor_name with its prequal_status and scope_code_fit."
            )

        entry = ShortlistEntry(
            **_with_id(entry_id),
            rfq_id=rfq_id,
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            prequal_status=prequal_status,
            scope_code_fit=scope_code_fit,
            included=included,
            override_by=override_by,
            override_reason=override_reason,
        )
        self._shortlists.setdefault(rfq_id, []).append(entry)
        self._revoke_shortlist_approval(rfq_id)
        return entry

    def adopt_draft_shortlist(self, rfq_id: str, item_id: str) -> DraftAdoption:
        """Turn an item's draft picks into invitations on this RFQ.

        This is the moment a draft stops being a draft, and it is called when
        an RFQ is raised over the item — the buyer assembled that selection
        for exactly this, so there is no second screen asking them to confirm
        what they already chose.

        Four rules, and each is somebody else's rule reused rather than a new
        one:

        - **It does not clear the draft.** The item may be covered by a second
          RFQ later, and silently emptying a buyer's basket because one RFQ
          consumed it is a surprise they cannot undo.
        - **It is idempotent.** A vendor already on this RFQ's shortlist is
          skipped, keyed the way the draft itself is keyed — the registry id
          where there is one and the name where there is not, so a registry
          row and a hand-typed one sharing a trading name adopt as two rows
          rather than one.
        - **A registry pick adopts through the `vendor_id` path**, so the
          entry's `vendor_name`, `prequal_status` and `scope_code_fit` are
          derived from the registry at adoption time by `add_shortlist_entry`
          rather than copied from the draft, which holds none of them. That
          derivation is not reimplemented here; there is one of it.
        - **A blocked bidder is skipped, not invited, and the skip is
          reported.** Inviting one requires a recorded reason and adoption has
          nobody to attribute one to, so they are left for the buyer to invite
          deliberately — which is the entire point of that guard. What must
          *not* happen is that they disappear quietly: the returned
          `DraftAdoption` carries every skipped vendor and the refusal's own
          sentence, and the RFQ-creation route sends them back with the 201.

        Exactly two conditions are caught, and both are named: a bidder the
        registry no longer holds, checked here before the call, and
        `BlockedBidder`, which is a distinct type precisely so this catch can be
        narrow. Everything else — `IncompleteShortlistEntry`, a validation error
        `add_shortlist_entry` grows later — propagates. A blanket
        `except (ValueError, KeyError)` here would turn any future regression
        into vendors quietly vanishing from shortlists with nothing to point at.
        """
        if rfq_id not in self._rfqs:
            raise KeyError(f"Unknown RFQ: {rfq_id}")
        if item_id not in self._items:
            raise KeyError(f"Unknown item: {item_id}")

        def key(vendor_id: str | None, vendor_name: str) -> tuple[str, str]:
            return ("id", vendor_id) if vendor_id else ("name", vendor_name)

        seen = {
            key(e.vendor_id, e.vendor_name) for e in self._shortlists.get(rfq_id, [])
        }
        added = 0
        skipped: list[SkippedPick] = []
        for pick in self.draft_shortlist(item_id):
            if key(pick.vendor_id, pick.vendor_name) in seen:
                continue
            if pick.vendor_id and pick.vendor_id not in self._bidders:
                # The draft outlived the registry row it pointed at. Checked
                # rather than caught, so the `KeyError` that means "unknown
                # RFQ" upstairs stays an error rather than a skip.
                skipped.append(SkippedPick(
                    vendor_name=pick.vendor_name,
                    reason=(
                        f"{pick.vendor_name} is no longer in the bidder "
                        f"registry, so there was nothing to invite."
                    ),
                ))
                continue
            try:
                if pick.vendor_id:
                    self.add_shortlist_entry(rfq_id, vendor_id=pick.vendor_id)
                else:
                    self.add_shortlist_entry(
                        rfq_id,
                        vendor_name=pick.vendor_name,
                        # The same defaults the item screen and the wizard's
                        # hand-typed row post. A curated pick has no registry
                        # row, so there is no prequalification finding to
                        # snapshot — "Under review" is what nobody has looked
                        # at yet, which is the true fact.
                        prequal_status="Under review",
                        scope_code_fit=True,
                    )
            except BlockedBidder as exc:
                # One such pick must not stop the other twenty-four being
                # invited, and raising would fail the RFQ creation this runs
                # inside. The refusal's own sentence travels with it.
                skipped.append(SkippedPick(pick.vendor_name, str(exc)))
                continue
            seen.add(key(pick.vendor_id, pick.vendor_name))
            added += 1
        return DraftAdoption(added=added, skipped=skipped)

    def remove_shortlist_entry(self, rfq_id: str, entry_id: str) -> None:
        """Refused while the entry has raised any clarification.

        Instance four of the rule `delete_item`, `delete_project` and
        `delete_bidder` each state, and the reason is identical:
        `persistence.save` replaces the document wholesale, so a query left
        pointing at a removed entry is not merely wrong in memory — it survives
        the restart as a dangling reference.

        Answered and withdrawn queries hold it too, not only open ones. A bidder
        who took part in the clarification round is part of its record; if they
        decline to bid they stay on the shortlist as a non-bidder, which is the
        true fact. Erasing them would make the register read as though they had
        never asked.
        """
        entries = self._shortlists.get(rfq_id, [])
        remaining = [e for e in entries if e.id != entry_id]
        if len(remaining) == len(entries):
            raise KeyError(f"Unknown shortlist entry: {entry_id}")

        raised = sorted(
            q.number for q in self._queries.get(rfq_id, [])
            if q.raised_by_entry_id == entry_id
        )
        if raised:
            vendor = next(e.vendor_name for e in entries if e.id == entry_id)
            raise ValueError(
                f"{vendor} cannot be removed from the shortlist: they raised "
                f"{', '.join(raised)}. The clarification register is the record "
                f"of who took part."
            )

        self._shortlists[rfq_id] = remaining
        self._revoke_shortlist_approval(rfq_id)

    def _revoke_shortlist_approval(self, rfq_id: str) -> None:
        """Approval is of a specific set of vendors, so it cannot outlive an
        edit to that set. Otherwise a vendor could be swapped in after
        procurement signed off and the RFQ would still issue as approved."""
        self._shortlist_approvals.pop(rfq_id, None)

    def shortlist_for(self, rfq_id: str) -> list[ShortlistEntry]:
        return list(self._shortlists.get(rfq_id, []))

    def approve_shortlist(self, rfq_id: str, by: str) -> None:
        if rfq_id not in self._rfqs:
            raise KeyError(f"Unknown RFQ: {rfq_id}")
        if not self._shortlists.get(rfq_id):
            raise ValueError("Cannot approve an empty shortlist")
        self._shortlist_approvals[rfq_id] = by

    def is_shortlist_approved(self, rfq_id: str) -> bool:
        return rfq_id in self._shortlist_approvals

    def set_tbe_template(
        self,
        rfq_id: str,
        criteria: list[str],
        source_rfq_reference: str | None = None,
    ) -> TbeTemplate:
        if rfq_id not in self._rfqs:
            raise KeyError(f"Unknown RFQ: {rfq_id}")
        template = TbeTemplate(
            rfq_id=rfq_id, criteria=list(criteria), source_rfq_reference=source_rfq_reference
        )
        self._tbe[rfq_id] = template
        return template

    def get_tbe_template(self, rfq_id: str) -> TbeTemplate | None:
        return self._tbe.get(rfq_id)

    def add_vdrl_line(
        self, rfq_id: str, doc_code: str, title: str, doc_type: str, mandatory: bool = True
    ) -> VdrlLine:
        if rfq_id not in self._rfqs:
            raise KeyError(f"Unknown RFQ: {rfq_id}")
        line = VdrlLine(
            rfq_id=rfq_id, doc_code=doc_code, title=title, doc_type=doc_type, mandatory=mandatory
        )
        self._vdrl.setdefault(rfq_id, []).append(line)
        return line

    def remove_vdrl_line(self, rfq_id: str, line_id: str) -> None:
        lines = self._vdrl.get(rfq_id, [])
        remaining = [line for line in lines if line.id != line_id]
        if len(remaining) == len(lines):
            raise KeyError(f"Unknown VDRL line: {line_id}")
        self._vdrl[rfq_id] = remaining

    def vdrl_for(self, rfq_id: str) -> list[VdrlLine]:
        return list(self._vdrl.get(rfq_id, []))

    # -- bids -------------------------------------------------------------

    def register_bid(
        self, rfq_id: str, vendor_name: str, headline_price_aed: int, currency: str = "AED"
    ) -> Bid:
        if rfq_id not in self._rfqs:
            raise KeyError(f"Unknown RFQ: {rfq_id}")
        bid = Bid(
            rfq_id=rfq_id,
            vendor_name=vendor_name,
            received_at=datetime.now(timezone.utc),
            headline_price_aed=headline_price_aed,
            currency=currency,
        )
        self._bids[bid.id] = bid
        return bid

    def bids_for(self, rfq_id: str) -> list[Bid]:
        return [b for b in self._bids.values() if b.rfq_id == rfq_id]

    def record_vdrl_receipt(
        self, bid_id: str, doc_code: str, state: ReceiptState, revision: str | None = None
    ) -> VdrlReceipt:
        if bid_id not in self._bids:
            raise KeyError(f"Unknown bid: {bid_id}")
        receipt = VdrlReceipt(bid_id=bid_id, doc_code=doc_code, state=state, revision=revision)
        self._receipts.setdefault(bid_id, []).append(receipt)
        return receipt

    def receipts_for(self, bid_id: str) -> list[VdrlReceipt]:
        return list(self._receipts.get(bid_id, []))

    def vdrl_summary(self, bid_id: str) -> tuple[int, int]:
        """Returns (received, required). Only `received` counts — `unreadable`
        and `not_received` are both gaps, and the count must not flatter a bid
        by treating a corrupt file as a delivered one."""
        bid = self._bids.get(bid_id)
        if bid is None:
            raise KeyError(f"Unknown bid: {bid_id}")
        required = [line for line in self.vdrl_for(bid.rfq_id) if line.mandatory]
        received_codes = self._received_codes(bid_id)
        return len([line for line in required if line.doc_code in received_codes]), len(required)

    def missing_vdrl_lines(self, bid_id: str) -> list[str]:
        bid = self._bids.get(bid_id)
        if bid is None:
            raise KeyError(f"Unknown bid: {bid_id}")
        received_codes = self._received_codes(bid_id)
        return [
            line.doc_code
            for line in self.vdrl_for(bid.rfq_id)
            if line.mandatory and line.doc_code not in received_codes
        ]

    def _received_codes(self, bid_id: str) -> set[str]:
        return {r.doc_code for r in self._receipts.get(bid_id, []) if r.state == "received"}

    def select_bids(
        self, rfq_id: str, bid_ids: list[str], by: str, rationale: str
    ) -> BidShortlist:
        if rfq_id not in self._rfqs:
            raise KeyError(f"Unknown RFQ: {rfq_id}")
        if not bid_ids:
            raise ValueError("At least one bid must be selected")
        if not rationale.strip():
            raise ValueError("A selection rationale is required")
        for bid_id in bid_ids:
            bid = self._bids.get(bid_id)
            if bid is None:
                raise KeyError(f"Unknown bid: {bid_id}")
            if bid.rfq_id != rfq_id:
                raise ValueError(f"Bid {bid_id} does not belong to RFQ {rfq_id}")
        shortlist = BidShortlist(
            rfq_id=rfq_id,
            selected_bid_ids=list(bid_ids),
            selected_by=by,
            rationale=rationale,
            at=datetime.now(timezone.utc),
        )
        self._bid_shortlists[rfq_id] = shortlist
        return shortlist

    def get_bid_shortlist(self, rfq_id: str) -> BidShortlist | None:
        return self._bid_shortlists.get(rfq_id)

    # -- clarifications ---------------------------------------------------
    #
    # Every refusal below is a read that gates a write, so it lives here and not
    # in the route: the route runs the whole method inside
    # `persistence.locked_update`, which makes the check and the write one
    # critical section. The same rule as the auth store and `delete_item`.

    def _query_or_raise(self, rfq_id: str, query_id: str) -> ClarificationQuery:
        for query in self._queries.get(rfq_id, []):
            if query.id == query_id:
                return query
        raise KeyError(f"Unknown clarification query: {query_id}")

    def _replace_query(
        self, rfq_id: str, updated: ClarificationQuery
    ) -> ClarificationQuery:
        """Replace in place, by id. Addressing by position would rewrite the
        wrong row the moment a list was reordered — the same rule the
        procurement store states as "by id, never by index"."""
        self._queries[rfq_id] = [
            updated if q.id == updated.id else q for q in self._queries.get(rfq_id, [])
        ]
        return updated

    def raise_query(
        self,
        rfq_id: str,
        entry_id: str,
        question: str,
        category: QueryCategory,
        raised_on: date,
        query_id: str | None = None,
    ) -> ClarificationQuery:
        """A query is raised against this RFQ by somebody invited to it.

        `raised_by_name` is snapshotted here for the same reason
        `ShortlistEntry` snapshots `vendor_name`: the register records who asked
        at the time they asked, and a later rename does not rewrite it.
        """
        if rfq_id not in self._rfqs:
            raise KeyError(f"Unknown RFQ: {rfq_id}")
        entry = next(
            (e for e in self._shortlists.get(rfq_id, []) if e.id == entry_id), None
        )
        if entry is None:
            # Two different failures, and they are not the same fact. An id that
            # exists on another RFQ's shortlist is a caller mixing up RFQs; one
            # that exists nowhere is a caller inventing a vendor.
            if any(
                e.id == entry_id
                for entries in self._shortlists.values()
                for e in entries
            ):
                raise ValueError(
                    f"Shortlist entry {entry_id} does not belong to RFQ {rfq_id}."
                )
            raise KeyError(f"Unknown shortlist entry: {entry_id}")
        if not entry.included:
            raise ValueError(
                f"{entry.vendor_name} is not included on this shortlist, so they "
                f"cannot raise a clarification against it."
            )
        if not question.strip():
            raise ValueError("A clarification needs a question.")

        query = ClarificationQuery(
            **_with_id(query_id),
            rfq_id=rfq_id,
            number=clarifications.next_query_number(
                self._queries.get(rfq_id, []), category
            ),
            raised_by_entry_id=entry.id,
            raised_by_name=entry.vendor_name,
            raised_on=raised_on,
            category=category,
            question=question.strip(),
        )
        self._queries.setdefault(rfq_id, []).append(query)
        return query

    def answer_query(
        self,
        rfq_id: str,
        query_id: str,
        answer: str,
        by: str,
        restricted_reason: str | None = None,
    ) -> ClarificationQuery:
        """Answering circulates to the whole included shortlist. Withholding
        requires an attributed reason.

        That is the shape `override_reason` and `rationale` already use, and the
        reason a `circulate` boolean was refused: a boolean makes a restricted
        answer indistinguishable from an oversight, and an answer given to one
        bidder and not the others is the classic tender-fairness failure.

        Re-answering is allowed and overwrites — a buyer revising an answer is
        normal practice, and the revision goes to the same audience. Answering a
        withdrawn query is not: it would put a live answer under a dead
        question.
        """
        query = self._query_or_raise(rfq_id, query_id)
        if query.withdrawn_at is not None:
            raise ValueError(
                f"{query.number} was withdrawn and can no longer be answered."
            )
        if not answer.strip():
            raise ValueError("An answer cannot be empty.")
        if restricted_reason is not None and not restricted_reason.strip():
            raise ValueError(
                "Withholding an answer from the rest of the shortlist needs a "
                "recorded reason. Leave it unset to circulate the answer."
            )
        return self._replace_query(rfq_id, query.model_copy(update={
            "answer": answer.strip(),
            "answered_by": by,
            "answered_at": datetime.now(timezone.utc),
            "restricted_reason": restricted_reason.strip() if restricted_reason else None,
        }))

    def withdraw_query(
        self, rfq_id: str, query_id: str, reason: str, by: str
    ) -> ClarificationQuery:
        """Take a query out of the round. The number stays in the register — it
        has already been quoted to a bidder in writing."""
        query = self._query_or_raise(rfq_id, query_id)
        if query.withdrawn_at is not None:
            raise ValueError(f"{query.number} is already withdrawn.")
        if not reason.strip():
            raise ValueError("Withdrawing a clarification needs a recorded reason.")
        return self._replace_query(rfq_id, query.model_copy(update={
            "withdrawn_reason": reason.strip(),
            "withdrawn_by": by,
            "withdrawn_at": datetime.now(timezone.utc),
        }))

    def queries_for(self, rfq_id: str) -> list[ClarificationQuery]:
        return list(self._queries.get(rfq_id, []))

    # -- addenda ----------------------------------------------------------

    def _addendum_or_raise(self, rfq_id: str, addendum_id: str) -> Addendum:
        for addendum in self._addenda.get(rfq_id, []):
            if addendum.id == addendum_id:
                return addendum
        raise KeyError(f"Unknown addendum: {addendum_id}")

    def _frozen_package_or_raise(self, rfq_id: str) -> TechnicalPackage:
        package = self._packages.get(rfq_id)
        if package is None:
            raise ValueError(
                "This RFQ has no technical package, so there is nothing for an "
                "addendum to supersede."
            )
        if package.frozen_at is None:
            raise ValueError(
                "The technical package is not frozen yet. Edit it directly — an "
                "addendum amends what vendors were already given."
            )
        return package

    def draft_addendum(
        self,
        rfq_id: str,
        revision: str,
        summary: str,
        attachments: list[Attachment],
        arising_from_query_ids: list[str] | None = None,
        bid_due_date: date | None = None,
        addendum_id: str | None = None,
    ) -> Addendum:
        """`supersedes_revision` is read from the package, never supplied.

        A caller-supplied "what I am superseding" is a claim; the store already
        knows the answer, and the answer is what makes the addenda list a
        revision trail rather than a pile of assertions.
        """
        if rfq_id not in self._rfqs:
            raise KeyError(f"Unknown RFQ: {rfq_id}")
        package = self._frozen_package_or_raise(rfq_id)
        if not summary.strip():
            raise ValueError("An addendum needs a summary of what changed.")
        if revision.strip() == package.revision:
            raise ValueError(
                f"An addendum must move the revision. The package is already at "
                f"{package.revision}, so bidders would have no way to tell which "
                f"one they hold."
            )
        known = {q.id for q in self._queries.get(rfq_id, [])}
        for query_id in arising_from_query_ids or []:
            if query_id not in known:
                raise KeyError(f"Unknown clarification query for this RFQ: {query_id}")

        addendum = Addendum(
            **_with_id(addendum_id),
            rfq_id=rfq_id,
            number=clarifications.next_addendum_number(self._addenda.get(rfq_id, [])),
            supersedes_revision=package.revision,
            revision=revision.strip(),
            summary=summary.strip(),
            attachments=list(attachments),
            arising_from_query_ids=list(arising_from_query_ids or []),
            bid_due_date=bid_due_date,
        )
        self._addenda.setdefault(rfq_id, []).append(addendum)
        return addendum

    def update_addendum(self, rfq_id: str, addendum_id: str, changes: dict) -> Addendum:
        """Partial, and validating, for the same two reasons as
        `update_project`: an absent field is left alone rather than cleared, and
        rebuilding through the model rather than `model_copy(update=...)` is
        what stops an invalid value reaching the store."""
        addendum = self._addendum_or_raise(rfq_id, addendum_id)
        if addendum.issued_at is not None:
            raise ValueError(
                f"{addendum.number} has been issued and can no longer be edited. "
                f"Bidders hold it."
            )
        _reject_immutable(changes, _ADDENDUM_IMMUTABLE)
        updated = Addendum(**{**addendum.model_dump(), **changes})
        self._addenda[rfq_id] = [
            updated if a.id == addendum_id else a for a in self._addenda.get(rfq_id, [])
        ]
        return updated

    def issue_addendum(self, rfq_id: str, addendum_id: str, by: str) -> Addendum:
        """Supersede the frozen package.

        The one sanctioned door through the immutability rule, exactly as
        retender and renegotiate are the only documented backward edges — the
        rule is not weakened, it is given one exit that says who used it and
        why. `set_technical_package` keeps refusing a frozen package.

        Four reads gate this write, and all four are here rather than in the
        route because the route runs this whole method inside `locked_update`.
        Check 2 is the one that would actually be lost by splitting them: two
        concurrent issues, each reading "current revision is B" outside the
        lock, would both write and the second would undo the first.
        """
        addendum = self._addendum_or_raise(rfq_id, addendum_id)
        if addendum.issued_at is not None:
            raise ValueError(f"{addendum.number} has already been issued.")

        package = self._frozen_package_or_raise(rfq_id)            # 1
        if addendum.supersedes_revision != package.revision:       # 2
            raise ValueError(
                f"{addendum.number} was drafted against "
                f"{addendum.supersedes_revision}, but the package is now at "
                f"{package.revision}. Redraft it against the current revision."
            )
        if addendum.revision == package.revision:                  # 3
            raise ValueError(
                f"{addendum.number} does not move the revision away from "
                f"{package.revision}."
            )
        missing = [a.doc_code for a in addendum.attachments if not a.revision]   # 4
        if missing:
            raise ValueError(
                f"Cannot issue {addendum.number}: attachments without a definite "
                f"revision: {', '.join(missing)}"
            )

        at = datetime.now(timezone.utc)
        self._packages[rfq_id] = TechnicalPackage(
            rfq_id=rfq_id,
            revision=addendum.revision,
            basis_of_design=package.basis_of_design,
            attachments=list(addendum.attachments),
            frozen_at=at,
            frozen_by=by,
        )
        issued = addendum.model_copy(update={"issued_at": at, "issued_by": by})
        self._addenda[rfq_id] = [
            issued if a.id == addendum_id else a for a in self._addenda.get(rfq_id, [])
        ]
        return issued

    def delete_addendum(self, rfq_id: str, addendum_id: str) -> None:
        addendum = self._addendum_or_raise(rfq_id, addendum_id)
        if addendum.issued_at is not None:
            raise ValueError(
                f"{addendum.number} has been issued and cannot be deleted. "
                f"Bidders hold it."
            )
        self._addenda[rfq_id] = [
            a for a in self._addenda.get(rfq_id, []) if a.id != addendum_id
        ]

    def addenda_for(self, rfq_id: str) -> list[Addendum]:
        return list(self._addenda.get(rfq_id, []))

    def current_bid_due_date(self, rfq_id: str) -> date | None:
        """The operative due date: the latest *issued* addendum that names one.

        Ordered by `issued_at`, not by list position or by number. A draft does
        not count — a due date nobody has been told about is not a due date.

        No field is added to `RfqRecord`: the original due date is set at
        Issued, which this phase does not touch, so a stored field here would be
        half-owned and wrong for every RFQ that has no addendum.
        """
        dated = [
            a for a in self._addenda.get(rfq_id, [])
            if a.issued_at is not None and a.bid_due_date is not None
        ]
        if not dated:
            return None
        return max(dated, key=lambda a: a.issued_at).bid_due_date
