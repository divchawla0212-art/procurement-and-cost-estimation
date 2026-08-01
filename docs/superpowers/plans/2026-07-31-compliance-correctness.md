# Compliance Correctness — Deviation Scoping and Duplicate RFQ Documents — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This plan follows [`docs/superpowers/PLAN-TEMPLATE.md`](../PLAN-TEMPLATE.md).** Read it before starting.

**Spec:** [`docs/superpowers/specs/2026-07-31-compliance-correctness-design.md`](../specs/2026-07-31-compliance-correctness-design.md) (approved 2026-07-31)

**Goal:** Stop the compliance matrix from asserting things it cannot attribute — a deviation binds only to a clause it provably names, and three copies of one MR report as one visible group instead of three silent near-copies of every cell.

**Architecture:** Two data-layer changes. `compliance.evaluate_project` computes a live clause-ref count map once per run and passes it into `evaluate`, which binds a deviation only where the count is 1 and otherwise falls through to normal evaluation, downgrading a `pass` to `review`. `pipeline._run_rfq_pass` gains an observational near-duplicate pass over live spec documents' clause-ref sets, writing a `duplicate_group` id onto `DocumentRecord` and nothing else. `portal/` is untouched and `load_dataset` keeps its three keys.

**Tech Stack:** Python 3.12, Pydantic v2, `hashlib`, `re`, `pytest`. No new dependencies.

---

## State of the spec on disk before this plan starts

Read this section first. Two of the spec's assumptions are already stale.

1. **Spec fix 2 (`between`) has already shipped**, in commit `6bed22b` ("a stated range needs its own operator, or `in` fails a compliant vendor"). `_OPERATORS` in [`extract_requirements.py:26`](../../../procurement/extract_requirements.py:26) already carries `between`; [`units.py:205-230`](../../../procurement/units.py:205) implements inclusive bounds with reversed-bound sorting; `requirements_v2.txt` teaches range-vs-enumeration; `REQUIREMENTS_PROMPT_VERSION` is already `requirements_v2`. **This plan does not re-implement it.** Task 0 is a five-minute verification that it is genuinely complete, and stops there.

   Consequence for spec §3's task ordering: the spec put fix 2 last so that duplicate detection would be exercised against both the old and the new requirement sets. That exposure is no longer available from ordering. Task 4's matrix rows 25–27 buy it back explicitly by mutating the requirement set between runs.

2. **The baseline is `486 passed, 3 skipped, 1 failed`, not the spec §11 figure of 456.** Measured on this branch at `ed3caeb`, 2026-07-31. The one failure is the documented environment-dependent `tests/test_portal_app.py::test_missing_api_key_does_not_block_creation` (see `CLAUDE.md`). Anything else is a real regression.

3. **INV-10 is contested by a draft plan.** [`docs/superpowers/plans/2026-07-31-compliance-accuracy-from-live-corpus.md`](2026-07-31-compliance-accuracy-from-live-corpus.md) is untracked, unexecuted, and claims INV-10 for a `units.py` coverage invariant and matrix rows 21–23. The approved spec assigns **INV-10 to deviation scoping and INV-11 to duplicate detection**, so this plan uses those numbers and rows 21–27. If the accuracy plan is executed afterwards, its invariant becomes **INV-12** and its rows renumber. Whoever lands second renumbers; the approved spec's numbering does not move.

---

## Global Constraints

Every task's requirements implicitly include this section.

- Python `>=3.12`; dependencies unchanged.
- The snapshots under `projects/<slug>/store/` are the only authoritative store. Every write goes through `procurement/store/snapshots.py`; never hand-roll a snapshot write.
- `generation` bumps **once per write transaction**, never once per file.
- `field_path` addresses list members by **id**, never index.
- A stored collection contains exactly the records of its currently-live sources — no more.
- Missing data is never coerced to a passing or zero value. **`unanswered` is never `fail`.**
- **Arithmetic stays in Python.** The model reads; code decides. No extractor is asked whether a vendor complies.
- A failed extraction never blanks previously-good stored data, and always records why in `DocumentRecord.notes`.
- **No document is superseded, pruned or merged by anything in this plan.** Duplicate detection is observational; acting on a group is Spec 2.
- `classify.py`'s vocabulary and `_RULES` stay frozen. `portal/` is untouched.
- Tests run key-free via `MockLLMClient` or a local stub. No test may require `ANTHROPIC_API_KEY`.
- Run all tests from the repo root: `python -m pytest`.
- Baseline: **486 passed, 3 skipped, 1 failed.**

---

## Judgment calls made while writing this plan

These are decisions the spec does not settle. Each is implemented as described below; flag disagreement before starting, not mid-execution.

1. **`clause_counts=None` means "unscoped", and preserves today's binding behaviour.** Every existing `evaluate(...)` call site in `tests/test_compliance.py` passes five positional arguments; making the map required would rewrite twenty tests that are about something else. The map is a keyword argument defaulting to `None`, and `evaluate_project` — the only production caller — always passes it. The risk this accepts is that a future caller could forget it and silently get pre-fix behaviour; INV-10 is asserted over `compliance.json`, which only `evaluate_project` writes, so the risk is confined to code nobody has written yet.

2. **The zero-match event is emitted from `evaluate_project`, not from `evaluate`.** A deviation matching no live requirement reaches no cell at all, so `evaluate` structurally cannot see it. Both reasons are therefore emitted from one loop in `evaluate_project`, once per run per deviation, rather than one from each place — which is also what stops the ambiguous case being emitted once per affected cell.

3. **`evaluate_project` gains an optional `run_id`.** It has to write events and `Event.run_id` is required. `run_id=None` mints a fresh one via `events.new_run_id()`, so the existing direct-call tests keep working; `pipeline.run_ingestion` passes the run's own id so the events sit in the run they belong to.

4. **A synthesized clause ref (`#7`) is excluded from a document's clause set.** `extract_requirements` writes `#{position}` when the model gives no clause number. Two documents whose clauses are all synthesized would overlap perfectly on position alone and group as duplicates while sharing nothing. Excluding them means such a document has a set below the 5-clause floor and never groups — the correct outcome.

5. **Only `extraction_status == "ok"` documents are eligible to group.** `"failed"` counts as *live* everywhere else in this pipeline, deliberately, so that an outage does not delete good stored data. A failed document's stored requirements describe an earlier run, so grouping on them would report a duplicate that is not visible in the current corpus. The spec states this asymmetry; this is where it is implemented.

6. **`_norm_clause` is imported from `compliance`, not re-spelled a third time.** `extract_mom.py` already carries a private copy. A third would be a third place for the normalisation to drift, and a group found on refs `compliance` treats as distinct would be a group nobody can explain. Accepted cost: `"2.3#7"` and `"2.3.7"` both normalise to `"237"`, so a disambiguated duplicate ref can collide with a real three-level ref. The consequence is at most a misleading grouping, never a lost requirement — INV-11 is what guarantees that.

7. **Detection reads requirements back from the store, not from `_run_rfq_pass`'s local `applied` dict.** Same reasoning `run_ingestion` already applies to the parameter vocabulary: an override that edits a clause ref must be part of what detection sees.

---

## File structure

| file | change |
|---|---|
| `procurement/compliance.py` | `evaluate` gains `clause_counts`; `evaluate_project` builds the map, passes it, emits `deviation.unattributed`, gains `run_id` |
| `procurement/store/models.py` | `DocumentRecord.duplicate_group: str \| None`; `duplicate_group_id(doc_ids)` beside the other id helpers |
| `procurement/pipeline.py` | `_clause_sets`, `near_duplicate_groups`, the detection block at the end of `_run_rfq_pass`, and `run_id=` on the `evaluate_project` call |
| `tests/test_compliance.py` | deviation scoping, unit and project level |
| `tests/test_store_models.py` | `duplicate_group_id` stability |
| `tests/test_pipeline_rfq.py` | grouping, the clause floor, transitivity, a failed member |
| `tests/test_phase3_lifecycle.py` | `MatrixClient` deviation knob; matrix rows 21–27 |

