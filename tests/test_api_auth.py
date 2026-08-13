"""Key-free tests for the auth routes."""

from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return TestClient(api_main.app)


def test_signup_then_me_returns_a_reviewer(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    res = client.post("/api/auth/signup", json={"email": "R@Client.com", "password": "longenough"})
    assert res.status_code == 201
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "r@client.com"
    assert me.json()["role"] == "reviewer"


def test_signup_cannot_choose_its_own_role(tmp_path, monkeypatch):
    """Privilege escalation: role is not an input, so a crafted body cannot set it."""
    client = _client(tmp_path, monkeypatch)
    client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough", "role": "admin"})
    assert client.get("/api/auth/me").json()["role"] == "reviewer"


def test_signup_rejects_short_passwords(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    res = client.post("/api/auth/signup", json={"email": "a@b.com", "password": "short"})
    assert res.status_code == 422


def test_signup_rejects_a_duplicate_email(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough"})
    res = client.post("/api/auth/signup", json={"email": "A@B.com", "password": "different"})
    assert res.status_code == 409


def test_signup_is_refused_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOW_SIGNUP", "0")
    client = _client(tmp_path, monkeypatch)
    res = client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough"})
    assert res.status_code == 403


def test_login_sets_an_httponly_cookie(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough"})
    client.cookies.clear()
    res = client.post("/api/auth/login", json={"email": "a@b.com", "password": "longenough"})
    assert res.status_code == 200
    cookie = res.headers["set-cookie"]
    assert "te_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    # Plain-HTTP request (TestClient's default scheme): `Secure` must be
    # absent, or the documented plain-HTTP docker-compose deployment breaks.
    assert "Secure" not in cookie


def test_login_over_https_marks_the_cookie_secure(tmp_path, monkeypatch):
    """`Secure` is derived from the request scheme, not hardcoded either way."""
    client = _client(tmp_path, monkeypatch)
    client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough"})
    client.cookies.clear()
    https_client = TestClient(client.app, base_url="https://testserver")
    res = https_client.post("/api/auth/login", json={"email": "a@b.com", "password": "longenough"})
    assert res.status_code == 200
    cookie = res.headers["set-cookie"]
    assert "Secure" in cookie


def test_wrong_password_and_unknown_email_give_the_same_answer(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough"})
    wrong = client.post("/api/auth/login", json={"email": "a@b.com", "password": "wrongpass"})
    unknown = client.post("/api/auth/login", json={"email": "nobody@b.com", "password": "wrongpass"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"] == "Invalid email or password."


def test_me_without_a_session_is_401(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/auth/me").status_code == 401


def test_logout_invalidates_the_session(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough"})
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_password_change_keeps_this_session_and_drops_the_others(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough"})
    other = TestClient(client.app)
    other.post("/api/auth/login", json={"email": "a@b.com", "password": "longenough"})
    res = client.post("/api/auth/password", json={"current": "longenough", "next": "newlongenough"})
    assert res.status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    assert other.get("/api/auth/me").status_code == 401


def test_no_response_ever_carries_a_password_hash(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough"})
    for res in [client.get("/api/auth/me"),
                client.post("/api/auth/login", json={"email": "a@b.com", "password": "longenough"})]:
        assert "password_hash" not in res.text
        assert "scrypt$" not in res.text


def test_login_cookie_dies_with_the_browser(tmp_path, monkeypatch):
    """No Max-Age and no Expires: a persistent cookie is what silently carried a
    stale reviewer session into a fresh launch of the platform."""
    client = _client(tmp_path, monkeypatch)
    client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough"})

    res = client.post("/api/auth/login", json={"email": "a@b.com", "password": "longenough"})
    assert res.status_code == 200

    cookie = res.headers["set-cookie"]
    assert "te_session=" in cookie
    assert "max-age" not in cookie.lower(), cookie
    assert "expires" not in cookie.lower(), cookie
    # the protections that must survive the change
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()


def test_app_startup_signs_everyone_out(tmp_path, monkeypatch):
    """The reported bug, end to end: a session that was valid before the API
    started must not let anyone straight back in after it starts.

    Bare TestClient() does not run lifespan; only `with TestClient(app) as c:`
    does — same reason as test_auth_bootstrap.py's startup test.
    """
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))

    from api.auth import store as auth_store
    user = auth_store.create_user(str(tmp_path), "abc@gmail.com", "hash", "reviewer")
    stale = auth_store.create_session(str(tmp_path), user.id)
    assert auth_store.resolve_session(str(tmp_path), stale) is not None

    with TestClient(api_main.app) as client:
        assert auth_store.resolve_session(str(tmp_path), stale) is None
        client.cookies.set("te_session", stale)
        assert client.get("/api/auth/me").status_code == 401


def test_startup_leaves_a_session_created_after_it_alone(tmp_path, monkeypatch):
    """Only sessions predating the boot are cleared — signing in after startup
    has to work, or nobody can use the app at all."""
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))

    with TestClient(api_main.app) as client:
        client.post("/api/auth/signup", json={"email": "a@b.com", "password": "longenough"})
        assert client.get("/api/auth/me").status_code == 200
