# Comparative Statement Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the project's vendor bids as the one-page comparative statement a reviewer already reads, sourced entirely from the phase 1–3 store.

**Architecture:** A pure builder, `procurement/statement.py`, reads snapshots and returns a `Statement` record; `portal/views/statement.py` renders it as two Streamlit tables and owns the one editable field. The builder performs the statement's own arithmetic and reads `normalize_bid`'s figure without re-deriving it. Two new `VendorFacts` fields carry a reviewer's note and the link to the quotation the commercial terms came from.

**Tech Stack:** Python 3.11+, pydantic v2, Streamlit (`streamlit.testing.v1.AppTest` for view tests), openpyxl, pytest.

Spec: [`docs/superpowers/specs/2026-07-31-comparative-statement-dashboard-design.md`](../specs/2026-07-31-comparative-statement-dashboard-design.md)
House rules: [`docs/superpowers/PLAN-TEMPLATE.md`](../PLAN-TEMPLATE.md)

## Global Constraints

- Run all tests from the repo root: `python -m pytest`.
- Baseline before starting: **486 passed, 3 skipped, 1 failed**, measured on this branch at `ed3caeb` on 2026-07-31. The one failure is `tests/test_portal_app.py::test_missing_api_key_does_not_block_creation`, environment-dependent and documented in `CLAUDE.md`. Anything else is a real regression. (`CLAUDE.md` still quotes phase 2's 272/3/1; it is stale and is not this plan's to fix.)
- Tests are key-free. No test may require `ANTHROPIC_API_KEY`; use the schema-aware stubs in `tests/test_pipeline_rfq.py`.
- **Every write goes through `procurement/store/snapshots.py`.** Never hand-roll a snapshot write.
- **`generation` bumps once per write transaction**, never once per file.
- **Missing data is never coerced to a passing or zero value.** An unfound number is omitted, not emitted as `0` or `""`. This is the single most load-bearing constraint in this plan: a comparative statement that prints `0` where a price is unknown produces a wrong award.
- **Arithmetic stays in Python.** No extractor is asked to compute or compare.
- `field_path` addresses list members by id, never by index.
- `normalize_bid` is **not modified** by this work.
- Do not implement anything under §8 of the spec (base-scope qty/unit, alternative offers per vendor, manual row merging, scope normalization, the other five phase-4 screens).

## Store invariant ledger

Every invariant is owned by exactly one task and defended by at least one row of Task 8's mutation matrix.

| id | invariant (over stored state) | owner | matrix rows |
|---|---|---|---|
| INV-S1 | `build_statement` writes nothing: `generation` and every snapshot file are byte-identical before and after a build. | Task 4 | 13 |
| INV-S2 | The statement has exactly one column per vendor in `project.vendors`, in that order — no column for a vendor the project no longer has, and a full column of blanks for a vendor with no facts. | Task 3 | 1, 2, 3, 5, 6, 7, 9, 10, 11 |
| INV-S3 | A vendor's `technical_feedback` survives every re-extraction of that vendor. | Task 1 | 4, 8 |
| INV-S4 | No compliance verdict count is ever stored: `facts.json` contains no tally, and the statement's tally is computed at read time from `compliance.json`. | Task 5 | 12 |
| INV-S5 | Saving a note bumps `generation` exactly once and appends exactly one event. | Task 7 | 14 |
| INV-S6 | `VendorFacts.commercial` and `.normalized` are present only while the vendor has a live quotation document — the same "exactly the records of its currently-live sources" rule `technical` and `deviations` already follow. | Task 2 | 2, 6 |

## Judgment calls made while writing this plan

These are decisions the spec does not settle. Each is implemented as described; flag disagreement before starting, not mid-execution.

1. **`VendorFacts` gains `quotation_doc_id`, not just `technical_feedback`.** The spec's `Rev.` and Quotation File Name rows need to know which document produced `commercial`. That selection is non-trivial — [`pipeline.py:344-381`](../../../procurement/pipeline.py) prefers a classified `quotation`, then falls back to `pick_quote` over documents no other extractor claims — and re-deriving it in the builder would duplicate logic that will drift. Storing the link at the point of extraction, where the document is already in hand, is one line and makes both rows exact.

2. **Task 2 fixes a pre-existing defect, and it is scope this plan chose to take on.** `_prune_orphan_facts` filters `technical` and `deviations` by live `doc_id` but never touches `commercial`, so deleting or reclassifying a vendor's quotation leaves its prices stored forever. This screen is the first to display those numbers as current, so shipping the screen without the fix ships a wrong award figure. It is the exact shape CLAUDE.md's "a stored collection contains exactly the records of its currently-live sources" invariant forbids. If you want it out, strike Task 2 and downgrade matrix rows 2 and 6 to documented limitations — but do that deliberately.

3. **This plan does not build the phase-4 navigation shell.** `portal/views/` is created and the statement replaces today's §5 Results block in `app.py`. The shell arrives with the other five screens, which is a larger change and a separate plan.

4. **`Statement` lives in `procurement/statement.py`, not `models.py`.** It is a view record, not stored state, and putting it beside the store models invites someone to persist it.

---

## File structure

| file | responsibility |
|---|---|
| `procurement/store/models.py` (modify) | `VendorFacts` gains `technical_feedback` and `quotation_doc_id` |
| `procurement/pipeline.py` (modify) | set and carry forward both fields; prune `commercial` with its quotation |
| `procurement/statement.py` (create) | `StatementCell` / `StatementRow` / `Statement`; `build_statement`; all statement arithmetic |
| `procurement/export.py` (modify) | `statement_to_rows` / `_csv_str` / `_xlsx_bytes` beside the existing comparison exporters |
| `portal/views/__init__.py` (create) | package marker |
| `portal/views/statement.py` (create) | render the two tables; the technical-feedback editor |
| `portal/app.py` (modify) | call the view in place of the §5 Results block |
| `tests/test_statement_columns.py` (create) | Task 3 |
| `tests/test_statement_pricing.py` (create) | Task 4 |
| `tests/test_statement_feedback.py` (create) | Tasks 1, 5 |
| `tests/test_statement_export.py` (create) | Task 6 |
| `tests/test_statement_view.py` (create) | Task 7 |
| `tests/test_statement_lifecycle.py` (create) | Task 8 — the mutation matrix |
| `tests/test_pipeline_lifecycle.py` (modify) | Task 2 — commercial pruning sits with the other pruning tests |

> **Reference code below is intent, not paste-able.** It shows the shape and the reasoning, and it has not been executed. Read it, then write the implementation against the test.
>
> **Load-bearing** in every block: the "blank, never zero" rule; the `included_in_base` contributes-zero rule; the exclusion of VAT from the discount base; the `("ok", "failed")` liveness pair; and that the builder opens no transaction.
> **Illustrative**: exact normalisation regexes, exact row `key` strings, exact label wording, dataframe construction details.

---

## Task 1: Carry a reviewer's note and the quotation link on VendorFacts

**Files:**
- Modify: `procurement/store/models.py` (`VendorFacts`)
- Modify: `procurement/pipeline.py` (the quotation branch and the `save_facts` call, ~line 481 and ~line 540)
- Test: `tests/test_statement_feedback.py`

