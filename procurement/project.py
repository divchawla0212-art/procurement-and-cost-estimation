import os
import re
import json
import zipfile
from datetime import datetime, timezone
from procurement.models import Project
from procurement.store import layout

_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    return _SLUG.sub("-", name.lower()).strip("-")


def _validate_slug(root: str, slug: str) -> None:
    """Validate that slug does not escape root. Raises ValueError if unsafe."""
    root_real = os.path.realpath(root)
    project_real = os.path.realpath(os.path.join(root, slug))
    if not (project_real == root_real or project_real.startswith(root_real + os.sep)):
        raise ValueError(f"Unsafe slug: {slug}")


def _project_dir(root: str, slug: str) -> str:
    return os.path.join(root, slug)


def save_project(root: str, project: Project) -> None:
    layout.atomic_write_json(
        os.path.join(_project_dir(root, project.slug), "project.json"),
        project.model_dump(),
    )


def create_project(root: str, name: str, target_currency: str = "USD") -> Project:
    slug = slugify(name)
    pdir = _project_dir(root, slug)
    os.makedirs(os.path.join(pdir, "requirements"), exist_ok=True)
    os.makedirs(os.path.join(pdir, "vendors"), exist_ok=True)
    project = Project(
        name=name, slug=slug,
        created_at=datetime.now(timezone.utc).isoformat(),
        target_currency=target_currency,
        store_version=layout.STORE_VERSION,
    )
    save_project(root, project)
    return project


def load_project(root: str, slug: str) -> Project:
    with open(os.path.join(_project_dir(root, slug), "project.json"), "r", encoding="utf-8") as fh:
        return Project.model_validate(json.load(fh))


def list_projects(root: str) -> list[Project]:
    if not os.path.isdir(root):
        return []
    out = []
    for slug in sorted(os.listdir(root)):
        if os.path.exists(os.path.join(root, slug, "project.json")):
            out.append(load_project(root, slug))
    return out


def _is_skippable(name: str) -> bool:
    # Normalize separators for consistent handling of both / and \ paths
    normalized = name.replace("\\", "/")
    parts = normalized.split("/")
    base = parts[-1]
    return (not base) or normalized.startswith("__MACOSX") or base.startswith(".") or base.startswith("~$")


def list_vendor_dirs(root: str, slug: str) -> list[str]:
    """The vendors that have actually been uploaded, read off the filesystem.

    `vendors/` is the ground truth for the roster, not any single archive: a
    buyer sends one ZIP per vendor as each bid arrives, so "what this archive
    contained" and "what this project has" are different questions.
    """
    vdir = os.path.join(_project_dir(root, slug), "vendors")
    if not os.path.isdir(vdir):
        return []
    return sorted(v for v in os.listdir(vdir)
                  if os.path.isdir(os.path.join(vdir, v)))


def unpack_vendor_zip(root: str, slug: str, zip_path: str) -> list[str]:
    """Unpack one vendor archive and return the project's full vendor roster."""
    _validate_slug(root, slug)
    vendors_dir = os.path.realpath(os.path.join(_project_dir(root, slug), "vendors"))
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir() or _is_skippable(name):
                continue
            dest = os.path.realpath(os.path.join(vendors_dir, name))
            if not (dest == vendors_dir or dest.startswith(vendors_dir + os.sep)):
                raise ValueError(f"Unsafe path in archive: {name}")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                out.write(src.read())
    # Scanned from disk, never from this archive's top-level names. Assigning
    # only what the ZIP carried silently withdrew every vendor uploaded before
    # it: `inventory_documents` walks `project.vendors`, so their files were
    # never classified or extracted, and the stale-vendor sweep in
    # `run_ingestion` then deleted the facts an earlier run had stored for
    # them. A vendor is withdrawn by removing its folder, which this still
    # reports, because then the folder is genuinely gone.
    project = load_project(root, slug)
    project.vendors = list_vendor_dirs(root, slug)
    save_project(root, project)
    return list(project.vendors)


def vendor_files(root: str, slug: str, vendor: str) -> list[str]:
    vdir = os.path.join(_project_dir(root, slug), "vendors", vendor)
    out = []
    for dirpath, _dirs, files in os.walk(vdir):
        for f in files:
            if not (f.startswith(".") or f.startswith("~$")):
                out.append(os.path.join(dirpath, f))
    return sorted(out)
