"""API tests for `/api/workflow/*`.

These routes sit behind the same fail-closed session middleware as every other
`/api/` path — they are deliberately NOT in `middleware.PUBLIC_PATHS`, so each
test signs in first, and one test below asserts the challenge directly.

`api.main` is imported lazily inside the helpers for the reason spelled out at
the top of `tests/test_api_setup.py`: importing it at module scope runs
`load_dotenv()` during collection and can turn a permanently-skipped live-API
test into a billed call.
"""
from fastapi.testclient import TestClient

from tests.auth_helpers import ADMIN_EMAIL, ADMIN_PASSWORD, signed_in_admin


# No store-resetting fixture is needed: the workflow lives in
# `<ROOT>/workflow.json` and every test monkeypatches `ROOT` to its own
# `tmp_path`, so isolation comes from the same mechanism the auth suites use.
def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return signed_in_admin(TestClient(api_main.app), tmp_path)


def create_project(client: TestClient) -> str:
    r = client.post("/api/workflow/projects", json={
        "name": "Haliba Field Development",
        "code": "HAL",
        "client": "Al Dhafra Petroleum",
        "location": "Haliba field, UAE",
        "live_period_start": "2026-01-01",
        "live_period_end": "2029-12-31",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def create_rfq(client: TestClient) -> str:
    project_id = create_project(client)
    r = client.post(f"/api/workflow/projects/{project_id}/items", json={
        "item_type": "Wellhead tie-in materials",
        "description": "Wellhead & CGF tie-in materials",
        "qty": 1,
        "uom": "lot",
        "discipline": "Mechanical / piping",
        "estimated_value_aed": 46200000,
    })
    assert r.status_code == 201, r.text
    item_id = r.json()["id"]

    r = client.post("/api/workflow/rfqs", json={
        "project_id": project_id,
        "item_ids": [item_id],
        "reference": "ADP-RFQ-2026-014",
        "package": "Wellhead & CGF tie-in materials",
        "discipline": "Mechanical / piping",
        "value_estimate_aed": 46200000,
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_workflow_routes_require_a_session(tmp_path, monkeypatch):
    """Fail-closed by default — no route here is on the public allowlist."""
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    anonymous = TestClient(api_main.app)
    assert anonymous.get("/api/workflow/stages").status_code == 401


def test_stages_endpoint_lists_all_eight_in_order(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.get("/api/workflow/stages")
    assert r.status_code == 200
    assert r.json()["stages"] == [
        "Shortlisting", "Issued", "Clarifications",
        "Bids Received", "Evaluation", "Negotiation", "Awarded", "PO Issued",
    ]
    assert "Scoping" not in r.json()["stages"]


def test_new_rfq_starts_at_shortlisting_with_a_blocking_gate(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    r = client.get(f"/api/workflow/rfqs/{rfq_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["rfq"]["stage"] == "Shortlisting"
    assert body["gate"]["passed"] is False
    assert "no included vendors" in body["gate"]["reason"].lower()


def test_blocked_transition_returns_409_with_the_reason(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/transition", json={
        "target": "Issued",
    })
    assert r.status_code == 409
    assert "no included vendors" in r.json()["detail"].lower()


def test_a_blocked_transition_does_not_move_the_rfq(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    client.post(f"/api/workflow/rfqs/{rfq_id}/transition", json={
        "target": "Issued",
    })
    body = client.get(f"/api/workflow/rfqs/{rfq_id}").json()
    assert body["rfq"]["stage"] == "Shortlisting"
    assert len(body["rfq"]["history"]) == 1


def test_unknown_rfq_returns_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.get("/api/workflow/rfqs/rfq_missing")
    assert r.status_code == 404


def test_item_under_an_unknown_project_returns_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/workflow/projects/prj_missing/items", json={
        "item_type": "Generator",
        "description": "Generator package",
        "qty": 1,
        "uom": "ea",
        "discipline": "Electrical",
        "estimated_value_aed": 100,
    })
    assert r.status_code == 404


def test_rfq_against_a_foreign_item_returns_422(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    mine = create_project(client)
    theirs = create_project(client)
    r = client.post(f"/api/workflow/projects/{theirs}/items", json={
        "item_type": "Generator",
        "description": "Generator package",
        "qty": 1,
        "uom": "ea",
        "discipline": "Electrical",
        "estimated_value_aed": 100,
    })
    foreign_item = r.json()["id"]

    r = client.post("/api/workflow/rfqs", json={
        "project_id": mine,
        "item_ids": [foreign_item],
        "reference": "ADP-RFQ-2026-015",
        "package": "Package",
        "discipline": "Electrical",
        "value_estimate_aed": 100,
    })
    assert r.status_code == 422
    assert "does not belong" in r.json()["detail"]


def test_invalid_target_stage_returns_422(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/transition", json={
        "target": "Not A Stage",
    })
    assert r.status_code == 422


def _satisfy_the_shortlisting_gate(client: TestClient, rfq_id: str) -> None:
    """An included vendor, procurement's approval, and a TBE template — the
    three clauses of the first gate an RFQ now meets."""
    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={
        "vendor_name": "Galfar", "prequal_status": "Qualified",
        "scope_code_fit": True, "included": True,
    })
    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist/approve")
    client.put(f"/api/workflow/rfqs/{rfq_id}/tbe-template",
               json={"criteria": ["Throughput", "Materials"]})


def test_a_satisfied_gate_lets_the_rfq_advance(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    _satisfy_the_shortlisting_gate(client, rfq_id)

    body = client.get(f"/api/workflow/rfqs/{rfq_id}").json()
    assert body["gate"]["passed"] is True

    r = client.post(f"/api/workflow/rfqs/{rfq_id}/transition", json={
        "target": "Issued",
    })
    assert r.status_code == 200, r.text
    assert r.json()["stage"] == "Issued"


def test_who_moved_an_rfq_comes_from_the_session_not_the_request(tmp_path, monkeypatch):
    """The history exists to answer "who did this", so `by` must not be a body
    field — a signed-in user could otherwise sign a stage change as anyone."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    client.put(f"/api/workflow/rfqs/{rfq_id}/technical-package", json={
        "revision": "Rev. B",
        "basis_of_design": "basis",
        "attachments": [{"doc_code": "HAL-PID-001", "title": "P&ID", "revision": "Rev. C"}],
    })
    client.post(f"/api/workflow/rfqs/{rfq_id}/technical-package/freeze", json={})
    _satisfy_the_shortlisting_gate(client, rfq_id)

    r = client.post(f"/api/workflow/rfqs/{rfq_id}/transition", json={
        "target": "Issued",
        "by": "someone.else@example.com",  # ignored, not honoured
        "reason": "shortlist prepared",
    })
    assert r.status_code == 200, r.text
    assert r.json()["history"][-1]["by"] == ADMIN_EMAIL
    assert r.json()["history"][-1]["reason"] == "shortlist prepared"

    from workflow import persistence
    package = persistence.load(str(tmp_path)).get_technical_package(rfq_id)
    assert package.frozen_by == ADMIN_EMAIL


def test_freezing_a_package_with_an_indefinite_revision_returns_409(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    client.put(f"/api/workflow/rfqs/{rfq_id}/technical-package", json={
        "revision": "Rev. B",
        "basis_of_design": "basis",
        "attachments": [{"doc_code": "HAL-PID-001", "title": "P&ID", "revision": None}],
    })
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/technical-package/freeze", json={})
    assert r.status_code == 409
    assert "definite revision" in r.json()["detail"]


def test_rfq_detail_carries_the_artifacts_each_gate_reads(tmp_path, monkeypatch):
    """The detail view has to show *why* a stage is blocked, and the reason is
    always some artifact being absent or unfrozen. Returning the gate sentence
    without the artifacts behind it leaves the screen unable to say more than
    "no"."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)

    body = client.get(f"/api/workflow/rfqs/{rfq_id}").json()
    assert body["technical_package"] is None
    assert body["shortlist"] == []
    assert body["shortlist_approved"] is False
    assert body["tbe_template"] is None
    assert body["vdrl"] == []
    assert body["bids"] == []

    client.put(f"/api/workflow/rfqs/{rfq_id}/technical-package", json={
        "revision": "Rev. B",
        "basis_of_design": "129 wellhead tie-ins",
        "attachments": [{"doc_code": "HAL-PID-001", "title": "P&ID", "revision": "Rev. C"}],
    })
    client.post(f"/api/workflow/rfqs/{rfq_id}/technical-package/freeze", json={})

    body = client.get(f"/api/workflow/rfqs/{rfq_id}").json()
    assert body["technical_package"]["revision"] == "Rev. B"
    assert body["technical_package"]["frozen_by"] == ADMIN_EMAIL
    assert body["technical_package"]["attachments"][0]["doc_code"] == "HAL-PID-001"
    # Freezing no longer opens anything — the first gate reads the shortlist,
    # the approval and the TBE template, and the detail carries all three.
    assert body["gate"]["passed"] is False

    _satisfy_the_shortlisting_gate(client, rfq_id)
    body = client.get(f"/api/workflow/rfqs/{rfq_id}").json()
    assert [e["vendor_name"] for e in body["shortlist"]] == ["Galfar"]
    assert body["shortlist_approved"] is True
    assert body["tbe_template"]["criteria"] == ["Throughput", "Materials"]
    assert body["gate"]["passed"] is True


def test_rfq_detail_reports_each_bid_against_its_vdrl(tmp_path, monkeypatch):
    """`unreadable` must stay distinct from `not_received` all the way to the
    screen: one is a file to chase, the other a vendor to chase."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)

    from workflow import persistence
    with persistence.locked_update(str(tmp_path)) as store:
        store.add_vdrl_line(rfq_id, doc_code="GA-001", title="GA", doc_type="Doc")
        store.add_vdrl_line(rfq_id, doc_code="DS-001", title="Datasheet", doc_type="Doc")
        bid = store.register_bid(rfq_id, vendor_name="Petrofac", headline_price_aed=51_400_000)
        store.record_vdrl_receipt(bid.id, doc_code="GA-001", state="received", revision="Rev. A")
        store.record_vdrl_receipt(bid.id, doc_code="DS-001", state="unreadable")

    body = client.get(f"/api/workflow/rfqs/{rfq_id}").json()
    assert [line["doc_code"] for line in body["vdrl"]] == ["GA-001", "DS-001"]

    assert len(body["bids"]) == 1
    reported = body["bids"][0]
    assert reported["vendor_name"] == "Petrofac"
    assert reported["vdrl_received"] == 1
    assert reported["vdrl_required"] == 2
    assert reported["vdrl_missing"] == ["DS-001"], "an unreadable file is not a received one"


def test_rfq_roster_counts_every_stage_including_the_empty_ones(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    create_rfq(client)

    body = client.get("/api/workflow/rfqs").json()
    assert len(body["rfqs"]) == 1
    assert len(body["stage_counts"]) == 8, "a strip that drops empty stages changes shape"
    assert body["stage_counts"]["Shortlisting"] == 1
    assert body["stage_counts"]["Awarded"] == 0
    assert sum(body["stage_counts"].values()) == len(body["rfqs"])


def test_rfq_roster_filters_by_project(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    create_rfq(client)
    project_id = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["rfq"]["project_id"]

    body = client.get("/api/workflow/rfqs", params={"project_id": project_id}).json()
    assert [r["id"] for r in body["rfqs"]] == [rfq_id]


def test_project_roster_lists_created_projects(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    body = client.get("/api/workflow/projects").json()
    assert [p["id"] for p in body["projects"]] == [project_id]


def test_items_of_an_unknown_project_returns_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/workflow/projects/prj_missing/items").status_code == 404


def test_terminal_stage_reports_a_passing_gate(tmp_path, monkeypatch):
    """PO Issued has no successor, so there is nothing left to block."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    from workflow import persistence
    from workflow.stages import Stage

    # Park the RFQ at the terminal stage directly rather than walking eight
    # gated transitions — this test is about the last one, not the road there.
    with persistence.locked_update(str(tmp_path)) as store:
        store._rfqs[rfq_id] = store.get_rfq(rfq_id).model_copy(
            update={"stage": Stage.PO_ISSUED}
        )

    body = client.get(f"/api/workflow/rfqs/{rfq_id}").json()
    assert body["rfq"]["stage"] == "PO Issued"
    assert body["gate"]["passed"] is True


def test_an_rfq_survives_a_restart(tmp_path, monkeypatch):
    """The point of the whole persistence layer: a fresh TestClient is a fresh
    process's worth of in-memory state, and the RFQ is still there."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)

    import api.main as api_main
    fresh = TestClient(api_main.app)
    fresh.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})

    body = fresh.get(f"/api/workflow/rfqs/{rfq_id}").json()
    assert body["rfq"]["reference"] == "ADP-RFQ-2026-014"


def test_a_refused_transition_leaves_the_document_untouched(tmp_path, monkeypatch):
    """`locked_update` writes only on a clean exit, so a closed gate cannot
    half-land a transition on disk."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    from workflow import persistence

    before = persistence.load(str(tmp_path)).get_rfq(rfq_id)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/transition", json={
        "target": "Issued",
    })
    assert r.status_code == 409

    after = persistence.load(str(tmp_path)).get_rfq(rfq_id)
    assert after.stage is before.stage
    assert len(after.history) == len(before.history)


# -- the wizard's own path: edit a step, tick it off, move to the next --------

def _freeze_package(client: TestClient, rfq_id: str) -> None:
    client.put(f"/api/workflow/rfqs/{rfq_id}/technical-package", json={
        "revision": "Rev. B",
        "basis_of_design": "129 wellhead tie-ins",
        "attachments": [{"doc_code": "HAL-PID-001", "title": "P&ID", "revision": "Rev. C"}],
    })
    client.post(f"/api/workflow/rfqs/{rfq_id}/technical-package/freeze", json={})


def test_a_user_can_walk_shortlisting_to_clarifications(tmp_path, monkeypatch):
    """The whole point of the wizard: each step is satisfied through the API,
    and only then does the next one open."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)

    # Shortlisting: needs an included vendor, approval, and a TBE template.
    # It is where an RFQ now begins — there is no step before it.
    assert client.post(f"/api/workflow/rfqs/{rfq_id}/transition",
                       json={"target": "Issued"}).status_code == 409
    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={
        "vendor_name": "Galfar", "prequal_status": "Qualified",
        "scope_code_fit": True, "included": True,
    })
    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist/approve")
    client.put(f"/api/workflow/rfqs/{rfq_id}/tbe-template",
               json={"criteria": ["Throughput", "Materials"]})
    assert client.post(f"/api/workflow/rfqs/{rfq_id}/transition",
                       json={"target": "Issued"}).status_code == 200

    # Issuance: the technical package and the VDRL are edited here; the edge
    # itself is not gated.
    _freeze_package(client, rfq_id)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/vdrl", json={
        "doc_code": "RFQ-GA-001", "title": "Skid GA drawing", "doc_type": "GA Drawing",
    })
    assert r.status_code == 201
    assert client.post(f"/api/workflow/rfqs/{rfq_id}/transition",
                       json={"target": "Clarifications"}).status_code == 200

    body = client.get(f"/api/workflow/rfqs/{rfq_id}").json()
    assert body["rfq"]["stage"] == "Clarifications"
    assert [h["to_stage"] for h in body["rfq"]["history"]] == [
        "Shortlisting", "Issued", "Clarifications",
    ]


def test_a_shortlist_entry_can_be_added_and_removed(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)

    r = client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={
        "vendor_name": "Galfar", "prequal_status": "Qualified",
        "scope_code_fit": True, "included": True,
    })
    assert r.status_code == 201
    entry_id = r.json()["id"]

    assert client.delete(
        f"/api/workflow/rfqs/{rfq_id}/shortlist/{entry_id}"
    ).status_code == 204
    assert client.get(f"/api/workflow/rfqs/{rfq_id}").json()["shortlist"] == []


def test_editing_the_shortlist_re_opens_its_approval(tmp_path, monkeypatch):
    """Going back a step and changing vendors must un-tick the step, or an RFQ
    could issue with a vendor procurement never saw."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={
        "vendor_name": "Galfar", "prequal_status": "Qualified",
        "scope_code_fit": True, "included": True,
    })
    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist/approve")
    assert client.get(f"/api/workflow/rfqs/{rfq_id}").json()["shortlist_approved"] is True

    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={
        "vendor_name": "Petrofac", "prequal_status": "Qualified",
        "scope_code_fit": True, "included": True,
    })
    assert client.get(f"/api/workflow/rfqs/{rfq_id}").json()["shortlist_approved"] is False


