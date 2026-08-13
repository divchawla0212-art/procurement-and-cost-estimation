"""API tests for the bidder registry and the candidate view.

Like every other `/api/workflow/*` path these are absent from
`middleware.PUBLIC_PATHS`, so each test signs in first.

What these tests are *not* for: re-asserting the store's rules. The delete
guard and the override requirement are the store's, and they are tested there.
What matters here is that the route decides nothing — it maps an exception to a
status code and passes the store's own sentence through. A guard implemented
here rather than in the store would be a read outside `locked_update`, which is
the defect class CLAUDE.md records this repository shipping twice.
"""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from tests.auth_helpers import ADMIN_EMAIL, signed_in_admin


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return signed_in_admin(TestClient(api_main.app), tmp_path)


def create_rfq(client: TestClient) -> str:
    r = client.post("/api/workflow/projects", json={
        "name": "Haliba Field Development",
        "code": "HAL",
        "client": "Al Dhafra Petroleum",
        "location": "Haliba field, UAE",
        "live_period_start": "2026-01-01",
        "live_period_end": "2029-12-31",
    })
    project_id = r.json()["id"]
    r = client.post(f"/api/workflow/projects/{project_id}/items", json={
        "item_type": "LV switchgear",
        "description": "LV switchboards and MCCs",
        "qty": 6,
        "uom": "ea",
        "discipline": "Electrical",
        "estimated_value_aed": 4000000,
    })
    item_id = r.json()["id"]
    r = client.post("/api/workflow/rfqs", json={
        "project_id": project_id,
        "item_ids": [item_id],
        "reference": "ADP-RFQ-2026-014",
        "package": "LV switchgear",
        "discipline": "Electrical",
        "value_estimate_aed": 4000000,
    })
    return r.json()["id"]


