"""Recompute stored normalized totals from stored commercial facts.

Currency conversion is arithmetic over a price that has already been
extracted, so changing an FX rate must not cost an LLM call or a
re-extraction. `run_ingestion` only re-derives `normalized` for documents it
actually (re)extracts; a fully-cached document hits its `continue` before
ever reaching `normalize_bid`, so a plain re-run after setting a rate leaves
the store untouched. This module recomputes `normalized` for every vendor's
already-stored `commercial` facts directly, without touching documents,
classification, or extraction at all.

Does not import `procurement.pipeline`: pipeline imports
`procurement.store.migrate`, and pipeline is expected to import this module,
so importing pipeline back here would close an import cycle.
"""
from datetime import datetime, timezone

from procurement.models import VendorBid
from procurement.normalize import normalize_bid
from procurement.project import load_project, save_project
from procurement.store import events, layout, snapshots
from procurement.store.models import Event


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def renormalize(root: str, slug: str) -> int:
    """Recompute stored `normalized` for every vendor with stored `commercial`
    facts, from those facts and the project's current `target_currency` and
    `fx_rates`. Returns the number of vendors whose stored `normalized`
    changed.

    Store invariant owned here: after this returns, every vendor with stored
    `commercial` facts has a stored `normalized` exactly equal to what
    `normalize_bid` produces from those facts and the project's current
    settings -- for every such vendor, and no others are written. A vendor
    with no stored `commercial` facts is skipped, never zeroed: a failed
    extraction never blanks previously-good stored data.
    """
    project = load_project(root, slug)

    # Compute everything first. `transaction` bumps `generation` on any body
    # that completes, written or not, and Task 4 calls this from the read
    # path -- opening a transaction before knowing whether anything changed
    # would advance `generation` on every page load, forever. This pass only
    # decides WHETHER to open a transaction; its result is a candidate list
    # of vendor names, not the values to write -- see the recompute below.
    candidates = []
    for vendor in snapshots.list_fact_vendors(root, slug):
        facts = snapshots.load_facts(root, slug, vendor)
        if facts is None or not facts.commercial:
            continue                      # skipped, never zeroed
        fresh = normalize_bid(
            VendorBid.model_validate(facts.commercial),
            project.target_currency, project.fx_rates).model_dump()
        if fresh != facts.normalized:
            candidates.append(vendor)

    if not candidates:
        return 0

    changed = 0
    with snapshots.transaction(root, slug):
        for vendor in candidates:
            # Recompute inside the lock, from the facts and the project as
            # they are NOW. The pre-pass only decided candidacy; writing the
            # value it saw back over facts a concurrent run has since
            # changed is BUG-010.
            try:
                with snapshots.update_facts(root, slug, vendor) as facts:
                    if not facts.commercial:
                        continue               # candidacy evaporated
                    current_project = load_project(root, slug)
                    fresh = normalize_bid(
                        VendorBid.model_validate(facts.commercial),
                        current_project.target_currency,
                        current_project.fx_rates).model_dump()
                    if fresh == facts.normalized:
                        continue               # candidacy evaporated; not a change
                    facts.normalized = fresh   # the only field this function writes
                    changed += 1
            except LookupError:
                # The vendor was pruned (pipeline.py deletes a departed
                # vendor's facts inside a run) between the pre-pass and this
                # lock acquire. `update_facts(create=False)` raises rather
                # than resurrecting it; skip, don't recreate -- a stored
                # collection holds exactly the records of its currently-live
                # sources, and re-saving here would bring a deleted vendor
                # back. Not a TOCTOU: this is the lock-acquire failure
                # itself, not a pre-lock existence check.
                continue

    events.append_event(root, slug, Event(
        at=_now(), run_id=events.new_run_id(), actor="renormalize",
        action="fx.renormalized", detail={"vendors": changed}))
    return changed


def migrate_normalization(root: str, slug: str) -> bool:
    """One-shot: bring a pre-BUG-005 store's normalized totals up to date.

    Idempotent, and safe to retry after a partial run -- every value is
    derived from `commercial` facts this never modifies. Called from the read
    path (`pipeline._live_fact_vendors`), so completion is keyed on the
    `store_version` stamp written after `renormalize` returns, not on any
    inference from disk state: `snapshots.transaction` is not a rollback, so a
    half-finished migration must be retried rather than declared done.
    """
    project = load_project(root, slug)
    if project.store_version >= layout.STORE_VERSION:
        return False

    renormalize(root, slug)        # opens no transaction when nothing changes

    project = load_project(root, slug)     # reload: renormalize may have bumped
    project.store_version = layout.STORE_VERSION
    save_project(root, project)
    return True
