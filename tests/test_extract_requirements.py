import os

import pytest

from procurement.extract_requirements import (REQUIREMENTS_PROMPT_VERSION,
                                              extract_requirements, _chunks)
from procurement.store.models import RequirementSet, req_id_for
from procurement.store import snapshots
from procurement.project import create_project
from shared.llm.mock_client import MockLLMClient


class StubClient:
    supports_vision = True

    def __init__(self, response, raises=False):
        self._response, self._raises = response, raises
        self.calls = []

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        self.calls.append({"prompt": prompt, "context_text": context_text})
        if self._raises:
            raise RuntimeError("provider unavailable")
        return self._response


def _spec(tmp_path, text="4.2.7 H2S tolerance shall be at least 50 ppm"):
    p = tmp_path / "MR.txt"
    p.write_text(text, encoding="utf-8")
    return str(p)


_TWO_CLAUSES = {"requirements": [
    {"clause_ref": "4.2.7", "text": "H2S tolerance shall be at least 50 ppm",
     "category": "technical", "checkability": "auto", "parameter": "h2s_tolerance",
     "operator": ">=", "value": 50, "unit": "ppm"},
    {"clause_ref": "9.1", "text": "Vendor shall submit an O&M manual in English",
     "category": "documentation", "checkability": "judgement"},
]}


def test_extracts_both_tiers_with_stable_ids(tmp_path):
    records, status, notes = extract_requirements("d1", _spec(tmp_path),
                                                  StubClient(_TWO_CLAUSES))
    assert (status, notes) == ("ok", None)
    assert [r.req_id for r in records] == [req_id_for("d1", "4.2.7"),
                                           req_id_for("d1", "9.1")]
    auto, judgement = records
    assert (auto.checkability, auto.parameter, auto.operator, auto.value,
            auto.unit) == ("auto", "h2s_tolerance", ">=", 50, "ppm")
    assert judgement.checkability == "judgement"
    assert all(r.source_doc_id == "d1" for r in records)


@pytest.mark.parametrize("missing", ["parameter", "operator", "value", "unit"])
def test_an_auto_clause_missing_any_bound_is_demoted_to_judgement(tmp_path, missing):
    entry = dict(_TWO_CLAUSES["requirements"][0])
    entry[missing] = None
    [record], status, _ = extract_requirements(
        "d1", _spec(tmp_path), StubClient({"requirements": [entry]}))
    assert status == "ok"
    assert record.checkability == "judgement"
    # and the clause is still stored - demotion is not deletion
    assert record.clause_ref == "4.2.7"
    # demotion clears all four bound fields together, not just the one that
    # was missing on input
    assert (record.parameter, record.operator, record.value, record.unit) == (
        None, None, None, None)


def test_a_zero_bound_is_a_real_bound_and_stays_auto(tmp_path):
    # value is not None is the demotion test, not truthiness - a bound of 0
    # (e.g. "vibration shall be 0 mm/s") is a real, statable bound and must
    # not be mistaken for a missing one.
    entry = dict(_TWO_CLAUSES["requirements"][0], value=0)
    [record], status, _ = extract_requirements(
        "d1", _spec(tmp_path), StubClient({"requirements": [entry]}))
    assert (status, record.checkability, record.value) == ("ok", "auto", 0)


def test_a_dimensionless_auto_clause_may_declare_unit_none_explicitly(tmp_path):
    # "frequency shall be 50 Hz" has a unit; "number of starts shall be >= 3"
    # does not. The model states unit "" for genuinely dimensionless bounds,
    # which is a stated unit, not a missing one.
    entry = {"clause_ref": "6.4", "text": "At least 3 black starts",
             "category": "technical", "checkability": "auto",
             "parameter": "black_starts", "operator": ">=", "value": 3, "unit": ""}
    [record], status, _ = extract_requirements(
        "d1", _spec(tmp_path), StubClient({"requirements": [entry]}))
    assert (status, record.checkability, record.unit) == ("ok", "auto", "")


def test_an_unknown_operator_demotes_rather_than_storing_it(tmp_path):
    entry = dict(_TWO_CLAUSES["requirements"][0], operator="approximately")
    [record], _, _ = extract_requirements(
        "d1", _spec(tmp_path), StubClient({"requirements": [entry]}))
    assert record.checkability == "judgement" and record.operator is None


