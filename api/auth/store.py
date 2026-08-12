"""The `auth.json` store: file layout and a locked read-modify-write.

`<ROOT>/auth.json` is written only from this module. It holds users, sessions
and grants in one document so a single lock and a single atomic write keep
all three consistent with each other; sessions (Task 3) and grants (Task 10)
are populated by later tasks, but the empty document already reserves their
keys so those tasks have somewhere to write without touching this file's
shape again.
"""
import hashlib
import hmac
import os
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from procurement.store import layout
from api.auth.models import User

# One process, one lock. The shipped deployment is a single uvicorn worker
# (docker-compose.yml, .claude/launch.json — no --workers), so a thread lock
# is enough to serialize writers within that process. If that ever becomes
# multiple workers or multiple containers over a shared volume, this must
# become a cross-process file lock: layout.atomic_write_json() builds its
# temp file as path + ".tmp", a name shared by every writer of this path, so
# two concurrent writers would collide on it (see BUG-008 for the snapshot
# writer's version of the same defect). Serializing every write through this
# lock is what makes the shared temp name safe here.
_LOCK = threading.Lock()

_EMPTY = {"version": 1, "users": [], "grants": [], "sessions": []}

SESSION_TTL_SECONDS = 604800  # 7 days, absolute — no sliding refresh


class EmailTaken(Exception):
    """A user with this normalized email already exists."""


class UnknownUser(Exception):
    """No user with this id exists.

    Raised from inside the lock, so a caller cannot act on a user that was
    deleted between its own existence check and its write.
    """


class LastAdmin(Exception):
    """This write would leave the deployment with no administrator.

    Recovering from that needs someone to hand-edit `auth.json`, since every
    `/api/admin/*` route requires an admin to reach it — the same inert state
    `bootstrap.seed_admin_if_empty` warns about, arrived at by accident.
    """


def auth_path(root: str) -> str:
    return os.path.join(root, "auth.json")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def read_auth(root: str) -> dict:
    """Load the document, tolerating absence or a partial hand-edit.

    A missing file reads as the empty document rather than raising, and a
    document that is missing one of the top-level keys (or isn't a dict at
    all) gets those keys filled in rather than failing later lookups. This is
    defensive against a truncated or hand-edited file, not a migration
    framework: `version` is stored and otherwise unused for now.
    """
    doc = layout.read_json(auth_path(root), default=None)
    if not isinstance(doc, dict):
        doc = {}
    for key, empty in _EMPTY.items():
        if key not in doc:
            doc[key] = list(empty) if isinstance(empty, list) else empty
    return doc


@contextmanager
def locked_update(root: str) -> Iterator[dict]:
    """Read, mutate, write — as one unit, with no other writer interleaving.

    The write happens only on a clean exit, so a caller that raises leaves
    the on-disk document exactly as it was before the block ran.
    """
    with _LOCK:
        doc = read_auth(root)
        yield doc
        layout.atomic_write_json(auth_path(root), doc)


def create_user(root: str, email: str, password_hash: str, role: str) -> User:
    """Register a new account.

    The duplicate-email check runs inside the lock, not before it: checking
    outside would let two concurrent signups both read "no such email" and
    both write, leaving two records for one address (violates S1).
    """
    normalized = normalize_email(email)
    with locked_update(root) as doc:
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


def _to_user(record: dict) -> User:
    """Drop `password_hash` before handing a stored row back to a caller."""
    return User(
        id=record["id"],
        email=record["email"],
        role=record["role"],
        created_at=record["created_at"],
    )


def find_by_email(root: str, email: str) -> User | None:
    normalized = normalize_email(email)
    for record in read_auth(root)["users"]:
        if record["email"] == normalized:
            return _to_user(record)
    return None


def find_by_id(root: str, user_id: str) -> User | None:
    for record in read_auth(root)["users"]:
        if record["id"] == user_id:
            return _to_user(record)
    return None


def password_hash_for(root: str, user_id: str) -> str | None:
    """The only way to reach a stored digest.

    `User` has no `password_hash` field, so a `User` returned from
    `find_by_email`/`find_by_id` can never leak it into a response body. This
    is the one function that reads the digest, for password verification in
    Task 4's login route.
    """
    for record in read_auth(root)["users"]:
        if record["id"] == user_id:
            return record.get("password_hash")
    return None


def granted_slugs(root: str, user_id: str) -> set[str]:
    """The set of project slugs this user has been granted access to.

    Reads `auth.json` fresh on every call — never cached at login or on the
    session — so a grant made or revoked between two requests takes effect on
    the very next one.
    """
    return {g["slug"] for g in read_auth(root)["grants"] if g["user_id"] == user_id}


def list_users(root: str) -> list[User]:
    """Every registered user, scrubbed of `password_hash` by `_to_user`.

    The only way `api/admin_routes.py`'s user list reaches `auth.json` —
    routes never read the file directly.
    """
    return [_to_user(record) for record in read_auth(root)["users"]]


