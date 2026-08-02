import pytest

from procurement.compliance import (VERDICTS, evaluate, evaluate_project,
                                    vocabulary, vocabulary_sha)
from procurement.project import create_project, load_project, save_project
from procurement.store import snapshots
from procurement.store.models import (RequirementRecord, RequirementSet,
                                      VendorFacts, req_id_for)

NOW = "2026-07-30T12:00:00+00:00"


def _req(clause="4.2.7", **kw):
    body = dict(clause_ref=clause, text="H2S at least 50 ppm",
                category="technical", checkability="auto",
                parameter="h2s_tolerance", operator=">=", value=50.0,
                unit="ppm", source_doc_id="d1")
    body.update(kw)
    return RequirementRecord(req_id=req_id_for("d1", clause), **body)


def _fact(parameter="h2s_tolerance", value=70.0, unit="ppm", doc_id="d9"):
    return {"fact_id": f"f-{parameter}", "parameter": parameter, "value": value,
            "unit": unit, "verbatim": f"{value} {unit}", "doc_id": doc_id}


def _dev(clause="4.2.7", disposition="deviate"):
    return {"deviation_id": "v-1", "clause_ref": clause, "statement": "we differ",
            "disposition": disposition, "doc_id": "d8"}


def test_a_satisfied_bound_passes_and_cites_its_evidence():
    r = evaluate(_req(), [_fact()], [], "KERUI", NOW)
    assert r.verdict == "pass"
    assert (r.req_id, r.vendor, r.fact_id, r.doc_id) == (
        _req().req_id, "KERUI", "f-h2s_tolerance", "d9")
    assert "70" in r.rationale and r.evaluated_at == NOW


def test_an_unsatisfied_bound_fails():
    assert evaluate(_req(), [_fact(value=40.0)], [], "KERUI", NOW).verdict == "fail"


def test_a_missing_fact_is_unanswered_never_fail():
    r = evaluate(_req(), [_fact(parameter="frequency", value=50, unit="Hz")],
                 [], "KERUI", NOW)
    assert r.verdict == "unanswered" and r.fact_id is None
    assert "h2s_tolerance" in r.rationale


def test_no_facts_at_all_is_unanswered_not_fail():
    assert evaluate(_req(), [], [], "KERUI", NOW).verdict == "unanswered"


def test_a_deviated_clause_is_a_deviation_whatever_the_numbers_say():
    r = evaluate(_req(), [_fact(value=999.0)], [_dev()], "KERUI", NOW)
    assert r.verdict == "deviation" and "we differ" in r.rationale


def test_a_comply_disposition_is_not_evidence_of_compliance():
    # the vendor asserting compliance must not overturn its own datasheet
    r = evaluate(_req(), [_fact(value=40.0)], [_dev(disposition="comply")],
                 "KERUI", NOW)
    assert r.verdict == "fail"


def test_a_deviation_on_another_clause_is_ignored():
    r = evaluate(_req(), [_fact()], [_dev(clause="9.9")], "KERUI", NOW)
    assert r.verdict == "pass"


def test_clause_refs_match_across_printing_differences():
    r = evaluate(_req(), [_fact()], [_dev(clause="Clause 4.2.7")], "KERUI", NOW)
    assert r.verdict == "deviation"


def test_a_judgement_requirement_is_review_with_candidates_gathered():
    r = evaluate(_req(checkability="judgement", parameter=None, operator=None,
                      value=None, unit=None, text="Submit an O&M manual"),
                 [_fact()], [_dev(clause="9.9", disposition="noted")],
                 "KERUI", NOW)
    assert r.verdict == "review"
    assert "O&M manual" in r.rationale


def test_an_unconvertible_unit_is_unanswered_with_the_reason_stated():
    r = evaluate(_req(unit="kW"), [_fact(unit="kVA")], [], "KERUI", NOW)
    assert r.verdict == "unanswered"
    assert "power factor" in r.rationale.lower()
    assert r.fact_id == "f-h2s_tolerance"       # the evidence is still cited


def test_a_non_numeric_vendor_value_is_unanswered_not_fail():
    r = evaluate(_req(), [_fact(value="as per standard")], [], "KERUI", NOW)
    assert r.verdict == "unanswered" and "not a number" in r.rationale.lower()


