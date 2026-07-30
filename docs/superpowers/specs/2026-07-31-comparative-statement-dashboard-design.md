# Design: Comparative Statement Dashboard

Status: approved, not yet planned
Depends on: phases 1–3 (store, routed extraction, requirements & compliance)
Belongs to: phase 4 — Portal review UI
Reference artefact: `data/procurement-data/Comparative Statement (CS) - Gas Generators [Rev.2].xlsx`

---

## 1. Context and goal

Phase 4 of [the store design](2026-07-29-project-scoped-extraction-store-design.md) splits
`portal/app.py` into a shell plus six review screens. This spec adds a seventh, and makes
it the landing screen: a **Comparative Statement** reproducing the one-page sheet a
reviewer already reads when awarding a package.

The reference sheet is the artefact this replaces. It is hand-made, and everything on it
except two rows is already in the store after phase 3.

### Success criteria

- Opening a project shows the statement without a re-run.
- The five columns of the reference sheet are reproducible from stored data, modulo the
  two documented divergences in §5.
- A reviewer's technical-feedback note survives a re-run that re-extracts the vendor.
- The screen renders honestly when data is missing: blanks, never zeros.

---

## 2. What the reference sheet contains

Columns are **offers**, not vendors — KERUI appears twice (standard, and "KERUI (Chinese
Brand)"), each with its own `Rev.` row. Each column splits into Qty / Unit Price / Total.

Rows fall into two halves:

| half | rows |
|---|---|
| priced | Gas Generator + Catalytic Convertor · PEMS (Optional) · Tools (Optional) · Two Years Spares (Optional) · Freight · Balance of Plant (Optional) · VAT 5% |
| single value | VAT Included · Discount % · Discount Amount · **FINAL VALUE** · Engine Make · Delivery Time · Delivery Terms · Payment Terms · Vendor Quotation File Name · Technical Feedback |

**The sheet is already sparse.** Only "Gas Generator + Catalytic Convertor" is filled
across all five columns. PEMS and Tools are KERUI-only, Freight is MKON-only, VAT 5% is
ADPOWER-only, Balance of Plant is AESL-only. A human aligned those rows by hand.

**FINAL VALUE is a column sum, and it includes the optional items.** Verified against
every column:

| offer | sum | FINAL VALUE |
|---|---|---|
| KERUI | 1,170,000 + 83,000 + 17,000 + 30,000 | 1,300,000 |
| KERUI (Chinese) | 847,000 + 83,000 | 930,000 |
| ADPOWER | 1,194,196.92 + 59,709.85 VAT − 119,419.69 discount | 1,134,487.07 |
| MKON | 1,707,773.09 + 17,500 freight | 1,725,273.09 |
| AESL | 1,350,000 + 490,000 BOP | 1,840,000 |

---

## 3. What the store already holds

[`BidExtraction`](../../../procurement/models.py) carries `base_price`, `vat_included`,
`vat_rate`, `freight_amount`, `freight_included`, `discount_pct`, `delivery_time`,
`delivery_terms`, `payment_terms`, `engine_make`, and `optional_items` — each with
`qty`, `unit_price`, `total`, and `included_in_base`, which is exactly MKON's
*"This has been included in Base Price."* cell.

`revisions.py` resolves quotation lineage, supplying the `Rev.` row. `classify.py` gives
the `quotation` document class, supplying the file-name row. Phase 3's compliance matrix
supplies the technical-feedback tally.

Two rows have no source: the base scope's **Qty and Unit Price** (we store `base_price`
as a scalar), and the reviewer's **prose judgement**.

---

## 4. Architecture

Two modules, split so the arithmetic is testable without a browser.

**`procurement/statement.py`** — a pure builder.

```python
def build_statement(root: str, slug: str) -> Statement
```

Reads only through `snapshots.load_documents`, `load_facts`, `list_fact_vendors`,
`load_compliance`, and `project.load_project`. No Streamlit import. No writes.

**`portal/views/statement.py`** — renders it. The landing screen of the phase-4 shell.

The builder owns the statement's own arithmetic. `normalize_bid` is read, never
re-derived. Two bases, each computed in exactly one place.

### Record model