def test_a_clause_with_no_text_is_dropped(tmp_path):
    entry = {"clause_ref": "4.2.7", "text": "   ", "checkability": "judgement"}
    records, status, _ = extract_requirements(
        "d1", _spec(tmp_path), StubClient({"requirements": [entry]}))
    assert (records, status) == ([], "ok")


def test_a_clause_with_no_clause_ref_still_stores_with_a_synthesised_ref(tmp_path):
    entry = {"clause_ref": None, "text": "Painting to manufacturer standard",
             "checkability": "judgement"}
    [record], status, _ = extract_requirements(
        "d1", _spec(tmp_path), StubClient({"requirements": [entry]}))
    assert status == "ok" and record.clause_ref
    # the id must still be reproducible from the same input
    assert record.req_id == req_id_for("d1", record.clause_ref)


def test_one_malformed_entry_does_not_discard_the_others(tmp_path):
    payload = {"requirements": [
        {"clause_ref": "4.2.7", "text": ["not", "a", "string"]},
        _TWO_CLAUSES["requirements"][1],
    ]}
    records, status, _ = extract_requirements("d1", _spec(tmp_path),
                                              StubClient(payload))
    assert status == "ok" and len(records) == 1
    assert records[0].clause_ref == "9.1"


def test_an_omitted_requirements_key_is_an_empty_extraction_not_a_failure(tmp_path):
    records, status, notes = extract_requirements("d1", _spec(tmp_path),
                                                  StubClient({}))
    assert (records, status, notes) == ([], "ok", None)


def test_a_provider_failure_returns_failed_with_a_reason_and_does_not_raise(tmp_path):
    records, status, notes = extract_requirements(
        "d1", _spec(tmp_path), StubClient({}, raises=True))
    assert (records, status) == ([], "failed")
    assert "provider unavailable" in notes


def test_an_unreadable_docx_is_recorded_as_failed_never_extracted_from_mojibake(tmp_path):
    """The whole point of the .docx branch. Before it, a Word file fell through
    to a UTF-8 raw read of a ZIP container, and the resulting mojibake reached
    the model as if it were clause text - status `ok`, garbage requirements.
    An unreadable one must reach the store as `failed` with a stated reason."""
    bad = tmp_path / "MR-4471.docx"
    bad.write_bytes(b"not a real docx")
    client = StubClient(_TWO_CLAUSES)

    records, status, notes = extract_requirements("d1", str(bad), client)

    assert (records, status) == ([], "failed")
    assert "docx" in notes.lower()
    assert client.calls == []  # the model was never asked to read garbage


def test_a_word_requisition_reaches_the_model_as_text_including_table_clauses(tmp_path):
    """Paragraph *and* table clauses must be in the context the extractor sends;
    dropping tables would be a quieter version of the same bug."""
    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "rfq_clauses.docx")
    client = StubClient(_TWO_CLAUSES)

    _, status, notes = extract_requirements("d1", fixture, client)

    assert (status, notes) == ("ok", None)
    context = client.calls[0]["context_text"]
    assert "H2S tolerance shall be at least 50 ppm" in context
    assert "Rated output shall be at least 1500" in context


def test_duplicate_clause_refs_in_one_document_do_not_collide_silently(tmp_path):
    payload = {"requirements": [
        {"clause_ref": "4.2.7", "text": "first statement", "checkability": "judgement"},
        {"clause_ref": "4.2.7", "text": "second statement", "checkability": "judgement"},
    ]}
    records, status, _ = extract_requirements("d1", _spec(tmp_path),
                                              StubClient(payload))
    assert status == "ok"
    assert len({r.req_id for r in records}) == len(records)


def test_a_stated_range_is_stored_with_the_between_operator(tmp_path):
    entry = {"clause_ref": "1.1", "text": "Temperature 5-58 deg C",
             "category": "technical", "checkability": "auto",
             "parameter": "ambient_design_temp", "operator": "between",
             "value": [5, 58], "unit": "degC"}
    [record], status, _ = extract_requirements(
        "d1", _spec(tmp_path), StubClient({"requirements": [entry]}))
    assert (status, record.checkability) == ("ok", "auto")
    assert record.operator == "between" and record.value == [5, 58]