**Interfaces:**
- Consumes: `snapshots.load_facts`, `snapshots.save_facts`, `run_ingestion`
- Produces: `VendorFacts.technical_feedback: str | None`, `VendorFacts.quotation_doc_id: str | None`
- **Store invariant owned (INV-S3):** a vendor's `technical_feedback` survives every re-extraction of that vendor — a prompt-version bump, a changed source file, or a failed re-run never clears it.

[`pipeline.py`](../../../procurement/pipeline.py) constructs a **fresh** `VendorFacts` on every extraction rather than mutating the loaded one, so any new field is dropped unless it is explicitly carried from `base`. This is the identical shape to phase 3's `vocabulary_sha` defect: a guard stated in prose, mandated by no test, and invisible until a third run. Prove the carry-forward by sabotage, not by reading the diff.

`_prune_orphan_facts` mutates a loaded `VendorFacts` and re-saves it, so it needs no change for these fields. Only the fresh construction does.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_statement_feedback.py
import os

from procurement.pipeline import run_ingestion
from procurement.store import snapshots

from tests.test_pipeline_rfq import RfqClient
from tests.test_pipeline_vocabulary import _project

_QUOTE = "Quotation.txt"


def _note(root, vendor, text):
    facts = snapshots.load_facts(root, "p", vendor)
    facts.technical_feedback = text
    snapshots.save_facts(root, "p", facts)


def test_a_note_survives_a_forced_reextraction(tmp_path):
    """INV-S3. Touching the quotation forces run 2 to re-extract KERUI, which
    rebuilds VendorFacts from scratch — the note must be carried, not dropped."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    _note(root, "KERUI", "FULLY COMPLIED")

    (tmp_path / "p" / "vendors" / "KERUI" / _QUOTE).write_text(
        "base price 2000", encoding="utf-8")
    run_ingestion(root, "p", RfqClient())

    facts = snapshots.load_facts(root, "p", "KERUI")
    assert facts.technical_feedback == "FULLY COMPLIED"
    assert facts.commercial["base_price"] == 2000.0, "the re-extraction did not happen"


def test_the_facts_record_which_document_priced_them(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())

    facts = snapshots.load_facts(root, "p", "KERUI")
    quote = next(d for d in snapshots.load_documents(root, "p")
                 if d.path.endswith(_QUOTE))
    assert facts.quotation_doc_id == quote.doc_id


def test_a_failed_requotation_keeps_the_note_and_the_link(tmp_path):
    """A provider outage on run 2 must not blank either field."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    _note(root, "KERUI", "FULLY COMPLIED")
    before = snapshots.load_facts(root, "p", "KERUI").quotation_doc_id

    (tmp_path / "p" / "vendors" / "KERUI" / _QUOTE).write_text(
        "base price 2000", encoding="utf-8")
    run_ingestion(root, "p", RfqClient(fail_on=("bid",)))

    facts = snapshots.load_facts(root, "p", "KERUI")
    assert facts.technical_feedback == "FULLY COMPLIED"
    assert facts.quotation_doc_id == before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_statement_feedback.py -v`
Expected: FAIL — `AttributeError` / pydantic rejects `technical_feedback`, since the field does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/store/models.py — VendorFacts
class VendorFacts(BaseModel):
    vendor: str
    commercial: dict | None = None
    normalized: dict | None = None
    technical: list[dict] = []
    deviations: list[dict] = []
    overrides: list[Override] = []
    # The document whose extraction produced `commercial`. Stored rather than
    # re-derived: choosing it runs a classified-then-pick_quote fallback in
    # pipeline.py that would drift if a second copy existed.
    quotation_doc_id: str | None = None
    # A reviewer's judgement, not an extracted value and not an Override —
    # reconcile() flags any field_path absent from the extracted view, so a
    # synthetic override path would raise a false conflict on every run.
    technical_feedback: str | None = None
```

```python
# procurement/pipeline.py — in the quotation branch, beside `commercial_dump`
if route == "quotation":
    bid = extract_bid(doc.vendor, [full], client, pdf_fallback=pdf_fallback)
    status = bid.extraction_status
    doc.notes = bid.notes
    commercial_dump = bid.model_dump() if status == "ok" else base.commercial
    # only on success: a failed re-extraction must not repoint the link at a
    # document that produced nothing, which would then prune good prices
    quotation_doc_id = doc.doc_id if status == "ok" else base.quotation_doc_id
    technical = list(base.technical)
    deviations = list(base.deviations)
elif route == "datasheet":
    ...
    quotation_doc_id = base.quotation_doc_id
else:   # deviation
    ...
    quotation_doc_id = base.quotation_doc_id
```

```python
# procurement/pipeline.py — the save, ~line 540
snapshots.save_facts(root, slug, VendorFacts(
    vendor=doc.vendor,
    commercial=resolved["commercial"],
    normalized=normalized,
    technical=resolved["technical"],
    deviations=resolved["deviations"],
    overrides=overrides,
    quotation_doc_id=quotation_doc_id,
    # carried or a re-extraction silently discards the reviewer's judgement —
    # vocabulary_sha's defect, one field over
    technical_feedback=base.technical_feedback))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_statement_feedback.py -v`
Expected: PASS (3 tests)

- [ ] **Step 4b: Prove the carry-forward by sabotage**

Delete `technical_feedback=base.technical_feedback` from the `save_facts` call. Run `python -m pytest tests/test_statement_feedback.py -v`. Expected: `test_a_note_survives_a_forced_reextraction` and `test_a_failed_requotation_keeps_the_note_and_the_link` fail, and nothing else in the file does. Restore the line and re-run green. Record the result in the ledger.

- [ ] **Step 5: Commit**

```bash
git add procurement/store/models.py procurement/pipeline.py tests/test_statement_feedback.py
git commit -m "feat: carry a reviewer's note and the quotation link on VendorFacts"
```

---

## Task 2: Prune commercial terms with their quotation

**Files:**
- Modify: `procurement/pipeline.py` (`_prune_orphan_facts`)
- Test: `tests/test_pipeline_lifecycle.py`

**Interfaces:**
- Consumes: `VendorFacts.quotation_doc_id` (Task 1)
- Produces: no new signature — `_prune_orphan_facts(root, slug, run_id, vendors, documents)` is unchanged
- **Store invariant owned (INV-S6):** `VendorFacts.commercial` and `.normalized` are present only while the vendor has a live quotation document.

This is a pre-existing defect, not one this feature introduces. `_prune_orphan_facts` filters `technical` and `deviations` by live `doc_id` and never touches `commercial`, because in phase 1 one document per vendor overwrote `VendorFacts` wholesale and pruning was automatic. Multi-document accumulation removed that property for the technical half and the fix stopped there. Delete a vendor's quotation today and its prices are stored forever; the comparative statement is the first screen to display them as current.

"Live" here means the same `("ok", "failed")` pair the existing function uses. A transient provider outage must not delete good stored prices — that is I3, and it is why the pair exists.

- [ ] **Step 1: Write the failing test**

**Name the import, or you break the file you are appending to.**
`tests/test_pipeline_lifecycle.py` already defines a module-level
`_project(tmp_path, entries)` — a **two-argument** helper that every
pre-existing test in that file calls. A bare
`from tests.test_pipeline_vocabulary import _project` at the bottom of the
module rebinds that name and breaks all of them, and it breaks them at
*collection-adjacent* time rather than in the test you just wrote, so the
failure reads as unrelated. Import it aliased. This is the one place in the
plan where the obvious copy is wrong.

