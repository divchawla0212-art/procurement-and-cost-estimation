# tests/test_auth_passwords.py
from api.auth import passwords


def test_hash_is_self_describing_and_verifies():
    stored = passwords.hash_password("correct horse battery")
    assert stored.startswith("scrypt$16384$8$1$")
    assert passwords.verify_password("correct horse battery", stored) is True


def test_wrong_password_fails():
    stored = passwords.hash_password("right")
    assert passwords.verify_password("wrong", stored) is False


def test_same_password_hashes_differently():
    """Distinct salts — two users with the same password must not share a digest."""
    assert passwords.hash_password("same") != passwords.hash_password("same")


def test_plaintext_never_appears_in_the_stored_form():
    """S2: the digest must not carry the password it was made from."""
    assert "hunter2" not in passwords.hash_password("hunter2")


def test_verify_rejects_malformed_stored_values_without_raising():
    for bad in ["", "not-a-hash", "scrypt$1$2$3", "bcrypt$a$b$c$d$e", "scrypt$x$8$1$aa$bb"]:
        assert passwords.verify_password("anything", bad) is False


def test_verify_reads_cost_parameters_from_the_stored_string():
    """A digest made at lower cost still verifies — this is what allows raising n later."""
    cheap = passwords.hash_password("pw", n=1024)
    assert cheap.startswith("scrypt$1024$")
    assert passwords.verify_password("pw", cheap) is True


def test_dummy_hash_is_a_valid_digest_that_matches_nothing_useful():
    assert passwords.verify_password("", passwords.DUMMY_HASH) is False
