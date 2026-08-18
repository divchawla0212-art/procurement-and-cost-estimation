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
import hashlib
import io
import os
import tempfile
import zipfile
from datetime import date, datetime, timezone
from typing import Annotated, NamedTuple, get_args

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from api.auth.deps import current_user
from api.auth.models import User
from workflow import clarifications, contact_db, persistence
from workflow import bidder_db, disciplines, doc_store, item_vendor_lists
from workflow.contact_import import parse_contacts_counted
from workflow.eligibility import assess as assess_eligibility
from workflow.models.rfq_document import EligibilityCategory, RfqDocument
from workflow.safe_extract import safe_destination, safe_relative_path
from workflow.avl_import import parse_avl
from workflow.bidders import (
    AVAILABLE_APPROVERS,
    CLIENT_APPROVER,
    effective_prequal,
    evaluate,
    missing_client_approval,
)
from workflow.gates import GateResult, check_gate
from workflow.models.bidder import Bidder, PrequalStatus
from workflow.models.clarification import Addendum, ClarificationQuery, QueryCategory
from workflow.models.draft_shortlist import DraftShortlistEntry, DraftShortlistSource
from workflow.models.project import (
    ItemVendorEntry,
    Item,
    ProjectStatus,
    VendorListSource,
)

#: All four sources, derived from the literal rather than restated, so the
#: payload cannot drift from what the store will accept.
VENDOR_LIST_SOURCES: tuple[str, ...] = get_args(VendorListSource)

#: How a curated row says it arrived. `source_document` on an uploaded row names
#: the export; on these it names the act, so every row on the item screen is
#: traceable however it got there. The `Suggested` sentence is the honest-label
#: rule again: these providers cannot browse, so a row a model named says so on
#: its face rather than posing as something found.
CURATED_PROVENANCE: dict[str, str] = {
    "Manual": "added by hand",
    "Suggested": "suggested by the model",
}
from workflow.models.rfq import Attachment, ShortlistEntry
from workflow.rfq_extractor import extract_rfq_info
from workflow.vendor_suggestions import suggest as suggest_vendors
from shared.llm.factory import get_client
from workflow.stages import STAGE_CODES, STAGE_ORDER, Stage
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


def _stage_codes() -> dict[str, str]:
    """Each stage's place in the client's process document, keyed by stage name
    exactly as `stage_counts` is.

    Sent for the same reason `selectable_approvers` and `client_approver` are:
    the browser must not spell the process's own vocabulary. It could not do so
    correctly here in any case — `RFQ-04A` follows no counter, and Negotiation
    and Awarded deliberately share `RFQ-06` — which is precisely what a strip
    numbering its own stages got wrong.
    """
    return {stage.value: STAGE_CODES[stage] for stage in STAGE_ORDER}


