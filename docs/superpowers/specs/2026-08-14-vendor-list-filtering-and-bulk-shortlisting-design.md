# Filtering, selecting and bulk-shortlisting the vendor list — design

**Date:** 2026-08-14
**Branch:** `rfq-platform-phase-1`
**Follows:** [`2026-08-14-rfq-form-fixes-and-approval-pills-design.md`](2026-08-14-rfq-form-fixes-and-approval-pills-design.md)

---

## The problem

Four reports against the item screen's **Available vendors list** and the
raise-an-RFQ form, all raised from the running app.

1. **The search bar is a box inside a box.** Measured on the live page:

   | element | class | computed |
   |---|---|---|
   | `<label>` | `field` | `border: 0.8px solid rgb(194,204,214)`, `padding: 8px 12px`, `display: inline` |
   | `<input>` | *none* | `border: 1.6px inset rgb(118,118,118)`, `padding: 1px 2px` |

   `.field` is the **input** class — `forms.tsx` puts it on `<input>` elements.
   Putting it on the `<label>` gives the label an input's chrome, and the real
   input inside it, carrying no class at all, falls back to the browser's raw
   user-agent border. Hence two nested boxes and a cramped, unaligned control.

2. **Nothing filters the list by approval.** Every row shows both pills and
   there is no way to ask for one. That is not merely missing: the card is
   `bidder_db.approved_by_all(root, [ADNOC, Astra], groups)` — a hard **AND** —
   so *every* row already carries both, and the ~1,346-vendor client list and
   our ~111 are both invisible from here.

3. **Vendors can only be shortlisted one at a time.** The per-row Shortlist
   button added last phase is the only route, so inviting eight vendors is
   eight round trips through a re-rendering table.

4. **The enquiry-document picker still says "No file chosen".** Last phase put
   the filename and a View control *underneath* it, but the native input's own
   label is browser-owned and cannot be relabelled from CSS or JS. The control
   therefore contradicts the line directly below it.

## What this builds

- The search row rebuilt on the pattern the wizard already uses.
- Approval chips that are the **required approvals**, defaulting to today's view.
- A repeatable `approver` parameter on `GET /bidders/available`.
- Row checkboxes, a select-the-visible control, and a bulk **Shortlist selected**.
- A file control that states what it holds, with a tick.

**Nothing new is stored.** No field on any model, no key in `workflow.json`, no
change to `bidders.available()`'s AND rule, no stage/gate/transition touched.

---

## 1. The search row — `web/src/pages/ItemDetail.tsx`

Replaced with the pattern `ShortlistingStep.tsx` already uses for exactly this
job — a search input beside its filter toggles:

```tsx
<div className="fxrow">
  <input
    className="input"
    type="search"
    aria-label="Search vendors"
    placeholder="Vendor, product group or manufacturer"
    value={query}
    onChange={(e) => setQuery(e.target.value)}
  />
  {/* chips, §2 */}
</div>
```

`aria-label` rather than a visible `<span>Search</span>`: the placeholder
already names what the field takes, and the wrapping-label-as-input is what
produced the nested boxes. This is the one place in the codebase that put
`.field` on a `<label>`, so the fix is local and there is no second instance to
chase.

**Why `.input` and not `.field`.** Both exist. `.field` is the forms' full-width
block input; `.input` is the inline one the wizard's filter rows use, and this
row is now a filter row.

## 2. Approval chips — the required approvals

Three chips sit beside the search box, reusing the existing `.chip` / `.chip.on`
styles:

| chip | behaviour |
|---|---|
| `ADNOC` | ticked by default |
| `Astra` | ticked by default |
| `From the internet` | **disabled**, captioned `Not built yet` |

**They are the approvals a vendor must carry — AND, not OR.** Both ticked is
today's list exactly, so the card opens unchanged. Untick Astra and it widens to
the client's full list; untick ADNOC and it narrows to ours alone. This matches
`approved_by_all`'s `HAVING count(DISTINCT approver_key) = ?` semantics rather
than inventing a second meaning, and it leaves `bidders.available()`'s
"AND, never OR" rule untouched — that function still answers what *available*
means, and the chips are a query the reader makes, not a redefinition.

**The last ticked chip cannot be unticked.** Zero required approvals has no
expression in that query — `count(...) = 0` matches nobody — so the alternatives
are a silently empty table or a fourth meaning ("no filter, whole registry").
Blocking it is the honest one: the chip is `disabled` with a `title` saying at
least one approval is required.

**The internet chip is disabled rather than tickable-and-empty**, matching the
web-search button on the RFQ wizard. No vendor can carry that source, so a
tickable chip would only ever produce an empty table — a control that looks
broken rather than unbuilt.

**Chips filter; checkboxes select.** They are deliberately two jobs. A chip that
selected directly would leave the table showing rows the reader had not chosen
beside ones they had, and unticking it would silently drop hand-made selections.

## 3. The server — `api/workflow_routes.py`

`GET /bidders/available` gains a repeatable `approver` query parameter:

```python
def list_available_bidders(
    discipline: str | None = None,
    approver: Annotated[list[str] | None, Query()] = None,
) -> dict:
```

- **Absent → `AVAILABLE_APPROVERS`.** Every existing caller is unaffected, and
  the default stays the server's to decide rather than the browser's to send.
- **Present → that list**, straight into the existing
  `bidder_db.approved_by_all(root, approvers, groups)`. Only the hardcoded
  constant goes; the query is unchanged.
