# Project → item hierarchy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the workflow store the screens it never had — create a project, add
equipment items under it, edit and delete both, and raise an RFQ from selected
items — so the project → item → RFQ path is walkable from the browser.

**Architecture:** Five new routes in `api/workflow_routes.py` over five new
`WorkflowStore` methods, all writing through `persistence.locked_update`; three
new React pages under `web/src/pages/` that drill down from a roster, mirroring
how `RfqWorkflow` already opens one RFQ. No new store, no new dependency, no
change to `procurement/`.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, pytest · React 19, Vite,
TypeScript, vitest + @testing-library/react.

**Spec:** [`docs/superpowers/specs/2026-08-13-project-item-hierarchy-design.md`](../specs/2026-08-13-project-item-hierarchy-design.md)

## Global Constraints

- **No new dependencies**, Python or npm. Everything here is built from what the
  repo already has.
- **Tests stay key-free.** No test may require `ANTHROPIC_API_KEY`; the suite
  runs against `shared/llm/mock_client.py`.
- **Every write, and every decision that gates one, happens inside
  `workflow.persistence.locked_update`.** A guard evaluated in the route and a
  write performed in the store are two critical sections, not one. Guards
  therefore live in the store method.
- **New routes are NOT added to `api/auth/middleware.PUBLIC_PATHS`**, which stays
  pinned at three entries by `test_the_allowlist_is_exactly_these_three_paths`.
- **Status mapping**, fixed by the module docstring of `api/workflow_routes.py`:
  `404` the named entity does not exist · `409` it exists but the workflow
  refuses · `422` the request is self-inconsistent.
- **Lists are addressed by `id`, never by index or position.**
- Run tests from the repo root: `python -m pytest`. Web tests: `npm test` from
  `web/`.
- Commit after every task. Never `--no-verify`.

---

## Note on PLAN-TEMPLATE.md compliance

[`docs/superpowers/PLAN-TEMPLATE.md`](../PLAN-TEMPLATE.md) governs plans for
phases 3 and 4. Its three rules are applied here in full — a named store
invariant per task (Rule 1), a two-run mutation matrix on the integration task
(Rule 2), and the reference-code banner (Rule 3).

**Its nine required matrix rows are not reproduced verbatim, and that is a
deliberate substitution rather than a skip.** Every one of those rows is a
mutation of the *extraction pipeline* — a newer document revision arriving, a
prompt-version constant bumping, an LLM call failing on run 1 and succeeding on
run 2. This work touches no document, no prompt and no model call; there is
nothing in `workflow.json` for those mutations to act on, and writing them in
would produce nine tests asserting the absence of subsystems this task never
loads.

What carries across is their *shape*: every one catches state that should have
left the store and didn't. Task 4's matrix reproduces that shape against the
entities this work actually mutates. The mapping is stated in Task 4.

---

## File structure

| File | Change | Responsibility |
| --- | --- | --- |
| `workflow/store.py` | modify | `get_item`, `update_project`, `update_item`, `delete_item`, `delete_project`; `rename_project` removed |
| `api/workflow_routes.py` | modify | five routes + counts on the roster + the live-period warning |
| `tests/test_workflow_store.py` | modify | store-level tests, incl. the three `rename_project` call sites moved to `update_project` |
| `tests/test_workflow_endpoints.py` | modify | route-level tests, one per status code |
| `tests/test_workflow_persistence.py` | modify | the two-run mutation matrix |
| `tests/test_auth_middleware.py` | modify | one line: the `{item_id}` probe substitution |
| `web/src/types.ts` | modify | `WorkflowProject`, `WorkflowItem`, and their wrappers |
| `web/src/api.ts` | modify | eight fetchers; `sendJson` widened to accept `PATCH` |
| `web/src/api.test.ts` | modify | request shape of the new fetchers |
| `web/src/pages/Projects.tsx` | **create** | roster + create form + drill-down state |
| `web/src/pages/ProjectDetail.tsx` | **create** | project header/edit, items table, RFQ table |
| `web/src/pages/ItemDetail.tsx` | **create** | one item, editable, plus the RFQs covering it |
| `web/src/pages/forms.tsx` | **create** | `ProjectForm`, `ItemForm`, `RaiseRfqForm` — shared by the three pages |
| `web/src/pages/Projects.test.tsx` | **create** | roster and create |
| `web/src/pages/ProjectDetail.test.tsx` | **create** | items, delete refusal, raise RFQ |
| `web/src/pages/ItemDetail.test.tsx` | **create** | fields and covering RFQs |
| `web/src/App.tsx` | modify | one `View` value, the nav table, the landing screen |
| `web/src/App.test.tsx` | modify | the landing-screen assertion |
| `web/src/pages/Dashboard.tsx` | modify | `PageHeader` eyebrow, "Portfolio" → "Bid sets" |
| `CLAUDE.md` | modify | both test-count rows |

The forms live in their own module because `RfqWizard.tsx` already demonstrates
the alternative at 634 lines: a page that owns its screens, its forms and its
state at once is past the size where an edit is reliable.

---

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the
> implementation against the test.
>
> **Load-bearing** throughout: the guard's *placement* (inside the store method,
> inside the lock), the cascade building its id list before deleting, addressing
> by `id`, and the exact HTTP status for each refusal. **Illustrative:** the
> exact wording of every message, and the exact CSS class names.

---

### Task 1: Store — read one item, and partial updates

**Files:**
- Modify: `workflow/store.py`
- Test: `tests/test_workflow_store.py`

**Interfaces:**
- Consumes: `WorkflowStore._projects`, `WorkflowStore._items` (existing).
- Produces:
  - `get_item(item_id: str) -> Item | None`
  - `update_project(project_id: str, changes: dict) -> Project`
  - `update_item(item_id: str, changes: dict) -> Item`
- **Store invariant owned:** `_projects` and `_items` hold exactly one record per
  id, and an update replaces that record in place — an update never adds a
  record, never changes an entity's `id`, and never changes an item's
  `project_id`.

`rename_project` is **replaced** by `update_project`, not kept beside it. Its
three call sites in `tests/test_workflow_store.py` (lines 43, 54 and 108) move
across in this task; leaving both would give the store two ways to write one
field.

`changes` is a plain dict because the route hands it `model_dump(exclude_unset=True)`
from an all-optional pydantic model, which is what makes the `PATCH` partial: an
absent key means "leave it alone", never "clear it".

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_workflow_store.py

def test_update_project_replaces_only_the_named_fields():
    store = WorkflowStore()
    p = store.create_project(
        name="Haliba Field Development",
        code="HAL",
        client="Al Dhafra Petroleum",
        location="Haliba field, UAE",
        live_period_start=date(2026, 1, 1),
        live_period_end=date(2029, 12, 31),
    )

    updated = store.update_project(p.id, {"name": "Haliba Phase 2", "status": "On Hold"})

    assert updated.name == "Haliba Phase 2"
    assert updated.status == "On Hold"
    assert updated.code == "HAL"          # absent from `changes`, so untouched
    assert updated.id == p.id
    # Replaced in place. A record appended rather than replaced would leave two.
    assert len(store.list_projects()) == 1


def test_update_project_refuses_to_change_the_id():
    store = WorkflowStore()
    p = store.create_project(
        name="Haliba", code="HAL", client="ADP", location="UAE",
        live_period_start=date(2026, 1, 1), live_period_end=date(2029, 12, 31),
    )
    with pytest.raises(ValueError, match="id"):
        store.update_project(p.id, {"id": "prj_somethingelse"})


def test_update_project_rejects_an_unknown_project():
    with pytest.raises(KeyError):
        WorkflowStore().update_project("prj_missing", {"name": "Anything"})


def test_update_item_replaces_only_the_named_fields():
    store = WorkflowStore()
    p = store.create_project(
        name="Haliba", code="HAL", client="ADP", location="UAE",
        live_period_start=date(2026, 1, 1), live_period_end=date(2029, 12, 31),
    )
    item = store.create_item(
        project_id=p.id, item_type="Gas generator", description="2 x 5 MW",
        qty=2, uom="no", discipline="Electrical", estimated_value_aed=18_000_000,
    )

    updated = store.update_item(item.id, {"qty": 3, "is_long_lead": True})

    assert updated.qty == 3
    assert updated.is_long_lead is True
    assert updated.item_type == "Gas generator"
    assert updated.id == item.id
    assert len(store.items_for_project(p.id)) == 1


