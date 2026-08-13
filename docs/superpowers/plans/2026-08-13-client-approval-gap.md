# Client approval gap — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface "this bidder is not on the client's Approved Vendor List" as a
computed caution on the registry roster and in RFQ shortlist suitability, without
storing anything and without blocking anyone.

**Architecture:** One pure predicate, `missing_client_approval(bidder) -> str | None`,
added to `workflow/bidders.py` beside `effective_prequal`. Two readers: `evaluate()`
appends it to `Suitability.cautions`, and `_bidder_payload` exposes it as
`approval_caution`. The `ADNOC`/`ASTRA` constants move to `workflow/models/bidder.py`
so the pure module can name the client approver without importing `openpyxl`.

**Tech Stack:** Python 3.12, pydantic v2, FastAPI, pytest; React 19 + TypeScript +
vitest for the web half.

**Spec:** [`docs/superpowers/specs/2026-08-13-client-approval-gap-design.md`](../specs/2026-08-13-client-approval-gap-design.md)

## Global Constraints

- **Caution, never blocker.** `Suitability.eligible` must be unchanged in every
  case. No new path demands an `override_reason`.
- **Derived, never stored.** No new field on `Bidder`, on `ShortlistEntry`, or in
  `workflow.json`. This is the rule `PrequalStatus` already keeps by having no
  `Expired` member.
- **One definition.** The sentence is built in exactly one function. Neither the
  API boundary nor the browser reconstructs it.
- **Caution order is fixed:** prequalification → approval → scope.
- **`workflow/bidders.py` stays pure** — no store, no I/O, no clock, and after this
  change still no `openpyxl`.
- **Exact sentence wording** (copied verbatim into the implementation):
  - with approvals held: `"{name} is not on the ADNOC Approved Vendor List — approved by {held} only."`
  - with none held: `"{name} is not on the ADNOC Approved Vendor List and has no approval recorded."`
  - The `—` is an em dash (U+2014), matching the rest of `bidders.py`.
- **Run tests from the repo root** with `python -m pytest`; the web suite with
  `npm test` under `web/`.

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the implementation
> against the test.
>
> **Load-bearing** in every block below: the caution/blocker split, the exact
> sentence wording above, the insertion *position* of the caution, the absence of
> any new stored field, and the fixture-default changes in Tasks 2 and 3.
> **Illustrative:** variable names, helper-function names inside tests, and the
> precise phrasing of docstrings.

---

## File structure

| file | responsibility after this change |
|---|---|
| `workflow/models/bidder.py` | the model, `PrequalStatus`, and now the approver-organisation constants `ADNOC` / `ASTRA` |
| `workflow/bidders.py` | the pure suitability rules; gains `CLIENT_APPROVER` and `missing_client_approval` |
| `workflow/avl_import.py` | unchanged behaviour; imports the two constants instead of defining them |
| `api/workflow_routes.py` | `_bidder_payload` gains `approval_caution` |
| `web/src/types.ts` | `BidderSummary` gains `approval_caution` |
| `web/src/pages/Bidders.tsx` | `BidderCard` renders the sentence in the `warn` slot |
| `web/src/pages/workflow-fixtures.ts` | the three bidder fixtures gain the field |

---

## Task 1: The rule, and the constants that let it exist

**Files:**
- Modify: `workflow/models/bidder.py` (add constants near `PrequalStatus`)
- Modify: `workflow/avl_import.py:34-35` (replace the two definitions with an import)
- Modify: `workflow/bidders.py` (add `CLIENT_APPROVER` and `missing_client_approval`)
- Test: `tests/test_bidder_suitability.py` (new section), `tests/test_avl_import.py` (unchanged, must still pass)

**Interfaces:**
- Consumes: `Bidder.approved_by: list[str]`
- Produces: `workflow.models.bidder.ADNOC: str`, `workflow.models.bidder.ASTRA: str`,
  `workflow.bidders.CLIENT_APPROVER: str`,
  `workflow.bidders.missing_client_approval(bidder: Bidder) -> str | None`
