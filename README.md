# procurement-and-cost-estimation

Two Python packages over one shared LLM layer (`procurement/`, `cost_estimation/`), plus:

- **Streamlit portal** (`portal/`) — create projects, upload requirements/vendors, run ingestion
- **Enterprise review UI** (`web/` + `api/`) — read-only compliance comparison matrix

## Setup

Starting from a machine with nothing installed? See [`docs/SETUP.md`](docs/SETUP.md).

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cd web && npm install && cd ..
```

Copy `.env.example` to `.env` and set provider keys as needed for ingestion.

## Streamlit (upload & ingestion)

```bash
.venv/bin/python -m streamlit run portal/app.py
```

Create a project, upload requirements + vendor ZIP, set FX rates, click **Run ingestion**.

## Enterprise matrix UI (review)

After ingestion has written store data under `projects/`:

```bash
# terminal 1 — API (reads PROCUREMENT_PROJECTS_ROOT, default projects/)
.venv/bin/uvicorn api.main:app --reload --port 8000

# terminal 2 — React app (proxies /api → :8000)
cd web && npm run dev
```

Open http://localhost:5173 — pick a project in the sidebar to view the compliance matrix (worklist / full grid, coverage, filters). This UI does not write to the store.

## Docker

One image, two services. The React bundle is compiled in a node stage and
served by FastAPI itself, so the API and the UI share a single origin — no
nginx, no CORS, one port.

```bash
docker compose up --build
```

- http://localhost:8000 — enterprise review UI + API (`/api/...`)
- http://localhost:8501 — Streamlit portal

Both containers mount the same `projects` volume at `/data/projects`. That
volume is the authoritative snapshot store (see [`CLAUDE.md`](CLAUDE.md)); it
survives `docker compose down` and is destroyed only by `down -v`.

Provider keys come from `.env` at run time and are never baked into an image —
`.env` is listed in `.dockerignore`. With no key present the API reports
`provider.ready = false` and ingestion is refused rather than silently faked.

```bash
docker compose exec api python -m pytest
```

The image serves the SPA from `WEB_DIST` (`/app/web/dist`). Outside a
container the mount activates only once `cd web && npm run build` has produced
`web/dist`; without it, `uvicorn api.main:app` serves the API alone and the
Vite dev server on :5173 remains the front end.

## Tests

```bash
.venv/bin/python -m pytest
```

API coverage: `tests/test_api_compliance.py` (key-free).
