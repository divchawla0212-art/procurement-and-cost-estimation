# Vendor-list filtering and bulk shortlisting — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the item screen's search bar, let a buyer narrow the vendor list
by which approvals a vendor must carry, select several vendors and shortlist
them in one action, and make the enquiry-document picker state what it holds.

**Architecture:** One server change — an existing hardcoded constant becomes a
validated query parameter on a route that already takes a list. Everything else
is `ItemDetail.tsx`'s vendor card and `forms.tsx`'s file row. No bulk endpoint:
the batch is N calls to the existing single-invite route, so its per-vendor
guards keep working.

**Tech Stack:** React 19 + TypeScript (vitest, @testing-library/react) over
FastAPI + Pydantic (pytest, `TestClient`).

**Spec:** [`docs/superpowers/specs/2026-08-14-vendor-list-filtering-and-bulk-shortlisting-design.md`](../specs/2026-08-14-vendor-list-filtering-and-bulk-shortlisting-design.md)

## Global Constraints

- **Nothing new is stored.** No model field, no `workflow.json` key, no change
  to `bidders.available()`'s "AND, never OR" rule.
- **The server owns every rule.** No screen decides eligibility. Refusals render
  **verbatim**, beside the row that caused them, keyed by **bidder id** — never
  a row index, because the table re-sorts under a search and re-fetches under a
  chip.
- **Missing data is never coerced to a passing or failing value.** An unknown
  approver is a 422, never an empty list.
- **No screen spells "ADNOC".** Approver names come from the server.
- **Measured baselines to move from:** Python **1625 passed, 3 skipped**; web
  **249 passed across 20 files**. CI row is derived by `-4 -3 -2 -11`.
- **The AVL gate stays at eleven and is not re-measured** — no task here touches
  `test_avl_import.py`, `test_seed_demo.py` or `test_disciplines.py`.

---

## File structure

| file | responsibility | task |
|---|---|---|
| `web/src/pages/ItemDetail.tsx` | Modify — search row, chips, selection, bulk invite | 1, 3, 4, 5 |
| `api/workflow_routes.py` | Modify — `approver` parameter on `/bidders/available` | 2 |
| `web/src/api.ts` | Modify — `fetchAvailableBidders` takes approvers | 2 |
| `web/src/types.ts` | Modify — `AvailableBidders.selectable_approvers` | 2 |
| `web/src/pages/forms.tsx` | Modify — the file control | 6 |
| `web/src/theme.css` | Modify — file-control and selection-bar rules | 6 |
| `CLAUDE.md` | Modify — baselines and the new invariants | 7 |

---

## Task 1: Rebuild the search row

**Files:**
- Modify: `web/src/pages/ItemDetail.tsx` — `AvailableVendorList`'s search label
- Test: `web/src/pages/ItemDetail.test.tsx`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing other tasks import. Task 3 renders its chips into the same
  `.fxrow`.
- **Store invariant owned:** none — markup only.

Measured on the live page: the `<label>` carries `className="field"`, which is
the **input** class (`forms.tsx` puts it on `<input>` elements), so the label
gets `border: 0.8px solid` and `padding: 8px 12px`; the `<input>` inside carries
no class and falls back to the user agent's `border: 1.6px inset`. Two nested
boxes.

This is the only place in the codebase that puts `.field` on a `<label>`, so
there is no second instance to chase.

- [ ] **Step 1: Write the failing test**

```tsx
  it('renders the vendor search as one field, not a box inside a box', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )

    renderItem()

    const search = await screen.findByLabelText('Search vendors')
    // `.field`/`.input` are input classes. On the input they style the control;
    // on the wrapping label they gave the label an input's chrome and left the
    // real input with the browser's raw default border.
    expect(search).toHaveClass('input')
    expect(search.closest('label')).toBeNull()
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web; npx vitest run src/pages/ItemDetail.test.tsx -t "one field"`
Expected: FAIL — `Unable to find a label with the text of: Search vendors`
(today the accessible name comes from the `<span>Search</span>` inside the
label).