```python
class StatementCell(BaseModel):
    qty: float | None = None
    unit_price: float | None = None
    total: float | None = None
    text: str | None = None      # value/text rows
    note: str | None = None      # e.g. "included in base price"

class StatementRow(BaseModel):
    key: str                     # stable: "base_scope", "opt:<normalised desc>", "delivery_time"
    label: str
    kind: str                    # priced | value | text
    cells: dict[str, StatementCell]   # vendor -> cell; absent key = blank

class Statement(BaseModel):
    project: str
    currency: str                         # target currency; labels the Normalised row only
    generation: int
    vendors: list[str]                    # column order = project.vendors
    revisions: dict[str, str | None]      # live quotation's revision_label
    rows: list[StatementRow]
```

`key` is stable across runs so the view can address a row without depending on list
order — the same reason `field_path` addresses list members by id.

### Row order

| # | rows | kind | source |
|---|---|---|---|
| 1 | Base scope | priced | `commercial.base_price` — Total only; Qty and Unit Price blank |
| 2 | one row per distinct optional-item description | priced | `optional_items`, first-seen order across columns |
| 3 | Freight | priced | `freight_amount`; blank when `freight_included` |
| 4 | VAT | priced | rule (b) below |
| 5 | VAT Included · Discount % | value | `vat_included`, `discount_pct` |
| 6 | Discount Amount | priced | rule (a) below |
| 7 | **FINAL VALUE** | priced | column sum |
| 8 | Normalised (ex-VAT, ex-options, `<target>`) | priced | `NormalizedBid.normalized_total`, gated on a known base price — see below |
| 9 | Engine Make · Delivery Time · Delivery Terms · Payment Terms | text | `BidExtraction` |
| 10 | Vendor Quotation File Name | text | live `quotation` `DocumentRecord.path`, basename |
| 11 | Technical Feedback | text | compliance tally + stored note (§6) |

Optional-item rows are grouped by **exact description after normalisation** — lowercase,
collapsed whitespace, trailing `- optional` stripped. Differently-worded items from two
vendors get two rows. This is deliberate: automatic merging of free-text descriptions
would silently equate scopes that are not equal, and the reference sheet's own sparseness
shows the human did this by hand. Manual merging is out of scope (§8).

---

## 5. The three arithmetic rules

### (a) Discount base

```
FINAL VALUE = (base + optionals + freight + VAT) − discount_amount
discount_amount = discount_pct × (base + optionals + freight)      # VAT excluded
```

This reproduces ADPOWER exactly: 10% × 1,194,196.92 = 119,419.692, not 10% of the
VAT-inclusive 1,253,906.77.

**Assumption flagged.** ADPOWER is the only column in the reference sheet with a
discount, and it has neither optionals nor freight — so the sheet cannot distinguish
"discount on base only" from "discount on everything pre-VAT". The latter is assumed.
Revisit if a real quotation contradicts it.

Items with `included_in_base = True` contribute zero to every sum; their cell shows the
note instead of a number, so the scope is visible without being double-counted.

### (b) VAT

- `vat_included = False` and `vat_rate > 0` → VAT row = `base × vat_rate`, added to the sum.
- `vat_included = True` → VAT row shows *"included"*, contributes zero.

**Documented divergence.** The reference sheet's ADPOWER column is internally
contradictory: `VAT Included = YES`, yet VAT 5% is added as a separate priced line.
Adding VAT to a price that already contains it double-counts. Four of the five columns
reproduce exactly under this rule; ADPOWER's will differ if its `vat_included` extracts
as `True`, and the *"included"* note makes the divergence visible rather than silent.

### (c) Currency

Priced cells stay in each vendor's quoted currency, with the currency printed in the
column header. A column sum across mixed currencies is meaningless, so no cross-column
arithmetic is performed on the priced rows. Only the **Normalised** row is in the
project's target currency, and it is the row to compare across columns when currencies
differ. On the reference corpus everything is USD, so the two read side by side.

The Normalised row is `NormalizedBid.normalized_total` verbatim, on
[`normalize_bid`](../../../procurement/normalize.py)'s own basis: FX-converted, VAT
**removed**, freight added, discount applied, **optional items excluded**. That is a
different basis from FINAL VALUE, which is why both rows exist and why each carries its
basis in its label. `normalize_bid` is not modified by this work.

---

## 6. Technical feedback

The reference sheet's cells are human prose: *"FULLY COMPLIED"*, *"Equipment suitable
only for H2S content ≤ 30ppm. However as per project specifications, 50ppm is the minimum
requirement. Also SOx not complied."*

