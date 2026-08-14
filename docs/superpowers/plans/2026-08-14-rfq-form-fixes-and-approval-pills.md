# RFQ form fixes and approval pills — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two defects in the raise-an-RFQ form, carry both approvals onto
the shortlist and the item screen as coloured pills, let a buyer shortlist a
vendor without leaving the item screen, and place a disabled web-search button
on the RFQ wizard.

**Architecture:** Almost entirely front end. The one server change adds a single
derived key to an existing response body — no new route, no new stored field, no
change to any stage, gate or transition. The shared approval pill becomes a
component in `primitives.tsx` so three screens render one definition of the
fact, and the item screen's two new features share one read of the covering
RFQs rather than each taking their own.

**Tech Stack:** React 19 + TypeScript (vitest, @testing-library/react) over
FastAPI + Pydantic (pytest, `TestClient`).

**Spec:** [`docs/superpowers/specs/2026-08-14-rfq-form-fixes-and-approval-pills-design.md`](../specs/2026-08-14-rfq-form-fixes-and-approval-pills-design.md)

## Global Constraints

Every task's requirements implicitly include these. They are copied from the
spec and from `CLAUDE.md`'s store invariants.

- **Nothing new is stored.** No field on `ShortlistEntry`, none on `Bidder`, no
  key in `workflow.json`, no file written for an uploaded enquiry document.
- **The server owns every rule.** No screen decides eligibility, approval or
  legality. A refusal is rendered **verbatim** — never rewritten to "Something
  went wrong".
- **A refusal renders beside the row that caused it**, keyed by **id**, never by
  row index.
- **Derived values are computed in one place.** `missing_client_approval` stays
  the one definition of client approval; the browser never rebuilds it.
- **`client_approver` comes from the server.** No screen spells "ADNOC" itself.
- **Test baselines:** the workstation Python row is **measured** with
  `python -m pytest` and the CI row **derived** from it by the subtraction
  `CLAUDE.md` documents. The current measured baselines are **1620 passed, 3
  skipped** (Python) and **225 passed across 18 files** (web, `npm test` under
  `web/`).
- **The AVL gate stays at eleven and is not re-measured**: no task here touches
  `test_avl_import.py`, `test_seed_demo.py` or `test_disciplines.py`, which is
  the condition `CLAUDE.md` sets for re-measuring it.

---

## File structure

| file | responsibility | task |
|---|---|---|
| `web/src/pages/forms.tsx` | Modify — both money inputs become text; `RaiseRfqForm` holds the picked `File` | 1, 2 |
| `api/workflow_routes.py` | Modify — `_shortlist_payload` gains `approved_by` | 3 |
| `web/src/types.ts` | Modify — `ShortlistEntry.approved_by` | 3 |
| `web/src/components/primitives.tsx` | Modify — new `ApprovalPills` export | 4 |
| `web/src/theme.css` | Modify — two approver colours | 4 |
| `web/src/pages/wizard/ShortlistingStep.tsx` | Modify — the Approvals column and the candidate badges | 5 |
| `web/src/pages/wizard/ShortlistingStep.test.tsx` | **Create** — this step has no test file today | 5 |
| `web/src/pages/covering-shortlists.ts` | **Create** — one read of every covering RFQ's shortlist, shared by tasks 6 and 7 | 6 |
| `web/src/pages/ItemDetail.tsx` | Modify — summary column, then pills and the Shortlist control | 6, 7 |
| `web/src/pages/RfqWizard.tsx` | Modify — the disabled web-search button | 8 |
| `CLAUDE.md` | Modify — both baseline rows and the change's contribution | 9 |

**Why `covering-shortlists.ts` is its own module.** `ItemDetail.tsx` is already
~410 lines with two components in it. Tasks 6 and 7 both need the same fetched
data, and putting the fetch in a module they share is what stops task 7 adding a
second fetch that can disagree with task 6's. It is also the piece with the
subtle dependency-array rule below, which deserves to be stated once in a place
that owns it.

---

## Task 1: Both money fields become text inputs

**Files:**
- Modify: `web/src/pages/forms.tsx` — `ItemForm` (~line 282–318), `RaiseRfqForm` (~line 361–456)
- Test: `web/src/pages/ProjectDetail.test.tsx`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: no exported signature changes. `ItemForm` and `RaiseRfqForm` keep
  their existing props exactly.
- **Store invariant owned:** none — this task writes nothing. Its guarantee is
  the negative one: a value the reader did not type never reaches
  `createRfq`/`createWorkflowItem`, and a value that is not a number never
  reaches the server at all.

The defect is that `type="number"` steps on wheel scroll in Chrome and Edge, so
scrolling a long form past a filled-in budget silently rewrites it.
`type="text"` removes wheel stepping, arrow-key stepping and the spinners in one
change; `inputMode="decimal"` keeps the numeric keypad on a phone, which is the
only thing `type="number"` was buying here.

`ItemForm` currently calls `Number(e.target.value)` on **every keystroke**, so a
half-typed value round-trips through `NaN`. After this task the raw string is
the source of truth for the input and the number is derived once, at submit.

- [ ] **Step 1: Write the failing tests**

Add to `web/src/pages/ProjectDetail.test.tsx`, inside the existing
`describe('ProjectDetail', …)`:

```tsx
  it('takes the estimated budget as text, so the scroll wheel cannot alter it', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))

    // A number input steps on wheel scroll in Chrome and Edge; a text input
    // has no stepping behaviour to suppress.
    expect(screen.getByLabelText('Estimated budget (AED)')).toHaveAttribute('type', 'text')
  })

  it('takes the estimated value as text too', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())

    renderDetail()
    fireEvent.click(await screen.findByRole('button', { name: /add item/i }))

    expect(screen.getByLabelText('Estimated value (AED)')).toHaveAttribute('type', 'text')
  })

  it('refuses a budget that is not a number rather than sending NaN', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))

    const type = (label: string, value: string) =>
      fireEvent.change(screen.getByLabelText(label), { target: { value } })
    await screen.findByRole('option', { name: /^Cables/ })
    type('Reference', 'ADP-RFQ-2026-014')
    type('Package', 'Power generation')
    type('Discipline', 'Cables')
    // Grouping separators are what a reader types into a money field, and
    // `Number('1,800,000')` is NaN — which serialises to null and comes back
    // as a validation error naming a field they did not think they touched.
    type('Estimated budget (AED)', '1,800,000')
    fireEvent.click(screen.getByRole('button', { name: /create rfq/i }))

    expect(
      await screen.findByText('Estimated budget must be a number.'),
    ).toBeInTheDocument()
    expect(createRfq).not.toHaveBeenCalled()
  })
```

