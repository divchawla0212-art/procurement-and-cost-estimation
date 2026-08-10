# Bugs Tracker

Running log of known bugs. One row per bug in the index, one section per bug
below it. Add new bugs at the **top** of the open table so the newest is first.

- **ID** — `BUG-001`, `BUG-002`, … never reused, even after a bug is closed.
- **Severity** — `S1` data loss / wrong award decision · `S2` feature broken, no
  workaround · `S3` broken with a workaround · `S4` cosmetic or annoyance.
- **Status** — `Open` · `Investigating` · `Fixed` (merged, not yet verified) ·
  `Closed` (verified) · `Won't fix` (say why in the section).
- **Dates** are absolute, `YYYY-MM-DD`. No "yesterday", no "last week".

A bug is only `Closed` once two things are true:

1. There is a test that fails without the fix. If there is no such test, say so
   explicitly in **Fix** — that is a known gap, not a detail to leave out.
2. The **Fix** block links the spec and the plan the solution came from:
   - spec → `docs/superpowers/specs/YYYY-MM-DD-<slug>-design.md`
   - plan → `docs/superpowers/plans/YYYY-MM-DD-<slug>.md`
   - ledger → `.superpowers/sdd/<phase>/progress.md`, when the fix ran inside a
     phase

   A bug small enough that no spec or plan was written closes with
   `none — <one line on why>` in those fields. Leaving them blank is not the
   same as saying none was needed, and a reader six months out cannot tell the
   difference. If a fix changed the design, link the spec **and** say which
   section moved — the tracker points at the design, it does not restate it.

---

## Open

| ID | Severity | Area | Summary | Reported | Status |
|---|---|---|---|---|---|
| BUG-007 | S3 | web / api / procurement/pipeline | After a run ends `done_with_failures`, the only retry controls are project-wide — nothing re-extracts one document, or one vendor's documents | 2026-08-05 | Open |
| BUG-006 | S3 | web / api | Extraction reports no live progress: a 12-minute run shows one static banner, while the per-document events that would fill it are already being written to disk | 2026-08-05 | Open |

## Closed

| ID | Severity | Area | Summary | Closed | Fixed in | Spec / Plan |
|---|---|---|---|---|---|---|
| BUG-010 | S1 | procurement/pipeline, procurement/renormalize, procurement/feedback | `facts.json` had BUG-009's defect and none of its guard: a run held a vendor's facts across an entire LLM extraction, then saved them back over anything written meanwhile — a reviewer's note, or a corrected FX rate's totals | 2026-08-06 | `1a6bc60`, `797bb19`, `639af24`, `867bc7b`, `8ee8890`, `9ec3043` | [spec](docs/superpowers/specs/2026-08-05-facts-concurrent-write-design.md) / [plan](docs/superpowers/plans/2026-08-05-facts-concurrent-write.md) |
| BUG-009 | S3 | api, procurement/project | Every `project.json` writer was an unguarded read-modify-write, so a `PUT /fx-rates` that returned 200 was discarded by a run finishing in the same window | 2026-08-05 | `d2bfefe` | none — see the Fix block |
| BUG-008 | S3 | procurement/store, api | Nothing serialised two ingestion runs, and `atomic_write_json` shared one fixed `.tmp` name, so concurrent writers interleaved into one snapshot | 2026-08-05 | `5080a2c` | none — see the Fix block; measured, not designed up front |
| BUG-005 | S1 | procurement/normalize | A project ships with no FX rates, and an unconfigured currency is silently converted at 1.0 — a EUR bid is ranked as if €1 = $1 | 2026-08-05 | `3dc1e5f`, `cb8e673` | [spec](docs/superpowers/specs/2026-08-05-fx-rate-normalization-design.md) / [plan](docs/superpowers/plans/2026-08-05-fx-rate-normalization.md) |
| BUG-004 | S1 | api | With no `LLM_PROVIDER` set, ingestion silently falls back to the **mock** provider and stores fabricated facts as `ok` | 2026-08-05 | `dfc3f37`, `9d3ba95`, `cddbea1` | [spec](docs/superpowers/specs/2026-08-05-tracked-bugs-001-004-design.md) / [plan](docs/superpowers/plans/2026-08-05-tracked-bugs-001-004.md) |
| BUG-003 | S4 | web | Empty states still tell the user to run ingestion "in the Streamlit portal", which no longer exists | 2026-08-05 | `d3cfa61` | [spec](docs/superpowers/specs/2026-08-05-tracked-bugs-001-004-design.md) / [plan](docs/superpowers/plans/2026-08-05-tracked-bugs-001-004.md) |
| BUG-002 | S3 | api / procurement/pipeline | No way to force a full re-extraction: `run_ingestion(force=True)` is unreachable from the API and the UI | 2026-08-05 | `f095220`, `7f066e4` | [spec](docs/superpowers/specs/2026-08-05-tracked-bugs-001-004-design.md) / [plan](docs/superpowers/plans/2026-08-05-tracked-bugs-001-004.md) |
| BUG-001 | S3 | web | Compliance matrix and comparative statement are reachable before ingestion has completed | 2026-08-05 | `88f0403` (harness: `042e8fa`) | [spec](docs/superpowers/specs/2026-08-05-tracked-bugs-001-004-design.md) / [plan](docs/superpowers/plans/2026-08-05-tracked-bugs-001-004.md) |

---

## BUG-001 — Review screens are reachable before ingestion has completed

- **Severity:** S3
- **Area:** `web/src/App.tsx`
- **Status:** Closed
- **Reported:** 2026-08-05
- **Reporter:** Rahul Jana (client-reported)

### What happens

Once a project is selected, the `02 Compliance matrix` and `03 Comparative
statement` nav buttons are enabled regardless of whether ingestion has run,
finished, or failed. The only gate is
`const disabled = item.needsProject && !slug` (`web/src/App.tsx:93`) — it asks
whether a project is *selected*, never what state it is in. `project.status` is
not consulted anywhere in the nav.

Opening a project from the dashboard makes it worse: `open()`
(`web/src/App.tsx:40`) sets the view to `matrix` directly, so a freshly created
project with zero ingested documents lands the user on the compliance matrix.

### What should happen

While ingestion has not completed, screens 02 and 03 should be unreachable —
disabled in the rail, and not the destination `open()` jumps to.

`ProjectSummary.status` is already returned by `GET /api/projects`
(`_project_summary`, `api/main.py:86`) and is already on the client, so no API
change is needed. The four values it can hold are set in
`procurement/pipeline.py:999`:

| status | meaning |
|---|---|
| `new` | default on a created project, no run yet (`procurement/models.py:87`) |
| `failed` | a run finished with `extracted == 0` |
| `done_with_failures` | a run finished, but some documents failed |
| `done` | a run finished with no failures |

**Open design question, to settle before implementing:** whether
`done_with_failures` should admit the user. Blocking it entirely may be too
strict — a project with one unreadable drawing still has a usable matrix — but
admitting it silently is what produced this report. A banner naming the
unextracted documents, with the rail still open, is the likelier answer. This
tracker entry does not decide it.

### Reproduce

1. Start the stack: `PROCUREMENT_HOST_PORT=8300 docker compose up -d`
2. Open http://localhost:8300 and create a new project.
3. Add a vendor and a requirements file, but **do not** run ingestion.
4. Select the new project in the rail's project switcher.

**Reproduces:** always

```
Observed: nav items 02 Compliance matrix and 03 Comparative statement are
enabled and clickable. Both render an EmptyState rather than erroring, so
nothing crashes — the screens simply present as available when they are not.
```

### Environment

- Branch / commit: `remove-streamlit-portal` @ `028e33f`
- Python / Node: 3.12.3 / v24.15.0
- Provider: any — the bug is client-side and provider-independent
- Data: any project whose `status` is not `done`; reproduces on a newly created
  empty project

### Notes

- **2026-08-05** — Found from a client question about partially-ingested files.
  Read the nav gate rather than reproducing in the UI first: `needsProject` is
  the only condition in `NAV` (`web/src/App.tsx:13`) and `disabled` at line 93
  is its only consumer, so no status check exists to have missed. Severity kept
  at S3 because both screens degrade to an `EmptyState` instead of showing wrong
  numbers, and screen `04 Extraction status` already reports the truth — that is
  the workaround. **Re-triage upward if** a partially-ingested project can
  render a matrix that *looks* complete: a reviewer comparing vendors whose
  facts are still missing is an award-decision risk, not a cosmetic one. That
  case was not tested here.

### Fix

- **Spec:** [`docs/superpowers/specs/2026-08-05-tracked-bugs-001-004-design.md`](docs/superpowers/specs/2026-08-05-tracked-bugs-001-004-design.md) §1.1
- **Plan:** [`docs/superpowers/plans/2026-08-05-tracked-bugs-001-004.md`](docs/superpowers/plans/2026-08-05-tracked-bugs-001-004.md), Task 4
- **Ledger:** [`.superpowers/sdd/2026-08-05-tracked-bugs-001-004/progress.md`](.superpowers/sdd/2026-08-05-tracked-bugs-001-004/progress.md) — untracked, since `.superpowers/` is gitignored; not a broken link, just not in git
- **Design change:** the open design question above was first settled in spec
  §1.1 as `status ∉ {new, failed}`, with `done_with_failures` admitting the
  user behind a banner. Final review (I2) found that predicate wrong for
  `failed` and corrected it — see the **Correction** block in spec §1.1 for
  why. The gate is now `has_results`, not `status`; `status` drives only the
  review-screen banners.
- **Commit / PR:** `042e8fa` (front-end test harness, prerequisite), `88f0403`
  (the fix), `d0f5abb` (I2 correction: gate switched from `status` to
  `has_results`), `7770774` (docs: record the I2 correction in the spec)
- **Test:** `web/src/nav.test.ts` (`reviewReachable` driven directly by
  `has_results`: `true` admits, `false`/`null`/`undefined` reject, plus the
  I2 case confirming a `'failed'`-status project with `has_results: true`
  still admits) and `web/src/App.test.tsx` (renders `App` with a mocked
  project per `status`/`has_results` pair — `new`/`false`, `failed`/`false`,
  `done`/`true`, `done_with_failures`/`true`, and the I2 case `failed`/`true`
  — asserting `02`/`03` disabled only when `has_results` is `false`, `04`
  unaffected throughout). The `new`/`false` and `failed`/`false` cases fail
  without any gate at all; the `failed`/`true` case is the one that fails
  under the original `status`-only predicate but passes under `has_results`.
- **Verified:** yes. Task 4: `npm test` 12/12, `npm run build` and `npm run
  lint` clean, reviewed. Re-verified in this task's full-suite run (below).

