"""The contact directory over HTTP, and the address on a shortlist row.

Workbooks are built in memory, so this runs in CI with no fixture directory.
What matters most here is the negative space: the upload changes nothing about
the registry, and no shortlist entry stores the address it reports.
"""
import json

import openpyxl
from fastapi.testclient import TestClient

from tests.auth_helpers import ADMIN_EMAIL, signed_in_admin
from workflow import bidder_db, persistence
from workflow.models.bidder import ADNOC, ASTRA, Bidder


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return signed_in_admin(TestClient(api_main.app), tmp_path)


def _sheet(tmp_path, rows, name="vendors.xlsx", headers=("Vendor", "Email")):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(headers))
    for row in rows:
        ws.append(list(row))
    path = tmp_path / name
    wb.save(path)
    return path


def _upload(client, path):
    with open(path, "rb") as handle:
        return client.post(
            "/api/workflow/vendor-contacts",
            files={"file": (path.name, handle,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )


def _an_rfq_with_one_invited_vendor(client, tmp_path, vendor_name, vendor_id=None):
    """A project, an item, an RFQ and one shortlist entry — the shortest path
    to a row that can carry an address."""
    project = client.post("/api/workflow/projects", json={
        "name": "Habshan", "code": "HAB-01", "client": "ADNOC",
        "location": "Habshan", "live_period_start": "2026-01-01",
        "live_period_end": "2026-12-31",
    }).json()
    item = client.post(f"/api/workflow/projects/{project['id']}/items", json={
        "item_type": "cable", "description": "LV power cable", "qty": 1,
        "uom": "lot", "discipline": "CABLES - LV POWER DISTRIBUTION",
        "estimated_value_aed": 100000,
    }).json()
    rfq = client.post("/api/workflow/rfqs", json={
        "project_id": project["id"], "item_ids": [item["id"]],
        "reference": "HAB-RFQ-001",
        "package": "LV cable", "discipline": "CABLES - LV POWER DISTRIBUTION",
        "value_estimate_aed": 100000,
    }).json()
    rfq_id = rfq["id"]
    if vendor_id:
        body = {"vendor_id": vendor_id}
    else:
        # The free-text path needs both alongside the name — the store raises
        # IncompleteShortlistEntry otherwise, since eligibility can only be
        # judged against a registry record.
        body = {
            "vendor_name": vendor_name,
            "prequal_status": "Qualified",
            "scope_code_fit": True,
        }
    response = client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json=body)
    assert response.status_code == 201, response.text
    return rfq_id


def test_an_upload_reports_what_it_read(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    bidder_db.replace_all(str(tmp_path), [
        Bidder(id="bdr_1", name="DANWAY ABU DHABI L.L.C", approved_by=[ADNOC]),
    ])
    path = _sheet(tmp_path, [
        ("DANWAY ABU DHABI L.L.C", "sales@danway.example; bids@danway.example"),
        ("BIN SARI SPECIALIZED TECHNOLOGIES", "bids@binsari.example"),
    ])
    response = _upload(client, path)
    assert response.status_code == 200
    assert response.json()["summary"] == {
        "parsed": 2, "stored": 2, "addresses": 3, "matched": 1,
    }


def test_a_workbook_with_no_email_column_is_refused_with_the_parsers_sentence(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, monkeypatch)
    path = _sheet(tmp_path, [("DANWAY ABU DHABI L.L.C", "Abu Dhabi")],
                  headers=("Vendor", "City"))
    response = _upload(client, path)
    assert response.status_code == 422
    assert "Email" in response.json()["detail"]


def test_the_upload_changes_nothing_about_the_registry(tmp_path, monkeypatch):
    """The registry is organisation-wide and arrives whole from its own import.
    A sheet of addresses must not enrol a company or grant an approval."""
    client = _client(tmp_path, monkeypatch)
    bidder_db.replace_all(str(tmp_path), [
        Bidder(id="bdr_1", name="DANWAY ABU DHABI L.L.C", approved_by=[ASTRA]),
    ])
    before = [b.model_dump(mode="json") for b in bidder_db.list_all(str(tmp_path))]
    _upload(client, _sheet(tmp_path, [
        ("DANWAY ABU DHABI L.L.C", "sales@danway.example"),
        ("A COMPANY NOBODY HAS REGISTERED", "hello@nobody.example"),
    ]))
    after = [b.model_dump(mode="json") for b in bidder_db.list_all(str(tmp_path))]
    assert after == before


def test_reading_the_directory_names_who_loaded_it(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _upload(client, _sheet(tmp_path, [("DANWAY ABU DHABI L.L.C", "sales@danway.example")]))
    body = client.get("/api/workflow/vendor-contacts").json()
    assert body["count"] == 1
    assert body["uploaded_by"] == ADMIN_EMAIL
    assert body["source_document"] == "vendors.xlsx"
    assert body["contacts"][0]["emails"] == ["sales@danway.example"]


def test_an_empty_directory_reads_as_a_count_of_zero_and_no_uploader(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, monkeypatch)
    body = client.get("/api/workflow/vendor-contacts").json()
    assert body["count"] == 0
    assert body["uploaded_by"] is None


def test_a_second_upload_replaces_the_directory(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _upload(client, _sheet(tmp_path, [
        ("DANWAY ABU DHABI L.L.C", "sales@danway.example"),
        ("BIN SARI SPECIALIZED TECHNOLOGIES", "bids@binsari.example"),
    ], name="first.xlsx"))
    _upload(client, _sheet(tmp_path, [
        ("BIN SARI SPECIALIZED TECHNOLOGIES", "bids@binsari.example"),
    ], name="second.xlsx"))
    names = [c["vendor_name"] for c in
             client.get("/api/workflow/vendor-contacts").json()["contacts"]]
    assert names == ["BIN SARI SPECIALIZED TECHNOLOGIES"]


def test_a_shortlist_row_carries_the_address_held_for_its_vendor(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, monkeypatch)
    rfq_id = _an_rfq_with_one_invited_vendor(client, tmp_path, "DANWAY ABU DHABI L.L.C")
    _upload(client, _sheet(tmp_path, [
        ("danway  abu dhabi l.l.c", "sales@danway.example"),
    ]))
    (row,) = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["shortlist"]
    assert row["email"] == ["sales@danway.example"]


def test_a_vendor_with_no_row_reports_no_address_rather_than_an_empty_list(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, monkeypatch)
    rfq_id = _an_rfq_with_one_invited_vendor(client, tmp_path, "BIN SARI SPECIALIZED TECHNOLOGIES")
    _upload(client, _sheet(tmp_path, [("DANWAY ABU DHABI L.L.C", "sales@danway.example")]))
    (row,) = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["shortlist"]
    assert row["email"] is None


def test_a_spelling_the_fold_does_not_reach_reports_no_address(tmp_path, monkeypatch):
    """`L.L.C` against `LLC` is a miss, and that is the correct outcome.
    Widening the fold is how an enquiry reaches the wrong company."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = _an_rfq_with_one_invited_vendor(client, tmp_path, "DANWAY ABU DHABI L.L.C")
    _upload(client, _sheet(tmp_path, [("Danway Abu Dhabi LLC", "sales@danway.example")]))
    (row,) = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["shortlist"]
    assert row["email"] is None


def test_a_hand_typed_vendor_gets_an_address_too(tmp_path, monkeypatch):
    """The upside of name-keying: a vendor on nobody's register still resolves.
    An id-keyed directory could never reach them."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = _an_rfq_with_one_invited_vendor(client, tmp_path, "A COMPANY NOBODY HAS REGISTERED")
    _upload(client, _sheet(tmp_path, [("A COMPANY NOBODY HAS REGISTERED", "hello@nobody.example")]))
    (row,) = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["shortlist"]
    assert row["vendor_id"] is None
    assert row["email"] == ["hello@nobody.example"]


def test_no_shortlist_entry_stores_the_address_it_reports(tmp_path, monkeypatch):
    """V-F. An absence assertion, so it passes the moment it is written —
    verify it by adding `email` to `ShortlistEntry` and watching it go red.
    Third instance of this discipline in this repository."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = _an_rfq_with_one_invited_vendor(client, tmp_path, "DANWAY ABU DHABI L.L.C")
    _upload(client, _sheet(tmp_path, [("DANWAY ABU DHABI L.L.C", "sales@danway.example")]))
    client.get(f"/api/workflow/rfqs/{rfq_id}")
    with open(persistence.workflow_path(str(tmp_path)), encoding="utf-8") as handle:
        raw = handle.read()
    assert "sales@danway.example" not in raw
    document = json.loads(raw)
    for entry in document.get("shortlists", []):
        assert "email" not in entry


def test_correcting_the_sheet_corrects_every_shortlist_with_nothing_rewritten(
    tmp_path, monkeypatch
):
    """The whole argument for deriving on read: re-upload, and every shortlist
    in the system is right."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = _an_rfq_with_one_invited_vendor(client, tmp_path, "DANWAY ABU DHABI L.L.C")
    _upload(client, _sheet(tmp_path, [("DANWAY ABU DHABI L.L.C", "wrong@danway.example")],
                           name="first.xlsx"))
    _upload(client, _sheet(tmp_path, [("DANWAY ABU DHABI L.L.C", "right@danway.example")],
                           name="second.xlsx"))
    (row,) = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["shortlist"]
    assert row["email"] == ["right@danway.example"]


def test_the_upload_needs_a_session(tmp_path, monkeypatch):
    """Workflow routes are not on PUBLIC_PATHS and must not be."""
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    anonymous = TestClient(api_main.app)
    assert anonymous.get("/api/workflow/vendor-contacts").status_code == 401