def create_bidder(client: TestClient, **overrides) -> dict:
    body = {
        "name": "Al Munara Switchgear LLC",
        "country": "United Arab Emirates",
        "trade_categories": ["Electrical"],
        "prequal_status": "Approved",
        "prequal_expires_on": "2027-03-31",
        **overrides,
    }
    r = client.post("/api/workflow/bidders", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# -- the registry ------------------------------------------------------------


def test_the_registry_starts_empty(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.get("/api/workflow/bidders")
    assert r.status_code == 200
    assert r.json()["bidders"] == []


def test_a_created_bidder_appears_on_the_roster(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    bidder = create_bidder(client)

    roster = client.get("/api/workflow/bidders").json()["bidders"]
    assert [b["id"] for b in roster] == [bidder["id"]]
    assert roster[0]["effective_prequal"] == "Approved"
    # Counted here so no screen re-derives it from a list it only partly holds
    # — the same reasoning as `list_projects`' item and RFQ counts.
    assert roster[0]["invited_count"] == 0


def test_the_roster_reports_a_lapsed_approval_as_expired(tmp_path, monkeypatch):
    """The stored status is still "Approved". Only the derived one moves, which
    is the whole reason it is derived."""
    client = _client(tmp_path, monkeypatch)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    create_bidder(client, prequal_expires_on=yesterday)

    roster = client.get("/api/workflow/bidders").json()["bidders"]
    assert roster[0]["prequal_status"] == "Approved"
    assert roster[0]["effective_prequal"] == "Expired"


def test_one_bidder_carries_the_rfqs_that_invited_them(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    bidder = create_bidder(client)
    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": bidder["id"]})

    r = client.get(f"/api/workflow/bidders/{bidder['id']}")
    assert r.status_code == 200
    assert r.json()["invited_by"] == ["ADP-RFQ-2026-014"]


def test_an_unknown_bidder_is_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/workflow/bidders/bdr_nope").status_code == 404


def test_a_patch_is_partial(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    bidder = create_bidder(client, notes="Preferred on HAL")

    r = client.patch(
        f"/api/workflow/bidders/{bidder['id']}",
        json={"prequal_status": "Suspended"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["prequal_status"] == "Suspended"
    assert r.json()["notes"] == "Preferred on HAL"


def test_a_patch_with_an_unknown_status_is_422(tmp_path, monkeypatch):
    """Refused by the request model, so the store never sees it."""
    client = _client(tmp_path, monkeypatch)
    bidder = create_bidder(client)
    r = client.patch(
        f"/api/workflow/bidders/{bidder['id']}",
        json={"prequal_status": "Sort of approved"},
    )
    assert r.status_code == 422


def test_patching_an_unknown_bidder_is_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.patch("/api/workflow/bidders/bdr_nope", json={"country": "Oman"})
    assert r.status_code == 404


def test_an_unreferenced_bidder_can_be_deleted(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    bidder = create_bidder(client)
    assert client.delete(f"/api/workflow/bidders/{bidder['id']}").status_code == 204
    assert client.get("/api/workflow/bidders").json()["bidders"] == []


def test_deleting_a_shortlisted_bidder_is_409_and_names_the_rfq(tmp_path, monkeypatch):
    """409, not 422: the bidder exists and the request is well-formed. The
    workflow is what says no."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    bidder = create_bidder(client)
    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": bidder["id"]})

    r = client.delete(f"/api/workflow/bidders/{bidder['id']}")
    assert r.status_code == 409
    assert "ADP-RFQ-2026-014" in r.json()["detail"]


def test_deleting_an_unknown_bidder_is_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.delete("/api/workflow/bidders/bdr_nope").status_code == 404


# -- candidates --------------------------------------------------------------


def test_candidates_evaluate_every_bidder_against_this_rfq(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    fits = create_bidder(client)
    misfits = create_bidder(
        client, name="Sandstone Piping Industries", trade_categories=["Piping"]
    )

    r = client.get(f"/api/workflow/rfqs/{rfq_id}/candidates")
    assert r.status_code == 200
    by_id = {c["bidder"]["id"]: c for c in r.json()["candidates"]}

    assert by_id[fits["id"]]["suitability"]["scope_fit"] is True
    assert by_id[fits["id"]]["suitability"]["eligible"] is True
    assert by_id[misfits["id"]]["suitability"]["scope_fit"] is False
    # A mismatch is a caution, so they stay invitable.
    assert by_id[misfits["id"]]["suitability"]["eligible"] is True
    assert by_id[misfits["id"]]["suitability"]["cautions"]


def test_candidates_flag_who_is_already_shortlisted(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    invited = create_bidder(client)
    spare = create_bidder(client, name="Northwind Valve Works")
    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": invited["id"]})

    by_id = {
        c["bidder"]["id"]: c
        for c in client.get(f"/api/workflow/rfqs/{rfq_id}/candidates").json()["candidates"]
    }
    assert by_id[invited["id"]]["shortlisted"] is True
    assert by_id[spare["id"]]["shortlisted"] is False


def test_candidates_for_an_unknown_rfq_is_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/workflow/rfqs/rfq_nope/candidates").status_code == 404


def test_a_blocked_bidder_reports_the_blocker_on_the_candidate_list(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    blocked = create_bidder(
        client, on_hold=True, hold_reason="Unresolved dispute on HAL-19"
    )

    candidate = next(
        c for c in client.get(f"/api/workflow/rfqs/{rfq_id}/candidates").json()["candidates"]
        if c["bidder"]["id"] == blocked["id"]
    )
    assert candidate["suitability"]["eligible"] is False
    assert "Unresolved dispute on HAL-19" in " ".join(candidate["suitability"]["blockers"])


# -- inviting from the registry ----------------------------------------------


def test_inviting_a_registry_bidder_snapshots_the_registry(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    bidder = create_bidder(client)

    r = client.post(
        f"/api/workflow/rfqs/{rfq_id}/shortlist",
        # A client sending a contradicting status is exactly what the store
        # ignores. The route must not helpfully forward it either.
        json={"vendor_id": bidder["id"], "prequal_status": "Not qualified"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["vendor_id"] == bidder["id"]
    assert r.json()["vendor_name"] == "Al Munara Switchgear LLC"
    assert r.json()["prequal_status"] == "Approved"


def test_inviting_a_blocked_bidder_without_a_reason_is_409(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    blocked = create_bidder(client, prequal_status="Suspended")

    r = client.post(
        f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": blocked["id"]}
    )
    assert r.status_code == 409
    assert "suspended" in r.json()["detail"].lower()
    assert client.get(f"/api/workflow/rfqs/{rfq_id}").json()["shortlist"] == []


def test_an_override_is_attributed_to_the_session_not_the_body(tmp_path, monkeypatch):
    """`override_by` is never an input, for the same reason `by` never is: a
    client-supplied one would let any signed-in user sign somebody else's name
    to the exception."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    blocked = create_bidder(client, prequal_status="Suspended")

    r = client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={
        "vendor_id": blocked["id"],
        "override_reason": "Sole source for this frame size",
        "override_by": "someone.else@example.com",
    })
    assert r.status_code == 201, r.text
    assert r.json()["override_by"] == ADMIN_EMAIL


def test_inviting_an_unknown_bidder_is_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": "bdr_nope"})
    assert r.status_code == 404


def test_the_free_text_shortlist_route_is_unchanged(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={
        "vendor_name": "A one-off fabricator",
        "prequal_status": "Under review",
        "scope_code_fit": False,
        "included": True,
    })
    assert r.status_code == 201, r.text
    assert r.json()["vendor_id"] is None
    assert r.json()["prequal_status"] == "Under review"


def test_a_shortlist_post_with_neither_a_vendor_id_nor_a_name_is_422(tmp_path, monkeypatch):
    """Missing data is never coerced to a passing value — an unnamed vendor
    assumed qualified is the record this feature exists to prevent."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"included": True})
    assert r.status_code == 422
