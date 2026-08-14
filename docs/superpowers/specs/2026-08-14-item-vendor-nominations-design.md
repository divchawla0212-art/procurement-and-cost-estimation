# Client and Astra vendor lists, per item — design

**Date:** 2026-08-14
**Branch:** `rfq-platform-phase-1`
**Follows:** [`2026-08-14-vendor-list-filtering-and-bulk-shortlisting-design.md`](2026-08-14-vendor-list-filtering-and-bulk-shortlisting-design.md)

> **Revised after reading the real files.** A first draft of this spec guessed
> at the document format, called the second source "Contractor", planned a new
> parser, and justified its storage choice on the list being "tens of rows".
> Three of those four were wrong. What replaced them is measured, and §8 records
> the measurements so the next reader does not have to repeat them.

---

## The problem

Two documents arrive against a package, and neither has anywhere to go:

- the **client's** approved vendor list, and
- **Astra's** own list — the subset of it we have qualified.

The registry answers a different question. It is organisation-wide by design —
"a prequalification is a fact about a company, and holding it per project would
mean re-entering and re-approving the same company for every one of them" — and
it holds the client's whole list and ours. Neither says **which of those vendors
matter for this generator package**, which is the question a buyer has when they
open an item.

## What this builds

- Two upload controls on the item form: **Add client list** and **Add Astra
  list**, where the annotation puts them.
- A new **item-scoped stored collection**, filtered to the item's discipline on
  the way in.
- Registry links by vendor id, so each row shows the vendor's real approvals.

**This is the first change in three phases that stores something new**, so it
carries a named store invariant and a real two-run mutation matrix (§9).

---

## 1. A nomination is not an approval

`Bidder.approved_by` continues to come **only** through
`python -m workflow.avl_db`. This route never writes to `bidder_approvals` and
never creates a bidder.

The reasoning changed once the files were read, so it is worth stating
precisely. These are not arbitrary spreadsheets — they are the genuine ADNOC
export and a genuine subset of it, so a vendor on them really is approved. The
objection is not that the evidence is weak; it is **the scope of the side
effect**. An upload attached to one item would silently rewrite what a company
is approved for across every project in the platform, from a form whose subject
is a single line of equipment. Registry changes stay on the deliberate,
auditable path that already exists.

What the screen shows instead: each row's **existing** approvals, via the same
`ApprovalPills` the rest of the product uses. "On the client's list **and**
ours" and "on the client's list only" are then both visible, which is the
distinction the two uploads exist to draw.

## 2. Where it is stored — `workflow.json`

In the document, beside the other item- and RFQ-scoped collections.

**The sizing objection is real and the filter is what answers it.** The client
export yields **1 346 vendors** (measured, §8). Storing that per item in a
document read and written whole would be indefensible. Filtered to the item's
discipline (§5) a realistic list is **tens** — the Cables product groups run 26
to 52 vendors each — which the document carries comfortably.

**The deciding reason is still the cascade.** `delete_item` and `delete_project`
run inside `persistence.locked_update` on the document. A table in `bidders.db`
would put the cascade across two stores and two write paths, which is exactly
the orphaning shape `PLAN-TEMPLATE.md`'s Rule 1 was written about. In the
document it is one more line in a cascade that already exists and is tested.

### Store invariant owned

> `workflow.json` holds exactly the vendor-list entries of items that still
> exist — no more. Deleting an item removes its entries in the same write, and
> deleting a project removes the entries of every item it took with it.

## 3. The model — `workflow/models/item.py`

```python
VendorListSource = Literal["Client", "Astra"]


class ItemVendorEntry(BaseModel):
    """One vendor on one item's list, from one of the two uploads.

    `vendor_id` is the registry key, and it is the export's own vendor number
    (`bdr_<number>`) — the same scheme `avl_import` uses, which is why matching
    is an exact id lookup rather than name comparison. `None` means the export
    named a vendor number the registry does not hold: a real, reportable state,
    and not the same as "unapproved".
    """

    id: str
    item_id: str
    source: VendorListSource
    vendor_id: str | None
    vendor_name: str            # as the export wrote it
    trade_categories: list[str] # the item-relevant groups only
    uploaded_by: str            # from the session, never the request body
    uploaded_at: datetime
    source_document: str        # the filename, so a row is traceable
```

