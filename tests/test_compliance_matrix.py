"""The compliance screen's model — read-only, over snapshots written directly.

No LLM client and no Streamlit: `build_matrix` is pure, which is the whole
reason it lives in `procurement/` rather than in the view.

The load-bearing test here is
`test_rewording_every_rationale_changes_no_number`. Spec section 5 forbids the
screen from reading its own verdict prose, and that assertion is what fails the
moment somebody reintroduces string-matching.
"""
import pytest

from procurement.matrix import build_matrix, rows_in_group
from procurement.project import create_project, load_project, save_project
from procurement.store import snapshots
from procurement.store.models import (ComplianceResult, DocumentRecord,
                                      RequirementRecord, RequirementSet,
                                      VendorFacts, req_id_for)

NOW = "2026-07-31T00:00:00+00:00"
_DOC = "d1"


def _requirement(clause, *, auto=True, withdrawn=False, parameter="h2s",
                 operator=">=", value=50, unit="ppm"):
    return RequirementRecord(
        req_id=req_id_for(_DOC, clause), clause_ref=clause,
        text=f"clause {clause}", checkability="auto" if auto else "judgement",
        parameter=parameter if auto else None,
        operator=operator if auto else None,
        value=value if auto else None, unit=unit if auto else None,
        source_doc_id=_DOC, withdrawn=withdrawn)


def _cell(clause, vendor, verdict, *, fact_id=None, rationale="because",
          candidate_fact_ids=()):
    return ComplianceResult(
        req_id=req_id_for(_DOC, clause), vendor=vendor, verdict=verdict,
        fact_id=fact_id, doc_id="d9" if fact_id else None,
        candidate_fact_ids=list(candidate_fact_ids),
        rationale=rationale, evaluated_at=NOW)


def _store(tmp_path, requirements, cells, vendors=("KERUI",)):
    root = str(tmp_path)
    create_project(root, "P")
    project = load_project(root, "p")
    project.vendors = list(vendors)
    save_project(root, project)
    snapshots.save_requirements(root, "p", RequirementSet(requirements=requirements))
    snapshots.save_compliance(root, "p", cells)
    return root


def test_each_verdict_lands_in_its_declared_group(tmp_path):
    verdicts = ["pass", "fail", "deviation", "unanswered", "review"]
    requirements = [_requirement(f"{i}.1", auto=(v != "review"))
                    for i, v in enumerate(verdicts)]
    cells = [_cell(f"{i}.1", "KERUI", v) for i, v in enumerate(verdicts)]
    matrix = build_matrix(_store(tmp_path, requirements, cells), "p")

    got = {row.cells["KERUI"].verdict: row.cells["KERUI"].group
           for row in matrix.rows}
    assert got == {"pass": "matched", "fail": "not_matched",
                   "deviation": "not_matched", "unanswered": "needs_human",
                   "review": "needs_human"}


def test_a_row_joins_a_group_when_any_vendor_is_in_it(tmp_path):
    # KERUI passes, MKON fails: the requirement is still unmet somewhere, so a
    # reviewer must see it on the Not matched worklist.
    requirements = [_requirement("1.1")]
    cells = [_cell("1.1", "KERUI", "pass"), _cell("1.1", "MKON", "fail")]
    matrix = build_matrix(_store(tmp_path, requirements, cells,
                                 vendors=("KERUI", "MKON")), "p")

    assert [r.clause_ref for r in rows_in_group(matrix, "not_matched")] == ["1.1"]
    assert [r.clause_ref for r in rows_in_group(matrix, "matched")] == ["1.1"]
    assert rows_in_group(matrix, "needs_human") == []


def test_unanswered_splits_on_whether_a_fact_was_cited(tmp_path):
    # Deliberately asymmetric - two silent against one refused. With one of
    # each, inverting the split swaps two equal numbers and the assertion still
    # holds, so the test would pass against exactly the defect it exists for.
    requirements = [_requirement(f"{i}.1") for i in range(3)]
    cells = [
        # the vendor never stated it - the real coverage gap
        _cell("0.1", "KERUI", "unanswered", fact_id=None),
        _cell("1.1", "KERUI", "unanswered", fact_id=None),
        # the fact was found and the comparison was refused - our reach
        _cell("2.1", "KERUI", "unanswered", fact_id="f-2"),
    ]
    coverage = build_matrix(_store(tmp_path, requirements, cells), "p").coverage
    assert coverage.unanswered_silent == 2
    assert coverage.unanswered_refused == 1
    assert (coverage.unanswered_silent + coverage.unanswered_refused
            == coverage.by_verdict["unanswered"])


