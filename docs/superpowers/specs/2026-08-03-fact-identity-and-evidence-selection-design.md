# Design: Fact Identity and Evidence Selection

Status: approved (brainstormed 2026-08-03)
Follows: `docs/superpowers/plans/2026-08-02-extraction-coverage-phase4.md` — this
is that phase's parked **Important 4**, bundled with Task 7c's carried-forward
`compliance.py` observation, as its final review directed.
Branch: `updated-information-extractor`, from `69ab9f7`.

---

## 1. Context and goal

Phase 4's Task 7c changed the technical extractor's dedup key from the parameter
to the whole fact, deliberately, so that a datasheet genuinely stating one
parameter twice with different numbers keeps both readings. Its own comment
(`procurement/extract_tech.py:102-109`) states the reason: dropping the second
"would hand `compliance.py` whichever the model happened to print first."

The extractor kept both readings. Nothing downstream learned to tell them apart.

`fact_id_for(doc_id, parameter)` keys on the parameter alone, so the two readings
share one id, and three call sites resolve an address with `next(...)` over a list
whose members are not uniquely keyed:

| site | consequence |
|---|---|
| `procurement/store/overrides.py:65`, `:73` | an override aimed at the 700 kW reading lands on the 525 kW record, and `reconcile` reports no conflict |
| `procurement/compliance.py:143` (`stated`) | the text verdict is computed against whichever reading printed first |
| `procurement/compliance.py:197` (`auto`) | the arithmetic verdict is computed against whichever reading printed first |

One cause, three symptoms. The phase-4 final review ruled that fixing any one of
them without the others "buys little," and scheduled the set to be fixed **before
any operator starts overriding technical facts**.

### That window is still open, and the defect is live

Measured across every store under `projects/` at the time of writing:

| store | technical facts | duplicate `fact_id`s | stored overrides |
|---|---|---|---|
| `phase4b-acceptance` | 973 | 101 | **0** |
| `phase4-acceptance` | 621 | 59 | **0** |
| `coverage-floor-t7` | 496 | 51 | **0** |
| `phase4c-shipped-defaults` | 824 | 0 | **0** |

No store holds a single override, so an id-scheme change orphans no human
decision. (`phase4c`'s zero is a property of the retired parameter-only dedup
rule it was ingested under, not of `HEAD`.)

Meanwhile the decision layer is already producing a wrong answer, not merely a
fragile one. On `coverage-floor-t7`, ADPOWER states `rated_power` at both
`700 kW` and `525 kW`; the cell currently reads **`fail`**. A vendor is being
failed today on print order. The ledger recorded this case as a `pass` risk; it
is worse than that.

### Success criteria

- No override resolves to a record other than the one its `field_path` names.
- No compliance verdict depends on the order two readings were printed in.
- A parameter with two genuinely different live readings is never auto-decided.
- Readings that are the same quantity written twice (`525 kW`, `525000 W`) do not
  escalate.
- Every snapshot already on disk still validates without migration.
- The four floors in `tests/test_real_corpus_coverage.py` stay green at shipped
  defaults, unlowered.

---

## 2. Decisions locked (from brainstorming)

1. **Two distinct readings are `review`, never an auto-decision.** Not
   best-reading-wins, not strictest-reading-wins, not a document-class precedence
   table. 525 kW and 700 kW on one datasheet usually means the two numbers are
   scoped differently — prime versus standby — and the document does not say
   which governs. Any rule that picks one invents a fact the source never stated.
   This is the same argument `compliance.py` already makes at its `review`-not-
   `fail` branch, and the same one `2026-07-31-compliance-correctness-design.md`
   §2.3 made in refusing to auto-resolve duplicate RFQ documents.
2. **`fact_id_for` widens to the whole reading**, mirroring `deviation_id_for`.
   An occurrence ordinal was rejected: it is document order, which is not stable
   across re-extraction, and it re-creates exactly the index-selector problem
   `overrides.py:_parse` already rejects outright. A second, parallel addressing
   scheme was rejected for leaving `_walk`'s ambiguity in place, merely narrowed.
3. **The rule spans documents, not just one document.** `evaluate` receives one
   flat fact list per vendor, so first-match-wins already chooses silently
   between a datasheet's number and a quotation's number. A datasheet-outranks-
   quotation precedence rule is the same class of invented fact as decision 1's
   rejected options.
4. **Unit-equivalent readings collapse before the count.** Without this the rule
   fires on agreement that only looks like disagreement.
5. **Presence-only `stated` requirements are exempt from the count**, but run the
   negation guard across every candidate. See §5.

---

## 3. Component: `fact_id_for` keys on the whole reading

`procurement/store/models.py`

```
fact_id_for(doc_id, parameter, value, unit) -> str
```

The value is normalised before hashing so that `525.0` and `"525"` mint one id
rather than two — closing Task 7c's other deferred minor (`fact.value` is
`str|float|None`, so two chunks formatting one number differently produced two
keys) as a consequence of this change rather than leaving it open.

