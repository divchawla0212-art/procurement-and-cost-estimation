import pytest

from procurement.compliance import (VERDICTS, _readings, evaluate,
                                    evaluate_project, vocabulary, vocabulary_sha)
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


def _fact(parameter="h2s_tolerance", value=70.0, unit="ppm", doc_id="d9",
          fact_id=None):
    return {"fact_id": fact_id if fact_id is not None else f"f-{parameter}",
            "parameter": parameter, "value": value,
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


def test_a_null_valued_fact_is_unanswered_for_a_presence_requirement():
    # a fact naming the parameter with no value must not satisfy "must be
    # stated" - that is missing data coerced into a pass
    r = evaluate(_stated(parameter="anchor_bolt", value=None),
                 [_stated_fact("anchor_bolt", None)], [], "KERUI", "now")
    assert r.verdict == "unanswered" and r.fact_id is None
    assert "anchor_bolt" in r.rationale


def test_a_null_valued_fact_is_unanswered_for_a_stated_value_requirement():
    r = evaluate(_stated(), [_stated_fact("generator_insulation_class", None)],
                 [], "KERUI", "now")
    assert r.verdict == "unanswered" and r.fact_id is None


def test_a_stated_value_that_negates_the_requirement_is_review_not_pass():
    # The token subset says "Class F" is present, so the tier used to call this
    # a pass and file it under `matched`, where a reviewer looks least - while
    # the sentence says the opposite. 93 of 143 requirements land in this tier,
    # and this is the error direction that silently clears a vendor.
    r = evaluate(_stated(),
                 [_stated_fact("generator_insulation_class",
                               "Class B rise (Class F insulation not offered)")],
                 [], "MKON", "now")
    assert r.verdict == "review"
    assert "Class F" in r.rationale                   # the requirement
    assert "not offered" in r.rationale               # and the vendor's words
    assert r.fact_id == "f-1"                         # cite the evidence


def test_a_negated_presence_answer_does_not_satisfy_must_be_stated():
    # The other `pass` the tier can return. "N/A" against "Anchor Bolt
    # Required" is the vendor declining, not the vendor answering, and coercing
    # it into a pass is missing data read as compliance.
    r = evaluate(_stated(parameter="anchor_bolt", value=None),
                 [_stated_fact("anchor_bolt", "N/A")], [], "KERUI", "now")
    assert r.verdict == "review" and r.fact_id == "f-1"
    assert "N/A" in r.rationale


def test_the_requirements_own_negation_is_not_read_as_a_refusal():
    # The false-positive direction that matters: a clause that is itself
    # phrased in the negative, echoed back verbatim by the vendor, is a match
    # and must stay one. Only a negation the *vendor* added counts.
    r = evaluate(_stated(parameter="asbestos_content", value="no asbestos"),
                 [_stated_fact("asbestos_content",
                               "No asbestos used in any component")],
                 [], "ADPOWER", "now")
    assert r.verdict == "pass"


def test_a_compound_value_carrying_an_unrelated_negation_is_reviewed_not_passed():
    # Deliberate, and the conservative side of the trade: the guard fires on a
    # negation anywhere in the stated value, so a compound value whose "no"
    # qualifies something else is demoted to `review` rather than passed. A
    # positional heuristic over tokenized prose would be a confident guess, and
    # `review` costs a reviewer a minute where `pass` costs an award. Never
    # `fail` on this path, whatever the words say (INV-E).
    r = evaluate(_stated(),
                 [_stated_fact("generator_insulation_class",
                               "Class F, no derating below 40 degC")],
                 [], "AESL", "now")
    assert r.verdict == "review"


def test_a_blank_valued_fact_is_unanswered_not_a_token_match():
    # "   " tokenizes to nothing; it is not evidence, whatever the string diff says
    r = evaluate(_stated(), [_stated_fact("generator_insulation_class", "   ")],
                 [], "KERUI", "now")
    assert r.verdict == "unanswered" and r.fact_id is None


def test_a_refusal_on_a_later_reading_is_not_hidden_by_the_first():
    req = _stated(parameter="anchor_bolt", value=None)
    facts = [_stated_fact("anchor_bolt", "Supplied", fact_id="f-a"),
             _stated_fact("anchor_bolt", "N/A", fact_id="f-b")]
    r = evaluate(req, facts, [], "ADPOWER", "now")
    assert r.verdict == "review"
    assert "f-b" in r.candidate_fact_ids


def test_a_refusal_in_a_middle_reading_is_not_hidden_by_its_neighbours():
    # The two-reading row above pins `groups[:1]` but survives a sweep that
    # checked only the first and last reading. Three readings with the refusal
    # in the middle is the shape that pins the sweep to *every* group - the
    # invariant Task 4 owns, on the tier that is 93 of 143 requirements.
    req = _stated(parameter="anchor_bolt", value=None)
    facts = [_stated_fact("anchor_bolt", "Supplied", fact_id="f-a"),
             _stated_fact("anchor_bolt", "N/A", fact_id="f-b"),
             _stated_fact("anchor_bolt", "M20 galvanised", fact_id="f-c")]
    r = evaluate(req, facts, [], "ADPOWER", "now")
    assert r.verdict == "review"
    assert r.fact_id == "f-b"                         # the refusing reading
    assert set(r.candidate_fact_ids) == {"f-a", "f-b", "f-c"}


def test_presence_only_with_two_clean_readings_still_passes():
    # An enumeration is an answer, at length — not a conflict.
    req = _stated(parameter="applicable_standard", value=None)
    facts = [_stated_fact("applicable_standard", "ISO 8528", fact_id="f-a"),
             _stated_fact("applicable_standard", "IEC 60034", fact_id="f-b")]
    assert evaluate(req, facts, [], "ADPOWER", "now").verdict == "pass"


def test_the_requirements_own_negation_is_still_not_a_refusal():
    req = RequirementRecord(req_id="r-1", clause_ref="2.6", text="no asbestos",
                            source_doc_id="d", checkability="stated",
                            parameter="asbestos", value=None)
    facts = [_stated_fact("asbestos", "No asbestos used in any component", fact_id="f-a")]
    assert evaluate(req, facts, [], "ADPOWER", "now").verdict == "pass"


# --- grouping facts into distinct readings ----------------------------------

def test_one_reading_stays_one_reading():
    facts = [_fact("continuous_rating", "525", "kW")]
    assert len(_readings(facts, "continuous_rating")) == 1


def test_two_different_numbers_are_two_readings():
    facts = [_fact("continuous_rating", "525", "kW"),
             _fact("continuous_rating", "700", "kW")]
    assert len(_readings(facts, "continuous_rating")) == 2


def test_one_quantity_written_in_two_units_is_one_reading():
    facts = [_fact("continuous_rating", "525", "kW"),
             _fact("continuous_rating", "525000", "W")]
    groups = _readings(facts, "continuous_rating")
    assert len(groups) == 1
    assert len(groups[0]) == 2          # both members kept, for citation


def test_an_unconvertible_unit_falls_back_to_text_and_does_not_raise():
    facts = [_fact("applicable_standard", "ISO 8528", None),
             _fact("applicable_standard", "ISO 8528", None)]
    assert len(_readings(facts, "applicable_standard")) == 1


def test_two_standards_sharing_digits_are_not_one_reading():
    # to_number("ISO 8528") is 8528.0, so keying on it would merge these.
    facts = [_fact("applicable_standard", "ISO 8528", None),
             _fact("applicable_standard", "API 8528", None)]
    assert len(_readings(facts, "applicable_standard")) == 2


def test_an_internal_space_does_not_split_a_reading_in_two():
    # Same text key material, differently spaced - not a genuine disagreement.
    facts = [_fact("ip_rating", "IP 55", None),
             _fact("ip_rating", "IP55", None)]
    assert len(_readings(facts, "ip_rating")) == 1


def test_a_non_breaking_space_or_tab_does_not_split_a_reading_either():
    # `pdftotext` and pypdf both emit U+00A0 and tabs inside values, so an
    # ASCII-space-only squeeze left the same formatting difference splitting
    # one reading in two - and a spurious second reading escalates a decidable
    # cell to `review`. Built with chr(): an invisible literal in the source
    # is a test nobody can review by reading it.
    nbsp, tab = chr(0x00A0), chr(0x09)   # U+00A0 no-break space, tab
    facts = [_fact("ip_rating", "IP 55", None),
             _fact("ip_rating", f"IP{nbsp}55", None),
             _fact("ip_rating", f"IP{tab}55", None)]
    assert len(_readings(facts, "ip_rating")) == 1


def test_whitespace_folding_never_merges_two_different_values():
    # The squeeze is formatting tolerance, not value tolerance: it removes
    # whitespace, so it can only ever merge strings that are already equal
    # once whitespace is gone. Two genuinely different ratings stay apart.
    facts = [_fact("ip_rating", "IP 55", None),
             _fact("ip_rating", "IP 66", None)]
    assert len(_readings(facts, "ip_rating")) == 2


def test_degc_and_the_single_glyph_celsius_sign_are_one_reading():
    # "55" degC resolves through the numeric path; "55" ℃ used to fall to text
    # because units._ALIASES had no entry for the single-glyph sign, splitting
    # one quantity into two "distinct" readings.
    facts = [_fact("ambient_design_temp", "55", "degC"),
             _fact("ambient_design_temp", "55", "℃")]
    assert len(_readings(facts, "ambient_design_temp")) == 1


def test_a_range_is_not_collapsed_onto_its_lower_bound():
    # ADPOWER states ambient_design_temp as 55, 5-58 and 4-58 on one store.
    facts = [_fact("ambient_design_temp", "55", "Deg C"),
             _fact("ambient_design_temp", "5-58", "deg C"),
             _fact("ambient_design_temp", "4-58", "deg C")]
    assert len(_readings(facts, "ambient_design_temp")) == 3


def test_only_facts_naming_the_parameter_are_grouped():
    facts = [_fact("continuous_rating", "525", "kW"),
             _fact("h2s_tolerance", "500", "ppm")]
    assert len(_readings(facts, "continuous_rating")) == 1


def test_a_fact_stating_nothing_is_not_a_reading():
    facts = [_fact("continuous_rating", "525", "kW"),
             _fact("continuous_rating", None), _fact("continuous_rating", "   ")]
    assert len(_readings(facts, "continuous_rating")) == 1


def test_groups_and_members_stay_in_document_order():
    facts = [_fact("p", "700", "kW", fact_id="f-b"),
             _fact("p", "525", "kW", fact_id="f-a"),
             _fact("p", "700000", "W", fact_id="f-c")]
    groups = _readings(facts, "p")
    assert [g[0]["fact_id"] for g in groups] == ["f-b", "f-a"]
    assert [m["fact_id"] for m in groups[0]] == ["f-b", "f-c"]


# --- wiring _readings into evaluate: multiplicity is review, never fail -----

def test_two_distinct_readings_are_review_not_an_auto_decision():
    req = _req(parameter="continuous_rating", checkability="auto",
               operator=">=", value=600, unit="kW")
    facts = [_fact("continuous_rating", "700", "kW", fact_id="f-a"),
             _fact("continuous_rating", "525", "kW", fact_id="f-b")]
    r = evaluate(req, facts, [], "ADPOWER", "now")
    assert r.verdict == "review"
    assert set(r.candidate_fact_ids) == {"f-a", "f-b"}
    assert "700" in r.rationale and "525" in r.rationale


def test_a_vendor_is_no_longer_failed_on_print_order():
    # The live defect: on coverage-floor-t7 this cell currently reads `fail`.
    req = _req(parameter="continuous_rating", checkability="auto",
               operator=">=", value=600, unit="kW")
    facts = [_fact("continuous_rating", "525", "kW", fact_id="f-b"),
             _fact("continuous_rating", "700", "kW", fact_id="f-a")]
    assert evaluate(req, facts, [], "ADPOWER", "now").verdict == "review"


def test_one_reading_decides_exactly_as_before():
    req = _req(parameter="continuous_rating", checkability="auto",
               operator=">=", value=600, unit="kW")
    facts = [_fact("continuous_rating", "700", "kW", fact_id="f-a")]
    r = evaluate(req, facts, [], "ADPOWER", "now")
    assert r.verdict == "pass"
    assert r.fact_id == "f-a"
    assert r.candidate_fact_ids == []


def test_equivalent_readings_still_decide():
    req = _req(parameter="continuous_rating", checkability="auto",
               operator=">=", value=600, unit="kW")
    facts = [_fact("continuous_rating", "700", "kW", fact_id="f-a"),
             _fact("continuous_rating", "700000", "W", fact_id="f-c")]
    r = evaluate(req, facts, [], "ADPOWER", "now")
    assert r.verdict == "pass"
    assert r.fact_id == "f-a"                  # first in document order
    assert r.candidate_fact_ids == ["f-a", "f-c"]


def test_a_stated_value_requirement_with_two_readings_is_review():
    req = _req(parameter="insulation_class", checkability="stated",
               value="Class F")
    facts = [_fact("insulation_class", "Class F", fact_id="f-a"),
             _fact("insulation_class", "Class H", fact_id="f-b")]
    r = evaluate(req, facts, [], "ADPOWER", "now")
    assert r.verdict == "review"
    assert set(r.candidate_fact_ids) == {"f-a", "f-b"}


def test_multiplicity_never_produces_fail():
    req = _req(parameter="continuous_rating", checkability="auto",
               operator=">=", value=900, unit="kW")
    facts = [_fact("continuous_rating", "700", "kW"),
             _fact("continuous_rating", "525", "kW")]
    assert evaluate(req, facts, [], "ADPOWER", "now").verdict == "review"


def test_a_declared_deviation_still_wins_over_multiplicity():
    req = _req(clause="2.3", parameter="continuous_rating", checkability="auto",
               operator=">=", value=600, unit="kW")
    facts = [_fact("continuous_rating", "700", "kW"),
             _fact("continuous_rating", "525", "kW")]
    devs = [{"clause_ref": "2.3", "disposition": "deviate", "statement": "no"}]
    assert evaluate(req, facts, devs, "ADPOWER", "now").verdict == "deviation"


def test_no_reading_is_still_unanswered_not_review():
    req = _req(parameter="continuous_rating", checkability="auto",
               operator=">=", value=600, unit="kW")
    assert evaluate(req, [], [], "ADPOWER", "now").verdict == "unanswered"


def test_candidate_fact_ids_are_deduplicated():
    # No current call site can produce this (every group is disjoint), but
    # nothing enforces that upstream, so the id list itself must not repeat.
    req = _req(parameter="continuous_rating", checkability="auto",
               operator=">=", value=600, unit="kW")
    fact = _fact("continuous_rating", "700", "kW", fact_id="f-a")
    facts = [fact, dict(fact)]      # same fact_id, appears twice in one group
    r = evaluate(req, facts, [], "ADPOWER", "now")
    assert r.candidate_fact_ids == ["f-a"]


def test_a_blank_or_none_valued_fact_does_not_manufacture_a_second_reading():
    # Before _readings filtered blank evidence, a bare next(...) could pick up
    # this fact first and reach units.compare with a blank value, producing a
    # confusing Unconvertible -> unanswered instead of deciding on the real one.
    req = _req(parameter="continuous_rating", checkability="auto",
               operator=">=", value=600, unit="kW")
    facts = [_fact("continuous_rating", "", None, fact_id="f-b"),
             _fact("continuous_rating", None, None, fact_id="f-c"),
             _fact("continuous_rating", "700", "kW", fact_id="f-a")]
    r = evaluate(req, facts, [], "ADPOWER", "now")
    assert r.verdict == "pass"
    assert r.fact_id == "f-a"
    assert r.candidate_fact_ids == []
