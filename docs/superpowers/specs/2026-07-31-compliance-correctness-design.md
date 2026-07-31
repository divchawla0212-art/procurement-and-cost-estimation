# Design: Compliance Correctness — Deviation Scoping, Ranges, and Duplicate RFQ Documents

Status: approved (brainstormed 2026-07-31)
Follows: `docs/superpowers/specs/2026-07-29-project-scoped-extraction-store-design.md` (phases 1–4)
Precedes: Spec 2 — the phase 4 Compliance screen (see §11)

---

## 1. Context and goal

Phase 3 shipped the compliance matrix data layer: `requirements.json`, `units.py`,
`compliance.py`, and one `ComplianceResult` per (live requirement, live vendor)
pair. The first live run over `processed-data/01-client-mr-rfq/` with ADPOWER and
KERUI produced 515 cells and, with them, three defects that make the matrix
misleading rather than merely incomplete:

| verdict | cells | share |
|---|---|---|
| review | 417 | 81.0% |
| unanswered | 52 | 10.1% |
| pass | 25 | 4.9% |
| fail | 17 | 3.3% |
| deviation | 4 | 0.8% |

1. **Six of the seventeen `fail`s are wrong.** The MR states `Temperature 5-58 deg C`;
   the extractor emits `operator="in", value=[5,58]`, meaning a range;
   `units.compare` implements `in` as set membership, so a vendor stating 55 °C is
   failed against `['5','58']`. This is the outcome `units.py`'s own docstring calls
   indefensible.
2. **Deviations bleed across documents.** `compliance.evaluate` matches a vendor
   deviation to a requirement on normalised `clause_ref` alone, unscoped by source
   document. KERUI's single deviation (clause `2.3`, about ambient temperature)
   bound to four requirements from four different documents that each happen to
   print a clause `2.3`. `deviation` wins outright in the verdict order, so it
   overrode real numeric evidence on cells including `starting_voltage`.
3. **The RFQ folder holds the same MR three times** — one PDF and two xlsx exports —
   yielding 115 + 114 + 114 near-identical requirements out of 515. Nothing groups
   them, so the matrix carries roughly three cells per real clause.

Defects 1 and 2 sit directly in the path a reviewer looks at first. This spec fixes
all three in the data layer, so that the phase 4 Compliance screen is built over
verdicts worth displaying.

### Success criteria

- No vendor is failed by a range comparison implemented as set membership.
- No deviation contributes a verdict to a requirement it cannot be attributed to.
- Duplicate RFQ documents are visible as a group, with no requirement discarded.
- `requirements.json` is byte-identical whether or not duplicate detection ran.
- An unchanged re-run still makes zero LLM calls.

---

## 2. Decisions locked (from brainstorming)

1. **Two specs, correctness first.** The Compliance screen is Spec 2. Fixes 2 and 3
   change the shape of `requirements.json`, so building the UI first would mean
   writing its tests twice; and store work and view work do not share an invariant
   ledger comfortably.
2. **Ranges get their own operator (`between`), not a range-aware `in`.** A
   two-element numeric list is genuinely ambiguous — `[5,58]` for temperature is a
   range, `[50,60]` for frequency is membership — and only whoever read the source
   sentence knows which. Inferring from list shape would silently pass a vendor
   offering 55 Hz against a 50/60 Hz requirement. The model signals intent at read
   time; Python still decides.
3. **Duplicate documents are detected and surfaced, never auto-resolved.** The three
   MR copies yield 115/114/114 requirements, so they are not identical, and any
   rule that picks a winner discards at least one real requirement. Consistent with
   `Unconvertible` → `unanswered`, with `unanswered` ≠ `fail`, and with commit
   `60f2188`'s refusal to apply an ambiguous amendment.
4. **An ambiguous deviation falls through to normal evaluation, except that it
   downgrades a `pass` to `review`.** Forcing every ambiguous cell to `review` would
   discard correct arithmetic and inflate a bucket already at 81%. But `pass`
   sitting beside an unattributed deviation is the one combination that hides a real
   problem, so it is downgraded; `fail` and `unanswered` already draw attention and
   are left alone.
5. **Detection thresholds are constants in code, not project configuration.** They
   are testable that way and there is no evidence yet that any project needs
   different ones.

---

## 3. Architecture