`pipeline.py` is already 644 lines and this adds ~60. It stays there because spec §3 names it as the module for fix 3 and the detection block is three statements at the end of a function that already owns the RFQ pass. Splitting it out is not in scope here.

---

## Store invariant ledger

| id | invariant (over stored state) | owner | matrix rows |
|---|---|---|---|
| **INV-2** *(extended)* | An `auto` requirement with `operator == "between"` carries `value` as a two-element numeric list with `lo <= hi`. Any other shape is stored `judgement`. | **already shipped** (`6bed22b`); Task 0 verifies | — |
| **INV-10** *(new)* | Every `deviation` cell in `compliance.json` names a clause carried by exactly one live requirement in `requirements.json`, and no cell reads `pass` while an unattributed deviation cites its clause — it reads `review`, naming the deviation and its source document. | Task 1 | 21, 22, 23, 24 |
| **INV-11** *(new)* | Duplicate detection is observational: `requirements.json` is byte-identical whether or not it ran, and every member of a `duplicate_group` in `documents.json` is live and un-pruned. | Task 3 | 25, 26, 27 |

INV-4, INV-6, INV-7, INV-8 and INV-9 are inherited unchanged and must still hold. INV-4 is the one Task 3 would break if grouping ever became pruning; row 26 is its guard.

**Tasks 0 and 2 own no store invariant.** Task 0 writes no code. Task 2 adds a field with no writer and a pure id function — Rule 1 is explicit that anything checkable without loading a snapshot is not a store invariant, and Rule 1 also says an invariant belongs to the task that writes the store last, which is Task 3. Task 4 owns none either: it is the assertion layer for INV-10 and INV-11, both already claimed.

**The nine baseline mutation rows are not re-derived here.** They exist as rows 1–20 of `tests/test_phase3_lifecycle.py` and this plan introduces no new *accumulating* collection — `duplicate_group` is a field on a collection replaced wholesale every run, and `compliance.json` is already recomputed wholesale. Task 4 Step 4c requires rows 1–20 green rather than re-implementing them. Rows 21–27 cover what this plan does change.

---

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the
> implementation against the test.
>
> **Load-bearing in every sample:** that a deviation binds only on a count of
> exactly 1; that `fail` and `unanswered` are never rewritten; that the count
> map is built fresh inside `evaluate_project` and never stored or cached; that
> detection writes to `DocumentRecord` and to nothing else; that the group id is
> a hash of the sorted member ids. **Illustrative and expected to change:** the
> exact rationale and event-detail wording, the union-find spelling, the field
> ordering in every detail dict.

---

### Task 0: Confirm the `between` operator is genuinely complete

**Files:**
- Modify: none.
- Test: none new.

**Interfaces:**
- Consumes: nothing.
- Produces: nothing.
- **Store invariant owned: none.** This task writes no code. INV-2's extension was shipped by commit `6bed22b` and is defended by the tests that commit added.

Spec §5 describes work that already exists on this branch. Executing it again would bump `REQUIREMENTS_PROMPT_VERSION` a second time and buy a whole re-extraction of every spec document for nothing. This task exists so that the person executing the plan checks rather than assumes — and so that, if any of the four checks below fails, they stop and re-scope instead of discovering it in Task 4.

- [ ] **Step 1: Run the four checks**

```bash
python -m pytest tests/test_units.py tests/test_extract_requirements.py -q -k "between or range"
```

Expected: PASS, with at least one test each for inclusive bounds, reversed-bound sorting, family mismatch, and demotion of a malformed `between` to `judgement`.

- [ ] **Step 2: Confirm the four artefacts by inspection**

| artefact | expected |
|---|---|
| `procurement/extract_requirements.py:26` | `_OPERATORS = (">=", "<=", "==", "in", "between")` |
| `procurement/extract_requirements.py` | `_is_range` demotes a non-two-numeric `between` to `judgement` |
| `procurement/units.py` | a `between` branch, inclusive at both ends via `math.isclose`, raising `Unconvertible` on a bad shape |
| `shared/llm/prompts/requirements_v2.txt` | teaches `between` for `5-58 deg C` and keeps `in` for `50/60 Hz`; `REQUIREMENTS_PROMPT_VERSION == "requirements_v2"` |

- [ ] **Step 3: Record the baseline**

```bash
python -m pytest -q
```

Expected: `486 passed, 3 skipped, 1 failed`. Write the exact numbers into `.superpowers/sdd/<phase>/progress.md`. If they differ, stop — the plan's later "no regression" claims are measured against this line.

- [ ] **Step 4: No commit.** Nothing changed.

---

### Task 1: A deviation binds only to a clause it can be attributed to

**Files:**
- Modify: `procurement/compliance.py`
- Modify: `procurement/pipeline.py:586` (pass the run id)
- Test: `tests/test_compliance.py`

**Interfaces:**
- Consumes: `procurement.store.events.append_event`, `events.new_run_id`, `procurement.store.models.Event`.
- Produces:
  - `evaluate(requirement, facts: list[dict], deviations: list[dict], vendor: str, now: str, clause_counts: dict[str, int] | None = None) -> ComplianceResult`
  - `evaluate_project(root: str, slug: str, now: str | None = None, run_id: str | None = None) -> list[ComplianceResult]`
- **Store invariant owned (INV-10):** every `deviation` cell in `compliance.json` names a clause carried by exactly one live requirement in `requirements.json`, and no cell reads `pass` while an unattributed deviation cites its clause — it reads `review`, naming the deviation and its source document.

INV-10 is deliberately phrased over two snapshots at once. It cannot be checked from `compliance.json` alone, because "was this deviation attributable?" is a fact about the requirement set — which is exactly why nobody noticed KERUI's single clause-`2.3` deviation binding to four requirements from four different documents.

The rule is the one commit `60f2188` already applies to amendments (`extract_mom.apply_amendments`): exactly one match binds, zero and two-or-more are both recorded unapplied with a reason in words. Read that function before writing this one — the shapes should rhyme.

The `pass` → `review` downgrade is the only place this fix *changes* a verdict rather than declining to. The reasoning is in spec §2 decision 4: forcing every ambiguous cell to `review` would discard correct arithmetic and inflate a bucket already at 81%, but a clean `pass` sitting beside an unattributed deviation is the one combination that buries a real problem. `fail` and `unanswered` already draw a reviewer's eye and are left exactly as computed.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compliance.py  (additions)

from procurement.compliance import _norm_clause          # for building count maps
from procurement.store import events


def _req_from(doc, clause="4.2.7", **kw):
    """A requirement sourced from a named document — for the collision cases,
    where two documents each print the same clause ref."""
    body = dict(clause_ref=clause, text="H2S at least 50 ppm",
                category="technical", checkability="auto",
                parameter="h2s_tolerance", operator=">=", value=50.0,
                unit="ppm", source_doc_id=doc)
    body.update(kw)
    return RequirementRecord(req_id=req_id_for(doc, clause), **body)


_UNIQUE = {_norm_clause("4.2.7"): 1}
_AMBIGUOUS = {_norm_clause("4.2.7"): 2}


# --- evaluate: binding ------------------------------------------------------

def test_a_deviation_binds_when_exactly_one_requirement_carries_the_clause():
    r = evaluate(_req(), [_fact(value=999.0)], [_dev()], "KERUI", NOW,
                 clause_counts=_UNIQUE)
    assert r.verdict == "deviation" and "we differ" in r.rationale


def test_an_omitted_count_map_binds_exactly_as_before():
    # every pre-existing caller passes five arguments; behaviour is unchanged
    assert evaluate(_req(), [_fact()], [_dev()], "KERUI", NOW).verdict == "deviation"


# --- evaluate: not binding --------------------------------------------------

def test_an_unattributed_deviation_downgrades_a_pass_to_review():
    # 999 >= 50 passes on the numbers; the unattributed deviation is exactly
    # the case where a clean pass would bury a real problem
    r = evaluate(_req(), [_fact(value=999.0)], [_dev()], "KERUI", NOW,
                 clause_counts=_AMBIGUOUS)
    assert r.verdict == "review"
    assert "we differ" in r.rationale          # the deviation is named
    assert "d8" in r.rationale                 # and so is its source document


