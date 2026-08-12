import base64
import hashlib
import hmac
import secrets

_N, _R, _P = 16384, 8, 1     # 128 * n * r = 16 MiB, under OpenSSL's 32 MiB maxmem
_SALT_BYTES = 16
_DKLEN = 32


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str, *, n: int = _N, r: int = _R, p: int = _P) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_DKLEN)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    """False on any failure, including a malformed stored value. Never raises."""
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
    except (ValueError, TypeError, MemoryError, OverflowError):
        return False
    return hmac.compare_digest(dk, expected)


# Login verifies against this when the email is unknown, so an unregistered
# address costs the same wall-clock as a registered one. Without it, timing
# enumerates the user list.
DUMMY_HASH = hash_password(secrets.token_urlsafe(32))
