"""FastAPI entry for the enterprise review UI.

Read routes wrap an existing procurement builder or project loader. The setup
routes (create project, attach requirements, upload vendor archives, set FX
rates, run ingestion) wrap the same functions the Streamlit portal calls, so
the two front ends stay behaviourally identical.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile

from fastapi import (
    Body,
    FastAPI,
    File,
    HTTPException,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware

from procurement import project as proj
from procurement.matrix import GROUP_ORDER, build_matrix, rows_in_group
from procurement.pipeline import load_dataset, run_ingestion
from procurement.quote_select import pick_quote
from procurement.statement import build_statement

ROOT = os.environ.get("PROCUREMENT_PROJECTS_ROOT", "projects")

REQUIREMENTS_EXTS = {".pdf", ".docx", ".xlsx"}

app = FastAPI(title="Procurement Review API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _project_summary(p) -> dict:
    return {
        "slug": p.slug,
        "name": p.name,
        "vendors": list(p.vendors),
        "target_currency": p.target_currency,
        "status": p.status,
        "generation": p.generation,
    }


def _load_or_404(slug: str):
    try:
        proj._validate_slug(ROOT, slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return proj.load_project(ROOT, slug)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"project not found: {slug}") from exc


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/projects")
def list_projects() -> list[dict]:
    return [_project_summary(p) for p in proj.list_projects(ROOT)]


@app.get("/api/projects/{slug}")
def get_project(slug: str) -> dict:
    return _project_summary(_load_or_404(slug))


@app.get("/api/projects/{slug}/compliance-matrix")
def get_compliance_matrix(slug: str) -> dict:
    _load_or_404(slug)
    matrix = build_matrix(ROOT, slug)
    payload = matrix.model_dump()
    payload["groups"] = {
        group: [row.model_dump() for row in rows_in_group(matrix, group)]
        for group in GROUP_ORDER
    }
    return payload


@app.get("/api/projects/{slug}/summary")
def get_summary(slug: str) -> dict:
    """Lightweight dashboard readout: counts and coverage, no matrix rows."""
    project = _load_or_404(slug)
    matrix = build_matrix(ROOT, slug)
    group_counts = {group: len(rows_in_group(matrix, group)) for group in GROUP_ORDER}
    return {
        "slug": project.slug,
        "name": project.name,
        "vendors": list(project.vendors),
        "target_currency": project.target_currency,
        "generation": project.generation,
        "requirement_count": len(matrix.rows),
        "coverage": matrix.coverage.model_dump(),
        "group_counts": group_counts,
    }


@app.get("/api/projects/{slug}/statement")
def get_statement(slug: str) -> dict:
    _load_or_404(slug)
    return build_statement(ROOT, slug).model_dump()


# --------------------------------------------------------------- setup routes


def _provider_state() -> dict:
    """What the server will use to run extraction, and whether it can."""
    provider = os.getenv("LLM_PROVIDER", "mock").lower()
    needed = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }.get(provider)
    return {
        "provider": provider,
        "needs_key": needed,
        "ready": provider in ("mock", "bedrock") or bool(needed and os.getenv(needed)),
    }


def _setup_state(project) -> dict:
    slug = project.slug
    vendors = [
        {
            "name": v,
            "file_count": len(proj.vendor_files(ROOT, slug, v)),
            "quote": os.path.basename(pick_quote(proj.vendor_files(ROOT, slug, v)) or "")
            or None,
        }
        for v in project.vendors
    ]
    return {
        "slug": slug,
        "name": project.name,
        "target_currency": project.target_currency,
        "requirements_file": project.requirements_file,
        "vendors": vendors,
        "fx_rates": project.fx_rates,
        "status": project.status,
        "generation": project.generation,
        "has_results": bool(load_dataset(ROOT, slug)),
        "provider": _provider_state(),
    }


@app.get("/api/projects/{slug}/setup")
def get_setup(slug: str) -> dict:
    return _setup_state(_load_or_404(slug))


@app.post("/api/projects", status_code=201)
def create_project(payload: dict = Body(...)) -> dict:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=422, detail="A project name is required.")
    currency = str(payload.get("target_currency", "USD")).strip().upper() or "USD"
    slug = proj.slugify(name)
    if not slug:
        raise HTTPException(status_code=422, detail="Name must contain letters or digits.")
    if os.path.exists(os.path.join(ROOT, slug, "project.json")):
        raise HTTPException(status_code=409, detail=f"A project named '{name}' already exists.")
    project = proj.create_project(ROOT, name, target_currency=currency)
    return _setup_state(project)


@app.post("/api/projects/{slug}/requirements")
def upload_requirements(slug: str, file: UploadFile = File(...)) -> dict:
    project = _load_or_404(slug)
    filename = os.path.basename(file.filename or "")
    if not filename:
        raise HTTPException(status_code=422, detail="No file name was provided.")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in REQUIREMENTS_EXTS:
        raise HTTPException(
            status_code=422,
            detail=f"Requirements must be one of {', '.join(sorted(REQUIREMENTS_EXTS))}.",
        )
    dest_dir = os.path.join(ROOT, slug, "requirements")
    os.makedirs(dest_dir, exist_ok=True)
    with open(os.path.join(dest_dir, filename), "wb") as fh:
        shutil.copyfileobj(file.file, fh)
    project.requirements_file = filename
    proj.save_project(ROOT, project)
    return _setup_state(project)


@app.post("/api/projects/{slug}/vendors")
def upload_vendors(slug: str, file: UploadFile = File(...)) -> dict:
    _load_or_404(slug)
    fd, tmp = tempfile.mkstemp(suffix=".zip", dir=os.path.join(ROOT, slug))
    try:
        with os.fdopen(fd, "wb") as fh:
            shutil.copyfileobj(file.file, fh)
        try:
            proj.unpack_vendor_zip(ROOT, slug, tmp)
        except (ValueError, zipfile.BadZipFile) as exc:
            raise HTTPException(status_code=422, detail=f"Rejected archive: {exc}") from exc
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return _setup_state(proj.load_project(ROOT, slug))


@app.put("/api/projects/{slug}/fx-rates")
def set_fx_rates(slug: str, payload: dict = Body(...)) -> dict:
    project = _load_or_404(slug)
    raw = payload.get("rates", {})
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="`rates` must be an object of code → rate.")
    rates: dict[str, float] = {}
    for code, value in raw.items():
        try:
            rates[str(code).strip().upper()] = float(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail=f"Rate for '{code}' is not a number."
            ) from exc
    project.fx_rates = rates
    proj.save_project(ROOT, project)
    return _setup_state(project)


@app.post("/api/projects/{slug}/ingest")
def ingest(slug: str) -> dict:
    project = _load_or_404(slug)
    if not project.vendors:
        raise HTTPException(
            status_code=422,
            detail="Add at least one vendor before running ingestion.",
        )
    from shared.llm.factory import get_client

    # The scanned-PDF transcription fallback uses Anthropic; enable it only when
    # an Anthropic key is present, mirroring the Streamlit portal.
    pdf_fallback = None
    if os.getenv("ANTHROPIC_API_KEY"):
        from procurement.pdf_llm import transcribe_pdf

        pdf_fallback = transcribe_pdf
    try:
        run_ingestion(ROOT, slug, get_client(), pdf_fallback=pdf_fallback)
    except Exception as exc:  # surface extraction failures to the UI verbatim
        raise HTTPException(status_code=502, detail=f"Ingestion failed: {exc}") from exc
    return _setup_state(proj.load_project(ROOT, slug))
