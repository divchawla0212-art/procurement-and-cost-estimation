from datetime import date, datetime, timezone

from workflow.bidders import evaluate
from workflow.models.bid import Bid, BidShortlist, ReceiptState, VdrlReceipt
from workflow.models.bidder import Bidder, PrequalStatus
from workflow.models.project import Item, Project
from workflow.models.rfq import (
    Attachment,
    RfqRecord,
    ShortlistEntry,
    StageTransition,
    TbeTemplate,
    TechnicalPackage,
    VdrlLine,
)
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

    # -- bidders ----------------------------------------------------------
    #
    # The registry is organisation-wide, not per project: a prequalification is
    # a fact about a company, and holding it per project would mean re-entering
    # and re-approving the same company for every one of them.

    def create_bidder(
        self,
        name: str,
        country: str,
        currency: str = "AED",
        trade_categories: list[str] | None = None,
        prequal_status: PrequalStatus = "Under review",
        prequal_expires_on: date | None = None,
        on_hold: bool = False,
        hold_reason: str | None = None,
        turnover_band: str | None = None,
        performance_rating: float | None = None,
        past_awards: int = 0,
        notes: str | None = None,
        bidder_id: str | None = None,
    ) -> Bidder:
        bidder = Bidder(
            **_with_id(bidder_id),
            name=name,
            country=country,
            currency=currency,
            trade_categories=list(trade_categories or []),
            prequal_status=prequal_status,
            prequal_expires_on=prequal_expires_on,
            on_hold=on_hold,
            hold_reason=hold_reason,
            turnover_band=turnover_band,
            performance_rating=performance_rating,
            past_awards=past_awards,
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
                    to_stage=Stage.SCOPING,
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
        existing = self._packages.get(rfq_id)
        if existing is not None and existing.frozen_at is not None:
            # Freezing is the Scoping exit criterion precisely because vendors
            # bid against a fixed revision. Replacing it afterwards moves the
            # goalposts under bids already invited against the old one.
            raise ValueError(
                "The technical package is frozen and can no longer be edited."
            )
        package = TechnicalPackage(
            rfq_id=rfq_id,
            revision=revision,
            basis_of_design=basis_of_design,
            attachments=list(attachments),
        )
        self._packages[rfq_id] = package
        return package

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
                raise ValueError(
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

    def remove_shortlist_entry(self, rfq_id: str, entry_id: str) -> None:
        entries = self._shortlists.get(rfq_id, [])
        remaining = [e for e in entries if e.id != entry_id]
        if len(remaining) == len(entries):
            raise KeyError(f"Unknown shortlist entry: {entry_id}")
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
