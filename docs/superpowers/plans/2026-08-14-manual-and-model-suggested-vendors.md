# Manual and model-suggested vendors — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a buyer add a vendor to an item by hand, ask the model to suggest
vendors it has never heard of, and open a covering RFQ from the item screen.

**Architecture:** Two more members of `VendorListSource`, reusing the record and
the cards built last phase. The suggestion route stores nothing and calls the
existing `shared/llm/` interface, so tests stay key-free through
`MockLLMClient`. The `›` control is a prop.

**Tech Stack:** FastAPI + Pydantic over `shared/llm/`, React 19 + TypeScript.

**Spec:** [`docs/superpowers/specs/2026-08-14-manual-and-model-suggested-vendors-design.md`](../specs/2026-08-14-manual-and-model-suggested-vendors-design.md)

## Global Constraints

- **No route here creates a bidder, grants an approval, or sets `vendor_id`.**
- **The model reads; code decides.** It is never asked whether a vendor is
  eligible, approved or preferable.
- **Nothing the model returns is stored** until a human adds it, one at a time.
- **The label matches the mechanism.** "Suggested by the model", never "found on
  the internet" — these providers cannot browse.
- **Tests are key-free.** `MockLLMClient` only; no test may need a provider key.
- **Measured baselines to move from:** Python **1641 passed, 14 skipped**; web
  **271 passed across 20 files**. ⚠️ The 14 includes **11 spurious skips** from
  the renamed AVL fixture — resolve that before writing a baseline row.

---

## File structure

| file | responsibility | task |
|---|---|---|
| `workflow/models/project.py` | Modify — two more `VendorListSource` members | 1 |
| `workflow/store.py` | Modify — `add_`/`remove_item_vendor_entry`, and the source guards | 1 |
| `shared/llm/prompts/vendor_search_v1.txt` | **Create** — the versioned prompt | 2 |
| `workflow/vendor_suggestions.py` | **Create** — the pure call and its schema | 2 |
| `api/workflow_routes.py` | Modify — three routes | 3 |
| `web/src/pages/ItemDetail.tsx` | Modify — two more cards, add/remove, the `›` | 4, 5, 6 |
| `web/src/routes.tsx` | Modify — `onOpenRfq` | 6 |
| `CLAUDE.md` | Modify — invariants and baselines | 7 |

---

## Task 1: Two more sources, and the append/replace fork

**Files:**
- Modify: `workflow/models/project.py`, `workflow/store.py`
- Test: `tests/test_item_vendor_lists.py`, `tests/test_workflow_persistence.py`

**Interfaces:**
- Produces: `VendorListSource = Literal["Client", "Astra", "Manual", "Suggested"]`;
  `store.add_item_vendor_entry(item_id, entry) -> ItemVendorEntry`;
  `store.remove_item_vendor_entry(item_id, entry_id) -> None`.
- **Store invariant owned:** unchanged from last phase — `workflow.json` holds
  exactly the vendor-list entries of items that still exist. The two new
  sources are pruned by the same cascade, which is what the new matrix row
  checks.

The uploaded sources replace wholesale; the two new ones accumulate. Two
methods rather than one with a flag, and **each refuses the other's sources**:
an upload that wiped a buyer's hand-added companies would destroy work with no
undo, and a hand-add that appended to an uploaded list would leave that list no
longer matching its source document.

- [ ] **Step 1: Write the failing tests**

```python
def test_hand_added_vendors_accumulate_rather_than_replace():
    store, item = _store_with_item()
    store.add_item_vendor_entry(item.id, _entry(item.id, "Manual", "First Co"))
    store.add_item_vendor_entry(item.id, _entry(item.id, "Manual", "Second Co"))

    names = [e.vendor_name for e in store.item_vendor_list(item.id, "Manual")]
    assert names == ["First Co", "Second Co"]


def test_an_upload_cannot_replace_the_hand_added_list():
    """It would destroy a buyer's work with no undo."""
    store, item = _store_with_item()
    store.add_item_vendor_entry(item.id, _entry(item.id, "Manual", "First Co"))

    with pytest.raises(ValueError, match="Manual"):
        store.set_item_vendor_list(item.id, "Manual", [])


def test_a_hand_add_cannot_append_to_an_uploaded_list():
    """It would leave the export no longer matching its source document."""
    store, item = _store_with_item()

    with pytest.raises(ValueError, match="Client"):
        store.add_item_vendor_entry(item.id, _entry(item.id, "Client", "X"))


def test_a_vendor_is_removed_by_id_never_by_name():
    """Two suppliers can share a trading name."""
    store, item = _store_with_item()
    a = store.add_item_vendor_entry(item.id, _entry(item.id, "Manual", "Same Name"))
    store.add_item_vendor_entry(item.id, _entry(item.id, "Manual", "Same Name"))

    store.remove_item_vendor_entry(item.id, a.id)

    assert len(store.item_vendor_list(item.id, "Manual")) == 1


def test_an_uploaded_row_cannot_be_removed_one_at_a_time():
    """Correcting an export means re-uploading it."""
    store, item = _store_with_item()
    store.set_item_vendor_list(item.id, "Client", [_entry(item.id, "Client", "X")])
    stored = store.item_vendor_list(item.id, "Client")[0]

    with pytest.raises(ValueError, match="Client"):
        store.remove_item_vendor_entry(item.id, stored.id)
```