Data layer only. `portal/` is untouched and `load_dataset` keeps its three keys
(`bids`, `normalized`, `comparison`), so nothing downstream breaks.

| fix | modules | blast radius |
|---|---|---|
| Deviation scoping | `compliance.py` | `compliance.json` only — already recomputed wholesale every run, so no migration |
| `between` operator | `extract_requirements.py`, `shared/llm/prompts/requirements_v2.txt`, `units.py` | `requirements.json`, rewritten once by the prompt version bump |
| Near-duplicate detection | `pipeline.py` (`_run_rfq_pass`), `store/models.py` | `documents.json` gains one field; `requirements.json` unchanged |

**Deliberately unchanged:**

- No document is superseded, pruned or merged by duplicate detection. All copies stay
  live and every requirement stays stored. Acting on a group is a Spec 2 control.
- The `auto` parameter vocabulary is untouched by fixes 1 and 3, so neither triggers
  datasheet re-extraction. Only fix 2 can, and only insofar as the new prompt
  promotes clauses from `judgement` to `auto`.
- `classify.py`'s vocabulary and `_RULES` stay frozen — the same boundary phase 3 held.
- `unanswered` is never `fail`. All three fixes preserve it; fix 1 leans on it.

**Task ordering.** Fixes 1 and 3 are independent. Fix 2 lands last: it is the only
one that rewrites `requirements.json`, so running it after duplicate detection
exercises the detection logic against both the old and the new requirement sets —
free two-run exposure of the kind `PLAN-TEMPLATE.md` exists to force.

**One-time cost.** Fix 2 re-extracts every spec document once (4 on the current
corpus) and, if the vocabulary shifts, every datasheet once (3 more). Roughly 7 LLM
calls, then zero again on unchanged re-runs.

---

## 4. Fix 1 — deviations bind only when the clause is unambiguous

`evaluate_project` computes, once per run, how many live requirements carry each
normalised clause ref, and passes that count map into `evaluate`. `evaluate` then
applies the rule commit `60f2188` already applies to amendments:

- **exactly one** live requirement carries the clause → the deviation binds; verdict
  `deviation`, as today, now provably attributable.
- **more than one** → the deviation cannot be attributed and does not override.
  Evaluation proceeds normally (`auto` comparison, or `review` for a judgement
  clause), and the rationale gains a stated note naming the unattributed deviation
  and its source document.
- **a fall-through verdict of `pass`** is downgraded to `review`, because a clean
  pass beside an unattributed deviation is the one combination that buries a real
  problem. `fail` and `unanswered` are left as computed.

Every deviation that fails to bind emits a `deviation.unattributed` event, and the
event's detail **distinguishes the two reasons in words** — the same choice commit
`60f2188` made when it gave `Amendment` an `unresolved_reason`, because "no
requirement carries this clause" and "several do, say which you meant" call for
different responses from a reviewer:

- matched **no** live requirement — contributes to no cell at all, so without the
  event it would be entirely invisible. Usually an extraction gap or a vendor citing
  a clause number that does not exist.
- matched **more than one** — visible per-cell in the rationale, but the event
  records the ambiguity once per run rather than once per affected cell.

The count map is recomputed every run from the live requirements. It is never
cached — a cached map is the defect two-run mutation 1 (§9) exists to catch.

---

## 5. Fix 2 — the `between` operator

```python
_OPERATORS = (">=", "<=", "==", "in", "between")
```

**Extraction.** `between` requires `value` to be exactly two numeric members.
Reversed bounds are sorted, since `[58,5]` has only one possible reading. Any other
shape — one member, three members, a non-numeric member — is stored as `judgement`,
under INV-2's existing rule that a half-stated bound is never a live `auto`.

