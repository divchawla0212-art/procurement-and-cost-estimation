"""An item's four vendor lists, over HTTP.

Two of them are uploads. Both are Approved Vendor List exports in the same
format, so the route runs `avl_import.parse_avl` rather than a parser of its
own. What matters here is what the route does with the result: narrow it to the
item's discipline, store it as that item's list, and **change nothing about the
registry**.

Two of them are curated — built one vendor at a time, by hand or from a
suggestion. What matters there is the negative space: a hand-added vendor never
acquires a registry link, and the suggestion route stores nothing at all.

Workbooks are built in memory with `openpyxl`, the way `test_avl_import.py`'s
non-gated tests do, and the suggestion route is served a `MockLLMClient`, so
these run in CI with no fixture directory and no provider key.
"""
import json

import openpyxl
import pytest
from fastapi.testclient import TestClient

from shared.llm.mock_client import MockLLMClient
from tests.auth_helpers import ADMIN_EMAIL, signed_in_admin
from workflow import bidder_db
from workflow.avl_import import parse_avl
from workflow.models.bidder import Bidder

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


# -- adding a vendor by hand --------------------------------------------------


def add_vendor(client, project_id, item_id, **body):
    body.setdefault("vendor_name", "Hand Added Co")
    return client.post(
        f"/api/workflow/projects/{project_id}/items/{item_id}/vendors", json=body
    )


def lists_for(client, project_id, item_id) -> dict:
    detail = client.get(f"/api/workflow/projects/{project_id}").json()
    return detail["item_vendor_lists"][item_id]


def test_a_vendor_added_by_hand_lands_on_the_manual_list(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)

    r = add_vendor(client, project_id, item_id, vendor_name="Gulf Cable Works LLC")

    assert r.status_code == 201, r.text
    assert r.json()["source"] == "Manual"
    lists = lists_for(client, project_id, item_id)
    assert [e["vendor_name"] for e in lists["Manual"]] == ["Gulf Cable Works LLC"]


def test_a_blank_vendor_name_is_refused(tmp_path, monkeypatch):
    """A vendor with no name cannot be invited later, and storing one would put
    a row on screen that no action can be taken on."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)

    assert add_vendor(client, project_id, item_id, vendor_name="   ").status_code == 422
    assert lists_for(client, project_id, item_id)["Manual"] == []


def test_a_vendor_name_is_stored_trimmed(tmp_path, monkeypatch):
    """Two rows differing only by a trailing space are one company, and the
    screen cannot show the difference."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)

    body = add_vendor(client, project_id, item_id, vendor_name="  Ducab  ").json()

    assert body["vendor_name"] == "Ducab"


def test_a_hand_added_vendor_never_links_to_the_registry(tmp_path, monkeypatch):
    """Even when a registry row has that exact name. A lookup here would attach
    a real company's approvals to whatever somebody typed, and this repository
    has twice recorded name matching as a shipped defect. If the vendor really
    is in the registry, the available-vendor card is where to find them."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    bidder_db.replace_all(str(tmp_path), [
        Bidder(id="bdr_10000045", name="OMEIR BIN YOUSSEF & SONS LLC",
               trade_categories=[LV_CABLE], approved_by=["ADNOC", "Astra"]),
    ])

    body = add_vendor(client, project_id, item_id,
                      vendor_name="OMEIR BIN YOUSSEF & SONS LLC").json()

    assert body["vendor_id"] is None


def test_a_hand_added_vendor_creates_no_bidder(tmp_path, monkeypatch):
    """The registry arrives whole from its own import. Typing a name against
    one item must not enrol a company organisation-wide."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    before = client.get("/api/workflow/bidders").json()

    add_vendor(client, project_id, item_id)

    assert client.get("/api/workflow/bidders").json() == before


