# Procurement Comparison Portal (MVP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local single-user Streamlit portal where a buyer creates a project, uploads a requirements doc + vendor folders (ZIP), runs real Anthropic extraction of each vendor's commercial bid, and views a light-normalized side-by-side comparison.

**Architecture:** A focused `procurement/` package (models → project store → loaders → quote picker → LLM extractor → normalizer → comparison → export → pipeline orchestrator) with a thin Streamlit UI in `portal/app.py`. Reuses the existing `shared/llm` provider layer (Anthropic adapter) and `shared/provenance`. All numbers are read by the LLM from quote text; normalization is deterministic and records every adjustment.

**Tech Stack:** Python 3.12, pydantic v2, Streamlit, openpyxl, `pdftotext` CLI (fallback `pypdf`), the `anthropic` SDK behind `shared/llm`.

## Global Constraints

- Python 3.12; pydantic v2 (`model_validate` / `model_dump`).
- Reuse `shared/llm` (`get_client()`, the `LLMClient.classify_structure(prompt, output_schema, context_text) -> dict` method) and `shared/provenance.ProvenanceRef`. Do NOT add a new LLM plumbing path.
- Raw as-stated values are never mutated; normalization produces a separate `NormalizedBid`.
- Every extracted bid carries a `ProvenanceRef` (source document).
- Per-vendor **graceful failure**: a vendor that can't be found/extracted is marked `extraction_status="failed"` with a reason; the run continues.
- LLM-dependent tests run against the **mock adapter** (`MockLLMClient`); live tests `skipif` no `ANTHROPIC_API_KEY`.
- Projects live on the local filesystem under `projects/<slug>/`.
- Vendor ZIP extraction must reject path traversal (zip-slip) and ignore hidden/`~$` files.
- Money comparisons in tests use tolerance `abs(a - b) <= 0.01`.
- TDD: failing test first, watch it fail, minimal implementation, watch it pass, commit.

---

### Task 1: Package scaffolding + data models

**Files:**
- Create: `procurement/__init__.py`
- Create: `procurement/models.py`
- Test: `tests/test_procurement_models.py`

**Interfaces:**
- Consumes: `shared.provenance.ProvenanceRef`.
- Produces (pydantic v2 models in `procurement.models`):
  - `OptionalItem(description: str, qty: float | None = None, unit_price: float | None = None, total: float | None = None, category: str = "other", included_in_base: bool = False)`.
  - `BidExtraction(BaseModel)` — the LLM-facing fields, all defaulted so a failed bid still constructs: `currency: str = ""`, `base_price: float = 0.0`, `vat_included: bool = False`, `vat_rate: float = 0.0`, `freight_amount: float = 0.0`, `freight_included: bool = False`, `discount_pct: float = 0.0`, `optional_items: list[OptionalItem] = []`, `delivery_time: str | None = None`, `delivery_terms: str | None = None`, `payment_terms: str | None = None`, `engine_make: str | None = None`, `notes: str | None = None`.
  - `VendorBid(BidExtraction)` — adds `vendor: str`, `source_document: str | None = None`, `provenance: ProvenanceRef | None = None`, `extraction_status: str = "ok"`.
  - `NormalizationAdjustment(kind: str, description: str, from_value: float, to_value: float, delta: float)`.
  - `NormalizedBid(vendor: str, normalized_currency: str, normalized_total: float | None, adjustments: list[NormalizationAdjustment] = [], extraction_status: str = "ok")`.
  - `ComparisonRow(vendor: str, currency: str, raw_base_price: float | None, normalized_total: float | None, delivery_terms: str | None, delivery_time: str | None, payment_terms: str | None, engine_make: str | None, extraction_status: str)`.
  - `ComparisonTable(target_currency: str, rows: list[ComparisonRow] = [])`.
  - `Project(name: str, slug: str, created_at: str, target_currency: str = "USD", fx_rates: dict[str, float] = {}, vendors: list[str] = [], requirements_file: str | None = None, status: str = "new")`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_procurement_models.py
from shared.provenance import ProvenanceRef
from procurement.models import (
    OptionalItem, BidExtraction, VendorBid, NormalizedBid,
    NormalizationAdjustment, ComparisonRow, ComparisonTable, Project,
)


def test_vendor_bid_composes_extraction_plus_metadata():
    bid = VendorBid(
        vendor="ADPOWER", currency="EUR", base_price=1000.0,
        vat_included=True, vat_rate=0.05, freight_amount=100.0,
        source_document="q.pdf",
        provenance=ProvenanceRef(document_path="q.pdf", extractor="anthropic:v1"),
    )
    assert bid.vendor == "ADPOWER"
    assert bid.base_price == 1000.0
    assert bid.extraction_status == "ok"
    assert VendorBid.model_validate(bid.model_dump()).currency == "EUR"


def test_failed_bid_constructs_with_defaults():
    bid = VendorBid(vendor="AESL", extraction_status="failed", notes="no quote found")
    assert bid.extraction_status == "failed"
    assert bid.base_price == 0.0 and bid.currency == ""


def test_comparison_table_holds_rows():
    t = ComparisonTable(target_currency="USD", rows=[
        ComparisonRow(vendor="A", currency="USD", raw_base_price=100.0,
                      normalized_total=100.0, delivery_terms=None, delivery_time=None,
                      payment_terms=None, engine_make=None, extraction_status="ok")
    ])
    assert t.rows[0].vendor == "A"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_procurement_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'procurement.models'`

- [ ] **Step 3: Write minimal implementation**

Create `procurement/__init__.py` (empty), then:

```python
# procurement/models.py
from pydantic import BaseModel
from shared.provenance import ProvenanceRef


class OptionalItem(BaseModel):
    description: str
    qty: float | None = None
    unit_price: float | None = None
    total: float | None = None
    category: str = "other"
    included_in_base: bool = False


