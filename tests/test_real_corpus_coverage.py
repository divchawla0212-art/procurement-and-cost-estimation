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


def test_few_vendor_documents_are_skipped_without_extraction():
    store = _latest_multi_vendor_store()
    if store is None:
        pytest.skip("no ingested multi-vendor project in projects/")
    with open(os.path.join(store, "documents.json"), encoding="utf-8") as fh:
        docs = json.load(fh)
    vendor_docs = [d for d in docs if d.get("vendor")]
    if not vendor_docs:
        pytest.skip("no vendor documents ingested")
    skipped = [d["path"] for d in vendor_docs
               if d["extraction_status"] == "skipped"]
    # The pre-plan figure was 23/32 = 0.72 - two thirds of the corpus was never
    # read at all. Nothing else here catches that: a skipped document has no
    # text_source and is excluded from the check above, so a routing regression
    # would make that test *greener* rather than redder, and the two loudest
    # numbers this plan moved would go unguarded.
    #
    # A ceiling rather than an exact count, because a skip is not by itself a
    # defect: a superseded revision should be skipped, and a heavily re-issued
    # tender legitimately carries many. 0.25 sits well clear of both ends - four
    # times today's 2/36, and comfortably under the 0.72 it has to fail on.
    assert len(skipped) / len(vendor_docs) < 0.25, (
        f"{len(skipped)}/{len(vendor_docs)} vendor documents were skipped "
        f"without extraction: {skipped}")


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