- **Store invariant owned:** `Bidder`'s persisted field set is **exactly** what it
  was before this change — a bidder loaded from `workflow.json` and re-saved
  round-trips byte-identically, and no key named `approval_caution` or any
  approval-provenance field appears anywhere in the document. This task is the one
  that could have been tempted to add a field instead of deriving, so it owns the
  invariant that says it did not.

**Why the constants move at all.** `avl_import.py` imports `openpyxl`, and
`bidders.py`'s module docstring promises no I/O. Importing `ADNOC` from its current
home would pull a spreadsheet library into the pure module on every
`import workflow.bidders`. Defining a second `"ADNOC"` literal in `bidders.py`
instead leaves two spellings of one organisation's name to drift. Moving them to
the model module — which imports only `datetime`, `typing`, `uuid` and `pydantic` —
is what makes one definition reachable from both.

Every existing `from workflow.avl_import import ADNOC, ASTRA` keeps working,
because an imported name is still a module attribute. Those live in
`workflow/seed_demo.py:48`, `tests/test_avl_import.py:26` and
`tests/test_seed_demo.py`. **Do not edit those imports** — leaving them alone is
the evidence the move was compatible.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bidder_suitability.py`:

```python
# -- the client approval gap -------------------------------------------------
#
# Derived from `approved_by`, for the same reason `Expired` is derived from
# `prequal_expires_on`: a stored copy is wrong the moment the list is edited.


def test_a_client_approved_bidder_has_no_gap():
    assert missing_client_approval(a_bidder(approved_by=[ADNOC])) is None


def test_holding_both_approvals_is_still_no_gap():
    assert missing_client_approval(a_bidder(approved_by=[ADNOC, ASTRA])) is None


def test_an_astra_only_bidder_names_what_it_holds_instead():
    gap = missing_client_approval(a_bidder(approved_by=[ASTRA]))
    assert gap == (
        "Al Munara Switchgear LLC is not on the ADNOC Approved Vendor List "
        "— approved by Astra only."
    )


def test_a_bidder_with_no_approval_at_all_says_so_differently():
    """One rule, two sentences. "We approved them, the client has not" and
    "nobody has approved them" call for different actions, so they must not
    render identically."""
    gap = missing_client_approval(a_bidder(approved_by=[]))
    assert gap == (
        "Al Munara Switchgear LLC is not on the ADNOC Approved Vendor List "
        "and has no approval recorded."
    )


def test_the_rule_is_absence_of_the_client_not_presence_of_astra():
    """A bidder on some third party's list, and on neither of ours, is caught
    by the same rule — the predicate is 'the client approver is absent', which
    cannot be sidestepped by leaving `approved_by` empty or filling it with
    something else."""
    gap = missing_client_approval(a_bidder(approved_by=["Some Other Operator"]))
    assert gap is not None
    assert "approved by Some Other Operator only." in gap


def test_the_pure_module_does_not_import_a_spreadsheet_library():
    """`bidders.py` promises no I/O. Naming the client approver must not be
    what breaks that promise."""
    import sys

    for module in ("workflow.bidders", "workflow.models.bidder", "openpyxl"):
        sys.modules.pop(module, None)
    importlib.import_module("workflow.bidders")
    assert "openpyxl" not in sys.modules
```

Extend the imports at the top of that file:

```python
import importlib

