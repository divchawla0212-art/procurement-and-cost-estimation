# Technical Specification & Requisites

What this system is made of, what it needs to run, and what contracts it holds
itself to. Derived from the code on branch `rfq-platform-phase-1` as of
2026-08-17.

Companion documents: [`README.md`](../README.md) for the quick start,
[`docs/SETUP.md`](SETUP.md) for a fresh-machine install, and
[`CLAUDE.md`](../CLAUDE.md) for the store invariants and the test baselines this
document cites rather than restates.

---

## 1. Scope

One deployable web application over two Python domain packages and a shared LLM
layer:

| Concern | Package | Summary |
|---|---|---|
| Tender comparison | `procurement/` | Ingests a ZIP of vendor folders, classifies each document, resolves revision lineage, routes each document to a per-class extractor, stores facts per vendor, and builds a compliance matrix and comparative statement. |
| RFQ process | `workflow/` | The eight-stage RFQ lifecycle: projects → items → RFQs, bidder registry, shortlisting, clarifications, addenda, enquiry documents. |
| Costing sheets | `cost_estimation/` | The older costing-sheet ingestion CLI (`cost-est`). Not reachable from the web app. |
| Model access | `shared/llm/` | Anthropic, OpenAI, Gemini, Bedrock and a mock behind one `classify_structure` call, plus versioned prompt files. |
| Delivery | `api/` + `web/` | FastAPI, and the React SPA it serves. The single front end — setup, ingestion and review. |

There is no second front end. The Streamlit portal was removed; `run.ps1` and
the Docker image are the two supported ways to bring the system up.

---

## 2. Deployment topology

Two supported shapes. Both run **one** application process.

**Development** — two processes, two origins, CORS in play:

```
browser :5173  ──►  Vite dev server  ──/api──►  uvicorn :8000  ──►  <ROOT>/ on disk
```

`api/main.py` allows exactly `http://localhost:5173` and
`http://127.0.0.1:5173` as CORS origins, with credentials. Launch both with
[`run.ps1`](../run.ps1), never bare `uvicorn` / `npm run dev`.

**Container** — one process, one origin, CORS never applies:

```
browser :8000  ──►  uvicorn (api.main:app)  ──┬── /api/*        FastAPI routes
                                              └── /*            web/dist (SPA fallback)
                                                     └────────►  /data/projects volume
```

The React bundle is compiled in a `node:22-alpine` stage and copied into the
Python image, so FastAPI serves both. No nginx, no reverse proxy, no second
container. The SPA mount is conditional on `web/dist` existing — a source
checkout without `npm run build` serves the API alone.

---

## 3. Platform prerequisites

| Requisite | Version | Required for | Notes |
|---|---|---|---|
| Python | **≥ 3.12** | everything server-side | `requires-python` floor in `pyproject.toml`; CI pins 3.12, the image is `python:3.12-slim`. No version matrix — nothing consumes this repo as a library. |
| Node.js | **≥ 20.19** (22 or 24 fine) | building/serving the SPA | Vite 8 floor. CI and the Docker web stage both use 22. |
| npm | ships with Node | `npm ci` from `web/package-lock.json` | |
| Git | any 2.x | checkout | Windows deep checkouts need `core.longpaths=true` — processed-data PDF names exceed MAX_PATH. |
| PowerShell 7+ | — | `run.ps1` | The launcher is PowerShell-only. Linux/macOS run the two servers by hand per the README. |
| Docker + Compose | any current | container deployment | Only if deploying the image. |
| `pdftotext` (poppler-utils) | — | **optional** | A PDF reader arm. Two tests in `test_datasheet_row_recall.py` skip without it; CI deliberately does not install it. |

**Operating systems.** Development is Windows 11 first (the launcher, the setup
guide); CI is Ubuntu; the image is Debian slim. Code that touches paths must
work on all three — see §8 on the drive-letter check, which shipped wrong
precisely because `posixpath.splitdrive` is a no-op.

**Network.** Outbound HTTPS to the selected model provider during ingestion,
extraction and vendor suggestion. Nothing else phones home. With
`LLM_PROVIDER=mock` the system needs no network at all.

