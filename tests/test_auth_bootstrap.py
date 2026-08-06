from api.auth import bootstrap, store


def test_seeds_one_admin_when_env_is_set_and_store_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "Boss@Client.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "bootstrappw")
    user = bootstrap.seed_admin_if_empty(str(tmp_path))
    assert user.role == "admin"
    assert user.email == "boss@client.com"


def test_seeding_twice_does_not_create_a_second_admin(tmp_path, monkeypatch):
    """S1 across restarts: startup runs on every boot, not just the first."""
    monkeypatch.setenv("ADMIN_EMAIL", "boss@client.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "bootstrappw")
    bootstrap.seed_admin_if_empty(str(tmp_path))
    assert bootstrap.seed_admin_if_empty(str(tmp_path)) is None
    assert len(store.read_auth(str(tmp_path))["users"]) == 1


def test_seeds_nothing_without_env_and_logs_a_warning(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    assert bootstrap.seed_admin_if_empty(str(tmp_path)) is None
    assert store.read_auth(str(tmp_path))["users"] == []
    assert "ADMIN_EMAIL" in caplog.text and "ADMIN_PASSWORD" in caplog.text


def test_does_not_seed_when_users_already_exist(tmp_path, monkeypatch):
    """A populated store must not gain a surprise admin from a stale env var."""
    store.create_user(str(tmp_path), "someone@b.com", "h", "reviewer")
    monkeypatch.setenv("ADMIN_EMAIL", "boss@client.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "bootstrappw")
    assert bootstrap.seed_admin_if_empty(str(tmp_path)) is None
    assert [u["role"] for u in store.read_auth(str(tmp_path))["users"]] == ["reviewer"]
