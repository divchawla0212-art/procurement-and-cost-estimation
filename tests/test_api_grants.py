from fastapi.testclient import TestClient

from api.auth import store as auth_store


def _clients(tmp_path, monkeypatch):
    """Admin + reviewer client pair sharing one `auth.json` under `tmp_path`.

    Mirrors `tests/test_api_authorization.py`'s `_clients` exactly (same
    emails, same promotion-by-email pattern) rather than inventing a third
    variant — this task's tests key user lookups off the same
    "admin@t.local" / "rev@t.local" addresses that suite uses.
    """
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))

    admin = TestClient(api_main.app)
    admin.post("/api/auth/signup", json={"email": "admin@t.local", "password": "testpassword"})
    # By email, never by index — S1's rule.
    with auth_store.locked_update(str(tmp_path)) as doc:
        for row in doc["users"]:
            if row["email"] == "admin@t.local":
                row["role"] = "admin"

    reviewer = TestClient(api_main.app)
    reviewer.post("/api/auth/signup", json={"email": "rev@t.local", "password": "testpassword"})
    return admin, reviewer


def test_grant_then_revoke_moves_the_reviewers_visibility(tmp_path, monkeypatch):
    """S4 + A2: the enforcement from Task 7 now has a way to be configured."""
    admin, reviewer = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    rev = auth_store.find_by_email(str(tmp_path), "rev@t.local")

    assert reviewer.get("/api/projects").json() == []
    assert admin.post(f"/api/admin/users/{rev.id}/grants", json={"slug": "tender-one"}).status_code == 201
    assert [p["slug"] for p in reviewer.get("/api/projects").json()] == ["tender-one"]
    assert admin.delete(f"/api/admin/users/{rev.id}/grants/tender-one").status_code == 204
    assert reviewer.get("/api/projects").json() == []


def test_granting_twice_is_idempotent(tmp_path, monkeypatch):
    """A duplicate row would survive a single revoke and silently keep access."""
    admin, reviewer = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    rev = auth_store.find_by_email(str(tmp_path), "rev@t.local")
    for _ in range(2):
        admin.post(f"/api/admin/users/{rev.id}/grants", json={"slug": "tender-one"})
    assert len(auth_store.read_auth(str(tmp_path))["grants"]) == 1
    admin.delete(f"/api/admin/users/{rev.id}/grants/tender-one")
    assert reviewer.get("/api/projects").json() == []


def test_a_reviewer_cannot_reach_the_admin_api(tmp_path, monkeypatch):
    admin, reviewer = _clients(tmp_path, monkeypatch)
    rev = auth_store.find_by_email(str(tmp_path), "rev@t.local")
    assert reviewer.get("/api/admin/users").status_code == 403
    assert reviewer.post(f"/api/admin/users/{rev.id}/grants", json={"slug": "x"}).status_code == 403
    assert reviewer.post("/api/admin/users", json={"email": "z@t.local", "password": "testpassword"}).status_code == 403
    assert reviewer.delete(f"/api/admin/users/{rev.id}").status_code == 403
    assert reviewer.delete(f"/api/admin/users/{rev.id}/grants/x").status_code == 403


def test_the_last_admin_cannot_be_deleted(tmp_path, monkeypatch):
    """An admin-less deployment is unrecoverable without hand-editing JSON.

    Deletion is the only way to reach that state: no route anywhere in this
    API changes a user's role, so "an admin demotes themselves" is not a
    reachable transition and there is nothing to pin about it. The brief's
    second half is vacuous rather than unimplemented — say so here instead of
    carrying a name that claims coverage this test does not have.

    What actually returns the 409 below is the *self-delete* guard, not the
    last-admin guard: the caller is an admin, so the sole admin they could
    name is themselves. Removing the last-admin check leaves this green.
    That check is deliberately kept anyway; `api/admin_routes.py` says why.
    """
    admin, _ = _clients(tmp_path, monkeypatch)
    me = auth_store.find_by_email(str(tmp_path), "admin@t.local")
    assert admin.delete(f"/api/admin/users/{me.id}").status_code == 409
    assert auth_store.find_by_id(str(tmp_path), me.id) is not None


