"""Key-free API tests for the compliance matrix review surface."""

from fastapi.testclient import TestClient

from procurement.project import create_project, load_project, save_project
from procurement.store import snapshots
from procurement.store.models import (ComplianceResult, RequirementRecord,
                                      RequirementSet, req_id_for)

NOW = "2026-07-31T00:00:00+00:00"


def _seed(root: str) -> None:
    create_project(root, "P")
    project = load_project(root, "p")
    project.vendors = ["KERUI"]
    save_project(root, project)
    requirements = [
        RequirementRecord(req_id=req_id_for("d1", "1.1"), clause_ref="1.1",
                          text="H2S at least 50 ppm", checkability="auto",
                          parameter="h2s", operator=">=", value=50, unit="ppm",
                          source_doc_id="d1"),
        RequirementRecord(req_id=req_id_for("d1", "9.1"), clause_ref="9.1",
                          text="Submit an O&M manual", source_doc_id="d1"),
    ]
    cells = [
        ComplianceResult(req_id=req_id_for("d1", "1.1"), vendor="KERUI",
                         verdict="fail", fact_id="f-1", doc_id="d9",
                         rationale="40 ppm = 40 ppm >= 50 ppm", evaluated_at=NOW),
        ComplianceResult(req_id=req_id_for("d1", "9.1"), vendor="KERUI",
                         verdict="review", rationale="human judgement required",
                         evaluated_at=NOW),
    ]
    snapshots.save_requirements(root, "p", RequirementSet(requirements=requirements))
    snapshots.save_compliance(root, "p", cells)


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    # ROOT is read at import time in api.main — reload after env is set.
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return TestClient(api_main.app)


def test_list_projects_returns_seeded_project(tmp_path, monkeypatch):
    _seed(str(tmp_path))
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/projects")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["slug"] == "p"
    assert body[0]["vendors"] == ["KERUI"]


def test_get_project_404_for_unknown_slug(tmp_path, monkeypatch):
    create_project(str(tmp_path), "P")
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/projects/missing").status_code == 404


def test_compliance_matrix_returns_cells_and_groups(tmp_path, monkeypatch):
    _seed(str(tmp_path))
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/projects/p/compliance-matrix")
    assert response.status_code == 200
    body = response.json()
    assert body["vendors"] == ["KERUI"]
    assert len(body["rows"]) == 2
    assert "not_matched" in body["groups"]
    assert "needs_human" in body["groups"]
    assert "matched" in body["groups"]
    fail_row = next(r for r in body["rows"] if r["clause_ref"] == "1.1")
    assert fail_row["cells"]["KERUI"]["verdict"] == "fail"
    assert fail_row["cells"]["KERUI"]["rationale"] == "40 ppm = 40 ppm >= 50 ppm"
    assert body["coverage"]["auto_cells"] == 1


def test_empty_project_returns_empty_matrix_not_error(tmp_path, monkeypatch):
    create_project(str(tmp_path), "P")
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/projects/p/compliance-matrix")
    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == []
    assert body["vendors"] == []


def test_summary_reports_counts_and_coverage(tmp_path, monkeypatch):
    _seed(str(tmp_path))
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/projects/p/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["vendors"] == ["KERUI"]
    assert body["requirement_count"] == 2
    assert body["coverage"]["auto_cells"] == 1
    assert set(body["group_counts"]) == {"not_matched", "needs_human", "matched"}


def test_statement_endpoint_returns_vendors_and_rows(tmp_path, monkeypatch):
    _seed(str(tmp_path))
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/projects/p/statement")
    assert response.status_code == 200
    body = response.json()
    assert body["vendors"] == ["KERUI"]
    assert isinstance(body["rows"], list)
    assert body["currency"]


def test_health(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/health").json() == {"ok": True}
