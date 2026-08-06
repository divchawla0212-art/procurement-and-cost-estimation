"""Key-free tests for the auth.json store primitive."""

import json
import os

import pytest

from api.auth import store


def test_read_auth_on_missing_file_returns_empty_document(tmp_path):
    doc = store.read_auth(str(tmp_path))
    assert doc == {"version": 1, "users": [], "grants": [], "sessions": []}


def test_locked_update_persists_and_is_atomic(tmp_path):
    root = str(tmp_path)
    with store.locked_update(root) as doc:
        doc["users"].append({"id": "u_1", "email": "a@b.com"})
    on_disk = json.loads(open(store.auth_path(root), encoding="utf-8").read())
    assert on_disk["users"][0]["email"] == "a@b.com"
    assert not os.path.exists(store.auth_path(root) + ".tmp")


def test_locked_update_writes_nothing_when_body_raises(tmp_path):
    root = str(tmp_path)
    store.create_user(root, "a@b.com", "hash", "admin")
    with pytest.raises(RuntimeError):
        with store.locked_update(root) as doc:
            doc["users"].append({"id": "u_2", "email": "b@c.com"})
            raise RuntimeError("boom")
    assert len(store.read_auth(root)["users"]) == 1


def test_create_user_normalizes_email_and_assigns_id(tmp_path):
    root = str(tmp_path)
    user = store.create_user(root, "  Mixed.Case@Client.COM ", "hash", "reviewer")
    assert user.email == "mixed.case@client.com"
    assert user.id.startswith("u_")
    assert store.find_by_email(root, "MIXED.CASE@client.com").id == user.id


def test_create_user_rejects_duplicate_email(tmp_path):
    """S1: exactly one record per account — a second signup cannot shadow the first."""
    root = str(tmp_path)
    store.create_user(root, "a@b.com", "hash", "reviewer")
    with pytest.raises(store.EmailTaken):
        store.create_user(root, "A@B.com", "other-hash", "reviewer")
    assert len(store.read_auth(root)["users"]) == 1


def test_delete_user_removes_exactly_that_user(tmp_path):
    """S1: 'exactly', not 'includes' — the other account must survive untouched."""
    root = str(tmp_path)
    keep = store.create_user(root, "keep@b.com", "h", "reviewer")
    drop = store.create_user(root, "drop@b.com", "h", "reviewer")
    store.delete_user(root, drop.id)
    remaining = store.read_auth(root)["users"]
    assert [u["id"] for u in remaining] == [keep.id]
