from procurement.store.models import (FactRecord, DeviationRecord,
                                      fact_id_for, deviation_id_for)


def test_fact_record_defaults():
    f = FactRecord(fact_id="f-1", parameter="h2s_tolerance", doc_id="d1")
    assert f.value is None and f.unit is None and f.verbatim is None


def test_the_same_reading_hashes_to_the_same_id():
    a = fact_id_for("d1", "h2s_tolerance", "500", "ppm")
    assert a == fact_id_for("d1", "h2s_tolerance", "500", "ppm")
    assert a.startswith("f-")


def test_the_document_and_the_parameter_still_participate():
    assert (fact_id_for("d1", "h2s", "500", "ppm")
            != fact_id_for("d2", "h2s", "500", "ppm"))
    assert (fact_id_for("d1", "h2s", "500", "ppm")
            != fact_id_for("d1", "kw_rating", "500", "ppm"))


def test_the_parameter_is_still_case_and_whitespace_insensitive():
    assert (fact_id_for("d1", "  H2S_Tolerance ", "500", "ppm")
            == fact_id_for("d1", "h2s_tolerance", "500", "ppm"))


def test_two_readings_of_one_parameter_no_longer_collide():
    # The defect this task exists to fix: ADPOWER prints continuous_rating at
    # both 525 kW and 700 kW, and both are stored.
    assert (fact_id_for("d1", "continuous_rating", "525", "kW")
            != fact_id_for("d1", "continuous_rating", "700", "kW"))


def test_one_number_written_two_ways_is_one_id():
    # Task 7c's other deferred minor: value is str|float|None, so two chunks
    # formatting one number differently produced two keys and both survived.
    assert (fact_id_for("d1", "continuous_rating", 525.0, "kW")
            == fact_id_for("d1", "continuous_rating", "525", "kW"))


def test_the_unit_is_folded_but_never_converted():
    assert (fact_id_for("d1", "continuous_rating", "525", " kW ")
            == fact_id_for("d1", "continuous_rating", "525", "kw"))
    # Different quantity as printed, therefore separately addressable. The
    # equivalence is the compliance layer's to draw, not the store's.
    assert (fact_id_for("d1", "continuous_rating", "525", "kW")
            != fact_id_for("d1", "continuous_rating", "525000", "W"))


def test_a_missing_value_or_unit_is_keyed_not_crashed():
    assert fact_id_for("d1", "p", None, None).startswith("f-")
    assert (fact_id_for("d1", "p", None, None)
            != fact_id_for("d1", "p", "", None))


def test_deviation_record_defaults_to_noted():
    d = DeviationRecord(deviation_id="v-1", statement="Uses 60 Hz", doc_id="d1")
    assert d.disposition == "noted"
    assert d.clause_ref is None


def test_deviation_id_is_stable():
    a = deviation_id_for("d1", "4.2.7", "Vendor proposes an alternative")
    assert a == deviation_id_for("d1", "4.2.7", "Vendor proposes an alternative")
    assert a.startswith("v-")


def test_deviation_id_falls_back_to_the_statement_when_no_clause():
    a = deviation_id_for("d1", None, "Vendor proposes an alternative")
    b = deviation_id_for("d1", None, "A different statement entirely")
    assert a != b


def test_deviation_id_avoids_collision_same_clause_different_statements():
    """Two records sharing a clause_ref but with different statements get different ids."""
    a = deviation_id_for("d1", "4.2.7", "Vendor proposes an alternative")
    b = deviation_id_for("d1", "4.2.7", "Uses 60 Hz instead")
    assert a != b


def test_deviation_id_identical_clause_and_statement_produce_same_id():
    """Two records sharing a clause_ref AND the same statement get the same id."""
    a = deviation_id_for("d1", "4.2.7", "Vendor proposes an alternative")
    b = deviation_id_for("d1", "4.2.7", "Vendor proposes an alternative")
    assert a == b


def test_deviation_id_ignores_case_and_whitespace_on_both_components():
    """Case insensitivity and whitespace trimming apply to both clause_ref and statement."""
    a = deviation_id_for("d1", "  4.2.7 ", "  Vendor Proposes An Alternative ")
    b = deviation_id_for("d1", "4.2.7", "vendor proposes an alternative")
    assert a == b


def test_records_round_trip_through_json():
    f = FactRecord(fact_id="f-1", parameter="kw", value=550.0, unit="kW",
                   verbatim="550 kW continuous", doc_id="d1")
    assert FactRecord.model_validate(f.model_dump()).value == 550.0
    d = DeviationRecord(deviation_id="v-1", clause_ref="4.2", statement="s",
                        disposition="deviate", doc_id="d1")
    assert DeviationRecord.model_validate(d.model_dump()).disposition == "deviate"


def test_document_record_gains_classified_with():
    from procurement.store.models import DocumentRecord
    d = DocumentRecord(doc_id="d1", path="vendors/K/q.pdf", vendor="K",
                       content_sha256="0" * 64)
    assert d.classified_with is None
