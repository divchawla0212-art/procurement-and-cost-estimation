"""Real documents against an RFQ — the blob store, the records, and the route.

Three things are being defended here.

**I-D** — `<ROOT>/rfq-docs/<rfq_id>/` contains exactly the blobs referenced by
live `RfqDocument` records for that RFQ. No orphan blobs, no dangling records.
Content addressing means two records legitimately point at one blob, so
deleting a record deletes the blob **only when no other record still
references it**. A single-document test cannot tell "always deletes" from
"deletes when unshared", which is why the delete cases below upload the same
bytes twice.

**I-E** — `workflow.json` holds exactly the `RfqDocument` records whose RFQ
still exists. Nothing in this repository deletes an RFQ (project deletion is
refused while a project holds one), so the invariant is held at both ends
instead: a record cannot be created for an RFQ that does not exist, and a
document carrying a record whose RFQ is gone does not load it.

**The traversal guard** — every stored name goes through
`workflow.safe_extract`, the same module `procurement/project.py` uses. The
zip-slip case here is the same attack that file's own tests cover, arriving
through a different door.

Key-free and fixture-free.
"""
import hashlib
import io
import json
import os
import zipfile
from datetime import date

import pytest
from fastapi.testclient import TestClient

from tests.auth_helpers import ADMIN_EMAIL, ADMIN_PASSWORD, signed_in_admin
from workflow import doc_store, persistence
from workflow.models.rfq_document import EligibilityCategory, RfqDocument
from workflow.store import WorkflowStore


# -- helpers ------------------------------------------------------------------


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    return buffer.getvalue()


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return signed_in_admin(TestClient(api_main.app), tmp_path)


def make_rfq(client: TestClient) -> str:
    r = client.post("/api/workflow/projects", json={
        "name": "Haliba Field Development", "code": "HAL",
        "client": "Al Dhafra Petroleum", "location": "Haliba field, UAE",
        "live_period_start": "2026-01-01", "live_period_end": "2029-12-31",
    })
    project_id = r.json()["id"]
    r = client.post(f"/api/workflow/projects/{project_id}/items", json={
        "item_type": "HV cable", "description": "11 kV, 3-core",
        "qty": 1200, "uom": "m", "discipline": "Cables",
        "estimated_value_aed": 900000,
    })
    item_id = r.json()["id"]
    r = client.post("/api/workflow/rfqs", json={
        "project_id": project_id, "item_ids": [item_id],
        "reference": "ADP-RFQ-2026-014", "package": "HV cable",
        "discipline": "Cables", "value_estimate_aed": 900000,
    })
    return r.json()["id"]


def upload(client: TestClient, rfq_id: str, files, **data):
    return client.post(
        f"/api/workflow/rfqs/{rfq_id}/documents", files=files, data=data or None
    )


def blob_files(root, rfq_id: str) -> list[str]:
    """Every blob actually on disk for one RFQ, as `<sha>/<leaf>` pairs."""
    base = os.path.join(str(root), "rfq-docs", rfq_id)
    found = []
    for dirpath, _dirs, names in os.walk(base):
        for name in names:
            found.append(os.path.join(dirpath, name))
    return sorted(found)


def populated_store() -> tuple[WorkflowStore, str]:
    store = WorkflowStore()
    project = store.create_project(
        name="Haliba", code="HAL", client="ADP", location="UAE",
        live_period_start=date(2026, 1, 1), live_period_end=date(2029, 12, 31),
    )
    item = store.create_item(
        project_id=project.id, item_type="HV cable", description="11 kV",
        qty=1200, uom="m", discipline="Cables", estimated_value_aed=900_000,
    )
    rfq = store.create_rfq(
        project_id=project.id, item_ids=[item.id], reference="ADP-RFQ-2026-014",
        package="HV cable", discipline="Cables", value_estimate_aed=900_000,
    )
    return store, rfq.id


def a_document(rfq_id: str, **changes) -> RfqDocument:
    return RfqDocument(**{
        "rfq_id": rfq_id,
        "filename": "datasheet.pdf",
        "rel_path": "datasheet.pdf",
        "sha256": hashlib.sha256(b"x").hexdigest(),
        "size_bytes": 1,
        "content_type": "application/pdf",
        "category": None,
        "uploaded_by": "buyer@example.com",
        "uploaded_at": "2026-08-15T09:00:00+00:00",
        "submitted_by_vendor_id": None,
        **changes,
    })


