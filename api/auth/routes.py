"""The `/api/auth/*` routes: signup, login, logout, me, password change.

The fail-closed authentication middleware arrives in Task 6. Until then,
`/me`, `/logout` and `/password` resolve the session themselves from the
cookie via `_require_session`, which is written so Task 6's middleware slots
in without a rewrite: it prefers `request.state.user` when present and only
falls back to reading the cookie itself when that attribute is absent.
"""
import os

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from api.auth import passwords, store
from api.auth.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "te_session"
MIN_PASSWORD = 8


def store_root(request: Request) -> str:
    """Read `api.main.ROOT` at call time, not at import time.

    Tests do `monkeypatch.setattr(api_main, "ROOT", str(tmp_path))`; a
    module-level `from api.main import ROOT` would bind the pre-patch value
    and every route would keep writing to the real `projects/` directory.
    The import is local to dodge the circular import: `api/main.py` imports
    this module's `router` at module load.
    """
    import api.main as api_main
    return api_main.ROOT


class Credentials(BaseModel):
    # `str`, not pydantic's EmailStr: EmailStr needs the email-validator
    # package, which is NOT installed here (verified) and which the project's
    # global constraints forbid adding. A shape check is all this needs — the
    # address is an identifier, and nothing in this system mails it.
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=MIN_PASSWORD)
    # Note the absence of `role`. Signup cannot elect its own privilege.

    @field_validator("email")
    @classmethod
    def _looks_like_an_address(cls, v: str) -> str:
        local, _, domain = v.strip().partition("@")
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("not an email address")
        return v


class PasswordChange(BaseModel):
    current: str
    next: str = Field(min_length=MIN_PASSWORD)


def set_session_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=store.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="Lax",
        secure=request.url.scheme == "https",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def _require_session(request: Request) -> User:
    # Task 6's middleware sets request.state.user. Prefer it when present so
    # these routes do not re-read auth.json on every call once it lands; fall
    # back to resolving the cookie ourselves, which is what makes these
    # routes correct before that middleware exists.
    user = getattr(request.state, "user", None)
    if user is not None:
        return user
    user = store.resolve_session(store_root(request), request.cookies.get(COOKIE_NAME, ""))
    if user is None:
        raise HTTPException(401, "authentication required")
    return user


@router.post("/signup", status_code=201)
def signup(body: Credentials, request: Request, response: Response) -> dict:
    if os.environ.get("ALLOW_SIGNUP", "1") == "0":
        raise HTTPException(403, "Sign-up is disabled.")
    root = store_root(request)
    password_hash = passwords.hash_password(body.password)
    try:
        user = store.create_user(root, body.email, password_hash, role="reviewer")
    except store.EmailTaken:
        raise HTTPException(409, "An account with this email already exists.")
    set_session_cookie(response, store.create_session(root, user.id), request)
    return user.model_dump()


@router.post("/login")
def login(body: Credentials, request: Request, response: Response) -> dict:
    root = store_root(request)
    user = store.find_by_email(root, body.email)
    stored = store.password_hash_for(root, user.id) if user else passwords.DUMMY_HASH
    # Always verify, even with no such user: equal work means equal timing.
    verified = passwords.verify_password(body.password, stored)
    if user is None or not verified:
        raise HTTPException(401, "Invalid email or password.")
    set_session_cookie(response, store.create_session(root, user.id), request)
    return user.model_dump()


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(COOKIE_NAME, "")
    if token:
        store.delete_session(store_root(request), token)
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict:
    return _require_session(request).model_dump()


@router.post("/password")
def change_password(body: PasswordChange, request: Request) -> dict:
    user = _require_session(request)
    root = store_root(request)
    stored = store.password_hash_for(root, user.id)
    if stored is None or not passwords.verify_password(body.current, stored):
        raise HTTPException(401, "Invalid email or password.")
    new_hash = passwords.hash_password(body.next)
    with store.locked_update(root) as doc:
        for record in doc["users"]:
            if record["id"] == user.id:
                record["password_hash"] = new_hash
                break
    token = request.cookies.get(COOKIE_NAME, "")
    store.delete_sessions_for(root, user.id, keep_token=token)
    return {"ok": True}
