import os

from procurement.classify import CLASSIFY_PROMPT_VERSION
from procurement.pipeline import (RFQ_PROMPT_VERSION_BY_CLASS, run_ingestion,
                                  inventory_rfq_documents)
from procurement.project import create_project
from procurement.store import events, snapshots


class RfqClient:
    """Answers according to the schema it is handed, and records every call."""
    supports_vision = True

    def __init__(self, fail_on=(), h2s=50):
        self.calls, self._fail_on, self._h2s = [], set(fail_on), h2s
        # the `auto` parameter the spec is read as stating; overridden by the
        # vocabulary suite to make a requirement edit change the vocabulary
        self.requirement_parameter = "h2s_tolerance"

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        fields = output_schema.model_fields
        kind = ("requirements" if "requirements" in fields else
                "amendments" if "amendments" in fields else
                "doc_class" if "doc_class" in fields else
                "facts" if "facts" in fields else
                "deviations" if "deviations" in fields else "bid")
        self.calls.append(kind)
        if kind in self._fail_on:
            raise RuntimeError(f"{kind} provider unavailable")
        if kind == "requirements":
            return {"requirements": [
                {"clause_ref": "4.2.7", "text": "H2S at least 50 ppm",
                 "category": "technical", "checkability": "auto",
                 "parameter": self.requirement_parameter, "operator": ">=",
                 "value": self._h2s, "unit": "ppm"},
                {"clause_ref": "9.1", "text": "Submit an O&M manual",
                 "category": "documentation", "checkability": "judgement"},
            ]}
        if kind == "amendments":
            return {"amendments": [
                {"clause_ref": "4.2.7", "text": "H2S raised to 60 ppm",
                 "action": "modify", "parameter": "h2s_tolerance",
                 "operator": ">=", "value": 60, "unit": "ppm"}]}
        if kind == "doc_class":
            return {"doc_class": "other"}
        if kind == "facts":
            return {"facts": [{"parameter": "h2s_tolerance", "value": 70,
                               "unit": "ppm", "verbatim": "H2S up to 70 ppm"}]}
        if kind == "deviations":
            return {"deviations": []}
        return {"currency": "USD", "base_price": 1000.0}


def _rfq(tmp_path, files):
    root = str(tmp_path)
    create_project(root, "P")
    rdir = tmp_path / "p" / "requirements"
    rdir.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (rdir / name).write_text(body, encoding="utf-8")
    return root


_MR = "ADN-AEC-ME-SPC-026 MR Gas Genset.txt"
_MOM = "00 MOM 20241111 ASTRA.txt"


def test_rfq_documents_are_inventoried_with_no_vendor(tmp_path):
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm"})
    [doc] = inventory_rfq_documents(root, "p")
    assert doc.vendor is None
    assert doc.path == f"requirements/{_MR}"
    assert doc.content_sha256


def test_a_spec_document_produces_stored_requirements(tmp_path):
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm"})
    run_ingestion(root, "p", RfqClient())
    reqset = snapshots.load_requirements(root, "p")
    assert [r.clause_ref for r in reqset.requirements] == ["4.2.7", "9.1"]
    assert reqset.requirements[0].checkability == "auto"
    doc = next(d for d in snapshots.load_documents(root, "p") if d.vendor is None)
    assert (doc.doc_class, doc.extraction_status) == ("spec", "ok")
    assert doc.prompt_version == "requirements_v3"


def test_a_mom_amends_the_requirement_and_keeps_the_base(tmp_path):
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm",
                           _MOM: "Clause 4.2.7 revised to 60 ppm"})
    run_ingestion(root, "p", RfqClient())
    reqset = snapshots.load_requirements(root, "p")
    amended = next(r for r in reqset.requirements if r.clause_ref == "4.2.7")
    assert amended.value == 60
    assert amended.base_body["value"] == 50
    assert amended.amended_by == reqset.amendments[0].amendment_id
    assert reqset.amendments[0].req_id == amended.req_id


def test_an_rfq_datasheet_is_routed_to_requirements_with_an_event(tmp_path):
    # judgment call 1: the client's blank datasheet is the RFQ's most
    # parameter-dense document; routing it by class would drop it silently.
    root = _rfq(tmp_path, {"DOD-30201 DataSheet Gas Generator.txt": "H2S 50 ppm"})
    run_ingestion(root, "p", RfqClient())
    doc = next(d for d in snapshots.load_documents(root, "p") if d.vendor is None)
    assert doc.doc_class == "datasheet"          # the classifier's answer stands
    assert doc.extraction_status == "ok"
    assert doc.prompt_version == "requirements_v3"
    assert snapshots.load_requirements(root, "p").requirements
    actions = [e.action for e in events.read_events(root, "p")]
    assert "rfq.requirements_inferred" in actions


def test_a_drawing_in_the_rfq_folder_is_skipped_not_extracted(tmp_path):
    root = _rfq(tmp_path, {"15 LAYOUT - KGW550GF-T.txt": "a drawing"})
    run_ingestion(root, "p", RfqClient())
    doc = next(d for d in snapshots.load_documents(root, "p") if d.vendor is None)
    assert doc.extraction_status == "skipped" and "drawing" in doc.notes
    assert snapshots.load_requirements(root, "p").requirements == []


def test_a_superseded_spec_is_never_extracted(tmp_path):
    root = _rfq(tmp_path, {
        "ADN-AEC MR Gas Genset Superseded with MOM 20241111.txt": "old",
        "ADN-AEC MR Gas Genset copy.txt": "current"})
    run_ingestion(root, "p", RfqClient())
    docs = {os.path.basename(d.path): d for d in snapshots.load_documents(root, "p")}
    old = docs["ADN-AEC MR Gas Genset Superseded with MOM 20241111.txt"]
    new = docs["ADN-AEC MR Gas Genset copy.txt"]
    assert old.superseded_by == new.doc_id
    assert old.extraction_status == "skipped" and "superseded" in old.notes
    assert new.extraction_status == "ok"
    reqs = snapshots.load_requirements(root, "p").requirements
    assert {r.source_doc_id for r in reqs} == {new.doc_id}