def test_rewording_every_rationale_changes_no_number(tmp_path):
    """Spec section 5: the screen displays prose, it never reads it."""
    requirements = [_requirement(f"{i}.1") for i in range(3)]
    plain = [_cell("0.1", "KERUI", "unanswered", fact_id=None),
             _cell("1.1", "KERUI", "unanswered", fact_id="f-1"),
             _cell("2.1", "KERUI", "pass", fact_id="f-2")]
    before = build_matrix(_store(tmp_path, requirements, plain), "p").coverage

    reworded = [c.model_copy(update={"rationale": "☃ nothing parseable here"})
                for c in plain]
    after = build_matrix(_store(tmp_path / "b", requirements, reworded), "p").coverage
    assert after == before


def test_a_withdrawn_requirement_leaves_the_screen(tmp_path):
    requirements = [_requirement("1.1"), _requirement("2.1", withdrawn=True)]
    cells = [_cell("1.1", "KERUI", "pass")]
    matrix = build_matrix(_store(tmp_path, requirements, cells), "p")
    assert [r.clause_ref for r in matrix.rows] == ["1.1"]


def test_a_vendor_with_no_stored_facts_still_gets_a_column(tmp_path):
    # MKON's extraction produced nothing, so no cell was stored for it. It must
    # not vanish from the matrix - a missing column reads as "not offered".
    requirements = [_requirement("1.1")]
    cells = [_cell("1.1", "KERUI", "pass")]
    matrix = build_matrix(_store(tmp_path, requirements, cells,
                                 vendors=("KERUI", "MKON")), "p")

    assert matrix.vendors == ["KERUI", "MKON"]
    [row] = matrix.rows
    assert set(row.cells) == {"KERUI", "MKON"}
    assert row.cells["MKON"].verdict == "unanswered"
    assert row.cells["MKON"].group == "needs_human"
    assert row.cells["MKON"].fact_id is None      # counts as silence, correctly


def test_coverage_counts_only_the_machine_checkable_cells(tmp_path):
    # a percentage over every cell would be dominated by `review` and would
    # mean nothing - spec section 9, risk 2
    requirements = [_requirement("1.1"), _requirement("2.1", auto=False)]
    cells = [_cell("1.1", "KERUI", "pass"), _cell("2.1", "KERUI", "review")]
    coverage = build_matrix(_store(tmp_path, requirements, cells), "p").coverage
    assert coverage.auto_cells == 1
    assert coverage.by_verdict == {"pass": 1}


