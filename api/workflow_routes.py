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
from workflow import clarifications, persistence
from workflow.bidders import effective_prequal, evaluate, missing_client_approval
from workflow.gates import GateResult, check_gate
from workflow.models.bidder import Bidder, PrequalStatus
from workflow.models.clarification import Addendum, ClarificationQuery, QueryCategory
from workflow.models.project import Item, ProjectStatus
from workflow.models.rfq import Attachment
from workflow.stages import STAGE_ORDER, Stage
from workflow.store import IncompleteShortlistEntry, WorkflowStore

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


class ProjectPatch(BaseModel):
    """Every field optional: an absent key means "leave it alone", never "clear
    it". `id` is deliberately not a field here — identity is not editable, and
    `WorkflowStore` refuses it a second time for callers that never come
    through this route.

    `status` keeps its `ProjectStatus` literal rather than widening to `str`, so
    an unknown status is a 422 from the request model rather than a value the
    store has to reject.
    """

    name: str | None = None
    code: str | None = None
    client: str | None = None
    location: str | None = None
    live_period_start: date | None = None
    live_period_end: date | None = None
    currency: str | None = None
    status: ProjectStatus | None = None


class ItemPatch(BaseModel):
    """Partial for the same reason as `ProjectPatch`. `project_id` is absent:
    an item's parent is not editable."""

    item_type: str | None = None
    description: str | None = None
    qty: float | None = None
    uom: str | None = None
    discipline: str | None = None
    estimated_value_aed: int | None = None
    required_on_site: date | None = None
    is_long_lead: bool | None = None


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


class BidderIn(BaseModel):
    name: str
    country: str | None = None
    currency: str = "AED"
    approved_by: list[str] = []
    trade_categories: list[str] = []
    prequal_status: PrequalStatus = "Under review"
    prequal_expires_on: date | None = None
    on_hold: bool = False
    hold_reason: str | None = None
    turnover_band: str | None = None
    performance_rating: float | None = None
    past_awards: int = 0
    represented_manufacturers: list[str] = []
    notes: str | None = None


class BidderPatch(BaseModel):
    """Partial for the same reason as `ProjectPatch`: an absent key means
    "leave it alone", never "clear it". `id` is deliberately not a field, and
    the store refuses it a second time for callers that never come through
    here.

    `prequal_status` keeps its literal rather than widening to `str`, so an
    unknown status is a 422 from the request model and never reaches the
    store."""

    name: str | None = None
    country: str | None = None
    currency: str | None = None
    approved_by: list[str] | None = None
    trade_categories: list[str] | None = None
    prequal_status: PrequalStatus | None = None
    prequal_expires_on: date | None = None
    on_hold: bool | None = None
    hold_reason: str | None = None
    turnover_band: str | None = None
    performance_rating: float | None = None
    past_awards: int | None = None
    represented_manufacturers: list[str] | None = None
    notes: str | None = None


class ShortlistEntryIn(BaseModel):
    """Either a `vendor_id` from the registry, or the three free-text fields.

    The three stay optional rather than being split into two request models
    because the store is what decides between the paths, and it is the store —
    inside the lock — that ignores them when a `vendor_id` is present. A route
    that pre-emptively dropped them would be making that decision a second
    time, in a second place.
    """

    vendor_id: str | None = None
    vendor_name: str | None = None
    prequal_status: str | None = None
    scope_code_fit: bool | None = None
    included: bool = True
    # An override is a positive act, so it carries its own reason. `override_by`
    # is not an input for the same reason `by` never is — it comes from the
    # session, or a user could sign someone else's name to the exception.
    override_reason: str | None = None


class TbeTemplateIn(BaseModel):
    criteria: list[str]
    source_rfq_reference: str | None = None


class VdrlLineIn(BaseModel):
    doc_code: str
    title: str
    doc_type: str
    mandatory: bool = True


class QueryIn(BaseModel):
    raised_by_entry_id: str
    category: QueryCategory
    question: str
    raised_on: date


class AnswerIn(BaseModel):
    answer: str
    # Absent means circulate. The screen omits the field rather than sending an
    # empty string, so an unrestricted answer is never recorded as restricted
    # with no text — the same shape the invite control uses for override_reason.
    restricted_reason: str | None = None


class WithdrawIn(BaseModel):
    reason: str


class AddendumIn(BaseModel):
    revision: str
    summary: str
    attachments: list[Attachment] = []
    arising_from_query_ids: list[str] = []
    bid_due_date: date | None = None