from workflow.bidders import (
    Suitability,
    effective_prequal,
    evaluate,
    missing_client_approval,
)
from workflow.models.bidder import ADNOC, ASTRA, Bidder
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
python -m pytest tests/test_bidder_suitability.py -v
```

Expected: collection error — `ImportError: cannot import name 'ADNOC' from 'workflow.models.bidder'`.

- [ ] **Step 3: Write the minimal implementation**

In `workflow/models/bidder.py`, above `PrequalStatus`:

```python
# The organisations whose approved-vendor lists this platform knows about.
# They live here, next to the `approved_by` field they annotate, rather than in
# `avl_import` — that module imports `openpyxl`, and `workflow/bidders.py` needs
# to name the client approver without taking a spreadsheet library with it.
ADNOC = "ADNOC"
ASTRA = "Astra"
```

In `workflow/avl_import.py`, delete lines 34–35 and extend the existing import:

```python
from workflow.models.bidder import ADNOC, ASTRA, Bidder
```

In `workflow/bidders.py`, after the `_STATUS_BLOCKERS` table:

```python
# Whose Approved Vendor List counts as *the client's*. One constant, so a second
# client's AVL is a one-line change here rather than a redesign. It is not
# per-project: `Project.client` is free text ("Al Dhafra Petroleum"), and
# inferring an approving organisation from it would be guessing.
CLIENT_APPROVER = ADNOC


def missing_client_approval(bidder: Bidder) -> str | None:
    """None when the bidder is on the client's Approved Vendor List.

    Derived, never stored, for the same reason `PrequalStatus` has no "Expired"
    member: a stored copy is wrong the moment `approved_by` is edited, and
    keeping it honest would need a sweep job nobody has written.

    The predicate is the *absence of the client approver*, not the presence of
    Astra, so a bidder with an empty `approved_by` is caught too — a vendor
    nobody has approved is at least as worth flagging as one only we approved.
    The two cases share a rule but not a sentence, because the action they call
    for differs.
    """
    if CLIENT_APPROVER in bidder.approved_by:
        return None
    held = ", ".join(bidder.approved_by)
    return (
        f"{bidder.name} is not on the {CLIENT_APPROVER} Approved Vendor List"
        + (f" — approved by {held} only." if held else " and has no approval recorded.")
    )
```

Add `from workflow.models.bidder import ADNOC, Bidder` to that module's imports.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest tests/test_bidder_suitability.py tests/test_avl_import.py tests/test_seed_demo.py -v
```

Expected: PASS. `test_avl_import.py` and `test_seed_demo.py` are in this command
specifically to prove the constants move broke neither importer.

- [ ] **Step 5: Commit**

```bash
git add workflow/models/bidder.py workflow/avl_import.py workflow/bidders.py tests/test_bidder_suitability.py
git commit -m "feat: a bidder off the client's list can be named as such"
```

---

## Task 2: The caution reaches `evaluate()` — and the fixture that hid it

**Files:**
- Modify: `workflow/bidders.py` (inside `evaluate`, between the prequalification block and `scope_fit`)
- Modify: `tests/test_bidder_suitability.py:25-33` (`a_bidder` defaults)
- Test: `tests/test_bidder_suitability.py`

**Interfaces:**
- Consumes: `missing_client_approval` from Task 1
- Produces: `Suitability.cautions` may now carry the approval sentence, always at
  index-position between the prequalification caution and the scope caution.
  `Suitability`'s field set is unchanged — no new attribute.
- **Store invariant owned:** `ShortlistEntry`'s persisted snapshot fields are
  **exactly** `vendor_name`, `prequal_status`, `scope_code_fit` and the override
  pair — the approval caution is computed at read time and never captured onto an
  entry. An entry created while a bidder lacked client approval, then re-read after
  the bidder gains it, must show no trace of the old caution anywhere in
  `workflow.json`.

**Read this before touching the fixture.** `a_bidder()` currently sets no
`approved_by`, so it defaults to `[]`. Under the new rule that fixture is a bidder
with no approval recorded, and three existing tests assert against it that there is
nothing to say:

- `test_an_approved_in_scope_bidder_is_eligible_with_nothing_to_say` (line 51) —
  full-equality against `cautions=[]`
- line 150 — `assert evaluate(...).cautions == []`
- line 156 — `assert result.cautions == []`

