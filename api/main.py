"""FastAPI entry for the web app — the only front end.

Read routes wrap an existing procurement builder or project loader. The setup
routes (create project, attach requirements, upload vendor archives, set FX
rates, run ingestion) wrap the same `procurement` functions directly, so the
HTTP layer stays a thin adapter and never becomes a second implementation.
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
    Query,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

import dotenv

from procurement import project as proj
from procurement.coverage import build_extraction_status, rollup
from procurement.feedback import save_feedback
from procurement.export import (
    matrix_to_csv_str,
    matrix_to_xlsx_bytes,
    statement_to_csv_str,
    statement_to_xlsx_bytes,
)
from procurement.matrix import GROUP_ORDER, build_matrix, rows_in_group
from procurement.pipeline import has_results, run_ingestion
from procurement.quote_select import pick_quote
from procurement.statement import build_statement
from procurement.store import snapshots

# Load a local `.env` here rather than relying on the launcher. `load_dotenv()`
# resolves the file by walking up from this module, not from the working
# directory, so it works under `uvicorn`, Docker and pytest alike — and it never
# overrides a real environment variable, which is what a deployment injects.
dotenv.load_dotenv()

ROOT = os.environ.get("PROCUREMENT_PROJECTS_ROOT", "projects")

REQUIREMENTS_EXTS = {".pdf", ".docx", ".xlsx"}

# Provider → the env var holding its API key. None means no key is needed:
# bedrock authenticates via AWS IAM, and mock calls nothing. Insertion order is
# the order the UI lists them in, so it must stay stable.
PROVIDER_KEYS: dict[str, str | None] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "bedrock": None,
    "mock": None,
}


def _provider_ready(provider: str) -> bool:
    if provider not in PROVIDER_KEYS:
        return False
    needed = PROVIDER_KEYS[provider]
    return needed is None or bool(os.getenv(needed))


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
        # Whether the store holds an extraction to read, independent of
        # `status` (BUG-001 follow-up / I2). `status` can be "failed" while
        # the store still holds a complete prior extraction — CLAUDE.md: "a
        # failed extraction never blanks previously-good stored data" — so
        # `status` alone is the wrong predicate for whether screens 02/03 are
        # reachable. Same computation `_setup_state` already uses; kept to
        # one implementation. This route renders every project, so it takes
        # the predicate that reads the store's shape rather than
        # `bool(load_dataset(...))`, which loaded every vendor's facts and
        # built a price comparison per project purely to discard it.
        "has_results": has_results(ROOT, p.slug),
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
        "extraction": rollup(build_extraction_status(ROOT, slug)),
    }


@app.get("/api/projects/{slug}/extraction-status")
def get_extraction_status(slug: str) -> dict:
    _load_or_404(slug)
    return build_extraction_status(ROOT, slug).model_dump()


@app.get("/api/projects/{slug}/statement")
def get_statement(slug: str) -> dict:
    _load_or_404(slug)
    return build_statement(ROOT, slug).model_dump()


# -------------------------------------------------------------- export routes
#
# Both are reads. They build from the store at request time and cache nothing,
# so the file a reviewer opens is the store as it stood when they clicked —
# never a stale artefact left over from an earlier ingestion.
#
# The compliance export takes no filter parameters, deliberately. The screen's
# search, verdict chips and vendor selector are a reading aid; a downloaded
# deliverable that silently carried them would hand someone a file of twelve
# rows with no way to tell whether the other two hundred passed or were merely
# filtered away, and that file backs an award decision.

CSV_MIME = "text/csv; charset=utf-8"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _download(payload: bytes, media_type: str, filename: str) -> Response:
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _export(fmt: str, slug: str, stem: str, build_xlsx, build_csv) -> Response:
    if fmt == "xlsx":
        return _download(build_xlsx(), XLSX_MIME, f"{slug}-{stem}.xlsx")
    # utf-8-sig: Excel reads a BOM-less UTF-8 CSV as cp1252, and both documents
    # carry non-ASCII — the statement's U+00B7 tally separator, the matrix's
    # rationale text.
    return _download(build_csv().encode("utf-8-sig"), CSV_MIME,
                     f"{slug}-{stem}.csv")


# `Query(pattern=...)` rather than a hand-rolled check: an unknown format must
# be a 422 and not quietly fall through to whichever branch is written last.
_FORMAT = Query("xlsx", pattern="^(xlsx|csv)$")


@app.get("/api/projects/{slug}/compliance-matrix/export")
def export_compliance_matrix(slug: str, format: str = _FORMAT) -> Response:
    _load_or_404(slug)
    matrix = build_matrix(ROOT, slug)
    return _export(format, slug, "compliance-matrix",
                   lambda: matrix_to_xlsx_bytes(matrix),
                   lambda: matrix_to_csv_str(matrix))


@app.get("/api/projects/{slug}/statement/export")
def export_statement(slug: str, format: str = _FORMAT) -> Response:
    _load_or_404(slug)
    statement = build_statement(ROOT, slug)
    return _export(format, slug, "comparative-statement",
                   lambda: statement_to_xlsx_bytes(statement),
                   lambda: statement_to_csv_str(statement))


# -------------------------------------------------------------- review writes
#
# The only write a reviewer makes outside setup and ingestion. It was the
# Streamlit portal's sole write until the portal was removed; the invariants it
# has to honour (one generation bump per note, a reason on every note) are
# unchanged, and now live in `procurement/feedback.py` beside the other store
# writers rather than in a view.


@app.put("/api/projects/{slug}/vendors/{vendor}/feedback")
def put_feedback(slug: str, vendor: str, payload: dict = Body(...)) -> dict:
    _load_or_404(slug)
    try:
        save_feedback(ROOT, slug, vendor,
                      str(payload.get("text", "")),
                      str(payload.get("reason", "")))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "vendor": vendor,
        "technical_feedback": snapshots.load_facts(ROOT, slug, vendor).technical_feedback,
        "generation": snapshots.get_generation(ROOT, slug),
    }


# --------------------------------------------------------------- setup routes


def _provider_state() -> dict:
    """The server's default provider, plus what every provider would need.

    The first three keys describe the default, not any selection — the front end
    and tests both depend on that meaning being unchanged.
    """
    raw = os.getenv("LLM_PROVIDER")
    provider = raw.strip().lower() if raw and raw.strip() else None
    return {
        "provider": provider,
        "needs_key": PROVIDER_KEYS.get(provider) if provider else None,
        "ready": _provider_ready(provider) if provider else False,
        "catalog": [
            {"id": name, "needs_key": key, "ready": _provider_ready(name)}
            for name, key in PROVIDER_KEYS.items()
        ],
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
        "has_results": has_results(ROOT, slug),
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
def ingest(slug: str, payload: dict | None = Body(default=None)) -> dict:
    project = _load_or_404(slug)
    if not project.vendors:
        raise HTTPException(
            status_code=422,
            detail="Add at least one vendor before running ingestion.",
        )

    # Validate before any work starts. A provider that was never runnable is a
    # configuration error (400), not an extraction failure (502) — and a 400
    # must leave the store untouched.
    #
    # What is validated is the *effective* provider, not just a named one. An
    # omitted `provider` resolves through LLM_PROVIDER exactly as `get_client`
    # would, and that resolution is checked the same way: an unvalidated server
    # default wrote a whole store of failed extractions while reporting
    # `has_results: true`, and an unknown one surfaced as a 502 from inside the
    # try below.
    requested = (payload or {}).get("provider")
    # A *present* `provider` must be a string. Without this check `False` and
    # `0` are falsy and silently fell through to the env default rather than
    # being rejected, while `123` was stringified to `"123"` and rejected as
    # an *unknown provider* (400) rather than as a malformed field (422) —
    # the same class of accident `force`'s boolean check two lines below
    # guards against, so `provider` is held to the same standard.
    if requested is not None and not isinstance(requested, str):
        raise HTTPException(
            status_code=422,
            detail="`provider` must be a string.",
        )
    provider = requested.strip().lower() if requested else None
    effective = provider or (os.getenv("LLM_PROVIDER") or "").strip().lower() or None

    # Defaults to False: a client that predates this field (or omits it) keeps
    # today's cache-respecting behaviour exactly (design spec §1.2, guard 1).
    # A *present* value must be an actual boolean — this is the switch that
    # re-spends the full LLM cost of the project, so a truthy-coerced typo
    # like {"force": "false"} silently becoming a forced run would be exactly
    # the "hard to hit by accident" guarantee failing by surprise.
    _body = payload or {}
    if "force" in _body and not isinstance(_body["force"], bool):
        raise HTTPException(
            status_code=422,
            detail="`force` must be a boolean.",
        )
    force = bool(_body.get("force", False))
    if effective is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No extraction provider is configured. Set LLM_PROVIDER in the API "
                f"environment (one of: {', '.join(PROVIDER_KEYS)}) and restart it, "
                "or name a provider in this request."
            ),
        )
    if effective not in PROVIDER_KEYS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown provider '{effective}'. "
                f"Choose one of: {', '.join(PROVIDER_KEYS)}."
            ),
        )
    if not _provider_ready(effective):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Provider '{effective}' is not configured. "
                f"Set {PROVIDER_KEYS[effective]} in the API environment "
                f"and restart it."
            ),
        )

    from shared.llm.factory import get_client

    # The scanned-PDF transcription fallback uses Anthropic; enable it only when
    # an Anthropic key is present. This is deliberately independent of the
    # selected provider — see §5.1 of the spec.
    pdf_fallback = None
    if os.getenv("ANTHROPIC_API_KEY"):
        from procurement.pdf_llm import transcribe_pdf

        pdf_fallback = transcribe_pdf
    try:
        run_ingestion(ROOT, slug, get_client(provider), pdf_fallback=pdf_fallback,
                     force=force)
    except Exception as exc:  # surface extraction failures to the UI verbatim
        raise HTTPException(status_code=502, detail=f"Ingestion failed: {exc}") from exc
    return _setup_state(proj.load_project(ROOT, slug))


# ------------------------------------------------------------- static front end
# In a container the compiled React bundle is served from this same app, so the
# UI and the API share one origin and CORS never applies. Mounted last, after
# every /api route, and only when a build is present — a source checkout without
# `npm run build` keeps serving the API alone.
_WEB_DIST = os.environ.get("WEB_DIST", os.path.join(os.path.dirname(__file__), "..", "web", "dist"))
if os.path.isdir(_WEB_DIST):
    app.mount("/", StaticFiles(directory=_WEB_DIST, html=True), name="web")