# -- the blob store -----------------------------------------------------------


def test_a_blob_lands_under_the_fan_out_with_its_own_name(tmp_path):
    blobs = doc_store.LocalBlobStore(str(tmp_path))

    ref = blobs.put("rfq_abc", "datasheet.pdf", io.BytesIO(b"hello"))

    digest = hashlib.sha256(b"hello").hexdigest()
    assert ref.sha256 == digest
    assert ref.size == 5
    expected = tmp_path / "rfq-docs" / "rfq_abc" / digest[:2] / digest / "datasheet.pdf"
    assert expected.read_bytes() == b"hello"


def test_a_blob_ref_carries_no_absolute_path(tmp_path):
    """So the record is still valid after the root moves, and so an S3
    implementation has somewhere to put a key."""
    blobs = doc_store.LocalBlobStore(str(tmp_path))

    ref = blobs.put("rfq_abc", "datasheet.pdf", io.BytesIO(b"hello"))

    assert not os.path.isabs(ref.rel_path)
    assert str(tmp_path) not in ref.rel_path
    assert ref.rel_path.startswith("rfq-docs/rfq_abc/")
    assert "\\" not in ref.rel_path


def test_the_same_bytes_under_the_same_name_are_written_once(tmp_path):
    blobs = doc_store.LocalBlobStore(str(tmp_path))

    first = blobs.put("rfq_abc", "datasheet.pdf", io.BytesIO(b"hello"))
    second = blobs.put("rfq_abc", "datasheet.pdf", io.BytesIO(b"hello"))

    assert first == second
    assert len(blob_files(tmp_path, "rfq_abc")) == 1


def test_two_files_with_the_same_name_do_not_collide(tmp_path):
    """They differ before the leaf, which is the whole point of the fan-out."""
    blobs = doc_store.LocalBlobStore(str(tmp_path))

    one = blobs.put("rfq_abc", "datasheet.pdf", io.BytesIO(b"first"))
    two = blobs.put("rfq_abc", "datasheet.pdf", io.BytesIO(b"second"))

    assert one.rel_path != two.rel_path
    assert len(blob_files(tmp_path, "rfq_abc")) == 2


def test_a_blob_reads_back(tmp_path):
    blobs = doc_store.LocalBlobStore(str(tmp_path))
    ref = blobs.put("rfq_abc", "datasheet.pdf", io.BytesIO(b"hello"))

    with blobs.open(ref) as fh:
        assert fh.read() == b"hello"


def test_deleting_a_blob_leaves_no_empty_directories_behind(tmp_path):
    blobs = doc_store.LocalBlobStore(str(tmp_path))
    ref = blobs.put("rfq_abc", "datasheet.pdf", io.BytesIO(b"hello"))

    blobs.delete(ref)

    assert blob_files(tmp_path, "rfq_abc") == []
    digest = ref.sha256
    assert not (tmp_path / "rfq-docs" / "rfq_abc" / digest[:2]).exists()


def test_deleting_a_blob_twice_is_not_an_error(tmp_path):
    """The record is the authority. A blob already gone is the state the
    caller was asking for."""
    blobs = doc_store.LocalBlobStore(str(tmp_path))
    ref = blobs.put("rfq_abc", "datasheet.pdf", io.BytesIO(b"hello"))

    blobs.delete(ref)
    blobs.delete(ref)


def test_no_leaf_name_may_carry_a_path_separator(tmp_path):
    """The blob path is built out of leaves — an rfq id, a digest and a file
    name — so containment holds by construction rather than by a second
    resolution check. This is the assertion that keeps it that way."""
    blobs = doc_store.LocalBlobStore(str(tmp_path))

    for bad in ["../evil.pdf", "a/b.pdf", r"a\b.pdf", "", ".", ".."]:
        with pytest.raises(ValueError):
            blobs.put("rfq_abc", bad, io.BytesIO(b"x"))