def test_units_are_converted_before_comparing():
    r = evaluate(_req(parameter="continuous_rating", value=0.5, unit="MW"),
                 [_fact(parameter="continuous_rating", value=550.0, unit="kW")],
                 [], "KERUI", NOW)
    assert r.verdict == "pass"


def test_a_range_requirement_passes_a_value_inside_it():
    # end to end for the live false-fail: 55 degC against "5-58 deg C"
    r = evaluate(_req(parameter="ambient_design_temp", operator="between",
                      value=[5, 58], unit="degC"),
                 [_fact(parameter="ambient_design_temp", value=55, unit="degC")],
                 [], "KERUI", NOW)
    assert r.verdict == "pass"


def test_a_range_requirement_fails_a_value_outside_it():
    r = evaluate(_req(parameter="ambient_design_temp", operator="between",
                      value=[5, 58], unit="degC"),
                 [_fact(parameter="ambient_design_temp", value=60, unit="degC")],
                 [], "KERUI", NOW)
    assert r.verdict == "fail"


def test_parameter_matching_tolerates_naming_differences():
    r = evaluate(_req(), [_fact(parameter="H2S Tolerance")], [], "KERUI", NOW)
    assert r.verdict == "pass"


def test_every_verdict_is_from_the_declared_vocabulary():
    for facts in ([], [_fact()], [_fact(value=1.0)], [_fact(unit="kVA")]):
        assert evaluate(_req(), facts, [], "KERUI", NOW).verdict in VERDICTS


# --- evaluate_project -------------------------------------------------------

def _project(tmp_path, requirements, facts_by_vendor):
    root = str(tmp_path)
    create_project(root, "P")
    project = load_project(root, "p")
    project.vendors = sorted(facts_by_vendor)
    save_project(root, project)
    snapshots.save_requirements(root, "p", RequirementSet(requirements=requirements))
    for vendor, facts in facts_by_vendor.items():
        if facts is not None:
            snapshots.save_facts(root, "p", VendorFacts(vendor=vendor,
                                                        technical=facts))
    return root


def test_the_matrix_has_one_cell_per_live_pair_and_no_other(tmp_path):
    # INV-6
    root = _project(tmp_path, [_req("4.2.7"), _req("4.2.8")],
                    {"KERUI": [_fact()], "MKON": [_fact(value=10.0)]})
    results = evaluate_project(root, "p", now=NOW)
    assert len(results) == 4
    assert {(r.req_id, r.vendor) for r in results} == {
        (req_id_for("d1", c), v) for c in ("4.2.7", "4.2.8")
        for v in ("KERUI", "MKON")}
    assert snapshots.load_compliance(root, "p") == results


def test_a_vendor_with_no_facts_still_gets_a_full_unanswered_column(tmp_path):
    root = _project(tmp_path, [_req()], {"KERUI": [_fact()], "AESL": None})
    results = evaluate_project(root, "p", now=NOW)
    aesl = [r for r in results if r.vendor == "AESL"]
    assert len(aesl) == 1 and aesl[0].verdict == "unanswered"


def test_a_withdrawn_requirement_produces_no_cell(tmp_path):
    root = _project(tmp_path, [_req("4.2.7"), _req("4.2.8", withdrawn=True)],
                    {"KERUI": [_fact()]})
    results = evaluate_project(root, "p", now=NOW)
    assert {r.req_id for r in results} == {req_id_for("d1", "4.2.7")}


def test_a_vendor_removed_from_the_project_leaves_the_matrix(tmp_path):
    root = _project(tmp_path, [_req()], {"KERUI": [_fact()], "MKON": [_fact()]})
    evaluate_project(root, "p", now=NOW)
    project = load_project(root, "p")
    project.vendors = ["KERUI"]
    save_project(root, project)
    results = evaluate_project(root, "p", now=NOW)
    assert {r.vendor for r in results} == {"KERUI"}
    assert {r.vendor for r in snapshots.load_compliance(root, "p")} == {"KERUI"}


def test_no_verdict_cites_a_fact_that_is_no_longer_stored(tmp_path):
    # INV-7
    root = _project(tmp_path, [_req()], {"KERUI": [_fact()]})
    assert evaluate_project(root, "p", now=NOW)[0].fact_id == "f-h2s_tolerance"
    snapshots.save_facts(root, "p", VendorFacts(vendor="KERUI", technical=[]))
    results = evaluate_project(root, "p", now=NOW)
    stored = {f["fact_id"] for f in
              (snapshots.load_facts(root, "p", "KERUI").technical or [])}
    assert all(r.fact_id is None or r.fact_id in stored for r in results)
    assert results[0].verdict == "unanswered"


