"""Paths, atomic JSON IO, and content hashing for the project store.

Imports stdlib only: procurement.project imports this module, so importing
anything from procurement.* here would create a circular import.
"""
import hashlib
import json
import os
import time
import uuid

STORE_VERSION = 2


def project_dir(root: str, slug: str) -> str:
    return os.path.join(root, slug)


def store_dir(root: str, slug: str) -> str:
    return os.path.join(project_dir(root, slug), "store")


def index_path(root: str, slug: str) -> str:
    return os.path.join(project_dir(root, slug), "index", "store.db")


def documents_path(root: str, slug: str) -> str:
    return os.path.join(store_dir(root, slug), "documents.json")


def facts_path(root: str, slug: str, vendor: str) -> str:
    return os.path.join(store_dir(root, slug), "vendors", vendor, "facts.json")


def requirements_path(root: str, slug: str) -> str:
    return os.path.join(store_dir(root, slug), "requirements.json")


def compliance_path(root: str, slug: str) -> str:
    return os.path.join(store_dir(root, slug), "compliance.json")


def migration_marker_path(root: str, slug: str) -> str:
    """Positive record that the legacy dataset.json import completed."""
    return os.path.join(store_dir(root, slug), "migrated.json")


def events_path(root: str, slug: str) -> str:
    return os.path.join(store_dir(root, slug), "events.jsonl")


def atomic_write_json(path: str, data) -> None:
    """Write JSON so an interrupted write cannot destroy the previous copy.

    The fsync matters: os.replace() is atomic with respect to the directory
    entry, but without flushing the new contents to disk first a power loss can
    leave the *replaced* name pointing at a truncated file. Any failure - a
    serialization error mid-dump included - removes the temp file rather than
    orphaning it next to the real one.

    The temp name is unique per write, never `path + ".tmp"` (BUG-008). A name
    derived from the destination is shared by every concurrent writer of that
    destination: the second open(..., "w") truncates what the first is still
    writing, both rename the result into place, and the surviving file holds a
    mix of the two - valid JSON, wrong content, undetectable downstream. It
    also made writers delete each other's temp on the error path below. With a
    unique name each writer owns its own file and os.replace decides the winner
    atomically, so a concurrent write is last-writer-wins rather than corrupt.
    That is a safe primitive, NOT a substitute for serialising the callers:
    two runs that each write a *different* snapshot still interleave at the
    collection level, which is what the ingest lock in `api/main.py` is for.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        _replace_with_retry(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _replace_with_retry(tmp: str, path: str, attempts: int = 10) -> None:
    """os.replace, retried briefly on Windows' transient sharing errors.

    POSIX rename(2) replaces an existing destination atomically and never fails
    because someone else is replacing it at the same time, so this loop is a
    no-op on Linux - the deployment target - and succeeds on the first attempt.

    Windows is not POSIX here: MoveFileEx raises PermissionError ("Access is
    denied") when two threads replace one destination in the same instant, even
    though each owns a distinct source file. Measured at 3 collisions in 15
    two-thread races on the workstation. The source is this writer's own unique
    temp, untouched by anyone else, so retrying is safe and idempotent - it
    re-attempts the rename, never the write.
    """
    for attempt in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.005 * (attempt + 1))


def read_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def content_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def doc_id_for(vendor: str | None, rel_path: str) -> str:
    """Path-addressed id: stable across content edits, so revision history
    attaches to a location and two vendors submitting a byte-identical file
    remain two distinct submissions."""
    key = f"{vendor or '_rfq'}/{rel_path.replace(os.sep, '/')}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
