# Extraction Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the compliance matrix reflect what the vendors actually wrote — by routing every readable vendor document to an extractor, making requirement extraction complete and reproducible, and adding a `stated` checkability tier so text-valued clauses are machine-checked instead of dumped on a human — and make the result legible, with a per-vendor extraction status showing which of each vendor's files were read and what each one contributed.

**Architecture:** Four changes, all inside the existing pass structure. (1) The pipeline reads each document's text **once**, records which reader produced it in the already-declared-but-never-populated `DocumentRecord.text_source`, and records a document it cannot read as `failed` rather than feeding an extractor an empty string. (2) A vendor-side routing table replaces the three-class `_EXTRACTABLE` gate, so a vendor's marked-up spec, BOM, attachments and drawings reach the technical extractor — `doc_class` is left untouched, exactly as `_rfq_route` and `inferred_quotes` already do. (3) Requirement extraction splits the MR into deterministic line-aligned chunks and merges all-or-nothing, so the requirement set stops being bounded by the output-token ceiling. (4) A third `checkability` tier, `stated`, carries a parameter and an expected text value; `compliance.evaluate` matches it against the vendor's fact by normalised token subset and never returns `fail` on a text mismatch.

**Tech Stack:** Python 3.12, pydantic v2, pytest, openpyxl, pypdf / `pdftotext`. No new dependencies.

---

## Why this plan exists — the evidence

Measured on the stores in `projects/`, not inferred. `gas-11` is the best run in the repository: three vendors, 33 documents.

| measurement | value |
|---|---|
| documents skipped without extraction | **21 of 33** |
| requirements that are machine-checkable | **14 of 100** |
| ADPOWER auto-requirements answered | **0 of 14** |
| verdicts | 255 `review`, 31 `unanswered`, 10 `pass`, 3 `deviation`, 1 `fail` |

Four distinct causes, confirmed against the stored snapshots:

1. **Technical facts come only from `datasheet`-classified documents.** ADPOWER's folder holds a quotation, a superseded quotation, a BOM, and `ADN-AEC-ME-SPC-026 MR Gas Genset copy.pdf` — 60,696 characters, which is ADPOWER's own marked-up copy of the client MR, i.e. their clause-by-clause compliance response. It is classified `spec` and skipped with "spec documents are not extracted". ADPOWER has contributed **zero** technical facts in every run in the repository (gas-1, 5, 6, 7, 8, 11, 12).

2. **The `other` and `drawing` buckets are a black hole.** KERUI's skipped documents include Attachment-1 International Codes and Standards (36,380 chars), Attachment-3 Synchronization (16,971), Equipment & Sub-Vendor List (10,847), Single Line Diagrams (20,197), Power Auxiliary List, Two Years Spares. 24 of the 100 requirements are in the `codes` / `documentation` / `testing` categories — precisely what those documents answer.

3. **Requirement extraction is bounded by the output ceiling and is not reproducible.** The MR workbook is 25,176 chars in (~6.3k tokens), but the JSON echoes every clause verbatim, so output exceeds input. The same file across runs yielded **0, 84, 97, 97, 100, 100** requirements. The `0` is `gas-12`, which hit the hard 8192 ceiling, failed, and left `requirements.json` and `compliance.json` empty. The source has 135 populated rows in `Table 1` plus a 48-row `Notes` sheet. The row set of the matrix changes between runs on identical input.

4. **86 of 100 clauses are `judgement`, and they are not numeric bounds.** Sampled: "Anchor Bolt Required", "Neutral Earthing: Solidly Earthed for LV System", "Generator Insulation Temperature: Class F", "Hazardous Area Classification: Safe Area", "Governing class A2", "Generator Duty Type: S1 according to IEC 60034-1". These are presence and stated-value requirements. The `auto` tier only compares through `units.compare`, though the stored `ip_rating == "IP 55"` proves string equality already works end to end.

**A fifth cause was investigated and is already fixed.** Both `gas-11` quotation failures — ADPOWER's `base_price` arriving as `{"value": 1110836}`, and MKON's payload arriving under `parameter_name` — were replayed against the current working tree and both now extract cleanly, via `unbox_scalar_fields` and `unwrap_envelope` in `shared/llm/anthropic_client.py`. **No task in this plan touches quotation extraction.** Task 6 adds a regression row so it stays fixed.

---

## Global Constraints

Every task's requirements implicitly include this section.

- **Run tests from the repository root:** `python -m pytest`. Tests are key-free — they use `shared/llm/mock_client.py`. No test may require `ANTHROPIC_API_KEY`.
- **Two correct green baselines**, per `CLAUDE.md`. A workstation with `.env` and `data/` present: **649 passed, 3 skipped, 1 failed** (the failure is `tests/test_portal_app.py::test_missing_api_key_does_not_block_creation`, which is not yours to fix). CI and any clean checkout: **647 passed, 6 skipped, 0 failed**. This plan adds tests; the counts rise. Record the new baseline in the ledger and update `CLAUDE.md` in Task 6. Anything other than "the previous baseline plus the tests this plan adds" is a real regression.
- **Every write goes through `procurement/store/snapshots.py`.** Never hand-roll a snapshot write. `generation` bumps once per write transaction, never once per file.
- **`field_path` addresses list members by id, never by index.** `overrides.py` rejects index selectors outright.
- **A stored collection contains exactly the records of its currently-live sources — no more.** Facts of superseded, reclassified, deleted or newly-unreadable documents must be pruned, not merely skipped on re-extraction.
- **Missing data is never coerced to a passing or zero value.** An unfound parameter is omitted, not emitted as `0` or `""`.
- **Arithmetic stays in Python.** Extractors capture numbers and units verbatim; the model reads, code decides. Never ask an extractor whether a vendor complies.
- **A failed extraction never blanks previously-good stored data, and always records why in `DocumentRecord.notes`.**
- Design specs live in `docs/superpowers/specs/`, plans in `docs/superpowers/plans/`, the per-phase ledger in `.superpowers/sdd/2026-08-02-extraction-coverage/progress.md`.
- The portal runs via the `procurement-portal` entry in `.claude/launch.json` — use the preview tooling, not a bare `streamlit run`.

### Cost note — read before Task 2

Task 2 routes roughly **three times as many vendor documents** to an extractor. On the `gas-11` corpus that is 21 additional LLM calls per full run. Two things hold it down and both are load-bearing: the thin-text guard from Task 1 removes the five documents with no text layer before any call is made, and the `tech_facts_v1` prompt is already vocabulary-focused so the output stays proportional to the requirement set rather than to the document. The existing content-hash cache means the increase is once per document, not once per run. Do not implement Task 2 before Task 1.

---

## Invariant register

Every invariant is owned by exactly one task and defended by at least one row of the Task 6 mutation matrix.

| # | store invariant | owner |
|---|---|---|
| **INV-A** | Every document in `documents.json` with a `vendor` and `extraction_status` in `("ok", "failed")` carries a non-null `text_source`; no document whose readable text is below `MIN_EXTRACTABLE_CHARS` has status `ok`. | Task 1 |
| **INV-B** | `VendorFacts.technical` contains exactly the facts of the vendor's currently-live, currently-routable documents — no more, no fewer. | Task 2 |
| **INV-C** | `requirements.json` contains exactly the requirements of the currently authoritative spec revision, and a chunk failure during re-extraction leaves the previously-stored set for that document intact. | Task 3 |
| **INV-D** | Every requirement stored with `checkability == "stated"` carries a non-empty `parameter`, a scalar-or-null `value`, and null `operator` and `unit`; every requirement stored `auto` carries all four of `parameter`, `operator`, `value`, `unit`. | Task 4 |
| **INV-E** | Every compliance verdict names the `req_id` it was computed from; a `stated` verdict of `pass` names the `fact_id` that satisfied it, and a `stated` requirement with no matching fact is `unanswered`, never `fail`. | Task 5 |

---

## File structure

| file | responsibility after this plan |
|---|---|
| `procurement/loaders.py` | **Modify.** Adds `read_text_with_source` returning `(text, reader)`; `read_text` and `read_pdf_text` become thin delegates so there is one reading path, not two that drift. |
| `procurement/pipeline.py` | **Modify.** Reads each document's text once and passes it down; owns `_VENDOR_ROUTE` and the unreadable-document guard. |
| `procurement/extract_tech.py`, `extract_deviation.py`, `extract_requirements.py`, `extract_mom.py`, `extract.py` | **Modify.** Each accepts an optional pre-read `text=`. `extract_requirements.py` additionally owns chunking and the `stated` tier's extraction-side validation. |
| `shared/llm/prompts/requirements_v4.txt` | **Create.** `requirements_v3` plus the `stated` tier. v3 stays on disk; prompts are versioned, never edited in place. |
| `procurement/compliance.py` | **Modify.** `vocabulary()` includes `stated` parameters; `evaluate()` gains the `stated` branch. |
| `procurement/store/models.py`, `procurement/matrix.py` | **Modify.** Comment and coverage-tally updates for the third tier. |
| `portal/views/compliance.py`, `web/src/constants.ts`, `web/src/pages/ComplianceMatrix.tsx` | **Modify.** Display the `stated` tier's bound rather than falling through to raw clause text. |
| `shared/llm/mock_client.py` | **Modify.** `MockLLMClient` accepts a list of responses so a chunked call sequence is testable. Backwards compatible. |
| `procurement/coverage.py` | **Create.** Per-vendor extraction status: which files were read, by which extractor, and what each contributed. A read-only derivation over snapshots, like `matrix.py` — no new stored collection. |
| `api/main.py` | **Modify.** `GET /api/projects/{slug}/extraction-status`, plus an `extraction` roll-up key on `/summary` so the Dashboard needs no second round trip. |
| `web/src/pages/ExtractionStatus.tsx` | **Create.** The detail screen: one panel per vendor, one row per file. |
| `web/src/types.ts`, `api.ts`, `App.tsx`, `pages/Dashboard.tsx`, `theme.css` | **Modify.** Types, fetcher, a fourth nav entry, and the per-vendor roll-up on each project card. |
| `tests/test_extraction_coverage.py` | **Create.** The Task 6 two-run mutation matrix. |
| `tests/test_real_corpus_coverage.py` | **Create.** The Task 7 coverage floor, skipped when `data/` is absent. |
| `tests/test_extraction_status.py`, `tests/test_api_extraction_status.py` | **Create.** Task 8's derivation and route. |

---

> **Reference code below is intent, not paste-able.** It shows the shape and the reasoning, and it has not been executed. Read it, then write the implementation against the test.
>
> **Load-bearing** throughout this plan: the failure conventions (`return [], "failed", notes` — never raise, never blank prior data), the id-stability rules in `store/models.py`, the all-or-nothing chunk merge in Task 3, and the "never `fail` on weak evidence" rule in Task 5. **Illustrative** and free to change: exact threshold constants, exact chunk budget, exact note wording, exact token-splitting regex.

---

### Task 1: Read each document once, and refuse to extract from text that is not there

**Files:**
- Modify: `procurement/loaders.py`
- Modify: `procurement/extract_tech.py:31`, `procurement/extract_deviation.py:32`, `procurement/extract_requirements.py:61`, `procurement/extract_mom.py:53`, `procurement/extract.py:10`
- Modify: `procurement/pipeline.py` — the vendor loop at `:504` and the RFQ loop at `:286`
- Test: `tests/test_procurement_loaders.py`, `tests/test_pipeline_routing.py`

**Interfaces:**
- Consumes: `read_pdf_text`, `read_xlsx_text`, `read_docx_text` (existing, unchanged behaviour)
- Produces: `read_text_with_source(path: str, llm_fallback=None) -> tuple[str, str]`; `MIN_EXTRACTABLE_CHARS: int`; every extractor gains a keyword-only-in-practice `text: str | None = None` parameter
- **Store invariant owned (INV-A):** every document in `documents.json` with a `vendor` and `extraction_status` in `("ok", "failed")` carries a non-null `text_source`; no document whose readable text is below `MIN_EXTRACTABLE_CHARS` has status `ok`.