Phase 3 supplies verdicts but they are **not yet summarisable into a word**. The
[2026-07-31 live run](../../../.superpowers/sdd/2026-07-30-requirements-compliance-phase3/progress.md)
measured 81.0% `review` and found 6 of 17 `fail`s to be wrong (the `in`-operator range
bug). Auto-writing "FULLY COMPLIED" from that matrix would be fabrication.

The cell therefore shows **both**:

- the live tally, computed in the builder from `load_compliance`
  (`17 fail · 4 deviation · 52 unanswered · 417 review`), and
- the reviewer's note, when one has been written.

### Storage: an annotation, not an override

The note is stored as a real field, `VendorFacts.technical_feedback: str | None`, written
through `snapshots.transaction`.

**It is deliberately not an `Override`.** `reconcile` resolves each override's
`field_path` against the fresh extracted record, and a path that does not resolve sets
`conflict = True` unconditionally. A synthetic path such as `technical_feedback` resolves
to nothing in the extracted view, so every run would raise a false conflict. Showing the
note beside the live tally gives the reviewer the same visibility that `conflict` exists
to produce — a note gone stale under a shifted tally is visible side by side — without
inventing a conflict the store would then have to carry.

Consequently this screen does **not** contribute to phase 4's
"a disagreeing re-extraction appears on the Conflicts screen" done-criterion. The Facts
and Requirements screens, which override genuinely extracted values, do.

Editing UI: an expander per vendor holding a text area pre-filled with the tally, a short
reason field per [spec §8](2026-07-29-project-scoped-extraction-store-design.md), and a
Save button. Saving writes the note, appends one `Event`
(`action="facts.feedback_edited"`, `target=<vendor>`, `detail={"reason": ..., "tally": ...}`),
and bumps `generation` — all in a single `snapshots.transaction`. The reason lives in the
event, not on the field: `VendorFacts` has no per-field metadata slot, and the History
screen is where a rationale is read anyway.

### The carry-forward risk, named

[`pipeline.py`](../../../procurement/pipeline.py) constructs a fresh `VendorFacts` on each
run, so the new field requires a deliberate `technical_feedback=base.technical_feedback`
carry-forward. This is the identical shape to the `vocabulary_sha` carry-forward that
phase 3's Task 7 caught: a guard stated in prose, mandated by no test, and invisible to
every single-run assertion. It gets its own sabotage-verified test (§9), not a prose note.

---

## 7. Rendering

**Two tables, not one.** Streamlit's grid cannot merge a cell across the three
Qty/Unit/Total sub-columns, and KERUI's five-line payment terms are unreadable squeezed
into a one-third-width column. The `kind` field routes each row:

- **Priced table** — MultiIndex columns `(vendor, Qty | Unit Price | Total)`; rows where
  `kind == "priced"`. Column header carries the vendor's quoted currency and `Rev.` label.
- **Attributes table** — one column per vendor; rows where `kind` is `value` or `text`.

Both via `st.dataframe`. The existing Excel and CSV download buttons are repointed at the
statement instead of today's `ComparisonTable`; `export.py` gains
`statement_to_rows` / `statement_to_xlsx_bytes` / `statement_to_csv_str` beside the
existing comparison exporters, which stay for the migration path.

---

## 8. Out of scope

- **Base-scope Qty and Unit Price.** BOM line-item extraction, roadmap item per
  [spec §12](2026-07-29-project-scoped-extraction-store-design.md).
- **Alternative offers per vendor** — the KERUI two-brand case. Column = vendor, matching
  how the store is keyed. Where a folder yields several non-superseding quotations, the
  statement uses `pick_quote`'s selection and captions the others, so the omission is
  visible rather than silent. Making column = offer would change `facts.json` keying, the
  compliance matrix's vendor axis, and INV-6's cell count.
- **Manual merging** of differently-worded optional items.
- **Choosing which optionals count toward a total** — full scope normalization, spec §12.
- **Changing `normalize_bid`'s basis.**
- The other five phase-4 screens, and the `app.py` shell split itself, which this screen
  lands into.

---

## 9. Store invariants and testing