def test_update_item_refuses_to_move_it_between_projects():
    """An item's parent is not editable. Moving one would silently change which
    project's RFQs may cover it, so the honest operation is delete and re-add."""
    store = WorkflowStore()
    a = store.create_project(
        name="A", code="A", client="c", location="l",
        live_period_start=date(2026, 1, 1), live_period_end=date(2029, 12, 31),
    )
    b = store.create_project(
        name="B", code="B", client="c", location="l",
        live_period_start=date(2026, 1, 1), live_period_end=date(2029, 12, 31),
    )
    item = store.create_item(
        project_id=a.id, item_type="Cable", description="HV cable",
        qty=1200, uom="m", discipline="Electrical", estimated_value_aed=900_000,
    )

    with pytest.raises(ValueError, match="project_id"):
        store.update_item(item.id, {"project_id": b.id})

    assert store.get_item(item.id).project_id == a.id


def test_get_item_returns_none_for_an_unknown_id():
    assert WorkflowStore().get_item("itm_missing") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest tests/test_workflow_store.py -v
```

Expected: FAIL — `AttributeError: 'WorkflowStore' object has no attribute 'update_project'` (and `get_item`, `update_item`).

- [ ] **Step 3: Write the implementation**

Replace `rename_project` in `workflow/store.py` with:

```python
    # Fields a caller may never write. `id` is identity; `project_id` is
    # parentage, and moving an item between projects would change which
    # project's RFQs are allowed to cover it without either project's RFQ
    # records changing. Both are enforced here rather than only in the route's
    # pydantic model, so a future caller that bypasses the route cannot do it.
    _PROJECT_IMMUTABLE = frozenset({"id"})
    _ITEM_IMMUTABLE = frozenset({"id", "project_id"})

    def update_project(self, project_id: str, changes: dict) -> Project:
        project = self._projects.get(project_id)
        if project is None:
            raise KeyError(f"Unknown project: {project_id}")
        blocked = self._PROJECT_IMMUTABLE & set(changes)
        if blocked:
            raise ValueError(f"These fields cannot be changed: {', '.join(sorted(blocked))}")
        updated = project.model_copy(update=changes)
        self._projects[project_id] = updated
        return updated

    def update_item(self, item_id: str, changes: dict) -> Item:
        item = self._items.get(item_id)
        if item is None:
            raise KeyError(f"Unknown item: {item_id}")
        blocked = self._ITEM_IMMUTABLE & set(changes)
        if blocked:
            raise ValueError(f"These fields cannot be changed: {', '.join(sorted(blocked))}")
        updated = item.model_copy(update=changes)
        self._items[item_id] = updated
        return updated

    def get_item(self, item_id: str) -> Item | None:
        return self._items.get(item_id)
```

Load-bearing: assignment back into `self._items[item_id]` / `self._projects[project_id]`
under the **same** key. `model_copy` returns a new object; forgetting the
assignment makes the update silently vanish, and forgetting to key it by the
original id is how a duplicate appears.

Then move the three `rename_project` call sites in `tests/test_workflow_store.py`
to `store.update_project(p.id, {"name": ...})`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest tests/test_workflow_store.py -v
```

Expected: PASS, and no remaining reference to `rename_project`:

```bash
grep -rn "rename_project" --include=*.py . | grep -v __pycache__
```

Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add workflow/store.py tests/test_workflow_store.py
git commit -m "feat: partial updates for workflow projects and items"
```

---

### Task 2: Store — deletion, with its two guards

**Files:**
- Modify: `workflow/store.py`
- Test: `tests/test_workflow_store.py`

**Interfaces:**
- Consumes: `get_item` (Task 1), `WorkflowStore._rfqs`.
- Produces:
  - `delete_item(item_id: str) -> None` — raises `KeyError` if unknown,
    `ValueError` if an RFQ covers it.
  - `delete_project(project_id: str) -> None` — raises `KeyError` if unknown,
    `ValueError` if the project holds any RFQ.
- **Store invariant owned:** every id reachable from a live entity resolves —
  `_items` contains exactly the items of projects that are in `_projects`, and
  every `item_id` in every `RfqRecord.item_ids` resolves to a live item in
  `_items`.

Both guards are *reads that gate a write*, so they live in the store method,
which the route runs inside `locked_update`. CLAUDE.md records that both auth
guards this repository shipped wrong were reads outside the lock; `store.delete_user`
and `store.grant` are the shape being copied.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_workflow_store.py

def _project_with_item(store: WorkflowStore, name: str = "Haliba"):
    project = store.create_project(
        name=name, code=name[:3].upper(), client="ADP", location="UAE",
        live_period_start=date(2026, 1, 1), live_period_end=date(2029, 12, 31),
    )
    item = store.create_item(
        project_id=project.id, item_type="Gas generator", description="2 x 5 MW",
        qty=2, uom="no", discipline="Electrical", estimated_value_aed=18_000_000,
    )
    return project, item


def test_deleting_an_item_covered_by_an_rfq_is_refused_and_names_the_rfq():
    store = WorkflowStore()
    project, item = _project_with_item(store)
    store.create_rfq(
        project_id=project.id, item_ids=[item.id], reference="ADP-RFQ-2026-014",
        package="Power generation", discipline="Electrical",
        value_estimate_aed=18_000_000,
    )

    with pytest.raises(ValueError) as exc:
        store.delete_item(item.id)

    # The reason must name what blocks it — a bare refusal leaves the user with
    # nothing to act on. Same rule the stage gates already follow.
    assert "ADP-RFQ-2026-014" in str(exc.value)
    assert store.get_item(item.id) is not None


def test_deleting_an_item_no_rfq_covers_removes_it():
    store = WorkflowStore()
    project, item = _project_with_item(store)
    store.delete_item(item.id)
    assert store.get_item(item.id) is None
    assert store.items_for_project(project.id) == []


def test_deleting_an_unknown_item_raises():
    with pytest.raises(KeyError):
        WorkflowStore().delete_item("itm_missing")


def test_deleting_a_project_removes_its_items_in_the_same_call():
    """The cascade is the whole point: `save` replaces the document wholesale,
    so an item left behind here is an item pointing at a project that is gone —
    and it persists."""
    store = WorkflowStore()
    project, item = _project_with_item(store)
    second = store.create_item(
        project_id=project.id, item_type="HV cable", description="11 kV",
        qty=1200, uom="m", discipline="Electrical", estimated_value_aed=900_000,
    )

    store.delete_project(project.id)

    assert store.get_project(project.id) is None
    assert store.get_item(item.id) is None
    assert store.get_item(second.id) is None


def test_deleting_a_project_leaves_another_projects_items_alone():
    store = WorkflowStore()
    doomed, doomed_item = _project_with_item(store, name="Haliba")
    kept, kept_item = _project_with_item(store, name="Bab")

    store.delete_project(doomed.id)

    assert store.get_item(doomed_item.id) is None
    assert store.get_item(kept_item.id) is not None
    assert store.get_project(kept.id) is not None


def test_deleting_a_project_holding_an_rfq_is_refused_and_names_it():
    store = WorkflowStore()
    project, item = _project_with_item(store)
    store.create_rfq(
        project_id=project.id, item_ids=[item.id], reference="ADP-RFQ-2026-014",
        package="Power generation", discipline="Electrical",
        value_estimate_aed=18_000_000,
    )

    with pytest.raises(ValueError) as exc:
        store.delete_project(project.id)

    assert "ADP-RFQ-2026-014" in str(exc.value)
    assert store.get_project(project.id) is not None
    assert store.get_item(item.id) is not None   # nothing half-deleted


def test_deleting_an_unknown_project_raises():
    with pytest.raises(KeyError):
        WorkflowStore().delete_project("prj_missing")
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest tests/test_workflow_store.py -k delete -v
```

Expected: FAIL — `AttributeError: 'WorkflowStore' object has no attribute 'delete_item'`.

- [ ] **Step 3: Write the implementation**

