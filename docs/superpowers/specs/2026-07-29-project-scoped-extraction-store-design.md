# Design: Project-Scoped Extraction Store, RFQ Requirements & Compliance Matching

**Date:** 2026-07-29
**Status:** Approved design, pre-implementation
**Supersedes:** the `dataset.json` run-output model from `2026-07-28-procurement-comparison-portal-design.md` §3.1

---

## 1. Context and goal

The MVP portal extracts one commercial quotation per vendor and writes the whole run to a single `dataset.json` at the end. That model has four limits this design removes:

1. **Nothing is stored per vendor or per document.** Results accumulate in memory and are written once, so an interrupted run loses everything and a re-run re-pays for every vendor.
2. **The requirements document is filed but never parsed**, so no bid can be checked against what was actually asked for.
3. **There is no way to correct a wrong extraction.** A bad value can only be fixed by editing the source document or the output file, and the next run silently overwrites it.
4. **There is no history.** Each run overwrites the last, so there is no record of what the comparison said on the date of award.

This design delivers a **project-scoped store** holding everything extracted from both the client RFQ and the vendor documents, which can be **inspected, corrected, and re-run incrementally over time**, plus the **requirement extraction and compliance matching** that make the RFQ side meaningful.

`data/procurement-data` (ADPOWER, AESL, KERUI, MKON) is the concrete test instance: 34 PDFs, 169 pages, ~90k tokens of extractable text, six image-only scans, and two real revision chains.

### Success criteria

1. A completed run stores every extracted fact, its source document, and its provenance, per project.
2. Re-running an unchanged project performs **zero** LLM extraction calls; changing one document re-extracts exactly that document.
3. A value corrected in the portal survives later runs, and a disagreeing re-extraction is surfaced as a conflict rather than silently applied or silently discarded.
4. The MR is decomposed into requirement records, and every requirement × vendor pair carries a verdict, with missing evidence reported as `unanswered` and never as `fail`.
5. Deleting the SQLite index changes no query result.
6. `events.jsonl` reconstructs what the store said at any past run.

---

## 2. Decisions locked (from brainstorming)

| Decision | Choice |
|---|---|
| Scope | Store **+** RFQ requirement extraction **+** compliance matching |
| Substrate | **Hybrid** — JSON snapshots authoritative, SQLite as a derived, disposable index |
| Document coverage | **Route by document class** — classify each vendor file, run a purpose-built extractor per class |
| Conflict rule | **Override wins, conflict surfaced** |
| RFQ grain | **All clauses, tiered** into `auto` (machine-checkable) and `judgement` |
| Edit surface | **In the portal UI**, writing override records to the store |
| Update needs | Human corrections surviving re-runs · audit history · incremental re-runs · document revision tracking |

---

## 3. Architecture

```
procurement/
  store/
    __init__.py
    layout.py       # path helpers, generation counter, atomic snapshot writes
    models.py       # DocumentRecord, RequirementRecord, FactRecord, Deviation, Override, Event
    snapshots.py    # read/write the JSON snapshots
    events.py       # append-only event log
    index.py        # SQLite: sole owner of store.db; query fns + rebuild()
    migrate.py      # one-shot dataset.json -> store import
  classify.py       # document -> doc_class (rules first, LLM for leftovers)
  revisions.py      # revision label parsing + supersession resolution
  extract_tech.py   # datasheet -> FactRecord[]
  extract_deviation.py
  extract_requirements.py   # MR -> RequirementRecord[]
  extract_mom.py    # MOM -> requirement amendments
  compliance.py     # (requirements, facts, deviations) -> ComplianceResult[]
  units.py          # unit normalisation table + conversion
portal/
  app.py            # shell + navigation only
  views/            # setup, documents, requirements, facts, compliance, history
```

Unchanged and reused: `shared/llm/` (provider-swappable `LLMClient`, mock adapter for CI), `shared/provenance.py`, `loaders.py`, `pdf_llm.py`, `normalize.py`, `compare.py`, `export.py`, and `extract.py` with its `bid_extract_v1` prompt.

### 3.1 Storage layout

```
projects/<slug>/
  project.json                      # + store_version, generation
  requirements/<file>               # source MR (unchanged)
  vendors/<vendor>/<files>          # source vendor docs (unchanged)

  store/                            # authoritative, plain JSON, git-diffable
    documents.json
    requirements.json
    vendors/<vendor>/facts.json
    compliance.json
    events.jsonl                    # append-only

  index/store.db                    # derived, disposable, gitignored
```

