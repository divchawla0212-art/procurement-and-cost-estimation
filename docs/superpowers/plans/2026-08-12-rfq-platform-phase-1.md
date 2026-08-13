# RFQ Platform Phase 1 — Process Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the eight-stage RFQ workflow real end to end — projects, items, stage transitions with enforced gates, and the artifacts each gate depends on — running on the existing in-memory seed store with no new infrastructure.

**Architecture:** Extend the existing FastAPI backend. `models.py` becomes a package so the ~15 new workflow entities do not bloat one file. New workflow logic lives in `app/workflow/` — a pure state machine (`stages.py`), gate predicates (`gates.py`), and an in-memory store (`store.py`) that mirrors the existing `app/store.py` pattern. Behaviour lives in services, not on models; models stay pure Pydantic data. The frontend widens the RFQ stage strip from five statuses to the eight workflow stages.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest. Next.js 16 (App Router), React 19, TypeScript, Tailwind 4.

## Global Constraints

- **No new infrastructure.** No database, no ORM, no migrations, no cloud services. Seed JSON + in-memory store only.
- **Stage codes are fixed:** `Scoping → Shortlisting → Issued → Clarifications → Bids Received → Evaluation → Negotiation → Awarded → PO Issued`. Nine enum members covering the eight process stages (RFQ-06 spans Negotiation and Awarded).
- **All references bind to immutable IDs, never to names** (spec P-R3). Project names are mutable; IDs are not.
- **Deny-by-default on transitions:** a transition not explicitly listed in `TRANSITIONS` is rejected.
- **Every gate failure returns a reason string.** No bare booleans — a blocked transition must say what is blocking it.
- **Backward transitions preserve the prior attempt** (spec C5-R4). Never overwrite; append to history.
- Existing tests in `backend/tests/` must continue to pass except where a task explicitly updates one.
- Python: 4-space indent, type hints on all public functions, no docstring required on private helpers.
- Run tests from `backend/` with `python -m pytest`.

---

## Plan Set

This is **plan 1 of 5**. Each phase produces working, testable software on its own.

| Plan | Phase | Scope |
| --- | --- | --- |
| **1 (this)** | Process model | Projects, items, eight-stage workflow, gates, artifacts |
| 2 | Persistence & identity | RDS, S3, Entra SSO, project/item scoping, vendor database, portal |
| 3 | Ingestion & retrieval | Textract, ACL-tagged chunks, hybrid retrieval |
| 4 | Agents | TQ drafter → bid intake → spec compliance → dispatch → screening → discovery |
| 5 | Governance & eval | KMS bid isolation, injection defences, eval harness, audit surface |

---

## File Structure

**Created:**

| File | Responsibility |
| --- | --- |
| `backend/app/models/__init__.py` | Re-exports every model so existing imports keep working |
| `backend/app/models/seed.py` | The current `models.py` contents, moved verbatim |
| `backend/app/models/project.py` | `Project`, `Item`, `ItemType` |
| `backend/app/models/workflow.py` | `RfqRecord`, `TechnicalPackage`, `ShortlistEntry`, `TbeTemplate`, `VdrlLine`, `ReturnablesChecklist`, `StageTransition` |
| `backend/app/models/bid.py` | `Bid`, `VdrlReceipt`, `BidShortlist` |
| `backend/app/workflow/__init__.py` | Package marker |
| `backend/app/workflow/stages.py` | `Stage` enum, `TRANSITIONS` table — pure data, no I/O |
| `backend/app/workflow/gates.py` | Gate predicates: one function per gated transition |
| `backend/app/workflow/store.py` | In-memory workflow store: projects, items, RFQs, artifacts |
| `backend/app/routers/workflow.py` | `/api/workflow/*` endpoints |
| `backend/tests/test_workflow_stages.py` | State machine and gate tests |
| `backend/tests/test_workflow_store.py` | Store, rename safety, history preservation |
| `backend/tests/test_workflow_endpoints.py` | API tests |
| `frontend/components/rfq/StageStrip.tsx` | Eight-stage progress strip |

**Modified:**

| File | Change |
| --- | --- |
| `backend/app/models.py` | Deleted — replaced by the package |
| `backend/app/main.py:5,20` | Register the workflow router |
| `backend/app/routers/procurement.py:9` | `STAGES` widens from 5 to 9 |
| `backend/tests/test_endpoints.py:128` | Updated stage assertion |
| `frontend/app/(shell)/procurement/rfq-status/page.tsx` | Use `StageStrip` |
| `frontend/lib/types.ts` | Workflow types |

---

## Task 1: Split models into a package

Pure refactor. No behaviour changes. This exists so the ~15 new entities in later tasks land in focused files instead of one 400-line module.

**Files:**
- Create: `backend/app/models/__init__.py`
- Create: `backend/app/models/seed.py`
- Delete: `backend/app/models.py`
- Test: `backend/tests/test_models_package.py`

**Interfaces:**
- Consumes: nothing
- Produces: `app.models` package exporting `Activity`, `CostLine`, `SCurvePoint`, `Rfq`, `Tq`, `PoDocument`, `Revision`, `EngDocument`, `ReviewComment`, `KbEntry`, `HseQuality`, `SeedData`, `Discipline` — identical names and shapes to before

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_models_package.py`:

```python
def test_every_seed_model_importable_from_package_root():
    from app.models import (
        Activity, CostLine, SCurvePoint, Rfq, Tq, PoDocument, Revision,
        EngDocument, ReviewComment, KbEntry, HseQuality, SeedData,
    )
    assert SeedData.model_fields.keys() >= {
        "schedule", "costs", "scurve", "rfqs", "tqs", "po_documents",
        "engineering_documents", "review_comments", "knowledge_base", "hse_quality",
    }

def test_seed_models_also_importable_from_submodule():
    from app.models.seed import Rfq as RfqFromSubmodule
    from app.models import Rfq as RfqFromRoot
    assert RfqFromSubmodule is RfqFromRoot
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_models_package.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.seed'`

- [ ] **Step 3: Perform the split**

Create directory `backend/app/models/`. Move the entire current contents of `backend/app/models.py` into `backend/app/models/seed.py` **unchanged** — do not edit a single line of it. Then delete `backend/app/models.py`.

Create `backend/app/models/__init__.py`:

```python
from app.models.seed import (
    Activity,
    CostLine,
    Discipline,
    EngDocument,
    HseQuality,
    KbEntry,
    PoDocument,
    Revision,
    ReviewComment,
    Rfq,
    SCurvePoint,
    SeedData,
    Tq,
)

__all__ = [
    "Activity",
    "CostLine",
    "Discipline",
    "EngDocument",
    "HseQuality",
    "KbEntry",
    "PoDocument",
    "Revision",
    "ReviewComment",
    "Rfq",
    "SCurvePoint",
    "SeedData",
    "Tq",
]
```

- [ ] **Step 4: Run the full suite to verify nothing broke**

Run: `cd backend && python -m pytest tests/ -v`
Expected: PASS — every pre-existing test still passes, plus the two new ones. If any import error appears in `store.py`, `routers/`, or `services/`, the `__init__.py` re-export list is missing a name; add it.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models backend/tests/test_models_package.py
git rm backend/app/models.py
git commit -m "refactor: convert models.py to a package with re-exports"
```

---

## Task 2: Project entity with immutable ID and safe rename

**Files:**
- Create: `backend/app/models/project.py`
- Create: `backend/app/workflow/__init__.py`
- Create: `backend/app/workflow/store.py`
- Test: `backend/tests/test_workflow_store.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Project(id: str, name: str, code: str, client: str, location: str, live_period_start: date, live_period_end: date, currency: str, status: str)`
  - `WorkflowStore.create_project(name, code, client, location, live_period_start, live_period_end, currency="AED") -> Project`
  - `WorkflowStore.get_project(project_id: str) -> Project | None`
  - `WorkflowStore.rename_project(project_id: str, new_name: str) -> Project`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_workflow_store.py`:

```python
from datetime import date

import pytest

from app.workflow.store import WorkflowStore


def make_store() -> WorkflowStore:
    return WorkflowStore()


def make_project(store: WorkflowStore):
    return store.create_project(
        name="Haliba Field Development",
        code="HAL",
        client="Al Dhafra Petroleum",
        location="Haliba field, UAE",
        live_period_start=date(2026, 1, 1),
        live_period_end=date(2029, 12, 31),
    )


def test_created_project_gets_a_stable_generated_id():
    store = make_store()
    p = make_project(store)
    assert p.id.startswith("prj_")
    assert store.get_project(p.id) is not None


def test_rename_preserves_the_id():
    store = make_store()
    p = make_project(store)
    original_id = p.id

    store.rename_project(p.id, "Haliba Phase 2")

    renamed = store.get_project(original_id)
    assert renamed is not None, "renaming must not orphan the project"
    assert renamed.id == original_id
    assert renamed.name == "Haliba Phase 2"


def test_rename_of_unknown_project_raises():
    store = make_store()
    with pytest.raises(KeyError):
        store.rename_project("prj_missing", "Anything")


def test_two_projects_may_share_a_name_but_never_an_id():
    store = make_store()
    a = make_project(store)
    b = make_project(store)
    assert a.name == b.name
    assert a.id != b.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_workflow_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.workflow'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/models/project.py`:

```python
from datetime import date
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

ProjectStatus = Literal["Active", "On Hold", "Closed"]


def new_project_id() -> str:
    return f"prj_{uuid4().hex[:8]}"


class Project(BaseModel):
    """A project is the top-level container. `name` is user-editable at any
    time; `id` never changes, and every reference elsewhere binds to `id`."""

    id: str = Field(default_factory=new_project_id)
    name: str
    code: str
    client: str
    location: str
    live_period_start: date
    live_period_end: date
    currency: str = "AED"
    status: ProjectStatus = "Active"
```

Create `backend/app/workflow/__init__.py` as an empty file.

Create `backend/app/workflow/store.py`:

```python
from datetime import date

from app.models.project import Project


class WorkflowStore:
    """In-memory store for workflow entities. Mirrors the pattern in
    `app/store.py` — no database in Phase 1."""

    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_workflow_store.py -v`
Expected: PASS — 4 tests

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/project.py backend/app/workflow backend/tests/test_workflow_store.py
git commit -m "feat: project entity with immutable id and safe rename"
```

---

## Task 3: Items, item types, and live-period validation

**Files:**
- Modify: `backend/app/models/project.py`
- Modify: `backend/app/workflow/store.py`
- Test: `backend/tests/test_workflow_store.py`

**Interfaces:**
- Consumes: `Project`, `WorkflowStore` from Task 2
- Produces:
  - `Item(id: str, project_id: str, item_type: str, description: str, qty: float, uom: str, discipline: str, estimated_value_aed: int, required_on_site: date | None, is_long_lead: bool)`
  - `WorkflowStore.create_item(project_id, item_type, description, qty, uom, discipline, estimated_value_aed, required_on_site=None, is_long_lead=False) -> Item`
  - `WorkflowStore.items_for_project(project_id: str) -> list[Item]`
  - `WorkflowStore.validate_against_live_period(project_id: str, when: date) -> str | None` — returns `None` when in range, otherwise a reason string

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_workflow_store.py`:

```python
def make_item(store: WorkflowStore, project_id: str, item_type: str = "Generator"):
    return store.create_item(
        project_id=project_id,
        item_type=item_type,
        description=f"{item_type} package",
        qty=2,
        uom="ea",
        discipline="Electrical",
        estimated_value_aed=4_200_000,
        required_on_site=date(2027, 6, 1),
    )


def test_item_belongs_to_its_project_by_id():
    store = make_store()
    p = make_project(store)
    item = make_item(store, p.id)
    assert item.project_id == p.id
    assert item.id.startswith("itm_")


def test_items_for_project_returns_only_that_projects_items():
    store = make_store()
    a = make_project(store)
    b = make_project(store)
    make_item(store, a.id, "Generator")
    make_item(store, a.id, "Cable")
    make_item(store, b.id, "Generator")

    assert len(store.items_for_project(a.id)) == 2
    assert len(store.items_for_project(b.id)) == 1


def test_creating_an_item_under_an_unknown_project_raises():
    store = make_store()
    with pytest.raises(KeyError):
        make_item(store, "prj_missing")


def test_renaming_a_project_does_not_orphan_its_items():
    store = make_store()
    p = make_project(store)
    make_item(store, p.id)
    store.rename_project(p.id, "Renamed Entirely")
    assert len(store.items_for_project(p.id)) == 1


def test_date_inside_live_period_validates():
    store = make_store()
    p = make_project(store)
    assert store.validate_against_live_period(p.id, date(2027, 6, 1)) is None


def test_date_outside_live_period_returns_a_reason():
    store = make_store()
    p = make_project(store)
    reason = store.validate_against_live_period(p.id, date(2030, 1, 1))
    assert reason is not None
    assert "live period" in reason.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_workflow_store.py -v`
Expected: FAIL — `AttributeError: 'WorkflowStore' object has no attribute 'create_item'`

- [ ] **Step 3: Write the implementation**

Append to `backend/app/models/project.py`:

```python
def new_item_id() -> str:
    return f"itm_{uuid4().hex[:8]}"


class Item(BaseModel):
    """A procurement item within a project. `item_type` references the shared
    catalogue so cross-project questions do not rely on free-text matching."""

    id: str = Field(default_factory=new_item_id)
    project_id: str
    item_type: str
    description: str
    qty: float
    uom: str
    discipline: str
    estimated_value_aed: int
    required_on_site: date | None = None
    is_long_lead: bool = False
```

Add to `backend/app/workflow/store.py` — extend `__init__` and add the item methods:

```python
    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}
        self._items: dict[str, Item] = {}

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
```

Update the import line at the top of `store.py`:

```python
from app.models.project import Item, Project
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_workflow_store.py -v`
Expected: PASS — 10 tests

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/project.py backend/app/workflow/store.py backend/tests/test_workflow_store.py
git commit -m "feat: items scoped to projects with live-period validation"
```

---

## Task 4: Stage enum and transition table

Pure data and pure functions. No store, no I/O — this is the piece every gate and endpoint depends on, so it is built and tested in isolation first.

**Files:**
- Create: `backend/app/workflow/stages.py`
- Test: `backend/tests/test_workflow_stages.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Stage` — str enum with members `SCOPING`, `SHORTLISTING`, `ISSUED`, `CLARIFICATIONS`, `BIDS_RECEIVED`, `EVALUATION`, `NEGOTIATION`, `AWARDED`, `PO_ISSUED`
  - `TRANSITIONS: dict[Stage, frozenset[Stage]]`
  - `is_allowed(source: Stage, target: Stage) -> bool`
  - `STAGE_ORDER: list[Stage]` — forward process order
  - `is_backward(source: Stage, target: Stage) -> bool`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_workflow_stages.py`:

```python
import pytest

from app.workflow.stages import (
    STAGE_ORDER,
    TRANSITIONS,
    Stage,
    is_allowed,
    is_backward,
)


def test_nine_stages_in_process_order():
    assert STAGE_ORDER == [
        Stage.SCOPING,
        Stage.SHORTLISTING,
        Stage.ISSUED,
        Stage.CLARIFICATIONS,
        Stage.BIDS_RECEIVED,
        Stage.EVALUATION,
        Stage.NEGOTIATION,
        Stage.AWARDED,
        Stage.PO_ISSUED,
    ]


def test_each_stage_advances_to_its_successor():
    for current, following in zip(STAGE_ORDER, STAGE_ORDER[1:]):
        assert is_allowed(current, following), f"{current} should advance to {following}"


def test_skipping_a_stage_is_rejected():
    assert not is_allowed(Stage.SCOPING, Stage.ISSUED)
    assert not is_allowed(Stage.ISSUED, Stage.EVALUATION)


def test_retender_returns_evaluation_to_issued():
    assert is_allowed(Stage.EVALUATION, Stage.ISSUED)
    assert is_backward(Stage.EVALUATION, Stage.ISSUED)


def test_renegotiation_returns_to_evaluation():
    assert is_allowed(Stage.NEGOTIATION, Stage.EVALUATION)
    assert is_backward(Stage.NEGOTIATION, Stage.EVALUATION)


def test_forward_transitions_are_not_backward():
    assert not is_backward(Stage.SCOPING, Stage.SHORTLISTING)


def test_terminal_stage_has_no_successors():
    assert TRANSITIONS[Stage.PO_ISSUED] == frozenset()


def test_unlisted_transition_is_denied_by_default():
    assert not is_allowed(Stage.PO_ISSUED, Stage.SCOPING)
    assert not is_allowed(Stage.AWARDED, Stage.SHORTLISTING)


def test_stage_values_are_human_readable_strings():
    assert Stage.BIDS_RECEIVED.value == "Bids Received"
    assert Stage.PO_ISSUED.value == "PO Issued"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_workflow_stages.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.workflow.stages'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/workflow/stages.py`:

```python
from enum import Enum


class Stage(str, Enum):
    """The eight process stages. RFQ-06 spans two states — terms are agreed
    at NEGOTIATION and the approval chain completes at AWARDED — so there are
    nine enum members."""

    SCOPING = "Scoping"              # RFQ-01
    SHORTLISTING = "Shortlisting"    # RFQ-02
    ISSUED = "Issued"                # RFQ-03
    CLARIFICATIONS = "Clarifications"  # RFQ-04
    BIDS_RECEIVED = "Bids Received"  # RFQ-04A
    EVALUATION = "Evaluation"        # RFQ-05
    NEGOTIATION = "Negotiation"      # RFQ-06
    AWARDED = "Awarded"              # RFQ-06
    PO_ISSUED = "PO Issued"          # RFQ-07


STAGE_ORDER: list[Stage] = [
    Stage.SCOPING,
    Stage.SHORTLISTING,
    Stage.ISSUED,
    Stage.CLARIFICATIONS,
    Stage.BIDS_RECEIVED,
    Stage.EVALUATION,
    Stage.NEGOTIATION,
    Stage.AWARDED,
    Stage.PO_ISSUED,
]

# Deny by default: a transition absent from this table is rejected.
# Backward edges are the documented recoveries — retender and renegotiate.
TRANSITIONS: dict[Stage, frozenset[Stage]] = {
    Stage.SCOPING: frozenset({Stage.SHORTLISTING}),
    Stage.SHORTLISTING: frozenset({Stage.ISSUED}),
    Stage.ISSUED: frozenset({Stage.CLARIFICATIONS}),
    Stage.CLARIFICATIONS: frozenset({Stage.BIDS_RECEIVED}),
    Stage.BIDS_RECEIVED: frozenset({Stage.EVALUATION}),
    Stage.EVALUATION: frozenset({Stage.NEGOTIATION, Stage.ISSUED}),
    Stage.NEGOTIATION: frozenset({Stage.AWARDED, Stage.EVALUATION}),
    Stage.AWARDED: frozenset({Stage.PO_ISSUED}),
    Stage.PO_ISSUED: frozenset(),
}


def is_allowed(source: Stage, target: Stage) -> bool:
    return target in TRANSITIONS[source]


def is_backward(source: Stage, target: Stage) -> bool:
    return STAGE_ORDER.index(target) < STAGE_ORDER.index(source)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_workflow_stages.py -v`
Expected: PASS — 9 tests

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflow/stages.py backend/tests/test_workflow_stages.py
git commit -m "feat: eight-stage RFQ state machine with deny-by-default transitions"
```

---

## Task 5: RFQ record and unguarded transitions with history

Gates arrive in Task 7. This task establishes the record, the transition mechanic, and the history that backward transitions must preserve.

**Files:**
- Create: `backend/app/models/workflow.py`
- Modify: `backend/app/workflow/store.py`
- Test: `backend/tests/test_workflow_store.py`

**Interfaces:**
- Consumes: `Stage` (Task 4), `WorkflowStore` (Tasks 2–3)
- Produces:
  - `StageTransition(from_stage: Stage | None, to_stage: Stage, at: datetime, by: str, reason: str | None)`
  - `RfqRecord(id, reference, project_id, item_ids, package, discipline, stage, history: list[StageTransition], value_estimate_aed)`
  - `WorkflowStore.create_rfq(project_id, item_ids, reference, package, discipline, value_estimate_aed) -> RfqRecord`
  - `WorkflowStore.get_rfq(rfq_id: str) -> RfqRecord | None`
  - `WorkflowStore.transition(rfq_id: str, target: Stage, by: str, reason: str | None = None) -> RfqRecord` — raises `ValueError` when the transition is not allowed

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_workflow_store.py`:

```python
from app.workflow.stages import Stage


def make_rfq(store: WorkflowStore, project_id: str, item_ids: list[str]):
    return store.create_rfq(
        project_id=project_id,
        item_ids=item_ids,
        reference="ADP-RFQ-2026-014",
        package="Wellhead & CGF tie-in materials",
        discipline="Mechanical / piping",
        value_estimate_aed=46_200_000,
    )


def test_new_rfq_starts_at_scoping():
    store = make_store()
    p = make_project(store)
    item = make_item(store, p.id)
    rfq = make_rfq(store, p.id, [item.id])
    assert rfq.stage is Stage.SCOPING
    assert rfq.project_id == p.id
    assert rfq.item_ids == [item.id]


def test_rfq_may_span_several_items():
    store = make_store()
    p = make_project(store)
    a = make_item(store, p.id, "Generator")
    b = make_item(store, p.id, "Cable")
    rfq = make_rfq(store, p.id, [a.id, b.id])
    assert set(rfq.item_ids) == {a.id, b.id}


def test_creation_records_an_opening_history_entry():
    store = make_store()
    p = make_project(store)
    item = make_item(store, p.id)
    rfq = make_rfq(store, p.id, [item.id])
    assert len(rfq.history) == 1
    assert rfq.history[0].from_stage is None
    assert rfq.history[0].to_stage is Stage.SCOPING


def test_allowed_transition_advances_the_stage_and_appends_history():
    store = make_store()
    p = make_project(store)
    item = make_item(store, p.id)
    rfq = make_rfq(store, p.id, [item.id])

    updated = store.transition(rfq.id, Stage.SHORTLISTING, by="amal@example.com")

    assert updated.stage is Stage.SHORTLISTING
    assert len(updated.history) == 2
    assert updated.history[-1].from_stage is Stage.SCOPING
    assert updated.history[-1].by == "amal@example.com"


def test_disallowed_transition_raises_and_leaves_the_stage_untouched():
    store = make_store()
    p = make_project(store)
    item = make_item(store, p.id)
    rfq = make_rfq(store, p.id, [item.id])

    with pytest.raises(ValueError, match="not allowed"):
        store.transition(rfq.id, Stage.ISSUED, by="amal@example.com")

    assert store.get_rfq(rfq.id).stage is Stage.SCOPING


def test_backward_transition_preserves_the_prior_attempt():
    store = make_store()
    p = make_project(store)
    item = make_item(store, p.id)
    rfq = make_rfq(store, p.id, [item.id])
    for target in [
        Stage.SHORTLISTING,
        Stage.ISSUED,
        Stage.CLARIFICATIONS,
        Stage.BIDS_RECEIVED,
        Stage.EVALUATION,
    ]:
        store.transition(rfq.id, target, by="amal@example.com")

    retendered = store.transition(
        rfq.id, Stage.ISSUED, by="amal@example.com", reason="retender — all bids over estimate"
    )

    assert retendered.stage is Stage.ISSUED
    # the first pass through ISSUED is still in the record
    issued_entries = [h for h in retendered.history if h.to_stage is Stage.ISSUED]
    assert len(issued_entries) == 2
    assert issued_entries[-1].reason == "retender — all bids over estimate"


def test_creating_an_rfq_against_a_foreign_item_raises():
    store = make_store()
    a = make_project(store)
    b = make_project(store)
    foreign = make_item(store, b.id)
    with pytest.raises(ValueError, match="does not belong"):
        make_rfq(store, a.id, [foreign.id])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_workflow_store.py -v`
Expected: FAIL — `AttributeError: 'WorkflowStore' object has no attribute 'create_rfq'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/models/workflow.py`:

```python
from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from app.workflow.stages import Stage


def new_rfq_id() -> str:
    return f"rfq_{uuid4().hex[:8]}"


class StageTransition(BaseModel):
    """One entry in an RFQ's stage history. History is append-only: a backward
    transition adds an entry, it never rewrites or removes an earlier one."""

    from_stage: Stage | None
    to_stage: Stage
    at: datetime
    by: str
    reason: str | None = None


class RfqRecord(BaseModel):
    id: str = Field(default_factory=new_rfq_id)
    reference: str
    project_id: str
    item_ids: list[str]
    package: str
    discipline: str
    value_estimate_aed: int
    stage: Stage = Stage.SCOPING
    history: list[StageTransition] = Field(default_factory=list)
```

Add to `backend/app/workflow/store.py`. Extend the imports and `__init__`, then add the RFQ methods:

```python
from datetime import date, datetime, timezone

from app.models.project import Item, Project
from app.models.workflow import RfqRecord, StageTransition
from app.workflow.stages import Stage, is_allowed
```

```python
    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}
        self._items: dict[str, Item] = {}
        self._rfqs: dict[str, RfqRecord] = {}

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_workflow_store.py -v`
Expected: PASS — 17 tests

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/workflow.py backend/app/workflow/store.py backend/tests/test_workflow_store.py
git commit -m "feat: RFQ record with append-only stage history"
```

---

## Task 6: Stage artifacts — package, shortlist, TBE, VDRL

The artifacts the gates in Task 7 check for. Data and storage only; no gate logic yet.

**Files:**
- Modify: `backend/app/models/workflow.py`
- Modify: `backend/app/workflow/store.py`
- Test: `backend/tests/test_workflow_store.py`

**Interfaces:**
- Consumes: `RfqRecord`, `WorkflowStore`
- Produces:
  - `Attachment(doc_code: str, title: str, revision: str | None)`
  - `TechnicalPackage(rfq_id, revision, basis_of_design, attachments: list[Attachment], frozen_at: datetime | None, frozen_by: str | None)`
  - `ShortlistEntry(rfq_id, vendor_name, prequal_status, scope_code_fit, included, override_by, override_reason)`
  - `TbeTemplate(rfq_id, source_rfq_reference: str | None, criteria: list[str])`
  - `VdrlLine(rfq_id, doc_code, title, doc_type, mandatory)`
  - Store methods: `set_technical_package`, `get_technical_package`, `freeze_package(rfq_id, by) -> TechnicalPackage`, `add_shortlist_entry`, `shortlist_for`, `approve_shortlist(rfq_id, by)`, `is_shortlist_approved`, `set_tbe_template`, `get_tbe_template`, `add_vdrl_line`, `vdrl_for`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_workflow_store.py`:

```python
from app.models.workflow import Attachment


def seed_rfq(store: WorkflowStore):
    p = make_project(store)
    item = make_item(store, p.id)
    return make_rfq(store, p.id, [item.id])


def test_technical_package_stores_attachments_at_revisions():
    store = make_store()
    rfq = seed_rfq(store)
    store.set_technical_package(
        rfq.id,
        revision="Rev. B",
        basis_of_design="129 wellhead tie-ins, CGF, 16in export line to ASAB",
        attachments=[Attachment(doc_code="HAL-PID-001", title="P&ID export line", revision="Rev. C")],
    )
    pkg = store.get_technical_package(rfq.id)
    assert pkg.revision == "Rev. B"
    assert pkg.attachments[0].doc_code == "HAL-PID-001"
    assert pkg.frozen_at is None


def test_freezing_a_package_records_who_and_when():
    store = make_store()
    rfq = seed_rfq(store)
    store.set_technical_package(
        rfq.id,
        revision="Rev. B",
        basis_of_design="basis",
        attachments=[Attachment(doc_code="HAL-PID-001", title="P&ID", revision="Rev. C")],
    )
    frozen = store.freeze_package(rfq.id, by="lead.engineer@example.com")
    assert frozen.frozen_at is not None
    assert frozen.frozen_by == "lead.engineer@example.com"


def test_freezing_is_blocked_when_an_attachment_has_no_revision():
    store = make_store()
    rfq = seed_rfq(store)
    store.set_technical_package(
        rfq.id,
        revision="Rev. B",
        basis_of_design="basis",
        attachments=[Attachment(doc_code="HAL-PID-001", title="P&ID", revision=None)],
    )
    with pytest.raises(ValueError, match="definite revision"):
        store.freeze_package(rfq.id, by="lead.engineer@example.com")


def test_shortlist_entries_record_inclusion_and_override():
    store = make_store()
    rfq = seed_rfq(store)
    store.add_shortlist_entry(rfq.id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)
    store.add_shortlist_entry(rfq.id, vendor_name="OQC", prequal_status="Under review",
                              scope_code_fit=False, included=False)
    entries = store.shortlist_for(rfq.id)
    assert len(entries) == 2
    assert [e.included for e in entries] == [True, False]


def test_shortlist_approval_is_recorded():
    store = make_store()
    rfq = seed_rfq(store)
    store.add_shortlist_entry(rfq.id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)
    assert store.is_shortlist_approved(rfq.id) is False
    store.approve_shortlist(rfq.id, by="procurement@example.com")
    assert store.is_shortlist_approved(rfq.id) is True


def test_tbe_template_records_its_source_when_pulled_from_a_past_project():
    store = make_store()
    rfq = seed_rfq(store)
    store.set_tbe_template(rfq.id, criteria=["Throughput", "Materials", "Delivery"],
                           source_rfq_reference="ADP-RFQ-2025-008")
    tbe = store.get_tbe_template(rfq.id)
    assert tbe.criteria == ["Throughput", "Materials", "Delivery"]
    assert tbe.source_rfq_reference == "ADP-RFQ-2025-008"


def test_vdrl_lines_accumulate_for_an_rfq():
    store = make_store()
    rfq = seed_rfq(store)
    store.add_vdrl_line(rfq.id, doc_code="RFQ-014-GA-001", title="Skid GA drawing",
                        doc_type="GA Drawing", mandatory=True)
    store.add_vdrl_line(rfq.id, doc_code="RFQ-014-DS-001", title="Datasheet",
                        doc_type="Datasheet", mandatory=True)
    assert len(store.vdrl_for(rfq.id)) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_workflow_store.py -v`
Expected: FAIL — `ImportError: cannot import name 'Attachment' from 'app.models.workflow'`

- [ ] **Step 3: Write the implementation**

Append to `backend/app/models/workflow.py`:

```python
class Attachment(BaseModel):
    doc_code: str
    title: str
    revision: str | None = None


class TechnicalPackage(BaseModel):
    rfq_id: str
    revision: str
    basis_of_design: str
    attachments: list[Attachment] = Field(default_factory=list)
    frozen_at: datetime | None = None
    frozen_by: str | None = None


class ShortlistEntry(BaseModel):
    rfq_id: str
    vendor_name: str
    prequal_status: str
    scope_code_fit: bool
    included: bool
    override_by: str | None = None
    override_reason: str | None = None


class TbeTemplate(BaseModel):
    rfq_id: str
    criteria: list[str]
    source_rfq_reference: str | None = None


class VdrlLine(BaseModel):
    rfq_id: str
    doc_code: str
    title: str
    doc_type: str
    mandatory: bool = True
```

Add to `backend/app/workflow/store.py`. Extend the imports:

```python
from app.models.workflow import (
    Attachment,
    RfqRecord,
    ShortlistEntry,
    StageTransition,
    TbeTemplate,
    TechnicalPackage,
    VdrlLine,
)
```

Extend `__init__` with the artifact stores and add the methods:

```python
        self._packages: dict[str, TechnicalPackage] = {}
        self._shortlists: dict[str, list[ShortlistEntry]] = {}
        self._shortlist_approvals: dict[str, str] = {}
        self._tbe: dict[str, TbeTemplate] = {}
        self._vdrl: dict[str, list[VdrlLine]] = {}

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_workflow_store.py -v`
Expected: PASS — 24 tests

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/workflow.py backend/app/workflow/store.py backend/tests/test_workflow_store.py
git commit -m "feat: technical package, shortlist, TBE template and VDRL artifacts"
```

---

## Task 7: Sequential gating

The heart of the plan. A transition is now permitted only when the source stage's exit criteria are met, and a blocked transition says exactly what is blocking it.

**Files:**
- Create: `backend/app/workflow/gates.py`
- Modify: `backend/app/workflow/store.py`
- Test: `backend/tests/test_workflow_stages.py`

**Interfaces:**
- Consumes: `Stage`, `WorkflowStore` and all artifact getters from Task 6
- Produces:
  - `GateResult(passed: bool, reason: str | None)`
  - `check_gate(store, rfq_id: str, source: Stage, target: Stage) -> GateResult`
  - `WorkflowStore.transition` now raises `ValueError` carrying the gate reason

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_workflow_stages.py`:

```python
from datetime import date

from app.models.workflow import Attachment
from app.workflow.gates import check_gate
from app.workflow.store import WorkflowStore


def gated_store() -> tuple[WorkflowStore, str]:
    store = WorkflowStore()
    project = store.create_project(
        name="Haliba Field Development",
        code="HAL",
        client="Al Dhafra Petroleum",
        location="Haliba field, UAE",
        live_period_start=date(2026, 1, 1),
        live_period_end=date(2029, 12, 31),
    )
    item = store.create_item(
        project_id=project.id,
        item_type="Wellhead tie-in materials",
        description="Wellhead & CGF tie-in materials",
        qty=1,
        uom="lot",
        discipline="Mechanical / piping",
        estimated_value_aed=46_200_000,
    )
    rfq = store.create_rfq(
        project_id=project.id,
        item_ids=[item.id],
        reference="ADP-RFQ-2026-014",
        package="Wellhead & CGF tie-in materials",
        discipline="Mechanical / piping",
        value_estimate_aed=46_200_000,
    )
    return store, rfq.id


def freeze_the_package(store: WorkflowStore, rfq_id: str) -> None:
    store.set_technical_package(
        rfq_id,
        revision="Rev. B",
        basis_of_design="129 wellhead tie-ins, CGF, 16in export line to ASAB",
        attachments=[Attachment(doc_code="HAL-PID-001", title="P&ID", revision="Rev. C")],
    )
    store.freeze_package(rfq_id, by="lead.engineer@example.com")


def test_scoping_gate_blocks_until_the_package_is_frozen():
    store, rfq_id = gated_store()
    result = check_gate(store, rfq_id, Stage.SCOPING, Stage.SHORTLISTING)
    assert result.passed is False
    assert "frozen" in result.reason.lower()


def test_scoping_gate_passes_once_the_package_is_frozen():
    store, rfq_id = gated_store()
    freeze_the_package(store, rfq_id)
    assert check_gate(store, rfq_id, Stage.SCOPING, Stage.SHORTLISTING).passed is True


def test_shortlisting_gate_blocks_until_the_shortlist_is_approved():
    store, rfq_id = gated_store()
    freeze_the_package(store, rfq_id)
    store.transition(rfq_id, Stage.SHORTLISTING, by="amal@example.com")
    store.add_shortlist_entry(rfq_id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)

    result = check_gate(store, rfq_id, Stage.SHORTLISTING, Stage.ISSUED)
    assert result.passed is False
    assert "approved" in result.reason.lower()


def test_issuance_gate_blocks_without_a_tbe_template():
    store, rfq_id = gated_store()
    freeze_the_package(store, rfq_id)
    store.transition(rfq_id, Stage.SHORTLISTING, by="amal@example.com")
    store.add_shortlist_entry(rfq_id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)
    store.approve_shortlist(rfq_id, by="procurement@example.com")

    result = check_gate(store, rfq_id, Stage.SHORTLISTING, Stage.ISSUED)
    assert result.passed is False
    assert "tbe" in result.reason.lower()


def test_issuance_gate_passes_with_shortlist_approved_and_tbe_present():
    store, rfq_id = gated_store()
    freeze_the_package(store, rfq_id)
    store.transition(rfq_id, Stage.SHORTLISTING, by="amal@example.com")
    store.add_shortlist_entry(rfq_id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)
    store.approve_shortlist(rfq_id, by="procurement@example.com")
    store.set_tbe_template(rfq_id, criteria=["Throughput", "Materials"])

    assert check_gate(store, rfq_id, Stage.SHORTLISTING, Stage.ISSUED).passed is True


def test_transition_raises_with_the_gate_reason():
    store, rfq_id = gated_store()
    with pytest.raises(ValueError, match="frozen"):
        store.transition(rfq_id, Stage.SHORTLISTING, by="amal@example.com")


def test_backward_transitions_are_not_gated():
    """A retender must not be blocked by the gate that guards going forward."""
    store, rfq_id = gated_store()
    freeze_the_package(store, rfq_id)
    store.transition(rfq_id, Stage.SHORTLISTING, by="amal@example.com")
    store.add_shortlist_entry(rfq_id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)
    store.approve_shortlist(rfq_id, by="procurement@example.com")
    store.set_tbe_template(rfq_id, criteria=["Throughput"])
    for target in [Stage.ISSUED, Stage.CLARIFICATIONS, Stage.BIDS_RECEIVED, Stage.EVALUATION]:
        store.transition(rfq_id, target, by="amal@example.com")

    retendered = store.transition(rfq_id, Stage.ISSUED, by="amal@example.com", reason="retender")
    assert retendered.stage is Stage.ISSUED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_workflow_stages.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.workflow.gates'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/workflow/gates.py`:

```python
from typing import TYPE_CHECKING

from pydantic import BaseModel

from app.workflow.stages import Stage, is_backward

if TYPE_CHECKING:  # avoids a circular import at runtime
    from app.workflow.store import WorkflowStore


class GateResult(BaseModel):
    """A gate never returns a bare False — a blocked transition must say what
    is blocking it, so the caller can show the user something actionable."""

    passed: bool
    reason: str | None = None


def _passed() -> GateResult:
    return GateResult(passed=True)


def _blocked(reason: str) -> GateResult:
    return GateResult(passed=False, reason=reason)


def _scoping_exit(store: "WorkflowStore", rfq_id: str) -> GateResult:
    package = store.get_technical_package(rfq_id)
    if package is None:
        return _blocked("No technical package has been attached to this RFQ.")
    if package.frozen_at is None:
        return _blocked("The technical package must be frozen before shortlisting can begin.")
    return _passed()


def _shortlisting_exit(store: "WorkflowStore", rfq_id: str) -> GateResult:
    entries = store.shortlist_for(rfq_id)
    if not any(e.included for e in entries):
        return _blocked("The shortlist contains no included vendors.")
    if not store.is_shortlist_approved(rfq_id):
        return _blocked("The shortlist must be approved by procurement before issuance.")
    if store.get_tbe_template(rfq_id) is None:
        return _blocked("A TBE template must be attached before the RFQ can be issued.")
    return _passed()


# Only forward transitions are gated. Backward transitions (retender,
# renegotiate) are recoveries and must not be blocked by a forward gate.
_GATES = {
    (Stage.SCOPING, Stage.SHORTLISTING): _scoping_exit,
    (Stage.SHORTLISTING, Stage.ISSUED): _shortlisting_exit,
}


def check_gate(
    store: "WorkflowStore", rfq_id: str, source: Stage, target: Stage
) -> GateResult:
    if is_backward(source, target):
        return _passed()
    gate = _GATES.get((source, target))
    if gate is None:
        return _passed()
    return gate(store, rfq_id)
```

Modify `transition` in `backend/app/workflow/store.py` — add the gate check after the `is_allowed` check:

```python
        if not is_allowed(rfq.stage, target):
            raise ValueError(f"Transition {rfq.stage.value} -> {target.value} is not allowed")

        gate = check_gate(self, rfq_id, rfq.stage, target)
        if not gate.passed:
            raise ValueError(gate.reason)
```

Add the import at the top of `store.py`:

```python
from app.workflow.gates import check_gate
```

- [ ] **Step 4: Run the full suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: PASS. Note `test_allowed_transition_advances_the_stage_and_appends_history` and `test_backward_transition_preserves_the_prior_attempt` in `test_workflow_store.py` will now FAIL, because they transition without freezing the package first. Fix them by calling the package-freeze helper before the first transition — add this helper to `test_workflow_store.py` and call it at the start of both tests:

```python
def freeze_and_prepare(store: WorkflowStore, rfq_id: str) -> None:
    store.set_technical_package(
        rfq_id,
        revision="Rev. B",
        basis_of_design="basis",
        attachments=[Attachment(doc_code="HAL-PID-001", title="P&ID", revision="Rev. C")],
    )
    store.freeze_package(rfq_id, by="lead.engineer@example.com")
    store.add_shortlist_entry(rfq_id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)
    store.approve_shortlist(rfq_id, by="procurement@example.com")
    store.set_tbe_template(rfq_id, criteria=["Throughput"])
```

Re-run until green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflow/gates.py backend/app/workflow/store.py backend/tests/
git commit -m "feat: sequential gating — transitions blocked by unmet exit criteria"
```

---

## Task 8: Bid register, VDRL receipt and the manual selection gate

**Files:**
- Create: `backend/app/models/bid.py`
- Modify: `backend/app/workflow/store.py`
- Modify: `backend/app/workflow/gates.py`
- Test: `backend/tests/test_workflow_store.py`

**Interfaces:**
- Consumes: `WorkflowStore`, `VdrlLine`
- Produces:
  - `Bid(id, rfq_id, vendor_name, received_at, headline_price_aed, currency)`
  - `VdrlReceipt(bid_id, doc_code, state: Literal["received","not_received","unreadable"], revision)`
  - `BidShortlist(rfq_id, selected_bid_ids, selected_by, rationale, at)`
  - `WorkflowStore.register_bid(...) -> Bid`, `record_vdrl_receipt(...)`, `vdrl_summary(bid_id) -> tuple[int, int]`, `missing_vdrl_lines(bid_id) -> list[str]`, `select_bids(rfq_id, bid_ids, by, rationale)`, `get_bid_shortlist(rfq_id)`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_workflow_store.py`:

```python
def test_vdrl_summary_counts_received_against_required():
    store = make_store()
    rfq = seed_rfq(store)
    for code in ["GA-001", "DS-001", "TP-001"]:
        store.add_vdrl_line(rfq.id, doc_code=code, title=code, doc_type="Doc", mandatory=True)
    bid = store.register_bid(rfq.id, vendor_name="Petrofac", headline_price_aed=51_400_000)

    store.record_vdrl_receipt(bid.id, doc_code="GA-001", state="received", revision="Rev. A")
    store.record_vdrl_receipt(bid.id, doc_code="DS-001", state="received", revision="Rev. A")
    store.record_vdrl_receipt(bid.id, doc_code="TP-001", state="not_received")

    received, required = store.vdrl_summary(bid.id)
    assert (received, required) == (2, 3)
    assert store.missing_vdrl_lines(bid.id) == ["TP-001"]


def test_unreadable_is_distinct_from_not_received():
    store = make_store()
    rfq = seed_rfq(store)
    store.add_vdrl_line(rfq.id, doc_code="GA-001", title="GA", doc_type="Doc", mandatory=True)
    bid = store.register_bid(rfq.id, vendor_name="Petrofac", headline_price_aed=51_400_000)
    store.record_vdrl_receipt(bid.id, doc_code="GA-001", state="unreadable")

    received, required = store.vdrl_summary(bid.id)
    assert received == 0, "an unreadable document is not a received document"
    assert store.missing_vdrl_lines(bid.id) == ["GA-001"]


def test_selection_records_who_and_why():
    store = make_store()
    rfq = seed_rfq(store)
    a = store.register_bid(rfq.id, vendor_name="Galfar", headline_price_aed=46_200_000)
    store.register_bid(rfq.id, vendor_name="Petrofac", headline_price_aed=51_400_000)

    store.select_bids(rfq.id, [a.id], by="client@example.com", rationale="Lowest compliant bid")

    shortlist = store.get_bid_shortlist(rfq.id)
    assert shortlist.selected_bid_ids == [a.id]
    assert shortlist.selected_by == "client@example.com"
    assert shortlist.rationale == "Lowest compliant bid"


def test_selection_without_a_rationale_is_rejected():
    store = make_store()
    rfq = seed_rfq(store)
    bid = store.register_bid(rfq.id, vendor_name="Galfar", headline_price_aed=46_200_000)
    with pytest.raises(ValueError, match="rationale"):
        store.select_bids(rfq.id, [bid.id], by="client@example.com", rationale="")


def test_evaluation_gate_blocks_until_bids_are_selected():
    store = make_store()
    rfq = seed_rfq(store)
    from app.workflow.gates import check_gate
    result = check_gate(store, rfq.id, Stage.BIDS_RECEIVED, Stage.EVALUATION)
    assert result.passed is False
    assert "select" in result.reason.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_workflow_store.py -v`
Expected: FAIL — `AttributeError: 'WorkflowStore' object has no attribute 'register_bid'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/models/bid.py`:

```python
from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

ReceiptState = Literal["received", "not_received", "unreadable"]


def new_bid_id() -> str:
    return f"bid_{uuid4().hex[:8]}"


class Bid(BaseModel):
    id: str = Field(default_factory=new_bid_id)
    rfq_id: str
    vendor_name: str
    received_at: datetime
    headline_price_aed: int
    currency: str = "AED"


class VdrlReceipt(BaseModel):
    """Receipt of one VDRL line item for one bid. `unreadable` is deliberately
    distinct from `not_received` — a corrupt file is a delivery failure to
    chase, not a refusal to submit."""

    bid_id: str
    doc_code: str
    state: ReceiptState
    revision: str | None = None


class BidShortlist(BaseModel):
    rfq_id: str
    selected_bid_ids: list[str]
    selected_by: str
    rationale: str
    at: datetime
```

Add to `backend/app/workflow/store.py`. Extend imports and `__init__`, then add the methods:

```python
from app.models.bid import Bid, BidShortlist, ReceiptState, VdrlReceipt
```

```python
        self._bids: dict[str, Bid] = {}
        self._receipts: dict[str, list[VdrlReceipt]] = {}
        self._bid_shortlists: dict[str, BidShortlist] = {}

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

    def vdrl_summary(self, bid_id: str) -> tuple[int, int]:
        """Returns (received, required). Only `received` counts — `unreadable`
        and `not_received` are both gaps."""
        bid = self._bids.get(bid_id)
        if bid is None:
            raise KeyError(f"Unknown bid: {bid_id}")
        required = [line for line in self.vdrl_for(bid.rfq_id) if line.mandatory]
        received_codes = {
            r.doc_code for r in self._receipts.get(bid_id, []) if r.state == "received"
        }
        return len([line for line in required if line.doc_code in received_codes]), len(required)

    def missing_vdrl_lines(self, bid_id: str) -> list[str]:
        bid = self._bids.get(bid_id)
        if bid is None:
            raise KeyError(f"Unknown bid: {bid_id}")
        received_codes = {
            r.doc_code for r in self._receipts.get(bid_id, []) if r.state == "received"
        }
        return [
            line.doc_code
            for line in self.vdrl_for(bid.rfq_id)
            if line.mandatory and line.doc_code not in received_codes
        ]

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
            if bid_id not in self._bids:
                raise KeyError(f"Unknown bid: {bid_id}")
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
```

Add the gate in `backend/app/workflow/gates.py`:

```python
def _bids_received_exit(store: "WorkflowStore", rfq_id: str) -> GateResult:
    if store.get_bid_shortlist(rfq_id) is None:
        return _blocked(
            "Bids must be selected for evaluation before evaluation can begin."
        )
    return _passed()
```

and register it:

```python
_GATES = {
    (Stage.SCOPING, Stage.SHORTLISTING): _scoping_exit,
    (Stage.SHORTLISTING, Stage.ISSUED): _shortlisting_exit,
    (Stage.BIDS_RECEIVED, Stage.EVALUATION): _bids_received_exit,
}
```

- [ ] **Step 4: Run the full suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: PASS. `test_backward_transition_preserves_the_prior_attempt` now needs a bid selection before reaching `EVALUATION` — add to its setup, before the transition loop:

```python
    bid = store.register_bid(rfq.id, vendor_name="Galfar", headline_price_aed=46_200_000)
    store.select_bids(rfq.id, [bid.id], by="client@example.com", rationale="Only compliant bid")
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/bid.py backend/app/workflow backend/tests/
git commit -m "feat: bid register, VDRL receipt tracking and manual selection gate"
```

---

## Task 9: Workflow API endpoints

**Files:**
- Create: `backend/app/routers/workflow.py`
- Modify: `backend/app/main.py:5,20`
- Test: `backend/tests/test_workflow_endpoints.py`

**Interfaces:**
- Consumes: everything from Tasks 2–8
- Produces:
  - `GET /api/workflow/stages` → `{"stages": [...9 stage values...]}`
  - `POST /api/workflow/projects` → `Project`
  - `POST /api/workflow/projects/{project_id}/items` → `Item`
  - `POST /api/workflow/rfqs` → `RfqRecord`
  - `GET /api/workflow/rfqs/{rfq_id}` → `{rfq, gate: {passed, reason}}`
  - `POST /api/workflow/rfqs/{rfq_id}/transition` → `RfqRecord` · **409** with the gate reason when blocked
  - `workflow_store` — module-level singleton, following the `app/store.py` pattern

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_workflow_endpoints.py`:

```python
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def create_project() -> str:
    r = client.post("/api/workflow/projects", json={
        "name": "Haliba Field Development",
        "code": "HAL",
        "client": "Al Dhafra Petroleum",
        "location": "Haliba field, UAE",
        "live_period_start": "2026-01-01",
        "live_period_end": "2029-12-31",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def create_rfq() -> str:
    project_id = create_project()
    r = client.post(f"/api/workflow/projects/{project_id}/items", json={
        "item_type": "Wellhead tie-in materials",
        "description": "Wellhead & CGF tie-in materials",
        "qty": 1,
        "uom": "lot",
        "discipline": "Mechanical / piping",
        "estimated_value_aed": 46200000,
    })
    assert r.status_code == 201, r.text
    item_id = r.json()["id"]

    r = client.post("/api/workflow/rfqs", json={
        "project_id": project_id,
        "item_ids": [item_id],
        "reference": "ADP-RFQ-2026-014",
        "package": "Wellhead & CGF tie-in materials",
        "discipline": "Mechanical / piping",
        "value_estimate_aed": 46200000,
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_stages_endpoint_lists_all_nine_in_order():
    r = client.get("/api/workflow/stages")
    assert r.status_code == 200
    assert r.json()["stages"] == [
        "Scoping", "Shortlisting", "Issued", "Clarifications",
        "Bids Received", "Evaluation", "Negotiation", "Awarded", "PO Issued",
    ]


def test_new_rfq_starts_at_scoping_with_a_blocking_gate():
    rfq_id = create_rfq()
    r = client.get(f"/api/workflow/rfqs/{rfq_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["rfq"]["stage"] == "Scoping"
    assert body["gate"]["passed"] is False
    assert "frozen" in body["gate"]["reason"].lower()


def test_blocked_transition_returns_409_with_the_reason():
    rfq_id = create_rfq()
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/transition", json={
        "target": "Shortlisting", "by": "amal@example.com",
    })
    assert r.status_code == 409
    assert "frozen" in r.json()["detail"].lower()


def test_unknown_rfq_returns_404():
    r = client.get("/api/workflow/rfqs/rfq_missing")
    assert r.status_code == 404


def test_invalid_target_stage_returns_422():
    rfq_id = create_rfq()
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/transition", json={
        "target": "Not A Stage", "by": "amal@example.com",
    })
    assert r.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_workflow_endpoints.py -v`
Expected: FAIL — 404 on every route; `/api/workflow/*` is not registered

- [ ] **Step 3: Write the implementation**

Create `backend/app/routers/workflow.py`:

```python
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.workflow.gates import check_gate
from app.workflow.stages import STAGE_ORDER, Stage
from app.workflow.store import WorkflowStore

router = APIRouter(prefix="/api/workflow", tags=["workflow"])

# Module-level singleton, mirroring the `app/store.py` pattern.
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


@router.get("/stages")
def get_stages() -> dict:
    return {"stages": [s.value for s in STAGE_ORDER]}


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn) -> dict:
    project = workflow_store.create_project(**body.model_dump())
    return project.model_dump(mode="json")


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


@router.get("/rfqs/{rfq_id}")
def get_rfq(rfq_id: str) -> dict:
    rfq = workflow_store.get_rfq(rfq_id)
    if rfq is None:
        raise HTTPException(status_code=404, detail=f"Unknown RFQ: {rfq_id}")

    next_stages = []
    for target in STAGE_ORDER:
        gate = check_gate(workflow_store, rfq_id, rfq.stage, target)
        if target.value in [s.value for s in STAGE_ORDER]:
            pass
    gate = _next_forward_gate(rfq_id, rfq.stage)
    return {
        "rfq": rfq.model_dump(mode="json"),
        "gate": gate.model_dump(),
    }


def _next_forward_gate(rfq_id: str, stage: Stage):
    """Gate status for the natural next stage in process order."""
    index = STAGE_ORDER.index(stage)
    if index + 1 >= len(STAGE_ORDER):
        from app.workflow.gates import GateResult

        return GateResult(passed=True, reason=None)
    return check_gate(workflow_store, rfq_id, stage, STAGE_ORDER[index + 1])


@router.post("/rfqs/{rfq_id}/transition")
def transition(rfq_id: str, body: TransitionIn) -> dict:
    try:
        rfq = workflow_store.transition(
            rfq_id, target=body.target, by=body.by, reason=body.reason
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return rfq.model_dump(mode="json")
```

Remove the dead loop in `get_rfq` — it was scaffolding; the function body should read:

```python
@router.get("/rfqs/{rfq_id}")
def get_rfq(rfq_id: str) -> dict:
    rfq = workflow_store.get_rfq(rfq_id)
    if rfq is None:
        raise HTTPException(status_code=404, detail=f"Unknown RFQ: {rfq_id}")
    gate = _next_forward_gate(rfq_id, rfq.stage)
    return {"rfq": rfq.model_dump(mode="json"), "gate": gate.model_dump()}
```

Modify `backend/app/main.py` line 5 and line 20:

```python
from app.routers import dashboard, engineering, planning, projects, procurement, workflow
```

```python
app.include_router(workflow.router)
```

- [ ] **Step 4: Run the full suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: PASS — all tests including the 5 new endpoint tests

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/workflow.py backend/app/main.py backend/tests/test_workflow_endpoints.py
git commit -m "feat: workflow API endpoints with gate-aware transitions"
```

---

## Task 10: Widen the legacy stage list to the nine workflow stages

Brings the existing RFQ status page onto the same vocabulary as the workflow. Maps the five seed statuses forward.

**Files:**
- Modify: `backend/app/routers/procurement.py:9`
- Modify: `backend/tests/test_endpoints.py:128`

**Interfaces:**
- Consumes: `Stage`, `STAGE_ORDER` (Task 4)
- Produces: `/api/procurement/rfqs` returns `stages` as the nine workflow stage values; `stage_counts` keyed by the same

- [ ] **Step 1: Update the existing test to the new expectation**

In `backend/tests/test_endpoints.py`, replace the stage assertions inside `test_rfqs_endpoint_flags_overdue_packages`:

```python
def test_rfqs_endpoint_flags_overdue_packages():
    r = client.get("/api/procurement/rfqs")
    assert r.status_code == 200
    b = r.json()
    assert len(b["rfqs"]) == 12
    assert b["stages"] == [
        "Scoping", "Shortlisting", "Issued", "Clarifications",
        "Bids Received", "Evaluation", "Negotiation", "Awarded", "PO Issued",
    ]
    # seed data uses the five legacy statuses; the other four are simply empty
    assert sum(b["stage_counts"].values()) == 12
    assert b["stage_counts"]["Scoping"] == 0
    overdue = [r for r in b["rfqs"] if r["is_overdue"]]
    assert len(overdue) >= 1
    assert all(r["days_overdue"] > 0 for r in overdue)
    assert all(r["actual_closure_date"] is None for r in overdue)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_endpoints.py::test_rfqs_endpoint_flags_overdue_packages -v`
Expected: FAIL — `stages` is still the five-item legacy list

- [ ] **Step 3: Write the implementation**

Modify `backend/app/routers/procurement.py`. Replace the `STAGES` constant and the counting logic:

```python
from datetime import date

from fastapi import APIRouter

from app import store
from app.workflow.stages import STAGE_ORDER

router = APIRouter(prefix="/api/procurement", tags=["procurement"])

STAGES = [s.value for s in STAGE_ORDER]

# Seed data predates the workflow model and uses five legacy statuses.
# "Technical Eval" and "Commercial Eval" both map to the single Evaluation stage.
_LEGACY_STATUS_MAP = {
    "Issued": "Issued",
    "Bids Received": "Bids Received",
    "Technical Eval": "Evaluation",
    "Commercial Eval": "Evaluation",
    "Awarded": "Awarded",
}


@router.get("/rfqs")
def get_rfqs() -> dict:
    today = date.today()
    rows = []
    for r in store.data.rfqs:
        overdue = r.actual_closure_date is None and r.planned_closure_date < today
        rows.append({
            **r.model_dump(mode="json"),
            "stage": _LEGACY_STATUS_MAP[r.status],
            "is_overdue": overdue,
            "days_overdue": (today - r.planned_closure_date).days if overdue else 0,
        })
    return {
        "rfqs": rows,
        "stages": STAGES,
        "stage_counts": {
            s: sum(1 for r in store.data.rfqs if _LEGACY_STATUS_MAP[r.status] == s)
            for s in STAGES
        },
    }
```

- [ ] **Step 4: Run the full suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: PASS — every test green

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/procurement.py backend/tests/test_endpoints.py
git commit -m "feat: widen RFQ stage vocabulary to the nine workflow stages"
```

---

## Task 11: Frontend stage strip

**Files:**
- Create: `frontend/components/rfq/StageStrip.tsx`
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/app/(shell)/procurement/rfq-status/page.tsx`

**Interfaces:**
- Consumes: `/api/procurement/rfqs` returning `stages: string[]` and `stage_counts: Record<string, number>` (Task 10)
- Produces: `<StageStrip stages={string[]} counts={Record<string, number>} current={string | null} />`

- [ ] **Step 1: Add the type**

Append to `frontend/lib/types.ts`:

```typescript
export type RfqStage =
  | "Scoping"
  | "Shortlisting"
  | "Issued"
  | "Clarifications"
  | "Bids Received"
  | "Evaluation"
  | "Negotiation"
  | "Awarded"
  | "PO Issued";

export interface StageStripProps {
  stages: string[];
  counts: Record<string, number>;
  current?: string | null;
}
```

- [ ] **Step 2: Write the component**

Create `frontend/components/rfq/StageStrip.tsx`:

```tsx
import type { StageStripProps } from "@/lib/types";

/**
 * Horizontal strip of the nine workflow stages. Reflects state — it does not
 * set it. Clicking a stage does not advance an RFQ; only a gated transition
 * on the server does that.
 */
export default function StageStrip({ stages, counts, current }: StageStripProps) {
  const currentIndex = current ? stages.indexOf(current) : -1;

  return (
    <ol className="flex gap-1.5 overflow-x-auto pb-1" aria-label="RFQ stages">
      {stages.map((stage, i) => {
        const isCurrent = i === currentIndex;
        const isDone = currentIndex >= 0 && i < currentIndex;
        const count = counts[stage] ?? 0;

        return (
          <li
            key={stage}
            aria-current={isCurrent ? "step" : undefined}
            className={[
              "min-w-[7.5rem] flex-1 rounded-lg border px-3 py-2",
              isCurrent
                ? "border-teal-700 bg-teal-50"
                : isDone
                  ? "border-slate-300 bg-slate-50"
                  : "border-slate-200 bg-white",
            ].join(" ")}
          >
            <span className="block font-mono text-[11px] tracking-wide text-slate-500">
              {`RFQ-${String(i + 1).padStart(2, "0")}`}
              {isDone ? " ✓" : ""}
            </span>
            <span className="block text-[12.5px] font-semibold leading-tight text-slate-900">
              {stage}
            </span>
            <span className="block text-[11px] text-slate-500">{count}</span>
          </li>
        );
      })}
    </ol>
  );
}
```

- [ ] **Step 3: Use it on the RFQ status page**

In `frontend/app/(shell)/procurement/rfq-status/page.tsx`, import the component and render it above the existing table, passing the `stages` and `stage_counts` already returned by the endpoint:

```tsx
import StageStrip from "@/components/rfq/StageStrip";
```

```tsx
<StageStrip stages={data.stages} counts={data.stage_counts} />
```

Replace whatever existing five-stage strip markup is present. If the page currently derives its own stage list locally, delete that and use the endpoint's `stages` array — the server is the source of stage vocabulary.

- [ ] **Step 4: Verify in the browser**

Start the backend (`cd backend && uvicorn app.main:app --reload`) and the frontend (`cd frontend && npm run dev`). Open `http://localhost:3000/procurement/rfq-status`.

Expected: nine stage cards render in order, each showing its count; the four new stages show `0`; the table below is unchanged. Check the browser console for errors and confirm no horizontal page scroll — the strip scrolls inside itself.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/rfq/StageStrip.tsx frontend/lib/types.ts "frontend/app/(shell)/procurement/rfq-status/page.tsx"
git commit -m "feat: nine-stage RFQ progress strip"
```

---

## Self-Review

**Spec coverage.** Phase 1 requirements from `platform-requirements-and-capabilities.md`:

| Requirement | Task |
| --- | --- |
| C2-R1 Project CRUD, immutable ID | 2 |
| C2-R2 Project metadata incl. live period | 2 |
| C2-R3 Item CRUD under a project | 3 |
| C2-R5 Live-period validation | 3 |
| C2-R6 Rename safety, explicitly tested | 2, 3 |
| C5-R1 RFQ scoped to project and items | 5 |
| C5-R2 Eight-stage state machine | 4 |
| C5-R3 Sequential gating | 7 |
| C5-R4 Backward transitions preserve prior attempt | 5, 7 |
| C5-R5 Shortlist approval gate | 6, 7 |
| C5-R6 Issuance blocked without TBE | 6, 7 |
| C5-R9 Stage progress reflects state | 11 |
| C3-R2 Document register / attachment manifest | 6 |
| C3-R3 Technical package freeze | 6 |
| C3-R4 Freeze blocked without definite revisions | 6 |
| C7-R12 Manual/top-N selection by a named person | 8 |
| C7-R13 Selection rationale; non-selected retained | 8 |
| C7-R14 Evaluation gated on selection | 8 |
| C7-R18 Three receipt states incl. unreadable | 8 |
| C10-R6 Overrides are positive acts | 6 |

**Known gaps, deferred with reason:**
- **C2-R4 shared item-type catalogue** — `Item.item_type` is a free string in Phase 1. The catalogue as an entity needs persistence to be worth building; deferred to Phase 2.
- **C5-R7 bid window timers** — needs a scheduler, which is Phase 2 infrastructure.
- **C7-R1/R2 returnables checklist** — the nine-item checklist and conditional compliance rule are modelled at RFQ-03 but not enforced in Phase 1; the VDRL covers the gating need. Phase 2.
- **C10-R5 named gates per stage** — `by` is captured on every transition, but there is no role check without identity. Phase 2.

**Placeholder scan:** clean. Every code step contains runnable code; every test step names the exact command and expected outcome.

**Type consistency:** `Stage` used identically across `stages.py`, `gates.py`, `store.py`, `workflow.py`, and the router. `GateResult` returned by `check_gate` and serialised by the router. `vdrl_summary` returns `tuple[int, int]` in both its definition and its two call sites. `WorkflowStore` method names match between the interfaces blocks and implementations.

**One deliberate rework:** Task 7 breaks two tests written in Task 5, and Task 8 breaks one from Task 7. This is expected — gates did not exist when those tests were written — and each task states the exact fix. Do not treat these as regressions.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-12-rfq-platform-phase-1.md`. Two execution options:

**1. Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