def test_no_rfq_id_may_carry_a_path_separator(tmp_path):
    blobs = doc_store.LocalBlobStore(str(tmp_path))

    with pytest.raises(ValueError):
        blobs.put("../../etc", "datasheet.pdf", io.BytesIO(b"x"))


# -- the store ----------------------------------------------------------------


def test_a_document_for_an_unknown_rfq_is_refused():
    """Half of I-E: no record can be created without an RFQ to hang it on."""
    store, _rfq_id = populated_store()

    with pytest.raises(KeyError):
        store.add_rfq_document(a_document("rfq_nope"))


def test_the_documents_come_back_in_the_order_they_arrived():
    store, rfq_id = populated_store()

    store.add_rfq_document(a_document(rfq_id, filename="one.pdf"))
    store.add_rfq_document(a_document(rfq_id, filename="two.pdf"))

    assert [d.filename for d in store.rfq_documents(rfq_id)] == ["one.pdf", "two.pdf"]


def test_removing_a_document_reports_whether_its_blob_is_now_unreferenced():
    """The rule I-D turns on, stated once, in the store — the route only acts
    on the answer."""
    store, rfq_id = populated_store()
    first = store.add_rfq_document(a_document(rfq_id))
    second = store.add_rfq_document(a_document(rfq_id))

    _removed, orphaned = store.remove_rfq_document(rfq_id, first.id)
    assert orphaned is False

    _removed, orphaned = store.remove_rfq_document(rfq_id, second.id)
    assert orphaned is True


def test_two_records_of_the_same_bytes_under_different_names_are_two_blobs():
    """Same hash, different leaf, so they are different files on disk and
    neither one keeps the other alive."""
    store, rfq_id = populated_store()
    first = store.add_rfq_document(a_document(rfq_id, filename="one.pdf"))
    store.add_rfq_document(a_document(rfq_id, filename="two.pdf"))

    _removed, orphaned = store.remove_rfq_document(rfq_id, first.id)

    assert orphaned is True


def test_removing_an_unknown_document_raises():
    store, rfq_id = populated_store()

    with pytest.raises(KeyError):
        store.remove_rfq_document(rfq_id, "rdoc_nope")


def test_a_contractor_document_cannot_be_added_to_a_frozen_package():
    """The freeze rule, unchanged, now guarding `documents` as well: vendors
    bid against a fixed revision."""
    store, rfq_id = populated_store()
    store.set_technical_package(
        rfq_id, revision="Rev. B", basis_of_design="basis", attachments=[]
    )
    store.freeze_package(rfq_id, by="lead@example.com")

    with pytest.raises(ValueError, match="frozen"):
        store.add_rfq_document(a_document(rfq_id))


def test_a_vendor_submission_is_not_blocked_by_a_frozen_package():
    """Bids arrive *after* the freeze. One collection serves both halves, and
    the freeze rule belongs to the contractor's half of it."""
    store, rfq_id = populated_store()
    store.set_technical_package(
        rfq_id, revision="Rev. B", basis_of_design="basis", attachments=[]
    )
    store.freeze_package(rfq_id, by="lead@example.com")

    stored = store.add_rfq_document(
        a_document(rfq_id, submitted_by_vendor_id="bdr_1")
    )

    assert stored.submitted_by_vendor_id == "bdr_1"


def test_the_package_lists_the_documents_the_contractor_issued():
    store, rfq_id = populated_store()
    store.set_technical_package(
        rfq_id, revision="Rev. B", basis_of_design="basis", attachments=[]
    )
    ours = store.add_rfq_document(a_document(rfq_id))
    store.add_rfq_document(a_document(rfq_id, submitted_by_vendor_id="bdr_1"))

    assert store.get_technical_package(rfq_id).documents == [ours.id]


def test_a_package_created_after_the_upload_still_lists_them():
    """`documents` is rebuilt from the records rather than accumulated, so the
    order the two arrive in cannot make the two disagree."""
    store, rfq_id = populated_store()
    ours = store.add_rfq_document(a_document(rfq_id))

    package = store.set_technical_package(
        rfq_id, revision="Rev. B", basis_of_design="basis", attachments=[]
    )

    assert package.documents == [ours.id]