def test_zero_requirements_writes_an_empty_matrix_not_a_missing_file(tmp_path):
    root = _project(tmp_path, [], {"KERUI": [_fact()]})
    assert evaluate_project(root, "p", now=NOW) == []
    assert snapshots.load_compliance(root, "p") == []


def test_recomputing_with_unchanged_inputs_is_byte_identical(tmp_path):
    root = _project(tmp_path, [_req()], {"KERUI": [_fact()]})
    first = [r.model_dump() for r in evaluate_project(root, "p", now=NOW)]
    second = [r.model_dump() for r in evaluate_project(root, "p", now=NOW)]
    assert first == second


# --- the parameter vocabulary ----------------------------------------------

def test_the_vocabulary_is_the_sorted_auto_parameter_names():
    reqset = RequirementSet(requirements=[
        _req("4.2.7"),
        _req("4.2.8", parameter="continuous_rating"),
        _req("9.1", checkability="judgement", parameter=None),
    ])
    assert vocabulary(reqset) == ["continuous_rating", "h2s_tolerance"]


def test_the_vocabulary_fingerprint_is_stable_and_order_independent():
    a = vocabulary_sha(["continuous_rating", "h2s_tolerance"])
    b = vocabulary_sha(["h2s_tolerance", "continuous_rating"])
    assert a == b == vocabulary_sha(["continuous_rating", "h2s_tolerance"])
    assert a != vocabulary_sha(["continuous_rating"])
    assert vocabulary_sha([])          # an empty vocabulary still fingerprints


def test_a_withdrawn_requirement_leaves_the_vocabulary():
    # INV-6 makes it produce no cell, so asking datasheets to look for its
    # parameter would buy nothing and would keep the fingerprint churning
    reqset = RequirementSet(requirements=[
        _req("4.2.7"),
        _req("4.2.8", parameter="continuous_rating", withdrawn=True)])
    assert vocabulary(reqset) == ["h2s_tolerance"]


# --- the `stated` tier -------------------------------------------------------

def _stated(parameter="generator_insulation_class", value="Class F", clause="2.6"):
    return RequirementRecord(req_id="r-1", clause_ref=clause, text="Insulation Class F",
                             source_doc_id="d", checkability="stated",
                             parameter=parameter, value=value)


def _stated_fact(parameter, value, fact_id="f-1"):
    return {"fact_id": fact_id, "parameter": parameter, "value": value,
            "unit": None, "doc_id": "d9"}


def test_stated_passes_when_the_required_tokens_are_present():
    r = evaluate(_stated(), [_stated_fact("generator_insulation_class",
                                          "Class F / Class B rise")], [], "KERUI", "now")
    assert (r.verdict, r.fact_id) == ("pass", "f-1")


def test_stated_with_no_matching_fact_is_unanswered_never_fail():
    r = evaluate(_stated(), [_stated_fact("continuous_rating", "525")],
                 [], "ADPOWER", "now")
    assert r.verdict == "unanswered"
    assert "generator_insulation_class" in r.rationale


def test_a_stated_mismatch_is_review_not_fail():
    # Class H is better insulation than Class F; a token diff cannot know that
    r = evaluate(_stated(), [_stated_fact("generator_insulation_class", "Class H")],
                 [], "MKON", "now")
    assert r.verdict == "review"
    assert "Class F" in r.rationale and "Class H" in r.rationale
    assert r.fact_id == "f-1"       # cite the evidence the human must weigh


def test_a_presence_requirement_passes_when_the_parameter_is_stated_at_all():
    r = evaluate(_stated(parameter="anchor_bolt", value=None),
                 [_stated_fact("anchor_bolt", "Provided")], [], "KERUI", "now")
    assert (r.verdict, r.fact_id) == ("pass", "f-1")


def test_a_declared_deviation_still_beats_a_stated_pass():
    dev = [{"clause_ref": "2.6", "statement": "Class B only", "disposition": "deviate"}]
    r = evaluate(_stated(), [_stated_fact("generator_insulation_class", "Class F")],
                 dev, "MKON", "now")
    assert r.verdict == "deviation"