```python
# tests/test_pipeline_lifecycle.py — append (`import os`, `run_ingestion` and
# `snapshots` are already imported at the top of this file; do not re-import)
from tests.test_pipeline_rfq import RfqClient
from tests.test_pipeline_vocabulary import _project as _vendor_project


def test_deleting_the_quotation_removes_the_stored_prices(tmp_path):
    """INV-S6. A vendor whose quotation is gone must not keep quoting a price."""
    root = _vendor_project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    assert snapshots.load_facts(root, "p", "KERUI").commercial is not None

    os.remove(tmp_path / "p" / "vendors" / "KERUI" / "Quotation.txt")
    run_ingestion(root, "p", RfqClient())

    facts = snapshots.load_facts(root, "p", "KERUI")
    assert facts.commercial is None
    assert facts.normalized is None
    assert facts.quotation_doc_id is None
    # the datasheet is untouched: pruning is per-source, not per-vendor
    assert facts.technical


def test_a_failed_quotation_extraction_keeps_the_previous_prices(tmp_path):
    """I3, in the commercial half. The document is still there and still
    routed, so an outage must not be read as 'the quotation is gone'."""
    root = _vendor_project(tmp_path)
    run_ingestion(root, "p", RfqClient())

    (tmp_path / "p" / "vendors" / "KERUI" / "Quotation.txt").write_text(
        "base price 2000", encoding="utf-8")
    run_ingestion(root, "p", RfqClient(fail_on=("bid",)))

    facts = snapshots.load_facts(root, "p", "KERUI")
    assert facts.commercial["base_price"] == 1000.0
    assert facts.normalized is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline_lifecycle.py -k quotation -v`
Expected: `test_deleting_the_quotation_removes_the_stored_prices` FAILS on `assert facts.commercial is None` — the stale record is still there. The second test passes already; it is a regression guard for the fix.

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/pipeline.py — inside _prune_orphan_facts's per-vendor loop,
# after the technical/deviations filters and before the early `continue`
technical = [f for f in facts.technical if f.get("doc_id") in live]
deviations = [d for d in facts.deviations if d.get("doc_id") in live]
# commercial has no per-record doc_id, so it is pruned via the stored link.
# A vendor whose quotation was deleted or reclassified away kept its prices
# forever: the technical half's C1, one field over.
commercial_dead = (facts.quotation_doc_id is not None
                   and facts.quotation_doc_id not in live)
if (len(technical) == len(facts.technical)
        and len(deviations) == len(facts.deviations)
        and not commercial_dead):
    continue

detail = {...}                      # as today
if commercial_dead:
    detail["commercial_dropped"] = facts.quotation_doc_id
    facts.commercial = None
    facts.normalized = None
    facts.quotation_doc_id = None
facts.technical = technical
facts.deviations = deviations
snapshots.save_facts(root, slug, facts)
```

Note the guard `facts.quotation_doc_id is not None`. Facts written before this change have no link, and treating a missing link as "dead" would delete every pre-existing vendor's prices on the first run after upgrade.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pipeline_lifecycle.py -v`
Expected: PASS, including every pre-existing test in the file.

- [ ] **Step 5: Commit**

```bash
git add procurement/pipeline.py tests/test_pipeline_lifecycle.py
git commit -m "fix: prune a vendor's commercial terms when its quotation dies"
```

---

## Task 3: Statement columns, revisions, and the text rows

**Files:**
- Create: `procurement/statement.py`
- Test: `tests/test_statement_columns.py`

**Interfaces:**
- Consumes: `snapshots.load_documents`, `load_facts`, `load_compliance`, `project.load_project`
- Produces:
  - `StatementCell(qty, unit_price, total, text, note)` — all optional, all defaulting to `None`
  - `StatementRow(key: str, label: str, kind: str, cells: dict[str, StatementCell])`
  - `Statement(project, currency, generation, vendors, revisions, rows)`
  - `build_statement(root: str, slug: str) -> Statement`
- **Store invariant owned (INV-S2):** the statement has exactly one column per vendor in `project.vendors`, in that order — no column for a vendor the project no longer has, and a full column of blanks for a vendor with no facts.

Columns come from `project.vendors`, never from `snapshots.list_fact_vendors`. Phase 3's Task 6 proved the difference matters: sourcing the vendor axis from stored files makes a vendor with no extractable document vanish from the comparison instead of showing an honest empty column. A vendor that disappears reads as "not bidding"; a blank column reads as "we have nothing on them". Only the second is true.

This task builds the frame and the `text` rows. Task 4 adds the priced rows; Task 5 the feedback row. A row absent from `cells` renders blank — the model never carries a zero to mean "unknown".

- [ ] **Step 1: Write the failing test**

```python
# tests/test_statement_columns.py
from procurement.pipeline import run_ingestion
from procurement.project import load_project, save_project
from procurement.statement import build_statement
from procurement.store import snapshots

from tests.test_pipeline_rfq import RfqClient
from tests.test_pipeline_vocabulary import _project


def _row(statement, key):
    return next(r for r in statement.rows if r.key == key)


def test_columns_follow_the_project_vendor_order(tmp_path):
    root = _project(tmp_path)
    for vendor in ("AESL", "MKON"):
        vdir = tmp_path / "p" / "vendors" / vendor
        vdir.mkdir(parents=True)
        (vdir / "Quotation.txt").write_text("base price 1000", encoding="utf-8")
    project = load_project(root, "p")
    project.vendors = ["MKON", "KERUI", "AESL"]
    save_project(root, project)
    run_ingestion(root, "p", RfqClient())

    assert build_statement(root, "p").vendors == ["MKON", "KERUI", "AESL"]


def test_a_vendor_with_no_facts_gets_a_full_blank_column(tmp_path):
    """INV-S2. Absent, not zero, and present, not missing."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    project = load_project(root, "p")
    project.vendors = ["KERUI", "GHOST"]
    save_project(root, project)

    statement = build_statement(root, "p")
    assert statement.vendors == ["KERUI", "GHOST"]
    for row in statement.rows:
        cell = row.cells.get("GHOST")
        assert cell is None or (cell.total is None and cell.text is None)


def test_the_text_rows_come_from_the_commercial_block(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    statement = build_statement(root, "p")

    assert _row(statement, "engine_make").kind == "text"
    assert [r.key for r in statement.rows if r.kind == "text"] == [
        "engine_make", "delivery_time", "delivery_terms", "payment_terms",
        "quotation_file", "technical_feedback"]


def test_the_quotation_file_row_names_the_live_document(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    statement = build_statement(root, "p")

    assert _row(statement, "quotation_file").cells["KERUI"].text == "Quotation.txt"
    assert statement.revisions["KERUI"] is None      # no revision label parsed


def test_a_vendor_whose_quotation_failed_shows_the_status_not_zeros(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient(fail_on=("bid",)))
    statement = build_statement(root, "p")

    assert statement.statuses["KERUI"] == "failed"
    assert _row(statement, "engine_make").cells.get("KERUI") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_statement_columns.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'procurement.statement'`.

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/statement.py
"""Build the comparative statement — a read-only view over the store.

This module never writes. It opens no transaction and touches no snapshot
path; `build_statement` called twice leaves `generation` unchanged. See
docs/superpowers/specs/2026-07-31-comparative-statement-dashboard-design.md.
"""
from pydantic import BaseModel