def test_an_empty_store_is_an_empty_matrix_not_a_crash(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    matrix = build_matrix(root, "p")
    assert (matrix.rows, matrix.vendors) == ([], [])
    assert matrix.coverage.auto_cells == 0


def test_a_multi_reading_cell_carries_its_candidates_into_the_matrix(tmp_path):
    # ADPOWER states continuous_rating twice; the two readings that made the
    # verdict `review` must survive into the matrix cell, not just the prose.
    requirements = [_requirement("1.1", parameter="continuous_rating")]
    cells = [_cell("1.1", "ADPOWER", "review",
                   candidate_fact_ids=["f-a", "f-b"])]
    matrix = build_matrix(_store(tmp_path, requirements, cells,
                                 vendors=("ADPOWER",)), "p")

    row = next(r for r in matrix.rows if r.parameter == "continuous_rating")
    cell = row.cells["ADPOWER"]
    assert cell.verdict == "review"
    assert len(cell.candidate_fact_ids) == 2


def test_a_single_reading_cell_carries_an_empty_candidate_list(tmp_path):
    requirements = [_requirement("1.1", parameter="continuous_rating")]
    cells = [_cell("1.1", "ADPOWER", "pass", fact_id="f-a")]
    matrix = build_matrix(_store(tmp_path, requirements, cells,
                                 vendors=("ADPOWER",)), "p")

    row = next(r for r in matrix.rows if r.parameter == "continuous_rating")
    assert row.cells["ADPOWER"].candidate_fact_ids == []


def test_build_matrix_never_writes(tmp_path):
    requirements = [_requirement("1.1")]
    root = _store(tmp_path, requirements, [_cell("1.1", "KERUI", "pass")])
    before = snapshots.get_generation(root, "p")
    build_matrix(root, "p")
    build_matrix(root, "p")
    assert snapshots.get_generation(root, "p") == before


# ------------------------------------------------- the document behind a cell

def _documents(*specs):
    """(doc_id, path) pairs -> DocumentRecords. `content_sha256` is required by
    the model and irrelevant here."""
    return [DocumentRecord(doc_id=d, path=p, vendor="KERUI",
                           content_sha256=f"sha-{d}") for d, p in specs]


def _facts(*specs):
    """(fact_id, doc_id) pairs -> the technical facts list `load_facts` returns."""
    return VendorFacts(vendor="KERUI", technical=[
        {"fact_id": f, "doc_id": d, "parameter": "h2s", "value": 40,
         "unit": "ppm"} for f, d in specs])


def test_a_cell_names_the_document_its_verdict_was_read_from(tmp_path):
    root = _store(tmp_path, [_requirement("1.1")],
                  [_cell("1.1", "KERUI", "fail", fact_id="f-1")])
    snapshots.save_documents(root, "p", _documents(
        ("d9", "vendors/KERUI/datasheets/gen-datasheet-rev-C.pdf")))

    cell = build_matrix(root, "p").rows[0].cells["KERUI"]
    assert cell.doc_name == "gen-datasheet-rev-C.pdf"


def test_a_cell_with_no_evidence_names_no_document(tmp_path):
    """A judgement row and a silent `unanswered` both cite nothing. Naming a
    document there would attribute the verdict to a file that did not produce
    it."""
    root = _store(tmp_path, [_requirement("1.1")],
                  [_cell("1.1", "KERUI", "unanswered")])
    snapshots.save_documents(root, "p", _documents(("d9", "vendors/KERUI/x.pdf")))

    cell = build_matrix(root, "p").rows[0].cells["KERUI"]
    assert cell.doc_name is None and cell.candidate_doc_names == []


def test_an_unknown_doc_id_names_no_document_rather_than_inventing_one(tmp_path):
    root = _store(tmp_path, [_requirement("1.1")],
                  [_cell("1.1", "KERUI", "fail", fact_id="f-1")])
    snapshots.save_documents(root, "p", _documents(("other", "vendors/KERUI/x.pdf")))

    assert build_matrix(root, "p").rows[0].cells["KERUI"].doc_name is None


def test_readings_from_two_documents_name_both(tmp_path):
    """The `review` this exists for: a parameter stated twice with different
    values. Naming only the first document hides the disagreement that made
    the cell a review in the first place."""
    root = _store(tmp_path, [_requirement("1.1")],
                  [_cell("1.1", "KERUI", "review", fact_id="f-1",
                         candidate_fact_ids=["f-1", "f-2"])])
    snapshots.save_documents(root, "p", _documents(
        ("d9", "vendors/KERUI/datasheet.pdf"),
        ("d7", "vendors/KERUI/technical-offer.pdf")))
    snapshots.save_facts(root, "p", _facts(("f-1", "d9"), ("f-2", "d7")))

    cell = build_matrix(root, "p").rows[0].cells["KERUI"]
    assert cell.candidate_doc_names == ["datasheet.pdf", "technical-offer.pdf"]


def test_two_readings_in_one_document_name_it_once(tmp_path):
    root = _store(tmp_path, [_requirement("1.1")],
                  [_cell("1.1", "KERUI", "review", fact_id="f-1",
                         candidate_fact_ids=["f-1", "f-2"])])
    snapshots.save_documents(root, "p", _documents(("d9", "vendors/KERUI/ds.pdf")))
    snapshots.save_facts(root, "p", _facts(("f-1", "d9"), ("f-2", "d9")))

    assert build_matrix(root, "p").rows[0].cells["KERUI"].candidate_doc_names == ["ds.pdf"]


def test_naming_documents_still_writes_nothing(tmp_path):
    root = _store(tmp_path, [_requirement("1.1")],
                  [_cell("1.1", "KERUI", "fail", fact_id="f-1")])
    snapshots.save_documents(root, "p", _documents(("d9", "vendors/KERUI/x.pdf")))
    snapshots.save_facts(root, "p", _facts(("f-1", "d9")))
    before = snapshots.get_generation(root, "p")
    build_matrix(root, "p")
    assert snapshots.get_generation(root, "p") == before