**Resources.** No fixed floor is enforced. Sizing is driven by the corpus: a
single vendor proposal of ~220k characters is chunked and sent to the model in
pieces (§6), and the store holds the full uploaded documents plus content-
addressed RFQ blobs.

---

## 4. Software dependencies

### 4.1 Python runtime (`pyproject.toml` → `[project].dependencies`)

| Package | Floor | Used for |
|---|---|---|
| `pydantic` | 2.6 | every domain model and extraction schema |
| `fastapi` | 0.115 | the HTTP layer |
| `uvicorn[standard]` | 0.32 | the ASGI server |
| `python-multipart` | 0.0.9 | file uploads |
| `openpyxl` | 3.1 | `.xlsx` reading (costing sheets, AVL import) and export |
| `pypdf` | 4.0 | PDF text extraction |
| `python-docx` | 1.1 | `.docx` reading |
| `pyyaml` | 6.0 | discipline configuration |
| `python-dotenv` | 1.0 | `.env` loading at import of `api.main` |
| `anthropic` | 0.39 | Anthropic client and the scanned-PDF fallback |
| `openai` | 1.0 | OpenAI client |

Optional extras: `dev` = `pytest>=8`, `httpx>=0.27`; `llamaparse` =
`llama-cloud>=1.0`, used **only** by `tools/parser_ab.py` and never by the
pipeline.

Bedrock and Gemini clients are imported lazily by their modules; neither SDK is
a declared core dependency, so selecting those providers requires installing
their SDK in the environment.

### 4.2 Web runtime (`web/package.json`)

Runtime: `react` 19, `react-dom` 19, `react-router` 8. Build/test: `vite` 8,
`typescript` ~6.0, `vitest` 4, `jsdom` 30, `@testing-library/react` 16,
`oxlint` 1.

The SPA has **no** charting, component or CSS framework dependency —
`web/src/theme.css` and `web/src/components/` are hand-written.

---

## 5. External services and credentials

`shared/llm/factory.py` resolves a provider name to a client. There is no
argument-free path to the mock: an unset `LLM_PROVIDER` and an empty string
both raise rather than defaulting.

| Provider id | Credential | Authentication |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | API key |
| `openai` | `OPENAI_API_KEY` | API key |
| `gemini` | `GEMINI_API_KEY` | API key |
| `bedrock` | *(none)* | AWS IAM; `AWS_REGION` selects the region |
| `mock` | *(none)* | no network |

Insertion order of `PROVIDER_KEYS` in `api/main.py` is the order the UI lists
providers in and must stay stable.

**Readiness is checked before work starts.** `POST /api/projects/{slug}/ingest`
resolves the *effective* provider (request value, else `LLM_PROVIDER`), and
refuses with **400** if it is unset, unknown, or missing its key — a
configuration error, never a 502, and the store is left untouched. A provider
that fails mid-run surfaces as **502** with the exception text verbatim.

**The scanned-PDF fallback is independent of the selected provider.** If
`ANTHROPIC_API_KEY` is present, `procurement/pdf_llm.transcribe_pdf` is wired in
for image-only PDFs even when ingestion runs on OpenAI or Bedrock.

**One capability is deliberately absent.** Vendor suggestion
(`workflow/vendor_suggestions.py`) is model *recall*, not search — no crawler,
no search index, no provider web-search tool. Every surface that shows it says
"suggested by the model". Adopting a real web-search tool is a separate phase.

---

## 6. Configuration reference

All configuration is environment variables. `api/main.py` calls
`dotenv.load_dotenv()` at import, resolving `.env` by walking up from the module
— so it works identically under `uvicorn`, Docker and pytest. A real environment
variable always wins over the file, which is what makes runtime injection work
against an image that baked one in.

### 6.1 Model selection

| Variable | Default | Effect |
|---|---|---|
| `LLM_PROVIDER` | *(none — required for ingestion)* | `anthropic` \| `openai` \| `gemini` \| `bedrock` \| `mock`. Overridable per ingestion run from the UI. |
| `ANTHROPIC_API_KEY` | — | Anthropic provider **and** the scanned-PDF transcription fallback. |
| `OPENAI_API_KEY` | — | OpenAI provider. |
| `GEMINI_API_KEY` | — | Gemini provider. |
| `AWS_REGION` | — | Bedrock region (auth is IAM). |
| `LLM_MODEL` | provider default | Overrides the extraction model. |
| `PDF_LLM_MODEL` | `claude-sonnet-4-5` | Model for the PDF transcription fallback. |
| `LLM_TEMPERATURE` | *unset* | Unset means **send none**, which is what production does. |
| `LLM_MAX_TOKENS` | provider default | Output ceiling. Do not set it to green a coverage floor — see §12. |

