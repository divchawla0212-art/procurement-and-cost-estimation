"""The two-run mutation matrix (Task 9): auth's own two-run hazards.

Per-task TDD produces single-run, single-module tests. Phase 2 of this
codebase's earlier history shipped seven defects that survived exactly that
kind of test, because every one of them needed two requests or two modules to
see. This module is the structural answer for Phase 1 of the auth plan: each
test below makes a first request, mutates state the way a real admin action,
a role change, a deleted file, a restart, or a second concurrent caller
would, and then makes a second request that must behave differently because
of the mutation -- not just assert that the mutation happened.

The ten rows, from `.superpowers/sdd/2026-08-06-auth-user-hierarchy/task-9-brief.md`:

| # | mutation between run 1 and run 2                          | invariant | test |
|---|-------------------------------------------------------------|-----------|------|
| 1 | user deleted while their cookie is still held                | S3        | `test_deleted_user_cannot_keep_using_their_cookie` |
| 2 | a grant is revoked while the session is live                  | A2        | `test_revoked_grant_takes_effect_without_a_re_login` |
| 3 | a granted project is deleted from disk                        | A2, S4    | `test_a_project_deleted_from_disk_vanishes_despite_a_live_grant` |
| 4 | two grants to different users written concurrently            | S4        | `test_two_concurrent_grants_to_different_users_both_survive` |
| 5 | a role is changed reviewer -> admin between requests          | A2        | `test_promotion_to_admin_takes_effect_without_a_re_login` |
| 6 | a password is changed while a second session is live          | S3        | `test_password_change_keeps_the_changer_in_and_signs_the_other_out` |
| 7 | a route is added to `app.routes` with no guard                | A1        | already covered -- see note below, not duplicated here |
| 8 | the process restarts between requests                         | S3        | `test_a_session_survives_a_process_restart` |
| 9 | `ALLOW_SIGNUP` flips 1 -> 0 between requests                   | --        | `test_disabling_signup_mid_session_blocks_new_signups_not_existing_sessions` |
| 10| the same email signs up twice concurrently                    | S1        | `test_two_concurrent_signups_for_the_same_email_leave_exactly_one_user` |

Row 7 is not duplicated here. `tests/test_auth_middleware.py::
test_every_api_route_outside_the_allowlist_requires_a_session` already sweeps
every route on `app.routes` outside `PUBLIC_PATHS` and asserts a 401 with no
session -- a route added later, with no guard, fails that sweep by name the
day it is written. Re-implementing the same sweep here would be a fourth copy
of the same logic for no additional coverage.

Rows 4 and 10 are real concurrency, exercised with actual `threading.Thread`s
(not asyncio tasks pretending to interleave) plus a short injected delay
inside the write path. Without that delay, two fast threads racing through a
correctly-locked critical section will almost always just serialize by luck
of the scheduler, and the test would pass whether or not the lock actually
worked. The delay forces the two threads' read-modify-write windows to
overlap in real wall-clock time, so the assertion is actually exercising
`api/auth/store.py`'s `_LOCK`, not the scheduler's mood.

Row 8 is exercised as a real OS process restart (two separate `uvicorn`
subprocesses against the same `auth.json`), not a fresh `TestClient` against
the same long-lived Python process. A fresh `TestClient` still shares the
same interpreter, the same imported modules, and any module-level state a
broken implementation might use to cache sessions in memory -- so it would
not detect that class of defect. Only tearing down the process and starting
a new one proves sessions are recovered from the file rather than survived
in memory. See `test_a_session_survives_a_process_restart` for the concrete
probe that confirms this distinction actually matters.
"""
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import threading
import time

import httpx
from fastapi.testclient import TestClient

from api.auth import store as auth_store
from procurement.store import layout
from tests.auth_helpers import signed_in_admin

REVIEWER_EMAIL = "rev@t.local"
REVIEWER_PASSWORD = "testpassword"

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _clients(tmp_path, monkeypatch):
    """Admin + reviewer client pair sharing one `auth.json` under `tmp_path`.

    Mirrors `tests/test_api_authorization.py`'s `_clients`, reusing
    `tests/auth_helpers.signed_in_admin` for the admin half rather than
    inlining a fourth copy of the seed-and-promote block.
    """
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))

    admin = signed_in_admin(TestClient(api_main.app), tmp_path)

    reviewer = TestClient(api_main.app)
    res = reviewer.post("/api/auth/signup",
                        json={"email": REVIEWER_EMAIL, "password": REVIEWER_PASSWORD})
    assert res.status_code == 201, f"reviewer signup failed: {res.status_code} {res.text}"
    return admin, reviewer