Five KERUI PDFs in the live corpus yield between 1 and 42 characters — they are scanned or vector-only drawings. Today `read_pdf_text` returns `""` without raising, so a document routed to an extractor produces a successful, empty, unexplained extraction: MKON's `HSD 230.pdf` is classified `datasheet`, has 5,248 characters of text, reports `extraction_status: "ok"` and contributed **zero** facts. Task 2 triples the number of documents reaching an extractor, so this guard must land first or it multiplies silent empties.

Two decisions carry the reasoning:

**An unreadable document is `failed`, not `skipped`.** `_prune_orphan_facts` counts `("ok", "failed")` as live. Marking it `skipped` would delete every fact a previous good run stored for it the first time `pdftotext` is missing from the PATH — an environment difference silently destroying stored data. `failed` keeps the prior facts, records the reason in `notes`, and costs one text read and no LLM call.

**The pipeline reads the text, not the extractor.** Each extractor currently calls `read_text` itself. Adding a guard in the pipeline without passing the text down would read every PDF twice — and would call the paid LLM transcription fallback twice for a scanned document. The optional `text=` parameter keeps each extractor usable standalone while giving the pipeline one read.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_procurement_loaders.py
from procurement.loaders import read_text_with_source, read_text


def test_read_text_with_source_names_the_reader(tmp_path):
    p = tmp_path / "sheet.xlsx"
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active["A1"] = "Continuous rating 525 kW"
    wb.save(p)
    text, source = read_text_with_source(str(p))
    assert "525" in text and source == "xlsx"


def test_read_text_still_returns_only_text(tmp_path):
    p = tmp_path / "note.txt"
    p.write_text("plain body", encoding="utf-8")
    # read_text must delegate, so the two paths cannot drift
    assert read_text(str(p)) == read_text_with_source(str(p))[0] == "plain body"


def test_a_pdf_with_no_text_layer_reports_its_reader_not_a_crash(tmp_path):
    p = tmp_path / "drawing.pdf"
    p.write_bytes(b"%PDF-1.4\n%%EOF\n")     # structurally a PDF, no text
    text, source = read_text_with_source(str(p))
    assert text.strip() == "" and source in ("pdftotext", "pypdf")
```

```python
# tests/test_pipeline_routing.py — append
def test_a_document_with_no_readable_text_is_failed_with_a_reason(tmp_path):
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    z = tmp_path / "v.zip"
    z.write_bytes(_zip({
        "KERUI/Quotation of Gas Generator.txt": b"base price 1000",
        "KERUI/01 DataSheet Gas Generator.txt": b"x",     # 1 char: unreadable
    }))
    unpack_vendor_zip(root, "p", str(z))
    client = RoutingClient()
    run_ingestion(root, "p", client)

    doc = next(d for d in snapshots.load_documents(root, "p")
               if d.path.endswith("01 DataSheet Gas Generator.txt"))
    assert doc.extraction_status == "failed"
    assert "no readable text" in doc.notes
    assert doc.text_source is not None
    # the guard fires before the model is asked, so no facts prompt was sent
    assert not any("facts" in c.get("prompt", "") for c in client.calls)