### 6.2 Extraction tuning

| Variable | Default | Effect |
|---|---|---|
| `TECH_CHUNK_CHARS` | `12000` | Line-boundary chunk budget for the technical-facts extractor. |
| `REQUIREMENTS_CHUNK_CHARS` | `8000` | Same, for the requirements extractor. |

Both floor at `MIN_CHUNK_CHARS = 1000`; an unparseable or too-small value logs
and falls back to the default rather than failing the run.

### 6.3 Storage and serving

| Variable | Default | Effect |
|---|---|---|
| `PROCUREMENT_PROJECTS_ROOT` | `projects` (`/data/projects` in the image) | **`<ROOT>`** for every store in §7. |
| `WEB_DIST` | `../web/dist` relative to `api/` | Compiled SPA. Mount is skipped if the directory is absent. |

### 6.4 Accounts

| Variable | Default | Effect |
|---|---|---|
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | — | Seed the first administrator on startup, **only while no user exists**. Unset logs a warning and starts anyway into a state where nobody can reach anything. |
| `ALLOW_SIGNUP` | `1` | `0` closes public self-registration; admins can still add people. |
| `AUTH_DISABLED` | off | Development bypass — serves an unauthenticated caller as the first admin. Never expose the port when set. |
| `DEV_USER_EMAIL` | first admin | Which account the bypass acts as. |

### 6.5 Container / launcher

| Variable | Default | Effect |
|---|---|---|
| `PROCUREMENT_HOST_PORT` | `8000` | Host port. Windows WinNAT reserves ranges that often include 8000. |
| `SEED_SAMPLE_PROJECTS` | `1` | `0` brings the container up with an empty store. |
| `INCLUDE_ENV` (build arg) | `false` | `true` bakes `.env` — including the provider key — into the image. Compose passes `true` for local builds; never push such an image. |
| `SAMPLE` (build arg) | `gas-14` | `none` builds without the bundled sample project. |
| `LLAMA_CLOUD_API_KEY`, `LLAMAPARSE_TIER`, `LLAMAPARSE_CACHE_DIR` | — | `tools/parser_ab.py` only. |

---

## 7. Persistence requisites

Four stores, all file-backed under `<ROOT>`, all with distinct rules. There is
no database server, no message broker and no cache.

| Store | Path | Format | Authority |
|---|---|---|---|
| Project snapshots | `<ROOT>/<slug>/store/` | JSON per collection + `events.jsonl` | **Authoritative** for extraction results. |
| Search index | `<ROOT>/<slug>/index/store.db` | SQLite | **Derived and disposable** — rebuildable from the snapshots. |
| Accounts | `<ROOT>/auth.json` | one JSON document | Users, sessions, grants. |
| RFQ workflow | `<ROOT>/workflow.json` | one JSON document | Projects, items, RFQs, shortlists, clarifications, documents. |
| Bidder registry | `<ROOT>/bidders.db` | SQLite | Organisation-wide vendor registry (~1 300 rows). |
| RFQ blobs | `<ROOT>/rfq-docs/<rfq_id>/<sha[:2]>/<sha>/<filename>` | content-addressed files | The actual uploaded enquiry and bid documents. |

`STORE_VERSION` is `2` (`procurement/store/layout.py`).

### 7.1 Durability contract

Every JSON write goes through `layout.atomic_write_json`: unique temp name per
writer, `flush` + `fsync`, then `os.replace`. A unique temp name is load-bearing
— a name derived from the destination lets two concurrent writers truncate each
other into valid-but-mixed JSON. That primitive makes a concurrent write
last-writer-wins; it is **not** a substitute for serialising callers, which is
what the locks in §9 are for.

### 7.2 Invariants

The full set lives in [`CLAUDE.md`](../CLAUDE.md) and is normative there. The
load-bearing shape, in brief:

- **Snapshot store** — every write through `store/snapshots.py`; `generation`
  bumps once per *transaction*, not per file; `field_path` addresses list
  members by **id**, never index; a collection holds exactly the records of its
  currently-live sources (orphans are pruned, not skipped); missing data is
  omitted, never coerced to `0` or `""`; a failed extraction never blanks
  previously-good data and always records why.
- **Auth store** — every write, *and every decision that gates one*, inside
  `locked_update`; sessions persist `sha256(token)` only; `password_hash` is not
  a field on `User`; roles are set once at creation and no route changes one.
- **Workflow store** — `save` replaces the document wholesale, so any field
  added to `WorkflowStore.__init__` needs a line in both `to_document` and
  `from_document`; history is append-only; deletion is refused while anything
  live references the target; derived values (client approval, clarification
  state, bid due date) are computed on read and stored nowhere.
- **Blob store** — a blob is deleted only when no surviving record references
  it, keyed on digest **and** leaf name; `BlobRef.rel_path` is storage-relative,
  so the root can move and an S3 backend has somewhere to put a key.

**Arithmetic stays in Python.** Extractors capture numbers and units verbatim;
code decides compliance. No extractor is ever asked whether a vendor complies.

---

## 8. Component specification

### 8.1 `procurement/` — tender comparison

Entry point `pipeline.run_ingestion(root, slug, client, pdf_fallback=, force=)`.

1. **Inventory** — walk the project directory into `DocumentRecord`s (vendor
   documents and RFQ documents separately).
2. **Classify** — `classify.py`: filename rules first, model second. Classes are
   `spec`, `quotation`, `datasheet`, `deviation`, and others.
3. **Revision lineage** — `revisions.py` resolves `supersedes` /
   `superseded_by`; superseded documents' facts are pruned.
4. **Route and extract** — per class: `extract.py` (quotations),
   `extract_tech.py`, `extract_requirements.py`, `extract_deviation.py`,
   `extract_mom.py`. Whole-document extractors chunk on line boundaries
   (`chunking.py`) and merge all-or-nothing.
5. **Normalize** — `normalize.py` / `renormalize.py` apply FX rates to a target
   currency; `normalization_status` distinguishes `ok` from `no_fx_rate`.
6. **Build** — `matrix.py` (compliance matrix), `statement.py` (comparative
   statement), `coverage.py` (extraction status rollup), `export.py`
   (CSV/XLSX).

### 8.2 `workflow/` — the RFQ process

Eight stages, **deny-by-default** transitions (`workflow/stages.py`):

| Stage | Code | Forward to |
|---|---|---|
| Shortlisting | RFQ-02 | Issued |
| Issued | RFQ-03 | Clarifications |
| Clarifications | RFQ-04 | Bids Received |
| Bids Received | RFQ-04A | Evaluation |
| Evaluation | RFQ-05 | Negotiation *(back: Issued — retender)* |
| Negotiation | RFQ-06 | Awarded *(back: Evaluation — renegotiate)* |
| Awarded | RFQ-06 | PO Issued |
| PO Issued | RFQ-07 | *(terminal)* |

Codes are **fixed references, not ordinals** — RFQ-04A exists, and RFQ-06 covers
two stages. They are served to the browser rather than computed there.
`Scoping` is retired: it keeps its name in stored history, and only an RFQ's own
`stage` is migrated forward.

Only **forward** transitions are gated (`workflow/gates.py`); the backward edges
are documented recoveries and must never be blocked. A gate never returns a bare
`False` — `GateResult` carries a reason, surfaced as a 409.

Supporting modules: `bidders.py` / `eligibility.py` (pure suitability logic, no
I/O, no clock), `bidder_db.py` (SQLite registry with child tables for the three
filtered list-valued fields), `disciplines.py` (the controlled vocabulary and
the single label-folding function), `clarifications.py`,
`clarification_answers.py` (grounded answer drafting), `mr_index.py` /
`passages.py` (retrieval over the enquiry package), `doc_store.py` (blobs),
`safe_extract.py` (the **only** traversal guard in the repository),
`avl_import.py` / `avl_db.py` / `astra_import.py` (vendor-list import),
`rfq_extractor.py` (fixture-backed enquiry read), `vendor_suggestions.py`.

