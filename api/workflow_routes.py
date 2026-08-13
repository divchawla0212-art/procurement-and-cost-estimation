"""The eight-stage RFQ workflow: `/api/workflow/*`.

A thin adapter over `workflow.store` — the HTTP layer maps exceptions to status
codes and never decides anything the state machine or a gate should decide.

These routes are deliberately absent from `middleware.PUBLIC_PATHS`, so the
fail-closed session middleware challenges every one of them.

**Every mutating route runs inside `persistence.locked_update`, and so does
every decision that gates one.** The gate reads the technical package, the
shortlist and the TBE template before allowing a transition; checking that in
the route and writing in the store would be two critical sections rather than
one, and two concurrent transitions could each read "gate open" and both
write. `WorkflowStore.transition` therefore runs *inside* the block, not
against a store loaded before it.

Status mapping, kept consistent across the module:
  404  the named project / item / RFQ does not exist
  409  it exists, but the workflow says no — a closed gate or a refused freeze
  422  the request is self-inconsistent, e.g. an item from another project
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth.deps import current_user
from api.auth.models import User
from workflow import persistence
from workflow.gates import GateResult, check_gate
from workflow.models.rfq import Attachment
from workflow.stages import STAGE_ORDER, Stage
from workflow.store import WorkflowStore

router = APIRouter(prefix="/api/workflow", tags=["workflow"])


def _root() -> str:
    """Read `api.main.ROOT` at call time, not at import time — tests do
    `monkeypatch.setattr(api_main, "ROOT", str(tmp_path))`, and a module-level
    binding would resolve against the real `projects/` directory. Mirrors
    `api/auth/routes.py`'s `store_root`."""
    import api.main as api_main
    return api_main.ROOT


def _read() -> WorkflowStore:
    return persistence.load(_root())


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
    """`by` is deliberately absent. Who moved an RFQ is taken from the session,
    never from the request body — a client-supplied `by` would let any
    signed-in user write someone else's name into an append-only history that
    exists precisely to answer "who did this". `reason` stays a body field:
    it is the actor's own words, and only they can supply it."""

    target: Stage
    reason: str | None = None


class TechnicalPackageIn(BaseModel):
    revision: str
    basis_of_design: str
    attachments: list[Attachment] = []


class FreezeIn(BaseModel):
    """Empty for the same reason `TransitionIn` has no `by`: freezing a package
    is an attributed act, and the attribution comes from the session."""


@router.get("/stages")
def get_stages() -> dict:
    return {"stages": [s.value for s in STAGE_ORDER]}


@router.get("/projects")
def list_projects() -> dict:
    return {"projects": [p.model_dump(mode="json") for p in _read().list_projects()]}


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn) -> dict:
    with persistence.locked_update(_root()) as store:
        project = store.create_project(**body.model_dump())
    return project.model_dump(mode="json")


@router.get("/projects/{project_id}/items")
def list_items(project_id: str) -> dict:
    store = _read()
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")
    return {"items": [i.model_dump(mode="json") for i in store.items_for_project(project_id)]}


@router.post("/projects/{project_id}/items", status_code=201)
def create_item(project_id: str, body: ItemIn) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            item = store.create_item(project_id=project_id, **body.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return item.model_dump(mode="json")


@router.get("/rfqs")
def list_rfqs(project_id: str | None = None) -> dict:
    """The RFQ roster, plus the counts the stage strip renders.

    `stage_counts` covers every stage, including the empty ones — a strip that
    silently dropped a stage with no RFQs in it would change shape as work
    moved through, which is exactly when a reader needs it to hold still.
    """
    rfqs = _read().list_rfqs(project_id=project_id)
    return {
        "rfqs": [r.model_dump(mode="json") for r in rfqs],
        "stages": [s.value for s in STAGE_ORDER],
        "stage_counts": {s.value: sum(1 for r in rfqs if r.stage is s) for s in STAGE_ORDER},
    }


@router.post("/rfqs", status_code=201)
def create_rfq(body: RfqIn) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            rfq = store.create_rfq(**body.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return rfq.model_dump(mode="json")


def _next_forward_gate(store: WorkflowStore, rfq_id: str, stage: Stage) -> GateResult:
    """Gate status for the natural next stage in process order. The terminal
    stage has no successor, so there is nothing left to block."""
    index = STAGE_ORDER.index(stage)
    if index + 1 >= len(STAGE_ORDER):
        return GateResult(passed=True, reason=None)
    return check_gate(store, rfq_id, stage, STAGE_ORDER[index + 1])


@router.get("/rfqs/{rfq_id}")
def get_rfq(rfq_id: str) -> dict:
    store = _read()
    rfq = store.get_rfq(rfq_id)
    if rfq is None:
        raise HTTPException(status_code=404, detail=f"Unknown RFQ: {rfq_id}")
    gate = _next_forward_gate(store, rfq_id, rfq.stage)
    return {"rfq": rfq.model_dump(mode="json"), "gate": gate.model_dump()}


@router.post("/rfqs/{rfq_id}/transition")
def transition(
    rfq_id: str, body: TransitionIn, user: User = Depends(current_user)
) -> dict:
    try:
        # The gate check lives inside `transition`, which runs inside the lock.
        # A ValueError here leaves the document untouched — `locked_update`
        # writes only on a clean exit — so a refused transition cannot half-land.
        with persistence.locked_update(_root()) as store:
            rfq = store.transition(
                rfq_id, target=body.target, by=user.email, reason=body.reason
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
        with persistence.locked_update(_root()) as store:
            package = store.set_technical_package(
                rfq_id,
                revision=body.revision,
                basis_of_design=body.basis_of_design,
                attachments=body.attachments,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return package.model_dump(mode="json")


@router.post("/rfqs/{rfq_id}/technical-package/freeze")
def freeze_technical_package(
    rfq_id: str, body: FreezeIn, user: User = Depends(current_user)
) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            package = store.freeze_package(rfq_id, by=user.email)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return package.model_dump(mode="json")