def _grant(tmp_path, user_id: str, slug: str) -> None:
    """Write a grant directly into `auth.json`.

    Task 10's admin API to create grants does not exist yet -- this task's
    brief is explicit that rows needing one write straight through
    `store.locked_update`, the way `tests/test_api_authorization.py` already
    does.
    """
    with auth_store.locked_update(str(tmp_path)) as doc:
        doc["grants"].append({
            "user_id": user_id, "slug": slug,
            "granted_at": "2026-08-06T00:00:00+00:00", "granted_by": "u_admin",
        })


# --- Row 1 ------------------------------------------------------------------

def test_deleted_user_cannot_keep_using_their_cookie(tmp_path, monkeypatch):
    """Row 1 defends S3: a session must not outlive the account it authenticates.

    `_clients` signs in both an admin and a reviewer, so the admin's own
    session is expected to remain -- the invariant is "no row for the
    deleted id", not "no rows at all" (`auth.json.sessions` is shared by
    every signed-in user, not scoped to the one being deleted)."""
    admin, reviewer = _clients(tmp_path, monkeypatch)
    assert reviewer.get("/api/auth/me").status_code == 200          # run 1
    rev = auth_store.find_by_email(str(tmp_path), REVIEWER_EMAIL)
    auth_store.delete_user(str(tmp_path), rev.id)                   # mutation
    assert reviewer.get("/api/auth/me").status_code == 401          # run 2
    assert not any(s["user_id"] == rev.id
                  for s in auth_store.read_auth(str(tmp_path))["sessions"])


# --- Row 2 ------------------------------------------------------------------

def test_revoked_grant_takes_effect_without_a_re_login(tmp_path, monkeypatch):
    """Row 2 defends A2: `granted_slugs` is read fresh on every call, so
    revoking access must be visible on the very next request -- no logout,
    no new session, same cookie throughout."""
    admin, reviewer = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    rev = auth_store.find_by_email(str(tmp_path), REVIEWER_EMAIL)
    _grant(tmp_path, rev.id, "tender-one")

    assert [p["slug"] for p in reviewer.get("/api/projects").json()] == ["tender-one"]  # run 1

    with auth_store.locked_update(str(tmp_path)) as doc:
        doc["grants"] = [g for g in doc["grants"] if g["user_id"] != rev.id]  # mutation: revoke

    assert reviewer.get("/api/projects").json() == []  # run 2, same cookie


# --- Row 3 ------------------------------------------------------------------

def test_a_project_deleted_from_disk_vanishes_despite_a_live_grant(tmp_path, monkeypatch):
    """Row 3 defends A2 and S4: the project list is read off disk on every
    request. A grant to a project that no longer exists there must not
    resurrect it -- and the grant itself is left untouched by this deletion,
    so the disappearance has to be the disk read doing its job, not some
    incidental grant-pruning cleaning up after it."""
    admin, reviewer = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    rev = auth_store.find_by_email(str(tmp_path), REVIEWER_EMAIL)
    _grant(tmp_path, rev.id, "tender-one")

    assert [p["slug"] for p in reviewer.get("/api/projects").json()] == ["tender-one"]  # run 1

    shutil.rmtree(os.path.join(str(tmp_path), "tender-one"))  # mutation: gone from disk

    assert reviewer.get("/api/projects").json() == []  # run 2, same cookie, same grant
    # the stale grant itself must still be there -- the empty list has to come
    # from the disk read in list_projects, not from the grant being cleaned up
    assert any(g["slug"] == "tender-one"
               for g in auth_store.read_auth(str(tmp_path))["grants"])


# --- Row 4 --------------------------------------------------------------

