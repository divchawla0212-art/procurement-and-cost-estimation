"""Auth data shapes.

`User` deliberately carries no `password_hash` field. Handlers build responses
by returning `User` instances (or their `model_dump()`), so the digest can
never leak through a route that forgets to scrub it — reaching it at all
requires calling `store.password_hash_for` explicitly.
"""
from pydantic import BaseModel


class User(BaseModel):
    id: str
    email: str
    role: str            # "admin" | "reviewer"
    created_at: str