**`safe_extract` is the one traversal guard, and it is deliberately strict.** A
`..` segment is refused on the segment, not on where it resolves; both
separators are normalised first, so `..\..\evil` is refused on Linux as well as
Windows. Refusals name the uploader's entry, never our destination directory.
`has_drive` is a regex, not `os.path.splitdrive` — the latter is `posixpath` on
Linux and answers `('', 'C:/Windows/x')`, so the check read as one and was not.

### 8.3 `api/` — HTTP

`api/main.py` (project setup, ingestion, review, exports), `api/auth/`,
`api/admin_routes.py`, `api/workflow_routes.py` (47 routes). Middleware order is
load-bearing: auth is registered *before* CORS so CORS ends up outermost and a
401 still carries the headers a cross-origin browser needs to read the status.

### 8.4 `web/` — SPA

React 19 + react-router 8, Vite build, URL-backed navigation (every screen has a
shareable deep link — the `_SpaFiles` mount serves `index.html` for unknown
*file* paths but never for `/api/*` and never in place of a real asset).

---

## 9. Concurrency and consistency requisites

| Boundary | Mechanism | Scope |
|---|---|---|
| Ingestion runs | in-process `threading.Lock` **per project slug** | One run per project. Non-blocking: a second caller gets **409**, never a queue. Per-slug, so a slow run is not a queue for other projects. |
| `project.json` mutation | `procurement.project.update_project` context manager | Read-modify-write as one critical section. |
| Snapshot writes | `store/snapshots.py` transaction | One `generation` bump per transaction. |
| Auth writes | `api/auth/store.locked_update` | The write **and** the decision that gates it. |
| Workflow writes | `workflow/persistence.locked_update` | Same. `transition` runs inside the block, never against a store loaded before it. |

**The ingest lock's limits are stated, not implied.** It is in-process, which
matches the shipped deployment exactly — one uvicorn worker, no `--workers`,
one volume. It does **not** serialise two containers on a shared volume, a
multi-worker uvicorn, or `cost-est` writing alongside the API. Any of those
needs a lockfile with a stale-lock policy first.

**The recurring rule, five times over:** a check made in the caller and a write
made in the store are two critical sections, not one. Both guards the auth
subsystem shipped wrong were reads outside the lock.

---

## 10. Security requisites

- **Fail closed.** Every `/api/*` path requires a session except exactly three:
  `/api/health`, `/api/auth/login`, `/api/auth/signup`. That set is pinned by a
  test asserting its exact membership, so a new route is protected the day it is
  added.
- **Passwords** are `scrypt` with a per-password salt, stored as
  `scrypt$n$r$p$salt$dk`. The digest is never a field on `User`, so it cannot
  reach a response body.
- **Sessions** persist `sha256(token)` only, in `auth.json`. The cookie is a
  browser-session cookie with no `max_age`.
- **Startup signs everyone out.** The lifespan calls `clear_sessions`, so a
  relaunch always lands on the sign-in page rather than dropping whoever signed
  in last back inside. The deliberate cost: restarting the API signs out every
  user.
- **Roles are `admin` and `reviewer`**, set once at creation; no route changes a
  role. A reviewer sees only granted projects — enforced server-side on every
  route, with the nav merely hiding what they cannot use.
- **Slugs** are matched against `list_projects` (which reads `os.listdir`), never
  by asking the filesystem whether a path exists — Windows and macOS resolve
  case-insensitively and CI does not, so that bug class cannot fail in CI.
- **Uploads** pass through `safe_extract` (§8.2). Requirements uploads are
  restricted to `.pdf`, `.docx`, `.xlsx`.
- **Secrets** never reach an image unless `INCLUDE_ENV=true` is passed
  explicitly; `.env` is in `.dockerignore` and stripped in the source stage so
  the switch really means absent.

**Two known, deliberate exposures**, recorded rather than hidden:

1. `AUTH_DISABLED=1` serves an unauthenticated caller as the first
   administrator. It never mints a user — with no admin in the store it stays
   closed — and a real session still wins so attribution stays honest. Never run
   it on a reachable port.
