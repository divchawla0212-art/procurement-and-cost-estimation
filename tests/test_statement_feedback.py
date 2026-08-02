import pathlib
import re

from procurement.compliance import VERDICTS
from procurement.pipeline import run_ingestion
from procurement.project import load_project, save_project
from procurement.statement import build_statement, compliance_tally
from procurement.store import snapshots
from procurement.store.models import ComplianceResult

from tests.test_pipeline_rfq import RfqClient
from tests.test_pipeline_vocabulary import _project, _PAD

_QUOTE = "Quotation.txt"


def _note(root, vendor, text):
    facts = snapshots.load_facts(root, "p", vendor)
    facts.technical_feedback = text
    snapshots.save_facts(root, "p", facts)


def _feedback(root, vendor):
    statement = build_statement(root, "p")
    row = next(r for r in statement.rows if r.key == "technical_feedback")
    return row.cells.get(vendor)


def _store_bytes(root) -> dict[str, bytes]:
    return {str(p): p.read_bytes()
            for p in sorted(pathlib.Path(root).rglob("*")) if p.is_file()}


_RENDERED_TALLY = re.compile(r"\d+\s*(" + "|".join(VERDICTS) + r")", re.I)


def _holds_verdict_counts(value) -> bool:
    """Any mapping from a verdict name to a number, anywhere in the tree,
    OR any string that reads as a rendered tally.

    A substring scan of the dump cannot do this job: `deviations` is already
    a legitimate VendorFacts field, so `"deviation" in str(stored)` is true
    before a single tally is written. But the structural half alone is not
    enough either — a tally cached as the string "1 pass · 1 review", which
    is the tempting shape once a view wants to pre-fill it, is a dict of
    neither verdicts nor ints. Both halves are needed."""
    if isinstance(value, dict):
        if any(k in VERDICTS and isinstance(v, int) for k, v in value.items()):
            return True
        # technical_feedback is a reviewer's prose and may legitimately say
        # "3 pass"; every other field is fair game.
        return any(_holds_verdict_counts(v) for k, v in value.items()
                   if k != "technical_feedback")
    if isinstance(value, list):
        return any(_holds_verdict_counts(v) for v in value)
    return isinstance(value, str) and bool(_RENDERED_TALLY.search(value))


def test_a_note_survives_a_forced_reextraction(tmp_path):
    """INV-S3. Touching the quotation forces run 2 to re-extract KERUI, which
    rebuilds VendorFacts from scratch — the note must be carried, not dropped."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    _note(root, "KERUI", "FULLY COMPLIED")

    (tmp_path / "p" / "vendors" / "KERUI" / _QUOTE).write_text(
        "base price 2000" + _PAD, encoding="utf-8")
    second = RfqClient()
    run_ingestion(root, "p", second)

    # RfqClient's "bid" answer is fixed regardless of context_text (see
    # tests/test_pipeline_rfq.py), so the re-extraction can't be proven by the
    # resulting price the way the brief's draft assumed — proven by call
    # count instead, the same signal test_pipeline_incremental.py uses.
    assert "bid" in second.calls, "the re-extraction did not happen"
    facts = snapshots.load_facts(root, "p", "KERUI")
    assert facts.technical_feedback == "FULLY COMPLIED"


def test_the_facts_record_which_document_priced_them(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())

    facts = snapshots.load_facts(root, "p", "KERUI")
    quote = next(d for d in snapshots.load_documents(root, "p")
                 if d.path.endswith(_QUOTE))
    assert facts.quotation_doc_id == quote.doc_id


def test_a_failed_requotation_keeps_the_note_and_the_link(tmp_path):
    """A provider outage on run 2 must not blank either field."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    _note(root, "KERUI", "FULLY COMPLIED")
    before = snapshots.load_facts(root, "p", "KERUI").quotation_doc_id

    (tmp_path / "p" / "vendors" / "KERUI" / _QUOTE).write_text(
        "base price 2000" + _PAD, encoding="utf-8")
    run_ingestion(root, "p", RfqClient(fail_on=("bid",)))

    facts = snapshots.load_facts(root, "p", "KERUI")
    assert facts.technical_feedback == "FULLY COMPLIED"
    assert facts.quotation_doc_id == before


def test_the_tally_counts_only_verdicts_that_occur():
    """A clean vendor reads `25 pass`, not a row of four zeros beside it."""
    results = [ComplianceResult(req_id="a", vendor="KERUI", verdict="fail",
                                evaluated_at="t"),
               ComplianceResult(req_id="b", vendor="KERUI", verdict="review",
                                evaluated_at="t"),
               ComplianceResult(req_id="c", vendor="MKON", verdict="pass",
                                evaluated_at="t")]

    assert compliance_tally(results, "KERUI") == {"fail": 1, "review": 1}


