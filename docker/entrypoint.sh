#!/bin/sh
# Seed bundled sample projects into the store volume, then hand off to the CMD.
#
# The samples ship in the image at /app/samples, read-only and root-owned. They
# are copied into PROCUREMENT_PROJECTS_ROOT at start rather than baked straight
# into /data/projects, because Docker only initialises a named volume from image
# content when that volume is *empty* — baking would silently skip every host
# that already has a store. Copying here is deterministic either way.
#
# A sample is never installed over an existing slug. The store under
# projects/<slug>/store/ is authoritative (see CLAUDE.md); clobbering a live
# project with a fixture would destroy award decisions.
set -e

ROOT="${PROCUREMENT_PROJECTS_ROOT:-/data/projects}"
SEED_DIR="${SAMPLE_SEED_DIR:-/app/samples}"

if [ "${SEED_SAMPLE_PROJECTS:-1}" = "1" ] && [ -d "$SEED_DIR" ]; then
    for src in "$SEED_DIR"/*/; do
        [ -d "$src" ] || continue          # no matches: the glob stayed literal
        slug=$(basename "$src")
        dest="$ROOT/$slug"
        if [ -e "$dest" ]; then
            echo "seed: '$slug' already in the store, left untouched"
            continue
        fi
        mkdir -p "$ROOT"
        # Copy to a temp name first, then rename. A half-copied project
        # directory that already holds project.json is discoverable by
        # procurement.project.list_projects, so it must never be visible under
        # its real slug while the copy is still in flight.
        staging="$ROOT/.seeding-$slug"
        rm -rf "$staging"
        cp -R "$src." "$staging"
        mv "$staging" "$dest"
        echo "seed: installed sample project '$slug'"
    done
fi

exec "$@"