def test_a_hand_added_vendor_is_attributed_and_says_how_it_arrived(tmp_path, monkeypatch):
    """Every row on this screen is attributable however it arrived — the
    uploaded ones name their document, and these name the act."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)

    body = add_vendor(client, project_id, item_id).json()

    assert body["uploaded_by"] == ADMIN_EMAIL
    assert body["source_document"] == "added by hand"


def test_a_note_rides_on_the_provenance_rather_than_a_new_field(tmp_path, monkeypatch):
    """`ItemVendorEntry` gains nothing this phase: a hand-added company is
    already exactly what the record describes. A note is *why this row is
    here*, which is what `source_document` already means."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)

    body = add_vendor(client, project_id, item_id, note="Met at ADIPEC").json()

    assert body["source_document"] == "added by hand: Met at ADIPEC"


def test_the_trade_categories_default_to_the_items_discipline(tmp_path, monkeypatch):
    """So a hand-added vendor sits in the same shape as an uploaded one, scoped
    to this item rather than claiming the company's whole trade."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client, discipline="Cables")

    body = add_vendor(client, project_id, item_id).json()

    assert LV_CABLE in body["trade_categories"]


def test_an_uploaded_source_cannot_be_added_to_one_vendor_at_a_time(tmp_path, monkeypatch):
    """It would leave the list no longer matching the export it came from."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)

    r = add_vendor(client, project_id, item_id, source="Client")

    assert r.status_code == 422
    assert "Client" in r.text


def test_adding_to_an_item_from_another_project_is_refused(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    other_project, _ = make_item(client)

    r = add_vendor(client, other_project, item_id)

    assert r.status_code == 404
    assert item_id in r.text


# -- removing one --------------------------------------------------------------


def test_a_hand_added_vendor_can_be_removed(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    entry_id = add_vendor(client, project_id, item_id).json()["id"]

    r = client.delete(
        f"/api/workflow/projects/{project_id}/items/{item_id}/vendors/{entry_id}"
    )

    assert r.status_code == 204, r.text
    assert lists_for(client, project_id, item_id)["Manual"] == []


def test_removing_leaves_the_row_beside_it_alone(tmp_path, monkeypatch):
    """Removal addresses the entry by id, never by name or position — two
    suppliers can share a trading name."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    first = add_vendor(client, project_id, item_id, vendor_name="Same Name").json()
    add_vendor(client, project_id, item_id, vendor_name="Same Name")

    client.delete(
        f"/api/workflow/projects/{project_id}/items/{item_id}/vendors/{first['id']}"
    )

    remaining = lists_for(client, project_id, item_id)["Manual"]
    assert [e["id"] for e in remaining] == [
        e["id"] for e in remaining if e["id"] != first["id"]
    ]
    assert len(remaining) == 1


def test_deleting_an_uploaded_row_is_refused(tmp_path, monkeypatch):
    """An uploaded row is part of a document. Correcting it means re-uploading
    the corrected export, not editing the copy."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    upload(client, tmp_path, project_id, item_id)
    entry_id = lists_for(client, project_id, item_id)["Client"][0]["id"]

    r = client.delete(
        f"/api/workflow/projects/{project_id}/items/{item_id}/vendors/{entry_id}"
    )

    assert r.status_code == 422
    assert "Client" in r.text
    assert len(lists_for(client, project_id, item_id)["Client"]) == 1


def test_removing_an_unknown_entry_is_not_found(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)

    r = client.delete(
        f"/api/workflow/projects/{project_id}/items/{item_id}/vendors/ive_nope"
    )

    assert r.status_code == 404


# -- suggesting some -----------------------------------------------------------
#
# `MockLLMClient` throughout: `_client` sets `LLM_PROVIDER=mock`, so the route's
# own `get_client()` already returns one, and a test wanting a specific answer
# monkeypatches the name the route resolves. No test here needs a provider key,
# which is the rule CLAUDE.md sets for CI.


def serve(monkeypatch, response):
    import api.workflow_routes as routes

    stub = MockLLMClient(response=response)
    monkeypatch.setattr(routes, "get_client", lambda *a, **k: stub)
    return stub


def suggestions(client, project_id, item_id):
    return client.post(
        f"/api/workflow/projects/{project_id}/items/{item_id}/vendor-suggestions"
    )


def test_the_suggestion_route_stores_nothing(tmp_path, monkeypatch):
    """The document is byte-identical before and after. Same shape as
    `/rfqs/extract`: a suggestion the reader rejects leaves nothing behind, and
    one they accept is recorded as *their* act."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    serve(monkeypatch, {"vendors": [{"name": "Ducab"}, {"name": "Jeddah Cables"}]})
    before = (tmp_path / "workflow.json").read_bytes()

    r = suggestions(client, project_id, item_id)

    assert r.status_code == 200, r.text
    assert [v["name"] for v in r.json()["vendors"]] == ["Ducab", "Jeddah Cables"]
    assert (tmp_path / "workflow.json").read_bytes() == before


def test_a_suggestion_carries_no_approval_and_no_registry_link(tmp_path, monkeypatch):
    """A model-named company claiming the client's approval is the failure
    `test_no_invented_vendor_claims_the_clients_approval` guards, arriving
    through a new door."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    serve(monkeypatch, {"vendors": [
        {"name": "Ducab", "approved_by": ["ADNOC"], "vendor_id": "bdr_1"},
    ]})

    vendor = suggestions(client, project_id, item_id).json()["vendors"][0]

    assert "approved_by" not in vendor
    assert "vendor_id" not in vendor
    assert "prequal_status" not in vendor


