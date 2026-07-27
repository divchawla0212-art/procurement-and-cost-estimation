# Design: Procurement Comparison Portal (MVP)

**Date:** 2026-07-28
**Status:** Approved design, pre-implementation
**Deadline:** 4 days — scope is deliberately minimal. Everything beyond this is in `docs/future-improvements-roadmap.md`.

---

## 1. Context and goal

A buyer runs a competitive tender: a **requirements document** defines what is wanted, and several **vendors** submit priced quotations (one folder each). The quotes are **not directly comparable** — they mix currencies, VAT in/out, freight in/out, discounts, and differing terms.

This project delivers a **local, single-user web portal** where the buyer creates a **project**, uploads the requirements doc and the vendor folders, runs an **automated ingestion** that extracts each vendor's commercial bid, and views a **light-normalized side-by-side comparison**. Extraction is real (Anthropic LLM); the app reuses the `shared/llm` engine already built.

The `data/procurement-data` folders (KERUI, ADPOWER, MKON, AESL) are the concrete test instance.

### Success criteria
1. Create a project, upload a requirements doc + vendor folders, click **Run**, and get per-vendor extracted bids + a comparison table — end to end, on a real vendor folder.
2. Prices are restated to a **common currency, VAT-excluded, freight-included**, with **every adjustment shown**.
3. One vendor failing to extract does not stop the others.
4. Deterministic logic is unit-tested; the app runs locally with an `ANTHROPIC_API_KEY`.

---

## 2. Decisions locked (from brainstorming)

| Decision | Choice |
|---|---|
| Fidelity | Working **single-user local prototype** (no auth, no hosting) |
| UI stack | **Streamlit** (pure-Python, reuses the backend directly) |
| Extraction | **Real**, via the existing **Anthropic** adapter (`ANTHROPIC_API_KEY`) |
| Output | **Extracted commercial bids + light-normalized side-by-side comparison** |
| Requirements doc | **Stored & attached** to the project (not parsed in the MVP) |
| Normalization | **Light** — common currency (configurable FX), VAT-excluded, freight-included, adjustments shown |
| Extraction scope | **Commercial bid** fields (price, currency, VAT, freight, discount, optional items, delivery, payment, engine make) |
| Vendor upload | **ZIP whose top-level subfolders are vendors** (maps to their folder structure) |

---

## 3. Architecture

Reuses the existing foundation (`shared/llm/` incl. the Anthropic + Bedrock adapters, `shared/provenance.py`).

```
procurement/
  models.py       # VendorBid, OptionalItem, NormalizationAdjustment, ComparisonTable, Project
  project.py      # filesystem project store: create/list/load; safe unzip of vendor folders
  loaders.py      # extract text from PDF (pdftotext CLI -> pypdf -> LLM fallback) and xlsx (openpyxl)
  pdf_llm.py      # LLM PDF transcription (Anthropic native PDF input) — fallback for scanned/thin PDFs
  quote_select.py # choose the quotation document inside a vendor folder (filename heuristics)
  extract.py      # vendor folder -> VendorBid  (LLM structured extraction via shared/llm)
  normalize.py    # VendorBid -> normalized total + explicit NormalizationAdjustments
  compare.py      # [VendorBid] -> ComparisonTable
portal/
  app.py          # Streamlit UI (projects, upload, run, results, export)
tests/
  test_procurement_*.py
```

Each module has one responsibility and a clear interface; the LLM sits behind the existing `LLMClient` protocol so extraction is provider-swappable and testable with the mock adapter.

### 3.1 Project storage (filesystem)
```
projects/<project-slug>/
  project.json              # name, created_at, currency target, fx_rates, vendor list, status
  requirements/<file>       # the uploaded requirements doc (stored, not parsed)
  vendors/<vendor-name>/…    # each vendor's uploaded files (from the ZIP subfolders)
  dataset.json              # extracted bids + normalization + comparison (the run output)
```
A `projects/` root under the working directory. `project.json` is the source of truth for project state; `dataset.json` is the latest run's result.

---

## 4. Data flow

1. **Create project** → `projects/<slug>/` + `project.json`.
2. **Upload requirements doc** → stored under `requirements/` (viewable; not parsed).
3. **Upload vendor ZIP** → **safely unzipped** (zip-slip guarded); each top-level subfolder becomes a vendor under `vendors/<name>/`. Detected vendors + their files are listed; the auto-selected quote doc per vendor is shown and confirmable.
4. **Set FX rates** for any non-target currency (default 1.0 with a visible warning).
5. **Run** → for each vendor: `quote_select` picks the quotation → `loaders` reads its text → `extract` calls the LLM for a `VendorBid` → `normalize` produces the normalized total + adjustments. Results assembled by `compare` and written to `dataset.json`.
6. **Results** → per-vendor bid (expanders) + the comparison table (raw + normalized + adjustments) + **Excel/CSV export**.

---

## 5. Data models (Pydantic, `procurement/models.py`)

