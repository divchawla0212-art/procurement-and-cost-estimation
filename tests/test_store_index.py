# tests/test_store_index.py
import os
import sqlite3
import pytest
from procurement.project import create_project
from procurement.store import index, layout, snapshots
from procurement.store.models import DocumentRecord, VendorFacts


def _seed(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    with snapshots.transaction(root, "p"):
        snapshots.save_documents(root, "p", [
            DocumentRecord(doc_id="d1", path="vendors/KERUI/Q.pdf", vendor="KERUI",
                           doc_class="quotation", content_sha256="a" * 64,
                           extraction_status="ok"),
            DocumentRecord(doc_id="d2", path="vendors/KERUI/BOM.pdf", vendor="KERUI",
                           doc_class="bom", content_sha256="b" * 64,
                           extraction_status="skipped"),
        ])
        snapshots.save_facts(root, "p", VendorFacts(vendor="KERUI",
                                                    commercial={"base_price": 1000.0},
                                                    normalized={"normalized_total": 1000.0}))
    return root


def test_query_documents_returns_rows(tmp_path):
    root = _seed(tmp_path)
    assert len(index.query_documents(root, "p")) == 2
    assert len(index.query_documents(root, "p", doc_class="quotation")) == 1
    assert len(index.query_documents(root, "p", vendor="NOPE")) == 0


def test_deleting_the_index_changes_no_result(tmp_path):
    root = _seed(tmp_path)
    before = index.query_documents(root, "p")
    os.remove(layout.index_path(root, "p"))
    assert index.query_documents(root, "p") == before


def test_stale_generation_triggers_rebuild_before_answering(tmp_path):
    root = _seed(tmp_path)
    index.query_documents(root, "p")            # builds
    assert index.is_stale(root, "p") is False

    with snapshots.transaction(root, "p"):      # generation moves on
        snapshots.save_documents(root, "p", [
            DocumentRecord(doc_id="d1", path="vendors/KERUI/Q.pdf", vendor="KERUI",
                           doc_class="quotation", content_sha256="a" * 64,
                           extraction_status="ok"),
        ])
    assert index.is_stale(root, "p") is True
    assert len(index.query_documents(root, "p")) == 1   # rebuilt, not stale data
    assert index.is_stale(root, "p") is False


def test_snapshot_edited_without_generation_bump_is_still_detected(tmp_path):
    root = _seed(tmp_path)
    index.query_documents(root, "p")
    # hand-edit the snapshot, bypassing the generation counter entirely
    layout.atomic_write_json(layout.documents_path(root, "p"), [])
    assert index.query_documents(root, "p") == []


def test_vendor_summary_joins_documents_and_facts(tmp_path):
    root = _seed(tmp_path)
    rows = index.query_vendor_summary(root, "p")
    assert rows == [{"vendor": "KERUI", "documents": 2, "extracted": 1,
                     "normalized_total": 1000.0}]


def test_index_is_never_opened_writable_outside_rebuild(tmp_path, monkeypatch):
    root = _seed(tmp_path)
    index.query_documents(root, "p")
    real_connect = sqlite3.connect
    opened = []

    def spy(path, *a, **k):
        opened.append(k.get("uri", False))
        return real_connect(path, *a, **k)

    monkeypatch.setattr(index.sqlite3, "connect", spy)
    index.query_documents(root, "p")
    assert opened and all(opened), "queries must open the index read-only (uri=True)"


def test_corrupt_index_falls_back_to_snapshots(tmp_path):
    root = _seed(tmp_path)
    index.query_documents(root, "p")
    with open(layout.index_path(root, "p"), "wb") as fh:
        fh.write(b"not a database")
    assert len(index.query_documents(root, "p")) == 2