@router.get("/stages")
def get_stages() -> dict:
    return {"stages": [s.value for s in STAGE_ORDER], "stage_codes": _stage_codes()}


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
    items = store.items_for_project(project_id)
    return {
        "project": project.model_dump(mode="json"),
        "items": [i.model_dump(mode="json") for i in items],
        "rfqs": [r.model_dump(mode="json") for r in store.list_rfqs(project_id=project_id)],
        # Grouped by item and then by source, so the item screen draws a card
        # per source without regrouping a flat list itself. Sent with the
        # project rather than behind a route of its own: the item screen
        # already reads this payload, and a second fetch could disagree with
        # the items rendered beside it.
        "item_vendor_lists": {
            item.id: {
                source: [
                    e.model_dump(mode="json")
                    for e in store.item_vendor_list(item.id, source)
                ]
                for source in VENDOR_LIST_SOURCES
            }
            for item in items
        },
        # The vendors picked against each item before any RFQ covers it. Flat
        # per item rather than grouped by source: the buyer picked one basket,
        # and `source` on each row says where each came from. Every item gets a
        # key, so the browser reads a list rather than guarding for a missing
        # one on every render.
        "draft_shortlists": {
            item.id: [
                e.model_dump(mode="json") for e in store.draft_shortlist(item.id)
            ]
            for item in items
        },
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


@router.post("/projects/{project_id}/items/{item_id}/vendor-list")
def upload_item_vendor_list(
    project_id: str,
    item_id: str,
    source: VendorListSource,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
) -> dict:
    """Read an Approved Vendor List export and store it as this item's list.

    **No new parser.** `avl_import.parse_avl` reads both the client export and
    the Astra subset unmodified: same eight headers, same first sheet, same
    `bdr_<vendor number>` keys. Which of the two an upload is comes from
    `source` rather than from the file, because the Astra list is a subset *of*
    the client's and so carries nothing in it that says whose list it is.

    **Nothing here touches the registry.** These are genuine exports, so the
    objection is not that the evidence is weak — it is that a write attached to
    one item would otherwise decide what a company is approved for across every
    project, from a form whose subject is one line of equipment. `approved_by`
    moves only through `python -m workflow.avl_db`.

    The parse happens outside the lock (it is pure CPU over a temporary file
    and can take a second on an 18 000-row export), and everything that reads
    or writes the store happens inside it.
    """
    if not file.filename:
        raise HTTPException(status_code=422, detail="The upload has no file name.")

    # `parse_avl` takes a path, so the bytes land in a temporary file that is
    # removed either way. Nothing is kept: the entries are the record, and the
    # document itself is not stored — the same rule `/rfqs/extract` keeps.
    suffix = os.path.splitext(file.filename)[1] or ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as scratch:
        scratch.write(file.file.read())
        scratch_path = scratch.name
    try:
        parsed = parse_avl(scratch_path)
    except Exception as exc:
        # The parser's own sentence, which names the missing header. A refusal
        # that does not say what is wrong with the file leaves the reader
        # nothing to act on.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        os.unlink(scratch_path)

    try:
        with persistence.locked_update(_root()) as store:
            item = store.get_item(item_id)
            if item is None or item.project_id != project_id:
                raise KeyError(f"Unknown item: {item_id}")
            entries = item_vendor_lists.entries_for(
                parsed,
                item_id=item_id,
                discipline=item.discipline,
                source=source,
                known_ids={b.id for b in store.list_bidders()},
                uploaded_by=user.email,
                uploaded_at=datetime.now(timezone.utc),
                source_document=file.filename,
            )
            stored = store.set_item_vendor_list(item_id, source, entries)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc

    return {
        "entries": [e.model_dump(mode="json") for e in stored],
        # Reported rather than left to be inferred from the table: an empty
        # list after a 1 346-vendor upload is a narrowing that found nobody,
        # not a failed read, and only the counts can say which.
        "summary": {
            "parsed": len(parsed),
            "kept": len(stored),
            "linked": sum(1 for e in stored if e.vendor_id is not None),
        },
    }


class ItemVendorIn(BaseModel):
    """One vendor a person is putting on an item's list.

    There is no `vendor_id` on this body, and the route never derives one. See
    `add_item_vendor`.

    `source` defaults to `Manual` because typing a name is the common case; a
    row accepted off the suggestion card sends `Suggested` so it stays labelled
    as one. The uploaded sources are refused, by the store rather than here.
    """

    vendor_name: str
    source: VendorListSource = "Manual"
    trade_categories: list[str] | None = None
    note: str | None = None


@router.post("/projects/{project_id}/items/{item_id}/vendors", status_code=201)
def add_item_vendor(
    project_id: str,
    item_id: str,
    body: ItemVendorIn,
    user: User = Depends(current_user),
) -> dict:
    """Put one vendor on this item's list, by hand or off the suggestion card.

    **`vendor_id` is always `None`, and the name is never looked up in the
    registry.** Not an omission: a match here would silently attach a real
    company's approvals to whatever somebody typed, and this repository has
    twice recorded name matching as a shipped defect. If the vendor really is in
    the registry, the available-vendor card is where to find them — and that
    card links by id, which is a fact rather than a guess.

    **Nothing here creates a bidder or grants an approval.** The registry is
    organisation-wide and arrives whole from its own import; a write attached to
    one line of equipment must not enrol a company across every project.

    `note` rides on `source_document` rather than on a new field, because a note
    here is *why this row exists*, which is what `source_document` already
    means. `ItemVendorEntry` gains nothing this phase — a hand-added company is
    already exactly what that record describes.
    """
    name = body.vendor_name.strip()
    if not name:
        # A vendor with no name cannot be invited later, so storing one would
        # put a row on screen that no action can be taken on.
        raise HTTPException(status_code=422, detail="A vendor needs a name.")

    try:
        with persistence.locked_update(_root()) as store:
            item = _owned_item(store, project_id, item_id)
            # Falls back to the source's own name for an uploaded source, which
            # never lands: `add_item_vendor_entry` refuses it below, and the
            # refusal is defined there once rather than restated here.
            provenance = CURATED_PROVENANCE.get(body.source, body.source)
            note = (body.note or "").strip()
            stored = store.add_item_vendor_entry(item_id, ItemVendorEntry(
                item_id=item_id,
                source=body.source,
                vendor_id=None,
                vendor_name=name,
                # The item's own scope, so a curated row sits in the same shape
                # as an uploaded one: "on this item's list, for this trade".
                trade_categories=(
                    body.trade_categories
                    if body.trade_categories is not None
                    else list(disciplines.product_groups(item.discipline))
                ),
                uploaded_by=user.email,
                uploaded_at=datetime.now(timezone.utc),
                source_document=f"{provenance}: {note}" if note else provenance,
            ))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        # The store's sentence, which names the source and says what would work
        # instead — re-uploading the corrected export.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return stored.model_dump(mode="json")


class DraftShortlistIn(BaseModel):
    """One vendor a buyer is picking against an item, out of the four-source
    pool on the item screen.

    `source` is a `DraftShortlistSource`, not a `VendorListSource`: it names the
    chip the buyer was looking at, and `Client` — the uploaded export's name for
    the client's own list — is deliberately not a member. A wrong value is a 422
    from pydantic rather than a stored source no screen renders.

    There is no `added_by` on this body. Attribution comes from the session, so
    a screen cannot name somebody else as the picker.
    """

    vendor_name: str
    source: DraftShortlistSource
    vendor_id: str | None = None


@router.post(
    "/projects/{project_id}/items/{item_id}/draft-shortlist", status_code=201
)
def add_draft_shortlist_pick(
    project_id: str,
    item_id: str,
    body: DraftShortlistIn,
    user: User = Depends(current_user),
) -> dict:
    """Pick a vendor against this item, before any RFQ covers it.

    Picking the same vendor twice is a **no-op that returns the row already
    there**, so a browser firing one call per ticked row cannot produce
    duplicates by double-submitting. The guard itself lives in the store, inside
    this `locked_update` — a check made here and a write made there would be two
    critical sections, and two concurrent picks would each read "not there yet".

    **`vendor_id` is taken from the body and never derived from the name.** A
    registry row is picked by id because the pool knew its id; a curated row has
    none, and looking one up would attach a real company's approvals to whatever
    somebody typed. Nothing here creates a bidder or grants an approval.
    """
    name = body.vendor_name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="A vendor needs a name.")

    try:
        with persistence.locked_update(_root()) as store:
            _owned_item(store, project_id, item_id)
            stored = store.add_draft_shortlist_entry(item_id, DraftShortlistEntry(
                item_id=item_id,
                vendor_id=body.vendor_id,
                vendor_name=name,
                source=body.source,
                added_by=user.email,
                added_at=datetime.now(timezone.utc).isoformat(),
            ))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    return stored.model_dump(mode="json")


