import threading
import time

from fastapi.testclient import TestClient

from api.auth import store as auth_store
from procurement.store import layout


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


def test_two_admins_deleting_each_other_at_once_cannot_reach_zero_admins(tmp_path, monkeypatch):
    """The never-zero-admins rule, at the only moment it is reachable.

    Sequentially the self-delete guard already covers every path to an
    admin-less deployment, because the caller is an admin and so the only
    "last admin" they can name is themselves. Concurrently it does not: two
    admins deleting *each other* both pass that guard. If the admin count is
    read outside the write's lock, both threads read "two admins, fine" and
    both writes land on zero.

    Same forcing technique as the Task 9 matrix rows: a barrier releases both
    threads together and a delay injected into the write itself holds each
    read-modify-write window open long enough to overlap for real.
    """
    admin_a, _ = _clients(tmp_path, monkeypatch)
    root = str(tmp_path)
    admin_a.post("/api/admin/users", json={
        "email": "admin-b@t.local", "password": "testpassword", "role": "admin",
    })
    admin_b = TestClient(admin_a.app)
    admin_b.post("/api/auth/login", json={"email": "admin-b@t.local", "password": "testpassword"})

    a = auth_store.find_by_email(root, "admin@t.local")
    b = auth_store.find_by_email(root, "admin-b@t.local")

    real_write = layout.atomic_write_json

    def slow_write(path, doc):
        time.sleep(0.05)
        real_write(path, doc)

    monkeypatch.setattr(layout, "atomic_write_json", slow_write)

    barrier = threading.Barrier(2)
    statuses: dict[str, int] = {}

    def delete(client, label, target_id):
        barrier.wait()
        statuses[label] = client.delete(f"/api/admin/users/{target_id}").status_code

    threads = [
        threading.Thread(target=delete, args=(admin_a, "a_deletes_b", b.id)),
        threading.Thread(target=delete, args=(admin_b, "b_deletes_a", a.id)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    admins = [u for u in auth_store.list_users(root) if u.role == "admin"]
    assert len(admins) >= 1, f"deployment left with no admin; statuses were {statuses}"
    # Exactly one of the two is refused, and with the 409 the rule owns —
    # not, say, a 500 from a write that raced and lost.
    assert sorted(statuses.values()) == [204, 409], statuses


def test_a_grant_racing_that_users_deletion_leaves_no_orphan_row(tmp_path, monkeypatch):
    """S4 head-on: `grants` holds exactly the grants whose user still exists.

    A user-exists check made before the write is a check made in a different
    critical section. The delete prunes what is present when it runs, so an
    append that lands afterwards is a row it already had no chance to prune.
    Both threads are released together with the write slowed, so the grant's
    read-modify-write really does span the delete.
    """
    admin, _ = _clients(tmp_path, monkeypatch)
    root = str(tmp_path)
    admin.post("/api/projects", json={"name": "Tender One"})
    rev = auth_store.find_by_email(root, "rev@t.local")

    real_write = layout.atomic_write_json

    def slow_write(path, doc):
        time.sleep(0.05)
        real_write(path, doc)

    monkeypatch.setattr(layout, "atomic_write_json", slow_write)

    barrier = threading.Barrier(2)
    results: dict[str, int] = {}

    def do(label, fn):
        barrier.wait()
        results[label] = fn()

    threads = [
        threading.Thread(target=do, args=("grant", lambda: admin.post(
            f"/api/admin/users/{rev.id}/grants", json={"slug": "tender-one"}).status_code)),
        threading.Thread(target=do, args=("delete", lambda: admin.delete(
            f"/api/admin/users/{rev.id}").status_code)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    doc = auth_store.read_auth(root)
    live_ids = {u["id"] for u in doc["users"]}
    assert all(g["user_id"] in live_ids for g in doc["grants"]), (
        f"orphan grant survived; results were {results}, grants {doc['grants']}"
    )


def test_a_slug_differing_only_in_case_is_refused(tmp_path, monkeypatch):
    """A regression guard for Windows and macOS specifically.

    Case-insensitive filesystems resolve "Tender-One" to the real project, so
    an existence check that asks the filesystem answers yes and the grant is
    then stored verbatim — where `granted_slugs` compares it against
    "tender-one" and never matches. The admin sees 201, the reviewer sees
    nothing, and neither can tell why. On Linux/CI this passes either way, so
    it can only fail on a developer workstation.
    """
    admin, reviewer = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    rev = auth_store.find_by_email(str(tmp_path), "rev@t.local")

    res = admin.post(f"/api/admin/users/{rev.id}/grants", json={"slug": "Tender-One"})
    assert res.status_code == 404, res.text
    assert auth_store.read_auth(str(tmp_path))["grants"] == []
    assert reviewer.get("/api/projects").json() == []


def test_the_user_list_carries_each_users_grants(tmp_path, monkeypatch):
    """The admin screen draws a checkbox per project per reviewer, so the list
    has to say which are already ticked."""
    admin, _ = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    admin.post("/api/projects", json={"name": "Tender Two"})
    rev = auth_store.find_by_email(str(tmp_path), "rev@t.local")
    admin.post(f"/api/admin/users/{rev.id}/grants", json={"slug": "tender-two"})

    by_email = {u["email"]: u for u in admin.get("/api/admin/users").json()}
    assert by_email["rev@t.local"]["grants"] == ["tender-two"]
    assert by_email["admin@t.local"]["grants"] == []


def test_an_admin_can_create_users_while_public_signup_is_closed(tmp_path, monkeypatch):
    """`ALLOW_SIGNUP=0` closes *public* self-registration. Closing it is
    exactly when an admin needs to add people by hand, and this route is
    already behind require_admin, so there is no escalation to guard."""
    admin, _ = _clients(tmp_path, monkeypatch)
    monkeypatch.setenv("ALLOW_SIGNUP", "0")
    assert admin.post("/api/auth/signup", json={
        "email": "public@t.local", "password": "testpassword"}).status_code == 403
    assert admin.post("/api/admin/users", json={
        "email": "byhand@t.local", "password": "testpassword"}).status_code == 201


def test_revoking_a_grant_that_never_existed_is_a_no_op(tmp_path, monkeypatch):
    admin, reviewer = _clients(tmp_path, monkeypatch)
    rev = auth_store.find_by_email(str(tmp_path), "rev@t.local")
    assert admin.delete(f"/api/admin/users/{rev.id}/grants/never-granted").status_code == 204
    assert auth_store.read_auth(str(tmp_path))["grants"] == []
