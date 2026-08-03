from procurement.coverage import build_extraction_status
from procurement.project import create_project, load_project, save_project
from procurement.store import snapshots
from procurement.store.models import (ComplianceResult, DocumentRecord,
                                      RequirementRecord, RequirementSet,
                                      VendorFacts)

NOW = "2026-08-02T00:00:00+00:00"


def _store(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    project = load_project(root, "p")
    project.vendors = ["ADPOWER", "SILENT"]
    save_project(root, project)

    snapshots.save_documents(root, "p", [
        DocumentRecord(doc_id="d1", path="vendors/ADPOWER/quote.pdf",
                       vendor="ADPOWER", doc_class="quotation",
                       content_sha256="a", extraction_status="ok",
                       text_source="pdftotext:11682chars"),
        DocumentRecord(doc_id="d2", path="vendors/ADPOWER/MR copy.pdf",
                       vendor="ADPOWER", doc_class="spec",
                       content_sha256="b", extraction_status="ok",
                       text_source="pdftotext:60696chars"),
        DocumentRecord(doc_id="d3", path="vendors/ADPOWER/layout.pdf",
                       vendor="ADPOWER", doc_class="drawing",
                       content_sha256="c", extraction_status="failed",
                       notes="no readable text layer (42 chars via pypdf)",
                       text_source="pypdf:42chars"),
        DocumentRecord(doc_id="d4", path="vendors/ADPOWER/old-quote.pdf",
                       vendor="ADPOWER", doc_class="quotation",
                       content_sha256="d", extraction_status="skipped",
                       superseded_by="d1", notes="superseded by d1"),
    ])
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="ADPOWER",
        commercial={"vendor": "ADPOWER", "base_price": 1110836.0},
        quotation_doc_id="d1",
        technical=[{"fact_id": "f-1", "parameter": "continuous_rating",
                    "value": 525.0, "unit": "kW", "doc_id": "d2"},
                   {"fact_id": "f-2", "parameter": "rated_voltage",
                    "value": 415.0, "unit": "V", "doc_id": "d2"}]))
    snapshots.save_requirements(root, "p", RequirementSet(requirements=[
        RequirementRecord(req_id="r-1", clause_ref="1.1", text="t",
                          source_doc_id="s", checkability="auto",
                          parameter="frequency", operator="==", value=50.0,
                          unit="Hz")]))
    snapshots.save_compliance(root, "p", [
        ComplianceResult(req_id="r-1", vendor="ADPOWER", verdict="unanswered",
                         evaluated_at=NOW),
        ComplianceResult(req_id="r-1", vendor="SILENT", verdict="unanswered",
                         evaluated_at=NOW)])
    return root


def test_each_document_reports_its_route_beside_its_class(tmp_path):
    status = build_extraction_status(_store(tmp_path), "p")
    docs = {d.filename: d for d in status.vendors[0].documents}
    # the whole point of the panel: classification and routing now differ
    assert (docs["MR copy.pdf"].doc_class, docs["MR copy.pdf"].route) == (
        "spec", "datasheet")
    assert (docs["quote.pdf"].doc_class, docs["quote.pdf"].route) == (
        "quotation", "quotation")


def test_each_document_reports_the_facts_it_contributed(tmp_path):
    status = build_extraction_status(_store(tmp_path), "p")
    docs = {d.filename: d for d in status.vendors[0].documents}
    assert docs["MR copy.pdf"].fact_count == 2
    assert docs["quote.pdf"].fact_count == 0        # commercial, not technical
    assert docs["quote.pdf"].is_quotation is True


def test_an_unreadable_document_carries_its_reason_and_reader(tmp_path):
    status = build_extraction_status(_store(tmp_path), "p")
    doc = next(d for d in status.vendors[0].documents if d.filename == "layout.pdf")
    assert doc.status == "failed"
    assert "no readable text layer" in doc.notes
    assert doc.text_source == "pypdf:42chars"