---

## BUG-002 — No way to force a full re-extraction

- **Severity:** S3
- **Area:** `api/main.py`, `procurement/pipeline.py`
- **Status:** Closed
- **Reported:** 2026-08-05
- **Reporter:** found during code review of a client question about re-running
  ingestion

### What happens

`run_ingestion` takes `force: bool = False` (`procurement/pipeline.py:613`),
which bypasses the per-document extraction cache. Nothing can set it:

- `api/main.py:424` calls `run_ingestion(ROOT, slug, get_client(provider), pdf_fallback=pdf_fallback)` — `force` is left at its default.
- The endpoint's payload reads only `provider` (`api/main.py:392`).
- The web client sends only `{ provider }` (`web/src/api.ts:115`).

So a document cached as `ok` on unchanged bytes can never be re-extracted from
the product. Re-running ingestion re-reads only what the cache key rejects
(`procurement/pipeline.py:752`): changed bytes, a bumped prompt version, a
changed datasheet vocabulary hash, or a prior status that is not `ok`.

This is correct and desirable most of the time — it is what makes a re-run cheap
and what makes failed documents retry automatically. It becomes a problem when a
cached `ok` extraction is *wrong* rather than absent, and the operator has no
way to say so.

### What should happen

An explicit, deliberately-not-default re-extraction path — e.g. `force` accepted
in the ingest payload and surfaced as a distinct UI action, separate from the
ordinary "Run ingestion" button. It must stay hard to hit by accident: a forced
run re-spends the full LLM cost of the project.

### Reproduce

1. Ingest a project once, letting it finish.
2. Press "Run ingestion" again with every input file unchanged.
3. Read `projects/<slug>/store/events.jsonl`.

**Reproduces:** always

```
gas-14, two runs, 33 documents:

  run 23674e3b28e1 (first)   32 document.extracted
                              1 document.skipped   superseded by c7e2cb129356

  run 8116af1e6564 (second)   1 document.extracted
                             31 document.skipped   unchanged
                              1 document.skipped   superseded by c7e2cb129356

The second run re-extracted 1 of 33 documents. There is no input to that
endpoint that would have made it re-extract the other 31.
```

### Environment

- Branch / commit: `remove-streamlit-portal` @ `028e33f`
- Python / Node: 3.12.3 / v24.15.0
- Provider: any
- Data: `projects/gas-14` (the bundled sample), which carries two runs

### Notes

- **2026-08-05** — Not a caching defect: the cache is working as designed, and
  the `prior.extraction_status == "ok"` term in the skip condition
  (`procurement/pipeline.py:757`) is what makes failed documents retry without
  being asked. The gap is purely that the existing `force` switch has no caller.
  Severity is S3 rather than S2 because two workarounds exist — re-upload the
  file, which changes `content_sha256` and defeats the cache for that document;
  or call `run_ingestion(..., force=True)` directly from Python against the
  store.
- **2026-08-05** — Whoever fixes this should check what a forced run does to
  `_prune_orphan_facts` and to the generation counter before exposing it. A
  forced full re-extraction is the widest-blast-radius write the system has, and
  it has never been exercised from the API.

### Fix

- **Spec:** [`docs/superpowers/specs/2026-08-05-tracked-bugs-001-004-design.md`](docs/superpowers/specs/2026-08-05-tracked-bugs-001-004-design.md) §1.2
- **Plan:** [`docs/superpowers/plans/2026-08-05-tracked-bugs-001-004.md`](docs/superpowers/plans/2026-08-05-tracked-bugs-001-004.md), Task 5
- **Ledger:** [`.superpowers/sdd/2026-08-05-tracked-bugs-001-004/progress.md`](.superpowers/sdd/2026-08-05-tracked-bugs-001-004/progress.md) — untracked, since `.superpowers/` is gitignored; not a broken link, just not in git
- **Design change:** settled in spec §1.2 — `force` is accepted in the
  `POST /ingest` payload (default `false`, so an existing client sees no
  behaviour change); the UI exposes it as a separate `Force full
  re-extraction` button, secondary-styled, shown only once `has_results` is
  true, gated behind a `window.confirm` that names the cost before firing.
  Before exposing the switch, the brief's required Step 1b measurement was
  taken directly against `run_ingestion(..., force=True)` on an
  already-ingested fixture — the measurements that matter are inlined below
  rather than left behind a pointer to the gitignored task report:
  - `generation` moves **exactly once per forced run** — confirmed on two
    consecutive forced runs, 1→2 and then 2→3.
  - `_prune_orphan_facts` **runs unconditionally** inside the write
    transaction, not gated on `force` — confirmed by a `facts.pruned` event
    correctly dropping a deleted document's orphaned commercial-fact link.
  - No stored collection was left holding a record whose source was gone:
    after the forced run, the affected vendor's technical/deviations lists
    were empty, its commercial/normalized/quotation fields were all `None`,
    and it had zero `DocumentRecord`s citing the deleted `doc_id`.
  - Verdict: **not blocked** — `force` is consulted only inside the
    per-document cache-skip decisions; the transaction boundary, the prune,
    and the stale-vendor sweep are unconditional, so exposing the switch does
    not put the store invariants at risk.
- **Commit / PR:** `f095220` (the fix), `7f066e4` (review-round fixes —
  non-boolean `force` now rejected with 422, an untested RFQ-side mutation
  row, a vacuity guard, mock-isolation in the front-end tests, a duplicate
  running-state button label)
- **Test:** `tests/test_api_setup.py` (payload-level `force` tests, including
  rejecting a non-boolean `force`), `tests/test_pipeline_force.py` (an 8-row
  two-run mutation matrix, one row per invariant in the plan's Step 4b table;
  every row independently verified by reinstating and reverting its own
  defect in `procurement/pipeline.py` and confirming only that row failed),
  and `web/src/pages/Setup.test.tsx` (6 tests: null-provider render, button
  visibility gated on `has_results`, confirm-declined vs. confirmed, and that
  the plain `Run ingestion` button never passes `force`).
- **Verified:** yes. Task 5: Python 893 passed / 3 skipped, web 18/18; a
  reviewer independently reinstated 6 of the 8 matrix defects and confirmed
  each failed only its own row. Re-verified in this task's full-suite run
  (below).

---

## BUG-003 — Empty states still point at the removed Streamlit portal

- **Severity:** S4
- **Area:** `web/src/pages/ComparativeStatement.tsx`, `web/src/pages/ComplianceMatrix.tsx`
- **Status:** Closed
- **Reported:** 2026-08-05
- **Reporter:** found incidentally while investigating BUG-001

### What happens

Both review screens tell the user to go somewhere that no longer exists:

- `ComparativeStatement.tsx:47` — "Add a vendor and run ingestion in the
  Streamlit portal to build the statement."
- `ComplianceMatrix.tsx:113` — "There are no stored requirements to compare. Run
  ingestion in the Streamlit portal to build the compliance matrix; this screen
  is read-only."

The portal was removed in `028e33f` ("refactor: remove the Streamlit portal,
keeping its one write"). These two strings were missed.

### What should happen

Both should direct the user to `01 Set up & ingest`, the screen that now owns
ingestion. This is the copy a user hits precisely when they are most lost, so
pointing them at deleted software is worse than the usual stale-string cost.

### Reproduce

1. Open a project that has no stored requirements or no vendors.
2. Go to `02 Compliance matrix`, then `03 Comparative statement`.

**Reproduces:** always

```
grep -rn "Streamlit portal" web/src
web/src/pages/ComparativeStatement.tsx:47
web/src/pages/ComplianceMatrix.tsx:113
```

### Environment

- Branch / commit: `remove-streamlit-portal` @ `028e33f`
- Python / Node: 3.12.3 / v24.15.0
- Provider: n/a — static copy
- Data: any project with an empty matrix or statement

### Notes

- **2026-08-05** — Worth a `grep -rn "Streamlit\|portal" web/ docs/ README.md`
  as part of the fix; these two were found by reading the empty-state branches
  reached via BUG-001, not by an exhaustive sweep, so there may be more.

### Fix

- **Spec:** [`docs/superpowers/specs/2026-08-05-tracked-bugs-001-004-design.md`](docs/superpowers/specs/2026-08-05-tracked-bugs-001-004-design.md) §1.4
- **Plan:** [`docs/superpowers/plans/2026-08-05-tracked-bugs-001-004.md`](docs/superpowers/plans/2026-08-05-tracked-bugs-001-004.md), Task 6
- **Ledger:** [`.superpowers/sdd/2026-08-05-tracked-bugs-001-004/progress.md`](.superpowers/sdd/2026-08-05-tracked-bugs-001-004/progress.md) — untracked, since `.superpowers/` is gitignored; not a broken link, just not in git
- **Design change:** settled in spec §1.4 — there was no open design question
  to settle for this one. Both known strings, plus every other live hit found
  by the repo-wide sweep (`grep -rni "streamlit\|portal"` across `web/src`,
  `api/`, `procurement/`, `shared/`, `cost_estimation/`, `docs/`, `README.md`,
  `CLAUDE.md`), become pointers to `01 Set up & ingest`, the screen that now
  owns ingestion. Historical hits under `docs/superpowers/specs/` and
  `docs/superpowers/plans/` are left alone, per the plan's Task 6 note and
  `CLAUDE.md`'s own rule — rewriting a spec to match the present is how it
  stops being evidence of what was designed at the time.
- **Commit / PR:** `d3cfa61`
- **Test:** none — deliberate; see the sweep. A string assertion on
  empty-state copy would be a change-detector test, and the repo-wide sweep —
  re-run after the fix and confirmed to leave only the historical hits listed
  in the task's own report — is the better guard, per this tracker's header
  rule and Task 6's independently-reproduced sweep.
- **Verified:** yes. Task 6's sweep re-run came back clean (only historical
  hits remain); the regression suite stayed green through the change (Python
  893/3, web 23/23), and the task additionally added 5 behaviour tests for a
  banner-placement defect folded in from Task 4 (reverting the `.tsx` changes
  reproduces 3 failures, so those are non-vacuous). Re-verified in this
  task's full-suite run (below).

---

## BUG-004 — A deployment with no `LLM_PROVIDER` silently ingests with the mock provider

- **Severity:** S1
- **Area:** `api/main.py`
- **Status:** Closed
- **Reported:** 2026-08-05
- **Reporter:** found while verifying what a client receives in the Docker image

### What happens

`api/main.py:394` resolves the provider as:

```python
effective = provider or os.getenv("LLM_PROVIDER", "mock").lower()
```

