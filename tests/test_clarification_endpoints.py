"""The clarification routes.

Each mirrors one control on the wizard's Clarifications step. Every rule lives
in the store; these assertions are about the HTTP contract — status codes,
attribution to the session user, and the fact that a refusal is a 409 carrying
the store's own sentence rather than a 500.
"""
from tests.test_workflow_endpoints import _client, create_rfq


def frozen_and_shortlisted(client) -> tuple[str, str]:
    rfq_id = create_rfq(client)
    client.put(f"/api/workflow/rfqs/{rfq_id}/technical-package", json={
        "revision": "Rev. A",
        "basis_of_design": "Battery-limit transmitters.",
        "attachments": [{"doc_code": "IO-411", "title": "IO list", "revision": "Rev. A"}],
    })
    client.post(f"/api/workflow/rfqs/{rfq_id}/technical-package/freeze", json={})
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={
        "vendor_name": "Al Munara Switchgear LLC", "prequal_status": "Approved",
        "scope_code_fit": True, "included": True,
    })
    assert r.status_code == 201, r.text
    return rfq_id, r.json()["id"]


def raise_a_query(client, rfq_id: str, entry_id: str, **overrides) -> str:
    body = {
        "raised_by_entry_id": entry_id,
        "category": "Technical",
        "question": "Which IO list revision governs?",
        "raised_on": "2026-08-13",
    }
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/queries", json={**body, **overrides})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_a_raised_query_comes_back_numbered(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, entry_id = frozen_and_shortlisted(client)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/queries", json={
        "raised_by_entry_id": entry_id, "category": "Technical",
        "question": "Which IO list revision?", "raised_on": "2026-08-13",
    })
    assert r.status_code == 201
    assert r.json()["number"] == "TQ-001"
    assert r.json()["state"] == "Open"


def test_a_query_from_an_unknown_entry_is_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, _entry_id = frozen_and_shortlisted(client)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/queries", json={
        "raised_by_entry_id": "sle_nobody", "category": "Technical",
        "question": "q", "raised_on": "2026-08-13",
    })
    assert r.status_code == 404