- [ ] **Step 3: Write minimal implementation**

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the
> implementation against the test.
>
> **Load-bearing:** the class moves onto the `<input>`, and the label wrapper
> goes. **Illustrative:** the exact placeholder wording.

```tsx
        <div className="fxrow">
          <input
            className="input"
            type="search"
            // The placeholder already says what the field takes, and the
            // wrapping-label-as-input is what produced the nested boxes.
            aria-label="Search vendors"
            placeholder="Vendor, product group or manufacturer"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {/* Task 3 renders the approval chips here. */}
        </div>
```

`.input` rather than `.field`: `.field` is the forms' full-width block input,
`.input` is the inline one the wizard's filter rows use, and this is now a
filter row.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web; npx vitest run src/pages/ItemDetail.test.tsx`
Expected: PASS — including the existing search test, whose
`getByLabelText('Search')` becomes `'Search vendors'`. Update that reference
rather than adding a second accessible name.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/ItemDetail.tsx web/src/pages/ItemDetail.test.tsx
git commit -m "fix: the vendor search is one field, not a box inside a box"
```

---

## Task 2: An approver filter on `/bidders/available`

**Files:**
- Modify: `api/workflow_routes.py` — `list_available_bidders` (~line 512)
- Modify: `web/src/api.ts` — `fetchAvailableBidders`
- Modify: `web/src/types.ts` — `AvailableBidders`
- Test: `tests/test_bidder_endpoints.py`

**Interfaces:**
- Consumes: `bidder_db.approved_by_all(root, approvers, groups)`, unchanged.
- Produces:
  - `GET /bidders/available?discipline=&approver=ADNOC&approver=Astra`
  - payload gains `selectable_approvers: list[str]`; `approvers` becomes the
    **applied** filter.
  - `fetchAvailableBidders(discipline?: string, approvers?: string[])`
- **Store invariant owned:** none — a read. Its guarantee is that an approver
  the registry does not know is refused rather than answered with an empty list.

`approved_by_all` already takes a list; only the hardcoded
`list(AVAILABLE_APPROVERS)` goes.

**Why a 422 and not an empty list.** A typo'd approver returns nobody from the
`HAVING count(DISTINCT approver_key) = ?` query, and an empty table reads on
screen as "no vendor qualifies" — a finding nobody made. Same reason
`client_approved` distinguishes `None` from `False`.

**Why an empty `approver=` is refused too.** Zero required approvals has no
expression in that query: `count(...) = 0` matches nobody.

- [ ] **Step 1: Write the failing tests**

In `tests/test_bidder_endpoints.py`, beside the existing `/bidders/available`
cases (which establish `_client`, `create_bidder` and the AVL-free fixtures):

```python
def test_available_defaults_to_every_approval(tmp_path, monkeypatch):
    """No parameter means the server's own list, not the browser's."""
    client = _client(tmp_path, monkeypatch)
    create_bidder(client, approved_by=["ADNOC", "Astra"])
    create_bidder(client, name="Client only", approved_by=["ADNOC"])

    body = client.get("/api/workflow/bidders/available").json()

    assert body["approvers"] == ["ADNOC", "Astra"]
    assert [b["name"] for b in body["bidders"]] == ["Al Munara Switchgear LLC"]


def test_one_approver_widens_the_list_to_that_one(tmp_path, monkeypatch):
    """The whole point: the client's list is far larger than the intersection,
    and there was no way to ask for it from this route."""
    client = _client(tmp_path, monkeypatch)
    create_bidder(client, approved_by=["ADNOC", "Astra"])
    create_bidder(client, name="Client only", approved_by=["ADNOC"])

    body = client.get(
        "/api/workflow/bidders/available", params={"approver": ["ADNOC"]}
    ).json()

    assert body["approvers"] == ["ADNOC"]
    assert sorted(b["name"] for b in body["bidders"]) == [
        "Al Munara Switchgear LLC",
        "Client only",
    ]


def test_an_unknown_approver_is_refused_rather_than_answered_with_nobody(
    tmp_path, monkeypatch
):
    """A typo returns nobody from the HAVING query, and an empty table reads as
    "no vendor qualifies" — a finding nobody made."""
    client = _client(tmp_path, monkeypatch)
    create_bidder(client)

    r = client.get("/api/workflow/bidders/available", params={"approver": ["ADNCO"]})

    assert r.status_code == 422
    assert "ADNCO" in r.text


def test_no_approver_at_all_is_refused(tmp_path, monkeypatch):
    """Zero required approvals has no expression in the query."""
    client = _client(tmp_path, monkeypatch)
    create_bidder(client)

    r = client.get("/api/workflow/bidders/available?approver=")

    assert r.status_code == 422


def test_the_payload_names_what_may_be_selected(tmp_path, monkeypatch):
    """So the browser builds its chips from the server's names and still never
    spells the client's for itself."""
    client = _client(tmp_path, monkeypatch)

    body = client.get("/api/workflow/bidders/available").json()

    assert body["selectable_approvers"] == ["ADNOC", "Astra"]
```

