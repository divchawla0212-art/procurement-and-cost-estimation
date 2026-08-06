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