from procurement.project import load_project
from procurement.store import snapshots


class StatementCell(BaseModel):
    qty: float | None = None
    unit_price: float | None = None
    total: float | None = None
    text: str | None = None
    note: str | None = None


class StatementRow(BaseModel):
    key: str
    label: str
    kind: str                       # priced | value | text
    cells: dict[str, StatementCell] = {}


class Statement(BaseModel):
    project: str
    currency: str                   # target currency; labels the Normalised row
    generation: int
    vendors: list[str] = []
    revisions: dict[str, str | None] = {}
    currencies: dict[str, str] = {}     # vendor -> its quoted currency
    statuses: dict[str, str] = {}       # vendor -> ok | failed | missing
    rows: list[StatementRow] = []


_TEXT_ROWS = [("engine_make", "Engine Make"),
              ("delivery_time", "Delivery Time"),
              ("delivery_terms", "Delivery Terms"),
              ("payment_terms", "Payment Terms")]


def build_statement(root: str, slug: str) -> Statement:
    project = load_project(root, slug)
    docs = {d.doc_id: d for d in snapshots.load_documents(root, slug)}
    facts = {v: snapshots.load_facts(root, slug, v) for v in project.vendors}

    statement = Statement(project=project.name, currency=project.target_currency,
                          generation=project.generation, vendors=list(project.vendors))

    for vendor in project.vendors:
        f = facts.get(vendor)
        quote = docs.get(f.quotation_doc_id) if f and f.quotation_doc_id else None
        statement.revisions[vendor] = quote.revision_label if quote else None
        statement.currencies[vendor] = (f.commercial or {}).get("currency", "") if f else ""
        statement.statuses[vendor] = ("missing" if f is None
                                      else "ok" if f.commercial else "failed")

    for key, label in _TEXT_ROWS:
        row = StatementRow(key=key, label=label, kind="text")
        for vendor in project.vendors:
            value = ((facts.get(vendor).commercial or {}).get(key)
                     if facts.get(vendor) else None)
            if value:                       # absent, blank, and 0 all mean "unknown"
                row.cells[vendor] = StatementCell(text=str(value))
        statement.rows.append(row)

    quote_row = StatementRow(key="quotation_file", label="Vendor Quotation File Name",
                             kind="text")
    for vendor in project.vendors:
        f = facts.get(vendor)
        quote = docs.get(f.quotation_doc_id) if f and f.quotation_doc_id else None
        if quote:
            quote_row.cells[vendor] = StatementCell(text=quote.path.rsplit("/", 1)[-1])
    statement.rows.append(quote_row)

    statement.rows.append(StatementRow(key="technical_feedback",
                                       label="Technical Feedback", kind="text"))
    return statement
```

`statuses` distinguishes `missing` (no facts at all) from `failed` (facts exist, prices do not). The view prints it in the column header; conflating them would tell a reviewer a vendor never bid when in fact the extraction broke.

Task 4 inserts the priced rows ahead of the text rows and Task 5 fills the feedback row; both keep this ordering contract.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_statement_columns.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add procurement/statement.py tests/test_statement_columns.py
git commit -m "feat: build the comparative statement's columns and text rows"
```

---

## Task 4: The priced rows and the statement's arithmetic

**Files:**
- Modify: `procurement/statement.py`
- Test: `tests/test_statement_pricing.py`

**Interfaces:**
- Consumes: `Statement`, `StatementRow`, `StatementCell`, `build_statement` (Task 3)
- Produces: `build_statement` additionally emits, in order, `base_scope`, `opt:<key>`…, `freight`, `vat`, `vat_included`, `discount_pct`, `discount_amount`, `final_value`, `normalised` — ahead of the text rows
- **Store invariant owned (INV-S1):** `build_statement` writes nothing — `generation` and every snapshot file are unchanged across two consecutive builds.

The three rules from spec §5, restated because getting them wrong produces a plausible wrong number rather than a crash:

**(a) Discount.** `discount_amount = discount_pct × (base + optionals + freight)`, **excluding VAT**. `final_value = base + optionals + freight + vat − discount_amount`. This reproduces the reference sheet's ADPOWER column exactly (10% × 1,194,196.92 = 119,419.692, not 10% of the VAT-inclusive 1,253,906.77).

**(b) VAT.** `vat_included is False and vat_rate > 0` → the VAT row is `base × vat_rate` and joins the sum. `vat_included is True` → the row shows the note `"included"` and contributes **zero**; the quoted base already contains it, and adding it again double-counts.

**(c) Currency.** No arithmetic crosses a column. Each column sums in its own quoted currency; `Statement.currencies` already carries it for the header.

Optional items group by normalised description. Two vendors wording the same scope differently get two rows — deliberate, because silently equating scopes that are not equal is how a comparison lies. An item with `included_in_base` shows its note and contributes **zero**, never its stated total, or the base would be counted twice.

Do not use `normalize_bid`'s discount adjustment for the Discount Amount row: it is computed on a different base (post-VAT-removal, post-freight) and would disagree with `final_value` by a few percent — two numbers on one screen that should reconcile and don't.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_statement_pricing.py
from procurement.statement import build_statement
from procurement.store import snapshots
from procurement.store.models import VendorFacts

from tests.test_pipeline_vocabulary import _project


def _cell(statement, key, vendor):
    row = next((r for r in statement.rows if r.key == key), None)
    return row.cells.get(vendor) if row else None


def _facts(root, vendor, **commercial):
    body = {"currency": "USD", "base_price": 0.0, "vat_included": False,
            "vat_rate": 0.0, "freight_amount": 0.0, "freight_included": False,
            "discount_pct": 0.0, "optional_items": []}
    body.update(commercial)
    snapshots.save_facts(root, "p", VendorFacts(
        vendor=vendor, commercial=body,
        normalized={"vendor": vendor, "normalized_currency": "USD",
                    "normalized_total": 999.0, "adjustments": [],
                    "extraction_status": "ok"}))


def test_final_value_is_the_column_sum_including_options(tmp_path):
    """The reference sheet's KERUI column: 1170000 + 83000 + 17000 + 30000."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1170000.0, optional_items=[
        {"description": "PEMS for Exhaust - Optional", "qty": 2,
         "unit_price": 41500.0, "total": 83000.0},
        {"description": "Tools - Optional", "qty": 1,
         "unit_price": 17000.0, "total": 17000.0},
        {"description": "Two Years Spare Parts - Optional", "qty": 2,
         "unit_price": 15000.0, "total": 30000.0}])

    statement = build_statement(root, "p")
    assert _cell(statement, "final_value", "KERUI").total == 1300000.0


def test_the_discount_excludes_vat_from_its_base(tmp_path):
    """Rule (a), on the reference sheet's ADPOWER numbers."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1194196.92, vat_rate=0.05, discount_pct=0.10)

    statement = build_statement(root, "p")
    assert _cell(statement, "vat", "KERUI").total == 59709.85
    assert _cell(statement, "discount_amount", "KERUI").total == 119419.69
    assert _cell(statement, "final_value", "KERUI").total == 1134487.07


