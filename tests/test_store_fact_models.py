from procurement.store.models import (FactRecord, DeviationRecord,
                                      fact_id_for, deviation_id_for)


def test_fact_record_defaults():
    f = FactRecord(fact_id="f-1", parameter="h2s_tolerance", doc_id="d1")
    assert f.value is None and f.unit is None and f.verbatim is None


def test_fact_id_is_stable_across_re_extraction():
    a = fact_id_for("d1", "h2s_tolerance")
    assert a == fact_id_for("d1", "h2s_tolerance")
    assert a.startswith("f-")


def test_fact_id_varies_by_document_and_parameter():
    assert fact_id_for("d1", "h2s") != fact_id_for("d2", "h2s")
    assert fact_id_for("d1", "h2s") != fact_id_for("d1", "kw_rating")


def test_fact_id_ignores_parameter_case_and_padding():
    assert fact_id_for("d1", "  H2S_Tolerance ") == fact_id_for("d1", "h2s_tolerance")


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
