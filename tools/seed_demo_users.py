"""Seed the demo accounts and write the shareable credentials file.

Creates one administrator and two reviewers in `<ROOT>/auth.json`, then writes
`demo-accounts.json` at the repo root so the logins can be handed to anyone who
wants to walk the platform.

Run it from the repo root:

    .venv\\Scripts\\python.exe -m tools.seed_demo_users

Idempotent: an account whose email already exists is left exactly as it is,
including its password. Re-running after adding a reviewer below creates only
the new one, and re-running changes nobody's password out from under them. Use
`--reset-passwords` to force the listed passwords back onto existing accounts.

Two deliberate choices worth knowing:

* **Accounts are created through `api.auth.store`, never by writing the file.**
  `<ROOT>/auth.json` is written only from that module (CLAUDE.md's auth
  invariants), so a seed script that hand-rolled the JSON would be the one
  writer that skips the lock and the schema.
* **Roles are set at creation, because nothing can change them later.** No
  route in this API changes a role, and signup always produces a reviewer — so
  the administrator cannot be made by signing up and promoting, and is made
  here with `role="admin"` directly, the same way `bootstrap.seed_admin_if_empty`
  does it.

These are demo credentials for a demo deployment. The password below is chosen
to be shared, not to be secure — do not reuse it anywhere that matters, and do
not point this script at a real deployment's ROOT.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

from api.auth import passwords, store

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CREDENTIALS_FILE = REPO_ROOT / "demo-accounts.json"

# The reviewers deliberately start with **no** project grants. Access is the
# administrator's to give, from "Users and access" in the app — showing that
# hand-off is part of what the demo is for, so pre-granting here would hide it.
DEMO_ACCOUNTS: list[dict[str, str]] = [
    {
        "email": "admin@gmail.com",
        "password": "Admin@1234",
        "role": "admin",
        "note": "Full access to every project, plus Users and access.",
    },
    {
        "email": "reviewer.one@gmail.com",
        "password": "Reviewer@1234",
        "role": "reviewer",
        "note": "Sees only the projects the administrator grants.",
    },
    {
        "email": "reviewer.two@gmail.com",
        "password": "Reviewer@1234",
        "role": "reviewer",
        "note": "Sees only the projects the administrator grants.",
    },
]


def seed(root: str, *, reset_passwords: bool = False) -> list[dict[str, str]]:
    """Create any missing demo account. Returns one row per account with what
    actually happened to it."""
    results: list[dict[str, str]] = []
    for spec in DEMO_ACCOUNTS:
        email = store.normalize_email(spec["email"])
        existing = store.find_by_email(root, email)

        if existing is None:
            store.create_user(
                root, email, passwords.hash_password(spec["password"]), spec["role"]
            )
            outcome = "created"
        elif reset_passwords:
            # Mutating the document inside `store.locked_update` is the shape
            # `routes.change_password` already uses; the lock is what the
            # invariant is about, not who assigns the field.
            new_hash = passwords.hash_password(spec["password"])
            with store.locked_update(root) as doc:
                for record in doc["users"]:
                    if record["id"] == existing.id:
                        record["password_hash"] = new_hash
                        break
            # Any session signed in under the old password is no longer the
            # same credential, so it should not outlive the change.
            store.delete_sessions_for(root, existing.id)
            outcome = "password reset"
        else:
            outcome = "already existed, left alone"

        results.append({**spec, "email": email, "outcome": outcome})
    return results


def write_credentials_file(rows: list[dict[str, str]], path: pathlib.Path) -> None:
    document = {
        "_comment": (
            "Demo credentials for the Tender Eval platform. Sign in at "
            "http://localhost:5173 after starting the app with .\\run.ps1. "
            "Reviewers see no projects until the administrator grants them "
            "access from 'Users and access'. Regenerate with: "
            "python -m tools.seed_demo_users"
        ),
        "url": "http://localhost:5173",
        "accounts": [
            {
                "email": r["email"],
                "password": r["password"],
                "role": r["role"],
                "note": r["note"],
            }
            for r in rows
        ],
    }
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        default=os.environ.get("PROCUREMENT_PROJECTS_ROOT", "projects"),
        help="Where auth.json lives. Defaults to PROCUREMENT_PROJECTS_ROOT, else 'projects'.",
    )
    parser.add_argument(
        "--reset-passwords",
        action="store_true",
        help="Force the listed passwords onto accounts that already exist.",
    )
    args = parser.parse_args(argv)

    os.makedirs(args.root, exist_ok=True)
    rows = seed(args.root, reset_passwords=args.reset_passwords)
    write_credentials_file(rows, CREDENTIALS_FILE)

    width = max(len(r["email"]) for r in rows)
    print(f"auth store: {os.path.abspath(args.root)}")
    for r in rows:
        print(f"  {r['email']:<{width}}  {r['role']:<8}  {r['outcome']}")
    print(f"\ncredentials written to {CREDENTIALS_FILE}")
    print("Reviewers start with no project access - grant it from 'Users and access'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
