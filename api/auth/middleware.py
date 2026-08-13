"""Fail-closed authentication: every `/api/` path needs a session by default.

The enforcement point is one middleware with a named allowlist, deliberately
not a per-route dependency. Per-route guards are fail-open — a route added
next year is public until somebody remembers to decorate it, which is exactly
how this platform came to serve every project to every caller. A prefix test
plus `PUBLIC_PATHS` inverts that: a new route is protected the day it is
written, and making one public is an edit to a named constant that shows up
in review.

This module answers only "is there a valid session?". Whether *this* user may
touch *that* project is Task 7's authorization layer.
"""
import logging
import os

from fastapi.responses import JSONResponse

from api.auth import store
from api.auth.routes import COOKIE_NAME

log = logging.getLogger(__name__)

# Exactly three. Adding a fourth is a reviewable decision, not a detail.
PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/api/auth/signup"}

# Values that turn the development bypass on. Anything else — including the
# variable being absent, empty, "0" or "false" — leaves authentication on.
_TRUTHY = {"1", "true", "yes", "on"}


def bypass_enabled() -> bool:
    """Whether `AUTH_DISABLED` is set to something affirmative.

    Read at call time, not at import, so a test can set it per-case and so the
    launcher can turn it on without the import order mattering.
    """
    return os.environ.get("AUTH_DISABLED", "").strip().lower() in _TRUTHY


def _bypass_user(root: str):
    """The identity an unauthenticated caller acts as when the bypass is on.

    It **borrows** an existing account and never mints one: inventing a user
    would put a real row in `auth.json` that nobody created, with a role nobody
    granted. `DEV_USER_EMAIL` picks a specific account; otherwise the first
    administrator is used. With no administrator at all there is nobody to act
    as, and the middleware stays fail-closed rather than inventing someone.
    """
    wanted = os.environ.get("DEV_USER_EMAIL", "").strip()
    if wanted:
        return store.find_by_email(root, wanted)
    admins = [u for u in store.list_users(root) if u.role == "admin"]
    return admins[0] if admins else None


def install(app) -> None:
    """Register the authentication middleware (invariant A1).

    MUST be called before `add_middleware(CORSMiddleware, ...)`. Starlette
    inserts each middleware at position 0 of `app.user_middleware` and builds
    the stack by wrapping in reverse, so the *last* registered is the
    outermost. If auth were outermost, a 401 would never pass back through
    CORS and a cross-origin browser could not even read the status.
    """

    @app.middleware("http")
    async def authenticate(request, call_next):
        path = request.url.path
        if not path.startswith("/api/") or path in PUBLIC_PATHS:
            # Non-API paths carry the SPA bundle and the login screen from the
            # StaticFiles mount at "/". Challenging them would make the app
            # unreachable — there would be nowhere to log in.
            return await call_next(request)
        if request.method == "OPTIONS":  # a CORS preflight carries no cookie
            return await call_next(request)

        # Late import, read at call time: tests monkeypatch `api.main.ROOT`,
        # and a module-level binding would resolve sessions against the real
        # `projects/` directory. Same reason as `routes.store_root`.
        import api.main as api_main

        user = store.resolve_session(api_main.ROOT, request.cookies.get(COOKIE_NAME, ""))

        if user is None and bypass_enabled():
            # Development bypass. Deliberately placed *after* the normal
            # resolve, so a real session still wins and is still attributed to
            # the person holding it — the bypass only fills the gap where a
            # 401 would otherwise be.
            user = _bypass_user(api_main.ROOT)
            if user is not None:
                log.warning(
                    "AUTH_DISABLED is set: serving %s as %s with no session. "
                    "Never run this way anywhere reachable by anyone else.",
                    path, user.email,
                )

        if user is None:
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        request.state.user = user
        return await call_next(request)
