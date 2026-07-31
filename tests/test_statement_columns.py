from procurement.pipeline import run_ingestion
from procurement.project import load_project, save_project
from procurement.statement import build_statement
from procurement.store import snapshots

from tests.test_pipeline_rfq import RfqClient
from tests.test_pipeline_vocabulary import _project


def _row(statement, key):
    return next(r for r in statement.rows if r.key == key)


def test_columns_follow_the_project_vendor_order(tmp_path):
    root = _project(tmp_path)
    for vendor in ("AESL", "MKON"):
        vdir = tmp_path / "p" / "vendors" / vendor
        vdir.mkdir(parents=True)
        (vdir / "Quotation.txt").write_text("base price 1000", encoding="utf-8")
    project = load_project(root, "p")
    project.vendors = ["MKON", "KERUI", "AESL"]
    save_project(root, project)
    run_ingestion(root, "p", RfqClient())

    assert build_statement(root, "p").vendors == ["MKON", "KERUI", "AESL"]


def test_a_vendor_with_no_facts_gets_a_full_blank_column(tmp_path):
    """INV-S2. Absent, not zero, and present, not missing."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    project = load_project(root, "p")
    project.vendors = ["KERUI", "GHOST"]
    save_project(root, project)

    statement = build_statement(root, "p")
    assert statement.vendors == ["KERUI", "GHOST"]
    for row in statement.rows:
        cell = row.cells.get("GHOST")
        assert cell is None or (cell.total is None and cell.text is None)


def test_the_text_rows_come_from_the_commercial_block(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    statement = build_statement(root, "p")

    assert _row(statement, "engine_make").kind == "text"
    assert [r.key for r in statement.rows if r.kind == "text"] == [
        "engine_make", "delivery_time", "delivery_terms", "payment_terms",
        "quotation_file", "technical_feedback"]


def test_the_quotation_file_row_names_the_live_document(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    statement = build_statement(root, "p")

    assert _row(statement, "quotation_file").cells["KERUI"].text == "Quotation.txt"
    assert statement.revisions["KERUI"] is None      # no revision label parsed


def test_a_vendor_whose_quotation_failed_shows_the_status_not_zeros(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient(fail_on=("bid",)))
    statement = build_statement(root, "p")

    assert statement.statuses["KERUI"] == "failed"
    assert _row(statement, "engine_make").cells.get("KERUI") is None