def test_two_concurrent_grants_to_different_users_both_survive(tmp_path, monkeypatch):
    """Row 4 defends S4: `locked_update`'s single lock must serialize
    concurrent writers, so two admins granting two different users at the
    same moment don't lose one of the writes.

    A short sleep is injected inside the write itself (`layout.
    atomic_write_json`) and both threads are released from a `threading.
    Barrier` at the same instant, so their read-modify-write windows are
    forced to overlap in real time. Without the injected delay, two threads
    this fast would almost always just serialize by scheduler luck, and the
    test would pass whether or not the lock actually worked.
    """
    admin, reviewer = _clients(tmp_path, monkeypatch)
    root = str(tmp_path)
    u1 = auth_store.create_user(root, "g1@t.local", "hash", "reviewer")
    u2 = auth_store.create_user(root, "g2@t.local", "hash", "reviewer")

    real_write = layout.atomic_write_json

    def slow_write(path, doc):
        time.sleep(0.05)
        real_write(path, doc)

    monkeypatch.setattr(layout, "atomic_write_json", slow_write)

    barrier = threading.Barrier(2)

    def grant(user_id, slug):
        barrier.wait()
        with auth_store.locked_update(root) as doc:
            doc["grants"].append({
                "user_id": user_id, "slug": slug,
                "granted_at": "2026-08-06T00:00:00+00:00", "granted_by": "u_admin",
            })

    t1 = threading.Thread(target=grant, args=(u1.id, "tender-one"))
    t2 = threading.Thread(target=grant, args=(u2.id, "tender-two"))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not t1.is_alive() and not t2.is_alive(), "a thread hung -- the lock deadlocked"

    grants = {(g["user_id"], g["slug"]) for g in auth_store.read_auth(root)["grants"]}
    assert grants == {(u1.id, "tender-one"), (u2.id, "tender-two")}


# --- Row 5 ------------------------------------------------------------------

def test_promotion_to_admin_takes_effect_without_a_re_login(tmp_path, monkeypatch):
    """Row 5 defends A2: `list_projects` re-derives visibility from the
    caller's live role on every request, so a promotion is visible on the
    very next call -- same cookie, no new session."""
    admin, reviewer = _clients(tmp_path, monkeypatch)
    admin.post("/api/projects", json={"name": "Tender One"})
    admin.post("/api/projects", json={"name": "Tender Two"})

    assert reviewer.get("/api/projects").json() == []  # run 1: plain reviewer, no grants

    with auth_store.locked_update(str(tmp_path)) as doc:
        for row in doc["users"]:
            if row["email"] == REVIEWER_EMAIL:
                row["role"] = "admin"  # mutation

    slugs = {p["slug"] for p in reviewer.get("/api/projects").json()}  # run 2, same cookie
    assert slugs == {"tender-one", "tender-two"}


# --- Row 6 ------------------------------------------------------------------

def test_password_change_keeps_the_changer_in_and_signs_the_other_out(tmp_path, monkeypatch):
    """Row 6 defends S3: changing a password must revoke every *other*
    session for that account while keeping the session that made the change
    signed in -- `store.delete_sessions_for(..., keep_token=...)`."""
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))

    changer = TestClient(api_main.app)
    changer.post("/api/auth/signup",
                json={"email": "two-session@t.local", "password": "testpassword"})

    other = TestClient(api_main.app)
    login = other.post("/api/auth/login",
                       json={"email": "two-session@t.local", "password": "testpassword"})
    assert login.status_code == 200
    assert other.get("/api/auth/me").status_code == 200  # run 1: both sessions live

    change = changer.post("/api/auth/password",
                          json={"current": "testpassword", "next": "newpassword1"})
    assert change.status_code == 200  # mutation

    assert changer.get("/api/auth/me").status_code == 200  # run 2: the changer survives
    assert other.get("/api/auth/me").status_code == 401     # run 2: the other session 401s


