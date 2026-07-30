# tests/test_extract_deviation.py
from procurement.extract_deviation import extract_deviations, DEVIATION_PROMPT_VERSION
from shared.llm.mock_client import MockLLMClient


def _txt(tmp_path):
    p = tmp_path / "Attachment-2 Vendor Deviation Form.txt"
    p.write_text("Clause 4.2.7 - vendor proposes 60 Hz. Deviation.", encoding="utf-8")
    return str(p)


def _client():
    return MockLLMClient(response={"deviations": [
        {"clause_ref": "4.2.7", "statement": "Vendor proposes 60 Hz",
         "disposition": "deviate"},
        {"clause_ref": "5.1", "statement": "Complies fully", "disposition": "comply"},
    ]})


def test_extracts_deviations_with_ids_and_provenance(tmp_path):
    items, status, _notes = extract_deviations("d1", _txt(tmp_path), _client())
    assert status == "ok"
    assert [i.clause_ref for i in items] == ["4.2.7", "5.1"]
    assert [i.disposition for i in items] == ["deviate", "comply"]
    assert all(i.doc_id == "d1" and i.deviation_id.startswith("v-") for i in items)


def test_ids_are_reproducible_across_runs(tmp_path):
    first, _, _notes = extract_deviations("d1", _txt(tmp_path), _client())
    second, _, _notes = extract_deviations("d1", _txt(tmp_path), _client())
    assert [i.deviation_id for i in first] == [i.deviation_id for i in second]


def test_unknown_disposition_degrades_to_noted_never_to_comply(tmp_path):
    client = MockLLMClient(response={"deviations": [
        {"clause_ref": "9.9", "statement": "Unclear", "disposition": "probably fine"},
    ]})
    items, _, _notes = extract_deviations("d1", _txt(tmp_path), client)
    assert items[0].disposition == "noted"


def test_entries_without_a_statement_are_dropped(tmp_path):
    client = MockLLMClient(response={"deviations": [
        {"clause_ref": "1.1", "statement": "   "},
        {"clause_ref": "1.2", "statement": "Real deviation"},
    ]})
    items, _, _notes = extract_deviations("d1", _txt(tmp_path), client)
    assert [i.clause_ref for i in items] == ["1.2"]


def test_empty_result_is_ok_not_failed(tmp_path):
    items, status, _notes = extract_deviations("d1", _txt(tmp_path),
                                       MockLLMClient(response={"deviations": []}))
    assert items == [] and status == "ok"


def test_extraction_error_returns_failed_without_raising(tmp_path):
    class Boom:
        supports_vision = True

        def __init__(self):
            self.calls = []

        def classify_structure(self, *a, **k):
            raise RuntimeError("provider down")

    items, status, notes = extract_deviations("d1", _txt(tmp_path), Boom())
    assert items == [] and status == "failed"
    assert notes and "provider down" in notes


def test_a_response_omitting_the_deviations_key_is_ok_with_no_entries(tmp_path):
    items, status, notes = extract_deviations("d1", _txt(tmp_path),
                                              MockLLMClient(response={}))
    assert items == [] and status == "ok" and notes is None


def test_malformed_disposition_type_is_skipped_not_whole_document(tmp_path):
    client = MockLLMClient(response={"deviations": [
        {"clause_ref": "1.1", "statement": "bad row", "disposition": None},
        {"clause_ref": "1.2", "statement": "good row", "disposition": "comply"},
    ]})
    items, status, _notes = extract_deviations("d1", _txt(tmp_path), client)
    assert status == "ok"
    assert [i.clause_ref for i in items] == ["1.2"]


def test_malformed_clause_ref_type_is_skipped_not_whole_document(tmp_path):
    client = MockLLMClient(response={"deviations": [
        {"clause_ref": 4.2, "statement": "bad row", "disposition": "deviate"},
        {"clause_ref": "5.1", "statement": "good row", "disposition": "comply"},
    ]})
    items, status, _notes = extract_deviations("d1", _txt(tmp_path), client)
    assert status == "ok"
    assert [i.clause_ref for i in items] == ["5.1"]


def test_prompt_version_is_exposed():
    assert DEVIATION_PROMPT_VERSION == "deviation_v1"
