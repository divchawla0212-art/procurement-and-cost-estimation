# Plan template — phases 3 and 4

House rules for writing an implementation plan in `docs/superpowers/plans/`.
These are additions to `superpowers:writing-plans`, not a replacement for it.

**Why this exists.** Phase 2 shipped seven defects that survived per-task TDD and
seven honest per-task reviews. The pattern is exact:

> Every plan defect caught *during* phase 2 execution was inside a single
> function's contract. Every defect that survived to the final review needed
> **two runs or two modules** to see.

Per-task TDD structurally produces single-run, single-module tests. Each task
passed its own review correctly — the gap was architectural, not a reviewer
failing. The three rules below are the structural fix. Skipping them reproduces
the same class of defect.

---

## Rule 1 — Every task brief names the store invariant it owns

A task's `Interfaces` block currently declares what a function returns. That is a
**function contract**. It says nothing about what must be true of the store
afterwards, so nobody tests it, so it breaks silently.

Add a third bullet to every task's `Interfaces` block:

```markdown
**Interfaces:**
- Consumes: …
- Produces: `extract_tech_facts(...) -> tuple[list[FactRecord], str, str | None]`
- **Store invariant owned:** `VendorFacts.technical` contains exactly the facts
  of the vendor's currently-extractable documents — no more, no fewer.
```

The difference, on the exact defect this would have caught:

| | |
|---|---|
| Function contract | "`extract_tech_facts` returns `(facts, status)`" |
| Store invariant | "`VendorFacts.technical` contains exactly the facts of the vendor's currently-extractable documents" |

The second is what **C1** violated. No phase-2 task owned it, so no phase-2 test
asserted it, and facts of superseded, reclassified and deleted documents
accumulated forever.

**Constraints on an invariant statement:**

- It is a sentence about **stored state**, not about a return value. If it can be
  checked without loading a snapshot, it is not a store invariant.
- It must be assertable in roughly one assertion over a loaded snapshot.
- It says **"exactly"**, not "includes". "Includes" invariants never catch
  orphaning — the whole C1 family is about what should have *left*.
- If two tasks name the same invariant, one of them does not own it. Assign it to
  the task that writes the store last.
- An invariant with no defending row in the mutation matrix (Rule 2) is untested.
  An invariant nobody claims is the next C1.

**Pre-declared for phase 3** — these have C1's exact shape and must be owned:

- `requirements.json` contains exactly the requirements of the currently
  authoritative spec revision.
- An amendment sourced from a superseded MOM never contributes to a
  requirement's effective value.
- Every compliance verdict names the `req_id` and `fact_id` it was computed
  from, and no verdict outlives the disappearance of either.
- The requirement × vendor matrix has a cell for every (live requirement, live
  vendor) pair and no cell for any other pair.

Phase 3 is **more** exposed than phase 2, not less: requirements and amendments
are a second accumulating collection with the same orphaning shape, and verdicts
are a third that derives from both.

---

## Rule 2 — The integration task carries a named two-run mutation matrix

"Test the pipeline" produces single-run tests. Name the mutations instead.

The integration task's test step must contain a table with these three columns
and no fewer:

```markdown
| mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|
```

Every row names an invariant declared under Rule 1. A row defending no named
invariant is decoration; an invariant defended by no row is untested.

**Rows required in every phase's matrix** — each corresponds to a defect that
actually shipped:

| mutation | what it catches |
|---|---|
| a newer revision of an already-extracted document arrives | orphaned facts of the superseded document (**C1**) |
| a document is deleted from its source folder | orphaned facts of a document that no longer exists (**C1**) |
| a second upload arrives carrying a sibling revision | lineage pointers dropped by the extraction cache (**C2b**) |
| each prompt-version constant is bumped, one at a time | the bump not persisting → permanent per-run re-work (**C2a**); and cross-class cache invalidation |
| an LLM call fails on run 1 and succeeds on run 2 | a transient failure cached as a permanent answer (**I1**) |
| an LLM call fails on both runs | no diagnostic recorded, retried forever (**I4**) |
| an entity's only source document is unrecognised | the entity silently vanishing downstream (**I2**) |
| a re-extraction fails after a successful one | a failed overwrite blanking good stored data (**I3**) |
| the model returns a response omitting an optional array | "no results" misread as "failed" (**I5**) |