| id | invariant |
|---|---|
| INV-S1 | `build_statement` is read-only: opens no transaction, bumps no `generation`, writes no file. |
| INV-S2 | The statement has exactly one column per vendor in `project.vendors`, in that order — a vendor removed from the project loses its column; a vendor with no facts keeps a full column of blanks. Same shape as INV-6. |
| INV-S3 | A technical-feedback note survives a re-run that re-extracts the vendor. |

**Deterministic units on the builder** (no Streamlit, no client): column order · sparse
union of optional descriptions · `included_in_base` contributes zero and shows its note ·
discount rule (a) · VAT rule (b) in both directions · a `failed` vendor yields blank
cells, never zeros · mixed-currency column headers · `normalized_total is None` leaves the
Normalised row blank.

**Two-run rows**, per [`PLAN-TEMPLATE.md`](../PLAN-TEMPLATE.md) Rule 2 — each names the
invariant it defends and is verified by reinstating the defect:

| mutation | invariant at risk | assert |
|---|---|---|
| drop the `technical_feedback` carry-forward in `pipeline.py` | INV-S3 | note written in run 1 is still present after run 2 re-extracts the vendor |
| remove a vendor from the project between runs | INV-S2 | run 2's statement has no column for it |
| add a vendor with no extractable quotation | INV-S2 | it gets a full blank column, not an absent one |
| make `build_statement` write | INV-S1 | `generation` is unchanged across two consecutive builds |
| supersede the live quotation between runs | INV-S2 | the file-name and `Rev.` rows show the superseding document |
| change a requirement so the tally shifts | — | note and new tally are both visible; the note is not overwritten |

**Error handling.** A vendor with `extraction_status == "failed"` gets a column of blanks
with the status in its header — never zeros, per CLAUDE.md's coercion rule. Missing
`commercial`, absent facts, or `normalized_total is None` render blank without crashing.
A project with no vendors or no documents shows a message, not an empty grid.

**Amended during Task 4 — the schema's non-null defaults.** `BidExtraction` gives
`base_price: float = 0.0` and `currency: str = ""`, so an extraction that *succeeded and
found nothing* is byte-identical to one that found zero, and `extraction_status` stays
`"ok"`. Three rows therefore need more than the `is None` checks this section originally
described:

- **Base scope / FINAL VALUE** — a `base_price` of `0.0` is read as unknown, not as free.
  No vendor quotes a zero base scope, and a `0.00` FINAL VALUE sorts to the top of the
  award screen as the cheapest bid.
- **Normalised** — `normalize_bid` derives its total *from* `base_price`, so this row
  inherits the same zero. It is blanked when the **base** is unknown, not when the total
  is zero: a 100% discount normalises to a genuine `0.0` that FINAL VALUE also prints,
  and the two rows must not contradict each other. The gate also covers a `normalized`
  that outlives its `commercial` — [`pipeline.py`](../../../procurement/pipeline.py)
  carries the prior one forward whenever a run resolves no commercial terms.
- **Freight** — `freight_amount: float = 0.0` likewise, but a zero freight contributes
  zero to the column either way, so suppressing the row is the whole of it.

The proper fix is nullable defaults on `BidExtraction`; that changes the extractor prompt
contract and `normalize_bid`, both outside this plan's scope, and is carried in the phase
ledger as an open escalation. Until then `VAT Included: NO` and `Discount %: 0%` still
appear for vendors whose quotation mentioned neither.

**A blank cell may carry a note.** `total is None` with `note` set now occurs on the
optional rows (`included in base price`), on FINAL VALUE (`base price not stated`,
`excludes N option(s) with no stated price`), and on VAT (`included`). §7's priced table
defines only Qty / Unit Price / Total sub-columns, so the note renders into the Total
cell — Task 6's export must not assume that column is numeric.

---

## 10. Open questions carried forward

- The discount base in rule (a) is an assumption the reference corpus cannot confirm.
- The ADPOWER VAT divergence in rule (b) should be checked against the actual extracted
  `vat_included` value on a real run before the screen is shown to a reviewer.
- The [live run](../../../.superpowers/sdd/2026-07-30-requirements-compliance-phase3/progress.md)
  found three defects that make the tally in §6 misleading today: `in` produces false
  FAILs on ranges, deviations match across documents, and triplicate MR documents inflate
  the requirement count. None is caused by this screen, and none is fixed by it — but the
  tally it displays will be wrong in those specific ways until they are addressed.