Plus, in `test_workflow_persistence.py`, one more matrix row:

```python
def test_deleting_an_item_takes_its_hand_added_vendors_too(tmp_path):
    """The cascade is per item, not per source — a source added later must not
    need the cascade extending to cover it."""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_item_vendor_lists.py -v`
Expected: FAIL — `ValidationError` on source `"Manual"`, then
`AttributeError: add_item_vendor_entry`.

- [ ] **Step 3: Write minimal implementation**

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the
> implementation against the test.
>
> **Load-bearing:** the two guards and their messages, and removal by id.
> **Illustrative:** the exact wording.

```python
#: Sources that arrive as a whole document, and are replaced by re-uploading it.
UPLOADED_SOURCES = ("Client", "Astra")
#: Sources built up one vendor at a time by a person.
CURATED_SOURCES = ("Manual", "Suggested")
```

```python
def add_item_vendor_entry(self, item_id, entry):
    """Append one vendor. For the curated sources only.

    `Client` and `Astra` are refused because they are documents: appending to
    one would leave it no longer matching the export it came from, and the
    screen would show a list no re-upload reproduces.
    """
    if entry.source not in CURATED_SOURCES:
        raise ValueError(
            f"{entry.source} is an uploaded list — re-upload it to change it, "
            "rather than adding vendors to it one at a time."
        )
```

and the mirror guard in `set_item_vendor_list`.

- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

---

## Task 2: The suggestion module and its prompt

**Files:**
- Create: `shared/llm/prompts/vendor_search_v1.txt`, `workflow/vendor_suggestions.py`
- Test: `tests/test_vendor_suggestions.py` *(create)*

**Interfaces:**
- Consumes: any client with `classify_structure(prompt, output_schema, context_text)`.
- Produces: `SuggestedVendor`, `SuggestedVendors`,
  `suggest(client, *, discipline, description, exclude) -> list[SuggestedVendor]`.
- **Store invariant owned:** none — this module stores nothing and has no store.
  Its guarantee is the negative one: **it returns candidates and writes nothing.**

The client is **passed in**, not fetched, so the tests hand it a
`MockLLMClient` and never touch `get_client` or a key.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_response_omitting_the_array_reads_as_no_vendors():
    """CLAUDE.md records this exact defect (I5): an omitted optional array read
    as a failure rather than as no results. A model asked about an obscure
    discipline may legitimately name none."""
    client = MockLLMClient(response={})

    assert suggest(client, discipline="Cables", description="11 kV", exclude=[]) == []


def test_names_already_on_the_item_are_filtered_in_python():
    """The prompt asks; only the filter guarantees. A model that ignores the
    instruction must not put a duplicate on screen."""
    client = MockLLMClient(response={"vendors": [
        {"name": "Al Munara Switchgear LLC"}, {"name": "New Co"}]})

    found = suggest(client, discipline="Cables", description="",
                    exclude=["AL MUNARA SWITCHGEAR LLC"])

    assert [v.name for v in found] == ["New Co"]


def test_the_model_is_never_asked_for_a_verdict():
    """The rule the extractors keep: the model reads, code decides. A prompt
    that asked whether a vendor is suitable would be a judgement made where it
    cannot be reviewed."""
    client = MockLLMClient(response={"vendors": []})
    suggest(client, discipline="Cables", description="", exclude=[])

    prompt = client.last_call["prompt"].lower()
    for word in ("approved", "eligible", "qualified", "recommend", "best", "rank"):
        assert word not in prompt


def test_a_provider_failure_is_raised_rather_than_read_as_no_vendors():
    """An outage and "no such vendors exist" must not look the same."""
    class Failing:
        def classify_structure(self, **_): raise RuntimeError("provider is down")

    with pytest.raises(RuntimeError):
        suggest(Failing(), discipline="Cables", description="", exclude=[])


def test_a_suggestion_carries_no_approval_and_no_registry_link():
    """A model-named company claiming ADNOC approval is the failure
    `test_no_invented_vendor_claims_the_clients_approval` guards, arriving
    through a new door."""
    fields = set(SuggestedVendor.model_fields)
    assert "approved_by" not in fields
    assert "vendor_id" not in fields
    assert "prequal_status" not in fields
