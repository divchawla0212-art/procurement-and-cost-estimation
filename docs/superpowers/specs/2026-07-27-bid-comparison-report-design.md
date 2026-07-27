# Design: Bid Comparison & Completeness Report — Phase 2

**Date:** 2026-07-27
**Status:** Approved design, pre-implementation
**Scope:** Phase 2 only. Consumes the validated `dataset.json` produced by Phase 1 and emits a Comparative Statement plus a completeness report. Scoring, ranking, and award recommendation are explicitly out of scope.
**Amends:** `2026-07-25-procurement-ingestion-phase1-design.md` — see §3.1.
**Follows:** the implemented `cost_estimation/` package and the `shared/` package as the house pattern — see §4.1.

---

## 1. Context and goal

Phase 1 ingests tender documents into a structured, provenance-carrying dataset. Phase 2 turns that dataset into the two artefacts a buyer's evaluation team actually works from:

1. A **Comparative Statement** in the same shape as the hand-built `Comparative Statement (CS) - Gas Generators [Rev.2].xlsx`.
2. A **completeness report** naming everything missing, pending, unverified, or not comparable.

> This design is client-agnostic. The dataset in `./data/procurement-data` is one concrete instance used to ground and test the pipeline; nothing in the tool, schema, or configuration is tied to a particular buyer organization or end project.

**The tool does not choose a winner.** It presents every bid faithfully, states what is missing, and leaves selection to the buyer. This is a deliberate constraint, not a deferral: it removes any need for weighting, gating, or ranking logic, and it keeps the output defensible because the tool asserts only what a source document says.

### Design principles (inherited from Phase 1, still binding)

- **Commodity-agnostic.** Report rows, required scope, and column roles are declared in config. A new category is a new YAML file, not new code.
- **Auditability.** Every value traces to a source document, and now additionally to **the party that asserted it** (§4.2).
- **Validation.** Extracted line items reconcile to each vendor's own stated totals.

---

## 2. Data findings (grounding)

Confirmed by exploring `./data/procurement-data`.

### 2.1 Folder location does not indicate authorship

Buyer documents sit inside vendor folders, because one identical RFQ package went to every vendor:

| File | Folder | Authored by |
|---|---|---|
| `ADN-AEC-ME-SPC-026 MR Gas Genset copy.pdf` | ADPOWER | Buyer — requirement spec |
| `Copy of ADN-AEC-ME-SPC-026_MR_Gas_Genset.xlsx` | MKON | Buyer — the same document |
| `DOD-…-16-00-004 DataSheet Gas Generator.pdf` | KERUI | Buyer — datasheet template |
| `DOD-…-16-00-004 DataSheet Gas Generator.xlsx` | MKON | Buyer — the same document |
| `Attachment-2 Vendor Deviation Form.pdf` | KERUI, MKON | Buyer — RFQ template |

A vendor folder tells you which bid a file arrived with. It never tells you who wrote it.

### 2.2 Authorship is a field-level property, not a document-level one

The filled datasheet is a multi-party ledger. Every row carries the requirement and the response side by side, with the column header declaring the author:

```
KERUI  DOD-…-004:
  Sr.No | DESCRIPTION | Project Requirements | Data Filled By Vendor | <Buyer> Response <date> | MOM <date>
                        ^ buyer requirement    ^ vendor claim          ^ buyer evaluation        ^ joint outcome

MKON   DOD-…-004:
  Sr.No | DESCRIPTION | Project Requirements | Data Filled By Vendor
```

One row spans lifecycle stages 1 → 3 → 4 → 5. This is what makes deviation detection computable: requirement and response are adjacent and separately attributed. Row 1.9 reads `Design Life | 06 years | 60 – 80k as per pipeline gas quality` — the requirement is in years, the response in hours. The two vendors' copies of the same buyer template carry different column counts, so the column set must be discovered per document, never assumed.

### 2.3 The completeness report already exists as a human artefact

The buyer's evaluation column contains a hand-written gap list — folder "Pending Compliance" not filled, catalogue and compliance certificate not submitted, emission data not responded, synchronisation scheme not responded. This is exactly the output Phase 2 must generate, and it serves as the ground-truth fixture for the gap engine the way the CS serves the comparison report.

### 2.4 The CS is a hand-adjusted artefact

Carried forward from Phase 1 §2 and still governing: KERUI's quotation states $478,000/unit while the CS carries $585,000; ADPOWER quoted EUR (€1,110,836) and the CS shows USD with a 10% discount and VAT extracted. The CS is a **reference benchmark, not extraction ground truth**.

