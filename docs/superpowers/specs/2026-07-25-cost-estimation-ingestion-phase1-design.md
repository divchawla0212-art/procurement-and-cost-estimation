# Design: Cost Estimation Tool — Phase 1 (Ingestion + Structured Dataset)

**Date:** 2026-07-25
**Status:** Approved design, pre-implementation
**Scope:** Phase 1 only (ingestion + validated structured dataset). Rate-library build, BOQ pricing / should-cost, roll-up, and proposal generation are named here but specified in their own later cycles.

> This design is client-agnostic. The dataset in `./data/cost-estimation-data` is one concrete instance used to ground and test the pipeline; nothing in the tool, schema, or configuration is tied to a particular client, project, or site.

---

## 1. Context and goal

This is a **construction / EPC cost-estimation** capability: build a defensible cost estimate for a scope of works by pricing a Bill of Quantities (BOQ) line-by-line, where each line ("component") is costed from a built-up unit rate — **labour + equipment + material (+ consumables + installation)** — multiplied by quantity and rolled up by discipline and area to a total estimate and a priced proposal.

The `./data/cost-estimation-data` directory holds real cost-estimation documents used as the first working example. The domain is distinct from vendor-bid comparison: instead of normalizing competing priced offers, the estimator **builds a cost from quantities and unit rates**.

**This document specifies only Phase 1: ingestion and a clean, validated, structured dataset** of the cost-estimation documents. It is the foundation every later phase (rate library, pricing, roll-up, proposal) builds on.

### Relationship to the procurement (bid-comparison) tool

Cost estimation is a **new, separate pipeline that shares the procurement design's foundation**, not a replacement:

- **Shared** (promoted to `shared/`): the multi-provider `LLMClient` interface (Anthropic live-first; OpenAI, Gemini, and a key-free mock behind one protocol, selected by config), the `ProvenanceRef` auditability pattern, versioned provider-neutral prompts, and the "reference-not-equality" test philosophy.
- **Own** to cost estimation: the domain models (cost items, rate build-ups, work packages, resource rates) — cost build-up is a different problem from comparing bids.

### Design principles (hard requirements, all phases)

- **Client-agnostic.** No client/project/site name is baked into the tool, schema, or config. The sample dataset is one instance.
- **Discipline/area-agnostic.** Electrical and Instrumentation are the first cases; Civil, Steel, Mechanical, Insulation, Painting and any area code generalize via config, not hard-coding.
- **Auditability.** Every figure traces to a source document (workbook, sheet, cell). Cost estimates get contested.
- **Numbers are read, never inferred.** Monetary and quantity values come from deterministic spreadsheet reads. The LLM never emits or transforms a figure.
- **Provider-swappable LLM.** All LLM calls sit behind one interface; the model/provider is switchable by config with zero code change.
- **Validation.** Extracted items reconcile to their own computed totals and to the workbook's built-in Summary rollups.

---

## 2. Data findings (grounding)

Confirmed by exploring `./data/cost-estimation-data`:

Two document layers plus supporting docs:

1. **Schedule of Prices** (`Section-3 … Additional Tie-in Works.xlsx`) — a **blank-priced BOQ template**: a `Summary` sheet rolling up 7 discipline sections (3A Civil, 3B Steel, 3C Mechanical, 3D Electrical, 3E Instrumentation, 3F Insulation, 3G Painting). Each discipline sheet lists line items with a **hierarchical cost code** (e.g. `E.03.03.01.01`), a bilingual (EN + IT) description, UoM, a **quantity**, and **empty** unit-rate columns (Unitary Labour / Equipment / Material → Unit Price → Total = Unit Price × Qty). This is the artifact to be priced.

2. **Costing workbooks** (`GDX-P-26-072 … Electrical BOQs …`, `… Instrumentation Works Schedule Of Unit Rates …`) — **fully populated rate build-ups**. A `SUMMARY` sheet aggregates work packages (e.g. `TF Main Elec. Equipment`, `IA Cable and Access.`, `APP-8 Instrument IA`) across cost elements: **Total Manhours, Materials, Consumables, Installation, Total Value**, with a **manhour rate** (e.g. 70) and a **crew composition** table (E&I Technicians/Fitters, Helpers, Engineers, Supervisors, QC roles with counts). Grand totals reach tens of millions. Detailed per-package sheets (20+ tabs, up to ~72 columns) carry the line-level build-up and use **area codes** (TF, IA, JPS, JD).

3. **Supporting docs:** a `.docx` **proposal template** (narrative proposal) and a **Scope of Work** `.pdf` that defines methods of measurement referenced by the BOQ (e.g. "See method of measurement note A").

Findings that shape the design:

- **Layouts vary and headers are messy.** Merged header blocks, bilingual descriptions, and inconsistent column positions across 20+ sheets mean sheet structure must be normalized before values are read.
- **Blank vs populated must be distinguished.** The Schedule of Prices has quantities but no rates; the costing workbooks have full rates. A single dataset must represent both and mark which is which.
- **Built-in rollups enable strong validation.** Each costing workbook already sums items → work packages → Summary. Extraction can be checked against these existing totals, not just against externally supplied numbers.

