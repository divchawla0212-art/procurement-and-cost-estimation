import pytest

from procurement.extract_requirements import (REQUIREMENTS_PROMPT_VERSION,
                                              extract_requirements)
from procurement.store.models import RequirementSet, req_id_for
from procurement.store import snapshots
from procurement.project import create_project


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


def test_duplicate_clause_refs_in_one_document_do_not_collide_silently(tmp_path):
    payload = {"requirements": [
        {"clause_ref": "4.2.7", "text": "first statement", "checkability": "judgement"},
        {"clause_ref": "4.2.7", "text": "second statement", "checkability": "judgement"},
    ]}
    records, status, _ = extract_requirements("d1", _spec(tmp_path),
                                              StubClient(payload))
    assert status == "ok"
    assert len({r.req_id for r in records}) == len(records)


def test_the_prompt_version_is_the_prompt_filename():
    assert REQUIREMENTS_PROMPT_VERSION == "requirements_v1"


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
