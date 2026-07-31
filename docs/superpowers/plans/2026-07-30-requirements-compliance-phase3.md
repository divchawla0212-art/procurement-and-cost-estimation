# Requirements & Compliance (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This plan follows [`docs/superpowers/PLAN-TEMPLATE.md`](../PLAN-TEMPLATE.md).** Read it before starting. Its three rules — a named store invariant per task, a two-run mutation matrix on the integration task, and reference code treated as intent — are why phase 2's defect class does not repeat here.

**Goal:** Decompose the client MR (and the MOM that amends it) into requirement records, and produce a verdict for every requirement × vendor pair, with `unanswered` distinct from `fail`.

**Architecture:** The pipeline grows a second inventory pass over `requirements/`, mirroring the vendor pass: RFQ documents are hashed, classified, lineage-resolved, and routed — `spec` → `requirements_v1`, `mom` → `mom_amend_v1`. Base requirements and amendments accumulate in `store/requirements.json`, with amendments applied at write time onto a preserved `base_body` so the resolution is idempotent and reversible. The `auto` requirements' parameter names then seed the `tech_facts_v1` prompt before any datasheet is read, and a fingerprint of that vocabulary joins the datasheet cache key. Finally `compliance.py` recomputes `store/compliance.json` wholesale from (requirements, facts, deviations), with `units.py` doing every conversion in Python.

**Tech Stack:** Python 3.12, Pydantic v2, `re`, `hashlib`, `math`, `pytest`. No new dependencies.

## Global Constraints

- Python `>=3.12`; dependencies unchanged.
- Snapshots under `projects/<slug>/store/` remain the only authoritative store; `index/store.db` stays derived and disposable.
- Every snapshot write is atomic — use the existing `snapshots.*` helpers, never hand-roll.
- `generation` bumps **once per write transaction**, never once per file.
- `field_path` addresses list members by **id**, never by index.
- Missing data is never silently coerced to a passing or zero value. A parameter the model could not find is omitted, not emitted as `0` or `""`.
- **`unanswered` is never `fail`.** Missing evidence means the pipeline did not read enough, not that the vendor failed.
- **Arithmetic stays in Python.** The model reads; code decides. No extractor is ever asked whether a vendor complies.
- A failed extraction never blanks previously-good stored data, and always records why in `DocumentRecord.notes`.
- Tests run key-free via `MockLLMClient` or a local stub client. No test may require `ANTHROPIC_API_KEY`.
- Run all tests from the repo root: `python -m pytest`.
- Baseline before starting: **272 passed, 3 skipped, 1 failed** (`test_portal_app.py::test_missing_api_key_does_not_block_creation`, environment-dependent, documented in `CLAUDE.md`). Anything else is a real regression.

## Scope boundaries — do not exceed

- **No portal UI work.** The six review screens are phase 4. `portal/app.py` needs no edit; `load_dataset`'s returned dict keeps its current three keys (`bids`, `normalized`, `comparison`) so nothing downstream breaks. Phase 4 reads `snapshots.load_requirements` / `load_compliance` directly.
- **No BOM line-item extraction**, no weighted scoring, no award recommendation (spec §12).
- **No new document classes.** `classify.py`'s vocabulary and `_RULES` are unchanged; phase 3 only *routes* differently for RFQ-side documents.
- **`.docx` requirement documents remain unreadable.** `loaders.read_text` handles `.xlsx`, `.pdf`, and plain text; a `.docx` upload will extract nothing and record a failure note. Fixing `read_text` is a separate change — do not fold it in.

## Judgment calls made while writing this plan

These are decisions the spec does not settle. Each is implemented as described; flag any disagreement before starting, not mid-execution.

1. **An RFQ-side `datasheet` is routed to `requirements_v1`, not to `tech_facts_v1`.** `processed-data/01-client-mr-rfq/DOD-…DataSheet Gas Generator.xlsx` is the client's blank datasheet — the most parameter-dense statement of what is required — and the rules classify it `datasheet`. Routing it by class would either skip it or try to write `VendorFacts` for `vendor=None`. Both silently drop the RFQ's richest source, which is the **I2** shape the template requires a matrix row for. It is routed as `spec` with an `rfq.requirements_inferred` event, mirroring the existing `vendor.quotation_inferred` precedent in `pipeline.py`. `doc_class` still records what the classifier decided.
2. **The `auto` parameter vocabulary joins the datasheet cache key.** Spec §7 makes the vocabulary the join key between the two halves of the system, and spec §13 lists vocabulary drift as the top open risk. Without this, editing a requirement changes what the datasheet pass is asked to look for but never re-asks — every affected cell stays `unanswered` forever with nothing to show why. Cost: a requirement edit re-extracts that project's datasheets once. The first phase-3 run over a phase-2 store re-extracts every datasheet once (stored `vocabulary_sha` is `None`); that is a bounded one-time migration cost.
3. **`compliance.json` is recomputed wholesale every run.** It is pure Python over ~100 requirements × 4 vendors — a few thousand comparisons, no LLM calls. Wholesale recompute makes the matrix-coverage and verdict-lineage invariants hold by construction instead of by pruning logic, which is where phase 2's **C1** came from.
4. **`overrides._id_of` is corrected as part of Task 1.** It resolves ids from `("fact_id", "req_id", "doc_id", "clause_ref")`, which does not include `deviation_id`. A `DeviationRecord` dump therefore resolves on `doc_id`, so `deviations[<deviation_id>]` never matches and `deviations[<doc_id>]` silently matches whichever entry from that document comes first. That is a latent phase-2 defect and it blocks phase 4's Facts screen; adding `amendment_id` at the same time is what makes amendments addressable at all. Small, tested, justified — not opportunistic refactoring.

## Store invariant ledger

Every invariant is owned by exactly one task and defended by at least one row of Task 8's mutation matrix.

| id | invariant (over stored state) | owner | matrix rows |
|---|---|---|---|
| **INV-1** | Every id in `requirements.json` is a pure function of its source: re-extracting an unchanged spec yields exactly the same set of `req_id`s and `amendment_id`s, and each record type resolves by its own id key. | Task 1 | 12 |
| **INV-2** | Every requirement stored with `checkability == "auto"` carries exactly all four of `parameter`, `operator`, `value`, `unit`. A clause missing any of them is stored as `judgement`. | Task 2 | 18 |
| **INV-3** | Every amendment in `requirements.json` names exactly one `source_doc_id` and the `clause_ref` it targets, and is applied only when **exactly one** stored requirement matches that clause. Matching none — or more than one, since clause numbers are unique only within a document — stores it with `req_id = None` and a stated `unresolved_reason`: never dropped, never applied. | Task 3 | 19, 20 |
| **INV-4** | `requirements.json` contains exactly the requirements of the currently-authoritative (non-superseded, still-present, still-spec-routed) RFQ documents — no more, no fewer. | Task 4 | 1, 3, 4, 5, 6, 7, 8, 9, 10 |
| **INV-5** | An amendment sourced from a superseded or deleted MOM never contributes to a requirement's effective value: every requirement's `amended_by` names a live amendment or is `None`, and a requirement with `amended_by is None` equals its `base_body`. | Task 4 | 2, 11 |
| **INV-6** | `compliance.json` has exactly one cell per (live requirement, live vendor) pair and no cell for any other pair. | Task 6 | 15 |
| **INV-7** | Every verdict names the `req_id` it was computed from; a verdict citing a `fact_id` exists only while that fact is in that vendor's `technical`. | Task 6 | 14, 16 |
| **INV-8** | Every datasheet `DocumentRecord` records the `vocabulary_sha` its stored facts were extracted under, and that value equals the current fingerprint for exactly the datasheets whose facts are in `technical`. | Task 7 | 4, 13 |
| **INV-9** | `compliance.json` is never computed from a pre-change snapshot: after any run, every verdict's `evaluated_at` is at or after that run's `run.started`. | Task 8 | 17 |

`units.py` (Task 5) owns **no** store invariant — it never writes to the store, and Rule 1 is explicit that anything checkable without loading a snapshot is not a store invariant. Its correctness is defended through INV-7 (row 16).

---

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the
> implementation against the test.
>
> Load-bearing in every sample: the failure convention (`(records, status, notes)`,
> never raising), id stability rules, `"exactly"` pruning semantics, the
> `Unconvertible` → `unanswered` path, and the ordering of verdict checks.
> Illustrative and expected to change: exact regexes, molar-mass table contents,
> rationale wording, and prompt phrasing.

---

### Task 1: Requirement and amendment records, and their snapshot IO

**Files:**
- Modify: `procurement/store/models.py`
- Modify: `procurement/store/layout.py`
- Modify: `procurement/store/snapshots.py`
- Modify: `procurement/store/overrides.py:43-47`
- Test: `tests/test_store_requirement_models.py`

**Interfaces:**
- Consumes: `Override`, `layout.atomic_write_json`, `layout.read_json`.
- Produces:
  - `RequirementRecord`, `Amendment`, `RequirementSet`, `ComplianceResult` (all in `store/models.py`)
  - `req_id_for(source_doc_id: str, clause_ref: str) -> str`
  - `amendment_id_for(source_doc_id: str, clause_ref: str | None, text: str) -> str`
  - `layout.requirements_path(root, slug) -> str`, `layout.compliance_path(root, slug) -> str`
  - `snapshots.load_requirements(root, slug) -> RequirementSet` (an empty set when absent, never `None`)
  - `snapshots.save_requirements(root, slug, reqset) -> None`
  - `snapshots.load_compliance(root, slug) -> list[ComplianceResult]`
  - `snapshots.save_compliance(root, slug, results) -> None`
- **Store invariant owned (INV-1):** every id in `requirements.json` is a pure function of its source — re-extracting an unchanged spec document produces exactly the same set of `req_id`s and `amendment_id`s, so an override addressed at `requirements[<req_id>].value` still resolves after a re-run — and each record type in every snapshot resolves by its own id key, so `deviations[<deviation_id>]` and `amendments[<amendment_id>]` address exactly one record.

`ComplianceResult` lives in `store/models.py`, not in `compliance.py`: `snapshots.py` must serialise it, and `compliance.py` imports `snapshots`, so defining it in `compliance.py` would make the import cycle.

`RequirementRecord` carries **both** the effective body and `base_body`, the as-extracted body preserved whenever an amendment changed it. Storing only the effective value would make amendment application non-idempotent — the second run would amend the already-amended value — and would leave no way to revert when the MOM is withdrawn. That reversion is exactly INV-5.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store_requirement_models.py
import json
import os

from procurement.project import create_project
from procurement.store import layout, snapshots
from procurement.store.models import (Amendment, ComplianceResult, Override,
                                      RequirementRecord, RequirementSet,
                                      amendment_id_for, req_id_for)
from procurement.store.overrides import get_by_path, reconcile


def _req(doc_id="d1", clause="4.2.7", **kw):
    body = dict(clause_ref=clause, text="H2S tolerance shall be at least 50 ppm",
                category="technical", checkability="auto", parameter="h2s_tolerance",
                operator=">=", value=50.0, unit="ppm", source_doc_id=doc_id)
    body.update(kw)
    return RequirementRecord(req_id=req_id_for(doc_id, clause), **body)


def test_req_id_is_stable_for_the_same_document_and_clause():
    assert req_id_for("d1", "4.2.7") == req_id_for("d1", "4.2.7")
    # whitespace and case in a printed clause ref must not shift the id
    assert req_id_for("d1", " 4.2.7 ") == req_id_for("d1", "4.2.7")


def test_req_id_separates_clauses_and_documents():
    assert req_id_for("d1", "4.2.7") != req_id_for("d1", "4.2.8")
    assert req_id_for("d1", "4.2.7") != req_id_for("d2", "4.2.7")


def test_amendment_id_uses_document_clause_and_text():
    a = amendment_id_for("m1", "4.2.7", "raised to 60 ppm")
    assert a == amendment_id_for("m1", "4.2.7", "raised to 60 ppm")
    assert a != amendment_id_for("m1", "4.2.7", "lowered to 40 ppm")
    assert a != amendment_id_for("m1", "4.2.8", "raised to 60 ppm")
    assert a != amendment_id_for("m2", "4.2.7", "raised to 60 ppm")
    assert a.startswith("a-")