Then **update the existing assertion** at `ProjectDetail.test.tsx:322`. A text
input's value is a string; this is a real behaviour change, so the test states
the new value rather than being loosened to accept either:

```tsx
    expect(screen.getByLabelText('Estimated budget (AED)')).toHaveValue('500000')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web; npx vitest run src/pages/ProjectDetail.test.tsx`
Expected: the two `toHaveAttribute('type', 'text')` tests FAIL with
`expected "number" to equal "text"`; the NaN test FAILS because no error text is
found; the updated line FAILS with `expected 500000 to equal '500000'`.

- [ ] **Step 3: Write minimal implementation**

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the
> implementation against the test.
>
> **Load-bearing** in this task: `type="text"`, the `Number.isFinite` guard, and
> that each form's message names its own field. **Illustrative:** the exact
> state variable names and where the guard sits inside the callback.

In `RaiseRfqForm`, `value` is already a string, so only the input and the submit
change:

```tsx
  const { onSubmit: submit, error, busy } = useSubmit(async () => {
    // Parsed once, here, rather than on every keystroke: a half-typed value is
    // not an error, and a value that never parses must not reach the server.
    const parsed = Number(value.trim())
    if (!Number.isFinite(parsed)) throw new Error('Estimated budget must be a number.')
    await onSubmit({
      project_id: projectId,
      item_ids: itemIds,
      reference,
      package: pkg,
      discipline,
      value_estimate_aed: parsed,
    })
  })
```

```tsx
      <Row id="r-value" label="Estimated budget (AED)">
        {/* Text, not number: a focused number input steps on wheel scroll, so
            scrolling past a filled-in budget silently rewrote it. `inputMode`
            keeps the numeric keypad, which is all `type="number"` was for. */}
        <input id="r-value" className="field" type="text" inputMode="decimal"
               required value={value}
               onChange={(e) => setValue(e.target.value)} />
      </Row>
```

`ItemForm` needs a second piece of state, because its field is backed by a
number on the input object:

```tsx
  const [f, setF] = useState<WorkflowItemInput>(initial ?? BLANK_ITEM)
  // The string the reader is typing is the source of truth for the field; the
  // number is derived from it at submit. Holding only the number is what forced
  // `Number(e.target.value)` on every keystroke, so a half-typed value became
  // NaN and back again.
  const [valueText, setValueText] = useState(String(f.estimated_value_aed))

  const { onSubmit: submit, error, busy } = useSubmit(async () => {
    const parsed = Number(valueText.trim())
    if (!Number.isFinite(parsed)) throw new Error('Estimated value must be a number.')
    await onSubmit({ ...f, estimated_value_aed: parsed })
  })
```

```tsx
      <Row id="i-value" label="Estimated value (AED)">
        <input id="i-value" className="field" type="text" inputMode="decimal"
               required value={valueText}
               onChange={(e) => setValueText(e.target.value)} />
      </Row>
```

The two messages differ on purpose: both forms can be open on the same screen,
and a shared sentence would not say which field to go and fix.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web; npx vitest run src/pages/ProjectDetail.test.tsx src/pages/ItemDetail.test.tsx`
Expected: PASS. `ItemDetail.test.tsx` is run too because it renders both forms
through the item screen's own door.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/forms.tsx web/src/pages/ProjectDetail.test.tsx
git commit -m "fix: the scroll wheel no longer edits a money field"
```

---

## Task 2: The form stops denying the file it just read

**Files:**
- Modify: `web/src/pages/forms.tsx` — `RaiseRfqForm`
- Test: `web/src/pages/ProjectDetail.test.tsx`

**Interfaces:**
- Consumes: `RaiseRfqForm`'s existing `onExtract: (file: File) => Promise<Partial<RfqInput>>`.
- Produces: no prop changes.
- **Store invariant owned:** none, and that is the point — the document is never
  uploaded. `POST /rfqs/extract` reads and discards, and nothing in this task
  writes a byte to disk or adds a field to any record.

`e.target.value = ''` **stays.** Its comment already explains why: it is what
makes picking the same file twice read it twice, which is the retry case. The
bug is that the input's native label was the only thing reporting what had been
picked, so clearing it reset the screen to **"No file chosen"** immediately
after a successful read. React state becomes that report instead, and it
survives the clear.

The object URL is created **on click**, not on pick — creating one per picked
file would leak a blob for every file the reader tried.

- [ ] **Step 1: Write the failing tests**