```python
    def delete_item(self, item_id: str) -> None:
        """Refused while any RFQ covers the item. An RFQ's `item_ids` is what
        defines its scope, so removing a member silently would change what
        vendors were invited to bid on after the fact."""
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
        every one of its items go together, in this one call."""
        if project_id not in self._projects:
            raise KeyError(f"Unknown project: {project_id}")
        held = sorted(r.reference for r in self._rfqs.values() if r.project_id == project_id)
        if held:
            raise ValueError(
                f"This project cannot be deleted: it holds "
                f"{', '.join(held)}. Delete or retender first."
            )
        # The id list is built *before* the first deletion — mutating a dict
        # while iterating it raises, and comprehending over `.values()` lazily
        # would do exactly that.
        doomed = [i.id for i in self._items.values() if i.project_id == project_id]
        for item_id in doomed:
            del self._items[item_id]
        del self._projects[project_id]
```

Load-bearing: the `doomed` list materialising before the loop; the `project_id`
filter on it (without which the cascade takes every project's items); and both
guards raising *before* anything is removed, so a refusal cannot half-land.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest tests/test_workflow_store.py -v
```

Expected: PASS, all of them.

- [ ] **Step 5: Commit**

```bash
git add workflow/store.py tests/test_workflow_store.py
git commit -m "feat: delete workflow items and projects, with referential guards"
```

---

### Task 3: Routes — read one project, edit and delete both levels

**Files:**
- Modify: `api/workflow_routes.py`
- Modify: `tests/test_auth_middleware.py` (one line)
- Test: `tests/test_workflow_endpoints.py`

**Interfaces:**
- Consumes: `update_project`, `update_item`, `delete_item`, `delete_project`,
  `get_item` (Tasks 1–2); `persistence.locked_update`.
- Produces, all under `/api/workflow`:
  - `GET /projects` — now `{"projects": [{…project…, "item_count": int, "rfq_count": int}]}`
  - `GET /projects/{project_id}` — `{"project": {…}, "items": [...], "rfqs": [...]}`
  - `PATCH /projects/{project_id}` — body `ProjectPatch`, returns the project
  - `DELETE /projects/{project_id}` — 204
  - `PATCH /projects/{project_id}/items/{item_id}` — body `ItemPatch`, returns
    the item's fields plus `live_period_warning: str | None`
  - `DELETE /projects/{project_id}/items/{item_id}` — 204
  - `POST /projects/{project_id}/items` — **shape change**: the same item fields
    it already returns, plus `live_period_warning: str | None`
- **Store invariant owned:** a refused route call leaves `workflow.json` exactly
  as it was — `locked_update` writes only on a clean exit, so a 404 or 409 from
  any route in this module writes nothing at all.

Two shape decisions:

`live_period_warning` is merged into the item's own object rather than wrapping
it (`{**item.model_dump(), "live_period_warning": …}`). That is the convention
`get_rfq` already uses for a bid's VDRL tally, and it keeps `r.json()["id"]`
working for the existing callers in `tests/test_workflow_endpoints.py`.

`GET /projects/{project_id}` returns project, items and RFQs together because
the detail page renders all three; three separate calls could return three
different generations of the document.

`{item_id}` is a new path parameter, so it must be added to the probe
substitutions in
`test_every_api_route_outside_the_allowlist_requires_a_session` at
`tests/test_auth_middleware.py:37-43`. That sweep is designed to fail when a
route appears — this is it working, not a broken test.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_workflow_endpoints.py

def create_item(client: TestClient, project_id: str, **over) -> dict:
    body = {
        "item_type": "Gas generator",
        "description": "2 x 5 MW containerised",
        "qty": 2,
        "uom": "no",
        "discipline": "Electrical",
        "estimated_value_aed": 18_000_000,
    }
    body.update(over)
    r = client.post(f"/api/workflow/projects/{project_id}/items", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_the_project_roster_carries_item_and_rfq_counts(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    create_item(client, project_id)

    row = client.get("/api/workflow/projects").json()["projects"][0]

    assert row["item_count"] == 1
    assert row["rfq_count"] == 0


def test_project_detail_returns_project_items_and_rfqs(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    item = create_item(client, project_id)
    client.post("/api/workflow/rfqs", json={
        "project_id": project_id, "item_ids": [item["id"]],
        "reference": "ADP-RFQ-2026-014", "package": "Power generation",
        "discipline": "Electrical", "value_estimate_aed": 18_000_000,
    })

    body = client.get(f"/api/workflow/projects/{project_id}").json()

    assert body["project"]["id"] == project_id
    assert [i["id"] for i in body["items"]] == [item["id"]]
    assert [r["reference"] for r in body["rfqs"]] == ["ADP-RFQ-2026-014"]


def test_project_detail_404s_for_an_unknown_project(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/workflow/projects/prj_missing").status_code == 404


def test_patching_a_project_changes_only_what_was_sent(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)

    r = client.patch(f"/api/workflow/projects/{project_id}", json={"status": "On Hold"})

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "On Hold"
    assert r.json()["code"] == "HAL"      # untouched by an absent key


def test_patching_an_unknown_project_404s(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.patch("/api/workflow/projects/prj_missing", json={"status": "Closed"})
    assert r.status_code == 404


def test_patching_an_item_through_the_wrong_project_404s(tmp_path, monkeypatch):
    """The path names a parent; an item that is not that parent's is not found
    under it, whatever the item id says."""
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    item = create_item(client, project_id)
    other_id = create_project(client)

    r = client.patch(
        f"/api/workflow/projects/{other_id}/items/{item['id']}", json={"qty": 9}
    )

    assert r.status_code == 404


def test_an_item_dated_outside_the_live_period_warns_but_is_stored(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)   # live period 2026-01-01 .. 2029-12-31

    body = create_item(client, project_id, required_on_site="2030-06-01")

    assert body["live_period_warning"] is not None
    assert "2030-06-01" in body["live_period_warning"]
    assert len(client.get(f"/api/workflow/projects/{project_id}").json()["items"]) == 1


def test_an_item_dated_inside_the_live_period_carries_no_warning(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    assert create_item(client, project_id, required_on_site="2027-06-01")[
        "live_period_warning"
    ] is None


def test_deleting_an_item_an_rfq_covers_is_409_and_writes_nothing(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    item = create_item(client, project_id)
    client.post("/api/workflow/rfqs", json={
        "project_id": project_id, "item_ids": [item["id"]],
        "reference": "ADP-RFQ-2026-014", "package": "Power generation",
        "discipline": "Electrical", "value_estimate_aed": 18_000_000,
    })

    r = client.delete(f"/api/workflow/projects/{project_id}/items/{item['id']}")

    assert r.status_code == 409
    assert "ADP-RFQ-2026-014" in r.json()["detail"]
    assert len(client.get(f"/api/workflow/projects/{project_id}").json()["items"]) == 1


def test_deleting_a_free_item_is_204(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    item = create_item(client, project_id)

    r = client.delete(f"/api/workflow/projects/{project_id}/items/{item['id']}")

    assert r.status_code == 204
    assert client.get(f"/api/workflow/projects/{project_id}").json()["items"] == []


def test_deleting_a_project_holding_an_rfq_is_409(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    item = create_item(client, project_id)
    client.post("/api/workflow/rfqs", json={
        "project_id": project_id, "item_ids": [item["id"]],
        "reference": "ADP-RFQ-2026-014", "package": "Power generation",
        "discipline": "Electrical", "value_estimate_aed": 18_000_000,
    })

    r = client.delete(f"/api/workflow/projects/{project_id}")

    assert r.status_code == 409
    assert client.get(f"/api/workflow/projects/{project_id}").status_code == 200


def test_deleting_a_project_takes_its_items_with_it(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    create_item(client, project_id)

    assert client.delete(f"/api/workflow/projects/{project_id}").status_code == 204
    assert client.get("/api/workflow/projects").json()["projects"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest tests/test_workflow_endpoints.py -v
```

Expected: FAIL — 405 Method Not Allowed on the `PATCH`/`DELETE` paths, and
`KeyError: 'item_count'` on the roster test.

- [ ] **Step 3: Write the implementation**

Add the request models beside the existing `ItemIn`:

```python
class ProjectPatch(BaseModel):
    """Every field optional: an absent key means "leave it alone", never
    "clear it". `id` is deliberately not a field here — identity is not
    editable, and the store refuses it a second time."""

    name: str | None = None
    code: str | None = None
    client: str | None = None
    location: str | None = None
    live_period_start: date | None = None
    live_period_end: date | None = None
    currency: str | None = None
    status: str | None = None


class ItemPatch(BaseModel):
    item_type: str | None = None
    description: str | None = None
    qty: float | None = None
    uom: str | None = None
    discipline: str | None = None
    estimated_value_aed: int | None = None
    required_on_site: date | None = None
    is_long_lead: bool | None = None
```

The routes:

```python
def _item_payload(store: WorkflowStore, item) -> dict:
    """An item, plus the live-period caution if its delivery date falls outside
    the project's window. Non-blocking on purpose: needing something after a
    live period closes is unusual, not impossible, and a hard refusal would make
    the field unusable in exactly those cases. Merged into the item's own object
    rather than wrapping it — the convention `get_rfq` already uses for a bid."""
    warning = (
        store.validate_against_live_period(item.project_id, item.required_on_site)
        if item.required_on_site
        else None
    )
    return {**item.model_dump(mode="json"), "live_period_warning": warning}


@router.get("/projects/{project_id}")
def get_project(project_id: str) -> dict:
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
            # `exclude_unset` is what makes this a partial update: a field the
            # client never sent is absent from the dict, so `model_copy` leaves
            # it alone. `exclude_none` would be wrong — it cannot tell "not sent"
            # from "sent as null".
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
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _owned_item(store: WorkflowStore, project_id: str, item_id: str):
    """The path names a parent, so an item belonging to a different project is
    *not found* under this one. Returning it would let a client edit any item by
    guessing any project id."""
    item = store.get_item(item_id)
    if item is None or item.project_id != project_id:
        raise HTTPException(status_code=404, detail=f"Unknown item: {item_id}")
    return item


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
        raise HTTPException(status_code=409, detail=str(exc)) from exc
```

Load-bearing: `_owned_item` is called **inside** the `locked_update` block, not
before it — it is a read that gates a write, which is the whole reason this
module's docstring exists. The `HTTPException` it raises propagates out of the
context manager, so `locked_update` never reaches its `save`.

Also: the `_item_payload` call in `patch_item` sits inside the block, because it
reads the project's live period; and `create_item` changes its return to
`_item_payload(store, item)` — likewise inside the block.

Extend the roster:

```python
@router.get("/projects")
def list_projects() -> dict:
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
```

And one line in `tests/test_auth_middleware.py`, after `.replace("{rfq_id}", "any")`:

```python
                .replace("{item_id}", "any")
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest tests/test_workflow_endpoints.py tests/test_auth_middleware.py -v
```

Expected: PASS. In particular
`test_every_api_route_outside_the_allowlist_requires_a_session` must pass, and
`test_the_allowlist_is_exactly_these_three_paths` must still pass untouched.

- [ ] **Step 5: Commit**

```bash
git add api/workflow_routes.py tests/test_workflow_endpoints.py tests/test_auth_middleware.py
git commit -m "feat: read, edit and delete routes for workflow projects and items"
```

---

### Task 4: Integration — the two-run mutation matrix

**Files:**
- Test: `tests/test_workflow_persistence.py`

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces: no new production code. This task is the defence of the invariants
  the previous three claimed.
- **Store invariant owned:** `workflow.json` holds exactly the entities the store
  holds, *across a reload* — an entity removed in memory does not survive on
  disk, and an entity a refused call did not remove is still there.

**Why the template's nine rows are substituted.** Each required row mutates the
extraction pipeline — a document revision, a prompt-version constant, an LLM
call. None of those exists in `workflow.json`. What carries across is their
shape: every one catches *state that should have left the store and didn't*.
These rows reproduce that shape against the entities this work mutates.

| mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|
| a project holding items is deleted | Task 2 — `_items` holds exactly the items of live projects | reload: neither the project nor any of its items is in the document |
| a project holding items is deleted while a **second** project also holds items | Task 2 — same | reload: the second project's items are all still present |
| an item covered by an RFQ is deleted (refused, 409) | Task 3 — a refused call writes nothing | reload: the item is present, and the document is byte-identical to before the call |
| a project holding an RFQ is deleted (refused, 409) | Task 3 — same | reload: project, items and RFQ all still present |
| a project is patched, then the API is restarted | Task 1 — one record per id, replaced in place | reload: exactly one project, carrying the new value and the untouched fields |
| an item is patched to a date outside the live period | Task 3 — the warning is advisory, not a write barrier | reload: the item is stored with the new date |
| an item is deleted, then a new item is added | Task 2 — every `item_id` in every RFQ resolves | reload: the deleted id appears in no RFQ's `item_ids`, and the new item is present |
| the whole document is reloaded after a create → patch → delete sequence | this task — the document equals the store | reload: `to_document(load(root))` equals the document on disk |

- [ ] **Step 1: Write the failing tests**

One test per row. The shape, using the row that matters most:

```python
# tests/test_workflow_persistence.py

def test_deleting_a_project_does_not_leave_its_items_on_disk(tmp_path):
    """Run 1 creates and deletes; run 2 is a fresh load. An item pruned only in
    memory would reappear here — this is the orphan defect in its new home."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        project = store.create_project(
            name="Haliba", code="HAL", client="ADP", location="UAE",
            live_period_start=date(2026, 1, 1), live_period_end=date(2029, 12, 31),
        )
        item = store.create_item(
            project_id=project.id, item_type="Gas generator", description="2 x 5 MW",
            qty=2, uom="no", discipline="Electrical", estimated_value_aed=18_000_000,
        )

    with persistence.locked_update(root) as store:
        store.delete_project(project.id)

    reloaded = persistence.load(root)            # run 2: from disk, not memory
    assert reloaded.get_project(project.id) is None
    assert reloaded.get_item(item.id) is None
    assert persistence.to_document(reloaded)["items"] == []


def test_a_refused_item_delete_leaves_the_document_untouched(tmp_path):
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        project = store.create_project(
            name="Haliba", code="HAL", client="ADP", location="UAE",
            live_period_start=date(2026, 1, 1), live_period_end=date(2029, 12, 31),
        )
        item = store.create_item(
            project_id=project.id, item_type="Gas generator", description="2 x 5 MW",
            qty=2, uom="no", discipline="Electrical", estimated_value_aed=18_000_000,
        )
        store.create_rfq(
            project_id=project.id, item_ids=[item.id], reference="ADP-RFQ-2026-014",
            package="Power generation", discipline="Electrical",
            value_estimate_aed=18_000_000,
        )

    before = Path(persistence.workflow_path(root)).read_bytes()

    with pytest.raises(ValueError):
        with persistence.locked_update(root) as store:
            store.delete_item(item.id)

    # `locked_update` writes only on a clean exit, so the refusal must not have
    # touched the file at all — not "written the same content back".
    assert Path(persistence.workflow_path(root)).read_bytes() == before
    assert persistence.load(root).get_item(item.id) is not None
```

Write the remaining six rows in the same shape, each naming its invariant in the
docstring.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest tests/test_workflow_persistence.py -v
```

Expected: FAIL on the rows whose store methods were only just written — confirm
each failure names the row's own assertion, not an import error.

- [ ] **Step 3: No implementation**

If any row fails here, the defect is in Task 1, 2 or 3 and belongs fixed there,
with the fixing test left in this file.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest tests/test_workflow_persistence.py -v
```

- [ ] **Step 4b: Verify the matrix is real, not decorative**

For each row, reintroduce the defect it claims to catch, one at a time, and
confirm **that row fails and the others do not**. Concretely:

| defect to reinstate | expected sole failure |
|---|---|
| drop the `for item_id in doomed` loop from `delete_project` | row 1 |
| change the cascade filter to take every item, not just this project's | row 2 |
| move the `covering` guard in `delete_item` to after the `del` | row 3 |
| move the `held` guard in `delete_project` to after the `del` | row 4 |
| make `update_project` insert under a fresh id instead of `project_id` | row 5 |
| make the live-period warning raise `ValueError` instead of returning | row 6 |

A row that still passes with its defect reinstated is not testing what it
claims — fix the row, not the defect. Revert every reinstated defect before
committing; `git diff` must show only the test file.

- [ ] **Step 5: Commit**

```bash
git add tests/test_workflow_persistence.py
git commit -m "test: two-run mutation matrix for project and item deletion"
```

---

### Task 5: Web — types and API client

**Files:**
- Modify: `web/src/types.ts`, `web/src/api.ts`
- Test: `web/src/api.test.ts`

**Interfaces:**
- Consumes: the routes from Task 3.
- Produces:
  - Types `WorkflowProject`, `WorkflowItem`, `WorkflowItemSaved`,
    `WorkflowProjectSummary`, `WorkflowProjectDetail`
  - `fetchWorkflowProjects(): Promise<WorkflowProjectSummary[]>`
  - `fetchWorkflowProject(projectId: string): Promise<WorkflowProjectDetail>`
  - `createWorkflowProject(body: WorkflowProjectInput): Promise<WorkflowProject>`
  - `updateWorkflowProject(projectId: string, changes: Partial<WorkflowProjectInput>): Promise<WorkflowProject>`
  - `deleteWorkflowProject(projectId: string): Promise<void>`
  - `createWorkflowItem(projectId: string, body: WorkflowItemInput): Promise<WorkflowItemSaved>`
  - `updateWorkflowItem(projectId: string, itemId: string, changes: Partial<WorkflowItemInput>): Promise<WorkflowItemSaved>`
  - `deleteWorkflowItem(projectId: string, itemId: string): Promise<void>`
  - `createRfq(body: RfqInput): Promise<Rfq>`
- **Store invariant owned:** none — this task writes no server state. It is the
  typed surface the three screens consume.

The `Workflow…` prefix is not decoration: `types.ts` already exports
`ProjectSummary` and `ProjectDetail` for the *ingestion* project, which is a
different entity in a different store. Two types named `ProjectDetail` in one
file is how the two identities get confused in code, which is exactly what the
spec's §2 scope decision is trying to avoid.

- [ ] **Step 1: Write the failing tests**

```typescript
// web/src/api.test.ts

describe('workflow project and item fetchers', () => {
  it('patches a project with only the changed fields', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: 'prj_1' }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await updateWorkflowProject('prj_1', { status: 'On Hold' })

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/workflow/projects/prj_1')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(init.body)).toEqual({ status: 'On Hold' })
  })

  it('deletes an item under its project and expects no body', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(deleteWorkflowItem('prj_1', 'itm_1')).resolves.toBeUndefined()

    expect(fetchMock.mock.calls[0][0]).toBe('/api/workflow/projects/prj_1/items/itm_1')
    expect(fetchMock.mock.calls[0][1].method).toBe('DELETE')
  })

  it("raises the server's own sentence when a delete is refused", async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'This item cannot be deleted: it is covered by ADP-RFQ-2026-014.' }), { status: 409 }),
    ))

    await expect(deleteWorkflowItem('prj_1', 'itm_1')).rejects.toThrow(
      /covered by ADP-RFQ-2026-014/,
    )
  })
})
```

Match the existing `web/src/api.test.ts` setup conventions — read the top of
that file before writing, and reuse its `fetch` stubbing pattern rather than
introducing a second one.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd web && npm test -- api.test.ts
```

Expected: FAIL — `updateWorkflowProject is not exported`.

- [ ] **Step 3: Write the implementation**

In `web/src/types.ts`:

```typescript
export interface WorkflowProject {
  id: string
  name: string
  code: string
  client: string
  location: string
  live_period_start: string
  live_period_end: string
  currency: string
  status: 'Active' | 'On Hold' | 'Closed'
}

export type WorkflowProjectInput = Omit<WorkflowProject, 'id' | 'status'> &
  Partial<Pick<WorkflowProject, 'status'>>

/** The roster row. Counts are computed server-side so no screen re-derives
 *  them from a list it only partly holds. */
export interface WorkflowProjectSummary extends WorkflowProject {
  item_count: number
  rfq_count: number
}

export interface WorkflowItem {
  id: string
  project_id: string
  item_type: string
  description: string
  qty: number
  uom: string
  discipline: string
  estimated_value_aed: number
  required_on_site: string | null
  is_long_lead: boolean
}

export type WorkflowItemInput = Omit<WorkflowItem, 'id' | 'project_id'>

/** What create and patch return: the item, plus an advisory caution when its
 *  delivery date falls outside the project's live period. Advisory — the item
 *  is stored either way. */
export interface WorkflowItemSaved extends WorkflowItem {
  live_period_warning: string | null
}

export interface WorkflowProjectDetail {
  project: WorkflowProject
  items: WorkflowItem[]
  rfqs: Rfq[]
}

export interface RfqInput {
  project_id: string
  item_ids: string[]
  reference: string
  package: string
  discipline: string
  value_estimate_aed: number
}
```

In `web/src/api.ts`, widen the method union and add the fetchers:

```typescript
function sendJson<T>(
  path: string,
  method: 'POST' | 'PUT' | 'PATCH',   // ← 'PATCH' is the addition
  body: unknown,
): Promise<T> { /* unchanged body */ }

/* --------------------------------------------- workflow projects and items */

export function fetchWorkflowProjects(): Promise<WorkflowProjectSummary[]> {
  return getJson<{ projects: WorkflowProjectSummary[] }>(
    '/api/workflow/projects',
  ).then((body) => body.projects)
}

export function fetchWorkflowProject(projectId: string): Promise<WorkflowProjectDetail> {
  return getJson(`/api/workflow/projects/${encodeURIComponent(projectId)}`)
}

export function createWorkflowProject(
  body: WorkflowProjectInput,
): Promise<WorkflowProject> {
  return sendJson('/api/workflow/projects', 'POST', body)
}

export function updateWorkflowProject(
  projectId: string,
  changes: Partial<WorkflowProjectInput>,
): Promise<WorkflowProject> {
  return sendJson(
    `/api/workflow/projects/${encodeURIComponent(projectId)}`,
    'PATCH',
    changes,
  )
}

export function deleteWorkflowProject(projectId: string): Promise<void> {
  return fetch(`/api/workflow/projects/${encodeURIComponent(projectId)}`, {
    method: 'DELETE',
  }).then(expectNoContent)
}

export function createWorkflowItem(
  projectId: string,
  body: WorkflowItemInput,
): Promise<WorkflowItemSaved> {
  return sendJson(
    `/api/workflow/projects/${encodeURIComponent(projectId)}/items`,
    'POST',
    body,
  )
}

export function updateWorkflowItem(
  projectId: string,
  itemId: string,
  changes: Partial<WorkflowItemInput>,
): Promise<WorkflowItemSaved> {
  return sendJson(
    `/api/workflow/projects/${encodeURIComponent(projectId)}/items/${encodeURIComponent(itemId)}`,
    'PATCH',
    changes,
  )
}

export function deleteWorkflowItem(projectId: string, itemId: string): Promise<void> {
  return fetch(
    `/api/workflow/projects/${encodeURIComponent(projectId)}/items/${encodeURIComponent(itemId)}`,
    { method: 'DELETE' },
  ).then(expectNoContent)
}

export function createRfq(body: RfqInput): Promise<Rfq> {
  return sendJson('/api/workflow/rfqs', 'POST', body)
}
```

Load-bearing: `Partial<…>` on both patch signatures, and passing `changes`
straight through. A caller that spreads a whole object into a patch defeats
`exclude_unset` server-side and turns every edit into a full overwrite.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd web && npm test -- api.test.ts && npm run build
```

Expected: PASS, and the build type-checks (it includes the test files).

- [ ] **Step 5: Commit**

```bash
git add web/src/types.ts web/src/api.ts web/src/api.test.ts
git commit -m "feat: typed client for workflow project and item routes"
```

---

### Task 6: Web — the Projects roster

**Files:**
- Create: `web/src/pages/Projects.tsx`, `web/src/pages/forms.tsx`
- Test: `web/src/pages/Projects.test.tsx`

**Interfaces:**
- Consumes: `fetchWorkflowProjects`, `createWorkflowProject` (Task 5);
  `PageHeader`, `Card`, `EmptyState`, `ErrorState`, `LoadingState` from
  `../components/primitives`; `useAsync`.
- Produces:
  - `Projects(): JSX.Element` — a **named** export (`export function Projects`),
    matching how `RfqWorkflow` is exported and imported; it owns `openProjectId`
    / `openItemId` and renders `ProjectDetail` or `ItemDetail` when either is
    set.
  - `ProjectForm({ initial, submitLabel, onSubmit, onCancel })` in `forms.tsx`,
    where `initial?: WorkflowProjectInput` and
    `onSubmit: (body: WorkflowProjectInput) => Promise<void>`.
- **Store invariant owned:** none — no server state is written by the roster
  beyond the create it delegates to Task 5's fetcher.

`Projects` holds the drill-down state and renders one of three screens, the way
`RfqWorkflow` holds `openRfqId` and renders the roster, the wizard or the detail
view. `App.tsx` therefore learns one new `View` value and nothing about items.

`ProjectForm` is written here and reused by Task 7's edit mode, which is why it
takes `initial` and `submitLabel` rather than being a create-only form.

- [ ] **Step 1: Write the failing test**

```typescript
// web/src/pages/Projects.test.tsx
import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Projects } from './Projects'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    fetchWorkflowProjects: vi.fn(),
    createWorkflowProject: vi.fn(),
  }
})

import { createWorkflowProject, fetchWorkflowProjects } from '../api'

const HALIBA = {
  id: 'prj_1',
  name: 'Haliba Field Development',
  code: 'HAL',
  client: 'Al Dhafra Petroleum',
  location: 'Haliba field, UAE',
  live_period_start: '2026-01-01',
  live_period_end: '2029-12-31',
  currency: 'AED',
  status: 'Active' as const,
  item_count: 4,
  rfq_count: 1,
}

describe('Projects', () => {
  it('lists projects with their item and RFQ counts', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([HALIBA])

    render(<Projects />)

    expect(await screen.findByText('Haliba Field Development')).toBeInTheDocument()
    const row = screen.getByText('Haliba Field Development').closest('tr')!
    expect(row).toHaveTextContent('4')
    expect(row).toHaveTextContent('1')
  })

  it('tells the user what an empty roster means', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([])
    render(<Projects />)
    expect(await screen.findByText(/No projects yet/i)).toBeInTheDocument()
  })

  it('creates a project and shows it in the roster', async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValueOnce([]).mockResolvedValue([HALIBA])
    vi.mocked(createWorkflowProject).mockResolvedValue(HALIBA)

    render(<Projects />)
    await screen.findByText(/No projects yet/i)

    await userEvent.click(screen.getByRole('button', { name: /new project/i }))
    await userEvent.type(screen.getByLabelText(/^name/i), 'Haliba Field Development')
    await userEvent.type(screen.getByLabelText(/^code/i), 'HAL')
    await userEvent.type(screen.getByLabelText(/^client/i), 'Al Dhafra Petroleum')
    await userEvent.type(screen.getByLabelText(/^location/i), 'Haliba field, UAE')
    await userEvent.type(screen.getByLabelText(/live period start/i), '2026-01-01')
    await userEvent.type(screen.getByLabelText(/live period end/i), '2029-12-31')
    await userEvent.click(screen.getByRole('button', { name: /create project/i }))

    await waitFor(() =>
      expect(createWorkflowProject).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'Haliba Field Development', code: 'HAL' }),
      ),
    )
    expect(await screen.findByText('Haliba Field Development')).toBeInTheDocument()
  })

  it("shows the server's own message when creation is refused", async () => {
    vi.mocked(fetchWorkflowProjects).mockResolvedValue([])
    vi.mocked(createWorkflowProject).mockRejectedValue(new Error('Project code HAL is already in use.'))

    render(<Projects />)
    await userEvent.click(await screen.findByRole('button', { name: /new project/i }))
    await userEvent.type(screen.getByLabelText(/^name/i), 'Haliba')
    await userEvent.type(screen.getByLabelText(/^code/i), 'HAL')
    await userEvent.type(screen.getByLabelText(/^client/i), 'ADP')
    await userEvent.type(screen.getByLabelText(/^location/i), 'UAE')
    await userEvent.type(screen.getByLabelText(/live period start/i), '2026-01-01')
    await userEvent.type(screen.getByLabelText(/live period end/i), '2029-12-31')
    await userEvent.click(screen.getByRole('button', { name: /create project/i }))

    expect(await screen.findByText(/already in use/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd web && npm test -- Projects.test.tsx
```

Expected: FAIL — cannot resolve `./Projects`.

- [ ] **Step 3: Write the implementation**

`web/src/pages/forms.tsx` exports `ProjectForm`. Shape:

```tsx
export function ProjectForm({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial?: WorkflowProjectInput
  submitLabel: string
  onSubmit: (body: WorkflowProjectInput) => Promise<void>
  onCancel: () => void
}) {
  const [fields, setFields] = useState<WorkflowProjectInput>(initial ?? BLANK_PROJECT)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await onSubmit(fields)
    } catch (err) {
      // The server's own sentence, verbatim. Never "Something went wrong" —
      // the same rule the stage gates follow.
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  // …every field with an explicit <label htmlFor>, currency defaulting to 'AED'
}
```

Every input needs a real `<label htmlFor>`; the tests select by label, and a
placeholder-only field is unreachable by screen reader.

`web/src/pages/Projects.tsx`:

```tsx
export function Projects(): JSX.Element {
  const [tick, setTick] = useState(0)
  const [openProjectId, setOpenProjectId] = useState<string | null>(null)
  const [openItemId, setOpenItemId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const { data, error, loading } = useAsync(() => fetchWorkflowProjects(), [tick])

  // Drill-down first: these return before the roster renders, exactly as
  // RfqWorkflow returns its wizard before its table.
  if (openItemId && openProjectId) {
    return (
      <ItemDetail
        projectId={openProjectId}
        itemId={openItemId}
        onBack={() => setOpenItemId(null)}
      />
    )
  }
  if (openProjectId) {
    return (
      <ProjectDetail
        projectId={openProjectId}
        onOpenItem={setOpenItemId}
        onBack={() => { setOpenProjectId(null); setTick((t) => t + 1) }}
      />
    )
  }
  // …loading / error / empty / table, project name as a `linkish` button
}
```

Load-bearing: `onBack` from the detail screen bumps `tick`, so returning to the
roster shows counts that account for anything added or deleted while inside.
Reuse the existing `table`, `card`, `linkish`, `btn` and `input` classes — no
new CSS unless a control genuinely has none.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd web && npm test -- Projects.test.tsx
```

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/Projects.tsx web/src/pages/forms.tsx web/src/pages/Projects.test.tsx
git commit -m "feat: workflow projects roster with a create form"
```

---

### Task 7: Web — project detail, items, and delete

**Files:**
- Create: `web/src/pages/ProjectDetail.tsx`
- Modify: `web/src/pages/forms.tsx` (add `ItemForm`)
- Test: `web/src/pages/ProjectDetail.test.tsx`

**Interfaces:**
- Consumes: `fetchWorkflowProject`, `updateWorkflowProject`,
  `deleteWorkflowProject`, `createWorkflowItem`, `deleteWorkflowItem` (Task 5);
  `ProjectForm` (Task 6).
- Produces:
  - `ProjectDetail({ projectId, onOpenItem, onBack }): JSX.Element`
  - `ItemForm({ initial, submitLabel, onSubmit, onCancel })` in `forms.tsx`,
    with `initial?: WorkflowItemInput` and
    `onSubmit: (body: WorkflowItemInput) => Promise<void>`
- **Store invariant owned:** none — no server state beyond the fetchers it calls.

The one rule this screen must not break: **a refused delete renders its reason
beside the row that refused it.** Not a toast, not a generic banner. The 409's
`detail` names the RFQ that blocks it, which is the only actionable thing on the
screen at that moment.

- [ ] **Step 1: Write the failing test**

```typescript
// web/src/pages/ProjectDetail.test.tsx — mocks as in Projects.test.tsx

const GENERATOR = {
  id: 'itm_1',
  project_id: 'prj_1',
  item_type: 'Gas generator',
  description: '2 x 5 MW containerised',
  qty: 2,
  uom: 'no',
  discipline: 'Electrical',
  estimated_value_aed: 18_000_000,
  required_on_site: '2027-06-01',
  is_long_lead: true,
}

// `WorkflowProjectDetail.project` is a `WorkflowProject` — the roster's
// `item_count` / `rfq_count` are not on it, because the detail response
// carries the actual lists.
const HALIBA_PROJECT = {
  id: 'prj_1',
  name: 'Haliba Field Development',
  code: 'HAL',
  client: 'Al Dhafra Petroleum',
  location: 'Haliba field, UAE',
  live_period_start: '2026-01-01',
  live_period_end: '2029-12-31',
  currency: 'AED',
  status: 'Active' as const,
}

function detail(over: Partial<WorkflowProjectDetail> = {}): WorkflowProjectDetail {
  return { project: HALIBA_PROJECT, items: [GENERATOR], rfqs: [], ...over }
}

it('lists the project’s items', async () => {
  vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
  render(<ProjectDetail projectId="prj_1" onOpenItem={() => {}} onBack={() => {}} />)
  expect(await screen.findByText('Gas generator')).toBeInTheDocument()
  expect(screen.getByText('2 x 5 MW containerised')).toBeInTheDocument()
})

it('adds an item and reloads', async () => {
  vi.mocked(fetchWorkflowProject)
    .mockResolvedValueOnce(detail({ items: [] }))
    .mockResolvedValue(detail())
  vi.mocked(createWorkflowItem).mockResolvedValue({ ...GENERATOR, live_period_warning: null })

  render(<ProjectDetail projectId="prj_1" onOpenItem={() => {}} onBack={() => {}} />)
  await userEvent.click(await screen.findByRole('button', { name: /add item/i }))
  await userEvent.type(screen.getByLabelText(/item type/i), 'Gas generator')
  // …remaining fields
  await userEvent.click(screen.getByRole('button', { name: /save item/i }))

  expect(await screen.findByText('Gas generator')).toBeInTheDocument()
})

it('shows the live-period caution returned with a saved item', async () => {
  vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [] }))
  vi.mocked(createWorkflowItem).mockResolvedValue({
    ...GENERATOR,
    required_on_site: '2030-06-01',
    live_period_warning:
      '2030-06-01 falls outside the project live period (2026-01-01 to 2029-12-31)',
  })
  // …fill and submit
  expect(await screen.findByText(/falls outside the project live period/i)).toBeInTheDocument()
})

it('renders a refused delete next to the item that refused it', async () => {
  vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
  vi.mocked(deleteWorkflowItem).mockRejectedValue(
    new Error('This item cannot be deleted: it is covered by ADP-RFQ-2026-014. Amend or retender first.'),
  )

  render(<ProjectDetail projectId="prj_1" onOpenItem={() => {}} onBack={() => {}} />)
  await userEvent.click(await screen.findByRole('button', { name: /delete gas generator/i }))
  await userEvent.click(screen.getByRole('button', { name: /^delete$/i }))   // confirm

  const row = screen.getByText('Gas generator').closest('tr')!
  expect(within(row).getByText(/ADP-RFQ-2026-014/)).toBeInTheDocument()
  expect(screen.getByText('Gas generator')).toBeInTheDocument()   // still there
})
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd web && npm test -- ProjectDetail.test.tsx
```

Expected: FAIL — cannot resolve `./ProjectDetail`.

- [ ] **Step 3: Write the implementation**

`ProjectDetail` loads once via `useAsync(() => fetchWorkflowProject(projectId), [projectId, tick])`
and renders:

1. `PageHeader` with the project name, a back button, "Edit" and "Delete project".
2. Edit mode swaps the header detail for `ProjectForm` with `initial` filled from
   the loaded project, submitting `updateWorkflowProject(projectId, changes)`.
   Compute `changes` as **only the fields that differ from `initial`** — sending
   the whole object defeats the partial update server-side.
3. An items `Card`: table with a checkbox column, `item_type` as a `linkish`
   button calling `onOpenItem(item.id)`, and a per-row delete button labelled
   `Delete ${item.item_type}` so the accessible name is unique.
4. Per-row error state: `const [rowError, setRowError] = useState<Record<string, string>>({})`,
   keyed by **item id**, rendered inside that row. Keyed by id, never by index —
   the row order is not a contract.
5. Delete confirmation before the request, stating for a project how many items
   go with it. The confirmation is not the guard; the server is.
6. An RFQs `Card` listing `reference / package / discipline / stage`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd web && npm test -- ProjectDetail.test.tsx && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/ProjectDetail.tsx web/src/pages/forms.tsx web/src/pages/ProjectDetail.test.tsx
git commit -m "feat: project detail with items, editing and guarded deletion"
```

---

### Task 8: Web — raise an RFQ from selected items

**Files:**
- Modify: `web/src/pages/ProjectDetail.tsx`, `web/src/pages/forms.tsx`
- Test: `web/src/pages/ProjectDetail.test.tsx`

**Interfaces:**
- Consumes: `createRfq` (Task 5); the item selection state from Task 7.
- Produces: `RaiseRfqForm({ itemIds, projectId, onSubmit, onCancel })` in
  `forms.tsx`, `onSubmit: (body: RfqInput) => Promise<void>`.
- **Store invariant owned:** none client-side. The server's existing
  `create_rfq` already refuses an item from another project with 422, and this
  screen surfaces that rather than pre-empting it.

This is the step that closes the loop the spec names: until now no RFQ could be
created from the browser at all.

- [ ] **Step 1: Write the failing test**

```typescript
it('raises an RFQ covering the ticked items', async () => {
  vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
  vi.mocked(createRfq).mockResolvedValue({ /* an Rfq */ })

  render(<ProjectDetail projectId="prj_1" onOpenItem={() => {}} onBack={() => {}} />)
  await userEvent.click(await screen.findByRole('checkbox', { name: /select gas generator/i }))
  await userEvent.click(screen.getByRole('button', { name: /raise rfq/i }))
  await userEvent.type(screen.getByLabelText(/reference/i), 'ADP-RFQ-2026-014')
  await userEvent.type(screen.getByLabelText(/package/i), 'Power generation')
  await userEvent.type(screen.getByLabelText(/discipline/i), 'Electrical')
  await userEvent.type(screen.getByLabelText(/value estimate/i), '18000000')
  await userEvent.click(screen.getByRole('button', { name: /create rfq/i }))

  await waitFor(() =>
    expect(createRfq).toHaveBeenCalledWith({
      project_id: 'prj_1',
      item_ids: ['itm_1'],
      reference: 'ADP-RFQ-2026-014',
      package: 'Power generation',
      discipline: 'Electrical',
      value_estimate_aed: 18000000,
    }),
  )
})

it('cannot raise an RFQ with nothing selected', async () => {
  vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
  render(<ProjectDetail projectId="prj_1" onOpenItem={() => {}} onBack={() => {}} />)
  expect(await screen.findByRole('button', { name: /raise rfq/i })).toBeDisabled()
})

it("surfaces the server's refusal", async () => {
  vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
  vi.mocked(createRfq).mockRejectedValue(
    new Error('Item itm_1 does not belong to project prj_1'),
  )
  // …select, open, fill, submit
  expect(await screen.findByText(/does not belong to project/i)).toBeInTheDocument()
})
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd web && npm test -- ProjectDetail.test.tsx
```

Expected: FAIL — no `Raise RFQ` button.

- [ ] **Step 3: Write the implementation**

Selection is `const [selected, setSelected] = useState<Set<string>>(new Set())`,
holding **item ids**. The "Raise RFQ" button is `disabled={selected.size === 0}`
— an RFQ covering no item is meaningless, and the server would refuse it anyway.

`value_estimate_aed` is `parseInt(raw, 10)`; the field is `type="number"` and the
form refuses to submit when it is `NaN`, with its own message. After a successful
create, clear `selected`, bump `tick` so the RFQ table reloads, and leave the
user on the project page — they can click into the wizard from the RFQ row.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd web && npm test -- ProjectDetail.test.tsx && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/ProjectDetail.tsx web/src/pages/forms.tsx web/src/pages/ProjectDetail.test.tsx
git commit -m "feat: raise an RFQ from selected project items"
```

---

### Task 9: Web — item detail

**Files:**
- Create: `web/src/pages/ItemDetail.tsx`
- Test: `web/src/pages/ItemDetail.test.tsx`

**Interfaces:**
- Consumes: `fetchWorkflowProject`, `updateWorkflowItem` (Task 5); `ItemForm`
  (Task 7).
- Produces: `ItemDetail({ projectId, itemId, onBack }): JSX.Element`
- **Store invariant owned:** none.

It reads the same `fetchWorkflowProject` response and selects its item by `id`,
rather than adding a per-item route. One document, one generation — and the
"RFQs covering this item" list has to be filtered from the project's RFQs
anyway, so a per-item route would have to return them too.

- [ ] **Step 1: Write the failing test**

```typescript
// Reuse HALIBA_PROJECT and GENERATOR from Task 7's fixtures; CABLE is the same
// shape with id 'itm_2' and item_type 'HV cable'.
function rfq(id: string, reference: string, itemIds: string[]): Rfq {
  return {
    id,
    reference,
    project_id: 'prj_1',
    item_ids: itemIds,
    package: 'Power generation',
    discipline: 'Electrical',
    value_estimate_aed: 18_000_000,
    stage: 'Scoping',
    history: [
      {
        from_stage: null,
        to_stage: 'Scoping',
        at: '2026-08-12T09:00:00Z',
        by: 'system',
        reason: 'RFQ created',
      },
    ],
  }
}

it('shows the item and the RFQs covering it', async () => {
  vi.mocked(fetchWorkflowProject).mockResolvedValue({
    project: HALIBA_PROJECT,
    items: [GENERATOR, CABLE],
    rfqs: [
      rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1']),
      rfq('rfq_2', 'ADP-RFQ-2026-015', ['itm_2']),
    ],
  })

  render(<ItemDetail projectId="prj_1" itemId="itm_1" onBack={() => {}} />)

  expect(await screen.findByText('Gas generator')).toBeInTheDocument()
  expect(screen.getByText('ADP-RFQ-2026-014')).toBeInTheDocument()
  // The other RFQ covers a different item and must not appear.
  expect(screen.queryByText('ADP-RFQ-2026-015')).not.toBeInTheDocument()
})

it('404s gracefully when the item is not in this project', async () => {
  vi.mocked(fetchWorkflowProject).mockResolvedValue({
    project: HALIBA_PROJECT, items: [], rfqs: [],
  })
  render(<ItemDetail projectId="prj_1" itemId="itm_missing" onBack={() => {}} />)
  expect(await screen.findByText(/could not be found/i)).toBeInTheDocument()
})

it('edits the item and shows the live-period caution', async () => {
  // updateWorkflowItem resolves with live_period_warning set; assert it renders
})
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd web && npm test -- ItemDetail.test.tsx
```

- [ ] **Step 3: Write the implementation**

Filter with `rfqs.filter((r) => r.item_ids.includes(itemId))` — by id, and note
that an RFQ may cover several items, so this is not a `find`. The
item-not-in-project case renders an `EmptyState`, not a crash: the id can be
stale if the item was deleted in another tab.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd web && npm test -- ItemDetail.test.tsx && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/ItemDetail.tsx web/src/pages/ItemDetail.test.tsx
git commit -m "feat: item detail with the RFQs covering it"
```

---

### Task 10: Wire it into the shell, and re-measure the counts

**Files:**
- Modify: `web/src/App.tsx`, `web/src/App.test.tsx`, `web/src/pages/Dashboard.tsx`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `Projects` (Task 6).
- Produces: the `'projects'` value of `App`'s `View` union, and the nav table of
  spec §3.
- **Store invariant owned:** none.

- [ ] **Step 1: Write the failing test**

Update `web/src/App.test.tsx`:

```typescript
it('mounts the projects screen on first render', async () => {
  // Replaces 'mounts the RFQ workflow screen on first render' — the landing
  // screen moves one step up the same half of the app (spec §3).
  render(<App />)
  expect(await screen.findByText(/Projects & items/i)).toBeInTheDocument()
  expect(navButton(/Projects & items/)).toHaveClass('active')
})

it('has exactly one nav entry whose label is about projects in each group', () => {
  // Guards the rename: two entries reading "Projects" over two different
  // stores is the confusion §3 exists to prevent.
  render(<App />)
  expect(navButton(/^Bid sets$/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /^Projects$/ })).not.toBeInTheDocument()
})
```

The existing mocks in that file stay exactly as they are. In particular
`auth/context`'s `useAuth` must keep returning the **stable** object built once
outside the `vi.mock` factory — a fresh identity per call makes `App` refetch
the roster until the vitest worker dies of heap exhaustion, which surfaces as
`Worker exited unexpectedly` rather than a failed assertion.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd web && npm test -- App.test.tsx
```

