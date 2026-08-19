"""Supervisor entry-point for the Procurement Review API.

This file is a thin shim so the platform's supervisor (which expects
`/app/backend/server.py` with a `server:app` ASGI target on port 8001) can
launch the existing FastAPI application defined in `/app/api/main.py`.

No backend behaviour, routes, or business logic are defined here — everything
still lives under `/app/api/`. This module only re-exports the ASGI app.
"""
from __future__ import annotations

import os
import sys

# Ensure the repository root is on sys.path so `api.main` resolves.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Re-export the FastAPI application unchanged.
from api.main import app  # noqa: E402,F401