### 2.5 Stated final values are not like-for-like

Each column's arithmetic reconciles internally, but each sums a different bundle:

| Bid | Final value (USD) | What that figure contains |
|---|---:|---|
| KERUI (Chinese Brand) | 930,000 | base + PEMS; no tools, no spares |
| ADPOWER | 1,134,487.07 | base + VAT 5% − 10% discount; no options line |
| KERUI (Waukesha) | 1,300,000 | base + PEMS + tools + 2yr spares |
| MKON (KAN) | 1,725,273.09 | base + freight; spares folded into base |
| AESL | 1,840,000 | base + $490,000 balance of plant |

---

## 3. Decisions locked

| Decision | Choice | Rationale |
|---|---|---|
| Scoring / ranking / award | **Excluded** | The buyer selects. The tool reports and flags only. |
| Compliance handling | Reported as extracted text + a gap flag | No gate, no score. Disclosure only. |
| Landed-cost inputs | Buyer override → config lane table → gap | Deterministic and sourced; unattended runs still possible. |
| Missing-lane behaviour | Bid marked `NOT_COMPARABLE`, still reported | Never silently guess a logistics cost. |
| Scope basis | **Dual** — core and leveled, side by side | Shows how much of the picture rests on imputed prices. |
| Imputed prices | Marked as imputed, with the source of the estimate | A leveled figure must never look like a quoted one. |
| FX | Pinned rate + as-of date in config, never a live lookup | A report must regenerate identically next month. |
| Report shape | Rows declared in commodity config | CS row labels are commodity-specific; code stays generic. |
| Authorship | Three-level model (§4.2) | Folder location is not authorship. |

### 3.1 Amendments to the Phase 1 design

Three corrections, all prerequisites for Phase 2, to be folded into Phase 1's implementation plan. Phase 1 is specified but not yet implemented — no `procurement/` package exists — so these are cheap to absorb now.

**a. Authorship.** Phase 1 assigns `Document.vendor` during classification. Per §2.1 this is wrong for buyer documents sitting inside vendor folders: the spec in `ADPOWER/` is not ADPOWER's.

- `Document.vendor` means **"arrived in this bid folder"**, not "authored by".
- A new `Document.origin` field carries authorship (§5).
- Buyer-authored documents are excluded from a bid's own line-item extraction, so a blank RFQ template is never read as a vendor's priced submission.

**b. The LLM abstraction already exists.** Phase 1 §4.3 specifies building `procurement/ingestion/llm/` with per-provider adapters. That work is done and lives in `shared/llm/` — `interface.py`, `factory.py`, and Anthropic, Bedrock, Gemini, OpenAI and mock adapters, with versioned prompts in `shared/llm/prompts/`. Procurement consumes it; it does not rebuild it. The protocol method is `classify_structure(prompt, output_schema, context_text, images=None)`, not the `extract_structured` name Phase 1 used.

**c. `ProvenanceRef` needs a `page` field.** The shared model carries `document_path`, `sheet`, `cell`, `extractor`, `prompt_version`. Cost estimation is workbook-only so it never needed pagination, but procurement's evidence is largely PDF and Phase 1 already promised page-level provenance. Adding an optional `page: int | None` to `shared/provenance.py` is backward-compatible and must land before extraction work starts.

---

## 4. Architecture

### 4.1 Project structure

`procurement/` mirrors the implemented `cost_estimation/` package rather than inventing a parallel shape — same `models/schema.py` + `ingestion/` + `config/loader.py` + `cli.py` layout, same reliance on `shared/`:

```
shared/                       # EXISTS — reused, not rebuilt
  provenance.py               #   + optional `page` field (§3.1c)
  llm/                        #   interface, factory, 5 adapters, versioned prompts
    prompts/party_map_v1.txt  # NEW prompt, existing mechanism

procurement/                  # NEW package
  models/schema.py            # one module, as in cost_estimation
  ingestion/
    classifier.py             # doc_type + origin        (authorship L1)
    column_mapper.py          # column -> party + role    (authorship L2)
    loaders.py                # pdf text-layer + xlsx
    extractor.py
    reconcile.py
    baseline.py               # cross-folder dedup        (authorship L3)
  comparison/
    rowmap.py                 # line items -> canonical report rows
    fx.py                     # conversion at a pinned rate
    adjust.py                 # landed-cost adjustments (override/lane/gap)
    gaps.py                   # completeness engine
    render.py                 # xlsx emitter (CS layout + gap annex)
  config/
    loader.py                 # mirrors cost_estimation/config/loader.py
    commodities/gas_genset.yaml
    logistics/lanes.yaml
  cli.py                      # `procurement report`
```

