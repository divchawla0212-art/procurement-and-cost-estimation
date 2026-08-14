# Per-item client and Astra vendor lists — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upload the client's Approved Vendor List and Astra's subset against a
single item, narrow each to that item's discipline, and store the result as that
item's own vendor list — linked to the registry, never adding to it.

**Architecture:** No new parser: `avl_import.parse_avl` reads both uploads as
they are. A new item-scoped collection lives in `workflow.json` beside the other
item- and RFQ-scoped ones, so the delete cascade stays in the single write path
that already exists. The discipline filter is what makes the list item-specific
and what keeps a 1 346-vendor export down to tens of rows.

**Tech Stack:** FastAPI + Pydantic + openpyxl (pytest, `TestClient`) with React
19 + TypeScript (vitest) on top.

**Spec:** [`docs/superpowers/specs/2026-08-14-item-vendor-nominations-design.md`](../specs/2026-08-14-item-vendor-nominations-design.md)

## Global Constraints

- **No upload ever writes `approved_by`, touches `bidder_approvals`, or creates
  a bidder.** The registry changes only through `python -m workflow.avl_db`.
- **Every write, and every read that gates one, happens inside
  `persistence.locked_update`.**
- **Matching is an exact id lookup** on `bdr_<vendor_number>`. No name
  comparison, no substring matching.
- **A stored collection contains exactly the records of its currently-live
  source.** A second upload of a source replaces that source wholesale.
- **Refusals are the store's own sentence**, surfaced verbatim.
- **Measured baselines to move from:** Python **1629 passed, 3 skipped**; web
  **265 passed across 20 files**. CI derived by `-4 -3 -2 -11`.
- **Tests build workbooks in memory with `openpyxl`**, as
  `test_avl_import.py`'s non-gated tests do. Nothing new reads the real export,
  so the AVL gate stays at **eleven** and is not re-measured.

---

## File structure

| file | responsibility | task |
|---|---|---|
| `workflow/models/project.py` | Modify — `ItemVendorEntry`, `VendorListSource`, `new_item_vendor_entry_id` beside `Item` | 1 |
| `workflow/item_vendor_lists.py` | **Create** — the pure narrowing rule, no store and no I/O | 2 |
| `workflow/store.py` | Modify — `set_item_vendor_list`, `item_vendor_list`, and the two cascades | 3 |
| `workflow/persistence.py` | Modify — the collection in `to_document` **and** `from_document` | 3 |
| `api/workflow_routes.py` | Modify — the upload route | 4 |
| `web/src/api.ts`, `web/src/types.ts` | Modify — the uploader and its types | 4 |
| `web/src/pages/forms.tsx` | Modify — the two buttons, edit-only | 5 |
| `web/src/pages/ItemDetail.tsx` | Modify — a card per source | 6 |
| `tests/test_workflow_persistence.py` | Modify — the mutation matrix | 7 |
| `CLAUDE.md` | Modify — baselines and the new invariant | 7 |

---

## Task 1: The model

**Files:**
- Modify: `workflow/models/project.py` (beside `Item`, ~line 29–50)
- Test: `tests/test_item_vendor_lists.py` *(create)*

**Interfaces:**
- Produces: `VendorListSource = Literal["Client", "Astra"]`;
  `ItemVendorEntry`; `new_item_vendor_entry_id() -> str`.
- **Store invariant owned:** none yet — this task defines the record; Task 3
  owns what the store must hold.

`Item` lives in `models/project.py`, not a module of its own, so the entry goes
beside it rather than into a new file for one class.

- [ ] **Step 1: Write the failing test**

```python
def test_an_entry_records_the_export_name_and_the_registry_link():
    entry = ItemVendorEntry(
        item_id="itm_1", source="Client", vendor_id="bdr_10007739",
        vendor_name="ABU DHABI CABLE FACTORY",
        trade_categories=["CABLES - LV POWER DISTRIBUTION"],
        uploaded_by="buyer@example.com", uploaded_at=datetime(2026, 8, 14, 9, 0),
        source_document="avl.xlsx",
    )
    assert entry.id.startswith("ive_")
    # Not in the registry is a real, distinct state.
    unlinked = entry.model_copy(update={"vendor_id": None})
    assert unlinked.vendor_id is None
```

