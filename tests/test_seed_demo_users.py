"""The demo-account seeder.

Its whole job is to be safe to re-run: the platform is demonstrated by handing
these logins to someone, and a second run must not change a password that
somebody has already been given.
"""
import json

import pytest

from api.auth import passwords, store
from tools import seed_demo_users


def test_seeding_creates_one_admin_and_two_reviewers(tmp_path):
    seed_demo_users.seed(str(tmp_path))

    users = store.list_users(str(tmp_path))
    by_role = sorted((u.role, u.email) for u in users)
    assert by_role == [
        ("admin", "admin@gmail.com"),
        ("reviewer", "reviewer.one@gmail.com"),
        ("reviewer", "reviewer.two@gmail.com"),
    ]


def test_the_admin_password_is_the_documented_one(tmp_path):
    seed_demo_users.seed(str(tmp_path))
    admin = store.find_by_email(str(tmp_path), "admin@gmail.com")
    stored = store.password_hash_for(str(tmp_path), admin.id)
    assert passwords.verify_password("Admin@1234", stored)


def test_reviewers_start_with_no_project_access(tmp_path):
    """Granting is the administrator's act. A pre-granted reviewer would hide
    the very hand-off the demo exists to show."""
    seed_demo_users.seed(str(tmp_path))
    for email in ("reviewer.one@gmail.com", "reviewer.two@gmail.com"):
        user = store.find_by_email(str(tmp_path), email)
        assert store.granted_slugs(str(tmp_path), user.id) == set()


def test_seeding_twice_creates_nothing_new(tmp_path):
    seed_demo_users.seed(str(tmp_path))
    rows = seed_demo_users.seed(str(tmp_path))

    assert len(store.list_users(str(tmp_path))) == 3
    assert all("already existed" in r["outcome"] for r in rows)


def test_re_running_does_not_change_an_existing_password(tmp_path):
    """Someone may already be using a password they were given; a second run
    of the seeder must not lock them out."""
    root = str(tmp_path)
    seed_demo_users.seed(root)
    admin = store.find_by_email(root, "admin@gmail.com")
    with store.locked_update(root) as doc:
        for record in doc["users"]:
            if record["id"] == admin.id:
                record["password_hash"] = passwords.hash_password("SomethingElse@9")

    seed_demo_users.seed(root)

    stored = store.password_hash_for(root, admin.id)
    assert passwords.verify_password("SomethingElse@9", stored)


def test_reset_passwords_forces_the_documented_password_back(tmp_path):
    root = str(tmp_path)
    seed_demo_users.seed(root)
    admin = store.find_by_email(root, "admin@gmail.com")
    with store.locked_update(root) as doc:
        for record in doc["users"]:
            if record["id"] == admin.id:
                record["password_hash"] = passwords.hash_password("SomethingElse@9")

    seed_demo_users.seed(root, reset_passwords=True)

    stored = store.password_hash_for(root, admin.id)
    assert passwords.verify_password("Admin@1234", stored)


def test_a_reset_signs_out_sessions_held_under_the_old_password(tmp_path):
    root = str(tmp_path)
    seed_demo_users.seed(root)
    admin = store.find_by_email(root, "admin@gmail.com")
    token = store.create_session(root, admin.id)
    assert store.resolve_session(root, token) is not None

    seed_demo_users.seed(root, reset_passwords=True)

    assert store.resolve_session(root, token) is None


def test_seeding_leaves_accounts_it_does_not_own_alone(tmp_path):
    root = str(tmp_path)
    store.create_user(root, "real.person@client.com", "hash", "reviewer")
    seed_demo_users.seed(root)
    assert store.find_by_email(root, "real.person@client.com") is not None


def test_the_credentials_file_lists_every_account_with_its_password(tmp_path):
    root = str(tmp_path)
    rows = seed_demo_users.seed(root)
    out = tmp_path / "demo-accounts.json"

    seed_demo_users.write_credentials_file(rows, out)

    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["url"] == "http://localhost:5173"
    assert {a["email"] for a in doc["accounts"]} == {
        "admin@gmail.com", "reviewer.one@gmail.com", "reviewer.two@gmail.com",
    }
    admin = next(a for a in doc["accounts"] if a["role"] == "admin")
    assert admin["password"] == "Admin@1234"


@pytest.mark.parametrize("spec", seed_demo_users.DEMO_ACCOUNTS)
def test_every_demo_password_clears_the_signup_minimum(spec):
    """A demo password the login form would itself reject is a broken demo."""
    from api.auth.routes import MIN_PASSWORD
    assert len(spec["password"]) >= MIN_PASSWORD