def test_removing_a_document_takes_it_off_the_package():
    store, rfq_id = populated_store()
    store.set_technical_package(
        rfq_id, revision="Rev. B", basis_of_design="basis", attachments=[]
    )
    ours = store.add_rfq_document(a_document(rfq_id))

    store.remove_rfq_document(rfq_id, ours.id)

    assert store.get_technical_package(rfq_id).documents == []


# -- persistence --------------------------------------------------------------


def test_a_document_record_survives_a_round_trip(tmp_path):
    store, rfq_id = populated_store()
    store.add_rfq_document(a_document(rfq_id, category=EligibilityCategory.DRAWINGS))
    persistence.save(str(tmp_path), store)

    reloaded = persistence.load(str(tmp_path))

    [document] = reloaded.rfq_documents(rfq_id)
    assert document.filename == "datasheet.pdf"
    assert document.category is EligibilityCategory.DRAWINGS
    assert document.uploaded_by == "buyer@example.com"


def test_a_document_written_before_rfq_documents_existed_loads_as_none(tmp_path):
    store, rfq_id = populated_store()
    persistence.save(str(tmp_path), store)
    path = persistence.workflow_path(str(tmp_path))
    doc = json.loads(open(path, encoding="utf-8").read())
    doc.pop("rfq_documents", None)
    open(path, "w", encoding="utf-8").write(json.dumps(doc))

    reloaded = persistence.load(str(tmp_path))

    assert reloaded.rfq_documents(rfq_id) == []


def test_a_record_whose_rfq_is_gone_does_not_load(tmp_path):
    """The other half of I-E. Nothing deletes an RFQ today, so this is the
    end that can actually be exercised — and it is the end that would matter
    the day something does."""
    store, rfq_id = populated_store()
    store.add_rfq_document(a_document(rfq_id))
    persistence.save(str(tmp_path), store)
    path = persistence.workflow_path(str(tmp_path))
    doc = json.loads(open(path, encoding="utf-8").read())
    doc["rfqs"] = []
    open(path, "w", encoding="utf-8").write(json.dumps(doc))

    reloaded = persistence.load(str(tmp_path))

    assert reloaded.rfq_documents(rfq_id) == []


def test_removing_a_document_does_not_survive_on_disk(tmp_path):
    store, rfq_id = populated_store()
    document = store.add_rfq_document(a_document(rfq_id))
    persistence.save(str(tmp_path), store)
    store.remove_rfq_document(rfq_id, document.id)
    persistence.save(str(tmp_path), store)

    assert persistence.load(str(tmp_path)).rfq_documents(rfq_id) == []


# -- the upload route ---------------------------------------------------------


def test_several_files_arrive_in_one_call(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)

    r = upload(client, rfq_id, [
        ("files", ("datasheet.pdf", b"one", "application/pdf")),
        ("files", ("sld.dwg", b"two", "application/octet-stream")),
    ])

    assert r.status_code == 201, r.text
    stored = r.json()["documents"]
    assert [d["filename"] for d in stored] == ["datasheet.pdf", "sld.dwg"]
    assert all(d["id"].startswith("rdoc_") for d in stored)
    assert len(blob_files(tmp_path, rfq_id)) == 2


def test_an_upload_is_attributed_to_whoever_made_it(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)

    r = upload(client, rfq_id, [("files", ("a.pdf", b"one", "application/pdf"))])

    assert r.json()["documents"][0]["uploaded_by"] == ADMIN_EMAIL


def test_a_category_rides_on_every_file_in_the_call(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)

    r = upload(
        client, rfq_id,
        [("files", ("a.pdf", b"one", "application/pdf"))],
        category="Drawings",
    )

    assert r.json()["documents"][0]["category"] == "Drawings"


def test_an_unknown_category_is_refused(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)

    r = upload(
        client, rfq_id,
        [("files", ("a.pdf", b"one", "application/pdf"))],
        category="Whatever",
    )

    assert r.status_code == 422