**Comparison.** `units.compare` converts both bounds and the vendor value into the
family's canonical unit and checks `lo <= x <= hi`, inclusive, using the same
`math.isclose` tolerance the other operators use at both ends. Family mismatch, an
unrecognised unit, or a non-numeric vendor value raises `Unconvertible` →
`unanswered`, never `fail`. The existing "vendor unit not stated; assumed `<req
unit>`" note applies unchanged.

**Prompt.** `shared/llm/prompts/requirements_v2.txt` teaches `between` for `5-58 °C`,
`X to Y`, and `between X and Y`, and keeps `in` for genuine alternatives — `50/60 Hz`,
`ISO 8528 or equivalent`. `REQUIREMENTS_PROMPT_VERSION` becomes `requirements_v2`,
which re-extracts every spec document exactly once.

---

## 6. Fix 3 — near-duplicate RFQ documents

Runs at the end of `_run_rfq_pass`, over live spec-routed documents, using the
requirements just stored.

- **Signal:** overlap coefficient over normalised clause-ref sets,
  `|A ∩ B| / min(|A|, |B|)` — not Jaccard, so that 115-vs-114 where one nearly
  contains the other scores ~1.0 regardless of the size gap.
- **Thresholds**, as named constants: overlap `>= 0.9`, and a floor of `>= 5` clauses
  per document so two thin documents sharing a couple of refs never group.
- **Grouping is transitive**, so three copies form one group rather than three pairs.
- **`DocumentRecord.duplicate_group: str | None`** — a stable id hashed from the
  sorted member `doc_id`s, so an unchanged set of copies yields an unchanged group id
  across runs. `None` for every non-member.
- **Event `documents.near_duplicates`** per group, carrying the members and their
  pairwise overlap.
- A document whose extraction failed has no clause set and therefore never groups.
  This is stated explicitly because `"failed"` counts as *live* everywhere else in
  this pipeline, and the asymmetry is deliberate rather than an oversight.

Nothing is superseded, pruned or merged. `requirements.json` comes out byte-identical
whether or not detection ran — the property that proves detection is observational,
and the one most likely to be broken by a later "improvement" that turns grouping
into pruning.

---

## 7. Record model changes

```python
class DocumentRecord(BaseModel):
    ...
    # Stable id of the near-duplicate group this RFQ document belongs to, or None.
    # Observational only: membership never changes which requirements are stored.
    duplicate_group: str | None = None