class BidExtraction(BaseModel):
    currency: str = ""
    base_price: float = 0.0
    vat_included: bool = False
    vat_rate: float = 0.0
    freight_amount: float = 0.0
    freight_included: bool = False
    discount_pct: float = 0.0
    optional_items: list[OptionalItem] = []
    delivery_time: str | None = None
    delivery_terms: str | None = None
    payment_terms: str | None = None
    engine_make: str | None = None
    notes: str | None = None


class VendorBid(BidExtraction):
    vendor: str
    source_document: str | None = None
    provenance: ProvenanceRef | None = None
    extraction_status: str = "ok"


class NormalizationAdjustment(BaseModel):
    kind: str
    description: str
    from_value: float
    to_value: float
    delta: float


class NormalizedBid(BaseModel):
    vendor: str
    normalized_currency: str
    normalized_total: float | None
    adjustments: list[NormalizationAdjustment] = []
    extraction_status: str = "ok"


class ComparisonRow(BaseModel):
    vendor: str
    currency: str
    raw_base_price: float | None
    normalized_total: float | None
    delivery_terms: str | None
    delivery_time: str | None
    payment_terms: str | None
    engine_make: str | None
    extraction_status: str


class ComparisonTable(BaseModel):
    target_currency: str
    rows: list[ComparisonRow] = []


class Project(BaseModel):
    name: str
    slug: str
    created_at: str
    target_currency: str = "USD"
    fx_rates: dict[str, float] = {}
    vendors: list[str] = []
    requirements_file: str | None = None
    status: str = "new"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_procurement_models.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add procurement/__init__.py procurement/models.py tests/test_procurement_models.py
git commit -m "feat: procurement domain models"
```

---

### Task 2: Filesystem project store + safe unzip

**Files:**
- Create: `procurement/project.py`
- Test: `tests/test_procurement_project.py`

**Interfaces:**
- Consumes: `Project` (Task 1).
- Produces:
  - `slugify(name: str) -> str` — lowercase, non-alphanumerics → `-`, collapse repeats, strip.
  - `create_project(root: str, name: str, target_currency: str = "USD") -> Project` — makes `root/<slug>/` with `requirements/` and `vendors/` subdirs, writes `project.json`, returns the `Project`.
  - `load_project(root: str, slug: str) -> Project` / `list_projects(root: str) -> list[Project]` / `save_project(root: str, project: Project) -> None`.
  - `unpack_vendor_zip(root: str, slug: str, zip_path: str) -> list[str]` — extracts a ZIP whose top-level subfolders are vendors into `root/<slug>/vendors/`, **rejecting zip-slip** (entries resolving outside the target) and skipping hidden/`~$`/`__MACOSX` entries; returns the vendor names created. Updates and saves the project's `vendors`.
  - `vendor_files(root: str, slug: str, vendor: str) -> list[str]` — the file paths under a vendor folder.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_procurement_project.py
import io
import os
import zipfile
import pytest
from procurement.project import (
    slugify, create_project, load_project, list_projects, unpack_vendor_zip, vendor_files,
)


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_slugify():
    assert slugify("ADNOC Gas P5 !!") == "adnoc-gas-p5"


def test_create_and_load_project(tmp_path):
    p = create_project(str(tmp_path), "Gas Gensets", target_currency="USD")
    assert p.slug == "gas-gensets"
    assert os.path.isdir(tmp_path / "gas-gensets" / "vendors")
    assert load_project(str(tmp_path), "gas-gensets").name == "Gas Gensets"
    assert [x.slug for x in list_projects(str(tmp_path))] == ["gas-gensets"]


def test_unpack_vendor_zip_creates_vendors(tmp_path):
    create_project(str(tmp_path), "Proj")
    zpath = tmp_path / "v.zip"
    zpath.write_bytes(_zip_bytes({
        "KERUI/quote.pdf": b"x",
        "KERUI/bom.pdf": b"x",
        "ADPOWER/ADP-935.pdf": b"x",
        "__MACOSX/junk": b"x",
        "ADPOWER/~$temp.docx": b"x",
    }))
    vendors = unpack_vendor_zip(str(tmp_path), "proj", str(zpath))
    assert sorted(vendors) == ["ADPOWER", "KERUI"]
    assert len(vendor_files(str(tmp_path), "proj", "KERUI")) == 2
    # hidden/temp file skipped
    assert all("~$" not in f for f in vendor_files(str(tmp_path), "proj", "ADPOWER"))
    assert load_project(str(tmp_path), "proj").vendors == sorted(vendors)


def test_unpack_rejects_zip_slip(tmp_path):
    create_project(str(tmp_path), "Proj")
    zpath = tmp_path / "evil.zip"
    zpath.write_bytes(_zip_bytes({"../../evil.txt": b"pwned"}))
    with pytest.raises(ValueError):
        unpack_vendor_zip(str(tmp_path), "proj", str(zpath))
    assert not (tmp_path.parent / "evil.txt").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_procurement_project.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'procurement.project'`

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/project.py
import os
import re
import json
import zipfile
from datetime import datetime, timezone
from procurement.models import Project

_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    return _SLUG.sub("-", name.lower()).strip("-")


def _project_dir(root: str, slug: str) -> str:
    return os.path.join(root, slug)


def save_project(root: str, project: Project) -> None:
    with open(os.path.join(_project_dir(root, project.slug), "project.json"), "w", encoding="utf-8") as fh:
        json.dump(project.model_dump(), fh, indent=2)


def create_project(root: str, name: str, target_currency: str = "USD") -> Project:
    slug = slugify(name)
    pdir = _project_dir(root, slug)
    os.makedirs(os.path.join(pdir, "requirements"), exist_ok=True)
    os.makedirs(os.path.join(pdir, "vendors"), exist_ok=True)
    project = Project(
        name=name, slug=slug,
        created_at=datetime.now(timezone.utc).isoformat(),
        target_currency=target_currency,
    )
    save_project(root, project)
    return project