They will fail, and **they are right to**. The fix is the fixture, not the
assertions: the happy-path bidder in a test file about suitability should be a
fully approved one. Add `approved_by=[ADNOC]` to `a_bidder`'s defaults. Every test
that wants the gap overrides it explicitly. Do **not** weaken those three
assertions to `!= None` or drop the full-equality check — that equality is what
catches an unintended fourth caution appearing later.

This is exactly the failure class the plan template warns about: the defect is
invisible inside `missing_client_approval`'s own contract and only appears when a
second module's fixture meets it.

- [ ] **Step 1: Write the failing test**

First, fix the fixture at `tests/test_bidder_suitability.py:25`:

```python
def a_bidder(**overrides) -> Bidder:
    defaults = dict(
        name="Al Munara Switchgear LLC",
        country="United Arab Emirates",
        # On the client's list by default. Without this the happy-path bidder
        # is one nobody has approved, and every "nothing to say" assertion in
        # this file would be asserting the wrong thing.
        approved_by=[ADNOC],
        trade_categories=["Electrical"],
        prequal_status="Approved",
        prequal_expires_on=date(2027, 3, 31),
    )
    return Bidder(**{**defaults, **overrides})
```

Then append:

```python
def test_a_bidder_off_the_client_list_is_cautioned_but_still_eligible():
    """The whole decision this feature turns on. Asserted as `eligible is True`
    directly, not inferred from an empty `blockers` list, because those are two
    different claims and only one of them is the promise made to the user."""
    result = evaluate(a_bidder(approved_by=[ASTRA]), an_rfq(), TODAY)
    assert result.eligible is True
    assert result.blockers == []
    assert result.cautions == [
        "Al Munara Switchgear LLC is not on the ADNOC Approved Vendor List "
        "— approved by Astra only."
    ]


def test_a_bidder_with_no_approval_is_cautioned_but_still_eligible():
    result = evaluate(a_bidder(approved_by=[]), an_rfq(), TODAY)
    assert result.eligible is True
    assert result.blockers == []
    assert any("has no approval recorded." in c for c in result.cautions)


def test_the_cautions_read_prequalification_then_approval_then_scope():
    """Order is fixed, not incidental — the list is rendered in order, and a
    later edit that appends in the wrong place silently reorders a screen."""
    bidder = a_bidder(
        approved_by=[ASTRA],
        prequal_expires_on=date(2026, 9, 1),   # inside the caution window
        trade_categories=["Mechanical"],        # mismatches the rfq's discipline
    )
    result = evaluate(bidder, an_rfq(), TODAY)
    assert len(result.cautions) == 3
    assert "expires on" in result.cautions[0]
    assert "Approved Vendor List" in result.cautions[1]
    assert "Not registered for" in result.cautions[2]


def test_a_blocked_bidder_off_the_client_list_reports_both_separately():
    """The gap never migrates into `blockers`, even when the bidder has real
    ones — otherwise it would start demanding an override_reason."""
    bidder = a_bidder(approved_by=[ASTRA], on_hold=True, hold_reason="NCRs open")
    result = evaluate(bidder, an_rfq(), TODAY)
    assert result.eligible is False
    assert any("NCRs open" in b for b in result.blockers)
    assert not any("Approved Vendor List" in b for b in result.blockers)
    assert any("Approved Vendor List" in c for c in result.cautions)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
python -m pytest tests/test_bidder_suitability.py -v
```

Expected: the four new tests FAIL (the caution is never appended, so
`cautions == []` and the ordering test sees 2 entries, not 3). The three
pre-existing tests named above should now **pass**, because the fixture fix
restored their premise.

- [ ] **Step 3: Write the minimal implementation**

In `workflow/bidders.py`, inside `evaluate`, immediately after the
prequalification `if/elif/elif` block and immediately before
`scope_fit = _matches_scope(bidder, rfq)`:

```python
    # Position is deliberate: prequalification → approval → scope. The list is
    # rendered in order, and "are they approved at all" precedes "approved for
    # this trade".
    gap = missing_client_approval(bidder)
    if gap:
        cautions.append(gap)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest tests/test_bidder_suitability.py -v
```

