"""Key-free API tests for the two export routes.

These assert the transport contract only — that the right bytes reach the
browser under a filename it will save. What is *inside* the workbook is
`test_procurement_export.py`'s business.
"""
import io

import openpyxl
import pytest
from fastapi.testclient import TestClient

from procurement.project import create_project, load_project, save_project
from procurement.store import snapshots
from procurement.store.models import (ComplianceResult, RequirementRecord,
                                      RequirementSet, req_id_for)

NOW = "2026-08-03T00:00:00+00:00"

XLSX_MIME = ("application/vnd.openxmlformats-officedocument"
             ".spreadsheetml.sheet")


def _seed(root: str) -> None:
    create_project(root, "P")
    project = load_project(root, "p")
    project.vendors = ["KERUI"]
    save_project(root, project)
    snapshots.save_requirements(root, "p", RequirementSet(requirements=[
        RequirementRecord(req_id=req_id_for("d1", "1.1"), clause_ref="1.1",
                          text="H2S at least 50 ppm", checkability="auto",
                          parameter="h2s", operator=">=", value=50, unit="ppm",
                          source_doc_id="d1")]))
    snapshots.save_compliance(root, "p", [
        ComplianceResult(req_id=req_id_for("d1", "1.1"), vendor="KERUI",
                         verdict="fail", fact_id="f-1", doc_id="d9",
                         rationale="40 ppm >= 50 ppm is false",
                         evaluated_at=NOW)])


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return TestClient(api_main.app)


DOCUMENTS = [
    ("compliance-matrix", "compliance-matrix"),
    ("statement", "comparative-statement"),
]


@pytest.mark.parametrize("route,stem", DOCUMENTS)
def test_xlsx_download_is_an_attachment_named_for_the_project(
        tmp_path, monkeypatch, route, stem):
    _seed(str(tmp_path))
    response = _client(tmp_path, monkeypatch).get(
        f"/api/projects/p/{route}/export?format=xlsx")
    assert response.status_code == 200
    assert response.headers["content-type"] == XLSX_MIME
    assert response.headers["content-disposition"] == (
        f'attachment; filename="p-{stem}.xlsx"')
    # Proves it is a real workbook, not an error page with a hopeful header.
    openpyxl.load_workbook(io.BytesIO(response.content))


@pytest.mark.parametrize("route,stem", DOCUMENTS)
def test_csv_download_is_an_attachment_named_for_the_project(
        tmp_path, monkeypatch, route, stem):
    _seed(str(tmp_path))
    response = _client(tmp_path, monkeypatch).get(
        f"/api/projects/p/{route}/export?format=csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="p-{stem}.csv"')


@pytest.mark.parametrize("route,_stem", DOCUMENTS)
def test_csv_carries_a_bom_so_excel_does_not_read_it_as_cp1252(
        tmp_path, monkeypatch, route, _stem):
    """The rationale and the statement's tally both carry non-ASCII. Without
    the BOM Excel decodes the file as cp1252 and the deliverable is mojibake."""
    _seed(str(tmp_path))
    response = _client(tmp_path, monkeypatch).get(
        f"/api/projects/p/{route}/export?format=csv")
    assert response.content.startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize("route,_stem", DOCUMENTS)
def test_an_unknown_format_is_refused_not_silently_defaulted(
        tmp_path, monkeypatch, route, _stem):
    _seed(str(tmp_path))
    response = _client(tmp_path, monkeypatch).get(
        f"/api/projects/p/{route}/export?format=pdf")
    assert response.status_code == 422


@pytest.mark.parametrize("route,_stem", DOCUMENTS)
def test_an_unknown_project_is_a_404(tmp_path, monkeypatch, route, _stem):
    _seed(str(tmp_path))
    response = _client(tmp_path, monkeypatch).get(
        f"/api/projects/nope/{route}/export?format=xlsx")
    assert response.status_code == 404


def test_the_matrix_export_carries_the_stored_rationale(tmp_path, monkeypatch):
    _seed(str(tmp_path))
    response = _client(tmp_path, monkeypatch).get(
        "/api/projects/p/compliance-matrix/export?format=xlsx")
    wb = openpyxl.load_workbook(io.BytesIO(response.content))
    assert wb.sheetnames == ["Matrix", "Detail"]
    assert any(cell.value == "40 ppm >= 50 ppm is false"
               for row in wb["Detail"].iter_rows() for cell in row)


def test_the_export_never_writes_to_the_store(tmp_path, monkeypatch):
    """INV: the compliance surface is read-only. Downloading is a read, and a
    read must leave `generation` where it found it."""
    _seed(str(tmp_path))
    client = _client(tmp_path, monkeypatch)
    before = load_project(str(tmp_path), "p").generation
    client.get("/api/projects/p/compliance-matrix/export?format=xlsx")
    client.get("/api/projects/p/statement/export?format=csv")
    assert load_project(str(tmp_path), "p").generation == before