@pytest.mark.parametrize("bad", [5, [5], [1, 2, 3], "5-58"])
def test_a_between_clause_without_exactly_two_bounds_is_demoted(tmp_path, bad):
    # INV-2 again: a range missing an end is a half-stated bound, and
    # compliance.py must never be handed one
    entry = {"clause_ref": "1.1", "text": "Temperature range", "category": "technical",
             "checkability": "auto", "parameter": "ambient_design_temp",
             "operator": "between", "value": bad, "unit": "degC"}
    [record], status, _ = extract_requirements(
        "d1", _spec(tmp_path), StubClient({"requirements": [entry]}))
    assert (status, record.checkability, record.operator) == ("ok", "judgement", None)


def test_a_two_member_list_under_in_is_left_as_membership(tmp_path):
    # `in` and `between` are decided by the model, never inferred from shape:
    # "50 or 60 Hz" is a two-member list and is not a range
    entry = {"clause_ref": "3.1", "text": "Frequency 50 or 60 Hz",
             "category": "technical", "checkability": "auto",
             "parameter": "frequency", "operator": "in", "value": [50, 60],
             "unit": "Hz"}
    [record], _, _ = extract_requirements(
        "d1", _spec(tmp_path), StubClient({"requirements": [entry]}))
    assert record.operator == "in" and record.value == [50, 60]


def test_the_prompt_version_is_the_prompt_filename():
    assert REQUIREMENTS_PROMPT_VERSION == "requirements_v3"


def test_the_prompt_tells_the_model_which_operator_a_limit_takes():
    # the prompt is the fix here, so the prompt is what the test inspects
    from procurement.extract_requirements import _PROMPT
    text = _PROMPT.read_text(encoding="utf-8").lower()
    assert "maximum" in text and "minimum" in text
    assert "<=" in text and ">=" in text
    # v2 already said all of the above. What it never said - and what the live
    # run's false FAILs came from - is which operator a stated limit takes and
    # what == is reserved for.
    assert "up to" in text
    assert "== only when" in text


def test_a_maximum_clause_extracted_as_a_limit_is_stored_as_one(tmp_path):
    entry = {"clause_ref": "2.5.1", "text": "H2S content up to 700 ppm",
             "category": "technical", "checkability": "auto",
             "parameter": "h2s_content", "operator": "<=", "value": 700,
             "unit": "ppm"}
    [record], status, _ = extract_requirements(
        "d1", _spec(tmp_path), StubClient({"requirements": [entry]}))
    assert (status, record.operator, record.value) == ("ok", "<=", 700)


def test_stored_auto_requirements_all_carry_four_bounds(tmp_path):
    # INV-2, asserted over a loaded snapshot rather than over return values
    root = str(tmp_path / "projects")
    create_project(root, "P")
    entry_missing = dict(_TWO_CLAUSES["requirements"][0], unit=None)
    payload = {"requirements": [_TWO_CLAUSES["requirements"][0], entry_missing,
                                _TWO_CLAUSES["requirements"][1]]}
    records, _, _ = extract_requirements("d1", _spec(tmp_path), StubClient(payload))
    snapshots.save_requirements(root, "p", RequirementSet(requirements=records))
    for r in snapshots.load_requirements(root, "p").requirements:
        if r.checkability == "auto":
            assert r.parameter and r.operator and r.value is not None and r.unit is not None


def test_chunks_never_split_a_line(tmp_path):
    text = "\n".join(f"Table 1 | A{i}=4.{i} | B{i}=param | C{i}={i} | D{i}=kW"
                     for i in range(1, 40))
    parts = _chunks(text, budget=200)
    assert len(parts) > 1
    assert "\n".join(parts) == text          # lossless and in order
    for part in parts:
        for line in part.splitlines():
            assert line in text.splitlines()


def test_chunking_is_deterministic():
    text = "\n".join(f"line {i}" for i in range(200))
    assert _chunks(text, 500) == _chunks(text, 500)


