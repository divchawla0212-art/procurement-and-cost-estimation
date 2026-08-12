import logging
import os

from api.auth import passwords, store
from api.auth.models import User

log = logging.getLogger(__name__)


def seed_admin_if_empty(root: str) -> User | None:
    if store.read_auth(root)["users"]:
        return None
    email = os.getenv("ADMIN_EMAIL")
    password = os.getenv("ADMIN_PASSWORD")
    if not email or not password:
        log.warning(
            "No users exist and ADMIN_EMAIL / ADMIN_PASSWORD are unset. "
            "No administrator will be created, so no project is reachable by "
            "anyone. Set both and restart."
        )
        return None
    return store.create_user(root, email, passwords.hash_password(password), "admin")
