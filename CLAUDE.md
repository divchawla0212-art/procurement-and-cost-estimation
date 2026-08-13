# CLAUDE.md

Two Python packages over one shared LLM layer:

- **`procurement/`** — the tender comparison pipeline. Ingests a ZIP of vendor
  folders, classifies each document, resolves revision lineage, routes each
  document to a per-class extractor, and stores the result per vendor.
- **`cost_estimation/`** — the older costing-sheet ingestion CLI (`cost-est`).
- **`shared/llm/`** — provider clients behind one `classify_structure` interface,
  plus the versioned prompt files in `shared/llm/prompts/`.
- **`api/main.py` + `web/`** — FastAPI and the React SPA it serves: the single
  front end, covering setup, ingestion and review.

## Running things

```bash
python -m pytest
```

Run from the repo root. Tests are key-free — they use `shared/llm/mock_client.py`,
and no test may require `ANTHROPIC_API_KEY`.

There are **two** baselines, and both are correct — they differ only in which
untracked fixture directories are present, never in pass/fail:

| where | baseline |
|---|---|
| a developer workstation, `data/` and an ingested multi-vendor `projects/` present, `pdftotext` on PATH | **1269 passed, 3 skipped, 0 failed** |
| CI, and any clean checkout | **1260 passed, 12 skipped, 0 failed** |

Anything else is a real regression.

CI being green is not luck. With no `projects/` all four
`test_real_corpus_coverage.py` tests skip on their module-level guard, and the
three tests guarded on the untracked `data/` sample directory skip too. The
three skips a workstation already shows are credential guards
(`test_anthropic_client.py`, `test_bedrock_client.py`,
`test_procurement_real_data.py`) and skip in both places.

There is now a **third** environmental gate, and it splits the two rows further
apart than the sentence above describes. Two tests in
`test_datasheet_row_recall.py` are guarded on the `pdftotext` CLI, which comes
from poppler-utils; `.github/workflows/tests.yml` does not install it, so they
pass on a workstation that has it and skip in CI.

So the CI row is the workstation row with the four corpus-coverage passes, the
three `data/` passes and the two `pdftotext` passes turned into skips —
`1260 = 1269 - 4 - 3 - 2`, `12 = 3 + 4 + 3 + 2`; 1272 tests either way. When
the counts move, measure the workstation row and derive the CI row from it;
editing the two rows independently is how they drift apart.

**The workstation row is measured, not derived**: **1269 passed, 3 skipped**,
taken on 2026-08-13 on the `rfq-platform-phase-1` branch, in an environment
with `pdftotext`, `data/` and an ingested multi-vendor `projects/` all present.
That matters, because a row this file once carried was not. While the auth
branch was in flight the workstation figure was *derived backwards* — measured
on a checkout that had neither fixture directory, then extrapolated upward —
and was flagged here as an assumption rather than a result. Both rows since
have been real measurements, and the caveat that used to sit here is deleted
rather than reworded. The CI row is still derived from the workstation row by
the subtraction above; derive it that way again when the counts move.

The jump from 1097 is the RFQ workflow: 83 tests across
`test_workflow_stages.py`, `test_workflow_store.py`,
`test_workflow_endpoints.py` and `test_workflow_persistence.py`. None of them
touches a fixture directory or a provider key, so every one of them lands in
both rows.

The further 40 on top of that are the project → item hierarchy, in the same
four files: the store's updates and delete guards, the five project/item
routes, and the eight-row mutation matrix in `test_workflow_persistence.py`.
Same story — no fixture directory, no provider key, both rows.

The web suite is separate and not part of either row above — both rows are
`python -m pytest` counts. Run it with `npm test` under `web/` (vitest,
non-watching, exits non-zero on failure); `npm run build` also type-checks the
test files, since `web/tsconfig.app.json` includes `src`. CI runs both, in the
`web` job of the same workflow. It stands at **106 passed** across 13 files.

