from fastapi.testclient import TestClient

from api.auth import store as auth_store

ADMIN_ONLY = [
    ("POST", "/api/projects"),
    ("POST", "/api/projects/{slug}/requirements"),
    ("POST", "/api/projects/{slug}/vendors"),
    ("PUT", "/api/projects/{slug}/fx-rates"),
    ("POST", "/api/projects/{slug}/ingest"),
]


def _clients(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))

    admin = TestClient(api_main.app)
    admin.post("/api/auth/signup", json={"email": "admin@t.local", "password": "testpassword"})
    # By email, never by index — S1's rule, and the reviewer signs up next.
    with auth_store.locked_update(str(tmp_path)) as doc:
        for row in doc["users"]:
            if row["email"] == "admin@t.local":
                row["role"] = "admin"

    reviewer = TestClient(api_main.app)
    reviewer.post("/api/auth/signup", json={"email": "rev@t.local", "password": "testpassword"})
    return admin, reviewer


def test_admin_sees_every_project_and_reviewer_sees_none(tmp_path, monkeypatch):
    """A2: an ungranted reviewer's list is empty — not filtered-but-populated."""
    admin, reviewer = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    admin.post("/api/projects", json={"name": "Tender Two"})
    assert {p["slug"] for p in admin.get("/api/projects").json()} == {"tender-one", "tender-two"}
    assert reviewer.get("/api/projects").json() == []


def test_reviewer_gets_403_not_404_on_an_unentitled_slug(tmp_path, monkeypatch):
    """403 is deliberate: 404 would make a typo indistinguishable from a refusal."""
    admin, reviewer = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    assert reviewer.get("/api/projects/tender-one/summary").status_code == 403


def test_every_admin_only_route_refuses_a_reviewer(tmp_path, monkeypatch):
    """A3: the ingestion-class writes move every number on a tender."""
    admin, reviewer = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    for method, template in ADMIN_ONLY:
        path = template.replace("{slug}", "tender-one")
        assert reviewer.request(method, path).status_code == 403, f"{method} {path} let a reviewer through"


def test_admin_reaches_the_admin_only_routes(tmp_path, monkeypatch):
    """The guard must not be so tight it locks the admin out too."""
    admin, _ = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    assert admin.put("/api/projects/tender-one/fx-rates", json={"rates": {}}).status_code < 400


def test_a_granted_reviewer_reads_and_may_record_feedback(tmp_path, monkeypatch):
    """D4: reviewers keep the review-write; grants are honoured the moment they exist."""
    admin, reviewer = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    rev = auth_store.find_by_email(str(tmp_path), "rev@t.local")
    with auth_store.locked_update(str(tmp_path)) as doc:
        doc["grants"].append({"user_id": rev.id, "slug": "tender-one",
                              "granted_at": "2026-08-06T00:00:00+00:00", "granted_by": "u_admin"})
    assert [p["slug"] for p in reviewer.get("/api/projects").json()] == ["tender-one"]
    assert reviewer.get("/api/projects/tender-one/summary").status_code == 200
