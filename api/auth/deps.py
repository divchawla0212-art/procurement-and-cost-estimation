"""Authorization dependencies: which signed-in user may reach which route.

`api/auth/middleware.py` already answers "is there a valid session?" for
every `/api/` path outside its allowlist and sets `request.state.user`. These
dependencies answer the second question — "may *this* user touch *that*
project?" — for the routes that need finer-grained access than "any session".
"""
from fastapi import HTTPException, Request

from api.auth.models import User


def current_user(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if user is None:
        # Only reachable if a route outside PUBLIC_PATHS bypassed the middleware.
        raise HTTPException(401, "authentication required")
    return user


def require_admin(request: Request) -> User:
    user = current_user(request)
    if user.role != "admin":
        raise HTTPException(403, "administrator access required")
    return user


def require_project_access(slug: str, request: Request) -> User:
    """403, never 404 — a typo in a slug staff already know buys nothing by
    being hidden, so an unentitled reviewer gets a refusal, not a not-found.

    Admin short-circuits before any grant lookup: an admin's access is never
    conditional on `auth.json`'s grants list.
    """
    # Late import: tests monkeypatch `api.main.ROOT`, and a module-level
    # `from api.main import ROOT` would bind the pre-patch value. Same reason
    # as `api/auth/routes.py`'s `store_root` and the middleware's late import.
    import api.main as api_main
    from api.auth import store

    user = current_user(request)
    if user.role == "admin":
        return user
    if slug not in store.granted_slugs(api_main.ROOT, user.id):
        raise HTTPException(403, "no access to this project")
    return user