```python
def test_the_source_is_one_of_two_and_nothing_else():
    with pytest.raises(ValidationError):
        ItemVendorEntry(item_id="itm_1", source="Contractor", vendor_id=None,
                        vendor_name="X", trade_categories=[],
                        uploaded_by="a@b.c", uploaded_at=datetime(2026, 8, 14),
                        source_document="x.xlsx")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_item_vendor_lists.py -v`
Expected: FAIL — `ImportError: cannot import name 'ItemVendorEntry'`.

- [ ] **Step 3: Write minimal implementation**

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the
> implementation against the test.
>
> **Load-bearing:** the `Literal` source, `vendor_id` being optional, and the
> absence of any copied approval. **Illustrative:** the id prefix.

```python
def new_item_vendor_entry_id() -> str:
    return f"ive_{uuid4().hex[:8]}"


VendorListSource = Literal["Client", "Astra"]


class ItemVendorEntry(BaseModel):
    """One vendor on one item's list, from one of the two uploads.

    `vendor_id` is the export's own vendor number as `bdr_<number>` — the key
    `avl_import` already assigns — so matching the registry is an exact lookup
    rather than a name comparison. `None` means the export named a vendor the
    registry does not hold: a reportable state, and not the same as
    "unapproved".

    There is deliberately no `approved` field and no copy of the linked
    bidder's status: those are read live through `vendor_id`, the same rule
    `client_approved` keeps on a shortlist row.
    """

    id: str = Field(default_factory=new_item_vendor_entry_id)
    item_id: str
    source: VendorListSource
    vendor_id: str | None
    vendor_name: str
    trade_categories: list[str] = Field(default_factory=list)
    uploaded_by: str
    uploaded_at: datetime
    source_document: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_item_vendor_lists.py -v` → PASS

- [ ] **Step 5: Commit**

```bash
git add workflow/models/project.py tests/test_item_vendor_lists.py
git commit -m "feat: a record for one vendor on one item's list"
```

---

## Task 2: The narrowing rule

**Files:**
- Create: `workflow/item_vendor_lists.py`
- Test: `tests/test_item_vendor_lists.py`

**Interfaces:**
- Consumes: `disciplines.product_groups`, `Bidder`, `ItemVendorEntry`.
- Produces:
  ```python
  def entries_for(
      bidders: Iterable[Bidder], *, item_id: str, discipline: str,
      source: VendorListSource, known_ids: Container[str],
      uploaded_by: str, uploaded_at: datetime, source_document: str,
  ) -> list[ItemVendorEntry]
  ```
- **Store invariant owned:** none — pure. No store, no I/O, no clock; the clock
  and the registry are passed in, the way `workflow/bidders.py` is written.

**This is the function that makes the list item-specific.** Without the filter a
client upload attaches all 1 346 vendors to one line of equipment.

`known_ids` is passed in rather than a store handle so the module stays pure and
the caller decides what "in the registry" means.

- [ ] **Step 1: Write the failing tests**

