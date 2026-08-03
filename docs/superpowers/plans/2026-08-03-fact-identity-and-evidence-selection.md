# Fact Identity and Evidence Selection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every stored technical fact uniquely addressable, and stop the
compliance matrix from deciding an award on which of two readings the model
printed first.

**Architecture:** `fact_id_for` widens its key from `(doc_id, parameter)` to the
whole reading, which makes `overrides.py:_walk`'s existing `next(...)` correct by
construction without touching that module. `compliance.evaluate` then gathers
every fact matching the parameter instead of the first, collapses readings that
are the same quantity written twice, and returns `review` when two or more
genuinely distinct readings survive.

**Tech Stack:** Python 3.12, pydantic v2, pytest. No new dependencies. No LLM
calls are made by any task in this plan; the whole plan is validated against the
key-free suite and the stores already on disk.

**Spec:** `docs/superpowers/specs/2026-08-03-fact-identity-and-evidence-selection-design.md`

## Global Constraints

- Run everything from the repo root. `python -m pytest`.
- Tests are key-free. No test may require `ANTHROPIC_API_KEY`.
- **Baseline before this plan (workstation, `.env` + `data/` + `projects/`
  present): `797 passed, 3 skipped, 1 failed`.** The single failure is
  `tests/test_portal_app.py::test_missing_api_key_does_not_block_creation`,
  documented in `CLAUDE.md`; it is not this plan's to fix.
- The CI row is **derived** from the workstation row, never edited
  independently: `CI_passed = workstation_passed + 1 - 4 - 3`,
  `CI_skipped = workstation_skipped + 4 + 3`, `CI_failed = 0`. Update both rows
  in `CLAUDE.md` together, in the final task, once.
- `tests/test_real_corpus_coverage.py`'s four floors must stay green at shipped
  defaults. **Never lower a floor, and never use an environment override to
  green one.**
- **Arithmetic stays in Python.** No extractor and no prompt is changed by this
  plan. The model is never asked which of two readings governs.
- Every store write goes through `procurement/store/snapshots.py`. `generation`
  bumps once per write transaction, never once per file.
- `field_path` addresses list members by id, never by index.
- Missing data is never coerced to a passing or zero value.
- A failed extraction never blanks previously-good stored data.
- No task in this plan runs an ingestion or makes a paid call. `projects/` is
  read-only for the whole plan.

---

## File Structure

| file | responsibility | task |
|---|---|---|
| `procurement/units.py` | promote `_pure_number` → `pure_number`; the one primitive both layers key on | 1 |
| `procurement/store/models.py` | `fact_id_for` widened; `ComplianceResult.candidate_fact_ids` | 1, 3 |
| `procurement/extract_tech.py` | pass value + unit into `fact_id_for` | 1 |
| `procurement/compliance.py` | `_reading_key`, `_readings`; multi-candidate `evaluate` | 2, 3, 4 |
| `procurement/matrix.py` | `MatrixCell.candidate_fact_ids` mirror | 5 |
| `web/src/types.ts` | mirror of the API payload, by its own comment | 5 |
| `tests/test_store_fact_models.py` | id uniqueness and normalisation | 1 |
| `tests/test_extract_tech.py` | the extractor mints distinct ids for distinct readings | 1 |
| `tests/test_compliance.py` | collapse, multi-candidate verdicts, negation across candidates | 2, 3, 4 |
| `tests/test_compliance_matrix.py` | the mirror field survives into the matrix | 5 |
| `tests/test_fact_identity_lifecycle.py` | **new** — the two-run mutation matrix | 6 |

`procurement/store/overrides.py` is **not modified by any task.** If a task finds
itself editing it, the identity fix in Task 1 is wrong and should be revisited
rather than patched around.

---

## Task 1: Fact identity keys on the whole reading

**Files:**
- Modify: `procurement/units.py:147` (rename `_pure_number` → `pure_number`, update its two callers at `:285` and `:290`)
- Modify: `procurement/store/models.py:84-89` (`fact_id_for`)
- Modify: `procurement/extract_tech.py:115-128` (the one production caller)
- Test: `tests/test_store_fact_models.py`, `tests/test_extract_tech.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `units.pure_number(value) -> float | None`;
  `fact_id_for(doc_id: str, parameter: str, value: str | float | None, unit: str | None) -> str`
- **Store invariant owned:** every `FactRecord` in a stored
  `VendorFacts.technical` has a `fact_id` unique within that collection.

`_pure_number` is promoted rather than duplicated because both layers of this
plan key on the same question — "is this value entirely a number?" — and a second
copy would drift. Its docstring already carries the reasoning that makes it the
right primitive and `to_number` the wrong one: `to_number("ISO 8528")` returns
`8528.0`, so keying on it would collapse `ISO 8528` and `API 8528` into one
reading. That is a wrong answer, not a near miss.

The identity key deliberately does **not** unit-convert. `525 kW` and `525000 W`
get different ids, because an operator may want to override those two records
separately and baking the equivalence into the store makes that irreversible.
Collapsing happens in Task 2, at the decision layer, where it can be revisited.

- [ ] **Step 1: Write the failing tests**

In `tests/test_store_fact_models.py`, replace the existing `fact_id_for` block
(the calls at `:11-22` pass two arguments and will not compile against the new
signature — update them, do not delete them; their stability properties still
hold):

```python
from procurement.store.models import fact_id_for