> Read the existing `/bidders/available` tests (~line 489 onward) for how they
> build bidders without the real AVL, and match that construction.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_bidder_endpoints.py -k "approver or selectable or widens" -v`
Expected: the narrowing test FAILS (the parameter is ignored, so the answer is
still the intersection); both refusal tests FAIL with `200` instead of `422`;
the `selectable_approvers` test FAILS with `KeyError`.

- [ ] **Step 3: Write minimal implementation**

```python
@router.get("/bidders/available")
def list_available_bidders(
    discipline: str | None = None,
    approver: Annotated[list[str] | None, Query()] = None,
) -> dict:
    # Absent means the server's own list. Sending the default from the browser
    # would put the definition of "available" in two places.
    approvers = list(AVAILABLE_APPROVERS) if approver is None else list(approver)

    unknown = [a for a in approvers if a not in AVAILABLE_APPROVERS]
    if unknown:
        # Never an empty list: `approved_by_all` on a typo returns nobody, and
        # an empty table reads as "no vendor qualifies" — a finding nobody made.
        raise HTTPException(
            status_code=422,
            detail=f"Unknown approver(s): {', '.join(unknown)}.",
        )
    if not approvers:
        raise HTTPException(
            status_code=422,
            detail="At least one approver is required.",
        )
    ...
    rows = bidder_db.approved_by_all(_root(), approvers, groups)
```

and in the payload:

```python
        "approvers": approvers,               # what was applied
        "selectable_approvers": list(AVAILABLE_APPROVERS),
```

Note `?approver=` sends `[""]`, not `[]`, so the empty case is caught by the
`unknown` check first — its message names the empty string, which is unhelpful.
Filter blanks out **before** the unknown check so the "at least one" message is
the one that surfaces.

In `web/src/api.ts`:

```ts
export function fetchAvailableBidders(
  discipline?: string,
  approvers?: string[],
): Promise<AvailableBidders> {
  const q = new URLSearchParams()
  if (discipline) q.set('discipline', discipline)
  // Repeated, not comma-joined: an approver name could contain a comma, and
  // FastAPI reads repeats into a list.
  for (const a of approvers ?? []) q.append('approver', a)
  const s = q.toString()
  return getJson(`/api/workflow/bidders/available${s ? `?${s}` : ''}`)
}
```

and `AvailableBidders` in `types.ts` gains `selectable_approvers: string[]`,
with `approvers` re-documented as the applied filter.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_bidder_endpoints.py -q`
Run: `cd web; npm run build`
Expected: PASS, no type errors. `availableList` in `ItemDetail.test.tsx` needs
`selectable_approvers` for the build to pass.

- [ ] **Step 5: Commit**

```bash
git add api/workflow_routes.py web/src/api.ts web/src/types.ts \
        tests/test_bidder_endpoints.py web/src/pages/ItemDetail.test.tsx
git commit -m "feat: the available list can be asked for one approval or both"
```

---

## Task 3: The approval chips

**Files:**
- Modify: `web/src/pages/ItemDetail.tsx` — `AvailableVendorList`
- Test: `web/src/pages/ItemDetail.test.tsx`

**Interfaces:**
- Consumes: Task 2's `fetchAvailableBidders(discipline, approvers)` and
  `selectable_approvers`.