def load_project(root: str, slug: str) -> Project:
    with open(os.path.join(_project_dir(root, slug), "project.json"), "r", encoding="utf-8") as fh:
        return Project.model_validate(json.load(fh))


def list_projects(root: str) -> list[Project]:
    if not os.path.isdir(root):
        return []
    out = []
    for slug in sorted(os.listdir(root)):
        if os.path.exists(os.path.join(root, slug, "project.json")):
            out.append(load_project(root, slug))
    return out


def _is_skippable(name: str) -> bool:
    parts = name.split("/")
    base = parts[-1]
    return (not base) or name.startswith("__MACOSX") or base.startswith(".") or base.startswith("~$")


def unpack_vendor_zip(root: str, slug: str, zip_path: str) -> list[str]:
    vendors_dir = os.path.realpath(os.path.join(_project_dir(root, slug), "vendors"))
    vendors: set[str] = set()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir() or _is_skippable(name):
                continue
            dest = os.path.realpath(os.path.join(vendors_dir, name))
            if not (dest == vendors_dir or dest.startswith(vendors_dir + os.sep)):
                raise ValueError(f"Unsafe path in archive: {name}")
            top = name.replace("\\", "/").split("/")[0]
            vendors.add(top)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                out.write(src.read())
    project = load_project(root, slug)
    project.vendors = sorted(vendors)
    save_project(root, project)
    return sorted(vendors)