def test_the_same_reading_hashes_to_the_same_id():
    a = fact_id_for("d1", "h2s_tolerance", "500", "ppm")
    assert a == fact_id_for("d1", "h2s_tolerance", "500", "ppm")
    assert a.startswith("f-")


def test_the_document_and_the_parameter_still_participate():
    assert (fact_id_for("d1", "h2s", "500", "ppm")
            != fact_id_for("d2", "h2s", "500", "ppm"))
    assert (fact_id_for("d1", "h2s", "500", "ppm")
            != fact_id_for("d1", "kw_rating", "500", "ppm"))


def test_the_parameter_is_still_case_and_whitespace_insensitive():
    assert (fact_id_for("d1", "  H2S_Tolerance ", "500", "ppm")
            == fact_id_for("d1", "h2s_tolerance", "500", "ppm"))


def test_two_readings_of_one_parameter_no_longer_collide():
    # The defect this task exists to fix: ADPOWER prints continuous_rating at
    # both 525 kW and 700 kW, and both are stored.
    assert (fact_id_for("d1", "continuous_rating", "525", "kW")
            != fact_id_for("d1", "continuous_rating", "700", "kW"))


def test_one_number_written_two_ways_is_one_id():
    # Task 7c's other deferred minor: value is str|float|None, so two chunks
    # formatting one number differently produced two keys and both survived.
    assert (fact_id_for("d1", "continuous_rating", 525.0, "kW")
            == fact_id_for("d1", "continuous_rating", "525", "kW"))


def test_the_unit_is_folded_but_never_converted():
    assert (fact_id_for("d1", "continuous_rating", "525", " kW ")
            == fact_id_for("d1", "continuous_rating", "525", "kw"))
    # Different quantity as printed, therefore separately addressable. The
    # equivalence is the compliance layer's to draw, not the store's.
    assert (fact_id_for("d1", "continuous_rating", "525", "kW")
            != fact_id_for("d1", "continuous_rating", "525000", "W"))


def test_a_missing_value_or_unit_is_keyed_not_crashed():
    assert fact_id_for("d1", "p", None, None).startswith("f-")
    assert (fact_id_for("d1", "p", None, None)
            != fact_id_for("d1", "p", "", None))
```

In `tests/test_extract_tech.py`, the assertions at `:239` and `:268-269` call
`fact_id_for` with two arguments. Update them to the new signature and add:

```python
def test_two_readings_of_one_parameter_get_distinct_ids():
    client = _client_returning([
        {"parameter": "continuous_rating", "value": "525", "unit": "kW"},
        {"parameter": "continuous_rating", "value": "700", "unit": "kW"},
    ])
    facts, status, notes = extract_tech_facts("d1", "x.txt", client, text="body")
    assert status == "ok"
    assert len({f.fact_id for f in facts}) == 2
```

Reuse whatever stub helper `tests/test_extract_tech.py` already defines for a
canned response — do not add a second one. Read the top of that file first.

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_store_fact_models.py tests/test_extract_tech.py -q
```

Expected: `TypeError: fact_id_for() missing 2 required positional arguments`.

- [ ] **Step 3: Write minimal implementation**

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the
> implementation against the test.
>
> Load-bearing: that a pure number is normalised through one shared primitive;
> that the unit is folded but never converted; that the `"f-"` prefix and the
> 10-hex-char truncation are unchanged, so ids stay the same *shape* as every id
> already on disk. Illustrative: the exact separator characters and the exact
> `repr` used for a non-numeric value.

`procurement/units.py` — rename only, no behaviour change:

```python
def pure_number(value) -> float | None:
    """The value only if it is *entirely* a number — "60" but not "ISO 8528".
    ...docstring unchanged...
    """
```

Update the two internal callers at `:285` and `:290`.

`procurement/store/models.py`:

```python
from procurement import units


def fact_id_for(doc_id: str, parameter: str,
                value: str | float | None, unit: str | None) -> str:
    """Stable across re-extraction for a given (document, parameter, value, unit).

    All four participate, for the reason `deviation_id_for` gives: if a later
    extraction rewords the value the id shifts and an override orphans, which is
    strictly better than an override silently retargeting a different reading of
    the same parameter. A datasheet that states one parameter twice with
    different numbers is stating two facts, and they must be addressable apart.

    The value is normalised through `units.pure_number` so that 525.0 and "525"
    are one id. The unit is folded to a stripped, lowercased spelling but never
    converted: "525 kW" and "525000 W" are two readings as printed, and whether
    they mean one quantity is the compliance layer's question, not the store's.
    """
    number = units.pure_number(value)
    v = repr(number) if number is not None else str(value).strip().lower()
    u = (unit or "").strip().lower()
    key = f"{doc_id}:{parameter.strip().lower()}:{v}:{u}"
    return "f-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]
```

Confirm `procurement.units` imports nothing from `procurement.store` before
adding that import — it currently imports only `math` and `re`, so there is no
cycle. If that has changed, inline the two-line normalisation instead of
importing.

`procurement/extract_tech.py:115-128` — pass what it already has, and replace the
comment that explains why the collision was *not* this extractor's to fix:

```python
        out.append(FactRecord(
            # Keyed on the whole reading, so the two ADPOWER ratings above are
            # separately addressable by an override and separately visible to
            # compliance.py. See docs/superpowers/specs/
            # 2026-08-03-fact-identity-and-evidence-selection-design.md.
            fact_id=fact_id_for(doc_id, name, fact.value, fact.unit),
            ...
        ))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_store_fact_models.py tests/test_extract_tech.py tests/test_units.py -q
```

Expected: PASS. `tests/test_units.py` is included because Step 3 renamed a
function it may reference.

- [ ] **Step 4b: Confirm no other caller broke**

```bash
python -m pytest -q
```

Expected: the documented `797 passed, 3 skipped, 1 failed` plus this task's new
tests, and **no new failure**. If a test outside the two files above fails, a
caller was missed — find it rather than adjusting the test.

- [ ] **Step 5: Commit**

```bash
git add procurement/units.py procurement/store/models.py procurement/extract_tech.py tests/test_store_fact_models.py tests/test_extract_tech.py
git commit -m "fix(store): a fact id keys on the whole reading, not the parameter"
```

---

## Task 2: Distinct readings, with equivalents collapsed

**Files:**
- Modify: `procurement/compliance.py` (add below `_states_something`, above `evaluate`)
- Test: `tests/test_compliance.py`

**Interfaces:**
- Consumes: `units.pure_number` from Task 1.
- Produces: `_reading_key(value, unit, parameter) -> tuple`;
  `_readings(facts: list[dict], parameter: str | None) -> list[list[dict]]`
  — the facts stating `parameter`, grouped into distinct readings, each group in
  document order and the groups themselves in first-appearance order.
- **Store invariant owned:** none — this task adds two pure functions and writes
  nothing. It is defended through the invariant Task 3 owns. (Precedent: the
  `units.py` task of `2026-07-31-compliance-accuracy-from-live-corpus`.)

Grouping is by canonical quantity where both sides permit it, and by exact text
otherwise. `units.to_canonical` raises `Unconvertible` for an unrecognised or
absent unit; that is not an error here, it is the signal to fall back to the text
key. Rounding before comparison keeps `525 kW` and `525000 W` from missing each
other on float representation.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_compliance.py`:

```python
from procurement.compliance import _readings


def _f(parameter, value, unit=None, fact_id="f-x", doc_id="d1"):
    return {"parameter": parameter, "value": value, "unit": unit,
            "fact_id": fact_id, "doc_id": doc_id}


def test_one_reading_stays_one_reading():
    facts = [_f("continuous_rating", "525", "kW")]
    assert len(_readings(facts, "continuous_rating")) == 1


def test_two_different_numbers_are_two_readings():
    facts = [_f("continuous_rating", "525", "kW"),
             _f("continuous_rating", "700", "kW")]
    assert len(_readings(facts, "continuous_rating")) == 2


def test_one_quantity_written_in_two_units_is_one_reading():
    facts = [_f("continuous_rating", "525", "kW"),
             _f("continuous_rating", "525000", "W")]
    groups = _readings(facts, "continuous_rating")
    assert len(groups) == 1
    assert len(groups[0]) == 2          # both members kept, for citation


def test_an_unconvertible_unit_falls_back_to_text_and_does_not_raise():
    facts = [_f("applicable_standard", "ISO 8528"),
             _f("applicable_standard", "ISO 8528")]
    assert len(_readings(facts, "applicable_standard")) == 1


def test_two_standards_sharing_digits_are_not_one_reading():
    # to_number("ISO 8528") is 8528.0, so keying on it would merge these.
    facts = [_f("applicable_standard", "ISO 8528"),
             _f("applicable_standard", "API 8528")]
    assert len(_readings(facts, "applicable_standard")) == 2


def test_a_range_is_not_collapsed_onto_its_lower_bound():
    # ADPOWER states ambient_design_temp as 55, 5-58 and 4-58 on one store.
    facts = [_f("ambient_design_temp", "55", "Deg C"),
             _f("ambient_design_temp", "5-58", "deg C"),
             _f("ambient_design_temp", "4-58", "deg C")]
    assert len(_readings(facts, "ambient_design_temp")) == 3


def test_only_facts_naming_the_parameter_are_grouped():
    facts = [_f("continuous_rating", "525", "kW"), _f("h2s_tolerance", "500", "ppm")]
    assert len(_readings(facts, "continuous_rating")) == 1


def test_a_fact_stating_nothing_is_not_a_reading():
    facts = [_f("continuous_rating", "525", "kW"),
             _f("continuous_rating", None), _f("continuous_rating", "   ")]
    assert len(_readings(facts, "continuous_rating")) == 1


