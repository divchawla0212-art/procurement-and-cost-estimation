"""One-shot import of a pre-store dataset.json into the project store.

Idempotent: a project that already has vendor facts is left untouched. The
source dataset.json is never modified or deleted.
"""
import os

from procurement.store import layout, snapshots
from procurement.store.models import VendorFacts


def migrate_dataset_json(root: str, slug: str) -> bool:
    if snapshots.list_fact_vendors(root, slug):
        return False                      # store already populated
    path = os.path.join(layout.project_dir(root, slug), "dataset.json")
    data = layout.read_json(path)
    if not data:
        return False

    normalized_by_vendor = {n.get("vendor"): n for n in data.get("normalized", [])}
    bids = data.get("bids", [])
    if not bids:
        return False

    with snapshots.transaction(root, slug):
        for bid in bids:
            vendor = bid.get("vendor")
            if not vendor:
                continue
            snapshots.save_facts(root, slug, VendorFacts(
                vendor=vendor,
                commercial=bid,
                normalized=normalized_by_vendor.get(vendor),
            ))
    return True
