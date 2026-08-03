# tests/test_extract_tech.py
from procurement.extract_tech import (extract_tech_facts, TECH_PROMPT_VERSION,
                                      TECH_CHUNK_CHARS)
from procurement.store.models import fact_id_for
from shared.llm.mock_client import MockLLMClient


def _txt(tmp_path, body="Continuous rating 550 kW. H2S tolerance 50 ppm."):
    p = tmp_path / "datasheet.txt"
    p.write_text(body, encoding="utf-8")
    return str(p)


_ROW = "continuous rating 550 kW as stated on this vendor datasheet"


def _rows_txt(tmp_path, chunks: int):
    """A datasheet whose text spans exactly `chunks` chunks at the shipped budget.

    Sized from TECH_CHUNK_CHARS rather than from a literal line count, so the
    fixture keeps meaning what its name says if the budget is ever retuned -
    a fixture that quietly became one chunk would turn every row below green
    for the wrong reason."""
    per_chunk = TECH_CHUNK_CHARS // (len(_ROW) + 1)
    lines = [_ROW] * (per_chunk * (chunks - 1) + 1)
    p = tmp_path / "big-datasheet.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
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


def test_every_chunk_is_asked_and_the_facts_merge(tmp_path):
    client = MockLLMClient([
        {"facts": [{"parameter": "continuous_rating", "value": 550.0, "unit": "kW"}]},
        {"facts": [{"parameter": "h2s_tolerance", "value": 50.0, "unit": "ppm"}]},
        {"facts": [{"parameter": "ambient_temp", "value": 55.0, "unit": "degC"}]},
    ])
    facts, status, notes = extract_tech_facts("d1", _rows_txt(tmp_path, 3), client)
    assert status == "ok" and notes is None
    assert len(client.calls) == 3
    # merged in chunk order, so the stored list reads in document order
    assert [f.parameter for f in facts] == ["continuous_rating", "h2s_tolerance",
                                            "ambient_temp"]


def test_one_failing_chunk_fails_the_whole_extraction(tmp_path):
    class SecondChunkFails(MockLLMClient):
        def classify_structure(self, prompt, output_schema, context_text, images=None):
            super().classify_structure(prompt, output_schema, context_text, images)
            if len(self.calls) == 2:
                raise RuntimeError("response truncated at the max_tokens ceiling")
            return {"facts": [{"parameter": "continuous_rating", "value": 550.0}]}

    facts, status, notes = extract_tech_facts(
        "d1", _rows_txt(tmp_path, 3), SecondChunkFails({}))
    # the pipeline replaces this document's stored facts whenever status is
    # "ok", so storing 2 chunks of 3 as a success would delete the facts the
    # missing chunk carried with nothing recording why the count shrank
    assert (facts, status) == ([], "failed")
    assert notes and "max_tokens ceiling" in notes


def test_a_fact_repeated_across_chunks_is_stored_once(tmp_path):
    # a header row reprinted at the top of two chunks yields the same fact
    # twice, and two records sharing one fact_id make an id-addressed override
    # ambiguous about which row it corrects
    same = {"facts": [{"parameter": "h2s_tolerance", "value": 50.0, "unit": "ppm"}]}
    client = MockLLMClient([same, same, same])
    facts, status, _notes = extract_tech_facts("d1", _rows_txt(tmp_path, 3), client)
    assert status == "ok"
    assert len(client.calls) == 3           # genuinely three chunks, not one
    assert len(facts) == 1


def test_a_document_under_the_budget_is_asked_exactly_once(tmp_path):
    # chunking must not add a call to the common case: every small datasheet in
    # the corpus would otherwise cost an extra request per run
    client = _client()
    facts, status, _notes = extract_tech_facts("d1", _txt(tmp_path), client)
    assert status == "ok" and len(facts) == 2
    assert len(client.calls) == 1


def test_fact_ids_across_a_chunk_boundary_are_what_one_call_would_produce(tmp_path):
    # fact_id_for keys on (doc_id, parameter) and nothing ordinal, so which
    # chunk a fact arrives in must not touch its id. If it did, every stored
    # override and every compliance verdict keyed on that id would orphan the
    # first time the budget was retuned.
    first = {"parameter": "continuous_rating", "value": 550.0, "unit": "kW"}
    second = {"parameter": "h2s_tolerance", "value": 50.0, "unit": "ppm"}

    chunked = MockLLMClient([{"facts": [first]}, {"facts": [second]}])
    split, status, _notes = extract_tech_facts("d1", _rows_txt(tmp_path, 2), chunked)
    assert status == "ok" and len(chunked.calls) == 2

    one_call = MockLLMClient({"facts": [first, second]})
    whole, _status, _notes = extract_tech_facts("d1", _txt(tmp_path), one_call)
    assert len(one_call.calls) == 1

    assert [f.fact_id for f in split] == [f.fact_id for f in whole]
    assert [f.fact_id for f in split] == [fact_id_for("d1", "continuous_rating"),
                                          fact_id_for("d1", "h2s_tolerance")]

    # and the same fact arriving in the *other* chunk keeps its id, which is
    # the assertion a per-chunk-seeded id would fail
    swapped = MockLLMClient([{"facts": [second]}, {"facts": [first]}])
    other, _status, _notes = extract_tech_facts("d1", _rows_txt(tmp_path, 2), swapped)
    assert {f.fact_id for f in other} == {f.fact_id for f in split}