class AddendumPatch(BaseModel):
    """Partial: `model_dump(exclude_unset=True)` is what makes an absent field
    mean "leave it alone" rather than "clear it". The immutable fields are not
    declared here at all, so naming one is a 422 before the store sees it — and
    the store refuses them again, for a caller that bypasses this model."""

    revision: str | None = None
    summary: str | None = None
    attachments: list[Attachment] | None = None
    arising_from_query_ids: list[str] | None = None
    bid_due_date: date | None = None


class IssueIn(BaseModel):
    """Empty on purpose: who issued it comes from the session, not the body."""


@router.get("/stages")
def get_stages() -> dict:
    return {"stages": [s.value for s in STAGE_ORDER]}


def _item_payload(store: WorkflowStore, item: Item) -> dict:
    """An item, plus the live-period caution when its delivery date falls
    outside the project's window.

    Non-blocking on purpose: needing something after a live period closes is
    unusual, not impossible, and a hard refusal would make the field unusable in
    exactly those cases. Merged into the item's own object rather than wrapping
    it — the convention `get_rfq` already uses for a bid's VDRL tally, and it
    keeps `response["id"]` working for callers that predate the warning.
    """
    warning = (
        store.validate_against_live_period(item.project_id, item.required_on_site)
        if item.required_on_site
        else None
    )
    return {**item.model_dump(mode="json"), "live_period_warning": warning}


def _owned_item(store: WorkflowStore, project_id: str, item_id: str) -> Item:
    """The path names a parent, so an item belonging to a different project is
    *not found* under this one. Returning it anyway would let a caller edit any
    item by pairing its id with any project id.

    Called inside `locked_update`, never before it: this is a read that gates a
    write, and the `HTTPException` it raises leaves the block without reaching
    `save`.
    """
    item = store.get_item(item_id)
    if item is None or item.project_id != project_id:
        raise HTTPException(status_code=404, detail=f"Unknown item: {item_id}")
    return item


@router.get("/projects")
def list_projects() -> dict:
    """The roster the Projects screen reads. Counts are computed here so no
    screen re-derives them from a list it only partly holds."""
    store = _read()
    rfqs = store.list_rfqs()
    return {"projects": [
        {
            **p.model_dump(mode="json"),
            "item_count": len(store.items_for_project(p.id)),
            "rfq_count": sum(1 for r in rfqs if r.project_id == p.id),
        }
        for p in store.list_projects()
    ]}


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn) -> dict:
    with persistence.locked_update(_root()) as store:
        project = store.create_project(**body.model_dump())
    return project.model_dump(mode="json")