```python
def test_only_vendors_registered_for_the_item_discipline_are_kept():
    """The whole point: the client export is 1 346 vendors and an item is one
    line of equipment."""
    cable = Bidder(id="bdr_1", name="Cable Co",
                   trade_categories=["CABLES - LV POWER DISTRIBUTION"])
    valve = Bidder(id="bdr_2", name="Valve Co",
                   trade_categories=['VALVES - BALL - API 6D - UP TO 12"'])

    entries = entries_for([cable, valve], item_id="itm_1", discipline="Cables",
                          source="Client", known_ids={"bdr_1", "bdr_2"},
                          uploaded_by="a@b.c", uploaded_at=NOW,
                          source_document="avl.xlsx")

    assert [e.vendor_name for e in entries] == ["Cable Co"]


def test_an_entry_carries_only_the_item_relevant_groups():
    """A cable supplier who also sells valves is on this item's list *for
    cables*; the valves would be noise on this screen."""
    both = Bidder(id="bdr_1", name="Both Co", trade_categories=[
        "CABLES - LV POWER DISTRIBUTION", 'VALVES - BALL - API 6D - UP TO 12"'])

    entries = entries_for([both], item_id="itm_1", discipline="Cables",
                          source="Client", known_ids={"bdr_1"},
                          uploaded_by="a@b.c", uploaded_at=NOW,
                          source_document="avl.xlsx")

    assert entries[0].trade_categories == ["CABLES - LV POWER DISTRIBUTION"]


def test_a_vendor_the_registry_does_not_hold_is_kept_and_marked():
    """Kept, because the export named them; unlinked, because we cannot show
    approvals we do not have. Never created as a bidder."""
    stranger = Bidder(id="bdr_9", name="Unknown Co",
                      trade_categories=["CABLES - LV POWER DISTRIBUTION"])

    entries = entries_for([stranger], item_id="itm_1", discipline="Cables",
                          source="Client", known_ids=set(),
                          uploaded_by="a@b.c", uploaded_at=NOW,
                          source_document="avl.xlsx")

    assert entries[0].vendor_id is None
    assert entries[0].vendor_name == "Unknown Co"


def test_a_discipline_matching_nothing_yields_an_empty_list_not_everything():
    """Items predating the vocabulary carry free text like "1". Falling back to
    the whole export would attach 1 346 vendors to one item."""
    cable = Bidder(id="bdr_1", name="Cable Co",
                   trade_categories=["CABLES - LV POWER DISTRIBUTION"])

    assert entries_for([cable], item_id="itm_1", discipline="1",
                       source="Client", known_ids={"bdr_1"}, uploaded_by="a@b.c",
                       uploaded_at=NOW, source_document="avl.xlsx") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_item_vendor_lists.py -v`
Expected: FAIL — `ModuleNotFoundError: workflow.item_vendor_lists`.

- [ ] **Step 3: Write minimal implementation**

> Load-bearing: the whole-string membership test (never substring), the
> per-entry group narrowing, and the empty-on-no-match behaviour.
> Illustrative: argument order.

```python
def entries_for(bidders, *, item_id, discipline, source, known_ids,
                uploaded_by, uploaded_at, source_document):
    """The item's list: the upload narrowed to what this item is scoped to.

    `discipline` is expanded through `workflow.disciplines` first, the same way
    `/bidders/available` expands it, so "Cables" reaches the eleven cable
    product groups the sheet names rather than looking for a group called
    "Cables" and finding none.

    A discipline that expands to nothing yields **no entries**, never the whole
    upload: falling back would attach a 1 346-vendor export to a single line of
    equipment, which is the opposite of what this list is for.
    """
    wanted = set(disciplines.product_groups(discipline))
    entries = []
    for bidder in bidders:
        # Whole-string membership. A plausible near-miss would put vendors on
        # an item's list who do not do the work, and look identical on screen.
        relevant = sorted(g for g in bidder.trade_categories if g in wanted)
        if not relevant:
            continue
        entries.append(ItemVendorEntry(
            item_id=item_id, source=source,
            vendor_id=bidder.id if bidder.id in known_ids else None,
            vendor_name=bidder.name, trade_categories=relevant,
            uploaded_by=uploaded_by, uploaded_at=uploaded_at,
            source_document=source_document,
        ))
    return entries
```

- [ ] **Step 4: Run tests to verify they pass** → PASS
- [ ] **Step 5: Commit**

```bash
git add workflow/item_vendor_lists.py tests/test_item_vendor_lists.py
git commit -m "feat: narrow an uploaded vendor list to an item's discipline"
```

---

## Task 3: The store and the cascade

**Files:**
- Modify: `workflow/store.py` — new dict in `__init__`, two methods, both cascades (~line 195–235)
- Modify: `workflow/persistence.py` — `to_document` **and** `from_document`
- Test: `tests/test_workflow_store.py`, `tests/test_workflow_persistence.py`

