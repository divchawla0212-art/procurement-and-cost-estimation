import json
import os
import pytest
from procurement.store import layout


def test_atomic_write_creates_file(tmp_path):
    p = str(tmp_path / "a.json")
    layout.atomic_write_json(p, {"x": 1})
    assert json.load(open(p)) == {"x": 1}
    assert not os.path.exists(p + ".tmp")


def test_atomic_write_leaves_previous_intact_on_failure(tmp_path, monkeypatch):
    p = str(tmp_path / "a.json")
    layout.atomic_write_json(p, {"x": 1})

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(layout.os, "replace", boom)
    with pytest.raises(OSError):
        layout.atomic_write_json(p, {"x": 2})

    # the previous good copy must survive an interrupted write
    assert json.load(open(p)) == {"x": 1}


def test_read_json_missing_returns_default(tmp_path):
    assert layout.read_json(str(tmp_path / "nope.json"), default=[]) == []


def test_content_sha256_is_stable_and_content_sensitive(tmp_path):
    f = tmp_path / "d.bin"
    f.write_bytes(b"hello")
    first = layout.content_sha256(str(f))
    assert first == layout.content_sha256(str(f))
    f.write_bytes(b"hello!")
    assert layout.content_sha256(str(f)) != first


def test_doc_id_is_path_addressed_not_content_addressed():
    # same path -> same id regardless of content; different vendor -> different id
    a = layout.doc_id_for("KERUI", "vendors/KERUI/Quote.pdf")
    b = layout.doc_id_for("KERUI", "vendors/KERUI/Quote.pdf")
    c = layout.doc_id_for("ADPOWER", "vendors/ADPOWER/Quote.pdf")
    assert a == b and a != c
    assert len(a) == 12


def test_doc_id_for_rfq_document():
    assert layout.doc_id_for(None, "requirements/MR.pdf") != layout.doc_id_for("KERUI", "requirements/MR.pdf")
