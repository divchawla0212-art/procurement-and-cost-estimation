"""Read and write the authoritative JSON snapshots.

Generation semantics: individual saves never bump. Wrap a set of related
saves in `transaction()` and the counter bumps exactly once, on success.
"""
import os
import threading
from contextlib import contextmanager

from procurement.project import load_project, save_project, update_project
from procurement.store import layout
from procurement.store.models import (ComplianceResult, DocumentRecord,
                                      RequirementSet, VendorFacts)

# Per-vendor read-modify-write serialisation (BUG-010). facts.json had
# BUG-009's defect and none of its guard: pipeline.py held a vendor's facts
# across an entire LLM extraction and then wrote them back, discarding a
# reviewer's note or a corrected rate's totals written meanwhile.
#
# Per vendor, not per project: two vendors' facts are separate files with no
# cross-field rule between them, and a per-project lock would serialise a run
# against itself for nothing.
#
# A plain Lock, not an RLock, for project.py:23-27's reason -- re-entering it
# would mean an inner update saving and the outer then saving its own older
# copy over the top, which is the lost write this exists to prevent, only
# harder to see. Nothing nests today; keep it that way.
_FACTS_LOCKS: dict[str, threading.Lock] = {}
_FACTS_LOCKS_GUARD = threading.Lock()


def _facts_lock_for(root: str, slug: str, vendor: str) -> threading.Lock:
    key = os.path.join(os.path.realpath(root), slug, vendor)
    with _FACTS_LOCKS_GUARD:
        return _FACTS_LOCKS.setdefault(key, threading.Lock())


@contextmanager
def facts_lock(root: str, slug: str, vendor: str):
    """Serialise against other writers of this vendor's facts.

    For the one caller whose write is conditional on the facts being absent
    (`migrate.migrate_dataset_json`) and so cannot express itself as a
    read-modify-write. Every other writer wants `update_facts`.
    """
    with _facts_lock_for(root, slug, vendor):
        yield


@contextmanager
def update_facts(root: str, slug: str, vendor: str, *, create: bool = False):
    """Load, mutate, save - atomically with respect to other mutators.

    Mutate the yielded object and assign ONLY the fields your caller owns:
    every field you leave alone keeps whatever is stored now, which is the
    point. Saved on a clean exit, left untouched if the body raises.

    `create=True` yields a fresh VendorFacts when none is stored; the default
    raises LookupError. It is a parameter rather than a check at the call site
    because a check outside the lock is a TOCTOU - the vendor can be pruned
    between the check and the acquire.

    Does NOT bump `generation`: that is `transaction`'s job, once per write
    transaction rather than once per file. Do not call `transaction`,
    `bump_generation` or `update_project` from inside this body - the project
    lock must never be taken while a facts lock is held.
    """
    with _facts_lock_for(root, slug, vendor):
        facts = load_facts(root, slug, vendor)
        if facts is None:
            if not create:
                raise LookupError(f"no facts stored for {vendor}")
            facts = VendorFacts(vendor=vendor)
        yield facts
        save_facts(root, slug, facts)


def get_generation(root: str, slug: str) -> int:
    return load_project(root, slug).generation


def bump_generation(root: str, slug: str) -> int:
    # Read-modify-write under the project lock (BUG-009). Unlocked, two
    # transactions that interleaved here both read N and both wrote N+1, so a
    # counter documented as moving once per transaction moved once for two.
    with update_project(root, slug) as project:
        project.generation += 1
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
    """Write a vendor's facts wholesale.

    Right for a first write, and for tests that construct facts directly. What
    must not come back is the load/modify/save pair spelled out at a call site
    - that pair is BUG-010, and `update_facts` is the replacement.
    """
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


def load_requirements(root: str, slug: str) -> RequirementSet:
    """Absent snapshot -> an empty set, not None. 'No spec has been extracted'
    and 'the spec stated nothing' are the same thing to every reader here, and
    an Optional return would put a None-check in front of each of them."""
    raw = layout.read_json(layout.requirements_path(root, slug))
    return RequirementSet.model_validate(raw) if raw is not None else RequirementSet()


def save_requirements(root: str, slug: str, reqset: RequirementSet) -> None:
    layout.atomic_write_json(layout.requirements_path(root, slug), reqset.model_dump())


def load_compliance(root: str, slug: str) -> list[ComplianceResult]:
    raw = layout.read_json(layout.compliance_path(root, slug), default=[])
    return [ComplianceResult.model_validate(r) for r in raw]


def save_compliance(root: str, slug: str, results: list[ComplianceResult]) -> None:
    layout.atomic_write_json(layout.compliance_path(root, slug),
                             [r.model_dump() for r in results])


def list_fact_vendors(root: str, slug: str) -> list[str]:
    vdir = os.path.join(layout.store_dir(root, slug), "vendors")
    if not os.path.isdir(vdir):
        return []
    return sorted(v for v in os.listdir(vdir)
                  if os.path.exists(layout.facts_path(root, slug, v)))