Expected: PASS, all of them.

- [ ] **Step 5: Commit**

```bash
git add workflow/bidders.py tests/test_bidder_suitability.py
git commit -m "feat: the approval gap shows against an RFQ without blocking it"
```

---

## Task 3: The registry payload

**Files:**
- Modify: `api/workflow_routes.py:424-435` (`_bidder_payload` and its docstring)
- Modify: `tests/test_bidder_endpoints.py:58-69` (`create_bidder` helper default)
- Test: `tests/test_bidder_endpoints.py`

**Interfaces:**
- Consumes: `missing_client_approval` from Task 1
- Produces: `GET /api/workflow/bidders` and `GET /api/workflow/bidders/{id}` each
  carry `approval_caution: str | None`
- **Store invariant owned:** the document on disk contains **exactly** the bidder
  fields the model declares — `approval_caution` is present in every response body
  and absent from every stored record, and a `PATCH` to `approved_by` changes the
  next response with no second write.

**The same fixture trap as Task 2, in a second file.** `create_bidder` at
`tests/test_bidder_endpoints.py:58` posts no `approved_by`, so every bidder those
tests create is one with no approval recorded. Add `"approved_by": [ADNOC]` to the
helper's default body. No existing assertion in that file breaks either way — it
asserts nothing about cautions today — but leaving the default empty would mean
every future test in the file quietly carries a caution nobody intended.

- [ ] **Step 1: Write the failing test**

Add `"approved_by": ["ADNOC"]` to `create_bidder`'s default body, then append:

```python
def test_the_roster_reports_a_bidder_off_the_client_list(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    create_bidder(client, approved_by=["Astra"])

    roster = client.get("/api/workflow/bidders").json()["bidders"]
    assert roster[0]["approval_caution"] == (
        "Al Munara Switchgear LLC is not on the ADNOC Approved Vendor List "
        "— approved by Astra only."
    )


def test_a_client_approved_bidder_carries_no_caution(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    create_bidder(client)

    roster = client.get("/api/workflow/bidders").json()["bidders"]
    assert roster[0]["approval_caution"] is None


def test_the_single_bidder_view_carries_it_too(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    bidder = create_bidder(client, approved_by=[])

    body = client.get(f"/api/workflow/bidders/{bidder['id']}").json()
    assert "has no approval recorded." in body["approval_caution"]


def test_the_caution_follows_a_patch_with_no_second_write(tmp_path, monkeypatch):
    """Derived, not stored: correcting `approved_by` is the only edit needed."""
    client = _client(tmp_path, monkeypatch)
    bidder = create_bidder(client, approved_by=["Astra"])

    r = client.patch(
        f"/api/workflow/bidders/{bidder['id']}", json={"approved_by": ["ADNOC", "Astra"]}
    )
    assert r.status_code == 200, r.text
    body = client.get(f"/api/workflow/bidders/{bidder['id']}").json()
    assert body["approval_caution"] is None


def test_a_bidder_off_the_client_list_needs_no_override_to_be_shortlisted(
    tmp_path, monkeypatch
):
    """A caution is not a blocker. If this ever 409s, the feature has changed
    meaning."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    bidder = create_bidder(client, approved_by=["Astra"])

    r = client.post(
        f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": bidder["id"]}
    )
    assert r.status_code == 201, r.text
```

> The shortlist POST path and body above are illustrative — copy the exact route
> and payload shape from the existing
> `test_inviting_a_registry_bidder_snapshots_the_registry` in the same file rather
> than trusting this block.

- [ ] **Step 2: Run the test to verify it fails**

```bash
python -m pytest tests/test_bidder_endpoints.py -v
```

Expected: FAIL with `KeyError: 'approval_caution'`.

- [ ] **Step 3: Write the minimal implementation**

Replace `_bidder_payload`:

```python
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
```

Extend the existing import at `api/workflow_routes.py:30`:

```python
from workflow.bidders import effective_prequal, evaluate, missing_client_approval
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest tests/test_bidder_endpoints.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/workflow_routes.py tests/test_bidder_endpoints.py
git commit -m "feat: the roster reports who is off the client's list"
```

---

## Task 4: The registry screen

**Files:**
- Modify: `web/src/types.ts:466-469` (`BidderSummary`)
- Modify: `web/src/pages/workflow-fixtures.ts:96-139` (three fixtures)
- Modify: `web/src/pages/Bidders.tsx` (`BidderCard`, below the `on_hold` line)
- Test: `web/src/pages/Bidders.test.tsx`

**Interfaces:**
- Consumes: `approval_caution: string | null` from Task 3
- Produces: no exported API; a rendered `<p className="warn">`
- **Store invariant owned:** none — this task writes to no store. Its client-side
  analogue, and the thing its test defends: **the browser never constructs the
  sentence.** `Bidders.tsx` must contain no `'ADNOC'` literal and no
  `approved_by.includes(...)` test; the string is rendered verbatim or not at all.
  A second definition in the client is how the two drift.

- [ ] **Step 1: Write the failing test**

Add the field to `BidderSummary` in `web/src/types.ts`:

```typescript
export interface BidderSummary extends Bidder {
  effective_prequal: string
  /** The server's sentence when this bidder is not on the client's Approved
   *  Vendor List, or `null` when they are. Rendered verbatim — deriving it here
   *  from `approved_by` would be a second definition of the rule. */
  approval_caution: string | null
  invited_count: number
}
```

Add `approval_caution: null` to `APPROVED_BIDDER` in `workflow-fixtures.ts`.
`EXPIRED_BIDDER` and `SUSPENDED_BIDDER` spread it and both carry `ADNOC`, so they
inherit the correct value and need no line of their own.

Append to `web/src/pages/Bidders.test.tsx`:

```typescript
it('renders the server sentence when a bidder is off the client list', async () => {
  vi.mocked(fetchBidders).mockResolvedValue([
    {
      ...APPROVED_BIDDER,
      approved_by: ['Astra'],
      approval_caution:
        'Al Munara Switchgear LLC is not on the ADNOC Approved Vendor List — approved by Astra only.',
    },
  ])
  render(<Bidders />)
  expect(
    await screen.findByText(/not on the ADNOC Approved Vendor List/),
  ).toBeInTheDocument()
})

it('says nothing when the bidder is on the client list', async () => {
  vi.mocked(fetchBidders).mockResolvedValue([APPROVED_BIDDER])
  render(<Bidders />)
  await screen.findByText(APPROVED_BIDDER.name)
  expect(
    screen.queryByText(/Approved Vendor List/),
  ).not.toBeInTheDocument()
})
```

> The `render` / mock setup above is illustrative. Copy the exact imports,
> `vi.mock('../api')` factory and render helper from the top of the existing
> `Bidders.test.tsx` — and note CLAUDE.md's rule that any test rendering `App` or
> `Setup` needs a **stable** `useAuth` mock. `Bidders` is rendered directly here,
> so that rule does not bite, but do not "helpfully" switch to rendering `App`.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd web && npm test -- Bidders
```

Expected: FAIL — nothing renders the text. A TypeScript error on the fixtures is
also expected until `approval_caution` is added to them.

- [ ] **Step 3: Write the minimal implementation**

In `BidderCard`, directly below the existing `on_hold` block so a bidder that is
both on hold and unapproved shows both facts in reading order:

```tsx
      {bidder.approval_caution && (
        <p className="warn">{bidder.approval_caution}</p>
      )}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd web && npm test
