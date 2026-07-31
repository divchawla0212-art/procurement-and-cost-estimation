# Compliance Accuracy from the Live Corpus — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This plan follows [`docs/superpowers/PLAN-TEMPLATE.md`](../PLAN-TEMPLATE.md).** Read it before starting.

**Goal:** Make the `unanswered` verdict mean "the vendor did not state it", not "`units.py` could not read it" — so the coverage percentage measures extraction, which is what spec §7 uses it for.

**Architecture:** Three changes to `procurement/units.py` (compare identical units without converting; recognise the unit families the real corpus actually prints; compare non-numeric `==` by token) and one prompt change (`requirements_v3`) so the model stops emitting `==` for a clause that states a maximum. Nothing new is stored; `compliance.py` recomputes wholesale as it already does, so every verdict changes on the next run with no migration.

**Tech Stack:** Python 3.12, Pydantic v2, `re`, `math`, `pytest`. No new dependencies.

## Evidence this plan is built on

Every number below is from the live run of 2026-07-31 (`claude-sonnet-5`, `processed-data/01-client-mr-rfq/` + `02-vendor-bids/KERUI/`, 533 cells, 103 of them machine-checkable). This is not a speculative cleanup — each task names the cells it converts.

| unanswered cells | cause | task |
|---|---|---|
| 8 | `%` unrecognised (`sustained_overload_current >= 110 %` vs a fact with no unit) | 2 |
| 7 | `winding_insulation_class == H` vs `Class H` — requirement value is not a number | 3 |
| 6 | `months` unrecognised (`warranty_period == 12 months` vs `12 Months after commissioning`) | 2 |
| 4 | `dBA` / `dB(A)` unrecognised, one of them against `dB(A) at 1m` | 1, 2 |
| 1 | `barg` unrecognised — **both sides already say `barg`** | 1 |
| 1 | `Amp` unrecognised — **both sides already say `Amp`** | 1 |
| 1 | `mg/Nm3` vs `mg/Nm3` refused for an unknown molar mass — **both sides identical** | 1 |
| 1 | `kW@ 55 Deg C` unrecognised | 1 |
| 1 | `m`, 1 `s`, 1 `VAC` unrecognised | 2 |
| 2 | `%` requirement against an `A` fact — genuine family mismatch, stays unanswered | — |
| 1 | `kW` requirement against a `V` fact — genuine extraction error, stays unanswered | — |
| **14** | **vendor genuinely never stated the parameter — the real metric** | — |

Three of those rows say *both sides already state the same unit*. `to_canonical` converts anyway, and for `mg/Nm3` that conversion needs a molar mass we refuse to guess — so a comparison needing no arithmetic at all fails. That is Task 1 and it costs nothing in risk.