The default is `mock`. `PROVIDER_KEYS["mock"] is None`, so `_provider_ready`
returns `True` without a key, the readiness gate passes, and ingestion runs to
completion against `shared/llm/mock_client.py`.

A container started with no `.env` — the exact state a client is in before they
configure anything — therefore accepts `POST /ingest`, returns `200`, and writes
fabricated extractions into the authoritative store. The stored record is
**indistinguishable from a real one**: `extraction_status: ok`, `notes: null`,
and `extractor: llm:tech_facts_v1` — the same extractor string a real Anthropic
run writes. Nothing in the store, the compliance matrix or the comparative
statement says the numbers are synthetic.

### What should happen

`mock` must not be reachable as an *implicit* default outside the test suite.
Either the env-var default becomes unset — making a provider-less deployment a
`400` the way an unconfigured `anthropic` already is — or selecting `mock`
requires an explicit opt-in, and every fact it produces is marked synthetic so
it can never be read as an extraction.

This violates two invariants in `CLAUDE.md` at once: "Missing data is never
coerced to a passing or zero value", and "A failed extraction ... always records
why in `DocumentRecord.notes`." An absent provider is missing data, and it is
currently coerced into a passing value with an empty `notes`.

### Reproduce

1. `docker run -d --name keytest -p 8399:8000 rahuljana/procurement-review:1.0.0`
   — no `--env-file`, no `LLM_PROVIDER`, no API key.
2. Add a document with no cached extraction, so the run cannot be a cache hit:
   `docker exec keytest sh -c 'cp /data/projects/gas-14/vendors/ADPOWER/BOM.pdf /data/projects/gas-14/vendors/ADPOWER/NEW-uncached-doc.pdf'`
3. `curl -X POST -H "Content-Type: application/json" -d '{}' http://localhost:8399/api/projects/gas-14/ingest`
4. Read the new document's record in `store/documents.json`.

**Reproduces:** always

```
POST /ingest -> HTTP 200

  path:              vendors/ADPOWER/NEW-uncached-doc.pdf
  doc_class:         other
  classified_by:     llm
  extractor:         llm:tech_facts_v1
  extraction_status: ok
  notes:             None

No key was present in the container. `classified_by: llm` and
`extractor: llm:tech_facts_v1` are what a real run records.
```

### Environment

- Branch / commit: `remove-streamlit-portal` @ `028e33f` plus uncommitted Docker changes
- Python / Node: 3.12.3 / v24.15.0
- Provider: none configured — that is the bug
- Data: `projects/gas-14`, the bundled sample

### Notes

- **2026-08-05** — The comment above this code (`api/main.py:382`) shows the
  hazard was already understood once: "an unvalidated server default wrote a
  whole store of failed extractions while reporting `has_results: true`". The
  validation added then checks that the *effective* provider is known and
  configured — but `mock` satisfies both, so the check passes and the default
  slips through the guard built to catch it.
- **2026-08-05** — On an already-ingested project the damage is bounded by the
  extraction cache: every document is a cache hit, no mock call is made, and
  only `generation` bumps (observed 2 → 3). The exposure is uncached
  documents — i.e. every document of a client's first real project.
- **2026-08-05** — Blocks distribution. Do not ship the image to a third party
  until this is fixed or the client is told in writing that `LLM_PROVIDER` must
  be set before their first ingestion.

### Fix

- **Spec:** [`docs/superpowers/specs/2026-08-05-tracked-bugs-001-004-design.md`](docs/superpowers/specs/2026-08-05-tracked-bugs-001-004-design.md) §1.3
- **Plan:** [`docs/superpowers/plans/2026-08-05-tracked-bugs-001-004.md`](docs/superpowers/plans/2026-08-05-tracked-bugs-001-004.md), Tasks 1–2
- **Ledger:** [`.superpowers/sdd/2026-08-05-tracked-bugs-001-004/progress.md`](.superpowers/sdd/2026-08-05-tracked-bugs-001-004/progress.md) — untracked, since `.superpowers/` is gitignored; not a broken link, just not in git
- **Design change:** settled in spec §1.3 — `mock` stays reachable, but only
  when named explicitly; there is no implicit default any more. Every
  `os.getenv("LLM_PROVIDER", "mock")` became `os.getenv("LLM_PROVIDER")`
  (`api/main.py`, `shared/llm/factory.py`). An unconfigured `POST /ingest`
  (no payload provider, no env var) now returns **400** naming
  `LLM_PROVIDER` and leaves the store untouched; `_provider_state()` reports
  `provider: null, ready: false`; `get_client(None)` with nothing configured
  raises `ValueError` as an unreachable-from-a-correct-path backstop for the
  next provider-less caller; `cost_estimation/cli.py`'s bare `get_client()`
  call now surfaces that as a readable CLI failure (exit 1, message on
  stderr) instead of a traceback. Additionally, per §1.3's provenance
  paragraph, the `run.started` event now carries
  `detail={"client": type(client).__name__}`, so `MockLLMClient` is visible
  in the event log whenever it is deliberately selected.
- **Commit / PR:** `dfc3f37` (the 400 guard + `run.started` provenance),
  `9d3ba95` (the factory no longer defaults to mock; `cost-est` fails
  readably), `cddbea1` (test coverage for the literal-empty-string
  `LLM_PROVIDER` case, not just whitespace)
- **Test:** `tests/test_api_setup.py`
  (`test_ingest_without_provider_is_400_and_leaves_store_untouched`,
  `test_provider_state_reports_null_when_unset`,
  `test_explicit_mock_still_ingests`,
  `test_run_started_event_names_the_client_class`), `tests/test_llm_factory.py`
  (`test_unset_provider_raises`, `test_blank_provider_counts_as_unset`,
  `test_explicit_mock_still_works_with_provider_unset`), and `tests/test_cli.py`
  (`test_main_without_llm_provider_fails_readably`). The store-untouched
  assertion is checked directly — `generation` unchanged, `documents.json`
  and `events.jsonl` byte-identical before/after the rejected request.
