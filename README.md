# procurement-and-cost-estimation

Two Python packages over one shared LLM layer (`procurement/`, `cost_estimation/`), plus:

- **Streamlit portal** (`portal/`) — create projects, upload requirements/vendors, run ingestion
- **Enterprise review UI** (`web/` + `api/`) — read-only compliance comparison matrix

## Setup

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

## Tests

```bash
.venv/bin/python -m pytest
```

API coverage: `tests/test_api_compliance.py` (key-free).