@router.get("/projects/{project_id}")
def get_project(project_id: str) -> dict:
    """One project with its items and its RFQs, in one response.

    The detail screen renders all three, and three separate calls could return
    three different generations of the document.
    """
    store = _read()
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")
    return {
        "project": project.model_dump(mode="json"),
        "items": [i.model_dump(mode="json") for i in store.items_for_project(project_id)],
        "rfqs": [r.model_dump(mode="json") for r in store.list_rfqs(project_id=project_id)],
    }


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, body: ProjectPatch) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            # `exclude_unset` is what makes this partial: a field the client
            # never sent is absent from the dict, so the store leaves it alone.
            # `exclude_none` would be wrong — it cannot tell "not sent" from
            # "sent as null".
            project = store.update_project(project_id, body.model_dump(exclude_unset=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return project.model_dump(mode="json")


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str) -> None:
    try:
        with persistence.locked_update(_root()) as store:
            store.delete_project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        # The project exists and the request is well-formed; the workflow
        # refuses because an RFQ still hangs off it.
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
            payload = _item_payload(store, item)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return payload


@router.patch("/projects/{project_id}/items/{item_id}")
def patch_item(project_id: str, item_id: str, body: ItemPatch) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            _owned_item(store, project_id, item_id)
            item = store.update_item(item_id, body.model_dump(exclude_unset=True))
            payload = _item_payload(store, item)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return payload


@router.delete("/projects/{project_id}/items/{item_id}", status_code=204)
def delete_item(project_id: str, item_id: str) -> None:
    try:
        with persistence.locked_update(_root()) as store:
            _owned_item(store, project_id, item_id)
            store.delete_item(item_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        # An RFQ covers it. The item exists; the workflow refuses.
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# -- bidders -----------------------------------------------------------------
#
# The registry is organisation-wide, so these paths hang off `/bidders` rather
# than off a project. Nothing here decides anything: the delete guard and the
# eligibility rule both live in `WorkflowStore`, because a check in the route
# and a write in the store are two critical sections rather than one.


def _bidder_payload(store: WorkflowStore, bidder: Bidder, as_of: date) -> dict:
    """A bidder, plus the three values a screen must not compute for itself.

    `effective_prequal` and `approval_caution` are both derived rather than
    stored, so there has to be exactly one definition of each and this is where
    callers read them. Letting a screen compare `prequal_expires_on` to its own
    clock, or test `approved_by` for the client's name, would be a second.
    """
    return {
        **bidder.model_dump(mode="json"),
        "effective_prequal": effective_prequal(bidder, as_of),
        "approval_caution": missing_client_approval(bidder),
        "invited_count": len(store.rfqs_inviting(bidder.id)),
    }


@router.get("/bidders")
def list_bidders() -> dict:
    store = _read()
    today = date.today()
    return {"bidders": [_bidder_payload(store, b, today) for b in store.list_bidders()]}


@router.post("/bidders", status_code=201)
def create_bidder(body: BidderIn) -> dict:
    with persistence.locked_update(_root()) as store:
        bidder = store.create_bidder(**body.model_dump())
    return bidder.model_dump(mode="json")


@router.get("/bidders/{bidder_id}")
def get_bidder(bidder_id: str) -> dict:
    store = _read()
    bidder = store.get_bidder(bidder_id)
    if bidder is None:
        raise HTTPException(status_code=404, detail=f"Unknown bidder: {bidder_id}")
    return {
        **_bidder_payload(store, bidder, date.today()),
        # Named rather than counted here: this is the screen that has to tell
        # the reader why a delete was refused before they attempt it.
        "invited_by": store.rfqs_inviting(bidder_id),
    }


@router.patch("/bidders/{bidder_id}")
def patch_bidder(bidder_id: str, body: BidderPatch) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            bidder = store.update_bidder(bidder_id, body.model_dump(exclude_unset=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return bidder.model_dump(mode="json")


@router.delete("/bidders/{bidder_id}", status_code=204)
def delete_bidder(bidder_id: str) -> None:
    try:
        with persistence.locked_update(_root()) as store:
            store.delete_bidder(bidder_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        # A shortlist still references them. The bidder exists and the request
        # is well-formed; the workflow refuses.
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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


def _query_payload(query: ClarificationQuery) -> dict:
    """`state` and `circulated` are computed here rather than stored, so no
    screen re-derives that withdrawal beats an answer."""
    return {
        **query.model_dump(mode="json"),
        "state": clarifications.state(query),
        "circulated": clarifications.is_circulated(query),
    }


def _addendum_payload(addendum: Addendum) -> dict:
    return {
        **addendum.model_dump(mode="json"),
        "draft": clarifications.is_draft(addendum),
    }


def _next_forward_gate(store: WorkflowStore, rfq_id: str, stage: Stage) -> GateResult:
    """Gate status for the natural next stage in process order. The terminal
    stage has no successor, so there is nothing left to block."""
    index = STAGE_ORDER.index(stage)
    if index + 1 >= len(STAGE_ORDER):
        return GateResult(passed=True, reason=None)
    return check_gate(store, rfq_id, stage, STAGE_ORDER[index + 1])


@router.get("/rfqs/{rfq_id}")
def get_rfq(rfq_id: str) -> dict:
    """One RFQ, with every artifact its gates read.

    The gate sentence alone tells a reader that a stage is blocked but not what
    to do about it — every reason here resolves to some artifact being absent,
    unfrozen or unapproved, so the screen needs them alongside it.

    Each bid carries its own VDRL tally rather than the raw receipts: a caller
    recomputing "received" from receipt rows would have to re-derive that
    `unreadable` does not count, and that rule belongs in one place.
    """
    store = _read()
    rfq = store.get_rfq(rfq_id)
    if rfq is None:
        raise HTTPException(status_code=404, detail=f"Unknown RFQ: {rfq_id}")

    package = store.get_technical_package(rfq_id)
    tbe = store.get_tbe_template(rfq_id)
    selection = store.get_bid_shortlist(rfq_id)
    selected = set(selection.selected_bid_ids) if selection else set()

    bids = []
    for bid in store.bids_for(rfq_id):
        received, required = store.vdrl_summary(bid.id)
        bids.append({
            **bid.model_dump(mode="json"),
            "vdrl_received": received,
            "vdrl_required": required,
            "vdrl_missing": store.missing_vdrl_lines(bid.id),
            "selected": bid.id in selected,
        })

    return {
        "rfq": rfq.model_dump(mode="json"),
        "gate": _next_forward_gate(store, rfq_id, rfq.stage).model_dump(),
        "technical_package": package.model_dump(mode="json") if package else None,
        "shortlist": [e.model_dump(mode="json") for e in store.shortlist_for(rfq_id)],
        "shortlist_approved": store.is_shortlist_approved(rfq_id),
        "tbe_template": tbe.model_dump(mode="json") if tbe else None,
        "vdrl": [line.model_dump(mode="json") for line in store.vdrl_for(rfq_id)],
        "bids": bids,
        "bid_selection": selection.model_dump(mode="json") if selection else None,
        "queries": [_query_payload(q) for q in store.queries_for(rfq_id)],
        "addenda": [_addendum_payload(a) for a in store.addenda_for(rfq_id)],
        # Derived from the latest issued addendum, never stored: see
        # `WorkflowStore.current_bid_due_date`. An RFQ with no addendum has no
        # due date here, rather than an invented one.
        "bid_due_date": (
            due.isoformat() if (due := store.current_bid_due_date(rfq_id)) else None
        ),
    }


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
    except ValueError as exc:
        # A frozen package refusing an edit: the RFQ exists and the request is
        # well-formed, the workflow simply says no.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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


# -- artifact editing --------------------------------------------------------
#
# Each of these is what one step of the wizard writes. They are all gated the
# same way as everything else in this module: inside `locked_update`, so a
# decision the store makes (a frozen package, an unknown id) and the write it
# authorises are one critical section.


@router.get("/rfqs/{rfq_id}/candidates")
def list_candidates(rfq_id: str) -> dict:
    """The whole registry, judged against this one RFQ.

    This is the screen that replaces typing a vendor name into a box. Each
    candidate carries the reasons it is blocked or worth a second look, so the
    reader decides with the same information the store will decide with when
    they click Invite.

    `as_of` is resolved here, at the boundary, and passed down — that is what
    keeps `workflow.bidders` a pure module with testable expiry boundaries.
    """
    store = _read()
    rfq = store.get_rfq(rfq_id)
    if rfq is None:
        raise HTTPException(status_code=404, detail=f"Unknown RFQ: {rfq_id}")

    today = date.today()
    shortlisted = {e.vendor_id for e in store.shortlist_for(rfq_id) if e.vendor_id}
    candidates = [
        {
            "bidder": _bidder_payload(store, bidder, today),
            "suitability": evaluate(bidder, rfq, today).model_dump(),
            "shortlisted": bidder.id in shortlisted,
        }
        for bidder in store.list_bidders()
    ]
    # Ordered here rather than on the screen, so every consumer sees the same
    # list and the ordering rule has one definition. Whoever can be invited
    # without an argument comes first; a blocked bidder is last because
    # inviting them is the exceptional act, not the default one. Name breaks
    # the tie, because insertion order into the registry means nothing to a
    # reader scanning for a company.
    candidates.sort(
        key=lambda c: (
            not c["suitability"]["eligible"],
            not c["suitability"]["scope_fit"],
            c["bidder"]["name"].casefold(),
        )
    )
    return {"candidates": candidates}


@router.post("/rfqs/{rfq_id}/shortlist", status_code=201)
def add_shortlist_entry(
    rfq_id: str, body: ShortlistEntryIn, user: User = Depends(current_user)
) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            entry = store.add_shortlist_entry(
                rfq_id,
                vendor_id=body.vendor_id,
                vendor_name=body.vendor_name,
                prequal_status=body.prequal_status,
                scope_code_fit=body.scope_code_fit,
                included=body.included,
                # Attributed only when there is something to attribute.
                override_by=user.email if body.override_reason else None,
                override_reason=body.override_reason,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IncompleteShortlistEntry as exc:
        # Caught before the `ValueError` it subclasses. This one is a malformed
        # request, not a refusal — the caller named neither a bidder nor a
        # vendor.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        # A blocked bidder invited with no override reason: both entities
        # exist and the request is well-formed. The workflow says no.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return entry.model_dump(mode="json")


@router.delete("/rfqs/{rfq_id}/shortlist/{entry_id}", status_code=204)
def remove_shortlist_entry(rfq_id: str, entry_id: str) -> None:
    try:
        with persistence.locked_update(_root()) as store:
            store.remove_shortlist_entry(rfq_id, entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        # The entry raised a clarification, so removing it would orphan a query.
        # Both entities exist and the request is well-formed — the workflow says
        # no, which is the same 409 every other refusal in this module answers.
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/rfqs/{rfq_id}/shortlist/approve")
def approve_shortlist(rfq_id: str, user: User = Depends(current_user)) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            store.approve_shortlist(rfq_id, by=user.email)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"approved_by": user.email}


@router.put("/rfqs/{rfq_id}/tbe-template")
def set_tbe_template(rfq_id: str, body: TbeTemplateIn) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            template = store.set_tbe_template(
                rfq_id,
                criteria=body.criteria,
                source_rfq_reference=body.source_rfq_reference,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return template.model_dump(mode="json")


@router.post("/rfqs/{rfq_id}/vdrl", status_code=201)
def add_vdrl_line(rfq_id: str, body: VdrlLineIn) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            line = store.add_vdrl_line(
                rfq_id,
                doc_code=body.doc_code,
                title=body.title,
                doc_type=body.doc_type,
                mandatory=body.mandatory,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return line.model_dump(mode="json")


@router.delete("/rfqs/{rfq_id}/vdrl/{line_id}", status_code=204)
def remove_vdrl_line(rfq_id: str, line_id: str) -> None:
    try:
        with persistence.locked_update(_root()) as store:
            store.remove_vdrl_line(rfq_id, line_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

# -- clarifications ----------------------------------------------------------
#
# Same shape as every other write in this module: the whole store method runs
# inside `locked_update`, so the reads that gate it and the write itself are one
# critical section, and a refusal leaves the document untouched.
#
# There is deliberately no GET for either collection. `get_rfq` already returns
# every artifact its gates read, and the Clarifications gate now reads both.


@router.post("/rfqs/{rfq_id}/queries", status_code=201)
def raise_query(rfq_id: str, body: QueryIn) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            query = store.raise_query(
                rfq_id,
                entry_id=body.raised_by_entry_id,
                question=body.question,
                category=body.category,
                raised_on=body.raised_on,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _query_payload(query)


@router.post("/rfqs/{rfq_id}/queries/{query_id}/answer")
def answer_query(
    rfq_id: str, query_id: str, body: AnswerIn, user: User = Depends(current_user)
) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            query = store.answer_query(
                rfq_id,
                query_id,
                answer=body.answer,
                by=user.email,
                restricted_reason=body.restricted_reason,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _query_payload(query)


@router.post("/rfqs/{rfq_id}/queries/{query_id}/withdraw")
def withdraw_query(
    rfq_id: str, query_id: str, body: WithdrawIn, user: User = Depends(current_user)
) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            query = store.withdraw_query(
                rfq_id, query_id, reason=body.reason, by=user.email
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _query_payload(query)


@router.post("/rfqs/{rfq_id}/addenda", status_code=201)
def draft_addendum(rfq_id: str, body: AddendumIn) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            addendum = store.draft_addendum(
                rfq_id,
                revision=body.revision,
                summary=body.summary,
                attachments=body.attachments,
                arising_from_query_ids=body.arising_from_query_ids,
                bid_due_date=body.bid_due_date,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _addendum_payload(addendum)


@router.patch("/rfqs/{rfq_id}/addenda/{addendum_id}")
def patch_addendum(rfq_id: str, addendum_id: str, body: AddendumPatch) -> dict:
    try:
        with persistence.locked_update(_root()) as store:
            addendum = store.update_addendum(
                rfq_id, addendum_id, body.model_dump(exclude_unset=True)
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _addendum_payload(addendum)


@router.post("/rfqs/{rfq_id}/addenda/{addendum_id}/issue")
def issue_addendum(
    rfq_id: str, addendum_id: str, body: IssueIn, user: User = Depends(current_user)
) -> dict:
    """The one sanctioned door through the frozen-package rule. Four reads gate
    it, all of them inside the store method this block runs."""
    try:
        with persistence.locked_update(_root()) as store:
            addendum = store.issue_addendum(rfq_id, addendum_id, by=user.email)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _addendum_payload(addendum)


@router.delete("/rfqs/{rfq_id}/addenda/{addendum_id}", status_code=204)
def delete_addendum(rfq_id: str, addendum_id: str) -> None:
    try:
        with persistence.locked_update(_root()) as store:
            store.delete_addendum(rfq_id, addendum_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