@router.delete(
    "/projects/{project_id}/items/{item_id}/draft-shortlist/{entry_id}",
    status_code=204,
)
def remove_draft_shortlist_pick(project_id: str, item_id: str, entry_id: str) -> None:
    """Unpick a vendor, **by id** — two suppliers can share a trading name, and
    the pool re-sorts under its own search."""
    try:
        with persistence.locked_update(_root()) as store:
            _owned_item(store, project_id, item_id)
            store.remove_draft_shortlist_entry(item_id, entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc


@router.delete(
    "/projects/{project_id}/items/{item_id}/vendors/{entry_id}", status_code=204
)
def remove_item_vendor(project_id: str, item_id: str, entry_id: str) -> None:
    """Take one curated vendor off this item's list, **by id**.

    An uploaded row is refused, by the store: it is part of a document, and
    correcting it means re-uploading the corrected export rather than editing
    the copy. By id and never by name, because two suppliers can share a
    trading name.
    """
    try:
        with persistence.locked_update(_root()) as store:
            _owned_item(store, project_id, item_id)
            store.remove_item_vendor_entry(item_id, entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/projects/{project_id}/items/{item_id}/vendor-suggestions")
def suggest_item_vendors(project_id: str, item_id: str) -> dict:
    """Companies a model believes supply this item. **Stores nothing.**

    Same shape as `/rfqs/extract`, and for the same reason: a suggestion the
    reader rejects should leave nothing behind, and one they accept should be
    recorded as *their* act through `add_item_vendor` — one vendor at a time,
    with their name on it. There is deliberately no bulk accept.

    **This is not a web search.** `shared/llm/` has no crawler and no provider
    search tool, so what comes back is what the model recalls from training.
    `workflow/vendor_suggestions.py` says so at length; the screen says so to
    the reader; and nothing in the response is dressed up as a lookup.

    Read-only, so `_owned_item` runs against a loaded store rather than inside
    `locked_update` — there is no write for it to gate.

    A provider failure is a 502 and never an empty list: an outage and "no such
    companies exist" must not look the same on screen.
    """
    store = _read()
    item = _owned_item(store, project_id, item_id)
    # Every source, uploaded and curated alike. The reader's question is "who
    # else", not "who else that I did not upload".
    exclude = [e.vendor_name for e in store.item_vendor_list(item_id)]
    try:
        found = suggest_vendors(
            get_client(),
            discipline=item.discipline,
            description=f"{item.item_type} — {item.description}",
            exclude=exclude,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"The model could not be asked for vendors: {exc}",
        ) from exc
    return {"vendors": [v.model_dump(mode="json") for v in found]}


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


def _shortlist_payload(store: WorkflowStore, entry: ShortlistEntry) -> dict:
    """One shortlist row, plus whether the client has approved that vendor.

    Derived on read, and deliberately not a field on `ShortlistEntry` — the
    same rule, and the same reason, as `approval_caution` on `Bidder`: a copy
    taken when the invitation was issued is wrong the moment `approved_by` is
    corrected, and correcting it is the common case.

    `prequal_status` on this very row *is* a snapshot, on purpose, and the two
    sitting side by side is not an inconsistency. That column records what was
    known when the invitation went out, which is what makes the row an audit
    trail; this one answers who is approved now.

    Three states, not two, and both derived keys below are keyed on the same
    `bidder is None`. `None` is a vendor typed in by hand: there is no registry
    row, so nothing says they are off the client's list — only that we cannot
    tell. Coercing that to `False` would put a finding on screen that nobody
    made, which is the same mistake as an unfound parameter stored as `0`. A
    `vendor_id` pointing at a deleted bidder lands here too, and for the same
    reason it stays unknown rather than becoming a refusal.

    `approved_by` is sent **as well as**, never instead of, `client_approved`.
    The boolean carries the *rule* — `missing_client_approval` is its one
    definition — and the list carries *identity*. Astra approval is not a
    function of the boolean, so a screen wanting both approvals has to be sent
    both; and rebuilding the client-approval rule in the browser from the list
    would be the second definition of it this repository forbids.
    """
    bidder = store.get_bidder(entry.vendor_id) if entry.vendor_id else None
    return {
        **entry.model_dump(mode="json"),
        "client_approved": (
            None if bidder is None else missing_client_approval(bidder) is None
        ),
        "approved_by": None if bidder is None else list(bidder.approved_by),
        # Derived on read, like the two keys above and for the same reason: a
        # copy taken at invitation is wrong the moment the directory is
        # corrected, and correcting it is the common case. Keyed on the
        # **name**, not `vendor_id` — the directory holds no registry link, so
        # a hand-typed row resolves exactly as a registry-linked one does.
        # Two states: a list, or `None` for no contact held. Never `[]`.
        "email": store.emails_for(entry.vendor_name),
    }


@router.post("/vendor-contacts")
def upload_vendor_contacts(
    file: UploadFile = File(...),
    user: User = Depends(current_user),
) -> dict:
    """Replace the organisation-wide vendor contact directory from a sheet.

    **Organisation-wide, not per-RFQ**, even though the control that calls this
    sits on one RFQ's Shortlisting step. The screen says so; this docstring is
    the other half of saying it.

    Parse outside the lock, write inside it — parsing is pure CPU over a file
    and holding the store lock through it blocks every other writer. The
    workbook is not kept: the entries are the record and `source_document`
    holds the file name, the rule `/vendor-list` and `/rfqs/extract` keep.

    **Nothing here touches the registry.** A sheet of addresses must not enrol
    a company or grant an approval; `approved_by` moves only through
    `python -m workflow.avl_db`.
    """
    if not file.filename:
        raise HTTPException(status_code=422, detail="The upload has no file name.")

    suffix = os.path.splitext(file.filename)[1] or ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as scratch:
        scratch.write(file.file.read())
        scratch_path = scratch.name
    try:
        parsed, rows_read = parse_contacts_counted(
            scratch_path,
            uploaded_by=user.email,
            uploaded_at=datetime.now(timezone.utc),
            source_document=file.filename,
        )
    except ValueError as exc:
        # The parser's own sentence, which names the missing column or the
        # offending row. A refusal that does not say what is wrong with the
        # file leaves the reader nothing to act on.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        # A file that is not a workbook at all — openpyxl raises its own
        # exception types for a corrupt or non-xlsx upload, not ValueError.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        os.unlink(scratch_path)

    with persistence.locked_update(_root()) as store:
        stored = store.set_vendor_contacts(parsed)
        # Informational only, computed here and kept nowhere: nothing else in
        # the system consults it and no stored field is written from it. An
        # upload that matches nobody is almost always a spelling problem, and
        # a bare "8 rows stored" hides that.
        registered = {contact_db.fold(b.name) for b in store.list_bidders()}
        matched = sum(
            1 for c in store.vendor_contacts()
            if contact_db.fold(c.vendor_name) in registered
        )
        contacts = [c.model_dump(mode="json") for c in store.vendor_contacts()]
        addresses = sum(len(c.emails) for c in store.vendor_contacts())

    return {
        "contacts": contacts,
        "summary": {
            "parsed": rows_read, "stored": stored,
            "addresses": addresses, "matched": matched,
        },
    }


@router.get("/vendor-contacts")
def read_vendor_contacts() -> dict:
    """The whole directory, plus who loaded it and when.

    The provenance rides alongside so the screen can say *organisation-wide,
    uploaded by … on …* — a control that sits inside one RFQ while writing a
    shared directory has to say which it is.
    """
    store = _read()
    contacts = store.vendor_contacts()
    latest = max(contacts, key=lambda c: c.uploaded_at, default=None)
    return {
        "contacts": [c.model_dump(mode="json") for c in contacts],
        "count": len(contacts),
        "uploaded_by": latest.uploaded_by if latest else None,
        "uploaded_at": latest.uploaded_at.isoformat() if latest else None,
        "source_document": latest.source_document if latest else None,
    }


@router.get("/bidders")
def list_bidders() -> dict:
    store = _read()
    today = date.today()
    return {"bidders": [_bidder_payload(store, b, today) for b in store.list_bidders()]}


@router.get("/disciplines")
def list_disciplines() -> dict:
    """The disciplines an item may be scoped to.

    Served rather than hardcoded in the browser for the same reason the
    approver is: `workflow/disciplines.py` is the one definition, and adding a
    third family should be an edit there and nowhere else.

    `product_groups` rides along so a screen can show what a discipline covers
    without asking for the whole export's vocabulary.
    """
    return {
        "disciplines": [
            {"name": name, "product_groups": list(groups)}
            for name, groups in disciplines.DISCIPLINES.items()
        ]
    }


@router.get("/bidders/available")
def list_available_bidders(
    discipline: str | None = None,
    approver: Annotated[list[str] | None, Query()] = None,
) -> dict:
    """The vendors that may actually be invited: on the client's list *and* on
    ours.

    Declared above `/bidders/{bidder_id}`: FastAPI matches in declaration
    order, so the literal path has to come first or "available" is read as a
    bidder id and every request 404s.

    Narrower than "ADNOC-approved" on purpose. The client's approval says they
    may be used on an ADNOC project; ours says we have qualified them. A screen
    offering the first as though it were the second would put vendors in front
    of a buyer that procurement has never assessed.

    `discipline` narrows to one family and is optional; absent means the whole
    available list, never none of it.

    `approver` is which approvals a vendor must carry — **AND, never OR**, the
    same rule `bidders.available` states, and repeatable. Absent means
    `AVAILABLE_APPROVERS`, so the default stays the server's to decide rather
    than something the browser has to know and send. Narrowing it to one is how
    a caller asks for the client's whole list, which is far larger than the
    intersection and had no route to it before.
    """
    # Blanks dropped first: `?approver=` arrives as `[""]`, and letting that
    # reach the unknown check below would answer with a message naming an empty
    # string instead of the one the caller needs.
    approvers = (
        list(AVAILABLE_APPROVERS)
        if approver is None
        else [a for a in approver if a.strip()]
    )
    if not approvers:
        raise HTTPException(
            status_code=422, detail="At least one approver is required."
        )
    unknown = [a for a in approvers if a not in AVAILABLE_APPROVERS]
    if unknown:
        # Refused, never answered with an empty list. `approved_by_all` on a
        # typo returns nobody, and an empty table reads on screen as "no vendor
        # qualifies" — a finding nobody made, which is the same mistake as an
        # unfound parameter stored as `0`.
        raise HTTPException(
            status_code=422,
            detail=f"Unknown approver(s): {', '.join(unknown)}.",
        )

    groups = (
        list(disciplines.product_groups(discipline))
        if (discipline or "").strip()
        else None
    )
    # Straight to the database: this read wants the registry and nothing else,
    # and the indexes on the folded approver and product group are what make it
    # a query rather than a scan of 1 346 objects.
    rows = bidder_db.approved_by_all(_root(), approvers, groups)

    store = _read()
    today = date.today()
    return {
        # The approvals actually applied, so the caption follows what was asked
        # for rather than always naming both.
        "approvers": approvers,
        # Every approval that may be asked for, sent rather than spelled out in
        # the browser — one constant, so a second client's AVL stays a one-line
        # change here.
        "selectable_approvers": list(AVAILABLE_APPROVERS),
        "discipline": discipline,
        "total": len(rows),
        "bidders": [_bidder_payload(store, b, today) for b in rows],
    }


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

    `stage_codes` rides along for the same reason the counts do: the strip
    renders both beside each stage name, and neither is something a screen can
    work out for itself.
    """
    rfqs = _read().list_rfqs(project_id=project_id)
    return {
        "rfqs": [r.model_dump(mode="json") for r in rfqs],
        "stages": [s.value for s in STAGE_ORDER],
        "stage_counts": {s.value: sum(1 for r in rfqs if r.stage is s) for s in STAGE_ORDER},
        "stage_codes": _stage_codes(),
    }


@router.post("/rfqs", status_code=201)
def create_rfq(body: RfqIn) -> dict:
    """Raise an RFQ, and take the draft shortlists of the items it covers.

    Adoption is not a second step and has no screen of its own: the buyer
    assembled that selection against the item for exactly this moment, and an
    RFQ raised over an item with picks that then showed an empty shortlist
    would read as the picks having been lost.

    Inside the same `locked_update` as the creation, so an RFQ never exists
    with its adoption half-done. `adopt_draft_shortlist` is idempotent and
    leaves the draft alone — the item may be covered again later.

    **A pick adoption could not invite is reported, never dropped.** A buyer
    who picked a suspended vendor would otherwise get a shortlist quietly one
    row short. `shortlist_skipped` carries each one with the refusal's own
    sentence — the same shape the item screen already renders beside a row it
    could not shortlist — and `shortlist_adopted` counts what did land, so
    "nobody was picked" and "everybody was refused" are different answers.
    """
    skipped: list[dict] = []
    adopted = 0
    try:
        with persistence.locked_update(_root()) as store:
            rfq = store.create_rfq(**body.model_dump())
            for item_id in rfq.item_ids:
                result = store.adopt_draft_shortlist(rfq.id, item_id)
                adopted += result.added
                skipped.extend(
                    {"vendor_name": s.vendor_name, "reason": s.reason}
                    for s in result.skipped
                )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        **rfq.model_dump(mode="json"),
        "shortlist_adopted": adopted,
        "shortlist_skipped": skipped,
    }


@router.post("/rfqs/extract")
def extract_rfq_document(file: UploadFile = File(...)) -> dict:
    """The RFQ fields read out of an uploaded document, for the form to fill in.

    Declared above `/rfqs/{rfq_id}` for the same reason `/bidders/approved` is:
    a literal segment that arrives after a path parameter is read as a value
    for it. Nothing is stored — the answer goes back to the browser, and the
    RFQ is only created when the user submits the form, so an extraction the
    reader disagrees with is corrected before anything lands.
    """
    if not file.filename:
        raise HTTPException(status_code=422, detail="The upload has no file name.")
    return extract_rfq_info(file.filename, file.file.read())


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
        "shortlist": [_shortlist_payload(store, e) for e in store.shortlist_for(rfq_id)],
        "shortlist_approved": store.is_shortlist_approved(rfq_id),
        # Sent rather than spelled out in the browser, for the same reason
        # `/bidders/approved` sends it: a column headed "ADNOC" in the front end
        # would be a second place that has to change for a second client.
        "client_approver": CLIENT_APPROVER,
        # The files themselves, both halves of the enquiry in one list — the
        # bytes are on disk, and `submitted_by_vendor_id` says who put them
        # there. `document_categories` rides alongside for the same reason
        # `selectable_approvers` does: the upload card builds its picker
        # without spelling the vocabulary itself.
        "documents": [_document_payload(d) for d in store.rfq_documents(rfq_id)],
        "document_categories": [c.value for c in EligibilityCategory],
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


# -- the documents an RFQ carries --------------------------------------------


class _Pending(NamedTuple):
    """One file about to be stored, with its name already through the guard."""

    rel_path: str
    filename: str
    content_type: str | None
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def _expand(files: list[UploadFile], paths: list[str] | None, base: str) -> list[_Pending]:
    """Turn an upload into the files that will actually be stored.

    Three shapes arrive at one route. A plain multi-select sends files and no
    paths. A folder sends files plus their `webkitRelativePath`s, positional —
    which is why a `paths` list of the wrong length is refused rather than
    zipped short: a path attached to the wrong file is worse than no path.
    A `.zip` is expanded and each member stored individually, so what lands is
    the package rather than an archive nobody can look inside.

    **Every name goes through `workflow.safe_extract`**, the same guard
    `procurement/project.py` runs archive members through — a zip member and a
    browser-supplied relative path are the same risk arriving through two
    doors, and a second implementation of the check is the one that does not
    get fixed. Each refusal names the offending entry.

    Nothing is written here. The whole upload is materialised first so that one
    bad member refuses the lot: half an enquiry package on disk, with no record
    of the other half, is worse than a refusal the uploader can act on.
    """
    if not files:
        raise HTTPException(status_code=422, detail="The upload carried no files.")
    if paths is not None and len(paths) != len(files):
        raise HTTPException(
            status_code=422,
            detail=(
                f"{len(files)} files arrived with {len(paths)} paths. They are "
                f"positional, so a short list would attach a path to the wrong file."
            ),
        )

    pending: list[_Pending] = []
    for index, upload in enumerate(files):
        name = upload.filename or ""
        if not name.strip():
            raise HTTPException(status_code=422, detail="A file arrived with no name.")
        payload = upload.file.read()
        declared = paths[index] if paths else name

        if name.lower().endswith(".zip"):
            pending.extend(_expand_archive(name, payload, base))
            continue

        try:
            rel_path = safe_relative_path(declared, what="path")
            _guarded(base, rel_path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        pending.append(
            _Pending(
                rel_path=rel_path,
                filename=rel_path.rsplit("/", 1)[-1],
                content_type=upload.content_type,
                data=payload,
            )
        )
    return pending


def _guarded(base: str, rel_path: str) -> None:
    """Resolve the name under the RFQ's own directory and refuse an escape.

    The blob itself is stored by digest, not under this path, so this is not
    where containment is enforced — `doc_store` builds its paths out of leaves
    and cannot be escaped. It is enforced anyway, because the name is about to
    be recorded and shown, and a `rel_path` reading `../../etc/passwd` in a
    document register is a finding whether or not anything acted on it.
    """
    safe_destination(base, rel_path, what="path")


def _expand_archive(archive_name: str, payload: bytes, base: str) -> list[_Pending]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            members = []
            for info in zf.infolist():
                if info.is_dir():
                    continue
                rel_path = safe_relative_path(info.filename, what="path in archive")
                _guarded(base, rel_path)
                members.append(
                    _Pending(
                        rel_path=rel_path,
                        filename=rel_path.rsplit("/", 1)[-1],
                        # A zip carries no per-member content type, and
                        # guessing one from the extension would put a claim on
                        # the record that nobody made.
                        content_type=None,
                        data=zf.read(info),
                    )
                )
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=422, detail=f"{archive_name} is not a readable zip archive."
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return members


def _document_payload(document: RfqDocument) -> dict:
    return document.model_dump(mode="json")


def _store_documents(
    root: str,
    rfq_id: str,
    pending: list[_Pending],
    *,
    category: EligibilityCategory | None,
    uploaded_by: str,
    uploaded_at: str,
) -> list[RfqDocument]:
    """Write the blobs and the records as one unit.

    Inside `locked_update`, and that is deliberate even though writing bytes
    under a lock is not free: the freeze rule that refuses this write lives in
    the store, so checking it outside would be the two-critical-sections
    mistake this module names at the top.

    A refusal partway through unwinds the blobs **this call** created.
    `locked_update` writes nothing on a raise, so the records are gone already,
    and a blob with no record naming it is exactly the orphan I-D forbids. A
    blob an earlier record already shares is left where it is — the same rule
    the delete path keeps, arriving from the other direction.
    """
    blobs = doc_store.LocalBlobStore(root)
    stored: list[RfqDocument] = []
    created: list[doc_store.BlobRef] = []
    try:
        with persistence.locked_update(root) as store:
            if store.get_rfq(rfq_id) is None:
                raise KeyError(f"Unknown RFQ: {rfq_id}")
            for item in pending:
                # Whether this call is the one that puts the bytes on disk,
                # asked the way the delete path asks it: a blob is shared when
                # some record already names the same digest and leaf.
                shared = any(
                    (d.sha256, d.filename) == (item.sha256, item.filename)
                    for d in store.rfq_documents(rfq_id)
                )
                ref = blobs.put(rfq_id, item.filename, io.BytesIO(item.data))
                if not shared:
                    created.append(ref)
                stored.append(
                    store.add_rfq_document(
                        RfqDocument(
                            rfq_id=rfq_id,
                            filename=item.filename,
                            rel_path=item.rel_path,
                            sha256=ref.sha256,
                            size_bytes=ref.size,
                            content_type=item.content_type,
                            category=category,
                            uploaded_by=uploaded_by,
                            uploaded_at=uploaded_at,
                            # The contractor's own. A vendor's submission
                            # arrives through the bid door, which is a later
                            # phase; nothing here may claim to be one.
                            submitted_by_vendor_id=None,
                        )
                    )
                )
    except BaseException:
        for ref in created:
            blobs.delete(ref)
        raise
    return stored


@router.post("/rfqs/{rfq_id}/documents", status_code=201)
def upload_rfq_documents(
    rfq_id: str,
    files: list[UploadFile] = File(...),
    paths: list[str] | None = Form(None),
    category: EligibilityCategory | None = Form(None),
    user: User = Depends(current_user),
) -> dict:
    """Store real files against an RFQ: several at once, a folder, or a zip.

    Unlike every other upload route in this module, this one **keeps the
    bytes** — an enquiry package is the record, not a form to fill in.

    Two halves, and the split is where the work belongs: `_expand` turns the
    request into files with guarded names and touches nothing, and
    `_store_documents` writes them inside the lock. So an upload that names an
    unsafe member is refused before a single byte lands.
    """
    root = _root()
    pending = _expand(files, paths, doc_store.rfq_dir(root, rfq_id))
    try:
        stored = _store_documents(
            root,
            rfq_id,
            pending,
            category=category,
            uploaded_by=user.email,
            uploaded_at=datetime.now(timezone.utc).isoformat(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        # A frozen package refusing an edit: the RFQ exists and the request is
        # well-formed, the workflow simply says no.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"documents": [_document_payload(d) for d in stored]}


@router.delete("/rfqs/{rfq_id}/documents/{document_id}", status_code=204)
def remove_rfq_document(rfq_id: str, document_id: str) -> None:
    """Drop one document, and its blob **only when no record still shares it**.

    Content addressing means two records legitimately point at one file, so
    the store answers whether the blob is now unreferenced and this route acts
    on that answer. Deciding it here would be a check in the caller and a write
    in the store — two critical sections, which is the rule this module opens
    with.
    """
    try:
        with persistence.locked_update(_root()) as store:
            document, orphaned = store.remove_rfq_document(rfq_id, document_id)
            if orphaned:
                doc_store.LocalBlobStore(_root()).delete(
                    doc_store.blob_ref(
                        document.rfq_id,
                        document.sha256,
                        document.size_bytes,
                        document.filename,
                    )
                )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        # A frozen package refusing an edit.
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/rfqs/{rfq_id}/bidders/{vendor_id}/eligibility")
def bidder_eligibility(rfq_id: str, vendor_id: str) -> dict:
    """One bidder's checklist against this RFQ's enquiry package.

    Read-only and computed at call time — `workflow.eligibility` is why the
    verdict is never stored. The split into `issued` and `submitted` happens
    here, at the boundary, so the pure module never has to know what a
    document record looks like beyond its two fields.

    An unknown `vendor_id` is not a 404. This route never asks the bidder
    registry whether the id is real, and a bidder who submitted nothing is
    straightforwardly missing everything mandatory — reporting that as a
    verdict rather than a special case is the same "computed at read time,
    never stored" shape every other derived value in this module keeps.

    This is deliberately the only eligibility route this task adds. The
    aggregate Vendor List screen — one row per shortlisted bidder — is a later
    task (T10 / BD-11); this route exists so `eligibility.assess` has one real
    caller and one slice of coverage at the HTTP boundary, not so a screen can
    use it yet.
    """
    store = _read()
    if store.get_rfq(rfq_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown RFQ: {rfq_id}")

    documents = store.rfq_documents(rfq_id)
    issued = [d for d in documents if d.submitted_by_vendor_id is None]
    submitted = [d for d in documents if d.submitted_by_vendor_id == vendor_id]
    return assess_eligibility(issued, submitted).model_dump(mode="json")


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
