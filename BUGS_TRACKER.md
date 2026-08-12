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
| BUG-013 | S3 | auth / api | `POST /api/auth/login` has no rate limit or lockout, so password guessing is unbounded | 2026-08-06 | Open |
| BUG-004 | S1 | api | With no `LLM_PROVIDER` set, ingestion silently falls back to the **mock** provider and stores fabricated facts as `ok` | 2026-08-05 | Open |
| BUG-003 | S4 | web | Empty states still tell the user to run ingestion "in the Streamlit portal", which no longer exists | 2026-08-05 | Open |
| BUG-002 | S3 | api / procurement/pipeline | No way to force a full re-extraction: `run_ingestion(force=True)` is unreachable from the API and the UI | 2026-08-05 | Open |
| BUG-001 | S3 | web | Compliance matrix and comparative statement are reachable before ingestion has completed | 2026-08-05 | Open |

## Closed

| ID | Severity | Area | Summary | Closed | Fixed in | Spec / Plan |
|---|---|---|---|---|---|---|
| BUG-012 | S2 | auth / api | No way for an admin to grant a user access to some projects — access is all-or-nothing because no per-user grant exists to hold | 2026-08-06 | `850ae2c..03778fa` (Tasks 10–12) | [design](docs/superpowers/specs/2026-08-06-auth-user-hierarchy-design.md) / [plan](docs/superpowers/plans/2026-08-06-auth-user-hierarchy.md) |
| BUG-011 | S1 | auth / api / web | Anyone who reaches the platform can read and write **every** project; there is no admin role and no ownership check | 2026-08-06 | `c18dd5d..3b9206b` (Tasks 1–9) | [design](docs/superpowers/specs/2026-08-06-auth-user-hierarchy-design.md) / [plan](docs/superpowers/plans/2026-08-06-auth-user-hierarchy.md) |

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

---

## BUG-011 — Everyone who reaches the platform can read and write every project

- **Severity:** S1
- **Area:** `api/main.py`, `web/src/App.tsx`, `web/src/auth/`
- **Status:** Closed
- **Reported:** 2026-08-06
- **Reporter:** Rahul Jana (client-reported)

### What happens

There is no notion of *who* a request is from, anywhere in the system, and no
notion of which projects a person is entitled to.

**On the API — the authoritative surface.** None of the 16 routes in
`api/main.py` takes a caller identity. There is no `Depends(...)` security
dependency, no `Authorization` header is read, and no session is looked up.
`list_projects` (`api/main.py:113`) is the whole of the access decision:

```python
@app.get("/api/projects")
def list_projects() -> list[dict]:
    return [_project_summary(p) for p in proj.list_projects(ROOT)]
```

It enumerates every project under `ROOT` for every caller. The same holds for
the mutating routes — `POST /api/projects/{slug}/ingest`,
`PUT /api/projects/{slug}/fx-rates`,
`PUT /api/projects/{slug}/vendors/{vendor}/feedback`,
`POST /api/projects/{slug}/vendors` — so an anonymous caller can not only read
every tender's vendor pricing, they can overwrite the authoritative store.

