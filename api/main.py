"""FastAPI entry for the enterprise review UI.

Read-only: every route wraps an existing procurement builder or project
loader. Upload and ingestion stay in the Streamlit portal.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from procurement import project as proj
from procurement.matrix import GROUP_ORDER, build_matrix, rows_in_group

ROOT = os.environ.get("PROCUREMENT_PROJECTS_ROOT", "projects")

app = FastAPI(title="Procurement Review API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["GET"],
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