**Interfaces:**
- Produces: `store.set_item_vendor_list(item_id, source, entries) -> list[ItemVendorEntry]`;
  `store.item_vendor_list(item_id, source=None) -> list[ItemVendorEntry]`.
- **Store invariant owned:** `workflow.json` holds exactly the vendor-list
  entries of items that still exist. Deleting an item removes its entries in
  the same write; deleting a project removes the entries of every item it took.

**`CLAUDE.md` names this file's exact failure mode**: a field added to
`WorkflowStore.__init__` without a matching line in **both** `to_document` and
`from_document` silently fails to survive a restart. `_item_vendor_lists` is
such a field.

- [ ] **Step 1: Write the failing tests**

```python
def test_setting_a_source_replaces_only_that_source(tmp_path):
    """A second upload is a correction. Appending would leave a vendor dropped
    from the revised export indistinguishable from one still on it."""
    store, _ = populated_store()
    item = next(iter(store._items.values()))
    store.set_item_vendor_list(item.id, "Client", [entry(item.id, "Client", "A")])
    store.set_item_vendor_list(item.id, "Astra", [entry(item.id, "Astra", "B")])
    store.set_item_vendor_list(item.id, "Client", [entry(item.id, "Client", "C")])

    assert [e.vendor_name for e in store.item_vendor_list(item.id, "Client")] == ["C"]
    assert [e.vendor_name for e in store.item_vendor_list(item.id, "Astra")] == ["B"]


def test_deleting_an_item_takes_its_vendor_lists(tmp_path):
    store, _ = populated_store()
    item = store.create_item(project_id=..., ...)   # one with no covering RFQ
    store.set_item_vendor_list(item.id, "Client", [entry(item.id, "Client", "A")])

    store.delete_item(item.id)

    assert store.item_vendor_list(item.id) == []


def test_deleting_a_project_takes_its_items_vendor_lists(tmp_path):
    ...  # same shape, through delete_project


def test_a_vendor_list_survives_a_round_trip(tmp_path):
    """The failure mode CLAUDE.md names for this file: a field on __init__ with
    no matching line in *both* directions silently fails to survive."""
    store, _ = populated_store()
    item = next(iter(store._items.values()))
    store.set_item_vendor_list(item.id, "Client", [entry(item.id, "Client", "A")])
    persistence.save(str(tmp_path), store)

    loaded = persistence.load(str(tmp_path))
    assert [e.vendor_name for e in loaded.item_vendor_list(item.id, "Client")] == ["A"]
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: `AttributeError: 'WorkflowStore' object has no attribute
'set_item_vendor_list'`.

- [ ] **Step 3: Write minimal implementation**

> Load-bearing: replacement scoped to one source, the cascade materialising its
> id list before deleting, and the matching line in **both** persistence
> directions. Illustrative: the dict's internal shape.

```python
# in __init__
self._item_vendor_lists: dict[str, list[ItemVendorEntry]] = {}
```

```python
def set_item_vendor_list(self, item_id, source, entries):
    """Replace this item's entries **for one source**, leaving the other's.

    Wholesale within the source, for the same reason `save` replaces the
    document: a second upload is a correction, and appending would leave the
    previous export's vendors with nothing to mark them as stale.
    """
    if item_id not in self._items:
        raise KeyError(f"Unknown item: {item_id}")
    kept = [e for e in self._item_vendor_lists.get(item_id, []) if e.source != source]
    self._item_vendor_lists[item_id] = kept + list(entries)
    return self.item_vendor_list(item_id, source)
```

In `delete_item`, after the guard passes:

```python
    self._item_vendor_lists.pop(item_id, None)
```

In `delete_project`, inside the existing `for item_id in doomed:` loop — the
list is already materialised before the first deletion, which is the half that
must not be dropped.

In `persistence.to_document`, beside `"shortlists"`:

```python
    "item_vendor_lists": [
        e.model_dump(mode="json")
        for entries in store._item_vendor_lists.values()
        for e in entries
    ],