`web/src/pages/workflow-fixtures.ts` is test data in a non-test module on
purpose. Importing fixtures from a `.test.tsx` file re-runs that file's
`describe` blocks inside the importing suite, and its hoisted
`vi.mock('../api')` factory wins over the importer's — so a fetcher the first
file did not mock arrives unmocked, as `mockResolvedValue is not a function`.
Nothing in `src/` imports it at runtime, so it never reaches the bundle.

Component tests that render `App` or `Setup` must mock `auth/context`'s
`useAuth`, and must return a **stable** object from it — build the value once
outside the `vi.mock` factory (`vi.hoisted`), never a fresh literal per call.
`App` passes `user` in the dependency list of the `useAsync` that loads the
project roster, so a new identity per render refetches, re-renders and
refetches until the vitest worker dies of heap exhaustion. That failure
arrives as `Worker exited unexpectedly` with a V8 fatal-error stack, not as a
failed assertion, so it is worth recognising on sight.

**There is no longer a workstation-only failure.** Until the Streamlit portal
was removed, `tests/test_portal_app.py::test_missing_api_key_does_not_block_creation`
failed for anyone with a populated `.env`, because `portal/app.py` called
`load_dotenv()` and repopulated the key that the test had just deleted. That
test went with the portal, so a populated `.env` no longer changes the count. A
red workstation run is now always a real regression — do not go looking for the
old excuse.

The workstation row also depends on what your untracked `projects/` holds:
`test_real_corpus_coverage.py` asserts against the newest store there with more
than one vendor, so it is a coverage instrument for the live corpus, not a
fixture-backed unit test. **It holds at shipped defaults** — measured on
`projects/phase4c-shipped-defaults`, ingested with no `LLM_MAX_TOKENS` and no
chunk-budget override. Both extractors that read a whole document split their
input on line boundaries (`procurement/chunking.py`, budgeted by
`TECH_CHUNK_CHARS` and `REQUIREMENTS_CHUNK_CHARS`) and merge all-or-nothing, so
a quotation-only vendor's 220k-character proposal no longer overruns the 8192
output ceiling and lands its vendor back at zero facts. If a floor goes red,
chunk further or fix the routing — never lower the floor, and never green it
with an environment override, which makes the instrument assert something
weaker than the sentence it reads as.

CI runs that same command on every pull request into `main`, via
[`.github/workflows/tests.yml`](.github/workflows/tests.yml) — Ubuntu, Python
3.12, no provider secrets. Keep it that way: a test that needs a key belongs
behind a skip guard, not behind a repository secret.

The app runs via [`run.ps1`](run.ps1) at the repo root — one command for both
servers, with preflight checks and a clean Ctrl+C. Use it rather than bare
`uvicorn` / `npm run dev`.

**It makes its ports available rather than just checking them.** A port held by
this repo's own leftovers is reclaimed automatically; a port held by anything
else is reported with its command line and refused, and `-Force` is what takes
those. "Ours" is deliberately asymmetric: the web server has to prove it is this
checkout (`vite` plus the repo path on its command line), while the API is
matched on `api.main:app` alone — a venv's `python.exe` reports its *base*
interpreter, so the observed line is `…anaconda3\python.exe -m uvicorn
api.main:app …` with the repo path nowhere in it. Requiring the path there would
refuse to reclaim the script's own leftover API, which is the one case the
feature exists for. Reclaiming kills the outermost process of the holder's tree,
because `uvicorn --reload` and `npm run dev` are supervisors that respawn a
worker onto the same port otherwise, and it never crosses into this script's own
ancestry — the terminal running `run.ps1` also carries the repo path.

`.claude/launch.json` still defines `procurement-api` and `enterprise-web` for
the preview tooling, and both hardcode their ports. They can no longer both be
up *by accident*: a preview-started vite matches the "ours" test, so `run.ps1`
now reclaims its port instead of refusing to start. Stop the preview server
first if you wanted it.

## Store invariants — violating these corrupts award decisions

There are **two** authoritative stores, with separate rules. For project data:
the snapshots under `projects/<slug>/store/`; `index/store.db` is derived and
disposable. For accounts: `<ROOT>/auth.json` — see the section after this one.

- Every write goes through `procurement/store/snapshots.py`. Never hand-roll a
  snapshot write.