**Three roles, no overlap.** Snapshots answer *what is true now*. `events.jsonl` answers *how it got that way*. The index answers *quickly*.

### 3.2 The index contract

Approach C is only safe if the index can never become a second source of truth. Five rules enforce that:

1. `project.json` carries a monotonic `generation`, bumped **once per successful write transaction** — one ingestion run, or one UI edit — not once per file touched.
2. `store.db` has a `meta` table holding `built_from_generation` and the mtime/size of each snapshot it was built from.
3. Every query checks generation and snapshot stats first, and rebuilds before answering on any mismatch. Stale reads are structurally impossible, not merely discouraged.
4. `store/index.py` is the **only** module that opens `store.db`. It exposes query functions and `rebuild()`; nothing else writes to it.
5. `index/` is gitignored, and deleting it is a no-op with a millisecond rebuild cost.

Full rebuild at this scale (~100 requirements × 4 vendors × ~40 facts) is a few thousand inserts, so incremental index maintenance is deliberately not built.

Index failure is **non-fatal**: on any error, fall back to reading snapshots directly and log a warning. A cache must never be able to break the app.

### 3.3 Atomic writes

Every snapshot write goes to `<name>.tmp` then `os.replace()`. This also closes the existing `dataset.json` corruption window, where `open(..., "w")` truncates before `json.dump` runs and an interrupted save destroys both the new and previous copies.

`dataset.json` is superseded by the store. First load of a project containing one imports it via `store/migrate.py`, idempotently, then leaves the file alone.

---

## 4. Record model

### `DocumentRecord`

| field | purpose |
|---|---|
| `doc_id` | first 12 hex of `sha256("<vendor or _rfq>/<relative path>")` — **path**-addressed, not content-addressed, so revision history attaches to a location and two vendors submitting a byte-identical file remain two distinct submissions |
| `path`, `vendor` | location; `vendor` is null for MR documents |
| `doc_class` | `quotation \| datasheet \| deviation \| bom \| drawing \| mom \| spec \| other` |
| `classified_by` | `rule` or `llm` |
| `revision_label`, `supersedes`, `superseded_by` | revision lineage |
| `content_sha256`, `text_source` | `pdftotext \| pypdf \| llm_transcription \| xlsx` |
| `extraction_status`, `notes` | `ok \| failed \| skipped` |
| `extracted_at`, `extractor`, `prompt_version` | provenance |

`content_sha256` **plus** `prompt_version` is the incremental cache key.

### `RequirementRecord`

`req_id`, `clause_ref` as printed in the MR, verbatim `text`, `category` (`technical \| commercial \| documentation \| testing \| codes`), `checkability` (`auto \| judgement`), `source_doc_id`, `amended_by`.

`auto` records additionally carry `parameter`, `operator` (`>= \| <= \| == \| in`), `value`, `unit` — so H2S ≥ 50 ppm becomes arithmetic rather than string matching.

### `facts.json` (per vendor)

Shaped to the real documents rather than forced into one schema:

- **`commercial`** — the existing `BidExtraction` from the quotation. Unchanged and already tested.
- **`technical`** — `FactRecord[]` from datasheets: `parameter`, `value`, `unit`, `verbatim` source snippet, `doc_id`.
- **`deviations`** — from Attachment-3: `clause_ref`, vendor statement, `comply \| deviate \| noted`, `doc_id`.

### Overrides

Each snapshot record carries a sibling `_overrides` list: `field_path`, human `value`, `extracted_value`, `author`, `at`, `reason`, and `conflict`. Resolved values stay clean in the record body, so files read normally and the override history sits beside them rather than wrapping every field.

`field_path` is a dotted path relative to the snapshot root, with list members addressed by their own id rather than by index — `commercial.base_price`, `technical[f-3a91].value`, `requirements[r-4-2-7].value`. Index-based paths are forbidden: list order is not stable across re-extractions, so an index-addressed override would silently reattach to the wrong record.

### Events

`events.jsonl`, append-only, each with `run_id`, `at`, `actor`: `run.started`, `run.finished`, `document.classified`, `document.extracted`, `document.skipped`, `field.overridden`, `override.conflicted`, `compliance.evaluated`.

---

## 5. Resolution, conflict, and incremental re-runs

**Resolution happens at write time, not read time.** Snapshots store already-resolved values, so reading the store is just parsing JSON.

Per field, when a run completes: the extracted value becomes current, unless an override exists for that field path — then the override stays current. If the fresh extraction disagrees with the `extracted_value` the override was recorded against, the entry gets `conflict: true`, both values are kept, and the pair is listed on the Conflicts screen.

Per document:

```
hash = sha256(file bytes)
prior = documents.json[doc_id]
if prior and prior.content_sha256 == hash
        and prior.prompt_version == current_version:
    skip — reuse stored facts, append document.skipped
else:
    extract → new facts
    reapply overrides; flag conflicts where extraction moved
    append document.extracted
```

`force=True` re-extracts regardless of hash, for when a prompt is edited without a version bump.

### 5.1 Revision and supersession

`revisions.py` parses `(Rev1)`, `Rev A`, `Rev.2`, and explicit "Superseded with…" from filenames. Where two documents share a normalised base name, the higher revision supersedes the lower.

This is load-bearing for the real data: `ADP-13158-2024-935.pdf` vs `ADP-13158-2024-935(Rev1).pdf`, and the MR marked *"Superseded with MOM 20241111"*. Superseded documents remain in the store for audit but are excluded from extraction and compliance by default.

---

## 6. Classification and routed extraction

`classify.py` runs two passes:

1. **Rules first** — the existing `quote_select` keyword vocabulary promoted into a class map. The real filenames are highly descriptive ("Attachment-2 Vendor Deviation Form", "DataSheet Gas Generator", "BOM.pdf", "LAYOUT - KGW550GF-T"), so rules carry most of the corpus deterministically and for free.
2. **Model for leftovers only** — filename plus the first ~500 characters. `classified_by` records which pass decided, so the model's calls can be audited separately.

| class | prompt | writes to |
|---|---|---|
| `spec` | `requirements_v1` (new) | `requirements.json` |
| `quotation` | `bid_extract_v1` (existing, unchanged) | `commercial` |
| `datasheet` | `tech_facts_v1` (new) | `technical` |
| `deviation` | `deviation_v1` (new) | `deviations` |
| `mom` | `mom_amend_v1` (new) | requirement amendments |
| `drawing`, `layout`, `p&id`, `nameplate` | none — `extraction: skipped` | — |

Drawings are recorded but never extracted: they are image-only, and transcribing a single-line diagram yields nothing a compliance check can consume. Recording them keeps document coverage honest instead of silently ignoring six of KERUI's files.

**MOMs amend requirements.** The real MR is explicitly superseded by a Minutes of Meeting, so ignoring MOMs would make the requirement baseline simply wrong. Amendments are separate records applied on top of base requirements with lineage preserved, so both the original clause and the meeting's change remain visible.

---

## 7. Compliance matching

`compliance.py`, for each (requirement, vendor):

- **`auto`** — look up the vendor fact by `parameter`, normalise units, apply the operator in Python → `pass` or `fail`. If the vendor's deviation form marks that clause deviated, the verdict is `deviation` regardless of the numbers.
- **`judgement`** — verdict `review`, with the clause text and candidate facts and deviation statements gathered for a human.
- **No matching fact** — verdict `unanswered`.

`unanswered` is never `fail`. A missing fact means the evidence was not found, not that the vendor failed; collapsing the two is how a compliance review becomes indefensible. It also serves as the extraction-coverage metric — a vendor at 40% unanswered means the pipeline did not read enough, and that is visible rather than hidden.

**Arithmetic stays in Python**, consistent with the rest of the pipeline: the model extracts numbers, code compares them. `units.py` handles ppm / mg·Nm⁻³, kW / MW, °C / °F, bar / kPa. Units that cannot be converted produce `unanswered` with a stated reason, never a silent pass.

**Ordering:** requirements extraction runs *before* technical extraction, and its parameter vocabulary is passed into the `tech_facts_v1` prompt, so the datasheet pass targets the parameters that will be checked. The parameter vocabulary is the join key between the two halves of the system; leaving it to chance would be the most likely cause of spurious `unanswered` verdicts.

`ComplianceResult`: `req_id`, `vendor`, `verdict` (`pass \| fail \| deviation \| unanswered \| review`), `fact_id`, `doc_id`, `rationale`, `evaluated_at`.

---

## 8. Portal review UI

`portal/app.py` is ~165 lines of top-to-bottom script and this work would roughly triple it, so it is split as targeted improvement — not opportunistic refactoring — into a navigation shell plus `portal/views/`.

| view | contents |
|---|---|
| Documents | class, revision, superseded, extraction status, text source — so misclassification is catchable |
| Requirements | editable clause records; add/remove/retype requirements |
| Facts | per vendor: commercial block, technical table, deviations |
| Compliance | requirements × vendors matrix, colour-coded, filterable to `fail`/`unanswered`/`review`, click through to source |
| Conflicts | every `conflict: true` override, accept-extraction / keep-override per row |
| History | rendered `events.jsonl`, filterable by run |