```

Then type-check the test files too, which `npm test` alone does not do:

```bash
cd web && npm run build
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/types.ts web/src/pages/workflow-fixtures.ts web/src/pages/Bidders.tsx web/src/pages/Bidders.test.tsx
git commit -m "feat: the registry card shows who is off the client's list"
```

---

## Task 5: Integration — the round trip, the matrix, and the baselines

**Files:**
- Test: `tests/test_workflow_persistence.py` (mutation-matrix rows)
- Test: `tests/test_avl_import.py`, `tests/test_seed_demo.py` (the imported-registry property)
- Modify: `CLAUDE.md` (both baseline rows, and the bidder-registry paragraph)

**Interfaces:**
- Consumes: everything from Tasks 1–4
- Produces: no new code; the evidence the whole thing holds across a restart
- **Store invariant owned:** `workflow.json` holds **exactly** the entities the
  store holds and no derived value among them. Specifically: after any sequence of
  writes, no key named `approval_caution` exists anywhere in the document, and the
  caution computed on a store loaded from disk equals the caution computed on the
  store that wrote it.

**On the plan template's nine required matrix rows.** They describe the ingestion
pipeline — document revisions, LLM calls, prompt-version constants, extraction
caches. This subsystem has none of those: it stores nothing, calls no model, and
has no lineage. Fabricating rows for them would be exactly the decoration the
template forbids ("a row defending no named invariant is decoration"). Four of the
nine do have a real analogue here, and those are rows 1–4 below; the remaining five
(sibling-revision lineage, prompt-version bumps, transient LLM failure, permanent
LLM failure, omitted optional array from a model response) have no counterpart and
are deliberately absent. Rows 5 and 6 are specific to this change.

- [ ] **Step 1: Write the failing tests — the two-run mutation matrix**

| # | mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|---|
| 1 | `approved_by` loses the client approver via `PATCH`, then the store is reloaded from disk | Task 3's — a `PATCH` changes the next read with no second write | caution `None` on run 1, the Astra sentence on run 2 |
| 2 | `approved_by` gains the client approver, then reloaded | same | sentence on run 1, `None` on run 2 |
| 3 | a bidder is created, saved, and loaded in a **fresh process-equivalent store** | Task 1's — nothing derived was persisted | `"approval_caution"` is absent from the raw `workflow.json` text, and the reloaded store computes the same sentence |
| 4 | a document written **before** this change (no bidder ever having carried the key) is loaded | backward compatibility — an existing registry needs no migration | every bidder loads, and each computes its caution from `approved_by` alone |
| 5 | a bidder is shortlisted while cautioned, then gains the client approval, then reloaded | Task 2's — the caution never lands on `ShortlistEntry` | the entry survives unchanged; no caution text appears anywhere in `workflow.json` |
| 6 | a bidder is shortlisted while cautioned, then **deleted-attempt**, then reloaded | the existing delete guard is unaffected by the new caution | delete refused naming the RFQ; the bidder and entry both survive the reload |

Write one test per row in `tests/test_workflow_persistence.py`, each naming in its
docstring the invariant it defends. Follow that file's existing round-trip helpers
rather than opening `workflow.json` by hand, except in row 3, where reading the raw
text **is** the assertion.

Also append the imported-registry property tests:

```python
# tests/test_avl_import.py
def test_no_imported_bidder_is_ever_off_the_client_list(tmp_path):
    """Every AVL row is by definition on the client's list, so the caution must
    be unreachable through the importer."""
    for bidder in parse_avl(workbook(tmp_path), astra_subset=True):
        assert missing_client_approval(bidder) is None
```

```python
# tests/test_seed_demo.py
def test_none_of_the_invented_cast_is_off_the_client_list():
    """The demo registry shows this flag nowhere until somebody adds a bidder
    by hand — which is the only path that can produce it."""
    for bidder in invented_bidders(date(2026, 8, 13)):
        assert missing_client_approval(bidder) is None