def test_an_unattributed_deviation_leaves_a_fail_exactly_as_computed():
    r = evaluate(_req(), [_fact(value=40.0)], [_dev()], "KERUI", NOW,
                 clause_counts=_AMBIGUOUS)
    assert r.verdict == "fail"
    assert "we differ" in r.rationale
    assert r.fact_id == "f-h2s_tolerance"      # the evidence is still cited


def test_an_unattributed_deviation_leaves_an_unanswered_exactly_as_computed():
    r = evaluate(_req(), [], [_dev()], "KERUI", NOW, clause_counts=_AMBIGUOUS)
    assert r.verdict == "unanswered" and "we differ" in r.rationale


def test_an_unattributed_deviation_on_a_judgement_clause_stays_review():
    r = evaluate(_req(checkability="judgement", parameter=None, operator=None,
                      value=None, unit=None),
                 [], [_dev()], "KERUI", NOW, clause_counts=_AMBIGUOUS)
    assert r.verdict == "review"


def test_a_comply_disposition_is_never_an_unattributed_deviation():
    # only "deviate" is a deviation; a claim of compliance changes nothing
    r = evaluate(_req(), [_fact()], [_dev(disposition="comply")], "KERUI", NOW,
                 clause_counts=_AMBIGUOUS)
    assert r.verdict == "pass" and "we differ" not in r.rationale


# --- evaluate_project: the map, and the events ------------------------------

def test_two_documents_printing_one_clause_stop_the_deviation_binding(tmp_path):
    # INV-10, over the stored matrix
    root = _project(tmp_path, [_req_from("d1"), _req_from("d2")],
                    {"KERUI": [_fact(value=999.0)]})
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="KERUI", technical=[_fact(value=999.0)], deviations=[_dev()]))
    results = evaluate_project(root, "p", now=NOW)
    assert {r.verdict for r in results} == {"review"}
    assert all("d8" in r.rationale for r in results)


def test_one_document_printing_the_clause_lets_the_deviation_bind(tmp_path):
    root = _project(tmp_path, [_req_from("d1")], {"KERUI": []})
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="KERUI", technical=[_fact(value=999.0)], deviations=[_dev()]))
    [cell] = evaluate_project(root, "p", now=NOW)
    assert cell.verdict == "deviation"


def test_an_ambiguous_deviation_writes_one_event_per_run_not_per_cell(tmp_path):
    root = _project(tmp_path, [_req_from("d1"), _req_from("d2")],
                    {"KERUI": []})
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="KERUI", technical=[_fact()], deviations=[_dev()]))
    evaluate_project(root, "p", now=NOW)
    unattributed = [e for e in events.read_events(root, "p")
                    if e.action == "deviation.unattributed"]
    assert len(unattributed) == 1               # two cells, one event
    assert unattributed[0].detail["matched"] == 2
    assert "names no target document" in unattributed[0].detail["reason"]


def test_a_deviation_citing_no_requirement_is_invisible_without_its_event(tmp_path):
    root = _project(tmp_path, [_req_from("d1")], {"KERUI": []})
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="KERUI", technical=[_fact()], deviations=[_dev(clause="99.9")]))
    [cell] = evaluate_project(root, "p", now=NOW)
    assert cell.verdict == "pass"               # it contributes to no cell at all
    [event] = [e for e in events.read_events(root, "p")
               if e.action == "deviation.unattributed"]
    assert event.detail["matched"] == 0
    assert "no live requirement" in event.detail["reason"]
    assert event.detail["clause_ref"] == "99.9" and event.target == "KERUI"


def test_the_two_unattributed_reasons_are_told_apart_in_words(tmp_path):
    # "the requirements are incomplete" and "say which document you meant"
    # call for different responses from a reviewer - the same choice 60f2188
    # made when it gave Amendment an unresolved_reason
    root = _project(tmp_path, [_req_from("d1"), _req_from("d2")], {"KERUI": []})
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="KERUI", technical=[],
        deviations=[_dev(), _dev(clause="99.9") | {"deviation_id": "v-2"}]))
    evaluate_project(root, "p", now=NOW)
    reasons = {e.detail["matched"]: e.detail["reason"]
               for e in events.read_events(root, "p")
               if e.action == "deviation.unattributed"}
    assert set(reasons) == {0, 2}
    assert reasons[0] != reasons[2]


def test_the_count_map_is_recomputed_every_call_never_cached(tmp_path):
    # the defect this whole shape exists to prevent: a map cached on first
    # computation would keep the clause ambiguous after the collider is gone
    root = _project(tmp_path, [_req_from("d1"), _req_from("d2")], {"KERUI": []})
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="KERUI", technical=[_fact()], deviations=[_dev()]))
    assert {r.verdict for r in evaluate_project(root, "p", now=NOW)} == {"review"}

    snapshots.save_requirements(root, "p",
                                RequirementSet(requirements=[_req_from("d1")]))
    [cell] = evaluate_project(root, "p", now=NOW)
    assert cell.verdict == "deviation"


def test_a_withdrawn_requirement_does_not_make_a_clause_ambiguous(tmp_path):
    # the map counts *live* requirements: a withdrawn twin produces no cell,
    # so it must not block the deviation from binding either
    root = _project(tmp_path,
                    [_req_from("d1"), _req_from("d2", withdrawn=True)],
                    {"KERUI": []})
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="KERUI", technical=[_fact()], deviations=[_dev()]))
    [cell] = evaluate_project(root, "p", now=NOW)
    assert cell.verdict == "deviation"
```

> Note on `_project`: the existing helper writes `VendorFacts` with `technical`
> only, so each of the tests above re-saves facts to attach `deviations`. If
> that reads badly during execution, extend `_project` with a
> `deviations_by_vendor` argument instead — but do not change its existing
> signature, which twelve older tests depend on.

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_compliance.py -q -k "unattributed or binds or count_map or withdrawn_requirement_does_not"
```

Expected: FAIL — `evaluate() got an unexpected keyword argument 'clause_counts'`, and no `deviation.unattributed` events exist.

- [ ] **Step 3: Write minimal implementation**

Load-bearing: the count is `== 1`, not `>= 1`; the map is built inside `evaluate_project` on every call from `live`; only `pass` is rewritten; the event loop is separate from the cell loop. Illustrative: the note wording, the detail keys, `Counter` vs a dict comprehension.

```python
# procurement/compliance.py
from collections import Counter

from procurement.store import events
from procurement.store.models import ComplianceResult, Event, RequirementSet


def evaluate(requirement, facts: list[dict], deviations: list[dict],
             vendor: str, now: str,
             clause_counts: dict[str, int] | None = None) -> ComplianceResult:
    """One cell.

    `clause_counts` maps a normalised clause ref to the number of *live*
    requirements carrying it. A deviation binds only where that count is
    exactly 1 — clause numbering is unique within one document and nothing on
    a DeviationRecord names a target document, so binding on a shared ref is a
    coin flip dressed as a decision. `None` means "unscoped" and preserves the
    pre-fix behaviour for direct callers; `evaluate_project` always passes it.
    """
    def result(verdict, rationale, fact=None):
        ...                                     # unchanged

    want_clause = _norm_clause(requirement.clause_ref)
    deviated = next((d for d in deviations
                     if _norm_clause(d.get("clause_ref")) == want_clause
                     and d.get("disposition") == "deviate"), None)
    matched = None if clause_counts is None else clause_counts.get(want_clause, 0)
    if deviated is not None and (matched is None or matched == 1):
        # The vendor has said in writing that it does not comply, about a
        # clause it provably names. A `comply` disposition earns nothing: an
        # assertion of compliance is not evidence of it.
        return result("deviation",
                      f"vendor declared a deviation: {deviated.get('statement')}")

    unattributed = deviated if deviated is not None else None

    ...                                         # the existing judgement / fact /
                                                # compare tail, unchanged, but
                                                # assigned to `outcome` instead
                                                # of returned directly

    if unattributed is None:
        return outcome
    note = (f" | an unattributed deviation cites clause "
            f"{requirement.clause_ref!r}: {unattributed.get('statement')!r} "
            f"(document {unattributed.get('doc_id')}); {matched} live "
            f"requirements carry this clause, and the deviation names none "
            f"of them")
    # Only `pass` is rewritten. `fail` and `unanswered` already draw a
    # reviewer's eye; a clean `pass` beside an unattributed deviation is the
    # one combination that buries a real problem.
    verdict = "review" if outcome.verdict == "pass" else outcome.verdict
    return outcome.model_copy(update={"verdict": verdict,
                                      "rationale": outcome.rationale + note})
```

