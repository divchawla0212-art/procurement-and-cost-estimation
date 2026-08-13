from datetime import date, datetime, timezone

from workflow.models.bid import Bid, BidShortlist, ReceiptState, VdrlReceipt
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
    ) -> Project:
        project = Project(
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

    def rename_project(self, project_id: str, new_name: str) -> Project:
        project = self._projects.get(project_id)
        if project is None:
            raise KeyError(f"Unknown project: {project_id}")
        updated = project.model_copy(update={"name": new_name})
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
    ) -> Item:
        if project_id not in self._projects:
            raise KeyError(f"Unknown project: {project_id}")
        item = Item(
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

    def items_for_project(self, project_id: str) -> list[Item]:
        return [i for i in self._items.values() if i.project_id == project_id]

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

    # -- rfqs -------------------------------------------------------------

    def create_rfq(
        self,
        project_id: str,
        item_ids: list[str],
        reference: str,
        package: str,
        discipline: str,
        value_estimate_aed: int,
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
        vendor_name: str,
        prequal_status: str,
        scope_code_fit: bool,
        included: bool,
        override_by: str | None = None,
        override_reason: str | None = None,
    ) -> ShortlistEntry:
        if rfq_id not in self._rfqs:
            raise KeyError(f"Unknown RFQ: {rfq_id}")
        entry = ShortlistEntry(
            rfq_id=rfq_id,
            vendor_name=vendor_name,
            prequal_status=prequal_status,
            scope_code_fit=scope_code_fit,
            included=included,
            override_by=override_by,
            override_reason=override_reason,
        )
        self._shortlists.setdefault(rfq_id, []).append(entry)
        return entry

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
