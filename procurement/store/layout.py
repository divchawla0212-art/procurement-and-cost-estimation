"""Paths, atomic JSON IO, and content hashing for the project store.

Imports stdlib only: procurement.project imports this module, so importing
anything from procurement.* here would create a circular import.
"""
import hashlib
import json
import os

STORE_VERSION = 1


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
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


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
