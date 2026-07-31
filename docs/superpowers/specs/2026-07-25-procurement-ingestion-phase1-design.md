# Design: AI Procurement & Cost Estimation Tool — Phase 1 (Ingestion + Structured Dataset)

**Date:** 2026-07-25
**Status:** Approved design, pre-implementation
**Scope:** Phase 1 only. Normalization, comparison, scoring, and reporting are named here but specified in their own later cycles.

---

## 1. Context and goal

An EPC contractor (Astra) ran a competitive tender for **2 gas generator sets** (engine + catalytic converter) for the ADNOC Gas Buhasa project. Four vendors submitted **five bids** (KERUI submitted two engine variants). The `./data` directory holds the real tender documents. Final bid values range **~$930K to ~$1.84M for the same 2 units** because quotes mix VAT-in/out, freight in/out, spares included/optional, and differing delivery terms (FCA/EXW/CIF/site).

The overall tool ingests vendor tender documents, extracts structured bid data, normalizes it against a requirement spec, and produces a defensible cost estimate and award recommendation. **This document specifies only Phase 1: ingestion and a clean, validated, structured dataset for the five existing bids.** It is the foundation every later phase builds on.

### Design principles (hard requirements, all phases)

- **Commodity-agnostic.** Gas gensets are the first case. Schema, extraction, and (later) normalization/scoring generalize to other categories via config, not hard-coding.
- **Auditability.** Every derived number traces to a source document (+ later, the adjustments applied). Procurement decisions get contested.
- **Provider-swappable LLM.** All LLM calls sit behind one interface; the model/provider is switchable by config with zero code change.
- **Validation.** Extracted line items must reconcile to each vendor's own stated totals.

---

## 2. Data findings (grounding)

Confirmed by exploring `./data`:

- **4 vendor folders, 5 bids.** KERUI submitted two variants: Waukesha-Canada engine and a cheaper Baudouin-China "Chinese Brand".
- **Folder richness varies sharply:** KERUI (21 files, 32 MB — quotation, commented datasheet, BOM, consumption/spares/tools lists, deviation form, commented P&ID, SLD, GA drawings) is the richest and the best bottom-up candidate; ADPOWER (6 files — quotation `935` + `935(Rev1)`, BOM, MOM, superseded spec); MKON/KAN (8 files — quotation, deviation form, load list, **native Excel** datasheet + spec copy, MAN gas-quality docs); AESL (**1 file** — a techno-commercial proposal only, no BOM, no datasheet).
- **Comparative Statement** (`CS [Rev.2]`, single sheet) is the human-built normalized comparison. Five bid columns with base price, optional PEMS/tools/spares, freight, VAT-included flag (ADPOWER=YES, rest=NO), discount %, delivery time/terms, payment terms, engine make, and a Technical Feedback row.

Two findings that shape the design:

1. **Compliance gating is data-derived, never hard-coded.** The build prompt's example ("AESL fails H2S ≥50ppm") does **not** match the data. The CS actually flags **MKON/KAN** on H2S (equipment rated ≤30ppm; spec requires 50ppm; SOx also not complied) and **AESL** on site condition (55°C not complied). The tool must extract compliance facts from documents.
2. **The CS does not reconcile to the raw quotes.** KERUI's quote states $478,000/unit FCA ($956K total); the CS lists KERUI base at $585,000/unit ($1,170,000). ADPOWER quoted in **EUR** (€1,110,836); the CS shows USD with a 10% discount and VAT extracted. The CS is a *hand-adjusted* artifact. Therefore the CS is a **reference benchmark, not literal extraction ground truth** (see §7).

---

## 3. Decisions locked

| Decision | Choice | Rationale |
|---|---|---|
| Language/stack | Python 3.12 | Prompt default; best PDF/Excel/LLM ecosystem. |
| First LLM provider (live-tested) | Anthropic Claude API | Best doc-extraction quality; this is Claude Code. Needs `ANTHROPIC_API_KEY`. |
| Multi-provider | **Feature, not future work** — Anthropic, OpenAI, Gemini adapters + mock, all behind one protocol, selected by config | Prompt requires provider swappability. |
| Phase-1 scope | Ingestion + validated structured dataset only | Prompt: "don't build everything at once." |
| CS role in tests | Reference target, not literal ground truth | CS is hand-adjusted and doesn't reconcile to quotes. |

