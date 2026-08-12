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
from api.auth.routes import Credentials

router = APIRouter(prefix="/api/admin", tags=["admin"])

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
    """Whether `slug` names a real project, matched exactly.

    Deliberately NOT `api/main.py`'s `_load_or_404`, which answers by asking
    the filesystem: on Windows and macOS that resolves case-insensitively, so
    a grant for "Tender-One" would be accepted and then stored verbatim,
    where `granted_slugs` compares it against the real "tender-one" and never
    matches. The admin sees 201 and the reviewer still sees nothing — exactly
    the silent failure that refusing unknown slugs exists to prevent, arrived
    at by a different route. `list_projects` reads slugs off `os.listdir`, so
    comparing against those is case-exact on every platform.
    """
    import api.main as api_main
    from procurement import project as proj
    return slug in {p.slug for p in proj.list_projects(api_main.ROOT)}


class NewUser(Credentials):
    """Signup's shape check plus a role.

    Subclasses `Credentials` rather than restating the email and password
    rules: two copies of an input-validation rule drift, and the one thing
    this model adds is the one thing signup must never have — `role` is
    absent from `Credentials` precisely so a self-registration cannot elect
    its own privilege.
    """
    role: str = "reviewer"

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
    """Every user with the slugs they are granted.

    The grants ride along rather than living behind a second route: the admin
    screen renders a per-project checkbox per reviewer, so it needs the
    current state of every grant to draw one screen, and both come out of a
    single read of `auth.json` here.
    """
    root = store_root(request)
    grants = store.grants_by_user(root)
    return [
        {**u.model_dump(), "grants": grants.get(u.id, [])}
        for u in store.list_users(root)
    ]


@router.post("/users", status_code=201)
def create_user(body: NewUser, request: Request, _: User = Depends(require_admin)) -> dict:
    """Create an account directly, bypassing `ALLOW_SIGNUP`.

    Deliberate: that flag closes *public* self-registration, and closing it
    is exactly when an admin needs to be able to add people by hand. The
    privilege escalation it guards against is not reachable here — this route
    is already behind `require_admin`.
    """
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
    # An authorization rule about the caller, so it belongs here rather than
    # in the store: deleting your own account takes your own admin role with
    # it even when other admins remain.
    if target.id == caller.id:
        raise HTTPException(409, "cannot delete your own account")
    # The never-zero-admins rule is NOT checked here. Counting admins in this
    # process and deleting in another would be two critical sections, and two
    # admins deleting each other at the same instant would each read "two
    # admins, fine" and both writes would land. store.delete_user decides it
    # inside the same lock that does the write, and cascades to grants and
    # sessions there too.
    try:
        store.delete_user(root, user_id)
    except store.LastAdmin as exc:
        raise HTTPException(409, "cannot delete the last administrator") from exc


@router.post("/users/{user_id}/grants", status_code=201)
def create_grant(
    user_id: str, body: GrantBody, request: Request, caller: User = Depends(require_admin)
) -> dict:
    root = store_root(request)
    if not _project_exists(body.slug):
        raise HTTPException(404, f"no such project: {body.slug}")
    # The user's existence is checked by store.grant inside the lock, not
    # here — S4 says grants hold only rows whose user still exists, and a
    # pre-check here could pass just before that user is deleted, leaving an
    # orphan row that the delete had already finished pruning.
    try:
        store.grant(root, user_id, body.slug, granted_by=caller.id)
    except store.UnknownUser as exc:
        raise HTTPException(404, "no such user") from exc
    return {"user_id": user_id, "slug": body.slug}


@router.delete("/users/{user_id}/grants/{slug}", status_code=204)
def delete_grant(user_id: str, slug: str, request: Request, _: User = Depends(require_admin)) -> None:
    store.revoke(store_root(request), user_id, slug)