# --- Row 8 --------------------------------------------------------------

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_server(root: str, port: int) -> tuple[subprocess.Popen, str]:
    env = dict(os.environ)
    env["PROCUREMENT_PROJECTS_ROOT"] = root
    env["LLM_PROVIDER"] = "mock"
    env.pop("ADMIN_EMAIL", None)
    env.pop("ADMIN_PASSWORD", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read()
            raise RuntimeError(f"uvicorn exited before becoming healthy:\n{out}")
        try:
            if httpx.get(base + "/api/health", timeout=1).status_code == 200:
                return proc, base
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    proc.kill()
    raise RuntimeError("uvicorn never became healthy within 15s")


def _stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def test_a_session_survives_a_process_restart(tmp_path):
    """Row 8 defends S3, and is the reason sessions are persisted to
    `auth.json` at all rather than kept in memory.

    This runs two real, separate `uvicorn` processes against the same
    `PROCUREMENT_PROJECTS_ROOT`, killing the first before starting the
    second -- a genuine process boundary. A fresh `TestClient` against the
    same long-lived pytest process would not do the same job: it shares the
    interpreter, so any module-level cache a broken implementation kept
    sessions in would still be there. Only ending the process and starting a
    new one proves the session survives strictly because it was on disk.
    """
    root = str(tmp_path)
    port1 = _free_port()
    proc1, base1 = _start_server(root, port1)
    try:
        res = httpx.post(base1 + "/api/auth/signup",
                         json={"email": "restart@t.local", "password": "testpassword"})
        assert res.status_code == 201  # run 1
        cookie = res.cookies.get("te_session")
        assert cookie
    finally:
        _stop_server(proc1)  # the restart

    port2 = _free_port()
    proc2, base2 = _start_server(root, port2)
    try:
        me = httpx.get(base2 + "/api/auth/me", cookies={"te_session": cookie})
        assert me.status_code == 200  # run 2, brand-new process
        assert me.json()["email"] == "restart@t.local"
    finally:
        _stop_server(proc2)


# --- Row 9 --------------------------------------------------------------

def test_disabling_signup_mid_session_blocks_new_signups_not_existing_sessions(tmp_path, monkeypatch):
    """Row 9: flipping `ALLOW_SIGNUP` off between two requests must 403 the
    very next signup while leaving an already-registered account able to use
    its live session and to log in again -- the flag gates account
    creation, not existing access."""
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("ALLOW_SIGNUP", "1")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))

    existing = TestClient(api_main.app)
    res = existing.post("/api/auth/signup",
                        json={"email": "early@t.local", "password": "testpassword"})
    assert res.status_code == 201  # run 1
    assert existing.get("/api/auth/me").status_code == 200

    monkeypatch.setenv("ALLOW_SIGNUP", "0")  # mutation

    late = TestClient(api_main.app)
    blocked = late.post("/api/auth/signup",
                       json={"email": "late@t.local", "password": "testpassword"})
    assert blocked.status_code == 403  # run 2

    assert existing.get("/api/auth/me").status_code == 200  # existing session unaffected
    relogin = TestClient(api_main.app)
    still_works = relogin.post("/api/auth/login",
                               json={"email": "early@t.local", "password": "testpassword"})
    assert still_works.status_code == 200  # existing account unaffected


# --- Row 10 -------------------------------------------------------------

def test_two_concurrent_signups_for_the_same_email_leave_exactly_one_user(tmp_path, monkeypatch):
    """Row 10 defends S1: two real, concurrent signup requests for the same
    address must not both create an account.

    Same technique as row 4 -- an injected write delay plus a
    `threading.Barrier` force the two threads' check-then-write windows in
    `store.create_user` to overlap in real time, so this actually exercises
    the lock rather than passing on however fast the OS happens to
    interleave two threads.
    """
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))

    real_write = layout.atomic_write_json

    def slow_write(path, doc):
        time.sleep(0.05)
        real_write(path, doc)

    monkeypatch.setattr(layout, "atomic_write_json", slow_write)

    client_a, client_b = TestClient(api_main.app), TestClient(api_main.app)
    barrier = threading.Barrier(2)
    results: dict[str, int] = {}

    def signup(name, client):
        barrier.wait()
        res = client.post("/api/auth/signup",
                          json={"email": "dup@t.local", "password": "testpassword"})
        results[name] = res.status_code

    t1 = threading.Thread(target=signup, args=("a", client_a))
    t2 = threading.Thread(target=signup, args=("b", client_b))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not t1.is_alive() and not t2.is_alive(), "a thread hung -- the lock deadlocked"

    assert sorted(results.values()) == [201, 409]  # one wins, one is refused

    root = str(tmp_path)
    matches = [u for u in auth_store.read_auth(root)["users"] if u["email"] == "dup@t.local"]
    assert len(matches) == 1