Authorship is not a separate package: level 1 is document classification and level 2 is layout mapping, both of which are ingestion concerns in the existing house pattern. Level 3 is an aggregation step and sits beside them.

### 4.2 Authorship resolution — three levels

**Level 1 — document origin.** Resolved by signal, strongest first:

1. **Document-number scheme.** Buyer documents carry the buyer's numbering; vendor documents carry the vendor's own (`ADP-13158-2024-935`, `ZDG2024110701`, `AESL-GTC-60808`). Patterns are declared in config as regexes, so no client's scheme is hard-coded.
2. **Cross-folder recurrence.** A document whose normalized id or content hash appears in two or more vendor folders is buyer-issued — vendors do not share files. This signal is free and needs no configuration.
3. **Filename markers.** `commented` → buyer annotation; `MOM` → joint record; `Superseded` → revision status.

Origins: `BUYER_REQUIREMENT`, `RFQ_TEMPLATE`, `VENDOR_SUBMISSION`, `BUYER_ANNOTATION`, `JOINT_RECORD`. Where signals conflict, the document is marked `ORIGIN_UNCERTAIN` and raised as a gap rather than guessed.

**Level 2 — field authorship.** This reuses the existing header-mapping mechanism rather than a new one. `cost_estimation/ingestion/header_mapper.py` already sends a sheet preview through `LLMClient.classify_structure` with a versioned prompt and validates the result into a layout model. `column_mapper.py` does the same with a new `party_map_v1.txt` prompt, returning a `DatasheetLayout` that tags every column with a party and a role.

Config declares the canonical role vocabulary and its header aliases, in the same `dict[str, list[str]]` shape `DisciplineConfig.column_roles` already uses:

```yaml
column_roles:
  requirement: ["Project Requirements", "Project Requirement"]
  response:    ["Data Filled By Vendor", "Vendor Response"]
  evaluation:  ["Response"]            # buyer evaluation column, usually date-suffixed
  resolution:  ["MOM"]                 # joint meeting outcome, usually date-suffixed
role_parties:
  requirement: BUYER
  response:    VENDOR
  evaluation:  BUYER
  resolution:  JOINT
```

LLM mapping is used rather than pure regex because the column set genuinely varies per document (§2.2 — two copies of one buyer template carry different column counts, and evaluation columns are date-suffixed). Aliases give the mapper a controlled vocabulary and make its output assertable in tests. An unmatched column is preserved and flagged, never dropped or silently attributed.

**Level 3 — baseline reconciliation.** All `BUYER_REQUIREMENT` content is deduplicated across folders into one canonical requirement set. Where copies disagree on revision — one folder holds both a plain and a "Superseded with MOM" copy — the conflict is raised as `SUPERSEDED_SOURCE`, not silently resolved to the newest.

### 4.3 Comparison pipeline

```
dataset.json (Phase 1)
   → resolve authorship        (origin, field party, baseline)
   → map line items to canonical report rows   [config: report_rows]
   → convert currency          (pinned rate + as-of date)
   → apply landed adjustments  (override -> lane -> gap), emitted as visible rows
   → compute dual basis        (core = as quoted; leveled = to required scope)
   → detect gaps               (§4.4)
   → render                    comparative_statement.xlsx + gap annex + evaluation.json
```

Adjustments are always emitted as their own rows. Nothing is folded silently into a total.

### 4.4 Gap taxonomy

| Gap | Detected from |
|---|---|
| `MISSING_DOCUMENT` | required doc type absent for a bid |
| `PENDING_ATTACHMENT` | document classified but marked pending |
| `MISSING_SCOPE_ITEM` | required report row has no line item |
| `UNANSWERED_REQUIREMENT` | buyer requirement cell populated, vendor response cell empty or non-committal |
| `UNRESOLVED_COMPLIANCE` | compliance fact recorded but not concluded |
| `NO_LANDED_BASIS` | no buyer override and no matching lane |
| `RECONCILIATION_FAILURE` | line items do not sum to the vendor's stated total |
| `SUPERSEDED_SOURCE` | value read from a superseded revision, or baseline copies disagree |
| `ORIGIN_UNCERTAIN` | authorship signals conflict |

Every gap carries the `ProvenanceRef` of what triggered it, so each flag traces back to a document, page or cell.

---

## 5. Data schema (additions)