---

## 3. Decisions locked

| Decision | Choice | Rationale |
|---|---|---|
| Language/stack | Python 3.12 | Consistent with procurement; best Excel/LLM ecosystem. |
| Positioning | New separate pipeline, shared foundation | Cost estimation ≠ bid comparison, but reuses LLM/provenance/test foundation. |
| Primary job (this spec) | Ingestion + validated structured dataset | "Costing of components" starts from a clean structured dataset. |
| Phase-1 scope | Ingestion only | Consistent with procurement; pricing/roll-up/proposal are later specs. |
| Extraction stance | Deterministic-Excel-first; LLM only for structure normalization | Numbers must be exact; grids are already structured. |
| First LLM provider (live-tested) | Anthropic Claude API | Shared foundation; needs `ANTHROPIC_API_KEY`. |
| Multi-provider | Anthropic / OpenAI / Gemini / mock behind one protocol, config-selected | Provider swappability is a hard requirement. |

---

## 4. Architecture

### 4.1 Project structure

```
procurement/            # existing bid-comparison pipeline (unchanged)
shared/
  llm/
    interface.py         # LLMClient protocol (provider-agnostic)
    factory.py           # get_client(config) -> LLMClient
    anthropic_client.py  # built + live-tested first
    openai_client.py     # implemented, mock/recorded-tested
    gemini_client.py     # implemented, mock/recorded-tested
    mock_client.py       # deterministic, key-free, for CI
    prompts/             # versioned, provider-neutral prompt templates
  provenance.py          # ProvenanceRef shared across pipelines
cost_estimation/
  models/               # cost-domain pydantic schemas
  ingestion/
    classifier.py       # doc/sheet -> (doc_type, discipline, area, revision)
    workbook_loader.py  # openpyxl structured-grid reader (values)
    header_mapper.py    # LLM-assisted header/layout -> canonical column roles
    extractor.py        # orchestrates: classify -> map -> read -> validate
    reconcile.py        # item totals & work-package rollups vs Summary sheets
  config/
    disciplines/*.yaml  # discipline/area-agnostic knobs
  cli.py                # `cost-est ingest ./data/cost-estimation-data`
tests/                  # uses ./data/cost-estimation-data as fixtures
docs/superpowers/specs/
```

> `shared/llm` and `shared/provenance` are promoted from the procurement design so both pipelines use one implementation. Promotion is a small, mechanical refactor and is part of this phase's work only to the extent cost estimation needs it.

### 4.2 Ingestion pipeline (deterministic-Excel-first)

```
files in ./data/cost-estimation-data
   -> classify              (doc_type, discipline, area, revision)
   -> route by doc_type
        schedule_of_prices  -> per-sheet: header_mapper (LLM: structure only)
        costing_workbook       -> workbook_loader (openpyxl: read values)
                               -> build CostItems + RateBuildUps
        proposal_template   -> classify + index only (deferred phase)
        scope_of_work (pdf) -> classify + index only (deferred phase)
   -> reconcile   (item total == qty x unit_price;
                   work-package sum == Summary rollup)
   -> assemble validated cost_dataset.json (documents + work packages +
      cost items + rate build-ups + resource rates, all provenance-tracked)
```

Rationale:
- **Deterministic reads for every number.** `openpyxl` reads all grids; the LLM never touches figures. Zero hallucination risk on money.
- **LLM normalizes structure only.** Merged headers, bilingual text, and inconsistent layouts across 20+ tabs are mapped by the LLM to canonical column roles (code, description, UoM, qty, labour, equipment, material, consumables, installation, unit price, total). The LLM classifies *structure*; deterministic code reads *values* from the mapped columns.
- **Supporting docs indexed, not parsed** in Phase 1 (`.docx` proposal, SOW `.pdf`) — they feed later pricing/proposal phases.

Rejected: *LLM-reads-the-grid* (hundreds of rows × up to ~72 columns; expensive and risks corrupting figures) and *fixed hard-coded column positions* (layouts differ per sheet — brittle).

### 4.3 LLM provider abstraction (shared, switchable)

Reused from the procurement design (promoted to `shared/llm`), used here only by `header_mapper`:

- **`LLMClient` protocol** — one narrow method, roughly
  `classify_structure(prompt, output_schema, context_text, images=None) -> dict`.
- **Adapters** hide each provider's structured-output mechanism (Anthropic tool-use; OpenAI `response_format` JSON-schema; Gemini `responseSchema`; Mock returns deterministic canned mappings).
- **`get_client(config)` factory** reads `LLM_PROVIDER` (`anthropic|openai|gemini|mock`), `LLM_MODEL`, and the matching `*_API_KEY`. Switching providers = one env var, no code change.
- **Phase-1 test posture:** all providers selectable; Anthropic gets full live-tested coverage; OpenAI/Gemini ship implemented and mock/recorded-tested.

