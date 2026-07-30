# Classification & Routed Extraction (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Classify every document in a project by type, resolve revision lineage, and extract technical facts and deviation statements from the vendor documents the pipeline currently ignores — so phase 3 has something to check the RFQ against.

**Architecture:** A two-pass classifier assigns a `doc_class` to every file: deterministic filename rules first, an LLM call only for what the rules cannot decide. `revisions.py` then resolves supersession so an obsolete revision is never extracted. The pipeline routes each document to a purpose-built extractor chosen by class, accumulating per-vendor `technical` facts and `deviations` alongside the existing `commercial` block. Per-document content hashing keeps re-runs incremental exactly as in phase 1.

**Tech Stack:** Python 3.12, Pydantic v2, `re`, `hashlib`, `pytest`. No new dependencies.

## Global Constraints

- Python `>=3.12`; dependencies unchanged.
- Snapshots under `projects/<slug>/store/` remain the only authoritative store; `index/store.db` stays derived and disposable.
- Every snapshot write is atomic — use the existing `snapshots.*` helpers, never hand-roll.
- `generation` bumps **once per write transaction**, never once per file.
- `field_path` addresses list members by **id**, never by index — so every stored fact and deviation needs a stable id.
- Missing data is never silently coerced to a passing or zero value. A parameter the model could not find is omitted, not emitted as `0` or `""`.
- **Arithmetic stays in Python.** Extractors capture numbers and units verbatim; they never convert, total, or compare. Unit conversion is phase 3 (`units.py`).
- The model reads; code decides. An extractor must never be asked whether a vendor complies.
- Tests run key-free via `MockLLMClient`. No test may require `ANTHROPIC_API_KEY`.
- Run all tests from the repo root: `python -m pytest`.

## Scope boundaries — do not exceed

- **`spec` and `mom` documents are classified but NOT extracted this phase.** `requirements_v1` and `mom_amend_v1`, `requirements.json`, and requirement amendments are all phase 3. Classifying them now is what lets phase 3 find them.
- **No compliance matching.** No `compliance.py`, no `units.py`, no verdicts. Phase 2 produces facts; phase 3 judges them.
- **No portal UI work.** The review screens are phase 4. `portal/app.py` should need no edit.
- **`layout`, `p&id`, `nameplate` collapse into `drawing`** rather than becoming separate classes, matching the `doc_class` vocabulary already declared on `DocumentRecord`.

## Class vocabulary

`DocumentRecord.doc_class` takes exactly one of:

`unclassified` (pre-classification default) · `spec` · `quotation` · `datasheet` · `deviation` · `bom` · `drawing` · `mom` · `other`

Extraction routing this phase: `quotation` → existing `bid_extract_v1`; `datasheet` → `tech_facts_v1`; `deviation` → `deviation_v1`; everything else → `extraction_status = "skipped"`.

---

### Task 1: Filename classification rules

**Files:**
- Create: `procurement/classify.py`
- Test: `tests/test_classify_rules.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `classify_by_rules(path: str) -> str | None` — returns a `doc_class`, or `None` when no rule is confident and the LLM pass should decide. Also the module constant `DOC_CLASSES: tuple[str, ...]`.

The rules promote the vocabulary already in `procurement/quote_select.py` into a class map. Order matters: the first matching class wins, so more specific keywords must be tested before broader ones (`"datasheet"` before `"data"`, `"deviation"` before `"attachment"`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_classify_rules.py
import pytest
from procurement.classify import classify_by_rules, DOC_CLASSES


@pytest.mark.parametrize("filename,expected", [
    # real filenames from data/procurement-data and processed-data
    ("Quotation of Gas Generator ZDG2024110701.pdf", "quotation"),
    ("Quotation-MKON.pdf", "quotation"),
    ("Techno Commercial proposal AESL-GTC-60808 - 22102024 -REV00.pdf", "quotation"),
    ("01 DOD-30201-50150-BH-000-16-00-004 DataSheet Gas Generator.pdf", "datasheet"),
    ("02 Detailed Vendor Standard Datasheet of all components.pdf", "datasheet"),
    ("Attachment-1 DataSheet Gas Generator commented.pdf", "datasheet"),
    ("03 Attachment-2 Vendor Deviation Form.pdf", "deviation"),
    ("Attachment-2 Vendor Deviation Form.pdf", "deviation"),
    ("BOM.pdf", "bom"),
    ("00 MOM 20241111 ASTRA ADPOWER.pdf", "mom"),
    ("ADN-AEC-ME-SPC-026 MR Gas Genset copy.pdf", "spec"),
    ("13 Gas Generator PID Commented.pdf", "drawing"),
    ("15 LAYOUT - KGW550GF-T.pdf", "drawing"),
    ("17 Outline Diagrams && General Arrangement Diagrams(1).pdf", "drawing"),
    ("18 Name Plate drawings.pdf", "drawing"),
    ("20 Single Line Diagrams.pdf", "drawing"),
    ("21 System Architecture.pdf", "drawing"),
    ("04 Consumption List.pdf", "other"),
    ("06 Operation Special tools.pdf", "other"),
    ("07 Power Auxiliary List.pdf", "other"),
    ("08 Two Years Operation Spares.pdf", "other"),
    ("11 Attachment-4 Applicable Codes and Standards Pending.pdf", "other"),
    ("Attachment-7 Load List.pdf", "other"),
    ("2011 gas quality for MAN gas engines with MAN aftertreatment system.pdf", "other"),
])
def test_rules_classify_real_filenames(filename, expected):
    assert classify_by_rules(f"/proj/vendors/KERUI/{filename}") == expected


@pytest.mark.parametrize("filename", [
    # no keyword a rule can trust -> defer to the LLM pass
    "ADP-13158-2024-935.pdf",
    "ADP-13158-2024-935(Rev1).pdf",
    "HSD 230.pdf",
])
def test_rules_defer_when_not_confident(filename):
    assert classify_by_rules(f"/proj/vendors/ADPOWER/{filename}") is None


def test_datasheet_beats_the_attachment_prefix():
    # "Attachment-1 DataSheet ..." must not be swallowed by a generic rule
    assert classify_by_rules("Attachment-1 DataSheet Gas Generator commented.pdf") == "datasheet"


def test_classification_is_case_insensitive():
    assert classify_by_rules("BILL OF MATERIAL.PDF") == "bom"


def test_every_rule_result_is_a_known_class():
    for name in ["Quotation.pdf", "Datasheet.pdf", "Deviation Form.pdf", "BOM.pdf",
                 "MOM notes.pdf", "MR spec.pdf", "Layout.pdf", "Spares list.pdf"]:
        result = classify_by_rules(name)
        assert result is None or result in DOC_CLASSES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_classify_rules.py -v`
Expected: FAIL — `procurement.classify` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/classify.py
"""Assign a doc_class to a document.

Two passes: deterministic filename rules first, an LLM call only for what
the rules cannot decide. `classified_by` records which pass decided, so the
model's calls can be audited separately from the free ones.
"""
import os

DOC_CLASSES = ("spec", "quotation", "datasheet", "deviation",
               "bom", "drawing", "mom", "other")