```

- [ ] **Step 2: Run them to verify they fail**

```bash
python -m pytest tests/test_workflow_persistence.py tests/test_avl_import.py tests/test_seed_demo.py -v
```

Expected: the six matrix rows fail on missing test bodies; the two property tests
should **pass immediately** if Tasks 1–3 are correct. A property test that fails
here means the importer or the seed is producing a bidder without the client
approver — investigate that before proceeding, do not adjust the assertion.

- [ ] **Step 3: Implement**

No production code should be needed. If a matrix row cannot be made to pass without
changing `workflow/` or `api/`, that row has found a real defect — fix the code,
not the row.

- [ ] **Step 4: Run the full suite**

```bash
python -m pytest
```

```bash
cd web && npm test && npm run build
```

- [ ] **Step 4b: Verify the matrix is real, not decorative**

Reinstate each defect one at a time and confirm the intended row fails **and
nothing else does**:

| defect to reinstate | row that must fail |
|---|---|
| add `approval_caution` to `Bidder` as a stored field | 3 |
| cache the caution on the store at create time instead of deriving | 1, 2 |
| copy the caution onto `ShortlistEntry` at invite time | 5 |
| make `missing_client_approval` return `None` for an empty `approved_by` | 4, and Task 2's no-approval test |
| append the caution to `blockers` instead of `cautions` | Task 3's no-override-needed test, and 6 |

A row that still passes with its defect reinstated is not testing what it claims.
Revert each defect before moving on.

- [ ] **Step 5: Update `CLAUDE.md`'s baselines**

Measure, do not derive, and do not guess:

1. Run `python -m pytest` on this workstation and take the **printed** passed/skipped
   figures. That is the workstation row.
2. Derive the CI row from it by the subtraction `CLAUDE.md` documents
   (`- 4 - 3 - 2 - 9` passes become skips). Never edit the two rows independently —
   that is how they drift.
3. Update the bidder-registry paragraph's test count, and add one sentence to the
   **Store invariants** section recording that the approval gap is derived and never
   stored, alongside the existing note about `Expired`.
4. Confirm none of the new Python tests touches a fixture directory or a provider
   key, so every one of them lands in **both** rows.
5. Update the web suite's stated count (currently "222 passed across 19 files")
   from the actual `npm test` output.

- [ ] **Step 6: Commit**

```bash
git add tests/ CLAUDE.md
git commit -m "test: the approval gap survives a restart without being stored"
```

---

## Self-review against the spec

| spec section | task |
|---|---|
| §1 the rule, `missing_client_approval`, one rule two sentences | Task 1 |
| §1 derived never stored | Task 1 invariant, matrix row 3 |
| §2 constants move, `avl_import` re-export intact | Task 1, steps 3–4 |
| §3 appended to `cautions` never `blockers`, `eligible` unchanged | Task 2 |
| §3 fixed position prequalification → approval → scope | Task 2, ordering test |
| §3 `ShortlistingStep` needs no React change | Task 4 — deliberately not modified; Task 3's no-override test covers the behaviour |
| §4 `approval_caution` on both bidder routes, docstring "two" → "three" | Task 3 |
| §5 `types.ts`, `Bidders.tsx` warn slot, rendered verbatim | Task 4 |
| §6 New bidder form keeps `['Astra']` | untouched by every task, by design |
| §6 `astra_approves` untouched | untouched; Task 5's importer property test proves it still yields client-approved bidders |
| §7 known limitation | Task 1's `CLIENT_APPROVER` comment records it in the code |
| §8 all five test files, plus baselines | Tasks 1–5 |

**Type consistency:** `missing_client_approval(bidder: Bidder) -> str | None` is
named identically in Tasks 1, 2, 3 and 5. The payload key and the TypeScript field
are both `approval_caution`. The constants are `ADNOC` / `ASTRA` throughout, and
`CLIENT_APPROVER` is referenced only inside `workflow/bidders.py`.

**Known gap, stated rather than hidden:** `web/src/pages/Bidders.test.tsx:111`
builds a bidder literal with `approved_by: ['Astra']`. If that literal is typed as
`BidderSummary` it will need `approval_caution` to compile; if it is a
`BidderInput` for the create form it will not. Task 4's `npm run build` step is
what settles it — resolve whichever way the compiler says, and do not pre-emptively
edit it.