- Produces: nothing other tasks import.
- **Store invariant owned:** none.

The chips are **the approvals a vendor must carry — AND, not OR.** Both ticked
is today's list exactly, so the card opens unchanged. This matches
`approved_by_all`'s semantics rather than inventing a second meaning, and
`bidders.available()`'s rule is untouched: it still answers what *available*
means, and the chips are a query the reader makes.

**The last ticked chip is `disabled`.** Zero approvals has no expression in the
query, so the alternatives are a silently empty table or a fourth meaning.

The scoped/whole fallback added last phase still applies: the narrowed answer
being empty falls back to the whole list. Both reads now carry the chips.

- [ ] **Step 1: Write the failing tests**

```tsx
  it('opens with every approval required, so the list is unchanged', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )

    renderItem()

    expect(await screen.findByRole('button', { name: 'ADNOC' })).toHaveClass('on')
    expect(screen.getByRole('button', { name: 'Astra' })).toHaveClass('on')
  })

  it('asks the server again with one approval when a chip is unticked', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: 'Astra' }))

    // The browser sends the narrowed list; the server owns what it means.
    await waitFor(() =>
      expect(fetchAvailableBidders).toHaveBeenLastCalledWith('Electrical', ['ADNOC']),
    )
  })

  it('will not let the last approval be unticked', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: 'Astra' }))

    // Zero required approvals matches nobody in the HAVING query, so the
    // remaining chip cannot be turned off.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'ADNOC' })).toBeDisabled(),
    )
  })

  it('offers the internet source as not built yet', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))

    renderItem()

    const chip = await screen.findByRole('button', { name: /from the internet/i })
    expect(chip).toBeDisabled()
    expect(screen.getByText('Not built yet.')).toBeInTheDocument()
  })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web; npx vitest run src/pages/ItemDetail.test.tsx -t "approval"`
Expected: FAIL — no button named `ADNOC` exists (the pills are `<span>`s, not
buttons, so the role query does not match them).

- [ ] **Step 3: Write minimal implementation**

> Load-bearing: AND semantics, the default coming from `selectable_approvers`,
> and the last-chip `disabled` guard. Illustrative: chip wording and order.

```tsx
  // Null until the first response names them, so the browser never spells an
  // approver for itself. Once known, all of them are required — which is the
  // list the server would have used anyway, so the opening view is unchanged.
  const [required, setRequired] = useState<string[] | null>(null)
  const selectable = scoped.data?.selectable_approvers ?? []
  const approvers = required ?? selectable

  function toggle(org: string) {
    const next = approvers.includes(org)
      ? approvers.filter((o) => o !== org)
      : [...approvers, org]
    // Guarded here as well as by `disabled`, so a keyboard or a test cannot
    // reach the state the query has no expression for.
    if (next.length === 0) return
    setRequired(next)
  }
```

The chip row, inside Task 1's `.fxrow`:

```tsx
  {selectable.map((org) => (
    <button
      key={org}
      type="button"
      className={`chip${approvers.includes(org) ? ' on' : ''}`}
      // The last one standing cannot be turned off.
      disabled={approvers.length === 1 && approvers.includes(org)}
      onClick={() => toggle(org)}
    >
      {org}
    </button>
  ))}
  <button type="button" className="chip" disabled>
    From the internet
  </button>
```

with `<p className="muted">Not built yet.</p>` beneath, matching the RFQ
wizard's button.

**`theme.css` needs one new rule.** The chip styles dim on
`[aria-disabled='true']` (line 436), not on `:disabled`, so a genuinely
`disabled` chip renders at full strength and looks tickable. Add
`.chip:disabled` alongside the existing selector rather than switching to
`aria-disabled` — `toBeDisabled()` and real keyboard/pointer suppression both
want the actual attribute, and an `aria-disabled` button still receives clicks.

Both `useAsync` calls take `approvers` in their loader **and** their dependency
array — joined, as `covering-shortlists.ts` does, since the array is rebuilt
every render:

```tsx
  const scoped = useAsync(
    () => fetchAvailableBidders(discipline, approvers),
    [discipline, approvers.join(',')],
  )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web; npx vitest run src/pages/ItemDetail.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/ItemDetail.tsx web/src/pages/ItemDetail.test.tsx
git commit -m "feat: narrow the vendor list to one approval or both"
```

---

## Task 4: Selecting vendors

**Files:**
- Modify: `web/src/pages/ItemDetail.tsx`
- Test: `web/src/pages/ItemDetail.test.tsx`

**Interfaces:**
- Consumes: Task 3's filtered list.
- Produces: `selected: Set<string>` in `AvailableVendorList`, read by Task 5.
- **Store invariant owned:** none — nothing is written until Task 5.

**Selection is a `Set` of bidder ids, never row indices.** The table re-sorts
under a search and re-fetches under a chip; an index would move a tick onto a
different company. Third instance of that rule on this screen.

**`Select these N` adds only the rendered rows.** The table caps at
`BIDDER_CAP` (25) of up to 1,346, so this is the only scope where what you tick
is what you can see — and Task 5 turns a tick into an invitation with no undo.

**Selection survives filtering and says what it is hiding.** Pruning on every
keystroke would throw away the reader's work; dropping the count would let them
invite a vendor they can no longer see without knowing it.

- [ ] **Step 1: Write the failing tests**

```tsx
  it('selects the vendors that are actually on screen', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({
        bidders: [
          APPROVED_BIDDER,
          { ...APPROVED_BIDDER, id: 'bdr_other', name: 'Gulf Crescent Fabricators' },
        ],
      }),
    )

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /select these 2/i }))

    expect(screen.getByText(/2 selected/)).toBeInTheDocument()
  })

  it('keeps a selection the search has hidden, and says so', async () => {
    // Pruning on every keystroke throws away the reader's work; dropping the
    // count lets them invite a vendor they can no longer see.
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({
        bidders: [
          APPROVED_BIDDER,
          { ...APPROVED_BIDDER, id: 'bdr_other', name: 'Gulf Crescent Fabricators' },
        ],
      }),
    )

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /select these 2/i }))
    fireEvent.change(screen.getByLabelText('Search vendors'), {
      target: { value: 'Gulf' },
    })

    expect(screen.getByText(/2 selected · 1 not shown/)).toBeInTheDocument()
  })

  it('ticks a vendor by id, not by row', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )

    renderItem()
    fireEvent.click(
      await screen.findByRole('checkbox', { name: /select al munara/i }),
    )

    expect(screen.getByText(/1 selected/)).toBeInTheDocument()
  })

  it('clears the selection', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail({ items: [GENERATOR] }))
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /select these 1/i }))
    fireEvent.click(screen.getByRole('button', { name: /^clear$/i }))

    expect(screen.queryByText(/selected/)).toBeNull()
  })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web; npx vitest run src/pages/ItemDetail.test.tsx -t "select"`
Expected: FAIL — no `Select these 2` button and no checkboxes in that table.

- [ ] **Step 3: Write minimal implementation**

> Load-bearing: ids not indices, `Select these N` over `shown.slice(0, CAP)`
> only, and the not-shown count. Illustrative: the wording of the count line.

```tsx
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const rendered = shown.slice(0, BIDDER_CAP)
  // What is ticked but not currently rendered. Reported rather than pruned:
  // a selection the reader made is theirs, and a count they cannot see is the
  // thing worth saying out loud.
  const hidden = [...selected].filter((id) => !rendered.some((b) => b.id === id))
```

A checkbox cell per row, `aria-label={`Select ${b.name}`}`, `checked={selected.has(b.id)}`.
Above the table:

```tsx
  <button type="button" className="btn btn-sm"
          onClick={() => setSelected((prev) =>
            new Set([...prev, ...rendered.map((b) => b.id)]))}>
    Select these {rendered.length}
  </button>
  <button type="button" className="btn btn-sm btn-ghost"
          onClick={() => setSelected(new Set())}>
    Clear
  </button>
  {selected.size > 0 && (
    <span className="muted">
      {selected.size} selected
      {hidden.length > 0 && ` · ${hidden.length} not shown`}
    </span>
  )}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web; npx vitest run src/pages/ItemDetail.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/ItemDetail.tsx web/src/pages/ItemDetail.test.tsx
git commit -m "feat: select several vendors from the item screen"
```