def test_requirements_round_trip_through_the_snapshot(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    reqset = RequirementSet(
        requirements=[_req()],
        amendments=[Amendment(
            amendment_id=amendment_id_for("m1", "4.2.7", "raised to 60 ppm"),
            clause_ref="4.2.7", req_id=None, text="raised to 60 ppm",
            parameter="h2s_tolerance", operator=">=", value=60.0, unit="ppm",
            action="modify", source_doc_id="m1")],
        overrides=[])
    snapshots.save_requirements(root, "p", reqset)
    loaded = snapshots.load_requirements(root, "p")
    assert loaded.requirements[0].req_id == reqset.requirements[0].req_id
    assert loaded.requirements[0].value == 50.0
    assert loaded.amendments[0].action == "modify"
    assert loaded.requirements[0].base_body is None


def test_missing_requirements_snapshot_loads_as_an_empty_set(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    loaded = snapshots.load_requirements(root, "p")
    assert loaded.requirements == [] and loaded.amendments == []


def test_requirements_snapshot_is_written_atomically(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    snapshots.save_requirements(root, "p", RequirementSet(requirements=[_req()]))
    path = layout.requirements_path(root, "p")
    assert os.path.exists(path) and not os.path.exists(path + ".tmp")
    with open(path, encoding="utf-8") as fh:
        assert "requirements" in json.load(fh)


def test_compliance_round_trips(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    snapshots.save_compliance(root, "p", [ComplianceResult(
        req_id="r-abc", vendor="KERUI", verdict="pass", fact_id="f-123",
        doc_id="d9", rationale="50 ppm >= 50 ppm", evaluated_at="2026-07-30T00:00:00Z")])
    loaded = snapshots.load_compliance(root, "p")
    assert loaded[0].verdict == "pass" and loaded[0].fact_id == "f-123"
    assert snapshots.load_compliance(str(tmp_path / "nowhere"), "p") == []


def test_an_override_addresses_a_requirement_by_req_id():
    req = _req()
    view = {"requirements": [req.model_dump()]}
    assert get_by_path(view, f"requirements[{req.req_id}].value") == 50.0


def test_a_deviation_resolves_by_deviation_id_not_by_doc_id():
    # phase-2 latent defect: _id_of did not list deviation_id, so a dump fell
    # through to doc_id and two deviations from one document were
    # indistinguishable.
    view = {"deviations": [
        {"deviation_id": "v-aaa", "clause_ref": "4.1", "statement": "one",
         "disposition": "deviate", "doc_id": "d9"},
        {"deviation_id": "v-bbb", "clause_ref": "4.2", "statement": "two",
         "disposition": "comply", "doc_id": "d9"},
    ]}
    assert get_by_path(view, "deviations[v-bbb].statement") == "two"


def test_an_amendment_resolves_by_amendment_id_even_though_it_has_a_req_id():
    view = {"amendments": [
        {"amendment_id": "a-aaa", "req_id": "r-1", "text": "one", "source_doc_id": "m1"},
        {"amendment_id": "a-bbb", "req_id": "r-1", "text": "two", "source_doc_id": "m1"},
    ]}
    assert get_by_path(view, "amendments[a-bbb].text") == "two"


def test_an_override_on_a_vanished_requirement_flags_a_conflict():
    req = _req()
    o = Override(field_path=f"requirements[{req.req_id}].value", value=55.0,
                 extracted_value=50.0, author="rj", at="2026-07-30T00:00:00Z",
                 reason="clarified at the meeting")
    [updated] = reconcile([o], {"requirements": []})
    assert updated.conflict is True and updated.value == 55.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_store_requirement_models.py -v`
Expected: FAIL — `RequirementRecord` cannot be imported from `procurement.store.models`.

- [ ] **Step 3: Write minimal implementation**

Load-bearing: the id functions are pure and normalise their inputs; `load_requirements` returns an empty set rather than `None`; `_id_of` checks the most specific key first. Illustrative: the `"r-"`/`"a-"` prefixes and the 10-hex truncation, chosen to match `fact_id_for`.

```python
# procurement/store/models.py  (additions)

def req_id_for(source_doc_id: str, clause_ref: str) -> str:
    """Stable across re-extraction: same document + same printed clause ref ->
    same id. That is what lets an override on requirements[<id>].value survive
    a re-run, since list order is not stable and index paths are forbidden."""
    key = f"{source_doc_id}:{(clause_ref or '').strip().lower()}"
    return "r-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


def amendment_id_for(source_doc_id: str, clause_ref: str | None, text: str) -> str:
    """All three participate. Clause ref alone collides whenever one meeting
    changes the same clause twice; text alone collides across documents."""
    key = f"{source_doc_id}:{(clause_ref or '').strip().lower()}:{text.strip().lower()}"
    return "a-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


_REQUIREMENT_BODY = ("text", "category", "checkability",
                     "parameter", "operator", "value", "unit")


class RequirementRecord(BaseModel):
    """One clause of the RFQ, with amendments already applied.

    `base_body` holds the as-extracted body whenever an amendment changed it,
    so re-applying amendments is idempotent across runs and withdrawing the
    MOM restores the original clause exactly.
    """
    req_id: str
    clause_ref: str
    text: str
    category: str = "technical"      # technical|commercial|documentation|testing|codes
    checkability: str = "judgement"  # auto|judgement
    parameter: str | None = None     # auto only
    operator: str | None = None      # >= | <= | == | in
    value: str | float | list | None = None
    unit: str | None = None
    source_doc_id: str
    amended_by: str | None = None    # amendment_id, or None
    base_body: dict | None = None    # pre-amendment body, for audit and revert
    withdrawn: bool = False          # a MOM removed the clause; kept for audit


class Amendment(BaseModel):
    """A change a MOM makes to a clause. Stored separately from the
    requirement so both the original and the meeting's change stay visible."""
    amendment_id: str
    clause_ref: str | None = None
    req_id: str | None = None        # resolved target; None means unmatched
    text: str
    parameter: str | None = None
    operator: str | None = None
    value: str | float | list | None = None
    unit: str | None = None
    action: str = "modify"           # modify | withdraw
    source_doc_id: str


class RequirementSet(BaseModel):
    requirements: list[RequirementRecord] = []
    amendments: list[Amendment] = []
    overrides: list[Override] = []


class ComplianceResult(BaseModel):
    """One cell of the requirement x vendor matrix. Lives here rather than in
    compliance.py so snapshots.py can serialise it without an import cycle."""
    req_id: str
    vendor: str
    verdict: str                     # pass|fail|deviation|unanswered|review
    fact_id: str | None = None
    doc_id: str | None = None
    rationale: str = ""
    evaluated_at: str
```

```python
# procurement/store/layout.py  (additions)

def requirements_path(root: str, slug: str) -> str:
    return os.path.join(store_dir(root, slug), "requirements.json")


def compliance_path(root: str, slug: str) -> str:
    return os.path.join(store_dir(root, slug), "compliance.json")
```

```python
# procurement/store/snapshots.py  (additions)

def load_requirements(root: str, slug: str) -> RequirementSet:
    """Absent snapshot -> an empty set, not None. 'No spec has been extracted'
    and 'the spec stated nothing' are the same thing to every reader here, and
    an Optional return would put a None-check in front of each of them."""
    raw = layout.read_json(layout.requirements_path(root, slug))
    return RequirementSet.model_validate(raw) if raw is not None else RequirementSet()


def save_requirements(root: str, slug: str, reqset: RequirementSet) -> None:
    layout.atomic_write_json(layout.requirements_path(root, slug), reqset.model_dump())


def load_compliance(root: str, slug: str) -> list[ComplianceResult]:
    raw = layout.read_json(layout.compliance_path(root, slug), default=[])
    return [ComplianceResult.model_validate(r) for r in raw]


def save_compliance(root: str, slug: str, results: list[ComplianceResult]) -> None:
    layout.atomic_write_json(layout.compliance_path(root, slug),
                             [r.model_dump() for r in results])
```

```python
# procurement/store/overrides.py:43-47  (replace _id_of)

def _id_of(item: dict) -> str | None:
    # Most specific key first. A DeviationRecord dump carries both
    # deviation_id and doc_id, and an Amendment carries both amendment_id and
    # req_id; falling through to the broader key made every record from one
    # source document resolve to whichever came first in the list.
    for key in ("fact_id", "deviation_id", "amendment_id", "req_id",
                "doc_id", "clause_ref"):
        if key in item:
            return item[key]
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_store_requirement_models.py tests/test_store_overrides.py tests/test_store_snapshots.py -v`
Expected: PASS — including the existing override and snapshot suites, which the `_id_of` change must not disturb.

- [ ] **Step 5: Commit**

```bash
git add procurement/store/models.py procurement/store/layout.py procurement/store/snapshots.py procurement/store/overrides.py tests/test_store_requirement_models.py && git commit -m "feat: requirement, amendment and compliance records with snapshot IO"
```

---

### Task 2: Requirements extractor

**Files:**
- Create: `procurement/extract_requirements.py`
- Create: `shared/llm/prompts/requirements_v1.txt`
- Test: `tests/test_extract_requirements.py`

**Interfaces:**
- Consumes: `loaders.read_text`, `RequirementRecord`, `req_id_for` (Task 1).
- Produces:
  - `REQUIREMENTS_PROMPT_VERSION: str = "requirements_v1"`
  - `extract_requirements(doc_id: str, path: str, client, pdf_fallback=None) -> tuple[list[RequirementRecord], str, str | None]` — `(records, status, notes)`, status `"ok"` or `"failed"`, never raising, `notes` carrying the reason on failure and `None` on success.
- **Store invariant owned (INV-2):** every requirement stored in `requirements.json` with `checkability == "auto"` carries exactly all four of `parameter`, `operator`, `value`, `unit`; a clause missing any of them is stored as `judgement`, so no `auto` requirement with a null bound ever reaches the comparison.

The demotion rule is the whole point of this task. An `auto` requirement missing its `value` would be compared against `None` in `compliance.py`; whatever that produced — a crash, a `fail`, a `pass` — would be wrong, and the failure would be attributed to the vendor. Demoting to `judgement` says the honest thing: a human must read this clause.

The three failure conventions are copied deliberately from `extract_tech.py`, because all three were phase-2 defects (**I4**, **I5**, and the per-entry validation lost in a refactor):

1. never raise — one unreadable MR must not abort a run;
2. `raw.get("requirements") or []` — an omitted optional array is an empty extraction, not a failed one;
3. validate per entry — one malformed clause must not discard the other ninety.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extract_requirements.py
import pytest

from procurement.extract_requirements import (REQUIREMENTS_PROMPT_VERSION,
                                              extract_requirements)
from procurement.store.models import RequirementSet, req_id_for
from procurement.store import snapshots
from procurement.project import create_project


class StubClient:
    supports_vision = True

    def __init__(self, response, raises=False):
        self._response, self._raises = response, raises
        self.calls = []

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        self.calls.append({"prompt": prompt, "context_text": context_text})
        if self._raises:
            raise RuntimeError("provider unavailable")
        return self._response


def _spec(tmp_path, text="4.2.7 H2S tolerance shall be at least 50 ppm"):
    p = tmp_path / "MR.txt"
    p.write_text(text, encoding="utf-8")
    return str(p)


_TWO_CLAUSES = {"requirements": [
    {"clause_ref": "4.2.7", "text": "H2S tolerance shall be at least 50 ppm",
     "category": "technical", "checkability": "auto", "parameter": "h2s_tolerance",
     "operator": ">=", "value": 50, "unit": "ppm"},
    {"clause_ref": "9.1", "text": "Vendor shall submit an O&M manual in English",
     "category": "documentation", "checkability": "judgement"},
]}


def test_extracts_both_tiers_with_stable_ids(tmp_path):
    records, status, notes = extract_requirements("d1", _spec(tmp_path),
                                                  StubClient(_TWO_CLAUSES))
    assert (status, notes) == ("ok", None)
    assert [r.req_id for r in records] == [req_id_for("d1", "4.2.7"),
                                           req_id_for("d1", "9.1")]
    auto, judgement = records
    assert (auto.checkability, auto.parameter, auto.operator, auto.value,
            auto.unit) == ("auto", "h2s_tolerance", ">=", 50, "ppm")
    assert judgement.checkability == "judgement"
    assert all(r.source_doc_id == "d1" for r in records)


@pytest.mark.parametrize("missing", ["parameter", "operator", "value", "unit"])
def test_an_auto_clause_missing_any_bound_is_demoted_to_judgement(tmp_path, missing):
    entry = dict(_TWO_CLAUSES["requirements"][0])
    entry[missing] = None
    [record], status, _ = extract_requirements(
        "d1", _spec(tmp_path), StubClient({"requirements": [entry]}))
    assert status == "ok"
    assert record.checkability == "judgement"
    # and the clause is still stored - demotion is not deletion
    assert record.clause_ref == "4.2.7"


def test_a_dimensionless_auto_clause_may_declare_unit_none_explicitly(tmp_path):
    # "frequency shall be 50 Hz" has a unit; "number of starts shall be >= 3"
    # does not. The model states unit "" for genuinely dimensionless bounds,
    # which is a stated unit, not a missing one.
    entry = {"clause_ref": "6.4", "text": "At least 3 black starts",
             "category": "technical", "checkability": "auto",
             "parameter": "black_starts", "operator": ">=", "value": 3, "unit": ""}
    [record], status, _ = extract_requirements(
        "d1", _spec(tmp_path), StubClient({"requirements": [entry]}))
    assert (status, record.checkability, record.unit) == ("ok", "auto", "")


def test_an_unknown_operator_demotes_rather_than_storing_it(tmp_path):
    entry = dict(_TWO_CLAUSES["requirements"][0], operator="approximately")
    [record], _, _ = extract_requirements(
        "d1", _spec(tmp_path), StubClient({"requirements": [entry]}))
    assert record.checkability == "judgement" and record.operator is None


def test_a_clause_with_no_text_is_dropped(tmp_path):
    entry = {"clause_ref": "4.2.7", "text": "   ", "checkability": "judgement"}
    records, status, _ = extract_requirements(
        "d1", _spec(tmp_path), StubClient({"requirements": [entry]}))
    assert (records, status) == ([], "ok")


def test_a_clause_with_no_clause_ref_still_stores_with_a_synthesised_ref(tmp_path):
    entry = {"clause_ref": None, "text": "Painting to manufacturer standard",
             "checkability": "judgement"}
    [record], status, _ = extract_requirements(
        "d1", _spec(tmp_path), StubClient({"requirements": [entry]}))
    assert status == "ok" and record.clause_ref
    # the id must still be reproducible from the same input
    assert record.req_id == req_id_for("d1", record.clause_ref)


def test_one_malformed_entry_does_not_discard_the_others(tmp_path):
    payload = {"requirements": [
        {"clause_ref": "4.2.7", "text": ["not", "a", "string"]},
        _TWO_CLAUSES["requirements"][1],
    ]}
    records, status, _ = extract_requirements("d1", _spec(tmp_path),
                                              StubClient(payload))
    assert status == "ok" and len(records) == 1
    assert records[0].clause_ref == "9.1"


def test_an_omitted_requirements_key_is_an_empty_extraction_not_a_failure(tmp_path):
    records, status, notes = extract_requirements("d1", _spec(tmp_path),
                                                  StubClient({}))
    assert (records, status, notes) == ([], "ok", None)


def test_a_provider_failure_returns_failed_with_a_reason_and_does_not_raise(tmp_path):
    records, status, notes = extract_requirements(
        "d1", _spec(tmp_path), StubClient({}, raises=True))
    assert (records, status) == ([], "failed")
    assert "provider unavailable" in notes


def test_duplicate_clause_refs_in_one_document_do_not_collide_silently(tmp_path):
    payload = {"requirements": [
        {"clause_ref": "4.2.7", "text": "first statement", "checkability": "judgement"},
        {"clause_ref": "4.2.7", "text": "second statement", "checkability": "judgement"},
    ]}
    records, status, _ = extract_requirements("d1", _spec(tmp_path),
                                              StubClient(payload))
    assert status == "ok"
    assert len({r.req_id for r in records}) == len(records)


def test_the_prompt_version_is_the_prompt_filename():
    assert REQUIREMENTS_PROMPT_VERSION == "requirements_v1"


def test_stored_auto_requirements_all_carry_four_bounds(tmp_path):
    # INV-2, asserted over a loaded snapshot rather than over return values
    root = str(tmp_path / "projects")
    create_project(root, "P")
    entry_missing = dict(_TWO_CLAUSES["requirements"][0], unit=None)
    payload = {"requirements": [_TWO_CLAUSES["requirements"][0], entry_missing,
                                _TWO_CLAUSES["requirements"][1]]}
    records, _, _ = extract_requirements("d1", _spec(tmp_path), StubClient(payload))
    snapshots.save_requirements(root, "p", RequirementSet(requirements=records))
    for r in snapshots.load_requirements(root, "p").requirements:
        if r.checkability == "auto":
            assert r.parameter and r.operator and r.value is not None and r.unit is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_extract_requirements.py -v`
Expected: FAIL — `procurement.extract_requirements` does not exist.

- [ ] **Step 3: Write minimal implementation**

Load-bearing: the demotion rule and its exact condition (`value is not None`, not truthiness — a bound of `0` is a real bound); the `_OPERATORS` allow-list; the three failure conventions. Illustrative: the synthesised clause ref format and the category allow-list.

```python
# procurement/extract_requirements.py
"""MR / spec document -> RequirementRecords.

Two tiers, per spec section 4: `auto` requirements carry a machine-checkable
bound (parameter, operator, value, unit) and `judgement` requirements carry
clause text for a human. A clause that looks machine-checkable but is missing
any part of its bound is stored as `judgement` - never as an `auto` with a
null bound, which compliance.py would compare against and blame a vendor for.
"""
import logging
from pathlib import Path
from pydantic import BaseModel

from procurement.loaders import read_text
from procurement.store.models import RequirementRecord, req_id_for

_log = logging.getLogger(__name__)

REQUIREMENTS_PROMPT_VERSION = "requirements_v1"
_PROMPT = (Path(__file__).parents[1] / "shared" / "llm" / "prompts"
           / "requirements_v1.txt")

_OPERATORS = (">=", "<=", "==", "in")
_CATEGORIES = ("technical", "commercial", "documentation", "testing", "codes")


class _Requirement(BaseModel):
    clause_ref: str | None = None
    text: str = ""
    category: str = "technical"
    checkability: str = "judgement"
    parameter: str | None = None
    operator: str | None = None
    value: str | float | list | None = None
    unit: str | None = None


class _RequirementList(BaseModel):
    requirements: list[_Requirement] = []


def extract_requirements(doc_id: str, path: str, client, pdf_fallback=None
                         ) -> tuple[list[RequirementRecord], str, str | None]:
    """Return (records, status, notes). Never raises: one unreadable MR must
    not abort a run. `notes` carries the reason on failure, None on success -
    without it a permanently failing document is retried every run with no
    record of why."""
    try:
        text = read_text(path, llm_fallback=pdf_fallback)
        prompt = _PROMPT.read_text(encoding="utf-8")
        raw = client.classify_structure(prompt, _RequirementList, text)
        # An omitted optional array is an empty extraction, not a failed one.
        raw_items = list(raw.get("requirements") or [])
    except Exception as exc:
        _log.warning("requirement extraction failed for %s: %s", path, exc)
        return [], "failed", f"extraction error: {exc}"

    out: list[RequirementRecord] = []
    seen: set[str] = set()
    for position, raw_item in enumerate(raw_items, 1):
        try:
            item = _Requirement.model_validate(raw_item)
        except Exception:
            # One malformed clause must not discard the other ninety.
            continue
        body = item.text.strip()
        if not body:
            continue        # a clause with no text cannot be reviewed or checked

        clause = (item.clause_ref or "").strip() or f"#{position}"
        req_id = req_id_for(doc_id, clause)
        if req_id in seen:
            # Two clauses printed under one ref: keep both, distinguished by
            # position, rather than letting the second overwrite the first.
            clause = f"{clause}#{position}"
            req_id = req_id_for(doc_id, clause)
        seen.add(req_id)

        operator = (item.operator or "").strip()
        operator = operator if operator in _OPERATORS else None
        auto = (item.checkability == "auto"
                and bool((item.parameter or "").strip())
                and operator is not None
                and item.value is not None
                and item.unit is not None)

        out.append(RequirementRecord(
            req_id=req_id,
            clause_ref=clause,
            text=body,
            category=item.category if item.category in _CATEGORIES else "technical",
            checkability="auto" if auto else "judgement",
            parameter=(item.parameter or "").strip() or None if auto else None,
            operator=operator if auto else None,
            value=item.value if auto else None,
            unit=item.unit if auto else None,
            source_doc_id=doc_id,
        ))
    return out, "ok", None
```

```
# shared/llm/prompts/requirements_v1.txt
You are decomposing a client material requisition (MR) for gas generator sets
into individual requirement clauses, for a procurement compliance review.

Return a list of requirements. For each one give:
  clause_ref   - the clause number exactly as printed (e.g. "4.2.7"). Omit if
                 the document prints none.
  text         - the requirement stated verbatim, trimmed to one clause.
  category     - technical | commercial | documentation | testing | codes
  checkability - "auto" only when the clause states a single parameter, a
                 comparison, and a value that a computer could check against a
                 vendor datasheet. Everything else is "judgement".
  parameter    - auto only: a short snake_case name matching how a datasheet
                 would print it (h2s_tolerance, continuous_rating, frequency,
                 ambient_design_temp, nox_emission).
  operator     - auto only: one of >= <= == in
  value        - auto only: the number or the list of allowed values, exactly
                 as printed. Do not convert or compute.
  unit         - auto only: the unit exactly as printed (ppm, kW, degC, bar,
                 Hz, V). Use "" for a genuinely dimensionless bound.

Rules:
- Copy values exactly as stated. Never convert between units.
- Mark a clause "auto" only when all four of parameter, operator, value and
  unit are present. If any is missing or ambiguous, use "judgement" and leave
  those fields out - a half-stated bound is worse than no bound.
- Never invent a clause number, a value, or a unit.
- One clause per entry. Do not merge two requirements into one entry.
- Do not judge any vendor. This document is the requirement, not a bid.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_extract_requirements.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add procurement/extract_requirements.py shared/llm/prompts/requirements_v1.txt tests/test_extract_requirements.py && git commit -m "feat: extract tiered requirement records from the MR"
```

---

### Task 3: MOM amendment extractor

**Files:**
- Create: `procurement/extract_mom.py`
- Create: `shared/llm/prompts/mom_amend_v1.txt`
- Test: `tests/test_extract_mom.py`

**Interfaces:**
- Consumes: `loaders.read_text`, `Amendment`, `amendment_id_for` (Task 1).
- Produces:
  - `MOM_PROMPT_VERSION: str = "mom_amend_v1"`
  - `extract_amendments(doc_id: str, path: str, client, pdf_fallback=None) -> tuple[list[Amendment], str, str | None]`
  - `apply_amendments(requirements: list[RequirementRecord], amendments: list[Amendment]) -> tuple[list[RequirementRecord], list[Amendment]]` — returns the resolved requirements **and** the amendments with their `req_id` targets filled in. Pure; mutates neither argument.
- **Store invariant owned (INV-3):** every amendment in `requirements.json` names exactly one `source_doc_id` and the `clause_ref` it targets, and is applied only when **exactly one** stored requirement matches that clause. An amendment whose clause matches no stored requirement — or more than one — is kept with `req_id = None` and an `unresolved_reason` saying which case it is: never dropped, and never applied to anything.

`apply_amendments` is the idempotence guarantee. It always rebuilds each requirement from `base_body or <current body>`, so running it twice over the same inputs produces the same output, and removing an amendment reverts the clause exactly. Getting this wrong is how a second run would amend an already-amended value — the arithmetic equivalent of phase 2's **C1**.

An unmatched amendment is stored, not discarded: a MOM changing a clause the extractor missed is a signal that the requirements are incomplete, and phase 4's Requirements screen shows it. Discarding it would hide the gap.

An **ambiguous** amendment is not applied at all. Clause numbers are unique only within one document, and the RFQ side has several live spec documents, so the same printed ref appearing in two of them is a coincidence rather than a shared clause. Neither the `Amendment` nor the `RequirementRecord` names a target document, and a MOM saying "clause 4.2.7 revised" gives a human nothing to disambiguate with either — so there is nothing to scope the match by, and binding to whichever requirement happens to come first in the list is a coin flip dressed as a decision. `unresolved_reason` distinguishes the two unresolved cases in words, because "the requirements are incomplete" and "say which document you meant" need different responses from a reviewer.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extract_mom.py
import pytest

from procurement.extract_mom import (MOM_PROMPT_VERSION, apply_amendments,
                                     extract_amendments)
from procurement.store.models import (Amendment, RequirementRecord,
                                      RequirementSet, amendment_id_for,
                                      req_id_for)
from procurement.store import snapshots
from procurement.project import create_project


class StubClient:
    supports_vision = True

    def __init__(self, response, raises=False):
        self._response, self._raises = response, raises
        self.calls = []

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        self.calls.append({"prompt": prompt, "context_text": context_text})
        if self._raises:
            raise RuntimeError("provider unavailable")
        return self._response


def _mom(tmp_path):
    p = tmp_path / "MOM.txt"
    p.write_text("Clause 4.2.7 revised: H2S raised to 60 ppm", encoding="utf-8")
    return str(p)


def _requirement(clause="4.2.7", doc="d1", **kw):
    body = dict(clause_ref=clause, text="H2S tolerance at least 50 ppm",
                category="technical", checkability="auto",
                parameter="h2s_tolerance", operator=">=", value=50.0,
                unit="ppm", source_doc_id=doc)
    body.update(kw)
    return RequirementRecord(req_id=req_id_for(doc, clause), **body)


def _amendment(clause="4.2.7", doc="m1", text="H2S raised to 60 ppm", **kw):
    body = dict(clause_ref=clause, text=text, action="modify", source_doc_id=doc,
                parameter="h2s_tolerance", operator=">=", value=60.0, unit="ppm")
    body.update(kw)
    return Amendment(amendment_id=amendment_id_for(doc, clause, text), **body)


_ONE_AMENDMENT = {"amendments": [
    {"clause_ref": "4.2.7", "text": "H2S raised to 60 ppm", "action": "modify",
     "parameter": "h2s_tolerance", "operator": ">=", "value": 60, "unit": "ppm"},
]}


def test_extracts_an_amendment_with_provenance(tmp_path):
    [a], status, notes = extract_amendments("m1", _mom(tmp_path),
                                            StubClient(_ONE_AMENDMENT))
    assert (status, notes) == ("ok", None)
    assert a.source_doc_id == "m1" and a.clause_ref == "4.2.7"
    assert a.req_id is None            # unresolved until apply_amendments runs
    assert (a.action, a.value, a.unit) == ("modify", 60, "ppm")


def test_an_unknown_action_degrades_to_modify_never_to_withdraw(tmp_path):
    payload = {"amendments": [dict(_ONE_AMENDMENT["amendments"][0], action="scrap")]}
    [a], status, _ = extract_amendments("m1", _mom(tmp_path), StubClient(payload))
    assert (status, a.action) == ("ok", "modify")


def test_an_amendment_with_no_text_is_dropped(tmp_path):
    payload = {"amendments": [{"clause_ref": "4.2.7", "text": "  "}]}
    records, status, _ = extract_amendments("m1", _mom(tmp_path), StubClient(payload))
    assert (records, status) == ([], "ok")


def test_one_malformed_amendment_does_not_discard_the_others(tmp_path):
    payload = {"amendments": [{"clause_ref": {"bad": 1}, "text": "x"},
                              _ONE_AMENDMENT["amendments"][0]]}
    records, status, _ = extract_amendments("m1", _mom(tmp_path), StubClient(payload))
    assert status == "ok" and len(records) == 1


def test_an_omitted_amendments_key_is_empty_not_failed(tmp_path):
    assert extract_amendments("m1", _mom(tmp_path), StubClient({})) == ([], "ok", None)


def test_a_provider_failure_returns_failed_with_a_reason(tmp_path):
    records, status, notes = extract_amendments("m1", _mom(tmp_path),
                                                StubClient({}, raises=True))
    assert (records, status) == ([], "failed")
    assert "provider unavailable" in notes


def test_the_prompt_version_is_the_prompt_filename():
    assert MOM_PROMPT_VERSION == "mom_amend_v1"


# --- apply_amendments -------------------------------------------------------

def test_applying_an_amendment_preserves_the_base_body_and_names_both_sides():
    req = _requirement()
    amend = _amendment()
    [resolved], [linked] = apply_amendments([req], [amend])
    assert resolved.value == 60.0 and resolved.unit == "ppm"
    assert resolved.amended_by == amend.amendment_id
    assert resolved.base_body["value"] == 50.0
    assert linked.req_id == req.req_id


def test_applying_twice_is_idempotent():
    once, amendments = apply_amendments([_requirement()], [_amendment()])
    twice, _ = apply_amendments(once, amendments)
    assert twice[0].value == 60.0
    assert twice[0].base_body["value"] == 50.0     # not 60.0 - no re-baselining


def test_removing_the_amendment_reverts_the_requirement_exactly():
    amended, _ = apply_amendments([_requirement()], [_amendment()])
    [reverted], _ = apply_amendments(amended, [])
    assert reverted.value == 50.0 and reverted.unit == "ppm"
    assert reverted.amended_by is None and reverted.base_body is None
    assert reverted.model_dump() == _requirement().model_dump()


def test_a_withdrawing_amendment_marks_the_clause_withdrawn_without_deleting_it():
    [resolved], _ = apply_amendments([_requirement()],
                                     [_amendment(action="withdraw", value=None)])
    assert resolved.withdrawn is True
    assert resolved.text                      # the clause is still readable
    assert resolved.base_body is not None     # and still revertible


def test_an_amendment_matching_no_requirement_is_kept_unapplied():
    req = _requirement(clause="4.2.7")
    [resolved], [linked] = apply_amendments([req], [_amendment(clause="99.9")])
    assert resolved.value == 50.0 and resolved.amended_by is None
    assert linked.req_id is None              # kept, visible, never applied


def test_clause_refs_match_across_printing_differences():
    req = _requirement(clause="4.2.7")
    [resolved], _ = apply_amendments([req], [_amendment(clause=" Clause 4.2.7 ")])
    assert resolved.value == 60.0


def test_an_amendment_that_only_restates_text_leaves_the_bound_alone():
    req = _requirement()
    amend = _amendment(text="wording clarified", parameter=None, operator=None,
                       value=None, unit=None)
    [resolved], _ = apply_amendments([req], [amend])
    assert resolved.value == 50.0 and resolved.unit == "ppm"
    assert resolved.amended_by == amend.amendment_id
    assert "wording clarified" in resolved.text


def test_the_later_of_two_amendments_to_one_clause_wins_deterministically():
    first = _amendment(text="raised to 60 ppm", value=60.0)
    second = _amendment(text="raised again to 70 ppm", value=70.0)
    [a], _ = apply_amendments([_requirement()], [first, second])
    [b], _ = apply_amendments([_requirement()], [first, second])
    assert a.value == b.value == 70.0
    assert a.base_body["value"] == 50.0


def test_stored_amendments_all_carry_provenance(tmp_path):
    # INV-3, asserted over a loaded snapshot
    root = str(tmp_path / "projects")
    create_project(root, "P")
    resolved, linked = apply_amendments([_requirement()],
                                        [_amendment(), _amendment(clause="99.9")])
    snapshots.save_requirements(root, "p", RequirementSet(requirements=resolved,
                                                          amendments=linked))
    loaded = snapshots.load_requirements(root, "p")
    assert all(a.source_doc_id and a.clause_ref for a in loaded.amendments)
    assert len(loaded.amendments) == 2                      # nothing dropped
    live = {r.req_id for r in loaded.requirements}
    assert all(a.req_id is None or a.req_id in live for a in loaded.amendments)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_extract_mom.py -v`
Expected: FAIL — `procurement.extract_mom` does not exist.

- [ ] **Step 3: Write minimal implementation**

Load-bearing: rebuilding from `base_body` on every call; clearing `base_body`/`amended_by`/`withdrawn` when no amendment matches; `action` degrading to `"modify"` and never to `"withdraw"`; unmatched amendments kept with `req_id = None`. Illustrative: `_norm_clause`'s exact regex, and appending amendment text to the clause text.

```python
# procurement/extract_mom.py
"""Minutes of Meeting -> requirement amendments.

The real MR is explicitly superseded by a MOM, so ignoring meetings would make
the requirement baseline simply wrong. Amendments are stored as their own
records and applied on top of base requirements at write time, with lineage
preserved both ways: the requirement names the amendment in `amended_by`, the
amendment names the requirement in `req_id`, and the pre-amendment body stays
in `base_body`.
"""
import logging
import re
from pathlib import Path
from pydantic import BaseModel

from procurement.loaders import read_text
from procurement.store.models import (Amendment, RequirementRecord,
                                      amendment_id_for)

_log = logging.getLogger(__name__)

MOM_PROMPT_VERSION = "mom_amend_v1"
_PROMPT = (Path(__file__).parents[1] / "shared" / "llm" / "prompts"
           / "mom_amend_v1.txt")

_ACTIONS = ("modify", "withdraw")
_OPERATORS = (">=", "<=", "==", "in")
_BODY = ("text", "category", "checkability", "parameter", "operator", "value", "unit")
_CLAUSE_NOISE = re.compile(r"[^a-z0-9]+")


class _Amendment(BaseModel):
    clause_ref: str | None = None
    text: str = ""
    action: str = "modify"
    parameter: str | None = None
    operator: str | None = None
    value: str | float | list | None = None
    unit: str | None = None


class _AmendmentList(BaseModel):
    amendments: list[_Amendment] = []


def _norm_clause(ref: str | None) -> str:
    """Match "4.2.7", "Clause 4.2.7" and " 4.2.7 " as one clause."""
    return _CLAUSE_NOISE.sub("", (ref or "").lower().replace("clause", ""))


def extract_amendments(doc_id: str, path: str, client, pdf_fallback=None
                       ) -> tuple[list[Amendment], str, str | None]:
    """Return (amendments, status, notes). Never raises; same failure
    convention as every other extractor in this package."""
    try:
        text = read_text(path, llm_fallback=pdf_fallback)
        prompt = _PROMPT.read_text(encoding="utf-8")
        raw = client.classify_structure(prompt, _AmendmentList, text)
        raw_items = list(raw.get("amendments") or [])
    except Exception as exc:
        _log.warning("amendment extraction failed for %s: %s", path, exc)
        return [], "failed", f"extraction error: {exc}"

    out: list[Amendment] = []
    for raw_item in raw_items:
        try:
            item = _Amendment.model_validate(raw_item)
        except Exception:
            continue
        body = item.text.strip()
        if not body:
            continue
        operator = (item.operator or "").strip()
        out.append(Amendment(
            amendment_id=amendment_id_for(doc_id, item.clause_ref, body),
            clause_ref=(item.clause_ref or "").strip() or None,
            req_id=None,                 # resolved by apply_amendments
            text=body,
            parameter=(item.parameter or "").strip() or None,
            operator=operator if operator in _OPERATORS else None,
            value=item.value,
            unit=item.unit,
            # Degrade to "modify", never to "withdraw": silently deleting a
            # requirement because a word was unrecognised is the one failure
            # here that could corrupt an award decision.
            action=item.action if item.action in _ACTIONS else "modify",
            source_doc_id=doc_id,
        ))
    return out, "ok", None


def apply_amendments(requirements: list[RequirementRecord],
                     amendments: list[Amendment]
                     ) -> tuple[list[RequirementRecord], list[Amendment]]:
    """Resolve effective requirement values from base + live amendments.

    Idempotent by construction: every requirement is rebuilt from
    `base_body or <its current body>`, so applying twice changes nothing and
    removing an amendment restores the original clause exactly. That reversion
    is INV-5 - an amendment from a withdrawn MOM must stop contributing.
    """
    out = [r.model_copy(deep=True) for r in requirements]
    linked = [a.model_copy(deep=True) for a in amendments]

    by_clause: dict[str, list[RequirementRecord]] = {}
    for req in out:
        base = req.base_body or {k: getattr(req, k) for k in _BODY}
        for k, v in base.items():
            setattr(req, k, v)
        req.base_body = None
        req.amended_by = None
        req.withdrawn = False
        # every match, not the first: keeping one would silently pick a
        # document when two of them print the same clause number
        by_clause.setdefault(_norm_clause(req.clause_ref), []).append(req)

    for amend in linked:
        # cleared first, or a reason outlives the collision that caused it
        amend.unresolved_reason = None
        matches = by_clause.get(_norm_clause(amend.clause_ref), [])
        if len(matches) != 1:
            # kept and visible, never applied. Two cases, and a reviewer
            # responds to them differently, so they read differently.
            amend.req_id = None
            amend.unresolved_reason = (
                f"no requirement states clause {amend.clause_ref!r}"
                if not matches else
                f"clause {amend.clause_ref!r} matches {len(matches)} "
                f"requirements (documents: ...); the amendment names no "
                "target document")
            continue
        target = matches[0]
        amend.req_id = target.req_id
        if target.base_body is None:
            target.base_body = {k: getattr(target, k) for k in _BODY}
        target.amended_by = amend.amendment_id
        if amend.action == "withdraw":
            target.withdrawn = True
            continue
        target.text = f"{target.text}\n[amended] {amend.text}"
        for field in ("parameter", "operator", "value", "unit"):
            fresh = getattr(amend, field)
            if fresh is not None:
                setattr(target, field, fresh)
        # A partially-stated amendment must not leave an `auto` requirement
        # with a half-replaced bound - the INV-2 rule, re-applied here.
        if target.checkability == "auto" and not (
                target.parameter and target.operator
                and target.value is not None and target.unit is not None):
            target.checkability = "judgement"
    return out, linked
```

```
# shared/llm/prompts/mom_amend_v1.txt
You are reading the minutes of a technical clarification meeting between a
client and a vendor for gas generator sets. The meeting changes clauses of a
material requisition that was issued earlier.

Return only the changes the meeting made to requirement clauses. For each one:
  clause_ref - the requisition clause number the change applies to, exactly as
               referred to in the minutes (e.g. "4.2.7").
  text       - what the meeting decided, stated verbatim.
  action     - "modify" when the clause is changed or clarified,
               "withdraw" when the clause is deleted or no longer applies.
  parameter  - when the change states a new machine-checkable bound, the same
               short snake_case name a datasheet would use.
  operator   - one of >= <= == in
  value      - the new value exactly as printed. Do not convert or compute.
  unit       - the unit exactly as printed.

Rules:
- Report only changes to requirements. Ignore attendance, actions, and general
  discussion that does not change a clause.
- Never invent a clause number. If the minutes do not say which clause changed,
  leave clause_ref out.
- Use "withdraw" only when the minutes plainly remove the requirement.
- Copy values exactly. Never convert units.
- Do not judge whether any vendor complies.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_extract_mom.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add procurement/extract_mom.py shared/llm/prompts/mom_amend_v1.txt tests/test_extract_mom.py && git commit -m "feat: extract MOM amendments and resolve them onto requirements"
```

---

### Task 4: The RFQ pass — inventory, classify, lineage, route, prune

**Files:**
- Modify: `procurement/pipeline.py`
- Test: `tests/test_pipeline_rfq.py`

**Interfaces:**
- Consumes: Tasks 1–3; existing `classify_by_rules`, `classify_document`, `resolve_supersession`, `snapshots.*`, `events.append_event`, `overrides.reconcile` / `apply_overrides`.
- Produces:
  - `inventory_rfq_documents(root, slug) -> list[DocumentRecord]` — every file under `projects/<slug>/requirements/`, `vendor=None`, `doc_id` from `layout.doc_id_for(None, rel)`.
  - `RFQ_PROMPT_VERSION_BY_CLASS: dict[str, str]` — `{"spec": "requirements_v1", "mom": "mom_amend_v1"}`.
  - `_classify_pass(root, slug, docs, prior_docs, client, run_id) -> None` — the classification loop, extracted from `run_ingestion` so both passes share one cache gate.
  - `_run_rfq_pass(root, slug, client, prior_docs, run_id, pdf_fallback, force) -> tuple[list[DocumentRecord], int, int]` — `(documents, extracted, failed)`.
  - `run_ingestion` unchanged in signature; `documents.json` now holds RFQ documents alongside vendor documents.
- **Store invariant owned (INV-4):** `requirements.json` contains exactly the requirements of the currently-authoritative RFQ documents — a requirement whose source document was superseded, deleted, or reclassified out of `spec` routing is gone from the snapshot, not merely skipped on re-extraction.
- **Store invariant owned (INV-5):** an amendment sourced from a superseded or deleted MOM never contributes to a requirement's effective value — every requirement's `amended_by` names an amendment still in `requirements.json`, and a requirement with `amended_by is None` is byte-identical to its `base_body`.

This is the task the whole template exists for. Phase 2's **C1** was precisely this shape: lineage stopped re-*extraction* but nothing removed what an earlier run had stored. `requirements` and `amendments` are two more accumulating collections with the same shape, and `amendments` is worse — a stale one does not merely sit there, it actively rewrites a live requirement's value.

**Behaviour to implement:**

1. **Inventory `requirements/`** the same way vendor folders are inventoried: skip dotfiles and `~$` lock files, hash every file, `vendor=None`.
2. **Classify RFQ documents through the same gate** as vendor documents — including the `classified_by != "llm-failed"` retry condition and `classified_with == CLASSIFY_PROMPT_VERSION`. Extract that loop into `_classify_pass` rather than copying it; a second copy is a second place for the **I1** gate to rot.
3. **Resolve supersession across RFQ documents**, via the existing `resolve_supersession`. It groups by `(vendor, normalised_base)`, and `vendor=None` groups the RFQ set on its own. This is load-bearing on the real corpus: `ADN-AEC-ME-SPC-026 MR Gas Genset Superseded with MOM 20241111.pdf` and `ADN-AEC-ME-SPC-026 MR Gas Genset copy.pdf` share a normalised base, and the explicit marker sorts oldest.
4. **Route.** `spec` → `extract_requirements`; `mom` → `extract_amendments`; an RFQ-side `datasheet` is routed as `spec` with an `rfq.requirements_inferred` event (judgment call 1); everything else is `skipped` with a note naming the class. `doc_class` keeps reporting what the classifier decided.
5. **Never write `VendorFacts` from this pass.** `doc.vendor` is `None` here; `snapshots.load_facts(root, slug, None)` would raise or write to a junk path.
6. **Cache per class** via `RFQ_PROMPT_VERSION_BY_CLASS`, exactly as the vendor pass does — a `requirements_v1` bump must not invalidate MOM extractions.
7. **A failed extraction keeps the prior records** for that document and sets `doc.notes` (the **I3**/**I4** guards).
8. **Prune, then apply, then reconcile, then save — in that order.** Pruning after application would leave `amended_by` pointing at an amendment that has just been removed.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_rfq.py
import os

from procurement.classify import CLASSIFY_PROMPT_VERSION
from procurement.pipeline import (RFQ_PROMPT_VERSION_BY_CLASS, run_ingestion,
                                  inventory_rfq_documents)
from procurement.project import create_project
from procurement.store import events, snapshots


class RfqClient:
    """Answers according to the schema it is handed, and records every call."""
    supports_vision = True

    def __init__(self, fail_on=(), h2s=50):
        self.calls, self._fail_on, self._h2s = [], set(fail_on), h2s

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        fields = output_schema.model_fields
        kind = ("requirements" if "requirements" in fields else
                "amendments" if "amendments" in fields else
                "doc_class" if "doc_class" in fields else
                "facts" if "facts" in fields else
                "deviations" if "deviations" in fields else "bid")
        self.calls.append(kind)
        if kind in self._fail_on:
            raise RuntimeError(f"{kind} provider unavailable")
        if kind == "requirements":
            return {"requirements": [
                {"clause_ref": "4.2.7", "text": "H2S at least 50 ppm",
                 "category": "technical", "checkability": "auto",
                 "parameter": "h2s_tolerance", "operator": ">=",
                 "value": self._h2s, "unit": "ppm"},
                {"clause_ref": "9.1", "text": "Submit an O&M manual",
                 "category": "documentation", "checkability": "judgement"},
            ]}
        if kind == "amendments":
            return {"amendments": [
                {"clause_ref": "4.2.7", "text": "H2S raised to 60 ppm",
                 "action": "modify", "parameter": "h2s_tolerance",
                 "operator": ">=", "value": 60, "unit": "ppm"}]}
        if kind == "doc_class":
            return {"doc_class": "other"}
        if kind == "facts":
            return {"facts": [{"parameter": "h2s_tolerance", "value": 70,
                               "unit": "ppm", "verbatim": "H2S up to 70 ppm"}]}
        if kind == "deviations":
            return {"deviations": []}
        return {"currency": "USD", "base_price": 1000.0}


def _rfq(tmp_path, files):
    root = str(tmp_path)
    create_project(root, "P")
    rdir = tmp_path / "p" / "requirements"
    rdir.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (rdir / name).write_text(body, encoding="utf-8")
    return root


_MR = "ADN-AEC-ME-SPC-026 MR Gas Genset.txt"
_MOM = "00 MOM 20241111 ASTRA.txt"


def test_rfq_documents_are_inventoried_with_no_vendor(tmp_path):
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm"})
    [doc] = inventory_rfq_documents(root, "p")
    assert doc.vendor is None
    assert doc.path == f"requirements/{_MR}"
    assert doc.content_sha256


def test_a_spec_document_produces_stored_requirements(tmp_path):
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm"})
    run_ingestion(root, "p", RfqClient())
    reqset = snapshots.load_requirements(root, "p")
    assert [r.clause_ref for r in reqset.requirements] == ["4.2.7", "9.1"]
    assert reqset.requirements[0].checkability == "auto"
    doc = next(d for d in snapshots.load_documents(root, "p") if d.vendor is None)
    assert (doc.doc_class, doc.extraction_status) == ("spec", "ok")
    assert doc.prompt_version == "requirements_v1"


def test_a_mom_amends_the_requirement_and_keeps_the_base(tmp_path):
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm",
                           _MOM: "Clause 4.2.7 revised to 60 ppm"})
    run_ingestion(root, "p", RfqClient())
    reqset = snapshots.load_requirements(root, "p")
    amended = next(r for r in reqset.requirements if r.clause_ref == "4.2.7")
    assert amended.value == 60
    assert amended.base_body["value"] == 50
    assert amended.amended_by == reqset.amendments[0].amendment_id
    assert reqset.amendments[0].req_id == amended.req_id


def test_an_rfq_datasheet_is_routed_to_requirements_with_an_event(tmp_path):
    # judgment call 1: the client's blank datasheet is the RFQ's most
    # parameter-dense document; routing it by class would drop it silently.
    root = _rfq(tmp_path, {"DOD-30201 DataSheet Gas Generator.txt": "H2S 50 ppm"})
    run_ingestion(root, "p", RfqClient())
    doc = next(d for d in snapshots.load_documents(root, "p") if d.vendor is None)
    assert doc.doc_class == "datasheet"          # the classifier's answer stands
    assert doc.extraction_status == "ok"
    assert doc.prompt_version == "requirements_v1"
    assert snapshots.load_requirements(root, "p").requirements
    actions = [e.action for e in events.read_events(root, "p")]
    assert "rfq.requirements_inferred" in actions


def test_a_drawing_in_the_rfq_folder_is_skipped_not_extracted(tmp_path):
    root = _rfq(tmp_path, {"15 LAYOUT - KGW550GF-T.txt": "a drawing"})
    run_ingestion(root, "p", RfqClient())
    doc = next(d for d in snapshots.load_documents(root, "p") if d.vendor is None)
    assert doc.extraction_status == "skipped" and "drawing" in doc.notes
    assert snapshots.load_requirements(root, "p").requirements == []


def test_a_superseded_spec_is_never_extracted(tmp_path):
    root = _rfq(tmp_path, {
        "ADN-AEC MR Gas Genset Superseded with MOM 20241111.txt": "old",
        "ADN-AEC MR Gas Genset copy.txt": "current"})
    run_ingestion(root, "p", RfqClient())
    docs = {os.path.basename(d.path): d for d in snapshots.load_documents(root, "p")}
    old = docs["ADN-AEC MR Gas Genset Superseded with MOM 20241111.txt"]
    new = docs["ADN-AEC MR Gas Genset copy.txt"]
    assert old.superseded_by == new.doc_id
    assert old.extraction_status == "skipped" and "superseded" in old.notes
    assert new.extraction_status == "ok"
    reqs = snapshots.load_requirements(root, "p").requirements
    assert {r.source_doc_id for r in reqs} == {new.doc_id}


def test_rerunning_an_unchanged_rfq_makes_zero_llm_calls(tmp_path):
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm",
                           _MOM: "Clause 4.2.7 revised to 60 ppm"})
    run_ingestion(root, "p", RfqClient())
    second = RfqClient()
    run_ingestion(root, "p", second)
    assert second.calls == []


def test_rerunning_preserves_the_amendment_resolution_exactly(tmp_path):
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm",
                           _MOM: "Clause 4.2.7 revised to 60 ppm"})
    run_ingestion(root, "p", RfqClient())
    before = snapshots.load_requirements(root, "p").model_dump()
    run_ingestion(root, "p", RfqClient())
    assert snapshots.load_requirements(root, "p").model_dump() == before


def test_deleting_the_mom_reverts_the_requirement_and_drops_the_amendment(tmp_path):
    # INV-5
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm",
                           _MOM: "Clause 4.2.7 revised to 60 ppm"})
    run_ingestion(root, "p", RfqClient())
    os.remove(os.path.join(root, "p", "requirements", _MOM))
    run_ingestion(root, "p", RfqClient())
    reqset = snapshots.load_requirements(root, "p")
    assert reqset.amendments == []
    reverted = next(r for r in reqset.requirements if r.clause_ref == "4.2.7")
    assert reverted.value == 50
    assert reverted.amended_by is None and reverted.base_body is None


def test_deleting_the_spec_prunes_its_requirements(tmp_path):
    # INV-4
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm"})
    run_ingestion(root, "p", RfqClient())
    assert snapshots.load_requirements(root, "p").requirements
    os.remove(os.path.join(root, "p", "requirements", _MR))
    run_ingestion(root, "p", RfqClient())
    assert snapshots.load_requirements(root, "p").requirements == []
    assert "requirements.pruned" in [e.action for e in events.read_events(root, "p")]


def test_a_failed_spec_extraction_keeps_the_previous_requirements(tmp_path):
    # INV-4 under the I3 shape: a failed overwrite must not blank good data
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm"})
    run_ingestion(root, "p", RfqClient())
    before = snapshots.load_requirements(root, "p").model_dump()
    (tmp_path / "p" / "requirements" / _MR).write_text("edited", encoding="utf-8")
    run_ingestion(root, "p", RfqClient(fail_on=("requirements",)))
    assert snapshots.load_requirements(root, "p").model_dump() == before
    doc = next(d for d in snapshots.load_documents(root, "p") if d.vendor is None)
    assert doc.extraction_status == "failed"
    assert "provider unavailable" in doc.notes


def test_rfq_prompt_versions_are_per_class():
    assert RFQ_PROMPT_VERSION_BY_CLASS == {"spec": "requirements_v1",
                                           "mom": "mom_amend_v1"}


def test_the_rfq_pass_does_not_disturb_vendor_facts(tmp_path):
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm"})
    vdir = tmp_path / "p" / "vendors" / "KERUI"
    vdir.mkdir(parents=True)
    (vdir / "Quotation.txt").write_text("base price 1000", encoding="utf-8")
    from procurement.project import load_project, save_project
    project = load_project(root, "p")
    project.vendors = ["KERUI"]
    save_project(root, project)

    run_ingestion(root, "p", RfqClient())
    facts = snapshots.load_facts(root, "p", "KERUI")
    assert facts.commercial["base_price"] == 1000.0
    assert snapshots.load_facts(root, "p", "_rfq") is None
    assert snapshots.list_fact_vendors(root, "p") == ["KERUI"]


def test_a_reclassified_spec_loses_its_requirements(tmp_path):
    # INV-4: reclassification is the third orphaning route, alongside
    # supersession and deletion
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm"})
    run_ingestion(root, "p", RfqClient())
    os.rename(os.path.join(root, "p", "requirements", _MR),
              os.path.join(root, "p", "requirements", "15 LAYOUT drawing.txt"))
    run_ingestion(root, "p", RfqClient())
    assert snapshots.load_requirements(root, "p").requirements == []


def test_classification_is_cached_across_runs_for_rfq_documents(tmp_path):
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm"})
    run_ingestion(root, "p", RfqClient())
    doc = next(d for d in snapshots.load_documents(root, "p") if d.vendor is None)
    assert doc.classified_by == "rule"
    assert doc.classified_with == CLASSIFY_PROMPT_VERSION
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline_rfq.py -v`
Expected: FAIL — `inventory_rfq_documents` cannot be imported from `procurement.pipeline`.

- [ ] **Step 3: Write minimal implementation**

Load-bearing: the prune-then-apply-then-reconcile-then-save order; `"failed"` counting as live so an outage does not delete requirements; the failed branch reusing `prior_*` rather than the fresh (empty) result; routing an RFQ `datasheet` as `spec` while leaving `doc_class` alone. Illustrative: the event names, the `_rfq_route` helper's placement, and how `_classify_pass` is factored out.

```python
# procurement/pipeline.py  (additions and restructuring)

from procurement.extract_requirements import (extract_requirements,
                                              REQUIREMENTS_PROMPT_VERSION)
from procurement.extract_mom import (apply_amendments, extract_amendments,
                                     MOM_PROMPT_VERSION)
from procurement.store.models import Amendment, RequirementRecord, RequirementSet

RFQ_PROMPT_VERSION_BY_CLASS = {"spec": REQUIREMENTS_PROMPT_VERSION,
                               "mom": MOM_PROMPT_VERSION}


def inventory_rfq_documents(root: str, slug: str) -> list[DocumentRecord]:
    """Hash and record every file under requirements/. vendor is None, which
    is what keeps these documents out of every VendorFacts code path."""
    pdir = layout.project_dir(root, slug)
    rdir = os.path.join(pdir, "requirements")
    out: list[DocumentRecord] = []
    for dirpath, _dirs, files in os.walk(rdir):
        for name in sorted(files):
            if name.startswith(".") or name.startswith("~$"):
                continue
            full = os.path.join(dirpath, name)
            rel = _rel_path(pdir, full)
            out.append(DocumentRecord(
                doc_id=layout.doc_id_for(None, rel),
                path=rel,
                vendor=None,
                content_sha256=layout.content_sha256(full),
            ))
    return out


def _classify_pass(root, slug, docs, prior_docs, client, run_id) -> None:
    """Assign doc_class/classified_by/classified_with to every document.

    Shared by both passes on purpose: the cache gate here carries the I1 fix
    (`classified_by != "llm-failed"`) and the C2a fix (persisting
    classified_with), and a second copy is a second place for those to rot.
    """
    pdir = layout.project_dir(root, slug)
    for doc in docs:
        prior = prior_docs.get(doc.doc_id)
        if (prior is not None and prior.content_sha256 == doc.content_sha256
                and prior.doc_class != "unclassified"
                and prior.classified_by != "llm-failed"
                and prior.classified_with == CLASSIFY_PROMPT_VERSION):
            doc.doc_class = prior.doc_class
            doc.classified_by = prior.classified_by
            doc.classified_with = prior.classified_with
            continue
        full = os.path.join(pdir, doc.path)
        head = ""
        if classify_by_rules(full) is None:
            try:
                head = read_text(full, llm_fallback=None)[:500]
            except Exception:
                head = ""
        doc.doc_class, doc.classified_by = classify_document(full, client, head)
        doc.classified_with = CLASSIFY_PROMPT_VERSION
        events.append_event(root, slug, Event(
            at=_now(), run_id=run_id, actor="pipeline",
            action="document.classified", target=doc.doc_id,
            detail={"doc_class": doc.doc_class, "by": doc.classified_by}))


def _rfq_route(doc: DocumentRecord) -> str:
    """An RFQ-side datasheet is the client's blank datasheet: it states what is
    required, so it feeds requirements_v1. doc_class is left alone - documents
    .json must keep reporting what the classifier decided, not what routing
    did with it (the same rule the quotation fallback follows)."""
    return "spec" if doc.doc_class == "datasheet" else doc.doc_class


def _run_rfq_pass(root, slug, client, prior_docs, run_id,
                  pdf_fallback=None, force=False
                  ) -> tuple[list[DocumentRecord], int, int]:
    """Extract requirements and amendments. Returns (documents, ok, failed)."""
    pdir = layout.project_dir(root, slug)
    docs = inventory_rfq_documents(root, slug)
    _classify_pass(root, slug, docs, prior_docs, client, run_id)
    docs = resolve_supersession(docs)

    prior = snapshots.load_requirements(root, slug)
    requirements = list(prior.requirements)
    amendments = list(prior.amendments)
    extracted = failed = 0

    for doc in docs:
        route = _rfq_route(doc)
        if route != doc.doc_class and route == "spec":
            events.append_event(root, slug, Event(
                at=_now(), run_id=run_id, actor="pipeline",
                action="rfq.requirements_inferred", target=doc.doc_id,
                detail={"doc_class": doc.doc_class, "path": doc.path}))
        expected_version = RFQ_PROMPT_VERSION_BY_CLASS.get(route)

        skip_reason = None
        if doc.superseded_by is not None:
            skip_reason = f"superseded by {doc.superseded_by}"
        elif expected_version is None:
            skip_reason = f"{doc.doc_class} documents are not extracted"
        if skip_reason is not None:
            doc.extraction_status = "skipped"
            doc.notes = skip_reason
            events.append_event(root, slug, Event(
                at=_now(), run_id=run_id, actor="pipeline",
                action="document.skipped", target=doc.doc_id,
                detail={"reason": skip_reason}))
            continue

        prior_doc = prior_docs.get(doc.doc_id)
        unchanged = (prior_doc is not None
                     and prior_doc.content_sha256 == doc.content_sha256
                     and prior_doc.prompt_version == expected_version
                     and prior_doc.extraction_status == "ok")
        if unchanged and not force:
            # Carry the cached extraction forward onto the fresh record, never
            # append `prior` - that was C2, which threw away this run's
            # classification and lineage.
            doc.extraction_status = prior_doc.extraction_status
            doc.notes = prior_doc.notes
            doc.extracted_at = prior_doc.extracted_at
            doc.extractor = prior_doc.extractor
            doc.prompt_version = prior_doc.prompt_version
            doc.text_source = prior_doc.text_source
            extracted += 1
            events.append_event(root, slug, Event(
                at=_now(), run_id=run_id, actor="pipeline",
                action="document.skipped", target=doc.doc_id,
                detail={"reason": "unchanged"}))
            continue

        full = os.path.join(pdir, doc.path)
        if route == "spec":
            fresh, status, notes = extract_requirements(
                doc.doc_id, full, client, pdf_fallback=pdf_fallback)
            if status == "ok":
                requirements = [r for r in requirements
                                if r.source_doc_id != doc.doc_id] + fresh
            # on failure the previously-stored records for this document stay
        else:
            fresh, status, notes = extract_amendments(
                doc.doc_id, full, client, pdf_fallback=pdf_fallback)
            if status == "ok":
                amendments = [a for a in amendments
                              if a.source_doc_id != doc.doc_id] + fresh

        doc.notes = notes
        doc.extraction_status = status
        doc.extracted_at = _now()
        doc.extractor = f"llm:{expected_version}"
        doc.prompt_version = expected_version
        extracted += 1 if status == "ok" else 0
        failed += 0 if status == "ok" else 1
        events.append_event(root, slug, Event(
            at=_now(), run_id=run_id, actor="pipeline",
            action="document.extracted", target=doc.doc_id,
            detail={"status": status, "doc_class": doc.doc_class}))

    # Prune BEFORE applying: an amendment removed here must not still be
    # named by a requirement's amended_by. "failed" counts as live for the
    # same reason it does in _prune_orphan_facts - a transient outage must not
    # delete what a good earlier run stored.
    live_spec = {d.doc_id for d in docs if _rfq_route(d) == "spec"
                 and d.extraction_status in ("ok", "failed")}
    live_mom = {d.doc_id for d in docs if _rfq_route(d) == "mom"
                and d.extraction_status in ("ok", "failed")}
    kept_reqs = [r for r in requirements if r.source_doc_id in live_spec]
    kept_amends = [a for a in amendments if a.source_doc_id in live_mom]
    if len(kept_reqs) != len(requirements) or len(kept_amends) != len(amendments):
        events.append_event(root, slug, Event(
            at=_now(), run_id=run_id, actor="pipeline",
            action="requirements.pruned", target=None,
            detail={"requirements_dropped": len(requirements) - len(kept_reqs),
                    "amendments_dropped": len(amendments) - len(kept_amends)}))

    resolved_reqs, linked_amends = apply_amendments(kept_reqs, kept_amends)
    view = {"requirements": [r.model_dump() for r in resolved_reqs],
            "amendments": [a.model_dump() for a in linked_amends]}
    overrides = reconcile(prior.overrides, view)
    applied = apply_overrides(view, overrides)
    snapshots.save_requirements(root, slug, RequirementSet(
        requirements=[RequirementRecord.model_validate(r)
                      for r in applied["requirements"]],
        amendments=[Amendment.model_validate(a) for a in applied["amendments"]],
        overrides=overrides))
    for o in overrides:
        if o.conflict:
            events.append_event(root, slug, Event(
                at=_now(), run_id=run_id, actor="pipeline",
                action="override.conflicted", target=None,
                detail={"field_path": o.field_path}))
    return docs, extracted, failed
```

And in `run_ingestion`, inside the existing `with snapshots.transaction(...)` block, before the vendor loop:

```python
    with snapshots.transaction(root, slug):
        rfq_docs, rfq_ok, rfq_failed = _run_rfq_pass(
            root, slug, client, prior_docs, run_id,
            pdf_fallback=pdf_fallback, force=force)
        extracted += rfq_ok
        failed += rfq_failed
        ...                      # the existing vendor loop, unchanged
        snapshots.save_documents(root, slug, rfq_docs + documents)
```

`documents.json` must be saved with **both** lists. Saving only the vendor documents would drop every RFQ record each run, and the next run would re-classify and re-extract the MR from scratch — an unbounded per-run cost of exactly the **C2a** kind.

Replace the classification loop in `run_ingestion` with a call to `_classify_pass(root, slug, fresh_docs, prior_docs, client, run_id)` so both passes share one gate.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pipeline_rfq.py tests/test_pipeline_routing.py tests/test_pipeline_incremental.py tests/test_pipeline_lifecycle.py -v`
Expected: PASS — the three existing pipeline suites must stay green; they are the phase-1 and phase-2 lifecycle guarantees this restructuring is most likely to break.

- [ ] **Step 5: Commit**

```bash
git add procurement/pipeline.py tests/test_pipeline_rfq.py && git commit -m "feat: inventory, classify and extract RFQ documents into requirements.json"
```

---

### Task 5: Unit normalisation

**Files:**
- Create: `procurement/units.py`
- Test: `tests/test_units.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Unconvertible(Exception)` — carries the human-readable reason.
  - `to_number(value) -> float | None`
  - `to_canonical(value: float, unit: str | None, parameter: str | None = None) -> tuple[float, str]` — raises `Unconvertible`.
  - `compare(operator: str, req_value, req_unit, fact_value, fact_unit, parameter: str | None = None) -> tuple[bool, str]` — returns `(satisfied, rationale)`, raises `Unconvertible` when the two quantities cannot honestly be brought into one unit.
- **Store invariant owned: none.** `units.py` never writes to the store, and Rule 1 is explicit that anything checkable without loading a snapshot is not a store invariant. Its correctness is defended through INV-7 and matrix row 16, which assert that an unconvertible pair reaches `compliance.json` as `unanswered` with a stated reason rather than as a verdict.

**The trap this module exists to avoid.** ppm ↔ mg·Nm⁻³ is not a fixed ratio: `mg/Nm³ = ppm × M / 22.414`, where `M` is the molar mass of the substance being measured. Converting H2S with NOx's molar mass produces a number that looks right and is wrong by 35%. So the conversion is attempted **only** when the parameter name identifies a substance in `_MOLAR_MASS`; otherwise it raises, and the caller records `unanswered` with the reason. Guessing here would fail a compliant vendor on arithmetic nobody could audit.

The same reasoning bans kW ↔ kVA: apparent power and real power differ by the power factor, which no datasheet states in a form this module could read. They are separate families and never convert.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_units.py
import pytest

from procurement.units import Unconvertible, compare, to_canonical, to_number


@pytest.mark.parametrize("raw,expected", [
    (50, 50.0), (50.5, 50.5), ("50", 50.0), ("50.5", 50.5), (" 550 ", 550.0),
    ("550 kW", 550.0), (">= 550", 550.0), ("1,200", 1200.0), ("-5", -5.0),
])
def test_to_number_reads_a_leading_quantity(raw, expected):
    assert to_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", None, "as per standard", [], {}])
def test_to_number_returns_none_rather_than_zero(raw):
    # coercing an unparseable value to 0.0 would silently fail every vendor
    assert to_number(raw) is None


@pytest.mark.parametrize("value,unit,expected", [
    (1.0, "MW", 1000.0), (1000.0, "W", 1.0), (550.0, "kW", 550.0),
    (1.0, "bar", 100.0), (1.0, "MPa", 1000.0), (100.0, "kPa", 100.0),
    (1.0, "psi", 6.894757), (1.0, "kg/cm2", 98.0665),
    (50.0, "Hz", 50.0), (11.0, "kV", 11000.0), (400.0, "V", 400.0),
])
def test_to_canonical_scales_within_a_family(value, unit, expected):
    got, _ = to_canonical(value, unit)
    assert got == pytest.approx(expected, rel=1e-6)


@pytest.mark.parametrize("value,unit,expected", [
    (25.0, "degC", 25.0), (77.0, "degF", 25.0), (298.15, "K", 25.0),
    (25.0, "°C", 25.0), (77.0, "°F", 25.0),
])
def test_temperature_conversion_is_affine_not_scalar(value, unit, expected):
    got, canonical = to_canonical(value, unit)
    assert got == pytest.approx(expected, rel=1e-6) and canonical == "degc"


def test_a_dimensionless_bound_is_its_own_family():
    assert to_canonical(3.0, "")[0] == 3.0
    assert to_canonical(3.0, None)[0] == 3.0


def test_ppm_to_mg_per_nm3_uses_the_substance_molar_mass():
    # H2S: 50 ppm * 34.081 / 22.414 = 76.02 mg/Nm3
    got, canonical = to_canonical(76.02, "mg/Nm3", parameter="h2s_tolerance")
    assert got == pytest.approx(50.0, rel=1e-3) and canonical == "ppm"


@pytest.mark.parametrize("unit", ["mg/Nm3", "mg/nm³", "mg/Nm^3", "mg·Nm⁻³"])
def test_mg_per_nm3_spellings_all_fold_to_one_unit(unit):
    got, _ = to_canonical(76.02, unit, parameter="h2s_tolerance")
    assert got == pytest.approx(50.0, rel=1e-3)


def test_the_substance_comes_from_the_parameter_name_not_a_guess():
    # NOx has a different molar mass; the same reading must not convert alike
    h2s, _ = to_canonical(76.02, "mg/Nm3", parameter="h2s_tolerance")
    nox, _ = to_canonical(76.02, "mg/Nm3", parameter="nox_emission")
    assert h2s != pytest.approx(nox, rel=1e-3)


def test_an_unknown_substance_raises_rather_than_guessing_a_molar_mass():
    with pytest.raises(Unconvertible) as exc:
        to_canonical(76.02, "mg/Nm3", parameter="mystery_gas")
    assert "molar mass" in str(exc.value).lower()


def test_an_unrecognised_unit_raises_with_the_unit_named():
    with pytest.raises(Unconvertible) as exc:
        to_canonical(1.0, "furlongs")
    assert "furlongs" in str(exc.value)


def test_kva_never_converts_to_kw():
    with pytest.raises(Unconvertible) as exc:
        compare(">=", 500.0, "kW", 625.0, "kVA", parameter="continuous_rating")
    assert "power factor" in str(exc.value).lower()


def test_compare_across_units_within_a_family():
    ok, why = compare(">=", 0.5, "MW", 550.0, "kW", parameter="continuous_rating")
    assert ok is True and "550" in why


@pytest.mark.parametrize("op,req,fact,expected", [
    (">=", 50.0, 70.0, True), (">=", 50.0, 40.0, False),
    ("<=", 50.0, 40.0, True), ("<=", 50.0, 70.0, False),
    ("==", 50.0, 50.0, True), ("==", 50.0, 50.5, False),
    (">=", 50.0, 50.0, True), ("<=", 50.0, 50.0, True),
])
def test_the_four_operators(op, req, fact, expected):
    ok, _ = compare(op, req, "ppm", fact, "ppm", parameter="h2s_tolerance")
    assert ok is expected


def test_equality_tolerates_float_representation():
    ok, _ = compare("==", 50, "Hz", 50.0, "Hz", parameter="frequency")
    assert ok is True


def test_in_accepts_a_list_or_a_comma_string():
    assert compare("in", ["50", "60"], "Hz", 60, "Hz", parameter="frequency")[0]
    assert compare("in", "50, 60", "Hz", 60, "Hz", parameter="frequency")[0]
    assert not compare("in", "50, 60", "Hz", 55, "Hz", parameter="frequency")[0]


def test_in_matches_non_numeric_members_case_insensitively():
    ok, _ = compare("in", ["API 616", "ISO 8528"], None, "iso 8528", None,
                    parameter="applicable_standard")
    assert ok is True


def test_a_non_numeric_vendor_value_on_a_numeric_operator_raises():
    with pytest.raises(Unconvertible) as exc:
        compare(">=", 50.0, "ppm", "as per standard", "ppm",
                parameter="h2s_tolerance")
    assert "not a number" in str(exc.value).lower()


def test_a_missing_vendor_unit_is_assumed_to_match_a_stated_requirement_unit():
    # datasheets routinely print the number in a column headed by the unit
    ok, why = compare(">=", 50.0, "ppm", 70.0, None, parameter="h2s_tolerance")
    assert ok is True and "assumed" in why.lower()


def test_an_unknown_operator_raises_rather_than_defaulting_to_pass():
    with pytest.raises(Unconvertible):
        compare("approximately", 50.0, "ppm", 50.0, "ppm", parameter="x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_units.py -v`
Expected: FAIL — `procurement.units` does not exist.

- [ ] **Step 3: Write minimal implementation**

Load-bearing: `Unconvertible` carrying a reason and never being swallowed; `to_number` returning `None` rather than `0.0`; the molar-mass gate; kVA as its own family; the assumed-unit rationale being stated in the returned string. Illustrative: the alias table's exact contents, the substance token list, and the wording of each rationale.

```python
# procurement/units.py
"""Unit normalisation for compliance arithmetic.

The model reads; this module decides what two printed quantities mean in one
unit. Anything it cannot convert honestly raises `Unconvertible` with a
reason, and compliance.py turns that into `unanswered` - never a silent pass
and never a fail. Failing a vendor on arithmetic nobody can audit is the one
outcome here that would make a compliance review indefensible.
"""
import math
import re

_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")


class Unconvertible(Exception):
    """Carries the reason, which is stored verbatim in the verdict rationale."""


# canonical unit per family; every alias maps to (family, factor) where
# canonical_value = raw * factor. Temperature is affine and handled apart.
_SCALAR = {
    "w": ("power", 0.001), "kw": ("power", 1.0), "mw": ("power", 1000.0),
    # apparent power is deliberately its own family: kW <-> kVA needs a power
    # factor no datasheet states in a form this module could read.
    "kva": ("apparent_power", 1.0), "mva": ("apparent_power", 1000.0),
    "pa": ("pressure", 0.001), "kpa": ("pressure", 1.0),
    "mpa": ("pressure", 1000.0), "bar": ("pressure", 100.0),
    "mbar": ("pressure", 0.1), "psi": ("pressure", 6.894757),
    "kg/cm2": ("pressure", 98.0665),
    "ppm": ("concentration", 1.0),          # mg/nm3 handled by molar mass
    "hz": ("frequency", 1.0), "khz": ("frequency", 1000.0),
    "v": ("voltage", 1.0), "kv": ("voltage", 1000.0),
    "kg/h": ("mass_flow", 1.0), "t/h": ("mass_flow", 1000.0),
    "nm3/h": ("volume_flow", 1.0), "m3/h": ("volume_flow", 1.0),
    "": ("dimensionless", 1.0),
}
_CANONICAL = {"power": "kw", "apparent_power": "kva", "pressure": "kpa",
              "concentration": "ppm", "frequency": "hz", "voltage": "v",
              "mass_flow": "kg/h", "volume_flow": "nm3/h",
              "temperature": "degc", "dimensionless": ""}

_ALIASES = {
    "°c": "degc", "degc": "degc", "deg c": "degc", "celsius": "degc", "c": "degc",
    "°f": "degf", "degf": "degf", "deg f": "degf", "fahrenheit": "degf", "f": "degf",
    "k": "k", "kelvin": "k",
    "mg/nm³": "mg/nm3", "mg/nm^3": "mg/nm3", "mg·nm⁻³": "mg/nm3",
    "mg/nm3": "mg/nm3", "mgnm3": "mg/nm3", "mg/m3": "mg/nm3",
    "kg/cm²": "kg/cm2", "nm³/h": "nm3/h", "m³/h": "m3/h",
    "kilowatt": "kw", "megawatt": "mw", "volts": "v", "hertz": "hz",
    "parts per million": "ppm", "ppmv": "ppm", "vppm": "ppm",
}

# Molar mass (g/mol) per substance, and the molar volume of an ideal gas at
# 0 degC / 101.325 kPa. mg/Nm3 = ppm * M / 22.414.
_MOLAR_MASS = {"h2s": 34.081, "no2": 46.0055, "nox": 46.0055, "so2": 64.066,
               "co2": 44.009, "co": 28.010, "ch4": 16.043, "o2": 31.998,
               "nh3": 17.031}
_MOLAR_VOLUME = 22.414
# longest first, so "co2" is not read as "co" and "no2" not as "no"
_SUBSTANCES = sorted(_MOLAR_MASS, key=len, reverse=True)


def _fold(unit: str | None) -> str:
    u = (unit or "").strip().lower().replace(" ", "")
    u = _ALIASES.get((unit or "").strip().lower(), _ALIASES.get(u, u))
    return u


def to_number(value) -> float | None:
    """Leading quantity of a printed value, or None. Never 0.0 as a fallback:
    a zero would compare as a real bound and fail a compliant vendor."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = _NUMBER.search(value)
    if match is None:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _substance(parameter: str | None) -> str | None:
    name = (parameter or "").lower()
    for token in _SUBSTANCES:
        if token in name:
            return token
    return None


def to_canonical(value: float, unit: str | None,
                 parameter: str | None = None) -> tuple[float, str]:
    """Return (value in the family's canonical unit, canonical unit name)."""
    u = _fold(unit)
    if u == "degc":
        return float(value), "degc"
    if u == "degf":
        return (float(value) - 32.0) * 5.0 / 9.0, "degc"
    if u == "k":
        return float(value) - 273.15, "degc"
    if u == "mg/nm3":
        token = _substance(parameter)
        if token is None:
            raise Unconvertible(
                f"cannot convert mg/Nm3 to ppm for {parameter!r}: the molar "
                "mass of the measured substance is unknown")
        return float(value) * _MOLAR_VOLUME / _MOLAR_MASS[token], "ppm"
    if u in _SCALAR:
        family, factor = _SCALAR[u]
        return float(value) * factor, _CANONICAL[family]
    raise Unconvertible(f"unrecognised unit {unit!r}")


def _family(unit: str | None, parameter: str | None) -> str:
    u = _fold(unit)
    if u in ("degc", "degf", "k"):
        return "temperature"
    if u == "mg/nm3":
        return "concentration"
    if u in _SCALAR:
        return _SCALAR[u][0]
    raise Unconvertible(f"unrecognised unit {unit!r}")


def _members(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v).strip().lower() for v in value]
    return [v.strip().lower() for v in str(value).split(",") if v.strip()]


def compare(operator: str, req_value, req_unit, fact_value, fact_unit,
            parameter: str | None = None) -> tuple[bool, str]:
    """Return (satisfied, rationale). Raises Unconvertible with a reason the
    caller stores verbatim on an `unanswered` verdict."""
    if operator == "in":
        allowed = _members(req_value)
        got = str(fact_value).strip().lower()
        numeric = to_number(fact_value)
        if numeric is not None:
            allowed_numbers = [to_number(a) for a in allowed]
            if any(n is not None and math.isclose(n, numeric, rel_tol=1e-9)
                   for n in allowed_numbers):
                return True, f"{fact_value} is one of {allowed}"
        return got in allowed, f"{fact_value} against allowed {allowed}"

    if operator not in (">=", "<=", "=="):
        raise Unconvertible(f"unsupported operator {operator!r}")

    lhs = to_number(req_value)
    rhs = to_number(fact_value)
    if lhs is None:
        raise Unconvertible(f"requirement value {req_value!r} is not a number")
    if rhs is None:
        if operator == "==":
            same = str(req_value).strip().lower() == str(fact_value).strip().lower()
            return same, f"{fact_value!r} against required {req_value!r}"
        raise Unconvertible(f"vendor value {fact_value!r} is not a number")

    note = ""
    if fact_unit is None or str(fact_unit).strip() == "":
        if req_unit not in (None, ""):
            fact_unit = req_unit
            note = f" (vendor unit not stated; assumed {req_unit})"

    lhs_family = _family(req_unit, parameter)
    rhs_family = _family(fact_unit, parameter)
    if lhs_family != rhs_family:
        if {lhs_family, rhs_family} == {"power", "apparent_power"}:
            raise Unconvertible(
                "cannot compare kW with kVA: the conversion needs a power "
                "factor, which the documents do not state")
        raise Unconvertible(
            f"cannot compare {req_unit!r} with {fact_unit!r}: different "
            f"quantities ({lhs_family} vs {rhs_family})")

    lhs_c, canonical = to_canonical(lhs, req_unit, parameter)
    rhs_c, _ = to_canonical(rhs, fact_unit, parameter)
    if operator == ">=":
        ok = rhs_c >= lhs_c or math.isclose(rhs_c, lhs_c, rel_tol=1e-9)
    elif operator == "<=":
        ok = rhs_c <= lhs_c or math.isclose(rhs_c, lhs_c, rel_tol=1e-9)
    else:
        ok = math.isclose(rhs_c, lhs_c, rel_tol=1e-9, abs_tol=1e-9)
    return ok, (f"{fact_value} {fact_unit or ''} = {rhs_c:g} {canonical} "
                f"{operator} {lhs_c:g} {canonical}{note}").strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_units.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add procurement/units.py tests/test_units.py && git commit -m "feat: unit normalisation with an honest unconvertible path"
```

---

### Task 6: Compliance matching and `compliance.json`

**Files:**
- Create: `procurement/compliance.py`
- Test: `tests/test_compliance.py`

**Interfaces:**
- Consumes: `units.compare` / `Unconvertible` (Task 5); `RequirementRecord`, `ComplianceResult` (Task 1); `snapshots.load_requirements` / `load_facts` / `save_compliance`; `project.load_project`.
- Produces:
  - `VERDICTS: tuple[str, ...] = ("pass", "fail", "deviation", "unanswered", "review")`
  - `evaluate(requirement, facts, deviations, vendor, now) -> ComplianceResult`
  - `evaluate_project(root, slug, now=None) -> list[ComplianceResult]` — loads the store, recomputes every cell, writes `compliance.json`, returns the results.
  - `vocabulary(reqset) -> list[str]` and `vocabulary_sha(params) -> str` (consumed by Task 7).
- **Store invariant owned (INV-6):** `compliance.json` has exactly one cell per (live requirement, live vendor) pair and no cell for any other pair — where a live requirement is stored and not `withdrawn`, and a live vendor is one listed in `project.vendors`.
- **Store invariant owned (INV-7):** every verdict names the `req_id` it was computed from, and a verdict citing a `fact_id` exists only while that fact is in that vendor's `technical`.

Both invariants hold **by construction** because `evaluate_project` recomputes the whole matrix from the current snapshots on every call — there is no incremental path and therefore no orphaning path. That is a deliberate trade: a few thousand Python comparisons in exchange for removing phase 2's **C1** shape from this collection entirely.

**Verdict order — the reasoning matters more than the code:**

1. **A `deviate` disposition on the clause wins outright.** The vendor has said in writing that it does not comply; numbers found elsewhere in its own documents do not overturn that. A `comply` disposition wins nothing — a vendor asserting compliance is not evidence of it, and treating it as evidence is how a bid review becomes a formality.
2. **A withdrawn requirement produces no cell at all** (INV-6's definition of live).
3. **`judgement` → `review`**, carrying the clause text and the candidate facts and deviation statements a human needs.
4. **No matching fact → `unanswered`**, never `fail`. This doubles as the extraction-coverage metric: a vendor at 40% `unanswered` means the pipeline did not read enough, and that is visible rather than hidden.
5. **Unconvertible units → `unanswered`** with the `Unconvertible` message stored verbatim in `rationale`.
6. **Otherwise the operator decides** `pass` or `fail`.

A vendor with no `facts.json` at all still gets a full column of `unanswered` cells. Omitting the column would hide a vendor whose extraction failed entirely — the **I2** shape, one level up.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compliance.py
import pytest

from procurement.compliance import (VERDICTS, evaluate, evaluate_project,
                                    vocabulary, vocabulary_sha)
from procurement.project import create_project, load_project, save_project
from procurement.store import snapshots
from procurement.store.models import (RequirementRecord, RequirementSet,
                                      VendorFacts, req_id_for)

NOW = "2026-07-30T12:00:00+00:00"


def _req(clause="4.2.7", **kw):
    body = dict(clause_ref=clause, text="H2S at least 50 ppm",
                category="technical", checkability="auto",
                parameter="h2s_tolerance", operator=">=", value=50.0,
                unit="ppm", source_doc_id="d1")
    body.update(kw)
    return RequirementRecord(req_id=req_id_for("d1", clause), **body)


def _fact(parameter="h2s_tolerance", value=70.0, unit="ppm", doc_id="d9"):
    return {"fact_id": f"f-{parameter}", "parameter": parameter, "value": value,
            "unit": unit, "verbatim": f"{value} {unit}", "doc_id": doc_id}


def _dev(clause="4.2.7", disposition="deviate"):
    return {"deviation_id": "v-1", "clause_ref": clause, "statement": "we differ",
            "disposition": disposition, "doc_id": "d8"}


def test_a_satisfied_bound_passes_and_cites_its_evidence():
    r = evaluate(_req(), [_fact()], [], "KERUI", NOW)
    assert r.verdict == "pass"
    assert (r.req_id, r.vendor, r.fact_id, r.doc_id) == (
        _req().req_id, "KERUI", "f-h2s_tolerance", "d9")
    assert "70" in r.rationale and r.evaluated_at == NOW


def test_an_unsatisfied_bound_fails():
    assert evaluate(_req(), [_fact(value=40.0)], [], "KERUI", NOW).verdict == "fail"


def test_a_missing_fact_is_unanswered_never_fail():
    r = evaluate(_req(), [_fact(parameter="frequency", value=50, unit="Hz")],
                 [], "KERUI", NOW)
    assert r.verdict == "unanswered" and r.fact_id is None
    assert "h2s_tolerance" in r.rationale


def test_no_facts_at_all_is_unanswered_not_fail():
    assert evaluate(_req(), [], [], "KERUI", NOW).verdict == "unanswered"


def test_a_deviated_clause_is_a_deviation_whatever_the_numbers_say():
    r = evaluate(_req(), [_fact(value=999.0)], [_dev()], "KERUI", NOW)
    assert r.verdict == "deviation" and "we differ" in r.rationale


def test_a_comply_disposition_is_not_evidence_of_compliance():
    # the vendor asserting compliance must not overturn its own datasheet
    r = evaluate(_req(), [_fact(value=40.0)], [_dev(disposition="comply")],
                 "KERUI", NOW)
    assert r.verdict == "fail"


def test_a_deviation_on_another_clause_is_ignored():
    r = evaluate(_req(), [_fact()], [_dev(clause="9.9")], "KERUI", NOW)
    assert r.verdict == "pass"


def test_clause_refs_match_across_printing_differences():
    r = evaluate(_req(), [_fact()], [_dev(clause="Clause 4.2.7")], "KERUI", NOW)
    assert r.verdict == "deviation"


def test_a_judgement_requirement_is_review_with_candidates_gathered():
    r = evaluate(_req(checkability="judgement", parameter=None, operator=None,
                      value=None, unit=None, text="Submit an O&M manual"),
                 [_fact()], [_dev(clause="9.9", disposition="noted")],
                 "KERUI", NOW)
    assert r.verdict == "review"
    assert "O&M manual" in r.rationale


def test_an_unconvertible_unit_is_unanswered_with_the_reason_stated():
    r = evaluate(_req(unit="kW"), [_fact(unit="kVA")], [], "KERUI", NOW)
    assert r.verdict == "unanswered"
    assert "power factor" in r.rationale.lower()
    assert r.fact_id == "f-h2s_tolerance"       # the evidence is still cited


def test_a_non_numeric_vendor_value_is_unanswered_not_fail():
    r = evaluate(_req(), [_fact(value="as per standard")], [], "KERUI", NOW)
    assert r.verdict == "unanswered" and "not a number" in r.rationale.lower()


def test_units_are_converted_before_comparing():
    r = evaluate(_req(parameter="continuous_rating", value=0.5, unit="MW"),
                 [_fact(parameter="continuous_rating", value=550.0, unit="kW")],
                 [], "KERUI", NOW)
    assert r.verdict == "pass"


def test_parameter_matching_tolerates_naming_differences():
    r = evaluate(_req(), [_fact(parameter="H2S Tolerance")], [], "KERUI", NOW)
    assert r.verdict == "pass"


def test_every_verdict_is_from_the_declared_vocabulary():
    for facts in ([], [_fact()], [_fact(value=1.0)], [_fact(unit="kVA")]):
        assert evaluate(_req(), facts, [], "KERUI", NOW).verdict in VERDICTS


# --- evaluate_project -------------------------------------------------------

def _project(tmp_path, requirements, facts_by_vendor):
    root = str(tmp_path)
    create_project(root, "P")
    project = load_project(root, "p")
    project.vendors = sorted(facts_by_vendor)
    save_project(root, project)
    snapshots.save_requirements(root, "p", RequirementSet(requirements=requirements))
    for vendor, facts in facts_by_vendor.items():
        if facts is not None:
            snapshots.save_facts(root, "p", VendorFacts(vendor=vendor,
                                                        technical=facts))
    return root


def test_the_matrix_has_one_cell_per_live_pair_and_no_other(tmp_path):
    # INV-6
    root = _project(tmp_path, [_req("4.2.7"), _req("4.2.8")],
                    {"KERUI": [_fact()], "MKON": [_fact(value=10.0)]})
    results = evaluate_project(root, "p", now=NOW)
    assert len(results) == 4
    assert {(r.req_id, r.vendor) for r in results} == {
        (req_id_for("d1", c), v) for c in ("4.2.7", "4.2.8")
        for v in ("KERUI", "MKON")}
    assert snapshots.load_compliance(root, "p") == results


def test_a_vendor_with_no_facts_still_gets_a_full_unanswered_column(tmp_path):
    root = _project(tmp_path, [_req()], {"KERUI": [_fact()], "AESL": None})
    results = evaluate_project(root, "p", now=NOW)
    aesl = [r for r in results if r.vendor == "AESL"]
    assert len(aesl) == 1 and aesl[0].verdict == "unanswered"


def test_a_withdrawn_requirement_produces_no_cell(tmp_path):
    root = _project(tmp_path, [_req("4.2.7"), _req("4.2.8", withdrawn=True)],
                    {"KERUI": [_fact()]})
    results = evaluate_project(root, "p", now=NOW)
    assert {r.req_id for r in results} == {req_id_for("d1", "4.2.7")}


def test_a_vendor_removed_from_the_project_leaves_the_matrix(tmp_path):
    root = _project(tmp_path, [_req()], {"KERUI": [_fact()], "MKON": [_fact()]})
    evaluate_project(root, "p", now=NOW)
    project = load_project(root, "p")
    project.vendors = ["KERUI"]
    save_project(root, project)
    results = evaluate_project(root, "p", now=NOW)
    assert {r.vendor for r in results} == {"KERUI"}
    assert {r.vendor for r in snapshots.load_compliance(root, "p")} == {"KERUI"}


def test_no_verdict_cites_a_fact_that_is_no_longer_stored(tmp_path):
    # INV-7
    root = _project(tmp_path, [_req()], {"KERUI": [_fact()]})
    assert evaluate_project(root, "p", now=NOW)[0].fact_id == "f-h2s_tolerance"
    snapshots.save_facts(root, "p", VendorFacts(vendor="KERUI", technical=[]))
    results = evaluate_project(root, "p", now=NOW)
    stored = {f["fact_id"] for f in
              (snapshots.load_facts(root, "p", "KERUI").technical or [])}
    assert all(r.fact_id is None or r.fact_id in stored for r in results)
    assert results[0].verdict == "unanswered"


def test_zero_requirements_writes_an_empty_matrix_not_a_missing_file(tmp_path):
    root = _project(tmp_path, [], {"KERUI": [_fact()]})
    assert evaluate_project(root, "p", now=NOW) == []
    assert snapshots.load_compliance(root, "p") == []


def test_recomputing_with_unchanged_inputs_is_byte_identical(tmp_path):
    root = _project(tmp_path, [_req()], {"KERUI": [_fact()]})
    first = [r.model_dump() for r in evaluate_project(root, "p", now=NOW)]
    second = [r.model_dump() for r in evaluate_project(root, "p", now=NOW)]
    assert first == second


# --- the parameter vocabulary ----------------------------------------------

def test_the_vocabulary_is_the_sorted_auto_parameter_names():
    reqset = RequirementSet(requirements=[
        _req("4.2.7"),
        _req("4.2.8", parameter="continuous_rating"),
        _req("9.1", checkability="judgement", parameter=None),
    ])
    assert vocabulary(reqset) == ["continuous_rating", "h2s_tolerance"]


def test_the_vocabulary_fingerprint_is_stable_and_order_independent():
    a = vocabulary_sha(["continuous_rating", "h2s_tolerance"])
    b = vocabulary_sha(["h2s_tolerance", "continuous_rating"])
    assert a == b == vocabulary_sha(["continuous_rating", "h2s_tolerance"])
    assert a != vocabulary_sha(["continuous_rating"])
    assert vocabulary_sha([])          # an empty vocabulary still fingerprints
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_compliance.py -v`
Expected: FAIL — `procurement.compliance` does not exist.

- [ ] **Step 3: Write minimal implementation**

Load-bearing: the verdict order; `unanswered` for both "no fact" and "unconvertible"; citing `fact_id` even on an `unanswered` unit failure (the evidence was found, only the comparison failed); iterating `project.vendors` rather than `list_fact_vendors`; wholesale recompute. Illustrative: `_norm_param`'s regex, the rationale wording, and how many candidates a `review` verdict gathers.

```python
# procurement/compliance.py
"""(requirements, facts, deviations) -> one verdict per requirement x vendor.

`unanswered` is never `fail`. Missing evidence means the pipeline did not read
enough, not that the vendor failed; collapsing the two is how a compliance
review becomes indefensible. It doubles as the extraction-coverage metric.

The matrix is recomputed wholesale on every call. There is no incremental
path, so there is no way for a verdict to outlive the requirement or the fact
it was computed from.
"""
import hashlib
import re
from datetime import datetime, timezone

from procurement import units
from procurement.project import load_project
from procurement.store import snapshots
from procurement.store.models import ComplianceResult, RequirementSet

VERDICTS = ("pass", "fail", "deviation", "unanswered", "review")

_NOISE = re.compile(r"[^a-z0-9]+")
_MAX_CANDIDATES = 5


def _norm(text: str | None) -> str:
    return _NOISE.sub("", (text or "").lower())


def _norm_clause(ref: str | None) -> str:
    return _NOISE.sub("", (ref or "").lower().replace("clause", ""))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def vocabulary(reqset: RequirementSet) -> list[str]:
    """The parameter names the datasheet pass will be asked to look for."""
    return sorted({r.parameter for r in reqset.requirements
                   if r.checkability == "auto" and r.parameter and not r.withdrawn})


def vocabulary_sha(params: list[str]) -> str:
    """Fingerprint of the vocabulary, part of the datasheet cache key. Sorted
    so a reordering is not a change; an empty vocabulary still hashes."""
    joined = "\n".join(sorted(params))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def evaluate(requirement, facts: list[dict], deviations: list[dict],
             vendor: str, now: str) -> ComplianceResult:
    def result(verdict, rationale, fact=None):
        return ComplianceResult(
            req_id=requirement.req_id, vendor=vendor, verdict=verdict,
            fact_id=(fact or {}).get("fact_id"), doc_id=(fact or {}).get("doc_id"),
            rationale=rationale, evaluated_at=now)

    want_clause = _norm_clause(requirement.clause_ref)
    deviated = next((d for d in deviations
                     if _norm_clause(d.get("clause_ref")) == want_clause
                     and d.get("disposition") == "deviate"), None)
    if deviated is not None:
        # The vendor has said in writing that it does not comply. A `comply`
        # disposition earns nothing: an assertion of compliance is not
        # evidence of it.
        return result("deviation",
                      f"vendor declared a deviation: {deviated.get('statement')}")

    if requirement.checkability != "auto":
        candidates = [f"{f.get('parameter')}={f.get('value')} {f.get('unit') or ''}".strip()
                      for f in facts[:_MAX_CANDIDATES]]
        statements = [d.get("statement") for d in deviations
                      if _norm_clause(d.get("clause_ref")) == want_clause]
        return result("review",
                      f"human judgement required: {requirement.text}"
                      + (f" | candidate facts: {candidates}" if candidates else "")
                      + (f" | vendor statements: {statements}" if statements else ""))

    want = _norm(requirement.parameter)
    fact = next((f for f in facts if _norm(f.get("parameter")) == want), None)
    if fact is None:
        return result("unanswered",
                      f"no vendor document stated {requirement.parameter!r}")

    try:
        ok, why = units.compare(requirement.operator, requirement.value,
                                requirement.unit, fact.get("value"),
                                fact.get("unit"), parameter=requirement.parameter)
    except units.Unconvertible as exc:
        # The evidence was found; only the comparison could not be made. Cite
        # the fact so a human can finish the check by hand.
        return result("unanswered", str(exc), fact)
    return result("pass" if ok else "fail", why, fact)


def evaluate_project(root: str, slug: str, now: str | None = None
                     ) -> list[ComplianceResult]:
    """Recompute and store the whole requirement x vendor matrix."""
    now = now or _now()
    reqset = snapshots.load_requirements(root, slug)
    live = [r for r in reqset.requirements if not r.withdrawn]
    # Vendors come from the project, not from the facts directory: a vendor
    # whose extraction failed entirely must still show a column of
    # `unanswered`, not vanish from the matrix.
    vendors = load_project(root, slug).vendors

    out: list[ComplianceResult] = []
    for vendor in vendors:
        stored = snapshots.load_facts(root, slug, vendor)
        facts = list(stored.technical) if stored else []
        deviations = list(stored.deviations) if stored else []
        for requirement in live:
            out.append(evaluate(requirement, facts, deviations, vendor, now))
    snapshots.save_compliance(root, slug, out)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_compliance.py tests/test_units.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add procurement/compliance.py tests/test_compliance.py && git commit -m "feat: compliance matching with unanswered distinct from fail"
```

---

### Task 7: Seed the datasheet pass with the parameter vocabulary

**Files:**
- Modify: `procurement/store/models.py` (`DocumentRecord.vocabulary_sha`)
- Modify: `procurement/pipeline.py`
- Test: `tests/test_pipeline_vocabulary.py`

**Interfaces:**
- Consumes: `compliance.vocabulary`, `compliance.vocabulary_sha` (Task 6); `extract_tech_facts`'s existing `parameters` argument, which phase 2 added for exactly this purpose and no caller has ever supplied (it defaults to `None`), so no signature change is needed.
- Produces: `DocumentRecord.vocabulary_sha: str | None = None`; the vendor pass now computes the vocabulary once per run, after the RFQ pass and before any datasheet is read.
- **Store invariant owned (INV-8):** every datasheet `DocumentRecord` records the `vocabulary_sha` its stored facts were extracted under, and that value equals the current fingerprint for exactly the datasheets whose facts are in `technical` — so a requirement edit re-asks every datasheet exactly once and never again.

Spec §7 makes the parameter vocabulary the join key between the two halves of the system, and spec §13 names drift between it and the facts as the top open risk. Without the fingerprint in the cache key, editing a requirement changes what the datasheet pass *would* be asked for but never re-asks: every affected cell stays `unanswered` forever, and the coverage metric reports a pipeline failure as a vendor's silence.

**Two costs, both bounded and both intended.** The first phase-3 run over a phase-2 store re-extracts every datasheet once, because their stored `vocabulary_sha` is `None`. A later requirement edit re-extracts that project's datasheets once. Neither recurs: run 3 after either makes zero calls, and matrix rows 4 and 13 assert exactly that.

The fingerprint gates **datasheets only**. A quotation or deviation form is not asked about parameters, so folding the vocabulary into their cache key would re-extract them for no reason — the same cross-class invalidation phase 2's `PROMPT_VERSION_BY_CLASS` exists to prevent.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_vocabulary.py
import os

from procurement.compliance import vocabulary_sha
from procurement.pipeline import run_ingestion
from procurement.project import create_project, load_project, save_project
from procurement.store import snapshots

from tests.test_pipeline_rfq import RfqClient      # the schema-aware stub


class VocabClient(RfqClient):
    """Records the context text handed to each datasheet call."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.tech_context = []

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        if "facts" in output_schema.model_fields:
            self.tech_context.append(context_text)
        return super().classify_structure(prompt, output_schema, context_text, images)


_MR = "ADN-AEC-ME-SPC-026 MR Gas Genset.txt"


def _project(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    rdir = tmp_path / "p" / "requirements"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / _MR).write_text("4.2.7 H2S at least 50 ppm", encoding="utf-8")
    vdir = tmp_path / "p" / "vendors" / "KERUI"
    vdir.mkdir(parents=True)
    (vdir / "Quotation.txt").write_text("base price 1000", encoding="utf-8")
    (vdir / "01 DataSheet Gas Generator.txt").write_text("H2S up to 70 ppm",
                                                         encoding="utf-8")
    project = load_project(root, "p")
    project.vendors = ["KERUI"]
    save_project(root, project)
    return root


def _datasheet(root):
    return next(d for d in snapshots.load_documents(root, "p")
                if d.path.endswith("01 DataSheet Gas Generator.txt"))


def test_the_datasheet_prompt_is_seeded_with_the_auto_parameter_names(tmp_path):
    root = _project(tmp_path)
    client = VocabClient()
    run_ingestion(root, "p", client)
    assert client.tech_context, "the datasheet was never extracted"
    assert "h2s_tolerance" in client.tech_context[0]


def test_the_requirements_pass_runs_before_the_datasheet_pass(tmp_path):
    root = _project(tmp_path)
    client = VocabClient()
    run_ingestion(root, "p", client)
    assert client.calls.index("requirements") < client.calls.index("facts")


def test_the_datasheet_records_the_vocabulary_it_was_extracted_under(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", VocabClient())
    assert _datasheet(root).vocabulary_sha == vocabulary_sha(["h2s_tolerance"])


def test_a_rerun_with_unchanged_requirements_makes_zero_calls(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", VocabClient())
    second = VocabClient()
    run_ingestion(root, "p", second)
    assert second.calls == []


def test_changing_a_requirement_reextracts_every_datasheet_exactly_once(tmp_path):
    # INV-8
    root = _project(tmp_path)
    run_ingestion(root, "p", VocabClient())
    (tmp_path / "p" / "requirements" / _MR).write_text(
        "4.2.7 continuous rating at least 500 kW", encoding="utf-8")

    second = VocabClient()
    second_response_parameter = "continuous_rating"
    second.requirement_parameter = second_response_parameter   # see RfqClient
    run_ingestion(root, "p", second)
    assert second.calls.count("facts") == 1
    assert _datasheet(root).vocabulary_sha == vocabulary_sha([second_response_parameter])

    third = VocabClient()
    third.requirement_parameter = second_response_parameter
    run_ingestion(root, "p", third)
    assert third.calls.count("facts") == 0, "the bump did not persist"


def test_the_vocabulary_does_not_invalidate_quotations_or_deviations(tmp_path):
    root = _project(tmp_path)
    (tmp_path / "p" / "vendors" / "KERUI"
     / "03 Attachment-2 Vendor Deviation Form.txt").write_text("4.2.7 differs",
                                                               encoding="utf-8")
    run_ingestion(root, "p", VocabClient())
    (tmp_path / "p" / "requirements" / _MR).write_text(
        "4.2.7 continuous rating at least 500 kW", encoding="utf-8")
    second = VocabClient()
    second.requirement_parameter = "continuous_rating"
    run_ingestion(root, "p", second)
    assert second.calls.count("facts") == 1
    assert second.calls.count("deviations") == 0
    assert second.calls.count("bid") == 0


def test_a_project_with_no_requirements_still_extracts_datasheets(tmp_path):
    root = _project(tmp_path)
    os.remove(os.path.join(root, "p", "requirements", _MR))
    client = VocabClient()
    run_ingestion(root, "p", client)
    assert client.calls.count("facts") == 1
    assert _datasheet(root).vocabulary_sha == vocabulary_sha([])
    facts = snapshots.load_facts(root, "p", "KERUI")
    assert facts.technical, "an empty vocabulary must not suppress extraction"


def test_a_phase2_store_reextracts_each_datasheet_once_then_settles(tmp_path):
    # the one-time migration cost: stored vocabulary_sha is None
    root = _project(tmp_path)
    run_ingestion(root, "p", VocabClient())
    docs = snapshots.load_documents(root, "p")
    for doc in docs:
        if doc.path.endswith("01 DataSheet Gas Generator.txt"):
            doc.vocabulary_sha = None
    snapshots.save_documents(root, "p", docs)

    second = VocabClient()
    run_ingestion(root, "p", second)
    assert second.calls.count("facts") == 1
    third = VocabClient()
    run_ingestion(root, "p", third)
    assert third.calls.count("facts") == 0
```

`RfqClient` needs one addition for this suite — a `requirement_parameter` attribute, defaulting to `"h2s_tolerance"`, used in the `requirements` branch of its response. Add it in Task 7 and update `tests/test_pipeline_rfq.py` accordingly; keep the default so that suite's assertions are unchanged.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline_vocabulary.py -v`
Expected: FAIL — `DocumentRecord` has no `vocabulary_sha`, and the datasheet context carries no parameter list.

- [ ] **Step 3: Write minimal implementation**

Load-bearing: computing the vocabulary once, after the RFQ pass and before the vendor loop; gating on the fingerprint for `datasheet` only; carrying `vocabulary_sha` forward on a cache hit (omitting that is **C2a** exactly — the bump would never persist and every datasheet would re-extract on every run, forever).

```python
# procurement/store/models.py  (DocumentRecord, one added field)

class DocumentRecord(BaseModel):
    ...
    prompt_version: str | None = None
    # Fingerprint of the `auto` requirement vocabulary this document's facts
    # were extracted under. Part of the datasheet cache key: the vocabulary is
    # an input to the tech_facts_v1 prompt, so a requirement edit that changes
    # it must re-ask the datasheets exactly once. None for every other class.
    vocabulary_sha: str | None = None
```

In the snippet below, `...` marks existing lines of `run_ingestion` that are unchanged — it is a diff against the function as Task 4 leaves it, not an omission to fill in.

```python
# procurement/pipeline.py

from procurement.compliance import vocabulary, vocabulary_sha

    with snapshots.transaction(root, slug):
        rfq_docs, rfq_ok, rfq_failed = _run_rfq_pass(...)
        extracted += rfq_ok
        failed += rfq_failed

        # Requirements first, then their vocabulary, then the datasheets - the
        # ordering spec section 7 requires. Read back from the store rather
        # than from the pass's return value, so overrides applied to a
        # requirement's parameter are part of the vocabulary too.
        parameters = vocabulary(snapshots.load_requirements(root, slug))
        vocab_sha = vocabulary_sha(parameters)

        for doc in fresh_docs:
            ...
            unchanged = (prior is not None
                         and prior.content_sha256 == doc.content_sha256
                         and prior.prompt_version == expected_version
                         and prior.extraction_status == "ok"
                         and (route != "datasheet"
                              or prior.vocabulary_sha == vocab_sha))
            if unchanged and not force:
                ...
                doc.vocabulary_sha = prior.vocabulary_sha   # or the bump never persists
                ...
                continue
            ...
            elif route == "datasheet":
                facts, status, notes = extract_tech_facts(
                    doc.doc_id, full, client, pdf_fallback=pdf_fallback,
                    parameters=parameters)
                doc.vocabulary_sha = vocab_sha
                ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pipeline_vocabulary.py tests/test_pipeline_rfq.py tests/test_pipeline_routing.py tests/test_pipeline_incremental.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add procurement/store/models.py procurement/pipeline.py tests/test_pipeline_vocabulary.py tests/test_pipeline_rfq.py && git commit -m "feat: seed the datasheet prompt with the requirement vocabulary and cache on it"
```

---

### Task 8: Integration — compliance in the run, and the two-run mutation matrix

**Files:**
- Modify: `procurement/pipeline.py`
- Test: `tests/test_phase3_lifecycle.py`

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces: `run_ingestion` writes `compliance.json` inside its transaction and appends a `compliance.evaluated` event; `load_dataset`'s returned dict is unchanged.
- **Store invariant owned (INV-9):** `compliance.json` is never computed from a pre-change snapshot — after any run, every verdict's `evaluated_at` is at or after that run's `run.started`, and all four collections (`documents`, `requirements`, `vendors/*/facts`, `compliance`) are covered by the single `generation` bump of that run's transaction.

Compliance is evaluated **last inside the transaction**, after `_prune_orphan_facts`, after the stale-vendor sweep, and after `save_documents`. Every earlier position would compute verdicts from facts a later step then removes — a verdict citing a pruned fact, which is INV-7's failure mode arriving through the back door.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_phase3_lifecycle.py — the wiring assertions; the matrix follows in Step 4b
import os

from procurement.pipeline import run_ingestion
from procurement.store import events, snapshots

from tests.test_pipeline_vocabulary import VocabClient, _project


def test_a_run_writes_the_compliance_matrix(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", VocabClient())
    results = snapshots.load_compliance(root, "p")
    assert results and {r.vendor for r in results} == {"KERUI"}
    assert results[0].verdict == "pass"        # 70 ppm >= 50 ppm
    assert "compliance.evaluated" in [e.action for e in events.read_events(root, "p")]


def test_every_verdict_is_at_or_after_the_run_that_produced_it(tmp_path):
    # INV-9
    root = _project(tmp_path)
    run_ingestion(root, "p", VocabClient())
    (tmp_path / "p" / "vendors" / "KERUI"
     / "01 DataSheet Gas Generator.txt").write_text("H2S up to 40 ppm",
                                                    encoding="utf-8")
    run_ingestion(root, "p", VocabClient())
    log = events.read_events(root, "p")
    last_start = [e for e in log if e.action == "run.started"][-1].at
    assert all(r.evaluated_at >= last_start for r in snapshots.load_compliance(root, "p"))


def test_one_run_bumps_the_generation_exactly_once(tmp_path):
    root = _project(tmp_path)
    before = snapshots.get_generation(root, "p")
    run_ingestion(root, "p", VocabClient())
    assert snapshots.get_generation(root, "p") == before + 1


def test_load_dataset_keeps_its_existing_shape(tmp_path):
    from procurement.pipeline import load_dataset
    root = _project(tmp_path)
    run_ingestion(root, "p", VocabClient())
    assert set(load_dataset(root, "p")) == {"bids", "normalized", "comparison"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_phase3_lifecycle.py -v`
Expected: FAIL — `compliance.json` is never written; `snapshots.load_compliance` returns `[]`.

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/pipeline.py, at the end of the transaction body

        snapshots.save_documents(root, slug, rfq_docs + documents)

        # Last inside the transaction, on purpose: every earlier position
        # would evaluate against facts that _prune_orphan_facts or the
        # stale-vendor sweep then removes, producing a verdict citing a fact
        # that no longer exists.
        results = compliance.evaluate_project(root, slug)
        events.append_event(root, slug, Event(
            at=_now(), run_id=run_id, actor="pipeline",
            action="compliance.evaluated", target=None,
            detail={"cells": len(results),
                    "by_verdict": {v: sum(1 for r in results if r.verdict == v)
                                   for v in compliance.VERDICTS}}))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_phase3_lifecycle.py -v`
Expected: PASS

- [ ] **Step 4b: Two-run mutation matrix**

One test per row, in `tests/test_phase3_lifecycle.py`, each named for the row it defends and each asserting over a **loaded snapshot** after run 2 (or run 3 where stated). A row that can be satisfied by a single-run assertion is not testing what it claims.

| # | mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|---|
| 1 | a newer revision of the extracted spec arrives (`MR.txt` → also `MR(Rev1).txt`) | INV-4 | `requirements.json` holds no requirement whose `source_doc_id` is the superseded document; the old document's status is `skipped` with a `superseded by` note |
| 2 | the MOM is deleted from `requirements/` | INV-5 | its amendments are gone; every requirement it amended has `amended_by is None`, `base_body is None`, and its original value restored |
| 3 | a sibling spec revision arrives in a second upload (run 1 has only `MR(Rev1)`, run 2 adds `MR`) | INV-4 | the newest document's `supersedes` names its immediate predecessor, and requirements come only from the newest |
| 4 | bump each prompt-version constant one at a time: `REQUIREMENTS_PROMPT_VERSION`, `MOM_PROMPT_VERSION`, `TECH_PROMPT_VERSION`, `DEVIATION_PROMPT_VERSION`, `CLASSIFY_PROMPT_VERSION` | INV-4, INV-8 | run 2 re-extracts **only** that class and persists the new version onto the record; run 3 makes zero calls of any kind |
| 5 | the requirements call fails on run 1 and succeeds on run 2 | INV-4 | run 2 retries, the requirements land, and `doc.notes` is cleared to `None` |
| 6 | the requirements call fails on both runs | INV-4 | `doc.notes` carries the provider message after each run; `requirements.json` is unchanged; the document is not marked `ok` |
| 7 | the only spec document is one the rules classify as `datasheet` | INV-4 | it is still routed to `requirements_v1`, an `rfq.requirements_inferred` event exists, and requirements are stored — the entity does not silently vanish |
| 8 | the spec is edited and its re-extraction fails | INV-4 | the previously-stored requirements survive byte-for-byte; `extraction_status` is `failed` |
| 9 | the client returns `{}` with no `requirements` key | INV-4 | status is `ok` with zero requirements, not `failed`; the document is not retried on run 3 |
| 10 | the authoritative spec is renamed to `… Superseded with MOM ….txt` | INV-4 | its requirements leave `requirements.json` in one run, and a `requirements.pruned` event names the count |
| 11 | the MOM is withdrawn while its amendment is applied to a live requirement | INV-5 | the requirement reverts to exactly its `base_body` values, including `text` |
| 12 | a requirement disappears (its spec is deleted) while an override targets `requirements[<req_id>].value` | INV-1 | the override survives with `conflict=True` and its `value` intact; it never retargets another requirement |
| 13 | a requirement's `parameter` changes, changing the vocabulary fingerprint | INV-8 | every datasheet re-extracts exactly once on run 2 and zero times on run 3; each stores the new `vocabulary_sha` |
| 14 | the datasheet whose fact a verdict cited is deleted | INV-7 | no verdict in `compliance.json` cites a `fact_id` absent from that vendor's `technical`; the cell is `unanswered`, not `fail` |
| 15 | a vendor is removed from `project.vendors` | INV-6 | no cell names that vendor; the remaining vendors' cells are unchanged; cell count equals live requirements × live vendors |
| 16 | the vendor restates its value in a unit that cannot be converted (`ppm` → `mg/Nm3` on a parameter naming no known substance) | INV-7 | the verdict is `unanswered` with the molar-mass reason in `rationale`, and never `pass` or `fail` |
| 17 | a datasheet changes, so facts change after compliance was last computed | INV-9 | every verdict's `evaluated_at` is at or after run 2's `run.started`, and the changed fact's cell reflects the new value |
| 18 | the model returns an `auto`-looking clause whose `unit` is `null` | INV-2 | it is stored as `judgement`; its cell is `review`, never a numeric `pass` |
| 19 | the MOM amends a `clause_ref` no requirement has | INV-3 | the amendment is stored with `req_id is None`, no requirement's `amended_by` is set, and no requirement's value changed |
| 20 | a second spec document printing the same `clause_ref` arrives, after a run in which the amendment resolved | INV-3 | the amendment reverts to `req_id is None` with an `unresolved_reason` naming both documents, the previously-amended requirement is back at its `base_body` values with `base_body is None`, and neither document's requirement is amended |

- [ ] **Step 4c: Verify the matrix is real, not decorative**

Reinstate each defect one at a time and confirm the intended row fails **and that nothing else does**. A row that still passes with its defect reinstated is not testing what it claims. Phase 2's fix wave did this with one throwaway script; do the same and delete it afterwards.

Minimum defect list, one per row:

| row | defect to reinstate |
|---|---|
| 1, 10 | drop the `live_spec` filter in `_run_rfq_pass` |
| 2, 11 | make `apply_amendments` skip the rebuild-from-`base_body` step |
| 3 | restore `documents.append(prior)` in place of the carry-forward |
| 4 | drop `prompt_version` from the `unchanged` gate |
| 5 | drop `classified_by != "llm-failed"` / treat `failed` as cacheable |
| 6 | discard `notes` in the extractor's `except` |
| 7 | make `_rfq_route` return `doc.doc_class` unchanged |
| 8 | assign the fresh (empty) result regardless of `status` |
| 9 | use `raw["requirements"]` instead of `raw.get(...) or []` |
| 12 | change `req_id_for` to include the clause text |
| 13 | drop `vocabulary_sha` from the datasheet gate |
| 14 | make `evaluate_project` merge into the prior `compliance.json` instead of replacing it |
| 15 | iterate `snapshots.list_fact_vendors` instead of `project.vendors` |
| 16 | give `to_canonical` a default molar mass |
| 17 | move `evaluate_project` above `_prune_orphan_facts` |
| 18 | drop the `auto` demotion in `extract_requirements` |
| 19 | drop unmatched amendments instead of storing them |
| 20 | bind to `matches[0]` instead of requiring exactly one match |

- [ ] **Step 5: Commit**

```bash
git add procurement/pipeline.py tests/test_phase3_lifecycle.py && git commit -m "feat: evaluate compliance in the run, with the two-run mutation matrix"
```

---

## Verification of phase 3 done-criteria

Spec §11 says phase 3 is done when *"every requirement × vendor pair carries a verdict, with `unanswered` distinct from `fail`."* Confirm all of the following before declaring the phase complete.

- [ ] `python -m pytest` from the repo root. Expected: the phase-2 baseline (**272 passed, 3 skipped, 1 failed** — the environment-dependent portal test) **plus** every test added by Tasks 1–8. Any other failure is a real regression.
- [ ] `snapshots.load_compliance` on a fixture project returns exactly `len(live requirements) × len(project.vendors)` cells, and `{r.verdict for r in cells} ⊆ VERDICTS`.
- [ ] A vendor with no extractable technical document reports a column of `unanswered` — not absence, and not `fail`.
- [ ] Run the pipeline twice with no changes: zero LLM calls of any kind on the second run (`client.calls == []`).
- [ ] Delete `projects/<slug>/index/store.db` and re-run every query: identical results (the spec §10 index contract, unchanged by this phase).
- [ ] `git grep -n "requirements.json\|compliance.json" -- procurement | grep -v "store/layout.py"` returns nothing outside `store/snapshots.py` — no module hand-rolls a path to either snapshot.
- [ ] Live-guarded manual check, following the `a6d23cd` skip-guard pattern: one real extraction of `processed-data/01-client-mr-rfq/` against a real provider, confirming the MR yields recognisable clauses and the MOM amends at least one of them. Record the observed `unanswered` percentage — spec §7 makes it the extraction-coverage metric, and it is the number phase 4's Compliance screen has to make visible.

## Deliberately out of scope

Carried to phase 4, per spec §8 and §11:

- The six portal screens, `app.py`'s split into a shell plus `portal/views/`, and override editing with mandatory reasons. `requirements.json` already carries an `overrides` list and `reconcile` already flags conflicts against it; phase 4 writes to it.
- Making the `unanswered` coverage percentage visible in the UI.
- Editing a requirement's `checkability` by hand to promote a `judgement` clause to `auto`.

Carried to the roadmap, per spec §12:

- BOM line-item extraction and reconciliation; weighted vendor scoring and award recommendation; full scope normalization; `.docx` support in `loaders.read_text`.

## Checklist before this plan is approved

- [x] Every task's `Interfaces` block has a **Store invariant owned** bullet. Task 5 declares **none** with its reason — `units.py` writes nothing to the store, and Rule 1 forbids inventing an invariant checkable without a snapshot.
- [x] No invariant is claimed twice; every stored collection this phase introduces (`requirements`, `amendments`, `compliance`) is claimed — INV-4/INV-5, INV-3, INV-6/INV-7 respectively.
- [x] The integration task carries a mutation matrix with all nine required rows (1–9) plus ten phase-specific rows (10–19) covering each new accumulating collection.
- [x] Every matrix row names an invariant; every invariant has at least one row (see the ledger's right-hand column).
- [x] The Rule 3 banner appears above the first reference block.
- [x] Each reference block states which parts are load-bearing and which are illustrative.
