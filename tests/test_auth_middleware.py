"""Key-free tests for the fail-closed authentication middleware (invariant A1)."""

from fastapi.testclient import TestClient

from api.auth.middleware import PUBLIC_PATHS


def _anon_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return TestClient(api_main.app)


def test_every_api_route_outside_the_allowlist_requires_a_session(tmp_path, monkeypatch):
    """A1, and the guard that outlives us: a route added later is covered by this test.

    Iterating app.routes means nobody has to remember to protect route 17.
    """
    client = _anon_client(tmp_path, monkeypatch)
    import api.main as api_main

    checked = 0
    for route in api_main.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path in PUBLIC_PATHS:
            continue
        for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
            probe = path.replace("{slug}", "any").replace("{vendor}", "any")
            assert "{" not in probe, f"{path} has a path parameter the probe cannot fill"
            res = client.request(method, probe)
            assert res.status_code == 401, f"{method} {path} answered {res.status_code}, not 401"
            checked += 1
    assert checked > 0, "the route sweep matched nothing — the probe is broken, not passing"


def test_the_allowlist_is_exactly_these_three_paths():
    """The sweep above *skips* PUBLIC_PATHS, so widening the allowlist is a way
    to green it while opening a route back up. Pin the set so that move has to
    edit a test that says out loud what it is doing."""
    assert PUBLIC_PATHS == {"/api/health", "/api/auth/login", "/api/auth/signup"}


def test_allowlisted_paths_stay_reachable_without_a_session(tmp_path, monkeypatch):
    client = _anon_client(tmp_path, monkeypatch)
    assert client.get("/api/health").status_code == 200


def test_preflight_is_not_challenged(tmp_path, monkeypatch):
    """Trap 2: an OPTIONS preflight carries no cookie and must not 401."""
    client = _anon_client(tmp_path, monkeypatch)
    res = client.options(
        "/api/projects",
        headers={"Origin": "http://localhost:5173",
                 "Access-Control-Request-Method": "GET"},
    )
    assert res.status_code < 400


def test_a_401_still_carries_cors_headers(tmp_path, monkeypatch):
    """Trap 1: if auth is registered outside CORS, this header goes missing."""
    client = _anon_client(tmp_path, monkeypatch)
    res = client.get("/api/projects", headers={"Origin": "http://localhost:5173"})
    assert res.status_code == 401
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_non_api_paths_are_not_challenged(tmp_path, monkeypatch):
    client = _anon_client(tmp_path, monkeypatch)
    assert client.get("/definitely-not-an-api-path").status_code != 401


def test_a_valid_session_reaches_a_guarded_route(tmp_path, monkeypatch):
    """The other half of A1: the middleware challenges, it does not block.

    Without this, every assertion above would still hold if the middleware
    401'd unconditionally.
    """
    client = _anon_client(tmp_path, monkeypatch)
    signup = client.post("/api/auth/signup",
                         json={"email": "reviewer@test.local", "password": "testpassword"})
    assert signup.status_code == 201
    assert client.get("/api/projects").status_code == 200