No `approved` field and no copy of the linked bidder's status or approvals:
those are read live through `vendor_id`, the rule `client_approved` and
`approved_by` on the shortlist already keep.

## 4. Matching is an exact id lookup

`parse_avl` already sets `id=f"bdr_{vendor_number}"`, and its comment says why:
"a real, stable key, so re-importing a later export updates the same bidder
rather than creating a near-duplicate under a slightly different spelling."

The registry was built from the same exports with the same scheme, so a parsed
vendor either **is** `store.get_bidder("bdr_10007739")` or is genuinely absent.
There is no fuzzy matching, no name folding, and no substring comparison —
which is fortunate, because this registry contains both `AL MASAOOD L.L.C` and
`AL MASAOOD OIL INDUSTRY SUPPLIES &S`, and substring matching has already been
recorded twice in this repository as a shipped defect.

## 5. The discipline filter is what makes the list item-specific

On upload, the parsed vendors are narrowed to those registered for the item's
discipline, expanded through `workflow/disciplines.py` — the same expansion
`/bidders/available` uses, so "Cables" reaches the eleven cable product groups
the sheet actually names rather than looking for a group called "Cables" and
finding none.

Without this the client upload would attach all 1 346 vendors to a single line
of equipment, and "a vendor DB specific to that item" would not be true of it.

**Each entry's `trade_categories` holds only the item-relevant groups**, not the
vendor's full list — a cable supplier who also sells valves is on this item's
list *for cables*, and showing the valves would be noise on this screen.

**An item whose discipline matches nothing keeps the upload and reports it**:
the response says how many rows were read and how many survived the filter, and
a zero survives as an empty list with that sentence rather than as a silent
success. Items predating the discipline vocabulary carry free text like `"1"`,
so this is a live path, not a transitional one.

## 6. The route

```
POST /api/workflow/projects/{project_id}/items/{item_id}/vendor-list
     ?source=Client|Astra          multipart file upload
```

- Runs inside `persistence.locked_update`: the read that resolves the item and
  the write that replaces its entries are one critical section.
- Refuses an unknown `source`, and an item not belonging to the project, with
  the store's own sentence.
- Refuses a file missing a required header, naming it — `parse_avl` already
  does this, and the reason is recorded there: a re-export with a reordered
  column would otherwise be silently wrong about every company in it.
- Returns the stored entries plus a summary: rows read, vendors parsed, kept
  after the discipline filter, and how many linked to the registry.

**A second upload of the same source replaces that source's entries wholesale**,
leaving the other source untouched. Appending would leave the previous upload's
vendors present with nothing to distinguish them from the current list — a
vendor dropped from a revised export would be indistinguishable from one still
on it. Same rule as "a stored collection contains exactly the records of its
currently-live source", applied to a document rather than a folder.

**Unlike `/rfqs/extract`, this route stores**, and the difference is deliberate:
an enquiry document fills a form the reader then submits, so storing it would
mean undoing rather than correcting. A vendor list *is* the record.

## 7. The screens

**The item form** gains **Add client list** and **Add Astra list** beside
`Save item` / `Cancel`, where the annotation puts them. They render **only when
editing an existing item** — on the create form there is no item id to attach an
upload to, so they are absent with a line saying to save the item first. Both
reuse the file control this phase already built: a visually hidden input behind
a **Choose file** button with one `role="status"` line showing a tick and the
filename.

**The item screen** gains a card per source, above the available-vendor list.
Each row shows the vendor name as the export wrote it, its approval pills where
it linked, and `not in the registry` where it did not. The summary reads e.g.
`38 on the client's list for Cables · 34 in the registry · 4 not found`, with
the counts separate for the reason §3 gives.