- `generation` bumps **once per write transaction**, never once per file.
- `field_path` addresses list members by **id**, never by index — list order is
  not stable across re-extractions, and `overrides.py` rejects index selectors
  outright.
- **A stored collection contains exactly the records of its currently-live
  sources — no more.** Facts of superseded, reclassified or deleted documents
  must be pruned, not merely skipped on re-extraction. This is the invariant
  phase 2 shipped broken; see `_prune_orphan_facts` in `procurement/pipeline.py`.
- Missing data is never coerced to a passing or zero value. An unfound parameter
  is omitted, not emitted as `0` or `""`.
- **Arithmetic stays in Python.** Extractors capture numbers and units verbatim;
  the model reads, code decides. Never ask an extractor whether a vendor complies.
- A failed extraction never blanks previously-good stored data, and always
  records why in `DocumentRecord.notes`.

## Auth invariants — `<ROOT>/auth.json`

Users, sessions and grants live in one document so a single lock and a single
atomic write keep all three consistent. Written **only** from
`api/auth/store.py`; routes never touch the file.

- Every write is a read-modify-write inside `locked_update`. **And so is every
  decision that gates one.** A check made in the caller and a write made in
  the store are two critical sections, not one: two admins deleting each other
  concurrently each read "two admins, fine" and both writes land on zero
  admins. Both guards this subsystem shipped wrong were *reads* outside the
  lock, not writes — `store.delete_user` and `store.grant` show the shape.
- `grants` holds exactly the grants whose user still exists. `delete_user`
  cascades to grants and sessions in the same write. (A grant whose *project*
  is gone is deliberately tolerated and inert — `list_projects` filters
  against disk.)
- Sessions persist `sha256(token)` only. `password_hash` is never a field on
  `User`, so it cannot reach a response body; `store.password_hash_for` is the
  one reader of the digest.
- **`AUTH_DISABLED=1` is a development bypass, off unless set.** It serves an
  unauthenticated caller as the first administrator in `auth.json` (or
  `DEV_USER_EMAIL`), so the front end opens without signing in. It is a
  separate switch, *not* an edit to `PUBLIC_PATHS` — that set stays pinned at
  three by `test_the_allowlist_is_exactly_these_three_paths`, and turning the
  bypass off restores fail-closed exactly. It never mints a user: with no
  administrator in the store there is nobody to act as and it stays closed. A
  real session still wins, so actions keep their real attribution. Reach it
  with `.
un.ps1 -NoAuth`; never run it anywhere another person can reach the
  port.
- **Startup signs everyone out.** `api.main`'s lifespan calls
  `store.clear_sessions`, so launching the platform always lands on the
  sign-in page rather than dropping whoever signed in last back inside as
  whatever account that was. The session cookie is a browser-session cookie
  (no `max_age`) for the same reason. Both halves matter: the server-side row
  is what `resolve_session` actually checks, so clearing it is what makes a
  still-held cookie inert. The deliberate cost is that restarting the API
  signs out every user, not just the person restarting it.
- Roles are `admin` and `reviewer`, and **no route changes a role** — it is
  set once at creation. Signup always creates a reviewer.
- A project slug is matched against `list_projects` (which reads `os.listdir`),
  never by asking the filesystem whether a path exists. Windows and macOS
  resolve paths case-insensitively; CI does not, so that class of bug cannot
  fail on CI.

## RFQ workflow invariants — `<ROOT>/workflow.json`

The eight-stage RFQ process lives in the top-level `workflow/` package, with
its routes in `api/workflow_routes.py`. It is deliberately **not** part of
`procurement/`: that package's store has the snapshot invariants above
(`generation`, `field_path` by id, orphan pruning) and this one does not share
them, so keeping them apart stops a reader assuming one set covers both.

- **Stage codes are fixed**, and `TRANSITIONS` is **deny-by-default** — an edge
  absent from that table is refused. Every stage needs an entry, including the
  terminal `PO_ISSUED`, so `is_allowed` answers "no" instead of raising.
