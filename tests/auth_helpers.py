"""Shared test helper: give a `TestClient` an admin session.

Every `/api/` path outside `middleware.PUBLIC_PATHS` now needs a session
(invariant A1), so the five `tests/test_api_*.py` suites that exercise the
review and setup routes have to sign in before they assert anything. This
lives in one module rather than being pasted into five `_client` helpers
because the promotion step below has a rule that is easy to get wrong, and a
rule worth stating is worth stating once.
"""
from fastapi.testclient import TestClient

from api.auth import store as auth_store

ADMIN_EMAIL = "admin@test.local"
ADMIN_PASSWORD = "testpassword"


def signed_in_admin(client: TestClient, tmp_path) -> TestClient:
    """Sign `client` in as an admin, and hand the same client back.

    Signup is an allowlisted path, so it works before any session exists. It
    always creates a reviewer — role is not a signup input — so the promotion
    happens in the store, the only place role is settable.
    """
    res = client.post("/api/auth/signup",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert res.status_code == 201, f"test admin signup failed: {res.status_code} {res.text}"
    # Address the row by email, never by index: list order is not stable, and
    # that is the same rule invariant S1 states for stored collections.
    with auth_store.locked_update(str(tmp_path)) as doc:
        for row in doc["users"]:
            if row["email"] == ADMIN_EMAIL:
                row["role"] = "admin"
    return client
