"""Key-free API tests for the per-vendor extraction status panel."""

import pytest
from fastapi.testclient import TestClient

from procurement.project import create_project, load_project, save_project
from procurement.store import snapshots
from procurement.store.models import ComplianceResult, DocumentRecord, VendorFacts

from tests.auth_helpers import signed_in_admin

NOW = "2026-08-02T00:00:00+00:00"


@pytest.fixture
def project_slug(tmp_path):
    """Seed the same shape as tests/test_extraction_status.py's `_store`:
    one vendor with a mix of ok/failed/skipped documents and one vendor with
    none, so both the panel and the summary roll-up have something to show."""
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
    ])
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="ADPOWER",
        commercial={"vendor": "ADPOWER", "base_price": 1110836.0},
        quotation_doc_id="d1",
        technical=[{"fact_id": "f-1", "parameter": "continuous_rating",
                    "value": 525.0, "unit": "kW", "doc_id": "d2"}]))
    snapshots.save_compliance(root, "p", [
        ComplianceResult(req_id="r-1", vendor="ADPOWER", verdict="unanswered",
                         evaluated_at=NOW),
    ])
    return "p"


@pytest.fixture
def client(tmp_path, monkeypatch, project_slug) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    # ROOT is read at import time in api.main — reload after env is set.
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return signed_in_admin(TestClient(api_main.app), tmp_path)


def test_extraction_status_route_returns_every_vendor(client, project_slug):
    body = client.get(f"/api/projects/{project_slug}/extraction-status").json()
    assert [v["vendor"] for v in body["vendors"]] == ["ADPOWER", "SILENT"]
    assert body["totals"]["failed"] >= 1


def test_extraction_status_404s_on_an_unknown_project(client):
    assert client.get("/api/projects/nope/extraction-status").status_code == 404


def test_project_summary_carries_the_extraction_rollup(client, project_slug):
    body = client.get(f"/api/projects/{project_slug}/summary").json()
    rollup = {v["vendor"]: v for v in body["extraction"]}
    # the Dashboard card needs the counts without fetching the full document list
    assert rollup["ADPOWER"]["extracted"] == 2
    assert rollup["ADPOWER"]["failed"] == 1
    assert "documents" not in rollup["ADPOWER"]