Restructuring the tail into `outcome` is the only invasive part. Take the three existing `return result(...)` statements in the judgement / missing-fact / compare branches and turn them into assignments followed by a single exit — do not duplicate the branch logic.

```python
def evaluate_project(root: str, slug: str, now: str | None = None,
                     run_id: str | None = None) -> list[ComplianceResult]:
    """Recompute and store the whole requirement x vendor matrix."""
    now = now or _now()
    run_id = run_id or events.new_run_id()
    reqset = snapshots.load_requirements(root, slug)
    live = [r for r in reqset.requirements if not r.withdrawn]
    # Built here, on every call, from the live requirements. Never stored and
    # never cached: a cached map keeps a clause ambiguous after the second
    # document carrying it is gone, which is matrix row 22.
    clause_counts = Counter(_norm_clause(r.clause_ref) for r in live)
    vendors = load_project(root, slug).vendors

    out: list[ComplianceResult] = []
    for vendor in vendors:
        stored = snapshots.load_facts(root, slug, vendor)
        facts = list(stored.technical) if stored else []
        deviations = list(stored.deviations) if stored else []
        for requirement in live:
            out.append(evaluate(requirement, facts, deviations, vendor, now,
                                clause_counts=clause_counts))
        _report_unattributed(root, slug, run_id, vendor, deviations,
                             clause_counts, now)
    snapshots.save_compliance(root, slug, out)
    return out


def _report_unattributed(root, slug, run_id, vendor, deviations,
                         clause_counts, now) -> None:
    """One event per deviation that could not bind, per run.

    The two reasons are told apart in words, because they call for different
    responses: "no live requirement states this clause" usually means an
    extraction gap or a vendor citing a number that does not exist, and is
    otherwise entirely invisible — the deviation contributes to no cell at
    all. "Several requirements state it" is visible per-cell in the
    rationale; the event records the ambiguity once rather than once per
    affected cell.
    """
    for d in deviations:
        if d.get("disposition") != "deviate":
            continue
        matched = clause_counts.get(_norm_clause(d.get("clause_ref")), 0)
        if matched == 1:
            continue
        reason = ("no live requirement states this clause" if matched == 0 else
                  f"{matched} live requirements state this clause; the "
                  "deviation names no target document")
        events.append_event(root, slug, Event(
            at=now, run_id=run_id, actor="pipeline",
            action="deviation.unattributed", target=vendor,
            detail={"deviation_id": d.get("deviation_id"),
                    "clause_ref": d.get("clause_ref"),
                    "doc_id": d.get("doc_id"),
                    "matched": matched, "reason": reason}))
```

And one line in `pipeline.run_ingestion`, at the `evaluate_project` call:

```python
        results = compliance.evaluate_project(root, slug, run_id=run_id)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_compliance.py tests/test_phase3_lifecycle.py tests/test_store_events.py -v
```

Expected: PASS. `test_phase3_lifecycle.py` is in the list because `_full_project` gives KERUI a deviation form; if any of rows 1–20 turns red, the fix has changed a verdict it should not have.

- [ ] **Step 5: Commit**

```bash
git add procurement/compliance.py procurement/pipeline.py tests/test_compliance.py && git commit -m "fix: a deviation binds only to a clause exactly one requirement carries"
```

---

### Task 2: `duplicate_group` on the record, and a stable group id

**Files:**
- Modify: `procurement/store/models.py`
- Test: `tests/test_store_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `DocumentRecord.duplicate_group: str | None = None`
  - `duplicate_group_id(doc_ids: list[str]) -> str` — `"dg-"` + 10 hex chars of the sha256 of the sorted, `|`-joined ids.
- **Store invariant owned: none.** A field with no writer and a pure hash function are both checkable without loading a snapshot, which Rule 1 excludes. Rule 1 also assigns a shared invariant to the task that writes the store last — that is Task 3, which owns INV-11.

The id is hashed from the sorted member ids rather than allocated, so an unchanged set of copies yields an unchanged group id across runs — which is what lets Spec 2's "pick the authoritative copy" control persist a choice against a group. A member joining or leaving deliberately changes the id: it is a different group, and a stable id across a changed membership would silently re-target a stored decision.

`duplicate_group` defaults to `None`, so existing snapshots load unchanged. There is no migration.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store_models.py  (additions)

from procurement.store.models import DocumentRecord, duplicate_group_id


def test_a_group_id_is_stable_across_member_ordering():
    assert duplicate_group_id(["a", "b", "c"]) == duplicate_group_id(["c", "a", "b"])


def test_a_group_id_changes_when_the_membership_changes():
    # a different set of copies is a different group, and must not inherit a
    # decision a reviewer made about the old one
    assert duplicate_group_id(["a", "b"]) != duplicate_group_id(["a", "b", "c"])


def test_a_group_id_is_recognisable_and_short():
    got = duplicate_group_id(["a", "b"])
    assert got.startswith("dg-") and len(got) == 13


def test_a_document_carries_no_duplicate_group_by_default():
    doc = DocumentRecord(doc_id="d1", path="requirements/mr.pdf",
                         content_sha256="0" * 64)
    assert doc.duplicate_group is None


def test_an_existing_snapshot_record_loads_without_the_field():
    # no migration: every stored record predates the field
    doc = DocumentRecord.model_validate(
        {"doc_id": "d1", "path": "requirements/mr.pdf", "content_sha256": "0" * 64})
    assert doc.duplicate_group is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_store_models.py -q -k "group_id or duplicate_group"
```

Expected: FAIL — `ImportError: cannot import name 'duplicate_group_id'`.

- [ ] **Step 3: Write minimal implementation**

Load-bearing: sorting before hashing, and the prefix that makes the id recognisable in a snapshot. Illustrative: the separator and the 10-character truncation, which match the other id helpers in this file.

```python
# procurement/store/models.py

class DocumentRecord(BaseModel):
    ...
    vocabulary_sha: str | None = None
    # Stable id of the near-duplicate group this RFQ document belongs to, or
    # None. Observational only: membership never changes which requirements
    # are stored, and no member is superseded, pruned or merged. Acting on a
    # group - picking the authoritative copy - is a Spec 2 control.
    duplicate_group: str | None = None


def duplicate_group_id(doc_ids: list[str]) -> str:
    """Stable for a set of near-duplicate documents: the same copies yield the
    same id across runs, so a decision recorded against a group survives a
    re-run. A member joining or leaving yields a *new* id on purpose — it is a
    different group, and silently re-targeting a stored decision at it would be
    the same defect `deviation_id_for` avoids by keying on both fields."""
    key = "|".join(sorted(doc_ids))
    return "dg-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_store_models.py tests/test_store_snapshots.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add procurement/store/models.py tests/test_store_models.py && git commit -m "feat: a document records the near-duplicate group it belongs to"
```

---

### Task 3: Detect near-duplicate RFQ documents, and change nothing else

**Files:**
- Modify: `procurement/pipeline.py` (`_run_rfq_pass`, plus two new module-level functions)
- Test: `tests/test_pipeline_rfq.py`