Expected: FAIL — no "Projects & items" entry.

- [ ] **Step 3: Write the implementation**

In `App.tsx`: add `'projects'` to `View`; insert the nav row and renumber:

```typescript
  { view: 'projects', index: '01', label: 'Projects & items', group: 'RFQ process', needsProject: false, needsReview: false },
  { view: 'workflow', index: '02', label: 'RFQ workflow', group: 'RFQ process', needsProject: false, needsReview: false },
  { view: 'dashboard', index: '03', label: 'Bid sets', group: 'Bid evaluation', needsProject: false, needsReview: false },
  { view: 'setup', index: '04', label: 'Set up & ingest', … },
  { view: 'extraction', index: '05', … },
  { view: 'overview', index: '06', … },
  { view: 'matrix', index: '07', … },
  { view: 'statement', index: '08', … },
  { view: 'admin', index: '09', … },
```

Change the initial view to `useState<View>('projects')` and update the comment
above it — the reason is now that the project is the top-level container, not
merely that ingestion is not the process. Render `{view === 'projects' && <Projects />}`
beside the existing `{view === 'workflow' && <RfqWorkflow />}`. Neither
`needsProject` nor `needsReview` applies: both gate on the *ingestion* project.

In `Dashboard.tsx`, change the `PageHeader` eyebrow from `"Portfolio"` to
`"Bid sets"`, matching its nav label.