```tsx
  it('names the document it read, rather than reporting no file chosen', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    vi.mocked(extractRfqDoc).mockResolvedValue({ reference: 'MOCK-RFQ-1234' })

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))
    await screen.findByRole('option', { name: /^Cables/ })

    fireEvent.change(screen.getByLabelText(/fill in from the enquiry document/i), {
      target: { files: [new File(['%PDF-1.4'], 'haliba-enquiry.pdf')] },
    })

    // The input clears itself so the same file can be picked twice; this is
    // the state that has to survive that clear.
    expect(await screen.findByText('haliba-enquiry.pdf')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /view haliba-enquiry\.pdf/i })).toBeInTheDocument()
  })

  it('still offers the document when reading it failed', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    vi.mocked(extractRfqDoc).mockRejectedValue(new Error('The upload has no file name.'))

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))
    await screen.findByRole('option', { name: /^Cables/ })

    fireEvent.change(screen.getByLabelText(/fill in from the enquiry document/i), {
      target: { files: [new File([''], 'unreadable.pdf')] },
    })

    // Being able to open the file that failed is more useful than being able
    // to open only the ones that worked.
    expect(await screen.findByText(/has no file name/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /view unreadable\.pdf/i })).toBeInTheDocument()
  })

  it('opens the picked document in a new tab', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    vi.mocked(extractRfqDoc).mockResolvedValue({})
    // jsdom implements neither, so both are stubbed and asserted directly.
    const createObjectURL = vi.fn().mockReturnValue('blob:enquiry')
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL: vi.fn() })
    const open = vi.fn()
    vi.stubGlobal('open', open)

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))
    await screen.findByRole('option', { name: /^Cables/ })
    const file = new File(['%PDF-1.4'], 'haliba-enquiry.pdf')
    fireEvent.change(screen.getByLabelText(/fill in from the enquiry document/i), {
      target: { files: [file] },
    })

    fireEvent.click(await screen.findByRole('button', { name: /view haliba-enquiry\.pdf/i }))

    expect(createObjectURL).toHaveBeenCalledWith(file)
    expect(open).toHaveBeenCalledWith('blob:enquiry', '_blank', 'noopener')
    vi.unstubAllGlobals()
  })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web; npx vitest run src/pages/ProjectDetail.test.tsx`
Expected: all three FAIL with `Unable to find an element with the text:
haliba-enquiry.pdf` (and the equivalent for the other two) — the filename is
nowhere on screen because nothing holds it.

- [ ] **Step 3: Write minimal implementation**

> Load-bearing: that `picked` is set **before** `extract` runs (so a failed read
> still shows it), that `e.target.value = ''` is kept, and that the object URL
> is created on click and revoked on replacement. Illustrative: the wording of
> the caption and the exact effect cleanup.

```tsx
  const [picked, setPicked] = useState<File | null>(null)

  async function extract(file: File) {
    // Set first, so a failed read still leaves the reader able to open the
    // document the failure is about.
    setPicked(file)
    setExtracting(true)
    setExtractError(null)
    try {
      const found = await onExtract(file)
      …unchanged…
    }
  }
```

```tsx
        <span className="muted">
          {extracting
            ? 'Reading the document…'
            : picked
              ? picked.name
              : 'Optional. PDF, Word or Excel.'}
        </span>
        {picked && !extracting && (
          <button
            type="button"
            className="linkish"
            aria-label={`View ${picked.name}`}
            onClick={() => {
              // Created on click, not on pick: one blob per file the reader
              // tried would leak every one of them. Revoked on the next tick —
              // the tab has the URL by then.
              const url = URL.createObjectURL(picked)
              window.open(url, '_blank', 'noopener')
              setTimeout(() => URL.revokeObjectURL(url), 0)
            }}
          >
            View
          </button>
        )}
```

The accessible name is `View <filename>` so two forms open at once are
distinguishable, and so the test can address one without matching the other.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web; npx vitest run src/pages/ProjectDetail.test.tsx src/pages/ItemDetail.test.tsx`
Expected: PASS, including the two pre-existing enquiry-document tests.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/forms.tsx web/src/pages/ProjectDetail.test.tsx
git commit -m "fix: the RFQ form names the enquiry document it read"
```

---

## Task 3: `approved_by` on every shortlist row

**Files:**
- Modify: `api/workflow_routes.py` — `_shortlist_payload` (~line 448–474)
- Modify: `web/src/types.ts` — `ShortlistEntry` (~line 293–314)
- Modify: `web/src/pages/wizard/ClarificationsStep.test.tsx`, `web/src/pages/RfqWizard.test.tsx` — their inline `RfqDetail` fixtures
- Test: `tests/test_bidder_endpoints.py`, `tests/test_workflow_persistence.py`

**Interfaces:**
- Consumes: `store.get_bidder(entry.vendor_id)`, already called in this function.
- Produces: `_shortlist_payload` returns an extra key
  `approved_by: list[str] | None`; TypeScript `ShortlistEntry.approved_by:
  string[] | null`. Tasks 5, 6 and 7 read it.
- **Store invariant owned:** `workflow.json` gains no `approved_by` under any
  shortlist entry. After a shortlist is read over HTTP with `approved_by`
  present in the response, the persisted document's entry keys are exactly what
  they were before the read.

Astra approval is **not derivable** from `client_approved` — that is one boolean
about `CLIENT_APPROVER`. A screen wanting both approvals has to be sent both.

`client_approved` **stays**: it carries the *rule*
(`missing_client_approval` is its one definition), while `approved_by` carries
*identity*. Rebuilding the predicate in the browser from the list would be the
second definition `CLAUDE.md` forbids.

- [ ] **Step 1: Write the failing tests**

In `tests/test_bidder_endpoints.py`, beside the existing `client_approved`
cases (which already establish `_client`, `create_rfq`, `create_bidder` and
`shortlist_of`):

```python
def test_a_shortlist_row_carries_both_approvals_not_just_the_clients(
    tmp_path, monkeypatch
):
    """`client_approved` is one boolean about the client. Astra approval is not
    a function of it, so the row has to carry the list as well."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    bidder = create_bidder(client, approved_by=["ADNOC", "Astra"])

    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": bidder["id"]})

    assert shortlist_of(client, rfq_id)[0]["approved_by"] == ["ADNOC", "Astra"]


def test_a_registry_row_nobody_approved_reports_an_empty_list(tmp_path, monkeypatch):
    """`[]` is a real answer: the row exists and carries no approval."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    bidder = create_bidder(client, approved_by=[])

    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": bidder["id"]})

    assert shortlist_of(client, rfq_id)[0]["approved_by"] == []


def test_a_free_text_vendor_reports_no_approvals_rather_than_none_held(
    tmp_path, monkeypatch
):
    """None, not `[]`. There is no registry row, so nothing says this vendor
    holds no approvals — only that we cannot tell. The same distinction
    `client_approved` already keeps, and for the same reason."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)

    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={
        "vendor_name": "Galfar", "prequal_status": "Qualified",
        "scope_code_fit": True, "included": True,
    })

    assert shortlist_of(client, rfq_id)[0]["approved_by"] is None


def test_the_approvals_follow_the_registry_with_no_second_write(tmp_path, monkeypatch):
    """Derived on read. A stored copy would still read ["Astra"] here, which is
    exactly what this shape exists to prevent."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    bidder = create_bidder(client, approved_by=["Astra"])
    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": bidder["id"]})
    assert shortlist_of(client, rfq_id)[0]["approved_by"] == ["Astra"]

    r = client.patch(
        f"/api/workflow/bidders/{bidder['id']}", json={"approved_by": ["ADNOC", "Astra"]}
    )
    assert r.status_code == 200, r.text

    assert shortlist_of(client, rfq_id)[0]["approved_by"] == ["ADNOC", "Astra"]
```