def test_every_extracted_document_records_its_text_source(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    for doc in snapshots.load_documents(root, "p"):
        if doc.vendor and doc.extraction_status in ("ok", "failed"):
            assert doc.text_source, f"{doc.path} has no text_source"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_procurement_loaders.py tests/test_pipeline_routing.py -v
```

Expected: `ImportError: cannot import name 'read_text_with_source'`, and the two pipeline tests fail on `text_source is None`.

- [ ] **Step 3: Write the implementation**

`procurement/loaders.py` — the existing readers are unchanged; only the entry points move. Note that `read_pdf_text` and `read_text` become delegates: two independent copies of the reader-selection ladder would drift, and the ladder is what decides whether the paid LLM fallback runs.

```python
# Below this many characters of readable text, a document is not extracted.
# Chosen against the live corpus: the five KERUI drawings yield 1-42 chars,
# and "11 Attachment-4 Applicable Codes and Standards Pending.pdf" yields 168
# and is a real, if short, document. Illustrative, not load-bearing.
MIN_EXTRACTABLE_CHARS = 100


def read_pdf_text_with_source(path: str, llm_fallback=None,
                              min_chars: int = 200) -> tuple[str, str]:
    """Robust PDF text plus the reader that produced it."""
    text, source = _pdftotext(path), "pdftotext"
    if len(text.strip()) < min_chars:
        alt = _pypdf(path)
        if len(alt.strip()) > len(text.strip()):
            text, source = alt, "pypdf"
    if len(text.strip()) < min_chars and llm_fallback is not None:
        try:
            fb = llm_fallback(path)
            if fb and fb.strip():
                return fb, "llm"
        except Exception:
            pass
    return text, source


def read_pdf_text(path: str, llm_fallback=None, min_chars: int = 200) -> str:
    return read_pdf_text_with_source(path, llm_fallback, min_chars)[0]


def read_text_with_source(path: str, llm_fallback=None) -> tuple[str, str]:
    """Text plus the name of the reader that produced it.

    `text_source` has been declared on DocumentRecord since phase 2 and never
    populated. It is the only way to tell "this datasheet genuinely states
    nothing" from "we read this scan with the wrong reader".
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xlsx":
        return read_xlsx_text(path), "xlsx"
    if ext == ".pdf":
        return read_pdf_text_with_source(path, llm_fallback=llm_fallback)
    if ext == ".docx":
        return read_docx_text(path), "docx"
    if ext == ".doc":
        raise ValueError(
            f"legacy binary .doc is not supported: {path!r}. "
            "Convert it to .docx or .pdf and re-upload.")
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        return fh.read(), "text"


def read_text(path: str, llm_fallback=None) -> str:
    return read_text_with_source(path, llm_fallback=llm_fallback)[0]
```

Each of the five extractors takes the same two-line change. Shown for `extract_tech_facts`; apply the identical shape to `extract_deviations`, `extract_requirements`, `extract_amendments`, and `extract_bid`:

```python
def extract_tech_facts(doc_id: str, path: str, client, pdf_fallback=None,
                       parameters: list[str] | None = None,
                       text: str | None = None
                       ) -> tuple[list[FactRecord], str, str | None]:
    try:
        if text is None:                       # the pipeline reads once and
            text = read_text(path, llm_fallback=pdf_fallback)   # passes it down
        ...
```

`procurement/pipeline.py`, vendor loop — replacing the bare `full = os.path.join(pdir, doc.path)` at `:504`:

```python
            full = os.path.join(pdir, doc.path)
            try:
                text, text_source = read_text_with_source(
                    full, llm_fallback=pdf_fallback)
            except Exception as exc:
                text, text_source = "", "unreadable"
                doc.extraction_status = "failed"
                doc.notes = f"unreadable document: {exc}"
            doc.text_source = f"{text_source}:{len(text)}chars"

            if doc.extraction_status != "failed" and len(text.strip()) < MIN_EXTRACTABLE_CHARS:
                # `failed`, never `skipped`: _prune_orphan_facts treats skipped
                # as dead and would delete the facts a good earlier run stored,
                # the first time `pdftotext` is missing from the PATH.
                doc.extraction_status = "failed"
                doc.notes = (f"no readable text layer "
                             f"({len(text.strip())} chars via {text_source}); "
                             "scanned or drawing-only document")
            if doc.extraction_status == "failed":
                documents.append(doc)
                failed += 1
                events.append_event(root, slug, Event(
                    at=_now(), run_id=run_id, actor="pipeline",
                    action="document.unreadable", target=doc.doc_id,
                    detail={"text_source": doc.text_source}))
                continue
```

Then pass `text=text` into each of the three extractor branches (`extract_bid`, `extract_tech_facts`, `extract_deviations`). Apply the same read-guard-pass shape to the RFQ loop at `:286`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_procurement_loaders.py tests/test_pipeline_routing.py tests/test_extract_tech.py tests/test_extract_deviation.py tests/test_extract_requirements.py tests/test_extract_mom.py tests/test_procurement_extract.py -v
```

Then the full suite: `python -m pytest`.

- [ ] **Step 5: Commit**

```bash
git add procurement/loaders.py procurement/extract_tech.py procurement/extract_deviation.py procurement/extract_requirements.py procurement/extract_mom.py procurement/extract.py procurement/pipeline.py tests/test_procurement_loaders.py tests/test_pipeline_routing.py
git commit -m "feat(pipeline): read each document once and record its text source

An unreadable document is recorded failed with the reason, never extracted
from an empty string. failed rather than skipped so a missing pdftotext
cannot delete facts a good earlier run stored.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Route every readable vendor document to an extractor

**Files:**
- Modify: `procurement/pipeline.py:34-43` (routing table), `:440-466` (the skip gate)
- Test: `tests/test_pipeline_routing.py`

**Interfaces:**
- Consumes: `MIN_EXTRACTABLE_CHARS` and the `text=` parameters from Task 1
- Produces: `VENDOR_ROUTE: dict[str, str]` (public — Task 8 imports it); `_vendor_route(doc: DocumentRecord, inferred_quotes: set[str]) -> str | None`
- **Store invariant owned (INV-B):** `VendorFacts.technical` contains exactly the facts of the vendor's currently-live, currently-routable documents — no more, no fewer.

ADPOWER's marked-up copy of the client MR is their compliance response and is skipped as `spec`. KERUI's codes-and-standards list, synchronization attachment, equipment list and single-line diagrams are skipped as `other` or `drawing`. Together that is 21 of `gas-11`'s 33 documents. The fix is a routing table, not a classifier change: **`doc_class` must keep reporting what the classifier decided**, exactly as `_rfq_route` and the `inferred_quotes` fallback already establish. `documents.json` is the audit record of classification; routing is a separate decision layered on top.

Three things to get right:

**`_EXTRACTABLE` keeps its current membership.** It is `tuple(PROMPT_VERSION_BY_CLASS)` — the three *routes*, not the eight *classes*. The inferred-quotation fallback pool at `:392` filters on `d.doc_class not in _EXTRACTABLE`, and that must keep meaning "documents no extractor claims by class". Widening `_EXTRACTABLE` would empty the fallback pool and silently drop the vendors it exists to rescue.

**`mom` on the vendor side routes to the technical extractor, deliberately.** An RFQ-side MOM produces amendments to the requirements; a vendor-side MOM (ADPOWER ships one, 65,054 chars) records the technical positions agreed in the meeting. It has no amendment path today and is dropped entirely. Routing it as a datasheet captures its facts. The trade-off is that a superseding meeting outcome arrives as a fact rather than as an amendment — acceptable, because the alternative in force today is that it arrives as nothing.

**Newly-routable documents get a `vocabulary_sha`.** The `unchanged` cache key at `:472` applies the vocabulary fingerprint when `route == "datasheet"`. More documents now route there, so more of them carry it, and a requirement edit re-asks all of them exactly once. That is the intended behaviour, and Task 6's prompt-bump row defends it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pipeline_routing.py — append
def _wide_project(tmp_path):
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    z = tmp_path / "v.zip"
    z.write_bytes(_zip({
        "ADPOWER/ADP-13158-2024-935.txt": b"quotation, base price 1110836 AED",
        # ADPOWER's marked-up copy of the client MR: their compliance response
        "ADPOWER/ADN-AEC-ME-SPC-026 MR Gas Genset copy.txt":
            b"Continuous rating 525 kW offered. Insulation Class F.",
        "ADPOWER/BOM.txt": b"Bill of material: engine MAN, alternator Stamford",
        "ADPOWER/09 Attachment-1 International Codes and Standards.txt":
            b"IEC 60034-1 complied. ISO 8528 complied.",
    }))
    unpack_vendor_zip(root, "p", str(z))
    return root


def test_a_vendors_marked_up_spec_contributes_technical_facts(tmp_path):
    root = _wide_project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    facts = snapshots.load_facts(root, "p", "ADPOWER")
    doc_ids = {f["doc_id"] for f in facts.technical}
    spec = next(d for d in snapshots.load_documents(root, "p")
                if d.path.endswith("MR Gas Genset copy.txt"))
    assert spec.doc_class == "spec"          # classification is NOT rewritten
    assert spec.extraction_status == "ok"
    assert spec.doc_id in doc_ids


def test_bom_and_other_documents_contribute_technical_facts(tmp_path):
    root = _wide_project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    docs = {d.path.rsplit("/", 1)[-1]: d for d in snapshots.load_documents(root, "p")}
    assert docs["BOM.txt"].extraction_status == "ok"
    assert docs["09 Attachment-1 International Codes and Standards.txt"].extraction_status == "ok"
    facts = snapshots.load_facts(root, "p", "ADPOWER")
    assert {docs["BOM.txt"].doc_id,
            docs["09 Attachment-1 International Codes and Standards.txt"].doc_id
            } <= {f["doc_id"] for f in facts.technical}


def test_the_quotation_is_still_the_only_source_of_commercial_terms(tmp_path):
    root = _wide_project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    facts = snapshots.load_facts(root, "p", "ADPOWER")
    quote = next(d for d in snapshots.load_documents(root, "p")
                 if d.path.endswith("ADP-13158-2024-935.txt"))
    assert facts.quotation_doc_id == quote.doc_id
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_pipeline_routing.py -k "marked_up or bom_and_other" -v
```

Expected: FAIL — `spec.extraction_status == "skipped"`, and the spec/BOM doc_ids are absent from `facts.technical`.

- [ ] **Step 3: Write the implementation**

`procurement/pipeline.py`, beside the existing tables at `:29-43`:

```python
# Vendor-side routing. A vendor's copy of the client spec is their marked-up
# compliance response; the BOM, attachments and drawings state parameters too.
# All of them feed the technical extractor. doc_class is left untouched —
# documents.json must keep reporting what the classifier decided, not what
# routing did with it (the rule _rfq_route and inferred_quotes already follow).
#
# Values are ROUTES (keys of PROMPT_VERSION_BY_CLASS), not doc classes.
# _EXTRACTABLE deliberately keeps its current three-route membership: the
# inferred-quotation fallback pool filters on `doc_class not in _EXTRACTABLE`
# and must keep meaning "no extractor claims this document by its class".
# Public, unlike the tables above it: Task 8's coverage.py reports the route
# beside the class, and re-deriving the mapping there would be a second place
# for it to drift.
VENDOR_ROUTE = {
    "quotation": "quotation",
    "datasheet": "datasheet",
    "deviation": "deviation",
    "spec": "datasheet",
    "bom": "datasheet",
    "other": "datasheet",
    "drawing": "datasheet",
    "mom": "datasheet",
}


def _vendor_route(doc: DocumentRecord, inferred_quotes: set[str]) -> str | None:
    """The extractor a vendor document feeds, or None when nothing reads it.
    None is now reachable only for `unclassified`."""
    if doc.doc_id in inferred_quotes:
        return "quotation"
    return VENDOR_ROUTE.get(doc.doc_class)
```

In the vendor loop, replace `:446`:

```python
            route = _vendor_route(doc, inferred_quotes)
```

and `:452`:

```python
            elif route is None:
                skip_reason = f"{doc.doc_class} documents are not extracted"
```

Leave `_EXTRACTABLE`, `PROMPT_VERSION_BY_CLASS`, the supersession check, the not-the-selected-quotation check, the cache block and `_prune_orphan_facts` untouched. `_prune_orphan_facts` already keys on `extraction_status in ("ok", "failed")`, so a class that stops routing in a future edit prunes its facts without further work — which is what INV-B asserts and what the Task 6 matrix tests.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_pipeline_routing.py tests/test_pipeline_incremental.py tests/test_pipeline_lifecycle.py tests/test_pipeline_vocabulary.py -v
```

Then the full suite: `python -m pytest`.

- [ ] **Step 5: Commit**

```bash
git add procurement/pipeline.py tests/test_pipeline_routing.py
git commit -m "feat(pipeline): route every readable vendor document to an extractor

A vendor's marked-up copy of the client spec is their compliance response;
BOMs, attachments, drawings and vendor MOMs state parameters too. All feed
the technical extractor. doc_class is left untouched.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Extract requirements in deterministic chunks, merged all-or-nothing

**Files:**
- Modify: `procurement/extract_requirements.py`
- Modify: `shared/llm/mock_client.py`
- Test: `tests/test_extract_requirements.py`

**Interfaces:**
- Consumes: `read_text` / the `text=` parameter from Task 1
- Produces: `REQUIREMENTS_CHUNK_CHARS: int`; `_chunks(text: str, budget: int) -> list[str]`; `extract_requirements` signature unchanged apart from Task 1's `text=`
- **Store invariant owned (INV-C):** `requirements.json` contains exactly the requirements of the currently authoritative spec revision, and a chunk failure during re-extraction leaves the previously-stored set for that document intact.

The same 25,176-character MR produced 0, 84, 97, 97, 100 and 100 requirements across six runs. The `0` hit the 8192-token output ceiling and failed outright, emptying `requirements.json` and with it the whole matrix. The others succeeded while silently dropping clauses to fit. Raising `LLM_MAX_TOKENS` moves the ceiling; it does not make the extraction complete or reproducible.

Four design points, all load-bearing:

**Chunk on line boundaries, never mid-line.** `read_xlsx_text` emits one line per spreadsheet row (`Table 1 | A7=4.2.7 | B7=H2S content | C7=700 | D7=ppm`). Splitting inside a line tears a value from its unit and invites the model to pair the wrong number with the wrong unit — the exact failure `_docx_lines` already guards against in the loader.

**No overlap between chunks.** Overlap duplicates clauses. The dedup below is a safety net for repeated header rows, not a licence to overlap.

**`position` must be global across the merged list, not per chunk.** `req_id_for` falls back to `f"#{position}"` when a clause prints no reference. A per-chunk counter would give two different clauses the same id, and would shift every unreferenced clause's id whenever the chunk budget changed — orphaning every override and every stored verdict. Accumulating `raw_items` across all chunks and enumerating once at the end gives this for free, but it is the reason the loop is shaped that way.

**Any chunk failing fails the whole extraction.** A partial merge is worse than no extraction: the pipeline replaces `[r for r in requirements if r.source_doc_id != doc.doc_id] + fresh` on `status == "ok"`, so storing 3 chunks out of 5 as a success *deletes* the requirements the missing chunks would have carried. The matrix silently shrinks and nothing records why. Keeping the whole loop inside the existing `try` gives all-or-nothing, and the existing failure path preserves the prior records.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_extract_requirements.py — append
from procurement.extract_requirements import extract_requirements, _chunks
from shared.llm.mock_client import MockLLMClient


def test_chunks_never_split_a_line(tmp_path):
    text = "\n".join(f"Table 1 | A{i}=4.{i} | B{i}=param | C{i}={i} | D{i}=kW"
                     for i in range(1, 40))
    parts = _chunks(text, budget=200)
    assert len(parts) > 1
    assert "\n".join(parts) == text          # lossless and in order
    for part in parts:
        for line in part.splitlines():
            assert line in text.splitlines()


def test_chunking_is_deterministic():
    text = "\n".join(f"line {i}" for i in range(200))
    assert _chunks(text, 500) == _chunks(text, 500)


def test_every_chunk_is_asked_and_the_results_merge(tmp_path):
    src = tmp_path / "mr.txt"
    src.write_text("\n".join(f"clause {i} body text here" for i in range(400)),
                   encoding="utf-8")
    client = MockLLMClient([
        {"requirements": [{"clause_ref": "1.1", "text": "first"}]},
        {"requirements": [{"clause_ref": "2.2", "text": "second"}]},
        {"requirements": [{"clause_ref": "3.3", "text": "third"}]},
    ])
    records, status, notes = extract_requirements("d1", str(src), client)
    assert status == "ok" and notes is None
    assert [r.clause_ref for r in records] == ["1.1", "2.2", "3.3"]
    assert len(client.calls) == 3


def test_one_failing_chunk_fails_the_whole_extraction(tmp_path):
    src = tmp_path / "mr.txt"
    src.write_text("\n".join(f"clause {i} body text here" for i in range(400)),
                   encoding="utf-8")

    class SecondChunkFails(MockLLMClient):
        def classify_structure(self, prompt, output_schema, context_text, images=None):
            super().classify_structure(prompt, output_schema, context_text, images)
            if len(self.calls) == 2:
                raise RuntimeError("provider unavailable")
            return {"requirements": [{"clause_ref": "1.1", "text": "first"}]}

    records, status, notes = extract_requirements(
        "d1", str(src), SecondChunkFails({}))
    # a partial merge would delete the clauses the missing chunks carry
    assert (records, status) == ([], "failed")
    assert "provider unavailable" in notes


def test_a_clause_repeated_across_chunks_is_stored_once(tmp_path):
    src = tmp_path / "mr.txt"
    src.write_text("\n".join(f"clause {i} body" for i in range(400)), encoding="utf-8")
    same = {"requirements": [{"clause_ref": "4.2.7", "text": "H2S up to 700 ppm"}]}
    client = MockLLMClient([same, same, same])
    records, status, _ = extract_requirements("d1", str(src), client)
    assert status == "ok"
    assert len(records) == 1
```

```python
# tests/test_mock_client.py — append. `_Layout` is the pydantic schema this
# file already declares at :5; the mock ignores it, but pass the real one.
def test_mock_client_returns_a_sequence_in_order():
    c = MockLLMClient([{"rows": 1}, {"rows": 2}])
    assert c.classify_structure("p", _Layout, "t")["rows"] == 1
    assert c.classify_structure("p", _Layout, "t")["rows"] == 2
    # exhausted: the last response repeats rather than raising, so a test that
    # miscounts chunks fails on its assertion, not on an IndexError
    assert c.classify_structure("p", _Layout, "t")["rows"] == 2


def test_mock_client_still_accepts_a_single_dict():
    c = MockLLMClient({"rows": 1})
    assert c.classify_structure("p", _Layout, "t")["rows"] == 1
    assert c.classify_structure("p", _Layout, "t")["rows"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_extract_requirements.py tests/test_mock_client.py -v
```

Expected: `ImportError: cannot import name '_chunks'`, and `MockLLMClient` deep-copying a list rather than indexing it.

- [ ] **Step 3: Write the implementation**

`shared/llm/mock_client.py`:

```python
class MockLLMClient:
    def __init__(self, response: dict | list[dict], supports_vision: bool = True):
        # A list is a call sequence, for extractors that chunk their input. The
        # last entry repeats once exhausted: a test that miscounts chunks should
        # fail on its own assertion, not on an IndexError from the fixture.
        self._responses = list(response) if isinstance(response, list) else [response]
        self.supports_vision = supports_vision
        self.last_call: dict | None = None
        self.calls: list[dict] = []

    def classify_structure(self, prompt, output_schema, context_text, images=None) -> dict:
        self.last_call = {"prompt": prompt, "context_text": context_text}
        self.calls.append(self.last_call)
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return copy.deepcopy(self._responses[index])
```

`procurement/extract_requirements.py`:

```python
import os

# The output JSON echoes every clause verbatim, so it is larger than the input.
# The live 25,176-char MR overflowed the 8192-token ceiling in one call and
# came back empty. Budget is over INPUT chars; tune with the env var if a
# corpus needs it. Illustrative, not load-bearing.
REQUIREMENTS_CHUNK_CHARS = int(os.getenv("REQUIREMENTS_CHUNK_CHARS") or 8000)


def _chunks(text: str, budget: int) -> list[str]:
    """Split on line boundaries, in order, losslessly.

    Never mid-line: read_xlsx_text emits one spreadsheet row per line, and a
    value torn from its unit invites the model to pair the wrong number with
    the wrong unit. No overlap: overlap duplicates clauses.
    """
    out: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines():
        cost = len(line) + 1
        if current and size + cost > budget:
            out.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += cost
    if current:
        out.append("\n".join(current))
    return out or [""]
```

Inside `extract_requirements`, the read-and-ask block becomes:

```python
    try:
        if text is None:
            text = read_text(path, llm_fallback=pdf_fallback)
        prompt = _PROMPT.read_text(encoding="utf-8")
        raw_items: list = []
        for chunk in _chunks(text, REQUIREMENTS_CHUNK_CHARS):
            raw = client.classify_structure(prompt, _RequirementList, chunk)
            # An omitted optional array is an empty chunk, not a failed one.
            raw_items.extend(raw.get("requirements") or [])
    except Exception as exc:
        # All-or-nothing. A partial merge is stored as a complete success, and
        # the pipeline then replaces this document's requirements with it —
        # deleting the clauses the failed chunks carried, with nothing recording
        # why the matrix shrank.
        _log.warning("requirement extraction failed for %s: %s", path, exc)
        return [], "failed", f"extraction error: {exc}"
```

The per-item loop below it is unchanged except for the dedup, because `enumerate(raw_items, 1)` over the merged list already gives the global position that keeps `req_id` stable:

```python
    out: list[RequirementRecord] = []
    seen: set[str] = set()
    seen_bodies: set[tuple[str, str]] = set()
    for position, raw_item in enumerate(raw_items, 1):
        ...
        clause = (item.clause_ref or "").strip() or f"#{position}"

        # A header row repeated at the top of two chunks yields the same clause
        # twice. Dropping the exact repeat is right; the position-suffix branch
        # below stays, because two genuinely different clauses printed under one
        # ref must both survive.
        body_key = (clause, _NOISE.sub("", body.lower()))
        if body_key in seen_bodies:
            continue
        seen_bodies.add(body_key)

        req_id = req_id_for(doc_id, clause)
        if req_id in seen:
            clause = f"{clause}#{position}"
            req_id = req_id_for(doc_id, clause)
        seen.add(req_id)
        ...
```

Add `_NOISE = re.compile(r"[^a-z0-9]+")` and `import re` at module level, matching `compliance.py`'s convention.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_extract_requirements.py tests/test_mock_client.py tests/test_pipeline_rfq.py tests/test_phase3_lifecycle.py -v
```

Then the full suite: `python -m pytest`.

- [ ] **Step 5: Commit**

```bash
git add procurement/extract_requirements.py shared/llm/mock_client.py tests/test_extract_requirements.py tests/test_mock_client.py
git commit -m "fix(requirements): chunk the MR and merge all-or-nothing

The same 25k-char MR yielded 0, 84, 97, 97, 100 and 100 requirements across
runs; the 0 hit the output ceiling and emptied the matrix. Chunks split on
line boundaries, positions stay global so req_ids are stable, and one failing
chunk fails the extraction rather than storing a partial set as complete.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The `stated` checkability tier — extraction side

**Files:**
- Create: `shared/llm/prompts/requirements_v4.txt`
- Modify: `procurement/extract_requirements.py`, `procurement/store/models.py:135`, `procurement/extract_mom.py:183`, `procurement/compliance.py:38`
- Test: `tests/test_extract_requirements.py`, `tests/test_extract_mom.py`, `tests/test_pipeline_vocabulary.py`

**Interfaces:**
- Consumes: `_Requirement`, `RequirementRecord`, `req_id_for`
- Produces: `REQUIREMENTS_PROMPT_VERSION = "requirements_v4"`; `RequirementRecord.checkability ∈ {"auto", "stated", "judgement"}`; `vocabulary()` returns parameters from both `auto` and `stated`
- **Store invariant owned (INV-D):** every requirement stored with `checkability == "stated"` carries a non-empty `parameter`, a scalar-or-null `value`, and null `operator` and `unit`; every requirement stored `auto` carries all four of `parameter`, `operator`, `value`, `unit`.

86 of 100 clauses are `judgement`, and sampling shows why: "Anchor Bolt Required", "Neutral Earthing: Solidly Earthed for LV System", "Generator Insulation Temperature: Class F", "Hazardous Area Classification: Safe Area", "Generator Duty Type: S1 according to IEC 60034-1". None is a numeric bound, and about half are `<parameter>: <required value>` or `<thing> Required`. The stored `ip_rating == "IP 55"` already proves the pipeline carries a non-numeric value end to end.

`stated` deliberately does **not** reuse `auto`. `auto` means "compare through `units.compare`", and pushing "Class F" through unit conversion would either raise `Unconvertible` on every row or force `units.py` to grow a text branch it has no business having. `stated` has two shapes, both valid:

- **stated value** — `parameter="generator_insulation_class"`, `value="Class F"`. The vendor must state that parameter with that value.
- **presence** — `parameter="anchor_bolt"`, `value=None`. The vendor must state the parameter at all. `None` here is not missing data being coerced: the clause genuinely states no value, and Task 5 gives it a distinct verdict rationale.

`operator` and `unit` are always null on `stated`. A `stated` row that acquired an operator would be an `auto` row with no unit — the half-stated bound INV-2 has forbidden since phase 3.

**The prompt version bumps to `requirements_v4`.** That invalidates the cache and re-extracts every spec exactly once, which is required: v3 has no `stated` tier, so cached v3 records would leave the matrix at 14% forever. `requirements_v3.txt` stays on disk untouched — prompts are versioned, never edited in place.

**`vocabulary()` must include `stated` parameters.** It feeds the `tech_facts_v1` prompt's "the buyer will check these parameters" list. Omitting them means the datasheet pass is never asked for `generator_insulation_class`, no fact is stored, and every new `stated` row lands on `unanswered` — the tier would ship measuring nothing. This changes `vocabulary_sha` and re-asks every datasheet once, by design.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_extract_requirements.py — append
def test_a_stated_value_clause_is_stored_as_stated(tmp_path):
    src = tmp_path / "mr.txt"
    src.write_text("Generator Insulation Temperature: Class F", encoding="utf-8")
    client = MockLLMClient({"requirements": [{
        "clause_ref": "2.6", "text": "Generator Insulation Temperature: Class F",
        "category": "technical", "checkability": "stated",
        "parameter": "generator_insulation_class", "value": "Class F"}]})
    records, status, _ = extract_requirements("d1", str(src), client)
    r = records[0]
    assert (status, r.checkability, r.parameter, r.value) == (
        "ok", "stated", "generator_insulation_class", "Class F")
    assert r.operator is None and r.unit is None


def test_a_presence_clause_is_stated_with_a_null_value(tmp_path):
    src = tmp_path / "mr.txt"
    src.write_text("Anchor Bolt Required", encoding="utf-8")
    client = MockLLMClient({"requirements": [{
        "clause_ref": "1.10", "text": "Anchor Bolt Required",
        "checkability": "stated", "parameter": "anchor_bolt"}]})
    records, _, _ = extract_requirements("d1", str(src), client)
    assert (records[0].checkability, records[0].value) == ("stated", None)


def test_stated_without_a_parameter_degrades_to_judgement(tmp_path):
    src = tmp_path / "mr.txt"
    src.write_text("Vendor shall be reputable", encoding="utf-8")
    client = MockLLMClient({"requirements": [{
        "clause_ref": "9.1", "text": "Vendor shall be reputable",
        "checkability": "stated", "value": "reputable"}]})
    records, _, _ = extract_requirements("d1", str(src), client)
    # nothing to match a fact against: this is a human's call, not a check
    assert (records[0].checkability, records[0].parameter) == ("judgement", None)


def test_a_stated_list_value_degrades_to_judgement(tmp_path):
    src = tmp_path / "mr.txt"
    src.write_text("Codes: several", encoding="utf-8")
    client = MockLLMClient({"requirements": [{
        "clause_ref": "5.1", "text": "Codes: several", "checkability": "stated",
        "parameter": "codes", "value": ["IEC 60034-1", "ISO 8528"]}]})
    records, _, _ = extract_requirements("d1", str(src), client)
    # a set of permitted values is `in`, which is an auto bound, not a stated one
    assert records[0].checkability == "judgement"


def test_stated_never_keeps_an_operator_or_unit(tmp_path):
    src = tmp_path / "mr.txt"
    src.write_text("Insulation Class F", encoding="utf-8")
    client = MockLLMClient({"requirements": [{
        "clause_ref": "2.6", "text": "Insulation Class F", "checkability": "stated",
        "parameter": "generator_insulation_class", "value": "Class F",
        "operator": ">=", "unit": "degC"}]})
    records, _, _ = extract_requirements("d1", str(src), client)
    assert records[0].operator is None and records[0].unit is None
```

```python
# tests/test_pipeline_vocabulary.py — append
def test_vocabulary_includes_stated_parameters():
    from procurement.compliance import vocabulary
    from procurement.store.models import RequirementRecord, RequirementSet
    reqset = RequirementSet(requirements=[
        RequirementRecord(req_id="r-1", clause_ref="1", text="t", source_doc_id="d",
                          checkability="auto", parameter="h2s_content",
                          operator="<=", value=700, unit="ppm"),
        RequirementRecord(req_id="r-2", clause_ref="2", text="t", source_doc_id="d",
                          checkability="stated", parameter="generator_insulation_class",
                          value="Class F"),
        RequirementRecord(req_id="r-3", clause_ref="3", text="t", source_doc_id="d",
                          checkability="judgement"),
    ])
    # without this the datasheet pass is never asked for the stated parameter,
    # no fact is stored, and every stated row lands on `unanswered`
    assert vocabulary(reqset) == ["generator_insulation_class", "h2s_content"]
```

```python
# tests/test_extract_mom.py — append
def test_an_amendment_cannot_leave_a_stated_requirement_without_a_parameter():
    from procurement.extract_mom import apply_amendments
    from procurement.store.models import Amendment, RequirementRecord
    req = RequirementRecord(req_id="r-1", clause_ref="2.6", text="Class F",
                            source_doc_id="d", checkability="stated",
                            parameter="generator_insulation_class", value="Class F")
    amend = Amendment(amendment_id="a-1", clause_ref="2.6", text="withdrawn value",
                      value=None, source_doc_id="m", action="modify")
    out, _ = apply_amendments([req], [amend])
    assert out[0].checkability in ("stated", "judgement")
    if out[0].checkability == "stated":
        assert out[0].parameter
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_extract_requirements.py -k stated tests/test_pipeline_vocabulary.py -v
```

Expected: FAIL — every `stated` record comes back `judgement` (the current `auto` gate is the only promotion path), and `vocabulary` returns only `["h2s_content"]`.

- [ ] **Step 3: Write the implementation**

`shared/llm/prompts/requirements_v4.txt` — copy `requirements_v3.txt` verbatim, then change the `checkability` line and add the `stated` rules block:

```
  checkability - "auto", "stated", or "judgement".
                 "auto"      the clause states a single parameter, a
                             comparison, and a NUMBER a computer could check
                             against a vendor datasheet.
                 "stated"    the clause requires a named property to have a
                             particular non-numeric value, or to be present at
                             all. "Generator Insulation Temperature: Class F",
                             "Hazardous Area Classification: Safe Area",
                             "Neutral Earthing: Solidly Earthed", "Anchor Bolt
                             Required", "Generator Duty Type: S1 according to
                             IEC 60034-1".
                 "judgement" everything else - anything needing a human to weigh
                             wording, scope, or adequacy.
```

and, in the Rules block:

```
- A "stated" clause gives parameter and value only. Never give it an operator
  or a unit: a value with an operator is an "auto" bound, and one missing its
  unit is a half-stated bound, which is worse than no bound.
- A "stated" clause whose text names a property but no value - "Anchor Bolt
  Required", "Shaft Couplings Required" - gives the parameter and omits value.
  That means "the vendor must state this", not "the value is empty".
- Use "auto", never "stated", whenever the required value is a number with a
  unit. Use "judgement", never "stated", when you cannot name the single
  property the clause constrains.
- A set of permitted values is the "auto" operator "in", not a "stated" value.
```

`procurement/extract_requirements.py`:

```python
REQUIREMENTS_PROMPT_VERSION = "requirements_v4"
_PROMPT = (Path(__file__).parents[1] / "shared" / "llm" / "prompts"
           / "requirements_v4.txt")

_CHECKABILITY = ("auto", "stated", "judgement")
```

Replacing the tier decision in the per-item loop:

```python
        auto = (item.checkability == "auto"
                and bool((item.parameter or "").strip())
                and operator is not None
                and item.value is not None
                and item.unit is not None)

        # A stated clause needs something to match a fact against, so the
        # parameter is mandatory; the value is not, because "Anchor Bolt
        # Required" genuinely states none. A list value is the `in` operator's
        # shape - an auto bound - and never a stated one.
        stated = (not auto
                  and item.checkability == "stated"
                  and bool((item.parameter or "").strip())
                  and not isinstance(item.value, (list, tuple, dict)))

        if auto:
            tier, parameter, value, unit = ("auto", (item.parameter or "").strip(),
                                            item.value, item.unit)
        elif stated:
            # operator and unit stay None: a stated row that acquired either
            # would be an auto row with a half-stated bound (INV-2, phase 3).
            tier, parameter, value, unit = ("stated", (item.parameter or "").strip(),
                                            item.value, None)
            operator = None
        else:
            tier, parameter, value, unit, operator = ("judgement", None, None, None, None)

        out.append(RequirementRecord(
            req_id=req_id, clause_ref=clause, text=body,
            category=item.category if item.category in _CATEGORIES else "technical",
            checkability=tier, parameter=parameter, operator=operator,
            value=value, unit=unit, source_doc_id=doc_id))
```

`procurement/store/models.py:135` — comment only:

```python
    checkability: str = "judgement"  # auto|stated|judgement
```

`procurement/compliance.py:38`:

```python
def vocabulary(reqset: RequirementSet) -> list[str]:
    """The parameter names the datasheet pass will be asked to look for.

    Both checked tiers contribute. Omitting `stated` would mean never asking a
    datasheet for `generator_insulation_class`, so every stated row would land
    on `unanswered` and the tier would measure nothing.
    """
    return sorted({r.parameter for r in reqset.requirements
                   if r.checkability in ("auto", "stated")
                   and r.parameter and not r.withdrawn})
```

`procurement/extract_mom.py:183` — extend the post-amendment demotion so a `stated` requirement cannot lose its parameter:

```python
        if target.checkability == "auto" and not (
                target.parameter and target.operator
                and target.value is not None and target.unit is not None):
            target.checkability = "judgement"
        elif target.checkability == "stated" and not target.parameter:
            # same rule one tier over: a stated row with nothing to match a
            # fact against is a human's call, not a check
            target.checkability = "judgement"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_extract_requirements.py tests/test_extract_mom.py tests/test_pipeline_vocabulary.py tests/test_pipeline_rfq.py -v
```

Then the full suite: `python -m pytest`.

- [ ] **Step 5: Commit**

```bash
git add shared/llm/prompts/requirements_v4.txt procurement/extract_requirements.py procurement/store/models.py procurement/extract_mom.py procurement/compliance.py tests/test_extract_requirements.py tests/test_extract_mom.py tests/test_pipeline_vocabulary.py
git commit -m "feat(requirements): add the stated checkability tier

86 of 100 clauses were judgement, and they are not numeric bounds: 'Insulation
Class F', 'Anchor Bolt Required', 'Hazardous Area Classification: Safe Area'.
stated carries a parameter and an optional text value, never an operator or a
unit, and joins auto in the datasheet vocabulary.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The `stated` tier — compliance and display

**Files:**
- Modify: `procurement/compliance.py:51-98`, `procurement/matrix.py:41-109`
- Modify: `portal/views/compliance.py:18-24,56-59`, `web/src/constants.ts:21`, `web/src/pages/ComplianceMatrix.tsx:83`
- Test: `tests/test_compliance.py`, `tests/test_compliance_matrix.py`, `tests/test_compliance_coverage.py`

**Interfaces:**
- Consumes: `RequirementRecord.checkability == "stated"` from Task 4
- Produces: `_stated_matches(required, stated) -> bool`; `Coverage.stated_cells: int`
- **Store invariant owned (INV-E):** every compliance verdict names the `req_id` it was computed from; a `stated` verdict of `pass` names the `fact_id` that satisfied it, and a `stated` requirement with no matching fact is `unanswered`, never `fail`.

The verdict rule is the whole design, and it is deliberately asymmetric:

| situation | verdict | why |
|---|---|---|
| no fact names the parameter | `unanswered` | The pipeline did not read the answer. That is our gap, not the vendor's failure — the rule the module docstring already sets for `auto`. |
| `value is None` and a fact exists | `pass` | The clause required the property to be stated; it is stated. The rationale quotes the value so a reviewer sees what was accepted. |
| required tokens ⊆ stated tokens | `pass` | "Class F" is satisfied by "Insulation Class F, temperature rise Class B". |
| tokens do not match | **`review`**, never `fail` | Text mismatch is genuinely ambiguous. "Class H" is *better* insulation than "Class F"; a token comparison cannot know that, and marking it `fail` would blame a compliant vendor on the strength of a string diff. The rationale cites both values so the human decides in one glance instead of reading the clause from scratch. |

Subset rather than equality is the load-bearing part: a datasheet states `insulation_class = "Class F / Class B rise"`, and requiring equality would fail every real vendor.

`Coverage` gains `stated_cells` alongside `auto_cells` rather than folding the two together. They measure different things — `auto_cells` measures how much of the matrix arithmetic settled, `stated_cells` how much text matching settled — and the existing `unanswered_silent` / `unanswered_refused` split, which is the real coverage signal, only makes sense against `auto`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_compliance.py — append
from procurement.compliance import evaluate
from procurement.store.models import RequirementRecord


def _stated(parameter="generator_insulation_class", value="Class F", clause="2.6"):
    return RequirementRecord(req_id="r-1", clause_ref=clause, text="Insulation Class F",
                             source_doc_id="d", checkability="stated",
                             parameter=parameter, value=value)


def _fact(parameter, value, fact_id="f-1"):
    return {"fact_id": fact_id, "parameter": parameter, "value": value,
            "unit": None, "doc_id": "d9"}


def test_stated_passes_when_the_required_tokens_are_present():
    r = evaluate(_stated(), [_fact("generator_insulation_class",
                                   "Class F / Class B rise")], [], "KERUI", "now")
    assert (r.verdict, r.fact_id) == ("pass", "f-1")


def test_stated_with_no_matching_fact_is_unanswered_never_fail():
    r = evaluate(_stated(), [_fact("continuous_rating", "525")], [], "ADPOWER", "now")
    assert r.verdict == "unanswered"
    assert "generator_insulation_class" in r.rationale


def test_a_stated_mismatch_is_review_not_fail():
    # Class H is better insulation than Class F; a token diff cannot know that
    r = evaluate(_stated(), [_fact("generator_insulation_class", "Class H")],
                 [], "MKON", "now")
    assert r.verdict == "review"
    assert "Class F" in r.rationale and "Class H" in r.rationale
    assert r.fact_id == "f-1"       # cite the evidence the human must weigh


def test_a_presence_requirement_passes_when_the_parameter_is_stated_at_all():
    r = evaluate(_stated(parameter="anchor_bolt", value=None),
                 [_fact("anchor_bolt", "Provided")], [], "KERUI", "now")
    assert (r.verdict, r.fact_id) == ("pass", "f-1")


def test_a_declared_deviation_still_beats_a_stated_pass():
    dev = [{"clause_ref": "2.6", "statement": "Class B only", "disposition": "deviate"}]
    r = evaluate(_stated(), [_fact("generator_insulation_class", "Class F")],
                 dev, "MKON", "now")
    assert r.verdict == "deviation"
```

```python
# tests/test_compliance_coverage.py — append. Follows this file's existing
# idiom: build the store directly with snapshots, then evaluate and build.
from procurement.matrix import build_matrix


def test_stated_cells_are_counted_separately_from_auto(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    project = load_project(root, "p")
    project.vendors = ["KERUI"]
    save_project(root, project)

    snapshots.save_requirements(root, "p", RequirementSet(requirements=[
        RequirementRecord(req_id=req_id_for("d1", "1.1"), clause_ref="1.1",
                          text="noise limit 85 dBA", checkability="auto",
                          parameter="noise_limit", operator="<=", value=85.0,
                          unit="dBA", source_doc_id="d1"),
        RequirementRecord(req_id=req_id_for("d1", "2.6"), clause_ref="2.6",
                          text="Generator Insulation Temperature: Class F",
                          checkability="stated",
                          parameter="generator_insulation_class",
                          value="Class F", source_doc_id="d1"),
        RequirementRecord(req_id=req_id_for("d1", "3.1"), clause_ref="3.1",
                          text="Vendor shall be reputable",
                          checkability="judgement", source_doc_id="d1"),
    ]))
    snapshots.save_facts(root, "p", VendorFacts(vendor="KERUI", technical=[
        {"fact_id": "f-1", "parameter": "noise_limit", "value": 82.0,
         "unit": "dBA", "verbatim": "82 dBA", "doc_id": "d9"},
        {"fact_id": "f-2", "parameter": "generator_insulation_class",
         "value": "Class F / Class B rise", "unit": None,
         "verbatim": "Class F / Class B rise", "doc_id": "d9"},
    ]))
    evaluate_project(root, "p", now=NOW)
    coverage = build_matrix(root, "p").coverage

    # one vendor, one cell each: the judgement row is counted in neither
    assert (coverage.auto_cells, coverage.stated_cells) == (1, 1)
    assert coverage.by_verdict["pass"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_compliance.py -k stated -v
```

Expected: FAIL — every `stated` requirement returns `review` today, because `evaluate` branches on `checkability != "auto"`.

- [ ] **Step 3: Write the implementation**

`procurement/compliance.py` — insert after the deviation check, before the `checkability != "auto"` branch:

```python
def _stated_matches(required, stated) -> bool:
    """True when every token of the required value appears in the stated one.

    Subset, not equality: a datasheet prints "Class F / Class B rise" for a
    clause requiring "Class F", and equality would fail every real vendor.
    """
    req_tokens = {t for t in _NOISE.split(str(required).lower()) if t}
    got_tokens = {t for t in _NOISE.split(str(stated).lower()) if t}
    return bool(req_tokens) and req_tokens <= got_tokens
```

```python
    if requirement.checkability == "stated":
        want = _norm(requirement.parameter)
        fact = next((f for f in facts if _norm(f.get("parameter")) == want), None)
        if fact is None:
            # the same rule `auto` follows: not having read the answer is not
            # the vendor having answered wrongly
            return result("unanswered",
                          f"no vendor document stated {requirement.parameter!r}")
        got = fact.get("value")
        if requirement.value is None:
            return result("pass",
                          f"vendor states {requirement.parameter} = {got!r}", fact)
        if _stated_matches(requirement.value, got):
            return result("pass",
                          f"vendor states {requirement.parameter} = {got!r}, "
                          f"which carries the required {requirement.value!r}", fact)
        # never `fail`: "Class H" is better insulation than "Class F", and a
        # token comparison cannot know that. Cite both and let a human decide.
        return result("review",
                      f"required {requirement.parameter} = {requirement.value!r}; "
                      f"vendor states {got!r}", fact)
```

`procurement/matrix.py`:

```python
class MatrixRow(BaseModel):
    ...
    checkability: str               # auto|stated|judgement
```

```python
class Coverage(BaseModel):
    auto_cells: int = 0
    stated_cells: int = 0           # text-matched, counted separately: the
                                    # unanswered_silent/refused split below is
                                    # only meaningful against `auto`
    by_verdict: dict[str, int] = {}
    unanswered_silent: int = 0
    unanswered_refused: int = 0
```

In `build_matrix`, replace the `:95` tally condition:

```python
            if requirement.checkability == "auto":
                _tally(coverage, cells[vendor])
            elif requirement.checkability == "stated":
                coverage.stated_cells += 1
                coverage.by_verdict[cells[vendor].verdict] = (
                    coverage.by_verdict.get(cells[vendor].verdict, 0) + 1)
```

`portal/views/compliance.py`:

```python
def _bound(row) -> str:
    """The requirement's checkable bound, or its clause text."""
    if row.checkability == "stated":
        return (f"{row.parameter} = {row.value}" if row.value is not None
                else f"{row.parameter} (must be stated)")
    if row.checkability != "auto":
        return row.text
    value = row.value if not isinstance(row.value, list) else "..".join(
        str(v) for v in row.value)
    return f"{row.parameter} {row.operator} {value} {row.unit or ''}".strip()
```

and at `:58`, `if row.checkability == "auto":` becomes `if row.checkability in ("auto", "stated"):`, so the clause text is still shown as the caption under a `stated` bound.

`web/src/constants.ts:21` and `web/src/pages/ComplianceMatrix.tsx:83` take the matching change — the `!== 'auto'` / `=== 'auto'` tests become membership in `['auto', 'stated']`, with the `stated` bound rendered as `parameter = value` (or `parameter (must be stated)` when `value` is null).

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_compliance.py tests/test_compliance_matrix.py tests/test_compliance_coverage.py tests/test_portal_compliance.py tests/test_api_compliance.py -v
```

Then the full suite plus the web build: `python -m pytest` and `npm --prefix web run build`.

- [ ] **Step 5: Commit**

```bash
git add procurement/compliance.py procurement/matrix.py portal/views/compliance.py web/src/constants.ts web/src/pages/ComplianceMatrix.tsx tests/test_compliance.py tests/test_compliance_matrix.py tests/test_compliance_coverage.py
git commit -m "feat(compliance): evaluate the stated tier by token subset

pass when the required tokens appear in the vendor's stated value, unanswered
when no fact names the parameter, and review - never fail - on a mismatch:
Class H is better insulation than Class F and a token diff cannot know that.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Integration — the two-run mutation matrix

**Files:**
- Create: `tests/test_extraction_coverage.py`
- Modify: `CLAUDE.md` (baseline counts), `.superpowers/sdd/2026-08-02-extraction-coverage/progress.md`
- Test: itself

**Interfaces:**
- Consumes: everything from Tasks 1-5
- Produces: no production interface. This task's deliverable is the matrix.
- **Store invariant owned:** none new. This task **defends** INV-A through INV-E; every row below names the invariant it defends.

Per `docs/superpowers/PLAN-TEMPLATE.md` Rule 2: every plan defect that survived phase 2's per-task TDD needed **two runs or two modules** to see. Single-run tests structurally cannot catch them. Each row runs ingestion twice against the same project directory with one thing mutated in between.

- [ ] **Step 1: Write the failing tests — one per row**

| # | mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|---|
| 1 | a newer revision of an already-extracted document arrives | INV-B | the superseded document's facts are gone from `VendorFacts.technical`; the new revision's are present |
| 2 | a document is deleted from its vendor folder | INV-B | no fact in `technical` carries the deleted `doc_id` |
| 3 | a second upload carries a sibling revision | INV-B | `supersedes` / `superseded_by` survive the extraction cache on both records |
| 4 | `REQUIREMENTS_PROMPT_VERSION` is bumped | INV-C | run 2 re-extracts the spec exactly once, and `prompt_version` persists as `requirements_v4` |
| 5 | `TECH_PROMPT_VERSION` is bumped | INV-B | every routed vendor document re-extracts exactly once, across all eight routed classes |
| 6 | a requirement's `parameter` is edited, changing `vocabulary_sha` | INV-B, INV-D | every datasheet-routed document re-extracts exactly once; a `stated` parameter edit triggers it as surely as an `auto` one |
| 7 | the tech extractor fails on run 1, succeeds on run 2 | INV-B | run 1 records `failed` with a reason; run 2 stores the facts. The transient failure is not cached as an answer |
| 8 | the tech extractor fails on both runs | INV-B | `notes` is non-null on both runs; the previously-stored facts survive both |
| 9 | a vendor's only document becomes unclassified | INV-B | the vendor keeps a column of `unanswered` in the matrix and does not vanish |
| 10 | a re-extraction fails after a successful one | INV-B | the good facts from run 1 are still stored after run 2's failure |
| 11 | the model returns a response omitting `facts` | INV-B | status is `ok` with zero facts, not `failed`; `notes` records the empty result |
| 12 | **a document's text layer disappears between runs** | **INV-A** | run 2 records `failed` with "no readable text", and run 1's facts survive — this is the `pdftotext`-missing scenario |
| 13 | **a document's class changes from routed to `unclassified`** | **INV-B** | its facts are pruned from `technical` — routing widened in Task 2 makes this the new C1 surface |
| 14 | **chunk 2 of 3 fails on run 2 after all 3 succeeded on run 1** | **INV-C** | `requirements.json` still holds run 1's complete set; nothing partial is stored |
| 15 | **the MR grows past one chunk between runs** | **INV-C** | every clause of the larger MR is stored, and the `req_id` of an unchanged, clause-referenced requirement is unchanged |
| 16 | **a spec revision is superseded between runs** | INV-C | the superseded spec's requirements are pruned; the new revision's are stored |
| 17 | **a MOM is withdrawn between runs** | INV-C | the amendment is pruned and the clause's `base_body` is restored exactly |
| 18 | **a requirement is deleted while an override targets it** | INV-C | the override is retained and flagged `conflict=True`, not silently dropped |
| 19 | **a requirement flips `auto` → `stated` on re-extraction** | INV-D, INV-E | no stored requirement holds an `operator` with a null `unit`; no verdict outlives the `fact_id` it named |
| 20 | **a quotation returns `base_price` as `{"value": N}`** | — | the bid extracts cleanly. Regression row for the fix already in the working tree; it must not be undone by this plan |

Reference shape for row 12, the one that is new and load-bearing:

```python
def test_a_text_layer_disappearing_does_not_delete_the_facts(tmp_path):
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    z = tmp_path / "v.zip"
    z.write_bytes(_zip({
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/01 DataSheet.txt": b"Continuous rating 550 kW, insulation Class F",
    }))
    unpack_vendor_zip(root, "p", str(z))
    run_ingestion(root, "p", RoutingClient())
    before = snapshots.load_facts(root, "p", "KERUI").technical
    assert before

    # run 2: the reader now yields nothing for that document
    sheet = next(p for p in (tmp_path / "projects").rglob("01 DataSheet.txt"))
    sheet.write_text("x", encoding="utf-8")
    run_ingestion(root, "p", RoutingClient())

    doc = next(d for d in snapshots.load_documents(root, "p")
               if d.path.endswith("01 DataSheet.txt"))
    assert doc.extraction_status == "failed" and "no readable text" in doc.notes
    # `failed` counts as live in _prune_orphan_facts, so the good facts survive
    assert snapshots.load_facts(root, "p", "KERUI").technical == before
```

- [ ] **Step 2: Run the tests**

```bash
python -m pytest tests/test_extraction_coverage.py -v
```

Expected: **all rows pass.** This task runs after Tasks 1-5, so every defect these rows describe is already fixed — a red row here is a real defect in the owning task, not a red-green step. That is why this task's falsification step is Step 4 (reinstate each defect and confirm the row goes red) rather than the usual write-it-red-first cycle: there is nothing left to write red against.

- [ ] **Step 3: No implementation** — this task writes tests only. If a row fails against completed Tasks 1-5, that is a defect in the owning task. Fix it there, in its own commit, and re-run.

- [ ] **Step 4: Verify the matrix is real, not decorative**

Per Rule 2, before declaring this task done: reintroduce each defect one at a time — revert `_VENDOR_ROUTE` to the three-class gate, make the chunk merge partial instead of all-or-nothing, mark an unreadable document `skipped` instead of `failed`, drop `stated` from `vocabulary()` — and confirm **the intended row fails and nothing else does**. A row that still passes with its defect reinstated is decoration. One throwaway script is enough; phase 2's fix wave did exactly this for all nine of its fixes.

- [ ] **Step 4b: Record the new baselines**

```bash
python -m pytest
```

Update the baseline table in `CLAUDE.md` with the new counts — previous baseline plus the tests this plan adds, on both the workstation and the clean-checkout row. Write the run output into `.superpowers/sdd/2026-08-02-extraction-coverage/progress.md` alongside the per-row verification results from Step 4.

- [ ] **Step 5: Commit**

```bash
git add tests/test_extraction_coverage.py CLAUDE.md .superpowers/sdd/2026-08-02-extraction-coverage/progress.md
git commit -m "test: two-run mutation matrix for extraction coverage

Twenty rows, each naming the invariant it defends. Verified by reinstating
each defect and confirming the intended row - and only that row - fails.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: A coverage floor against the live corpus

**Files:**
- Create: `tests/test_real_corpus_coverage.py`
- Test: itself

**Interfaces:**
- Consumes: `build_matrix`, `snapshots.load_documents`, `snapshots.load_facts`
- Produces: no production interface.
- **Store invariant owned:** none. This is a regression floor, not an invariant.

Every measurement that motivated this plan came from stores in `projects/`. Without a test that reads them, the next prompt or routing change silently regresses coverage and nobody notices until a matrix is wrong in front of a client.

This test **must skip cleanly when `data/procurement-data/` is absent**, matching the three existing `data/`-guarded skips — CI has no corpus, and per the Global Constraints a test that needs a key or a fixture belongs behind a skip guard, never behind a repository secret. It asserts against the **already-ingested stores**, not by running ingestion, so it makes no LLM calls and needs no key.

- [ ] **Step 1: Write the failing test**

```python
import json
import os
import pytest

_PROJECTS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "projects")

pytestmark = pytest.mark.skipif(
    not os.path.isdir(_PROJECTS),
    reason="requires the untracked projects/ store directory")


def _latest_multi_vendor_store():
    """The newest store holding more than one vendor, or None."""
    best = None
    for slug in os.listdir(_PROJECTS):
        store = os.path.join(_PROJECTS, slug, "store")
        vendors = os.path.join(store, "vendors")
        if not os.path.isdir(vendors) or len(os.listdir(vendors)) < 2:
            continue
        stamp = os.path.getmtime(os.path.join(_PROJECTS, slug, "project.json"))
        if best is None or stamp > best[0]:
            best = (stamp, store)
    return best[1] if best else None


def test_no_vendor_in_a_multi_vendor_store_has_zero_technical_facts():
    store = _latest_multi_vendor_store()
    if store is None:
        pytest.skip("no ingested multi-vendor project in projects/")
    empty = []
    for vendor in os.listdir(os.path.join(store, "vendors")):
        path = os.path.join(store, "vendors", vendor, "facts.json")
        with open(path, encoding="utf-8") as fh:
            facts = json.load(fh)
        if not facts.get("technical"):
            empty.append(vendor)
    # ADPOWER had zero facts in every run before this plan, because their only
    # technical document was their marked-up copy of the client spec
    assert not empty, f"vendors with no technical facts at all: {empty}"


def test_every_extracted_document_records_a_text_source():
    store = _latest_multi_vendor_store()
    if store is None:
        pytest.skip("no ingested multi-vendor project in projects/")
    with open(os.path.join(store, "documents.json"), encoding="utf-8") as fh:
        docs = json.load(fh)
    missing = [d["path"] for d in docs
               if d.get("vendor") and d["extraction_status"] in ("ok", "failed")
               and not d.get("text_source")]
    assert not missing, f"documents with no text_source: {missing}"


def test_a_majority_of_requirements_are_machine_checkable():
    store = _latest_multi_vendor_store()
    if store is None:
        pytest.skip("no ingested multi-vendor project in projects/")
    with open(os.path.join(store, "requirements.json"), encoding="utf-8") as fh:
        reqs = json.load(fh)["requirements"]
    if not reqs:
        pytest.skip("no requirements ingested")
    checked = [r for r in reqs if r["checkability"] in ("auto", "stated")]
    # the pre-plan figure was 14/100. The floor is deliberately well under the
    # target: this guards against regression, it does not certify the prompt.
    assert len(checked) / len(reqs) >= 0.35, (
        f"only {len(checked)}/{len(reqs)} requirements are machine-checkable")
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
python -m pytest tests/test_real_corpus_coverage.py -v
```

Expected on a workstation, against the pre-plan stores: FAIL — ADPOWER has zero technical facts, no document has a `text_source`, and the checkable ratio is 0.14. Expected in CI: three skips.

- [ ] **Step 3: Re-ingest the corpus and confirm the floor is met**

This is the plan's real acceptance check, and it costs LLM calls. Create a fresh project from `data/procurement-data/` through the portal or the API, run ingestion once, and re-run the test. If the checkable ratio is below 0.35, the `requirements_v4` prompt needs another pass — iterate on the prompt, not on the threshold.

- [ ] **Step 4: Run the full suite**

```bash
python -m pytest
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_real_corpus_coverage.py
git commit -m "test: coverage floor against the live corpus

Skipped when projects/ is absent, so CI stays key-free and green. Asserts
what actually regressed: a vendor with zero technical facts, a document with
no recorded text source, and the machine-checkable ratio.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Per-vendor extraction status — derivation and API

**Files:**
- Create: `procurement/coverage.py`
- Modify: `api/main.py` (new route, and `_project_summary` roll-up)
- Test: `tests/test_extraction_status.py`, `tests/test_api_extraction_status.py`

**Interfaces:**
- Consumes: `snapshots.load_documents`, `snapshots.load_facts`, `snapshots.load_compliance`, `load_project`, and `VENDOR_ROUTE` from Task 2
- Produces: `DocumentStatus`, `VendorExtraction`, `ExtractionStatus` (pydantic models); `build_extraction_status(root: str, slug: str) -> ExtractionStatus`; `GET /api/projects/{slug}/extraction-status`
- **Store invariant owned:** none. `coverage.py` is a read-only derivation over the snapshots, recomputed wholesale on every call exactly as `matrix.py` and `compliance.evaluate_project` are, so a status can never outlive the document it describes. It **defends** INV-A by surfacing `text_source` and INV-B by reporting each document's fact count.

Everything this needs is already stored. `documents.json` carries `vendor`, `doc_class`, `extraction_status`, `notes` and — after Task 1 — `text_source`. `facts.json` carries each fact's `doc_id`. `compliance.json` carries the per-vendor verdicts. No new extraction, no new stored collection, no LLM call.

**Reporting `route` beside `doc_class` is the point of the panel.** Before Task 2 they were the same string, so the distinction was invisible. After it, "classified `spec`, read as a datasheet, contributed 12 facts" is the line that makes ADPOWER's situation legible at a glance instead of after a store audit — and "classified `drawing`, read as a datasheet, 0 facts, `pypdf:42chars`, *no readable text layer*" explains a genuinely empty document without anyone opening the PDF.

Two rules the derivation inherits from its neighbours:

**Vendors come from the project, not from the facts directory.** `evaluate_project` and `build_matrix` both do this, for the same reason: a vendor whose extraction produced nothing must still appear, because a missing entry reads as "did not bid". A vendor with zero documents gets an entry with an empty document list.

**`unanswered` counts only the checked tiers.** It is the extraction-coverage metric `compliance.py`'s docstring already claims to produce. Counting `review` cells in it would drown the signal — 255 of 300 cells in `gas-11` are `review`, and none of them says anything about whether we read the vendor's files.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_extraction_status.py
from procurement.coverage import build_extraction_status
from procurement.project import create_project, load_project, save_project
from procurement.store import snapshots
from procurement.store.models import (ComplianceResult, DocumentRecord,
                                      RequirementRecord, RequirementSet,
                                      VendorFacts)

NOW = "2026-08-02T00:00:00+00:00"


def _store(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    project = load_project(root, "p")
    project.vendors = ["ADPOWER", "SILENT"]
    save_project(root, project)

    snapshots.save_documents(root, "p", [
        DocumentRecord(doc_id="d1", path="vendors/ADPOWER/quote.pdf",
                       vendor="ADPOWER", doc_class="quotation",
                       content_sha256="a", extraction_status="ok",
                       text_source="pdftotext:11682chars"),
        DocumentRecord(doc_id="d2", path="vendors/ADPOWER/MR copy.pdf",
                       vendor="ADPOWER", doc_class="spec",
                       content_sha256="b", extraction_status="ok",
                       text_source="pdftotext:60696chars"),
        DocumentRecord(doc_id="d3", path="vendors/ADPOWER/layout.pdf",
                       vendor="ADPOWER", doc_class="drawing",
                       content_sha256="c", extraction_status="failed",
                       notes="no readable text layer (42 chars via pypdf)",
                       text_source="pypdf:42chars"),
        DocumentRecord(doc_id="d4", path="vendors/ADPOWER/old-quote.pdf",
                       vendor="ADPOWER", doc_class="quotation",
                       content_sha256="d", extraction_status="skipped",
                       superseded_by="d1", notes="superseded by d1"),
    ])
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="ADPOWER",
        commercial={"vendor": "ADPOWER", "base_price": 1110836.0},
        quotation_doc_id="d1",
        technical=[{"fact_id": "f-1", "parameter": "continuous_rating",
                    "value": 525.0, "unit": "kW", "doc_id": "d2"},
                   {"fact_id": "f-2", "parameter": "rated_voltage",
                    "value": 415.0, "unit": "V", "doc_id": "d2"}]))
    snapshots.save_requirements(root, "p", RequirementSet(requirements=[
        RequirementRecord(req_id="r-1", clause_ref="1.1", text="t",
                          source_doc_id="s", checkability="auto",
                          parameter="frequency", operator="==", value=50.0,
                          unit="Hz")]))
    snapshots.save_compliance(root, "p", [
        ComplianceResult(req_id="r-1", vendor="ADPOWER", verdict="unanswered",
                         evaluated_at=NOW),
        ComplianceResult(req_id="r-1", vendor="SILENT", verdict="unanswered",
                         evaluated_at=NOW)])
    return root


def test_each_document_reports_its_route_beside_its_class(tmp_path):
    status = build_extraction_status(_store(tmp_path), "p")
    docs = {d.filename: d for d in status.vendors[0].documents}
    # the whole point of the panel: classification and routing now differ
    assert (docs["MR copy.pdf"].doc_class, docs["MR copy.pdf"].route) == (
        "spec", "datasheet")
    assert (docs["quote.pdf"].doc_class, docs["quote.pdf"].route) == (
        "quotation", "quotation")


def test_each_document_reports_the_facts_it_contributed(tmp_path):
    status = build_extraction_status(_store(tmp_path), "p")
    docs = {d.filename: d for d in status.vendors[0].documents}
    assert docs["MR copy.pdf"].fact_count == 2
    assert docs["quote.pdf"].fact_count == 0        # commercial, not technical
    assert docs["quote.pdf"].is_quotation is True


def test_an_unreadable_document_carries_its_reason_and_reader(tmp_path):
    status = build_extraction_status(_store(tmp_path), "p")
    doc = next(d for d in status.vendors[0].documents if d.filename == "layout.pdf")
    assert doc.status == "failed"
    assert "no readable text layer" in doc.notes
    assert doc.text_source == "pypdf:42chars"


def test_vendor_totals_count_each_status(tmp_path):
    status = build_extraction_status(_store(tmp_path), "p")
    adpower = status.vendors[0]
    assert (adpower.extracted, adpower.failed, adpower.skipped) == (2, 1, 1)
    assert adpower.fact_count == 2
    assert adpower.has_commercial is True
    assert adpower.unanswered == 1


def test_a_vendor_with_no_documents_still_appears(tmp_path):
    status = build_extraction_status(_store(tmp_path), "p")
    silent = next(v for v in status.vendors if v.vendor == "SILENT")
    # a missing entry reads as "did not bid" — the rule build_matrix follows
    assert silent.documents == []
    assert (silent.extracted, silent.fact_count) == (0, 0)
    assert silent.has_commercial is False
```

```python
# tests/test_api_extraction_status.py
def test_extraction_status_route_returns_every_vendor(client, project_slug):
    body = client.get(f"/api/projects/{project_slug}/extraction-status").json()
    assert [v["vendor"] for v in body["vendors"]] == ["ADPOWER", "SILENT"]
    assert body["totals"]["failed"] >= 1


def test_extraction_status_404s_on_an_unknown_project(client):
    assert client.get("/api/projects/nope/extraction-status").status_code == 404


def test_project_summary_carries_the_extraction_rollup(client, project_slug):
    body = client.get(f"/api/projects/{project_slug}/summary").json()
    rollup = {v["vendor"]: v for v in body["extraction"]}
    # the Dashboard card needs the counts without fetching the full document list
    assert rollup["ADPOWER"]["extracted"] == 2
    assert rollup["ADPOWER"]["failed"] == 1
    assert "documents" not in rollup["ADPOWER"]
```

Follow the fixture idiom already in `tests/test_api_compliance.py` for `client` and `project_slug`.

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_extraction_status.py tests/test_api_extraction_status.py -v
```

Expected: `ModuleNotFoundError: No module named 'procurement.coverage'`, and 404 on the new route.

- [ ] **Step 3: Write the implementation**

`procurement/coverage.py`:

```python
"""(documents, facts, compliance) -> one extraction status per vendor.

Answers "which of this vendor's files did we actually read, and what did each
one give us". A read-only derivation over the snapshots, like matrix.py:
recomputed wholesale on every call, so a status can never outlive the document
it describes, and no new stored collection is introduced.
"""
import os
from pydantic import BaseModel

from procurement.pipeline import VENDOR_ROUTE
from procurement.project import load_project
from procurement.store import snapshots

# The verdicts that mean "we did not read the answer". `review` is excluded on
# purpose: 255 of gas-11's 300 cells are `review` and none of them says
# anything about extraction coverage.
_UNANSWERED = ("unanswered",)


class DocumentStatus(BaseModel):
    doc_id: str
    filename: str
    path: str
    doc_class: str              # what the classifier decided
    route: str | None           # which extractor read it; differs from the class
    status: str                 # ok | failed | skipped | pending
    notes: str | None = None
    text_source: str | None = None
    fact_count: int = 0
    is_quotation: bool = False
    superseded_by: str | None = None


class VendorExtraction(BaseModel):
    vendor: str
    documents: list[DocumentStatus] = []
    extracted: int = 0
    failed: int = 0
    skipped: int = 0
    fact_count: int = 0
    has_commercial: bool = False
    unanswered: int = 0


class ExtractionStatus(BaseModel):
    vendors: list[VendorExtraction] = []
    totals: dict[str, int] = {}


def build_extraction_status(root: str, slug: str) -> ExtractionStatus:
    documents = snapshots.load_documents(root, slug)
    compliance = snapshots.load_compliance(root, slug)
    # Vendors from the project, not from the facts directory: a vendor whose
    # extraction produced nothing must still appear, because a missing entry
    # reads as "did not bid" — the rule build_matrix and evaluate_project share.
    vendors = load_project(root, slug).vendors

    unanswered_by_vendor: dict[str, int] = {}
    for cell in compliance:
        if cell.verdict in _UNANSWERED:
            unanswered_by_vendor[cell.vendor] = (
                unanswered_by_vendor.get(cell.vendor, 0) + 1)

    out: list[VendorExtraction] = []
    for vendor in vendors:
        facts = snapshots.load_facts(root, slug, vendor)
        facts_by_doc: dict[str, int] = {}
        for fact in (facts.technical if facts else []):
            doc_id = fact.get("doc_id")
            facts_by_doc[doc_id] = facts_by_doc.get(doc_id, 0) + 1
        quotation_doc_id = facts.quotation_doc_id if facts else None

        entry = VendorExtraction(
            vendor=vendor,
            has_commercial=bool(facts and facts.commercial),
            unanswered=unanswered_by_vendor.get(vendor, 0))

        for doc in sorted((d for d in documents if d.vendor == vendor),
                          key=lambda d: d.path):
            entry.documents.append(DocumentStatus(
                doc_id=doc.doc_id,
                filename=doc.path.rsplit("/", 1)[-1],
                path=doc.path,
                doc_class=doc.doc_class,
                # re-derived, never re-invented: VENDOR_ROUTE is the pipeline's
                # own table. An inferred quotation is reported by its stored
                # link rather than by re-running the fallback heuristic.
                route=("quotation" if doc.doc_id == quotation_doc_id
                       else VENDOR_ROUTE.get(doc.doc_class)),
                status=doc.extraction_status,
                notes=doc.notes,
                text_source=doc.text_source,
                fact_count=facts_by_doc.get(doc.doc_id, 0),
                is_quotation=doc.doc_id == quotation_doc_id,
                superseded_by=doc.superseded_by))
            if doc.extraction_status == "ok":
                entry.extracted += 1
            elif doc.extraction_status == "failed":
                entry.failed += 1
            elif doc.extraction_status == "skipped":
                entry.skipped += 1

        entry.fact_count = sum(d.fact_count for d in entry.documents)
        out.append(entry)

    totals = {
        "vendors": len(out),
        "documents": sum(len(v.documents) for v in out),
        "extracted": sum(v.extracted for v in out),
        "failed": sum(v.failed for v in out),
        "skipped": sum(v.skipped for v in out),
        "facts": sum(v.fact_count for v in out),
        "unanswered": sum(v.unanswered for v in out),
    }
    return ExtractionStatus(vendors=out, totals=totals)


def rollup(status: ExtractionStatus) -> list[dict]:
    """The per-vendor counts without the document list, for the Dashboard card."""
    return [v.model_dump(exclude={"documents"}) for v in status.vendors]
```

`api/main.py` — the route, beside `get_compliance_matrix`:

```python
@app.get("/api/projects/{slug}/extraction-status")
def get_extraction_status(slug: str) -> dict:
    _load_or_404(slug)
    return build_extraction_status(ROOT, slug).model_dump()
```

and in `get_summary`, one line so the Dashboard card does not need a second round trip:

```python
        "extraction": rollup(build_extraction_status(ROOT, slug)),
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_extraction_status.py tests/test_api_extraction_status.py tests/test_api_compliance.py tests/test_api_setup.py -v
```

Then the full suite: `python -m pytest`.

- [ ] **Step 5: Commit**

```bash
git add procurement/coverage.py api/main.py tests/test_extraction_status.py tests/test_api_extraction_status.py
git commit -m "feat(api): per-vendor extraction status

Which of a vendor's files were read, by which extractor, and what each one
contributed. Reports route beside doc_class - after the routing change they
legitimately differ, and that difference is what explains a blank column.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Per-vendor extraction status — web UI

**Files:**
- Create: `web/src/pages/ExtractionStatus.tsx`
- Modify: `web/src/types.ts`, `web/src/api.ts`, `web/src/App.tsx`, `web/src/pages/Dashboard.tsx`, `web/src/theme.css`
- Test: `npm --prefix web run build` (the project has no front-end test runner; the API contract is covered by Task 8)

**Interfaces:**
- Consumes: `GET /api/projects/{slug}/extraction-status` and the `extraction` key on `/summary`, both from Task 8
- Produces: `ExtractionStatus` / `VendorExtraction` / `DocumentStatus` TypeScript interfaces; `fetchExtractionStatus(slug)`; a fourth nav entry
- **Store invariant owned:** none — display only.

Two placements, per the agreed design: a one-line roll-up per vendor on the Dashboard card, reading the `extraction` key already on `/summary`, and a dedicated screen with the full per-file listing.

The roll-up must be readable at a glance, so it carries only what changes a decision: `ADPOWER · 4 files · 2 read · 1 failed · 2 facts`. A vendor with `fact_count === 0` gets a visible warning marker — that is the single most useful signal on the page and the exact condition that went unnoticed across seven runs.

The detail screen groups by vendor, one row per file: filename, `doc_class → route`, status, fact count, and — for anything not `ok` — the stored `notes` verbatim. Rendering `notes` verbatim rather than re-wording it follows the same rule the compliance screen already applies to `rationale`.

- [ ] **Step 1: Add the types and the fetcher**

```ts
// web/src/types.ts — append
export interface DocumentStatus {
  doc_id: string
  filename: string
  path: string
  doc_class: string
  route: string | null
  status: 'ok' | 'failed' | 'skipped' | 'pending'
  notes: string | null
  text_source: string | null
  fact_count: number
  is_quotation: boolean
  superseded_by: string | null
}

export interface VendorExtraction {
  vendor: string
  documents: DocumentStatus[]
  extracted: number
  failed: number
  skipped: number
  fact_count: number
  has_commercial: boolean
  unanswered: number
}

export interface ExtractionStatus {
  vendors: VendorExtraction[]
  totals: Record<string, number>
}

// the /summary roll-up: the same record minus the document list
export type VendorExtractionRollup = Omit<VendorExtraction, 'documents'>
```

Add `extraction: VendorExtractionRollup[]` to the existing `ProjectDetail` interface.

```ts
// web/src/api.ts — append
export function fetchExtractionStatus(slug: string): Promise<ExtractionStatus> {
  return getJson(`/api/projects/${encodeURIComponent(slug)}/extraction-status`)
}
```

- [ ] **Step 2: Build the detail screen**

```tsx
// web/src/pages/ExtractionStatus.tsx
import type { JSX } from 'react'
import { fetchExtractionStatus } from '../api'
import type { DocumentStatus, VendorExtraction } from '../types'
import { useAsync } from '../useAsync'

const STATUS_MARK: Record<DocumentStatus['status'], string> = {
  ok: '✅', failed: '⚠️', skipped: '—', pending: '·',
}

export function ExtractionStatus({
  slug, projectName,
}: { slug: string; projectName: string }): JSX.Element {
  const { data, error, loading } = useAsync(
    () => fetchExtractionStatus(slug),
    [slug],
  )

  if (loading) return <div className="skeleton" style={{ height: 240 }} />
  if (error) return <p className="error">{error.message}</p>
  if (!data) return <p className="muted">No extraction status yet.</p>

  return (
    <>
      <header className="page-head">
        <h1>Extraction status</h1>
        <p className="muted">
          {projectName} — {data.totals.extracted} of {data.totals.documents}{' '}
          documents read, {data.totals.facts} technical facts,{' '}
          {data.totals.failed} could not be read.
        </p>
      </header>
      {data.vendors.map((vendor) => (
        <VendorPanel key={vendor.vendor} vendor={vendor} />
      ))}
    </>
  )
}

function VendorPanel({ vendor }: { vendor: VendorExtraction }): JSX.Element {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{vendor.vendor}</h2>
        <span className="meta">
          {vendor.documents.length} files · {vendor.extracted} read ·{' '}
          {vendor.failed} failed · {vendor.skipped} skipped ·{' '}
          {vendor.fact_count} facts · {vendor.unanswered} unanswered
        </span>
      </div>

      {vendor.fact_count === 0 && (
        // the condition that went unnoticed across seven runs
        <p className="warn">
          No technical facts were extracted for this vendor. Every
          machine-checked requirement will read as unanswered.
        </p>
      )}
      {!vendor.has_commercial && (
        <p className="warn">No commercial terms were extracted for this vendor.</p>
      )}

      {vendor.documents.length === 0 ? (
        <p className="muted mono">No documents uploaded.</p>
      ) : (
        <table className="grid">
          <thead>
            <tr>
              <th>File</th><th>Classified</th><th>Read as</th>
              <th>Status</th><th>Facts</th>
            </tr>
          </thead>
          <tbody>
            {vendor.documents.map((doc) => (
              <tr key={doc.doc_id}>
                <td>
                  {doc.filename}
                  {doc.is_quotation && <span className="vpill">quotation</span>}
                  {/* stored notes are displayed verbatim, never re-worded —
                      the rule the compliance screen applies to rationale */}
                  {doc.status !== 'ok' && doc.notes && (
                    <p className="muted mono">{doc.notes}</p>
                  )}
                </td>
                <td className="mono">{doc.doc_class}</td>
                <td className="mono">{doc.route ?? '—'}</td>
                <td>
                  {STATUS_MARK[doc.status]} {doc.status}
                  {doc.text_source && (
                    <span className="muted mono"> {doc.text_source}</span>
                  )}
                </td>
                <td className="mono">{doc.fact_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
```

- [ ] **Step 3: Wire the nav and the Dashboard roll-up**

`web/src/App.tsx` — add to the `View` union and the `NAV` array, and render it beside the other project-scoped screens. The compliance matrix stays `02`, so extraction status takes `04` rather than renumbering screens users already know:

```tsx
type View = 'dashboard' | 'setup' | 'matrix' | 'statement' | 'extraction'

  { view: 'extraction', index: '04', label: 'Extraction status', needsProject: true },
```

```tsx
            {view === 'extraction' && active && (
              <ExtractionStatus slug={active.slug} projectName={active.name} />
            )}
```

`web/src/pages/Dashboard.tsx` — inside `ProjectCard`, after the existing `vendors` block, using `data.extraction` which `/summary` now carries:

```tsx
      {data?.extraction && data.extraction.length > 0 && (
        <div className="meta" aria-label="Extraction status">
          {data.extraction.map((v) => (
            <span key={v.vendor} className={v.fact_count === 0 ? 'warn' : undefined}>
              {v.vendor} {v.extracted}/{v.extracted + v.failed + v.skipped} read
              {v.fact_count === 0 && ' · no facts'}
            </span>
          ))}
        </div>
      )}
```

`web/src/theme.css` — add a `.warn` rule and a `.grid` table rule if the sheet has no equivalent; reuse the existing `.panel`, `.meta`, `.mono`, `.muted` and `.vpill` classes rather than introducing parallel ones.

- [ ] **Step 4: Verify in the browser**

Build first, then run both dev servers from `.claude/launch.json` via the preview tooling — never a bare `npm run dev` or `streamlit run`:

```bash
npm --prefix web run build
```

Then open the app, select a project that has been ingested, and confirm: the Dashboard card shows the per-vendor roll-up; the Extraction status screen lists every file with its class, route, status and fact count; a vendor with no facts shows the warning; an unreadable document shows its stored note. Check the browser console for errors and take a screenshot of the populated screen for the ledger.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/ExtractionStatus.tsx web/src/types.ts web/src/api.ts web/src/App.tsx web/src/pages/Dashboard.tsx web/src/theme.css
git commit -m "feat(web): per-vendor extraction status screen

A roll-up on each Dashboard card and a detail screen listing every file with
its class, the extractor that read it, its status and its fact count. A vendor
with zero technical facts is called out explicitly.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Approval checklist

- [x] Every task's `Interfaces` block has a **Store invariant owned** bullet (Tasks 6-9 own none and say so explicitly — 6 and 7 defend others', 8 and 9 introduce no stored collection).
- [x] No invariant is claimed twice; each of `documents.json`, `VendorFacts.technical`, `requirements.json` and `compliance.json` has an owner.
- [x] The integration task carries all nine required rows (1-3, 5, 7-11) plus phase-specific rows for every collection this plan touches (12-19) and one regression row (20).
- [x] Every matrix row names an invariant; every invariant INV-A…INV-E has at least one row.
- [x] The Rule 3 banner appears above the first reference block.
- [x] The plan states which reference parts are load-bearing and which are illustrative.

