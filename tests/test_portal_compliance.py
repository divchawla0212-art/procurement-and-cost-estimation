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


def _seed_mixed(root):
    """One auto pass, one stated pass - the scenario the coverage percentages
    must handle without exceeding 100%, since `by_verdict` tallies both
    tiers together while `auto_cells` alone does not."""
    create_project(root, "P")
    project = load_project(root, "p")
    project.vendors = ["KERUI"]
    save_project(root, project)
    requirements = [
        RequirementRecord(req_id=req_id_for("d1", "1.1"), clause_ref="1.1",
                          text="noise limit 85 dBA", checkability="auto",
                          parameter="noise_limit", operator="<=", value=85.0,
                          unit="dBA", source_doc_id="d1"),
        RequirementRecord(req_id=req_id_for("d1", "2.6"), clause_ref="2.6",
                          text="Generator Insulation Temperature: Class F",
                          checkability="stated",
                          parameter="generator_insulation_class",
                          value="Class F", source_doc_id="d1"),
    ]
    cells = [
        ComplianceResult(req_id=req_id_for("d1", "1.1"), vendor="KERUI",
                         verdict="pass", fact_id="f-1", doc_id="d9",
                         rationale="82 dBA <= 85 dBA", evaluated_at=NOW),
        ComplianceResult(req_id=req_id_for("d1", "2.6"), vendor="KERUI",
                         verdict="pass", fact_id="f-2", doc_id="d9",
                         rationale="vendor states generator_insulation_class = "
                                   "'Class F / Class B rise'", evaluated_at=NOW),
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


def test_a_stated_pass_does_not_push_coverage_past_100_percent(tmp_path, monkeypatch):
    # by_verdict tallies auto and stated together; the denominator must too,
    # or a stated pass on top of an auto pass renders "Pass — 2 (200%)"
    _seed_mixed(str(tmp_path))
    at = _open_project(tmp_path, monkeypatch)
    assert not at.exception

    checked = next(m for m in at.metric if m.label == "Checked cells")
    assert checked.value == "2"
    passed = next(m for m in at.metric if m.label == "Pass")
    assert passed.value == "2  (100%)"


# --- the coverage note's arithmetic -----------------------------------------
#
# `stated_unanswered` is a remainder of three separately-tallied counts, so
# nothing in its type stops it going negative. Rendered on truthiness, a
# negative read as "**-3** unanswered on a `stated` requirement" - a measured-
# looking number that was never measured. web/src/pages/ComplianceMatrix.tsx
# guards with `> 0`; these pin the portal to the same rule. Driven through
# `_render_coverage` with a fake `st` rather than through a store, because
# `build_matrix` cannot currently produce the negative and a fixture that
# faked one into the store would be asserting against a lie.


class _FakeColumn:
    def metric(self, *args, **kwargs):
        pass


class _FakeStreamlit:
    def __init__(self):
        self.markdown_calls: list[str] = []
        self.captions: list[str] = []

    def markdown(self, text, **kwargs):
        self.markdown_calls.append(text)

    def caption(self, text, **kwargs):
        self.captions.append(text)

    def columns(self, count):
        return [_FakeColumn() for _ in range(count)]


def _render(monkeypatch, coverage):
    from portal.views import compliance as view
    fake = _FakeStreamlit()
    monkeypatch.setattr(view, "st", fake)
    view._render_coverage(coverage)
    return " ".join(fake.markdown_calls)


def test_a_negative_stated_remainder_is_not_rendered_as_a_count(monkeypatch):
    from procurement.matrix import Coverage
    body = _render(monkeypatch, Coverage(
        auto_cells=4, stated_cells=0, by_verdict={"unanswered": 1, "pass": 3},
        unanswered_silent=3, unanswered_refused=1))       # remainder: -3
    assert "-3" not in body
    assert "stated` requirement" not in body
    # the two counts that *were* measured are still reported
    assert "**3** unanswered because no vendor document stated" in body


def test_a_real_stated_remainder_is_still_reported(monkeypatch):
    from procurement.matrix import Coverage
    body = _render(monkeypatch, Coverage(
        auto_cells=4, stated_cells=2, by_verdict={"unanswered": 4, "pass": 2},
        unanswered_silent=1, unanswered_refused=1))        # remainder: 2
    assert "**2** unanswered on a `stated` requirement" in body