All models are pydantic `BaseModel`s in a single `procurement/models/schema.py`, matching `cost_estimation/models/schema.py`. Provenance is `shared.provenance.ProvenanceRef` — imported, not redefined.

- **`Party`** — enum: `BUYER`, `VENDOR`, `JOINT`.
- **`DocumentOrigin`** — enum per §4.2 level 1. Added to `Document`; `Document.vendor` is redefined as folder provenance only.
- **`FieldAssertion`** — `requirement_id`, `party`, `role` (requirement / response / evaluation / resolution), `value`, `provenance`. The unit that makes §2.2 representable.
- **`RequirementBaseline`** — the deduplicated canonical requirement set, with `source_documents` and any revision conflicts attached.
- **`NormalizationAdjustment`** — `bid`, `kind` (freight / insurance / duty / clearance / fx / vat / discount / scope_add / scope_remove), `amount`, `currency`, `origin` (`BUYER_OVERRIDE` / `CONFIG_LANE` / `IMPUTED`), `note`, `provenance`.
- **`ComparisonRow`** — `row_id`, per-bid cells for core and leveled bases, each cell flagged `quoted` or `imputed`.
- **`Gap`** — `kind` (§4.4), `bid`, `subject`, `detail`, `provenance`.

`Score` remains named but unimplemented, as in Phase 1. Nothing in Phase 2 produces one.

---

## 6. Phase-2 deliverable

`procurement report ./data/procurement-data` produces:

1. `comparative_statement.xlsx` — CS Rev.2 layout, core and leveled column sets, adjustments as visible rows, imputed cells marked.
2. Gap annex sheet — one row per gap, keyed to vendor and source document.
3. `evaluation.json` — the same content structured, for downstream use.

No ranking, no recommendation, no selected vendor.

---

## 7. Testing strategy

Real `./data/procurement-data` as fixtures; LLM-dependent paths use the mock adapter so CI needs no API key.

1. **Authorship classification.** Every file resolves to the correct origin. Specifically: the spec copies in the ADPOWER and MKON folders classify as `BUYER_REQUIREMENT`, not as those vendors' submissions; the deviation-form templates classify as `RFQ_TEMPLATE`; `…commented.pdf` as `BUYER_ANNOTATION`.
2. **Cross-folder dedup.** The five folders yield exactly one requirement baseline, and the plain-versus-superseded spec pair raises `SUPERSEDED_SOURCE`.
3. **Field authorship.** Both datasheet copies parse to the right party per column despite differing column counts, and row 1.9 yields a requirement/response pair.
4. **Gap engine against the human artefact.** Gaps generated for the KERUI bid are compared against the buyer's hand-written evaluation column (§2.3). Divergences are reported and explained, not asserted equal — the human list is a benchmark, not a schema.
5. **Reconciliation.** Carried forward from Phase 1: line items sum to each vendor's stated totals.
6. **Determinism.** Two runs over an unchanged dataset produce byte-identical `evaluation.json`, which the pinned FX rate exists to guarantee.

---

## 8. Assumptions and open items

- **No lane data exists yet.** Until `lanes.yaml` is populated or overrides supplied, FCA-origin bids will carry `NO_LANDED_BASIS` and appear as not comparable on the leveled basis. This is the intended behaviour, not a defect.
- **Imputed scope prices** use the median of other bids' quotes for the same item. With five bids this is thin; the figure is always marked imputed so a reader can discount it.
- **`ORIGIN_UNCERTAIN` is expected to be non-empty** on first run. The config regex patterns are tuned against the sample dataset and will need extension for other tenders.
- **AESL submitted one document.** It will produce a large number of `MISSING_DOCUMENT` gaps. That is a correct result, not a failure to extract.
- **Earlier interim decisions are void.** A leveled-basis "headline score" and a rank-sensitivity flag were discussed before scoring was removed from scope. Neither exists; there is no ranking to be sensitive to.
- **`./data/cost-estimation-data`** is out of scope here. It is already covered by `2026-07-25-cost-estimation-ingestion-phase1-design.md` and served by the implemented `cost_estimation/` package; this spec touches neither, beyond reusing `shared/`.
- **Changes to `shared/` affect cost estimation.** The `page` field in §3.1c is additive and optional, so existing `cost_estimation` call sites are unaffected, but its test suite should run before that change is merged.

---

## 9. What this phase explicitly does NOT do

Weighted scoring, compliance gating, vendor ranking, award recommendation, parametric or bottom-up should-cost estimation, deep-parsing of drawings, and PDF report export. Any of these is a later spec → plan → build cycle consuming Phase 2's output.
