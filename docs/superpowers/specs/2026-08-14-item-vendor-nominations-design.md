# Client and contractor vendor lists, per item — design

**Date:** 2026-08-14
**Branch:** `rfq-platform-phase-1`
**Follows:** [`2026-08-14-vendor-list-filtering-and-bulk-shortlisting-design.md`](2026-08-14-vendor-list-filtering-and-bulk-shortlisting-design.md)
**Status:** the parser's input format is **open** — see §8.

---

## The problem

Two documents arrive against a package in real procurement, and neither has
anywhere to go in this product:

- the **client** names the vendors they want approached for this package;
- the **main contractor** names theirs.

The registry answers a different question. It is organisation-wide by design —
"a prequalification is a fact about a company, and holding it per project would
mean re-entering and re-approving the same company for every one of them" — and
it holds the client's whole Approved Vendor List (1 346 rows) and ours (111).
Neither of those says **who was named for this generator package**, which is the
question a buyer actually has when they open an item.

So the fact is item-scoped, it arrives as a document, and there is no store for
it.

## What this builds

- Two upload controls on the item form, where the annotation puts them.
- A new **item-scoped stored collection**: vendor *nominations*, each carrying
  its source.
- Linking to registry bidders by folded name, so a nominated vendor shows its
  real approvals; unmatched names are kept and marked, never invented into the
  registry.
- A card per source on the item screen.

**This is the first change in three phases that stores something new**, which is
why it gets the full treatment `PLAN-TEMPLATE.md` asks for: a named store
invariant and a real two-run mutation matrix (§9).

---

## 1. A nomination is not an approval

The load-bearing rule, stated first because everything else bends around it.

`Bidder.approved_by` continues to come **only** from a real Approved Vendor List
import. Uploading a client list on this form records that somebody was *named
for this item*; it does not make them ADNOC-approved, and it never writes to
`bidder_approvals`.

This is the same rule the mock-round fixtures already keep, pointing the same
way: *a synthesised approval must not be indistinguishable on screen from a
recorded one*. A spreadsheet dropped on an item form making a company read as
client-approved across every project in the platform is precisely the failure
that rule exists to prevent, and it would be invisible afterwards — the row
would look identical to one imported from the client's own export.

What the screen shows instead: each nominated vendor's **existing** approvals,
rendered with the same `ApprovalPills` the rest of the product uses. "Nominated
by the client **and** on the client's AVL" is then visible at a glance, and so
is "nominated but not approved by anyone", which is the interesting case.

## 2. Where it is stored — `workflow.json`

In the document, beside the other item- and RFQ-scoped collections, **not** in
`bidders.db`.

`CLAUDE.md` draws the line as "the registry is in SQLite; everything else is in
the JSON document", and justifies the split on the registry being reference data
that arrives whole from a client export, runs to ~1 300 rows, and is queried by
attribute. A nomination list is none of those: it is tens of rows, it belongs to
one item, and it is read whole with that item.

**The deciding reason is the cascade.** `delete_item` and `delete_project` run
inside `persistence.locked_update` on the document. A nominations table in
SQLite would put the cascade across two stores and two write paths, which is
exactly the orphaning shape `PLAN-TEMPLATE.md`'s Rule 1 was written about. In
the document it is one more line in a cascade that already exists and is already
tested.

### Store invariant owned

> `workflow.json` holds exactly the nominations of items that still exist — no
> more. Deleting an item removes its nominations in the same write, and
> deleting a project removes the nominations of every item it took with it.

## 3. The model — `workflow/models/item.py`

```python
NominationSource = Literal["Client", "Contractor"]


class VendorNomination(BaseModel):
    """One vendor named for one item by one party.

    `vendor_name` is stored **verbatim as the document wrote it**, so the
    record can be read against the source. `vendor_id` is the registry link
    where the name matched, and `None` where it did not — the same third state
    the shortlist keeps, and for the same reason: an unmatched name is "we
    could not find them", not "they are unapproved".
    """

    id: str
    item_id: str
    source: NominationSource
    vendor_name: str
    vendor_id: str | None = None
    trade: str | None = None
    contact: str | None = None
    nominated_by: str          # the signed-in user, from the session
    nominated_at: datetime
    source_document: str       # the uploaded filename, so a row is traceable
```

There is deliberately **no** `approved` field, and no copy of the linked
bidder's name, status or approvals: those are read live through `vendor_id`, the
rule `client_approved` and `approved_by` on the shortlist already keep.

## 4. Matching a name to the registry

Exact, after folding — **never substring**.

`bidder_db.fold` (re-exported from `workflow/disciplines.py`) trims, collapses
*internal* whitespace and casefolds. That is the right normalisation for a
company name as well as a product group, and reusing it means one rule rather
than two that can drift. This repository has twice recorded substring matching
as a shipped defect; `"Al Masaood"` must not match `"Al Masaood Oil Industry
Supplies"`, because they are two different companies in this very registry.

A name that matches nothing is stored unlinked. It is **not** created as a
bidder: the registry arrives whole from a client export, and a name typed into
a contractor's spreadsheet is not evidence a company exists in it.

## 5. A second upload replaces, it does not accumulate

Uploading a client list again replaces **that item's Client nominations
wholesale**, leaving Contractor untouched, and vice versa.

