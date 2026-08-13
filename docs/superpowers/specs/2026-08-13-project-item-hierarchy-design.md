# Project → item hierarchy — design

**Date:** 2026-08-13
**Branch:** `rfq-platform-phase-1`
**Status:** approved, ready to plan

A project is the top-level container a client gives us — Haliba, say. Everything
procurement does hangs off it: the generators, the cables, the transformers are
its *items*, and an RFQ is raised against one or more of them. The workflow store
has modelled that shape since phase 1 and nothing in the front end has ever
reached it. This design builds the screens that do, and the routes they need.

---

## 1. What already exists, and what is missing

`workflow/models/project.py` already defines both entities, and
`api/workflow_routes.py` already exposes three of the routes:

| | modelled | route | UI |
| --- | --- | --- | --- |
| create project | yes | `POST /api/workflow/projects` | **none** |
| list projects | yes | `GET /api/workflow/projects` | **none** |
| create item | yes | `POST /api/workflow/projects/{id}/items` | **none** |
| list items | yes | `GET /api/workflow/projects/{id}/items` | **none** |
| create RFQ | yes | `POST /api/workflow/rfqs` | **none** |
| read one project | — | **none** | **none** |
| edit / delete either | partial (`rename_project`) | **none** | **none** |

So the gap is mostly, but not only, a front end. `WorkflowStore.rename_project`
exists and has no route; `WorkflowStore.validate_against_live_period` exists and
has no caller at all. An RFQ cannot be created from the browser today — the RFQ
workflow screen is a read-only roster over records that only Python can produce.

## 2. Scope decision — one project concept, not two

The word "project" means two different things in this application:

- a **workflow project** (`prj_…`) — name, code, client, location, live period,
  currency, status, and the eight-stage RFQ process;
- an **ingestion project** (`projects/<slug>/`) — the folder of vendor bids that
  Set up & ingest writes and that Overview, Compliance matrix and Comparative
  statement read.

**This work builds the hierarchy on the workflow project only.** Bid evaluation
keeps its own project list, its own store and its own screens, entirely
untouched. Unifying the two identities is real work with a real payoff and it is
deferred deliberately, not overlooked — the readiness note
([`2026-08-13-rfq-platform-phases-2-5-readiness.md`](../plans/2026-08-13-rfq-platform-phases-2-5-readiness.md))
already lists it as phase 2's `project/item scoping`, and it would rewire every
review screen. Building the hierarchy first is what makes that unification a
migration rather than a design exercise.

**Also out of scope, explicitly:** an item-type catalogue, per-type technical
spec templates, free-form spec lines on an item, and any change to the Bid
evaluation screens beyond the single nav label in §3. The item's existing eight
fields are the detail set; more fields are a later decision informed by using
these screens, not a guess made before.

## 3. Navigation

A new front-door screen in the **RFQ process** group:

| group | entries |
| --- | --- |
| RFQ process | `01` Projects & items · `02` RFQ workflow |
| Bid evaluation | `03` Bid sets · `04` Set up & ingest · `05` Extraction status · `06` Overview · `07` Compliance matrix · `08` Comparative statement |
| Administration | `09` Users and access |

Two existing decisions change, both on purpose:

1. **The Bid-evaluation entry named "Projects" is renamed "Bid sets".** Two rail
   entries reading "Projects" over two different stores is the first confusion a
   user would hit. This is a label in `App.tsx`'s `NAV` table and the
   `PageHeader` eyebrow on `Dashboard.tsx`; nothing about that half of the
   application moves.
2. **Signing in lands on Projects & items**, not on the RFQ workflow. Phase 1
   made the RFQ process the front door on the reasoning that ingestion is not
   the process; that reasoning still holds, and the project roster is one step
   further up the same half of the app. `App.test.tsx` asserts the current
   landing screen — that assertion is updated as part of this change, which is
   the test doing its job.

The rail's existing project switcher belongs to Bid evaluation and stays as it
is. A second project dropdown beside it, over a different store, would be worse
than no dropdown at all — so the workflow hierarchy lives entirely in the canvas.

## 4. The three screens

### 4.1 Projects

A table of every workflow project: name, code, client, location, live period,
status, item count, RFQ count. Below it, a "New project" form over the seven
`ProjectIn` fields. The name links to the detail screen.

### 4.2 Project detail

The project's own fields in a header that can be switched into an edit form,
then two tables.

**Items** — type, description, qty, UOM, discipline, estimated value,
required-on-site, long-lead. Each row carries a checkbox and links to the item.
An "Add item" form sits below the table.

**RFQs** — every RFQ raised against this project: reference, package,
discipline, stage. Each links into the existing wizard or detail view, which
already choose between themselves by stage.

Above the items table, **Raise RFQ from selected items** takes the ticked items
and collects reference, package, discipline and value estimate, then posts to
the existing `POST /api/workflow/rfqs`. This is the step that closes the loop:
project → items → RFQ → the wizard that was built in phase 1 and could not be
entered without a Python session.

### 4.3 Item detail

The item's eight fields, editable, and the RFQs whose `item_ids` include it.
Deliberately thin. It exists because "which RFQs cover this cable" has no other
answer in the product, and because a delete refused for that exact reason (§6)
needs somewhere to send the reader.

### 4.4 Component boundaries

Three page modules — `web/src/pages/Projects.tsx`, `ProjectDetail.tsx`,
`ItemDetail.tsx` — with the forms extracted into their own components rather
than inlined, so no file here repeats what `RfqWizard.tsx` already demonstrates
at 634 lines: a page that owns its screens, its forms and its state at once is
past the size where an edit is reliable.