- **An unknown approver is refused with 422 naming it**, never answered with an
  empty list. `approved_by_all` on a typo returns nobody, which on screen is
  indistinguishable from "no vendor qualifies" — the same "a finding nobody
  made" mistake this repository already forbids for `client_approved`. The
  accepted set is `AVAILABLE_APPROVERS`.
- **An empty `approver=` list is refused too**, for the reason §2 gives.

The payload's `approvers` key becomes the **applied** filter — the caption reads
"111 vendors approved by ADNOC and Astra" and must follow what was asked for —
and a new `selectable_approvers` carries `AVAILABLE_APPROVERS`, so the browser
builds the chips from the server's names and still never spells "ADNOC" itself.
That is the same rule `client_approver` on the RFQ payload exists to keep.

## 4. Selection — `web/src/pages/ItemDetail.tsx`

A checkbox column at the far left of the table, and above it:

```
[Select these 25]  [Clear]     3 selected · 1 not shown
```

- **Selection is a `Set` of bidder ids**, never row indices. The table re-sorts
  under a search and re-fetches under a chip, and an index would move a tick
  onto a different company. Third instance of that rule on this screen.
- **`Select these 25` adds only the rendered rows**, and names the count it is
  adding. The table caps at `BIDDER_CAP` of up to 1,346, so this is the only
  scope where what you tick is what you can see. Selecting all 1,346 behind one
  click, then inviting them, is a mistake with no undo — the shortlist would
  have to be unwound vendor by vendor.
- **Selection survives filtering, and says what it is hiding.** Ticking three
  vendors and then typing in the search does not silently discard them; the
  line reads `3 selected · 1 not shown`. Pruning on every keystroke would throw
  away the reader's work, and dropping the count would let them invite a vendor
  they can no longer see without knowing it.

## 5. Bulk invitation

**Shortlist selected (3)** appears once anything is ticked, beside the existing
per-row buttons, which stay for the single-vendor case.

- **Each vendor is sent individually**, through the same
  `inviteRegisteredBidder(target, { vendor_id })` the row button uses. There is
  no bulk endpoint and this design does not add one: the server's per-vendor
  guards — a blocked bidder demanding an `override_reason`, a bidder already on
  the shortlist — are what make an invitation an attributed act, and a bulk
  route would have to either reimplement them or bypass them.
- **A refusal fails alone.** Failures land in the existing per-bidder
  `rowError` map and render beside their own rows; the vendors that succeeded
  stay invited. The summary line reports both: `5 invited · 1 refused`.
- **Not all-or-nothing**, deliberately. One blocked vendor among fifty would
  otherwise block the batch, and there is no transaction to roll the others
  back with — the writes have already landed through `locked_update`.
- **Successful ids leave the selection; refused ids stay ticked**, so the
  reader can fix the reason and retry exactly the ones that failed.
- **One reload at the end**, not one per vendor: the screen re-reads once the
  batch finishes, so the Invited marks and the RFQ summary move together.
- The target RFQ resolves exactly as the row button's does — none, one, or the
  chooser — and the bulk button is absent when there is no target.

## 6. The file control — `web/src/pages/forms.tsx`

The native input's label is browser-owned, so the only fix is to stop showing
the native control:

- The `<input type="file">` **stays in the DOM** with its `id`, `accept` and
  label association intact, visually hidden. `getByLabelText(/fill in from the
  enquiry document/i)` keeps working, so last phase's tests hold unchanged.
- A **Choose file** button triggers it through a ref.
- One status line, which is now the only thing reporting state:

  | state | shows |
  |---|---|
  | nothing picked | `Optional. PDF, Word or Excel.` |
  | reading | `Reading the document…` |
  | held | **✓** `filename.xlsx`, and the View control |

- The tick is `aria-hidden` and the line is `role="status"`, so a screen reader
  is told the filename changed rather than reading out a glyph.

`e.target.value = ''` after the read **stays** — it is what makes picking the
same file twice read it twice — and nothing is uploaded, so the View control is
still an object URL that dies with the form.

## 7. What deliberately does not change

- **`bidders.available()` and its AND rule.** The chips query it; they do not
  redefine it.
- **`/bidders/approved`**, the wizard's Shortlisting step, and every other
  caller of `fetchAvailableBidders`.
- **No bulk invitation endpoint.** See §5.
- **`BIDDER_CAP` stays at 25.** Raising it to make "select all" safer would
  make the table slower and the problem larger.

## 8. Testing

| file | what it covers |
|---|---|
| `tests/test_bidder_endpoints.py` | The default is both approvals; one approver widens to the client's list; an unknown approver is refused with 422 naming it; an empty list is refused; the payload carries applied `approvers` and full `selectable_approvers`. |
| `web/src/pages/ItemDetail.test.tsx` | The search input carries `.input` and is not wrapped in a `.field` label. Chips render ticked and refetch on change; the last ticked chip is disabled; the internet chip is disabled. Selection is by id and survives a search that hides a ticked vendor, with the "not shown" count. `Select these N` adds only rendered rows. Bulk invite calls once per vendor, keeps refused ones ticked with their refusal beside their own row, and reloads once. |
| `web/src/pages/ProjectDetail.test.tsx` | The file control shows a tick and the filename, the native input is not visible, and Choose file opens it. |

**Baselines.** `CLAUDE.md`'s two Python rows are re-measured and the CI row
derived by the documented subtraction. The AVL gate stays at eleven and is not
re-measured — nothing here touches the three files carrying the marker. The web
count moves separately.
