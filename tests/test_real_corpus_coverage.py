import json
import os
import pytest

_PROJECTS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "projects")

pytestmark = pytest.mark.skipif(
    not os.path.isdir(_PROJECTS),
    reason="requires the untracked projects/ store directory")


def _latest_multi_vendor_store():
    """The newest store holding more than one vendor, or None."""
    best = None
    for slug in os.listdir(_PROJECTS):
        store = os.path.join(_PROJECTS, slug, "store")
        vendors = os.path.join(store, "vendors")
        if not os.path.isdir(vendors) or len(os.listdir(vendors)) < 2:
            continue
        stamp = os.path.getmtime(os.path.join(_PROJECTS, slug, "project.json"))
        if best is None or stamp > best[0]:
            best = (stamp, store)
    return best[1] if best else None


def test_no_vendor_in_a_multi_vendor_store_has_zero_technical_facts():
    store = _latest_multi_vendor_store()
    if store is None:
        pytest.skip("no ingested multi-vendor project in projects/")
    empty = []
    for vendor in os.listdir(os.path.join(store, "vendors")):
        path = os.path.join(store, "vendors", vendor, "facts.json")
        with open(path, encoding="utf-8") as fh:
            facts = json.load(fh)
        if not facts.get("technical"):
            empty.append(vendor)
    # ADPOWER had zero facts in every run before this plan, because their only
    # technical document was their marked-up copy of the client spec
    assert not empty, f"vendors with no technical facts at all: {empty}"


def test_every_extracted_document_records_a_text_source():
    store = _latest_multi_vendor_store()
    if store is None:
        pytest.skip("no ingested multi-vendor project in projects/")
    with open(os.path.join(store, "documents.json"), encoding="utf-8") as fh:
        docs = json.load(fh)
    missing = [d["path"] for d in docs
               if d.get("vendor") and d["extraction_status"] in ("ok", "failed")
               and not d.get("text_source")]
    assert not missing, f"documents with no text_source: {missing}"


def test_a_majority_of_requirements_are_machine_checkable():
    store = _latest_multi_vendor_store()
    if store is None:
        pytest.skip("no ingested multi-vendor project in projects/")
    with open(os.path.join(store, "requirements.json"), encoding="utf-8") as fh:
        reqs = json.load(fh)["requirements"]
    if not reqs:
        pytest.skip("no requirements ingested")
    checked = [r for r in reqs if r["checkability"] in ("auto", "stated")]
    # the pre-plan figure was 14/100. The floor is deliberately well under the
    # target: this guards against regression, it does not certify the prompt.
    assert len(checked) / len(reqs) >= 0.35, (
        f"only {len(checked)}/{len(reqs)} requirements are machine-checkable")
