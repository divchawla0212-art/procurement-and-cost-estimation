"""Read and write the authoritative JSON snapshots.

Generation semantics: individual saves never bump. Wrap a set of related
saves in `transaction()` and the counter bumps exactly once, on success.
"""
import os
from contextlib import contextmanager

from procurement.project import load_project, save_project
from procurement.store import layout
from procurement.store.models import DocumentRecord, VendorFacts


def get_generation(root: str, slug: str) -> int:
    return load_project(root, slug).generation


def bump_generation(root: str, slug: str) -> int:
    project = load_project(root, slug)
    project.generation += 1
    save_project(root, project)
    return project.generation


@contextmanager
def transaction(root: str, slug: str):
    """Bump `generation` once, only if the body completes without raising.

    This is NOT a rollback: it does not undo the writes a failed body already
    made. Each individual save is atomic, but the set is not, so a body that
    raises half way leaves the earlier files written and only withholds the
    generation bump. Callers whose partial state would be misread on the next
    run must record their own completion marker inside the body (see
    `migrate.migrate_dataset_json`) rather than inferring it from what happens
    to be on disk.
    """
    yield
    bump_generation(root, slug)


def load_documents(root: str, slug: str) -> list[DocumentRecord]:
    raw = layout.read_json(layout.documents_path(root, slug), default=[])
    return [DocumentRecord.model_validate(r) for r in raw]


def save_documents(root: str, slug: str, docs: list[DocumentRecord]) -> None:
    layout.atomic_write_json(layout.documents_path(root, slug),
                             [d.model_dump() for d in docs])


def load_facts(root: str, slug: str, vendor: str) -> VendorFacts | None:
    raw = layout.read_json(layout.facts_path(root, slug, vendor))
    return VendorFacts.model_validate(raw) if raw is not None else None


def save_facts(root: str, slug: str, facts: VendorFacts) -> None:
    layout.atomic_write_json(layout.facts_path(root, slug, facts.vendor),
                             facts.model_dump())


def delete_facts(root: str, slug: str, vendor: str) -> bool:
    """Remove a vendor's facts snapshot. Returns True if there was one.

    Used when a vendor leaves the project: the snapshots are authoritative, so
    facts for a vendor the project no longer has would be contradictory state,
    not merely unused state.
    """
    path = layout.facts_path(root, slug, vendor)
    if not os.path.exists(path):
        return False
    os.remove(path)
    vdir = os.path.dirname(path)
    if os.path.isdir(vdir) and not os.listdir(vdir):
        os.rmdir(vdir)
    return True


def list_fact_vendors(root: str, slug: str) -> list[str]:
    vdir = os.path.join(layout.store_dir(root, slug), "vendors")
    if not os.path.isdir(vdir):
        return []
    return sorted(v for v in os.listdir(vdir)
                  if os.path.exists(layout.facts_path(root, slug, v)))
