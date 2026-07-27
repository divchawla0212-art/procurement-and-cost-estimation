import os
import json
from procurement.project import load_project, save_project, vendor_files
from procurement.extract import extract_bid
from procurement.normalize import normalize_bid
from procurement.compare import build_comparison


def run_ingestion(root: str, slug: str, client, pdf_fallback=None) -> dict:
    project = load_project(root, slug)
    bids = []
    normalized = []
    for vendor in project.vendors:
        files = vendor_files(root, slug, vendor)
        bid = extract_bid(vendor, files, client, pdf_fallback=pdf_fallback)
        bids.append(bid)
        normalized.append(normalize_bid(bid, project.target_currency, project.fx_rates))
    comparison = build_comparison(bids, normalized, project.target_currency)

    result = {
        "bids": [b.model_dump() for b in bids],
        "normalized": [n.model_dump() for n in normalized],
        "comparison": comparison.model_dump(),
    }
    with open(os.path.join(root, slug, "dataset.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, default=str)
    project.status = "done"
    save_project(root, project)
    return result


def load_dataset(root: str, slug: str) -> dict | None:
    path = os.path.join(root, slug, "dataset.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