The remaining false FAILs (5 of 103) are a different shape: clause 2.5.1 was extracted twice from two duplicate MR copies, once as `h2s_content <= 700 ppm` (pass, right) and once as `== 700 ppm` (fail, wrong — the vendor's 50 ppm is better than required). That is Task 4.

## Global Constraints

- Python `>=3.12`; dependencies unchanged.
- Snapshots under `projects/<slug>/store/` remain the only authoritative store.
- **Arithmetic stays in Python.** The model reads; code decides. No extractor is asked whether a vendor complies.
- **`unanswered` is never `fail`,** and no change here may turn a refusal into a pass. Every new conversion must be exact; anything approximate raises `Unconvertible` with a reason.
- Missing data is never coerced to a passing or zero value.
- Tests run key-free via `MockLLMClient` or a local stub. No test may require `ANTHROPIC_API_KEY`.
- Run all tests from the repo root: `python -m pytest`.
- Baseline before starting: **486 passed, 3 skipped, 1 failed** (`test_portal_app.py::test_missing_api_key_does_not_block_creation`, environment-dependent, documented in `CLAUDE.md`). Anything else is a real regression.

## Judgment calls made while writing this plan

These are decisions the evidence does not settle. Each is implemented as described; flag disagreement before starting, not mid-execution.

1. **Identical units are compared without converting.** If both sides fold to the same unit string, the numbers are compared directly and `to_canonical` is never called. This is what rescues `mg/Nm3` vs `mg/Nm3`, whose molar-mass refusal is correct in general and absurd when no conversion is needed. Risk: a unit we fold too aggressively could make two different quantities look identical — which is why Task 1's folding is limited to stripping a trailing measurement condition, and why cross-unit pairs still go through the family check unchanged.
2. **A measurement condition is stripped from a unit and named in the rationale.** `dB(A) at 1m`, `kW@ 55 Deg C` and `mg/Nm3@3% O2` are a unit plus the condition it was measured under. The condition is dropped for comparison and quoted in the rationale, so a human sees exactly what was ignored. This is the same "state the assumption, never make it silently" rule the assumed-unit note already follows. It is a real loss of rigour for emissions, where `@3% O2` changes the number — recorded here rather than hidden.
3. **Calendar units are their own family and do not convert to seconds.** `months` and `years` convert to each other (12) because that is exact. They do **not** convert to hours or seconds, because a month has no fixed length. `weeks` gets its own family for the same reason — weeks-to-months is 4.348, not a conversion. A `weeks` fact against a `months` requirement therefore raises, and the cell stays `unanswered` with a reason.
4. **Gauge pressure is a separate family from absolute pressure.** `barg`/`psig` differ from `bar`/`psi` by one atmosphere. They fold to their own family, so `barg` vs `barg` compares and `barg` vs `bar` refuses. Silently treating them as equal would misjudge a fuel-gas pressure requirement by ~14 psi.
5. **`VAC` and `VDC` both fold to volts.** A requirement stating `230 VAC` against a fact stating `230 V` should compare. The cost is that an AC requirement and a DC fact would compare as equal magnitudes; in this domain they differ by an order of magnitude (24 V DC starting vs 230 V AC supply) so a wrong pass is implausible, and the alternative is that every battery and heater clause stays `unanswered`.
6. **Non-numeric `==` passes when the requirement's tokens are a subset of the vendor's.** `insulation_class == H` against `Class H` must pass; against `Class F` must fail. Exact string equality fails the first; substring matching would pass `F` against `Class F Rise` wrongly. Token-subset is the narrowest rule that gets the real corpus right, and it is stated in the rationale so a reviewer can see what matched.
7. **`percent` is its own family, not dimensionless.** Folding `%` into `""` would let `black_starts >= 3` compare against `10 %`.

## Store invariant ledger

| id | invariant (over stored state) | owner | matrix rows |
|---|---|---|---|
| **INV-10** | No cell in `compliance.json` is `unanswered` because of a unit that appears in `requirements.json`. Every unit the requirements actually use is either convertible or refused for a stated physical reason (unknown molar mass, gauge-vs-absolute, calendar-vs-clock) — never merely unrecognised. | Task 5 | 1, 2, 3 |

Tasks 1, 2 and 3 own **no** store invariant: `units.py` never writes to the store, and Rule 1 is explicit that anything checkable without loading a snapshot is not a store invariant. Their correctness is defended through INV-10.

Task 4 owns **no new** invariant either. Its store effect — a prompt bump re-extracts exactly that class, once, and persists the new version — is INV-4 and INV-8, already owned by phase 3's Tasks 4 and 7 and already defended by `test_row4_a_prompt_bump_reextracts_only_its_own_class`. Claiming it again would violate Rule 1's "if two tasks name the same invariant, one of them does not own it."

**The nine baseline mutation rows are not re-derived here.** This plan adds no accumulating collection, so rows 1–19 of `tests/test_phase3_lifecycle.py` remain the defence against the phase-2 defect class. Task 5 Step 4c re-runs them and requires them green; a plan that re-implemented them would be duplicating a matrix that already exists.

---

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the
> implementation against the test.
>
> Load-bearing in every sample: that an inexact conversion raises rather than
> approximates; that identical units skip conversion entirely; that every
> assumption reaches the rationale string. Illustrative and expected to change:
> the exact regex for a qualifier, the precise factor table contents, and the
> wording of each rationale.

---

### Task 1: Compare identical units without converting, and strip a measurement condition

**Files:**
- Modify: `procurement/units.py`
- Test: `tests/test_units.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `_strip_qualifier(unit: str | None) -> tuple[str, str]` — `(unit, condition)`, internal.
  - `compare` unchanged in signature; identical units now compare without calling `to_canonical`, and a stripped condition appears in the returned rationale.
- **Store invariant owned: none.** `units.py` writes nothing to the store; Rule 1 forbids inventing an invariant checkable without a snapshot. Defended through INV-10.

Four live cells fail today for the same reason: both sides already state the same unit, and the code converts anyway. `mg/Nm3` vs `mg/Nm3` is the clearest — it is refused for an unknown molar mass while needing no arithmetic at all.

The qualifier rule is second because it is what makes `dB(A) at 1m` fold to `dB(A)` so the identical-unit rule can then fire.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_units.py  (additions)

@pytest.mark.parametrize("unit,expected,condition", [
    ("dB(A) at 1m", "dB(A)", "at 1m"),
    ("kW@ 55 Deg C", "kW", "@ 55 Deg C"),
    ("mg/Nm3@3% O2", "mg/Nm3", "@3% O2"),
    ("mg/Nm3, To 3% O2", "mg/Nm3", ", To 3% O2"),
    ("kW", "kW", ""),
    ("kg.m2", "kg.m2", ""),          # a dot is not a condition
])
def test_a_measurement_condition_is_split_off_the_unit(unit, expected, condition):
    from procurement.units import _strip_qualifier
    got_unit, got_condition = _strip_qualifier(unit)
    assert got_unit == expected
    assert got_condition.strip() == condition.strip()


def test_identical_units_compare_without_any_conversion():
    # both sides say mg/Nm3, so no molar mass is needed and refusing is absurd
    ok, why = compare("<=", 10.0, "mg/Nm3", 10.0, "mg/Nm3",
                      parameter="particulate_matter_limit")
    assert ok is True and "mg/nm3" in why.lower()


def test_identical_units_still_compare_when_the_unit_has_no_family():
    ok, _ = compare(">=", 1250.0, "Amp", 1250.0, "Amp",
                    parameter="generator_breaker_rating")
    assert ok is True


def test_a_condition_is_ignored_for_comparison_and_named_in_the_rationale():
    ok, why = compare("<=", 85.0, "dBA", 85.0, "dB(A) at 1m",
                      parameter="noise_limit")
    assert ok is True
    assert "at 1m" in why           # the reader is told what was dropped


def test_stripping_a_condition_never_unifies_two_different_quantities():
    # kW@55degC folds to kW, which is still not volts
    with pytest.raises(Unconvertible):
        compare("<=", 10.0, "kW", 220.0, "V", parameter="heater_rating")


def test_a_gauge_pressure_never_compares_against_an_absolute_one():
    # barg and bar differ by one atmosphere; treating them as equal would
    # misjudge a fuel gas requirement
    with pytest.raises(Unconvertible):
        compare(">=", 2.76, "barg", 2.76, "bar", parameter="fuel_gas_pressure")


def test_between_also_skips_conversion_for_identical_units():
    ok, _ = compare("between", [2.76, 4.14], "barg", 3.5, "barg",
                    parameter="fuel_gas_pressure")
    assert ok is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_units.py -q -k "condition or identical or gauge"`
Expected: FAIL — `_strip_qualifier` does not exist, and `compare` raises `Unconvertible("unrecognised unit 'Amp'")`.

- [ ] **Step 3: Write minimal implementation**

Load-bearing: identical units skip `to_canonical` entirely; a stripped condition is reported, never silently dropped; cross-unit pairs still go through `_require_same_family` untouched. Illustrative: the regex, and the exact rationale wording.

```python
# procurement/units.py  (additions)

# A unit is sometimes printed with the condition it was measured under:
# "dB(A) at 1m", "kW@ 55 Deg C", "mg/Nm3, To 3% O2". The condition is not part
# of the unit, but it is not noise either - it is dropped for the comparison
# and quoted in the rationale so a reviewer sees what was ignored.
_QUALIFIER = re.compile(r"\s*(?:@|,|\bat\b).*$", re.IGNORECASE)


def _strip_qualifier(unit: str | None) -> tuple[str, str]:
    text = (unit or "").strip()
    match = _QUALIFIER.search(text)
    if match is None:
        return text, ""
    return text[:match.start()].strip(), match.group(0).strip()


def _same_unit(req_unit, fact_unit) -> bool:
    """True when both sides print the same unit, however spelled."""
    return _fold(req_unit) == _fold(fact_unit)
```

`_fold` gains one line at the top, so every existing caller benefits:

```python
def _fold(unit: str | None) -> str:
    spaced, _condition = _strip_qualifier(unit)
    spaced = spaced.lower()
    if spaced in _ALIASES:
        return _ALIASES[spaced]
    squeezed = spaced.replace(" ", "")
    return _ALIASES.get(squeezed, squeezed)
```

And `compare` grows one branch, used by both the scalar and the `between` path:

```python
def _bring_together(lhs, rhs, req_unit, fact_unit, parameter):
    """Return (lhs_canonical, rhs_canonical, unit_name, condition_note).

    Identical units are the whole point: two numbers already in the same unit
    need no arithmetic, so no conversion can refuse them.
    """
    conditions = [c for c in (_strip_qualifier(req_unit)[1],
                              _strip_qualifier(fact_unit)[1]) if c]
    note = f" (ignoring {', '.join(repr(c) for c in conditions)})" if conditions else ""
    if _same_unit(req_unit, fact_unit):
        return lhs, rhs, _fold(req_unit), note
    _require_same_family(req_unit, fact_unit, parameter)
    lhs_c, canonical = to_canonical(lhs, req_unit, parameter)
    rhs_c, _ = to_canonical(rhs, fact_unit, parameter)
    return lhs_c, rhs_c, canonical, note
```

Both paths in `compare` then call `_bring_together` instead of calling
`_require_same_family` and `to_canonical` themselves, and append `note` to the
rationale alongside the existing assumed-unit note.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_units.py tests/test_compliance.py -v`
Expected: PASS — including every existing units test, which is what proves the refactor did not move the family rules.

- [ ] **Step 5: Commit**

```bash
git add procurement/units.py tests/test_units.py && git commit -m "fix: compare identical units without converting, and split off a measurement condition"
```

---

### Task 2: The unit families the real corpus actually prints

**Files:**
- Modify: `procurement/units.py`
- Test: `tests/test_units.py`

**Interfaces:**
- Consumes: Task 1's `_fold`.
- Produces: no new functions. `_SCALAR`, `_CANONICAL` and `_ALIASES` gain the families below; `to_canonical` and `_family` pick them up with no change.
- **Store invariant owned: none** — same reason as Task 1. Defended through INV-10.

Every family here is one the live corpus prints in a requirement, with the cell count it converts. Nothing speculative is added: a family nobody states is a factor nobody has checked.

| family | canonical | members | live cells |
|---|---|---|---|
| `percent` | `%` | `%`, `percent`, `pct` | 8 |
| `calendar` | `months` | `months` (1), `years` (12) | 6 |
| `noise` | `db(a)` | `db(a)`, `dba`, `db (a)` | 4 |
| `length` | `m` | `m` (1), `mm` (0.001), `cm` (0.01), `km` (1000) | 1 |
| `time` | `s` | `s` (1), `ms` (0.001), `min` (60), `h` (3600) | 1 |
| `current` | `a` | `a` (1), `ma` (0.001), `ka` (1000) | 1 |
| `pressure_gauge` | `barg` | `barg` (100), `kpag` (1), `psig` (6.894757) | 1 |
| `mass` | `kg` | `kg` (1), `g` (0.001), `t` (1000) | 0 in this run; the previous run's `weight == 28000 kg` needed it |
| `weeks` | `weeks` | `weeks` | 0; exists **so it refuses**, see judgment call 3 |

Aliases: `amp`/`amps` → `a`; `vac`/`vdc`/`volts ac` → `v`; `meters`/`metre`/`metres` → `m`; `sec`/`secs`/`seconds` → `s`; `hours`/`hrs` → `h`; `year`/`yrs` → `years`; `month` → `months`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_units.py  (additions)

@pytest.mark.parametrize("value,unit,expected,canonical", [
    (110.0, "%", 110.0, "%"),
    (12.0, "months", 12.0, "months"),
    (1.0, "years", 12.0, "months"),
    (1000.0, "m", 1000.0, "m"),
    (1000.0, "mm", 1.0, "m"),
    (3.0, "s", 3.0, "s"),
    (3.0, "ms", 0.003, "s"),
    (1250.0, "Amp", 1250.0, "a"),
    (1.0, "kA", 1000.0, "a"),
    (230.0, "VAC", 230.0, "v"),
    (85.0, "dBA", 85.0, "db(a)"),
    (2.76, "barg", 276.0, "barg"),
    (28000.0, "kg", 28000.0, "kg"),
])
def test_the_new_families_scale_to_their_canonical_unit(value, unit, expected, canonical):
    got, got_canonical = to_canonical(value, unit)
    assert got == pytest.approx(expected, rel=1e-6)
    assert got_canonical == canonical


def test_a_year_is_twelve_months_but_a_month_is_not_any_number_of_hours():
    # exact, so it converts
    assert to_canonical(2.0, "years")[0] == pytest.approx(24.0)
    # not exact, so it refuses rather than approximating
    with pytest.raises(Unconvertible):
        compare("<=", 12.0, "months", 8760.0, "h", parameter="warranty_period")


def test_weeks_never_silently_become_months():
    with pytest.raises(Unconvertible):
        compare(">=", 6.0, "months", 26.0, "weeks", parameter="preservation")


def test_a_percentage_is_not_dimensionless():
    # a dimensionless count must never compare against a percentage
    with pytest.raises(Unconvertible):
        compare(">=", 3.0, "", 110.0, "%", parameter="black_starts")


def test_the_live_percentage_requirement_now_compares():
    # sustained_overload_current >= 110 %, vendor states the number bare
    ok, why = compare(">=", 110.0, "%", 300.0, None,
                      parameter="sustained_overload_current")
    assert ok is True and "assumed" in why.lower()


def test_the_live_warranty_requirement_now_compares():
    ok, _ = compare("==", 12.0, "months", 12.0, "Months",
                    parameter="warranty_period")
    assert ok is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_units.py -q -k "families or year or weeks or percentage or live_"`
Expected: FAIL — `Unconvertible: unrecognised unit '%'` and the same for each new family.

- [ ] **Step 3: Write minimal implementation**

Load-bearing: `percent` and `weeks` are separate families **so that they refuse**; calendar units convert only among themselves. Illustrative: the alias spellings, which will keep growing as more corpora arrive.

```python
# procurement/units.py  (additions to the existing tables)

_SCALAR.update({
    "%": ("percent", 1.0),
    "months": ("calendar", 1.0), "years": ("calendar", 12.0),
    # weeks is its own family on purpose: 4.348 weeks/month is an average,
    # not a conversion, and a warranty is not an average.
    "weeks": ("weeks", 1.0),
    "db(a)": ("noise", 1.0),
    "m": ("length", 1.0), "mm": ("length", 0.001),
    "cm": ("length", 0.01), "km": ("length", 1000.0),
    "s": ("time", 1.0), "ms": ("time", 0.001),
    "min": ("time", 60.0), "h": ("time", 3600.0),
    "a": ("current", 1.0), "ma": ("current", 0.001), "ka": ("current", 1000.0),
    # gauge pressure is one atmosphere away from absolute; see judgment call 4
    "barg": ("pressure_gauge", 100.0), "kpag": ("pressure_gauge", 1.0),
    "psig": ("pressure_gauge", 6.894757),
    "kg": ("mass", 1.0), "g": ("mass", 0.001), "t": ("mass", 1000.0),
})
_CANONICAL.update({
    "percent": "%", "calendar": "months", "weeks": "weeks", "noise": "db(a)",
    "length": "m", "time": "s", "current": "a", "pressure_gauge": "barg",
    "mass": "kg",
})
_ALIASES.update({
    "amp": "a", "amps": "a", "ampere": "a", "amperes": "a",
    "vac": "v", "vdc": "v", "volts ac": "v", "volts dc": "v",
    "meters": "m", "metres": "m", "metre": "m", "meter": "m",
    "sec": "s", "secs": "s", "seconds": "s", "second": "s",
    "hours": "h", "hour": "h", "hrs": "h", "hr": "h",
    "year": "years", "yrs": "years", "yr": "years", "month": "months",
    "week": "weeks", "percent": "%", "pct": "%",
    "dba": "db(a)", "db (a)": "db(a)", "dbа": "db(a)",
})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_units.py tests/test_compliance.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add procurement/units.py tests/test_units.py && git commit -m "feat: the unit families the real RFQ corpus states"
```

---

### Task 3: Non-numeric equality

**Files:**
- Modify: `procurement/units.py`
- Test: `tests/test_units.py`

**Interfaces:**
- Consumes: Task 1's `_fold`.
- Produces: `compare("==", ...)` returns a verdict when neither side is numeric, instead of raising.
- **Store invariant owned: none** — same reason as Task 1. Defended through INV-10.

Seven live cells are `winding_insulation_class == H` against a vendor stating `Class H`. Today `compare` raises on the *requirement* value not being a number: the existing string fallback only fires when the **vendor** value is non-numeric, so a class-against-class comparison can never be made at all.

Exact equality fails `H` against `Class H`. Substring matching would pass `F` against `Class F Rise` for the wrong reason, and would pass `H` against `Not H`. Token-subset is the narrowest rule that gets the corpus right: the requirement's tokens must all appear in the vendor's.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_units.py  (additions)

def test_a_class_requirement_matches_the_vendor_stating_it_with_context():
    ok, why = compare("==", "H", "", "Class H", "", parameter="insulation_class")
    assert ok is True and "Class H" in why


def test_a_class_requirement_fails_a_different_class():
    ok, _ = compare("==", "H", "", "Class F", "", parameter="insulation_class")
    assert ok is False


def test_a_non_numeric_equality_is_case_and_punctuation_insensitive():
    assert compare("==", "IP 55", "", "ip55", "", parameter="ip_rating")[0] is True


def test_a_non_numeric_equality_needs_every_requirement_token():
    # "IP 55" is not satisfied by "IP 23"
    assert compare("==", "IP 55", "", "IP 23", "", parameter="ip_rating")[0] is False


def test_an_empty_requirement_value_raises_rather_than_matching_everything():
    with pytest.raises(Unconvertible):
        compare("==", "   ", "", "Class H", "", parameter="insulation_class")


def test_a_numeric_equality_is_unaffected_by_the_token_path():
    assert compare("==", 50, "Hz", 50.0, "Hz", parameter="frequency")[0] is True
    assert compare("==", 50, "Hz", 60.0, "Hz", parameter="frequency")[0] is False


def test_a_non_numeric_value_on_an_ordering_operator_still_raises():
    # ">= H" has no meaning; only equality has a non-numeric reading
    with pytest.raises(Unconvertible):
        compare(">=", "H", "", "Class H", "", parameter="insulation_class")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_units.py -q -k "class_requirement or non_numeric_equality or empty_requirement"`
Expected: FAIL — `Unconvertible: requirement value 'H' is not a number`.

- [ ] **Step 3: Write minimal implementation**

Load-bearing: only `==` gets the non-numeric reading; an empty requirement raises rather than matching everything; the rationale quotes both sides so a reviewer can check the match by eye. Illustrative: the tokeniser's regex.

```python
# procurement/units.py

def _tokens(value) -> frozenset[str]:
    return frozenset(t for t in _NOISE_SPLIT.split(str(value).lower()) if t)


# in compare(), replacing the current `if rhs is None:` branch:
    if lhs is None or rhs is None:
        if operator != "==":
            # ">= H" has no reading; only equality can be non-numeric
            missing = req_value if lhs is None else fact_value
            raise Unconvertible(
                f"{'requirement' if lhs is None else 'vendor'} value "
                f"{missing!r} is not a number")
        want, got = _tokens(req_value), _tokens(fact_value)
        if not want:
            raise Unconvertible("the requirement states no value to compare")
        # every token the requirement states must appear in the vendor's
        # answer: "H" is satisfied by "Class H", "IP 55" is not by "IP 23"
        return want <= got, (f"{fact_value!r} against required {req_value!r} "
                             f"(matched on {sorted(want)})")
```

`_NOISE_SPLIT` is `re.compile(r"[^a-z0-9]+")` — the same splitting rule `_substance` already uses, so one idea has one spelling.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_units.py tests/test_compliance.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add procurement/units.py tests/test_units.py && git commit -m "feat: compare a non-numeric equality by token instead of refusing it"
```

---

### Task 4: `requirements_v3` — stop emitting `==` for a stated maximum

**Files:**
- Create: `shared/llm/prompts/requirements_v3.txt`
- Modify: `procurement/extract_requirements.py:18-20`
- Modify: `tests/test_phase3_lifecycle.py` (the row-4 sentinel)
- Test: `tests/test_extract_requirements.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `REQUIREMENTS_PROMPT_VERSION = "requirements_v3"`; `RFQ_PROMPT_VERSION_BY_CLASS` picks it up with no edit, because it is built from the constant.
- **Store invariant owned: none new.** The store effect of a prompt bump — exactly that class re-extracts, once, and the new version persists — is INV-4 and INV-8, owned by phase 3's Tasks 4 and 7 and defended by `test_row4_a_prompt_bump_reextracts_only_its_own_class`. Rule 1 forbids claiming it twice.

The live corpus shows the same clause extracted twice from two duplicate MR copies: once as `h2s_content <= 700 ppm` (pass, correct) and once as `== 700 ppm` (fail, wrong — the vendor's 50 ppm is *better* than required). `rated_power == 525 kW` against a 590 kW offer is the same shape. `==` on a clause that states a limit manufactures a false FAIL, which is the outcome this codebase treats as indefensible.

This is prompt guidance, not arithmetic. `units.py` is doing exactly what it was told.

**A trap this task must not spring:** `tests/test_phase3_lifecycle.py` patches `REQUIREMENTS_PROMPT_VERSION` to `"requirements_v3"` to prove a bump forces re-extraction. Once the shipped constant *is* `requirements_v3`, that patch becomes a no-op and the row silently stops testing anything. Step 3 changes the sentinel to `"requirements_v4"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extract_requirements.py  (additions)

def test_the_prompt_version_is_the_prompt_filename():
    assert REQUIREMENTS_PROMPT_VERSION == "requirements_v3"


def test_the_prompt_tells_the_model_which_operator_a_limit_takes():
    # the prompt is the fix here, so the prompt is what the test inspects
    from procurement.extract_requirements import _PROMPT
    text = _PROMPT.read_text(encoding="utf-8").lower()
    assert "maximum" in text and "minimum" in text
    assert "<=" in text and ">=" in text
    # and it must say what == is reserved for
    assert "exact" in text


def test_a_maximum_clause_extracted_as_a_limit_is_stored_as_one(tmp_path):
    entry = {"clause_ref": "2.5.1", "text": "H2S content up to 700 ppm",
             "category": "technical", "checkability": "auto",
             "parameter": "h2s_content", "operator": "<=", "value": 700,
             "unit": "ppm"}
    [record], status, _ = extract_requirements(
        "d1", _spec(tmp_path), StubClient({"requirements": [entry]}))
    assert (status, record.operator, record.value) == ("ok", "<=", 700)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_extract_requirements.py -q -k "prompt"`
Expected: FAIL — the constant is still `requirements_v2` and `requirements_v3.txt` does not exist.

- [ ] **Step 3: Write minimal implementation**

Copy `requirements_v2.txt` to `requirements_v3.txt` and add the operator-choice rule below to its `Rules:` block. Load-bearing: that `==` is reserved for an exactly-stated value. Illustrative: the examples.

```
- Choose the operator from what the clause requires, not from how it reads:
    "maximum 58 deg C", "up to 700 ppm", "not exceeding 85 dB(A)"  -> <=
    "minimum 525 kW", "at least 3 starts", "no less than 110 %"    -> >=
    "5-58 deg C", "between 2.76 and 4.14 barg"                     -> between
    "50 or 60 Hz", "415V / 690V"                                   -> in
    "exactly 415 V", "shall be 50 Hz"                              -> ==
  Use == only when the clause requires that exact value and any other value -
  higher or lower - would be non-compliant. A limit is never ==: writing
  "H2S up to 700 ppm" as "== 700" fails a vendor offering 50 ppm, which meets
  the requirement comfortably.
```

Then in `procurement/extract_requirements.py`:

```python
REQUIREMENTS_PROMPT_VERSION = "requirements_v3"
_PROMPT = (Path(__file__).parents[1] / "shared" / "llm" / "prompts"
           / "requirements_v3.txt")
```

And in `tests/test_phase3_lifecycle.py`, the row-4 sentinel — the bumped value must differ from the shipped one or the patch tests nothing:

```python
    ("REQUIREMENTS_PROMPT_VERSION", "requirements", _MR, "requirements_v4"),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_extract_requirements.py tests/test_pipeline_rfq.py tests/test_phase3_lifecycle.py -v`
Expected: PASS — `test_pipeline_rfq.py` and `test_phase3_lifecycle.py` assert the version string in five places; update each to `requirements_v3`.

- [ ] **Step 5: Commit**

```bash
git add shared/llm/prompts/requirements_v3.txt procurement/extract_requirements.py tests/ && git commit -m "fix: tell the extractor a stated limit is <= or >=, never =="
```

---

### Task 5: Integration — the coverage metric means what it says

**Files:**
- Test: `tests/test_compliance_coverage.py` (create)
- Modify: none — `compliance.py` recomputes wholesale, so no wiring changes.

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: no new production code. This task is the store-level assertion that the previous four add up.
- **Store invariant owned (INV-10):** no cell in `compliance.json` is `unanswered` because of a unit that appears in `requirements.json`. Every unit the requirements actually use is either convertible or refused for a stated physical reason — never merely unrecognised.

INV-10 is the whole plan in one sentence. It is deliberately phrased over stored state and over *both* snapshots at once: it can only be checked by loading `requirements.json` and `compliance.json` together, which is exactly the kind of cross-collection claim Rule 1 exists to force somebody to own.

The distinction it draws is between two refusals that look identical in the data and are opposites in meaning:

| rationale | meaning | acceptable? |
|---|---|---|
| `unrecognised unit '%'` | we cannot read the corpus | **no** — INV-10 violation |
| `cannot compare 'barg' with 'bar'` | we read both and they are different quantities | yes, with the reason |
| `the molar mass ... is unknown` | converting would need a fact nobody stated | yes, with the reason |
| `no vendor document stated 'x'` | the vendor was silent | yes — this is the metric |

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compliance_coverage.py
import pytest

from procurement.compliance import evaluate_project
from procurement.project import create_project, load_project, save_project
from procurement.store import snapshots
from procurement.store.models import (RequirementRecord, RequirementSet,
                                      VendorFacts, req_id_for)

NOW = "2026-07-31T00:00:00+00:00"

# one row per unit the live corpus states in a requirement, with the vendor
# answer it was actually compared against
_CORPUS = [
    ("sustained_overload_current", ">=", 110.0, "%", 300.0, None, "pass"),
    ("warranty_period", "==", 12.0, "months", 12.0, "Months", "pass"),
    ("noise_limit", "<=", 85.0, "dBA", 85.0, "dB(A) at 1m", "pass"),
    ("fuel_gas_pressure", ">=", 2.76, "barg", 3.5, "barg", "pass"),
    ("generator_breaker_rating", ">=", 1250.0, "Amp", 1250.0, "Amp", "pass"),
    ("particulate_matter_limit", "<=", 10.0, "mg/Nm3", 10.0, "mg/Nm3", "pass"),
    ("altitude", "<=", 1000.0, "m", 900.0, None, "pass"),
    ("short_circuit_withstand_time", "==", 3.0, "s", 3.0, None, "pass"),
    ("battery_charger_input_voltage", "==", 230.0, "VAC", 230.0, "V", "pass"),
    ("rated_capacity", "==", 525.0, "kW", 550.0, "kW@ 55 Deg C", "fail"),
    ("winding_insulation_class", "==", "H", "", "Class H", "", "pass"),
    # refusals that must survive, each for a stated physical reason
    ("heater_rating", "<=", 10.0, "kW", 220.0, "V", "unanswered"),
    ("preservation_duration", ">=", 6.0, "months", 26.0, "weeks", "unanswered"),
]


def _project(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    project = load_project(root, "p")
    project.vendors = ["KERUI"]
    save_project(root, project)

    requirements, facts = [], []
    for i, (param, op, value, unit, fact_value, fact_unit, _) in enumerate(_CORPUS):
        clause = f"{i}.1"
        requirements.append(RequirementRecord(
            req_id=req_id_for("d1", clause), clause_ref=clause,
            text=f"{param} {op} {value} {unit}", checkability="auto",
            parameter=param, operator=op, value=value, unit=unit,
            source_doc_id="d1"))
        facts.append({"fact_id": f"f-{i}", "parameter": param,
                      "value": fact_value, "unit": fact_unit,
                      "verbatim": f"{fact_value} {fact_unit}", "doc_id": "d9"})
    snapshots.save_requirements(root, "p", RequirementSet(requirements=requirements))
    snapshots.save_facts(root, "p", VendorFacts(vendor="KERUI", technical=facts))
    return root


@pytest.mark.parametrize("param,op,value,unit,fact_value,fact_unit,expected", _CORPUS)
def test_each_live_unit_reaches_its_expected_verdict(
        tmp_path, param, op, value, unit, fact_value, fact_unit, expected):
    root = _project(tmp_path)
    cells = {c.req_id: c for c in evaluate_project(root, "p", now=NOW)}
    index = [row[0] for row in _CORPUS].index(param)
    cell = cells[req_id_for("d1", f"{index}.1")]
    assert cell.verdict == expected, cell.rationale


def test_no_cell_is_unanswered_merely_because_a_unit_was_unrecognised(tmp_path):
    # INV-10, over both loaded snapshots
    root = _project(tmp_path)
    evaluate_project(root, "p", now=NOW)
    reqs = snapshots.load_requirements(root, "p").requirements
    stated = {r.unit for r in reqs if r.checkability == "auto"}
    for cell in snapshots.load_compliance(root, "p"):
        assert "unrecognised unit" not in cell.rationale, (
            f"{cell.rationale} - but the requirements state {sorted(stated)}")


def test_a_surviving_refusal_always_says_why(tmp_path):
    root = _project(tmp_path)
    evaluate_project(root, "p", now=NOW)
    for cell in snapshots.load_compliance(root, "p"):
        if cell.verdict != "unanswered":
            continue
        assert any(reason in cell.rationale for reason in (
            "no vendor document stated", "different quantities",
            "molar mass", "is not a number", "states no value")), cell.rationale


def test_the_unanswered_share_is_only_what_the_vendor_did_not_state(tmp_path):
    root = _project(tmp_path)
    cells = evaluate_project(root, "p", now=NOW)
    unanswered = [c for c in cells if c.verdict == "unanswered"]
    assert len(unanswered) == 2          # the two deliberate refusals, no more
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_compliance_coverage.py -v`
Expected: FAIL before Tasks 1–3 land — most rows are `unanswered` with `unrecognised unit`. Run it **before** starting Task 1 to see the red, then again after each task to watch rows go green one family at a time.

- [ ] **Step 3: Write minimal implementation**

None. If any row is still red after Tasks 1–3, the defect is in those tasks and belongs there — do not add a special case here.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_compliance_coverage.py -v`
Expected: PASS

- [ ] **Step 4b: Two-run mutation matrix**

This plan adds no accumulating collection, so it inherits rather than re-derives the nine baseline rows. Three rows are new, one test each in `tests/test_phase3_lifecycle.py`, each asserting over a loaded snapshot after run 2.

| # | mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|---|
| 21 | a requirement's unit changes from one this build cannot read to one it can (`furlongs` → `%`) | INV-10 | the cell moves from `unanswered` to a verdict, its `evaluated_at` is at or after run 2's `run.started`, and no cell mentions `unrecognised unit` |
| 22 | a requirement's operator changes from `==` to `<=` on re-extraction under a bumped prompt | INV-7, INV-10 | the cell's verdict changes from `fail` to `pass`, and no verdict computed from the old operator survives in `compliance.json` |
| 23 | a vendor restates a value in a unit of a different family (`barg` → `bar`) | INV-10 | the cell becomes `unanswered` with `different quantities` in the rationale — never `pass`, never `fail`, and never `unrecognised unit` |

- [ ] **Step 4c: Verify the matrix is real, not decorative**

Reinstate each defect one at a time, confirm the intended row fails and nothing else does, then delete the throwaway script — the same procedure phase 3's Step 4c used.

| row | defect to reinstate |
|---|---|
| 21 | drop the new families from `_SCALAR` |
| 22 | make the prompt-version constant static again so run 2 does not re-extract |
| 23 | fold `barg` into the `pressure` family instead of `pressure_gauge` |
| INV-10 | make `_same_unit` always return `False` |

Then run the full phase-3 matrix and require rows 1–20 still green:

```bash
python -m pytest tests/test_phase3_lifecycle.py -v
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_compliance_coverage.py tests/test_phase3_lifecycle.py && git commit -m "test: the coverage metric measures the vendor's silence, not our unit table"
```

---

## Verification of done-criteria

- [ ] `python -m pytest` from the repo root. Expected: the 486-passed baseline **plus** every test added here; the one documented portal failure remains. Anything else is a real regression.
- [ ] `tests/test_phase3_lifecycle.py` rows 1–20 still green — this plan changes verdicts, not storage, and must not disturb the phase-3 invariants.
- [ ] No `compliance.json` cell in the coverage fixture carries `unrecognised unit` (INV-10).
- [ ] **Live re-run, following the same procedure as 2026-07-31.** `LLM_MAX_TOKENS=32000`, `processed-data/01-client-mr-rfq/` plus `02-vendor-bids/KERUI/`, reusing the prior store so only re-extracted classes cost a call. Record the new verdict split and compare against the recorded baseline:

  | | 2026-07-31 baseline | target |
  |---|---|---|
  | pass (of auto cells) | 45.6% | higher |
  | fail (of auto cells) | 4.9% | lower — the `==`-as-limit false fails should go |
  | unanswered (of auto) | 47.6% | **substantially lower** |
  | unanswered that are our own gaps | 35 of 49 | **≤ 5** |
  | unanswered that are genuine | 14 of 49 | roughly unchanged — this is the real signal |

  The last two rows are the point. The headline percentage moving is not success on its own; the composition changing is.

## Deliberately out of scope

- **Deduplicating the three MR copies, and scoping deviations by document.** These are one piece of work, not two, and they are deliberately paired: 113 clause refs appear in more than one document, covering 352 of 533 requirements, and that is *why* KERUI's single deviation lands on four requirements at once. Measured on the live store, scoping deviations alone would remove three wrong labels (whose fallbacks are `unanswered`, `review`, `review` — all low-information) and destroy the one right one: `ambient_design_temp_max` would drop from `deviation`, the most informative verdict in the matrix, to a bare `fail` that hides that the gap is declared and under discussion. Deduplication first makes the scoping rule almost never fire; scoping first makes the corpus worse. Deduplication also needs a decision this plan cannot make from the data — whether `Copy of …_client_MR_only.xlsx` is a duplicate of the full MR or a deliberate subset — so it needs its own spec.
- **The `mom` misclassification.** `00 MOM 20241111 ASTRA ADPOWER.pdf` is a datasheet comparison table that the filename rules call a MOM. Fixing it means changing `classify.py`'s `_RULES`, which phase 3 explicitly froze.
- **Vendor-stated ranges.** `battery_charger_input_voltage == 230 VAC` against a vendor's `AC110V-250v` reads as 110 and fails, though 230 is inside the range the vendor offers. Requirements can express a range since `requirements_v2`; facts cannot. That is a `tech_facts` schema change.
- **`%`-against-absolute requirements.** Two live cells state `excitation_rated_current >= 110 %` against a fact of `957 A`. No unit table can reconcile a percentage with an absolute, because the base is not stated. These stay `unanswered` and correctly so.

## Checklist before this plan is approved

- [x] Every task's `Interfaces` block has a **Store invariant owned** bullet. Tasks 1–3 declare **none** with the Rule 1 reason (`units.py` writes nothing); Task 4 declares **none new** and names the existing owner.
- [x] No invariant is claimed twice. INV-10 is new and owned only by Task 5; the prompt-bump effect is left with phase 3's INV-4/INV-8 rather than re-claimed.
- [x] The integration task carries a mutation matrix. The nine baseline rows are inherited from `tests/test_phase3_lifecycle.py` — stated explicitly, with a step that requires them green — because this plan adds no accumulating collection. Three new rows cover what it does change.
- [x] Every matrix row names an invariant; INV-10 has three rows.
- [x] The Rule 3 banner appears above the first reference block.
- [x] Each reference block states which parts are load-bearing and which are illustrative.