```

- [ ] **Step 2: Run tests to verify they fail** — `ModuleNotFoundError`
- [ ] **Step 3: Write minimal implementation**

> Load-bearing: the `[]` default on `vendors`, the Python-side `exclude` filter
> folded through `disciplines.fold`, and letting a provider error propagate.
> Illustrative: the prompt's wording, which lives in the file.

The prompt file states plainly that a company the model is not confident exists
must be omitted, and that no approval, qualification or ranking is to be
asserted. It is versioned like every other prompt so a change to it is a
reviewable diff.

- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

---

## Task 3: The three routes

**Files:**
- Modify: `api/workflow_routes.py`
- Test: `tests/test_item_vendor_endpoints.py`

**Interfaces:**
- Produces:
  - `POST /projects/{p}/items/{i}/vendors` → the stored entry
  - `DELETE /projects/{p}/items/{i}/vendors/{entry_id}` → 204
  - `POST /projects/{p}/items/{i}/vendor-suggestions` → `{vendors: [...]}`
- **Store invariant owned:** none new. The guarantee asserted here is that the
  suggestion route **stores nothing** and that a hand-added vendor **never gets
  a `vendor_id`**.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_blank_vendor_name_is_refused(...)
def test_a_hand_added_vendor_never_links_to_the_registry(...)
    """Even when a registry row has that exact name. A lookup here would attach
    a real company's approvals to whatever somebody typed."""
def test_deleting_an_uploaded_row_is_refused(...)
def test_the_suggestion_route_stores_nothing(...)
    """The document is byte-identical before and after."""
def test_a_suggestion_can_then_be_added_by_hand(...)
```

The suggestion route's tests monkeypatch `get_client` to return a
`MockLLMClient`, so no key is needed.

- [ ] Steps 2–5 as usual.

---

## Task 4: Adding and removing on the item screen

**Files:** `web/src/pages/ItemDetail.tsx`, `web/src/api.ts`, `web/src/types.ts`
**Test:** `web/src/pages/ItemDetail.test.tsx`

Four cards instead of two. `VendorListCard` gains an optional `onRemove`,
passed only for the curated sources — the uploaded cards render no remove
control, matching the store's refusal rather than relying on it.

- [ ] Tests: the add form refuses a blank name client-side too; a removed row
      disappears; uploaded cards have no remove control; a refusal renders
      beside the row it refused.

---

## Task 5: The suggestion card

**Files:** `web/src/pages/ItemDetail.tsx`
**Test:** `web/src/pages/ItemDetail.test.tsx`

- [ ] Tests: the caption says *suggested by the model … not a web search*; each
      row has its own **Add**; there is **no** bulk add; a failed call shows the
      error rather than an empty list; the disabled *From the internet* chip is
      gone.

**No bulk add, deliberately.** Accepting twenty unverified companies in one
click is exactly the act that needs friction.

---

## Task 6: The `›` control

**Files:** `web/src/pages/ItemDetail.tsx`, `web/src/routes.tsx`
**Test:** `web/src/pages/ItemDetail.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
it('opens a covering RFQ from its row', async () => {
  // The accessible name is the reference, not the glyph — the same rule the
  // vendor row button follows.
  fireEvent.click(await screen.findByRole('button', { name: /open ADP-RFQ-2026-014/i }))
  expect(onOpenRfq).toHaveBeenCalledWith('rfq_1')
})
```

- [ ] Steps 2–5. `ItemRoute` already holds `useNavigate`; `/rfqs/:rfqId` already
      exists and already picks the wizard or the read-only screen by stage.

---

## Task 7: Invariants and baselines

**Files:** `CLAUDE.md`

- [ ] **Step 1: Resolve the AVL fixture rename first.** 11 `needs_real_avl`
      tests are skipping because `tests/test_avl_import.py:240` names the old
      filename. A baseline measured with those spuriously skipping is wrong.
- [ ] **Step 2: Measure both suites**, workstation measured and CI derived by
      the documented subtraction.
- [ ] **Step 3: Record the invariants**
  - `VendorListSource` has four members, two uploaded and two curated, and the
    store refuses each set the other's operations.
  - The suggestion route stores nothing; suggestions carry no approval, no
    registry link and no prequalification.
  - **The label matches the mechanism**: these providers cannot browse, so the
    screen says "suggested by the model", and the honest-labelling rule now has
    a fourth instance beside the AVL import, the mock rounds and the RFQ
    extractor.

---

## Self-review

**Spec coverage.** §1 → Task 5's caption test and Task 7's note. §2 → Task 1.
§3 → Task 3. §4 → Tasks 2 and 3. §5 → Tasks 4 and 5. §6 → Task 6. §7 is
non-changes. §8 is distributed. §9 is the open question below.

**Type consistency.** `VendorListSource` is the same four-member literal in
Tasks 1, 3 and 4. `SuggestedVendor` has no `vendor_id`, so a suggestion cannot
be mistaken for a registry row anywhere.

**Ordering note.** The prompt and the pure module (Task 2) come before the route
(Task 3), so what the model is asked — the part that cannot be reviewed once it
is buried in a request — is settled and tested on its own first.

**Open question carried from the spec §9:** whether an accepted suggestion stays
labelled `Suggested` or is promoted to `Manual`. This plan implements *stays
labelled*. Flipping it later is a one-line change in Task 3's add route plus a
test.