---

## Task 5: Bulk shortlisting

**Files:**
- Modify: `web/src/pages/ItemDetail.tsx`
- Test: `web/src/pages/ItemDetail.test.tsx`

**Interfaces:**
- Consumes: Task 4's `selected`; the existing
  `inviteRegisteredBidder(rfqId, { vendor_id })` and `rowError` map.
- Produces: nothing.
- **Store invariant owned:** none new. Every write goes through the existing
  single-invite route, so its guards — the override reason a blocked bidder
  demands, the duplicate-invitation refusal — are unchanged and un-bypassed.

**Each vendor is sent individually, and there is no bulk endpoint.** A bulk
route would have to either reimplement those per-vendor guards or bypass them,
and bypassing them is how a vendor procurement never signed off reaches an RFQ.

**A refusal fails alone.** Not all-or-nothing: one blocked vendor among fifty
would otherwise block the batch, and there is no transaction to roll the others
back with — the successful writes have already landed through `locked_update`.

**Successes leave the selection, refusals stay ticked**, so the reader can fix
the reason and retry exactly what failed.

- [ ] **Step 1: Write the failing tests**

```tsx
  it('invites every selected vendor', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
    )
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({
        bidders: [
          APPROVED_BIDDER,
          { ...APPROVED_BIDDER, id: 'bdr_other', name: 'Gulf Crescent Fabricators' },
        ],
      }),
    )
    vi.mocked(fetchRfq).mockResolvedValue({ ...RFQ_DETAIL, shortlist: [] })
    vi.mocked(inviteRegisteredBidder).mockResolvedValue(shortlistEntry())

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /select these 2/i }))
    fireEvent.click(screen.getByRole('button', { name: /shortlist selected \(2\)/i }))

    // One call per vendor, through the same route the row button uses, so the
    // server's per-vendor guards still run.
    await waitFor(() => expect(inviteRegisteredBidder).toHaveBeenCalledTimes(2))
    expect(inviteRegisteredBidder).toHaveBeenCalledWith('rfq_1', {
      vendor_id: 'bdr_almunara',
    })
    expect(inviteRegisteredBidder).toHaveBeenCalledWith('rfq_1', {
      vendor_id: 'bdr_other',
    })
  })

  it('keeps a refused vendor ticked and invites the rest', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], rfqs: [rfq('rfq_1', 'ADP-RFQ-2026-014', ['itm_1'])] }),
    )
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({
        bidders: [
          APPROVED_BIDDER,
          { ...APPROVED_BIDDER, id: 'bdr_other', name: 'Gulf Crescent Fabricators' },
        ],
      }),
    )
    vi.mocked(fetchRfq).mockResolvedValue({ ...RFQ_DETAIL, shortlist: [] })
    vi.mocked(inviteRegisteredBidder).mockImplementation((_rfq, body) =>
      body.vendor_id === 'bdr_other'
        ? Promise.reject(new Error('Gulf Crescent Fabricators is on hold — an override reason is required.'))
        : Promise.resolve(shortlistEntry()),
    )

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /select these 2/i }))
    fireEvent.click(screen.getByRole('button', { name: /shortlist selected \(2\)/i }))

    // The refusal beside its own row, the other vendor invited anyway, and the
    // failed one still ticked so it can be retried.
    const row = (await screen.findByText('Gulf Crescent Fabricators')).closest('tr')!
    expect(within(row).getByText(/an override reason is required/i)).toBeInTheDocument()
    expect(screen.getByText(/1 invited · 1 refused/i)).toBeInTheDocument()
    expect(screen.getByText(/^1 selected/)).toBeInTheDocument()
  })

  it('has no bulk control when no RFQ covers the item', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(
      detail({ items: [GENERATOR], rfqs: [] }),
    )
    vi.mocked(fetchAvailableBidders).mockResolvedValue(
      availableList({ bidders: [APPROVED_BIDDER] }),
    )

    renderItem()
    fireEvent.click(await screen.findByRole('button', { name: /select these 1/i }))

    expect(screen.queryByRole('button', { name: /shortlist selected/i })).toBeNull()
  })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web; npx vitest run src/pages/ItemDetail.test.tsx -t "selected"`