---

## 4. Architecture

### 4.1 Project structure

```
procurement/
  models/            # pydantic schemas — the contract every module speaks
  ingestion/
    classifier.py    # doc -> (stage, vendor, variant, revision, doc_type)
    loaders.py       # pdf text-layer + xlsx readers
    extractor.py     # orchestrates: load -> LLM-structure -> validate
    reconcile.py     # line items vs stated totals
    llm/
      interface.py         # LLMClient protocol (provider-agnostic)
      factory.py           # get_client(config) -> LLMClient
      anthropic_client.py  # built + live-tested first
      openai_client.py     # implemented, mock/recorded-tested
      gemini_client.py     # implemented, mock/recorded-tested
      mock_client.py       # deterministic, key-free, for CI
      prompts/             # versioned, provider-neutral prompt templates (v1/…)
  config/
    commodities/gas_genset.yaml   # commodity-agnostic knobs
  cli.py             # `procurement ingest ./data -> dataset.json`
tests/               # uses ./data as fixtures
docs/superpowers/specs/
```

### 4.2 Ingestion pipeline (hybrid, document-type-routed)

```
files in ./data
   -> classify        (stage, vendor, variant, revision, doc_type)
   -> route by doc_type
        commercial/compliance docs  -> load text-layer
                                       (vision fallback if no/low text)
                                    -> LLM extract_structured -> validate
        native Excel (MKON dsheet,  -> openpyxl direct read (no LLM)
          CS)
        drawings (P&ID/SLD/GA/      -> classify + index only (deep-parse
          nameplate)                   deferred to later phase)
   -> reconcile line items vs stated totals
   -> assemble validated dataset.json (5 bids, with provenance)
```

Rationale:
- **Route by doc type first** so we spend LLM budget only where structure must be inferred.
- **Text-layer first, vision fallback.** `pdftotext` confirmed working on these PDFs; only pages lacking a usable text layer fall back to provider vision. Cheaper and more deterministic than vision-everything.
- **Native Excel read directly** (no hallucination risk).
- **Drawings indexed, not parsed** in Phase 1 — they are visual/bottom-up inputs for later phases.

Rejected: *vision-everything* (costly, overkill for text PDFs); *regex/template parsing* (vendor layouts differ — brittle, defeats the LLM purpose).

### 4.3 LLM provider abstraction (switchable feature)

- **`LLMClient` protocol** in `interface.py`: one narrow method, roughly
  `extract_structured(prompt: str, output_schema: type[BaseModel], context_text: str, images: list | None = None) -> dict`.
  The extractor never knows which provider is behind it.
- **Adapters** implement the protocol and each hides its provider's structured-output mechanism:
  - Anthropic → tool-use
  - OpenAI → `response_format` JSON-schema / function calling
  - Gemini → `responseSchema`
  - Mock → returns deterministic canned structures keyed by input, no key needed
- **`get_client(config)` factory** reads `LLM_PROVIDER` (`anthropic|openai|gemini|mock`), `LLM_MODEL`, and the matching `*_API_KEY`. Switching providers = one env var, no code change.
- **Capability flags** per adapter (e.g. `supports_vision`) so the vision-fallback router degrades gracefully when a configured model can't take images.
- **Prompts are provider-neutral and versioned**; the same prompt text feeds every adapter — only the structured-output plumbing differs.

**Phase-1 test posture:** protocol + factory + config make all providers selectable. The **Anthropic adapter gets full live-tested coverage**; OpenAI and Gemini adapters ship implemented and are unit-tested with mock/recorded responses. Promoting either to a live path later is an isolated follow-up.

---

## 5. Data schema (Pydantic, `procurement/models/`)

Raw, **as-stated** values only in Phase 1 — no normalization applied yet.