Normalisation is specified, not left to the implementer: a value that parses as a
pure number is keyed on that number's canonical form; anything else is keyed on
the stripped, lowercased string, exactly as `deviation_id_for` already treats its
statement. The unit is folded the same way. Deliberately *not* unit-converted —
`525 kW` and `525000 W` are two readings that a document printed differently and
that an operator may want to override separately; collapsing them is a decision
for the compliance layer (§4), where it is reversible, not for the identity
layer, where it would be baked into the store.

This is a **signature change**: `fact_id_for` gains two required arguments, so
its callers in `procurement/extract_tech.py` and in `tests/test_store_fact_models.py`
and `tests/test_extract_tech.py` move with it. There is one production caller.

The docstring inherits `deviation_id_for`'s framing, because it is the same
trade: a re-extraction that rewords a value shifts the id and orphans an
override, which is strictly better than an override silently retargeting the
wrong row. The orphan surfaces — `reconcile` flags it, `apply_overrides` logs it.

`procurement/store/overrides.py` **is not changed.** `_walk`'s `next(...)`
becomes correct by construction once ids are unique. Adding a second selector
form would be treating the symptom.

`procurement/extract_tech.py:115-128` passes the value and unit it already has,
and its comment — which currently explains why the collision is *not* this
extractor's to fix — is replaced by one describing the key it now mints.

---

## 4. Component: candidate selection in `compliance.py`

`evaluate` gathers **all** facts whose normalised parameter matches, instead of
the first. Then:

1. **Collapse equivalents.** Readings are grouped by `units.to_canonical(...)`
   where the value is numeric and the unit convertible; anything else falls back
   to exact comparison, so two textually different strings stay distinct. `525 kW`
   and `525000 W` are one reading.
2. **One reading survives** → today's behaviour exactly. Every existing path,
   rationale and verdict is unchanged. This is the overwhelmingly common case.
   When that one reading was collapsed from several equivalents, the cell cites
   the first in document order as `fact_id` and lists the equivalents in
   `candidate_fact_ids` — the choice is arbitrary but harmless, because by
   construction the group members compare identically; recording the others keeps
   the cell honest about what it read.
3. **Two or more survive** → `review`, citing every reading with its `doc_id`.
   Never `fail`, consistent with the module's spine: a comparison that cannot
   know what it does not know must not blame a vendor.

The rationale names the parameter and lists each reading with its value, unit and
source document, so a reviewer can act on the cell without opening the store.

---

## 5. Which tiers the rule applies to

| shape | multi-reading rule | why |
|---|---|---|
| `auto` | applies | two numbers, one comparison; the arithmetic is undefined until a human picks |
| `stated`, `requirement.value` set | applies | two different stated values genuinely conflict about the thing asked |
| `stated`, `requirement.value is None` (presence-only) | **exempt** | two readings both satisfy presence; they do not disagree about the question |
| `judgement` | not applicable | already `review`, and already lists candidate facts |

The presence-only exemption is not a softening — it is what keeps the rule
pointed at conflicts. Measured on `coverage-floor-t7`, the parameters with the
most readings are enumerations, not disagreements: `applicable_standard` with 18
readings, `attachment` with 9, `country_of_origin` with 7. A vendor listing 18
applicable standards is answering the question, at length. Escalating those would
spend reviewer attention on nothing and would discredit the escalation everywhere
else.

**Presence-only cells still gain a fix.** Today `_negations_in` only ever
inspects the one reading `next(...)` returned, so a vendor stating a parameter
twice can hide a refusal behind a compliant-looking first print — the fix wave's
own Important 1 leaking through this gap. Under this design the negation guard
runs across **every** candidate, and any candidate carrying a refusal downgrades
the cell to `review`.

---

## 6. Component: carrying the citation

`ComplianceResult.fact_id` keeps its current meaning untouched, because
`procurement/matrix.py` depends on it: that module's docstring records that it
decomposes `unanswered` on `fact_id` specifically to avoid putting the wording of
`units.py`'s messages on the critical path of a number a reviewer acts on.

A new field carries the rest:

```
ComplianceResult.candidate_fact_ids: list[str] = []
```

Empty default, so every snapshot already on disk validates unchanged — the same
backward-compatibility rule the phase-4 review verified field by field. Mirrored
into `matrix.MatrixCell`, `web/src/types.ts`, and rendered by
`web/src/pages/ComplianceMatrix.tsx` and `portal/views/compliance.py`.

---

## 7. Existing stores

No migration. Concretely:

- **Overrides**: none exist anywhere, so none orphan.
- **`compliance.json`**: recomputed wholesale on every call, by
  `compliance.py`'s own design ("there is no way for a verdict to outlive the
  requirement or the fact it was computed from"). Stale cells cannot survive.
- **`facts.json`**: old snapshots keep their old ids until re-extracted. They are
  internally consistent, and with no overrides nothing external addresses them.
- **`index/store.db`**: derived and disposable, per the store invariants.

---

## 8. Invariants this must not move

- Every write goes through `procurement/store/snapshots.py`; no hand-rolled
  snapshot write.