def vendor_files(root: str, slug: str, vendor: str) -> list[str]:
    vdir = os.path.join(_project_dir(root, slug), "vendors", vendor)
    out = []
    for dirpath, _dirs, files in os.walk(vdir):
        for f in files:
            if not (f.startswith(".") or f.startswith("~$")):
                out.append(os.path.join(dirpath, f))
    return sorted(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_procurement_project.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add procurement/project.py tests/test_procurement_project.py
git commit -m "feat: filesystem project store with safe vendor-zip unpack"
```

---

### Task 3: Document text loaders (xlsx + pdf)

**Files:**
- Create: `procurement/loaders.py`
- Test: `tests/test_procurement_loaders.py`
- Modify: `pyproject.toml` (add `pypdf` dependency)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `read_xlsx_text(path: str) -> str` — flatten all sheets to text (`sheet | cell=value` lines) via openpyxl.
  - `read_pdf_text(path: str) -> str` — try the `pdftotext -layout <path> -` CLI (present on this machine); on any failure fall back to `pypdf` page-text extraction.
  - `read_text(path: str) -> str` — dispatch by extension (`.xlsx`→xlsx, `.pdf`→pdf, else read as utf-8 text ignoring errors).

- [ ] **Step 1: Add `pypdf` to `pyproject.toml` dependencies and install**

Add `"pypdf>=4.0"` to `[project].dependencies`, then:
Run: `python -m pip install pypdf`

- [ ] **Step 2: Write the failing test**

```python
# tests/test_procurement_loaders.py
import os
import openpyxl
import pytest
from procurement.loaders import read_xlsx_text, read_text, read_pdf_text


def test_read_xlsx_text(tmp_path):
    path = str(tmp_path / "q.xlsx")
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Offer"
    ws["A1"] = "Base Price"; ws["B1"] = 585000
    wb.save(path)
    text = read_xlsx_text(path)
    assert "Base Price" in text and "585000" in text and "Offer" in text


def test_read_text_dispatches_plain(tmp_path):
    p = tmp_path / "note.txt"; p.write_text("hello quote", encoding="utf-8")
    assert "hello quote" in read_text(str(p))


REAL_PDF = "data/procurement-data/ADPOWER/ADP-13158-2024-935.pdf"


@pytest.mark.skipif(not os.path.exists(REAL_PDF), reason="sample data not present")
def test_read_pdf_text_real(tmp_path):
    text = read_pdf_text(REAL_PDF)
    assert len(text) > 200
    assert "Baudouin" in text or "Generator" in text or "QUOTATION" in text.upper()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_procurement_loaders.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'procurement.loaders'`

- [ ] **Step 4: Write minimal implementation**

```python
# procurement/loaders.py
import os
import shutil
import subprocess
import openpyxl
from openpyxl.utils import get_column_letter


def read_xlsx_text(path: str) -> str:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    lines: list[str] = []
    for ws in wb.worksheets:
        for r, row in enumerate(ws.iter_rows(values_only=True), 1):
            cells = [
                f"{get_column_letter(c)}{r}={v}"
                for c, v in enumerate(row, 1)
                if v is not None
            ]
            if cells:
                lines.append(f"{ws.title} | " + " | ".join(cells))
    return "\n".join(lines)


def read_pdf_text(path: str) -> str:
    exe = shutil.which("pdftotext")
    if exe:
        try:
            out = subprocess.run(
                [exe, "-layout", path, "-"],
                capture_output=True, timeout=120,
            )
            text = out.stdout.decode("utf-8", errors="ignore")
            if text.strip():
                return text
        except Exception:
            pass
    # Fallback: pypdf
    from pypdf import PdfReader
    reader = PdfReader(path)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def read_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xlsx":
        return read_xlsx_text(path)
    if ext == ".pdf":
        return read_pdf_text(path)
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        return fh.read()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_procurement_loaders.py -v`
Expected: PASS (2 passed, 1 passed or skipped depending on sample-data presence)

- [ ] **Step 6: Commit**

```bash
git add procurement/loaders.py tests/test_procurement_loaders.py pyproject.toml
git commit -m "feat: xlsx + pdf text loaders (pdftotext CLI, pypdf fallback)"
```

---

### Task 4: Quotation-document picker

**Files:**
- Create: `procurement/quote_select.py`
- Test: `tests/test_procurement_quote_select.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `pick_quote(files: list[str]) -> str | None` — from a vendor folder's files, choose the quotation document. Scoring by filename keywords (`quotation`, `quote`, `offer`, `proposal`, `techno`, `commercial`) strongly; de-prioritise clearly non-quote docs (`bom`, `datasheet`, `deviation`, `mom`, `spec`, `load list`, `synchron`, `spare`, `tool`, `gas quality`, `single line`, `layout`, `nameplate`, `p&id`, `pid`, `drawing`). Prefer `.pdf`/`.xlsx`. Returns `None` if `files` is empty.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_procurement_quote_select.py
from procurement.quote_select import pick_quote


def test_picks_quotation_over_bom_and_datasheet():
    files = [
        "/v/MKON/Quotation-MKON.pdf",
        "/v/MKON/Attachment-2 Vendor Deviation Form.pdf",
        "/v/MKON/DOD-30201 DataSheet Gas Generator.xlsx",
        "/v/MKON/Attachment-7 Load List.pdf",
    ]
    assert pick_quote(files) == "/v/MKON/Quotation-MKON.pdf"


def test_prefers_techno_commercial_proposal():
    files = [
        "/v/AESL/Techno Commercial proposal AESL-GTC-60808 - REV00.pdf",
    ]
    assert pick_quote(files).endswith("REV00.pdf")


def test_returns_none_for_empty():
    assert pick_quote([]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_procurement_quote_select.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/quote_select.py
import os

_POSITIVE = ("quotation", "quote", "offer", "proposal", "techno", "commercial")
_NEGATIVE = (
    "bom", "datasheet", "data sheet", "deviation", "mom", "spec", "load list",
    "synchron", "spare", "tool", "gas quality", "single line", "layout",
    "nameplate", "p&id", "pid", "drawing", "consumption", "power auxiliary",
    "codes and standards", "documents list", "outline",
)


def _score(path: str) -> int:
    name = os.path.basename(path).lower()
    ext = os.path.splitext(name)[1]
    score = 0
    if any(k in name for k in _POSITIVE):
        score += 100
    if any(k in name for k in _NEGATIVE):
        score -= 50
    if ext in (".pdf", ".xlsx"):
        score += 5
    return score


def pick_quote(files: list[str]) -> str | None:
    if not files:
        return None
    return max(files, key=lambda f: (_score(f), -len(os.path.basename(f))))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_procurement_quote_select.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add procurement/quote_select.py tests/test_procurement_quote_select.py
git commit -m "feat: quotation-document picker"
```

---

### Task 5: LLM bid extractor

**Files:**
- Create: `procurement/extract.py`
- Create: `shared/llm/prompts/bid_extract_v1.txt`
- Test: `tests/test_procurement_extract.py`

**Interfaces:**
- Consumes: `read_text` (Task 3), `pick_quote` (Task 4), `BidExtraction`/`VendorBid` (Task 1), `LLMClient` (`shared/llm/interface`), `ProvenanceRef`.
- Produces: `extract_bid(vendor: str, files: list[str], client) -> VendorBid` — picks the quote, reads its text, calls `client.classify_structure(prompt, BidExtraction, text)`, validates into `BidExtraction`, and wraps into a `VendorBid` with vendor/source/provenance and `extraction_status="ok"`. On no quote found or any exception, returns a `VendorBid` with `extraction_status="failed"` and a `notes` reason (never raises).

- [ ] **Step 1: Write the prompt file**

```text
# shared/llm/prompts/bid_extract_v1.txt
You are extracting the commercial terms of a single vendor's quotation for a
competitive tender. From the quotation text, return: currency (ISO code),
base_price (the total price for the base scope, as a number), whether VAT is
included and its rate, freight amount and whether freight is included,
discount percentage (as a fraction, e.g. 0.10), any optional/priced line items
(spares, tools, PEMS), delivery_time, delivery_terms (e.g. FCA/EXW/CIF/site),
payment_terms, and engine_make. Use the numbers exactly as stated; do not
convert currencies or compute totals. If a field is not present, leave it at
its default. Do not invent values.
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_procurement_extract.py
from procurement.extract import extract_bid
from procurement.models import VendorBid
from shared.llm.mock_client import MockLLMClient


def test_extract_bid_from_mock(tmp_path):
    # Use .txt so read_text does not attempt PDF parsing; the mock ignores content.
    quote = tmp_path / "Quotation-X.txt"; quote.write_text("Base price USD 585000, FCA")
    other = tmp_path / "BOM-notes.txt"; other.write_text("bom line items")
    canned = {
        "currency": "USD", "base_price": 585000.0, "vat_included": False,
        "freight_included": False, "discount_pct": 0.0, "delivery_terms": "FCA",
        "engine_make": "Waukesha",
    }
    client = MockLLMClient(response=canned)
    bid = extract_bid("KERUI", [str(quote), str(other)], client)
    assert isinstance(bid, VendorBid)
    assert bid.extraction_status == "ok"
    assert bid.vendor == "KERUI"
    assert bid.base_price == 585000.0
    assert bid.source_document.endswith("Quotation-X.txt")
    assert bid.provenance is not None


def test_extract_bid_no_quote_is_failed():
    client = MockLLMClient(response={})
    bid = extract_bid("AESL", [], client)
    assert bid.extraction_status == "failed"
    assert "no quote" in (bid.notes or "").lower()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_procurement_extract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'procurement.extract'`

- [ ] **Step 4: Write minimal implementation**

```python
# procurement/extract.py
from pathlib import Path
from shared.provenance import ProvenanceRef
from procurement.models import BidExtraction, VendorBid
from procurement.quote_select import pick_quote
from procurement.loaders import read_text

_PROMPT = Path(__file__).parents[1] / "shared" / "llm" / "prompts" / "bid_extract_v1.txt"


def extract_bid(vendor: str, files: list[str], client) -> VendorBid:
    quote = pick_quote(files)
    if quote is None:
        return VendorBid(vendor=vendor, extraction_status="failed",
                         notes="no quote document found in vendor folder")
    try:
        text = read_text(quote)
        prompt = _PROMPT.read_text(encoding="utf-8")
        result = client.classify_structure(prompt, BidExtraction, text)
        extraction = BidExtraction.model_validate(result)
        return VendorBid(
            vendor=vendor,
            source_document=quote,
            provenance=ProvenanceRef(document_path=quote, extractor="anthropic:bid_extract_v1"),
            extraction_status="ok",
            **extraction.model_dump(),
        )
    except Exception as exc:  # graceful per-vendor failure
        return VendorBid(vendor=vendor, source_document=quote,
                         extraction_status="failed", notes=f"extraction error: {exc}")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_procurement_extract.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add procurement/extract.py shared/llm/prompts/bid_extract_v1.txt tests/test_procurement_extract.py
git commit -m "feat: LLM vendor-bid extractor"
```

---

### Task 6: Normalization

**Files:**
- Create: `procurement/normalize.py`
- Test: `tests/test_procurement_normalize.py`

**Interfaces:**
- Consumes: `VendorBid`, `NormalizedBid`, `NormalizationAdjustment` (Task 1).
- Produces: `normalize_bid(bid: VendorBid, target_currency: str, fx_rates: dict[str, float]) -> NormalizedBid` — restates to a common currency (VAT-excluded, freight-included, discount applied) and records every adjustment. A `failed` bid returns a `NormalizedBid` with `normalized_total=None`.
  - FX rate = `1.0` if `bid.currency` equals `target_currency` or is empty; else `fx_rates.get(bid.currency, 1.0)`.
  - Steps (each appends an adjustment when it changes the value): currency (base×rate), vat (÷(1+rate) if `vat_included`), freight (+freight×rate if not `freight_included`), discount (−total×discount_pct if `discount_pct>0`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_procurement_normalize.py
from procurement.models import VendorBid
from procurement.normalize import normalize_bid


def _close(a, b):
    return abs(a - b) <= 0.01


def test_currency_and_freight():
    bid = VendorBid(vendor="A", currency="EUR", base_price=1000.0,
                    freight_amount=100.0, freight_included=False)
    n = normalize_bid(bid, "USD", {"EUR": 1.1})
    # 1000*1.1 = 1100 base; +100*1.1 = 110 freight -> 1210
    assert _close(n.normalized_total, 1210.0)
    assert n.normalized_currency == "USD"
    kinds = [a.kind for a in n.adjustments]
    assert "currency" in kinds and "freight" in kinds


def test_vat_excluded_same_currency():
    bid = VendorBid(vendor="B", currency="USD", base_price=1050.0,
                    vat_included=True, vat_rate=0.05, freight_included=True)
    n = normalize_bid(bid, "USD", {})
    assert _close(n.normalized_total, 1000.0)  # 1050 / 1.05
    assert any(a.kind == "vat" for a in n.adjustments)


def test_discount_applied():
    bid = VendorBid(vendor="C", currency="USD", base_price=1000.0,
                    freight_included=True, discount_pct=0.1)
    n = normalize_bid(bid, "USD", {})
    assert _close(n.normalized_total, 900.0)


def test_failed_bid_has_no_total():
    bid = VendorBid(vendor="D", extraction_status="failed")
    n = normalize_bid(bid, "USD", {})
    assert n.normalized_total is None
    assert n.extraction_status == "failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_procurement_normalize.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/normalize.py
from procurement.models import VendorBid, NormalizedBid, NormalizationAdjustment


def normalize_bid(bid: VendorBid, target_currency: str, fx_rates: dict[str, float]) -> NormalizedBid:
    if bid.extraction_status == "failed":
        return NormalizedBid(vendor=bid.vendor, normalized_currency=target_currency,
                             normalized_total=None, extraction_status="failed")

    adjustments: list[NormalizationAdjustment] = []
    rate = 1.0 if (not bid.currency or bid.currency == target_currency) else fx_rates.get(bid.currency, 1.0)

    base = bid.base_price
    converted = base * rate
    if rate != 1.0:
        adjustments.append(NormalizationAdjustment(
            kind="currency", description=f"{bid.currency}->{target_currency} @ {rate}",
            from_value=base, to_value=converted, delta=converted - base))

    total = converted
    if bid.vat_included and bid.vat_rate:
        ex = total / (1.0 + bid.vat_rate)
        adjustments.append(NormalizationAdjustment(
            kind="vat", description=f"remove VAT {bid.vat_rate}",
            from_value=total, to_value=ex, delta=ex - total))
        total = ex

    if not bid.freight_included and bid.freight_amount:
        freight = bid.freight_amount * rate
        newtotal = total + freight
        adjustments.append(NormalizationAdjustment(
            kind="freight", description="add freight to reach freight-included basis",
            from_value=total, to_value=newtotal, delta=freight))
        total = newtotal

    if bid.discount_pct:
        discount = total * bid.discount_pct
        newtotal = total - discount
        adjustments.append(NormalizationAdjustment(
            kind="discount", description=f"apply discount {bid.discount_pct}",
            from_value=total, to_value=newtotal, delta=-discount))
        total = newtotal

    return NormalizedBid(vendor=bid.vendor, normalized_currency=target_currency,
                         normalized_total=round(total, 2), adjustments=adjustments)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_procurement_normalize.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add procurement/normalize.py tests/test_procurement_normalize.py
git commit -m "feat: transparent bid normalization"
```

---

### Task 7: Comparison table

**Files:**
- Create: `procurement/compare.py`
- Test: `tests/test_procurement_compare.py`

**Interfaces:**
- Consumes: `VendorBid`, `NormalizedBid`, `ComparisonRow`, `ComparisonTable` (Task 1).
- Produces: `build_comparison(bids: list[VendorBid], normalized: list[NormalizedBid], target_currency: str) -> ComparisonTable` — one row per vendor (matched by `vendor`), carrying raw base price, normalized total, key terms, and status; rows sorted by `normalized_total` ascending with `None` (failed) last.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_procurement_compare.py
from procurement.models import VendorBid, NormalizedBid
from procurement.compare import build_comparison


def test_build_comparison_sorts_and_includes_failed():
    bids = [
        VendorBid(vendor="A", currency="USD", base_price=1200.0, delivery_terms="FCA"),
        VendorBid(vendor="B", currency="USD", base_price=1000.0, delivery_terms="CIF"),
        VendorBid(vendor="C", extraction_status="failed"),
    ]
    norm = [
        NormalizedBid(vendor="A", normalized_currency="USD", normalized_total=1200.0),
        NormalizedBid(vendor="B", normalized_currency="USD", normalized_total=1000.0),
        NormalizedBid(vendor="C", normalized_currency="USD", normalized_total=None, extraction_status="failed"),
    ]
    table = build_comparison(bids, norm, "USD")
    assert [r.vendor for r in table.rows] == ["B", "A", "C"]
    assert table.rows[0].normalized_total == 1000.0
    assert table.rows[-1].extraction_status == "failed"
    assert table.rows[1].delivery_terms == "FCA"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_procurement_compare.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/compare.py
from procurement.models import VendorBid, NormalizedBid, ComparisonRow, ComparisonTable


def build_comparison(bids: list[VendorBid], normalized: list[NormalizedBid],
                     target_currency: str) -> ComparisonTable:
    norm_by_vendor = {n.vendor: n for n in normalized}
    rows: list[ComparisonRow] = []
    for bid in bids:
        n = norm_by_vendor.get(bid.vendor)
        rows.append(ComparisonRow(
            vendor=bid.vendor,
            currency=bid.currency,
            raw_base_price=bid.base_price if bid.extraction_status == "ok" else None,
            normalized_total=n.normalized_total if n else None,
            delivery_terms=bid.delivery_terms,
            delivery_time=bid.delivery_time,
            payment_terms=bid.payment_terms,
            engine_make=bid.engine_make,
            extraction_status=bid.extraction_status,
        ))
    rows.sort(key=lambda r: (r.normalized_total is None, r.normalized_total or 0.0))
    return ComparisonTable(target_currency=target_currency, rows=rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_procurement_compare.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add procurement/compare.py tests/test_procurement_compare.py
git commit -m "feat: comparison table assembly"
```

---

### Task 8: Excel / CSV export

**Files:**
- Create: `procurement/export.py`
- Test: `tests/test_procurement_export.py`

**Interfaces:**
- Consumes: `ComparisonTable` (Task 1).
- Produces:
  - `comparison_to_rows(table: ComparisonTable) -> list[dict]` — flat dict rows (header keys) for a DataFrame/CSV.
  - `comparison_to_xlsx_bytes(table: ComparisonTable) -> bytes` — an `.xlsx` workbook of the comparison, via openpyxl into a `BytesIO`.
  - `comparison_to_csv_str(table: ComparisonTable) -> str` — CSV text.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_procurement_export.py
import io
import openpyxl
from procurement.models import ComparisonRow, ComparisonTable
from procurement.export import comparison_to_rows, comparison_to_xlsx_bytes, comparison_to_csv_str


def _table():
    return ComparisonTable(target_currency="USD", rows=[
        ComparisonRow(vendor="B", currency="USD", raw_base_price=1000.0, normalized_total=1000.0,
                      delivery_terms="CIF", delivery_time="24w", payment_terms="LC",
                      engine_make="MAN", extraction_status="ok"),
    ])


def test_rows_and_csv():
    rows = comparison_to_rows(_table())
    assert rows[0]["vendor"] == "B" and rows[0]["normalized_total"] == 1000.0
    csv = comparison_to_csv_str(_table())
    assert "vendor" in csv and "MAN" in csv


def test_xlsx_bytes_roundtrip():
    data = comparison_to_xlsx_bytes(_table())
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb.active
    assert ws["A1"].value == "vendor"
    assert any(cell.value == "B" for row in ws.iter_rows() for cell in row)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_procurement_export.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/export.py
import io
import csv
import openpyxl
from procurement.models import ComparisonTable

_HEADERS = ["vendor", "currency", "raw_base_price", "normalized_total",
            "delivery_terms", "delivery_time", "payment_terms", "engine_make",
            "extraction_status"]


def comparison_to_rows(table: ComparisonTable) -> list[dict]:
    return [{h: getattr(r, h) for h in _HEADERS} for r in table.rows]


def comparison_to_csv_str(table: ComparisonTable) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_HEADERS)
    writer.writeheader()
    for row in comparison_to_rows(table):
        writer.writerow(row)
    return buf.getvalue()


def comparison_to_xlsx_bytes(table: ComparisonTable) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Comparison"
    ws.append(_HEADERS)
    for row in comparison_to_rows(table):
        ws.append([row[h] for h in _HEADERS])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_procurement_export.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add procurement/export.py tests/test_procurement_export.py
git commit -m "feat: comparison export to xlsx/csv"
```

---

### Task 9: Pipeline orchestrator

**Files:**
- Create: `procurement/pipeline.py`
- Test: `tests/test_procurement_pipeline.py`

**Interfaces:**
- Consumes: `project` store (Task 2), `extract_bid` (Task 5), `normalize_bid` (Task 6), `build_comparison` (Task 7), models (Task 1).
- Produces:
  - `run_ingestion(root: str, slug: str, client) -> dict` — loads the project; for each vendor: `vendor_files` → `extract_bid` → `normalize_bid`; builds the comparison; writes `dataset.json` (with `bids`, `normalized`, `comparison`) into the project dir; sets project `status="done"`; returns the assembled dict.
  - `load_dataset(root: str, slug: str) -> dict | None` — reads `dataset.json` if present.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_procurement_pipeline.py
import io
import zipfile
from procurement.project import create_project, unpack_vendor_zip
from procurement.pipeline import run_ingestion, load_dataset
from shared.llm.mock_client import MockLLMClient


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, d in entries.items():
            zf.writestr(n, d)
    return buf.getvalue()


def test_run_ingestion_end_to_end(tmp_path):
    create_project(str(tmp_path), "Proj", target_currency="USD")
    z = tmp_path / "v.zip"
    # .txt quotes so read_text does not attempt PDF parsing; the mock ignores content.
    z.write_bytes(_zip({
        "KERUI/Quotation.txt": b"base price 1000",
        "ADPOWER/Quotation.txt": b"base price 1000",
    }))
    unpack_vendor_zip(str(tmp_path), "proj", str(z))
    client = MockLLMClient(response={"currency": "USD", "base_price": 1000.0,
                                     "freight_included": True})
    result = run_ingestion(str(tmp_path), "proj", client)

    assert len(result["comparison"]["rows"]) == 2
    assert all(r["extraction_status"] == "ok" for r in result["comparison"]["rows"])
    # persisted
    ds = load_dataset(str(tmp_path), "proj")
    assert ds is not None and len(ds["bids"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_procurement_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/pipeline.py
import os
import json
from procurement.project import load_project, save_project, vendor_files
from procurement.extract import extract_bid
from procurement.normalize import normalize_bid
from procurement.compare import build_comparison


def run_ingestion(root: str, slug: str, client) -> dict:
    project = load_project(root, slug)
    bids = []
    normalized = []
    for vendor in project.vendors:
        files = vendor_files(root, slug, vendor)
        bid = extract_bid(vendor, files, client)
        bids.append(bid)
        normalized.append(normalize_bid(bid, project.target_currency, project.fx_rates))
    comparison = build_comparison(bids, normalized, project.target_currency)

    result = {
        "bids": [b.model_dump() for b in bids],
        "normalized": [n.model_dump() for n in normalized],
        "comparison": comparison.model_dump(),
    }
    with open(os.path.join(root, slug, "dataset.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, default=str)
    project.status = "done"
    save_project(root, project)
    return result


def load_dataset(root: str, slug: str) -> dict | None:
    path = os.path.join(root, slug, "dataset.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_procurement_pipeline.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass (live tests skip without keys/data).

- [ ] **Step 6: Commit**

```bash
git add procurement/pipeline.py tests/test_procurement_pipeline.py
git commit -m "feat: ingestion pipeline orchestrator"
```

---

### Task 10: Streamlit portal UI

**Files:**
- Create: `portal/__init__.py`
- Create: `portal/app.py`
- Modify: `pyproject.toml` (add `streamlit` dependency)

**Interfaces:**
- Consumes: `project` store (Task 2), `run_ingestion`/`load_dataset` (Task 9), `export` (Task 8), `shared.llm.factory.get_client`.

> UI is validated by running it, not by unit tests — all logic lives in the tested modules above. Keep `app.py` thin: it only wires widgets to those functions.

- [ ] **Step 1: Add `streamlit` to `pyproject.toml` dependencies** (already installed in this environment).

- [ ] **Step 2: Write `portal/__init__.py`** (empty) and `portal/app.py`

```python
# portal/app.py
import os
import streamlit as st
from procurement import project as proj
from procurement.pipeline import run_ingestion, load_dataset
from procurement.export import comparison_to_rows, comparison_to_xlsx_bytes, comparison_to_csv_str
from shared.llm.factory import get_client

ROOT = os.environ.get("PROCUREMENT_PROJECTS_ROOT", "projects")
# Default to real Anthropic extraction when a provider isn't explicitly chosen,
# so a set ANTHROPIC_API_KEY is actually used (get_client() otherwise defaults to mock).
os.environ.setdefault("LLM_PROVIDER", "anthropic")

st.set_page_config(page_title="Procurement Comparison Portal", layout="wide")
st.title("Procurement Comparison Portal")

if not os.getenv("ANTHROPIC_API_KEY"):
    st.warning("ANTHROPIC_API_KEY is not set — extraction will fail. Set it and restart to run real extraction.")

os.makedirs(ROOT, exist_ok=True)

# --- Sidebar: project selection / creation ---
st.sidebar.header("Projects")
projects = proj.list_projects(ROOT)
names = [p.slug for p in projects]
choice = st.sidebar.selectbox("Open project", ["<new>"] + names)

if choice == "<new>":
    new_name = st.sidebar.text_input("New project name")
    target = st.sidebar.text_input("Target currency", value="USD")
    if st.sidebar.button("Create") and new_name.strip():
        proj.create_project(ROOT, new_name.strip(), target_currency=target.strip() or "USD")
        st.rerun()
    st.info("Create a project in the sidebar to begin.")
    st.stop()

project = proj.load_project(ROOT, choice)
st.subheader(f"Project: {project.name}")

# --- Requirements doc ---
st.markdown("### 1. Requirements document")
req = st.file_uploader("Upload the requirements document", type=["pdf", "docx", "xlsx"], key="req")
if req is not None:
    dest = os.path.join(ROOT, project.slug, "requirements", req.name)
    with open(dest, "wb") as fh:
        fh.write(req.getbuffer())
    project.requirements_file = req.name
    proj.save_project(ROOT, project)
    st.success(f"Stored requirements: {req.name}")
elif project.requirements_file:
    st.caption(f"Attached: {project.requirements_file}")

# --- Vendor folders (ZIP) ---
st.markdown("### 2. Vendor folders (ZIP — top-level subfolders are vendors)")
vzip = st.file_uploader("Upload vendor ZIP", type=["zip"], key="vzip")
if vzip is not None:
    tmp = os.path.join(ROOT, project.slug, "_upload.zip")
    with open(tmp, "wb") as fh:
        fh.write(vzip.getbuffer())
    try:
        vendors = proj.unpack_vendor_zip(ROOT, project.slug, tmp)
        st.success(f"Detected vendors: {', '.join(vendors)}")
    except ValueError as e:
        st.error(f"Rejected archive: {e}")
    finally:
        os.remove(tmp)
    project = proj.load_project(ROOT, project.slug)

if project.vendors:
    for v in project.vendors:
        files = proj.vendor_files(ROOT, project.slug, v)
        from procurement.quote_select import pick_quote
        st.caption(f"**{v}** — {len(files)} files · quote: {os.path.basename(pick_quote(files) or '—')}")

# --- FX rates ---
st.markdown("### 3. FX rates (to target currency)")
st.caption(f"Target currency: {project.target_currency}. Enter a rate for any other currency the vendors used.")
fx_input = st.text_input("FX rates as CUR=rate, comma-separated (e.g. EUR=1.08)",
                         value=",".join(f"{k}={v}" for k, v in project.fx_rates.items()))
if st.button("Save FX rates"):
    rates = {}
    for pair in fx_input.split(","):
        if "=" in pair:
            k, val = pair.split("=", 1)
            try:
                rates[k.strip().upper()] = float(val)
            except ValueError:
                pass
    project.fx_rates = rates
    proj.save_project(ROOT, project)
    st.success(f"Saved FX rates: {rates}")

# --- Run ---
st.markdown("### 4. Run ingestion")
if st.button("Run ingestion", type="primary", disabled=not project.vendors):
    with st.spinner("Extracting and comparing vendor bids..."):
        run_ingestion(ROOT, project.slug, get_client())
    st.success("Done.")

# --- Results ---
dataset = load_dataset(ROOT, project.slug)
if dataset:
    st.markdown("### 5. Results")
    from procurement.models import ComparisonTable
    table = ComparisonTable.model_validate(dataset["comparison"])
    st.dataframe(comparison_to_rows(table), use_container_width=True)

    c1, c2 = st.columns(2)
    c1.download_button("Download Excel", comparison_to_xlsx_bytes(table),
                       file_name=f"{project.slug}-comparison.xlsx")
    c2.download_button("Download CSV", comparison_to_csv_str(table),
                       file_name=f"{project.slug}-comparison.csv")

    st.markdown("#### Per-vendor extracted bids")
    for bid in dataset["bids"]:
        with st.expander(f"{bid['vendor']} — {bid['extraction_status']}"):
            st.json(bid)
    st.markdown("#### Normalization adjustments")
    for n in dataset["normalized"]:
        if n["adjustments"]:
            st.write(f"**{n['vendor']}** → {n['normalized_total']} {n['normalized_currency']}")
            st.table(n["adjustments"])
```

- [ ] **Step 3: Manual verification (run the portal)**

Run: `streamlit run portal/app.py`
Then in the browser:
1. Create a project (e.g. "Gas Gensets", USD).
2. Zip the `data/procurement-data` vendor folders (KERUI, ADPOWER, MKON, AESL) into one ZIP and upload it; confirm the 4 vendors + detected quote docs.
3. Enter `EUR=1.08` in FX rates and save (ADPOWER quotes in EUR).
4. With `ANTHROPIC_API_KEY` set, click **Run ingestion**; confirm the comparison table populates, normalized totals appear, adjustments show, and Excel/CSV download works.
Expected: a populated side-by-side comparison; AESL may extract thinly (quotation-only) but must not break the run.

- [ ] **Step 4: Commit**

```bash
git add portal/__init__.py portal/app.py pyproject.toml
git commit -m "feat: Streamlit procurement comparison portal"
```

---

### Task 11: Real-data live extraction test (skip-guarded)

**Files:**
- Test: `tests/test_procurement_real_data.py`

**Interfaces:**
- Consumes: `extract_bid` (Task 5), `get_client` (`shared.llm.factory`), `project.vendor_files` semantics (plain folder listing).

- [ ] **Step 1: Write the test**

```python
# tests/test_procurement_real_data.py
import os
import glob
import pytest
from procurement.extract import extract_bid
from shared.llm.factory import get_client

VENDOR_DIR = "data/procurement-data/ADPOWER"


@pytest.mark.skipif(
    not (os.getenv("ANTHROPIC_API_KEY") and os.path.isdir(VENDOR_DIR)),
    reason="needs ANTHROPIC_API_KEY and sample data",
)
def test_live_extract_adpower():
    os.environ["LLM_PROVIDER"] = "anthropic"
    files = glob.glob(os.path.join(VENDOR_DIR, "*"))
    bid = extract_bid("ADPOWER", files, get_client())
    assert bid.extraction_status == "ok"
    assert bid.base_price > 0
    assert bid.currency  # e.g. EUR
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/test_procurement_real_data.py -v`
Expected: PASS with a key + data present; otherwise SKIPPED.

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass; live/real-data tests skip without their prerequisites.

- [ ] **Step 4: Commit**

```bash
git add tests/test_procurement_real_data.py
git commit -m "test: live ADPOWER bid extraction (skip-guarded)"
```

---

## Notes for the implementer

- **Run order:** Tasks 1→11 are dependency-ordered.
- **No network in CI:** every test except the two skip-guarded ones (`ANTHROPIC_API_KEY`, sample-data presence) uses the mock adapter or generated fixtures.
- **The LLM method is `classify_structure`** (generic structured output). We reuse it for bid extraction — do not add a new method to the LLM layer.
- **Deferred (do NOT build here):** requirements-doc parsing/compliance, full scope normalization, weighted scoring/award, full BOM extraction, revision/lineage, live FX, the client's exact CS-template export, auth/hosting. All in `docs/future-improvements-roadmap.md`.

## Suggested 4-day pacing
- **Day 1:** Tasks 1–4 (models, project store, loaders, quote picker).
- **Day 2:** Tasks 5–7 (extractor, normalize, compare).
- **Day 3:** Tasks 8–10 (export, pipeline, portal) + manual run-through.
- **Day 4:** Task 11, real-data polish, buffer for extraction-prompt tuning against the actual quotes.
