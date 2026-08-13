"""The eight-stage RFQ workflow: `/api/workflow/*`.

A thin adapter over `workflow.store` — the HTTP layer maps exceptions to status
codes and never decides anything the state machine or a gate should decide.

These routes are deliberately absent from `middleware.PUBLIC_PATHS`, so the
fail-closed session middleware challenges every one of them.

Status mapping, kept consistent across the module:
  404  the named project / item / RFQ does not exist
  409  it exists, but the workflow says no — a closed gate or a refused freeze
  422  the request is self-inconsistent, e.g. an item from another project
"""
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from workflow.gates import GateResult, check_gate
from workflow.models.rfq import Attachment
from workflow.stages import STAGE_ORDER, Stage
from workflow.store import WorkflowStore

router = APIRouter(prefix="/api/workflow", tags=["workflow"])

# Module-level singleton. Phase 1 has no persistence by design, so this dies
# with the process; Phase 2 replaces it with a repository over the database.
workflow_store = WorkflowStore()


class ProjectIn(BaseModel):
    name: str
    code: str
    client: str
    location: str
    live_period_start: date
    live_period_end: date
    currency: str = "AED"


class ItemIn(BaseModel):
    item_type: str
    description: str
    qty: float
    uom: str
    discipline: str
    estimated_value_aed: int
    required_on_site: date | None = None
    is_long_lead: bool = False


class RfqIn(BaseModel):
    project_id: str
    item_ids: list[str]
    reference: str
    package: str
    discipline: str
    value_estimate_aed: int


class TransitionIn(BaseModel):
    target: Stage
    by: str
    reason: str | None = None


class TechnicalPackageIn(BaseModel):
    revision: str
    basis_of_design: str
    attachments: list[Attachment] = []


class FreezeIn(BaseModel):
    by: str


@router.get("/stages")
def get_stages() -> dict:
    return {"stages": [s.value for s in STAGE_ORDER]}


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn) -> dict:
    project = workflow_store.create_project(**body.model_dump())
    return project.model_dump(mode="json")


@router.get("/projects")
def list_projects() -> dict:
    return {"projects": [p.model_dump(mode="json") for p in workflow_store.list_projects()]}


@router.get("/projects/{project_id}/items")
def list_items(project_id: str) -> dict:
    if workflow_store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")
    return {
        "items": [i.model_dump(mode="json") for i in workflow_store.items_for_project(project_id)]
    }


@router.post("/projects/{project_id}/items", status_code=201)
def create_item(project_id: str, body: ItemIn) -> dict:
    try:
        item = workflow_store.create_item(project_id=project_id, **body.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return item.model_dump(mode="json")


@router.post("/rfqs", status_code=201)
def create_rfq(body: RfqIn) -> dict:
    try:
        rfq = workflow_store.create_rfq(**body.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return rfq.model_dump(mode="json")


@router.get("/rfqs")
def list_rfqs(project_id: str | None = None) -> dict:
    """The RFQ roster, plus the counts the stage strip renders.

    `stage_counts` covers every stage, including the empty ones — a strip that
    silently dropped a stage with no RFQs in it would change shape as work
    moved through, which is exactly when a reader needs it to hold still.
    """
    rfqs = workflow_store.list_rfqs(project_id=project_id)
    return {
        "rfqs": [r.model_dump(mode="json") for r in rfqs],
        "stages": [s.value for s in STAGE_ORDER],
        "stage_counts": {
            s.value: sum(1 for r in rfqs if r.stage is s) for s in STAGE_ORDER
        },
    }


def _next_forward_gate(rfq_id: str, stage: Stage) -> GateResult:
    """Gate status for the natural next stage in process order. The terminal
    stage has no successor, so there is nothing left to block."""
    index = STAGE_ORDER.index(stage)
    if index + 1 >= len(STAGE_ORDER):
        return GateResult(passed=True, reason=None)
    return check_gate(workflow_store, rfq_id, stage, STAGE_ORDER[index + 1])


@router.get("/rfqs/{rfq_id}")
def get_rfq(rfq_id: str) -> dict:
    rfq = workflow_store.get_rfq(rfq_id)
    if rfq is None:
        raise HTTPException(status_code=404, detail=f"Unknown RFQ: {rfq_id}")
    gate = _next_forward_gate(rfq_id, rfq.stage)
    return {"rfq": rfq.model_dump(mode="json"), "gate": gate.model_dump()}


@router.post("/rfqs/{rfq_id}/transition")
def transition(rfq_id: str, body: TransitionIn) -> dict:
    try:
        rfq = workflow_store.transition(
            rfq_id, target=body.target, by=body.by, reason=body.reason
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        # Both a denied edge and a closed gate land here, and both are "the
        # workflow refuses", not "the request was malformed".
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return rfq.model_dump(mode="json")


@router.put("/rfqs/{rfq_id}/technical-package")
def set_technical_package(rfq_id: str, body: TechnicalPackageIn) -> dict:
    try:
        package = workflow_store.set_technical_package(
            rfq_id,
            revision=body.revision,
            basis_of_design=body.basis_of_design,
            attachments=body.attachments,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return package.model_dump(mode="json")


@router.post("/rfqs/{rfq_id}/technical-package/freeze")
def freeze_technical_package(rfq_id: str, body: FreezeIn) -> dict:
    try:
        package = workflow_store.freeze_package(rfq_id, by=body.by)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return package.model_dump(mode="json")