- `generation` bumps once per write transaction, never once per file.
- `field_path` addresses list members by id, never by index.
- A stored collection contains exactly the records of its currently-live sources.
- Missing data is never coerced to a passing or zero value.
- **Arithmetic stays in Python.** The model is never asked which of two readings
  governs — that is the entire reason this is `review` and not a resolution.
- A failed extraction never blanks previously-good stored data.

---

## 9. Testing

TDD per task. The load-bearing case is a **two-run mutation matrix**, per
`docs/superpowers/PLAN-TEMPLATE.md`, on the integration task:

| mutation | run 1 → run 2 | expected |
|---|---|---|
| a second, different reading appears | one reading → two | cell moves `pass`/`fail` → `review`, both cited |
| the second reading is corrected away | two → one | cell returns to auto-deciding, no stale cell, no orphan |
| the second reading is a unit restatement | one → two equivalent | cell still auto-decides, no escalation |
| an override addressed at the 700 kW reading | re-extraction preserves both | resolves to the 700 kW record, not the 525 kW one |
| a reading's value is reworded | id shifts | override orphans **visibly** — `reconcile` flags conflict |
| a negation appears on the second reading | presence-only cell | downgrades to `review`, not hidden behind the first print |

Unit coverage: `fact_id_for` uniqueness and normalisation; the collapse function
over numeric, convertible, unconvertible and non-numeric values; `evaluate`'s
single-reading path proven byte-identical to today's.

---

## 10. Expected, measured impact

Re-measured on Task 6, at the branch's final tip (098742e plus Task 6's own
changes), against every multi-vendor store under `projects/` — not just the two
this table originally cited. Each store's `evaluate()` was run twice per
`(requirement, vendor)` cell straight from the store's own `requirements.json`
and each vendor's `facts.json`: once with the shipped evaluator, and once with
the pre-plan evaluator as it stood at commit `fd9edba` (the plan commit, before
Task 1 touched `fact_id_for` and before Tasks 2/3 introduced `_readings` and
grouping) — a literal `next(...)`-over-an-unkeyed-list evaluator, reconstructed
by checking out that file rather than estimated. The two are diffable because
both read the same `RequirementRecord`/fact dicts and differ only in the
function body. The earlier figures in this table (31/664 and 33/740, later
41 and 36 per the ledger) were measured at different points in the same
sequence of fixes and are superseded by this run; this table is not additive
with them.

Cells whose verdict changes, computed against the live stores:

| store | cells | verdicts that change | currently `pass` | currently `fail` | currently `unanswered` |
|---|---|---|---|---|---|
| `coverage-floor-t7` | 664 | 31 | 29 | 1 | 1 |
| `gas-08` | 0 | 0 | 0 | 0 | 0 |
| `gas-1` | 291 | 9 | 8 | 0 | 1 |
| `gas-11` | 300 | 4 | 3 | 1 | 0 |
| `gas-6` | 252 | 9 | 8 | 1 | 0 |
| `phase4-acceptance` | 604 | 37 | 33 | 2 | 2 |
| `phase4b-acceptance` | 740 | 38 | 35 | 2 | 1 |
| `phase4c-shipped-defaults` | 572 | 42 | 36 | 3 | 3 |
| **total** | **3423** | **170** | **152** | **10** | **8** |

`gas-08` holds no ingested requirements, so it contributes zero cells; it is
listed for transparency rather than omitted. Every remaining multi-vendor store
under `projects/` at measurement time is represented. In every store and in
every cell, the change is always *into* `review` — never into `pass` or `fail`
— matching the direction the ledger recorded at every earlier measurement of
this same code.

A cell moving off `unanswered` is the one direction that *gains* information: the
comparison was refused (`units.Unconvertible`) against a single reading, and the
cell will now name every reading a human could finish the check with.

170/3423 = 4.97% of the matrix, and 152/795 = 19.12% of the cells the pre-plan
evaluator would have called `pass`. Cells move from `matched` into `needs_human`
in both the portal and the web matrix. **This is the correction, not a
regression** — those cells were decided on print order — but it is a number a
reviewer watches, so it is recorded here as expected.

Reproduce with `python measure_impact.py` from a scratch copy holding
`old_compliance.py` (checked out from `fd9edba`) beside the current
`procurement/` package — the script this measurement used is not checked in,
per the plan's own scratch-work convention.

The four live-corpus floors are unaffected by construction: they assert on
extraction and checkability (every vendor above zero facts, every document
records a `text_source`, skipped-without-extraction under 25%, machine-checkable
at or above 35%). None reads a verdict distribution. No floor is lowered.

---

## 11. Out of scope

- The requirements-side (`stated`) matcher's token-subset semantics. Phase 4's
  Important 1 addressed its negation blindness; the subset rule itself is
  unchanged here.
- `unpack_vendor_zip`'s partial extraction on an unsafe archive entry, carried
  forward from the FastAPI phase.
- Any change to how facts are extracted or chunked. This spec touches identity
  and selection only.