```

`RequirementRecord` is unchanged — `operator` is already a free string and `value`
already accepts `list`. `ComplianceResult` is unchanged; the unattributed-deviation
note lives in the existing `rationale`.

**The rationale note is prose for a human, not a machine-readable flag.** Spec 2 must
recompute the clause count map from `requirements.json` and `facts.json` rather than
parsing rationale strings for it — the map is cheap, and a screen that greps its own
verdict text would break the first time the wording changed. Keeping the note in
`rationale` is a deliberate choice not to add a field that only one consumer needs
and that would then have to be kept true across every recompute.

---

## 8. Store invariants

Extending the phase 3 ledger. One owner each; every one gets at least one row of the
implementation plan's two-run mutation matrix.

| id | invariant (over stored state) | owner |
|---|---|---|
| **INV-2** *(extended)* | An `auto` requirement with `operator == "between"` carries `value` as a two-element numeric list with `lo <= hi`. Any other shape is stored `judgement`. | Fix 2 |
| **INV-10** *(new)* | A `deviation` verdict exists only where exactly one live requirement carries that clause ref. No cell reads `pass` while an unattributed deviation cites its clause — it reads `review`, naming the deviation and its source document. | Fix 1 |
| **INV-11** *(new)* | Duplicate detection is observational: `requirements.json` is byte-identical whether or not it ran, and every member of a duplicate group is live and un-pruned. | Fix 3 |

INV-4, INV-6, INV-7, INV-8 and INV-9 are inherited unchanged and must still hold.
INV-4 is the one fix 3 would break if grouping ever became pruning.

---

## 9. Testing

Key-free throughout, via `MockLLMClient` or a local stub, per existing convention.

**Deterministic units.** `test_units.py` — `between` conversion across families,
inclusive bounds at both ends, family mismatch, non-numeric vendor value.
`test_extract_requirements.py` — `between` validation, reversed-bound sorting, and
demotion to `judgement` for every malformed shape. `test_compliance.py` — deviation
binding when unique, non-binding when ambiguous, the `pass` → `review` downgrade, and
`fail`/`unanswered` left alone. `test_pipeline_rfq.py` — transitive grouping, the
clause floor, and a failed document never grouping.

**The two-run mutations that carry the weight.** Every defect phase 2 shipped needed
two runs or two modules to see:

1. **Ambiguity is recomputed, not cached.** Run 1 with two documents sharing clause
   `2.3` produces no `deviation` verdict. Delete one document; run 2 finds the clause
   unique and the deviation binds. Caching the count map fails this and nothing else.
2. **The prompt bump costs exactly one pass.** Run 1 against a `requirements_v1`
   store; run 2 after the bump re-extracts every spec exactly once; run 3 makes zero
   calls. This is the C2a shape Task 7 caught late in phase 3.
3. **Detection never touches requirements.** Run with detection and with it disabled;
   assert `requirements.json` is byte-identical while `documents.json` differs.

**Live-guarded**, following the existing skip-guard pattern.

---

## 10. Error handling

- Duplicate detection is pure Python over already-stored data and is **not** wrapped
  in a try/except — the same call `evaluate_project` makes. A failure there is a bug,
  and swallowing it would hide precisely the class of defect this spec removes.
- A hand-edited snapshot carrying a malformed `between` value raises `Unconvertible`
  → `unanswered`, never `fail`.
- **No migration.** `duplicate_group` defaults to `None` on existing records, and
  existing `operator="in"` records keep evaluating as membership until the prompt
  bump re-extracts them.

---

## 11. Done criteria

- Full suite green against the current **456 passed, 3 skipped, 1 failed** baseline
  plus the new tests. The one failure is the documented environment-dependent
  `test_portal_app.py::test_missing_api_key_does_not_block_creation`.
- A live run over `processed-data/01-client-mr-rfq/` with the vendor bids present —
  `KERUI.zip` and `MKON.zip` are both currently untracked in
  `processed-data/02-vendor-bids/`, so MKON is new since the phase 3 run — confirming:
  - the six range-driven false `fail`s are gone;
  - the KERUI deviation no longer overrides `starting_voltage`;
  - the three MR copies report as a single duplicate group.
- A third unchanged run makes zero LLM calls.

---

## 12. Out of scope

Carried to **Spec 2 — the phase 4 Compliance screen**, whose shape was settled during
this brainstorm and is recorded here so it is not re-litigated:

- Two view modes on one screen: a **problem worklist** and a **full requirements ×
  vendors grid**, toggled.
- Three verdict groups: **Not matched** (`fail` + `deviation`), **Needs a human**
  (`unanswered` + `review`), **Matched** (`pass`). The worklist leads with Not
  matched, then Needs a human.
- A control to act on a `duplicate_group` — pick the authoritative copy — which is
  what converts fix 3's detection into a smaller matrix.
- Surfacing the `unanswered` coverage percentage, and the decomposition that shows
  most of it is `units.py` conversion gaps rather than vendor silence.

Carried further, unchanged from the phase 3 spec §12: BOM line-item extraction,
weighted scoring and award recommendation, full scope normalisation, `.docx` support
in `loaders.read_text`, and the remaining five phase 4 screens.

---

## 13. Open risks

1. **`units.py` conversion coverage dominates `unanswered`.** Of the 52 unanswered
   cells, 41 are missing units (`%` alone accounts for 18, plus years, months,
   dB(A), kg, barg, s, Amp, VAC, `mg/Nm3@3% O2`), 9 are non-numeric `==` where
   `units.compare` only falls back to string equality when the *vendor* value is
   non-numeric and never when the *requirement* value is, 1 is an unknown molar mass,
   and only **4 are a genuine "the vendor never stated it" gap**. This spec does not
   address that backlog. It is the largest remaining source of misleading verdicts
   after these three fixes, and the honest number to quote a reviewer today is 4/96
   real gaps, not 52/515.
2. **The new prompt may not reliably distinguish range from enumeration.** `between`
   gives the model the vocabulary, but nothing forces it to choose correctly. The
   live run in §11 is the only real check; if it misclassifies, the fallback is the
   refusal net considered and deferred during brainstorming — `in` with exactly two
   numeric members raises `Unconvertible`.
3. **Duplicate detection thresholds are tuned against one corpus.** 0.9 overlap and a
   5-clause floor separate three copies of one MR cleanly. A project whose documents
   legitimately share most clause numbers — a spec and its addendum, say — could group
   spuriously. The consequence is a misleading grouping, never a lost requirement,
   because detection is observational.
4. **`m3/h` is still folded onto `nm3/h`, and `mg/m3` onto `mg/nm3`.** Actual and
   normal cubic metres differ by temperature and pressure. Inherited from phase 3
   Task 5 and deliberately left alone here; it belongs with risk 1's backlog.