Editing uses `st.data_editor`. Each change writes an override entry plus an event, bumps `generation`, and atomically rewrites the snapshot. **Every edit requires a short reason** — an audit trail without rationale records what changed but not why, which is the half that matters later.

---

## 9. Error handling

- **Per-document isolation** extends the existing per-vendor pattern: a failed extraction marks that `DocumentRecord` failed with the error text, and the run continues.
- **Events are appended before snapshot writes**, so a crash leaves the log ahead of state — detectable and recoverable, never silently behind.
- **Index failure is non-fatal** (§3.2).
- **`project.status` becomes honest** — `done`, `done_with_failures`, or `failed`, replacing the current unconditional `"done"`, with per-document counts on `run.finished`.
- **Migration is idempotent.**

---

## 10. Testing

Mock-adapter path keeps CI key-free, per existing convention.

**Deterministic units:** classifier rules against the real filenames as fixtures · revision/supersession parsing · hash-skip logic · override resolution and conflict flagging · unit conversion · compliance operators including `unanswered` vs `fail` · atomic write, simulating a crash mid-write and asserting the previous snapshot survived.

**Index contract** — the tests that keep approach C honest:
- delete `store.db`, assert queries still return correct results
- stale `generation` triggers rebuild before answering
- nothing outside `rebuild()` opens the database writable

**The incremental proof:** run a fixture project with the mock client, re-run and assert **zero** extraction calls, then touch one file and assert **exactly one** re-extraction. This test is what stops the cache silently degrading into "re-extract everything".

**Migration:** existing `dataset.json` → store, values preserved.

**Live-guarded:** one real ADPOWER extraction, following the `a6d23cd` skip-guard pattern.

---

## 11. Build order

The design is one coherent system, but it is too large for a single implementation plan. Four phases, each leaving the app working and shippable on its own:

| phase | delivers | done when |
|---|---|---|
| **1 — Store foundation** | `store/` (layout, models, snapshots, events, atomic writes, index + contract, migration). Existing pipeline rewired to write to the store instead of `dataset.json`. No new extraction. | Re-running an unchanged project makes zero LLM calls; deleting `store.db` changes nothing; an existing `dataset.json` migrates cleanly |
| **2 — Classification & routed extraction** | `classify.py`, `revisions.py`, `extract_tech.py`, `extract_deviation.py` | Every vendor file is classified with lineage resolved; datasheets and deviation forms produce stored facts |
| **3 — Requirements & compliance** | `extract_requirements.py`, `extract_mom.py`, `units.py`, `compliance.py` | Every requirement × vendor pair carries a verdict, with `unanswered` distinct from `fail` |
| **4 — Portal review UI** | `app.py` split into shell + `views/`, six screens, override editing with mandatory reasons | A value corrected in the UI survives a re-run and a disagreeing re-extraction appears on the Conflicts screen |

Phase 1 alone already satisfies the original request's storage, incrementality and history requirements — the RFQ and compliance work builds on it rather than being entangled with it. Each phase gets its own implementation plan.

---

## 12. Out of scope

Deliberately excluded; these remain in `docs/future-improvements-roadmap.md`:

- **BOM line-item extraction and reconciliation.** BOMs are classified, and parameters they state land in `technical` facts, but full line-item extraction and the "numbers must add up" gate stay a separate roadmap item — folding it in would roughly double this cycle.
- **Weighted vendor scoring and award recommendation.** Compliance verdicts are produced; turning them into a ranked recommendation with configurable weights is the natural next cycle.
- **Full scope normalization** (delivery terms, spares/tools in-vs-out of base).
- **Multi-user, auth, hosting, and the database migration** of §2 of the roadmap. The `generation` counter and single-writer assumption are explicitly single-user.
- **Live FX rates**, comparative-statement templated export, cross-project queries.

---

## 13. Open risks

| Risk | Mitigation |
|---|---|
| Parameter vocabulary drift between requirements and facts leaves everything `unanswered` | Requirements run first and seed the tech prompt (§7); coverage metric makes drift visible immediately |
| Filename-based revision parsing misses a real supersession | Revisions surfaced in the Documents view and overridable like any other field |
| Classification errors route a document to the wrong extractor | `classified_by` recorded; Documents view is editable; misclassification is visible before it reaches compliance |
| Snapshot hand-edits bypass the generation counter | Index also records snapshot mtime/size and rebuilds on mismatch (§3.2) |
| Streamlit reruns during a long extraction | Run is already synchronous; background jobs remain a roadmap item |