Appending would leave the first upload's vendors present with nothing to
distinguish them from the second's — the reader could not tell a vendor who was
dropped from the revised list from one who is still on it. This is the same
"a stored collection contains exactly the records of its currently-live source"
rule the procurement store states, applied to a document instead of a folder.

## 6. The route

```
POST /api/workflow/projects/{project_id}/items/{item_id}/vendor-list
     ?source=Client|Contractor          multipart file upload
```

- Runs inside `persistence.locked_update`, like every other write: the read
  that resolves the item and the write that replaces its nominations are one
  critical section.
- Refuses an unknown `source`, and an item that does not belong to the project,
  with the store's own sentence.
- Refuses a file whose required header is missing, **naming the header** —
  `avl_import.py`'s rule, and the reason it locates columns by header and never
  by position: a re-export with a reordered column would otherwise be silently
  wrong about every company in it.
- Returns the stored nominations plus a summary: how many rows were read, how
  many linked to the registry, how many did not.

**Unlike `/rfqs/extract`, this route stores.** That difference is deliberate and
worth stating, because the two look similar: an enquiry document fills in a form
the reader then submits, so storing it would mean undoing rather than
correcting. A nomination list *is* the record — there is no later form that
captures it.

## 7. The screens

**The item form** ([`forms.tsx`](../../../web/src/pages/forms.tsx)) gains the two
controls beside `Save item` / `Cancel`, exactly where the annotation puts them:
**Add client list** and **Add contractor list**.

They render **only when editing an existing item**. On the create form there is
no item id to attach an upload to, so they are absent and a line says to save
the item first. Holding the file and posting it after the save was rejected: a
failed save would leave the reader believing a file was uploaded, and a failed
upload after a successful save would leave the item half-populated with no
indication which half.

Both reuse the file control this phase just built — a visually hidden input
behind a **Choose file** button, with one `role="status"` line showing a tick
and the filename — rather than a second pattern.

**The item screen** gains a card per source, below the item detail and above the
available-vendor list. Each row: the vendor name as the document wrote it, the
registry link's approval pills where it linked, and `not in the registry` where
it did not. A summary line reads e.g. `24 nominated · 19 in the registry · 5 not
found`, and the counts are reported separately for the reason §3 gives.

## 8. Open: the document format

**This is the one thing not decided, and it blocks only the parser.**

The samples are coming from the user. Until they arrive the parser is not
written, because a parser written against a guessed layout is worse than none:
it would appear to work and be wrong about which column held the vendor name.

What is already settled regardless of the samples:

- Columns are located **by header, never by position**, and a missing required
  header raises and names itself. This is `avl_import.py`'s convention and the
  reason is recorded there.
- The minimum required column is a vendor name. Trade and contact are optional
  and stored when present.
- `.xlsx` via `openpyxl`, which is already a dependency.

`data/bidders_details/ADNOC AVL subset - Cables and Generators.xlsx` is an
existing client subset scoped to two families and is the closest thing in the
repository to a per-package client list. It is **not** assumed to be the format;
it is noted as a candidate to check the samples against.

## 9. Testing, and the mutation matrix

This phase adds an accumulating collection, so `PLAN-TEMPLATE.md`'s Rule 2
applies for real here — unlike the previous two phases, where the honest answer
was that there was nothing to mutate.

| mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|
| an item holding nominations is deleted | `workflow.json` holds exactly the nominations of live items | no nomination in the reloaded document names the deleted item |
| a project holding such an item is deleted | same, through the item cascade | same, for every item the project took |
| the same source is uploaded again | a collection holds exactly its current source's records | the first upload's vendors are gone, not merged |
| the *other* source is uploaded | replacement is per source | Client nominations survive a Contractor upload untouched |
| a linked bidder is deleted from the registry | a nomination never becomes a dangling reference | the row survives, reading as unlinked rather than 404ing the screen |
| a linked bidder's `approved_by` is corrected | approvals are read live, never copied | the row's pills follow the registry with no second write |
| a linked bidder is renamed | the link is by id, not by name | the row still links, and still shows the name the document wrote |
| the upload raises partway through parsing | a failed write leaves the document untouched | the previous nominations are still there, unchanged |

Each row is verified by reinstating the defect it defends and confirming that
row — and only that row — fails.

Beyond the matrix: `tests/test_item_nominations.py` for the pure matching rule
(exact after folding; `"Al Masaood"` does not match `"Al Masaood Oil Industry
Supplies"`), and endpoint tests for the two refusals and the summary counts.
Web tests for the edit-only controls, the per-source cards, and the three-state
registry mark.

**Baselines.** Both `CLAUDE.md` rows are re-measured and the CI row derived by
the documented subtraction. Nothing here reads the AVL export, so the gate stays
at eleven and is not re-measured.

## 10. What deliberately does not change

- **`approved_by`, `bidder_approvals`, and the AVL import.** §1.
- **The registry stays organisation-wide.** Nominations reference it; they never
  add to it.
- **`/rfqs/extract` still stores nothing.** §6 says why the two differ.
- **The shortlist is untouched.** A nomination is not an invitation — inviting
  still goes through `POST /rfqs/{id}/shortlist` and its guards. Wiring
  "shortlist everyone the client nominated" is an obvious next step and is
  deliberately **not** in this phase.