def test_vendor_totals_count_each_status(tmp_path):
    status = build_extraction_status(_store(tmp_path), "p")
    adpower = status.vendors[0]
    assert (adpower.extracted, adpower.failed, adpower.skipped) == (2, 1, 1)
    assert adpower.fact_count == 2
    assert adpower.has_commercial is True
    assert adpower.unanswered == 1


def test_a_vendor_with_no_documents_still_appears(tmp_path):
    status = build_extraction_status(_store(tmp_path), "p")
    silent = next(v for v in status.vendors if v.vendor == "SILENT")
    # a missing entry reads as "did not bid" — the rule build_matrix follows
    assert silent.documents == []
    assert (silent.extracted, silent.fact_count) == (0, 0)
    assert silent.has_commercial is False


# ---------------------------------------------------- a document read twice
#
# Task 7b's SECONDARY_VENDOR_ROUTE routes a vendor's quotation to the
# technical extractor as well, when no live datasheet reaches it. A document
# in that state has two outcomes — one per extractor — and Task 7b's fix round
# additively merges a failed secondary pass's reason into `notes` while
# keeping `secondary_notes` as the structured copy. `extraction_status="ok"`
# on such a document is not "nothing to report": the secondary pass may have
# failed right beside it, and that exact blind spot was a review finding
# earlier in this phase.


def _store_with_secondary(tmp_path, secondary_status="ok"):
    root = str(tmp_path)
    create_project(root, "P")
    project = load_project(root, "p")
    project.vendors = ["SOLO"]
    save_project(root, project)

    notes = "vendor's commercial terms"
    if secondary_status == "failed":
        notes += "; secondary pass failed (datasheet): token ceiling exceeded"

    snapshots.save_documents(root, "p", [
        DocumentRecord(doc_id="d1", path="vendors/SOLO/techno-commercial.pdf",
                       vendor="SOLO", doc_class="quotation",
                       content_sha256="a", extraction_status="ok",
                       notes=notes, text_source="pdftotext:220000chars",
                       secondary_status=secondary_status,
                       secondary_notes=(None if secondary_status == "ok"
                                        else "token ceiling exceeded"),
                       secondary_prompt_version="tech_facts_v1"),
    ])
    facts = VendorFacts(
        vendor="SOLO",
        commercial={"vendor": "SOLO", "base_price": 900000.0},
        quotation_doc_id="d1",
        technical=([{"fact_id": "f-1", "parameter": "continuous_rating",
                     "value": 500.0, "unit": "kW", "doc_id": "d1"}]
                   if secondary_status == "ok" else []))
    snapshots.save_facts(root, "p", facts)
    return root


def test_a_document_read_by_two_extractors_reports_both_routes(tmp_path):
    root = _store_with_secondary(tmp_path, secondary_status="ok")
    status = build_extraction_status(root, "p")
    doc = status.vendors[0].documents[0]
    assert doc.route == "quotation"
    assert doc.secondary_route == "datasheet"
    assert doc.secondary_status == "ok"
    # facts.technical is keyed by doc_id regardless of which route wrote it
    assert doc.fact_count == 1


def test_an_ok_primary_pass_still_reports_a_failed_secondary_pass(tmp_path):
    root = _store_with_secondary(tmp_path, secondary_status="failed")
    status = build_extraction_status(root, "p")
    doc = status.vendors[0].documents[0]
    assert doc.status == "ok"                       # the primary pass succeeded
    assert doc.secondary_status == "failed"          # but the secondary did not
    assert doc.secondary_notes == "token ceiling exceeded"
    assert "secondary pass failed" in doc.notes      # the merged, human-facing copy
    assert doc.fact_count == 0                       # nothing was contributed

    vendor = status.vendors[0]
    assert vendor.extracted == 1        # the primary pass is still counted ok
    assert vendor.secondary_failed == 1  # but the blind spot is surfaced


def test_a_document_with_no_secondary_pass_reports_none(tmp_path):
    status = build_extraction_status(_store(tmp_path), "p")
    doc = next(d for d in status.vendors[0].documents if d.filename == "quote.pdf")
    assert doc.secondary_route is None
    assert doc.secondary_status is None
    assert status.vendors[0].secondary_failed == 0