**Interfaces:**
- Consumes: Task 2's `duplicate_group_id` and `DocumentRecord.duplicate_group`; `compliance._norm_clause`.
- Produces:
  - `_clause_sets(docs: list[DocumentRecord], requirements: list[RequirementRecord]) -> dict[str, set[str]]`
  - `near_duplicate_groups(clause_sets: dict[str, set[str]]) -> list[dict]` — each `{"group_id": str, "members": list[str], "overlaps": list[tuple[str, str, float]]}`, members sorted, groups of one dropped.
  - Module constants `_DUPLICATE_OVERLAP = 0.9`, `_DUPLICATE_MIN_CLAUSES = 5`.
  - Event `documents.near_duplicates`, one per group, `target` = the group id.
- **Store invariant owned (INV-11):** duplicate detection is observational — `requirements.json` is byte-identical whether or not it ran, and every member of a `duplicate_group` in `documents.json` is live and un-pruned.

The signal is the **overlap coefficient**, `|A ∩ B| / min(|A|, |B|)`, not Jaccard. The live corpus is 115 / 114 / 114 clauses across three copies of one MR: Jaccard punishes the size gap, overlap does not, and one document nearly containing another is precisely the shape we want to catch.

Grouping is **transitive** — three copies must be one group, not three pairs, or a reviewer picking an authoritative copy has to pick three times.

Nothing is superseded, pruned or merged. `requirements.json` is written *before* this block runs and is not read back into it except to build clause sets. That ordering is the cheapest possible proof of INV-11, and it is the property most likely to be broken by a later "improvement" that turns grouping into pruning.

**This block is not wrapped in a try/except** — the same choice `evaluate_project`'s call site makes. It is pure Python over already-stored data, so a failure there is a bug, and swallowing it would hide precisely the class of defect this plan removes. If it raises, the run raises; `snapshots.transaction` then withholds the generation bump.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_rfq.py  (additions)

import pytest

from procurement.pipeline import near_duplicate_groups
from procurement.store.models import duplicate_group_id


class ManyClauseClient(RfqClient):
    """A spec stub that states enough clauses to clear the 5-clause floor, and
    lets a test drop or rename some of them per document."""

    def __init__(self, clauses_by_name=None, **kw):
        super().__init__(**kw)
        # {substring of the document's text: [clause_ref, ...]}; the default
        # applies to every document that matches nothing
        self.clauses_by_name = clauses_by_name or {}
        self.default_clauses = ["2.1", "2.2", "2.3", "2.4", "2.5", "2.6"]

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        if "requirements" not in output_schema.model_fields:
            return super().classify_structure(prompt, output_schema,
                                              context_text, images)
        self.calls.append("requirements")
        if "requirements" in self._fail_on:      # the base stub's knob, honoured
            raise RuntimeError("requirements provider unavailable")
        clauses = self.default_clauses
        for marker, refs in self.clauses_by_name.items():
            if marker in context_text:
                clauses = refs
        return {"requirements": [
            {"clause_ref": ref, "text": f"clause {ref} states something",
             "category": "technical", "checkability": "judgement"}
            for ref in clauses]}


# --- the pure grouping function --------------------------------------------

