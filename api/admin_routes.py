"""Admin-only user and grant management: `/api/admin/*`.

Every route here sits behind `Depends(require_admin)`, on top of the
fail-closed session middleware that already challenges every `/api/` path
(`api/auth/middleware.py`). This module never touches `auth.json` directly —
`<ROOT>/auth.json` is written only from `api/auth/store.py`, and every read
here goes through a store function too, so a serialization change in the
store is the only place that could ever leak `password_hash`.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from api.auth import passwords, store
from api.auth.deps import require_admin
from api.auth.models import User

router = APIRouter(prefix="/api/admin", tags=["admin"])

MIN_PASSWORD = 8
ROLES = {"admin", "reviewer"}


def store_root(request: Request) -> str:
    """Read `api.main.ROOT` at call time, not at import time.

    Tests do `monkeypatch.setattr(api_main, "ROOT", str(tmp_path))`; a
    module-level `from api.main import ROOT` would bind the pre-patch value.
    Mirrors `api/auth/routes.py`'s `store_root`.
    """
    import api.main as api_main
    return api_main.ROOT


def _project_exists(slug: str) -> bool:
    """Existence check for a project slug, reusing `api/main.py`'s
    `_load_or_404` rather than a second, divergent existence check.

    An unsafe slug and a missing project both mean "no" for this purpose —
    either way there is nothing to grant access to.
    """
    import api.main as api_main
    try:
        api_main._load_or_404(slug)
        return True
    except HTTPException:
        return False


class NewUser(BaseModel):
    # `str`, not pydantic's EmailStr: email-validator is not installed and the
    # project's global constraints forbid adding it. Mirrors
    # `api/auth/routes.py`'s `Credentials` shape check.
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=MIN_PASSWORD)
    role: str = "reviewer"

    @field_validator("email")
    @classmethod
    def _looks_like_an_address(cls, v: str) -> str:
        local, _, domain = v.strip().partition("@")
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("not an email address")
        return v

    @field_validator("role")
    @classmethod
    def _known_role(cls, v: str) -> str:
        if v not in ROLES:
            raise ValueError(f"role must be one of {sorted(ROLES)}")
        return v


class GrantBody(BaseModel):
    slug: str = Field(min_length=1)


@router.get("/users")
def list_users(request: Request, _: User = Depends(require_admin)) -> list[dict]:
    return [u.model_dump() for u in store.list_users(store_root(request))]


@router.post("/users", status_code=201)
def create_user(body: NewUser, request: Request, _: User = Depends(require_admin)) -> dict:
    root = store_root(request)
    password_hash = passwords.hash_password(body.password)
    try:
        user = store.create_user(root, body.email, password_hash, role=body.role)
    except store.EmailTaken as exc:
        raise HTTPException(409, "An account with this email already exists.") from exc
    return user.model_dump()


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: str, request: Request, caller: User = Depends(require_admin)) -> None:
    root = store_root(request)
    target = store.find_by_id(root, user_id)
    if target is None:
        raise HTTPException(404, "no such user")
    # The self-delete guard is the one that carries the weight. It is also
    # what keeps the deployment out of the admin-less inert state that
    # bootstrap.seed_admin_if_empty warns about: the caller is always an
    # admin, so a target who is the *last* admin can only ever be the caller.
    if target.id == caller.id:
        raise HTTPException(409, "cannot delete your own account")
    # Hence the check below is unreachable through this route today —
    # deleting it leaves the whole suite green, which is stated plainly here
    # rather than left for a reader to discover. It is kept as the guard that
    # states the actual invariant (never zero admins) rather than a proxy for
    # it, so a later route that can lower the admin count without being a
    # self-delete — a role change, a bulk import — fails closed instead of
    # silently bricking the deployment. Any such route must also test it.
    if target.role == "admin":
        admins = [u for u in store.list_users(root) if u.role == "admin"]
        if len(admins) <= 1:
            raise HTTPException(409, "cannot delete the last administrator")
    # store.delete_user cascades to grants and sessions in one locked write —
    # call it rather than reimplementing that cascade here.
    store.delete_user(root, user_id)


@router.post("/users/{user_id}/grants", status_code=201)
def create_grant(
    user_id: str, body: GrantBody, request: Request, caller: User = Depends(require_admin)
) -> dict:
    root = store_root(request)
    if store.find_by_id(root, user_id) is None:
        raise HTTPException(404, "no such user")
    if not _project_exists(body.slug):
        raise HTTPException(404, f"no such project: {body.slug}")
    store.grant(root, user_id, body.slug, granted_by=caller.id)
    return {"user_id": user_id, "slug": body.slug}


@router.delete("/users/{user_id}/grants/{slug}", status_code=204)
def delete_grant(user_id: str, slug: str, request: Request, _: User = Depends(require_admin)) -> None:
    store.revoke(store_root(request), user_id, slug)