2. The sign-in screen's demo-account chip embeds `ADMIN_PASSWORD` as a string
   literal in `web/src/pages/Auth.tsx`, so it **ships in the bundle and is
   publicly readable**. This was asked for with the exposure stated. That
   address and password must not be reused anywhere it would matter, and this is
   not a pattern to copy for a second account.

---

## 11. HTTP interface

73 routes. Every one below requires a session unless marked public.

| Group | Prefix | Count | Notes |
|---|---|---|---|
| Health | `/api/health` | 1 | **public** |
| Auth | `/api/auth/*` | 5 | `login`, `signup` **public**; `logout`, `me`, `password` |
| Admin | `/api/admin/*` | 5 | users and per-project grants; `require_admin` |
| Projects & review | `/api/projects/*` | 15 | list/get, compliance matrix, summary, extraction status, statement, two exports, feedback, setup, create, requirements, vendors, fx-rates, ingest |
| RFQ workflow | `/api/workflow/*` | 47 | stages, projects/items, item vendor lists, draft shortlists, vendor suggestions, bidders, disciplines, RFQs, documents, shortlist, TBE, VDRL, queries, addenda |

Route-ordering constraints that are enforced by tests because FastAPI resolves
in declaration order: `/bidders/approved` must precede `/bidders/{bidder_id}`,
and `/rfqs/extract` must precede `/rfqs/{rfq_id}`.

Status-code conventions: **400** unrunnable configuration; **404** unknown
entity; **409** conflicting state (duplicate project, run in progress, blocked
gate); **422** malformed input (including a non-boolean `force`, a non-string
`provider`, and an unknown export format); **502** a provider or extraction
failure, surfaced verbatim.

---

## 12. Build, test and CI requisites

```bash
python -m pytest
```

```bash
cd web && npm test
```

Two Python baselines, both correct — they differ only in which untracked
fixture directories are present, never in pass/fail:

| Environment | Baseline |
|---|---|
| Workstation with `data/`, an ingested multi-vendor `projects/`, and `pdftotext` | **1819 passed, 3 skipped** |
| CI, and any clean checkout | **1799 passed, 23 skipped** |

The web suite is separate: **335 passed** across 23 files.