- **Verified:** yes. Task 1: 877 passed / 3 skipped, reviewed. Task 2: 881
  passed / 3 skipped, reviewed (including two post-review corrections to
  test accuracy, documented in the task's own report). Re-verified in this
  task's full-suite run (below).

---

## BUG-005 — A missing FX rate is silently assumed to be 1.0, flipping the ranking

- **Severity:** S1
- **Area:** `procurement/normalize.py`, `procurement/models.py`
- **Status:** Closed
- **Reported:** 2026-08-05
- **Reporter:** Rahul Jana (client-reported)

### What happens

`Project.fx_rates` defaults to `{}` (`procurement/models.py:82`), so a project
has no conversion rates until someone sets them through
`PUT /api/projects/{slug}/fx-rates`. Nothing requires that step, and nothing
blocks ingestion or the comparison table if it is skipped.

When a rate is missing, `normalize_bid` does not decline to convert — it
converts at 1.0:

```python
# procurement/normalize.py:11
rate = fx_rates.get(bid.currency, 1.0) if is_foreign else 1.0
```

The bid is then ranked on that number. A EUR quotation is compared against a
USD target as if €1 = $1, which understates it by whatever the true rate is.
The vendor is made to look cheaper than they are, and with two vendors close
together **the cheapest-bid ordering inverts**.

An adjustment recording `"no FX rate for EUR; assumed 1.0"` is attached to the
normalized bid, so the assumption is not literally hidden — but it is a
footnote on a row whose headline number is already wrong, and
`ComparisonRow.normalized_total` carries no flag distinguishing a real
conversion from an assumed one.

This is the store invariant in CLAUDE.md — "Missing data is never coerced to a
passing or zero value" — being violated with a *passing* value: 1.0 is the one
multiplier that looks like a successful conversion.

### What should happen

Two separable things, and the second is the actual defect:

1. **A new project should start with a usable EUR rate** rather than none —
   the reported ask: seed `fx_rates` with `{"EUR": 1.08}`.
2. **A currency with no configured rate must not be silently converted at
   1.0.** It should be surfaced as unconvertible — `normalized_total` omitted
   (the invariant's "omitted, not emitted as `0`") and the row marked as
   needing a rate, so it cannot be ranked against converted bids as though it
   were comparable.

**Open design question, to settle before implementing:** a hardcoded
`{"EUR": 1.08}` is a *direction- and date-specific* constant, and seeding it
blindly trades one silent wrong number for another:

- 1.08 is EUR→USD. It is only meaningful while `target_currency` is `USD`
  (the default, `models.py:81`). For a GBP- or INR-denominated project the
  seeded rate is simply wrong, and being pre-filled makes it *less* likely to
  be questioned than an empty field.
- The rate is a snapshot, not a live quote. It needs a visible "as of" date in
  the setup UI, or it silently ages into the same class of error this bug is
  about.
- Seeding EUR does nothing for GBP, JPY, INR or any other currency, which keep
  the 1.0 assumption. Fixing (2) covers every currency; fixing only (1) covers
  one and leaves the trap armed for the rest.

Recommendation: treat (2) as the fix and (1) as a convenience default layered
on top, entered through the same validated path as a user-set rate and dated.

### Reproduce

Two vendors, no FX rates configured, target currency at its `USD` default —
the state every new project is in:

```python
from procurement.models import VendorBid
from procurement.normalize import normalize_bid
from procurement.compare import build_comparison

eur = VendorBid(vendor='EUROVEND', currency='EUR', base_price=1000.0, freight_included=True)
usd = VendorBid(vendor='USVEND',  currency='USD', base_price=1050.0, freight_included=True)
ns = [normalize_bid(b, 'USD', {}) for b in (eur, usd)]
build_comparison([eur, usd], ns, 'USD')
```

**Reproduces:** always

```
EUROVEND  normalized_total = 1000.0   ['no FX rate for EUR; assumed 1.0']
USVEND    normalized_total = 1050.0   []

Ranking shown to the buyer:
  EUROVEND   EUR 1000.0  ->  1000.0   (cheapest)
  USVEND     USD 1050.0  ->  1050.0
```

At 1.08 the EUR bid is really **$1080** — the most expensive of the two. The
table awards it as the cheapest. The buyer is shown a reversed ranking with no
error, no failed status, and `extraction_status: ok` on both rows.

### Environment

- Branch / commit: `fix-tracked-bugs-001-004` @ `272df60`, plus the
  uncommitted `has_results` change
- Python / Node: 3.12.3 / v24.15.0
- Data: synthetic bids above; no project fixture needed — the defect is in
  normalization, not extraction
- Config: `fx_rates = {}`, `target_currency = "USD"` — both defaults

### Notes

- **2026-08-05** — `tests/test_procurement_normalize.py::test_missing_fx_rate_is_surfaced`
  already asserts this behaviour (`assert abs(n.normalized_total - 1000.0) <= 0.01
  # assumed 1:1`). It is a characterization test: it pins the 1.0 assumption as
  intended rather than catching it. Fixing (2) means **rewriting that test**,
  not just adding one — expect it to go red, and read it as the specification
  it currently is before changing it.
- **2026-08-05** — `Object.keys(setup.fx_rates).length > 0` is one of the four
  steps in the setup checklist (`web/src/pages/Setup.tsx:151-157`), and
  `activeIndex` is the first *incomplete* one. Seeding `fx_rates` at creation
  marks "FX rates" done before the user has looked at it and advances the
  wizard straight to the next step — so the default would remove the very
  prompt that currently gets real rates entered. Whatever shape (1) takes has
  to keep that step live, e.g. by tracking "seeded" separately from
  "confirmed by a human".
- **2026-08-05** — Arithmetic stays in Python here (CLAUDE.md), so this is
  entirely a code fix; no extractor or prompt change is involved.

### Fix

- **Spec:** [`docs/superpowers/specs/2026-08-05-fx-rate-normalization-design.md`](docs/superpowers/specs/2026-08-05-fx-rate-normalization-design.md)
- **Plan:** [`docs/superpowers/plans/2026-08-05-fx-rate-normalization.md`](docs/superpowers/plans/2026-08-05-fx-rate-normalization.md), Tasks 1–8
- **Ledger:** [`.superpowers/sdd/2026-08-05-fx-rate-normalization/progress.md`](.superpowers/sdd/2026-08-05-fx-rate-normalization/progress.md) — untracked, since `.superpowers/` is gitignored; not a broken link, just not in git
- **Design change:** none beyond the spec as written — both open design
  questions above were settled in the spec itself before Task 1 began: (2) is
  the fix (`normalize_bid` refuses to convert without a usable rate,
  `normalization_status: "no_fx_rate"`), and (1) ships as a suggestion only —
  the setup form prefills `EUR 1.08` (dated, labelled "as of 2026-08-05 —
  check before saving") when no rate is set and the target is USD, but saves
  nothing until a human clicks Save.
- **Commit / PR:** `3dc1e5f` (Task 1 — `normalize_bid` refuses to convert
  without a usable rate), `6ba4338` (Task 2 — reject a non-positive rate at
  the API), `587227e` (Task 3 — `renormalize`, no LLM call), `3d4d5f7` (the
  ingest lock restored after a concurrent-session collision during Task 3's
  landing — see the ledger's INCIDENT note), `9879106` (Task 5 — carry the
  no-rate reason to `ComparisonRow` and the export), `2ee8309` (Task 6 round
  1 — keep the Normalised row with a reason), `cdca14c` (Task 6 round 2 — gate
  the no-fx-rate note on a known base too), `682f8e1` (Task 7 — suggest the
  dated EUR 1.08 rate without storing it), `88da033` (Task 8 — the 12-row
  two-run mutation matrix, and this closure itself; recorded here in a
  follow-up commit because a commit cannot cite its own hash).

  Two commits carry this plan's code under a message that names a different
  bug, because a parallel session on this branch committed while this plan's
  changes sat staged in the shared git index:
  - `cb8e673`, messaged `docs: file BUG-009 -- ...`, carries all of Task 4's
    implementation (`STORE_VERSION` 1→2, `migrate_normalization`,
    `create_project` stamping `store_version`).
  - `d2bfefe`, messaged `fix(project,api): serialise every project.json
    mutation (BUG-009)`, carries Task 6's web change (threading `note` through
    `PricedCells` so the statement's Normalised-row reason is actually
    visible).

  Both were verified content-correct byte-for-byte against what each task's
  own implementer and reviewer produced; only the commit message is wrong.
  History was not rewritten — another session was live in the same tree at
  the time and remains so.
- **Test:** `tests/test_procurement_normalize.py` (Task 1 — rewrites the
  characterization test that pinned the 1.0 assumption), `tests/test_api_setup.py`
  (Task 2 — 422 on a non-positive rate), `tests/test_renormalize.py` (Tasks 3
  and 4 — `renormalize`'s no-LLM-call, single-generation-bump, no-op-when-
  unchanged contract, plus `migrate_normalization`'s one-shot store upgrade),
  `tests/test_procurement_compare.py` and `tests/test_procurement_export.py`
  (Task 5), `tests/test_statement_pricing.py` (Task 6), `web/src/pages/Setup.test.tsx`
  (Task 7), and `tests/test_fx_normalization_lifecycle.py` (Task 8 — the
  12-row two-run mutation matrix below). Every row of the Task 8 matrix was
  verified non-vacuous by reinstating its named defect and confirming the
  intended row failed and no other row did, then reverting:

  | reinstated defect | row(s) that failed | other rows |
  |---|---|---|
  | `rate = fx_rates.get(bid.currency, 1.0)` in `normalize.py` | added-rate, changed-rate, withdrawn-rate, withdrawn-rate-statement rows; 4 of Task 1's own unit tests | unaffected |
  | drop the `rate <= 0` guard in `normalize.py` | set-to-0 row (its store-level defence-in-depth assertion; the API-level 422 assertion is Task 2's own gate and stays green) | unaffected |
  | always open a `transaction` in `renormalize`, unconditionally | read-repeatedly row (its direct, no-pending-change `renormalize` calls; `test_renormalize.py`'s own equivalent unit test also failed) | unaffected |
  | skip `migrate_normalization` in `_live_fact_vendors` | read-repeatedly row (the migration never ran, so `store_version` never advanced) | unaffected — Task 4's own `test_renormalize.py` tests call `migrate_normalization` directly and do not exercise this call site, so they stayed green; this row is the only place the wiring itself is checked |
  | restore `statement.py`'s bare `if norm_total is not None` gate | withdrawn-rate-statement row, plus 3 of Task 6's own `test_statement_pricing.py` tests | unaffected |

  Two rows needed strengthening during this verification because they passed
  against their own probe on the first attempt: the changed-rate row did not
  assert the pre-mutation (no-rate) state, so the `.get(rate, 1.0)` defect
  went unobserved; the set-to-0 row exercised only the API's 422 gate, never
  normalize.py's own guard, so the `rate <= 0` defect went unobserved. Both
  gaps were closed before this task's commit — see the test file itself.
- **Verified:** yes. Workstation: **950 passed, 3 skipped** (re-measured;
  derives the CI row as 943 passed, 10 skipped — see `CLAUDE.md`). Web: **36
  passed**, `npm run build` clean. `tests/test_pipeline_incremental.py`
  (the four cache-invalidation rows this plan deliberately does not
  duplicate) still passes unchanged.

---

## BUG-006 — Extraction reports no live progress while it runs

- **Severity:** S3
- **Area:** `web/src/pages/Setup.tsx`, `api/main.py`
- **Status:** Open
- **Reported:** 2026-08-05
- **Reporter:** Rahul Jana (client-reported)

### What happens

`POST /api/projects/{slug}/ingest` is a plain synchronous handler
(`api/main.py:386`). It calls `run_ingestion(...)` inline (`api/main.py:470`)
and returns only once the entire project has been classified, extracted and
compared. There is no background task, no job id, no polling endpoint, no SSE
and no websocket — `grep` for `BackgroundTask`, `asyncio` or any lock across
`api/` and `procurement/` returns nothing relevant.

The client matches that shape. `runIngestion` (`web/src/api.ts:115`) is one
awaited `fetch` with no timeout, and `IngestionStep` holds a single boolean
(`const [running, setRunning] = useState(false)`, `web/src/pages/Setup.tsx:581`)
set before the request and cleared after it. While it is true the user sees one
fixed sentence:

```tsx
// web/src/pages/Setup.tsx:644-648
{running && (
  <div className="banner" …>
    Extracting and comparing vendor bids…
  </div>
)}
```

No document count, no vendor name, no elapsed time, no way to tell a working
run from a wedged one. Screen `04 Extraction status` does not help mid-run
either: `ExtractionStatus.tsx:26` fetches once on mount and never re-polls, and
the document records it reads are written in a single transaction near the end
of the run (`snapshots.save_documents`, `procurement/pipeline.py:985`), so
until the run finishes there is nothing new for it to show.

The information the banner is missing already exists, and is already being
written **incrementally, per document**, while the run is in flight.
`events.append_event` opens the file in append mode and closes it per call
(`procurement/store/events.py:14-18`) — it is not buffered to the end of the
run. On the bundled sample's first run that is 33 `document.classified` and 32
`document.extracted` records landing on disk one at a time over roughly 13
minutes, each carrying its `target` doc_id and a `detail.status` of `ok` or
`failed`. `procurement/store/events.py:21` even provides
`read_events(root, slug, run_id)`, filtered by run. Nothing in `api/main.py`
calls it — there is no `/events` or `/progress` route in the whole file.

### What should happen

While a run is in flight the UI should show what it is doing: which document is
being read, how many of how many are done, and how many have failed so far.

The reporting path does not need to be invented — the per-document events are
already there. What is missing is (a) an endpoint that exposes them, and (b) a
run that outlives its HTTP request, so there is something to poll *against*.

**Open design question, to settle before implementing:** whether the run stays
synchronous.

- Keeping it synchronous and adding a read-only progress endpoint is the small
  change: `GET …/runs/{run_id}/events` over `read_events`, polled by the client.
  But the client only learns `run_id` when the POST returns, i.e. when the run
  is already over. Progress would have to be keyed on "the newest run", which
  is ambiguous the moment two runs overlap.
- Making the run a background job (returning `202` and a `run_id` immediately)
  fixes that and also fixes the reload hazard below, but it is the larger
  change and it puts a job-state question in front of the store: what
  `project.status` reads as while a run is in flight, given `status` is only
  assigned at `procurement/pipeline.py:1000`, after everything.

Either way the fix must not weaken the store invariants — progress reporting is
a read of the append-only event log, and must stay a read. This tracker entry
does not decide it.

### Reproduce

1. Ingest a project with a realistic document count and watch screen
   `01 Set up & ingest`.
2. Time the interval between pressing `Run ingestion` and the banner changing.

**Reproduces:** always

Measured on `projects/gas-14`, the bundled sample, from its own
`store/events.jsonl` — `run.started` to `run.finished` per run:

```
run 23674e3b28e1 (first)    761.9 s  (12 min 42 s)
  33 document.classified · 32 document.extracted · 1 document.skipped
  run.finished detail: {'extracted': 31, 'failed': 1, 'documents': 33,
                        'status': 'done_with_failures'}

run 8116af1e6564 (second)    10.0 s
  32 document.skipped · 1 document.extracted

For 761.9 seconds the UI showed the string "Extracting and comparing vendor
bids…" and nothing else, while the 69 events describing exactly what was
happening were appended to disk one by one.
```

### Environment

- Branch / commit: `fix-tracked-bugs-001-004` @ `0a3ed43`
- Python / Node: 3.12.3 / v24.15.0
- Provider: any — the gap is in reporting, not extraction. A slower provider
  makes it worse, not different.
- Data: `projects/gas-14` (the bundled sample), 33 documents

### Notes

- **2026-08-05** — Severity S3, not S2: the run itself completes correctly and
  two workarounds exist — `04 Extraction status` after the fact, and tailing
  `projects/<slug>/store/events.jsonl` during. The second is not a user-facing
  workaround, only an operator one.
- **2026-08-05** — The reload hazard is real but **it is not the dangerous
  one**, and this note originally said otherwise. `running` is component state,
  so a refresh clears it while the server-side run continues, and the button
  re-enables — `canRun` reads only `noVendors`, `selected.ready` and `running`
  (`web/src/pages/Setup.tsx:606`). That path was then measured, and a
  *staggered* second run is safe: by the time it reaches its write transaction
  the first has long finished, so it merely redoes the work. What is not safe
  is a *simultaneous* second submission. Severity stays S3; the concurrency
  defect itself is now filed separately as **BUG-008**, which carries the
  measurements. This entry remains a missing-feature S3.
- **2026-08-05** — `sendJson` sets no timeout (`web/src/api.ts:28-38`), so the
  browser's own limits are the only ceiling on how long that request may hang.
  Whatever shape the fix takes should stop relying on a single long-lived
  request holding the UI's only piece of state.

---

## BUG-007 — A partially-failed run offers only project-wide retry, never one document or one vendor

- **Severity:** S3
- **Area:** `web/src/pages/ExtractionStatus.tsx`, `api/main.py`, `procurement/pipeline.py`
- **Status:** Open
- **Reported:** 2026-08-05
- **Reporter:** Rahul Jana (client-reported)

### What happens

A run ends `done_with_failures` when some documents extracted and some did not
(`procurement/pipeline.py:1000-1001`). Screen `04 Extraction status` then
reports the damage precisely — per vendor, per document, with the stored note
rendered verbatim (`ExtractionStatus.tsx:157-159`) and a separate column for a
failed second pass (`ExtractionStatus.tsx:169-182`).

It reports and stops there. `DocumentRow` (`ExtractionStatus.tsx:140-186`)
renders six cells — File, Classified, Read as, Status, Second pass, Facts — and
not one of them is actionable. `VendorPanel` (`ExtractionStatus.tsx:65-138`)
has an `actions` slot, and it holds a counts summary, not a control. The screen
that knows what failed cannot retry any of it.

Every retry control lives on `01 Set up & ingest`, and both are project-wide:

- `Run ingestion` (`Setup.tsx:711-717`) → `runIngestion(slug, provider)`.
- `Force full re-extraction` (`Setup.tsx:722-731`) → the same call with
  `force: true`, behind a `window.confirm` that names the cost
  (`Setup.tsx:624-629`).

Nothing below them can be scoped either. `run_ingestion(root, slug, client,
pdf_fallback=None, force=False)` (`procurement/pipeline.py:612`) takes no
vendor and no document argument; the endpoint reads only `provider`
(`api/main.py:404`) and `force` (`api/main.py:431`); `runIngestion` sends only
those two (`web/src/api.ts:115-127`).

The nearest thing to a targeted retry is a side effect of the cache key rather
than a control:

```python
# procurement/pipeline.py:755-760
unchanged = (prior is not None
             and prior.content_sha256 == doc.content_sha256
             and prior.prompt_version == expected_version
             and prior.extraction_status == "ok"
             and (route != "datasheet" or prior.vocabulary_sha == vocab_sha))
```

`prior.extraction_status == "ok"` means a plain re-run *does* retry failures
automatically, and `prior.secondary_status == "ok"` does the same for the second
pass (`procurement/pipeline.py:765-769`). So the failures are recoverable — but
only by re-running the whole project, and only as an invisible side effect the
UI never explains. The user is shown a list of failures and given one button
that does not mention them.

### What should happen

Two controls, both attached to the screen where the failures are actually
visible:

1. **Per document** — a retry on the row of a document whose `status` is
   `failed` (or whose `secondary_status` is `failed`), re-extracting exactly
   that document.
2. **Per vendor** — a retry in the `VendorPanel` header, re-extracting that
   vendor's documents and no one else's.

Both need a scope parameter that does not exist today: `run_ingestion` needs to
accept a vendor or a document set, the ingest payload needs to carry it, and
the client needs to send it.

**Open design question, to settle before implementing:** what a scoped run does
to the parts of `run_ingestion` that are deliberately whole-project.

- Classification, `resolve_supersession` and `pick_quote` all run across every
  vendor before extraction (`procurement/pipeline.py:628-664`). A vendor-scoped
  run that skips them may pick a different quotation than a full run would; one
  that keeps them is only "scoped" in its extraction pass.
- `_prune_orphan_facts`, the stale-vendor sweep and
  `compliance.evaluate_project` (`procurement/pipeline.py:991`) run
  unconditionally inside the write transaction — BUG-002's Step 1b measurement
  established that for `force`, and a scoped run must not be the thing that
  makes them conditional. **A stored collection contains exactly the records of
  its currently-live sources** (CLAUDE.md) is a whole-store invariant; a
  per-vendor write must still leave the whole store satisfying it.
- `generation` bumps once per write transaction, never once per file. A
  per-document retry is still one transaction and must still bump exactly once.
- Whether a scoped retry is `force`-like (ignore the cache for the named
  documents) or plain (let the cache decide, which for a `failed` document
  means re-extract anyway). These differ only for a document cached `ok` that
  the reviewer believes is wrong — which is precisely the case BUG-002's
  project-wide `force` was added for, at full project cost.

This tracker entry does not decide it. Whatever shape it takes, the plan needs
the same two-run mutation matrix BUG-002's fix used
(`tests/test_pipeline_force.py`) — a scope parameter is a new way to write a
subset of the store, and subset writes are where phase 2's pruning defect lived.

### Reproduce

1. Ingest a project until a run ends `done_with_failures`.
2. Open `04 Extraction status` and find the failed row.
3. Look for any control that acts on that row, or on that vendor.

**Reproduces:** always

From `projects/gas-14`'s own event log — the sample shipped with a failure and
recovered from it exactly this way:

```
run 23674e3b28e1  status done_with_failures
  document.extracted  target c7e2cb129356  detail {'status': 'failed',
                                                   'doc_class': 'quotation'}
  → 1 failed of 33

run 8116af1e6564  status done
  document.extracted  ×1     ← c7e2cb129356, retried
  document.skipped    ×32    ← everything else, 'unchanged'

The one failed document was recovered. The only way to ask for it was to
press the project-wide "Run ingestion" button, which walked all 33 documents
to re-extract 1. No control exists that names c7e2cb129356, and none names
its vendor.
```

### Environment

- Branch / commit: `fix-tracked-bugs-001-004` @ `0a3ed43`
- Python / Node: 3.12.3 / v24.15.0
- Provider: any — the gap is in scoping, not extraction
- Data: `projects/gas-14` (the bundled sample), whose two runs carry the
  failure and its recovery

### Notes

- **2026-08-05** — Severity S3, not S2: a workaround exists and it works — a
  plain `Run ingestion` retries failed documents automatically, and on the
  sample that cost 10 s against the first run's 762 s, because every healthy
  document was a cache hit. The complaint is that the workaround is invisible,
  unscoped, and lives on a different screen from the failures.
- **2026-08-05** — The cheap-retry claim above holds only while the cache holds.
  A vendor whose documents all failed re-reads all of them on every subsequent
  run, and a reviewer who reaches for `Force full re-extraction` to fix one bad
  document re-spends the entire project's LLM cost. Per-vendor scope is the
  control that makes the expensive case proportionate.
- **2026-08-05** — Related but distinct from BUG-001: that one is about
  *reaching* the review screens with incomplete data, settled with the
  `has_results` gate plus a `status` banner. This one is about *repairing* the
  incomplete data once you can see it. A fix here should reuse BUG-001's
  `done_with_failures` banner as the entry point rather than adding a second
  vocabulary for the same state.
- **2026-08-05** — Depends on BUG-006 in practice, not in code: a per-document
  retry that blocks the UI with no progress for the length of one extraction is
  a smaller version of the same complaint. Whichever ships second inherits the
  other's shape, so settle BUG-006's synchronous-vs-background question first.
- **2026-08-05** — **Blocked on BUG-008, and this is the load-bearing
  dependency.** A scoped retry is a short, write-dominated run — seconds of
  extraction against the same final write transaction a full run performs. That
  is precisely the run shape in which BUG-008's measurements produced actual
  data loss (an unparseable `facts.json`, `documents.json` emptied), whereas the
  extraction-dominated full-run shape never did across 32 staggered trials.
  Shipping per-vendor or per-document retries lets a reviewer fire two of them
  seconds apart on one project, which moves the product out of the shape that
  has been accidentally protecting it. Do not ship this without the serialisation
  guard BUG-008 asks for.

---

## BUG-008 — Nothing serialises two ingestion runs, and the snapshot writer shares one temp filename

- **Severity:** S3
- **Area:** `procurement/store/layout.py`, `procurement/store/snapshots.py`, `api/main.py`
- **Status:** Fixed
- **Reported:** 2026-08-05
- **Reporter:** found by testing BUG-006's untested concurrency note

### What happens

Two things compose badly, and neither is guarded.

**1. The snapshot writer shares one temp filename per path.**

```python
# procurement/store/layout.py:60
tmp = path + ".tmp"
```

The temp name is derived from the destination, not from the writer. Two
concurrent writers of the same snapshot therefore open the *same* temp file:
the second `open(tmp, "w")` truncates what the first is still writing, both
write into it at their own offsets, and both then `os.replace` it onto the
destination. The `except BaseException: os.remove(tmp)` cleanup
(`layout.py:67-69`) makes it worse rather than better — a failing writer
deletes the temp file the *other* writer is about to rename from.

**2. Nothing stops two runs from overlapping.** `def ingest(...)`
(`api/main.py:386`) is a synchronous FastAPI handler, so it runs in the
threadpool and two requests execute concurrently in one process. There is no
lock, no run registry and no busy check — a `grep` for `lock`, `threading` or
`asyncio` across `api/` and `procurement/` returns nothing. `run_ingestion`
(`procurement/pipeline.py:612`) takes no exclusivity of its own, and
`bump_generation` (`procurement/store/snapshots.py:19-23`) is a
read-modify-write over `project.json` with no guard either.

The client makes overlap reachable rather than theoretical: `running` is
component state (`web/src/pages/Setup.tsx:581`), so a reload re-enables the
button mid-run, and nothing server-side rejects the second request.

### What should happen

One ingestion run per project at a time, refused (`409`) rather than queued —
a second concurrent run is always either a mistake or a duplicate submission,
and running it costs a full project's LLM spend for a result the first run is
already producing.

Independently of that, `atomic_write_json` should not be corruptible by
concurrency it does not control. A per-writer temp name (`path + f".{os.getpid()}.{uuid4().hex}.tmp"`)
makes the primitive safe on its own terms; the `os.remove` cleanup then only
ever deletes the writer's own file. This is worth doing even with a lock in
place — the lock is the correctness fix, the unique temp name is the reason a
future caller cannot reintroduce the same defect.

**Open design question, to settle before implementing:** where exclusivity
lives. An in-process `threading.Lock` keyed by slug is enough for the single
`uvicorn` worker the image ships, and wrong the moment anyone runs two workers
or two containers against one volume. A lockfile in the project directory
survives both but needs a stale-lock policy for a crashed run — and a crashed
run is exactly what leaves one behind. This tracker entry does not decide it.

### Reproduce

**Reproduces:** deterministically for the invariant breach; the data-loss
variant is timing-dependent (see Notes). Measured on Linux inside the shipped
image (`docker run … procurement-review:latest`), and separately on the Windows
workstation.

*The primitive, in isolation — two threads, one path, 25 races:*

```
E1  concurrent atomic_write_json on ONE path        [linux / python 3.12.13]
     25x  MIXED A+B (valid JSON, wrong content)
     writer exceptions: {'FileNotFoundError': 25}

Every race produced a destination file holding records from BOTH writers.
It parses. Nothing downstream can tell it is wrong.
(Windows: 24x mixed, 1x destination missing, 43 PermissionErrors.)
```

*End to end — two `run_ingestion` calls, 28 documents, 4 vendors, offset 0,
30 repetitions, with a provider-latency client so the run is
extraction-dominated like a real one:*

```
E6  offset 0.00s, realistic run shape, 30 reps      [linux]
  reps with ANY invariant breach : 30/30
  reps with actual DATA LOSS     : 0/30
  run_ingestion exceptions       : {'B:FileNotFoundError': 14,
                                    'A:FileNotFoundError': 16}
    30/30  generation=1        (invariant: exactly 2 after two runs)
    30/30  2 runs started / 1 finished
```

*The data-loss variant, from the write-dominated sweep (1 of 3 at a ~10%
stagger):*

```
  UNPARSEABLE vendors/ADPOWER/facts.json: JSONDecodeError
  documents.json has 0 records, fixture has 28
  vendors with NO facts snapshot: ['HYOSUNG', 'KERUI', 'SIEMENS']
  generation=0        <- the store reads as though nothing ever ran
  2 runs started, 0 finished
```

### Environment

- Branch / commit: `fix-tracked-bugs-001-004` @ `3541076`
- Measured on: `linux / python 3.12.13` inside `procurement-review:latest` (the
  shipped image), and `win32 / python 3.12.3` on the workstation
- Provider: `RoutingClient` (the test double) and a latency-injecting subclass
  — no key needed; the defect is in the store writer, not extraction
- Data: synthetic, 28 documents across 4 vendors

### Notes

- **2026-08-05** — Filed after BUG-006's note flagged this as untested. The
  answer is **not** the S1 that note anticipated, and the reason is worth
  recording: at offset 0 the two runs collide on the *first* shared write, one
  dies immediately, and the survivor proceeds alone. The early crash is
  accidentally protective — it removes the second writer before the large
  collections are written. Damage is therefore loud (a `502` from
  `api/main.py:473`) rather than silent.
- **2026-08-05** — Severity S3, not S1, on measured reachability: 0 of 30
  offset-0 trials lost data, and 0 of 32 staggered trials across eight offsets
  (0.05s to 1.6s, plus 50% and 90% of run length) breached anything at all. The
  data-loss case was reproduced only in the write-dominated shape, where the
  final write transaction is most of the run. **Re-triage to S1 if** BUG-007's
  scoped retries ship — they make that shape the normal one.
- **2026-08-05** — A lost `generation` bump does *not* cause stale reads. The
  index's freshness key is `generation` **plus** the mtime_ns and size of every
  snapshot (`procurement/store/index.py:7-11, 35-50`), and the snapshot files
  did change, so `is_stale` still fires and the index rebuilds. The breach is to
  the audit trail — "generation bumps once per write transaction" (CLAUDE.md) —
  and to anything that later trusts the counter alone, not to what a reviewer
  currently sees.
- **2026-08-05** — No fix is proposed here and no regression test has been
  written yet. When one is: it belongs with the store tests rather than the
  pipeline ones, because the primitive in E1 fails on its own without any
  pipeline involved, and that is the smaller failing test.

### Fix

- **Spec:** none — the investigation above *is* the design record, and the one
  open question (where exclusivity lives) was settled in conversation, not in
  a spec. The choice and its limits are stated below.
- **Plan:** none — two files, one behaviour each; a plan would have restated
  the Fix block.
- **Design change:** two independent halves, deliberately not one.
  1. **The primitive is made safe on its own terms.** `atomic_write_json` now
     builds a unique temp name per write —
     `f"{path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"` — so concurrent writers
     of one destination each own their file and `os.replace` picks the winner
     atomically. A concurrent write is last-writer-wins instead of corrupt.
     `_replace_with_retry` was needed alongside it: POSIX `rename(2)` never
     fails when two threads replace one destination, but Windows `MoveFileEx`
     raises `PermissionError`, measured at 3 collisions in 15 races on the
     workstation. It retries the *rename* only, never the write, against the
     writer's own untouched temp. The loop is a no-op on Linux.
  2. **Runs are serialised per project.** A `threading.Lock` per slug in
     `api/main.py`, acquired non-blocking; a second concurrent run gets
     **409** rather than being queued, since it is always a duplicate or a
     mistake and executing it re-spends the project's LLM cost for a result
     already in flight. Released in `finally`, so the 502 path cannot strand
     it.

  **The limit of (2), stated because it is otherwise silent:** an in-process
  lock covers the shipped deployment exactly — one `uvicorn` worker
  (`Dockerfile` CMD, no `--workers`), one container, one volume. It does not
  serialise two containers on a shared volume, a multi-worker uvicorn, or
  `cost-est` writing alongside the API. Those need a lockfile with a
  stale-lock policy, which was considered and deferred: a crashed run holding
  a lockfile blocks a client who has no operator to clear it, and that
  trade-off deserves its own decision rather than being smuggled in here.
  Half (1) is what protects those cases from *corruption* today; what they
  still lack is mutual exclusion.
- **Commit / PR:** `5080a2c`
- **Test:** `tests/test_store_layout.py`
  (`test_concurrent_writers_never_interleave_into_one_file`,
  `test_concurrent_writers_do_not_fail_each_other`,
  `test_every_concurrent_writer_renames_its_own_temp_away`) and
  `tests/test_api_setup.py`
  (`test_a_second_ingest_while_one_is_running_is_rejected`,
  `test_the_run_lock_is_released_so_a_later_ingest_still_works`,
  `test_a_run_does_not_block_ingestion_of_a_different_project`).

  All six were mutation-checked by reinstating each defect one at a time and
  confirming the owning test failed: no lock at all; lock released only on
  success rather than in `finally`; one global lock instead of per-slug; the
  shared temp name; no cleanup of the temp on failure; a leaked temp on the
  success path. Six of six caught.

  Two test defects were found *by* that pass and are worth recording, since
  both were tests that looked fine and guarded nothing:
  - `_race_two_writers` originally read the destination once, after all 15
    rounds. A round that landed a corrupt file was overwritten by the next
    one, so reinstating the original defect left the interleaving test green.
    It now inspects the destination after **every** round.
  - `test_failed_serialization_leaves_no_orphan_tmp` — a **pre-existing** test
    — asserted `not os.path.exists(p + ".tmp")`. Unique temp names made that
    name one the writer never creates, so the assertion became unfalsifiable:
    this fix silently disarmed an existing guard. It now asserts over the
    directory contents, and the mutation pass confirms it fails when the
    cleanup is removed.
- **Verified:** yes. Python 925 passed / 3 skipped, web 31/31 — note that the
  925 includes an untracked `renormalize` change from concurrent work in the
  tree, not just this fix; the six tests above are this change's contribution.
  The original E1 probe was re-run on Linux inside the shipped image after the
  fix: **0 of 25 rounds landed a mixed or corrupt file (was 25 of 25), no
  writer raised, and no temp file was left behind.**

---

## BUG-009 — A `PUT /fx-rates` that returned 200 is discarded by a run finishing in the same window

- **Severity:** S3
- **Area:** `api/main.py`, `procurement/project.py`, `procurement/store/snapshots.py`
- **Status:** Fixed
- **Reported:** 2026-08-05
- **Reporter:** found while closing BUG-008 — noted there as untested, then tested

### What happens

BUG-008 serialised ingest against ingest. It did nothing about every *other*
writer of `project.json`, and there are five, each an unguarded
read-modify-write of the whole file:

| where | reads then writes |
|---|---|
| `api/main.py:347` | `requirements_file` |
| `api/main.py:389` | `fx_rates` |
| `procurement/project.py:110` | `vendors` |
| `procurement/pipeline.py:999-1002` | `status`, at the end of a run |
| `procurement/store/snapshots.py:19-23` | `generation`, once per transaction |

`save_project` writes the entire document (`procurement/project.py:28-33`), so
whichever writer saves last wins the whole file — including the fields it read
before the other writer changed them.

The reachable case: a reviewer sets FX rates while an ingestion run is
finishing. `run_ingestion` reloads the project at `pipeline.py:999`, sets
`status`, and saves at `1002`. An FX-rate write that lands between that reload
and that save is acknowledged with **200**, is visible on disk immediately, and
is then overwritten by the run's save — which is holding the `fx_rates` it read
a moment earlier.

Nothing reports the loss. The endpoint already returned success, and
`_setup_state` (`api/main.py:393`) reads back the state *before* the run's save
lands, so the response the user sees contains the rates they just set.

### What should happen

A write to `project.json` should not silently discard a concurrent one. Either
every mutation of a project takes the same per-slug lock BUG-008 introduced for
runs, or `save_project` stops being a whole-document overwrite and each caller
writes only the field it owns.

**Open design question, to settle before implementing:** which of those two.
Extending the lock is small and consistent with BUG-008, but it makes an
ordinary settings `PUT` block for the length of a 13-minute run — or fail with
a 409, which for a settings screen is a worse experience than for a duplicate
ingest. Field-level writes avoid blocking entirely and are the more correct
model for a document several actors legitimately edit, but they change the
shape of every `save_project` caller and need care around `generation`, which
is the one field that must stay a true read-modify-write. This tracker entry
does not decide it.

### Reproduce

**Reproduces:** deterministically when the window is hit; the window is narrow,
so hitting it by chance is timing-dependent (see Notes).

*Forced — the run held just before its `save_project`, an FX write allowed to
complete in the gap, then released. Same result on both platforms:*

```
B  forced: fx write inside the run's read-modify-write gap
  fx_rates immediately after the PUT: {'EUR': 1.08}      <- PUT returned 200
  fx_rates after the run finished  : {}                  <- silently discarded
  LOST
```

*Natural — `PUT /fx-rates` issued repeatedly throughout a forced run, then the
last 200-acknowledged rate compared against what is on disk:*

```
A  natural: fx-rates hammered throughout a run
  win32  batch 1:  4/10 trials lost an acknowledged write
  win32  batch 2:  0/10
  linux  batch 1:  0/10
```

### Environment

- Branch / commit: `fix-tracked-bugs-001-004` @ `3d4d5f7`
- Measured on `win32 / python 3.12.3` and `linux / python 3.12.13` (the shipped
  image)
- Provider: `mock`, with latency injected into `classify_structure` so the run
  is long enough for a concurrent request to land
- Data: synthetic, 16 documents across 4 vendors

### Notes

- **2026-08-05** — Severity S3, and the reason is a fix that already landed: a
  lost FX rate no longer produces a wrong ranking. Since BUG-005's normalize
  change (`3dc1e5f`), a currency with no usable rate returns
  `normalized_total = None` with `normalization_status = "no_fx_rate"` rather
  than converting at 1.0 — verified directly. So the failure surfaces as a
  vendor that will not convert, which is visible, instead of a vendor that
  looks cheapest. **Re-triage to S1 if** that refusal is ever softened back
  into a default rate: the same lost write would then silently reverse an
  award ranking, which is exactly BUG-005.
- **2026-08-05** — The natural rate is genuinely variable — 4/10 in one batch
  and 0/10 in two others on the same machines, under hammering far more
  aggressive than a human. Reported as a range rather than a single number
  because picking either end would misrepresent it. The forced test is the
  reliable evidence; the natural one only establishes that the window is
  reachable without contrivance.
- **2026-08-05** — `fx_rates` is the field a user is most likely to edit
  mid-run, but nothing about the mechanism is specific to it. `vendors` and
  `requirements_file` are lost the same way by the same window; they are
  simply less likely to be written while a run finishes.
- **2026-08-05** — Windows only, and not the same defect: a *reader* of
  `project.json` can raise `PermissionError` while a writer is replacing it,
  observed in this probe's output. POSIX `rename(2)` makes that impossible, so
  it does not affect the Linux deployment. Worth its own entry if the
  workstation ever becomes a supported target for concurrent use.
- **2026-08-05** — No fix attempted. `renormalize` (`procurement/renormalize.py`)
  writes per-vendor `facts.json` from the fx-rates path, and `run_ingestion`
  writes the same files, so the same class of stale write exists there too —
  loading facts, computing, then saving over whatever the run wrote in between.
  That was **not** tested here and should be, as part of whichever fix lands.

### Fix

- **Spec:** none — the investigation above is the design record; the one open
  question was settled by the reporter choosing to extend the per-slug lock.
- **Plan:** none — one primitive and five call sites.
- **Design change:** the lock lives in `procurement/project.py`, not in
  `api/main.py` where BUG-008's run lock sits. It has to: the race is
  `load → gap → save`, so a lock around only the save still leaves the read
  outside it, and `pipeline.py` and `snapshots.py` mutate the project too.

  `update_project(root, slug)` is a context manager holding a per-slug lock
  across load, mutation and save. All five read-modify-write sites now use it —
  `api/main.py` (requirements, fx-rates), `procurement/project.py`
  (`unpack_vendor_zip`), `procurement/pipeline.py` (the run's status write) and
  `procurement/store/snapshots.py` (`bump_generation`). `load_project` remains
  correct for a read, `save_project` for a project's first write; what must not
  return is the load/save pair spelled out at a call site.

  Two consequences worth stating:
  - **A settings write no longer blocks for the length of a run.** The critical
    section is one load-modify-save, not the whole ingestion, so the 13-minute
    stall the design note worried about does not arise. `PUT /fx-rates` during
    a run waits microseconds, not minutes, and there is no new 409.
  - **The lock is a plain `Lock`, deliberately not an `RLock`.** Re-entering it
    would let an inner update save and an outer update then save its own older
    copy over the top — the same lost write, harder to see. A plain Lock turns
    that mistake into a hang the suite catches. `renormalize` is therefore
    called *outside* the fx-rates critical section, since it opens its own
    transaction and bumps `generation` through the same lock.

  Scope limit, unchanged from BUG-008: this is in-process, matching the shipped
  single-worker image. Two containers on one volume are still unserialised.
- **Commit / PR:** `d2bfefe`
- **Test:** `tests/test_project_concurrency.py` — four tests, each forcing the
  window deterministically rather than racing for it:
  `test_an_fx_write_during_a_run_is_not_lost` (the reported case),
  `test_concurrent_field_writes_do_not_overwrite_each_other`,
  `test_concurrent_generation_bumps_are_not_lost`, and
  `test_a_vendor_upload_during_another_write_is_not_lost`.

  All six conversions were mutation-checked by reverting each to its old
  load/save pair, plus a seventh removing the lock from `update_project`
  itself. Six of six caught by their own test.

  Two test defects that pass surfaced, both worth recording:
  - The hook held the *first* project.json write of a run. A run writes it
    twice — `bump_generation` on the transaction's exit, then `status` — so the
    test was proving the bump atomic and never touched the status write the bug
    was actually reported against. Reverting that write failed no test. The
    hook now selects which write to hold.
  - Reverting `unpack_vendor_zip` and `bump_generation` was caught by nothing
    at all, in the whole suite. Both conversions were correct but unjustified
    until the last two tests above were written for them.
- **Verified:** yes. Python 936 passed / 3 skipped, web 32/32, no deadlock from
  the plain Lock. On Linux inside the shipped image, two concurrent
  `bump_generation` calls now leave `generation` at 2 in **0 of 25 trials
  wrong, against 25 of 25 wrong before the fix**. The original forced probe can
  no longer run at all — it hooks `pipeline.save_project`, which the fix
  removed from that path; the regression tests above replace it.

---

## BUG-010 — A run holds a vendor's facts across its whole extraction, then writes them back over anything else

- **Severity:** S1
- **Area:** `procurement/pipeline.py`, `procurement/renormalize.py`, `procurement/feedback.py`
- **Status:** Closed
- **Reported:** 2026-08-05
- **Closed:** 2026-08-06
- **Reporter:** found while closing BUG-009 — noted there as untested, then tested

### What happens

BUG-009 serialised `project.json`. `facts.json` has the same defect and none of
the guard, in three places:

| where | reads | writes | gap between them |
|---|---|---|---|
| `pipeline.py:833-942` | `base = load_facts(...)` | a whole `VendorFacts` | **an entire LLM extraction** |
| `renormalize.py:50-65` | `load_facts(...)` per vendor | the whole object | the compute loop |
| `feedback.py:30-35` | `load_facts(...)` | the whole object | short |

The first is the dangerous one. `run_ingestion` loads a vendor's stored facts,
performs the extraction — seconds with a mock, minutes with a real provider —
and then writes a freshly built `VendorFacts` carrying fields copied off the
copy it read *before* the extraction started:

```python
# procurement/pipeline.py:937-952
normalized = (normalize_bid(
    VendorBid.model_validate(resolved["commercial"]),
    project.target_currency, project.fx_rates).model_dump()
    if resolved["commercial"] else base.normalized)

snapshots.save_facts(root, slug, VendorFacts(
    ...
    # carried or a re-extraction silently discards the reviewer's
    # judgement — vocabulary_sha's defect, one field over
    technical_feedback=base.technical_feedback))
```

Both carried fields are correct **sequentially** — the comment on
`technical_feedback` is describing a real defect it fixes. Concurrently they
are how the write is lost: anything written to that vendor's facts during the
extraction is overwritten by `base`. `project` is likewise the copy loaded at
`pipeline.py:616`, so `project.fx_rates` on line 939 is the rate as it was when
the run *started*.

This needs no narrow window. The gap is the extraction itself, so "did it
during the run" is enough — which is exactly what a reviewer does.

### What should happen

A run must not write back a field it read minutes earlier and did not itself
change. Whatever shape the fix takes, the two carried fields are the ones that
matter: `technical_feedback`, which the run never modifies at all, and
`normalized`, which it should derive from the *current* rate rather than the
one it started with.

**Open design question, to settle before implementing:** the BUG-009 answer
does not transfer. Holding a per-vendor lock across the extraction would make
it correct and make a reviewer wait minutes for a feedback save — the
13-minute stall that fix specifically avoided. The plausible answers are
instead to re-read the stored facts immediately before the save and merge
field-wise (short critical section, but the merge rule has to be written down:
which fields the run owns, which it must never touch), or to stop writing whole
`VendorFacts` objects and write only the fields the run actually produced.
This tracker entry does not decide it.

### Reproduce

**Reproduces:** always, on both platforms, with no forcing hook — start a
forced run, issue the write partway through, wait for the run to finish.

*H1 — a reviewer's feedback note, `PUT /vendors/KERUI/feedback` mid-run,
acknowledged 200 with the note echoed back:*

```
H1  reviewer feedback written during a run
  linux  8/8 trials    H1 FEEDBACK LOST
  win32  5/8 trials    H1 FEEDBACK LOST
```

*H2 — `PUT /fx-rates` mid-run, then every vendor's stored `normalized`
compared against what `normalize_bid` produces from its own stored
`commercial` and the project's stored rate:*

```
H2  FX change + renormalize during a run
  linux  8/8 trials    H2 STALE normalized for 2 vendor(s)
```

*H2, severity variant — a rate that already existed is **corrected** mid-run
(1.08 -> 1.15), so a stale overwrite leaves a plausible number rather than a
blank:*

```
  KERUI: stored=1080.0  should be=1150.0  (project says EUR=1.15)
  ... 8 of 8 trials identical

trials where the stored total is a WRONG NUMBER (not a blank): 8/8
```

### Environment

- Branch / commit: `fix-tracked-bugs-001-004` @ `682f8e1`
- Measured on `linux / python 3.12.13` (the shipped image) and
  `win32 / python 3.12.3`
- Provider: `mock`, with latency injected into `classify_structure`, and — for
  H2 only — patched to return a real `EUR 1000` quotation (see Notes)
- Data: synthetic, 12 documents across 3 vendors

### Notes

- **2026-08-05** — S1, on the severity variant rather than the base case. With
  no rate configured, a stale overwrite yields `normalized_total = None` and
  the vendor visibly drops out of the ranking — bad, but loud. When a rate is
  *corrected* mid-run the stale value is a real number: the store then holds
  `1080.0` while the project holds the rate that produces `1150.0`, and the
  comparison ranks on the stale figure with nothing to indicate it. That is a
  wrong award decision from a silent write, which is what S1 is for.
- **2026-08-05** — **H2 first measured 0/8 and that result was worthless.** The
  stock mock returns `{}` for every schema, so commercial facts came back
  `currency=""`, `base_price=0.0`, and `normalize_bid` yields `0.0` whatever
  the rate is — the fixture could not have detected the defect it was written
  to find. Caught by inspecting the stored facts rather than trusting the clean
  number, and the probe now refuses to report a pass unless at least one vendor
  has a rate-sensitive price. Any future work here should keep that guard: a
  concurrency probe that cannot fail is indistinguishable from one that passes.
- **2026-08-05** — H1 reproduces on Linux more reliably than on Windows (8/8
  vs 5/8). Linux is the deployment target, so the higher number is the one that
  matters; the Windows shortfall is timing, not a difference in the defect.
- **2026-08-05** — Not covered by BUG-008's run lock or BUG-009's project lock.
  The run lock serialises runs against each other, and neither `PUT /feedback`
  nor `PUT /fx-rates` is a run; the project lock covers a different file.
  `save_feedback` and `renormalize` each hold their own load-modify-save window
  as well (the table above), so the reverse direction — a settings write
  clobbering what a run just extracted — is the same defect seen from the other
  side. That direction was **not** measured here.
- **2026-08-05** — No fix attempted, and no regression test written yet. When
  one is, `tests/test_project_concurrency.py` is the file it belongs beside;
  the forcing helper there is not needed, since the window is wide enough to
  hit by simply issuing the write during a run.
- **2026-08-06** — **Correction to the report above: `base` was re-loaded PER
  DOCUMENT, not once per run.** The table says `pipeline.py:833-942` reads
  `base = load_facts(...)` and writes a whole `VendorFacts`, which reads as one
  read/write pair spanning the run. It sat inside the per-document loop, so a
  vendor with four documents had four load-extract-save cycles, each one its
  own lost-write window. That makes the defect *more* reachable than reported
  (four windows per vendor, not one) and it is why the fix converts the loop
  body rather than wrapping the run.
- **2026-08-06** — **Correction to the severity reasoning: the run-start
  `project` copy is the more dangerous half, not `technical_feedback`.** The
  report leads with the lost note. A lost note is recoverable — the reviewer
  can see it is missing and write it again. The stale `project.fx_rates` read
  at run start stores a *plausible wrong number*: `1080.0` sits in the store
  while the project holds the rate that produces `1150.0`, the comparative
  statement ranks on it, and nothing anywhere indicates a stale figure. That is
  the half that decides an award wrongly and in silence, and it is the reason
  this entry is S1.

### Fix

- **Spec:** [`docs/superpowers/specs/2026-08-05-facts-concurrent-write-design.md`](docs/superpowers/specs/2026-08-05-facts-concurrent-write-design.md)
- **Plan:** [`docs/superpowers/plans/2026-08-05-facts-concurrent-write.md`](docs/superpowers/plans/2026-08-05-facts-concurrent-write.md)
- **Design change:** the open question in the report — hold a lock across the
  extraction, or re-read and merge — was settled as **re-read and merge, with
  a written-down ownership rule**. `procurement/store/snapshots.py` grows two
  primitives:

  - `update_facts(root, slug, vendor, *, create=False)` — a context manager
    holding a per-`(slug, vendor)` lock across load, mutation and save, and
    **re-reading inside the lock**. A caller assigns only the fields it owns;
    every field it leaves alone keeps whatever is stored now, which is the
    whole point. `create=False` raises `LookupError` when nothing is stored,
    as a parameter rather than a check at the call site — a check outside the
    lock is a TOCTOU, since a vendor can be pruned between it and the acquire.
    It does **not** bump `generation`; `transaction` still does that, once per
    write transaction.
  - `facts_lock(root, slug, vendor)` — the bare lock, for the one writer whose
    write is conditional on the facts being *absent*
    (`migrate.migrate_dataset_json`) and so cannot be a read-modify-write.

  The ownership rule, now enforced by the call sites rather than by comment:
  `save_feedback` writes only `technical_feedback`; `renormalize` writes only
  `normalized`, recomputed inside the lock from a project re-read there;
  `run_ingestion`'s extraction save writes `commercial`, `technical`,
  `deviations`, `overrides`, `quotation_doc_id` and `normalized`, and **never
  assigns `technical_feedback` at all**; `_prune_orphan_facts` writes only the
  collections it prunes.

  Three consequences worth stating:
  - **The lock never spans an LLM call.** The extraction runs outside it and
    the merge happens under it, so a reviewer's feedback save waits
    microseconds, not the minutes the report's design note worried about.
  - **Per vendor, not per project.** Two vendors' facts are separate files with
    no cross-field rule between them, and a per-project lock would serialise a
    run against itself for nothing. `5d28b21` is the probe that fails under a
    per-project lock, added because the first version of the lock test passed
    under either.
  - **A plain `Lock`, deliberately not an `RLock`**, for `update_project`'s
    reason one file over: re-entering would let an inner update save and an
    outer then save its own older copy over the top — the very lost write this
    exists to prevent, only harder to see.

  Scope limit, unchanged from BUG-008 and BUG-009: in-process, matching the
  shipped single-worker image. Two containers on one volume are still
  unserialised.
- **Commit / PR:** `1a6bc60` (the primitive), `5d28b21` (the per-project-lock
  probe), `797bb19` (feedback), `639af24` + `867bc7b` (renormalize), `8ee8890`
  (the run's extraction save), `9ec3043` (the prune and the legacy import); the
  concurrent mutation matrix lands in this entry's own commit.
- **Test:** `tests/test_facts_concurrency.py` — twelve rows, each naming the
  invariant it defends and which task owns it. Per-task TDD structurally
  produced single-writer tests; **every defect this entry names needs two
  writers to see**, so this file is where they are observed together. Run-side
  rows issue the competing write from inside a patched `extract_bid` (the
  window *is* the extraction, so no hook is needed and nothing races);
  short-window rows use `_hold_facts_write`, the `_hold_first_project_write`
  shape from `tests/test_project_concurrency.py` retargeted at a facts path.
  Every rate-sensitive row calls `_assert_rate_sensitive` **before** asserting
  its outcome — see the 0/8 note above; a row written against the stock mock's
  `currency=""`, `base_price=0.0` cannot fail whatever the code does.

  The matrix was mutation-checked by reinstating each defect one at a time:

  | reinstated defect | rows that failed |
  |---|---|
  | `technical_feedback` carried off a pre-extraction copy in the run's save | H1, the failed re-extraction, the failure-then-success recovery, the deleted-document prune (4 rows) |
  | normalize from the run-start `project` rather than an in-lock re-read | the FX-set-mid-run row, the FX-corrected-mid-run row, the newer-revision row (3 rows) |
  | `update_facts`'s `load_facts` moved outside the lock | the feedback-in-flight row, the renormalize-race row, and Task 1's own `test_update_facts_re_reads_inside_the_lock` |
  | `generation` bumped inside `update_facts` | the generation row, and four of Task 1's own tests in `test_store_snapshots.py` |
  | `renormalize` writing the pre-pass's value instead of recomputing in-lock | the renormalize-race row, and Task 3's own two recompute tests |
  | `migrate` checking existence outside the lock | Task 5's `test_migration_does_not_overwrite_facts_a_run_stored_meanwhile` |

  Six of six caught, each by the row that claims it, and every probe reverted
  immediately. **A row that still passes with its defect reinstated is not
  testing what it claims** — one such was found and fixed during this check:
  the first form of the renormalize probe left the in-lock
  `if fresh == facts.normalized: continue` guard computing the *correct* value,
  so the wrong value was never reached and the row passed. The faithful probe
  (the pre-pass value used for both the comparison and the write) fails it.

  One row of the six behaves differently from the plan's prediction and is
  recorded rather than papered over: the plan expected the feedback-in-flight
  row to fail under the `technical_feedback` probe too. It does not, and it
  cannot — that row's competing writer is a feedback save that blocks on the
  vendor lock and therefore always writes the note *last*, so the run's stale
  copy is overwritten either way. The row is sensitive to its own probe (the
  load moved outside the lock), which is the defect it was written for, and H1
  covers the `technical_feedback` direction.

  One deviation from PLAN-TEMPLATE Rule 2, declared rather than faked: five of
  the nine pre-declared mutation rows are extraction-*cache* mutations between
  two runs. This work changes no prompt, extractor, provider or cache-keying
  code, so re-testing them here would duplicate
  `tests/test_pipeline_incremental.py` verbatim. They stay there; that file is
  confirmed green (23 passed). While confirming it,
  `test_pruning_does_not_disturb_feedback_or_overrides` was found to assert
  nothing about `overrides` despite its name — half its title was untested. It
  now stores an `Override` and asserts it comes back byte-identical, conflict
  flag and `extracted_value` included.
- **Verified:** yes. Python **980 passed / 3 skipped / 0 failed** on a
  workstation (`win32 / python 3.12.3`, `data/` and `projects/` present), which
  derives the CI row as 973 passed / 10 skipped; web **36 passed / 36** plus a
  clean `npm run build`. The report's H1 and H2 probes can no longer be run as
  written — they hook a `save_facts` call the fix removed from that path — and
  `tests/test_facts_concurrency.py` replaces them, with the vacuity guard the
  H2 note above insists on built into every rate-sensitive row.
- **Not fixed here:** cross-process concurrency. Every lock in this fix, like
  BUG-008's and BUG-009's, is a `threading.Lock` in one interpreter. A second
  API worker, or a second container on the same volume, reintroduces the whole
  class. Worth its own entry when multi-worker deployment is on the table.