def test_a_verdict_the_vocabulary_does_not_define_is_counted_apart():
    """Nothing validates `verdict` as an enum, so a typo or a verdict added
    to evaluate() without updating VERDICTS must not be counted into a
    column a reviewer reads as authoritative. Nor may it be dropped: the
    tally's counts have to sum to the requirements actually evaluated, or
    `1 pass` over two evaluated requirements says one was never checked."""
    results = [ComplianceResult(req_id="a", vendor="KERUI", verdict="passed",
                                evaluated_at="t"),
               ComplianceResult(req_id="b", vendor="KERUI", verdict="pass",
                                evaluated_at="t")]

    assert compliance_tally(results, "KERUI") == {"pass": 1, "other": 1}


def test_the_tally_reads_in_the_specs_verdict_order():
    """Not alphabetical, and not the order the results happen to arrive in.
    Encounter order is per-vendor, so one column would read `3 fail · 1 pass`
    beside another's `1 pass · 3 fail` — and the reviewer is comparing
    columns. These three verdicts disagree under all three orderings."""
    results = [ComplianceResult(req_id="a", vendor="K", verdict="review",
                                evaluated_at="t"),
               ComplianceResult(req_id="b", vendor="K", verdict="deviation",
                                evaluated_at="t"),
               ComplianceResult(req_id="c", vendor="K", verdict="pass",
                                evaluated_at="t")]

    assert list(compliance_tally(results, "K")) == ["pass", "deviation", "review"]


def test_the_cell_shows_the_tally_when_no_note_is_written(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())

    cell = _feedback(root, "KERUI")
    assert cell.text is None
    # Exact, not `any(v in note for v in VERDICTS)`: a tally rendered without
    # its counts still contains every verdict name it names.
    assert cell.note == "1 pass · 1 review"


def test_a_note_is_shown_beside_the_tally_not_instead_of_it(tmp_path):
    """A note is a judgement made at one moment; the tally moves under it as
    extractions change. Shown together, a stale note can be seen to be stale."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    _note(root, "KERUI", "FULLY COMPLIED")

    cell = _feedback(root, "KERUI")
    assert cell.text == "FULLY COMPLIED"
    assert cell.note == "1 pass · 1 review"


def test_a_vendor_with_neither_a_note_nor_a_verdict_gets_no_cell(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    # `_project` seeds vendors = ["KERUI"], so MKON must be added to the
    # project or build_statement never visits it and this asserts nothing.
    project = load_project(root, "p")
    project.vendors = list(project.vendors) + ["MKON"]
    save_project(root, project)
    assert not compliance_tally(snapshots.load_compliance(root, "p"), "MKON"), \
        "fixture assumption changed"

    assert _feedback(root, "MKON") is None


def test_a_note_cleared_to_empty_reads_as_absent(tmp_path):
    """Task 7's text area stores "" when a reviewer clears it. The text rows
    already treat blank as unknown; this row must agree, or the cell renders
    an empty judgement beside a real tally."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    _note(root, "KERUI", "")

    cell = _feedback(root, "KERUI")
    assert cell.text is None
    assert cell.note == "1 pass · 1 review"


def test_the_feedback_row_is_labelled_for_the_reader(tmp_path):
    """Task 6 exports `label` as the Description column, so it is
    user-visible text and not an internal name."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())

    row = next(r for r in build_statement(root, "p").rows
               if r.key == "technical_feedback")
    assert row.label == "Technical Feedback"
    assert row.kind == "text"


def test_a_note_alone_is_shown_when_no_requirement_was_evaluated(tmp_path):
    """The reviewer's judgement is not conditional on the matrix existing."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    snapshots.save_compliance(root, "p", [])
    _note(root, "KERUI", "FULLY COMPLIED")

    cell = _feedback(root, "KERUI")
    assert cell.text == "FULLY COMPLIED"
    assert cell.note is None


def test_no_tally_is_ever_stored(tmp_path):
    """INV-S4. Phase 3 recomputes compliance.json wholesale every run exactly
    so the matrix cannot drift from its sources; copying counts into
    facts.json would reintroduce that drift one level down."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    assert compliance_tally(snapshots.load_compliance(root, "p"), "KERUI"), \
        "the fixture proves nothing if the vendor has no verdicts at all"

    # Every byte under the project root, not just `generation`: a write that
    # skips the transaction leaves generation untouched, which is exactly the
    # shape a tally silently persisted by the builder would take.
    before = _store_bytes(root)
    build_statement(root, "p")

    assert _store_bytes(root) == before
    stored = snapshots.load_facts(root, "p", "KERUI").model_dump()
    assert not _holds_verdict_counts(stored)
    assert "pass" not in str(stored.get("technical_feedback") or "")
