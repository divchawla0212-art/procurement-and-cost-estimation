"""The two enquiry routes: preview (stores nothing) and send (records).

The store's dispatch rules are covered in `tests/test_enquiry_dispatch.py`.
What matters here is the HTTP contract: that preview really does not touch
`workflow.json`, that `by` comes from the session rather than the request
body, and that an unknown RFQ is a 404 on both routes.
"""
import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from tests.auth_helpers import ADMIN_EMAIL, signed_in_admin
from tests.test_workflow_endpoints import create_rfq
from workflow import persistence
from workflow.models.vendor_contact import VendorContact


@pytest.fixture
def tmp_root(tmp_path, monkeypatch) -> str:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    return str(tmp_path)


@pytest.fixture
def client(tmp_root, monkeypatch) -> TestClient:
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", tmp_root)
    return signed_in_admin(TestClient(api_main.app), tmp_root)


def _shortlist(client: TestClient, rfq_id: str, vendor_name: str) -> str:
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={
        "vendor_name": vendor_name, "prequal_status": "Approved",
        "scope_code_fit": True, "included": True,
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _give_contact(tmp_root: str, vendor_name: str, email: str = "sales@example.com") -> None:
    with persistence.locked_update(tmp_root) as store:
        store.set_vendor_contacts([
            *store.vendor_contacts(),
            VendorContact(
                vendor_name=vendor_name, emails=[email],
                source_document="test-contacts.xlsx", uploaded_by="tester@example.com",
                uploaded_at=datetime.now(timezone.utc),
            ),
        ])


@pytest.fixture
def an_rfq_with_a_shortlist(client: TestClient, tmp_root: str) -> str:
    rfq_id = create_rfq(client)
    _shortlist(client, rfq_id, "Al Munara Switchgear LLC")
    _give_contact(tmp_root, "Al Munara Switchgear LLC")
    return rfq_id


@pytest.fixture
def an_rfq_with_a_mixed_shortlist(client: TestClient, tmp_root: str) -> str:
    rfq_id = create_rfq(client)
    _shortlist(client, rfq_id, "Al Munara Switchgear LLC")
    _shortlist(client, rfq_id, "Nowhere Trading LLC")
    _give_contact(tmp_root, "Al Munara Switchgear LLC")
    return rfq_id


def test_the_preview_route_stores_nothing(client, tmp_root, an_rfq_with_a_shortlist):
    """Byte-for-byte, which is the only assertion that catches a convenience
    write nobody meant to add."""
    path = os.path.join(tmp_root, "workflow.json")
    with open(path, "rb") as handle:
        before = handle.read()

    response = client.post(f"/api/workflow/rfqs/{an_rfq_with_a_shortlist}/enquiry/preview")

    assert response.status_code == 200
    with open(path, "rb") as handle:
        assert handle.read() == before


def test_the_preview_names_the_transport_it_would_use(client, an_rfq_with_a_shortlist):
    body = client.post(
        f"/api/workflow/rfqs/{an_rfq_with_a_shortlist}/enquiry/preview"
    ).json()
    assert body["transport"] == "outbox"


def test_an_unknown_rfq_is_a_404(client):
    assert client.post("/api/workflow/rfqs/rfq_nope/enquiry/preview").status_code == 404
    assert client.post("/api/workflow/rfqs/rfq_nope/enquiry/send").status_code == 404


def test_sending_records_who_sent_it_from_the_session(client, an_rfq_with_a_shortlist):
    """`by` comes from the session and is never an input — a caller could
    otherwise sign somebody else's name to a tender."""
    body = client.post(
        f"/api/workflow/rfqs/{an_rfq_with_a_shortlist}/enquiry/send"
    ).json()
    assert body["sent"][0]["by"] == ADMIN_EMAIL


def test_the_send_route_returns_both_halves(client, an_rfq_with_a_mixed_shortlist):
    body = client.post(
        f"/api/workflow/rfqs/{an_rfq_with_a_mixed_shortlist}/enquiry/send"
    ).json()
    assert body["sent"] and body["skipped"]
    assert "reason" in body["skipped"][0]