def test_an_override_is_attributed_to_the_session(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={
        "vendor_name": "OQC", "prequal_status": "Under review",
        "scope_code_fit": False, "included": True,
        "override_reason": "Sole source for this alloy",
    })
    assert r.json()["override_by"] == ADMIN_EMAIL
    assert r.json()["override_reason"] == "Sole source for this alloy"


def test_approving_an_empty_shortlist_returns_409(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist/approve")
    assert r.status_code == 409
    assert "empty shortlist" in r.json()["detail"]


def test_a_vdrl_line_can_be_added_and_removed(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/vdrl", json={
        "doc_code": "GA-001", "title": "GA", "doc_type": "Doc",
    })
    line_id = r.json()["id"]

    assert client.delete(f"/api/workflow/rfqs/{rfq_id}/vdrl/{line_id}").status_code == 204
    assert client.get(f"/api/workflow/rfqs/{rfq_id}").json()["vdrl"] == []


def test_removing_an_unknown_artifact_returns_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    assert client.delete(
        f"/api/workflow/rfqs/{rfq_id}/shortlist/sle_missing"
    ).status_code == 404
    assert client.delete(f"/api/workflow/rfqs/{rfq_id}/vdrl/vdl_missing").status_code == 404


def test_a_frozen_package_refuses_a_later_edit_with_409(tmp_path, monkeypatch):
    """Re-opening the package editor after the freeze must not silently rewrite
    what vendors were invited to bid against."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    _freeze_package(client, rfq_id)

    r = client.put(f"/api/workflow/rfqs/{rfq_id}/technical-package", json={
        "revision": "Rev. Z", "basis_of_design": "changed", "attachments": [],
    })
    assert r.status_code == 409
    assert "frozen" in r.json()["detail"].lower()

    body = client.get(f"/api/workflow/rfqs/{rfq_id}").json()
    assert body["technical_package"]["revision"] == "Rev. B"


# -- projects and items: read, edit, delete ----------------------------------


def create_item(client: TestClient, project_id: str, **over) -> dict:
    body = {
        "item_type": "Gas generator",
        "description": "2 x 5 MW containerised",
        "qty": 2,
        "uom": "no",
        "discipline": "Electrical",
        "estimated_value_aed": 18_000_000,
    }
    body.update(over)
    r = client.post(f"/api/workflow/projects/{project_id}/items", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def raise_rfq(client: TestClient, project_id: str, item_ids: list[str]) -> dict:
    r = client.post("/api/workflow/rfqs", json={
        "project_id": project_id,
        "item_ids": item_ids,
        "reference": "ADP-RFQ-2026-014",
        "package": "Power generation",
        "discipline": "Electrical",
        "value_estimate_aed": 18_000_000,
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_the_project_roster_carries_item_and_rfq_counts(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    create_item(client, project_id)

    row = client.get("/api/workflow/projects").json()["projects"][0]

    assert row["item_count"] == 1
    assert row["rfq_count"] == 0


def test_project_detail_returns_project_items_and_rfqs(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    item = create_item(client, project_id)
    raise_rfq(client, project_id, [item["id"]])

    body = client.get(f"/api/workflow/projects/{project_id}").json()

    assert body["project"]["id"] == project_id
    assert [i["id"] for i in body["items"]] == [item["id"]]
    assert [r["reference"] for r in body["rfqs"]] == ["ADP-RFQ-2026-014"]


def test_project_detail_404s_for_an_unknown_project(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/workflow/projects/prj_missing").status_code == 404


def test_patching_a_project_changes_only_what_was_sent(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)

    r = client.patch(f"/api/workflow/projects/{project_id}", json={"status": "On Hold"})

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "On Hold"
    assert r.json()["code"] == "HAL"      # untouched, because it was not sent


def test_patching_an_unknown_project_404s(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.patch("/api/workflow/projects/prj_missing", json={"status": "Closed"})
    assert r.status_code == 404


def test_patching_a_project_to_an_unknown_status_is_422(tmp_path, monkeypatch):
    """`status` is a Literal on the model, so an unknown value is a malformed
    request, not a workflow refusal."""
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    r = client.patch(f"/api/workflow/projects/{project_id}", json={"status": "Mothballed"})
    assert r.status_code == 422


def test_patching_an_item_changes_only_what_was_sent(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    item = create_item(client, project_id)

    r = client.patch(
        f"/api/workflow/projects/{project_id}/items/{item['id']}", json={"qty": 3}
    )

    assert r.status_code == 200, r.text
    assert r.json()["qty"] == 3
    assert r.json()["item_type"] == "Gas generator"


def test_patching_an_item_through_the_wrong_project_404s(tmp_path, monkeypatch):
    """The path names a parent; an item that is not that parent's is not found
    under it, whatever the item id says."""
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    item = create_item(client, project_id)
    other_id = create_project(client)

    r = client.patch(
        f"/api/workflow/projects/{other_id}/items/{item['id']}", json={"qty": 9}
    )

    assert r.status_code == 404
    # And nothing was written: a 404 must not half-land an edit.
    detail = client.get(f"/api/workflow/projects/{project_id}").json()
    assert detail["items"][0]["qty"] == 2


def test_an_item_dated_outside_the_live_period_warns_but_is_stored(tmp_path, monkeypatch):
    """Advisory, not a refusal: needing something after a live period closes is
    unusual, not impossible, and a hard block would make the field unusable in
    exactly those cases."""
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)   # live period 2026-01-01 .. 2029-12-31

    body = create_item(client, project_id, required_on_site="2030-06-01")

    assert body["live_period_warning"] is not None
    assert "2030-06-01" in body["live_period_warning"]
    assert len(client.get(f"/api/workflow/projects/{project_id}").json()["items"]) == 1


def test_an_item_dated_inside_the_live_period_carries_no_warning(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    body = create_item(client, project_id, required_on_site="2027-06-01")
    assert body["live_period_warning"] is None


def test_an_item_with_no_date_carries_no_warning(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    assert create_item(client, project_id)["live_period_warning"] is None


def test_patching_an_items_date_outside_the_live_period_warns(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    item = create_item(client, project_id)

    r = client.patch(
        f"/api/workflow/projects/{project_id}/items/{item['id']}",
        json={"required_on_site": "2030-06-01"},
    )

    assert r.status_code == 200, r.text
    assert "live period" in r.json()["live_period_warning"].lower()


def test_deleting_an_item_an_rfq_covers_is_409_and_writes_nothing(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    item = create_item(client, project_id)
    raise_rfq(client, project_id, [item["id"]])

    r = client.delete(f"/api/workflow/projects/{project_id}/items/{item['id']}")

    assert r.status_code == 409
    assert "ADP-RFQ-2026-014" in r.json()["detail"]
    assert len(client.get(f"/api/workflow/projects/{project_id}").json()["items"]) == 1


def test_deleting_a_free_item_is_204(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    item = create_item(client, project_id)

    r = client.delete(f"/api/workflow/projects/{project_id}/items/{item['id']}")

    assert r.status_code == 204
    assert client.get(f"/api/workflow/projects/{project_id}").json()["items"] == []


def test_deleting_an_item_through_the_wrong_project_404s(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    item = create_item(client, project_id)
    other_id = create_project(client)

    r = client.delete(f"/api/workflow/projects/{other_id}/items/{item['id']}")

    assert r.status_code == 404
    assert len(client.get(f"/api/workflow/projects/{project_id}").json()["items"]) == 1


def test_deleting_an_unknown_item_404s(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    r = client.delete(f"/api/workflow/projects/{project_id}/items/itm_missing")
    assert r.status_code == 404


def test_deleting_a_project_holding_an_rfq_is_409(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    item = create_item(client, project_id)
    raise_rfq(client, project_id, [item["id"]])

    r = client.delete(f"/api/workflow/projects/{project_id}")

    assert r.status_code == 409
    assert "ADP-RFQ-2026-014" in r.json()["detail"]
    assert client.get(f"/api/workflow/projects/{project_id}").status_code == 200


def test_deleting_a_project_takes_its_items_with_it(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id = create_project(client)
    create_item(client, project_id)

    assert client.delete(f"/api/workflow/projects/{project_id}").status_code == 204
    assert client.get("/api/workflow/projects").json()["projects"] == []


def test_deleting_an_unknown_project_404s(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.delete("/api/workflow/projects/prj_missing").status_code == 404