def test_two_documents_sharing_almost_every_clause_form_a_group():
    sets = {"a": {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10"},
            "b": {"1", "2", "3", "4", "5", "6", "7", "8", "9"}}
    [group] = near_duplicate_groups(sets)
    assert group["members"] == ["a", "b"]
    assert group["group_id"] == duplicate_group_id(["a", "b"])


def test_the_overlap_is_a_coefficient_not_a_jaccard_index():
    # 'b' is wholly contained in a much larger 'a': overlap 1.0, Jaccard 0.5.
    # Jaccard would miss the 115-vs-114 case this exists for.
    big = {str(i) for i in range(20)}
    sets = {"a": big, "b": {str(i) for i in range(10)}}
    assert len(near_duplicate_groups(sets)) == 1


def test_grouping_is_transitive_so_three_copies_are_one_group():
    base = {str(i) for i in range(20)}
    sets = {"a": base, "b": base - {"0"}, "c": base - {"1"}}
    [group] = near_duplicate_groups(sets)
    assert group["members"] == ["a", "b", "c"]
    assert group["group_id"] == duplicate_group_id(["a", "b", "c"])


def test_two_thin_documents_sharing_a_couple_of_refs_never_group():
    # the 5-clause floor: "1.1" and "1.2" appear in half the documents alive
    sets = {"a": {"1", "2", "3"}, "b": {"1", "2", "3"}}
    assert near_duplicate_groups(sets) == []


def test_documents_below_the_overlap_threshold_stay_separate():
    sets = {"a": {str(i) for i in range(10)},
            "b": {str(i) for i in range(5, 15)}}       # overlap 0.5
    assert near_duplicate_groups(sets) == []


def test_a_document_with_no_clauses_never_groups():
    sets = {"a": {str(i) for i in range(10)},
            "b": {str(i) for i in range(10)}, "c": set()}
    [group] = near_duplicate_groups(sets)
    assert "c" not in group["members"]


def test_each_group_reports_its_pairwise_overlaps():
    base = {str(i) for i in range(20)}
    [group] = near_duplicate_groups({"a": base, "b": base - {"0"}})
    [(a, b, overlap)] = group["overlaps"]
    assert (a, b) == ("a", "b") and overlap == pytest.approx(1.0)


# --- inside the RFQ pass ----------------------------------------------------

_MR_COPY = "Copy of ADN-AEC-ME-SPC-026 MR Gas Genset.xlsx.txt"
_MR_COPY2 = "ADN-AEC-ME-SPC-026 MR client only.txt"


def test_three_copies_of_one_mr_report_as_one_group(tmp_path):
    root = _rfq(tmp_path, {_MR: "copy one", _MR_COPY: "copy two",
                           _MR_COPY2: "copy three"})
    run_ingestion(root, "p", ManyClauseClient())
    docs = snapshots.load_documents(root, "p")
    groups = {d.duplicate_group for d in docs}
    assert len(groups) == 1 and None not in groups
    assert all(d.extraction_status == "ok" for d in docs)


def test_a_group_is_reported_as_an_event_with_its_members_and_overlaps(tmp_path):
    root = _rfq(tmp_path, {_MR: "copy one", _MR_COPY: "copy two"})
    run_ingestion(root, "p", ManyClauseClient())
    [event] = [e for e in events.read_events(root, "p")
               if e.action == "documents.near_duplicates"]
    assert len(event.detail["members"]) == 2
    assert event.target.startswith("dg-")
    assert {d.duplicate_group for d in snapshots.load_documents(root, "p")} == {
        event.target}
    assert event.detail["overlaps"][0]["overlap"] == 1.0


def test_a_document_stating_different_clauses_is_not_grouped(tmp_path):
    root = _rfq(tmp_path, {_MR: "the main MR",
                           "99 Attachment Scope of Work.txt": "the scope"})
    run_ingestion(root, "p", ManyClauseClient(clauses_by_name={
        "the scope": ["8.1", "8.2", "8.3", "8.4", "8.5", "8.6"]}))
    assert all(d.duplicate_group is None
               for d in snapshots.load_documents(root, "p"))


def test_a_failed_extraction_never_groups_even_though_it_stays_live(tmp_path):
    # "failed" counts as live everywhere else in this pipeline, deliberately.
    # Grouping is the one place it must not, because a failed document's
    # stored clauses describe an earlier run, not this corpus.
    root = _rfq(tmp_path, {_MR: "copy one", _MR_COPY: "copy two"})
    run_ingestion(root, "p", ManyClauseClient())
    assert all(d.duplicate_group for d in snapshots.load_documents(root, "p"))

    (tmp_path / "p" / "requirements" / _MR_COPY).write_text("copy two, edited",
                                                            encoding="utf-8")
    run_ingestion(root, "p", ManyClauseClient(fail_on={"requirements"}))
    docs = {d.path.split("/")[-1]: d for d in snapshots.load_documents(root, "p")}
    assert docs[_MR_COPY].extraction_status == "failed"
    assert docs[_MR_COPY].duplicate_group is None
    assert docs[_MR].duplicate_group is None        # a group of one is no group


def test_detection_stores_no_requirement_and_prunes_none(tmp_path):
    # INV-11, over the stored snapshots
    root = _rfq(tmp_path, {_MR: "copy one", _MR_COPY: "copy two"})
    run_ingestion(root, "p", ManyClauseClient())
    reqs = snapshots.load_requirements(root, "p").requirements
    sources = {r.source_doc_id for r in reqs}
    docs = snapshots.load_documents(root, "p")
    assert len(reqs) == 12                       # 6 clauses from each copy
    assert sources == {d.doc_id for d in docs}   # nothing was pruned
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_pipeline_rfq.py -q -k "group or duplicate or overlap or transitive or thin or copies"
```

Expected: FAIL — `ImportError: cannot import name 'near_duplicate_groups'`.

- [ ] **Step 3: Write minimal implementation**

Load-bearing: the overlap coefficient's `min()` denominator; the `extraction_status == "ok"` eligibility; the exclusion of synthesized `#N` refs; transitivity; that this block writes to `DocumentRecord` and to nothing else, after `save_requirements` has already run. Illustrative: the union-find spelling, the rounding, the detail keys.

```python
# procurement/pipeline.py
from procurement.compliance import _norm_clause
from procurement.store.models import duplicate_group_id

# Overlap coefficient |A n B| / min(|A|, |B|), not Jaccard: the live corpus is
# 115/114/114 clauses across three copies of one MR, and Jaccard punishes the
# size gap that overlap correctly ignores. Constants, not project config: they
# are testable that way and no project has yet needed different ones.
_DUPLICATE_OVERLAP = 0.9
# A floor, so two thin documents sharing "1.1" and "1.2" never group.
_DUPLICATE_MIN_CLAUSES = 5


def _clause_sets(docs, requirements) -> dict[str, set[str]]:
    """Normalised clause refs per *eligible* spec document.

    Eligible means routed to the requirements extractor and `extraction_status
    == "ok"`. "failed" counts as live everywhere else in this pipeline - so a
    provider outage never deletes good stored data - and deliberately does not
    here: a failed document's stored clauses describe an earlier run, so
    grouping on them would report a duplicate nobody can see in this corpus.

    A synthesized ref (`#7`, written when the model gave no clause number) is
    excluded. Two documents whose refs are all synthesized would overlap
    perfectly on position alone while sharing nothing.
    """
    eligible = {d.doc_id for d in docs
                if _rfq_route(d) == "spec" and d.extraction_status == "ok"}
    out: dict[str, set[str]] = {doc_id: set() for doc_id in eligible}
    for req in requirements:
        if req.source_doc_id in out and not req.clause_ref.startswith("#"):
            out[req.source_doc_id].add(_norm_clause(req.clause_ref))
    return out


def near_duplicate_groups(clause_sets: dict[str, set[str]]) -> list[dict]:
    """Transitively-grouped near-duplicate documents.

    Transitive on purpose: three copies of one MR must be one group, or a
    reviewer picking the authoritative copy has to pick three times.
    """
    ids = sorted(d for d, refs in clause_sets.items()
                 if len(refs) >= _DUPLICATE_MIN_CLAUSES)
    parent = {d: d for d in ids}

    def find(d):
        while parent[d] != d:
            parent[d] = parent[parent[d]]
            d = parent[d]
        return d

    pairs = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            sa, sb = clause_sets[a], clause_sets[b]
            overlap = len(sa & sb) / min(len(sa), len(sb))
            if overlap < _DUPLICATE_OVERLAP:
                continue
            pairs.append((a, b, round(overlap, 4)))
            parent[find(a)] = find(b)

    by_root: dict[str, list[str]] = {}
    for d in ids:
        by_root.setdefault(find(d), []).append(d)

    out = []
    for members in by_root.values():
        if len(members) < 2:
            continue            # a group of one is not a group
        members = sorted(members)
        out.append({"group_id": duplicate_group_id(members),
                    "members": members,
                    "overlaps": [p for p in pairs
                                 if p[0] in members and p[1] in members]})
    return sorted(out, key=lambda g: g["members"])
```

And the block at the very end of `_run_rfq_pass`, after `save_requirements` and the override-conflict events, immediately before `return docs, extracted, failed`:

```python
    # Observational only (INV-11). requirements.json is already written above
    # and is not touched here; no document is superseded, pruned or merged.
    # Requirements are read back from the store rather than taken from
    # `applied`, for the same reason run_ingestion reads the vocabulary back:
    # an override that edits a clause ref must be part of what detection sees.
    stored = snapshots.load_requirements(root, slug).requirements
    by_id = {d.doc_id: d for d in docs}
    for group in near_duplicate_groups(_clause_sets(docs, stored)):
        for doc_id in group["members"]:
            by_id[doc_id].duplicate_group = group["group_id"]
        events.append_event(root, slug, Event(
            at=_now(), run_id=run_id, actor="pipeline",
            action="documents.near_duplicates", target=group["group_id"],
            detail={"members": group["members"],
                    "paths": [by_id[d].path for d in group["members"]],
                    "overlaps": [{"a": a, "b": b, "overlap": o}
                                 for a, b, o in group["overlaps"]]}))
```

`duplicate_group` needs no explicit reset for non-members: `inventory_rfq_documents` builds fresh `DocumentRecord`s every run and the field defaults to `None`. Confirm that is still true before relying on it — if a future change starts carrying the field forward from `prior_docs`, a document that leaves a group would keep its old id forever.

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_pipeline_rfq.py tests/test_pipeline_incremental.py tests/test_pipeline_lifecycle.py tests/test_store_snapshots.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add procurement/pipeline.py tests/test_pipeline_rfq.py && git commit -m "feat: report near-duplicate RFQ documents as a group, and resolve nothing"
```

---

### Task 4: Integration — the two-run mutation matrix

**Files:**
- Modify: `tests/test_phase3_lifecycle.py` (`MatrixClient`, plus rows 21–27)
- Modify: none in production code. If a row needs a production change to pass, the defect belongs to Task 1 or Task 3 — fix it there.

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: no new production code. This task is the store-level assertion that the previous three add up across runs.
- **Store invariant owned: none new.** INV-10 is owned by Task 1 and INV-11 by Task 3; Rule 1 forbids claiming either twice. This task is where both get their defending rows.

Every row asserts over a **loaded snapshot after run 2** (or run 3 where the row says so). A row that can be satisfied by a single-run assertion is not testing what it claims — that is the phase-2 defect class this file exists to close.

`MatrixClient` needs one new knob, because the base `RfqClient` returns an empty deviation list:

```python
class MatrixClient(VocabClient):
    def __init__(self, **kw):
        super().__init__(**kw)
        ...
        self.deviation_clause = None    # None -> the base stub's empty list

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        fields = output_schema.model_fields
        if "deviations" in fields and self.deviation_clause is not None:
            self.calls.append("deviations")
            return {"deviations": [
                {"clause_ref": self.deviation_clause,
                 "statement": "we differ on ambient temperature",
                 "disposition": "deviate"}]}
        ...                                     # the existing dispatch
```

- [ ] **Step 1: Write the failing test**

Seven rows, one test each, appended to `tests/test_phase3_lifecycle.py`.

```python
# tests/test_phase3_lifecycle.py  (additions)

def _cell(root, clause, vendor="KERUI"):
    req = next(r for r in _reqs(root).requirements if r.clause_ref == clause)
    return next(c for c in snapshots.load_compliance(root, "p")
                if c.req_id == req.req_id and c.vendor == vendor)


def _unattributed(root):
    return [e for e in events.read_events(root, "p")
            if e.action == "deviation.unattributed"]


_SPEC_B = "ADN-AEC-ME-SPC-027 MR Gas Genset B.txt"


def test_row21_a_colliding_second_spec_downgrades_a_deviation_to_review(tmp_path):
    # INV-10: clause numbers are unique only within a document. Once a second
    # spec prints 4.2.7, the deviation citing it can no longer be attributed,
    # and the cell that would read `pass` on the numbers must read `review` -
    # a clean pass beside an unattributed deviation buries a real problem.
    root = _full_project(tmp_path)
    os.remove(os.path.join(root, "p", "requirements", _MOM))
    first = MatrixClient()
    first.deviation_clause = "4.2.7"
    run_ingestion(root, "p", first)
    assert _cell(root, "4.2.7").verdict == "deviation"

    _write_rfq(tmp_path, _SPEC_B)
    second = MatrixClient()
    second.deviation_clause = "4.2.7"
    run_ingestion(root, "p", second)

    cells = [c for c in snapshots.load_compliance(root, "p")
             if c.verdict == "deviation"]
    assert cells == [], "a deviation survived a clause two documents carry"
    downgraded = _cell(root, "4.2.7")
    assert downgraded.verdict == "review"
    assert "we differ" in downgraded.rationale
    assert "unattributed" in downgraded.rationale


def test_row22_removing_the_collider_lets_the_deviation_bind_again(tmp_path):
    # INV-10, and the row that fails if the count map is ever cached: run 3
    # must find the clause unique again and bind. Spec section 9, mutation 1.
    root = _full_project(tmp_path)
    os.remove(os.path.join(root, "p", "requirements", _MOM))
    _write_rfq(tmp_path, _SPEC_B)
    client = MatrixClient()
    client.deviation_clause = "4.2.7"
    run_ingestion(root, "p", client)
    assert _cell(root, "4.2.7").verdict == "review"

    os.remove(os.path.join(root, "p", "requirements", _SPEC_B))
    again = MatrixClient()
    again.deviation_clause = "4.2.7"
    run_ingestion(root, "p", again)

    assert _cell(root, "4.2.7").verdict == "deviation"
    assert _cell(root, "4.2.7").evaluated_at >= _last_run_started(root)


def test_row23_an_unattributed_deviation_never_rewrites_a_fail(tmp_path):
    # INV-10: only `pass` is downgraded. `fail` already draws a reviewer's eye
    # and must survive untouched, with the deviation named beside it.
    root = _full_project(tmp_path)
    os.remove(os.path.join(root, "p", "requirements", _MOM))
    _write_rfq(tmp_path, _SPEC_B)
    client = MatrixClient()
    client.deviation_clause = "4.2.7"
    client.fact_value = 10                       # 10 ppm against ">= 50 ppm"
    run_ingestion(root, "p", client)

    cell = _cell(root, "4.2.7")
    assert cell.verdict == "fail"
    assert "we differ" in cell.rationale and cell.fact_id is not None


def test_row24_a_deviation_citing_no_requirement_is_only_visible_as_an_event(tmp_path):
    # INV-10: this deviation contributes to no cell at all, so without the
    # event it is entirely invisible - usually an extraction gap, or a vendor
    # citing a clause number that does not exist.
    root = _full_project(tmp_path)
    run_ingestion(root, "p", MatrixClient())
    assert _unattributed(root) == []

    second = MatrixClient()
    second.deviation_clause = "77.7"
    run_ingestion(root, "p", second)

    [event] = _unattributed(root)
    assert event.detail["matched"] == 0 and event.target == "KERUI"
    assert "no live requirement" in event.detail["reason"]
    assert all(c.verdict != "deviation"
               for c in snapshots.load_compliance(root, "p"))


def test_row25_detection_leaves_requirements_json_byte_identical(tmp_path, monkeypatch):
    # INV-11: the property that proves detection is observational, and the one
    # most likely to be broken by a later change that turns grouping into
    # pruning.
    root = _duplicate_project(tmp_path)
    monkeypatch.setattr(pipeline, "_DUPLICATE_OVERLAP", 2.0)   # unreachable
    run_ingestion(root, "p", MatrixClient())
    monkeypatch.undo()
    path = layout.requirements_path(root, "p")
    without = open(path, "rb").read()
    docs_without = {d.doc_id: d.duplicate_group
                    for d in snapshots.load_documents(root, "p")}
    assert set(docs_without.values()) == {None}

    run_ingestion(root, "p", MatrixClient())
    assert open(path, "rb").read() == without
    docs_with = {d.doc_id: d.duplicate_group
                 for d in snapshots.load_documents(root, "p")}
    assert docs_with != docs_without
    assert set(docs_with) == set(docs_without)   # the same documents, both runs


def test_row26_a_third_copy_joins_the_one_group_and_nothing_is_pruned(tmp_path):
    # INV-11 and INV-4: transitive grouping, and every member still live.
    root = _duplicate_project(tmp_path)
    run_ingestion(root, "p", MatrixClient())
    before = {d.duplicate_group for d in snapshots.load_documents(root, "p")}
    assert len(before) == 1 and None not in before

    _write_rfq(tmp_path, "ADN-AEC-ME-SPC-026 MR client only.txt", _MANY_CLAUSES)
    run_ingestion(root, "p", MatrixClient())

    docs = snapshots.load_documents(root, "p")
    groups = {d.duplicate_group for d in docs}
    assert len(groups) == 1 and None not in groups, "three copies, three pairs"
    assert groups != before, "the group id must move when membership changes"
    assert all(d.extraction_status == "ok" for d in docs)
    # every member is still the source of stored requirements: grouping is not
    # pruning, and INV-4 is what a later "improvement" would break here
    members = {d.doc_id for d in docs if d.duplicate_group}
    assert len(members) == 3
    assert members <= {r.source_doc_id for r in _reqs(root).requirements}


def test_row27_a_member_that_starts_failing_leaves_the_group(tmp_path):
    # INV-11: "failed" is live everywhere else in this pipeline, deliberately.
    # Grouping is the documented exception, and this is where that asymmetry
    # is asserted rather than assumed.
    root = _duplicate_project(tmp_path)
    run_ingestion(root, "p", MatrixClient())
    kept = _doc(root, _MR).duplicate_group
    assert kept is not None

    edited = os.path.join(root, "p", "requirements", _MR_COPY)
    open(edited, "a", encoding="utf-8").write("\nedited\n")
    run_ingestion(root, "p", MatrixClient(fail_on={"requirements"}))

    failed = _doc(root, _MR_COPY)
    assert failed.extraction_status == "failed" and failed.notes
    assert failed.duplicate_group is None
    assert _doc(root, _MR).duplicate_group is None    # a group of one is none
    # and INV-4: the failed document's earlier requirements are still stored
    assert failed.doc_id in {r.source_doc_id for r in _reqs(root).requirements}
```

Two fixtures these rows need, alongside `_full_project`:

```python
_MR_COPY = "Copy of ADN-AEC-ME-SPC-026 MR Gas Genset.xlsx.txt"
# enough distinct clauses to clear _DUPLICATE_MIN_CLAUSES
_MANY_CLAUSES = "\n".join(f"{n} clause {n} states something"
                          for n in ("2.1", "2.2", "2.3", "2.4", "2.5", "2.6"))


def _duplicate_project(tmp_path):
    """Two near-identical MR copies, each yielding the same six clause refs."""
    root = _project(tmp_path)
    _write_rfq(tmp_path, _MR, _MANY_CLAUSES)
    _write_rfq(tmp_path, _MR_COPY, _MANY_CLAUSES + "\n(export)\n")
    return root
```

`MatrixClient.requirements_response` already exists and returns a fixed dict for every spec document — set it to the six clauses above so both copies produce the same refs. Check that `resolve_supersession` does not read `"Copy of …"` as a revision of `_MR`; if it does, rename the fixture rather than weakening the lineage rules.

Row 25 needs one import this file does not yet have: `from procurement.store import events, layout, snapshots`.

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_phase3_lifecycle.py -q -k "row21 or row22 or row23 or row24 or row25 or row26 or row27"
```

Expected: FAIL on every row before Tasks 1–3 land; after them, only whichever rows expose a real gap.

- [ ] **Step 3: Write minimal implementation**

None. If a row is still red after Tasks 1–3, the defect is in those tasks and belongs there — do not add a special case here.

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_phase3_lifecycle.py -v
```

Expected: PASS, rows 1–27.

- [ ] **Step 4b: The two-run mutation matrix**

| # | mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|---|
| 21 | a second spec document arrives printing a clause ref the first already prints | INV-10 | the cell that read `deviation` in run 1 reads `review` after run 2, its rationale names the deviation and its source document, and no `deviation` cell survives |
| 22 | the colliding second spec is deleted before run 3 | INV-10 | the clause is unique again and the cell reads `deviation`, with `evaluated_at` at or after run 3's `run.started` — a cached count map fails this and nothing else |
| 23 | the vendor's stated value drops below the bound, beside the same unattributed deviation | INV-10 | the cell reads `fail`, still citing its `fact_id`, with the deviation named in the rationale — `fail` is never rewritten |
| 24 | the vendor's deviation form starts citing a clause no live requirement states | INV-10 | one `deviation.unattributed` event with `matched == 0` and the "no live requirement" reason; no cell changes verdict |
| 25 | duplicate detection is disabled by raising `_DUPLICATE_OVERLAP` out of reach, then re-enabled | INV-11 | `requirements.json` is byte-identical across both runs while `documents.json` differs only in `duplicate_group`, over the same set of `doc_id`s |
| 26 | a third near-identical copy of the MR is added | INV-11, INV-4 | all three documents carry one `duplicate_group`, the id differs from run 1's, every member is `ok`, and every document is still the source of stored requirements |
| 27 | one member of the group is edited and its re-extraction fails | INV-11, INV-4 | the failed document's `duplicate_group` is `None` and the survivor's is too (a group of one is no group), and the failed document's earlier requirements are still stored |

Rows 1–20 in this file are the nine required baseline rows plus phase 3's own. This plan introduces no new accumulating collection — `duplicate_group` is a field on a collection replaced wholesale every run, and `compliance.json` is already recomputed wholesale — so those rows are inherited rather than re-derived. Step 4c requires them green.

- [ ] **Step 4c: Verify the matrix is real, not decorative**

Reintroduce each defect one at a time, confirm the intended row fails **and that nothing else does**, then delete the throwaway script. A row that still passes with its defect reinstated is not testing what it claims.

| row | defect to reinstate |
|---|---|
| 21 | bind a deviation whenever its clause matches, ignoring `clause_counts` |
| 22 | cache the count map on a module global after the first `evaluate_project` call |
| 23 | force every cell beside an unattributed deviation to `review`, not only `pass` |
| 24 | emit `deviation.unattributed` from `evaluate` instead of `_report_unattributed`, so a zero-match deviation is never seen |
| 25 | prune all but the largest member of each duplicate group in `_run_rfq_pass` |
| 26 | group pairwise instead of transitively (drop the union-find) |
| 27 | build clause sets from stored requirements alone, ignoring `extraction_status` |

Then require the inherited rows green:

```bash
python -m pytest tests/test_phase3_lifecycle.py -v
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_phase3_lifecycle.py && git commit -m "test: two-run rows for deviation scoping and duplicate detection"
```

---

### Task 5: Live corpus verification

**Files:**
- Create: `docs/superpowers/plans/notes/2026-07-31-correctness-live-run.md` (the recorded result)
- Modify: none.

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: the recorded verdict split, for spec §11's done criteria and as the baseline Spec 2's screen is designed against.
- **Store invariant owned: none.** This task writes no production code.

This is the only real check on the three defects the spec was written from. **It needs `ANTHROPIC_API_KEY` and costs LLM calls — it is not part of `python -m pytest` and no test may depend on it.** Skip it and say so plainly if no key is available; do not report the plan complete without it.

`KERUI.zip` and `MKON.zip` are currently untracked in `processed-data/02-vendor-bids/`, so MKON is new since the phase 3 run and the cell count will not match the spec's 515.

- [ ] **Step 1: Run the corpus against a reused store**

Follow the procedure the 2026-07-31 run used: `LLM_MAX_TOKENS=32000`, `processed-data/01-client-mr-rfq/` as the RFQ folder, `KERUI.zip` and `MKON.zip` as vendor bids, reusing the prior store so only genuinely changed documents cost a call. Launch the portal through the `procurement-portal` entry in `.claude/launch.json` — not a bare `streamlit run`.

- [ ] **Step 2: Confirm each of spec §11's three claims**

| claim | where to look |
|---|---|
| the six range-driven false `fail`s are gone | already true on this branch (`6bed22b`); confirm no `fail` cell's rationale shows a two-member `in` |
| the KERUI clause-`2.3` deviation no longer overrides `starting_voltage` | that cell is no longer `deviation`; its rationale names the unattributed deviation and the document it came from |
| the three MR copies report as a single duplicate group | one `documents.near_duplicates` event; three `documents.json` records sharing one `duplicate_group` |

- [ ] **Step 3: Confirm a third unchanged run makes zero LLM calls**

Re-run with nothing changed. Expected: every document `document.skipped` with `reason: "unchanged"`, and no `document.extracted` event.

- [ ] **Step 4: Record the numbers**

Write the verdict split (`pass` / `fail` / `deviation` / `unanswered` / `review`, as counts and as a share of machine-checkable cells), the number of `deviation.unattributed` events split by reason, and the duplicate group members, into the notes file. Spec 2's screen is designed against these numbers; a claim without them is not evidence.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/notes/2026-07-31-correctness-live-run.md && git commit -m "docs: the live verdict split after deviation scoping and duplicate detection"
```

---

## Verification of done-criteria

- [ ] `python -m pytest` from the repo root. Expected: the 486-passed baseline **plus** every test added here; the one documented portal failure remains. Anything else is a real regression.
- [ ] `tests/test_phase3_lifecycle.py` rows 1–20 still green.
- [ ] No `deviation` cell in a stored matrix names a clause carried by more than one live requirement (INV-10).
- [ ] `requirements.json` is byte-identical with and without detection (INV-11).
- [ ] Task 5's live run recorded, or explicitly reported as skipped for want of a key.

## Deliberately out of scope

- **Everything in spec §12** — the Spec 2 Compliance screen, the `duplicate_group` resolution control, and the phase-3 carry-overs. One constraint this plan hands forward, from spec §7: **the unattributed-deviation note is prose for a human, not a machine-readable flag.** Spec 2 must rebuild the clause count map from `requirements.json` and `facts.json` — it is cheap — and must never grep `rationale` for it. No `ComplianceResult` field is added here, deliberately: it would have exactly one consumer and would then have to be kept true across every recompute.
- **`units.py` conversion coverage** (spec risk 1): 41 of the 52 `unanswered` cells are missing units, not vendor silence. That is the draft [`2026-07-31-compliance-accuracy-from-live-corpus.md`](2026-07-31-compliance-accuracy-from-live-corpus.md), and it is the largest remaining source of misleading verdicts after this plan.
- **A refusal net for a two-member `in`** (spec risk 2). If the live run shows the model still misclassifying range as enumeration, that fallback gets its own task; nothing here pre-empts it.
- **`m3/h` folded onto `nm3/h`, `mg/m3` onto `mg/nm3`** (spec risk 4). Inherited from phase 3 and belongs with the coverage backlog.
- **Tuning the detection thresholds** (spec risk 3). 0.9 and a 5-clause floor separate three copies of one MR cleanly on the one corpus we have. A spec-plus-addendum pair could group spuriously; the consequence is a misleading grouping, never a lost requirement, because detection is observational.

## Checklist before this plan is approved

- [x] Every task's `Interfaces` block has a **Store invariant owned** bullet. Tasks 0, 2, 4 and 5 declare **none**, each with its Rule 1 reason.
- [x] No invariant is claimed twice. INV-10 is owned only by Task 1, INV-11 only by Task 3; INV-2's extension is left with the commit that shipped it.
- [x] The integration task carries a mutation matrix. The nine baseline rows are inherited from `tests/test_phase3_lifecycle.py` rows 1–20 — stated explicitly, with a step requiring them green — because this plan adds no accumulating collection. Seven new rows cover what it does change.
- [x] Every matrix row names an invariant; INV-10 has four rows and INV-11 has three.
- [x] The Rule 3 banner appears above the first reference block.
- [x] Each reference block states which parts are load-bearing and which are illustrative.
