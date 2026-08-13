# RFQ platform, phases 2–5 — readiness note

**Status:** phase 1 is implemented and green on this branch. Phases 2–5 are
**not plannable yet**, for the reasons below. This note exists so that the next
session does not start by re-deriving them.

---

## What phase 1 actually shipped

Implemented from
[`2026-08-12-rfq-platform-phase-1.md`](2026-08-12-rfq-platform-phase-1.md),
ported to this repository's layout. The plan targets a `backend/app/` +
`frontend/` (Next.js) tree that exists on **no branch of this repo** — checked
every local and remote ref. The mapping used:

| Plan | Here | Why |
| --- | --- | --- |
| `backend/app/workflow/` | `workflow/` (new top-level package) | Kept out of `procurement/`: that package's store carries snapshot invariants (`generation`, `field_path` by id, orphan pruning) that this in-memory store does not share, and mixing them invites a reader to assume one set applies to both. |
| `backend/app/models/` | `workflow/models/` | — |
| `backend/app/routers/workflow.py` | `api/workflow_routes.py` | `api/routers/` holds no modules any more; `api/admin_routes.py` is the live convention. |
| `backend/tests/` | `tests/` | One suite, run from the repo root. |
| `frontend/components/rfq/StageStrip.tsx` | `web/src/components/StageStrip.tsx` | React 19 + Vite + plain CSS with theme tokens. There is no Tailwind and no Next.js here, so the plan's utility classes were rewritten as `.stagestrip` / `.stagestep` rules in `web/src/theme.css`. |

**Plan tasks 1 and 10 were dropped as inapplicable, not skipped.** Task 1 splits
an existing `backend/app/models.py` into a package; there is no such module here,
so the package was created directly. Task 10 widens a five-item legacy `STAGES`
constant in `backend/app/routers/procurement.py`; this repo has no such constant
and no seed RFQ data — `grep` for `STAGES`/`stage_counts` matches nothing outside
`.venv`.

**Two defects in the plan, fixed rather than worked around:**

1. Its scoping-gate test asserts `"frozen" in result.reason.lower()` against a
   store with **no** technical package, but the no-package branch of
   `_scoping_exit` returns a message that never says "frozen". The message was
   widened to name the whole exit criterion. Weakening the test was the
   alternative and would have left a user who is told only "no package" to guess
   that attaching one is not sufficient.
2. Its `get_rfq` route body contains a dead scaffolding loop that the plan then
   tells you to delete two paragraphs later. Written correctly the first time.

**Additions beyond the plan, each forced by the goal "real end to end":**

- `GET /api/workflow/projects`, `GET /api/workflow/projects/{id}/items`,
  `GET /api/workflow/rfqs` — the roster the screen reads.
- `PUT /api/workflow/rfqs/{id}/technical-package` and its `/freeze` — without
  these no RFQ can pass a gate *through the API at all*, so the workflow would
  have been reachable only from Python.
- `web/src/pages/RfqWorkflow.tsx` — the plan hosts its strip on an existing
  `rfq-status` page. There is none here, so the component would have been dead
  code.
- `workflow/persistence.py` — see below.

**One pre-existing test needed an edit:**
`test_auth_middleware.py::test_every_api_route_outside_the_allowlist_requires_a_session`
sweeps every route and asserts its probe can fill each path parameter. The two
new parameters (`{project_id}`, `{rfq_id}`) had to be added to its substitution
list. That is the test's own documented extension point — it is designed to fail
when a route is added — and the workflow routes were deliberately **not** added
to `middleware.PUBLIC_PATHS`, so they are challenged like every other `/api/`
path.

---

## The one piece of phase 2 that was buildable, and is built

Phase 2's row reads *"RDS, S3, Entra SSO, project/item scoping, vendor database,
portal"*. Of those, **persistence** was both the highest-value gap and the only
one reachable without provisioning anything: phase 1's store was in memory, so
every RFQ died with the process, which made the whole feature a demo.

`workflow/persistence.py` writes `<ROOT>/workflow.json` using the pattern
`auth.json` already established here — one document, one lock, one
`layout.atomic_write_json`. RDS is one way to satisfy "persistence"; this is the
infrastructure-free way, and swapping it for a database is a change to that one
module, because `WorkflowStore` still knows nothing about disk.

The invariant it defends, in the phase-2 template's required form:

> **Store invariant owned:** `workflow.json` holds exactly the entities the
> store holds — `save` replaces the document wholesale, so an entity removed in
> memory cannot survive on disk.

Every mutating route now runs inside `persistence.locked_update`, **and so does
the gate check that authorises it**. That placement is deliberate and is the
lesson this repo already paid for: CLAUDE.md records that both auth guards
shipped wrong were *reads outside the lock*. A gate that reads the package, the
shortlist and the TBE template in the route and writes in the store would be two
critical sections, and two concurrent transitions could each read "gate open".

---

## Why phases 2–5 cannot be planned yet

### 1. The source specification does not exist in this repository

Phase 1's self-review traces every task to a requirement id — `C2-R1`, `C5-R4`,
`C7-R18`, `C10-R6` — from `platform-requirements-and-capabilities.md`. That file
is **not in `docs/`, not in `docs/superpowers/specs/`, and not on any branch**,
local or remote. Its "known gaps" section defers four further ids to phase 2
(`C2-R4` item-type catalogue, `C5-R7` bid window timers, `C7-R1/R2` returnables
checklist, `C10-R5` named gates per stage).

Without that document, phases 2–5 would be scoped from four one-line table rows.
Writing `C6-R3` into a plan without being able to read `C6-R3` is inventing
requirements and calling them traceable. **Supply that file and phases 2–5
become ordinary planning work.**

### 2. Phases 2, 3 and 5 name infrastructure that needs your accounts

RDS, S3, Entra SSO, Textract and KMS are not code decisions — they need an AWS
account, an Azure tenant, credentials, and a spend decision. Provisioning them
is also squarely outside what should happen unattended. Note too that phase 1's
global constraint is *"No new infrastructure"*, which phase 2 reverses; that
reversal is a design decision worth making deliberately rather than inheriting
from a table row. Much of what those services would provide already exists here
in another form — `api/auth/` is a working identity layer, `procurement/store/`
is a working persistence layer — so "adopt RDS" may be the wrong shape of
question.

### 3. The repo's own convention forbids skipping the planning step

`CLAUDE.md` requires `docs/superpowers/PLAN-TEMPLATE.md` be read before writing
a plan for phase 3 or 4, and that template exists because **phase 2 of the
earlier effort shipped seven defects that survived per-task TDD and seven honest
reviews** — every one needing two runs or two modules to see. Implementing four
phases overnight, unplanned and against an absent spec, reproduces exactly that
failure at four times the scale.

---

## What the next session should do

1. **Locate or rewrite `platform-requirements-and-capabilities.md`.** Everything
   else is blocked on it. If it is lost, the phase 1 plan's requirement table and
   its deferral list together recover a usable skeleton of chapters C2, C3, C5,
   C7 and C10.
2. **Decide the infrastructure question explicitly** — whether phase 2 really
   means RDS/S3/Entra, or whether it means extending `api/auth/` and the existing
   store. That answer changes the plan more than any other input.
3. **Then plan phase 2 against the template**, one store invariant per task and a
   two-run mutation matrix on the integration task.

The four gaps phase 1 deferred are the natural first tasks whenever that
happens, and one of them — persistence, the reason `C2-R4`'s catalogue was
deferred as "not worth building until persistence exists" — is now unblocked.
