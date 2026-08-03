"""Task 6 of docs/superpowers/plans/2026-08-03-fact-identity-and-evidence-selection.md:
the two-run mutation matrix for fact identity and evidence selection.

Every row here runs ingestion twice against the same project directory with
one thing mutated in between, and asserts over a **loaded snapshot** after
run 2 — a row satisfiable by a single-run assertion is not testing what it
claims, per docs/superpowers/PLAN-TEMPLATE.md Rule 2. This file writes only
the six rows specific to this plan's change to what a `fact_id` is and how a
parameter's facts are grouped into readings; the template's nine required
rows already exist in tests/test_extraction_coverage.py and are confirmed
green elsewhere, not duplicated here (see the plan's Task 6 controller
ruling).

Store invariant owned: across two runs, the requirement x vendor matrix has
a cell for every (live requirement, live vendor) pair and no cell for any
other pair; and no stored override or verdict references a `fact_id` absent
from the vendor's live facts.
"""
import copy
import logging

from procurement.pipeline import run_ingestion
from procurement.store import snapshots
from procurement.store.models import Override, fact_id_for

from tests.test_pipeline_rfq import RfqClient       # the schema-aware stub
from tests.test_pipeline_vocabulary import _PAD, _project

_DATASHEET = "01 DataSheet Gas Generator.txt"
_NOW = "2026-08-03T00:00:00+00:00"

_AUTO_REQ = {"requirements": [
    {"clause_ref": "4.2.7", "text": "generator rating at least 500 kW",
     "category": "technical", "checkability": "auto",
     "parameter": "generator_rating", "operator": ">=",
     "value": 500, "unit": "kw"},
]}

_STATED_REQ = {"requirements": [
    {"clause_ref": "2.6", "text": "Generator insulation class must be stated",
     "category": "technical", "checkability": "stated",
     "parameter": "generator_insulation_class", "value": None},
]}


