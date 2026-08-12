"""Key-free tests for server-side sessions in the auth.json store."""

from api.auth import store


def _user(root, email="a@b.com", role="admin"):
    return store.create_user(root, email, "hash", role)


def test_session_round_trips_and_returns_the_user(tmp_path):
    root = str(tmp_path)
    user = _user(root)
    token = store.create_session(root, user.id)
    assert store.resolve_session(root, token).id == user.id


def test_plaintext_token_is_never_stored(tmp_path):
    """S2/S3: the file must hold only the digest, so a leaked auth.json grants nothing."""
    root = str(tmp_path)
    token = store.create_session(root, _user(root).id)
    raw = open(store.auth_path(root), encoding="utf-8").read()
    assert token not in raw


def test_unknown_and_malformed_tokens_resolve_to_none(tmp_path):
    root = str(tmp_path)
    _user(root)
    for bad in ["", "not-a-token", "u_deadbeef"]:
        assert store.resolve_session(root, bad) is None


def test_expired_session_does_not_resolve(tmp_path):
    root = str(tmp_path)
    token = store.create_session(root, _user(root).id, ttl_seconds=-1)
    assert store.resolve_session(root, token) is None


def test_expired_sessions_are_pruned_on_the_next_write(tmp_path):
    """S3: 'exactly' — an expired row must leave, not merely stop resolving."""
    root = str(tmp_path)
    user = _user(root)
    store.create_session(root, user.id, ttl_seconds=-1)
    store.create_session(root, user.id)
    assert len(store.read_auth(root)["sessions"]) == 1


def test_deleting_a_user_removes_their_sessions(tmp_path):
    """S3: a session must not outlive the account it authenticates.

    A reviewer, not `_user`'s default admin: `delete_user` refuses to remove
    the last admin, and this test is about the session cascade, not that rule.
    """
    root = str(tmp_path)
    user = _user(root, role="reviewer")
    token = store.create_session(root, user.id)
    store.delete_user(root, user.id)
    assert store.resolve_session(root, token) is None
    assert store.read_auth(root)["sessions"] == []


def test_logout_invalidates_only_that_session(tmp_path):
    root = str(tmp_path)
    user = _user(root)
    a, b = store.create_session(root, user.id), store.create_session(root, user.id)
    store.delete_session(root, a)
    assert store.resolve_session(root, a) is None
    assert store.resolve_session(root, b).id == user.id


def test_delete_sessions_for_can_keep_the_current_one(tmp_path):
    """Password change revokes the other sessions and keeps the caller signed in."""
    root = str(tmp_path)
    user = _user(root)
    keep, other = store.create_session(root, user.id), store.create_session(root, user.id)
    store.delete_sessions_for(root, user.id, keep_token=keep)
    assert store.resolve_session(root, keep).id == user.id
    assert store.resolve_session(root, other) is None


def test_delete_session_prunes_an_unrelated_users_expired_session(tmp_path):
    """S3: 'exactly' applies to every writer, not just create_session.

    The expired row must be created *after* the surviving session so that
    create_session's own on-write pruning (which runs before this row exists)
    cannot be the thing that removes it — the assertion has to be about
    delete_session's own write, not a side effect of an earlier call.
    """
    root = str(tmp_path)
    user_a = _user(root, email="a@b.com")
    user_b = _user(root, email="c@d.com")
    b_token = store.create_session(root, user_b.id)
    store.create_session(root, user_a.id, ttl_seconds=-1)
    store.delete_session(root, b_token)
    assert store.read_auth(root)["sessions"] == []


def test_delete_sessions_for_prunes_an_unrelated_users_expired_session(tmp_path):
    """S3: 'exactly' applies to every writer, not just create_session.

    Same ordering rationale as the delete_session case above: user_a's
    expired row is created last so only delete_sessions_for's own write can
    be responsible for pruning it.
    """
    root = str(tmp_path)
    user_a = _user(root, email="a@b.com")
    user_b = _user(root, email="c@d.com")
    store.create_session(root, user_b.id)
    store.create_session(root, user_a.id, ttl_seconds=-1)
    store.delete_sessions_for(root, user_b.id)
    assert store.read_auth(root)["sessions"] == []
