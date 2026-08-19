# Test credentials

## Admin (seeded via ADMIN_EMAIL / ADMIN_PASSWORD)
- Email: admin@gmail.com
- Password: Admin@1234
- Role: admin

Seeded automatically at backend startup from `/app/.env` (see `api/auth/bootstrap.py`).

## Demo tiles on /login
The `/login` screen exposes a "Business Admin" demo tile that fills in the credentials above. The other three demo tiles (Project Lead, Vendor Lead, Technology Admin) are placeholder emails only — the tile fills the email but no password, so the admin account is the only working seed.