def test_every_chunk_is_asked_and_the_results_merge(tmp_path):
    src = tmp_path / "mr.txt"
    # 700 lines guarantees 3 chunks under the default 8000-char budget (the
    # 400-line fixture used elsewhere in this file only forces 2).
    src.write_text("\n".join(f"clause {i} body text here" for i in range(700)),
                   encoding="utf-8")
    client = MockLLMClient([
        {"requirements": [{"clause_ref": "1.1", "text": "first"}]},
        {"requirements": [{"clause_ref": "2.2", "text": "second"}]},
        {"requirements": [{"clause_ref": "3.3", "text": "third"}]},
    ])
    records, status, notes = extract_requirements("d1", str(src), client)
    assert status == "ok" and notes is None
    assert [r.clause_ref for r in records] == ["1.1", "2.2", "3.3"]
    assert len(client.calls) == 3


def test_one_failing_chunk_fails_the_whole_extraction(tmp_path):
    src = tmp_path / "mr.txt"
    src.write_text("\n".join(f"clause {i} body text here" for i in range(400)),
                   encoding="utf-8")

    class SecondChunkFails(MockLLMClient):
        def classify_structure(self, prompt, output_schema, context_text, images=None):
            super().classify_structure(prompt, output_schema, context_text, images)
            if len(self.calls) == 2:
                raise RuntimeError("provider unavailable")
            return {"requirements": [{"clause_ref": "1.1", "text": "first"}]}

    records, status, notes = extract_requirements(
        "d1", str(src), SecondChunkFails({}))
    # a partial merge would delete the clauses the missing chunks carry
    assert (records, status) == ([], "failed")
    assert "provider unavailable" in notes


def test_a_clause_repeated_across_chunks_is_stored_once(tmp_path):
    src = tmp_path / "mr.txt"
    src.write_text("\n".join(f"clause {i} body" for i in range(400)), encoding="utf-8")
    same = {"requirements": [{"clause_ref": "4.2.7", "text": "H2S up to 700 ppm"}]}
    client = MockLLMClient([same, same, same])
    records, status, _ = extract_requirements("d1", str(src), client)
    assert status == "ok"
    assert len(records) == 1


def test_position_stays_global_across_a_chunk_boundary_for_unreferenced_clauses(tmp_path):
    # req_id_for falls back to f"#{position}" when a clause prints no reference.
    # If the merge loop's position counter were per-chunk instead of global
    # over the merged raw_items list, this clause would collide with an
    # earlier unreferenced clause and every stored override/verdict keyed on
    # the resulting req_id would be orphaned the next time the chunk budget
    # changed. This is the property every other multi-chunk test in this file
    # leaves unpinned, because they all use explicit clause_refs.
    src = tmp_path / "mr.txt"
    # range(400) at this line shape produces exactly 2 chunks under the
    # default 8000-char budget (7975 + 2313 chars), confirmed empirically -
    # the same fixture already reused by the tests above.
    src.write_text("\n".join(f"clause {i} body text here" for i in range(400)),
                   encoding="utf-8")
    client = MockLLMClient([
        {"requirements": [{"clause_ref": "1.1", "text": "first stated clause"},
                          {"clause_ref": "", "text": "alpha body distinct one"}]},
        {"requirements": [{"clause_ref": "", "text": "beta body distinct two"}]},
    ])
    records, status, notes = extract_requirements("d1", str(src), client)
    assert status == "ok" and notes is None
    assert len(client.calls) == 2          # genuinely two chunks, not one

    by_text = {r.text: r for r in records}
    alpha = by_text["alpha body distinct one"]
    beta = by_text["beta body distinct two"]

    # alpha is the 2nd item overall (after "1.1" at position 1).
    assert alpha.clause_ref == "#2"
    assert alpha.req_id == req_id_for("d1", "#2")

    # beta is the 1st item of the 2nd chunk but the 3rd item overall. A
    # per-chunk counter would reset here and hand it "#1" - colliding with
    # whatever the first chunk's own "#1" would have been - instead of
    # continuing the count from the first chunk.
    assert beta.clause_ref == "#3"
    assert beta.req_id == req_id_for("d1", "#3")