Navigation state (`openProjectId`, `openItemId`) lives in the `Projects`
container, exactly as `RfqWorkflow` holds `openRfqId` today. `App.tsx` gains one
`View` value and the nav-table edits from §3, and nothing else.

## 5. Backend

### 5.1 Store

`workflow/store.py` gains `get_item`, `update_project`, `update_item`,
`delete_item` and `delete_project`.

`rename_project` is **replaced** by `update_project`, not kept beside it — one
way to do one thing. Its three call sites in `tests/test_workflow_store.py` move
across with it.

### 5.2 Routes

`api/workflow_routes.py` gains five routes, under the status mapping its module
docstring already fixes — 404 the named entity does not exist, 409 it exists but
the workflow refuses, 422 the request is self-inconsistent:

```
GET    /api/workflow/projects/{project_id}
PATCH  /api/workflow/projects/{project_id}
DELETE /api/workflow/projects/{project_id}
PATCH  /api/workflow/projects/{project_id}/items/{item_id}
DELETE /api/workflow/projects/{project_id}/items/{item_id}
```

Both `PATCH` bodies are partial: every field optional, and an absent field means
"leave it alone" rather than "clear it". A project accepts edits to all seven of
its `ProjectIn` fields plus `status`; an item accepts all eight of its own.
Neither accepts a change to an identity or a parent — `id` and `project_id` are
not writable, so an item cannot be moved between projects by editing it, and
`PATCH` on an item whose `project_id` does not match the path is a 404 on the
item rather than a silent write.

`GET /projects/{project_id}` returns the project, its items and its RFQs in one
response, the way `GET /rfqs/{rfq_id}` already returns every artifact its screen
reads. The detail page then makes one call, and the three lists cannot arrive
from three different generations of the document.

`{item_id}` is a new path parameter, so it must be added to the probe
substitutions in
`tests/test_auth_middleware.py::test_every_api_route_outside_the_allowlist_requires_a_session`.
That sweep is designed to fail when a route appears; this is it working. The new
routes are **not** added to `middleware.PUBLIC_PATHS`, which stays pinned at
three entries.

## 6. Deletion

Two guards, and **both live inside the store method, inside
`persistence.locked_update` — never in the route.**

This is not a stylistic preference. CLAUDE.md records that both auth guards this
repository shipped wrong were *reads outside the lock*: a check made in the
caller and a write made in the store are two critical sections, not one, so two
concurrent deletes each read "safe" and both land. `store.delete_user` and
`store.grant` are the shape to copy.

- **Delete item** — refused with 409 if any RFQ's `item_ids` contains it. The
  reason names the referencing RFQs by reference, so the reader knows what to
  amend or retender first. The item is addressed by `id`, never by position.
- **Delete project** — refused with 409 if the project holds any RFQ. Otherwise
  the project *and every one of its items* are removed in the same write.

Both deletes are confirmed in the browser before the request is sent, and the
project confirmation states how many items go with it. The server is still the
only authority on whether the delete is allowed — the dialog exists so a
permitted delete is not an accident, not to pre-empt the guard.

That last clause is the store invariant this work owns:

> **Store invariant owned:** `workflow.json` holds exactly the entities the store
> holds. `save` replaces the document wholesale, so a project deleted without its
> items leaves orphaned items in memory — and persists them.

This is phase 2's orphan-pruning defect reappearing in a different store, which
is why §8 gives it a **two-run** test — delete, reload from disk, assert the
items are gone — rather than a single-run assertion against memory that would
pass while the document on disk was wrong.

## 7. Live-period warning

`store.validate_against_live_period` already returns a reason string rather than
a bare boolean, and nothing calls it. Item create and item edit call it whenever
`required_on_site` is set, and return its reason as a **non-blocking warning**
in the response body, rendered as a caution under the form.

Non-blocking is the deliberate half: there are legitimate reasons to need
delivery after a live period closes, and a hard refusal would make the field
unusable in exactly those cases. A warning that names the period tells the user
what they are doing without deciding it for them.

## 8. Errors and testing

Every refusal reaches the user as the server's own sentence. A 409 from a delete
renders inline beside the row that refused it — not a toast, and never a bare
"failed". This is the rule the gates already follow: a refusal that does not say
what would unblock it is a defect, whether it comes from a gate or from a
foreign-key guard.

**Python**

- Store: each guard, the cascade, and each update path.
- Routes: each status code in §5.2, including the 409s from §6.
- Persistence: the two-run test from §6 — delete a project, reload the document,
  assert neither the project nor its items survive.
- `test_auth_middleware.py`: the `{item_id}` substitution.

**Web** (vitest, under `web/`)

- Projects: the roster renders, and the create form posts and refreshes.
- Project detail: items list, an RFQ is raised from a selection, and a refused
  delete surfaces the server's reason next to the row.
- Item detail: fields render and the covering RFQs are listed.
- Any test that renders `App` mocks `auth/context`'s `useAuth` and returns a
  **stable** object built once outside the `vi.mock` factory. A fresh identity
  per call refetches the project roster until the vitest worker dies of heap
  exhaustion, which surfaces as `Worker exited unexpectedly` rather than a
  failed assertion.

**Counts.** CLAUDE.md's two test-count rows both move. The workstation row is
re-measured (`pdftotext`, `data/` and an ingested multi-vendor `projects/` all
present) and the CI row is derived from it by the subtraction that file
documents — `CI = workstation − 4 − 3 − 2`. The rows are never edited
independently; that is how they drifted apart before.

## 9. What this deliberately does not do

- Unify the workflow project with the ingestion project (§2).
- Add an item-type catalogue or per-type spec templates (§2).
- Change any Bid evaluation screen beyond the nav label (§3).
- Change roles, permissions or the auth store. Both roles reach these screens;
  the server enforces the session boundary as it does for every `/api/` path.