def grant(root: str, user_id: str, slug: str, granted_by: str) -> None:
    """Give `user_id` access to `slug`. Idempotent: a second grant for the
    same (user, slug) pair is not a second row.

    Both checks run inside the lock, and both have to. The existence check is
    S4 itself: a grant racing that user's deletion would otherwise re-add a
    row for a user who no longer exists, since `delete_user` prunes what is
    there at the moment it runs and cannot prune an append that comes after.
    The duplicate check keeps two concurrent grants from both reading "not
    yet granted" and both appending — duplicates are not a privilege leak
    (`revoke` filters every matching row, so one revoke still removes them
    all) but they grow the file without bound and make the audit trail read
    as if access had been granted twice by two different admins.
    """
    with locked_update(root) as doc:
        if not any(u["id"] == user_id for u in doc["users"]):
            raise UnknownUser(user_id)
        if any(g["user_id"] == user_id and g["slug"] == slug for g in doc["grants"]):
            return
        doc["grants"].append({
            "user_id": user_id, "slug": slug,
            "granted_at": _now().isoformat(), "granted_by": granted_by,
        })


def grants_by_user(root: str) -> dict[str, list[str]]:
    """Every grant, grouped by user id, from one read of the document.

    The admin user list needs each user's grants to render a per-project
    checkbox with its current state; calling `granted_slugs` per user would
    re-read `auth.json` once per row.
    """
    grouped: dict[str, list[str]] = {}
    for g in read_auth(root)["grants"]:
        grouped.setdefault(g["user_id"], []).append(g["slug"])
    return {user_id: sorted(slugs) for user_id, slugs in grouped.items()}


def revoke(root: str, user_id: str, slug: str) -> None:
    """Remove any grant of `slug` to `user_id`. A no-op if none existed."""
    with locked_update(root) as doc:
        doc["grants"] = [
            g for g in doc["grants"]
            if not (g["user_id"] == user_id and g["slug"] == slug)
        ]


def delete_user(root: str, user_id: str) -> None:
    """Remove the user and everything that points at them, in one write.

    Refuses to remove the only remaining admin, and decides that inside the
    lock. Counting admins in the caller and deleting here would be two
    separate critical sections: two admins deleting each other at the same
    instant would each read "two admins, fine" and both writes would land,
    leaving zero. Raising here leaves the document untouched — `locked_update`
    writes only on a clean exit.
    """
    with locked_update(root) as doc:
        target = next((u for u in doc["users"] if u["id"] == user_id), None)
        if target is not None and target["role"] == "admin":
            if len([u for u in doc["users"] if u["role"] == "admin"]) <= 1:
                raise LastAdmin(user_id)
        doc["users"] = [u for u in doc["users"] if u["id"] != user_id]
        doc["grants"] = [g for g in doc["grants"] if g["user_id"] != user_id]
        doc["sessions"] = [s for s in doc["sessions"] if s["user_id"] != user_id]


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _unexpired(rows: list[dict]) -> list[dict]:
    """Drop rows whose `expires_at` has passed.

    Called on every write (S3: "exactly") so an expired row does not merely
    stop resolving on read — it leaves the file on the next mutation.
    """
    now = _now()
    return [r for r in rows if datetime.fromisoformat(r["expires_at"]) > now]


def _user_from_row(doc: dict, user_id: str) -> User | None:
    """Look up the user for a session row within an already-loaded document.

    Returns `None` when the referenced user is gone, so a session that
    somehow outlives its user fails closed rather than resolving to a
    partially-built user. `delete_user` already cascades to sessions, so this
    is defence in depth, not the primary mechanism.
    """
    for record in doc["users"]:
        if record["id"] == user_id:
            return _to_user(record)
    return None


def create_session(root: str, user_id: str, *, ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
    """Create a session and return its plaintext token.

    Only `sha256(token)` is written to disk — the plaintext exists solely in
    this return value, for the caller (Task 4's login route) to place in an
    HttpOnly cookie. It can never be recovered from a leaked `auth.json`.
    """
    token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(seconds=ttl_seconds)
    with locked_update(root) as doc:
        # Prune on write: expiry-on-read alone would let the file grow without bound.
        doc["sessions"] = _unexpired(doc["sessions"])
        doc["sessions"].append({
            "token_sha256": _token_digest(token),
            "user_id": user_id,
            "expires_at": expires.isoformat(),
        })
    return token


def resolve_session(root: str, token: str) -> User | None:
    """Return the session's user, or `None` if the token is missing, unknown,
    expired, or its user no longer exists."""
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


def delete_session(root: str, token: str) -> None:
    """Log out: invalidate exactly the session for this token.

    Prunes expired rows too — every writer to `sessions` must, or an expired
    row belonging to some other user lingers until the next call that
    happens to touch it (S3: "exactly", not "eventually").
    """
    digest = _token_digest(token)
    with locked_update(root) as doc:
        doc["sessions"] = _unexpired([
            s for s in doc["sessions"] if s["token_sha256"] != digest
        ])


def delete_sessions_for(root: str, user_id: str, *, keep_token: str | None = None) -> None:
    """Revoke a user's sessions, optionally keeping one.

    Used by the password-change flow (Task 4): revoking the user's *other*
    sessions while keeping the caller's current one signed in. Without
    `keep_token`, every session for `user_id` is revoked.

    Prunes expired rows too, for the same reason as `delete_session`.
    """
    keep_digest = _token_digest(keep_token) if keep_token else None
    with locked_update(root) as doc:
        doc["sessions"] = _unexpired([
            s for s in doc["sessions"]
            if s["user_id"] != user_id or s["token_sha256"] == keep_digest
        ])