And in `tests/test_workflow_persistence.py`, in the bidder-registry section
beside `test_no_derived_value_is_written_to_the_database`:

```python
def test_no_shortlist_entry_stores_the_approvals_it_reports(tmp_path):
    """The invariant this change owns. `approved_by` is served on every
    shortlist row and stored on none of them — a written copy is wrong the
    moment the registry is corrected, and correcting it is the common case."""
    ...  # build a store with a registry-linked shortlist entry, save, reload
    document = json.loads((tmp_path / "workflow.json").read_text())
    for entries in document["shortlist"].values():
        for entry in entries:
            assert "approved_by" not in entry
```

> Read the file's existing `test_a_shortlist_link_survives_a_round_trip`
> (~line 332) for how it builds a store and where the shortlist sits in the
> document. Match its construction rather than inventing a second one.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_bidder_endpoints.py -k approv -v`
Expected: the four new tests FAIL with `KeyError: 'approved_by'`.
Run: `python -m pytest tests/test_workflow_persistence.py -k stores_the_approvals -v`
Expected: PASS immediately — nothing writes the key yet. **This is the one test
in the plan that legitimately passes first**, because it asserts an absence. To
verify it can fail, temporarily add `"approved_by": []` to `ShortlistEntry` and
confirm it goes red, then remove it. Do that before moving on; an
absence-assertion never watched failing is not known to be wired up.

- [ ] **Step 3: Write minimal implementation**

In `_shortlist_payload`:

```python
    return {
        **entry.model_dump(mode="json"),
        "client_approved": (
            None if bidder is None else missing_client_approval(bidder) is None
        ),
        # Identity, where the key above is a verdict. Sent as well as, not
        # instead of, that one: Astra approval is not a function of it, and
        # rebuilding the client-approval rule here from this list would be a
        # second definition of it.
        "approved_by": None if bidder is None else list(bidder.approved_by),
    }
```

Extend the function's docstring's "Three states" paragraph to say it now governs
two keys, both keyed on the same `bidder is None`.

In `web/src/types.ts`, on `ShortlistEntry`:

```ts
  /** Every organisation that has approved this vendor, live from the registry
   *  on each read — the identity behind `client_approved`'s verdict. `null` is
   *  the same third state that key uses: no registry row, so no finding either
   *  way. `[]` is a row that exists and carries no approval. */
  approved_by: string[] | null
```

Then add `approved_by` to the inline `RfqDetail` shortlist fixtures in
`ClarificationsStep.test.tsx` (~line 39–52) and `RfqWizard.test.tsx`, or
`npm run build` fails on them — `web/tsconfig.app.json` type-checks the test
files.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_bidder_endpoints.py tests/test_workflow_persistence.py -q`
Expected: PASS.
Run: `cd web; npm run build`
Expected: no type errors.

- [ ] **Step 5: Commit**

```bash
git add api/workflow_routes.py web/src/types.ts tests/test_bidder_endpoints.py \
        tests/test_workflow_persistence.py web/src/pages/wizard/ClarificationsStep.test.tsx \
        web/src/pages/RfqWizard.test.tsx
git commit -m "feat: a shortlist row reports every approval, not only the client's"
```

---

## Task 4: One approval pill, two colours

**Files:**
- Modify: `web/src/components/primitives.tsx` — new export
- Modify: `web/src/theme.css` — beside `.approval-badge` (~line 1345)
- Create: `web/src/components/primitives.test.tsx` — `primitives.tsx` has no test
  file today (`StageStrip.test.tsx` is the only one under `components/`)

**Interfaces:**
- Consumes: nothing.
- Produces: `export function ApprovalPills({ approvers }: { approvers: string[] }): JSX.Element`.
  Tasks 5, 6 and 7 all render it.
- **Store invariant owned:** none — presentation only.

Three screens render the same fact today in two different ways and one not at
all. A component is what stops a fourth approver meaning four edits.

The class is a **slug**, not a lookup: an unrecognised approver falls through to
the base `.approval-badge` rule and renders in today's neutral grey. A lookup
table would have to decide what to do with a name it does not know, and the
honest answer — show it, uncoloured — is what the cascade gives for free.

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from '@testing-library/react'
import { ApprovalPills } from './primitives'

it('marks each approver with its own class', () => {
  render(<ApprovalPills approvers={['ADNOC', 'Astra']} />)

  expect(screen.getByText('ADNOC')).toHaveClass('approval-badge--adnoc')
  expect(screen.getByText('Astra')).toHaveClass('approval-badge--astra')
})

it('renders an unknown approver in the base style rather than dropping it', () => {
  // A second client's list must show up uncoloured, never vanish.
  render(<ApprovalPills approvers={['Borouge']} />)

  const pill = screen.getByText('Borouge')
  expect(pill).toHaveClass('approval-badge')
  expect(pill.className).toContain('approval-badge--borouge')
})

