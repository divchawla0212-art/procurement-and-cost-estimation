"""Phase 3 integration: compliance inside the run, and the two-run mutation
matrix required by docs/superpowers/PLAN-TEMPLATE.md.

Every matrix test asserts over a **loaded snapshot** after run 2 (or run 3
where the row says so). A row that can be satisfied by a single-run assertion
is not testing what it claims — that is the phase-2 defect class this file
exists to close.
"""
import os

import pytest

from procurement import pipeline
from procurement.pipeline import run_ingestion
from procurement.project import load_project, save_project
from procurement.store import events, snapshots
from procurement.store.models import Override

from tests.test_pipeline_vocabulary import VocabClient, _project, _PAD

_MR = "ADN-AEC-ME-SPC-026 MR Gas Genset.txt"
_MOM = "00 MOM 20241111 ASTRA.txt"
_DATASHEET = "01 DataSheet Gas Generator.txt"
_DEVIATION = "03 Attachment-2 Vendor Deviation Form.txt"
_NOW = "2026-07-30T00:00:00+00:00"


class MatrixClient(VocabClient):
    """VocabClient with the knobs each matrix row needs.

    Each response is post-processed rather than re-dispatched, so the call
    recording and failure injection of the base stub stay in one place.
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.requirement_unit = "ppm"
        self.requirement_operator = None       # None -> the stub's own operator
        self.requirements_response = None      # None -> the default two clauses
        self.amendment_clause = "4.2.7"
        self.fact_value = 70
        self.fact_unit = "ppm"

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        fields = output_schema.model_fields
        response = super().classify_structure(prompt, output_schema,
                                              context_text, images)
        if "requirements" in fields:
            if self.requirements_response is not None:
                return self.requirements_response
            for entry in response["requirements"]:
                if entry.get("checkability") == "auto":
                    entry["unit"] = self.requirement_unit
                    if self.requirement_operator is not None:
                        entry["operator"] = self.requirement_operator
        elif "amendments" in fields:
            for entry in response["amendments"]:
                entry["clause_ref"] = self.amendment_clause
        elif "facts" in fields:
            # the vendor answers the parameter the requirement asks about,
            # unless a row deliberately makes it unanswerable
            for entry in response["facts"]:
                entry["parameter"] = self.requirement_parameter
                entry["value"] = self.fact_value
                entry["unit"] = self.fact_unit
        return response


def _write_rfq(tmp_path, name, body="4.2.7 H2S at least 50 ppm"):
    # Padded: every document this helper writes is meant to reach an
    # extractor, and a body shorter than MIN_EXTRACTABLE_CHARS would instead
    # exercise the no-readable-text guard, not the extraction logic these
    # tests are about.
    (tmp_path / "p" / "requirements" / name).write_text(body + _PAD, encoding="utf-8")


def _write_vendor(tmp_path, vendor, name, body):
    vdir = tmp_path / "p" / "vendors" / vendor
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / name).write_text(body + _PAD, encoding="utf-8")


def _set_vendors(root, vendors):
    project = load_project(root, "p")
    project.vendors = list(vendors)
    save_project(root, project)


def _doc(root, suffix):
    return next(d for d in snapshots.load_documents(root, "p")
                if d.path.endswith(suffix))


def _reqs(root):
    return snapshots.load_requirements(root, "p")


def _last_run_started(root):
    return [e for e in events.read_events(root, "p")
            if e.action == "run.started"][-1].at


def _full_project(tmp_path):
    """MR + MOM on the RFQ side; quotation + datasheet + deviation form for
    KERUI. One document per routed class, so a per-class cache bump is
    observable as "only that class re-extracted"."""
    root = _project(tmp_path)
    _write_rfq(tmp_path, _MOM, "Clause 4.2.7 revised to 60 ppm")
    _write_vendor(tmp_path, "KERUI", _DEVIATION, "4.2.7 we differ")
    return root


# --- wiring -----------------------------------------------------------------

def test_a_run_writes_the_compliance_matrix(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", VocabClient())
    results = snapshots.load_compliance(root, "p")
    assert results and {r.vendor for r in results} == {"KERUI"}
    assert results[0].verdict == "pass"        # 70 ppm >= 50 ppm
    assert "compliance.evaluated" in [e.action for e in events.read_events(root, "p")]


def test_every_verdict_is_at_or_after_the_run_that_produced_it(tmp_path):
    # INV-9
    root = _project(tmp_path)
    run_ingestion(root, "p", VocabClient())
    (tmp_path / "p" / "vendors" / "KERUI" / _DATASHEET).write_text(
        "H2S up to 40 ppm" + _PAD, encoding="utf-8")
    run_ingestion(root, "p", VocabClient())
    last_start = _last_run_started(root)
    assert all(r.evaluated_at >= last_start
               for r in snapshots.load_compliance(root, "p"))


def test_one_run_bumps_the_generation_exactly_once(tmp_path):
    root = _project(tmp_path)
    before = snapshots.get_generation(root, "p")
    run_ingestion(root, "p", VocabClient())
    assert snapshots.get_generation(root, "p") == before + 1


def test_load_dataset_keeps_its_existing_shape(tmp_path):
    from procurement.pipeline import load_dataset
    root = _project(tmp_path)
    run_ingestion(root, "p", VocabClient())
    assert set(load_dataset(root, "p")) == {"bids", "normalized", "comparison"}


# --- the two-run mutation matrix -------------------------------------------

def test_row1_a_newer_spec_revision_orphans_the_old_ones_requirements(tmp_path):
    # INV-4
    root = _project(tmp_path)
    run_ingestion(root, "p", MatrixClient())
    _write_rfq(tmp_path, "ADN-AEC-ME-SPC-026 MR Gas Genset(Rev1).txt")
    run_ingestion(root, "p", MatrixClient())

    old, new = _doc(root, _MR), _doc(root, "(Rev1).txt")
    assert old.superseded_by == new.doc_id
    assert old.extraction_status == "skipped" and "superseded by" in old.notes
    sources = {r.source_doc_id for r in _reqs(root).requirements}
    assert sources == {new.doc_id} and old.doc_id not in sources


def test_row2_deleting_the_mom_drops_its_amendments_and_reverts(tmp_path):
    # INV-5
    root = _full_project(tmp_path)
    run_ingestion(root, "p", MatrixClient())
    assert next(r for r in _reqs(root).requirements
                if r.clause_ref == "4.2.7").value == 60

    os.remove(os.path.join(root, "p", "requirements", _MOM))
    run_ingestion(root, "p", MatrixClient())

    reqset = _reqs(root)
    assert reqset.amendments == []
    reverted = next(r for r in reqset.requirements if r.clause_ref == "4.2.7")
    assert (reverted.value, reverted.amended_by, reverted.base_body) == (50, None, None)


def test_row3_a_sibling_revision_arriving_later_takes_over(tmp_path):
    # INV-4
    root = _project(tmp_path)
    os.remove(os.path.join(root, "p", "requirements", _MR))
    _write_rfq(tmp_path, "ADN-AEC-ME-SPC-026 MR Gas Genset(Rev1).txt")
    run_ingestion(root, "p", MatrixClient())

    _write_rfq(tmp_path, _MR)               # the base arrives in a second upload
    run_ingestion(root, "p", MatrixClient())

    old, new = _doc(root, _MR), _doc(root, "(Rev1).txt")
    assert new.supersedes == old.doc_id     # the immediate predecessor, by name
    assert {r.source_doc_id for r in _reqs(root).requirements} == {new.doc_id}


# Bumping the dict entry is equivalent to bumping the constant it was built
# from: pipeline builds both tables at import time, so a source-level bump and
# this patch are the same input to the cache gate.
_BUMPS = [
    # the bumped value must differ from the shipped one, or the patch is a no-op
    ("REQUIREMENTS_PROMPT_VERSION", "requirements", _MR, "requirements_v5"),
    ("MOM_PROMPT_VERSION", "amendments", _MOM, "mom_amend_v2"),
    ("TECH_PROMPT_VERSION", "facts", _DATASHEET, "tech_facts_v2"),
    ("DEVIATION_PROMPT_VERSION", "deviations", _DEVIATION, "deviation_v2"),
    ("CLASSIFY_PROMPT_VERSION", None, None, "doc_class_v2"),
]


@pytest.mark.parametrize("constant,expected_call,document,bumped", _BUMPS)
def test_row4_a_prompt_bump_reextracts_only_its_own_class(
        tmp_path, monkeypatch, constant, expected_call, document, bumped):
    # INV-4, INV-8
    root = _full_project(tmp_path)
    run_ingestion(root, "p", MatrixClient())

    if constant == "CLASSIFY_PROMPT_VERSION":
        monkeypatch.setattr(pipeline, "CLASSIFY_PROMPT_VERSION", bumped)
    elif constant == "REQUIREMENTS_PROMPT_VERSION":
        monkeypatch.setitem(pipeline.RFQ_PROMPT_VERSION_BY_CLASS, "spec", bumped)
    elif constant == "MOM_PROMPT_VERSION":
        monkeypatch.setitem(pipeline.RFQ_PROMPT_VERSION_BY_CLASS, "mom", bumped)
    elif constant == "TECH_PROMPT_VERSION":
        monkeypatch.setitem(pipeline.PROMPT_VERSION_BY_CLASS, "datasheet", bumped)
    else:
        monkeypatch.setitem(pipeline.PROMPT_VERSION_BY_CLASS, "deviation", bumped)

    second = MatrixClient()
    run_ingestion(root, "p", second)
    assert second.calls == ([] if expected_call is None else [expected_call])
    if document is None:
        assert all(d.classified_with == bumped
                   for d in snapshots.load_documents(root, "p"))
    else:
        assert _doc(root, document).prompt_version == bumped

    third = MatrixClient()
    run_ingestion(root, "p", third)
    assert third.calls == [], "the bumped version did not persist"


def test_row5_a_failed_requirements_call_is_retried_and_then_clears_its_note(tmp_path):
    # INV-4
    root = _project(tmp_path)
    run_ingestion(root, "p", MatrixClient(fail_on=("requirements",)))
    assert _reqs(root).requirements == []
    assert _doc(root, _MR).extraction_status == "failed"

    run_ingestion(root, "p", MatrixClient())
    assert [r.clause_ref for r in _reqs(root).requirements] == ["4.2.7", "9.1"]
    doc = _doc(root, _MR)
    assert (doc.extraction_status, doc.notes) == ("ok", None)


def test_row6_a_permanently_failing_spec_keeps_its_reason_every_run(tmp_path):
    # INV-4
    root = _project(tmp_path)
    run_ingestion(root, "p", MatrixClient(fail_on=("requirements",)))
    first_doc, first_reqs = _doc(root, _MR), _reqs(root).model_dump()
    assert "provider unavailable" in first_doc.notes

    run_ingestion(root, "p", MatrixClient(fail_on=("requirements",)))
    doc = _doc(root, _MR)
    assert doc.extraction_status == "failed"
    assert "provider unavailable" in doc.notes
    assert _reqs(root).model_dump() == first_reqs


def test_row7_an_rfq_datasheet_is_still_routed_to_requirements(tmp_path):
    # INV-4: the entity must not silently vanish because of its class
    root = _project(tmp_path)
    os.remove(os.path.join(root, "p", "requirements", _MR))
    _write_rfq(tmp_path, "DOD-30201 DataSheet Gas Generator.txt", "H2S 50 ppm")
    run_ingestion(root, "p", MatrixClient())
    run_ingestion(root, "p", MatrixClient())

    doc = _doc(root, "DOD-30201 DataSheet Gas Generator.txt")
    assert doc.doc_class == "datasheet"          # the classifier's answer stands
    assert doc.prompt_version == "requirements_v4"
    assert _reqs(root).requirements
    assert "rfq.requirements_inferred" in [e.action for e in
                                           events.read_events(root, "p")]


def test_row8_an_edited_spec_whose_reextraction_fails_keeps_its_requirements(tmp_path):
    # INV-4, the I3 shape
    root = _project(tmp_path)
    run_ingestion(root, "p", MatrixClient())
    before = _reqs(root).model_dump()

    _write_rfq(tmp_path, _MR, "4.2.7 edited")
    run_ingestion(root, "p", MatrixClient(fail_on=("requirements",)))
    assert _reqs(root).model_dump() == before
    assert _doc(root, _MR).extraction_status == "failed"


def test_row9_an_empty_response_is_ok_with_zero_requirements_not_failed(tmp_path):
    # INV-4
    root = _project(tmp_path)
    run_ingestion(root, "p", MatrixClient())

    _write_rfq(tmp_path, _MR, "4.2.7 edited")
    second = MatrixClient()
    second.requirements_response = {}            # no "requirements" key at all
    run_ingestion(root, "p", second)
    doc = _doc(root, _MR)
    assert (doc.extraction_status, doc.notes) == ("ok", None)
    assert _reqs(root).requirements == []

    third = MatrixClient()
    run_ingestion(root, "p", third)
    assert third.calls.count("requirements") == 0, "an empty extraction was retried"


def test_row10_marking_the_spec_superseded_prunes_it_and_says_so(tmp_path):
    # INV-4
    root = _project(tmp_path)
    run_ingestion(root, "p", MatrixClient())
    stored = len(_reqs(root).requirements)
    assert stored

    os.rename(os.path.join(root, "p", "requirements", _MR),
              os.path.join(root, "p", "requirements",
                           "ADN-AEC MR Gas Genset Superseded with MOM 20241111.txt"))
    run_ingestion(root, "p", MatrixClient())

    assert _reqs(root).requirements == []
    pruned = [e for e in events.read_events(root, "p")
              if e.action == "requirements.pruned"]
    assert pruned and pruned[-1].detail["requirements_dropped"] == stored


def test_row11_withdrawing_the_mom_restores_the_clause_text_exactly(tmp_path):
    # INV-5
    root = _full_project(tmp_path)
    run_ingestion(root, "p", MatrixClient())
    amended = next(r for r in _reqs(root).requirements if r.clause_ref == "4.2.7")
    original = dict(amended.base_body)
    assert "[amended]" in amended.text

    os.remove(os.path.join(root, "p", "requirements", _MOM))
    run_ingestion(root, "p", MatrixClient())

    reverted = next(r for r in _reqs(root).requirements if r.clause_ref == "4.2.7")
    assert {k: getattr(reverted, k) for k in original} == original
    assert "[amended]" not in reverted.text


def test_row12_an_override_on_a_vanished_requirement_conflicts_and_stays_put(tmp_path):
    # INV-1
    root = _project(tmp_path)
    _write_rfq(tmp_path, "ADN-AEC-ME-SPC-027 MR Gas Genset B.txt")
    run_ingestion(root, "p", MatrixClient())

    reqset = _reqs(root)
    doomed = next(r for r in reqset.requirements
                  if r.source_doc_id == _doc(root, _MR).doc_id
                  and r.checkability == "auto")
    reqset.overrides = [Override(field_path=f"requirements[{doomed.req_id}].value",
                                 value=55.0, extracted_value=doomed.value,
                                 author="rj", at=_NOW, reason="clarified at the meeting")]
    snapshots.save_requirements(root, "p", reqset)

    os.remove(os.path.join(root, "p", "requirements", _MR))
    run_ingestion(root, "p", MatrixClient())

    after = _reqs(root)
    [override] = after.overrides
    assert override.conflict is True and override.value == 55.0
    assert doomed.req_id not in {r.req_id for r in after.requirements}
    # and it never retargeted a surviving requirement
    assert after.requirements and all(r.value != 55.0 for r in after.requirements)


def test_row13_changing_a_parameter_reextracts_every_datasheet_exactly_once(tmp_path):
    # INV-8
    root = _project(tmp_path)
    _write_vendor(tmp_path, "KERUI", "02 DataSheet Alternator.txt", "H2S up to 80 ppm")
    run_ingestion(root, "p", MatrixClient())

    _write_rfq(tmp_path, _MR, "4.2.7 continuous rating at least 500 kW")
    second = MatrixClient()
    second.requirement_parameter = "continuous_rating"
    run_ingestion(root, "p", second)
    assert second.calls.count("facts") == 2, "not every datasheet was re-asked"

    from procurement.compliance import vocabulary_sha
    expected = vocabulary_sha(["continuous_rating"])
    assert all(d.vocabulary_sha == expected
               for d in snapshots.load_documents(root, "p")
               if d.doc_class == "datasheet" and d.vendor)

    third = MatrixClient()
    third.requirement_parameter = "continuous_rating"
    run_ingestion(root, "p", third)
    assert third.calls.count("facts") == 0


def test_row14_deleting_the_cited_datasheet_makes_the_cell_unanswered(tmp_path):
    # INV-7
    root = _project(tmp_path)
    run_ingestion(root, "p", MatrixClient())
    cited = [r for r in snapshots.load_compliance(root, "p") if r.fact_id]
    assert cited and cited[0].verdict == "pass"

    os.remove(os.path.join(root, "p", "vendors", "KERUI", _DATASHEET))
    run_ingestion(root, "p", MatrixClient())

    stored = {f["fact_id"] for f in
              (snapshots.load_facts(root, "p", "KERUI").technical or [])}
    results = snapshots.load_compliance(root, "p")
    assert all(r.fact_id is None or r.fact_id in stored for r in results)
    auto = next(r for r in results if r.req_id == cited[0].req_id)
    assert auto.verdict == "unanswered"        # never fail


def test_row15_a_vendor_leaving_the_project_leaves_the_matrix(tmp_path):
    # INV-6
    root = _project(tmp_path)
    _write_vendor(tmp_path, "MKON", "Quotation.txt", "base price 900")
    _write_vendor(tmp_path, "MKON", _DATASHEET, "H2S up to 65 ppm")
    _set_vendors(root, ["KERUI", "MKON"])
    run_ingestion(root, "p", MatrixClient())
    before = {(r.req_id, r.verdict, r.fact_id)
              for r in snapshots.load_compliance(root, "p") if r.vendor == "KERUI"}

    _set_vendors(root, ["KERUI"])
    run_ingestion(root, "p", MatrixClient())

    results = snapshots.load_compliance(root, "p")
    assert {r.vendor for r in results} == {"KERUI"}
    assert {(r.req_id, r.verdict, r.fact_id) for r in results} == before
    live = [r for r in _reqs(root).requirements if not r.withdrawn]
    assert len(results) == len(live) * 1


def test_row16_an_unconvertible_restatement_is_unanswered_with_its_reason(tmp_path):
    # INV-7, defending units.py's refusal to guess a molar mass
    root = _project(tmp_path)
    client = MatrixClient()
    client.requirement_parameter = "trace_gas_limit"    # names no known substance
    run_ingestion(root, "p", client)

    (tmp_path / "p" / "vendors" / "KERUI" / _DATASHEET).write_text(
        "trace gas up to 70 mg/Nm3" + _PAD, encoding="utf-8")
    second = MatrixClient()
    second.requirement_parameter = "trace_gas_limit"
    second.fact_unit = "mg/Nm3"
    run_ingestion(root, "p", second)

    cell = next(r for r in snapshots.load_compliance(root, "p") if r.fact_id)
    assert cell.verdict == "unanswered"
    assert cell.verdict not in ("pass", "fail")
    assert "molar mass" in cell.rationale.lower()


def test_row17_changed_facts_are_reflected_in_verdicts_recomputed_this_run(tmp_path):
    # INV-9
    root = _project(tmp_path)
    run_ingestion(root, "p", MatrixClient())
    assert next(r for r in snapshots.load_compliance(root, "p")
                if r.fact_id).verdict == "pass"

    (tmp_path / "p" / "vendors" / "KERUI" / _DATASHEET).write_text(
        "H2S up to 40 ppm" + _PAD, encoding="utf-8")
    second = MatrixClient()
    second.fact_value = 40
    run_ingestion(root, "p", second)

    last_start = _last_run_started(root)
    results = snapshots.load_compliance(root, "p")
    assert all(r.evaluated_at >= last_start for r in results)
    assert next(r for r in results if r.fact_id).verdict == "fail"


def test_row18_a_half_stated_bound_is_judgement_and_its_cell_is_review(tmp_path):
    # INV-2
    root = _project(tmp_path)
    run_ingestion(root, "p", MatrixClient())

    _write_rfq(tmp_path, _MR, "4.2.7 H2S at least 50")
    second = MatrixClient()
    second.requirements_response = {"requirements": [
        {"clause_ref": "4.2.7", "text": "H2S at least 50", "category": "technical",
         "checkability": "auto", "parameter": "h2s_tolerance", "operator": ">=",
         "value": 50, "unit": None}]}
    run_ingestion(root, "p", second)

    [requirement] = _reqs(root).requirements
    assert requirement.checkability == "judgement"
    [cell] = snapshots.load_compliance(root, "p")
    assert cell.verdict == "review"


def test_row19_an_amendment_matching_no_clause_is_stored_unapplied(tmp_path):
    # INV-3
    root = _full_project(tmp_path)
    run_ingestion(root, "p", MatrixClient())

    (tmp_path / "p" / "requirements" / _MOM).write_text(
        "Clause 99.9 revised" + _PAD, encoding="utf-8")
    second = MatrixClient()
    second.amendment_clause = "99.9"
    run_ingestion(root, "p", second)

    reqset = _reqs(root)
    [amendment] = reqset.amendments
    assert amendment.clause_ref == "99.9" and amendment.req_id is None
    assert all(r.amended_by is None for r in reqset.requirements)
    assert next(r for r in reqset.requirements if r.clause_ref == "4.2.7").value == 50


def test_row20_a_colliding_second_spec_unresolves_a_resolved_amendment(tmp_path):
    # INV-3: clause numbers are unique only within a document, so once a
    # second spec prints the same ref there is nothing left to bind by — and
    # the run that discovers this must undo the resolution it made earlier,
    # not leave one document silently amended.
    root = _full_project(tmp_path)
    run_ingestion(root, "p", MatrixClient())
    amended = next(r for r in _reqs(root).requirements if r.clause_ref == "4.2.7")
    assert amended.value == 60 and amended.base_body is not None

    _write_rfq(tmp_path, "ADN-AEC-ME-SPC-027 MR Gas Genset B.txt")
    run_ingestion(root, "p", MatrixClient())

    reqset = _reqs(root)
    [amendment] = reqset.amendments
    assert amendment.req_id is None
    documents = {r.source_doc_id for r in reqset.requirements
                 if r.clause_ref == "4.2.7"}
    assert len(documents) == 2
    assert all(d in amendment.unresolved_reason for d in documents)

    reverted = [r for r in reqset.requirements if r.clause_ref == "4.2.7"]
    assert all(r.value == 50 and r.amended_by is None and r.base_body is None
               for r in reverted)


# --- INV-10: `unanswered` means the vendor was silent, not that we could not
# read the unit. Three rows, each asserting over a loaded snapshot after run 2.

def _auto_cells(root):
    """The machine-checked cells. `review` is the judgement clause 9.1, which
    no unit change can move."""
    return [c for c in snapshots.load_compliance(root, "p") if c.verdict != "review"]


def test_row21_a_unit_this_build_can_now_read_stops_being_unanswered(tmp_path):
    # INV-10. The two sides must state *different* members of the family, or
    # Task 1's identical-unit shortcut answers without consulting the table at
    # all and this row passes with the family deleted.
    root = _project(tmp_path)
    first = MatrixClient()
    first.requirement_parameter = "warranty_period"
    first.requirement_unit, first.fact_unit = "furlongs", "months"
    run_ingestion(root, "p", first)

    [cell] = _auto_cells(root)
    assert cell.verdict == "unanswered"
    assert "unrecognised unit" in cell.rationale

    # both documents are edited, so run 2 re-extracts both sides
    _write_rfq(tmp_path, _MR, "4.2.7 warranty at least 50 months")
    (tmp_path / "p" / "vendors" / "KERUI" / _DATASHEET).write_text(
        "warranty 70 years" + _PAD, encoding="utf-8")
    second = MatrixClient()
    second.requirement_parameter = "warranty_period"
    second.requirement_unit, second.fact_unit = "months", "years"
    run_ingestion(root, "p", second)

    started = _last_run_started(root)
    cells = snapshots.load_compliance(root, "p")
    # 70 years = 840 months >= 50 months, which needs the conversion
    assert [c.verdict for c in _auto_cells(root)] == ["pass"]
    assert all(c.evaluated_at >= started for c in cells)
    assert not any("unrecognised unit" in c.rationale for c in cells)


def test_row22_a_reextraction_under_a_bumped_prompt_replaces_the_old_operator(
        tmp_path, monkeypatch):
    # INV-7, INV-10 — the live requirements_v2 -> v3 story: a stated limit was
    # read as `==` and manufactured a FAIL against a vendor that complies.
    root = _project(tmp_path)
    first = MatrixClient()
    first.requirement_operator, first.fact_value = "==", 40
    run_ingestion(root, "p", first)

    [cell] = _auto_cells(root)
    assert cell.verdict == "fail"          # 40 == 50 is false

    monkeypatch.setitem(pipeline.RFQ_PROMPT_VERSION_BY_CLASS, "spec",
                        "requirements_v5")
    second = MatrixClient()
    second.requirement_operator, second.fact_value = "<=", 40
    run_ingestion(root, "p", second)

    stored = {r.operator for r in _reqs(root).requirements
              if r.checkability == "auto"}
    assert stored == {"<="}, "the bumped prompt did not re-extract"
    assert [c.verdict for c in _auto_cells(root)] == ["pass"]
    # no verdict computed from the old operator outlives it
    assert all(c.evaluated_at >= _last_run_started(root)
               for c in snapshots.load_compliance(root, "p"))


def test_row23_a_vendor_restating_in_another_family_becomes_unanswered(tmp_path):
    # INV-10: refusing for a stated physical reason is correct; the cell must
    # not silently become `pass` or `fail`, and must not read as unrecognised
    root = _project(tmp_path)
    first = MatrixClient()
    first.requirement_unit, first.fact_unit = "barg", "barg"
    run_ingestion(root, "p", first)
    assert [c.verdict for c in _auto_cells(root)] == ["pass"]

    # editing the datasheet forces run 2 to re-extract the vendor's answer
    (tmp_path / "p" / "vendors" / "KERUI" / _DATASHEET).write_text(
        "H2S up to 70 bar" + _PAD, encoding="utf-8")
    second = MatrixClient()
    second.requirement_unit, second.fact_unit = "barg", "bar"
    run_ingestion(root, "p", second)

    [cell] = _auto_cells(root)
    assert cell.verdict == "unanswered"
    assert "different quantities" in cell.rationale
    assert "unrecognised unit" not in cell.rationale


def test_row25_a_vanished_fact_counts_as_silence_not_as_our_reach(tmp_path):
    # INV-7, and the compliance screen's silent/refused split. The two look
    # identical in `verdict` and mean opposite things: one is the vendor never
    # answering, the other is us failing to read an answer we hold. Only a
    # second run can tell them apart, because run 1 has nothing to lose.
    from procurement.matrix import build_matrix

    root = _project(tmp_path)
    run_ingestion(root, "p", MatrixClient())
    before = build_matrix(root, "p").coverage
    assert before.by_verdict.get("pass") == 1
    assert (before.unanswered_silent, before.unanswered_refused) == (0, 0)

    os.remove(os.path.join(root, "p", "vendors", "KERUI", _DATASHEET))
    run_ingestion(root, "p", MatrixClient())

    after = build_matrix(root, "p").coverage
    assert after.by_verdict.get("unanswered") == 1
    assert (after.unanswered_silent, after.unanswered_refused) == (1, 0)