## Self-review notes

- **Cause coverage.** Cause 1 → Task 2. Cause 2 → Task 2, with Task 1 keeping the cost down. Cause 3 → Task 3. Cause 4 → Tasks 4 and 5. The silent-empty-success finding (MKON's `HSD 230.pdf`) → Task 1's guard plus matrix row 11. Making all of it visible without a store audit → Tasks 8 and 9.
- **Type consistency.** `checkability` is the string `"auto" | "stated" | "judgement"` in `store/models.py`, `matrix.py`, `compliance.py`, `extract_requirements.py`, `portal/views/compliance.py` and `web/src/types.ts`. `_vendor_route` returns a *route* (a key of `PROMPT_VERSION_BY_CLASS`), never a `doc_class`, and `coverage.py` imports the public `VENDOR_ROUTE` table rather than re-deriving the mapping. `read_text_with_source` returns `(text, reader)` and `text_source` stores `f"{reader}:{n}chars"` — the reader alone is not what is stored. `DocumentStatus.status` carries `DocumentRecord.extraction_status` unchanged, so the four values match on both sides of the API.
- **Ordering is load-bearing.** Task 1 before Task 2 (cost and silent empties). Task 4 before Task 5 (`vocabulary()` must include `stated` parameters before `evaluate` can find their facts). Task 8 before Task 9 (the UI consumes the route). Tasks 6 and 7 after 1-5; Task 7 last of those, because its acceptance check requires a real re-ingestion.
- **Tasks 8 and 9 are independent of 6 and 7.** They depend only on Task 1 (`text_source`) and Task 2 (`VENDOR_ROUTE`), and they touch disjoint files, so they can run in parallel with the mutation matrix. Doing them *early* has a practical benefit: the extraction status screen is the fastest way to read the result of every other task in this plan.
- **Tasks 8 and 9 own no store invariant, deliberately.** `coverage.py` introduces no stored collection — it derives from `documents.json`, `facts.json` and `compliance.json` and is recomputed wholesale on every call, exactly as `matrix.py` is. That is why neither appears in the invariant register and why the mutation matrix has no row for them: there is no accumulating state for a second run to corrupt.
- **Known gap, deliberately unclosed.** The inferred-quotation fallback pool at `pipeline.py:392` still filters on `doc_class not in _EXTRACTABLE`, so a vendor with no recognised quotation can have an `other`-classed document taken as their quotation, losing that document's technical facts. This is pre-existing, unchanged by this plan, and narrower than before, since such a vendor now has seven other routed classes to contribute facts from. Closing it means letting one document feed two extractors, which is a store-shape change and belongs in its own plan.