- [ ] **Step 4: Run everything**

```bash
cd web && npm test && npm run build
```

```bash
python -m pytest
```

Both green. Then re-measure and update CLAUDE.md's two rows:

- Take the **workstation** figure from the `python -m pytest` run above — it is a
  measurement, not a derivation, and this environment has `pdftotext`, `data/`
  and an ingested multi-vendor `projects/`.
- Derive the CI row from it by the subtraction that file documents:
  `CI passed = workstation passed − 4 − 3 − 2`, `CI skipped = 3 + 4 + 3 + 2`.
- Never edit the two rows independently; that is how they drifted apart before.
- Add one sentence to the RFQ-workflow paragraph naming the new tests, in the
  style of the existing "The jump from 1097 is the RFQ workflow" sentence.
- Update the web-suite figure (currently "70 passed across 10 files") from the
  `npm test` run.

- [ ] **Step 5: Commit**

```bash
git add web/src/App.tsx web/src/App.test.tsx web/src/pages/Dashboard.tsx CLAUDE.md
git commit -m "feat: projects and items as the platform's front door"
```

---

## Definition of done

- [ ] `python -m pytest` green, and CLAUDE.md's two rows match the measurement.
- [ ] `cd web && npm test` green; `npm run build` type-checks.
- [ ] `test_the_allowlist_is_exactly_these_three_paths` still passes — no new
      route reached `PUBLIC_PATHS`.
- [ ] `grep -rn "rename_project"` returns nothing.
- [ ] Every matrix row in Task 4 was verified by reinstating its defect, and all
      reinstated defects are reverted.
- [ ] The app runs from `./run.ps1`, and the walk works end to end: sign in →
      Projects & items → New project → Add item → tick it → Raise RFQ → the RFQ
      opens in the phase-1 wizard.