def test_a_vat_inclusive_price_is_not_taxed_twice(tmp_path):
    """Rule (b). The row is annotated, contributes zero, and says why."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1000.0, vat_rate=0.05, vat_included=True)

    statement = build_statement(root, "p")
    assert _cell(statement, "vat", "KERUI").total is None
    assert _cell(statement, "vat", "KERUI").note == "included"
    assert _cell(statement, "final_value", "KERUI").total == 1000.0


def test_an_option_included_in_base_contributes_nothing(tmp_path):
    """MKON's 'This has been included in Base Price.' cell."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1000.0, optional_items=[
        {"description": "Two Years Spare Parts", "total": 30000.0,
         "included_in_base": True}])

    statement = build_statement(root, "p")
    cell = _cell(statement, "opt:two years spare parts", "KERUI")
    assert cell.total is None
    assert cell.note == "included in base price"
    assert _cell(statement, "final_value", "KERUI").total == 1000.0


def test_differently_worded_options_get_their_own_rows(tmp_path):
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1.0, optional_items=[
        {"description": "Tools - Optional", "total": 10.0}])
    project_vendors_extended(root, "MKON")
    _facts(root, "MKON", base_price=1.0, optional_items=[
        {"description": "Special tooling", "total": 20.0}])

    keys = [r.key for r in build_statement(root, "p").rows if r.key.startswith("opt:")]
    assert keys == ["opt:tools", "opt:special tooling"]


def test_a_vendor_with_no_prices_shows_blanks_not_zeros(tmp_path):
    """The single most important assertion in this file."""
    root = _project(tmp_path)
    snapshots.save_facts(root, "p", VendorFacts(vendor="KERUI"))

    statement = build_statement(root, "p")
    for key in ("base_scope", "freight", "vat", "final_value", "normalised"):
        cell = _cell(statement, key, "KERUI")
        assert cell is None or cell.total is None, key


def test_the_normalised_row_is_normalize_bids_figure_verbatim(tmp_path):
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1170000.0, optional_items=[
        {"description": "Tools", "total": 17000.0}])

    statement = build_statement(root, "p")
    assert _cell(statement, "normalised", "KERUI").total == 999.0
    assert _cell(statement, "final_value", "KERUI").total == 1187000.0