---

## 5. Data schema (Pydantic, `cost_estimation/models/`)

Raw, **as-stated** values only in Phase 1 — no pricing computed, no rates applied to blank templates.

- **`Document`** — `path`, `doc_type` (schedule_of_prices / costing_workbook / proposal_template / scope_of_work), `discipline`, `area` (e.g. TF/IA/JPS/JD), `revision`, `extraction_status`. Every fact points back to one.
- **`ProvenanceRef`** (shared) — `document_path`, `sheet`, `cell`, `extractor` (e.g. `xlsx` or `anthropic:v1` for a header mapping), optional `prompt_version`. Attached to every derived value.
- **`WorkPackage`** — discipline/section + area grouping (e.g. section `3D Electrical`, or `TF Main Elec. Equipment`); holds its `cost_items` and its as-stated `summary_rollup`.
- **`CostItem`** (a "component") — hierarchical `code` (e.g. `E.03.03.01.01`), `description`, `uom`, `quantity`, `rate_buildup` (optional — absent/empty on blank-template lines), `total`, `priced: bool` (distinguishes blank-template lines from populated ones), `provenance`.
- **`RateBuildUp`** — `labour` (`manhours`, `manhour_rate`, optional `crew`), `equipment`, `material_supply`, `consumables`, `installation`, `unit_price`. As-stated values only; no recomputation in Phase 1.
- **`ResourceRate` / `Crew`** — manhour rate(s) and crew roles with counts (E&I Technician, Helper, Engineer, Supervisor, QC roles), captured from the costing workbooks' resource tables.
- **Deferred but named** (forward-compatible, not implemented in Phase 1): `RateLibrary`, `PricedEstimate`, `RollUp`, `Proposal`.

### Discipline config (`config/disciplines/*.yaml`)

Declares, as data: canonical column roles and their synonyms, discipline/section codes (3A–3G …), area codes, UoM vocabulary, and currency / manhour-rate defaults. A new discipline or area = a new/edited YAML file, no code change.

---

## 6. Phase-1 deliverable

`cost-est ingest ./data/cost-estimation-data` produces a validated `cost_dataset.json` containing: classified documents; work packages; cost items with their (as-stated) rate build-ups; resource rates and crew composition; and the blank-vs-priced distinction. Every figure is traceable to a workbook / sheet / cell.

---

## 7. Testing strategy

Tests use the real `./data/cost-estimation-data` as fixtures. LLM-dependent tests (header mapping) run against the **mock adapter** so CI needs no API key.

1. **Item arithmetic (hard requirement).** For every populated cost item, `total == quantity × unit_price` (within rounding tolerance).
2. **Rollup reconciliation (hard requirement).** For each costing workbook, the sum of item/work-package values equals that workbook's own **Summary sheet** totals (Total Manhours, Materials, Consumables, Installation, Total Value). This uses the built-in rollups as ground truth — stronger and more deterministic than the procurement CS benchmark.
3. **Classification.** Every file and sheet lands in the correct `(doc_type, discipline, area, revision)`, including the blank Schedule of Prices vs populated costing workbooks, and area codes (TF/IA/JPS/JD).
4. **Header mapping.** `header_mapper` maps representative messy/merged/bilingual header blocks to the correct canonical column roles; runs against the mock adapter, with the Anthropic adapter live-tested (skipped when no key present).
5. **Blank-vs-priced integrity.** Blank-template items carry quantity but no `rate_buildup` and `priced == False`; populated items carry a full build-up and `priced == True`.

---

## 8. Explicit assumptions and open items

- **Bilingual descriptions** (EN + IT) appear in the Schedule of Prices; Phase 1 captures both text columns as-is without translating.
- **Blank-priced template** lines are ingested with quantity only; applying rates to them is the later pricing phase, not Phase 1.
- **Area code vocabulary** (TF/IA/JPS/JD) is captured from the data into config; its business meaning (e.g. which physical area/unit each denotes) is recorded as data and interpreted in later phases.
- **`.docx` proposal template and SOW `.pdf`** are indexed in Phase 1 but not parsed; proposal generation and method-of-measurement enforcement are later phases.
- **`ANTHROPIC_API_KEY`** must be present for live header-mapping tests; absent it, tests fall back to the mock adapter and live tests skip.
- **Shared-code promotion:** `shared/llm` and `shared/provenance` are extracted from the procurement design. If the procurement pipeline is not yet implemented, this phase creates those shared modules; if it is, this phase refactors toward the shared location.

---

## 9. What Phase 1 explicitly does NOT do

Building a reusable rate library; pricing the blank Schedule of Prices (applying rates to quantities); computing should-cost estimates; discipline/project roll-up beyond echoing as-stated Summary values; generating the proposal from the `.docx` template; enforcing SOW methods of measurement. Each is a later spec → plan → build cycle consuming this dataset.