it('renders nothing for an empty list', () => {
  const { container } = render(<ApprovalPills approvers={[]} />)
  expect(container).toBeEmptyDOMElement()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web; npx vitest run src/components/primitives.test.tsx`
Expected: FAIL — `ApprovalPills is not exported` / is not a function.

- [ ] **Step 3: Write minimal implementation**

> Load-bearing: the slug transformation and that an unknown approver still
> renders. Illustrative: the exact hex values, which are the spec's.

```tsx
/**
 * Who has approved a vendor, as one pill per approver.
 *
 * A component rather than a `.map` at each call site because three screens
 * render this same fact — the candidate card, the shortlist row and the item
 * screen's vendor table — and three copies is three places to fix when a
 * fourth approver appears.
 *
 * The class is a slug rather than a lookup, so an approver the CSS does not
 * name renders in the base neutral style instead of vanishing.
 */
export function ApprovalPills({ approvers }: { approvers: string[] }) {
  return (
    <>
      {approvers.map((org) => (
        <span
          key={org}
          className={`approval-badge approval-badge--${org.toLowerCase().replace(/\s+/g, '-')}`}
        >
          {org}
        </span>
      ))}
    </>
  )
}
```

In `web/src/theme.css`, directly after the `.approval-badge` rule:

```css
/* Approver identity, deliberately *not* the pass/deviation/fail palette the
   prequalification chips share. That palette's comment says why it is shared:
   those chips answer "can work proceed here". An approver's name is not a
   verdict — a vendor on one list and not the other is not thereby failing
   anything — so borrowing green and amber would report trouble where the truth
   is only that two organisations keep different lists. An approver with no rule
   here keeps the neutral base style above. */
.approval-badge--adnoc {
  background: #e8eefb;
  border-color: #c3d3f0;
  color: #274a94;
}

/* Our own list in the house accent. */
.approval-badge--astra {
  background: var(--accent-soft);
  border-color: #b7dcdf;
  color: var(--accent-ink);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web; npx vitest run src/components/primitives.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/primitives.tsx web/src/components/primitives.test.tsx web/src/theme.css
git commit -m "feat: an approval pill carries its approver's colour"
```

---

## Task 5: The wizard's Approvals column

**Files:**
- Modify: `web/src/pages/wizard/ShortlistingStep.tsx` — the shortlist table (~line 51–107) and `CandidateRow` (~line 305–309)
- Create: `web/src/pages/wizard/ShortlistingStep.test.tsx`

**Interfaces:**
- Consumes: `ApprovalPills` (Task 4); `ShortlistEntry.approved_by` (Task 3);
  `RfqDetail.client_approver`, already present.
- Produces: nothing other tasks read.
- **Store invariant owned:** none — presentation only.

The *Client approval* column becomes *Approvals*, and its three states are
preserved exactly. The load-bearing case is the last: a vendor carrying
`["Astra"]` shows the Astra pill **and** the "not on the ADNOC list" warning.
Both are true, and showing only the pill would let our own qualification read as
clearance we do not have.

| `approved_by` | renders |
|---|---|
| `null` | `Not checked`, muted |
| `[]` | `Not on the {client_approver} list`, warn |
| non-empty | pills, plus the warn line when `client_approved` is `false` |

- [ ] **Step 1: Write the failing test**

Create `web/src/pages/wizard/ShortlistingStep.test.tsx`. Model the module mock
and the `BASE: RfqDetail` fixture on `ClarificationsStep.test.tsx:1–60` —
mocking `'../../api'` with a factory listing the fetchers this step calls
(`fetchCandidates`, `addShortlistEntry`, `approveShortlist`,
`inviteRegisteredBidder`, `removeShortlistEntry`, `setTbeTemplate`).

```tsx
const entry = (over: Partial<ShortlistEntry>): ShortlistEntry => ({
  id: 'sle_1', rfq_id: 'rfq_1', vendor_id: 'bdr_1',
  vendor_name: 'Al Munara Switchgear LLC', prequal_status: 'Approved',
  scope_code_fit: true, included: true, client_approved: true,
  approved_by: ['ADNOC', 'Astra'], override_by: null, override_reason: null,
  ...over,
})

const step = (shortlist: ShortlistEntry[]) =>
  render(<ShortlistingStep data={{ ...BASE, shortlist }} run={vi.fn()} busy={false} tick={0} />)

it('shows a pill for each approver on the row', async () => {
  step([entry({})])
  expect(await screen.findByText('ADNOC')).toHaveClass('approval-badge--adnoc')
  expect(screen.getByText('Astra')).toHaveClass('approval-badge--astra')
})

it('shows our own approval and the client gap at the same time', async () => {
  // The load-bearing case. Both facts are true, and the pill alone would let
  // our qualification read as clearance we do not have.
  step([entry({ approved_by: ['Astra'], client_approved: false })])

  expect(await screen.findByText('Astra')).toHaveClass('approval-badge--astra')
  expect(screen.getByText(/not on the ADNOC list/i)).toBeInTheDocument()
})

it('reports a hand-typed vendor as unchecked, not as unapproved', async () => {
  step([entry({ vendor_id: null, approved_by: null, client_approved: null })])

  expect(await screen.findByText(/not checked/i)).toBeInTheDocument()
  expect(screen.queryByText(/not on the ADNOC list/i)).not.toBeInTheDocument()
})

it('reports a registry vendor nobody approved as off the list', async () => {
  step([entry({ approved_by: [], client_approved: false })])

  expect(await screen.findByText(/not on the ADNOC list/i)).toBeInTheDocument()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web; npx vitest run src/pages/wizard/ShortlistingStep.test.tsx`
Expected: the first two FAIL — no element with text `Astra` exists, because the
column renders only the approver's name when `client_approved` is true.

- [ ] **Step 3: Write minimal implementation**

```tsx
                <td>
                  {e.approved_by === null ? (
                    <span className="muted">Not checked</span>
                  ) : (
                    <>
                      <ApprovalPills approvers={e.approved_by} />
                      {/* Shown alongside the pills, not instead of them: a
                          vendor we have qualified and the client has not is
                          both of those things at once. */}
                      {e.client_approved === false && (
                        <span className="warn">
                          Not on the {data.client_approver} list
                        </span>
                      )}
                    </>
                  )}
                </td>
```

Change the `<th>` to `Approvals`, and replace `CandidateRow`'s badge `.map`
(~line 305–309) with `<ApprovalPills approvers={bidder.approved_by} />`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web; npx vitest run src/pages/wizard/`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/wizard/ShortlistingStep.tsx web/src/pages/wizard/ShortlistingStep.test.tsx
git commit -m "feat: the shortlist shows both approvals, not only the client's"
```

---

## Task 6: Each covering RFQ's shortlist, read once

**Files:**
- Create: `web/src/pages/covering-shortlists.ts`
- Modify: `web/src/pages/ItemDetail.tsx` — the *RFQs covering this item* table (~line 384–405)
- Test: `web/src/pages/ItemDetail.test.tsx`

**Interfaces:**
- Consumes: `fetchRfq` from `../api`; `ShortlistEntry.approved_by` (Task 3).
- Produces:
  ```ts
  export interface CoveringShortlist { rfq: Rfq; shortlist: ShortlistEntry[] | null }
  export function useCoveringShortlists(covering: Rfq[], tick: number): AsyncState<CoveringShortlist[]>
  export function summarise(entry: CoveringShortlist, approvers: string[]): string
  ```
  Task 7 reads the same hook's result.
- **Store invariant owned:** none — this task only reads.

The project payload carries no shortlist, so this is one `fetchRfq` per covering
RFQ. That cost is accepted deliberately: for the one-to-three RFQs an item
typically has, N reads of an existing endpoint beat a new aggregate route that
would exist for one column. If an item ever covers enough RFQs to hurt, the fix
is that route — never a cap, which would under-report.

**The dependency array is the trap in this module.** `covering` is a fresh array
on every render of `ItemDetail`, so passing it straight to `useAsync`'s deps
re-runs the fetch forever. Key on the joined ids instead. This is the same class
of failure `CLAUDE.md` records for `useAuth` returning a fresh object, and it
fails the same way — a vitest worker dying of heap exhaustion rather than an
assertion.

- [ ] **Step 1: Write the failing test**

```tsx
  it("counts each covering RFQ's approvals", async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
    )
    vi.mocked(fetchRfq).mockResolvedValue({
      ...RFQ_DETAIL,
      shortlist: [
        shortlistEntry({ id: 'a', approved_by: ['ADNOC', 'Astra'] }),
        shortlistEntry({ id: 'b', approved_by: ['ADNOC'] }),
        shortlistEntry({ id: 'c', vendor_id: null, approved_by: null }),
      ],
    })

    renderItem()

    // "not checked" is reported, never folded into the total: a vendor typed
    // in by hand has no registry row, so there is no finding either way.
    expect(
      await screen.findByText('3 invited · 2 ADNOC · 1 Astra · 1 not checked'),
    ).toBeInTheDocument()
  })

  it('says nobody is invited rather than showing a row of zeroes', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
    )
    vi.mocked(fetchRfq).mockResolvedValue({ ...RFQ_DETAIL, shortlist: [] })

    renderItem()

    expect(await screen.findByText(/nobody invited yet/i)).toBeInTheDocument()
  })

  it('shows a dash when the shortlist could not be read', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
    )
    vi.mocked(fetchRfq).mockRejectedValue(new Error('nope'))

    renderItem()

    // An unanswered question and an empty shortlist are different facts.
    const row = (await screen.findByText('ADP-RFQ-2026-014')).closest('tr')!
    expect(within(row).getByText('—')).toBeInTheDocument()
  })