**On the web client — cosmetic only.** The mock auth added on branch
`authentication` (PR #7) gates rendering with one line,
`if (!user) return <Auth />` (`web/src/App.tsx:79`). Once past it, the project
switcher is populated straight from `GET /api/projects`, so every signed-in user
sees every project. The gate is client-side, so it is not a control at all:
`curl` reaches the same data without ever loading the SPA.

**No role exists to check.** `User` carries a single field
(`web/src/auth/context.ts:3-5`):

```ts
export interface User {
  email: string
}
```

There is no `role`, no `admin` flag, and no server-side user record —
`AuthProvider` stores accounts as plaintext `{email, password}` in
`localStorage` under `te_users` (`web/src/auth/AuthProvider.tsx:16-36`). The
file says so itself at line 9: "SECURITY NOTE: this is a UI/demo convenience,
NOT real auth."

### What should happen

Only an admin sees every project. A non-admin sees exactly the projects they
have been granted, and the decision is made **on the server**, per request,
against an authenticated identity — never in the SPA.

That requires three things this codebase does not yet have:

1. A real authentication backend — server-side accounts, hashed passwords,
   tokens — replacing the `localStorage` mock outright.
2. A role on the user record, with `admin` as a distinct, assignable value.
3. An ownership/grant check inside the API, so `list_projects` returns a
   filtered set and every `{slug}` route rejects a slug the caller has no claim
   to with `403` rather than serving it.

Point 3 is the load-bearing one: without it, points 1 and 2 still leave every
project readable by anyone who can call the API directly.

The per-user grant model that a non-admin's access is read from is tracked
separately as BUG-012; this entry covers the missing admin boundary and the
absent server-side check that BUG-012 depends on.

### Reproduce

The API-level reproduction needs no browser and no account, and works on `main`
as well as on the `authentication` branch:

1. Start the stack: `PROCUREMENT_HOST_PORT=8300 docker compose up -d`
2. `curl -s http://localhost:8300/api/projects`

**Reproduces:** always

```
$ curl -s http://localhost:8000/api/projects | python -c "import json,sys; print([p['slug'] for p in json.load(sys.stdin)])"
['coverage-floor-t7', 'gas-07', 'gas-14', 'gas-15', 'gas-7', 'gas-v1',
 'gas-v2', 'phase4-acceptance', 'phase4b-acceptance', 'phase4c-shipped-defaults']

Ten projects, no credentials presented, no header sent.
```

Via the UI, on the `authentication` branch:

1. Open the app and press **Sign up**.
2. Register any email and password — nothing validates the address, and no
   approval step exists.
3. Read the project switcher in the rail.

```
Observed: the switcher lists all ten projects for a brand-new self-registered
account. Every review screen, every export, and the ingest action are reachable
for all of them.
```

### Environment

- Branch / commit: `main` @ `596cb86`; also `authentication` @ `b919830` (PR #7),
  which adds the mock login without changing any of the above
- Python / Node: 3.12.3 / v24.15.0
- Provider: any — the defect is in the access path, not the extraction path
- Data: any `projects/` directory holding more than one project

### Notes

- **2026-08-06** — Severity is S1 rather than S2 because the exposure is
  bidirectional. Read access leaks one tender's vendor pricing to anyone
  evaluating another, which is a confidentiality breach in its own right; write
  access means an unauthenticated caller can `POST /ingest` or `PUT /fx-rates`
  against a project they have nothing to do with, which puts wrong numbers in
  front of a reviewer making an award decision. The store invariants in
  `CLAUDE.md` all assume the writer is entitled to write.
- **2026-08-06** — Do not fix this in the SPA. `web/src/App.tsx:79` is where the
  symptom is visible, but a client-side gate cannot be a control: the data is
  served by the API, and the API is what has to say no. A fix that only filters
  the switcher would close the report while leaving `curl` fully effective.
- **2026-08-06** — This blocks any multi-tenant or multi-client deployment.
  Single-operator local use is the only configuration in which the current
  behaviour is acceptable, and nothing in the product says so.
- **2026-08-06** — Related: BUG-004 is the other route by which an
  unauthenticated `POST /ingest` corrupts a store. They should probably be
  fixed in the same pass over the ingest endpoint.

### Fix

- **Spec:** [`docs/superpowers/specs/2026-08-06-auth-user-hierarchy-design.md`](docs/superpowers/specs/2026-08-06-auth-user-hierarchy-design.md)
- **Plan:** [`docs/superpowers/plans/2026-08-06-auth-user-hierarchy.md`](docs/superpowers/plans/2026-08-06-auth-user-hierarchy.md), Phase 1 (Tasks 1-9)
- **Ledger:** [`.superpowers/sdd/2026-08-06-auth-user-hierarchy/progress.md`](.superpowers/sdd/2026-08-06-auth-user-hierarchy/progress.md)
- **Design change:** none beyond what the spec already called for — real
  server-side accounts and hashed passwords behind `api/auth/store.py`
  (`auth.json`, S1-S3), a `role` on `User` (`admin` / `reviewer`), fail-closed
  session middleware on every `/api/` path outside a three-entry allowlist
  (`api/auth/middleware.py`, A1), and a `require_project_access` dependency
  that derives the visible project set per request from role and grants
  (`api/auth/deps.py`, A2/A3) — replacing the client-side `localStorage` mock
  auth described in "What happens" above outright, per D1/D5/D7 of the design.
- **Commit / PR:** `c18dd5d..3b9206b` (`test(auth): add the two-run
  mutation matrix and close BUG-011`) — Tasks 1-9 of the plan, branch
  `claude/build-verification-pr-26cc64`
- **Test:** `tests/test_auth_middleware.py::
  test_every_api_route_outside_the_allowlist_requires_a_session` fails without
  the fix: it sweeps every route in `app.routes` outside `PUBLIC_PATHS` and
  asserts 401 with no session, so removing or narrowing the fail-closed
  middleware makes it fail, naming the specific route left open. The per-role
  filtering half (A2/A3 — an admin sees every project, a reviewer sees exactly
  their grants, `{slug}` refuses with 403 not 404) is covered by
  `tests/test_api_authorization.py`. Task 9's
  `tests/test_auth_integration.py` additionally defends both across state
  changes a single request can't see: a revoked grant, a project deleted from
  disk, a promotion to admin, and a process restart, all without a re-login.
- **Verified:** the reproduction in "What happens" no longer reproduces —
  `GET /api/projects` 401s with no session and returns only the caller's
  admin-or-granted set otherwise; the ten-row mutation matrix (Task 9) confirms
  that boundary holds across two requests, not just one, for every hazard the
  matrix names.

---

## BUG-012 — An admin cannot grant a user access to a subset of projects

- **Severity:** S2
- **Area:** `api/auth/store.py`, `api/admin_routes.py`, `web/src/pages/Admin.tsx`
- **Status:** Closed
- **Reported:** 2026-08-06
- **Reporter:** Rahul Jana (client-reported)

### What happens

Access is all-or-nothing, and there is no mechanism by which it could be
anything else. Nothing in the system records that user *X* may see project *Y*:

- No grant/membership store exists. `projects/<slug>/store/` holds documents,
  facts, requirements and FX rates — no principal is named in any snapshot, and
  `procurement/store/snapshots.py` has no collection for one.
- The user record has no place to hold grants — `User` is `{ email }`
  (`web/src/auth/context.ts:3-5`), and the persisted form is
  `{ email, password }` (`web/src/auth/AuthProvider.tsx:16-19`).
- There is no admin surface to make a grant from. The nav in `web/src/App.tsx`
  has six screens — dashboard, setup, overview, compliance, statement,
  extraction status — and no user-management screen.
- There is no API to carry a grant: of the 16 routes in `api/main.py`, none is
  a user, role, membership or invitation route.

So even after an admin boundary exists, the only two states available are "sees
everything" and "sees nothing". A reviewer who should see one tender must be
given every tender, or kept out of the platform entirely.

### What should happen

An admin can grant and revoke a named user's access to a named project, and a
non-admin's view — the project list, every `{slug}` route, and every export —
is computed from those grants server-side.

Undecided, and to settle in the spec rather than here:

| question | why it matters |
|---|---|
| Are grants per-project, or per-group-of-projects? | Per-project is simpler; a client with forty tenders will ask for groups. |
| Is there a read-only grant distinct from a read-write one? | Ingest and FX edits change the award numbers; review does not. Collapsing them means anyone who can look can also overwrite. |
| Where do grants live? | A store snapshot inherits the invariants in `CLAUDE.md`. An `index/` table does not, and `index/store.db` is explicitly "derived and disposable" — so grants cannot live there. |
| What happens to a project whose only grantee is deleted? | An orphaned project no non-admin can reach is a support call. |

### Reproduce

Not reproducible as a sequence of steps — the capability is absent, so there is
nothing to drive. The evidence is the absence itself. Run from a checkout of
`authentication` (PR #7), where `web/src/auth/` exists; on `main` drop that path
and the result is the same:

**Reproduces:** always (capability absent)

```
$ grep -rniE "\b(role|admin|permission|grant|member|owner|acl)\b" api/main.py web/src/auth/
$ echo $?
1           # no matches anywhere in the API or the auth module

$ grep -c "^@app\." api/main.py
16          # 16 routes — 15 project routes plus /api/health, and not one
            # user, role or membership route among them
```

### Environment

- Branch / commit: `main` @ `596cb86`; also `authentication` @ `b919830` (PR #7)
- Python / Node: 3.12.3 / v24.15.0
- Provider: n/a — no provider involvement
- Data: n/a — independent of project contents

### Notes

- **2026-08-06** — Filed as a bug at the reporter's request. It is more
  precisely a missing capability than a defect in shipped behaviour: nothing
  here worked before and regressed. Recording it as a bug is fine, but whoever
  schedules it should treat it as a feature-sized piece of work — it needs a
  data model, an API, an admin UI and a migration for existing projects, none
  of which exist.
- **2026-08-06** — **Ordering: BUG-011 first.** This entry is the grant model;
  BUG-011 is the server-side check that would consult it. Building grants on
  top of an API that never authorizes anything produces a permissions screen
  that changes nothing, which is worse than no screen at all — it reads as a
  control while `curl` still returns every project. Whoever picks these up
  should expect one spec covering both.
- **2026-08-06** — Severity S2 rather than S1: no wrong number reaches a
  reviewer through this gap alone, and no data is lost. The exposure is
  BUG-011's. What is broken here is that the only workaround for a user who
  needs one tender is to grant them all of them — which is not a workaround,
  it is the S1.

### Fix

- **Spec:** [`docs/superpowers/specs/2026-08-06-auth-user-hierarchy-design.md`](docs/superpowers/specs/2026-08-06-auth-user-hierarchy-design.md)
  — the same spec as BUG-011, as the ordering note above expected
- **Plan:** [`docs/superpowers/plans/2026-08-06-auth-user-hierarchy.md`](docs/superpowers/plans/2026-08-06-auth-user-hierarchy.md), Phase 2 (Tasks 10-12)
- **Ledger:** [`.superpowers/sdd/2026-08-06-auth-user-hierarchy/progress.md`](.superpowers/sdd/2026-08-06-auth-user-hierarchy/progress.md)
- **Design change:** grants become a real stored relation and gain the API and
  screen to manage them. `auth.json.grants` holds `{user_id, slug, granted_at,
  granted_by}` written only through `store.grant` / `store.revoke`
  (invariant S4: exactly the grants whose user still exists), five
  `/api/admin/*` routes behind `require_admin`, and `web/src/pages/Admin.tsx`
  — a per-reviewer, per-project toggle. `granted_slugs`, which BUG-011 shipped
  reading a table nothing could write, now has a writer.
  **Three decisions the spec had left open, settled here:** a grant naming a
  slug with no project is **refused**, not stored (a silently accepted typo
  produces a reviewer who sees nothing and an admin who cannot tell why); a
  grant whose project is later deleted is **tolerated and inert** (there is no
  project-deletion route, and `list_projects` filters against disk); and slug
  reuse on project recreation can therefore **reactivate** a stale grant.
- **Commit / PR:** `850ae2c..03778fa` (`feat(auth): add per-user project grants
  and the admin API`, `fix(auth): decide both grant/delete guards inside the
  write's lock`, `feat(web): add the admin user and grant management screen`,
  `test(auth): extend the mutation matrix for grants and close BUG-012`) —
  Tasks 10-12 of the plan, branch `claude/build-verification-pr-26cc64`
- **Test:** `tests/test_api_grants.py` (19 tests) carries the fix's own
  coverage — grant then revoke moves a reviewer's visibility, granting twice
  is one row, a reviewer is 403'd from every admin route, and the user list
  never carries `password_hash` (asserted on raw response text, so a
  serialization change cannot leak it quietly). Two rows there fail without
  the fix's concurrency half:
  `test_two_admins_deleting_each_other_at_once_cannot_reach_zero_admins` and
  `test_a_grant_racing_that_users_deletion_leaves_no_orphan_row` — both pass
  if the guard merely *exists* and fail the moment it is decided outside the
  write's lock. Rows 11 and 12 of `tests/test_auth_integration.py` defend S4
  across two runs: deleting a user takes their grants and sessions with them,
  and a refused grant does not poison the pair — the same slug is grantable
  once the project exists.
- **Verified:** each new row was mutation-checked by reinstating the specific
  defect (delete the grant cascade → row 11 fails alone; accept unknown slugs
  → row 12 fails alone; move the grant write outside the lock → row 4 fails
  alone), then reverted with the source tree confirmed clean. End-to-end in
  the browser: an admin granted a reviewer one project, that reviewer saw
  exactly that project with no admin nav entry and a 403 from
  `GET /api/admin/users`, and after the revoke their list was empty.

---

## BUG-013 — Login accepts unlimited password guesses

- **Severity:** S3
- **Area:** `api/auth/routes.py` (planned), `POST /api/auth/login`
- **Status:** Open
- **Reported:** 2026-08-06
- **Reporter:** raised and deliberately deferred while designing the auth system

### What happens

The authentication design in
[`docs/superpowers/specs/2026-08-06-auth-user-hierarchy-design.md`](docs/superpowers/specs/2026-08-06-auth-user-hierarchy-design.md)
specifies no rate limit, no per-account lockout and no failed-attempt counter on
`POST /api/auth/login`. An attacker who can reach the port can therefore try
passwords as fast as the server will hash them, against any email they can
guess.

`hashlib.scrypt` at `n=16384, r=8, p=1` is the only thing slowing this down. That
is a real cost per attempt and it is why this is S3 rather than S2 — but it is a
speed bump, not a limit, and it applies equally to the defender: the same cost
makes the endpoint a cheap denial-of-service target, since each request buys the
attacker one guess and buys the server one scrypt.

This entry is filed **before the code exists**, so that the gap is recorded at
the moment it was chosen rather than discovered later as an oversight.

### What should happen

Failed attempts are counted and throttled. The shape is undecided; the spec
deliberately did not settle it:

| option | cost |
|---|---|
| Per-account lockout after N failures | Needs an unlock path, or an admin who can be locked out of their own deployment. |
| Per-IP throttle | Useless behind a single NAT — the whole client office is one address. |
| Exponential backoff per account, no hard lock | No unlock path needed, self-healing, but a determined attacker still gets slow progress. |

Whichever is chosen must not let an attacker lock the sole admin out of the
platform by guessing at their address.

### Reproduce

Not reproducible yet — the login route does not exist. The evidence is the
design decision:

**Reproduces:** n/a (defect specified, not yet built)

```
Section 10, "Out of scope", of the auth design:

  Login rate-limiting and account lockout. Real protection, deliberately
  deferred: it needs failed-attempt state and an unlock path, and this
  deployment is a single container on a client's network rather than an
  internet-facing service.
```

### Environment

- Branch / commit: `claude/build-verification-pr-26cc64` @ `d00bb8f` (design
  only; no implementation yet)
- Python / Node: 3.12.3 / v24.15.0
- Provider: n/a
- Data: n/a

### Notes

- **2026-08-06** — Severity S3, not S2: `scrypt` at the specified cost
  parameters makes bulk guessing expensive, and the deployment is a single
  container on a client's network rather than an internet-facing service. **Re-triage
  to S2 if** the platform is ever exposed to the internet, or if the deployment
  model changes to one host serving several client organisations — at that point
  unbounded guessing against a known admin address is the whole attack.
- **2026-08-06** — Fix this together with any work that exposes the service more
  widely, not on its own schedule. It is cheap to add and expensive to need.

### Fix

- **Spec:** none yet — the auth design
  (`docs/superpowers/specs/2026-08-06-auth-user-hierarchy-design.md`, section 10)
  records the deferral but does not design the throttle
- **Plan:** _not yet written_
- **Ledger:** none — not part of a phase
- **Design change:** _pending — needs the choice tabled above_
- **Commit / PR:** _unfixed_
- **Test:** _none yet. Needs a test asserting the Nth consecutive failed login
  for one account is rejected without a password check, and that a correct
  password still succeeds once the window has passed._
- **Verified:** _unfixed_
