# Closing BUG-001 … BUG-004 — design

Status: accepted, 2026-08-05.
Tracker: [`BUGS_TRACKER.md`](../../../BUGS_TRACKER.md).

Four reports filed on 2026-08-05 against `remove-streamlit-portal` @ `028e33f`.
Three of them left an open design question that the tracker deliberately
declined to settle. This spec settles all three, then describes the change.

The four are independent defects, but two of them (BUG-001, BUG-003) live in
`web/`, which today has **no test runner at all** — no vitest, no
`@testing-library`, not one `*.test.tsx`. `CLAUDE.md` closes a bug only when a
test fails without the fix. So this spec has a fifth piece of work that the
tracker does not list: standing up the front-end test harness. It is stated
here rather than smuggled into a task, because it is the largest single change
in the set and it moves the CI contract.

---

## 1. Decisions

### 1.1 BUG-001 — does `done_with_failures` admit the user?

**Yes, with a banner.** Screens `02 Compliance matrix` and `03 Comparative
statement` are reachable when `status` is `done` or `done_with_failures`, and
unreachable when it is `new` or `failed`.

The tracker called this out as the likelier answer and it survives scrutiny. A
project with one unreadable drawing among thirty documents still has a usable
matrix, and `04 Extraction status` already reports exactly which documents
failed. Blocking the whole review because one document did not parse would push
users toward the thing this bug is about — reading numbers without knowing what
is behind them — by making the honest screen unavailable.

What made the original report a bug is not that a partial project was readable;
it is that a project with **nothing** in it presented as ready. `new` and
`failed` are the two states where the store holds no extraction the user could
be reading, and those are the two we close.

The banner is deliberately not a list of the failed documents. Naming them
means fetching `/extraction-status` from two screens that do not otherwise need
it, and the screen that already does that job is one click away. The banner
says that some documents failed and points at `04`.

**Correction (final-review report, I2) — the paragraph above is wrong about
`failed`.** The original reasoning was: `new` and `failed` are the two states
where the store holds no extraction to read, so gating screens 02/03 on
`status ∉ {new, failed}` is safe. That is false for `failed`, and it is false
because of a `CLAUDE.md` store invariant this spec did not check against: "a
failed extraction never blanks previously-good stored data." `project.status`
is recomputed **per run** as `"failed" if extracted == 0 else ...`
(`procurement/pipeline.py`), so a project that ingested cleanly once and then
had a *second* run fail every document — an expired key, a provider outage, or
a forced re-run, which BUG-002's fix on this same branch newly makes reachable
— ends at `status: failed` with the entire first run's extraction still
sitting in the store. Gating on `status` alone made that project's compliance
matrix and comparative statement unreachable even though the store held a
complete, readable matrix, and left `web/src/pages/Setup.tsx`'s "Review
compliance matrix" button (gated on `has_results`, which was `true`) routing
to the screen the user was already on.

