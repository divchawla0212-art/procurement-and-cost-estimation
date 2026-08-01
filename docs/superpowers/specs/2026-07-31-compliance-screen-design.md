# Design: Compliance Screen

Status: approved, not yet planned
Depends on: phase 3 (requirements & compliance), and the comparative-statement screen
  it sits beside
Belongs to: phase 4 — Portal review UI
Implements: §12 of [the compliance-correctness design](2026-07-31-compliance-correctness-design.md),
  which settled this screen's shape and recorded it "so it is not re-litigated"

---

## 1. Context and goal

Phase 3 computes a requirement × vendor matrix and stores it in
`projects/<slug>/store/compliance.json`. **Nothing in `portal/` reads it.** The
verdicts that decide an award are visible only by opening the JSON, and the
coverage percentage that spec §7 treats as an extraction metric is visible
nowhere at all.

This screen renders that matrix. It is **read-only**: it adds no writes, no
overrides and no new stored state.

### Success criteria

- Opening a project shows every stored verdict without a re-run.
- A reviewer can see, in one place, which requirements no vendor satisfies and
  which ones nobody has been able to check.
- The `unanswered` count is decomposed into "the vendor never said" and "we
  could not compare", because those are opposite meanings that look identical
  in the data.
- Rewording any message in `units.py` changes what a reviewer reads and changes
  **no number on the screen**.
- A withdrawn requirement leaves the screen on the next run.

---

## 2. Why read-only

A verdict is derived state. `compliance.evaluate_project` recomputes the whole
matrix on every call and there is no incremental path — that is deliberate, and
it is what guarantees no verdict outlives the requirement or fact it came from.

Letting a reviewer overturn a verdict would put a value in the store that does
**not** follow from `requirements.json` + `facts.json`, and the next run would
either destroy it or have to preserve it against a wholesale recompute. Both
outcomes are worse than the screen simply being honest about what the data says.
Correcting a wrong verdict means correcting the requirement or the fact it was
computed from, which the Requirements and Facts screens own.

§12 named exactly one write for this screen — the duplicate-group control — and
that one is blocked (§6).

---

## 3. Structure

The split follows `statement.py`, the screen already shipped beside this one: a
pure model builder under `procurement/`, a renderer under `portal/views/`.

| module | responsibility |
|---|---|
| `procurement/matrix.py` | `build_matrix(root, slug) -> ComplianceMatrix`. Pure. No Streamlit import, so it is testable without a browser or a key. |
| `portal/views/compliance.py` | `render(root, slug)`. Display only. Holds no logic worth testing. |
| `portal/app.py` | §5 Results becomes `st.tabs(["Comparative Statement", "Compliance"])`. |

The tab wrapper is **not** the phase-4 shell split. It is the smallest change
that hangs a second screen off the existing Results section while keeping the
statement as the landing view. The shell split remains out of scope.

### The model

```python
class MatrixCell:      # one (requirement, vendor) pair, or absent
    vendor: str
    verdict: str                     # pass|fail|deviation|unanswered|review
    group: str                       # the three-way grouping of §4
    rationale: str
    fact_id: str | None
    doc_id: str | None

class MatrixRow:       # one live requirement, across every vendor
    req_id: str
    clause_ref: str
    text: str
    checkability: str
    parameter / operator / value / unit      # the bound, for an `auto` row
    cells: list[MatrixCell]

class Coverage:
    auto_cells: int
    by_verdict: dict[str, int]
    unanswered_silent: int           # no vendor document stated it
    unanswered_refused: int          # the fact was found, the comparison was not made

class ComplianceMatrix:
    vendors: list[str]
    rows: list[MatrixRow]
    coverage: Coverage
```

Rows are built from `requirements.json` filtered to `withdrawn == False`, and
cells attached by `(req_id, vendor)`. Building from the requirements rather than
from the cells is what makes two cases right at once: a withdrawn requirement
yields no row, and a vendor whose extraction produced nothing still gets a full
column of cells rather than disappearing from the screen.

---

## 4. The three verdict groups

Settled in §12 and reproduced here so the screen is readable without opening
that spec:

| group | verdicts | what a reviewer does with it |
|---|---|---|
| **Not matched** | `fail`, `deviation` | the vendor does not meet the requirement, or has said in writing that it will not |
| **Needs a human** | `unanswered`, `review` | nobody has decided yet — either we could not check it, or it was never machine-checkable |
| **Matched** | `pass` | nothing to do |

The worklist mode leads with Not matched, then Needs a human; Matched is
collapsed. The grid mode shows all rows in clause order.

`deviation` sits with `fail` rather than in its own group because the action is
the same — a reviewer must decide whether to accept it — and because a declared
deviation is the most informative negative verdict in the matrix, not a softer
one.