```

`RFQ_DETAIL` and `shortlistEntry` go in `workflow-fixtures.ts` so
`ItemDetail.test.tsx` and any later suite share one definition. Add `fetchRfq`
to the file's `vi.mock('../api', …)` factory.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web; npx vitest run src/pages/ItemDetail.test.tsx`
Expected: FAIL — the summary text is not found; the table has no such column.

- [ ] **Step 3: Write minimal implementation**

> Load-bearing: the `join(',')` dependency key, that a per-RFQ failure resolves
> to `shortlist: null` rather than rejecting the whole batch, and that
> `not checked` is counted separately. Illustrative: the separator characters
> and the exact hook signature.

`web/src/pages/covering-shortlists.ts`:

```ts
/**
 * Every covering RFQ's shortlist, in one read.
 *
 * The project payload carries no shortlist, so this is one `fetchRfq` per RFQ.
 * Shared by the summary column and the Shortlist control rather than fetched
 * twice: the screen must not offer to invite somebody it is simultaneously
 * counting as invited.
 *
 * A failure is per-RFQ. One unreadable RFQ leaves its own `shortlist` null and
 * the others intact — rejecting the batch would blank a column that is mostly
 * correct.
 */
export function useCoveringShortlists(covering: Rfq[], tick: number) {
  return useAsync<CoveringShortlist[]>(
    () =>
      Promise.all(
        covering.map((r) =>
          fetchRfq(r.id)
            .then((d) => ({ rfq: r, shortlist: d.shortlist }))
            .catch(() => ({ rfq: r, shortlist: null })),
        ),
      ),
    // The ids, not the array: `covering` is a fresh array on every render of
    // the item screen, and passing it here re-runs the fetch forever — the
    // heap-exhaustion failure `CLAUDE.md` records for a fresh `useAuth` object.
    [covering.map((r) => r.id).join(','), tick],
  )
}

export function summarise(entry: CoveringShortlist, approvers: string[]): string {
  const rows = entry.shortlist!
  const parts = [`${rows.length} invited`]
  for (const org of approvers) {
    const n = rows.filter((e) => e.approved_by?.includes(org)).length
    if (n > 0) parts.push(`${n} ${org}`)
  }
  const unchecked = rows.filter((e) => e.approved_by === null).length
  if (unchecked > 0) parts.push(`${unchecked} not checked`)
  return parts.join(' · ')
}
```

`approvers` is passed in rather than hardcoded — the caller takes it from the
RFQ payload's `client_approver` plus what the rows actually carry, so no screen
spells the client's name.