def test_groups_and_members_stay_in_document_order():
    facts = [_f("p", "700", "kW", fact_id="f-b"), _f("p", "525", "kW", fact_id="f-a"),
             _f("p", "700000", "W", fact_id="f-c")]
    groups = _readings(facts, "p")
    assert [g[0]["fact_id"] for g in groups] == ["f-b", "f-a"]
    assert [m["fact_id"] for m in groups[0]] == ["f-b", "f-c"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_compliance.py -q -k readings
```

Expected: `ImportError: cannot import name '_readings'`.

- [ ] **Step 3: Write minimal implementation**

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the
> implementation against the test.
>
> Load-bearing: that `pure_number` and not `to_number` decides the numeric path;
> that `Unconvertible` falls back rather than propagating; that
> `_states_something` still filters blank evidence before grouping; that order is
> preserved. Illustrative: the exact tuple shape of the key and the rounding
> precision.

```python
def _reading_key(value, unit, parameter: str | None):
    """The identity of a reading for comparison purposes.

    Numeric only when the value is *entirely* a number: `units.to_number` reads a
    leading quantity out of prose, which would key "ISO 8528" as 8528 and merge
    it with "API 8528". Unit conversion is attempted so that one quantity printed
    in two units is one reading; an unrecognised or absent unit is not an error
    here, it just means the text is the best identity available.
    """
    number = units.pure_number(value)
    if number is not None:
        try:
            canonical, canonical_unit = units.to_canonical(number, unit, parameter)
            return ("num", round(canonical, 9), canonical_unit)
        except units.Unconvertible:
            pass
    return ("txt", str(value).strip().lower())


def _readings(facts: list[dict], parameter: str | None) -> list[list[dict]]:
    """Facts stating `parameter`, grouped into distinct readings.

    Groups are returned in first-appearance order and members in document order,
    so a caller citing `group[0]` cites the first the document printed. Blank
    evidence is filtered by `_states_something` before grouping: a fact naming
    the parameter with no value is not a reading, and counting it would
    manufacture a multiplicity out of nothing.
    """
    want = _norm(parameter)
    groups: dict[tuple, list[dict]] = {}
    for fact in facts:
        if _norm(fact.get("parameter")) != want:
            continue
        if not _states_something(fact.get("value")):
            continue
        key = _reading_key(fact.get("value"), fact.get("unit"), parameter)
        groups.setdefault(key, []).append(fact)
    return list(groups.values())
```

Python dicts preserve insertion order, which is what makes the ordering
assertions hold. Do not sort.

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_compliance.py -q
```

Expected: PASS, including every pre-existing test in the file — this task adds
functions and changes no call site.

- [ ] **Step 5: Commit**

```bash
git add procurement/compliance.py tests/test_compliance.py
git commit -m "feat(compliance): group a parameter's facts into distinct readings"
```

---

## Task 3: `evaluate` decides on candidates, not the first match

**Files:**
- Modify: `procurement/store/models.py:185-194` (`ComplianceResult`)
- Modify: `procurement/compliance.py:120-212` (`evaluate`)
- Test: `tests/test_compliance.py`

**Interfaces:**
- Consumes: `_readings` from Task 2.
- Produces: `ComplianceResult.candidate_fact_ids: list[str] = []`. `evaluate`'s
  signature is unchanged.
- **Store invariant owned:** every stored `ComplianceResult` references, in
  `fact_id` and in `candidate_fact_ids`, only fact ids that exist in that
  vendor's currently-stored `VendorFacts.technical` — and a cell computed from
  more than one distinct reading names all of them.

`fact_id` keeps its current meaning exactly, because `matrix.py`'s docstring
records that it decomposes `unanswered` on `fact_id` specifically to avoid
putting the wording of `units.py`'s messages on the critical path of a number a
reviewer acts on. The new field carries the rest and defaults to empty, so every
snapshot already on disk still validates.

Scope: `auto`, and `stated` where `requirement.value` is not None. Presence-only
`stated` is Task 4. `judgement` is untouched — it is already `review` and already
lists candidate facts.

- [ ] **Step 1: Write the failing test**

```python
def test_two_distinct_readings_are_review_not_an_auto_decision():
    req = _requirement(parameter="continuous_rating", checkability="auto",
                       operator=">=", value=600, unit="kW")
    facts = [_f("continuous_rating", "700", "kW", fact_id="f-a"),
             _f("continuous_rating", "525", "kW", fact_id="f-b")]
    r = evaluate(req, facts, [], "ADPOWER", "now")
    assert r.verdict == "review"
    assert set(r.candidate_fact_ids) == {"f-a", "f-b"}
    assert "700" in r.rationale and "525" in r.rationale


def test_a_vendor_is_no_longer_failed_on_print_order():
    # The live defect: on coverage-floor-t7 this cell currently reads `fail`.
    req = _requirement(parameter="continuous_rating", checkability="auto",
                       operator=">=", value=600, unit="kW")
    facts = [_f("continuous_rating", "525", "kW", fact_id="f-b"),
             _f("continuous_rating", "700", "kW", fact_id="f-a")]
    assert evaluate(req, facts, [], "ADPOWER", "now").verdict == "review"


def test_one_reading_decides_exactly_as_before():
    req = _requirement(parameter="continuous_rating", checkability="auto",
                       operator=">=", value=600, unit="kW")
    facts = [_f("continuous_rating", "700", "kW", fact_id="f-a")]
    r = evaluate(req, facts, [], "ADPOWER", "now")
    assert r.verdict == "pass"
    assert r.fact_id == "f-a"
    assert r.candidate_fact_ids == []


def test_equivalent_readings_still_decide():
    req = _requirement(parameter="continuous_rating", checkability="auto",
                       operator=">=", value=600, unit="kW")
    facts = [_f("continuous_rating", "700", "kW", fact_id="f-a"),
             _f("continuous_rating", "700000", "W", fact_id="f-c")]
    r = evaluate(req, facts, [], "ADPOWER", "now")
    assert r.verdict == "pass"
    assert r.fact_id == "f-a"                  # first in document order
    assert r.candidate_fact_ids == ["f-a", "f-c"]


def test_a_stated_value_requirement_with_two_readings_is_review():
    req = _requirement(parameter="insulation_class", checkability="stated",
                       value="Class F")
    facts = [_f("insulation_class", "Class F", fact_id="f-a"),
             _f("insulation_class", "Class H", fact_id="f-b")]
    r = evaluate(req, facts, [], "ADPOWER", "now")
    assert r.verdict == "review"
    assert set(r.candidate_fact_ids) == {"f-a", "f-b"}


def test_multiplicity_never_produces_fail():
    req = _requirement(parameter="continuous_rating", checkability="auto",
                       operator=">=", value=900, unit="kW")
    facts = [_f("continuous_rating", "700", "kW"), _f("continuous_rating", "525", "kW")]
    assert evaluate(req, facts, [], "ADPOWER", "now").verdict == "review"


def test_a_declared_deviation_still_wins_over_multiplicity():
    req = _requirement(parameter="continuous_rating", checkability="auto",
                       operator=">=", value=600, unit="kW", clause_ref="2.3")
    facts = [_f("continuous_rating", "700", "kW"), _f("continuous_rating", "525", "kW")]
    devs = [{"clause_ref": "2.3", "disposition": "deviate", "statement": "no"}]
    assert evaluate(req, facts, devs, "ADPOWER", "now").verdict == "deviation"


def test_no_reading_is_still_unanswered_not_review():
    req = _requirement(parameter="continuous_rating", checkability="auto",
                       operator=">=", value=600, unit="kW")
    assert evaluate(req, [], [], "ADPOWER", "now").verdict == "unanswered"
```

Use whatever `_requirement` builder `tests/test_compliance.py` already defines;
read the file first and reuse it rather than adding a second one.

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_compliance.py -q
```

Expected: FAIL — `ComplianceResult` has no `candidate_fact_ids`, and the
multiplicity tests return `pass`/`fail`.

- [ ] **Step 3: Write minimal implementation**

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the
> implementation against the test.
>
> Load-bearing: that the deviation branch still runs *first*; that multiplicity
> returns `review` and never `fail`; that the single-reading path is left
> byte-identical; that `fact_id` keeps naming one fact. Illustrative: the exact
> rationale wording.

`procurement/store/models.py`:

```python
class ComplianceResult(BaseModel):
    ...
    fact_id: str | None = None
    # Every reading the cell was computed from, including the one named by
    # fact_id. Empty when a single reading settled it. Defaults to [] so every
    # snapshot written before this field existed still validates.
    candidate_fact_ids: list[str] = []
```

`procurement/compliance.py` — extend the inner `result` helper and insert one
branch per tier:

```python
    def result(verdict, rationale, fact=None, candidates=()):
        return ComplianceResult(
            req_id=requirement.req_id, vendor=vendor, verdict=verdict,
            fact_id=(fact or {}).get("fact_id"), doc_id=(fact or {}).get("doc_id"),
            candidate_fact_ids=[c.get("fact_id") for c in candidates
                                if c.get("fact_id")],
            rationale=rationale, evaluated_at=now)


def _cite(groups) -> str:
    """One printable clause per distinct reading, with its source document."""
    return "; ".join(
        f"{g[0].get('value')!r}{' ' + str(g[0].get('unit')) if g[0].get('unit') else ''}"
        f" (doc {g[0].get('doc_id')})" for g in groups)
```

In the `stated` branch, replacing the `next(...)` at `:143-144`:

```python
    if requirement.checkability == "stated":
        groups = _readings(facts, requirement.parameter)
        if not groups:
            return result("unanswered",
                          f"no vendor document stated {requirement.parameter!r}")
        if requirement.value is not None and len(groups) > 1:
            # Two different stated values disagree about the thing asked. Which
            # one governs is not in the documents, and a token comparison that
            # picked one would be deciding on print order.
            flat = [f for g in groups for f in g]
            return result("review",
                          f"required {requirement.parameter} = "
                          f"{requirement.value!r}; vendor states more than one "
                          f"value: {_cite(groups)} — decide which governs",
                          groups[0][0], flat)
        fact = groups[0][0]
        ...unchanged from here...
```

In the `auto` branch, replacing the `next(...)` at `:197`:

```python
    groups = _readings(facts, requirement.parameter)
    if not groups:
        return result("unanswered",
                      f"no vendor document stated {requirement.parameter!r}")
    if len(groups) > 1:
        flat = [f for g in groups for f in g]
        return result("review",
                      f"{requirement.parameter} is stated more than once with "
                      f"different values: {_cite(groups)} — decide which governs "
                      f"before comparing against {requirement.value!r}",
                      groups[0][0], flat)
    fact = groups[0][0]
    ...unchanged from here...
```

Note the `auto` branch previously used a bare `next(...)` with **no**
`_states_something` filter, while `stated` had one. `_readings` applies the
filter to both. That is a deliberate tightening: a fact naming the parameter with
no value was reaching `units.compare` and producing an `Unconvertible`
→ `unanswered` with a confusing reason. It now reads as absent, which is what it
is. Verify no existing test asserted the old behaviour; if one does, read it
before changing it.

Also set `candidate_fact_ids` on the single-reading paths so an equivalent-group
cell records every member — pass `groups[0]` as `candidates` where a group has
more than one member.

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_compliance.py tests/test_compliance_coverage.py tests/test_compliance_matrix.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add procurement/store/models.py procurement/compliance.py tests/test_compliance.py
git commit -m "fix(compliance): two distinct readings are review, not a decision on print order"
```

---

## Task 4: The negation guard runs across every candidate

**Files:**
- Modify: `procurement/compliance.py` (the `stated` branch's presence-only path)
- Test: `tests/test_compliance.py`

**Interfaces:**
- Consumes: `_readings` from Task 2, `result(..., candidates=)` from Task 3.
- Produces: no new symbol.
- **Store invariant owned:** no stored `ComplianceResult` reports `pass` for a
  presence-only `stated` requirement when any live reading of that parameter
  carries a refusal.

Presence-only requirements are exempt from the multiplicity rule — two readings
both satisfy presence, so escalating them would spend reviewer attention on
enumerations like `applicable_standard` (18 readings on the live corpus) rather
than on conflicts. But they are **not** exempt from the negation guard. Today
`_negations_in` only ever inspects the one reading `next(...)` returned, so a
vendor stating a parameter twice can hide an `"N/A"` behind a compliant-looking
first print. That is phase 4's own Important 1 leaking through this gap.

- [ ] **Step 1: Write the failing test**

```python
def test_a_refusal_on_a_later_reading_is_not_hidden_by_the_first():
    req = _requirement(parameter="anchor_bolt", checkability="stated", value=None)
    facts = [_f("anchor_bolt", "Supplied", fact_id="f-a"),
             _f("anchor_bolt", "N/A", fact_id="f-b")]
    r = evaluate(req, facts, [], "ADPOWER", "now")
    assert r.verdict == "review"
    assert "f-b" in r.candidate_fact_ids


def test_presence_only_with_two_clean_readings_still_passes():
    # An enumeration is an answer, at length — not a conflict.
    req = _requirement(parameter="applicable_standard", checkability="stated",
                       value=None)
    facts = [_f("applicable_standard", "ISO 8528", fact_id="f-a"),
             _f("applicable_standard", "IEC 60034", fact_id="f-b")]
    assert evaluate(req, facts, [], "ADPOWER", "now").verdict == "pass"


def test_the_requirements_own_negation_is_still_not_a_refusal():
    req = _requirement(parameter="asbestos", checkability="stated",
                       value=None, text="no asbestos")
    facts = [_f("asbestos", "No asbestos used in any component", fact_id="f-a")]
    assert evaluate(req, facts, [], "ADPOWER", "now").verdict == "pass"
```

The third test guards the mechanism phase 4's fix wave deliberately built and
pinned; it must keep passing.

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_compliance.py -q -k refusal
```

Expected: FAIL — the first test returns `pass`, because only `f-a` is inspected.

- [ ] **Step 3: Write minimal implementation**

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the
> implementation against the test.
>
> Load-bearing: that the requirement's own tokens are still subtracted, so a
> clause requiring "no asbestos" is still satisfiable; that the verdict is
> `review` and never `fail`; that the refusing reading is the one cited.

In the presence-only path of the `stated` branch:

```python
        if requirement.value is None:
            # Every reading, not just the first: a vendor stating a parameter
            # twice could otherwise hide a refusal behind a compliant-looking
            # first print, which is the negation guard's own defect restated.
            for group in groups:
                negations = _negations_in(None, group[0].get("value"))
                if negations:
                    flat = [f for g in groups for f in g]
                    return result("review",
                                  f"{requirement.parameter} must be stated; "
                                  f"vendor states {group[0].get('value')!r}, "
                                  f"which reads as a refusal "
                                  f"({', '.join(negations)}) — read it before "
                                  f"accepting", group[0], flat)
```

Then fall through to the existing `pass` path unchanged. Keep the existing
single-reading negation check for the value-matching path exactly as phase 4's
fix wave wrote it — this task adds the presence-only sweep, it does not rewrite
the guard.

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_compliance.py -q
```

Expected: PASS, including the four negation tests the phase-4 fix wave added.

- [ ] **Step 5: Commit**

```bash
git add procurement/compliance.py tests/test_compliance.py
git commit -m "fix(compliance): a refusal on any reading is not hidden by the first"
```

---

## Task 5: Mirror the citation into the matrix and the web types

**Files:**
- Modify: `procurement/matrix.py:32-38` (`MatrixCell`), `:95-99` (construction)
- Modify: `web/src/types.ts:18-19`
- Test: `tests/test_compliance_matrix.py`

**Interfaces:**
- Consumes: `ComplianceResult.candidate_fact_ids` from Task 3.
- Produces: `MatrixCell.candidate_fact_ids: list[str] = []`.
- **Store invariant owned:** none by construction — `matrix.py` never writes.
  The property it must preserve, and which its own docstring states, is that
  `build_matrix` called twice leaves `generation` unchanged.

Neither UI renders `fact_id` today: `portal/views/compliance.py:85-87` renders
`rationale` only, and `web/src/pages/ComplianceMatrix.tsx` declares the field
without displaying it. The reviewer therefore already sees every reading, because
Task 3 puts them in the rationale prose. This task carries the structured field
through for API consumers and keeps `web/src/types.ts` an exact mirror of the
payload, which is what that file's own comment says it is. **No JSX changes.**

- [ ] **Step 1: Write the failing test**

```python
def test_a_multi_reading_cell_carries_its_candidates_into_the_matrix(tmp_path):
    # Seed a store whose vendor states one parameter twice, run
    # evaluate_project, then build_matrix.
    ...use the file's existing store-seeding helper...
    row = next(r for r in matrix.rows if r.parameter == "continuous_rating")
    cell = row.cells["ADPOWER"]
    assert cell.verdict == "review"
    assert len(cell.candidate_fact_ids) == 2


def test_a_single_reading_cell_carries_an_empty_candidate_list(tmp_path):
    ...
    assert row.cells["ADPOWER"].candidate_fact_ids == []
```

Read `tests/test_compliance_matrix.py` first and reuse its existing seeding
helper. Do not hand-roll a snapshot write — every write goes through
`procurement/store/snapshots.py`.

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_compliance_matrix.py -q
```

Expected: FAIL — `MatrixCell` has no `candidate_fact_ids`.

- [ ] **Step 3: Write minimal implementation**

```python
class MatrixCell(BaseModel):
    vendor: str
    verdict: str
    group: str
    rationale: str = ""
    fact_id: str | None = None
    doc_id: str | None = None
    # Every reading the verdict was computed from. More than one means the cell
    # was not auto-decided; the rationale names them in prose.
    candidate_fact_ids: list[str] = []
```

and in the construction at `:95-99`, pass
`candidate_fact_ids=result.candidate_fact_ids`. The `_ABSENT` branch at `:91-93`
is left alone — no cell was computed, so there is nothing to cite.

`web/src/types.ts`, alongside the existing `fact_id` / `doc_id`:

```typescript
  candidate_fact_ids: string[]
```

- [ ] **Step 4: Run tests and build the web bundle**

```bash
python -m pytest tests/test_compliance_matrix.py tests/test_api_compliance.py tests/test_portal_compliance.py -q
```

```bash
npm --prefix web run build
```

Expected: PASS, and a clean build. If `tsc` reports the new field missing on an
object literal in a test fixture or mock, add it there — the mirror is the point.

- [ ] **Step 5: Commit**

```bash
git add procurement/matrix.py web/src/types.ts tests/test_compliance_matrix.py
git commit -m "feat(matrix,web): carry the candidate readings through to consumers"
```

---

## Task 6: Integration — the two-run mutation matrix

**Files:**
- Create: `tests/test_fact_identity_lifecycle.py`
- Modify: `CLAUDE.md` (both baseline rows, together, once)
- Test: the file created above, plus the full suite

**Interfaces:**
- Consumes: every symbol from Tasks 1–5.
- Produces: nothing importable.
- **Store invariant owned:** across two runs, the requirement × vendor matrix has
  a cell for every (live requirement, live vendor) pair and no cell for any
  other pair; and no stored override or verdict references a `fact_id` absent
  from the vendor's live facts.

Use the mock client. **No task in this plan makes an LLM call.** Model the two
runs the way `tests/test_phase3_lifecycle.py` and
`tests/test_pipeline_incremental.py` already do — read both before writing this
file and follow the established fixture shape rather than inventing one.

- [ ] **Step 1: Write the failing tests — one per row**

| mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|
| a second, different reading of one parameter appears | T3: a cell computed from more than one reading names all of them | cell moves `pass` → `review`; both ids in `candidate_fact_ids` |
| that second reading is corrected away | T3: cells reference only live fact ids | cell returns to `pass`; `candidate_fact_ids` empty; no id of the removed reading survives anywhere |
| the second reading is a unit restatement, not a disagreement | T3 | cell stays `pass`; both ids cited; no escalation |
| an override addressed at the 700 kW reading, both readings preserved | T1: ids unique within the collection | `apply_overrides` changes the 700 kW record and leaves the 525 kW record untouched |
| a reading's value is reworded between runs | T1 | the override orphans **visibly** — `reconcile` sets `conflict`, and `apply_overrides` logs; it does not silently retarget |
| a refusal appears as a later reading of a presence-only parameter | T4 | cell moves `pass` → `review`, citing the refusing reading |
| **a newer revision of an already-extracted document arrives** | T3 + store invariant C1 | facts of the superseded revision are pruned; no cell cites a pruned id |
| **a document is deleted from its source folder** | C1 | same — pruned, and no cell cites a pruned id |
| **a second upload arrives carrying a sibling revision** | C2b | lineage pointers survive; ids of the still-live revision unchanged |
| **each prompt-version constant is bumped, one at a time** | C2a | the bump persists; a re-run makes no further calls; ids are re-minted consistently |
| **an LLM call fails on run 1 and succeeds on run 2** | I1 | run 2's facts are stored with fresh ids; run 1's failure is not cached as an answer |
| **an LLM call fails on both runs** | I4 | the reason is in `DocumentRecord.notes`; prior good facts survive |
| **an entity's only source document is unrecognised** | I2 | the vendor still gets a column of `unanswered`, not a vanished column |
| **a re-extraction fails after a successful one** | I3 | previously-good facts and their ids survive unchanged |
| **the model returns a response omitting an optional array** | I5 | read as "no facts", not as a failure |

The nine bolded rows are the template's required set. They are not decorative
here: this plan changes what a `fact_id` *is*, so every row that involves facts
crossing a run boundary is exercising the new key.

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_fact_identity_lifecycle.py -q
```

Expected: FAIL on the rows this plan's behaviour is new for. Rows covering
pre-existing behaviour (C1, C2a/b, I1–I5) should **pass immediately** — they are
regression guards. If one of those fails, an earlier task broke something; stop
and fix it rather than adjusting the row.

- [ ] **Step 3: Make them pass**

If a row fails for a reason other than "not written yet", the defect is in Tasks
1–5, not in this file. Fix it there.

- [ ] **Step 4: Run the full suite**

```bash
python -m pytest -q
```

Expected: the documented single `.env` portal failure and nothing else.

- [ ] **Step 4b: Verify the matrix is real, not decorative**

For each row, reintroduce its defect one at a time — revert the relevant hunk in
a scratch copy — and confirm **the intended row fails and nothing else does**. A
row that still passes with its defect reinstated is not testing what it claims.
Phase 2's fix wave did this with one throwaway script; put yours in the
scratchpad, not in the repo.

Reinstatements to use:
- `fact_id_for` back to `(doc_id, parameter)` → the override-targeting and
  distinct-ids rows must fail.
- `_readings` → `next(...)` in the `auto` branch → the multiplicity rows must fail.
- `_reading_key` using `units.to_number` instead of `pure_number` → the
  standards-sharing-digits row must fail.
- The presence-only sweep reduced to `groups[0]` → the hidden-refusal row must fail.

- [ ] **Step 4c: Confirm the live-corpus floors are still green**

```bash
python -m pytest tests/test_real_corpus_coverage.py -q
```

Expected: 4 passed at shipped defaults, with no `LLM_MAX_TOKENS` or chunk-budget
override set. These floors assert on extraction and checkability, not on verdict
distribution, so this plan cannot redden them — if one goes red, something
unintended happened. **Do not lower a floor.**

- [ ] **Step 5: Update both baseline rows in `CLAUDE.md`, together**

Measure the workstation row, then derive the CI row with the arithmetic in Global
Constraints. Do not edit the two rows independently — that is how they drift.

- [ ] **Step 6: Commit**

```bash
git add tests/test_fact_identity_lifecycle.py CLAUDE.md
git commit -m "test: two-run mutation matrix for fact identity and evidence selection"
```

---

## Ledger

Record per-task outcomes in
`.superpowers/sdd/2026-08-03-fact-identity-and-evidence-selection/progress.md`,
following the format of the phase-4 ledger: commit range, test counts, the store
invariant defended, and any deferred minor with the reason it was deferred.

The phase-4 final review left one process observation worth acting on here: both
of its promoted findings were **pairs of tasks**, each graded minor by a reviewer
who could see only one half, and nothing in the process scheduled the
recombination. This plan is short enough to re-triage its whole deferred list in
one pass before the final gate. Do that, explicitly, as part of Task 6.

---

## Self-review

**Spec coverage.** §3 → Task 1. §4 → Tasks 2 and 3. §5 → Tasks 3 and 4. §6 →
Tasks 3 and 5. §7 (no migration) → asserted by Task 6's rows on superseded and
deleted documents. §8 invariants → Global Constraints plus one owned invariant
per task. §9 testing → Task 6. §10 impact → Task 6 Step 4c. §11 out of scope →
no task touches the subset matcher, `unpack_vendor_zip`, or any extractor
prompt.

**One deviation from the spec, recorded deliberately.** §6 says the field is
"rendered by `web/src/pages/ComplianceMatrix.tsx` and
`portal/views/compliance.py`". Neither renders `fact_id` today, so Task 5 mirrors
the type and renders nothing; the readings reach the reviewer through the
rationale prose that Task 3 writes. Rendering the ids as chips would be new UI
the spec did not ask for.

**One correction to the spec's §4, load-bearing.** The collapse must key on
`units.pure_number`, not `units.to_number`. `to_number("ISO 8528")` is `8528.0`,
so a `to_number` key would merge `ISO 8528` with `API 8528` — accepting a
different standard, which is the exact failure `_pure_number`'s docstring exists
to prevent. Task 2 pins this with
`test_two_standards_sharing_digits_are_not_one_reading`.

**Type consistency.** `fact_id_for(doc_id, parameter, value, unit)` is used with
four arguments in Tasks 1 and 6. `_readings(facts, parameter) -> list[list[dict]]`
returns groups in Task 2 and is consumed as groups in Tasks 3 and 4.
`candidate_fact_ids: list[str]` is named identically on `ComplianceResult`
(Task 3), `MatrixCell` (Task 5) and in `web/src/types.ts` (Task 5).

**Placeholder scan.** The `...` occurrences are all explicit "reuse the existing
helper in this file, read it first" instructions or "unchanged from here" markers
against a cited line range, not deferred decisions.
