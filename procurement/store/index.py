"""Derived SQLite index over the JSON snapshots.

This module is the ONLY one that opens store.db. The index is disposable:
deleting it must change no query result.

Every query checks freshness before answering and rebuilds on any mismatch.
The freshness key is the project `generation` plus the mtime_ns and size of
every snapshot file. `generation` only moves inside a `transaction()`, so for
a bare `save_*` outside one, mtime+size is the only guard - which catches
everything except a write that lands in the same mtime tick at an identical
size. Stale reads are therefore very unlikely, not impossible by construction.
"""
import logging
import os
import sqlite3

from procurement.store import layout, snapshots

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE documents (
    doc_id TEXT PRIMARY KEY, path TEXT, vendor TEXT, doc_class TEXT,
    content_sha256 TEXT, extraction_status TEXT, superseded_by TEXT
);
CREATE TABLE facts (
    vendor TEXT PRIMARY KEY, normalized_total REAL
);
CREATE INDEX idx_documents_vendor ON documents(vendor);
CREATE INDEX idx_documents_class ON documents(doc_class);
"""


def _snapshot_stat(root: str, slug: str) -> str:
    """mtime+size of every snapshot, so hand-edits that bypass the generation
    counter are still detected."""
    paths = [layout.documents_path(root, slug)]
    paths += [layout.facts_path(root, slug, v) for v in snapshots.list_fact_vendors(root, slug)]
    parts = []
    for p in sorted(paths):
        if os.path.exists(p):
            st = os.stat(p)
            parts.append(f"{os.path.basename(os.path.dirname(p))}/{os.path.basename(p)}:{st.st_mtime_ns}:{st.st_size}")
    return "|".join(parts)


def _expected_meta(root: str, slug: str) -> dict:
    return {"generation": str(snapshots.get_generation(root, slug)),
            "snapshot_stat": _snapshot_stat(root, slug)}


def is_stale(root: str, slug: str) -> bool:
    path = layout.index_path(root, slug)
    if not os.path.exists(path):
        return True
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        finally:
            # sqlite3.Connection used as a context manager only governs the
            # commit/rollback of a transaction, it never closes the file
            # handle - close explicitly so Windows doesn't keep store.db
            # locked (which would break the rebuild()'s later os.replace).
            conn.close()
    except sqlite3.Error:
        return True
    return rows != _expected_meta(root, slug)


def rebuild(root: str, slug: str) -> None:
    path = layout.index_path(root, slug)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    meta = _expected_meta(root, slug)      # captured BEFORE reading, so a
    docs = snapshots.load_documents(root, slug)   # concurrent write invalidates
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(_SCHEMA)
        conn.executemany(
            "INSERT INTO documents VALUES (?,?,?,?,?,?,?)",
            [(d.doc_id, d.path, d.vendor, d.doc_class, d.content_sha256,
              d.extraction_status, d.superseded_by) for d in docs])
        for vendor in snapshots.list_fact_vendors(root, slug):
            facts = snapshots.load_facts(root, slug, vendor)
            total = (facts.normalized or {}).get("normalized_total") if facts else None
            conn.execute("INSERT INTO facts VALUES (?,?)", (vendor, total))
        conn.executemany("INSERT INTO meta VALUES (?,?)", list(meta.items()))
        conn.commit()
    finally:
        conn.close()  # release the handle before os.replace() (see note above)
    os.replace(tmp, path)


def _connect_fresh(root: str, slug: str) -> sqlite3.Connection:
    if is_stale(root, slug):
        rebuild(root, slug)
    return sqlite3.connect(f"file:{layout.index_path(root, slug)}?mode=ro", uri=True)


def query_documents(root: str, slug: str, vendor: str | None = None,
                    doc_class: str | None = None) -> list[dict]:
    sql = "SELECT doc_id, path, vendor, doc_class, extraction_status FROM documents WHERE 1=1"
    args: list = []
    if vendor is not None:
        sql += " AND vendor = ?"
        args.append(vendor)
    if doc_class is not None:
        sql += " AND doc_class = ?"
        args.append(doc_class)
    sql += " ORDER BY path"
    try:
        conn = _connect_fresh(root, slug)
        try:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        log.warning("index unavailable (%s); falling back to snapshots", exc)
        return _fallback_documents(root, slug, vendor, doc_class)


def _fallback_documents(root, slug, vendor, doc_class) -> list[dict]:
    out = []
    for d in snapshots.load_documents(root, slug):
        if vendor is not None and d.vendor != vendor:
            continue
        if doc_class is not None and d.doc_class != doc_class:
            continue
        out.append({"doc_id": d.doc_id, "path": d.path, "vendor": d.vendor,
                    "doc_class": d.doc_class, "extraction_status": d.extraction_status})
    return sorted(out, key=lambda r: r["path"])


def query_vendor_summary(root: str, slug: str) -> list[dict]:
    sql = """
        SELECT d.vendor AS vendor,
               COUNT(*) AS documents,
               SUM(CASE WHEN d.extraction_status = 'ok' THEN 1 ELSE 0 END) AS extracted,
               f.normalized_total AS normalized_total
        FROM documents d LEFT JOIN facts f ON f.vendor = d.vendor
        WHERE d.vendor IS NOT NULL
        GROUP BY d.vendor ORDER BY d.vendor
    """
    try:
        conn = _connect_fresh(root, slug)
        try:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql)]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        log.warning("index unavailable (%s); falling back to snapshots", exc)
        rows: dict[str, dict] = {}
        for d in snapshots.load_documents(root, slug):
            if d.vendor is None:
                continue
            r = rows.setdefault(d.vendor, {"vendor": d.vendor, "documents": 0,
                                           "extracted": 0, "normalized_total": None})
            r["documents"] += 1
            r["extracted"] += 1 if d.extraction_status == "ok" else 0
        for vendor, r in rows.items():
            facts = snapshots.load_facts(root, slug, vendor)
            r["normalized_total"] = (facts.normalized or {}).get("normalized_total") if facts else None
        return [rows[k] for k in sorted(rows)]