def test_a_mom_clause_matching_two_spec_documents_is_stored_unapplied(tmp_path):
    # INV-3, over a loaded snapshot: the real RFQ has several live spec
    # documents, and neither side names a target document
    root = _rfq(tmp_path, {
        _MR: "4.2.7 H2S at least 50 ppm",
        "ADN-AEC-ME-SPC-027 MR Gas Genset B.txt": "4.2.7 H2S at least 50 ppm",
        _MOM: "Clause 4.2.7 revised to 60 ppm"})
    run_ingestion(root, "p", RfqClient())
    reqset = snapshots.load_requirements(root, "p")

    [amendment] = reqset.amendments
    assert amendment.req_id is None
    assert "4.2.7" in amendment.unresolved_reason
    assert all(r.amended_by is None for r in reqset.requirements)
    assert all(r.value == 50 for r in reqset.requirements
               if r.checkability == "auto")


def test_rerunning_an_unchanged_rfq_makes_zero_llm_calls(tmp_path):
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm",
                           _MOM: "Clause 4.2.7 revised to 60 ppm"})
    run_ingestion(root, "p", RfqClient())
    second = RfqClient()
    run_ingestion(root, "p", second)
    assert second.calls == []


def test_rerunning_preserves_the_amendment_resolution_exactly(tmp_path):
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm",
                           _MOM: "Clause 4.2.7 revised to 60 ppm"})
    run_ingestion(root, "p", RfqClient())
    before = snapshots.load_requirements(root, "p").model_dump()
    run_ingestion(root, "p", RfqClient())
    assert snapshots.load_requirements(root, "p").model_dump() == before


def test_deleting_the_mom_reverts_the_requirement_and_drops_the_amendment(tmp_path):
    # INV-5
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm",
                           _MOM: "Clause 4.2.7 revised to 60 ppm"})
    run_ingestion(root, "p", RfqClient())
    os.remove(os.path.join(root, "p", "requirements", _MOM))
    run_ingestion(root, "p", RfqClient())
    reqset = snapshots.load_requirements(root, "p")
    assert reqset.amendments == []
    reverted = next(r for r in reqset.requirements if r.clause_ref == "4.2.7")
    assert reverted.value == 50
    assert reverted.amended_by is None and reverted.base_body is None


def test_deleting_the_spec_prunes_its_requirements(tmp_path):
    # INV-4
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm"})
    run_ingestion(root, "p", RfqClient())
    assert snapshots.load_requirements(root, "p").requirements
    os.remove(os.path.join(root, "p", "requirements", _MR))
    run_ingestion(root, "p", RfqClient())
    assert snapshots.load_requirements(root, "p").requirements == []
    assert "requirements.pruned" in [e.action for e in events.read_events(root, "p")]


def test_a_failed_spec_extraction_keeps_the_previous_requirements(tmp_path):
    # INV-4 under the I3 shape: a failed overwrite must not blank good data
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm"})
    run_ingestion(root, "p", RfqClient())
    before = snapshots.load_requirements(root, "p").model_dump()
    (tmp_path / "p" / "requirements" / _MR).write_text("edited", encoding="utf-8")
    run_ingestion(root, "p", RfqClient(fail_on=("requirements",)))
    assert snapshots.load_requirements(root, "p").model_dump() == before
    doc = next(d for d in snapshots.load_documents(root, "p") if d.vendor is None)
    assert doc.extraction_status == "failed"
    assert "provider unavailable" in doc.notes


def test_rfq_prompt_versions_are_per_class():
    assert RFQ_PROMPT_VERSION_BY_CLASS == {"spec": "requirements_v3",
                                           "mom": "mom_amend_v1"}


def test_the_rfq_pass_does_not_disturb_vendor_facts(tmp_path):
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm"})
    vdir = tmp_path / "p" / "vendors" / "KERUI"
    vdir.mkdir(parents=True)
    (vdir / "Quotation.txt").write_text("base price 1000", encoding="utf-8")
    from procurement.project import load_project, save_project
    project = load_project(root, "p")
    project.vendors = ["KERUI"]
    save_project(root, project)

    run_ingestion(root, "p", RfqClient())
    facts = snapshots.load_facts(root, "p", "KERUI")
    assert facts.commercial["base_price"] == 1000.0
    assert snapshots.load_facts(root, "p", "_rfq") is None
    assert snapshots.list_fact_vendors(root, "p") == ["KERUI"]


def test_a_reclassified_spec_loses_its_requirements(tmp_path):
    # INV-4: reclassification is the third orphaning route, alongside
    # supersession and deletion
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm"})
    run_ingestion(root, "p", RfqClient())
    os.rename(os.path.join(root, "p", "requirements", _MR),
              os.path.join(root, "p", "requirements", "15 LAYOUT drawing.txt"))
    run_ingestion(root, "p", RfqClient())
    assert snapshots.load_requirements(root, "p").requirements == []


def test_classification_is_cached_across_runs_for_rfq_documents(tmp_path):
    root = _rfq(tmp_path, {_MR: "4.2.7 H2S at least 50 ppm"})
    run_ingestion(root, "p", RfqClient())
    doc = next(d for d in snapshots.load_documents(root, "p") if d.vendor is None)
    assert doc.classified_by == "rule"
    assert doc.classified_with == CLASSIFY_PROMPT_VERSION