# Ordered: the first class whose keywords match wins, so specific beats broad.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("datasheet", ("datasheet", "data sheet")),
    ("deviation", ("deviation",)),
    ("mom", ("mom ", " mom", "minutes of meeting")),
    ("bom", ("bom", "bill of material")),
    ("drawing", ("drawing", "layout", "p&id", "pid ", " pid", "single line",
                 "nameplate", "name plate", "outline", "general arrangement",
                 "diagram", "architecture")),
    ("quotation", ("quotation", "quote", "offer", "proposal",
                   "techno commercial", "commercial proposal")),
    ("spec", ("material requisition", " mr ", "mr_", "-mr-", "spc-", " spec")),
    # Supporting documents: recognised so they cost no LLM call, but not
    # extractable this phase. Phase 3 may promote some of these.
    ("other", ("consumption", "equipment list", "special tools", "spares",
               "power auxiliary", "codes and standards", "documents list",
               "load list", "gas quality", "synchron")),
)


def classify_by_rules(path: str) -> str | None:
    """Return a doc_class, or None when no rule is confident enough."""
    name = os.path.basename(path).lower()
    for doc_class, keywords in _RULES:
        if any(k in name for k in keywords):
            return doc_class
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_classify_rules.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add procurement/classify.py tests/test_classify_rules.py
git commit -m "feat: filename classification rules for vendor documents"
```

---

### Task 2: LLM classification fallback

**Files:**
- Modify: `procurement/classify.py`
- Create: `shared/llm/prompts/doc_class_v1.txt`
- Test: `tests/test_classify_llm.py`

**Interfaces:**
- Consumes: `classify_by_rules(path)`, `DOC_CLASSES` (Task 1)
- Produces: `classify_document(path, client, text_head="") -> tuple[str, str]` returning `(doc_class, classified_by)` where `classified_by` is `"rule"` or `"llm"`. Also `CLASSIFY_PROMPT_VERSION = "doc_class_v1"`.

The LLM is asked only for documents the rules declined. It sees the filename plus the first ~500 characters of extracted text. Any answer outside `DOC_CLASSES` degrades to `"other"` rather than raising — a misclassification must not abort a run.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_classify_llm.py
from procurement.classify import (classify_by_rules, classify_document,
                                  CLASSIFY_PROMPT_VERSION)
from shared.llm.mock_client import MockLLMClient


def test_rules_win_and_the_model_is_never_called():
    client = MockLLMClient(response={"doc_class": "drawing"})
    assert classify_document("/p/vendors/K/BOM.pdf", client) == ("bom", "rule")
    assert client.calls == []


def test_model_decides_when_rules_defer():
    client = MockLLMClient(response={"doc_class": "quotation"})
    result = classify_document("/p/vendors/ADPOWER/ADP-13158-2024-935.pdf", client,
                               text_head="Commercial offer, total price USD 1,200,000")
    assert result == ("quotation", "llm")
    assert len(client.calls) == 1


def test_filename_and_text_head_both_reach_the_model():
    client = MockLLMClient(response={"doc_class": "other"})
    classify_document("/p/vendors/M/HSD 230.pdf", client, text_head="Heat rate curve")
    sent = client.calls[0]["context_text"]
    assert "HSD 230.pdf" in sent
    assert "Heat rate curve" in sent


def test_text_head_is_truncated():
    client = MockLLMClient(response={"doc_class": "other"})
    classify_document("/p/vendors/M/HSD 230.pdf", client, text_head="x" * 5000)
    assert len(client.calls[0]["context_text"]) < 1500


def test_unknown_model_answer_degrades_to_other():
    client = MockLLMClient(response={"doc_class": "invoice"})
    assert classify_document("/p/vendors/M/HSD 230.pdf", client) == ("other", "llm")


def test_model_failure_degrades_to_other_without_raising():
    class Boom:
        supports_vision = True

        def __init__(self):
            self.calls = []

        def classify_structure(self, *a, **k):
            raise RuntimeError("provider down")

    assert classify_document("/p/vendors/M/HSD 230.pdf", Boom()) == ("other", "llm")


def test_prompt_version_is_exposed():
    assert CLASSIFY_PROMPT_VERSION == "doc_class_v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_classify_llm.py -v`
Expected: FAIL — `classify_document` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `shared/llm/prompts/doc_class_v1.txt`:

```
You are classifying one document from a competitive tender package. Using the
filename and the opening text, return exactly one doc_class from this list:

  spec       - the client's requirement specification / material requisition (MR)
  quotation  - a vendor's commercial offer carrying prices
  datasheet  - a technical datasheet stating equipment parameters and ratings
  deviation  - a vendor deviation / exception form against the specification
  bom        - a bill of materials or equipment list
  drawing    - any drawing: layout, P&ID, single line, nameplate, arrangement
  mom        - minutes of meeting or a clarification record
  other      - anything else, including supporting lists and correspondence

Choose on evidence in the filename and text. If the document does not clearly
belong to one of the first seven, return "other". Do not guess a specific class
to be helpful.
```

Then append to `procurement/classify.py`:

```python
from pathlib import Path
from pydantic import BaseModel

CLASSIFY_PROMPT_VERSION = "doc_class_v1"
_CLASSIFY_PROMPT = (Path(__file__).parents[1] / "shared" / "llm" / "prompts"
                    / "doc_class_v1.txt")
_TEXT_HEAD_CHARS = 500


class _DocClass(BaseModel):
    doc_class: str = "other"


def classify_document(path: str, client, text_head: str = "") -> tuple[str, str]:
    """Return (doc_class, classified_by). The model is consulted only when the
    filename rules decline, and never gets to abort a run: any failure or
    unrecognised answer degrades to "other"."""
    ruled = classify_by_rules(path)
    if ruled is not None:
        return ruled, "rule"

    context = (f"Filename: {os.path.basename(path)}\n\n"
               f"Opening text:\n{text_head[:_TEXT_HEAD_CHARS]}")
    try:
        prompt = _CLASSIFY_PROMPT.read_text(encoding="utf-8")
        result = _DocClass.model_validate(
            client.classify_structure(prompt, _DocClass, context))
        answer = result.doc_class
    except Exception:
        return "other", "llm"
    return (answer if answer in DOC_CLASSES else "other"), "llm"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_classify_llm.py tests/test_classify_rules.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add procurement/classify.py shared/llm/prompts/doc_class_v1.txt tests/test_classify_llm.py
git commit -m "feat: LLM classification fallback for undecidable filenames"
```

---

### Task 3: Revision parsing and supersession

**Files:**
- Create: `procurement/revisions.py`
- Test: `tests/test_revisions.py`

**Interfaces:**
- Consumes: `DocumentRecord` from `procurement.store.models`
- Produces: `parse_revision(filename: str) -> str | None`, `normalised_base(filename: str) -> str`, `resolve_supersession(docs: list[DocumentRecord]) -> list[DocumentRecord]` — returns new records with `revision_label`, `supersedes` and `superseded_by` populated.