Expected: FAIL — no `Shortlist selected (2)` button.

- [ ] **Step 3: Write minimal implementation**

> Load-bearing: sequential per-vendor calls, failures keyed by bidder id,
> successes removed from the selection, and exactly one reload at the end.
> Illustrative: the summary sentence.

```tsx
  const [summary, setSummary] = useState<string | null>(null)

  async function inviteSelected() {
    const ids = [...selected]
    const failed = new Map<string, string>()
    let invited = 0
    // Sequential, not Promise.all: every one of these is a read-modify-write on
    // the same document behind `locked_update`, and firing them together only
    // makes them queue on that lock with their errors interleaved.
    for (const id of ids) {
      try {
        await inviteRegisteredBidder(target, { vendor_id: id })
        invited += 1
      } catch (err) {
        failed.set(id, (err as Error).message)
      }
    }
    setRowError((prev) => ({ ...prev, ...Object.fromEntries(failed) }))
    // Successes leave; refusals stay ticked so the reader retries exactly what
    // failed rather than re-selecting from scratch.
    setSelected(new Set(failed.keys()))
    setSummary(
      `${invited} invited` + (failed.size ? ` · ${failed.size} refused` : ''),
    )
    // One reload for the batch, so the Invited marks and the RFQ summary below
    // move together rather than the table re-rendering under each call.
    onInvited()
  }
```

The button renders only when `target` is set and `selected.size > 0`:

```tsx
  {target && selected.size > 0 && (
    <button type="button" className="btn btn-sm" onClick={() => void inviteSelected()}>
      Shortlist selected ({selected.size})
    </button>
  )}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web; npx vitest run src/pages/ItemDetail.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/ItemDetail.tsx web/src/pages/ItemDetail.test.tsx
git commit -m "feat: shortlist several vendors in one action"
```

---

## Task 6: The file control states what it holds

**Files:**
- Modify: `web/src/pages/forms.tsx` — `RaiseRfqForm`'s document row
- Modify: `web/src/theme.css`
- Test: `web/src/pages/ProjectDetail.test.tsx`

**Interfaces:**
- Consumes: the `picked` state added last phase.
- Produces: nothing.
- **Store invariant owned:** none — the document is still never uploaded.

The native input's "No file chosen" is **browser-owned**: it cannot be
relabelled from CSS or JS. Last phase put the filename underneath it, which
left the control contradicting the line below it. The only fix is to stop
showing the native control.

The input **stays in the DOM** with its `id`, `accept` and label association, so
`getByLabelText(/fill in from the enquiry document/i)` keeps working and last
phase's three tests hold unchanged.

- [ ] **Step 1: Write the failing test**

```tsx
  it('marks the held document with a tick rather than "no file chosen"', async () => {
    vi.mocked(fetchWorkflowProject).mockResolvedValue(detail())
    vi.mocked(extractRfqDoc).mockResolvedValue({})

    renderDetail()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Gas generator' }))
    fireEvent.click(screen.getByRole('button', { name: /raise rfq/i }))
    await screen.findByRole('option', { name: /^Cables/ })

    const input = screen.getByLabelText(/fill in from the enquiry document/i)
    fireEvent.change(input, {
      target: { files: [new File(['%PDF-1.4'], 'haliba-enquiry.pdf')] },
    })

    // The native control is browser-labelled "No file chosen" and cannot be
    // relabelled, so it is hidden and this line is the only status.
    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent('haliba-enquiry.pdf')
    expect(status).toHaveTextContent('✓')
    expect(input).toHaveClass('sr-only')
    expect(screen.getByRole('button', { name: /choose file/i })).toBeInTheDocument()
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web; npx vitest run src/pages/ProjectDetail.test.tsx -t "tick"`
Expected: FAIL — `Unable to find an accessible element with the role "status"`.

