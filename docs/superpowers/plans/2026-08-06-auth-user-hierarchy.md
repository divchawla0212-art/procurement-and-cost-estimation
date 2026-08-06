# Authentication and User Hierarchy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an admin the only principal who sees every project, and every other
user see exactly the projects an admin has granted them — decided on the server,
per request, against an authenticated identity.

**Architecture:** A new `api/auth/` package owns identity end to end: `<ROOT>/auth.json`
behind a single locked read-modify-write primitive, `scrypt` password hashing,
opaque server-side session tokens in an `HttpOnly` cookie, one fail-closed HTTP
middleware that authenticates every `/api/` path outside a named allowlist, and two
explicit FastAPI dependencies (`require_admin`, `require_project_access`) that
authorize per route. The SPA's mock provider is rewritten to call these routes; it
makes no authorization decision of its own.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, `hashlib.scrypt` / `secrets` /
`hmac` from the standard library, pytest + `TestClient`, React 19 + TypeScript.

Spec: [`docs/superpowers/specs/2026-08-06-auth-user-hierarchy-design.md`](../specs/2026-08-06-auth-user-hierarchy-design.md)
Closes: `BUG-011` (S1), `BUG-012` (S2).

## Global Constraints

- **No new runtime dependencies.** Spec D2. Standard library only for hashing,
  tokens and comparison. Do not add `passlib`, `bcrypt`, `python-jose` or `PyJWT`.
- **Python floor 3.12**, matching `requires-python` in `pyproject.toml`.
- **Every test is key-free.** No test may require `ANTHROPIC_API_KEY` or any
  provider secret. Follow `tests/test_api_setup.py:10-15` for client construction.
- **`<ROOT>/auth.json` is written only by `api/auth/store.py`.** No other module
  reads or writes that path.
- **Every write to `auth.json` is a read-modify-write inside the module lock**,
  persisted with `layout.atomic_write_json`.
