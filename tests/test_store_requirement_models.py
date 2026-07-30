import json
import os

from procurement.project import create_project
from procurement.store import layout, snapshots
from procurement.store.models import (Amendment, ComplianceResult, Override,
                                      RequirementRecord, RequirementSet,
                                      amendment_id_for, req_id_for)
from procurement.store.overrides import get_by_path, reconcile


def _req(doc_id="d1", clause="4.2.7", **kw):
    body = dict(clause_ref=clause, text="H2S tolerance shall be at least 50 ppm",
                category="technical", checkability="auto", parameter="h2s_tolerance",
                operator=">=", value=50.0, unit="ppm", source_doc_id=doc_id)
    body.update(kw)
    return RequirementRecord(req_id=req_id_for(doc_id, clause), **body)


def test_req_id_is_stable_for_the_same_document_and_clause():
    assert req_id_for("d1", "4.2.7") == req_id_for("d1", "4.2.7")
    # whitespace and case in a printed clause ref must not shift the id
    assert req_id_for("d1", " 4.2.7 ") == req_id_for("d1", "4.2.7")


def test_req_id_separates_clauses_and_documents():
    assert req_id_for("d1", "4.2.7") != req_id_for("d1", "4.2.8")
    assert req_id_for("d1", "4.2.7") != req_id_for("d2", "4.2.7")


def test_amendment_id_uses_document_clause_and_text():
    a = amendment_id_for("m1", "4.2.7", "raised to 60 ppm")
    assert a == amendment_id_for("m1", "4.2.7", "raised to 60 ppm")
    assert a != amendment_id_for("m1", "4.2.7", "lowered to 40 ppm")
    assert a != amendment_id_for("m1", "4.2.8", "raised to 60 ppm")
    assert a != amendment_id_for("m2", "4.2.7", "raised to 60 ppm")
    assert a.startswith("a-")


def test_requirements_round_trip_through_the_snapshot(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    reqset = RequirementSet(
        requirements=[_req()],
        amendments=[Amendment(
            amendment_id=amendment_id_for("m1", "4.2.7", "raised to 60 ppm"),
            clause_ref="4.2.7", req_id=None, text="raised to 60 ppm",
            parameter="h2s_tolerance", operator=">=", value=60.0, unit="ppm",
            action="modify", source_doc_id="m1")],
        overrides=[])
    snapshots.save_requirements(root, "p", reqset)
    loaded = snapshots.load_requirements(root, "p")
    assert loaded.requirements[0].req_id == reqset.requirements[0].req_id
    assert loaded.requirements[0].value == 50.0
    assert loaded.amendments[0].action == "modify"
    assert loaded.requirements[0].base_body is None


def test_missing_requirements_snapshot_loads_as_an_empty_set(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    loaded = snapshots.load_requirements(root, "p")
    assert loaded.requirements == [] and loaded.amendments == []


def test_requirements_snapshot_is_written_atomically(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    snapshots.save_requirements(root, "p", RequirementSet(requirements=[_req()]))
    path = layout.requirements_path(root, "p")
    assert os.path.exists(path) and not os.path.exists(path + ".tmp")
    with open(path, encoding="utf-8") as fh:
        assert "requirements" in json.load(fh)


def test_compliance_round_trips(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    snapshots.save_compliance(root, "p", [ComplianceResult(
        req_id="r-abc", vendor="KERUI", verdict="pass", fact_id="f-123",
        doc_id="d9", rationale="50 ppm >= 50 ppm", evaluated_at="2026-07-30T00:00:00Z")])
    loaded = snapshots.load_compliance(root, "p")
    assert loaded[0].verdict == "pass" and loaded[0].fact_id == "f-123"
    assert snapshots.load_compliance(str(tmp_path / "nowhere"), "p") == []


def test_an_override_addresses_a_requirement_by_req_id():
    req = _req()
    view = {"requirements": [req.model_dump()]}
    assert get_by_path(view, f"requirements[{req.req_id}].value") == 50.0


def test_a_deviation_resolves_by_deviation_id_not_by_doc_id():
    # phase-2 latent defect: _id_of did not list deviation_id, so a dump fell
    # through to doc_id and two deviations from one document were
    # indistinguishable.
    view = {"deviations": [
        {"deviation_id": "v-aaa", "clause_ref": "4.1", "statement": "one",
         "disposition": "deviate", "doc_id": "d9"},
        {"deviation_id": "v-bbb", "clause_ref": "4.2", "statement": "two",
         "disposition": "comply", "doc_id": "d9"},
    ]}
    assert get_by_path(view, "deviations[v-bbb].statement") == "two"


def test_an_amendment_resolves_by_amendment_id_even_though_it_has_a_req_id():
    view = {"amendments": [
        {"amendment_id": "a-aaa", "req_id": "r-1", "text": "one", "source_doc_id": "m1"},
        {"amendment_id": "a-bbb", "req_id": "r-1", "text": "two", "source_doc_id": "m1"},
    ]}
    assert get_by_path(view, "amendments[a-bbb].text") == "two"


def test_an_override_on_a_vanished_requirement_flags_a_conflict():
    req = _req()
    o = Override(field_path=f"requirements[{req.req_id}].value", value=55.0,
                 extracted_value=50.0, author="rj", at="2026-07-30T00:00:00Z",
                 reason="clarified at the meeting")
    [updated] = reconcile([o], {"requirements": []})
    assert updated.conflict is True and updated.value == 55.0