Two documents supersede one another only when they share a **normalised base name** (revision markers, extension and leading index numbers stripped) **and** the same vendor. Higher revision wins; an explicit "superseded" marker in the filename always loses regardless of revision.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_revisions.py
from procurement.revisions import parse_revision, normalised_base, resolve_supersession
from procurement.store.models import DocumentRecord


def _doc(doc_id, path, vendor="ADPOWER"):
    return DocumentRecord(doc_id=doc_id, path=path, vendor=vendor,
                          content_sha256="0" * 64)


def test_parse_revision_forms():
    assert parse_revision("ADP-13158-2024-935(Rev1).pdf") == "1"
    assert parse_revision("Techno Commercial proposal AESL-GTC-60808 -REV00.pdf") == "0"
    assert parse_revision("Comparative Statement (CS) - Gas Generators [Rev.2].xlsx") == "2"
    assert parse_revision("GDX-P-26-072 REV-01 EA T-00935.docx") == "1"
    assert parse_revision("Some Drawing Rev A.pdf") == "A"
    assert parse_revision("ADP-13158-2024-935.pdf") is None


def test_normalised_base_strips_revision_index_and_extension():
    assert (normalised_base("ADP-13158-2024-935(Rev1).pdf")
            == normalised_base("ADP-13158-2024-935.pdf"))
    assert (normalised_base("01 DataSheet Gas Generator.pdf")
            == normalised_base("DataSheet Gas Generator.pdf"))


def test_higher_revision_supersedes_lower():
    docs = resolve_supersession([
        _doc("d1", "vendors/ADPOWER/ADP-13158-2024-935.pdf"),
        _doc("d2", "vendors/ADPOWER/ADP-13158-2024-935(Rev1).pdf"),
    ])
    by_id = {d.doc_id: d for d in docs}
    assert by_id["d1"].superseded_by == "d2"
    assert by_id["d2"].supersedes == "d1"
    assert by_id["d2"].superseded_by is None
    assert by_id["d2"].revision_label == "1"


def test_explicit_superseded_marker_loses_regardless_of_revision():
    docs = resolve_supersession([
        _doc("s1", "requirements/ADN-AEC-ME-SPC-026 MR Gas Genset Superseded with MOM 20241111.pdf", vendor=None),
        _doc("s2", "requirements/ADN-AEC-ME-SPC-026 MR Gas Genset copy.pdf", vendor=None),
    ])
    by_id = {d.doc_id: d for d in docs}
    assert by_id["s1"].superseded_by == "s2"
    assert by_id["s2"].superseded_by is None


def test_different_vendors_never_supersede_each_other():
    docs = resolve_supersession([
        _doc("k1", "vendors/KERUI/Quotation.pdf", vendor="KERUI"),
        _doc("m1", "vendors/MKON/Quotation.pdf", vendor="MKON"),
    ])
    assert all(d.superseded_by is None for d in docs)


def test_unrelated_documents_are_untouched():
    docs = resolve_supersession([
        _doc("a", "vendors/KERUI/BOM.pdf", vendor="KERUI"),
        _doc("b", "vendors/KERUI/Quotation.pdf", vendor="KERUI"),
    ])
    assert all(d.superseded_by is None and d.supersedes is None for d in docs)


def test_three_revisions_chain_to_the_newest_only():
    docs = resolve_supersession([
        _doc("v0", "vendors/A/Doc.pdf", vendor="A"),
        _doc("v1", "vendors/A/Doc Rev1.pdf", vendor="A"),
        _doc("v2", "vendors/A/Doc Rev2.pdf", vendor="A"),
    ])
    by_id = {d.doc_id: d for d in docs}
    assert by_id["v0"].superseded_by == "v2"
    assert by_id["v1"].superseded_by == "v2"
    assert by_id["v2"].superseded_by is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_revisions.py -v`
Expected: FAIL — `procurement.revisions` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/revisions.py
"""Revision-label parsing and supersession resolution.

Real tender packages carry several revisions of the same document
(`…-935.pdf` and `…-935(Rev1).pdf`) and sometimes say so outright
("Superseded with MOM 20241111"). Extracting an obsolete revision would
put withdrawn numbers into the comparison, so lineage is resolved before
anything is extracted.
"""
import os
import re

from procurement.store.models import DocumentRecord

_REV = re.compile(r"rev[\s._-]*([0-9]+|[a-z])\b", re.IGNORECASE)
_SUPERSEDED = re.compile(r"supersede", re.IGNORECASE)
_LEADING_INDEX = re.compile(r"^\s*\d{1,2}[\s._-]+")
_REV_CHUNK = re.compile(r"[\(\[]?\s*rev[\s._-]*(?:[0-9]+|[a-z])\s*[\)\]]?", re.IGNORECASE)
_NOISE = re.compile(r"[^a-z0-9]+")


def parse_revision(filename: str) -> str | None:
    """Return the revision label as an upper-case string, or None."""
    match = _REV.search(os.path.basename(filename))
    if match is None:
        return None
    label = match.group(1).upper()
    return str(int(label)) if label.isdigit() else label


def normalised_base(filename: str) -> str:
    """Identity of a document across its revisions: extension, revision
    marker, leading index number and punctuation removed."""
    name = os.path.splitext(os.path.basename(filename))[0]
    name = _LEADING_INDEX.sub("", name)
    name = _REV_CHUNK.sub("", name)
    name = _SUPERSEDED.sub("", name)
    return _NOISE.sub("", name.lower())


def _rank(doc: DocumentRecord) -> tuple[int, int, str]:
    """Higher sorts newer. An explicit 'superseded' marker always sorts oldest."""
    name = os.path.basename(doc.path)
    if _SUPERSEDED.search(name):
        return (-1, 0, "")
    label = parse_revision(name)
    if label is None:
        return (0, 0, "")
    return (1, int(label), "") if label.isdigit() else (1, 0, label)


def resolve_supersession(docs: list[DocumentRecord]) -> list[DocumentRecord]:
    """Populate revision_label / supersedes / superseded_by across a document set."""
    out = [d.model_copy() for d in docs]
    for doc in out:
        doc.revision_label = parse_revision(os.path.basename(doc.path))

    groups: dict[tuple[str | None, str], list[DocumentRecord]] = {}
    for doc in out:
        groups.setdefault((doc.vendor, normalised_base(doc.path)), []).append(doc)

    for members in groups.values():
        if len(members) < 2:
            continue
        newest = max(members, key=_rank)
        for doc in members:
            if doc is newest:
                continue
            doc.superseded_by = newest.doc_id
        newest.supersedes = next(
            (d.doc_id for d in members if d is not newest), None)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_revisions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add procurement/revisions.py tests/test_revisions.py
git commit -m "feat: revision parsing and supersession resolution"
```

---

### Task 4: Fact and deviation records

