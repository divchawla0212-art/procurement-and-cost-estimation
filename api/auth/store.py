"""The `auth.json` store: file layout and a locked read-modify-write.

`<ROOT>/auth.json` is written only from this module. It holds users, sessions
and grants in one document so a single lock and a single atomic write keep
all three consistent with each other; sessions (Task 3) and grants (Task 10)
are populated by later tasks, but the empty document already reserves their
keys so those tasks have somewhere to write without touching this file's
shape again.
"""
import os
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
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


class EmailTaken(Exception):
    """A user with this normalized email already exists."""


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


def delete_user(root: str, user_id: str) -> None:
    """Remove the user and everything that points at them, in one write."""
    with locked_update(root) as doc:
        doc["users"] = [u for u in doc["users"] if u["id"] != user_id]
        doc["grants"] = [g for g in doc["grants"] if g["user_id"] != user_id]
        doc["sessions"] = [s for s in doc["sessions"] if s["user_id"] != user_id]