- [ ] **Step 3: Write minimal implementation**

> Load-bearing: the input keeps its id and label association, the tick is
> `aria-hidden`, and the line is `role="status"`. Illustrative: the button
> label and the class name.

```tsx
  const fileRef = useRef<HTMLInputElement>(null)
```

```tsx
        <input
          id="r-doc"
          className="sr-only"
          ref={fileRef}
          type="file"
          accept=".pdf,.docx,.xlsx"
          disabled={extracting}
          onChange={...unchanged...}
        />
        <button type="button" className="btn btn-sm" disabled={extracting}
                onClick={() => fileRef.current?.click()}>
          Choose file
        </button>
        {/* The one place state is reported. `role="status"` so a change is
            announced; the tick is decorative and the filename carries the
            meaning. */}
        <span className="muted" role="status">
          {extracting ? (
            'Reading the document…'
          ) : picked ? (
            <>
              <span aria-hidden="true" className="ok-tick">✓</span> {picked.name}
            </>
          ) : (
            'Optional. PDF, Word or Excel.'
          )}
        </span>
```

`.sr-only` already exists (`theme.css:960`) and is what `ProjectDetail.tsx` and
`ItemDetail.tsx` use for table headers. Reuse it rather than adding a second
rule that does the same thing.

`.ok-tick` is new — one rule giving the glyph `color: var(--pass)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web; npx vitest run src/pages/ProjectDetail.test.tsx src/pages/ItemDetail.test.tsx`
Expected: PASS, including last phase's three enquiry-document tests.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/forms.tsx web/src/theme.css web/src/pages/ProjectDetail.test.tsx
git commit -m "fix: the file control says what it holds, with a tick"
```

---

## Task 7: Baselines and invariants

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Measure both suites**

```bash
python -m pytest -q
```

```bash
cd web && npm test
```

- [ ] **Step 2: Update the two Python rows**

Workstation is measured; CI derived by `-4 -3 -2 -11` passes and the matching
skips. The AVL gate stays at **eleven** and is **not** re-measured — no task
here touched the three files carrying the marker.

- [ ] **Step 3: Record the new invariants**

In the RFQ-workflow section, beside the existing "available is both approvals at
once" paragraph:

- `/bidders/available` takes a repeatable `approver`; absent means
  `AVAILABLE_APPROVERS`, and an unknown or empty one is a **422, never an empty
  list** — an empty table reads as "no vendor qualifies", a finding nobody made.
- `selectable_approvers` rides on the payload so the browser never spells an
  approver's name.
- **There is no bulk invitation endpoint, deliberately.** The batch is N calls
  to the single-invite route, so its per-vendor guards cannot be bypassed; a
  refusal fails alone and the rest still land.

- [ ] **Step 4: Verify the arithmetic**

Confirm `CI passed = workstation passed - 20` and `CI skipped = workstation
skipped + 20`.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: re-measure both baselines after the vendor-list work"
```

---

## Self-review

**Spec coverage.** §1 → Task 1. §2 → Task 3. §3 → Task 2. §4 → Task 4. §5 →
Task 5. §6 → Task 6. §7 is a list of non-changes. §8's test table is distributed
across the tasks owning each file. No gaps.

**Ordering note.** The spec presents the chips (§2) before the server (§3); the
plan **reverses them** — Task 2 is the server, Task 3 the chips — because the
chips are built from `selectable_approvers`, which does not exist until the
route sends it.

**Type consistency.** `approvers: string[]` is the applied filter in Tasks 2 and
3; `selectable_approvers: string[]` is the full set. `selected: Set<string>` of
bidder ids is defined in Task 4 and consumed unchanged in Task 5. `rowError:
Record<string, string>` keyed by bidder id is the map from last phase, reused
rather than duplicated.

**Known deviation from `PLAN-TEMPLATE.md`.** Rule 2's mutation matrix has
nothing to mutate: this change adds no stored collection, and its only writes go
through an existing, already-defended route. Deliberately absent rather than
fabricated — the same precedent `test_workflow_persistence.py` records.
