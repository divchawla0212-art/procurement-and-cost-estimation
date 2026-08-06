# Design: Authentication and User Hierarchy

Status: approved (brainstormed 2026-08-06)
Closes: `BUG-011` (S1, no admin boundary and no server-side authorization) and
`BUG-012` (S2, no per-user project grants) in
[`BUGS_TRACKER.md`](../../../BUGS_TRACKER.md).
Branch: `claude/build-verification-pr-26cc64`, from `d00bb8f`.

---

## 1. Context and goal

The platform has no notion of *who* a request is from, and no notion of which
projects a person is entitled to. Both gaps are measured, not assumed:

| claim | evidence |
|---|---|
| No route takes a caller identity | `grep -rn "Depends\|Authorization\|HTTPBearer\|Security" api/` → no matches |
| Every project is served to every caller | `list_projects` (`api/main.py:113`) returns `[_project_summary(p) for p in proj.list_projects(ROOT)]` — unfiltered |
| 16 routes, none of them a user or role route | `grep -c "^@app\." api/main.py` → 16 |
| Nothing can hold a grant | no principal field anywhere in `procurement/store/`; `User` is `{ email }` (`web/src/auth/context.ts:3-5`) |

The mock login added on branch `authentication` (PR #7) does not change this. It
gates rendering with one line — `if (!user) return <Auth />`
(`web/src/App.tsx:79`) — and `curl` reaches every route without loading the SPA.
Its own header says so: "SECURITY NOTE: this is a UI/demo convenience, NOT real
auth" (`web/src/auth/AuthProvider.tsx:9`).

The exposure is bidirectional, which is why BUG-011 is S1 rather than S2. Reads
leak one tender's vendor pricing to anyone evaluating another. Writes are worse:
`POST /{slug}/ingest` and `PUT /{slug}/fx-rates` are equally unauthenticated, so
an anonymous caller can move the numbers a reviewer makes an award decision
against. Every store invariant in `CLAUDE.md` assumes the writer is entitled to
write.

**Goal.** An admin sees and administers everything. Every other user sees exactly
the projects an admin has granted them. The decision is made on the server, per
request, against an authenticated identity — never in the SPA.

## 2. Decisions taken

Settled during brainstorming; recorded here so the plan does not reopen them.

| # | Decision | Rationale |
|---|---|---|
| D1 | **One buyer org per deployment.** Admin plus a handful of reviewers. No tenant or org entity. | The deployment is a single container on one client's network. An org entity would be a data model serving nobody. |
| D2 | **Stdlib sessions; no new dependencies.** `hashlib.scrypt` for passwords, opaque random session tokens, `HttpOnly` cookie. | Matches the repo's minimal-dependency, self-contained-image posture. Opaque server-side tokens revoke by deletion; stateless JWTs would need a denylist to do the same thing. |
| D3 | **Self sign-up stays, but is inert.** A new account is a `reviewer` with zero grants and therefore an empty project list. First admin is seeded from env. | Keeps PR #7's sign-up screen meaningful without letting registration confer access. |
| D4 | **Reviewers read and record review feedback; admins own ingestion.** | Ingestion, FX rates, vendor upload and requirements attachment move every number on a tender project-wide. Review feedback is the reviewer's actual job. |
| D5 | **Fail-closed middleware for authentication; explicit dependency for authorization.** | Authentication is uniform and belongs in one place that defaults to closed. Authorization varies per route and belongs visible at the route. |
| D6 | **Two slices, lockdown first.** | BUG-011 is the S1 that blocks distribution; it should stop blocking before the admin UI is built. |
| D7 | **Users and grants live in `<ROOT>/auth.json`.** | Persists with the Docker `projects` volume, so existing backup covers it. Not `projects/<slug>/store/`, which is per-project and carries snapshot invariants that do not apply. Not `index/store.db`, which `CLAUDE.md` declares "derived and disposable". |

## 3. Scope

**Slice 1 — closes BUG-011.** Auth store, password hashing, sessions, the
fail-closed middleware, the two authorization dependencies, the route matrix,
admin bootstrap, and the SPA rewrite from mock to real auth. End state: an admin
sees and does everything; a self-registered reviewer signs in successfully and
sees an empty project list. Shippable on its own.

**Slice 2 — closes BUG-012.** The grants API and an admin-only user-management
screen. `require_project_access` already consults grants in slice 1, so slice 2
adds the way to *create* grants, not the way to enforce them.

## 4. Data model and storage

A new module `api/auth/store.py` is the only reader and writer of
`<ROOT>/auth.json`. Nothing else touches the file, so the locking rule has a
single enforcement point — the discipline `procurement/store/snapshots.py`
applies to project writes.

`<ROOT>` is safe to hold this file: `proj.list_projects` counts only directories
containing `project.json` (`procurement/project.py:54-61`), so a plain file at
that level is invisible to project enumeration.

```json
{
  "version": 1,
  "users": [
    { "id": "u_9f3c1a7e2b04",
      "email": "reviewer@client.com",
      "role": "reviewer",
      "password_hash": "scrypt$16384$8$1$<salt_b64>$<hash_b64>",
      "created_at": "2026-08-06T09:12:44Z" }
  ],
  "grants": [
    { "user_id": "u_9f3c1a7e2b04", "slug": "gas-14",
      "granted_at": "2026-08-06T09:20:00Z", "granted_by": "u_0000admin0000" }
  ],
  "sessions": [
    { "token_sha256": "<hex>", "user_id": "u_9f3c1a7e2b04",
      "expires_at": "2026-08-13T09:12:44Z" }
  ]
}
```

Invariants, each with a reason it is stated rather than assumed:

- **A grant addresses a user by `id` and a project by `slug` — never by email,
  never by list index.** Email is mutable; an index is meaningless across a
  rewrite. This mirrors the store rule in `CLAUDE.md`: `field_path` addresses
  list members by id because list order is not stable.
- **Sessions persist only `sha256(token)`.** A leaked `auth.json` — a backup, a
  support dump, a mounted volume — yields no usable session. The cookie holds
  the only copy of the plaintext token.
- **`password_hash` is self-describing** (`scrypt$n$r$p$salt$hash`), so cost
  parameters can be raised later without a flag day; verification reads them
  back from the stored string.
- **Deleting a user cascades** to their grants and sessions in the same locked
  write. There is no `disabled` flag — a second state to test, for a team of a
  handful of people.
- **Every write is read-modify-write inside a lock, rewriting the file
  atomically** via `layout.atomic_write_json`. Two admins granting different
  users concurrently must not lose one grant. This is the same class of defect
  as BUG-009 and BUG-010; there is no reason to re-learn it here.

Accepted consequence: authenticating a request reads `auth.json`. At a few
kilobytes this is negligible. If it ever is not, an in-process cache keyed on
the file's mtime is the fix — not needed now.

## 5. Authentication

Routes live in `api/auth/routes.py`.

| route | public | behaviour |
|---|---|---|
| `POST /api/auth/signup` | yes | creates a `reviewer` with zero grants; 403 when `ALLOW_SIGNUP=0` (default `1`) |
| `POST /api/auth/login` | yes | verifies credentials, mints a session, sets the cookie |
| `POST /api/auth/logout` | no | deletes the session row, clears the cookie |
| `GET /api/auth/me` | no | returns `{id, email, role}`; 401 with no session |
| `POST /api/auth/password` | no | changes the signed-in user's password |

**Passwords.** `hashlib.scrypt` at `n=16384, r=8, p=1` with a 16-byte random
per-user salt, compared with `hmac.compare_digest`. Login runs a dummy hash when
the email is unknown so response timing does not reveal which addresses are
registered, and the error string stays `Invalid email or password.` for both the
unknown-email and wrong-password cases — as PR #7 already had it.

Minimum password length rises from PR #7's `MIN_PASSWORD = 6`
(`web/src/auth/context.ts:19`) to **8**. Six is short for a credential now
guarding real bid pricing.

**Sessions.** Login generates `secrets.token_urlsafe(32)` and stores only its
SHA-256. The cookie is `te_session`, `HttpOnly`, `SameSite=Lax`, `Path=/`,
`Max-Age` 7 days (absolute, no sliding refresh), with `Secure` derived from
`request.url.scheme` so it enables itself behind TLS without breaking the
documented plain-HTTP `docker compose` flow.

`SameSite=Lax` is also the CSRF control: it withholds the cookie from cross-site
`POST`/`PUT`, which covers every mutating route. Expired sessions are pruned on
the next write rather than by a background task.

**Bootstrap, and its deliberate failure mode.** On startup, if `auth.json` holds
no users and both `ADMIN_EMAIL` and `ADMIN_PASSWORD` are set, exactly one admin
is seeded. If those vars are not set, **nothing is seeded**: the app starts,
sign-up still works, but every account is an ungranted reviewer, so no project
is reachable by anyone until the operator sets the vars and restarts. Startup
logs a warning naming both variables.

The rejected alternative — "the first account to register becomes admin" — is
the exact shape of BUG-004: a silent default that quietly does the consequential
thing. A deployment with no configured administrator should be inert, not
self-appointing.

**Documented gotcha.** `ADMIN_PASSWORD` is read *only* while seeding. Once
`auth.json` exists the hash lives in the file, and editing the env var has no
effect. `POST /api/auth/password` exists so the bootstrap admin's password can
be rotated without hand-editing JSON.

## 6. Authorization

### 6.1 Authentication — fail-closed middleware

One `@app.middleware("http")` in `api/auth/middleware.py`. Any request whose
path starts with `/api/` is rejected with 401 unless it carries a valid session,
except for an explicit allowlist:

```python
PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/api/auth/signup"}
```

Non-`/api` paths pass through untouched, so the SPA bundle and the login screen
stay servable by the `StaticFiles` mount at `/` (`api/main.py:437`). The
resolved user is attached to `request.state.user`.

The property this buys: **a route added later is protected the day it is
written.** Making a route public requires editing a named constant, which is
visible in review. Per-route dependencies were rejected for the opposite
property — they are fail-open, and `api/main.py:382` already records one guard
in this codebase that was built and then bypassed by a default that slipped
past it.

### 6.2 Authorization — explicit dependencies

- `require_admin` — caller must be `role == "admin"`, else 403.
- `require_project_access(slug)` — admin passes; a reviewer passes only with a
  grant for that slug.

Applying D4, the 16 routes split:

| routes | guard |
|---|---|
| n | routes | guard |
|---|---|---|
| 1 | `GET /api/health` | public |
| 2 | `GET /api/projects` | authenticated, and **filtered** by grants |
| 3–10 | the eight slug reads: `GET /{slug}`, `/compliance-matrix`, `/summary`, `/extraction-status`, `/statement`, `/compliance-matrix/export`, `/statement/export`, `/setup` | `require_project_access` |
| 11 | `PUT /{slug}/vendors/{vendor}/feedback` | `require_project_access` |
| 12 | `POST /api/projects` | `require_admin` |
| 13–16 | `POST /{slug}/requirements`, `POST /{slug}/vendors`, `PUT /{slug}/fx-rates`, `POST /{slug}/ingest` | `require_admin` |

The five `/api/auth/*` routes in section 5 are additional to these 16.

Two details decide whether this is real or theatre:

**`GET /api/projects` filters; it does not merely authenticate.** If it only
required a session, every reviewer would still see every tender and the bug
would survive its own fix.

**An unentitled slug returns 403, not 404.** 404 would avoid confirming that a
project exists. Rejected: within one buyer org the slugs derive from tender
names staff already know, so the leak is worthless, while 404 would make a
typo indistinguishable from a permissions problem — a support call every time.

### 6.3 Error contract

`401` means "no valid session": the SPA clears its user and shows the login
screen. `403` means "signed in, not allowed": the SPA shows a refusal and
**must not log the user out**, or one mis-click ejects the user. The client's
`unwrap()` (`web/src/api.ts:10`) already surfaces `detail`, so both shapes fit
what exists.

**No authorization decision is made in the SPA.** Hiding a nav item is
presentation, not a control. The client's only job is to avoid offering actions
that would 403.

## 7. Client

`web/src/auth/AuthProvider.tsx` is rewritten to call the API instead of
`localStorage`, keeping the `useAuth()` shape so `App.tsx:79` and `Auth.tsx`'s
error rendering barely change. Three real changes:

- `login`/`signup` become `Promise<AuthResult>` — they are synchronous today
  (`context.ts:14-15`) — so `Auth.tsx`'s submit handlers become `async`.
- `User` gains `id` and `role`; `role` drives which nav items are offered.
- On first load the provider **deletes the legacy `te_users` and `te_session`
  keys**. PR #7 wrote plaintext passwords into every user's browser; the upgrade
  should clear them rather than leave them there.

Session restore moves from reading `localStorage` to `GET /api/auth/me` on
mount; a 401 simply means "show the login screen".

No fetch changes are needed for cookies: the client uses relative paths through
the vite proxy (`web/vite.config.ts`), and CORS already sets
`allow_credentials=True` against explicit origins (`api/main.py:74-83`).

## 8. Migration

Nothing to migrate. Existing projects have no grants, and no grants means only
admins see them. A deployment upgrading into this design gets **stricter, never
looser**, with no operator action.

## 9. Testing

New: `tests/test_auth_store.py`, `tests/test_api_auth.py`, and in slice 2
`tests/test_api_grants.py`. All key-free and `TestClient`-based, matching
existing conventions (`tests/test_api_setup.py:10-15`).

The guard that makes "fail-closed" real rather than aspirational:

> Iterate `app.routes` and assert that every `/api/` path outside
> `PUBLIC_PATHS` returns 401 when called without a session.

A route added next year is then covered by a test written today — the only
version of this guarantee that survives contact with future contributors.

Also asserted: a reviewer's `GET /api/projects` returns only granted slugs; a
reviewer gets 403 on an unentitled slug and on every admin-only route; expired
sessions reject; logout invalidates immediately; and no response body ever
contains `password_hash`.

**Existing suite.** All 49 tests across the five `tests/test_api_*.py` files
call routes unauthenticated and would 401. Each file builds its client in
exactly one place — a `_client(tmp_path, monkeypatch)` helper calling
`TestClient(api_main.app)` — so the fix is 5 helpers seeding an admin and
logging in, not 71 call sites.

**Baselines.** `CLAUDE.md` pins exact counts (873 workstation / 866 CI) and
warns that editing the two rows independently is how they drift. These counts
will move. Measure the workstation row on a real run and derive the CI row by
the documented `−4 −3` rule; do not guess either.

## 10. Out of scope

- **Login rate-limiting and account lockout.** Real protection, deliberately
  deferred: it needs failed-attempt state and an unlock path, and this
  deployment is a single container on a client's network rather than an
  internet-facing service. Tracked as `BUG-013` so the omission is recorded
  rather than silent.
- **Invite links, password reset by email, MFA, audit log of grant changes.**
  None is needed by a single buyer org with a handful of staff. Each would be
  its own spec.
- **Per-grant permission levels** (viewer vs editor). D4 settles on one grant
  level. Revisit only if a client asks.