def test_building_twice_writes_nothing(tmp_path):
    """INV-S1."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1000.0)
    before = snapshots.get_generation(root, "p")

    build_statement(root, "p")
    build_statement(root, "p")

    assert snapshots.get_generation(root, "p") == before
```

`project_vendors_extended(root, name)` is a helper this test file defines: load the project, append `name` to `vendors`, save. Write it at the top of the file rather than importing it — `tests/test_phase3_lifecycle.py` has a `_set_vendors` of the same shape, and duplicating four lines beats coupling two suites.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_statement_pricing.py -v`
Expected: FAIL — `_cell(...)` returns `None` for every priced key, since no priced row is emitted yet.

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/statement.py
import re

_OPTIONAL_TAIL = re.compile(r"[\s\-–—]*\(?optional\)?\s*$", re.I)


def _opt_key(description: str) -> str:
    """Group by normalised description. Illustrative regex; the load-bearing
    part is that grouping is exact-after-normalisation and never fuzzy."""
    return _OPTIONAL_TAIL.sub("", " ".join(description.split())).strip().lower()


def _money(value) -> float | None:
    """None unless this is a real number. '' and None both mean unknown, and
    unknown must never become 0.0 — a zero in a price column is an award-
    changing lie, not a rendering detail."""
    return float(value) if isinstance(value, (int, float)) else None


def _priced_rows(project, facts) -> list[StatementRow]:
    rows: dict[str, StatementRow] = {}

    def cell(key, label, vendor, **kw):
        row = rows.setdefault(key, StatementRow(key=key, label=label, kind="priced"))
        row.cells[vendor] = StatementCell(**kw)

    order: list[str] = []           # optional-row keys, first-seen across columns
    sums: dict[str, dict] = {}

    for vendor in project.vendors:
        f = facts.get(vendor)
        c = (f.commercial if f else None) or {}
        base = _money(c.get("base_price"))
        if base is not None:
            cell("base_scope", "Base scope", vendor, total=base)

        options = 0.0
        for item in c.get("optional_items", []):
            key = f"opt:{_opt_key(item.get('description', ''))}"
            if key not in order:
                order.append(key)
            if item.get("included_in_base"):
                cell(key, item.get("description", ""), vendor,
                     note="included in base price")
                continue
            total = _money(item.get("total"))
            cell(key, item.get("description", ""), vendor,
                 qty=_money(item.get("qty")), unit_price=_money(item.get("unit_price")),
                 total=total)
            options += total or 0.0

        freight = None
        if not c.get("freight_included"):
            freight = _money(c.get("freight_amount")) or None
            if freight:
                cell("freight", "Freight Charges", vendor, total=freight)

        rate = c.get("vat_rate") or 0.0
        vat = None
        if c.get("vat_included"):
            cell("vat", "VAT", vendor, note="included")
        elif rate and base is not None:
            vat = round(base * rate, 2)
            cell("vat", "VAT", vendor, total=vat)

        sums[vendor] = {"base": base, "options": options,
                        "freight": freight or 0.0, "vat": vat or 0.0,
                        "pct": c.get("discount_pct") or 0.0,
                        "has_prices": base is not None}

    for vendor in project.vendors:
        s = sums.get(vendor, {})
        if not s.get("has_prices"):
            continue                    # blank column, not a zeroed one
        pre_vat = s["base"] + s["options"] + s["freight"]
        discount = round(pre_vat * s["pct"], 2)        # rule (a): VAT excluded
        if s["pct"]:
            cell("discount_amount", "Discount Amount", vendor, total=discount)
        cell("final_value", "FINAL VALUE", vendor,
             total=round(pre_vat + s["vat"] - discount, 2))

    ordered = ["base_scope"] + order + ["freight", "vat", "discount_amount",
                                        "final_value"]
    return [rows[k] for k in ordered if k in rows]
```

The value rows (`vat_included`, `discount_pct`) and the `normalised` row follow the same shape: `vat_included` is `kind="value"` with `text` of `"YES"`/`"NO"`; `discount_pct` is `kind="value"` formatted as a percentage; `normalised` is `kind="priced"` with `total=(f.normalized or {}).get("normalized_total")` and the label `f"Normalised (ex-VAT, ex-options, {project.target_currency})"`.

Insert `_priced_rows(...)` output ahead of the text rows in `build_statement`, then the value rows, keeping the order declared in the Interfaces block.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_statement_pricing.py -v`
Expected: PASS (8 tests)

- [ ] **Step 4b: Prove the two rules by sabotage**

Change the discount base to `pre_vat + s["vat"]` — expected: only `test_the_discount_excludes_vat_from_its_base` fails. Change the `vat_included` branch to add VAT anyway — expected: only `test_a_vat_inclusive_price_is_not_taxed_twice` fails. Change `_money` to `float(value or 0)` — expected: `test_a_vendor_with_no_prices_shows_blanks_not_zeros` fails. Revert each and re-run green. Record all three in the ledger.

- [ ] **Step 5: Commit**

```bash
git add procurement/statement.py tests/test_statement_pricing.py
git commit -m "feat: the comparative statement's priced rows and column arithmetic"
```

---

## Task 5: The technical feedback cell

**Files:**
- Modify: `procurement/statement.py`
- Test: `tests/test_statement_feedback.py` (append)

**Interfaces:**
- Consumes: `snapshots.load_compliance`, `VendorFacts.technical_feedback` (Task 1)
- Produces: `compliance_tally(results, vendor) -> dict[str, int]`; the `technical_feedback` row's cells carry `text` (the note, when set) and `note` (the tally, always)
- **Store invariant owned (INV-S4):** no compliance verdict count is ever stored — `facts.json` contains no tally, and the statement's tally is computed at read time from `compliance.json`.

The tally is derived data. Phase 3 recomputes `compliance.json` wholesale every run precisely so the matrix cannot drift from its sources; copying counts into `facts.json` would reintroduce exactly that drift, one level down. Compute at read time, always.

Both values are always shown. A note is a reviewer's judgement made at one moment; the tally moves under it as extractions change. Showing them together lets a stale note be seen as stale — the visibility `conflict` provides for real overrides, without inventing a conflict the store would then have to carry.

The tally omits verdicts with a zero count, so a clean vendor reads `pass 25` rather than a row of zeros.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_statement_feedback.py — append
from procurement.compliance import VERDICTS
from procurement.statement import build_statement, compliance_tally
from procurement.store.models import ComplianceResult


def _feedback(root, vendor):
    statement = build_statement(root, "p")
    row = next(r for r in statement.rows if r.key == "technical_feedback")
    return row.cells.get(vendor)


def test_the_tally_counts_only_verdicts_that_occur(tmp_path):
    results = [ComplianceResult(req_id="a", vendor="KERUI", verdict="fail",
                                evaluated_at="t"),
               ComplianceResult(req_id="b", vendor="KERUI", verdict="review",
                                evaluated_at="t"),
               ComplianceResult(req_id="c", vendor="MKON", verdict="pass",
                                evaluated_at="t")]
    assert compliance_tally(results, "KERUI") == {"fail": 1, "review": 1}


def test_the_cell_shows_the_tally_when_no_note_is_written(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())

    cell = _feedback(root, "KERUI")
    assert cell.text is None
    assert cell.note and any(v in cell.note for v in VERDICTS)


def test_a_note_is_shown_beside_the_tally_not_instead_of_it(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    _note(root, "KERUI", "FULLY COMPLIED")

    cell = _feedback(root, "KERUI")
    assert cell.text == "FULLY COMPLIED"
    assert cell.note and any(v in cell.note for v in VERDICTS)


def test_no_tally_is_ever_stored(tmp_path):
    """INV-S4. facts.json holds the note and nothing derived from verdicts."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    build_statement(root, "p")

    stored = snapshots.load_facts(root, "p", "KERUI").model_dump()
    assert not any(v in str(stored) for v in ("pass", "unanswered", "review"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_statement_feedback.py -v`
Expected: FAIL — `ImportError: cannot import name 'compliance_tally'`.

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/statement.py
from procurement.compliance import VERDICTS


def compliance_tally(results, vendor: str) -> dict[str, int]:
    counts = {v: 0 for v in VERDICTS}
    for r in results:
        if r.vendor == vendor and r.verdict in counts:
            counts[r.verdict] += 1
    return {v: n for v, n in counts.items() if n}


def _tally_text(counts: dict[str, int]) -> str | None:
    return " · ".join(f"{n} {v}" for v, n in counts.items()) or None
```

In `build_statement`, fill the `technical_feedback` row after loading `results = snapshots.load_compliance(root, slug)`:

```python
for vendor in project.vendors:
    f = facts.get(vendor)
    note = _tally_text(compliance_tally(results, vendor))
    text = f.technical_feedback if f else None
    if note or text:
        feedback_row.cells[vendor] = StatementCell(text=text, note=note)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_statement_feedback.py -v`
Expected: PASS (7 tests — 3 from Task 1, 4 here)

- [ ] **Step 5: Commit**

```bash
git add procurement/statement.py tests/test_statement_feedback.py
git commit -m "feat: show the compliance tally beside the reviewer's note"
```

---

## Task 6: Export the statement

**Files:**
- Modify: `procurement/export.py`
- Test: `tests/test_statement_export.py`

**Interfaces:**
- Consumes: `Statement` (Task 3), populated by Tasks 4 and 5
- Produces: `statement_to_rows(s) -> list[dict]`, `statement_to_csv_str(s) -> str`, `statement_to_xlsx_bytes(s) -> bytes`
- **Store invariant owned:** none. These are pure functions over an in-memory `Statement` with no store access. Stated rather than omitted, per PLAN-TEMPLATE Rule 1's requirement that every task's ownership be explicit. Precedent: phase 3's Task 5 (`units.py`) likewise owned none.

One flat grid, `Description` plus one column per vendor, so the CSV opens as the sheet a reviewer recognises. Priced cells render their `total`; a cell carrying only a `note` renders the note; a blank cell renders `""` — **never `0`**. The existing `comparison_*` exporters stay untouched; `ComparisonTable` still has readers.

The column header carries the vendor's quoted currency and revision, matching the on-screen table, so an exported file cannot be misread as single-currency when it is not.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_statement_export.py
import io

import openpyxl

from procurement.export import (statement_to_csv_str, statement_to_rows,
                                statement_to_xlsx_bytes)
from procurement.statement import Statement, StatementCell, StatementRow


def _statement():
    return Statement(
        project="P", currency="USD", generation=3,
        vendors=["KERUI", "MKON"],
        revisions={"KERUI": "Rev.2", "MKON": None},
        currencies={"KERUI": "USD", "MKON": "EUR"},
        statuses={"KERUI": "ok", "MKON": "failed"},
        rows=[
            StatementRow(key="base_scope", label="Base scope", kind="priced",
                         cells={"KERUI": StatementCell(total=1170000.0)}),
            StatementRow(key="opt:tools", label="Tools - Optional", kind="priced",
                         cells={"KERUI": StatementCell(total=None,
                                                       note="included in base price")}),
            StatementRow(key="engine_make", label="Engine Make", kind="text",
                         cells={"KERUI": StatementCell(text="Waukesha-Canada")}),
        ])


def test_a_missing_price_exports_blank_never_zero(tmp_path):
    rows = statement_to_rows(_statement())
    assert rows[0]["MKON"] == ""


def test_a_note_only_cell_exports_its_note(tmp_path):
    rows = statement_to_rows(_statement())
    assert rows[1]["KERUI"] == "included in base price"


def test_headers_carry_the_currency_and_revision(tmp_path):
    header = statement_to_csv_str(_statement()).splitlines()[0]
    assert "KERUI (USD, Rev.2)" in header
    assert "MKON (EUR)" in header


def test_the_workbook_has_one_row_per_statement_row(tmp_path):
    wb = openpyxl.load_workbook(io.BytesIO(statement_to_xlsx_bytes(_statement())))
    ws = wb.active
    assert ws.max_row == 4                      # header + 3 rows
    assert ws.cell(row=2, column=1).value == "Base scope"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_statement_export.py -v`
Expected: FAIL — `ImportError: cannot import name 'statement_to_rows'`.

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/export.py — append; the comparison_* functions are unchanged
def _statement_header(statement, vendor: str) -> str:
    parts = [p for p in (statement.currencies.get(vendor),
                         statement.revisions.get(vendor)) if p]
    return f"{vendor} ({', '.join(parts)})" if parts else vendor


def _statement_value(cell) -> str | float:
    if cell is None:
        return ""
    if cell.total is not None:
        return cell.total
    # a note or a text value, else blank — never 0.0
    return cell.text or cell.note or ""


def statement_to_rows(statement) -> list[dict]:
    return [{"Description": row.label,
             **{_statement_header(statement, v): _statement_value(row.cells.get(v))
                for v in statement.vendors}}
            for row in statement.rows]


def statement_to_csv_str(statement) -> str:
    rows = statement_to_rows(statement)
    fields = ["Description"] + [_statement_header(statement, v)
                                for v in statement.vendors]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def statement_to_xlsx_bytes(statement) -> bytes:
    fields = ["Description"] + [_statement_header(statement, v)
                                for v in statement.vendors]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Comparative Statement"
    ws.append(fields)
    for row in statement_to_rows(statement):
        ws.append([row[f] for f in fields])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_statement_export.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add procurement/export.py tests/test_statement_export.py
git commit -m "feat: export the comparative statement to CSV and xlsx"
```

---

## Task 7: The portal view and the feedback editor

**Files:**
- Create: `portal/views/__init__.py`, `portal/views/statement.py`
- Modify: `portal/app.py` (replace the §5 Results block, lines 143–166)
- Test: `tests/test_statement_view.py`

**Interfaces:**
- Consumes: `build_statement`, `statement_to_csv_str`, `statement_to_xlsx_bytes`
- Produces: `render(root: str, slug: str) -> None`; `save_feedback(root, slug, vendor, text, reason) -> None`
- **Store invariant owned (INV-S5):** saving a note bumps `generation` exactly once and appends exactly one event.

Two tables, because Streamlit's grid cannot merge a cell across the three Qty/Unit/Total sub-columns and KERUI's five-line payment terms are unreadable in a one-third-width column. `kind == "priced"` goes to the priced table with MultiIndex columns; `value` and `text` go to the attributes table.

`save_feedback` is the only write on this screen. It goes through `snapshots.transaction` so the bump happens once, and the reason lands in the event rather than on the field — `VendorFacts` has no per-field metadata slot, and the History screen is where a rationale is read.

Keep `app.py`'s sections 1–4 exactly as they are. This task swaps section 5 only.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_statement_view.py
import os

import pytest
from streamlit.testing.v1 import AppTest

from procurement.pipeline import run_ingestion
from procurement.store import events, snapshots
from portal.views.statement import save_feedback

from tests.test_pipeline_rfq import RfqClient
from tests.test_pipeline_vocabulary import _project

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "portal", "app.py")


def test_saving_a_note_bumps_the_generation_once(tmp_path):
    """INV-S5."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    before = snapshots.get_generation(root, "p")

    save_feedback(root, "p", "KERUI", "FULLY COMPLIED", "checked against MR 4.2.7")

    assert snapshots.get_generation(root, "p") == before + 1
    assert snapshots.load_facts(root, "p", "KERUI").technical_feedback == "FULLY COMPLIED"


def test_saving_a_note_records_one_event_carrying_the_reason(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())

    save_feedback(root, "p", "KERUI", "FULLY COMPLIED", "checked against MR 4.2.7")

    edits = [e for e in events.read_events(root, "p")
             if e.action == "facts.feedback_edited"]
    assert len(edits) == 1
    assert edits[0].target == "KERUI"
    assert edits[0].detail["reason"] == "checked against MR 4.2.7"


def test_a_note_without_a_reason_is_refused(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    before = snapshots.get_generation(root, "p")

    with pytest.raises(ValueError):
        save_feedback(root, "p", "KERUI", "FULLY COMPLIED", "   ")

    assert snapshots.get_generation(root, "p") == before
    assert snapshots.load_facts(root, "p", "KERUI").technical_feedback is None


def test_the_app_renders_the_statement_after_a_run(tmp_path, monkeypatch):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", root)

    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.selectbox[0].set_value("p")
    at.run()

    assert not at.exception
    assert len(at.dataframe) == 2               # priced table + attributes table
    assert any("Comparative Statement" in str(m.value) for m in at.markdown)


def test_the_app_survives_a_project_with_no_facts(tmp_path, monkeypatch):
    """An empty project must say so, not raise."""
    root = _project(tmp_path)
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", root)

    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.selectbox[0].set_value("p")
    at.run()

    assert not at.exception
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_statement_view.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'portal.views'`.

- [ ] **Step 3: Write minimal implementation**

```python
# portal/views/statement.py
import pandas as pd
import streamlit as st

from procurement.export import statement_to_csv_str, statement_to_xlsx_bytes
from procurement.statement import build_statement
from procurement.store import events, snapshots
from procurement.store.models import Event

_SUBS = ["Qty", "Unit Price", "Total"]


def save_feedback(root: str, slug: str, vendor: str, text: str, reason: str) -> None:
    """The screen's only write. Refuses without a reason: an audit trail that
    records what changed but not why is the half that does not help later."""
    if not (reason or "").strip():
        raise ValueError("a reason is required")
    facts = snapshots.load_facts(root, slug, vendor)
    if facts is None:
        raise ValueError(f"no stored facts for {vendor}")
    with snapshots.transaction(root, slug):
        facts.technical_feedback = text.strip() or None
        snapshots.save_facts(root, slug, facts)
        events.append_event(root, slug, Event(
            at=_now(), run_id="portal", actor="portal",
            action="facts.feedback_edited", target=vendor,
            detail={"reason": reason.strip()}))


def _header(statement, vendor):
    parts = [p for p in (statement.currencies.get(vendor),
                         statement.revisions.get(vendor)) if p]
    status = statement.statuses.get(vendor)
    if status in ("failed", "missing"):
        parts.append(status)
    return f"{vendor} ({', '.join(parts)})" if parts else vendor


def _priced_frame(statement):
    columns = pd.MultiIndex.from_product(
        [[_header(statement, v) for v in statement.vendors], _SUBS])
    data = []
    for row in (r for r in statement.rows if r.kind == "priced"):
        line = []
        for vendor in statement.vendors:
            cell = row.cells.get(vendor)
            if cell is None:
                line += ["", "", ""]
            else:
                line += [cell.qty if cell.qty is not None else "",
                         cell.unit_price if cell.unit_price is not None else "",
                         cell.total if cell.total is not None else (cell.note or "")]
        data.append(line)
    index = [r.label for r in statement.rows if r.kind == "priced"]
    return pd.DataFrame(data, index=index, columns=columns)


def render(root: str, slug: str) -> None:
    statement = build_statement(root, slug)
    st.markdown("### Comparative Statement")
    if not statement.vendors:
        st.info("No vendors yet. Upload a vendor ZIP and run ingestion.")
        return

    st.dataframe(_priced_frame(statement), use_container_width=True)
    st.dataframe(_attributes_frame(statement), use_container_width=True)

    c1, c2 = st.columns(2)
    c1.download_button("Download Excel", statement_to_xlsx_bytes(statement),
                       file_name=f"{slug}-comparative-statement.xlsx",
                       mime="application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet")
    c2.download_button("Download CSV", statement_to_csv_str(statement),
                       file_name=f"{slug}-comparative-statement.csv",
                       mime="text/csv")

    st.markdown("#### Technical feedback")
    for vendor in statement.vendors:
        with st.expander(vendor):
            ...     # text_area prefilled from the cell, reason input, Save button
                    # calling save_feedback and st.rerun()
```

`_attributes_frame` is the same shape as `_priced_frame` with a plain column index and one value per vendor (`cell.text or cell.note or ""`). `_now()` is the ISO-8601 UTC helper; import it from `procurement.pipeline` rather than writing a second one.

In `portal/app.py`, replace lines 143–166 with:

```python
from portal.views import statement as statement_view
...
statement_view.render(ROOT, project.slug)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_statement_view.py tests/test_portal_app.py -v`
Expected: PASS, except the documented environment-dependent `test_missing_api_key_does_not_block_creation`.

- [ ] **Step 5: Commit**

```bash
git add portal/views/ portal/app.py tests/test_statement_view.py
git commit -m "feat: render the comparative statement as the portal's results view"
```

---

## Task 8: Integration — the two-run mutation matrix

**Files:**
- Create: `tests/test_statement_lifecycle.py`
- Test: itself

**Interfaces:**
- Consumes: everything from Tasks 1–7
- Produces: no new code — this task adds tests only
- **Store invariant owned:** none new. This task **defends** INV-S1 through INV-S6; every row below names the one it defends.

Every row asserts over a `build_statement` result taken **after run 2** (or run 3 where the row says so). A row satisfiable by a single-run assertion is not testing what it claims — that is the phase-2 defect class this file exists to close.

All nine of PLAN-TEMPLATE Rule 2's required mutations are observable through the statement, so none is exempted. Rows 10–14 are this feature's own accumulating state.

- [ ] **Step 1: Write the failing tests**

| # | mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|---|
| 1 | a newer revision of the quotation arrives | INV-S2 | the `Rev.` value and `quotation_file` cell name the **new** document; the superseded filename appears in no cell |
| 2 | the quotation is deleted from the vendor folder | INV-S6 | `base_scope` and `final_value` are blank for that vendor — not the run-1 numbers, and not `0` |
| 3 | a second upload carries a sibling revision | INV-S2 | `Rev.` shows the newest label; the datasheet's facts are unaffected |
| 4 | each of `PROMPT_VERSION`, `TECH_PROMPT_VERSION`, `DEVIATION_PROMPT_VERSION` bumped, one at a time | INV-S3 | a note written after run 1 survives the forced re-extraction, for every constant |
| 5 | the quotation call fails on run 1 and succeeds on run 2 | INV-S2 | run 1's column is blank with status `failed`; run 2's carries the price. A transient outage is not a cached answer |
| 6 | the quotation call fails on both runs | INV-S6 | the column stays blank, the header says `failed`, `final_value` is `None` — never `0` |
| 7 | the vendor's only document cannot serve as a quotation | INV-S2 | the vendor still gets a full blank column, not an absent one |
| 8 | a re-extraction fails after a successful one | INV-S3 | run 1's prices still render in run 2's statement **and** the note survives |
| 9 | the model returns a bid omitting `optional_items` | INV-S2 | no `opt:` rows; `final_value == base_scope`, not `0` |
| 10 | a vendor is removed from `project.vendors` | INV-S2 | no column for it, in any row |
| 11 | a vendor is added with no documents | INV-S2 | a full column of blanks appears, in `project.vendors` order |
| 12 | a requirement changes so the tally shifts | INV-S4 | the tally in run 2 differs from run 1's, the note is unchanged, and `facts.json` holds no verdict counts |
| 13 | `build_statement` called twice with no run between | INV-S1 | `generation` unchanged, and `documents.json` byte-identical |
| 14 | a note saved through `save_feedback`, then a re-run | INV-S5 + INV-S3 | `generation` bumped exactly once by the save; the note is still there after the run |

Row 4 is the strongest test in the file. Bumping a prompt-version constant forces `pipeline.py` to rebuild `VendorFacts` from scratch for that document class, which is precisely when a missing carry-forward drops the note. Parametrise over the three constants with `monkeypatch.setattr`, the way phase 3's row 4 does.

Row 12 needs three runs, not two: change the requirement, let run 2 re-evaluate, then assert. Two runs would compare a tally against itself.

Write one test per row, named for what it asserts, each carrying a one-line comment naming its invariant.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_statement_lifecycle.py -v`
Expected: every test fails or errors before the implementation from Tasks 1–7 is present. If any row passes on an unbuilt feature, that row is asserting something too weak — rewrite it before continuing.

- [ ] **Step 3: Make them pass**

No new production code should be needed. If a row fails against Tasks 1–7 as built, that is a real defect: fix the module that owns the invariant the row names, not the test.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest`
Expected: the baseline (**486 passed, 3 skipped, 1 failed** — the environment-dependent portal test) **plus** every test added by Tasks 1–8. Any other failure is a real regression.

- [ ] **Step 4b: Verify the matrix is real, not decorative**

Reinstate each defect below one at a time via a throwaway script, run the matrix after each, and confirm **the intended row fails and the others behave as predicted**. Delete the script afterwards. Record every result — including over-coverage — in `.superpowers/sdd/<phase>/progress.md`.

| defect to reinstate | row expected to fail |
|---|---|
| drop `technical_feedback=base.technical_feedback` | 4, 8, 14 |
| drop `quotation_doc_id` from the save | 1, 2 |
| revert the `commercial_dead` prune | 2 |
| set `quotation_doc_id = doc.doc_id` unconditionally (ignoring `status`) | 6 |
| source columns from `list_fact_vendors` instead of `project.vendors` | 10, 11 |
| make `_money` return `float(value or 0)` | 6, 9 |
| make `build_statement` call `bump_generation` | 13 |
| store the tally on `VendorFacts` | 12 |

A row that still passes with its defect reinstated is not testing what it claims. Note the phase-3 precedent: two of that plan's defect→row assignments were empirically wrong, and finding that out was worth more than the rows themselves.

- [ ] **Step 5: Commit**

```bash
git add tests/test_statement_lifecycle.py
git commit -m "test: two-run mutation matrix for the comparative statement"
```

---

## Approval checklist

- [ ] Every task's `Interfaces` block has a **Store invariant owned** bullet (Tasks 6 and 8 state "none" with a reason).
- [ ] No invariant is claimed twice; every stored collection this plan touches is claimed.
- [ ] The integration task's matrix carries all nine required rows plus five feature-specific ones.
- [ ] Every matrix row names an invariant; every invariant has at least one row.
- [ ] The Rule 3 banner appears above the first reference block, and load-bearing versus illustrative is stated.

## Manual verification, after Task 8

- [ ] Run the portal against a real project and confirm the statement renders without an exception on a project with (a) no vendors, (b) one vendor with a failed extraction, (c) several vendors.
- [ ] Download the xlsx and open it. Confirm no cell shows `0` where the screen shows a blank.
- [ ] Compare the rendered statement against `data/procurement-data/Comparative Statement (CS) - Gas Generators [Rev.2].xlsx` and record which of the five columns reproduce and which diverge, with the reason. Spec §5 predicts one divergence (ADPOWER's VAT); anything else is a finding.

## Deliberately out of scope

Per spec §8, unchanged by this plan: base-scope Qty and Unit Price (BOM work, roadmap) · alternative offers per vendor (the KERUI two-brand case) · manual merging of differently-worded optional items · choosing which optionals count toward a total · changing `normalize_bid`'s basis · the other five phase-4 screens and the `app.py` shell split.

Carried from phase 3's live run, and **not** fixed here — the tally this screen displays inherits all three: `in` produces false FAILs on ranges · deviations match across documents · triplicate MR documents inflate the requirement count.