def test_answering_attributes_to_the_session_user(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, entry_id = frozen_and_shortlisted(client)
    query_id = raise_a_query(client, rfq_id, entry_id)

    r = client.post(f"/api/workflow/rfqs/{rfq_id}/queries/{query_id}/answer",
                    json={"answer": "Rev. A."})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "Answered"
    assert body["circulated"] is True
    assert body["answered_by"]          # whoever the test client is signed in as


def test_restricting_without_a_reason_is_409(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, entry_id = frozen_and_shortlisted(client)
    query_id = raise_a_query(client, rfq_id, entry_id)

    r = client.post(f"/api/workflow/rfqs/{rfq_id}/queries/{query_id}/answer",
                    json={"answer": "Yes.", "restricted_reason": "   "})
    assert r.status_code == 409
    assert "reason" in r.json()["detail"]


def test_a_restricted_answer_records_why_it_was_not_circulated(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, entry_id = frozen_and_shortlisted(client)
    query_id = raise_a_query(client, rfq_id, entry_id)

    r = client.post(f"/api/workflow/rfqs/{rfq_id}/queries/{query_id}/answer", json={
        "answer": "Yes.",
        "restricted_reason": "Reveals the bidder's own layout.",
    })
    assert r.status_code == 200
    assert r.json()["circulated"] is False
    assert r.json()["restricted_reason"] == "Reveals the bidder's own layout."


def test_withdrawing_needs_a_reason_and_reports_the_state(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, entry_id = frozen_and_shortlisted(client)
    query_id = raise_a_query(client, rfq_id, entry_id)

    assert client.post(f"/api/workflow/rfqs/{rfq_id}/queries/{query_id}/withdraw",
                       json={"reason": ""}).status_code == 409
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/queries/{query_id}/withdraw",
                    json={"reason": "Duplicate of TQ-001."})
    assert r.status_code == 200
    assert r.json()["state"] == "Withdrawn"


def test_the_rfq_payload_carries_the_register_and_the_due_date(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, entry_id = frozen_and_shortlisted(client)
    raise_a_query(client, rfq_id, entry_id)

    body = client.get(f"/api/workflow/rfqs/{rfq_id}").json()
    assert [q["number"] for q in body["queries"]] == ["TQ-001"]
    assert body["addenda"] == []
    assert body["bid_due_date"] is None


def test_an_addendum_is_drafted_issued_and_then_immutable(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, _entry_id = frozen_and_shortlisted(client)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/addenda", json={
        "revision": "Rev. B", "summary": "IO list corrected.",
        "attachments": [{"doc_code": "IO-411", "title": "IO list", "revision": "Rev. B"}],
        "bid_due_date": "2026-10-15",
    })
    assert r.status_code == 201, r.text
    addendum_id = r.json()["id"]
    assert r.json()["number"] == "ADD-01"
    assert r.json()["supersedes_revision"] == "Rev. A"
    assert r.json()["draft"] is True

    assert client.patch(f"/api/workflow/rfqs/{rfq_id}/addenda/{addendum_id}",
                        json={"summary": "Reworded."}).status_code == 200
    issued = client.post(f"/api/workflow/rfqs/{rfq_id}/addenda/{addendum_id}/issue",
                         json={})
    assert issued.status_code == 200
    assert issued.json()["draft"] is False

    assert client.patch(f"/api/workflow/rfqs/{rfq_id}/addenda/{addendum_id}",
                        json={"summary": "Again."}).status_code == 409
    assert client.delete(
        f"/api/workflow/rfqs/{rfq_id}/addenda/{addendum_id}").status_code == 409

    body = client.get(f"/api/workflow/rfqs/{rfq_id}").json()
    assert body["technical_package"]["revision"] == "Rev. B"
    assert body["bid_due_date"] == "2026-10-15"


def test_a_draft_addendum_can_be_deleted(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, _entry_id = frozen_and_shortlisted(client)
    addendum_id = client.post(f"/api/workflow/rfqs/{rfq_id}/addenda", json={
        "revision": "Rev. B", "summary": "s", "attachments": [],
    }).json()["id"]

    assert client.delete(
        f"/api/workflow/rfqs/{rfq_id}/addenda/{addendum_id}").status_code == 204
    assert client.get(f"/api/workflow/rfqs/{rfq_id}").json()["addenda"] == []


def test_an_unknown_addendum_is_404_not_500(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, _entry_id = frozen_and_shortlisted(client)
    assert client.post(
        f"/api/workflow/rfqs/{rfq_id}/addenda/add_nobody/issue",
        json={}).status_code == 404


def test_removing_a_bidder_who_asked_is_409_naming_the_query(tmp_path, monkeypatch):
    """The store's guard, surfaced as the workflow refusing rather than as a
    malformed request."""
    client = _client(tmp_path, monkeypatch)
    rfq_id, entry_id = frozen_and_shortlisted(client)
    raise_a_query(client, rfq_id, entry_id)

    r = client.delete(f"/api/workflow/rfqs/{rfq_id}/shortlist/{entry_id}")
    assert r.status_code == 409
    assert "TQ-001" in r.json()["detail"]


def test_the_transition_route_surfaces_the_gate_sentence(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id, entry_id = frozen_and_shortlisted(client)
    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist/approve", json={})
    client.put(f"/api/workflow/rfqs/{rfq_id}/tbe-template",
               json={"criteria": ["Accuracy class"]})
    for target in ["Issued", "Clarifications"]:
        assert client.post(f"/api/workflow/rfqs/{rfq_id}/transition",
                           json={"target": target}).status_code == 200
    raise_a_query(client, rfq_id, entry_id)

    r = client.post(f"/api/workflow/rfqs/{rfq_id}/transition",
                    json={"target": "Bids Received"})
    assert r.status_code == 409
    assert "TQ-001" in r.json()["detail"]
