# tests/test_extract_tech.py
from procurement.extract_tech import extract_tech_facts, TECH_PROMPT_VERSION
from shared.llm.mock_client import MockLLMClient


def _txt(tmp_path, body="Continuous rating 550 kW. H2S tolerance 50 ppm."):
    p = tmp_path / "datasheet.txt"
    p.write_text(body, encoding="utf-8")
    return str(p)


def _client():
    return MockLLMClient(response={"facts": [
        {"parameter": "continuous_rating", "value": 550.0, "unit": "kW",
         "verbatim": "Continuous rating 550 kW"},
        {"parameter": "h2s_tolerance", "value": 50.0, "unit": "ppm",
         "verbatim": "H2S tolerance 50 ppm"},
    ]})


def test_extracts_facts_with_stable_ids_and_provenance(tmp_path):
    facts, status, _notes = extract_tech_facts("d1", _txt(tmp_path), _client())
    assert status == "ok"
    assert [f.parameter for f in facts] == ["continuous_rating", "h2s_tolerance"]
    assert all(f.doc_id == "d1" for f in facts)
    assert all(f.fact_id.startswith("f-") for f in facts)
    assert facts[0].unit == "kW" and facts[0].value == 550.0


def test_ids_are_reproducible_across_runs(tmp_path):
    first, _, _notes = extract_tech_facts("d1", _txt(tmp_path), _client())
    second, _, _notes = extract_tech_facts("d1", _txt(tmp_path), _client())
    assert [f.fact_id for f in first] == [f.fact_id for f in second]


def test_facts_without_a_parameter_name_are_dropped(tmp_path):
    client = MockLLMClient(response={"facts": [
        {"parameter": "", "value": 1.0},
        {"parameter": "kw", "value": 550.0},
    ]})
    facts, status, _notes = extract_tech_facts("d1", _txt(tmp_path), client)
    assert status == "ok"
    assert [f.parameter for f in facts] == ["kw"]


def test_a_missing_value_is_kept_as_none_not_zero(tmp_path):
    client = MockLLMClient(response={"facts": [{"parameter": "h2s", "unit": "ppm"}]})
    facts, _, _notes = extract_tech_facts("d1", _txt(tmp_path), client)
    assert facts[0].value is None


def test_empty_result_is_ok_not_failed(tmp_path):
    facts, status, _notes = extract_tech_facts("d1", _txt(tmp_path),
                                       MockLLMClient(response={"facts": []}))
    assert facts == [] and status == "ok"


def test_extraction_error_returns_failed_without_raising(tmp_path):
    class Boom:
        supports_vision = True

        def __init__(self):
            self.calls = []

        def classify_structure(self, *a, **k):
            raise RuntimeError("provider down")

    facts, status, notes = extract_tech_facts("d1", _txt(tmp_path), Boom())
    assert facts == [] and status == "failed"
    # the reason must survive the except: without it a permanently-failing
    # datasheet is retried on every run with nothing to diagnose it by
    assert notes and "provider down" in notes


def test_a_response_omitting_the_facts_key_is_ok_with_no_facts(tmp_path):
    # a tool call may legitimately omit an optional array (real clients return
    # dict(block.input) verbatim). "No facts" is an empty extraction, not a
    # failed one - calling it failed makes the document a permanent per-run charge.
    facts, status, notes = extract_tech_facts("d1", _txt(tmp_path),
                                              MockLLMClient(response={}))
    assert facts == [] and status == "ok" and notes is None


def test_parameter_vocabulary_is_passed_to_the_model(tmp_path):
    client = _client()
    extract_tech_facts("d1", _txt(tmp_path), client,
                       parameters=["h2s_tolerance", "ambient_temp"])
    sent = client.calls[0]["context_text"]
    assert "h2s_tolerance" in sent and "ambient_temp" in sent


def test_malformed_fact_entry_is_skipped_not_whole_datasheet(tmp_path):
    client = MockLLMClient(response={"facts": [
        {"parameter": "bad_one", "unit": 123},
        {"parameter": "kw", "value": 550.0, "unit": "kW"},
    ]})
    facts, status, _notes = extract_tech_facts("d1", _txt(tmp_path), client)
    assert status == "ok"
    assert [f.parameter for f in facts] == ["kw"]


def test_prompt_version_is_exposed():
    assert TECH_PROMPT_VERSION == "tech_facts_v1"