def test_a_zip_is_expanded_and_each_member_stored(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)
    archive = _zip_bytes({
        "package/datasheet.pdf": b"one",
        "package/drawings/sld.dwg": b"two",
    })

    r = upload(client, rfq_id, [("files", ("package.zip", archive, "application/zip"))])

    assert r.status_code == 201, r.text
    stored = r.json()["documents"]
    assert [d["rel_path"] for d in stored] == [
        "package/datasheet.pdf", "package/drawings/sld.dwg"
    ]
    assert [d["filename"] for d in stored] == ["datasheet.pdf", "sld.dwg"]
    # The archive itself is not stored — its members are.
    assert not any(d["filename"] == "package.zip" for d in stored)
    assert len(blob_files(tmp_path, rfq_id)) == 2


def test_a_zip_member_escaping_its_directory_is_refused_by_name(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)
    archive = _zip_bytes({"../../evil.txt": b"pwned"})

    r = upload(client, rfq_id, [("files", ("package.zip", archive, "application/zip"))])

    assert r.status_code == 422
    assert "../../evil.txt" in r.json()["detail"]


def test_a_refused_member_stores_nothing_at_all(tmp_path, monkeypatch):
    """All-or-nothing within one archive: half an enquiry package on disk with
    no record of the other half is worse than a refusal."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)
    archive = _zip_bytes({"good.pdf": b"one", "../../evil.txt": b"pwned"})

    upload(client, rfq_id, [("files", ("package.zip", archive, "application/zip"))])

    assert blob_files(tmp_path, rfq_id) == []
    assert client.get(f"/api/workflow/rfqs/{rfq_id}").json()["documents"] == []
    assert not (tmp_path.parent / "evil.txt").exists()


def test_a_folders_relative_paths_survive(tmp_path, monkeypatch):
    """A folder upload is many files plus their `webkitRelativePath`s,
    positional. The path is what tells a reader where the file sat."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)

    r = upload(
        client, rfq_id,
        [
            ("files", ("datasheet.pdf", b"one", "application/pdf")),
            ("files", ("sld.dwg", b"two", "application/octet-stream")),
        ],
        paths=["enquiry/datasheets/datasheet.pdf", "enquiry/drawings/sld.dwg"],
    )

    assert r.status_code == 201, r.text
    stored = r.json()["documents"]
    assert [d["rel_path"] for d in stored] == [
        "enquiry/datasheets/datasheet.pdf", "enquiry/drawings/sld.dwg"
    ]
    assert [d["filename"] for d in stored] == ["datasheet.pdf", "sld.dwg"]


def test_a_relative_path_that_escapes_is_refused_by_name(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)

    r = upload(
        client, rfq_id,
        [("files", ("evil.txt", b"pwned", "text/plain"))],
        paths=["../../evil.txt"],
    )

    assert r.status_code == 422
    assert "../../evil.txt" in r.json()["detail"]


def test_paths_must_line_up_with_the_files(tmp_path, monkeypatch):
    """They are positional, so a short list would silently attach the wrong
    path to the wrong file."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)

    r = upload(
        client, rfq_id,
        [
            ("files", ("a.pdf", b"one", "application/pdf")),
            ("files", ("b.pdf", b"two", "application/pdf")),
        ],
        paths=["only/one.pdf"],
    )

    assert r.status_code == 422


def test_an_upload_with_no_files_is_refused(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)

    r = client.post(f"/api/workflow/rfqs/{rfq_id}/documents", files=[])

    assert r.status_code == 422


def test_an_upload_against_an_unknown_rfq_is_a_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    r = upload(client, "rfq_nope", [("files", ("a.pdf", b"one", "application/pdf"))])

    assert r.status_code == 404


def test_uploading_to_a_frozen_package_is_refused(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)
    client.put(f"/api/workflow/rfqs/{rfq_id}/technical-package", json={
        "revision": "Rev. B", "basis_of_design": "basis", "attachments": [],
    })
    client.post(f"/api/workflow/rfqs/{rfq_id}/technical-package/freeze", json={})

    r = upload(client, rfq_id, [("files", ("a.pdf", b"one", "application/pdf"))])

    assert r.status_code == 409
    assert blob_files(tmp_path, rfq_id) == []


def test_the_documents_ride_on_the_rfq_payload(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)
    upload(client, rfq_id, [("files", ("a.pdf", b"one", "application/pdf"))])

    body = client.get(f"/api/workflow/rfqs/{rfq_id}").json()

    assert [d["filename"] for d in body["documents"]] == ["a.pdf"]
    assert body["document_categories"] == [c.value for c in EligibilityCategory]


# -- the delete path, and the invariant it turns on ---------------------------


def test_the_same_bytes_twice_are_one_blob_and_two_records(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)

    upload(client, rfq_id, [("files", ("a.pdf", b"same bytes", "application/pdf"))])
    upload(client, rfq_id, [("files", ("a.pdf", b"same bytes", "application/pdf"))])

    body = client.get(f"/api/workflow/rfqs/{rfq_id}").json()
    assert len(body["documents"]) == 2
    assert len(blob_files(tmp_path, rfq_id)) == 1


def test_deleting_one_of_two_records_sharing_a_blob_leaves_the_blob(
    tmp_path, monkeypatch
):
    """The load-bearing case. Deleting unconditionally is data loss that a
    test with one document cannot see."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)
    upload(client, rfq_id, [("files", ("a.pdf", b"same bytes", "application/pdf"))])
    upload(client, rfq_id, [("files", ("a.pdf", b"same bytes", "application/pdf"))])
    first, second = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["documents"]

    r = client.delete(f"/api/workflow/rfqs/{rfq_id}/documents/{first['id']}")

    assert r.status_code == 204
    assert len(blob_files(tmp_path, rfq_id)) == 1
    remaining = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["documents"]
    assert [d["id"] for d in remaining] == [second["id"]]


