"""`statement_to_html` — the comparative statement sheet.

These were in `test_statement_view.py` while the Streamlit portal existed, but
none of them ever exercised the portal: they render `procurement/render.py`
directly. They are kept under their own name so removing a front end cannot
take the renderer's coverage with it — the same HTML now reaches the reviewer
through the React statement page and the xlsx/csv exports.
"""
from procurement.pipeline import run_ingestion
from procurement.render import statement_to_html
from procurement.statement import build_statement
from procurement.store import snapshots

from tests.test_pipeline_rfq import RfqClient
from tests.test_pipeline_vocabulary import _project


def test_the_rendered_sheet_escapes_vendor_supplied_text(tmp_path):
    """Every value on this page came out of a vendor's document. A quotation
    whose payment terms contain a tag must not become markup."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    facts = snapshots.load_facts(root, "p", "KERUI")
    facts.commercial["payment_terms"] = "<script>alert(1)</script>"
    snapshots.save_facts(root, "p", facts)

    html = statement_to_html(build_statement(root, "p"))

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_the_sheet_renders_every_row_the_statement_carries(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())

    statement = build_statement(root, "p")
    html = statement_to_html(statement)

    assert "COMPARATIVE STATEMENT" in html
    assert "FINAL VALUE" in html
    for vendor in statement.vendors:
        assert vendor in html


def test_a_project_with_no_vendors_renders_a_message_not_a_table(tmp_path):
    root = _project(tmp_path)

    from procurement.project import load_project, save_project
    project = load_project(root, "p")
    project.vendors = []
    save_project(root, project)

    assert "<table" not in statement_to_html(build_statement(root, "p"))


def test_a_lone_bid_is_not_coloured_as_the_expensive_one(tmp_path):
    """The red flag on FINAL VALUE means "highest of the columns shown". With
    one column there is no comparison, so there is no judgement to render."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())

    assert 'class="num hot"' not in statement_to_html(build_statement(root, "p"))


def test_a_vendor_with_no_extracted_price_is_named_above_the_table(tmp_path):
    """A column with no priced cells draws two attribute rows and reads as a
    broken screen. It is not broken -- it is refusing to print a zero -- but
    only words above the table can say which."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    facts = snapshots.load_facts(root, "p", "KERUI")
    facts.commercial["base_price"] = 0.0
    snapshots.save_facts(root, "p", facts)

    html = statement_to_html(build_statement(root, "p"))

    assert "No prices extracted for KERUI" in html


def test_the_sheet_sets_its_own_text_colour(tmp_path):
    """Every background here is a light spreadsheet tint. Inheriting a dark
    theme's near-white font colour makes the whole sheet unreadable, which is
    not a detail the tests can afford to leave to the host page."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())

    assert "color: #1a1a1a" in statement_to_html(build_statement(root, "p"))