- **`OptionalItem`** — `description`, `qty`, `unit_price`, `total`, `category` (spares / tools / pems / other), `included_in_base: bool`.
- **`VendorBid`** — `vendor`, `currency`, `base_price`, `vat_included: bool`, `vat_rate` (e.g. 0.05), `freight_amount`, `freight_included: bool`, `discount_pct`, `optional_items: list[OptionalItem]`, `delivery_time`, `delivery_terms`, `payment_terms`, `engine_make`, `source_document`, `provenance: ProvenanceRef`, `extraction_status` (`ok` / `failed`), `notes`.
- **`NormalizationAdjustment`** — `kind` (`currency` / `vat` / `freight` / `discount`), `description`, `from_value`, `to_value`, `delta`.
- **`NormalizedBid`** — the `VendorBid` + `normalized_currency`, `normalized_total`, `adjustments: list[NormalizationAdjustment]`.
- **`ComparisonTable`** — target currency + a row per vendor (raw base, normalized total, key terms, extraction status).
- **`Project`** — `name`, `slug`, `created_at`, `target_currency`, `fx_rates: dict[str, float]`, `vendors: list[str]`, `requirements_file`, `status`.

Every extracted figure carries a `ProvenanceRef` (document + page). Raw as-stated values are preserved; normalization never mutates them (it produces a separate `NormalizedBid`).

---

## 6. Extraction (`extract.py`)

- `quote_select.pick_quote(files)` — choose the quotation doc by filename keywords (`quotation`, `quote`, `offer`, `proposal`, `techno`, or a vendor-ref pattern); fall back to the only/most-likely commercial document.
- `loaders.read_text(path, llm_fallback=None)` — PDF via `pdftotext` CLI → `pypdf` → **LLM transcription fallback** when the text layer is empty/thin (scanned or image-only PDFs); xlsx via `openpyxl` flattened to text. The fallback (`pdf_llm.transcribe_pdf`) sends the PDF to the LLM using Anthropic's native PDF document input and returns the transcribed text, so **every vendor and requirement PDF is extracted properly** regardless of whether it has a usable text layer.
- `extract_bid(vendor, files, client)` — builds a versioned, provider-neutral prompt + the `VendorBid` output schema, calls `client.classify_structure(prompt, VendorBid, text)` (the existing generic structured-output method), validates into a `VendorBid`, attaches provenance. On any failure → a `VendorBid` with `extraction_status="failed"` and a reason (never raises out of the run).

Numbers come from the model reading the quote text; a later phase (roadmap) adds deterministic reconciliation against stated totals.

---

## 7. Normalization (`normalize.py`) — transparent and auditable

Given a `VendorBid`, a `target_currency`, and `fx_rates`:
1. **Currency** → convert `base_price`, `freight_amount`, and option totals to the target currency using `fx_rates[currency]` (default 1.0 + warning if missing). Record a `currency` adjustment.
2. **VAT-excluded basis** → if `vat_included`, divide out `vat_rate` (`base / (1 + vat_rate)`). Record a `vat` adjustment.
3. **Freight-included basis** → if `freight_included` is false, add `freight_amount`. Record a `freight` adjustment.
4. **Discount** → apply `discount_pct` if not already reflected. Record a `discount` adjustment.
5. **`normalized_total`** = converted, VAT-excluded base + freight − discount. **Optional items are listed but excluded** from the normalized base (scope varies across vendors) — shown separately so the buyer sees them.

Every step appends a `NormalizationAdjustment` with from/to/delta, so the normalized total is fully explainable.

---

## 8. Portal (`portal/app.py`, Streamlit)

- **Projects screen** — list existing projects (scan `projects/`), **Create new project**.
- **Project screen** —
  - *Requirements*: upload / replace; shows the attached file.
  - *Vendors*: upload the vendor ZIP → detected-vendor list with per-vendor files and the auto-selected quote doc (confirmable).
  - *FX rates*: numeric inputs per non-target currency.
  - **Run** button → progress per vendor → writes `dataset.json`.
  - *Results*: per-vendor `VendorBid` (expanders, with provenance), the **comparison table** (raw + normalized + adjustments), and **Download (Excel/CSV)**.
- Clear banner if `ANTHROPIC_API_KEY` is unset (extraction disabled; rest of the UI still works).

---

## 9. Error handling

- **Per-vendor isolation:** a vendor whose quote can't be found or extracted is marked `failed` with a reason; the run continues and the comparison shows the others.
- **Missing key:** a visible message; no crash.
- **FX default 1.0** with a warning when a currency has no rate.
- **Safe unzip:** reject path-traversal (zip-slip) entries; ignore hidden/temp files (`~$…`).

---

## 10. Testing (pragmatic for 4 days)

Mock-adapter path keeps CI key-free.
- **Unit (deterministic):** `normalize` (currency/VAT/freight/discount math + adjustment records), `compare` (table assembly, mixed ok/failed bids), `project` (create/list/load, safe unzip incl. a zip-slip attempt), `quote_select` (picks the quote among mixed files), `loaders` (reads a small PDF/xlsx).
- **Extraction:** `extract_bid` against the **mock adapter** with a canned `VendorBid` dict; asserts validation + provenance + graceful failure on unreadable input.
- **Live (skip-guarded):** one real extraction against a folder in `data/procurement-data` (e.g. `ADPOWER`), `skipif` no `ANTHROPIC_API_KEY`.

---

## 11. Out of scope (deferred — see the roadmap)

Auth / multi-user / hosting; requirements-doc parsing & compliance gating; full scope normalization (delivery terms, spares/tools in-vs-out); weighted scoring & award recommendation; full BOM/line-item extraction & reconciliation; revision/lineage & MOM history; live FX rates; the client's exact CS-template export. All captured in `docs/future-improvements-roadmap.md`.