def test_deleting_both_records_removes_the_blob(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)
    upload(client, rfq_id, [("files", ("a.pdf", b"same bytes", "application/pdf"))])
    upload(client, rfq_id, [("files", ("a.pdf", b"same bytes", "application/pdf"))])
    first, second = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["documents"]

    client.delete(f"/api/workflow/rfqs/{rfq_id}/documents/{first['id']}")
    client.delete(f"/api/workflow/rfqs/{rfq_id}/documents/{second['id']}")

    assert blob_files(tmp_path, rfq_id) == []
    assert client.get(f"/api/workflow/rfqs/{rfq_id}").json()["documents"] == []


def test_deleting_an_unknown_document_is_a_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)

    r = client.delete(f"/api/workflow/rfqs/{rfq_id}/documents/rdoc_nope")

    assert r.status_code == 404


def test_a_document_cannot_be_removed_from_a_frozen_package(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)
    upload(client, rfq_id, [("files", ("a.pdf", b"one", "application/pdf"))])
    client.put(f"/api/workflow/rfqs/{rfq_id}/technical-package", json={
        "revision": "Rev. B", "basis_of_design": "basis", "attachments": [],
    })
    client.post(f"/api/workflow/rfqs/{rfq_id}/technical-package/freeze", json={})
    document = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["documents"][0]

    r = client.delete(f"/api/workflow/rfqs/{rfq_id}/documents/{document['id']}")

    assert r.status_code == 409
    assert len(blob_files(tmp_path, rfq_id)) == 1


def test_the_sharing_guard_still_holds_after_a_reload(tmp_path, monkeypatch):
    """The only two-run case here, and it is the one worth keeping.

    The delete path decides whether to drop a blob by reading the records
    beside the one it removed. On the second run that list came off disk
    rather than out of the call that built it, so a `rfq_documents` key lost
    in serialization reads as "nothing else references this" and the delete
    takes a file another record still names. A single-run test sees none of
    that.
    """
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)
    upload(client, rfq_id, [("files", ("a.pdf", b"same bytes", "application/pdf"))])
    upload(client, rfq_id, [("files", ("a.pdf", b"same bytes", "application/pdf"))])

    import api.main as api_main
    fresh = TestClient(api_main.app)
    # Signing in rather than signing up: the account is already in `auth.json`,
    # and startup clears sessions, which is the other half of a real restart.
    signed = fresh.post(
        "/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert signed.status_code == 200, signed.text
    first = fresh.get(f"/api/workflow/rfqs/{rfq_id}").json()["documents"][0]
    fresh.delete(f"/api/workflow/rfqs/{rfq_id}/documents/{first['id']}")

    assert len(blob_files(tmp_path, rfq_id)) == 1
    assert len(fresh.get(f"/api/workflow/rfqs/{rfq_id}").json()["documents"]) == 1
