# Phase 2 Final-Review Fixes

> **For agentic workers:** this is a single fix wave, not a task sequence. Dispatch ONE implementer with the whole findings list, then ONE scoped re-review over the fix range. Do not dispatch a fixer per finding.

**Status:** approved by the repository owner, deferred to a later session. Nothing here has been implemented.

**Goal:** close the two Critical and five Important findings from phase 2's final whole-branch review, so the store stops silently accumulating withdrawn vendor numbers and stops leaking unbounded LLM cost.

**Decision on record:** the owner chose "fix both Criticals + all five Importants" and asked that the work happen in a separate session.

---

## State to resume from

| | |
|---|---|
| Branch | `procurement-comparison-portal` |
| Phase 2 range | `3c53d5a..94bf0ef` (13 commits) |
| HEAD when paused | `94bf0ef` |
| Test baseline | **254 passed, 3 skipped, 1 pre-existing portal failure** |
| SDD workspace | `.superpowers/sdd/2026-07-30-classification-routed-extraction-phase2/` — **kept deliberately**; its `progress.md` is the ledger for the whole phase |
| Phase 2 plan | `docs/superpowers/plans/2026-07-30-classification-routed-extraction-phase2.md` |
| Design spec | `docs/superpowers/specs/2026-07-29-project-scoped-extraction-store-design.md` |

**The pre-existing failure is not ours.** `tests/test_portal_app.py::test_missing_api_key_does_not_block_creation` fails because `portal/app.py` calls `load_dotenv()`, which repopulates `ANTHROPIC_API_KEY` after that test's `monkeypatch.delenv`, and `.env` now holds a real key. Verified by hiding `.env`: all 3 portal tests then pass. Do not fix it as part of this wave.

## Global constraints (unchanged from phase 2)

- Python `>=3.12`, Pydantic v2; no new dependencies.
- Snapshots under `projects/<slug>/store/` are the only authoritative store; writes go through `snapshots.*`, never hand-rolled.
- `generation` bumps once per write transaction, never once per file.
- `field_path` addresses list members by **id**, never by index.
- Missing data is never silently coerced to a passing or zero value.
- Arithmetic stays in Python; the model reads, code decides.
- Tests run key-free via `MockLLMClient`.
- `portal/app.py` needs no edit.
- Run from the repo root: `python -m pytest`.

---

## C1 — Facts of superseded, reclassified and deleted documents are never pruned

**Where:** `procurement/pipeline.py:167` and `:179`

**What's wrong.** `technical` and `deviations` are filtered by `doc_id != doc.doc_id` **only for the document currently being extracted**. Nothing removes facts belonging to a document that stopped being extractable. Reviewer's probes:

```
RUN1 technical:      doc_id 2aaa9c827357 (01 DataSheet Gas Generator.txt)
[add "01 DataSheet Gas Generator Rev1.txt"]
RUN2 old doc status: skipped | notes: superseded by 6877d345209f
RUN2 technical:      ['2aaa9c827357', '6877d345209f']   <-- both, obsolete + current

before: 2 facts;  delete "02 DataSheet B.txt";  after: 2 facts   <-- orphan survives
```

**Why it matters.** This defeats the phase's own stated purpose. `revisions.py:1-7` says lineage is resolved so withdrawn numbers never reach the comparison — but lineage only stops re-*extraction*; it does not remove what a previous run already stored. Phase 3's compliance matrix reads `technical` and would check requirements against withdrawn values. Silent: no event, no way to notice.

**Phase 2 introduced this.** In phase 1 exactly one document per vendor contributed to `VendorFacts`, so the wholesale overwrite was self-pruning. Multi-document accumulation removed that property and nothing replaced it.

**Fix.** Inside the transaction, after the document loop, prune per vendor:

- build `live = {d.doc_id for d in documents if d.vendor == v and d.extraction_status == "ok"}`
- rewrite each vendor's facts dropping `technical` / `deviations` entries whose `doc_id` is not in `live`
- emit a `facts.pruned` event naming what went
- **do it for every vendor in the project, including vendors with no document extracted this run** — a vendor whose only datasheet was deleted gets no loop iteration at all

Overrides targeting a pruned fact will then correctly flag `conflict=True` via `reconcile`. That is the desired outcome, not a regression.

---

## C2 — The `unchanged` fast path discards freshly computed classification and lineage

**Where:** `procurement/pipeline.py:142` — `documents.append(prior)`

**What's wrong.** Passes 1 and 2 compute `doc_class`, `classified_by`, `classified_with`, `revision_label`, `supersedes` and `superseded_by` onto the *fresh* record. The cache hit appends `prior` and throws all of it away. Two distinct failures, both probed:

**(a) A `CLASSIFY_PROMPT_VERSION` bump never persists.**
```
run1:      doc_class=quotation by=llm classified_with=doc_class_v1  prompt_version=bid_extract_v1  status=ok
run2 (v2): classified_with = doc_class_v1  | llm calls: ['_DocClass']
run3 (v2): classified_with = doc_class_v1  | llm calls: ['_DocClass']    <-- and run 4, and run 5...
```
The gate at `pipeline.py:70` requires `prior.classified_with == CLASSIFY_PROMPT_VERSION`, but `classified_with` is never written back for any document that then hits the extraction cache. After any classifier version bump, **every rule-declined document costs an LLM classification call on every subsequent run, permanently** — an unbounded violation of behaviour 1 triggered by routine maintenance. `documents.json` also keeps reporting the stale `doc_class` / `classified_by`.

**(b) Lineage is silently wrong when a sibling revision arrives later** — no version bump needed:
```
run1: only "Quotation ADP-935(Rev1).txt"  -> extracted ok
run2: "Quotation ADP-935.txt" arrives
      old.superseded_by = 6a07bc6c75c5, status skipped   (correct)
      newest.supersedes = None  (expected a98bbe449f6d)  (WRONG - lineage pointer lost)
```
Vendors uploading in two batches is normal, and this is exactly the ADPOWER shape.

**Fix.** Don't append `prior` verbatim. Carry the extraction results forward onto the fresh record: copy `extraction_status`, `notes`, `extracted_at`, `extractor`, `prompt_version`, `text_source` from `prior` onto `doc`, then `documents.append(doc)`. Cache semantics stay identical; classification and lineage stay current.

---

## I1 — A transient classification failure is cached as `other` forever

**Where:** `procurement/pipeline.py:68-74`

```
run1 (provider down): doc_class=other  classified_by=llm-failed  classified_with=doc_class_v1
run2 (provider healthy, would answer "quotation"):
     doc_class=other  classified_by=llm-failed  |  llm calls: 0
```

The gate checks `doc_class != "unclassified"` and the prompt version, but not `classified_by`. One network blip permanently demotes a document to `other`; it is never extracted and never retried. The `"llm-failed"` marker exists precisely to make this distinguishable — the gate just doesn't consult it.

**Fix.** Add `and prior.classified_by != "llm-failed"` to the condition.

---

## I2 — A vendor whose quotation the classifier misses drops out of the comparison silently

**Where:** `procurement/pipeline.py:101-108`, `:123-125`

`quote_rel_by_vendor` is built only from `doc_class == "quotation"` candidates. If a vendor has none, there is no entry, every document is skipped, `facts.commercial` stays `None`, and `load_dataset` omits the vendor from `bids`.

**Why it matters, on the real corpus.** This is a regression against phase 1, where `pick_quote(vendor_files(...))` used `max()` and therefore **always** returned a file. ADPOWER's only plausible quotation is `ADP-13158-2024-935(Rev1).pdf`, which is one of the three files the rules decline — so ADPOWER's presence in the comparison now hinges entirely on the LLM answering `"quotation"` for an opaque filename. No event, no warning, no fallback.

**Fix.** When a vendor yields no `quotation` candidate, fall back to `pick_quote` over that vendor's non-superseded documents (restoring phase-1 behaviour) and emit a `vendor.quotation_inferred` event. At minimum emit `vendor.no_quotation` so the condition is visible in the audit trail.

---

## I3 — The quotation branch overwrites `commercial` with a failed dump

**Where:** `procurement/pipeline.py:159`

Triaged explicitly by the final reviewer as **fix now, not defer**. The "pre-existing phase-1 behaviour" defence is accurate about provenance but not about blast radius:

1. In phase 1, `VendorFacts` held nothing but the quotation, so a failed overwrite blanked a record with nothing else in it. Phase 2 puts `technical` and `deviations` in the same file, so the same failure now writes a record that is half-good (facts preserved) and half-blank (`commercial` wiped) — a shape that did not previously exist and that `load_dataset` publishes straight into the comparison.
2. The correct pattern already sits two branches away at `pipeline.py:169-172` and `:181-184`, with an explanatory comment. Leaving the third branch inconsistent inside the same function is how the next maintainer concludes the asymmetry is deliberate.

**Fix.** `commercial_dump = bid.model_dump() if status == "ok" else base.commercial`. Keep `doc.extraction_status = "failed"` and `doc.notes = bid.notes` unchanged. On a first-ever failure `base.commercial` is `None`, so nothing regresses.

---

## I4 — A failed datasheet/deviation extraction leaves no diagnostic and retries forever

**Where:** `procurement/extract_tech.py:42`, `procurement/extract_deviation.py:38`, `procurement/pipeline.py:188`

The extractors swallow the exception entirely (`except Exception: return [], "failed"`) — the message is discarded. `doc.notes` is set only in the quotation branch, so a failed datasheet stores `extraction_status="failed"`, `notes=None`. No log line. The event detail carries only `{"status": "failed"}`.

Meanwhile `unchanged` requires `prior.extraction_status == "ok"`, so a permanently-failing document is re-attempted **on every run, forever** — unbounded recurring LLM cost with no diagnostic. Against a corpus with six image-only scans this is not hypothetical.

