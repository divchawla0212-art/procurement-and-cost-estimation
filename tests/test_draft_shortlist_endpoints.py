"""The item's draft shortlist, over HTTP.

The store's rules are covered in `tests/test_draft_shortlist.py`. What matters
here is what the routes do with them: that the duplicate guard is reached
through the route rather than reimplemented in front of it, that a draft is
readable without asking a second endpoint for it, and that an id belonging to
another project's item is a 404 rather than a cross-project write.

Key-free and fixture-free.
"""
from fastapi.testclient import TestClient

from tests.auth_helpers import ADMIN_EMAIL, ADMIN_PASSWORD, signed_in_admin


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return signed_in_admin(TestClient(api_main.app), tmp_path)


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


def pick(client, project_id, item_id, **body):
    payload = {"vendor_name": "Al Munara Cables LLC", "source": "ADNOC", **body}
    return client.post(
        f"/api/workflow/projects/{project_id}/items/{item_id}/draft-shortlist",
        json=payload,
    )


def test_a_vendor_is_picked_onto_the_draft(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)

    r = pick(client, project_id, item_id, vendor_id="bdr_1")

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("dse_")
    assert body["vendor_name"] == "Al Munara Cables LLC"
    assert body["vendor_id"] == "bdr_1"
    assert body["source"] == "ADNOC"


def test_the_pick_is_attributed_to_whoever_made_it(tmp_path, monkeypatch):
    """`added_by` comes from the session, never from the body — a screen that
    could name the picker could name somebody else."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)

    r = pick(client, project_id, item_id, added_by="someone.else@example.com")

    assert r.json()["added_by"] == ADMIN_EMAIL


def test_the_draft_rides_on_the_project_payload(tmp_path, monkeypatch):
    """Read with the project the screen is already fetching, the same way
    `item_vendor_lists` is. A second endpoint would be a second read that could
    disagree with the first."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    pick(client, project_id, item_id, vendor_id="bdr_1")

    body = client.get(f"/api/workflow/projects/{project_id}").json()

    assert [e["vendor_name"] for e in body["draft_shortlists"][item_id]] == [
        "Al Munara Cables LLC"
    ]


def test_an_item_with_no_picks_is_absent_rather_than_null(tmp_path, monkeypatch):
    """Every item gets a key, so the browser reads a list rather than guarding
    for a missing one on every render."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)

    body = client.get(f"/api/workflow/projects/{project_id}").json()

    assert body["draft_shortlists"][item_id] == []


def test_picking_the_same_vendor_twice_is_one_row(tmp_path, monkeypatch):
    """The guard is the store's, and this is what says the route reaches it
    rather than reimplementing it in front of the lock."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    first = pick(client, project_id, item_id, vendor_id="bdr_1").json()

    again = pick(client, project_id, item_id, vendor_id="bdr_1", source="Astra")

    assert again.status_code == 201
    assert again.json()["id"] == first["id"]
    body = client.get(f"/api/workflow/projects/{project_id}").json()
    assert len(body["draft_shortlists"][item_id]) == 1


def test_a_pick_is_removed_by_id(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    entry_id = pick(client, project_id, item_id, vendor_id="bdr_1").json()["id"]

    r = client.delete(
        f"/api/workflow/projects/{project_id}/items/{item_id}/draft-shortlist/{entry_id}"
    )

    assert r.status_code == 204, r.text
    body = client.get(f"/api/workflow/projects/{project_id}").json()
    assert body["draft_shortlists"][item_id] == []


def test_removing_an_unknown_pick_is_a_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)

    r = client.delete(
        f"/api/workflow/projects/{project_id}/items/{item_id}/draft-shortlist/dse_nope"
    )

    assert r.status_code == 404
    assert "dse_nope" in r.json()["detail"]


def test_a_blank_vendor_name_is_refused(tmp_path, monkeypatch):
    """A vendor with no name cannot be invited later, so storing one would put a
    row on screen that no action can be taken on. Same rule as the hand-add
    route."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)

    r = pick(client, project_id, item_id, vendor_name="   ")

    assert r.status_code == 422


def test_a_source_outside_the_four_pool_chips_is_refused(tmp_path, monkeypatch):
    """`Client` is the uploaded list's name for the client's export, not a pool
    chip. Accepting it would store a source no screen renders."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)

    r = pick(client, project_id, item_id, source="Client")

    assert r.status_code == 422


def test_an_item_in_another_project_is_not_reachable(tmp_path, monkeypatch):
    """The path names both, so both are checked. Otherwise one project's screen
    could write onto another's item."""
    client = _client(tmp_path, monkeypatch)
    _project_id, item_id = make_item(client)
    other_project, _other_item = make_item(client)

    r = pick(client, other_project, item_id, vendor_id="bdr_1")

    assert r.status_code == 404


def test_a_curated_pick_carries_no_vendor_id(tmp_path, monkeypatch):
    """A hand-typed or model-named company has no registry row, and the route
    never looks one up — a match would attach a real company's approvals to
    whatever somebody typed."""
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)

    r = pick(client, project_id, item_id, vendor_name="Typed By Hand LLC", source="Manual")

    assert r.json()["vendor_id"] is None


def test_the_draft_survives_a_restart(tmp_path, monkeypatch):
    """The reason this collection is stored at all, asserted end to end.

    A second app object over the same root, signed in afresh, is what a restart
    looks like from outside: the process that held the first draft in memory is
    gone, and everything the new one knows it read off disk. Signing in rather
    than signing up, because the account is already in `auth.json` — and because
    startup clears sessions, which is the other half of a real restart.
    """
    client = _client(tmp_path, monkeypatch)
    project_id, item_id = make_item(client)
    pick(client, project_id, item_id, vendor_id="bdr_1")

    import api.main as api_main
    fresh = TestClient(api_main.app)
    signed = fresh.post(
        "/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert signed.status_code == 200, signed.text
    body = fresh.get(f"/api/workflow/projects/{project_id}").json()

    assert [e["vendor_name"] for e in body["draft_shortlists"][item_id]] == [
        "Al Munara Cables LLC"
    ]
