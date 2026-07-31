"""One-shot import of a pre-store dataset.json into the project store.

Idempotent, and keyed on a positive marker (`store/migrated.json`) written
inside the transaction rather than on "the store already has some facts".
That distinction is load-bearing: `transaction()` is not a rollback, so a
migration that dies half way leaves the first vendors written. Inferring
completion from those leftovers would declare the project migrated and drop
the vendors that never made it, permanently and silently. With a marker, an
incomplete migration is simply retried.

The retry does not overwrite facts that are already present - anything written
after the failed attempt (an ingestion run, a human correction) is newer than
dataset.json and wins. The source dataset.json is never modified or deleted.
"""
import os
from datetime import datetime, timezone

from procurement.store import layout, snapshots
from procurement.store.models import VendorFacts


def is_migrated(root: str, slug: str) -> bool:
    return os.path.exists(layout.migration_marker_path(root, slug))


def migrate_dataset_json(root: str, slug: str) -> bool:
    if is_migrated(root, slug):
        return False                      # already imported, marker proves it
    path = os.path.join(layout.project_dir(root, slug), "dataset.json")
    data = layout.read_json(path)
    if not data:
        return False

    normalized_by_vendor = {n.get("vendor"): n for n in data.get("normalized", [])}
    bids = data.get("bids", [])
    if not bids:
        return False

    imported: list[str] = []
    with snapshots.transaction(root, slug):
        for bid in bids:
            vendor = bid.get("vendor")
            if not vendor:
                continue
            if snapshots.load_facts(root, slug, vendor) is not None:
                continue              # already in the store and newer than this
            snapshots.save_facts(root, slug, VendorFacts(
                vendor=vendor,
                commercial=bid,
                normalized=normalized_by_vendor.get(vendor),
            ))
            imported.append(vendor)
        layout.atomic_write_json(layout.migration_marker_path(root, slug), {
            "source": "dataset.json",
            "at": datetime.now(timezone.utc).isoformat(),
            "store_version": layout.STORE_VERSION,
            "vendors": imported,
        })
    return True