**Files:**
- Modify: `procurement/store/models.py`
- Test: `tests/test_store_fact_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `FactRecord`, `DeviationRecord`, and the id helpers `fact_id_for(doc_id, parameter) -> str` and `deviation_id_for(doc_id, clause_ref, statement) -> str`.

**Why ids matter here:** `overrides.py` addresses list members by looking for `fact_id`, `req_id`, `doc_id` or `clause_ref` — an index-addressed override is rejected outright, because list order is not stable across re-extractions. So a fact's id must be derived from content that survives re-extraction: the document it came from plus the parameter it describes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store_fact_models.py
from procurement.store.models import (FactRecord, DeviationRecord,
                                      fact_id_for, deviation_id_for)


def test_fact_record_defaults():
    f = FactRecord(fact_id="f-1", parameter="h2s_tolerance", doc_id="d1")
    assert f.value is None and f.unit is None and f.verbatim is None


def test_fact_id_is_stable_across_re_extraction():
    a = fact_id_for("d1", "h2s_tolerance")
    assert a == fact_id_for("d1", "h2s_tolerance")
    assert a.startswith("f-")


def test_fact_id_varies_by_document_and_parameter():
    assert fact_id_for("d1", "h2s") != fact_id_for("d2", "h2s")
    assert fact_id_for("d1", "h2s") != fact_id_for("d1", "kw_rating")


def test_fact_id_ignores_parameter_case_and_padding():
    assert fact_id_for("d1", "  H2S_Tolerance ") == fact_id_for("d1", "h2s_tolerance")


def test_deviation_record_defaults_to_noted():
    d = DeviationRecord(deviation_id="v-1", statement="Uses 60 Hz", doc_id="d1")
    assert d.disposition == "noted"
    assert d.clause_ref is None


def test_deviation_id_is_stable():
    a = deviation_id_for("d1", "4.2.7", "Vendor proposes an alternative")
    assert a == deviation_id_for("d1", "4.2.7", "Vendor proposes an alternative")
    assert a.startswith("v-")


def test_deviation_id_falls_back_to_the_statement_when_no_clause():
    a = deviation_id_for("d1", None, "Vendor proposes an alternative")
    b = deviation_id_for("d1", None, "A different statement entirely")
    assert a != b


def test_records_round_trip_through_json():
    f = FactRecord(fact_id="f-1", parameter="kw", value=550.0, unit="kW",
                   verbatim="550 kW continuous", doc_id="d1")
    assert FactRecord.model_validate(f.model_dump()).value == 550.0
    d = DeviationRecord(deviation_id="v-1", clause_ref="4.2", statement="s",
                        disposition="deviate", doc_id="d1")
    assert DeviationRecord.model_validate(d.model_dump()).disposition == "deviate"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_store_fact_models.py -v`
Expected: FAIL — `FactRecord` cannot be imported.

- [ ] **Step 3: Write minimal implementation**

Append to `procurement/store/models.py` (keep the existing `import` block, adding `hashlib`):

```python
import hashlib


def fact_id_for(doc_id: str, parameter: str) -> str:
    """Stable across re-extraction: same document + same parameter -> same id.
    That is what lets a human override on technical[<id>].value survive a
    re-run, since list order is not stable and index paths are forbidden."""
    key = f"{doc_id}:{parameter.strip().lower()}"
    return "f-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


def deviation_id_for(doc_id: str, clause_ref: str | None, statement: str) -> str:
    key = f"{doc_id}:{(clause_ref or statement).strip().lower()}"
    return "v-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


class FactRecord(BaseModel):
    """One technical parameter as stated by a vendor document. Values are kept
    exactly as written, with the unit alongside — conversion and comparison
    happen in phase 3, in Python, never in the model."""
    fact_id: str
    parameter: str
    value: str | float | None = None
    unit: str | None = None
    verbatim: str | None = None      # the source sentence, for provenance
    doc_id: str


class DeviationRecord(BaseModel):
    """One entry from a vendor deviation form."""
    deviation_id: str
    clause_ref: str | None = None
    statement: str
    disposition: str = "noted"       # comply | deviate | noted
    doc_id: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_store_fact_models.py tests/test_store_models.py -v`
Expected: PASS — the existing store-model tests must still pass.

- [ ] **Step 5: Commit**

```bash
git add procurement/store/models.py tests/test_store_fact_models.py
git commit -m "feat: FactRecord and DeviationRecord with stable ids"
```

---

### Task 5: Technical facts extractor

**Files:**
- Create: `procurement/extract_tech.py`
- Create: `shared/llm/prompts/tech_facts_v1.txt`
- Test: `tests/test_extract_tech.py`

**Interfaces:**
- Consumes: `FactRecord`, `fact_id_for` (Task 4); `loaders.read_text`
- Produces: `extract_tech_facts(doc_id, path, client, pdf_fallback=None, parameters=None) -> tuple[list[FactRecord], str]` returning `(facts, status)` where status is `"ok"` or `"failed"`. Also `TECH_PROMPT_VERSION = "tech_facts_v1"`.

`parameters` is an optional vocabulary hint — phase 3 will pass the requirement parameter names so the datasheet pass targets what will actually be checked. This phase always passes `None`; the argument exists so phase 3 needs no signature change.

Follow the failure convention already in `procurement/extract.py`: catch broadly and return a failed status rather than raising, so one unreadable datasheet cannot abort a run.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extract_tech.py
from procurement.extract_tech import extract_tech_facts, TECH_PROMPT_VERSION
from shared.llm.mock_client import MockLLMClient


def _txt(tmp_path, body="Continuous rating 550 kW. H2S tolerance 50 ppm."):
    p = tmp_path / "datasheet.txt"
    p.write_text(body, encoding="utf-8")
    return str(p)


def _client():
    return MockLLMClient(response={"facts": [
        {"parameter": "continuous_rating", "value": 550.0, "unit": "kW",
         "verbatim": "Continuous rating 550 kW"},
        {"parameter": "h2s_tolerance", "value": 50.0, "unit": "ppm",
         "verbatim": "H2S tolerance 50 ppm"},
    ]})


def test_extracts_facts_with_stable_ids_and_provenance(tmp_path):
    facts, status = extract_tech_facts("d1", _txt(tmp_path), _client())
    assert status == "ok"
    assert [f.parameter for f in facts] == ["continuous_rating", "h2s_tolerance"]
    assert all(f.doc_id == "d1" for f in facts)
    assert all(f.fact_id.startswith("f-") for f in facts)
    assert facts[0].unit == "kW" and facts[0].value == 550.0


def test_ids_are_reproducible_across_runs(tmp_path):
    first, _ = extract_tech_facts("d1", _txt(tmp_path), _client())
    second, _ = extract_tech_facts("d1", _txt(tmp_path), _client())
    assert [f.fact_id for f in first] == [f.fact_id for f in second]


def test_facts_without_a_parameter_name_are_dropped(tmp_path):
    client = MockLLMClient(response={"facts": [
        {"parameter": "", "value": 1.0},
        {"parameter": "kw", "value": 550.0},
    ]})
    facts, status = extract_tech_facts("d1", _txt(tmp_path), client)
    assert status == "ok"
    assert [f.parameter for f in facts] == ["kw"]


def test_a_missing_value_is_kept_as_none_not_zero(tmp_path):
    client = MockLLMClient(response={"facts": [{"parameter": "h2s", "unit": "ppm"}]})
    facts, _ = extract_tech_facts("d1", _txt(tmp_path), client)
    assert facts[0].value is None