```

and the matching read in `from_document`, grouping by `item_id`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_workflow_store.py tests/test_workflow_persistence.py -q`

- [ ] **Step 5: Commit**

```bash
git add workflow/store.py workflow/persistence.py tests/test_workflow_store.py \
        tests/test_workflow_persistence.py
git commit -m "feat: an item's vendor lists, stored and cascaded"
```

---

## Task 4: The upload route

**Files:**
- Modify: `api/workflow_routes.py`
- Modify: `web/src/api.ts`, `web/src/types.ts`
- Test: `tests/test_item_vendor_endpoints.py` *(create)*

**Interfaces:**
- Produces: `POST /projects/{project_id}/items/{item_id}/vendor-list?source=`;
  `uploadItemVendorList(projectId, itemId, source, file)`.
- **Store invariant owned:** none new — Task 3 owns it. This task's guarantee is
  the negative one: **no upload writes `approved_by`, touches
  `bidder_approvals`, or creates a bidder.**

- [ ] **Step 1: Write the failing tests**

```python
def test_an_upload_stores_only_the_vendors_for_the_item_discipline(tmp_path, monkeypatch):
    ...  # in-memory workbook: one cable vendor, one valve vendor; item is Cables
    assert [e["vendor_name"] for e in body["entries"]] == ["Cable Co"]
    assert body["summary"] == {"parsed": 2, "kept": 1, "linked": 0}


def test_an_upload_creates_no_bidder_and_grants_no_approval(tmp_path, monkeypatch):
    """Section 1 of the spec, stated as an assertion. These are real exports, so
    the objection is not the evidence — it is that an item-form upload must not
    rewrite what a company is approved for platform-wide."""
    before = client.get("/api/workflow/bidders").json()["bidders"]
    ...  # upload
    after = client.get("/api/workflow/bidders").json()["bidders"]
    assert after == before


def test_an_unknown_source_is_refused(tmp_path, monkeypatch):
    r = _upload(client, source="Contractor")
    assert r.status_code == 422


def test_an_item_from_another_project_is_refused(tmp_path, monkeypatch): ...
def test_a_workbook_with_no_header_is_refused_naming_it(tmp_path, monkeypatch): ...
def test_a_second_upload_of_a_source_replaces_it(tmp_path, monkeypatch): ...
```

> Build the workbook with `openpyxl` in memory, the way
> `tests/test_avl_import.py`'s non-gated tests do — read one of them first and
> match its construction rather than inventing a second one.

- [ ] **Step 2: Run tests to verify they fail** → 404 on the route
- [ ] **Step 3: Write minimal implementation**

> Load-bearing: the whole thing runs inside `locked_update`; the uploaded bytes
> go to a temporary file because `parse_avl` takes a path; `uploaded_by` comes
> from the session and never the body.

```python
@router.post("/projects/{project_id}/items/{item_id}/vendor-list")
def upload_item_vendor_list(
    project_id: str, item_id: str,
    source: VendorListSource,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
) -> dict:
    """Read an Approved Vendor List export and store it as this item's list.

    No new parser: `parse_avl` reads both the client export and the Astra
    subset unmodified — same headers, same `Sheet1`, same `bdr_<number>` keys.

    **Nothing here touches the registry.** These are genuine exports, so the
    objection is not that the evidence is weak; it is that an upload attached
    to one item would otherwise rewrite what a company is approved for across
    every project, from a form whose subject is one line of equipment.
    """
```

The route resolves the item, checks its `project_id`, parses, calls
`entries_for(...)` with `known_ids` from the registry, and calls
`set_item_vendor_list` — all inside one `locked_update` block.

- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

---

## Task 5: The two upload buttons

**Files:**
- Modify: `web/src/pages/forms.tsx` — `ItemForm`'s actions
- Test: `web/src/pages/ProjectDetail.test.tsx`, `web/src/pages/ItemDetail.test.tsx`