In `ItemDetail.tsx`, add an *Approvals* column rendering, per row:
`shortlist === null` → `—`; `shortlist.length === 0` → `Nobody invited yet`;
otherwise `summarise(...)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web; npx vitest run src/pages/ItemDetail.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/covering-shortlists.ts web/src/pages/ItemDetail.tsx \
        web/src/pages/ItemDetail.test.tsx web/src/pages/workflow-fixtures.ts
git commit -m "feat: the item screen counts each covering RFQ's approvals"
```

---

## Task 7: Shortlist a vendor from the item screen

**Files:**
- Modify: `web/src/pages/ItemDetail.tsx` — `AvailableVendorList` (~line 64–184) and its call site (~line 342)
- Test: `web/src/pages/ItemDetail.test.tsx`

**Interfaces:**
- Consumes: `useCoveringShortlists` (Task 6); `ApprovalPills` (Task 4);
  `inviteRegisteredBidder(rfqId, { vendor_id })` from `../api`.
- Produces: `AvailableVendorList` gains props
  `{ covering: Rfq[]; shortlists: CoveringShortlist[] | null; onInvited: () => void }`.
- **Store invariant owned:** none new — the invitation goes through the existing
  `POST /rfqs/{id}/shortlist`, whose guards are unchanged.

The button needs a target RFQ, and the item may have none, one, or several:

| covering RFQs | behaviour |
|---|---|
| 0 | no button; a line says to raise an RFQ first |
| 1 | invites into it |
| 2+ | a `<select>` of references above the table chooses the target |

**Nothing is decided in the browser.** No eligibility check and no override
handling: the server owns whether an invitation is legal, and its refusal — the
blocked-bidder case demanding an `override_reason` — renders verbatim beside the
row that caused it, keyed by **bidder id**.

- [ ] **Step 1: Write the failing test**

```tsx
  it('invites a vendor into the one RFQ covering this item', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
    )
    vi.mocked(fetchRfq).mockResolvedValue({ ...RFQ_DETAIL, shortlist: [] })
    vi.mocked(inviteRegisteredBidder).mockResolvedValue({} as never)

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /shortlist al munara/i }))

    await waitFor(() =>
      expect(inviteRegisteredBidder).toHaveBeenCalledWith('rfq_1', {
        vendor_id: 'bdr_almunara',
      }),
    )
  })

  it('has nobody to invite into when no RFQ covers the item', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR], rfqs: [] }))

    renderItem()
    await screen.findByText('Al Munara Switchgear LLC')

    expect(screen.queryByRole('button', { name: /shortlist/i })).not.toBeInTheDocument()
    expect(screen.getByText(/raise an RFQ first/i)).toBeInTheDocument()
  })

  it('asks which RFQ when several cover the item', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({
        items: [GENERATOR],
        rfqs: [
          rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1']),
          rfq('rfq_2', 'ADP-RFQ-2026-021', ['itm_1']),
        ],
      }),
    )
    vi.mocked(fetchRfq).mockResolvedValue({ ...RFQ_DETAIL, shortlist: [] })
    vi.mocked(inviteRegisteredBidder).mockResolvedValue({} as never)

    renderItem()
    fireEvent.change(await screen.findByLabelText(/shortlist into/i), {
      target: { value: 'rfq_2' },
    })
    fireEvent.click(screen.getByRole('button', { name: /shortlist al munara/i }))

    await waitFor(() =>
      expect(inviteRegisteredBidder).toHaveBeenCalledWith('rfq_2', {
        vendor_id: 'bdr_almunara',
      }),
    )
  })

  it("renders the server's refusal against the vendor it refused", async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
    )
    vi.mocked(fetchRfq).mockResolvedValue({ ...RFQ_DETAIL, shortlist: [] })
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      // `availableList` takes a Partial<AvailableBidders> and derives `total`
      // from `bidders` — pass the list under that key, not positionally.
      availableList({
        bidders: [
          APPROVED_BIDDER,
          { ...APPROVED_BIDDER, id: 'bdr_other', name: 'Gulf Crescent Fabricators' },
        ],
      }),
    )
    vi.mocked(inviteRegisteredBidder).mockRejectedValue(
      new Error('Al Munara Switchgear LLC is on hold — an override reason is required.'),
    )

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /shortlist al munara/i }))

    // Beside the row that caused it, keyed by bidder id — not a page banner,
    // and not against the other vendor.
    const row = (await screen.findByText('Al Munara Switchgear LLC')).closest('tr')!
    expect(within(row).getByText(/an override reason is required/i)).toBeInTheDocument()
    const other = screen.getByText('Gulf Crescent Fabricators').closest('tr')!
    expect(within(other).queryByText(/override reason/i)).not.toBeInTheDocument()
  })

  it('marks a vendor already on the target RFQ as invited', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
    )
    vi.mocked(fetchRfq).mockResolvedValue({
      ...RFQ_DETAIL,
      shortlist: [shortlistEntry({ vendor_id: 'bdr_almunara' })],
    })

    renderItem()
    const row = (await screen.findByText('Al Munara Switchgear LLC')).closest('tr')!

    expect(within(row).getByText('Invited')).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: /shortlist/i })).not.toBeInTheDocument()
  })

  it('shows an approval pill against each available vendor', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))

    renderItem()

    expect(await screen.findByText('ADNOC')).toHaveClass('approval-badge--adnoc')
    expect(screen.getByText('Astra')).toHaveClass('approval-badge--astra')
  })
```

`availableList` is the file's existing helper at `ItemDetail.test.tsx:28` and
needs no change — it already accepts `{ bidders }` and derives `total` from it.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web; npx vitest run src/pages/ItemDetail.test.tsx`
Expected: FAIL — no `Shortlist …` button exists.

- [ ] **Step 3: Write minimal implementation**

> Load-bearing: the refusal keyed by bidder id, that `invited` is read from the
> **target** RFQ's shortlist and not from all of them, and that nothing here
> checks eligibility. Illustrative: the select's placement and label wording.

```tsx
  const [target, setTarget] = useState(covering[0]?.id ?? '')
  // Keyed by bidder id, never by row index: the table re-sorts under a search
  // and a stale index would attach the refusal to a different company.
  const [rowError, setRowError] = useState<Record<string, string>>({})

  const invited = new Set(
    (shortlists?.find((s) => s.rfq.id === target)?.shortlist ?? [])
      .map((e) => e.vendor_id)
      .filter(Boolean),
  )

  async function invite(bidderId: string) {
    setRowError((prev) => { const { [bidderId]: _gone, ...rest } = prev; return rest })
    try {
      await inviteRegisteredBidder(target, { vendor_id: bidderId })
      onInvited()
    } catch (err) {
      setRowError((prev) => ({ ...prev, [bidderId]: (err as Error).message }))
    }
  }