class ReadingsClient(RfqClient):
    """RfqClient with full control over the exact `requirements`/`facts`
    arrays returned, so one response can carry more than one reading of a
    parameter — the shape none of the existing knob-based clients need,
    because every existing mutation-matrix row changes at most one fact's
    value, never the count of facts a single call returns.
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.requirements_response = None   # None -> RfqClient's own two clauses
        self.facts_response = None          # None -> RfqClient's own one fact

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        fields = output_schema.model_fields
        response = super().classify_structure(prompt, output_schema,
                                              context_text, images)
        if "requirements" in fields and self.requirements_response is not None:
            return copy.deepcopy(self.requirements_response)
        if "facts" in fields and self.facts_response is not None:
            return {"facts": copy.deepcopy(self.facts_response)}
        return response


def _doc(root, suffix):
    return next(d for d in snapshots.load_documents(root, "p")
                if d.path.endswith(suffix))


def _edit_datasheet(tmp_path, marker):
    # Busts the pipeline's content-hash cache so the datasheet is genuinely
    # re-extracted; the manufactured facts_response is what actually decides
    # what comes back, not this text.
    (tmp_path / "p" / "vendors" / "KERUI" / _DATASHEET).write_text(
        marker + _PAD, encoding="utf-8")


def test_row1_a_second_different_reading_appears_and_escalates_to_review(tmp_path):
    root = _project(tmp_path)
    first = ReadingsClient()
    first.requirements_response = _AUTO_REQ
    first.facts_response = [
        {"parameter": "generator_rating", "value": 700, "unit": "kw"}]
    run_ingestion(root, "p", first)

    doc_id = _doc(root, _DATASHEET).doc_id
    id_700 = fact_id_for(doc_id, "generator_rating", 700, "kw")
    [cell] = snapshots.load_compliance(root, "p")
    assert cell.verdict == "pass"
    assert cell.fact_id == id_700
    assert cell.candidate_fact_ids == []

    _edit_datasheet(tmp_path, "generator rating restated")
    second = ReadingsClient()
    second.requirements_response = _AUTO_REQ
    second.facts_response = [
        {"parameter": "generator_rating", "value": 700, "unit": "kw"},
        {"parameter": "generator_rating", "value": 525, "unit": "kw"}]
    run_ingestion(root, "p", second)

    id_525 = fact_id_for(doc_id, "generator_rating", 525, "kw")
    [cell] = snapshots.load_compliance(root, "p")
    assert cell.verdict == "review"
    assert cell.fact_id == id_700          # first-appearance order
    assert set(cell.candidate_fact_ids) == {id_700, id_525}


def test_row2_correcting_away_the_second_reading_returns_to_pass(tmp_path):
    root = _project(tmp_path)
    first = ReadingsClient()
    first.requirements_response = _AUTO_REQ
    first.facts_response = [
        {"parameter": "generator_rating", "value": 700, "unit": "kw"},
        {"parameter": "generator_rating", "value": 525, "unit": "kw"}]
    run_ingestion(root, "p", first)

    doc_id = _doc(root, _DATASHEET).doc_id
    id_700 = fact_id_for(doc_id, "generator_rating", 700, "kw")
    id_525 = fact_id_for(doc_id, "generator_rating", 525, "kw")
    [cell] = snapshots.load_compliance(root, "p")
    assert cell.verdict == "review"
    assert set(cell.candidate_fact_ids) == {id_700, id_525}

    _edit_datasheet(tmp_path, "the second reading was a mistake, corrected")
    second = ReadingsClient()
    second.requirements_response = _AUTO_REQ
    second.facts_response = [
        {"parameter": "generator_rating", "value": 700, "unit": "kw"}]
    run_ingestion(root, "p", second)

    results = snapshots.load_compliance(root, "p")
    [cell] = results
    assert cell.verdict == "pass"
    assert cell.fact_id == id_700
    assert cell.candidate_fact_ids == []
    # no id of the removed reading survives anywhere: not on any stored
    # verdict, and not in the vendor's stored facts
    stored_ids = {f["fact_id"] for f in
                  snapshots.load_facts(root, "p", "KERUI").technical}
    assert id_525 not in stored_ids
    assert all(id_525 != r.fact_id and id_525 not in r.candidate_fact_ids
               for r in results)


def test_row3_a_unit_restatement_is_not_a_disagreement(tmp_path):
    root = _project(tmp_path)
    first = ReadingsClient()
    first.requirements_response = _AUTO_REQ
    first.facts_response = [
        {"parameter": "generator_rating", "value": 700, "unit": "kw"}]
    run_ingestion(root, "p", first)

    doc_id = _doc(root, _DATASHEET).doc_id
    id_kw = fact_id_for(doc_id, "generator_rating", 700, "kw")

    _edit_datasheet(tmp_path, "generator rating restated in watts too")
    second = ReadingsClient()
    second.requirements_response = _AUTO_REQ
    second.facts_response = [
        {"parameter": "generator_rating", "value": 700, "unit": "kw"},
        {"parameter": "generator_rating", "value": 700000, "unit": "w"}]
    run_ingestion(root, "p", second)

    id_w = fact_id_for(doc_id, "generator_rating", 700000, "w")
    [cell] = snapshots.load_compliance(root, "p")
    assert cell.verdict == "pass"              # no escalation
    assert cell.fact_id == id_kw               # first-appearance order
    assert set(cell.candidate_fact_ids) == {id_kw, id_w}


def test_row4_an_override_addressed_at_one_reading_leaves_the_other_untouched(
        tmp_path):
    root = _project(tmp_path)
    first = ReadingsClient()
    first.requirements_response = _AUTO_REQ
    first.facts_response = [
        {"parameter": "generator_rating", "value": 700, "unit": "kw"},
        {"parameter": "generator_rating", "value": 525, "unit": "kw"}]
    run_ingestion(root, "p", first)

    doc_id = _doc(root, _DATASHEET).doc_id
    id_700 = fact_id_for(doc_id, "generator_rating", 700, "kw")
    id_525 = fact_id_for(doc_id, "generator_rating", 525, "kw")

    facts = snapshots.load_facts(root, "p", "KERUI")
    facts.overrides = [Override(
        field_path=f"technical[{id_700}].value", value=750,
        extracted_value=700, author="rj", at=_NOW,
        reason="confirmed 750 kW on the vendor call")]
    snapshots.save_facts(root, "p", facts)

    # Run 2: a document that is a pure cache hit is never re-passed through
    # reconcile/apply_overrides at all (its stored facts already reflect
    # whatever override applied on the run that last touched it), so the
    # datasheet's bytes are touched here to force a genuine re-extraction —
    # restating the same two readings — which is what actually exercises
    # apply_overrides against a fresh technical list.
    _edit_datasheet(tmp_path, "generator rating restated, unchanged in substance")
    second = ReadingsClient()
    second.requirements_response = _AUTO_REQ
    second.facts_response = [
        {"parameter": "generator_rating", "value": 700, "unit": "kw"},
        {"parameter": "generator_rating", "value": 525, "unit": "kw"}]
    run_ingestion(root, "p", second)

    after = snapshots.load_facts(root, "p", "KERUI")
    rec_700 = next(f for f in after.technical if f["fact_id"] == id_700)
    rec_525 = next(f for f in after.technical if f["fact_id"] == id_525)
    assert rec_700["value"] == 750             # the override applied
    assert rec_525["value"] == 525             # untouched
    [override] = after.overrides
    assert override.conflict is False


def test_row5_a_reworded_reading_orphans_its_override_visibly(tmp_path, caplog):
    root = _project(tmp_path)
    first = ReadingsClient()
    first.requirements_response = _AUTO_REQ
    first.facts_response = [
        {"parameter": "generator_rating", "value": 700, "unit": "kw"}]
    run_ingestion(root, "p", first)

    doc_id = _doc(root, _DATASHEET).doc_id
    id_700 = fact_id_for(doc_id, "generator_rating", 700, "kw")

    facts = snapshots.load_facts(root, "p", "KERUI")
    facts.overrides = [Override(
        field_path=f"technical[{id_700}].value", value=750,
        extracted_value=700, author="rj", at=_NOW,
        reason="confirmed higher rating on the vendor call")]
    snapshots.save_facts(root, "p", facts)

    _edit_datasheet(tmp_path, "generator rating reworded to 700.5 kW")
    second = ReadingsClient()
    second.requirements_response = _AUTO_REQ
    second.facts_response = [
        {"parameter": "generator_rating", "value": 700.5, "unit": "kw"}]
    caplog.set_level(logging.WARNING, logger="procurement.store.overrides")
    run_ingestion(root, "p", second)

    assert "could not be applied" in caplog.text

    after = snapshots.load_facts(root, "p", "KERUI")
    id_reworded = fact_id_for(doc_id, "generator_rating", 700.5, "kw")
    assert [f["fact_id"] for f in after.technical] == [id_reworded]
    assert after.technical[0]["value"] == 700.5    # never silently retargeted
    [override] = after.overrides
    assert override.conflict is True
    assert override.value == 750                   # the human's intent kept
    assert override.extracted_value is None         # the path no longer resolves


def test_row6_a_refusal_in_a_later_reading_escalates_a_presence_only_pass(
        tmp_path):
    root = _project(tmp_path)
    first = ReadingsClient()
    first.requirements_response = _STATED_REQ
    first.facts_response = [
        {"parameter": "generator_insulation_class", "value": "Class F",
         "unit": None}]
    run_ingestion(root, "p", first)

    [cell] = snapshots.load_compliance(root, "p")
    assert cell.verdict == "pass"

    _edit_datasheet(tmp_path, "insulation class restated, with a refusal too")
    second = ReadingsClient()
    second.requirements_response = _STATED_REQ
    second.facts_response = [
        {"parameter": "generator_insulation_class", "value": "Class F",
         "unit": None},
        {"parameter": "generator_insulation_class",
         "value": "Class F insulation not offered", "unit": None}]
    run_ingestion(root, "p", second)

    doc_id = _doc(root, _DATASHEET).doc_id
    id_class_f = fact_id_for(doc_id, "generator_insulation_class", "Class F", None)
    id_refusal = fact_id_for(doc_id, "generator_insulation_class",
                             "Class F insulation not offered", None)
    [cell] = snapshots.load_compliance(root, "p")
    assert cell.verdict == "review"
    assert cell.fact_id == id_refusal          # the refusing reading is cited
    assert set(cell.candidate_fact_ids) == {id_class_f, id_refusal}
    assert "refusal" in cell.rationale
