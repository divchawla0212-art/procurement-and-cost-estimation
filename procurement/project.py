import os
import re
import json
import zipfile
from datetime import datetime, timezone
from procurement.models import Project

_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    return _SLUG.sub("-", name.lower()).strip("-")


def _project_dir(root: str, slug: str) -> str:
    return os.path.join(root, slug)


def save_project(root: str, project: Project) -> None:
    with open(os.path.join(_project_dir(root, project.slug), "project.json"), "w", encoding="utf-8") as fh:
        json.dump(project.model_dump(), fh, indent=2)


def create_project(root: str, name: str, target_currency: str = "USD") -> Project:
    slug = slugify(name)
    pdir = _project_dir(root, slug)
    os.makedirs(os.path.join(pdir, "requirements"), exist_ok=True)
    os.makedirs(os.path.join(pdir, "vendors"), exist_ok=True)
    project = Project(
        name=name, slug=slug,
        created_at=datetime.now(timezone.utc).isoformat(),
        target_currency=target_currency,
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
    parts = name.split("/")
    base = parts[-1]
    return (not base) or name.startswith("__MACOSX") or base.startswith(".") or base.startswith("~$")


def unpack_vendor_zip(root: str, slug: str, zip_path: str) -> list[str]:
    vendors_dir = os.path.realpath(os.path.join(_project_dir(root, slug), "vendors"))
    vendors: set[str] = set()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir() or _is_skippable(name):
                continue
            dest = os.path.realpath(os.path.join(vendors_dir, name))
            if not (dest == vendors_dir or dest.startswith(vendors_dir + os.sep)):
                raise ValueError(f"Unsafe path in archive: {name}")
            top = name.replace("\\", "/").split("/")[0]
            vendors.add(top)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                out.write(src.read())
    project = load_project(root, slug)
    project.vendors = sorted(vendors)
    save_project(root, project)
    return sorted(vendors)


def vendor_files(root: str, slug: str, vendor: str) -> list[str]:
    vdir = os.path.join(_project_dir(root, slug), "vendors", vendor)
    out = []
    for dirpath, _dirs, files in os.walk(vdir):
        for f in files:
            if not (f.startswith(".") or f.startswith("~$")):
                out.append(os.path.join(dirpath, f))
    return sorted(out)