```

Per row: an *Approvals* cell rendering `<ApprovalPills approvers={b.approved_by} />`,
and an action cell rendering `Invited` when `invited.has(b.id)`, a
`Shortlist {b.name}` button when there is a target, and nothing plus the
raise-an-RFQ line when `covering.length === 0`. The `<select>` renders above the
table only when `covering.length > 1`, labelled *Shortlist into*.

`onInvited` is `() => setTick((t) => t + 1)` at the call site, so the summary
column and the Invited marks move together.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web; npx vitest run src/pages/ItemDetail.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/ItemDetail.tsx web/src/pages/ItemDetail.test.tsx
git commit -m "feat: shortlist a vendor without leaving the item screen"
```

---

## Task 8: The web-search button

**Files:**
- Modify: `web/src/pages/RfqWizard.tsx` — the header (~line 97–108)
- Test: `web/src/pages/RfqWizard.test.tsx`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing.
- **Store invariant owned:** none.

A visible affordance with nothing behind it. `RfqWizard`'s docstring argues that
a disabled button explains nothing, which is why its stage control is enabled
and surfaces the gate's sentence. That reasoning does not transfer: there the
button *would* work and the gate is the information. Here there is nothing
behind it at all, and an enabled control that silently does nothing is worse
than a disabled one that says why — so the caption is what keeps the rule.

Explicitly out of scope: any search, any provider, any route.

- [ ] **Step 1: Write the failing test**

```tsx
  it('offers a web vendor search that is not built yet', async () => {
    vi.mocked(fetchRfq).mockResolvedValue(RFQ_DETAIL)

    renderWizard()

    const button = await screen.findByRole('button', {
      name: /search the web for similar vendors/i,
    })
    // Disabled, and captioned: a disabled control has to state its reason.
    expect(button).toBeDisabled()
    expect(screen.getByText('Not built yet.')).toBeInTheDocument()
  })
```

Match `renderWizard` and the existing fixture name to whatever
`RfqWizard.test.tsx` already uses.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web; npx vitest run src/pages/RfqWizard.test.tsx`
Expected: FAIL — `Unable to find an accessible element with the role "button"
and name /search the web for similar vendors/i`.

- [ ] **Step 3: Write minimal implementation**

```tsx
        {/* Nothing behind it yet. Disabled rather than enabled-and-inert, and
            captioned rather than bare: this screen's rule is that a disabled
            control states its reason. */}
        <button type="button" className="btn" disabled>
          Search the web for similar vendors
        </button>
        <p className="muted">Not built yet.</p>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web; npx vitest run src/pages/RfqWizard.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/RfqWizard.tsx web/src/pages/RfqWizard.test.tsx
git commit -m "feat: a placeholder for the vendor web search"
```

---

## Task 9: Baselines

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: the measured output of both suites.
- Produces: nothing.
- **Store invariant owned:** none.

- [ ] **Step 1: Measure both suites**

```bash
python -m pytest -q
```

```bash
cd web && npm test
```

- [ ] **Step 2: Update the two Python rows**

The **workstation row is the measured one**. Derive the CI row from it by the
subtraction `CLAUDE.md` documents (`-4 -3 -2 -11` passes, `+4 +3 +2 +11` skips),
never by editing the two rows independently. The AVL gate stays at **eleven**
and is **not** re-measured: no task here touched `test_avl_import.py`,
`test_seed_demo.py` or `test_disciplines.py`, which is the condition that file
sets for measuring it again.

- [ ] **Step 3: Record what this change contributed**

Add a paragraph in the file's existing style naming the new Python tests (four
in `test_bidder_endpoints.py`, one in `test_workflow_persistence.py`) and
stating that none reads a fixture directory or a provider key, so all of them
land in both rows. Update the web suite's count and name the new file
(`ShortlistingStep.test.tsx`) and the new component test.

- [ ] **Step 4: Verify the arithmetic**

Re-read the two rows and confirm `CI passed = workstation passed - 20` and
`CI skipped = workstation skipped + 20`.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: re-measure both baselines after the RFQ form work"
```

---

## Self-review

**Spec coverage.** §1 → Task 1. §2 → Task 2. §3 → Task 3. §4 → Task 4. §5 →
Task 5. §6 → Task 7. §7 → Task 6. §8 → Task 3's persistence test. §9 → Task 8.
§10 is a list of non-changes and needs no task. §11's test table is distributed
across the tasks that own each file. No gaps.

**Ordering note.** The spec presents the item screen's Shortlist control (§6)
before its summary column (§7); the plan **reverses them** — Task 6 is the
summary, Task 7 the control. The control needs the shortlist read to know who is
already invited, and building it first would mean either a second fetch or a
control that is wrong until the next task lands.

**Type consistency.** `approved_by: string[] | null` is used identically in
Tasks 3, 5, 6 and 7. `ApprovalPills` takes `approvers: string[]` throughout, and
every call site guards the `null` case before rendering it. `CoveringShortlist`
is defined once in Task 6 and consumed unchanged in Task 7.

**Known deviation from `PLAN-TEMPLATE.md`.** Rule 2 asks for a nine-row two-run
mutation matrix. This change adds **no** stored collection, so those rows have
nothing to mutate. The spec's §8 states the converse invariant instead and Task
3 defends it with one round-trip assertion. This follows the precedent
`test_workflow_persistence.py` already records for a subsystem that stores
nothing and calls no model — deliberately absent rather than fabricated.
