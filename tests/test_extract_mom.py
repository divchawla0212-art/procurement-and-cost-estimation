# tests/test_extract_mom.py
import pytest

from procurement.extract_mom import (MOM_PROMPT_VERSION, apply_amendments,
                                     extract_amendments)
from procurement.store.models import (Amendment, RequirementRecord,
                                      RequirementSet, amendment_id_for,
                                      req_id_for)
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


def _mom(tmp_path):
    p = tmp_path / "MOM.txt"
    p.write_text("Clause 4.2.7 revised: H2S raised to 60 ppm", encoding="utf-8")
    return str(p)


def _requirement(clause="4.2.7", doc="d1", **kw):
    body = dict(clause_ref=clause, text="H2S tolerance at least 50 ppm",
                category="technical", checkability="auto",
                parameter="h2s_tolerance", operator=">=", value=50.0,
                unit="ppm", source_doc_id=doc)
    body.update(kw)
    return RequirementRecord(req_id=req_id_for(doc, clause), **body)


def _amendment(clause="4.2.7", doc="m1", text="H2S raised to 60 ppm", **kw):
    body = dict(clause_ref=clause, text=text, action="modify", source_doc_id=doc,
                parameter="h2s_tolerance", operator=">=", value=60.0, unit="ppm")
    body.update(kw)
    return Amendment(amendment_id=amendment_id_for(doc, clause, text), **body)


_ONE_AMENDMENT = {"amendments": [
    {"clause_ref": "4.2.7", "text": "H2S raised to 60 ppm", "action": "modify",
     "parameter": "h2s_tolerance", "operator": ">=", "value": 60, "unit": "ppm"},
]}


def test_extracts_an_amendment_with_provenance(tmp_path):
    [a], status, notes = extract_amendments("m1", _mom(tmp_path),
                                            StubClient(_ONE_AMENDMENT))
    assert (status, notes) == ("ok", None)
    assert a.source_doc_id == "m1" and a.clause_ref == "4.2.7"
    assert a.req_id is None            # unresolved until apply_amendments runs
    assert (a.action, a.value, a.unit) == ("modify", 60, "ppm")


def test_an_unknown_action_degrades_to_modify_never_to_withdraw(tmp_path):
    payload = {"amendments": [dict(_ONE_AMENDMENT["amendments"][0], action="scrap")]}
    [a], status, _ = extract_amendments("m1", _mom(tmp_path), StubClient(payload))
    assert (status, a.action) == ("ok", "modify")


def test_an_amendment_with_no_text_is_dropped(tmp_path):
    payload = {"amendments": [{"clause_ref": "4.2.7", "text": "  "}]}
    records, status, _ = extract_amendments("m1", _mom(tmp_path), StubClient(payload))
    assert (records, status) == ([], "ok")


def test_one_malformed_amendment_does_not_discard_the_others(tmp_path):
    payload = {"amendments": [{"clause_ref": {"bad": 1}, "text": "x"},
                              _ONE_AMENDMENT["amendments"][0]]}
    records, status, _ = extract_amendments("m1", _mom(tmp_path), StubClient(payload))
    assert status == "ok" and len(records) == 1


def test_an_omitted_amendments_key_is_empty_not_failed(tmp_path):
    assert extract_amendments("m1", _mom(tmp_path), StubClient({})) == ([], "ok", None)


def test_a_provider_failure_returns_failed_with_a_reason(tmp_path):
    records, status, notes = extract_amendments("m1", _mom(tmp_path),
                                                StubClient({}, raises=True))
    assert (records, status) == ([], "failed")
    assert "provider unavailable" in notes


def test_the_prompt_version_is_the_prompt_filename():
    assert MOM_PROMPT_VERSION == "mom_amend_v1"


# --- apply_amendments -------------------------------------------------------

def test_applying_an_amendment_preserves_the_base_body_and_names_both_sides():
    req = _requirement()
    amend = _amendment()
    [resolved], [linked] = apply_amendments([req], [amend])
    assert resolved.value == 60.0 and resolved.unit == "ppm"
    assert resolved.amended_by == amend.amendment_id
    assert resolved.base_body["value"] == 50.0
    assert linked.req_id == req.req_id


def test_applying_twice_is_idempotent():
    once, once_amendments = apply_amendments([_requirement()], [_amendment()])
    twice, twice_amendments = apply_amendments(once, once_amendments)
    assert twice[0].value == 60.0
    assert twice[0].base_body["value"] == 50.0     # not 60.0 - no re-baselining
    # Full-record equality, not just the bound: a text-folding implementation
    # that appends amendment text on every call would still pass a
    # value-only assertion while corrupting `text` on the second pass.
    assert once[0].model_dump() == twice[0].model_dump()
    assert once_amendments[0].model_dump() == twice_amendments[0].model_dump()


def test_removing_the_amendment_reverts_the_requirement_exactly():
    amended, _ = apply_amendments([_requirement()], [_amendment()])
    [reverted], _ = apply_amendments(amended, [])
    assert reverted.value == 50.0 and reverted.unit == "ppm"
    assert reverted.amended_by is None and reverted.base_body is None
    assert reverted.model_dump() == _requirement().model_dump()


def test_a_withdrawing_amendment_marks_the_clause_withdrawn_without_deleting_it():
    [resolved], _ = apply_amendments([_requirement()],
                                     [_amendment(action="withdraw", value=None)])
    assert resolved.withdrawn is True
    assert resolved.text                      # the clause is still readable
    assert resolved.base_body is not None     # and still revertible


def test_an_amendment_matching_no_requirement_is_kept_unapplied():
    req = _requirement(clause="4.2.7")
    [resolved], [linked] = apply_amendments([req], [_amendment(clause="99.9")])
    assert resolved.value == 50.0 and resolved.amended_by is None
    assert linked.req_id is None              # kept, visible, never applied


def test_clause_refs_match_across_printing_differences():
    req = _requirement(clause="4.2.7")
    [resolved], _ = apply_amendments([req], [_amendment(clause=" Clause 4.2.7 ")])
    assert resolved.value == 60.0


def test_an_amendment_that_only_restates_text_leaves_the_bound_alone():
    req = _requirement()
    amend = _amendment(text="wording clarified", parameter=None, operator=None,
                       value=None, unit=None)
    [resolved], _ = apply_amendments([req], [amend])
    assert resolved.value == 50.0 and resolved.unit == "ppm"
    assert resolved.amended_by == amend.amendment_id
    assert "wording clarified" in resolved.text


def test_the_later_of_two_amendments_to_one_clause_wins_deterministically():
    first = _amendment(text="raised to 60 ppm", value=60.0)
    second = _amendment(text="raised again to 70 ppm", value=70.0)
    [a], _ = apply_amendments([_requirement()], [first, second])
    [b], _ = apply_amendments([_requirement()], [first, second])
    assert a.value == b.value == 70.0
    assert a.base_body["value"] == 50.0


def test_stored_amendments_all_carry_provenance(tmp_path):
    # INV-3, asserted over a loaded snapshot
    root = str(tmp_path / "projects")
    create_project(root, "P")
    resolved, linked = apply_amendments([_requirement()],
                                        [_amendment(), _amendment(clause="99.9")])
    snapshots.save_requirements(root, "p", RequirementSet(requirements=resolved,
                                                          amendments=linked))
    loaded = snapshots.load_requirements(root, "p")
    assert all(a.source_doc_id and a.clause_ref for a in loaded.amendments)
    assert len(loaded.amendments) == 2                      # nothing dropped
    live = {r.req_id for r in loaded.requirements}
    assert all(a.req_id is None or a.req_id in live for a in loaded.amendments)