---

## 5. Decomposing `unanswered` without reading prose

This is the constraint the screen exists under. §7 of the correctness design:

> The rationale note is prose for a human, not a machine-readable flag. Spec 2
> must recompute [...] rather than parsing rationale strings — a screen that
> greps its own verdict text would break the first time the wording changed.

`ComplianceResult` carries the field that makes this structural.
`compliance.evaluate` sets `fact_id` only when a matching vendor fact was found;
when no fact matched, it returns `unanswered` with no fact cited. So:

| condition | meaning | is it a real gap? |
|---|---|---|
| `verdict == "unanswered"` and `fact_id is None` | no vendor document stated the parameter | **yes — this is the metric** |
| `verdict == "unanswered"` and `fact_id` is set | the fact was found; `units.compare` refused | no — it is our reach, not the vendor's silence |

The screen shows both counts and displays `rationale` **verbatim** beside a
refused cell, so the human reads the reason and the machine never interprets it.

The finer split of a refusal — unrecognised unit vs. gauge-vs-absolute vs.
unknown molar mass — is deliberately **not** offered. Producing it means either
grepping the rationale, which §7 forbids, or adding a reason code to
`ComplianceResult`, which §7 declined on the grounds that only one consumer
would need it and it would have to be kept true across every recompute. That
decision is not reopened here.

---

## 6. Deliberately out of scope

- **The duplicate-group control.** §12 wants a control to pick the authoritative
  copy of a duplicated MR. It reads `DocumentRecord.duplicate_group`, which is
  fix 3 of the correctness design and has not shipped. The control is
  unbuildable until it does, and is not stubbed here.
- **Any write.** See §2.
- **The reason-code breakdown of a refusal.** See §5.
- **The phase-4 shell split and the remaining screens.** Unchanged from the
  statement spec's §12.

---

## 7. Store invariants

**This screen owns none, and adds none.** Rule 1 of the plan template: a store
invariant is a sentence about stored state, and anything checkable without
loading a snapshot is not one. `matrix.py` and `compliance.py` (the view) both
only read; no collection accumulates; no snapshot changes shape.

The screen's correctness is defended instead by the assertions in §8, and by
phase 3's existing matrix, which already owns every invariant governing the data
it displays — in particular INV-7 (no verdict outlives the requirement or fact
it was computed from) and INV-9 (every verdict is at or after the run that
produced it).

---

## 8. Testing

`tests/test_compliance_matrix.py` — key-free, Streamlit-free, over snapshots
written directly by the fixture:

- each verdict lands in its declared group
- `unanswered` splits on `fact_id`, and the two counts sum to the `unanswered`
  total
- **rewriting every `rationale` to arbitrary text leaves every coverage number
  identical** — the assertion that pins §5, and the one that fails the moment
  somebody reintroduces string-matching
- a withdrawn requirement produces no row
- a vendor with no stored facts still gets a cell in every row
- an empty store returns an empty matrix rather than raising

Two rows are added to the phase-3 two-run matrix in
`tests/test_phase3_lifecycle.py`, because the screen's honesty across a re-run
is the one thing a single-run test cannot show:

| # | mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|---|
| 24 | a requirement is withdrawn by a MOM between runs | INV-7 | the row leaves `build_matrix`'s output entirely — not merely rendering as blank |
| 25 | a vendor's datasheet is deleted, so a previously-cited fact vanishes | INV-7 | the cell moves from a verdict to `unanswered` **with `fact_id is None`**, so it counts as vendor silence and not as a refusal |

Row 25 is the one worth having: it is the only place where the silent/refused
split of §5 can be wrong in a way that a single-run fixture would agree with.

---

## 9. Open risks

1. **The grid mode is large.** `gas-1` has 291 cells across 3 vendors, 249 of
   them `review`. The worklist defaults first precisely because the grid is not
   the useful view on a real project, but no pagination is specified and a much
   larger RFQ may render slowly. Measure before optimising.
2. **`review` dominates.** On the real stores today, `judgement` requirements
   outnumber `auto` ones by four to one (`gas-6`: 16 auto of 84) to six to one
   (`gas-1`: 13 auto of 97), so "Needs a human" will be
   mostly clause text a person must read. That is an honest reflection of the
   corpus, not a screen defect, but it means the coverage percentage in §5 is
   computed over `auto` cells only and must be labelled as such — a percentage
   over all cells would be dominated by `review` and would mean nothing.
3. **The screen makes the `% of Rated Power` class of gap visible for the first
   time.** That is the point, but it will read as a regression to anyone who has
   not seen the correctness design. The refused count needs a caption saying it
   measures our reach, not the vendor's answer.
