# syntax=docker/dockerfile:1

# ---------------------------------------------------------------- web build --
# The React SPA is compiled here and copied into the Python image, so the
# runtime serves API and UI from one origin (no nginx, no CORS in production).
FROM node:22-alpine AS web
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ------------------------------------------------------------------ runtime --
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies are resolved from pyproject.toml alone so this layer stays cached
# across source edits. pyproject remains the single source of truth for them.
COPY pyproject.toml ./
RUN python - <<'PY' > /tmp/requirements.txt
import tomllib
with open("pyproject.toml", "rb") as fh:
    project = tomllib.load(fh)["project"]
print("\n".join(project["dependencies"]))
PY
RUN python -m pip install --upgrade pip setuptools wheel \
 && python -m pip install -r /tmp/requirements.txt

# Application source, then the compiled SPA on top.
COPY . .
COPY --from=web /build/dist ./web/dist
RUN python -m pip install --no-deps -e .

# The store lives on a volume, never in the image — see CLAUDE.md store invariants.
ENV PROCUREMENT_PROJECTS_ROOT=/data/projects \
    WEB_DIST=/app/web/dist

RUN useradd --create-home --uid 10001 app \
 && mkdir -p /data/projects \
 && chown -R app:app /data /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health').read()"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