**The rule now:** the gate is `has_results` (`ProjectSummary.has_results`,
computed in `api/main.py::_project_summary` as
`bool(load_dataset(ROOT, slug))` — the same computation `_setup_state` already
used for the setup screen, not a second implementation). A project with
results is reviewable whatever its `status`; a project without is not.
`status` no longer drives the gate at all — it drives only the banner. The
`done_with_failures` banner is unchanged (some documents in *this* run
failed). A `status: failed` project that is reachable (because it still holds
an earlier run's results) also gets a banner, with its own wording: the
*latest* run failed outright and everything on screen is from an earlier run,
which is a different situation from a partial failure in the current run and
reads better said plainly rather than reused. See `web/src/nav.ts`,
`web/src/pages/ComplianceMatrix.tsx` and `web/src/pages/ComparativeStatement.tsx`.

### 1.2 BUG-002 — how is a forced re-extraction surfaced and guarded?

**`force` in the ingest payload; a separate, secondary UI action behind an
explicit confirmation; never the default.**

`run_ingestion(..., force=True)` already exists and already does the right
thing — the whole defect is that nothing can reach it. So the API change is one
field, and the design work is entirely in making the action hard to hit by
accident: a forced run re-spends the full LLM cost of the project and is the
widest-blast-radius write the system has.

Three guards, in order of how much they matter:

1. The field defaults to `false`. An existing client that does not know about
   it keeps today's behaviour exactly.
2. The button is separate from `Run ingestion`, styled as secondary, and shown
   only when `has_results` is true — before the first run there is nothing to
   force.
3. It confirms before firing, naming the cost.

The tracker asks that whoever fixes this check what a forced run does to
`_prune_orphan_facts` and the generation counter before exposing it. That is a
task acceptance criterion, not a design question: the answer must be measured,
not decided here.

### 1.3 BUG-004 — does `mock` stay reachable in a deployed build?

**Yes, but only when someone names it. There is no implicit default any more.**

The choice is between deleting `mock` from deployed builds outright and keeping
it as an explicit opt-in. Deleting it is not available: the test suite selects
`mock` through the same code path (`tests/test_api_setup.py:12` sets
`LLM_PROVIDER=mock`), and a build-flavour-dependent provider catalog would mean
the suite no longer exercises what ships.

So the fix is to remove the *fallback*, not the provider. Every
`os.getenv("LLM_PROVIDER", "mock")` becomes `os.getenv("LLM_PROVIDER")`, and an
unset variable is a state the code names rather than papers over:

- `POST /ingest` with no provider in the payload and no `LLM_PROVIDER` returns
  **400**, with the same shape of message an unconfigured `anthropic` already
  gets, and leaves the store untouched.
- `_provider_state()` reports `provider: null`, `ready: false`. The front end
  renders that as "no provider configured" rather than as a ready one.
- `get_client(None)` with no `LLM_PROVIDER` raises `ValueError`. This is the
  load-bearing one: the API's guard already validated the *effective* provider
  before this call, so the raise is unreachable from a correct API path — it is
  there so that the next caller to grow a provider-less path fails loudly at
  the factory instead of silently extracting with a mock.

`cost_estimation/cli.py:10` calls bare `get_client()` and therefore starts
requiring `LLM_PROVIDER`. That is the same defect in the other package, and
correcting it is in scope: a costing CLI that silently fabricates is not better
than an API that does.

**Provenance, additionally.** Removing the default closes the reported hole,
but a deployment that explicitly sets `LLM_PROVIDER=mock` still writes facts
that the store cannot distinguish from real ones. `CLAUDE.md` requires that a
store say why it holds what it holds. So `run_ingestion` records the client
class on the `run.started` event:

```python
detail={"client": type(client).__name__}
```

`MockLLMClient` in the events log is then unmissable in a way that
`extractor: llm:tech_facts_v1` is not. This is one dict key on an event that is
already written; it touches no snapshot, no cache key and no generation bump,
and so cannot disturb the store invariants. It deliberately stops short of
stamping `DocumentRecord.notes` — that field is the extraction's own account of
itself, `notes: null` means "the extraction had nothing to report", and
overloading it with run-level provenance would make the per-document meaning
ambiguous for every reader.

### 1.4 BUG-003 — no question to settle

Two strings point at software deleted in `028e33f`. They become pointers to
`01 Set up & ingest`, which now owns ingestion. The tracker's own note asks for
a sweep rather than a fix of the two known lines, and it is right: these two
were found by reading the branches BUG-001 led to, not by looking.

---

## 2. Test strategy, and the front-end harness

`web/` has no test runner. BUG-001's fix is a predicate on `status` wired into
a `disabled` prop; BUG-003's is copy. Neither can be tested from `pytest`.

**Add vitest + jsdom + `@testing-library/react` to `web/`,** with an
`npm test` script and a CI job beside the Python one.

Testing only a pure predicate would be cheaper and would need no jsdom. It is
rejected: the defect is not that the repo lacked a correct predicate, it is
that the nav asked the wrong question. A test of a pure `reviewReachable()`
passes just as happily when nobody wires it into `disabled`, which is the exact
failure that produced the report.

So the split is:

- `web/src/nav.ts` — a new module holding the reachability rule as a pure
  function. Unit-tested over all four `status` values plus the no-project and
  unknown-status cases.
- `web/src/App.test.tsx` — renders `App` with `fetchProjects` mocked and
  asserts the two nav buttons' `disabled` state for a `new` project and for a
  `done` one. This is the test that fails without the fix.

CI runs `python -m pytest` on Ubuntu/3.12 with no provider secrets, and must
keep doing so unchanged. The web job is additive: same workflow file, a second
job, `npm ci && npm test` under `web/`, no secrets.

### 2.1 The baseline in `CLAUDE.md` moves

`CLAUDE.md` states two exact test counts and derives one from the other. New
Python tests change both rows. The rule there is explicit — measure the
workstation row, derive the CI row, never edit them independently — and it
applies to this change. Updating those numbers is a task with an acceptance
criterion, not a footnote.

The new web job does not enter those counts: they are `pytest` counts, and
`CLAUDE.md` says so. The web suite gets its own sentence.

---

## 3. What changes, by file

| File | Bug | Change |
|---|---|---|
| `shared/llm/factory.py` | 004 | drop the `"mock"` fallback; raise on unset |
| `api/main.py` (`_provider_state`) | 004 | `provider` may be `None`; `ready` false |
| `api/main.py` (`ingest`) | 004 | unset `LLM_PROVIDER` + no payload provider → 400 |
| `api/main.py` (`ingest`) | 002 | read `force` from the payload, pass it through |
| `procurement/pipeline.py` | 004 | `run.started` event carries the client class |
| `cost_estimation/cli.py` | 004 | surface the factory's error as a CLI failure |
| `web/src/nav.ts` *(new)* | 001 | the reachability rule, as a pure function |
| `web/src/App.tsx` | 001 | gate `02`/`03` on it; `open()` respects it |
| `web/src/pages/ComplianceMatrix.tsx` | 001, 003 | partial-run banner; empty-state copy |
| `web/src/pages/ComparativeStatement.tsx` | 001, 003 | partial-run banner; empty-state copy |
| `web/src/pages/Setup.tsx` | 002, 004 | force button + confirm; null-provider render |
| `web/src/api.ts`, `web/src/types.ts` | 002, 004 | `force` argument; `provider: string \| null` |
| `web/package.json`, `web/vitest.config.ts` *(new)* | — | the test harness |
| `.github/workflows/tests.yml` | — | a second job for the web suite |
| `CLAUDE.md` | — | re-measured baselines, web suite noted |
| `BUGS_TRACKER.md` | all | four entries moved to Closed, Fix blocks filled |

## 4. Order

BUG-004 first: it is the S1, it blocks distribution, and it is the only one
whose fix is confined to Python, so it lands against a green suite before the
front-end toolchain moves. Then the web harness, because BUG-001 cannot be
tested until it exists. Then BUG-001, then BUG-002, then BUG-003 — smallest
last, since its sweep is cheapest to redo if an earlier task touches the same
files.

## 5. Out of scope

- Any change to the extraction cache's key or skip condition. BUG-002 is about
  reaching the existing bypass, not about changing when the cache fires.
- Marking mock-produced facts synthetic inside the snapshots. §1.3 takes the
  event-log route instead, deliberately.
- Naming the failed documents in the partial-run banner (§1.1).
- The Docker packaging landed on `remove-streamlit-portal`; this branch does
  not revisit it.