## 8. What the real files contain — measured

Both uploads are the **same canonical export format**, and the existing
`parse_avl` reads both unmodified. **No new parser is written.**

| | rows | unique vendors | product groups | sheets |
|---|---|---|---|---|
| `…as of 10.12.2025(client).xlsx` | 18 033 | **1 346** | 1 141 | `Sheet1` |
| `…Cables and Generators(astra list).xlsx` | 465 | **111** | 19 | `Sheet1`, `Read me`, `Vendors`, `Coverage` |

Headers, identical in both: `Product Group Number`, `Product Group Description`,
`Vendor Number`, `Vendor Name`, `Manufacture Number`, `Manufacture name`,
`Manufacture Country`, `Remarks`.

Three things this measurement settled:

- **The extra sheets are harmless.** `parse_avl` takes `worksheets[0]`, which is
  `Sheet1` in both files. It is not the *active* sheet by accident — it is the
  first, and both files put the data there.
- **Both parse as `approved_by: ["ADNOC"]`.** The Astra file is a subset *of the
  ADNOC list*, so Astra-ness is not a fact in the document — it is the fact that
  this subset is the one we chose. That is exactly what `avl_db --astra`
  already assumes, and it is why the source is a parameter of the upload rather
  than something read out of the file.
- **Whitespace folding already happens.** The raw cell reads
  `CABLES - LV␣␣POWER DISTRIBUTION` in the Astra subset and with one space in
  the client file — the disagreement `CLAUDE.md` records between the client's
  own exports. Both land on the same folded label, so the discipline filter
  matches across the two files.

## 9. Testing, and the mutation matrix

This phase adds an accumulating collection, so `PLAN-TEMPLATE.md`'s Rule 2
applies for real — unlike the previous two phases, where the honest answer was
that there was nothing to mutate.

| mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|
| an item holding a list is deleted | the document holds exactly the entries of live items | no entry in the reloaded document names the deleted item |
| a project holding such an item is deleted | same, through the item cascade | same, for every item the project took |
| the same source is uploaded again | a collection holds exactly its current source's records | the first upload's vendors are gone, not merged |
| the *other* source is uploaded | replacement is per source | Client entries survive an Astra upload untouched |
| a linked bidder is deleted from the registry | an entry never becomes a dangling reference | the row survives, reading as unlinked rather than failing the screen |
| a linked bidder's `approved_by` is corrected | approvals are read live, never copied | the row's pills follow the registry with no second write |
| the item's discipline is changed between uploads | the filter is applied at upload, not at read | the stored list still reflects the discipline it was uploaded under, and the summary says which |
| the upload raises partway through parsing | a failed write leaves the document untouched | the previous entries are still there, unchanged |

Each row is verified by reinstating the defect it defends and confirming that
row — and only that row — fails.

Beyond the matrix: endpoint tests for the two refusals and the summary counts;
a test that the discipline filter reduces the client export to the item's
groups; a test that **no upload ever writes `approved_by` or creates a bidder**,
which is §1 stated as an assertion. Web tests for the edit-only controls, the
per-source cards and the three-state registry mark.

Tests use a **small in-memory workbook built with `openpyxl`**, as
`test_avl_import.py`'s twenty-one non-gated tests already do, so they run in CI.
Nothing here reads the real export, so the AVL gate stays at eleven and is not
re-measured.

## 10. What deliberately does not change

- **`approved_by`, `bidder_approvals`, and the AVL import path.** §1.
- **`parse_avl` itself.** It reads both files as they are; §8.
- **The registry stays organisation-wide.** Entries reference it; they never add
  to it.
- **`/rfqs/extract` still stores nothing.** §6 says why the two differ.
- **The shortlist is untouched.** A vendor on an item's list is not invited —
  inviting still goes through `POST /rfqs/{id}/shortlist` and its guards.
  Wiring "shortlist everyone on the client's list" is the obvious next step and
  is deliberately **not** in this phase.