The skip gates, in order of how much trouble they have caused: provider
credentials (3), an ingested `projects/` corpus (4), a `data/` sample directory
(3), `pdftotext` (2), and the real ADNOC AVL export (11). The arithmetic
relating the two rows, and the rule that the workstation row is *measured* and
the CI row *derived from it*, are normative in
[`CLAUDE.md`](../CLAUDE.md#running-things) — do not edit the two rows
independently.

> The header comment in [`.github/workflows/tests.yml`](../.github/workflows/tests.yml)
> still quotes `1088 passed, 12 skipped`. That is stale; `CLAUDE.md` is the
> authority. Worth correcting the next time that file is touched.

**The suite is key-free and must stay that way.** It runs against
`shared/llm/mock_client.py`, and no test may require `ANTHROPIC_API_KEY`. CI
configures no provider secret deliberately: supplying one would turn
skip-guarded live tests into billed calls on every PR, put network flakiness in
the merge gate, and widen the key's blast radius to fork PRs. A test that needs
a key belongs behind a skip guard, not behind a repository secret.

**CI triggers on `main` only** — pushes to and PRs into `main`. A PR targeting
any other branch merges without this gate.

`npm run build` type-checks `*.test.tsx` too (`web/tsconfig.app.json` includes
`src`), so CI runs it as a separate step after `npm test`.

**`test_real_corpus_coverage.py` is a coverage instrument, not a unit test.** It
asserts floors against the newest multi-vendor store in `projects/`, measured at
**shipped defaults** — no `LLM_MAX_TOKENS`, no chunk-budget override. If a floor
goes red, chunk further or fix the routing. Never lower the floor, and never
green it with an environment override.

---

## 13. Operational requisites

| Concern | Value |
|---|---|
| API port | 8000 (`ApiPort`, `PROCUREMENT_HOST_PORT`) |
| Web dev port | 5173 (`WebPort`) |
| Container port | 8000, fixed |
| Health check | `GET /api/health` — 30s interval, 5s timeout, 20s start period, 3 retries |
| Container user | non-root, uid 10001 |
| Workers | one; the ingest lock (§9) assumes it |
| Volume | `projects:/data/projects` — survives `down`, destroyed by `down -v` |

`run.ps1` **makes its ports available** rather than merely checking them. A port
held by this repo's own leftovers is reclaimed; a port held by anything else is
reported with its command line and refused, and `-Force` is what takes those.
The "ours" test is deliberately asymmetric — the web server must prove it is
this checkout (`vite` plus the repo path), while the API is matched on
`api.main:app` alone, because a venv's `python.exe` reports its *base*
interpreter and the repo path never appears on that line. Reclaiming kills the
outermost process of the holder's tree, since `uvicorn --reload` and
`npm run dev` are supervisors that would respawn onto the same port.

On Windows, a failed bind on 8000 with no process holding the port is usually
WinNAT: check `netsh interface ipv4 show excludedportrange protocol=tcp`.

The bundled sample project `gas-14` is copied into the volume on start unless a
project of that slug is already there. **It carries real vendor quotations** —
an image built with it is confidential and must not be pushed to a public
registry.

---

## 14. Data-handling requisites

**Nothing synthesised may be indistinguishable from something recorded.** This
is the rule that constrains the most code, and it has four instances:

- The AVL import folds ~1 300 real named companies out of a client export and
  leaves every field the sheet does not carry **empty** — no expiry, no hold, no
  turnover, no rating, no country. A synthesised suspension looks identical on
  screen to a recorded one.
- An invented vendor may carry `Astra` (an internal qualification of an invented
  company) but never `ADNOC` — that is a fact about a row in the client's
  export. A test enforces it; it shipped broken once.
- The RFQ extractor is a fixture and says so: its reference is prefixed `MOCK-`.
- Model-suggested vendors are labelled *suggested by the model*, carry no
  approvals, no `vendor_id` and no prequalification, and are never promoted to
  hand-added on acceptance — that the name originated with a model is the single
  thing a later reader would most want to know.

**Curated vendor rows never link to the registry.** `vendor_id` stays `None`,
and no route looks the typed name up. Name matching has been a shipped defect
twice; a match would silently attach a real company's approvals to a string
somebody typed. On screen that means no Registry column and no "not found" tally
— rendering "Not in the registry" reports a check nobody ran.

**A provider outage is a 502, never an empty list.** "The model is unreachable"
and "no such companies exist" must not render identically.

**Three-state truth is preserved.** `null` (no registry row — nothing was
checked), `false` (a row nobody approved), and `true` are distinct everywhere
client approval is shown.

---

## 15. Explicit non-requisites

Capabilities the system does **not** have. Listed so nobody plans against them,
and so nobody "fixes" a deliberate absence.

| Not built | Detail |
|---|---|
| Mail transport | Nothing sends an RFQ, an answer or an addendum to a bidder. Recipient addresses exist; no transport does. |
| S3 / remote blob storage | The `BlobStore` protocol exists so adding it is a new class rather than a refactor. Only the local implementation is written. |
| Real web search | See §5. The label matches the mechanism today. |
| Bulk invitation endpoint | "Shortlist selected" is N sequential calls to `POST /rfqs/{id}/shortlist`, one per vendor. The per-vendor guards are what make an invitation an attributed act; a bulk route would have to bypass them. Not all-or-nothing: one refusal fails alone. |
| Bulk acceptance of suggestions | Taking twenty unverified companies in one click is precisely the act that needs friction. |
| RFQ deletion | Nothing deletes an RFQ; deleting a project holding one is refused. The invariant is held at both ends instead. |
| Multi-worker / multi-container writes | See §9. Needs a lockfile with a stale-lock policy first. |
| Freezing a technical package from the browser | The Issued step was reduced to one upload control. `issue_addendum` is therefore unreachable in practice for new RFQs, though the store rule is untouched. Stated before the change and chosen anyway. |
| Setting a VDRL line from the browser | The `vdrl_received / vdrl_required` tally on Bids Received counts only lines the demo seed wrote. |
| Background jobs / scheduler / queue | Ingestion is synchronous in the request thread, under the per-slug lock. |
| Eligibility expiry sweep | Expiry is derived from `prequal_expires_on` against an `as_of` the caller passes, so no sweep job is needed to keep the store honest. |

The last two rows in that table describe routes that are **still served and
still covered by the Python suite** — only the browser controls were removed.