def test_empty_result_is_ok_not_failed(tmp_path):
    facts, status = extract_tech_facts("d1", _txt(tmp_path),
                                       MockLLMClient(response={"facts": []}))
    assert facts == [] and status == "ok"


def test_extraction_error_returns_failed_without_raising(tmp_path):
    class Boom:
        supports_vision = True

        def __init__(self):
            self.calls = []

        def classify_structure(self, *a, **k):
            raise RuntimeError("provider down")

    facts, status = extract_tech_facts("d1", _txt(tmp_path), Boom())
    assert facts == [] and status == "failed"


def test_parameter_vocabulary_is_passed_to_the_model(tmp_path):
    client = _client()
    extract_tech_facts("d1", _txt(tmp_path), client,
                       parameters=["h2s_tolerance", "ambient_temp"])
    sent = client.calls[0]["context_text"]
    assert "h2s_tolerance" in sent and "ambient_temp" in sent


def test_prompt_version_is_exposed():
    assert TECH_PROMPT_VERSION == "tech_facts_v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_extract_tech.py -v`
Expected: FAIL — `procurement.extract_tech` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `shared/llm/prompts/tech_facts_v1.txt`:

```
You are extracting technical parameters from one vendor document in a
competitive tender for gas generator sets.

Return a list of facts. For each fact give:
  parameter - a short snake_case name for what is being stated
              (e.g. continuous_rating, h2s_tolerance, ambient_design_temp,
              frequency, voltage, fuel_gas_pressure, nox_emission)
  value     - the number or short string exactly as printed. Do not convert
              units, do not compute, do not round.
  unit      - the unit exactly as printed (kW, ppm, degC, bar, Hz, V)
  verbatim  - the source phrase the value came from

Rules:
- Copy values exactly as stated. Never convert between units or totals.
- If a parameter is stated without a value, omit the fact entirely rather
  than inventing a number or writing zero.
- Do not judge whether any value is acceptable or compliant. Only report.
- Prefer parameters that a buyer would check against a specification.
```

Then `procurement/extract_tech.py`:

```python
"""Datasheet -> technical FactRecords.

The model reports what a document states; it never converts units, totals
anything, or judges compliance. Comparison happens in phase 3, in Python.
"""
from pathlib import Path
from pydantic import BaseModel

from procurement.loaders import read_text
from procurement.store.models import FactRecord, fact_id_for

TECH_PROMPT_VERSION = "tech_facts_v1"
_PROMPT = (Path(__file__).parents[1] / "shared" / "llm" / "prompts"
           / "tech_facts_v1.txt")


class _TechFact(BaseModel):
    parameter: str = ""
    value: str | float | None = None
    unit: str | None = None
    verbatim: str | None = None


class _TechFactList(BaseModel):
    facts: list[_TechFact] = []


def extract_tech_facts(doc_id: str, path: str, client, pdf_fallback=None,
                       parameters: list[str] | None = None
                       ) -> tuple[list[FactRecord], str]:
    """Return (facts, status). Status is "ok" or "failed"; a failure never
    raises, so one unreadable datasheet cannot abort a run."""
    try:
        text = read_text(path, llm_fallback=pdf_fallback)
        prompt = _PROMPT.read_text(encoding="utf-8")
        if parameters:
            prompt += ("\n\nThe buyer will check these parameters. Report them "
                       "when the document states them:\n"
                       + "\n".join(f"- {p}" for p in parameters))
        parsed = _TechFactList.model_validate(
            client.classify_structure(prompt, _TechFactList, text))
    except Exception:
        return [], "failed"

    out: list[FactRecord] = []
    for fact in parsed.facts:
        name = fact.parameter.strip()
        if not name:
            continue          # a fact with no parameter name cannot be checked
        out.append(FactRecord(
            fact_id=fact_id_for(doc_id, name),
            parameter=name,
            value=fact.value,
            unit=fact.unit,
            verbatim=fact.verbatim,
            doc_id=doc_id,
        ))
    return out, "ok"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_extract_tech.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add procurement/extract_tech.py shared/llm/prompts/tech_facts_v1.txt tests/test_extract_tech.py
git commit -m "feat: technical facts extractor for datasheets"
```

---

### Task 6: Deviation form extractor

**Files:**
- Create: `procurement/extract_deviation.py`
- Create: `shared/llm/prompts/deviation_v1.txt`
- Test: `tests/test_extract_deviation.py`

**Interfaces:**
- Consumes: `DeviationRecord`, `deviation_id_for` (Task 4); `loaders.read_text`
- Produces: `extract_deviations(doc_id, path, client, pdf_fallback=None) -> tuple[list[DeviationRecord], str]`. Also `DEVIATION_PROMPT_VERSION = "deviation_v1"`.

`disposition` is constrained to `comply | deviate | noted`; anything else the model returns degrades to `noted`, which is the neutral value — never to `comply`, since silently upgrading an unrecognised answer to compliance is the one failure mode that would corrupt an award decision.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extract_deviation.py
from procurement.extract_deviation import extract_deviations, DEVIATION_PROMPT_VERSION
from shared.llm.mock_client import MockLLMClient


def _txt(tmp_path):
    p = tmp_path / "Attachment-2 Vendor Deviation Form.txt"
    p.write_text("Clause 4.2.7 - vendor proposes 60 Hz. Deviation.", encoding="utf-8")
    return str(p)


def _client():
    return MockLLMClient(response={"deviations": [
        {"clause_ref": "4.2.7", "statement": "Vendor proposes 60 Hz",
         "disposition": "deviate"},
        {"clause_ref": "5.1", "statement": "Complies fully", "disposition": "comply"},
    ]})


def test_extracts_deviations_with_ids_and_provenance(tmp_path):
    items, status = extract_deviations("d1", _txt(tmp_path), _client())
    assert status == "ok"
    assert [i.clause_ref for i in items] == ["4.2.7", "5.1"]
    assert [i.disposition for i in items] == ["deviate", "comply"]
    assert all(i.doc_id == "d1" and i.deviation_id.startswith("v-") for i in items)


def test_ids_are_reproducible_across_runs(tmp_path):
    first, _ = extract_deviations("d1", _txt(tmp_path), _client())
    second, _ = extract_deviations("d1", _txt(tmp_path), _client())
    assert [i.deviation_id for i in first] == [i.deviation_id for i in second]


def test_unknown_disposition_degrades_to_noted_never_to_comply(tmp_path):
    client = MockLLMClient(response={"deviations": [
        {"clause_ref": "9.9", "statement": "Unclear", "disposition": "probably fine"},
    ]})
    items, _ = extract_deviations("d1", _txt(tmp_path), client)
    assert items[0].disposition == "noted"


def test_entries_without_a_statement_are_dropped(tmp_path):
    client = MockLLMClient(response={"deviations": [
        {"clause_ref": "1.1", "statement": "   "},
        {"clause_ref": "1.2", "statement": "Real deviation"},
    ]})
    items, _ = extract_deviations("d1", _txt(tmp_path), client)
    assert [i.clause_ref for i in items] == ["1.2"]