- **`Document`** — `path`, `stage` (1–7 lifecycle enum), `vendor`, `variant`, `revision`, `doc_type` (quotation / bom / datasheet / deviation_form / mom / spec / drawing / list / other), `extraction_status`. Every extracted fact points back to one.
- **`ProvenanceRef`** — `document_path`, `page` or `cell`, `extractor` (e.g. `anthropic:v1` or `xlsx`), optional `prompt_version`. Attached to every derived value.
- **`Vendor`** — id, name.
- **`Bid`** — `(vendor, variant, revision)` identity; `currency`, `delivery_time`, `delivery_terms` (FCA/EXW/CIF/site), `payment_terms`, `source_documents`, `line_items`, `compliance_facts`.
- **`LineItem`** — `description`, `qty`, `unit_price`, `total`, `currency`, `category` (base / option / spare / tool / freight / tax_vat / discount), `is_optional`, `provenance`. Values exactly as stated in the source.
- **`ComplianceFact`** — captured evidence only (deviation-form entries, MOM notes, "not complied" statements): `topic`, `statement`, `source` provenance. **Not evaluated or gated in Phase 1** — that is the scoring phase.
- **Deferred but named** (forward-compatible, not implemented in Phase 1): `NormalizationAdjustment`, `Score`, `RequirementSpec` (as an evaluated object). The `gas_genset.yaml` config captures requirement thresholds as data now so later phases can consume them.

### Commodity config (`config/commodities/gas_genset.yaml`)

Declares, as data: expected scope categories, compliance requirement fields (H2S/SOx/site-temp thresholds and their required values), unit basis (per-kW), default currency. A new commodity = a new YAML file, no code change.

---

## 6. Phase-1 deliverable

`procurement ingest ./data` produces a validated `dataset.json` containing all five bids: classified documents, structured line items (each with provenance), bid metadata (currency/delivery/payment), and captured compliance facts. Every number is traceable to a source document.

---

## 7. Testing strategy

Tests use the real `./data` as fixtures. LLM-dependent tests run against the **mock adapter** so CI needs no API key.

1. **Reconciliation (hard requirement).** For each bid, extracted line items sum to that vendor's **own stated totals** (base, options, grand total as stated). This is the primary correctness gate and does not depend on the CS.
2. **Classification.** Every file in `./data` lands in the correct `(stage, vendor, variant, revision, doc_type)`. Includes the KERUI two-variant split and the `935` → `935(Rev1)` revision lineage.
3. **CS sanity benchmark (reference, not equality).** Compare the structured dataset (and, in later phases, the normalized output) against the CS. Report and explain divergences (e.g. the KERUI $478K-vs-$585K gap, ADPOWER EUR→USD + discount + VAT) rather than asserting exact equality. Documents *why* the tool differs from the hand-built CS.
4. **Provider abstraction.** Factory selects the right adapter per `LLM_PROVIDER`; mock adapter yields deterministic output; Anthropic adapter live-tested (guarded/skipped when no key present).

---

## 8. Explicit assumptions and open items

- **AESL** has no BOM/datasheet — its bid will be structurally thin (quotation-only). The dataset must represent low-evidence bids without failing; downstream scoring can weight evidence completeness.
- **KERUI drawings/BOM** are indexed in Phase 1 but not deep-parsed; the bottom-up should-cost estimate that consumes them is a later phase.
- **`ANTHROPIC_API_KEY`** must be present in the environment for live Anthropic extraction; absent it, tests fall back to the mock adapter and live tests skip.
- **Lifecycle stage tagging** (1–7) is captured on each `Document` but full stage-workflow orchestration (revision supersession logic, MOM-driven scope changes) is surfaced as data in Phase 1 and acted on in later phases.

---

## 9. What Phase 1 explicitly does NOT do

Normalization to a common basis; regenerating the Comparative Statement; parametric/bottom-up should-cost estimation; weighted vendor scoring and award recommendation; Excel/PDF report export. Each is a later spec → plan → build cycle consuming this dataset.
