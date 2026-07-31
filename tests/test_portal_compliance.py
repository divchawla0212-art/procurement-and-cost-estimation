"""The compliance tab renders, and writes nothing.

`build_matrix` is tested directly in `test_compliance_matrix.py`; this file
only proves the screen is wired into the app and survives a real Streamlit
render, which is the part an import-level test cannot show.
"""
import os

from streamlit.testing.v1 import AppTest

from procurement.project import create_project, load_project, save_project
from procurement.store import snapshots
from procurement.store.models import (ComplianceResult, RequirementRecord,
                                      RequirementSet, req_id_for)

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "portal", "app.py")
NOW = "2026-07-31T00:00:00+00:00"


def _seed(root):
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


def _open_project(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.selectbox[0].set_value("p")
    at.run()
    return at


def test_the_compliance_tab_renders_a_stored_matrix(tmp_path, monkeypatch):
    _seed(str(tmp_path))
    at = _open_project(tmp_path, monkeypatch)

    assert not at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "Compliance" in body
    assert "Not matched" in body and "Needs a human" in body
    # the rationale is shown to the reader verbatim
    assert any("40 ppm" in c.value for c in at.caption)


def test_rendering_the_screen_bumps_no_generation(tmp_path, monkeypatch):
    # spec section 2: the screen is read-only, and a view that quietly wrote
    # would show up here as a generation bump
    root = str(tmp_path)
    _seed(root)
    before = snapshots.get_generation(root, "p")
    at = _open_project(tmp_path, monkeypatch)
    assert not at.exception
    assert snapshots.get_generation(root, "p") == before


def test_a_project_with_no_matrix_says_so_instead_of_breaking(tmp_path, monkeypatch):
    create_project(str(tmp_path), "P")
    at = _open_project(tmp_path, monkeypatch)
    assert not at.exception
    assert any("Run ingestion" in i.value for i in at.info)