def test_empty_result_is_ok_not_failed(tmp_path):
    items, status = extract_deviations("d1", _txt(tmp_path),
                                       MockLLMClient(response={"deviations": []}))
    assert items == [] and status == "ok"


def test_extraction_error_returns_failed_without_raising(tmp_path):
    class Boom:
        supports_vision = True

        def __init__(self):
            self.calls = []

        def classify_structure(self, *a, **k):
            raise RuntimeError("provider down")

    items, status = extract_deviations("d1", _txt(tmp_path), Boom())
    assert items == [] and status == "failed"


def test_prompt_version_is_exposed():
    assert DEVIATION_PROMPT_VERSION == "deviation_v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_extract_deviation.py -v`
Expected: FAIL — `procurement.extract_deviation` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `shared/llm/prompts/deviation_v1.txt`:

```
You are extracting entries from a vendor's deviation / exception form in a
competitive tender.

Return a list of deviations. For each entry give:
  clause_ref  - the specification clause the entry refers to, exactly as
                printed (e.g. "4.2.7"). Omit if the form does not cite one.
  statement   - what the vendor says about that clause, in their own words
  disposition - one of exactly: comply | deviate | noted

Rules:
- Use "comply" only when the vendor explicitly states compliance.
- Use "deviate" when the vendor takes exception, proposes an alternative,
  or states a limitation.
- Use "noted" when the entry is a remark, a clarification, or unclear.
- Never infer compliance from silence. An entry you cannot read is "noted".
- Copy clause references exactly. Do not renumber or normalise them.
```

Then `procurement/extract_deviation.py`:

```python
"""Vendor deviation form -> DeviationRecords.

`disposition` degrades to "noted" on anything unrecognised — never to
"comply". Silently upgrading an unreadable entry to compliance is the one
failure mode here that could corrupt an award decision.
"""
from pathlib import Path
from pydantic import BaseModel

from procurement.loaders import read_text
from procurement.store.models import DeviationRecord, deviation_id_for

DEVIATION_PROMPT_VERSION = "deviation_v1"
_PROMPT = (Path(__file__).parents[1] / "shared" / "llm" / "prompts"
           / "deviation_v1.txt")
_DISPOSITIONS = ("comply", "deviate", "noted")


class _Deviation(BaseModel):
    clause_ref: str | None = None
    statement: str = ""
    disposition: str = "noted"


class _DeviationList(BaseModel):
    deviations: list[_Deviation] = []


def extract_deviations(doc_id: str, path: str, client, pdf_fallback=None
                       ) -> tuple[list[DeviationRecord], str]:
    """Return (deviations, status). Status is "ok" or "failed"; a failure
    never raises."""
    try:
        text = read_text(path, llm_fallback=pdf_fallback)
        prompt = _PROMPT.read_text(encoding="utf-8")
        parsed = _DeviationList.model_validate(
            client.classify_structure(prompt, _DeviationList, text))
    except Exception:
        return [], "failed"

    out: list[DeviationRecord] = []
    for item in parsed.deviations:
        statement = item.statement.strip()
        if not statement:
            continue
        disposition = (item.disposition or "").strip().lower()
        out.append(DeviationRecord(
            deviation_id=deviation_id_for(doc_id, item.clause_ref, statement),
            clause_ref=item.clause_ref,
            statement=statement,
            disposition=disposition if disposition in _DISPOSITIONS else "noted",
            doc_id=doc_id,
        ))
    return out, "ok"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_extract_deviation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add procurement/extract_deviation.py shared/llm/prompts/deviation_v1.txt tests/test_extract_deviation.py
git commit -m "feat: deviation form extractor"
```

---

### Task 7: Route the pipeline by document class

**Files:**
- Modify: `procurement/pipeline.py`
- Test: `tests/test_pipeline_routing.py`

**Interfaces:**
- Consumes: everything from Tasks 1–6
- Produces: `PROMPT_VERSION_BY_CLASS: dict[str, str]`; `run_ingestion` unchanged in signature, now classifying and routing.

**Behaviour to implement, in `run_ingestion`'s document loop:**

1. **Classify every document** before extracting anything. Set `doc_class` and `classified_by` on each `DocumentRecord`. Feed the classifier the first 500 characters of text only for documents the rules declined — reading text costs nothing for a `.txt`, but avoid it when a rule already decided.
2. **Resolve supersession** across the freshly inventoried documents via `revisions.resolve_supersession`, before routing.
3. **Route by class.** `quotation` → `extract_bid` (unchanged); `datasheet` → `extract_tech_facts`; `deviation` → `extract_deviations`; everything else → `extraction_status = "skipped"` with a note naming the class.
4. **A superseded document is never extracted** — status `"skipped"`, note `"superseded by <doc_id>"`.
5. **The cache key uses the prompt version for that document's class**, via `PROMPT_VERSION_BY_CLASS`. A datasheet's cache must not be invalidated by a change to the quotation prompt.
6. **Facts accumulate across documents but are replaced per document.** A vendor with three datasheets holds facts from all three. Re-extracting one datasheet drops only that document's facts (`doc_id` match) and appends the fresh ones — the other two are preserved untouched.
7. **`quote_select.pick_quote` is no longer the router.** Classification decides what is a quotation. Keep `pick_quote` to choose *which* quotation when a vendor has several, so ADPOWER's two revisions do not both get extracted.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_routing.py
import io
import zipfile
from procurement.project import create_project, unpack_vendor_zip
from procurement.pipeline import run_ingestion, PROMPT_VERSION_BY_CLASS
from procurement.store import snapshots
from shared.llm.mock_client import MockLLMClient


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, d in entries.items():
            zf.writestr(n, d)
    return buf.getvalue()


def _project(tmp_path):
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    z = tmp_path / "v.zip"
    z.write_bytes(_zip({
        "KERUI/Quotation of Gas Generator.txt": b"base price 1000",
        "KERUI/01 DataSheet Gas Generator.txt": b"Continuous rating 550 kW",
        "KERUI/02 Detailed Vendor Standard Datasheet.txt": b"H2S tolerance 50 ppm",
        "KERUI/03 Attachment-2 Vendor Deviation Form.txt": b"Clause 4.2.7 deviation",
        "KERUI/15 LAYOUT - KGW550GF-T.txt": b"drawing, not extractable",
        "KERUI/08 Two Years Operation Spares.txt": b"spares list",
    }))
    unpack_vendor_zip(root, "p", str(z))
    return root


class RoutingClient:
    """Returns a shape appropriate to whichever prompt it is handed, and
    records the prompt version used for each call."""
    supports_vision = True

    def __init__(self):
        self.calls = []

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        self.calls.append({"prompt": prompt, "context_text": context_text})
        fields = output_schema.model_fields
        if "facts" in fields:
            return {"facts": [{"parameter": "continuous_rating", "value": 550.0,
                               "unit": "kW", "verbatim": "550 kW"}]}
        if "deviations" in fields:
            return {"deviations": [{"clause_ref": "4.2.7", "statement": "60 Hz",
                                    "disposition": "deviate"}]}
        if "doc_class" in fields:
            return {"doc_class": "other"}
        return {"currency": "USD", "base_price": 1000.0, "freight_included": True}


