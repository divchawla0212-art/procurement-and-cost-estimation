# syntax=docker/dockerfile:1

# Which bundled sample project to ship. `gas-14` is an ingested three-vendor
# project used as the out-of-the-box sample. Build with `--build-arg SAMPLE=none`
# on a checkout that has no `projects/gas-14` — that directory is gitignored, so
# a clean clone has nothing to copy. BuildKit only builds the stage actually
# referenced, so `none` never touches `projects/` at all.
ARG SAMPLE=gas-14

# Whether to bake `.env` — including the live provider key — into the image.
#
# `true` produces a self-contained deployment artifact that needs no runtime
# secret injection. It also means **anyone who can pull the image can read the
# key**: `docker run --rm <image> cat /app/.env` is the whole attack, and image
# layers cannot be edited after the fact, only replaced. Never push such an
# image to a repository whose readers should not hold that key, and rotate the
# key rather than deleting a tag if one escapes.
#
# `false` (the default) leaves the key to runtime — compose `env_file`, an
# orchestrator secret, or `-e`. Build the image you hand to anyone else this
# way. `docker-compose.yml` passes `true` for the local deployment build.
ARG INCLUDE_ENV=false

# ---------------------------------------------------------------- web build --
# The React SPA is compiled here and copied into the Python image, so the
# runtime serves API and UI from one origin (no nginx, no CORS in production).
FROM node:22-alpine AS web
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ------------------------------------------------------------ sample seeds --
# One stage per selectable sample, so a missing fixture directory is a build
# that opts out, not a build that fails. `sample-gas-14` extends `sample-none`
# so /seed exists in both and the runtime COPY below is unconditional.
FROM python:3.12-slim AS sample-none
RUN mkdir -p /seed

FROM sample-none AS sample-gas-14
COPY projects/gas-14 /seed/gas-14

FROM sample-${SAMPLE} AS sample

# ------------------------------------------------------------------ env seed --
# Same lazy-stage trick as the sample: with INCLUDE_ENV=false, BuildKit never
# builds `env-true`, so `.env` is never read and never reaches a layer — not
# even one that is later deleted.
FROM python:3.12-slim AS env-false
RUN mkdir -p /envseed

FROM env-false AS env-true
COPY .env /envseed/.env

FROM env-${INCLUDE_ENV} AS envseed

# ---------------------------------------------------------------- app source --
# `.dockerignore` has to re-include `projects/gas-14` so the sample stage above
# can see it, which also puts it back in reach of the application `COPY`. Strip
# it here rather than in the runtime stage: a `rm` in a later layer hides the
# files without reclaiming their size, and /app/projects is the default
# PROCUREMENT_PROJECTS_ROOT fallback — a stale copy of a project sitting there
# would be read as a store the moment that variable went unset.
FROM python:3.12-slim AS source
WORKDIR /src
COPY . .
# `.env` is stripped here even though the build context now carries it: it must
# reach the image only through the `envseed` stage above, so INCLUDE_ENV=false
# really means absent. Without this line the application COPY would smuggle it
# in regardless of the switch.
RUN rm -rf projects data processed-data .env

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
COPY --from=source /src .
COPY --from=web /build/dist ./web/dist
RUN python -m pip install --no-deps -e .

# The store lives on a volume, never in the image — see CLAUDE.md store invariants.
ENV PROCUREMENT_PROJECTS_ROOT=/data/projects \
    WEB_DIST=/app/web/dist

RUN useradd --create-home --uid 10001 app \
 && mkdir -p /data/projects \
 && chown -R app:app /data /app

# After the chown above, deliberately: a recursive chown rewrites every file it
# touches into a new layer, and the sample is the largest thing in the image.
# The seed only needs to be readable by `app`, which root-owned already is.
COPY --from=sample /seed /app/samples
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

# Empty when INCLUDE_ENV=false, so this is a no-op there. `load_dotenv()` in
# api/main.py walks up from the module and finds /app/.env; it never overrides a
# real environment variable, so a runtime `-e` or compose `environment:` still
# wins over the baked file.
COPY --from=envseed /envseed/. /app/
RUN if [ -f /app/.env ]; then chown app:app /app/.env && chmod 0600 /app/.env; fi

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health').read()"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
