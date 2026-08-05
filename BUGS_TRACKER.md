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
| BUG-004 | S1 | api | With no `LLM_PROVIDER` set, ingestion silently falls back to the **mock** provider and stores fabricated facts as `ok` | 2026-08-05 | Open |
| BUG-003 | S4 | web | Empty states still tell the user to run ingestion "in the Streamlit portal", which no longer exists | 2026-08-05 | Open |
| BUG-002 | S3 | api / procurement/pipeline | No way to force a full re-extraction: `run_ingestion(force=True)` is unreachable from the API and the UI | 2026-08-05 | Open |
| BUG-001 | S3 | web | Compliance matrix and comparative statement are reachable before ingestion has completed | 2026-08-05 | Open |

## Closed

| ID | Severity | Area | Summary | Closed | Fixed in | Spec / Plan |
|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — |

---

## BUG-001 — Review screens are reachable before ingestion has completed

- **Severity:** S3
- **Area:** `web/src/App.tsx`
- **Status:** Open
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

- **Spec:** _not yet written_
- **Plan:** _not yet written_
- **Ledger:** none — not part of a phase
- **Design change:** _pending — see the open design question above_
- **Commit / PR:** _unfixed_
- **Test:** _none yet. Needs a `web/` test asserting the two nav items are
  disabled for a project whose `status` is `new` or `failed`._
- **Verified:** _unfixed_

---

## BUG-002 — No way to force a full re-extraction

- **Severity:** S3
- **Area:** `api/main.py`, `procurement/pipeline.py`
- **Status:** Open
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

- **Spec:** _not yet written_
- **Plan:** _not yet written_
- **Ledger:** none — not part of a phase
- **Design change:** _pending — needs a decision on how the action is surfaced
  and guarded_
- **Commit / PR:** _unfixed_
- **Test:** _none yet. Needs a test asserting a forced run re-extracts a
  document whose cache key is otherwise unchanged._
- **Verified:** _unfixed_

---

## BUG-003 — Empty states still point at the removed Streamlit portal

- **Severity:** S4
- **Area:** `web/src/pages/ComparativeStatement.tsx`, `web/src/pages/ComplianceMatrix.tsx`
- **Status:** Open
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

- **Spec:** none — copy change, no design decision
- **Plan:** none — one-line change per file
- **Ledger:** none — not part of a phase
- **Design change:** none — the fix matches the existing design
- **Commit / PR:** _unfixed_
- **Test:** _none planned. A string assertion on empty-state copy would be a
  change-detector test; the sweep in Notes is the better guard._
- **Verified:** _unfixed_

---

## BUG-004 — A deployment with no `LLM_PROVIDER` silently ingests with the mock provider

- **Severity:** S1
- **Area:** `api/main.py`
- **Status:** Open
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

- **Spec:** _not yet written_
- **Plan:** _not yet written_
- **Ledger:** none — not part of a phase
- **Design change:** _pending — needs a decision on whether `mock` stays
  reachable in a deployed build at all_
- **Commit / PR:** _unfixed_
- **Test:** _none yet. Needs a test asserting `POST /ingest` with no
  `LLM_PROVIDER` and no keys returns 400 and leaves the store untouched. Note
  the suite itself depends on `mock` being selectable, so the fix must keep an
  explicit path to it._
- **Verified:** _unfixed_