def test_every_document_is_classified(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    by_name = {d.path.rsplit("/", 1)[-1]: d for d in snapshots.load_documents(root, "p")}
    assert by_name["Quotation of Gas Generator.txt"].doc_class == "quotation"
    assert by_name["01 DataSheet Gas Generator.txt"].doc_class == "datasheet"
    assert by_name["03 Attachment-2 Vendor Deviation Form.txt"].doc_class == "deviation"
    assert by_name["15 LAYOUT - KGW550GF-T.txt"].doc_class == "drawing"
    assert by_name["08 Two Years Operation Spares.txt"].doc_class == "other"
    assert all(d.classified_by == "rule" for d in by_name.values())


def test_datasheets_and_deviations_produce_stored_facts(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    facts = snapshots.load_facts(root, "p", "KERUI")
    assert facts.commercial["base_price"] == 1000.0
    assert len(facts.technical) == 2          # one fact from each of two datasheets
    assert len({f["doc_id"] for f in facts.technical}) == 2   # from distinct documents
    assert len({f["fact_id"] for f in facts.technical}) == 2  # ids do not collide
    assert len(facts.deviations) == 1
    assert facts.deviations[0]["disposition"] == "deviate"


def test_drawings_and_other_are_skipped_not_extracted(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    by_name = {d.path.rsplit("/", 1)[-1]: d for d in snapshots.load_documents(root, "p")}
    assert by_name["15 LAYOUT - KGW550GF-T.txt"].extraction_status == "skipped"
    assert by_name["08 Two Years Operation Spares.txt"].extraction_status == "skipped"
    assert "drawing" in by_name["15 LAYOUT - KGW550GF-T.txt"].notes


def test_rerun_with_no_changes_makes_zero_llm_calls(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    second = RoutingClient()
    run_ingestion(root, "p", second)
    assert second.calls == []


def test_touching_one_datasheet_reextracts_only_that_document(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    (tmp_path / "p" / "vendors" / "KERUI"
     / "01 DataSheet Gas Generator.txt").write_bytes(b"Continuous rating 600 kW")
    second = RoutingClient()
    run_ingestion(root, "p", second)
    assert len(second.calls) == 1


def test_facts_from_untouched_datasheets_survive_a_partial_reextraction(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    before = snapshots.load_facts(root, "p", "KERUI")
    touched_doc = next(d.doc_id for d in snapshots.load_documents(root, "p")
                       if d.path.endswith("01 DataSheet Gas Generator.txt"))
    survivor = next(f for f in before.technical if f["doc_id"] != touched_doc)

    (tmp_path / "p" / "vendors" / "KERUI"
     / "01 DataSheet Gas Generator.txt").write_bytes(b"Continuous rating 600 kW")
    run_ingestion(root, "p", RoutingClient())

    after = snapshots.load_facts(root, "p", "KERUI")
    assert len(after.technical) == len(before.technical)   # no duplication, no loss
    # the untouched datasheet's fact is preserved byte-for-byte
    assert survivor in after.technical
    # and the re-extracted document still contributes exactly one fact
    assert len([f for f in after.technical if f["doc_id"] == touched_doc]) == 1


def test_superseded_document_is_not_extracted(tmp_path):
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    z = tmp_path / "v.zip"
    z.write_bytes(_zip({
        "ADPOWER/Quotation ADP-935.txt": b"base price 1000",
        "ADPOWER/Quotation ADP-935(Rev1).txt": b"base price 1100",
    }))
    unpack_vendor_zip(root, "p", str(z))
    run_ingestion(root, "p", RoutingClient())
    docs = {d.path.rsplit("/", 1)[-1]: d for d in snapshots.load_documents(root, "p")}
    old = docs["Quotation ADP-935.txt"]
    new = docs["Quotation ADP-935(Rev1).txt"]
    assert old.superseded_by == new.doc_id
    assert old.extraction_status == "skipped"
    assert "superseded" in old.notes
    assert new.extraction_status == "ok"


def test_prompt_versions_are_per_class():
    assert PROMPT_VERSION_BY_CLASS["quotation"] == "bid_extract_v1"
    assert PROMPT_VERSION_BY_CLASS["datasheet"] == "tech_facts_v1"
    assert PROMPT_VERSION_BY_CLASS["deviation"] == "deviation_v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline_routing.py -v`
Expected: FAIL — `PROMPT_VERSION_BY_CLASS` cannot be imported.

- [ ] **Step 3: Write minimal implementation**

Add to the imports and constants at the top of `procurement/pipeline.py`:

```python
from procurement.classify import (classify_by_rules, classify_document,
                                  CLASSIFY_PROMPT_VERSION)
from procurement.revisions import resolve_supersession
from procurement.extract_tech import extract_tech_facts, TECH_PROMPT_VERSION
from procurement.extract_deviation import extract_deviations, DEVIATION_PROMPT_VERSION
from procurement.loaders import read_text

PROMPT_VERSION = "bid_extract_v1"        # kept: the quotation prompt version
PROMPT_VERSION_BY_CLASS = {
    "quotation": PROMPT_VERSION,
    "datasheet": TECH_PROMPT_VERSION,
    "deviation": DEVIATION_PROMPT_VERSION,
}
_EXTRACTABLE = tuple(PROMPT_VERSION_BY_CLASS)
```

Replace the body of `run_ingestion`'s document loop. The classification and lineage passes run before the extraction loop; the loop then routes:

```python
    prior_docs = {d.doc_id: d for d in snapshots.load_documents(root, slug)}
    fresh_docs = inventory_documents(root, slug)
    pdir = layout.project_dir(root, slug)

    # Pass 1: classify. Rules decide most of it for free; text is read only
    # for the leftovers the rules declined.
    for doc in fresh_docs:
        prior = prior_docs.get(doc.doc_id)
        if (prior is not None and prior.content_sha256 == doc.content_sha256
                and prior.doc_class != "unclassified"):
            doc.doc_class, doc.classified_by = prior.doc_class, prior.classified_by
            continue
        full = os.path.join(pdir, doc.path)
        head = ""
        if classify_by_rules(full) is None:
            # only the leftovers cost a text read; rules decide the rest free
            try:
                head = read_text(full, llm_fallback=None)[:500]
            except Exception:
                head = ""
        doc.doc_class, doc.classified_by = classify_document(full, client, head)
        events.append_event(root, slug, Event(
            at=_now(), run_id=run_id, actor="pipeline",
            action="document.classified", target=doc.doc_id,
            detail={"doc_class": doc.doc_class, "by": doc.classified_by}))

    # Pass 2: lineage, so an obsolete revision is never extracted.
    fresh_docs = resolve_supersession(fresh_docs)

    # Among several quotations for one vendor, pick_quote still chooses which
    # one carries the commercial terms.
    quote_rel_by_vendor: dict[str, str] = {}
    for vendor in project.vendors:
        candidates = [os.path.join(pdir, d.path) for d in fresh_docs
                      if d.vendor == vendor and d.doc_class == "quotation"
                      and d.superseded_by is None]
        chosen = pick_quote(candidates)
        if chosen:
            quote_rel_by_vendor[vendor] = os.path.relpath(chosen, pdir).replace(os.sep, "/")
```

Then, inside the transaction, replace the per-document branch:

```python
        for doc in fresh_docs:
            prior = prior_docs.get(doc.doc_id)
            expected_version = PROMPT_VERSION_BY_CLASS.get(doc.doc_class)

            skip_reason = None
            if doc.superseded_by is not None:
                skip_reason = f"superseded by {doc.superseded_by}"
            elif doc.doc_class not in _EXTRACTABLE:
                skip_reason = f"{doc.doc_class} documents are not extracted"
            elif (doc.doc_class == "quotation"
                  and quote_rel_by_vendor.get(doc.vendor) != doc.path):
                skip_reason = "not the selected quotation document"

            if skip_reason is not None:
                doc.extraction_status = "skipped"
                doc.notes = skip_reason
                documents.append(doc)
                events.append_event(root, slug, Event(
                    at=_now(), run_id=run_id, actor="pipeline",
                    action="document.skipped", target=doc.doc_id,
                    detail={"reason": skip_reason}))
                continue

            unchanged = (prior is not None
                         and prior.content_sha256 == doc.content_sha256
                         and prior.prompt_version == expected_version
                         and prior.extraction_status == "ok")
            if unchanged and not force:
                documents.append(prior)
                extracted += 1
                events.append_event(root, slug, Event(
                    at=_now(), run_id=run_id, actor="pipeline",
                    action="document.skipped", target=doc.doc_id,
                    detail={"reason": "unchanged"}))
                continue

            full = os.path.join(pdir, doc.path)
            prior_facts = snapshots.load_facts(root, slug, doc.vendor)
            base = prior_facts or VendorFacts(vendor=doc.vendor)
            status = "ok"

            if doc.doc_class == "quotation":
                bid = extract_bid(doc.vendor, [full], client, pdf_fallback=pdf_fallback)
                status = bid.extraction_status
                commercial_dump = bid.model_dump()
                technical = list(base.technical)
                deviations = list(base.deviations)
            elif doc.doc_class == "datasheet":
                facts, status = extract_tech_facts(doc.doc_id, full, client,
                                                   pdf_fallback=pdf_fallback)
                # replace only this document's facts; other datasheets survive
                technical = [f for f in base.technical if f.get("doc_id") != doc.doc_id]
                technical += [f.model_dump() for f in facts]
                commercial_dump = base.commercial
                deviations = list(base.deviations)
            else:   # deviation
                items, status = extract_deviations(doc.doc_id, full, client,
                                                   pdf_fallback=pdf_fallback)
                deviations = [d for d in base.deviations if d.get("doc_id") != doc.doc_id]
                deviations += [d.model_dump() for d in items]
                commercial_dump = base.commercial
                technical = list(base.technical)

            doc.extraction_status = status
            doc.extracted_at = _now()
            doc.extractor = f"llm:{expected_version}"
            doc.prompt_version = expected_version
            documents.append(doc)
            extracted += 1 if status == "ok" else 0
            failed += 0 if status == "ok" else 1

            facts_view = {"commercial": commercial_dump,
                          "technical": technical,
                          "deviations": deviations}
            overrides = reconcile(base.overrides, facts_view)
            resolved = apply_overrides(facts_view, overrides)
            normalized = (normalize_bid(
                VendorBid.model_validate(resolved["commercial"]),
                project.target_currency, project.fx_rates).model_dump()
                if resolved["commercial"] else base.normalized)

            snapshots.save_facts(root, slug, VendorFacts(
                vendor=doc.vendor,
                commercial=resolved["commercial"],
                normalized=normalized,
                technical=resolved["technical"],
                deviations=resolved["deviations"],
                overrides=overrides))

            events.append_event(root, slug, Event(
                at=_now(), run_id=run_id, actor="pipeline",
                action="document.extracted", target=doc.doc_id,
                detail={"status": status, "doc_class": doc.doc_class}))
            for o in overrides:
                if o.conflict:
                    events.append_event(root, slug, Event(
                        at=_now(), run_id=run_id, actor="pipeline",
                        action="override.conflicted", target=doc.doc_id,
                        detail={"field_path": o.field_path}))
```

**Order quotations first within each vendor.** After `resolve_supersession`, sort `fresh_docs` so quotation-class documents are processed before datasheets and deviation forms:

```python
    fresh_docs.sort(key=lambda d: (d.vendor or "", d.doc_class != "quotation", d.path))
```

This matters for override reconciliation. `facts_view["commercial"]` is `None` until a vendor's quotation has been extracted, and since phase 1's fix a field path that does not resolve is flagged `conflict=True`. Processing a datasheet first would therefore raise a spurious conflict on any `commercial.*` override. Extracting the quotation first means `commercial` is populated before anything else reconciles against it.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -v`
Expected: PASS — the new routing tests plus every phase-1 test. `portal/app.py` is untouched; `load_dataset`'s return shape is unchanged.

- [ ] **Step 5: Commit**

```bash
git add procurement/pipeline.py tests/test_pipeline_routing.py
git commit -m "feat: route extraction by document class with lineage resolution"
```

---

## Verification of phase 2 done-criteria

Run from the repo root after Task 7:

```bash
python -m pytest -v
```

Maps to the spec's §11 phase 2 exit criteria — *"every vendor file is classified with lineage resolved; datasheets and deviation forms produce stored facts"*:

| criterion | proving test |
|---|---|
| Every vendor file is classified | `test_every_document_is_classified` |
| Rules decide without an LLM call where they can | `test_rules_win_and_the_model_is_never_called` |
| The model decides only the leftovers | `test_model_decides_when_rules_defer` |
| Lineage resolved; obsolete revisions not extracted | `test_superseded_document_is_not_extracted` |
| Datasheets produce stored technical facts | `test_datasheets_and_deviations_produce_stored_facts` |
| Deviation forms produce stored deviations | `test_datasheets_and_deviations_produce_stored_facts` |
| Drawings recorded but never extracted | `test_drawings_and_other_are_skipped_not_extracted` |
| Incrementality survives the new routing | `test_rerun_with_no_changes_makes_zero_llm_calls`, `test_touching_one_datasheet_reextracts_only_that_document` |
| Partial re-extraction preserves other documents' facts | `test_facts_from_untouched_datasheets_survive_a_partial_reextraction` |
| A misclassification cannot abort a run | `test_model_failure_degrades_to_other_without_raising` |
| An unreadable deviation never reads as compliant | `test_unknown_disposition_degrades_to_noted_never_to_comply` |

## Deliberately out of scope

Carried to phase 3, per spec §11:

- `requirements_v1` / `extract_requirements.py` — the MR is classified `spec` here but not parsed.
- `mom_amend_v1` / `extract_mom.py` — MOMs are classified but their amendments are not applied.
- `units.py` and `compliance.py` — no unit conversion, no verdicts, no requirement × vendor matrix.
- BOM line-item extraction and reconciliation (roadmap §1, unchanged).
- Portal review screens (phase 4).
