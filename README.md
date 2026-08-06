# procurement-and-cost-estimation

Two Python packages over one shared LLM layer (`procurement/`, `cost_estimation/`), plus:

- **Web app** (`web/` + `api/`) — the single front end: create projects, upload
  requirements/vendors, run ingestion, then review the compliance matrix and
  comparative statement

## Setup

Starting from a machine with nothing installed? See [`docs/SETUP.md`](docs/SETUP.md).

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cd web && npm install && cd ..
```

Copy `.env.example` to `.env` and set provider keys as needed for ingestion.

## Running it

```bash
# terminal 1 — API (reads PROCUREMENT_PROJECTS_ROOT, default projects/)
.venv/bin/uvicorn api.main:app --reload --port 8000

# terminal 2 — React app (proxies /api → :8000)
cd web && npm run dev
```

Open http://localhost:5173 and sign in. Set `ADMIN_EMAIL` and `ADMIN_PASSWORD`
in `.env` before the first run — they create the first administrator, and only
while no user exists yet. Without them nobody can reach anything: every `/api/`
path requires a session, and a self-registered account is a reviewer with no
project access until an admin grants some. See [Accounts](#accounts).

Once signed in — create a project in the sidebar, attach the
requirements document, upload the vendor ZIP, set FX rates and run ingestion;
then review the compliance matrix (worklist / full grid, coverage, filters) and
the comparative statement.

Reviewing is otherwise read-only. The one write outside setup and ingestion is
the per-vendor technical note, `PUT /api/projects/{slug}/vendors/{vendor}/feedback`,
which requires a reason and is recorded as a `facts.feedback_edited` event.

## Accounts

Two roles. An **admin** sees every project and manages people; a **reviewer**
sees only the projects an admin has granted them, and nothing at all until then.
The boundary is enforced on the server for every route — the nav simply hides
what a reviewer cannot use.

| variable | effect |
|---|---|
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | Create the first administrator on startup, **only while no user exists**. Set both before the first run. |
| `ALLOW_SIGNUP` | `0` closes public self-registration; an admin can still add people. Default `1`. |

Set both admin variables before the first run. If they are unset the app logs a
warning and starts anyway, into a state where nobody can reach anything: signup
still works, but every new account is an ungranted reviewer, and only an admin
can grant. Recovering means editing `auth.json` by hand.

Once one user exists the seeding is skipped, so changing these later has no
effect — add and remove people from **Users and access** in the sidebar, which
is also where you grant a reviewer a project. Accounts, sessions and grants live
in `auth.json` beside the projects, so they persist with the same volume.

## Docker

One image, one service. The React bundle is compiled in a node stage and
served by FastAPI itself, so the API and the UI share a single origin — no
nginx, no CORS, one port.

```bash
docker compose up --build
```

- http://localhost:8000 — the web app + API (`/api/...`)

Put `ADMIN_EMAIL` and `ADMIN_PASSWORD` in `.env` before the first `up` —
compose passes `.env` through, and without them the container starts with no
administrator and nothing reachable. See [Accounts](#accounts).

The container mounts the `projects` volume at `/data/projects`. That volume is
the authoritative snapshot store (see [`CLAUDE.md`](CLAUDE.md)); it survives
`docker compose down` and is destroyed only by `down -v`. `auth.json` — the
accounts, sessions and grants — lives in that same volume, so people and their
project access survive a rebuild too, and `down -v` destroys them with it.

If the host reserves port 8000 — Windows hands wide ranges to WinNAT, and
`netsh interface ipv4 show excludedportrange protocol=tcp` will show 8000 and
8080 inside one — the bind fails with no process holding the port. Pick another
host port; the container port never changes:

```bash
PROCUREMENT_HOST_PORT=8300 docker compose up --build
```

### The bundled sample project

The image ships `gas-14`, an ingested three-vendor project, so a fresh install
has something to review before anyone uploads a tender. It lives read-only at
`/app/samples` and is copied into the store volume by the entrypoint
([`docker/entrypoint.sh`](docker/entrypoint.sh)) on start.

- An existing project of the same slug is **never** overwritten — the seed logs
  that it left the store alone. Once copied, the sample is an ordinary project:
  editable, and yours to delete.
- `SEED_SAMPLE_PROJECTS=0` brings the container up with an empty store.
- `projects/` is gitignored, so a clean clone has no `projects/gas-14` to copy.
  Build such a checkout with `docker build --build-arg SAMPLE=none .`; BuildKit
  then never evaluates the stage that reads the directory. Note that the sample
  carries real vendor quotations, so an image built with it should be treated as
  confidential and not pushed to a public registry.

Provider keys come from `.env` at run time and are never baked into an image —
`.env` is listed in `.dockerignore`. With no key present the API reports
`provider.ready = false` and ingestion is refused rather than silently faked.

The image carries runtime dependencies only — `pytest` and `httpx` come from
the `dev` extra, which it does not install — so the suite runs from the venv on
the host, not inside the container.

The image serves the SPA from `WEB_DIST` (`/app/web/dist`). Outside a
container the mount activates only once `cd web && npm run build` has produced
`web/dist`; without it, `uvicorn api.main:app` serves the API alone and the
Vite dev server on :5173 remains the front end.

## Tests

```bash
.venv/bin/python -m pytest
```

API coverage: `tests/test_api_compliance.py` (key-free).