**Interfaces:**
- Consumes: Task 4's `uploadItemVendorList`.
- **Store invariant owned:** none.

**Edit-only.** On the create form there is no item id to attach an upload to, so
both buttons are absent and a line says to save the item first.

Reuse the file control this phase already built — a hidden input behind a
**Choose file** button with one `role="status"` line — rather than a second
pattern.

- [ ] **Step 1: Write the failing tests**

```tsx
it('offers the two vendor lists only when editing an existing item', ...)
it('says to save the item first on the create form', ...)
it('uploads the client list against the item', ...)   // asserts the source arg
it("surfaces the server's refusal without clearing the form", ...)
```

- [ ] Steps 2–5 as usual.

---

## Task 6: A card per source on the item screen

**Files:**
- Modify: `web/src/pages/ItemDetail.tsx`
- Test: `web/src/pages/ItemDetail.test.tsx`

**Interfaces:**
- Consumes: the entries on the project payload (add them to
  `GET /projects/{id}` beside `items`, so the screen needs no extra round trip).
- **Store invariant owned:** none.

Each row: the vendor name as the export wrote it, `ApprovalPills` where it
linked, `not in the registry` where it did not. Summary:
`38 on the client's list for Cables · 34 in the registry · 4 not found`.

- [ ] **Step 1: Write the failing tests**

```tsx
it("lists the client's vendors for this item", ...)
it('marks a vendor the registry does not hold', ...)
it('shows the two sources separately', ...)
it('says when a source has not been uploaded', ...)
```

- [ ] Steps 2–5 as usual.

---

## Task 7: The mutation matrix and the baselines

**Files:**
- Modify: `tests/test_workflow_persistence.py` — the matrix section
- Modify: `CLAUDE.md`

**Interfaces:**
- **Store invariant owned:** the Task 3 invariant, defended here.

- [ ] **Step 1: Write the eight rows**

Each mutates the store between two runs and reads run 2 **from disk**, never
from the store object that made the change — the rule the existing matrix
section states.

| mutation between run 1 and run 2 | assert |
|---|---|
| an item holding a list is deleted | no entry in the reloaded document names it |
| a project holding such an item is deleted | same, for every item it took |
| the same source is uploaded again | the first upload's vendors are gone, not merged |
| the *other* source is uploaded | Client entries survive an Astra upload |
| a linked bidder is deleted from the registry | the row survives, reading as unlinked |
| a linked bidder's `approved_by` is corrected | the row follows, with no second write |
| the item's discipline is changed between uploads | the stored list reflects the discipline it was uploaded under |
| the upload raises partway through parsing | the previous entries are unchanged |

- [ ] **Step 2: Verify each row is real**

Reinstate each defect one at a time and confirm the intended row fails — **and
that nothing else does**. A row that still passes with its defect reinstated is
not testing what it claims.

- [ ] **Step 3: Measure both suites** — `python -m pytest -q`, `cd web && npm test`
- [ ] **Step 4: Update `CLAUDE.md`** — workstation measured, CI derived by the
      documented subtraction; the AVL gate stays at eleven and is **not**
      re-measured, because nothing here reads the real export.
- [ ] **Step 5: Commit**

---

## Self-review

**Spec coverage.** §1 → Task 4's no-approval assertion. §2 → Task 3. §3 → Task
1. §4 → Task 2 (`known_ids`). §5 → Task 2. §6 → Task 4. §7 → Tasks 5 and 6. §8
is measurement, already applied. §9 → Task 7. §10 is non-changes. No gaps.

**Type consistency.** `VendorListSource` is the same `Literal` in Tasks 1–4.
`entries_for` takes `known_ids: Container[str]` in Task 2 and is called with the
registry's ids in Task 4. `ItemVendorEntry.vendor_id` is `str | None`
throughout, and every renderer guards the `None`.

**Ordering note.** The pure rule (Task 2) is built before the store (Task 3) and
the route (Task 4), so the filter — the part that carries the real risk of
attaching 1 346 vendors to one item — is proven in isolation before anything
can persist its output.