def test_an_admin_cannot_delete_themselves_even_with_admins_to_spare(tmp_path, monkeypatch):
    """The self-delete guard is separate from the last-admin guard.

    With a second admin present the last-admin guard no longer fires, so
    without its own guard this call would succeed and take the caller's own
    session and role with it. Deleting the last-admin check alone leaves this
    test green; deleting the self check alone leaves only this one red.
    """
    admin, _ = _clients(tmp_path, monkeypatch)
    admin.post("/api/admin/users", json={
        "email": "second-admin@t.local", "password": "testpassword", "role": "admin",
    })
    me = auth_store.find_by_email(str(tmp_path), "admin@t.local")
    assert admin.delete(f"/api/admin/users/{me.id}").status_code == 409
    assert auth_store.find_by_id(str(tmp_path), me.id) is not None


def test_the_admin_user_list_never_carries_a_password_hash(tmp_path, monkeypatch):
    admin, _ = _clients(tmp_path, monkeypatch)
    res = admin.get("/api/admin/users")
    assert res.status_code == 200
    assert "password_hash" not in res.text and "scrypt$" not in res.text


def test_a_grant_naming_a_missing_project_is_refused_not_stored(tmp_path, monkeypatch):
    """The decision this task made explicit: a typo'd slug is a 404, not a
    silently-accepted grant that leaves a reviewer seeing nothing with no clue
    why."""
    admin, _ = _clients(tmp_path, monkeypatch)
    rev = auth_store.find_by_email(str(tmp_path), "rev@t.local")
    res = admin.post(f"/api/admin/users/{rev.id}/grants", json={"slug": "no-such-project"})
    assert res.status_code == 404
    assert auth_store.read_auth(str(tmp_path))["grants"] == []


def test_granting_to_a_nonexistent_user_404s(tmp_path, monkeypatch):
    admin, _ = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    res = admin.post("/api/admin/users/u_doesnotexist/grants", json={"slug": "tender-one"})
    assert res.status_code == 404
    assert auth_store.read_auth(str(tmp_path))["grants"] == []


def test_admin_can_create_a_user_directly(tmp_path, monkeypatch):
    admin, _ = _clients(tmp_path, monkeypatch)
    res = admin.post("/api/admin/users", json={"email": "new@t.local", "password": "testpassword"})
    assert res.status_code == 201
    body = res.json()
    assert body["email"] == "new@t.local"
    assert body["role"] == "reviewer"
    assert "password_hash" not in res.text
    # The new account is real: it can log in on its own client.
    fresh = TestClient(admin.app)
    login = fresh.post("/api/auth/login", json={"email": "new@t.local", "password": "testpassword"})
    assert login.status_code == 200


def test_admin_creating_a_duplicate_email_conflicts(tmp_path, monkeypatch):
    admin, _ = _clients(tmp_path, monkeypatch)
    admin.post("/api/admin/users", json={"email": "dup@t.local", "password": "testpassword"})
    res = admin.post("/api/admin/users", json={"email": "dup@t.local", "password": "testpassword"})
    assert res.status_code == 409


def test_deleting_a_nonexistent_user_404s(tmp_path, monkeypatch):
    admin, _ = _clients(tmp_path, monkeypatch)
    assert admin.delete("/api/admin/users/u_doesnotexist").status_code == 404


def test_deleting_a_user_prunes_their_grants(tmp_path, monkeypatch):
    """S4: auth.json.grants holds exactly the grants whose user still exists."""
    admin, reviewer = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    rev = auth_store.find_by_email(str(tmp_path), "rev@t.local")
    admin.post(f"/api/admin/users/{rev.id}/grants", json={"slug": "tender-one"})
    assert admin.delete(f"/api/admin/users/{rev.id}").status_code == 204
    assert auth_store.find_by_id(str(tmp_path), rev.id) is None
    assert auth_store.read_auth(str(tmp_path))["grants"] == []


def test_an_admin_may_delete_another_admin_when_not_the_last(tmp_path, monkeypatch):
    admin, _ = _clients(tmp_path, monkeypatch)
    res = admin.post("/api/admin/users", json={
        "email": "second-admin@t.local", "password": "testpassword", "role": "admin",
    })
    assert res.status_code == 201
    second = auth_store.find_by_email(str(tmp_path), "second-admin@t.local")
    assert admin.delete(f"/api/admin/users/{second.id}").status_code == 204


def test_revoking_a_grant_that_never_existed_is_a_no_op(tmp_path, monkeypatch):
    admin, reviewer = _clients(tmp_path, monkeypatch)
    rev = auth_store.find_by_email(str(tmp_path), "rev@t.local")
    assert admin.delete(f"/api/admin/users/{rev.id}/grants/never-granted").status_code == 204
    assert auth_store.read_auth(str(tmp_path))["grants"] == []