**Fix.** Return the message alongside the status (a third element, or log at `warning` — the two-value contract makes `f"failed: {exc}"` awkward), and set `doc.notes` in all three branches. Make the same change in `classify_document`'s `except`, which has the same blind spot — this subsumes the deferred "no log line" minor.

---

## I5 — `raw["facts"]` turns "the model returned no array" into `failed`

**Where:** `procurement/extract_tech.py:41`, `procurement/extract_deviation.py:37`

```python
raw_items = list(raw["facts"])      # KeyError if the model omits the key
```

Probed: a client returning `{}` yields `status == "failed"`, not `ok` with zero facts. Real clients return `dict(block.input)` (`shared/llm/anthropic_client.py:38`), and a tool call may legitimately omit an optional array — the likely response for an image-only scan that transcribed to noise. The plan's original `model_validate` defaulted to `[]`; the per-entry-validation refactor lost that. Combined with I4, every such document becomes a permanent per-run LLM charge.

**Fix.** `raw_items = list(raw.get("facts") or [])`, and the same for `deviations`.

---

## Tests this wave must add

The reviewer's central point: every defect that survived to the final review is a **lifecycle defect**, visible only across two runs or two modules. Single-run tests will not catch these. Add a two-run mutation matrix:

| mutation between run 1 and run 2 | assert |
|---|---|
| add a newer revision of an extracted datasheet | the obsolete `doc_id`'s facts are gone from `technical`; only the current one remains (**C1**) |
| delete a datasheet from the vendor folder | its facts are gone; the other datasheet's survive (**C1**) |
| bump `CLASSIFY_PROMPT_VERSION` | run 2 re-classifies and persists the new `classified_with`; run 3 makes **zero** classification calls (**C2a**) |
| a sibling revision arrives in the second upload | the newest document's `supersedes` points at its predecessor (**C2b**) |
| classification fails on run 1, succeeds on run 2 | run 2 retries and the document is reclassified (**I1**) |
| a vendor whose only document classifies as `other` | the vendor still appears in the comparison, with an event recording the inference (**I2**) |
| a quotation extraction fails on re-run | `commercial` retains the previously-stored value (**I3**) |
| a datasheet extraction fails | `doc.notes` carries the reason (**I4**) |
| a client returns `{}` with no `facts` key | status is `"ok"` with zero facts, not `"failed"` (**I5**) |

Also add, from the deferred list:
- a `spec` / `mom` skip test — behaviour 6 is currently verified by code inspection only (three lines)
- a monkeypatched per-class cache-independence test — behaviour 3 is correct (the reviewer verified it) but `test_prompt_versions_are_per_class` only asserts a dict literal and would not fail if routing broke

---

## Deferred minors — confirmed genuinely deferrable, do NOT fix in this wave

`classify.py` mid-file imports · the stale `test_model_failure_degrades_to_other_without_raising` name · `revisions._rank` ranking `Rev1 > RevA` · `_COPY_MARKER` leaving a stray "of" · the unescaped `":"` in the `deviation_id` key · `clause_ref=None` vs `""` hashing alike · the unused `MockLLMClient` import · `run_ingestion`'s length (split it *when* C1's prune lands, not before).

Two flagged as already resolved: `deviation_id_for`'s docstring was added in a fix round, and the non-string `clause_ref` / null `disposition` tests already exist.

---

## For phases 3 and 4 — change the plan template

The reviewer's diagnosis is worth acting on. Every plan defect caught *during* phase 2 execution was inside a single function's contract; every defect that survived to the final review needed two runs or two modules to see. Per-task TDD structurally produces single-run, single-module tests, and every task passed its own review honestly. The gap is architectural, not a reviewer failing.

1. **Give the integration task an explicit two-run test rubric** — a named mutation matrix like the one above, not "test the pipeline". Every Critical and Important above would have been caught by one row. Phase 3 is *more* exposed, because requirements and amendments create a second accumulating collection with C1's exact orphaning shape.
2. **Require each task brief to name the store invariant it owns**, not just its return value. "`extract_tech_facts` returns `(facts, status)`" is a function contract. "`VendorFacts.technical` contains exactly the facts of the vendor's currently-extractable documents" is a store invariant — the one C1 violates. No phase-2 task owned it, so nobody tested it.
3. **State in the plan that reference code is intent, not paste-able.** Five defects in five samples is a strong signal.

---

## Separately, not part of this wave

`tests/test_portal_app.py::test_missing_api_key_does_not_block_creation` is environment-dependent and currently tests nothing: `portal/app.py` calls `load_dotenv()`, so `monkeypatch.delenv("ANTHROPIC_API_KEY")` is undone before the assertion runs. It passes or fails depending on whether the developer has a populated `.env`. `monkeypatch.setattr` on the app's `load_dotenv` to a no-op would fix it. Owner's file, owner's call.