Add phase-specific rows for every accumulating collection the phase introduces.
For phase 3 that means at minimum: a spec revision superseded between runs; a MOM
withdrawn between runs; a requirement deleted while an override targets it.

**Verify the matrix is real, not decorative.** Before declaring the integration
task done, reintroduce each defect one at a time and confirm the intended row
fails — and that nothing else does. A row that still passes with its defect
reinstated is not testing what it claims. (Phase 2's fix wave did this for all
nine fixes; it took one throwaway script.)

---

## Rule 3 — Reference code is intent, not paste-able

Every plan that embeds reference implementations must carry this banner above the
first one:

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the
> implementation against the test.

This is not defensive boilerplate. **Seven of the phase-2 plan's reference
samples were wrong**, and two of them became shipped defects:

| plan's reference | what shipping actually required |
|---|---|
| `deviation_id_for`: `(clause_ref or statement)` | both must participate — the plan's key collides whenever two entries share a clause ref |
| `extract_tech_facts`: `prompt += parameters` | belongs on `text`, not `prompt` — the prompt is a versioned file |
| `_TechFactList.model_validate(...)` whole-list | per-entry validation, so one malformed row cannot discard a whole document — **and the refactor away from `model_validate` is what lost its `[]` default, becoming I5** |
| `resolve_supersession`: `newest.supersedes = next(...)` | must be the *immediate* predecessor; "any older sibling" breaks a 3+ revision chain |
| `normalised_base`: `_SUPERSEDED.sub("")` | must drop the whole tail ("Superseded with MOM 20241111"), plus an OS `copy` marker |
| `classify._RULES`: plain substrings `"bom"`, `" mr "` | lookaround regexes — the substrings false-match inside larger words and mishandle `_` as a delimiter |
| `classify_document` except → `("other", "llm")` | `"llm-failed"`, the marker that distinguishes an outage from an answer — **the plan's version is what makes I1 unfixable** |

The last row is the important one: a paste-able-looking sample removed the
information a later fix depended on, and the plan's own test asserted the wrong
value, so TDD locked the defect in.

**So:** label every reference block with what it demonstrates, and say plainly
which parts are load-bearing (the failure convention, the id-stability rule) and
which are illustrative (the exact regex, the exact key format).

---

## Task skeleton with the additions applied

```markdown
### Task N: <name>

**Files:**
- Create / Modify: …
- Test: …

**Interfaces:**
- Consumes: …
- Produces: …
- **Store invariant owned:** …

<prose: the reasoning a reader needs that the code does not carry>

- [ ] **Step 1: Write the failing test**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Write minimal implementation**   ← reference code carries the Rule 3 banner
- [ ] **Step 4: Run test to verify it passes**
- [ ] **Step 5: Commit**
```

And, for the phase's final integration task, one extra step before the commit:

```markdown
- [ ] **Step 4b: Two-run mutation matrix** — the table from Rule 2, one test per
      row, each naming the invariant it defends. Verify by reinstating each
      defect and confirming the intended row fails.
```

---

## Checklist before a phase plan is approved

- [ ] Every task's `Interfaces` block has a **Store invariant owned** bullet.
- [ ] No invariant is claimed twice; none of the phase's stored collections is unclaimed.
- [ ] The integration task has a mutation matrix with all nine required rows plus
      phase-specific rows for every new accumulating collection.
- [ ] Every matrix row names an invariant; every invariant has a row.
- [ ] The Rule 3 banner appears above the first reference block.
- [ ] The plan states which reference parts are load-bearing and which are illustrative.
