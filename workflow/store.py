from datetime import date, datetime, timezone

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
from workflow.stages import Stage, is_allowed


class WorkflowStore:
    """In-memory store for workflow entities. Phase 1 carries no database —
    persistence arrives in Phase 2, and every method here is written so that
    swapping the dicts for a repository does not change a caller."""

    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}
        self._items: dict[str, Item] = {}
        self._rfqs: dict[str, RfqRecord] = {}
        self._packages: dict[str, TechnicalPackage] = {}
        self._shortlists: dict[str, list[ShortlistEntry]] = {}
        self._shortlist_approvals: dict[str, str] = {}
        self._tbe: dict[str, TbeTemplate] = {}
        self._vdrl: dict[str, list[VdrlLine]] = {}

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