- **Only forward transitions are gated.** The backward edges are the documented
  recoveries — retender and renegotiate — and a forward gate must never block
  one, or a stuck RFQ has no way out.
- **A gate never returns a bare `False`.** `GateResult` carries a reason, and a
  blocked transition raises with it; the route surfaces that sentence as a 409.
  A reason must name the whole exit criterion, not the nearer half of it.
- **History is append-only.** A backward transition appends; it never rewrites
  or removes an earlier entry. The second pass through a stage is a second
  entry, which is why `to_stage` alone is not a unique key within one history.
- **`workflow.json` holds exactly the entities the store holds.** `save`
  replaces the document wholesale, so an entity removed in memory cannot
  survive on disk. Any field added to `WorkflowStore.__init__` needs a matching
  line in **both** `to_document` and `from_document`, or it silently fails to
  survive a restart.
- **Every write, and every decision that gates one, happens inside
  `persistence.locked_update`.** This is the same rule as the auth store and
  for the same reason: a check in the route and a write in the store are two
  critical sections, so two concurrent transitions could each read "gate open".
  `WorkflowStore.transition` therefore runs inside the block, never against a
  store loaded before it. The write lands only on a clean exit, so a refused
  transition leaves the document untouched.
- **Workflow routes are not on `middleware.PUBLIC_PATHS`** and must not be.
  `test_auth_middleware.py`'s route sweep covers them; a new path parameter
  needs adding to that test's probe substitutions, which is what its assertion
  is there to force.
- **Deleting a project takes its items with it, in the same call.** `save`
  replaces the document wholesale, so an item pruned in memory but not in the
  cascade is an item pointing at a project that is gone — and it survives the
  restart. The cascade materialises its id list *before* the first deletion and
  filters on `project_id`; dropping either half is a shipped orphan.
- **Nothing is deleted out from under a live reference.** An item covered by any
  RFQ, and a project holding any RFQ, both refuse with a reason naming the RFQ.
  Both guards live in the store method — they are reads that gate a write, and
  the route runs the whole method inside `locked_update`. This is the same rule
  as the auth store, and the third place this repository has needed it.
- **An update is partial, and never touches identity or parentage.** `changes`
  carries only what the caller sent (`model_dump(exclude_unset=True)`), so an
  absent field is left alone rather than cleared. `id` and `project_id` are
  refused: moving an item between projects would change which project's RFQs
  may cover it without either RFQ record changing. Updates rebuild through the
  model rather than `model_copy(update=...)`, which skips validation outright.
- **The live-period check is advisory.** An item whose `required_on_site` falls
  outside the project window is stored, with the reason returned alongside it as
  `live_period_warning`. A hard refusal would make the field unusable in exactly
  the cases where a late delivery is the fact being recorded.
- **A frozen technical package is immutable, and shortlist approval does not
  outlive an edit.** Both are guards on going *back* a step: a frozen package
  refuses a later write (vendors bid against that revision), and adding or
  removing a vendor revokes approval, so an RFQ cannot issue with a vendor
  procurement never signed off. Removal addresses `ShortlistEntry` and
  `VdrlLine` by their `id`, never by position.
- **`WorkflowStore` knows nothing about disk.** Serialization lives in
  `workflow/persistence.py`, the one module allowed to touch the store's dicts
  directly, so replacing the JSON file with a database is a change to that
  module alone.

## Planning convention

Design specs live in `docs/superpowers/specs/`, implementation plans in
`docs/superpowers/plans/`, and the per-phase execution ledger in
`.superpowers/sdd/<phase>/progress.md`.

**Before writing a plan for phase 3 or 4, read
[`docs/superpowers/PLAN-TEMPLATE.md`](docs/superpowers/PLAN-TEMPLATE.md).** It is
not boilerplate: phase 2 shipped seven defects that survived per-task TDD and
seven honest per-task reviews, because every one of them needed two runs or two
modules to see. The template's three rules — a named store invariant per task, a
two-run mutation matrix on the integration task, and reference code treated as
intent rather than paste-able — are the structural fix. Phase 3 is more exposed
than phase 2, not less.