- **Password minimum length is 8** (raised from PR #7's `MIN_PASSWORD = 6`).
- **Cookie name is `te_session`**; `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age`
  604800, `Secure` iff `request.url.scheme == "https"`.
- **`PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/api/auth/signup"}`** —
  exactly these three. Adding a fourth is a reviewable decision, not a detail.
- **403, never 404**, for an authenticated caller reaching an unentitled slug.

---

## How this plan applies `PLAN-TEMPLATE.md`

`CLAUDE.md` requires reading [`PLAN-TEMPLATE.md`](../PLAN-TEMPLATE.md) before writing
a plan. Its three rules are applied here, but **two of them need translation, and
pretending otherwise would produce a decorative checklist.**

**Rule 1 (every task names the store invariant it owns) — applied as written**, with
one extension. The template requires an invariant to be "a sentence about stored
state… if it can be checked without loading a snapshot, it is not a store
invariant." Two of the most important properties in this work — *who may see which
projects*, and *which paths require a session* — are properties of a **response**,
not of stored state. Rather than bend them into store-invariant shape and weaken
both, this plan declares a second, clearly separate category: **authorization
invariants**. Every task owns one invariant from one category or the other.

**Rule 2 (integration task carries a two-run mutation matrix) — applied, with a
different set of rows.** The template's nine required rows are all extraction
lifecycle: superseded revisions, prompt-version bumps, LLM calls failing on run 1.
This work has no LLM call, no document, and no extraction cache, so those nine rows
are untestable here — a row defending nothing is exactly the decoration the
template forbids. What transfers is the *principle*: per-task TDD produces
single-run, single-module tests, and defects that need two runs or two modules
survive it. Task 9 carries a matrix of that shape built from auth's own two-run
hazards — a session outliving its user, a grant revoked mid-session, a role
changed between requests, a restart between requests.

**Rule 3 (reference code is intent, not paste-able) — applied as written.** The
banner appears above the first reference block, and every block says which parts
are load-bearing.

### Invariant ownership

No invariant is claimed twice; every one has a defending row in Task 9's or Task
12's matrix.

| id | category | invariant | owned by |
|---|---|---|---|
| **S1** | store | `auth.json.users` holds exactly one record per registered account, and no two records share a normalized email. | Task 1 |
| **S2** | store | `auth.json` contains no plaintext password and no plaintext session token — only `scrypt` digests and SHA-256 token digests. | Task 2 |
| **S3** | store | `auth.json.sessions` holds exactly the unexpired sessions of users that still exist. | Task 3 |
| **S4** | store | `auth.json.grants` holds exactly the grants whose user still exists. | Task 10 |
| **A1** | authorization | Every `/api/` path outside `PUBLIC_PATHS` requires a valid session. | Task 6 |
| **A2** | authorization | `GET /api/projects` returns exactly the slugs the caller is entitled to — all of them for an admin, exactly the granted ones for a reviewer. | Task 7 |
| **A3** | authorization | Only an admin reaches the four ingestion-class writes and project creation. | Task 7 |

---

## File structure

| file | responsibility |
|---|---|
| `api/auth/__init__.py` | package marker; re-exports `User`, `require_admin`, `require_project_access` |
| `api/auth/models.py` | `User`, `Grant`, `Session` Pydantic models — no I/O |
| `api/auth/passwords.py` | `hash_password` / `verify_password` and nothing else |
| `api/auth/store.py` | the only reader and writer of `<ROOT>/auth.json`; lock, RMW, user/session/grant operations |
| `api/auth/middleware.py` | `PUBLIC_PATHS`, the fail-closed authentication middleware |
| `api/auth/deps.py` | `require_admin`, `require_project_access`, `current_user` |
| `api/auth/routes.py` | the five `/api/auth/*` routes |
| `api/auth/bootstrap.py` | seed the first admin from env, or stay inert |
| `api/admin_routes.py` | phase 2: user and grant administration routes |
| `api/main.py` | modified: register middleware and routers, apply guards, filter `list_projects` |
| `web/src/auth/AuthProvider.tsx` | modified: real API calls, no `localStorage` accounts |
| `web/src/auth/context.ts` | modified: `User` gains `id`/`role`; `login`/`signup` return promises |
| `web/src/pages/Admin.tsx` | phase 2: admin-only user and grant management |

Splitting `store.py` from `passwords.py` from `deps.py` is deliberate: a reviewer
can reject the hashing parameters while approving the storage layout, and each file
stays small enough to hold in context whole.

---

# Phase 1 — Slice 1: close BUG-011

End state: an admin sees and does everything; a self-registered reviewer signs in
and sees an empty project list. Shippable and distributable on its own.

---

### Task 1: Auth store — file layout and locked read-modify-write

**Files:**
- Create: `api/auth/__init__.py`, `api/auth/models.py`, `api/auth/store.py`
- Test: `tests/test_auth_store.py`

**Interfaces:**
- Consumes: `procurement.store.layout.atomic_write_json`, `layout.read_json`
- Produces:
  - `auth_path(root: str) -> str`
  - `read_auth(root: str) -> dict`
  - `locked_update(root: str) -> ContextManager[dict]` — yields the mutable
    document, writes it atomically on clean exit, writes nothing on exception
  - `create_user(root: str, email: str, password_hash: str, role: str) -> User`
  - `find_by_email(root: str, email: str) -> User | None`
  - `find_by_id(root: str, user_id: str) -> User | None`
  - `delete_user(root: str, user_id: str) -> None`
  - `normalize_email(email: str) -> str`
- **Store invariant owned (S1):** `auth.json.users` holds exactly one record per
  registered account, and no two records share a normalized email.

Two things about this task carry more weight than they look.

**The lock is load-bearing, not hygiene.** `layout.atomic_write_json` builds its temp
file as `path + ".tmp"` — a *shared* name (`procurement/store/layout.py:61`). Two
concurrent writers of `auth.json` would collide on that temp file, which is the
defect `BUG-008` records for the snapshot writer. Serializing every write of this
path through one module-level lock is what makes the shared temp name safe here.

**The lock is `threading.Lock`, and that is a documented single-process
assumption.** The shipped deployment is one `uvicorn` process
(`.claude/launch.json`, `docker-compose.yml` — no `--workers`). If that ever
changes, this needs a real cross-process file lock, and the docstring must say so
rather than leaving a future reader to discover it.

Email normalization is `email.strip().lower()`, matching PR #7's `normalizeEmail`
(`web/src/auth/AuthProvider.tsx:38-40`) so existing accounts keep the same identity
rule.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_store.py
"""Key-free tests for the auth.json store primitive."""

import json
import os

import pytest

from api.auth import store


def test_read_auth_on_missing_file_returns_empty_document(tmp_path):
    doc = store.read_auth(str(tmp_path))
    assert doc == {"version": 1, "users": [], "grants": [], "sessions": []}


def test_locked_update_persists_and_is_atomic(tmp_path):
    root = str(tmp_path)
    with store.locked_update(root) as doc:
        doc["users"].append({"id": "u_1", "email": "a@b.com"})
    on_disk = json.loads(open(store.auth_path(root), encoding="utf-8").read())
    assert on_disk["users"][0]["email"] == "a@b.com"
    assert not os.path.exists(store.auth_path(root) + ".tmp")


def test_locked_update_writes_nothing_when_body_raises(tmp_path):
    root = str(tmp_path)
    store.create_user(root, "a@b.com", "hash", "admin")
    with pytest.raises(RuntimeError):
        with store.locked_update(root) as doc:
            doc["users"].append({"id": "u_2", "email": "b@c.com"})
            raise RuntimeError("boom")
    assert len(store.read_auth(root)["users"]) == 1


def test_create_user_normalizes_email_and_assigns_id(tmp_path):
    root = str(tmp_path)
    user = store.create_user(root, "  Mixed.Case@Client.COM ", "hash", "reviewer")
    assert user.email == "mixed.case@client.com"
    assert user.id.startswith("u_")
    assert store.find_by_email(root, "MIXED.CASE@client.com").id == user.id


def test_create_user_rejects_duplicate_email(tmp_path):
    """S1: exactly one record per account — a second signup cannot shadow the first."""
    root = str(tmp_path)
    store.create_user(root, "a@b.com", "hash", "reviewer")
    with pytest.raises(store.EmailTaken):
        store.create_user(root, "A@B.com", "other-hash", "reviewer")
    assert len(store.read_auth(root)["users"]) == 1


def test_delete_user_removes_exactly_that_user(tmp_path):
    """S1: 'exactly', not 'includes' — the other account must survive untouched."""
    root = str(tmp_path)
    keep = store.create_user(root, "keep@b.com", "h", "reviewer")
    drop = store.create_user(root, "drop@b.com", "h", "reviewer")
    store.delete_user(root, drop.id)
    remaining = store.read_auth(root)["users"]
    assert [u["id"] for u in remaining] == [keep.id]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_auth_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.auth'`

- [ ] **Step 3: Write minimal implementation**

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the implementation
> against the test.
>
> **Load-bearing:** the lock wrapping read-modify-write as one unit; writing only on
> clean exit; the `EmailTaken` check happening *inside* the lock; ids that are
> opaque and stable. **Illustrative:** the id prefix and width, the exception name.

```python
# api/auth/models.py
from pydantic import BaseModel


class User(BaseModel):
    id: str
    email: str
    role: str            # "admin" | "reviewer"
    created_at: str
```

```python
# api/auth/store.py
import os
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from procurement.store import layout
from api.auth.models import User

# One process, one lock. The deployment runs a single uvicorn worker
# (docker-compose.yml, .claude/launch.json). If that ever becomes multiple
# workers or multiple containers over a shared volume, this must become a
# cross-process file lock: layout.atomic_write_json() builds its temp file as
# path + ".tmp", a shared name, so two concurrent writers of auth.json would
# collide on it. Serialising every write here is what makes that safe.
_LOCK = threading.Lock()

_EMPTY = {"version": 1, "users": [], "grants": [], "sessions": []}


class EmailTaken(Exception):
    """A user with this normalized email already exists."""


def auth_path(root: str) -> str:
    return os.path.join(root, "auth.json")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def read_auth(root: str) -> dict:
    doc = layout.read_json(auth_path(root), default=None)
    if not isinstance(doc, dict):
        return {k: (list(v) if isinstance(v, list) else v) for k, v in _EMPTY.items()}
    for key, empty in _EMPTY.items():
        doc.setdefault(key, empty if not isinstance(empty, list) else [])
    return doc


@contextmanager
def locked_update(root: str):
    """Read, mutate, write — as one unit, with no other writer interleaving.

    The write happens only on a clean exit, so a caller that raises leaves the
    previous document exactly as it was.
    """
    with _LOCK:
        doc = read_auth(root)
        yield doc
        layout.atomic_write_json(auth_path(root), doc)


def create_user(root: str, email: str, password_hash: str, role: str) -> User:
    normalized = normalize_email(email)
    with locked_update(root) as doc:
        # Inside the lock: a check outside it races another signup.
        if any(u["email"] == normalized for u in doc["users"]):
            raise EmailTaken(normalized)
        user = User(
            id="u_" + secrets.token_hex(6),
            email=normalized,
            role=role,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        doc["users"].append({**user.model_dump(), "password_hash": password_hash})
        return user


def delete_user(root: str, user_id: str) -> None:
    """Remove the user and everything that points at them, in one write."""
    with locked_update(root) as doc:
        doc["users"] = [u for u in doc["users"] if u["id"] != user_id]
        doc["grants"] = [g for g in doc["grants"] if g["user_id"] != user_id]
        doc["sessions"] = [s for s in doc["sessions"] if s["user_id"] != user_id]
```

`find_by_email` and `find_by_id` return `User | None` and must **not** include
`password_hash` in what they return — that field is read only by
`api/auth/routes.py` through a dedicated `_password_hash_for(root, user_id)` helper,
so a stray `User` in a response body can never carry it.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_auth_store.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add api/auth/__init__.py api/auth/models.py api/auth/store.py tests/test_auth_store.py
git commit -m "feat(auth): add the auth.json store with locked read-modify-write"
```

---

### Task 2: Password hashing

**Files:**
- Create: `api/auth/passwords.py`
- Test: `tests/test_auth_passwords.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces:
  - `hash_password(password: str) -> str` — returns `scrypt$n$r$p$salt_b64$hash_b64`
  - `verify_password(password: str, stored: str) -> bool`
  - `DUMMY_HASH: str` — a module constant for equal-time rejection of unknown emails
- **Store invariant owned (S2):** `auth.json` contains no plaintext password and no
  plaintext session token — only `scrypt` digests and SHA-256 token digests.

The parameters `n=16384, r=8, p=1` need 128·n·r = 16 MiB, which sits under OpenSSL's
32 MiB default `maxmem`, so `hashlib.scrypt` accepts them without passing `maxmem`.
Do not raise `n` without checking that ceiling — exceeding it raises at runtime, and
only on the login path.

`verify_password` reads the cost parameters back out of the stored string rather than
using the module constants. That is what lets the parameters be raised later without
invalidating every existing password.

`DUMMY_HASH` exists so that login spends comparable time on a known and an unknown
email. Without it, response timing tells an attacker which addresses are registered.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_passwords.py
from api.auth import passwords


def test_hash_is_self_describing_and_verifies():
    stored = passwords.hash_password("correct horse battery")
    assert stored.startswith("scrypt$16384$8$1$")
    assert passwords.verify_password("correct horse battery", stored) is True


def test_wrong_password_fails():
    stored = passwords.hash_password("right")
    assert passwords.verify_password("wrong", stored) is False


def test_same_password_hashes_differently():
    """Distinct salts — two users with the same password must not share a digest."""
    assert passwords.hash_password("same") != passwords.hash_password("same")


def test_plaintext_never_appears_in_the_stored_form():
    """S2: the digest must not carry the password it was made from."""
    assert "hunter2" not in passwords.hash_password("hunter2")


def test_verify_rejects_malformed_stored_values_without_raising():
    for bad in ["", "not-a-hash", "scrypt$1$2$3", "bcrypt$a$b$c$d$e", "scrypt$x$8$1$aa$bb"]:
        assert passwords.verify_password("anything", bad) is False


def test_verify_reads_cost_parameters_from_the_stored_string():
    """A digest made at lower cost still verifies — this is what allows raising n later."""
    cheap = passwords.hash_password("pw", n=1024)
    assert cheap.startswith("scrypt$1024$")
    assert passwords.verify_password("pw", cheap) is True


def test_dummy_hash_is_a_valid_digest_that_matches_nothing_useful():
    assert passwords.verify_password("", passwords.DUMMY_HASH) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_auth_passwords.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.auth.passwords'`

- [ ] **Step 3: Write minimal implementation**

> **Load-bearing:** reading cost parameters back from the stored string;
> `hmac.compare_digest` for the comparison; a fresh random salt per call; returning
> `False` rather than raising on any malformed input. **Illustrative:** the `$`
> delimiter and the base64 flavour.

```python
# api/auth/passwords.py
import base64
import hashlib
import hmac
import secrets

_N, _R, _P = 16384, 8, 1     # 128 * n * r = 16 MiB, under OpenSSL's 32 MiB maxmem
_SALT_BYTES = 16
_DKLEN = 32


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str, *, n: int = _N, r: int = _R, p: int = _P) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_DKLEN)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    """False on any failure, including a malformed stored value. Never raises."""
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
    except (ValueError, TypeError, MemoryError):
        return False
    return hmac.compare_digest(dk, expected)


# Login verifies against this when the email is unknown, so an unregistered
# address costs the same wall-clock as a registered one. Without it, timing
# enumerates the user list.
DUMMY_HASH = hash_password(secrets.token_urlsafe(32))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_auth_passwords.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
git add api/auth/passwords.py tests/test_auth_passwords.py
git commit -m "feat(auth): hash passwords with scrypt and self-describing parameters"
```

---

### Task 3: Sessions

**Files:**
- Modify: `api/auth/store.py`
- Test: `tests/test_auth_sessions.py`

**Interfaces:**
- Consumes: `store.locked_update`, `store.find_by_id` (Task 1)
- Produces:
  - `create_session(root: str, user_id: str, *, ttl_seconds: int = 604800) -> str`
    — returns the **plaintext** token; only its SHA-256 is stored
  - `resolve_session(root: str, token: str) -> User | None`
  - `delete_session(root: str, token: str) -> None`
  - `delete_sessions_for(root: str, user_id: str, *, keep_token: str | None = None) -> None`
- **Store invariant owned (S3):** `auth.json.sessions` holds exactly the unexpired
  sessions of users that still exist.

"Exactly" is the whole point. Three ways a stale session survives if this is written
as "includes": the user is deleted, the session expires, or the password changes.
Expiry is enforced on read *and* pruned on the next write — enforcing on read alone
leaves the file growing forever, and pruning alone leaves an expired row usable
until something else happens to write.

**A decision the spec did not settle, made here:** changing a password revokes that
user's *other* sessions and keeps the current one. Otherwise a user who changes their
password because they believe it was stolen leaves the thief signed in — which makes
the feature actively misleading. `delete_sessions_for(..., keep_token=...)` exists for
exactly this.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_sessions.py
import time

from api.auth import store


def _user(root, email="a@b.com", role="admin"):
    return store.create_user(root, email, "hash", role)


def test_session_round_trips_and_returns_the_user(tmp_path):
    root = str(tmp_path)
    user = _user(root)
    token = store.create_session(root, user.id)
    assert store.resolve_session(root, token).id == user.id


def test_plaintext_token_is_never_stored(tmp_path):
    """S2/S3: the file must hold only the digest, so a leaked auth.json grants nothing."""
    root = str(tmp_path)
    token = store.create_session(root, _user(root).id)
    raw = open(store.auth_path(root), encoding="utf-8").read()
    assert token not in raw


def test_unknown_and_malformed_tokens_resolve_to_none(tmp_path):
    root = str(tmp_path)
    _user(root)
    for bad in ["", "not-a-token", "u_deadbeef"]:
        assert store.resolve_session(root, bad) is None


def test_expired_session_does_not_resolve(tmp_path):
    root = str(tmp_path)
    token = store.create_session(root, _user(root).id, ttl_seconds=-1)
    assert store.resolve_session(root, token) is None


def test_expired_sessions_are_pruned_on_the_next_write(tmp_path):
    """S3: 'exactly' — an expired row must leave, not merely stop resolving."""
    root = str(tmp_path)
    user = _user(root)
    store.create_session(root, user.id, ttl_seconds=-1)
    store.create_session(root, user.id)
    assert len(store.read_auth(root)["sessions"]) == 1


def test_deleting_a_user_removes_their_sessions(tmp_path):
    """S3: a session must not outlive the account it authenticates."""
    root = str(tmp_path)
    user = _user(root)
    token = store.create_session(root, user.id)
    store.delete_user(root, user.id)
    assert store.resolve_session(root, token) is None
    assert store.read_auth(root)["sessions"] == []


def test_logout_invalidates_only_that_session(tmp_path):
    root = str(tmp_path)
    user = _user(root)
    a, b = store.create_session(root, user.id), store.create_session(root, user.id)
    store.delete_session(root, a)
    assert store.resolve_session(root, a) is None
    assert store.resolve_session(root, b).id == user.id


def test_delete_sessions_for_can_keep_the_current_one(tmp_path):
    """Password change revokes the other sessions and keeps the caller signed in."""
    root = str(tmp_path)
    user = _user(root)
    keep, other = store.create_session(root, user.id), store.create_session(root, user.id)
    store.delete_sessions_for(root, user.id, keep_token=keep)
    assert store.resolve_session(root, keep).id == user.id
    assert store.resolve_session(root, other) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_auth_sessions.py -v`
Expected: FAIL — `AttributeError: module 'api.auth.store' has no attribute 'create_session'`

- [ ] **Step 3: Write minimal implementation**

> **Load-bearing:** storing `sha256(token)` and never the token; checking expiry on
> read; pruning expired rows on every write; `delete_user` already cascading from
> Task 1. **Illustrative:** the token width and the TTL default.

```python
# api/auth/store.py  (additions)
import hashlib
from datetime import datetime, timedelta, timezone

SESSION_TTL_SECONDS = 604800   # 7 days, absolute — no sliding refresh


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _unexpired(rows: list[dict]) -> list[dict]:
    now = _now()
    return [r for r in rows if datetime.fromisoformat(r["expires_at"]) > now]


def create_session(root: str, user_id: str, *, ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
    token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(seconds=ttl_seconds)
    with locked_update(root) as doc:
        # Prune on write: expiry alone would let the file grow without bound.
        doc["sessions"] = _unexpired(doc["sessions"])
        doc["sessions"].append({
            "token_sha256": _token_digest(token),
            "user_id": user_id,
            "expires_at": expires.isoformat(),
        })
    return token


def resolve_session(root: str, token: str) -> User | None:
    if not token:
        return None
    digest = _token_digest(token)
    doc = read_auth(root)
    now = _now()
    for row in doc["sessions"]:
        if not hmac.compare_digest(row["token_sha256"], digest):
            continue
        if datetime.fromisoformat(row["expires_at"]) <= now:
            return None
        return _user_from_row(doc, row["user_id"])
    return None
```

`_user_from_row` returns `None` when the referenced user is gone, so a session row
that somehow outlives its user still fails closed rather than authenticating nobody
into an admin.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_auth_sessions.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add api/auth/store.py tests/test_auth_sessions.py
git commit -m "feat(auth): add opaque server-side sessions with digest-only storage"
```

---

### Task 4: The `/api/auth/*` routes

**Files:**
- Create: `api/auth/routes.py`
- Modify: `api/main.py` (include the router)
- Test: `tests/test_api_auth.py`

**Interfaces:**
- Consumes: `store.create_user`, `store.find_by_email`, `store.create_session`,
  `store.delete_session`, `store.delete_sessions_for`, `passwords.*`
- Produces: `router: APIRouter` mounted at `/api/auth`; `COOKIE_NAME = "te_session"`;
  `set_session_cookie(response, token, request)` / `clear_session_cookie(response)`
- **Store invariant owned:** none — this task composes Tasks 1–3 and owns no new
  stored state. Its correctness properties are asserted as route behaviour.

The five routes are in the spec's section 5 table. Three behaviours are worth stating
because getting them wrong is silent:

**One error string for two causes.** Unknown email and wrong password both return
`Invalid email or password.` — matching PR #7's copy — and both spend a `scrypt` on
the way, the unknown one against `passwords.DUMMY_HASH`. Different strings or
different timings enumerate the user list.

**`Secure` is derived, not configured.** `request.url.scheme == "https"` turns it on
behind TLS and leaves it off for the documented plain-HTTP `docker compose` flow. A
hardcoded `True` breaks local deployment; a hardcoded `False` ships a cookie over
TLS without the flag.

**Signup always creates a `reviewer`.** There is no request field that can set
`role`. An attacker who can POST to signup must not be able to POST themselves an
admin — so the role is not an input at all.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_auth.py
"""Key-free tests for the auth routes."""

from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return TestClient(api_main.app)


def test_signup_then_me_returns_a_reviewer(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    res = client.post("/api/auth/signup", json={"email": "R@Client.com", "password": "longenough"})
    assert res.status_code == 201
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "r@client.com"
    assert me.json()["role"] == "reviewer"


def test_signup_cannot_choose_its_own_role(tmp_path, monkeypatch):
    """Privilege escalation: role is not an input, so a crafted body cannot set it."""
    client = _client(tmp_path, monkeypatch)
    client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough", "role": "admin"})
    assert client.get("/api/auth/me").json()["role"] == "reviewer"


def test_signup_rejects_short_passwords(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    res = client.post("/api/auth/signup", json={"email": "a@b.com", "password": "short"})
    assert res.status_code == 422


def test_signup_rejects_a_duplicate_email(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough"})
    res = client.post("/api/auth/signup", json={"email": "A@B.com", "password": "different"})
    assert res.status_code == 409


def test_signup_is_refused_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOW_SIGNUP", "0")
    client = _client(tmp_path, monkeypatch)
    res = client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough"})
    assert res.status_code == 403


def test_login_sets_an_httponly_cookie(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough"})
    client.cookies.clear()
    res = client.post("/api/auth/login", json={"email": "a@b.com", "password": "longenough"})
    assert res.status_code == 200
    cookie = res.headers["set-cookie"]
    assert "te_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_wrong_password_and_unknown_email_give_the_same_answer(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough"})
    wrong = client.post("/api/auth/login", json={"email": "a@b.com", "password": "wrongpass"})
    unknown = client.post("/api/auth/login", json={"email": "nobody@b.com", "password": "wrongpass"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"] == "Invalid email or password."


def test_me_without_a_session_is_401(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/auth/me").status_code == 401


def test_logout_invalidates_the_session(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough"})
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_password_change_keeps_this_session_and_drops_the_others(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough"})
    other = TestClient(client.app)
    other.post("/api/auth/login", json={"email": "a@b.com", "password": "longenough"})
    res = client.post("/api/auth/password", json={"current": "longenough", "next": "newlongenough"})
    assert res.status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    assert other.get("/api/auth/me").status_code == 401


def test_no_response_ever_carries_a_password_hash(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough"})
    for res in [client.get("/api/auth/me"),
                client.post("/api/auth/login", json={"email": "a@b.com", "password": "longenough"})]:
        assert "password_hash" not in res.text
        assert "scrypt$" not in res.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_auth.py -v`
Expected: FAIL — 404 on every `/api/auth/*` path, since the router does not exist

- [ ] **Step 3: Write minimal implementation**

> **Load-bearing:** the identical error string and the `DUMMY_HASH` verify on the
> unknown-email path; `role` absent from the signup request model; deriving `Secure`
> from the request scheme; revoking sibling sessions on password change.
> **Illustrative:** status codes for duplicate signup, the request model names.

```python
# api/auth/routes.py
import os

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from api.auth import passwords, store

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "te_session"
MIN_PASSWORD = 8


class Credentials(BaseModel):
    # `str`, not pydantic's EmailStr: EmailStr needs the email-validator package,
    # which is NOT installed here (verified) and which Global Constraints forbid
    # adding. A shape check is all this needs — the address is an identifier, and
    # nothing in this system mails it.
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=MIN_PASSWORD)
    # Note the absence of `role`. Signup cannot elect its own privilege.

    @field_validator("email")
    @classmethod
    def _looks_like_an_address(cls, v: str) -> str:
        local, _, domain = v.strip().partition("@")
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("not an email address")
        return v


def set_session_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=store.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


@router.post("/login")
def login(body: Credentials, request: Request, response: Response) -> dict:
    user = store.find_by_email(store_root(request), body.email)
    stored = store.password_hash_for(store_root(request), user.id) if user else passwords.DUMMY_HASH
    # Always verify, even with no such user: equal work means equal timing.
    if not passwords.verify_password(body.password, stored) or user is None:
        raise HTTPException(401, "Invalid email or password.")
    set_session_cookie(response, store.create_session(store_root(request), user.id), request)
    return user.model_dump()
```

`store_root(request)` reads `api.main.ROOT` at call time rather than importing it at
module load, so the tests' `monkeypatch.setattr(api_main, "ROOT", ...)` takes effect.
Importing the value directly would bind the pre-patch path and every test would write
to the real `projects/`.

**Verified before writing this plan:** `from pydantic import EmailStr` raises
`ImportError: email-validator is not installed` in this environment, so `EmailStr`
is not an option — hence the hand-rolled validator above. Do not "fix" it by
installing the extra; Global Constraints forbid new runtime dependencies, and an
address that nothing ever mails does not need RFC-grade validation.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_api_auth.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Commit**

```bash
git add api/auth/routes.py api/main.py tests/test_api_auth.py
git commit -m "feat(auth): add signup, login, logout, me and password-change routes"
```

---

### Task 5: Admin bootstrap

**Files:**
- Create: `api/auth/bootstrap.py`
- Modify: `api/main.py` (call on startup)
- Test: `tests/test_auth_bootstrap.py`

**Interfaces:**
- Consumes: `store.read_auth`, `store.create_user`, `passwords.hash_password`
- Produces: `seed_admin_if_empty(root: str) -> User | None`
- **Store invariant owned:** none new — asserts S1 holds across restarts (seeding
  twice must not create a second admin).

The spec's deliberate failure mode: with no `ADMIN_EMAIL`/`ADMIN_PASSWORD` and no
users, **nothing is seeded**. The app starts, signup works, every account is an
ungranted reviewer, and no project is reachable until an operator sets the vars and
restarts. Startup logs a warning naming both.

The rejected alternative — first account to register becomes admin — is BUG-004's
shape: a silent default doing the consequential thing.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_bootstrap.py
from api.auth import bootstrap, store


def test_seeds_one_admin_when_env_is_set_and_store_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "Boss@Client.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "bootstrappw")
    user = bootstrap.seed_admin_if_empty(str(tmp_path))
    assert user.role == "admin"
    assert user.email == "boss@client.com"


def test_seeding_twice_does_not_create_a_second_admin(tmp_path, monkeypatch):
    """S1 across restarts: startup runs on every boot, not just the first."""
    monkeypatch.setenv("ADMIN_EMAIL", "boss@client.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "bootstrappw")
    bootstrap.seed_admin_if_empty(str(tmp_path))
    assert bootstrap.seed_admin_if_empty(str(tmp_path)) is None
    assert len(store.read_auth(str(tmp_path))["users"]) == 1


def test_seeds_nothing_without_env_and_logs_a_warning(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    assert bootstrap.seed_admin_if_empty(str(tmp_path)) is None
    assert store.read_auth(str(tmp_path))["users"] == []
    assert "ADMIN_EMAIL" in caplog.text and "ADMIN_PASSWORD" in caplog.text


def test_does_not_seed_when_users_already_exist(tmp_path, monkeypatch):
    """A populated store must not gain a surprise admin from a stale env var."""
    store.create_user(str(tmp_path), "someone@b.com", "h", "reviewer")
    monkeypatch.setenv("ADMIN_EMAIL", "boss@client.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "bootstrappw")
    assert bootstrap.seed_admin_if_empty(str(tmp_path)) is None
    assert [u["role"] for u in store.read_auth(str(tmp_path))["users"]] == ["reviewer"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_auth_bootstrap.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.auth.bootstrap'`

- [ ] **Step 3: Write minimal implementation**

> **Load-bearing:** seeding only when the store has **no users at all** (not "no
> admin"); returning `None` and warning rather than inventing a default.
> **Illustrative:** the log message wording.

```python
# api/auth/bootstrap.py
import logging
import os

from api.auth import passwords, store
from api.auth.models import User

log = logging.getLogger(__name__)


def seed_admin_if_empty(root: str) -> User | None:
    if store.read_auth(root)["users"]:
        return None
    email = os.getenv("ADMIN_EMAIL")
    password = os.getenv("ADMIN_PASSWORD")
    if not email or not password:
        log.warning(
            "No users exist and ADMIN_EMAIL / ADMIN_PASSWORD are unset. "
            "No administrator will be created, so no project is reachable by "
            "anyone. Set both and restart."
        )
        return None
    return store.create_user(root, email, passwords.hash_password(password), "admin")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_auth_bootstrap.py -v`
Expected: PASS, 4 tests

- [ ] **Step 5: Commit**

```bash
git add api/auth/bootstrap.py api/main.py tests/test_auth_bootstrap.py
git commit -m "feat(auth): seed the first admin from env, or stay inert"
```

---

### Task 6: Fail-closed authentication middleware

**Files:**
- Create: `api/auth/middleware.py`
- Modify: `api/main.py`, and the five `_client` helpers in `tests/test_api_*.py`
- Test: `tests/test_auth_middleware.py`

**Interfaces:**
- Consumes: `store.resolve_session`, `routes.COOKIE_NAME`
- Produces: `PUBLIC_PATHS: set[str]`; `install(app)` registering the middleware;
  `request.state.user` populated on every authenticated request
- **Authorization invariant owned (A1):** every `/api/` path outside `PUBLIC_PATHS`
  requires a valid session.

This is the task that actually closes the S1, and it has two traps.

**Trap 1 — middleware order versus CORS.** Starlette's `add_middleware` inserts at
position 0 and the stack is built by wrapping in reverse, so **the last-registered
middleware is the outermost**. If the auth middleware is registered after
`CORSMiddleware` (`api/main.py:74`), auth runs first and a 401 leaves without CORS
headers — a browser on a different origin cannot even read the status. Register auth
**before** the `add_middleware(CORSMiddleware, ...)` call so CORS stays outermost.

**Trap 2 — preflight.** A CORS preflight is an `OPTIONS` request carrying no cookies.
If the middleware 401s it, cross-origin calls fail entirely and the failure looks
like a CORS misconfiguration rather than an auth one. `OPTIONS` passes through.

**Non-`/api` paths pass through**, so the SPA bundle and the login screen stay
servable by the `StaticFiles` mount at `/` (`api/main.py:437`).

**Blast radius:** all 49 existing tests in the five `tests/test_api_*.py` files call
routes unauthenticated and will 401. Each file builds its client in exactly one
helper, so this task also updates those five helpers to seed an admin and log in.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_middleware.py
from fastapi.testclient import TestClient

from api.auth.middleware import PUBLIC_PATHS


def _anon_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return TestClient(api_main.app)


def test_every_api_route_outside_the_allowlist_requires_a_session(tmp_path, monkeypatch):
    """A1, and the guard that outlives us: a route added later is covered by this test.

    Iterating app.routes means nobody has to remember to protect route 17.
    """
    client = _anon_client(tmp_path, monkeypatch)
    import api.main as api_main

    checked = 0
    for route in api_main.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path in PUBLIC_PATHS:
            continue
        for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
            probe = path.replace("{slug}", "any").replace("{vendor}", "any")
            res = client.request(method, probe)
            assert res.status_code == 401, f"{method} {path} answered {res.status_code}, not 401"
            checked += 1
    assert checked > 0, "the route sweep matched nothing — the probe is broken, not passing"


def test_allowlisted_paths_stay_reachable_without_a_session(tmp_path, monkeypatch):
    client = _anon_client(tmp_path, monkeypatch)
    assert client.get("/api/health").status_code == 200


def test_preflight_is_not_challenged(tmp_path, monkeypatch):
    """Trap 2: an OPTIONS preflight carries no cookie and must not 401."""
    client = _anon_client(tmp_path, monkeypatch)
    res = client.options(
        "/api/projects",
        headers={"Origin": "http://localhost:5173",
                 "Access-Control-Request-Method": "GET"},
    )
    assert res.status_code < 400


def test_a_401_still_carries_cors_headers(tmp_path, monkeypatch):
    """Trap 1: if auth is registered outside CORS, this header goes missing."""
    client = _anon_client(tmp_path, monkeypatch)
    res = client.get("/api/projects", headers={"Origin": "http://localhost:5173"})
    assert res.status_code == 401
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_non_api_paths_are_not_challenged(tmp_path, monkeypatch):
    client = _anon_client(tmp_path, monkeypatch)
    assert client.get("/definitely-not-an-api-path").status_code != 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_auth_middleware.py -v`
Expected: FAIL — `ModuleNotFoundError`, and once the module exists, the sweep fails
naming the first unguarded route

- [ ] **Step 3: Write minimal implementation**

> **Load-bearing:** the prefix test plus a named allowlist (not a per-route
> decorator); letting `OPTIONS` through; registering before CORS. **Illustrative:**
> the response body wording.

```python
# api/auth/middleware.py
from fastapi.responses import JSONResponse

from api.auth import store
from api.auth.routes import COOKIE_NAME

# Exactly three. Adding a fourth is a reviewable decision, not a detail.
PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/api/auth/signup"}


def install(app) -> None:
    """Register the authentication middleware.

    MUST be called before add_middleware(CORSMiddleware, ...). Starlette inserts
    each middleware at position 0 and wraps in reverse, so the last registered is
    the outermost. If auth were outermost, a 401 would skip CORS entirely and a
    cross-origin caller could not read the status.
    """

    @app.middleware("http")
    async def authenticate(request, call_next):
        path = request.url.path
        if not path.startswith("/api/") or path in PUBLIC_PATHS:
            return await call_next(request)
        if request.method == "OPTIONS":       # CORS preflight carries no cookie
            return await call_next(request)

        import api.main as api_main           # late import: ROOT is monkeypatched in tests
        user = store.resolve_session(api_main.ROOT, request.cookies.get(COOKIE_NAME, ""))
        if user is None:
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        request.state.user = user
        return await call_next(request)
```

- [ ] **Step 4: Update the five existing test client helpers**

Each of `tests/test_api_compliance.py:38`, `test_api_export.py:41`,
`test_api_extraction_status.py` (~:52), `test_api_feedback.py:20`,
`test_api_setup.py:10` ends with `return TestClient(api_main.app)`. Replace that
line in each with an admin seed and login, leaving every other line untouched:

```python
    client = TestClient(api_main.app)
    client.post("/api/auth/signup", json={"email": "admin@test.local", "password": "testpassword"})
    # The first signup on an empty store would be a reviewer; promote directly in
    # the store, which is the only place role is settable.
    from api.auth import store as auth_store
    with auth_store.locked_update(str(tmp_path)) as doc:
        doc["users"][0]["role"] = "admin"
    return client
```

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS — the 49 pre-existing API tests assert exactly what they asserted
before, now over an authenticated client

- [ ] **Step 6: Commit**

```bash
git add api/auth/middleware.py api/main.py tests/
git commit -m "feat(auth): authenticate every /api path by default, fail closed"
```

---

### Task 7: Authorization — dependencies and the route matrix

**Files:**
- Create: `api/auth/deps.py`
- Modify: `api/main.py` (all 16 routes; filter `list_projects`)
- Test: `tests/test_api_authorization.py`

**Interfaces:**
- Consumes: `request.state.user` (Task 6), `store.granted_slugs`
- Produces:
  - `current_user(request: Request) -> User`
  - `require_admin(request: Request) -> User` — 403 for a reviewer
  - `require_project_access(slug: str, request: Request) -> User` — admin passes;
    reviewer passes only with a grant
  - `store.granted_slugs(root: str, user_id: str) -> set[str]`
- **Authorization invariants owned (A2, A3):** `GET /api/projects` returns exactly
  the slugs the caller is entitled to; only an admin reaches the four
  ingestion-class writes and project creation.

`granted_slugs` returns an empty set in phase 1 — no grant can exist until Task 10 —
which is precisely the intended end state for slice 1: a reviewer signs in and sees
an empty list. Do not stub it to return everything "until phase 2"; that would ship
the bug this plan exists to close.

**A2 is the one that decides whether this is real.** If `GET /api/projects` merely
required a session, every reviewer would still see every tender and BUG-011 would
survive its own fix.

Route guards follow the spec's section 6.2 table exactly.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_authorization.py
from fastapi.testclient import TestClient

from api.auth import store as auth_store

ADMIN_ONLY = [
    ("POST", "/api/projects"),
    ("POST", "/api/projects/{slug}/requirements"),
    ("POST", "/api/projects/{slug}/vendors"),
    ("PUT", "/api/projects/{slug}/fx-rates"),
    ("POST", "/api/projects/{slug}/ingest"),
]


def _clients(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))

    admin = TestClient(api_main.app)
    admin.post("/api/auth/signup", json={"email": "admin@t.local", "password": "testpassword"})
    with auth_store.locked_update(str(tmp_path)) as doc:
        doc["users"][0]["role"] = "admin"

    reviewer = TestClient(api_main.app)
    reviewer.post("/api/auth/signup", json={"email": "rev@t.local", "password": "testpassword"})
    return admin, reviewer


def test_admin_sees_every_project_and_reviewer_sees_none(tmp_path, monkeypatch):
    """A2: an ungranted reviewer's list is empty — not filtered-but-populated."""
    admin, reviewer = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    admin.post("/api/projects", json={"name": "Tender Two"})
    assert {p["slug"] for p in admin.get("/api/projects").json()} == {"tender-one", "tender-two"}
    assert reviewer.get("/api/projects").json() == []


def test_reviewer_gets_403_not_404_on_an_unentitled_slug(tmp_path, monkeypatch):
    """403 is deliberate: 404 would make a typo indistinguishable from a refusal."""
    admin, reviewer = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    assert reviewer.get("/api/projects/tender-one/summary").status_code == 403


def test_every_admin_only_route_refuses_a_reviewer(tmp_path, monkeypatch):
    """A3: the ingestion-class writes move every number on a tender."""
    admin, reviewer = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    for method, template in ADMIN_ONLY:
        path = template.replace("{slug}", "tender-one")
        assert reviewer.request(method, path).status_code == 403, f"{method} {path} let a reviewer through"


def test_admin_reaches_the_admin_only_routes(tmp_path, monkeypatch):
    """The guard must not be so tight it locks the admin out too."""
    admin, _ = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    assert admin.put("/api/projects/tender-one/fx-rates", json={"rates": {}}).status_code < 400


def test_a_granted_reviewer_reads_and_may_record_feedback(tmp_path, monkeypatch):
    """D4: reviewers keep the review-write; grants are honoured the moment they exist."""
    admin, reviewer = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    rev = auth_store.find_by_email(str(tmp_path), "rev@t.local")
    with auth_store.locked_update(str(tmp_path)) as doc:
        doc["grants"].append({"user_id": rev.id, "slug": "tender-one",
                              "granted_at": "2026-08-06T00:00:00+00:00", "granted_by": "u_admin"})
    assert [p["slug"] for p in reviewer.get("/api/projects").json()] == ["tender-one"]
    assert reviewer.get("/api/projects/tender-one/summary").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_authorization.py -v`
Expected: FAIL — the reviewer sees both projects and reaches every route

- [ ] **Step 3: Write minimal implementation**

> **Load-bearing:** `require_project_access` taking `slug` as a path parameter so
> FastAPI binds it; admin short-circuiting before any grant lookup; `list_projects`
> filtering rather than merely authenticating. **Illustrative:** the detail strings.

```python
# api/auth/deps.py
from fastapi import HTTPException, Request

from api.auth.models import User


def current_user(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if user is None:
        # Only reachable if a route outside PUBLIC_PATHS bypassed the middleware.
        raise HTTPException(401, "authentication required")
    return user


def require_admin(request: Request) -> User:
    user = current_user(request)
    if user.role != "admin":
        raise HTTPException(403, "administrator access required")
    return user


def require_project_access(slug: str, request: Request) -> User:
    """403, never 404 — see spec 6.2."""
    import api.main as api_main
    from api.auth import store

    user = current_user(request)
    if user.role == "admin":
        return user
    if slug not in store.granted_slugs(api_main.ROOT, user.id):
        raise HTTPException(403, "no access to this project")
    return user
```

```python
# api/main.py  — the filtering read, replacing lines 113-115
@app.get("/api/projects")
def list_projects(user: User = Depends(current_user)) -> list[dict]:
    visible = None if user.role == "admin" else auth_store.granted_slugs(ROOT, user.id)
    return [
        _project_summary(p)
        for p in proj.list_projects(ROOT)
        if visible is None or p.slug in visible
    ]
```

Every `{slug}` read and the feedback route gains `_: User = Depends(require_project_access)`;
`POST /api/projects` and the four ingestion-class writes gain `_: User = Depends(require_admin)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_api_authorization.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS — no regression in the 49 pre-existing API tests

- [ ] **Step 6: Commit**

```bash
git add api/auth/deps.py api/main.py tests/test_api_authorization.py
git commit -m "feat(auth): filter the project list and guard every route by role"
```

---

### Task 8: SPA — replace the mock provider with real auth

**Files:**
- Modify: `web/src/auth/context.ts`, `web/src/auth/AuthProvider.tsx`,
  `web/src/pages/Auth.tsx`, `web/src/App.tsx`
- Test: manual verification via the preview tooling (this repo has no web test runner —
  `web/package.json` has no `vitest`; `npm run build` and `npm run lint` are the gates)

**Interfaces:**
- Consumes: `/api/auth/signup`, `/api/auth/login`, `/api/auth/logout`, `/api/auth/me`
- Produces: `User` gains `id` and `role`; `login`/`signup` return `Promise<AuthResult>`
- **Invariant owned:** none — the client makes no authorization decision. Hiding a
  nav item is presentation, not a control.

Four changes, one of which is easy to skip and shouldn't be.

**`login`/`signup` become async.** They are synchronous today (`context.ts:14-15`),
so `Auth.tsx`'s submit handlers become `async` and must keep the submit button
disabled while in flight.

**`User` gains `id` and `role`**, and `role` decides which nav items are *offered*.

**Legacy key cleanup.** PR #7 wrote every account — including plaintext passwords —
into `localStorage` under `te_users` (`AuthProvider.tsx:21`). On first load the new
provider deletes `te_users` and `te_session`. Leaving a user's password sitting in
their browser after the upgrade would be a defect introduced by fixing this one.

**`MIN_PASSWORD` 6 → 8**, matching the server. A client minimum below the server's
turns a validation message into a 422.

No fetch changes are needed for cookies: the client uses relative paths through the
vite proxy (`web/vite.config.ts`), so requests are same-origin and carry the cookie
by default.

- [ ] **Step 1: Rewrite the context types**

```ts
// web/src/auth/context.ts
export interface User {
  id: string
  email: string
  role: 'admin' | 'reviewer'
}

export interface AuthContextValue {
  user: User | null
  ready: boolean          // false until the initial /api/auth/me settles
  login: (email: string, password: string) => Promise<AuthResult>
  signup: (email: string, password: string) => Promise<AuthResult>
  logout: () => Promise<void>
}

export const MIN_PASSWORD = 8
```

`ready` is new and load-bearing: without it, the first render has `user === null`
and `App.tsx:79` flashes the login screen at an already-signed-in user before
`/api/auth/me` returns.

- [ ] **Step 2: Rewrite the provider**

> **Load-bearing:** deleting the legacy keys; `ready` gating the first render;
> never storing a password anywhere. **Illustrative:** error copy, hook layout.

```tsx
// web/src/auth/AuthProvider.tsx
const LEGACY_KEYS = ['te_users', 'te_session']

export function AuthProvider({ children }: { children: ReactNode }): JSX.Element {
  const [user, setUser] = useState<User | null>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    // PR #7's mock stored accounts — including plaintext passwords — in the
    // browser. Clear them on upgrade rather than leaving them behind.
    LEGACY_KEYS.forEach((k) => localStorage.removeItem(k))

    fetch('/api/auth/me')
      .then((res) => (res.ok ? res.json() : null))
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setReady(true))
  }, [])
  // login/signup POST, then setUser from the response body; logout POSTs and clears.
}
```

- [ ] **Step 3: Gate the shell on `ready` and offer nav by role**

In `App.tsx`, replace `if (!user) return <Auth />` (line 79) with:

```tsx
  if (!ready) return <div className="boot" />
  if (!user) return <Auth />
```

- [ ] **Step 4: Build and lint**

Run: `npm --prefix web run build && npm --prefix web run lint`
Expected: build succeeds; lint reports only the pre-existing
`src/useAsync.ts:23` `exhaustive-deps` warning

- [ ] **Step 5: Verify in the browser**

Start `procurement-api` and `enterprise-web` via the preview tooling (not bare
`uvicorn`/`npm run dev`). Confirm: signing up lands on an empty project list;
signing in as the bootstrap admin lists every project; `document.cookie` does **not**
show `te_session` (it is `HttpOnly`); `localStorage` holds neither legacy key.

- [ ] **Step 6: Commit**

```bash
git add web/src/
git commit -m "feat(web): replace the mock auth provider with real server sessions"
```

---

### Task 9: Integration — two-run mutation matrix

**Files:**
- Test: `tests/test_auth_integration.py`
- Modify: `CLAUDE.md` (baselines), `BUGS_TRACKER.md` (close BUG-011)

**Interfaces:**
- Consumes: everything from Tasks 1–8
- Produces: no new API
- **Invariants owned:** none new — this task **defends** S1–S3 and A1–A3 across
  state changes that no single-run test can see.

Per `PLAN-TEMPLATE.md` Rule 2, translated to this domain as explained at the top of
this plan. The template's nine extraction rows have no counterpart here; these rows
are auth's own two-run hazards.

- [ ] **Step 1: Write the mutation matrix tests**

| # | mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|---|
| 1 | the user is deleted while their cookie is still held | S3 | the old cookie 401s, and `auth.json.sessions` holds no row for the deleted id |
| 2 | a grant is revoked while the session is live | A2 | the next `GET /api/projects` omits the slug, with no re-login |
| 3 | a granted project is deleted from disk | A2, S4 | the slug is absent from the list; the stale grant never resurrects it |
| 4 | two grants to different users are written concurrently | S4 | both survive — neither write is lost |
| 5 | a role is changed reviewer → admin between requests | A2 | the second request sees every project, with no re-login |
| 6 | a password is changed while a second session is live | S3 | the changing session survives; the other 401s |
| 7 | a route is added to `app.routes` with no guard | A1 | the Task 6 sweep fails, naming the route |
| 8 | the process restarts between requests | S3 | a session minted before the restart still authenticates |
| 9 | `ALLOW_SIGNUP` flips 1 → 0 between requests | — | the second signup 403s; existing accounts are unaffected |
| 10 | the same email signs up twice concurrently | S1 | exactly one user record exists afterwards |

Row 8 is the one that justifies persisting sessions to the file at all; if it fails,
every deploy signs the whole office out.

```python
# tests/test_auth_integration.py  — row 1, as the pattern for the rest
def test_deleted_user_cannot_keep_using_their_cookie(tmp_path, monkeypatch):
    """Row 1 defends S3: a session must not outlive the account it authenticates."""
    admin, reviewer = _clients(tmp_path, monkeypatch)
    assert reviewer.get("/api/auth/me").status_code == 200          # run 1
    rev = auth_store.find_by_email(str(tmp_path), "rev@t.local")
    auth_store.delete_user(str(tmp_path), rev.id)                   # mutation
    assert reviewer.get("/api/auth/me").status_code == 401          # run 2
    assert auth_store.read_auth(str(tmp_path))["sessions"] == []
```

- [ ] **Step 2: Verify the matrix is real, not decorative**

For each row, reintroduce the defect it claims to catch — one at a time — and confirm
**that row fails and nothing else does**. A row that still passes with its defect
reinstated is not testing what it claims. Concretely: for row 1, remove the
`sessions` filter from `delete_user`; for row 2, cache `granted_slugs` at login; for
row 8, move sessions to an in-memory dict. Revert each probe before moving on, and
confirm `git diff` is clean of probe markers before committing.

- [ ] **Step 3: Run the whole suite and record the baseline**

Run: `python -m pytest -q`
Record the exact workstation counts. Then derive the CI row by `CLAUDE.md`'s
documented rule — CI turns the four `test_real_corpus_coverage.py` passes and the
three `data/`-guarded passes into skips, so `CI_passed = workstation_passed − 7` and
`CI_skipped = workstation_skipped + 7`. Update **both** rows in `CLAUDE.md` from that
one measurement; editing them independently is how they drift apart.

- [ ] **Step 4: Close BUG-011 in the tracker**

Fill the `Fix` block: spec link, this plan, the commit, and the test that fails
without the fix (`tests/test_auth_middleware.py::test_every_api_route_outside_the_allowlist_requires_a_session`).
Move the row from `Open` to `Closed`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_auth_integration.py CLAUDE.md BUGS_TRACKER.md
git commit -m "test(auth): add the two-run mutation matrix and close BUG-011"
```

---

# Phase 2 — Slice 2: close BUG-012

Grants exist and are enforced from Task 7; this phase adds the way to create them.

---

### Task 10: Grant storage and the admin API

**Files:**
- Modify: `api/auth/store.py`
- Create: `api/admin_routes.py`
- Test: `tests/test_api_grants.py`

**Interfaces:**
- Consumes: `store.locked_update`, `deps.require_admin`
- Produces:
  - `store.grant(root, user_id, slug, granted_by) -> None` — idempotent
  - `store.revoke(root, user_id, slug) -> None`
  - `store.granted_slugs(root, user_id) -> set[str]` (real implementation)
  - `GET /api/admin/users`, `POST /api/admin/users`, `DELETE /api/admin/users/{id}`,
    `POST /api/admin/users/{id}/grants`, `DELETE /api/admin/users/{id}/grants/{slug}`
- **Store invariant owned (S4):** `auth.json.grants` holds exactly the grants whose
  user still exists.

Three rules the tests must pin:

**Granting twice is one grant, not two.** Otherwise revoking once leaves a duplicate
behind and access silently persists.

**An admin cannot remove their own admin role, and the last admin cannot be
deleted.** A deployment with no admin is unrecoverable without hand-editing JSON —
the same inert state Task 5 warns about, reached by accident instead of by
configuration.

**`GET /api/admin/users` never returns `password_hash`.** Assert on the raw response
text, not the parsed model, so a serialization change cannot leak it silently.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_grants.py
def test_grant_then_revoke_moves_the_reviewers_visibility(tmp_path, monkeypatch):
    """S4 + A2: the enforcement from Task 7 now has a way to be configured."""
    admin, reviewer = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    rev = auth_store.find_by_email(str(tmp_path), "rev@t.local")

    assert reviewer.get("/api/projects").json() == []
    assert admin.post(f"/api/admin/users/{rev.id}/grants", json={"slug": "tender-one"}).status_code == 201
    assert [p["slug"] for p in reviewer.get("/api/projects").json()] == ["tender-one"]
    assert admin.delete(f"/api/admin/users/{rev.id}/grants/tender-one").status_code == 204
    assert reviewer.get("/api/projects").json() == []


def test_granting_twice_is_idempotent(tmp_path, monkeypatch):
    """A duplicate row would survive a single revoke and silently keep access."""
    admin, reviewer = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    rev = auth_store.find_by_email(str(tmp_path), "rev@t.local")
    for _ in range(2):
        admin.post(f"/api/admin/users/{rev.id}/grants", json={"slug": "tender-one"})
    assert len(auth_store.read_auth(str(tmp_path))["grants"]) == 1
    admin.delete(f"/api/admin/users/{rev.id}/grants/tender-one")
    assert reviewer.get("/api/projects").json() == []


def test_a_reviewer_cannot_reach_the_admin_api(tmp_path, monkeypatch):
    admin, reviewer = _clients(tmp_path, monkeypatch)
    rev = auth_store.find_by_email(str(tmp_path), "rev@t.local")
    assert reviewer.get("/api/admin/users").status_code == 403
    assert reviewer.post(f"/api/admin/users/{rev.id}/grants", json={"slug": "x"}).status_code == 403


def test_the_last_admin_cannot_be_deleted_or_demoted(tmp_path, monkeypatch):
    """An admin-less deployment is unrecoverable without hand-editing JSON."""
    admin, _ = _clients(tmp_path, monkeypatch)
    me = auth_store.find_by_email(str(tmp_path), "admin@t.local")
    assert admin.delete(f"/api/admin/users/{me.id}").status_code == 409


def test_the_admin_user_list_never_carries_a_password_hash(tmp_path, monkeypatch):
    admin, _ = _clients(tmp_path, monkeypatch)
    res = admin.get("/api/admin/users")
    assert res.status_code == 200
    assert "password_hash" not in res.text and "scrypt$" not in res.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_grants.py -v`
Expected: FAIL — 404 on every `/api/admin/*` path

- [ ] **Step 3: Write minimal implementation**

> **Load-bearing:** the idempotence check inside the lock; the last-admin guard;
> `granted_slugs` reading grants fresh on every call rather than caching at login.
> **Illustrative:** the route shapes and status codes.

```python
# api/auth/store.py  (additions)
def grant(root: str, user_id: str, slug: str, granted_by: str) -> None:
    with locked_update(root) as doc:
        if any(g["user_id"] == user_id and g["slug"] == slug for g in doc["grants"]):
            return                      # idempotent: a second grant is not a second row
        doc["grants"].append({
            "user_id": user_id, "slug": slug,
            "granted_at": _now().isoformat(), "granted_by": granted_by,
        })


def granted_slugs(root: str, user_id: str) -> set[str]:
    """Read fresh every call. Caching this at login is mutation-matrix row 2."""
    return {g["slug"] for g in read_auth(root)["grants"] if g["user_id"] == user_id}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_api_grants.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add api/auth/store.py api/admin_routes.py api/main.py tests/test_api_grants.py
git commit -m "feat(auth): add per-user project grants and the admin API"
```

---

### Task 11: Admin user-management screen

**Files:**
- Create: `web/src/pages/Admin.tsx`
- Modify: `web/src/App.tsx` (admin-only nav entry), `web/src/api.ts` (admin calls)
- Test: manual verification via the preview tooling

**Interfaces:**
- Consumes: the `/api/admin/*` routes from Task 10
- Produces: an `Admin` page component
- **Invariant owned:** none — presentation only.

The screen lists users with their role, and for each reviewer a checkbox per project
that grants and revokes. The nav entry renders only for `role === 'admin'` — which is
convenience, not a control: Task 10's `require_admin` is what actually refuses a
reviewer, and Task 10's tests assert it independently of this screen.

- [ ] **Step 1: Build the page**

Follow the existing page structure (`web/src/pages/Overview.tsx` is the closest
model: a `useAsync` load, a `Card` layout, an error branch). Reuse `unwrap()` from
`api.ts` so a 403 surfaces its `detail`.

- [ ] **Step 2: Add the admin-only nav entry**

```tsx
// web/src/App.tsx — NAV filtering
const visibleNav = NAV.filter((item) => !item.adminOnly || user.role === 'admin')
```

- [ ] **Step 3: Build and lint**

Run: `npm --prefix web run build && npm --prefix web run lint`
Expected: build succeeds; only the pre-existing `useAsync.ts:23` warning

- [ ] **Step 4: Verify in the browser**

Sign in as admin, grant a reviewer one project, sign in as that reviewer in a second
browser context, confirm exactly that project is listed and the admin nav entry is
absent. Revoke, reload, confirm the list empties.

- [ ] **Step 5: Commit**

```bash
git add web/src/
git commit -m "feat(web): add the admin user and grant management screen"
```

---

### Task 12: Phase 2 integration and closeout

**Files:**
- Test: `tests/test_auth_integration.py` (extend)
- Modify: `CLAUDE.md`, `BUGS_TRACKER.md`, `CHANGELOG.md` if present on the branch

**Interfaces:**
- Consumes: everything
- **Invariants owned:** none new — extends Task 9's matrix to cover S4.

- [ ] **Step 1: Extend the mutation matrix**

Rows 2, 3, 4 and 5 from Task 9's table become executable now that grants can be
created through the API rather than by writing `auth.json` directly. Rewrite them to
use `/api/admin/*`, and add:

| # | mutation | invariant | assert |
|---|---|---|---|
| 11 | a user is deleted while holding grants | S4 | no grant row references the deleted id |
| 12 | a grant is created for a slug that does not exist | S4, A2 | accepted or rejected — assert whichever is chosen, and state it in the spec |

Row 12 is an open question this plan does **not** silently decide. Granting ahead of
project creation is a reasonable workflow; so is refusing a grant to a nonexistent
slug. Pick one during Task 10's review, assert it, and record the choice in the spec.

- [ ] **Step 2: Verify the matrix is real**

Same discipline as Task 9 Step 2: reinstate each defect, confirm exactly the intended
row fails, revert, confirm `git diff` is clean.

- [ ] **Step 3: Run the whole suite and update both baselines**

Run: `python -m pytest -q`
Update `CLAUDE.md` from the measured workstation row, deriving CI by `−7`.

- [ ] **Step 4: Close BUG-012**

Fill the `Fix` block with spec, plan, commit and the failing test, and move the row
to `Closed`. Leave `BUG-013` open — it is deliberately out of scope.

- [ ] **Step 5: Commit**

```bash
git add tests/ CLAUDE.md BUGS_TRACKER.md
git commit -m "test(auth): extend the mutation matrix for grants and close BUG-012"
```

---

## Self-review

**Spec coverage.** Every section maps to a task: §4 data model → Tasks 1, 3, 10;
§5 authentication → Tasks 2, 3, 4, 5; §6.1 middleware → Task 6; §6.2 route matrix →
Task 7; §6.3 error contract → Tasks 6, 7 (401/403 asserted separately); §7 client →
Tasks 8, 11; §8 migration → no task needed, and Task 7's
`test_admin_sees_every_project_and_reviewer_sees_none` proves the fail-closed
direction; §9 testing → every task, with Tasks 9 and 12 carrying the matrices;
§10 out of scope → no task, `BUG-013` filed.

**Two gaps found and closed while reviewing.** The spec did not say what a password
change does to sibling sessions — Task 3 decides it (revoke others, keep current)
and asserts it. The spec did not say whether a grant may name a nonexistent slug —
Task 12 row 12 refuses to decide it silently and routes it back to the spec.

**Type consistency.** `granted_slugs` returns `set[str]` in Tasks 7 and 10;
`create_session` returns the plaintext token in Task 3 and is consumed as such in
Task 4; `User` carries `id`/`email`/`role`/`created_at` in Task 1 and the SPA mirrors
`id`/`email`/`role` in Task 8. `password_hash` is never a `User` field in any task —
it is read only through `store.password_hash_for`.