def test_names_already_on_the_item_are_not_suggested_again(tmp_path, monkeypatch):
    """Across every source, uploaded and curated alike — the reader's question
    is "who else", not "who else that I did not upload"."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    upload(client, tmp_path, project_id, item_id)
    add_vendor(client, project_id, item_id, vendor_name="Gulf Cable Works LLC")
    serve(monkeypatch, {"vendors": [
        {"name": "omeir bin youssef & sons llc"},
        {"name": "GULF CABLE WORKS LLC"},
        {"name": "Somebody Else"},
    ]})

    body = suggestions(client, project_id, item_id).json()

    assert [v["name"] for v in body["vendors"]] == ["Somebody Else"]


def test_a_provider_failure_is_reported_rather_than_shown_as_no_vendors(
    tmp_path, monkeypatch
):
    """An outage and "no such companies exist" must not look the same."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)

    class Failing:
        def classify_structure(self, **_):
            raise RuntimeError("provider is down")

    import api.workflow_routes as routes
    monkeypatch.setattr(routes, "get_client", lambda *a, **k: Failing())

    r = suggestions(client, project_id, item_id)

    assert r.status_code == 502
    assert "vendors" not in r.json()


def test_suggesting_for_an_item_from_another_project_is_refused(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    other_project, _ = make_item(client)
    serve(monkeypatch, {"vendors": []})

    assert suggestions(client, other_project, item_id).status_code == 404


def test_a_suggestion_can_then_be_added_by_hand(tmp_path, monkeypatch):
    """Adding is a second, attributed call — never a bulk accept, and the row
    stays labelled `Suggested` so a later reader can see the name originated
    with a model."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    serve(monkeypatch, {"vendors": [{"name": "Ducab", "basis": "Well-known UAE maker"}]})
    vendor = suggestions(client, project_id, item_id).json()["vendors"][0]

    r = add_vendor(client, project_id, item_id, source="Suggested",
                   vendor_name=vendor["name"], note=vendor["basis"])

    assert r.status_code == 201, r.text
    stored = lists_for(client, project_id, item_id)["Suggested"]
    assert [e["vendor_name"] for e in stored] == ["Ducab"]
    assert stored[0]["source_document"] == "suggested by the model: Well-known UAE maker"
    assert stored[0]["vendor_id"] is None


def test_an_accepted_suggestion_is_excluded_from_the_next_ask(tmp_path, monkeypatch):
    """The two curated sources are both on the item's lists, so the second ask
    already knows about the first answer."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    add_vendor(client, project_id, item_id, source="Suggested", vendor_name="Ducab")
    stub = serve(monkeypatch, {"vendors": [{"name": "Ducab"}, {"name": "Another Co"}]})

    body = suggestions(client, project_id, item_id).json()

    assert "Ducab" in stub.last_call["context_text"]
    assert [v["name"] for v in body["vendors"]] == ["Another Co"]
