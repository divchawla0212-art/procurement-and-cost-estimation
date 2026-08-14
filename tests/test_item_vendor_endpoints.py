"""Uploading the client and Astra vendor lists against one item.

Both uploads are Approved Vendor List exports in the same format, so the route
runs `avl_import.parse_avl` rather than a parser of its own. What matters here
is what the route does with the result: narrow it to the item's discipline,
store it as that item's list, and **change nothing about the registry**.

Workbooks are built in memory with `openpyxl`, the way `test_avl_import.py`'s
non-gated tests do, so these run in CI with no fixture directory.
"""
import openpyxl
from fastapi.testclient import TestClient

from tests.auth_helpers import signed_in_admin
from workflow import bidder_db
from workflow.avl_import import parse_avl

HEADERS = [
    "Product Group Number",
    "Product Group Description",
    "Vendor Number",
    "Vendor Name",
    "Manufacture Number",
    "Manufacture name",
    "Manufacture Country",
    "Remarks",
]

# Real product group descriptions, quoted exactly, so the discipline expansion
# resolves against the vocabulary the export itself speaks.
LV_CABLE = "CABLES - LV POWER DISTRIBUTION"
GAS_TURBINE = "GENERATOR POWER-GAS TURBINE DRIVEN"

ROWS = [
    ["320603", LV_CABLE, "10000045", "OMEIR BIN YOUSSEF & SONS LLC",
     "20006636", "ABB NORSK KABEL AS", "Norway", ""],
    ["321835", GAS_TURBINE, "10000999", "TURBINE POWER LLC",
     "20009999", "SIEMENS ENERGY", "Germany", ""],
]


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return signed_in_admin(TestClient(api_main.app), tmp_path)


def workbook(tmp_path, headers=HEADERS, rows=ROWS, name="avl.xlsx") -> str:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    path = str(tmp_path / name)
    wb.save(path)
    return path


def make_item(client: TestClient, discipline: str = "Cables") -> tuple[str, str]:
    r = client.post("/api/workflow/projects", json={
        "name": "Haliba Field Development", "code": "HAL",
        "client": "Al Dhafra Petroleum", "location": "Haliba field, UAE",
        "live_period_start": "2026-01-01", "live_period_end": "2029-12-31",
    })
    project_id = r.json()["id"]
    r = client.post(f"/api/workflow/projects/{project_id}/items", json={
        "item_type": "HV cable", "description": "11 kV, 3-core",
        "qty": 1200, "uom": "m", "discipline": discipline,
        "estimated_value_aed": 900000,
    })
    return project_id, r.json()["id"]


def upload(client, tmp_path, project_id, item_id, *, source="Client", path=None):
    path = path or workbook(tmp_path)
    with open(path, "rb") as handle:
        return client.post(
            f"/api/workflow/projects/{project_id}/items/{item_id}/vendor-list",
            params={"source": source},
            files={"file": ("avl.xlsx", handle, "application/vnd.ms-excel")},
        )


def test_an_upload_stores_only_the_vendors_for_the_item_discipline(tmp_path, monkeypatch):
    """The whole point. The real client export is 1 346 vendors and an item is
    one line of equipment."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client, discipline="Cables")

    r = upload(client, tmp_path, project_id, item_id)

    assert r.status_code == 200, r.text
    body = r.json()
    assert [e["vendor_name"] for e in body["entries"]] == ["OMEIR BIN YOUSSEF & SONS LLC"]
    assert body["summary"] == {"parsed": 2, "kept": 1, "linked": 0}


def test_an_upload_creates_no_bidder_and_grants_no_approval(tmp_path, monkeypatch):
    """Section 1 of the design, as an assertion. These are genuine exports, so
    the objection is not that the evidence is weak — it is that an upload
    attached to one item must not decide what a company is approved for across
    every project."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    before = client.get("/api/workflow/bidders").json()

    upload(client, tmp_path, project_id, item_id)

    assert client.get("/api/workflow/bidders").json() == before


def test_a_vendor_the_registry_holds_is_linked(tmp_path, monkeypatch):
    """`parse_avl` keys bidders `bdr_<vendor number>`, the same scheme the
    registry was built with, so matching is an exact id lookup."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    # Seeded the way the registry is really built — `parse_avl` over the same
    # export — so the ids match by construction rather than by a literal that
    # could drift from the vendor number in the sheet.
    bidder_db.replace_all(str(tmp_path), parse_avl(workbook(tmp_path)))

    body = upload(client, tmp_path, project_id, item_id).json()

    assert body["entries"][0]["vendor_id"] == "bdr_10000045"
    assert body["summary"]["linked"] == 1


def test_a_second_upload_of_a_source_replaces_it(tmp_path, monkeypatch):
    """A correction, not an accumulation."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    upload(client, tmp_path, project_id, item_id)

    revised = workbook(tmp_path, rows=[
        ["320603", LV_CABLE, "10000777", "REPLACEMENT CABLES LLC",
         "20000777", "SOME MAKER", "Italy", ""],
    ], name="revised.xlsx")
    body = upload(client, tmp_path, project_id, item_id, path=revised).json()

    assert [e["vendor_name"] for e in body["entries"]] == ["REPLACEMENT CABLES LLC"]


def test_the_two_sources_are_stored_apart(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)

    upload(client, tmp_path, project_id, item_id, source="Client")
    upload(client, tmp_path, project_id, item_id, source="Astra")

    detail = client.get(f"/api/workflow/projects/{project_id}").json()
    lists = detail["item_vendor_lists"][item_id]
    assert [e["vendor_name"] for e in lists["Client"]] == ["OMEIR BIN YOUSSEF & SONS LLC"]
    assert [e["vendor_name"] for e in lists["Astra"]] == ["OMEIR BIN YOUSSEF & SONS LLC"]


def test_an_unknown_source_is_refused(tmp_path, monkeypatch):
    """`Contractor` was an earlier name for the second list. A typo must not
    quietly create a third one."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)

    r = upload(client, tmp_path, project_id, item_id, source="Contractor")

    assert r.status_code == 422


def test_an_item_from_another_project_is_refused(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    other_project, _ = make_item(client)

    r = upload(client, tmp_path, other_project, item_id)

    assert r.status_code == 404
    assert item_id in r.text


def test_a_workbook_with_no_usable_header_is_refused(tmp_path, monkeypatch):
    """`parse_avl` locates columns by header and raises when a required one is
    missing — the reason being that a re-export with a reordered column would
    otherwise be silently wrong about every company in it."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    bad = workbook(tmp_path, headers=["Nope", "Also nope"], rows=[["a", "b"]],
                   name="bad.xlsx")

    r = upload(client, tmp_path, project_id, item_id, path=bad)

    assert r.status_code == 422


def test_a_discipline_matching_nothing_stores_nothing_and_says_so(tmp_path, monkeypatch):
    """Items predating the vocabulary carry free text like "1". Falling back to
    the whole upload would attach the entire export to one item."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client, discipline="1")

    body = upload(client, tmp_path, project_id, item_id).json()

    assert body["entries"] == []
    assert body["summary"] == {"parsed": 2, "kept": 0, "linked": 0}
