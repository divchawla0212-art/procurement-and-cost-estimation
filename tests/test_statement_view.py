import os

import pytest
from streamlit.testing.v1 import AppTest

from portal.views.statement import save_feedback
from procurement.pipeline import run_ingestion
from procurement.render import statement_to_html
from procurement.statement import build_statement
from procurement.store import events, snapshots

from tests.test_pipeline_rfq import RfqClient
from tests.test_pipeline_vocabulary import _project

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "portal", "app.py")


def test_saving_a_note_bumps_the_generation_once(tmp_path):
    """INV-S5."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    before = snapshots.get_generation(root, "p")

    save_feedback(root, "p", "KERUI", "FULLY COMPLIED", "checked against MR 4.2.7")

    assert snapshots.get_generation(root, "p") == before + 1
    assert snapshots.load_facts(root, "p", "KERUI").technical_feedback == "FULLY COMPLIED"


def test_saving_a_note_records_one_event_carrying_the_reason(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())

    save_feedback(root, "p", "KERUI", "FULLY COMPLIED", "checked against MR 4.2.7")

    edits = [e for e in events.read_events(root, "p")
             if e.action == "facts.feedback_edited"]
    assert len(edits) == 1
    assert edits[0].target == "KERUI"
    assert edits[0].detail["reason"] == "checked against MR 4.2.7"


def test_a_note_without_a_reason_is_refused(tmp_path):
    """The reason is what makes the note auditable; a blank one is refused
    before anything is written, not after."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    before = snapshots.get_generation(root, "p")

    with pytest.raises(ValueError):
        save_feedback(root, "p", "KERUI", "FULLY COMPLIED", "   ")

    assert snapshots.get_generation(root, "p") == before
    assert snapshots.load_facts(root, "p", "KERUI").technical_feedback is None


def test_the_rendered_sheet_escapes_vendor_supplied_text(tmp_path):
    """Every value on this page came out of a vendor's document and is
    injected with unsafe_allow_html. A quotation whose payment terms contain
    a tag must not become markup."""
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


def test_the_app_renders_the_statement_after_a_run(tmp_path, monkeypatch):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", root)

    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.sidebar.selectbox[0].set_value("p")
    at.run()

    assert not at.exception
    assert any("COMPARATIVE STATEMENT" in str(m.value) for m in at.markdown)


def test_the_app_survives_a_project_with_no_facts(tmp_path, monkeypatch):
    """An empty project must say so, not raise."""
    root = _project(tmp_path)
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", root)

    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.sidebar.selectbox[0].set_value("p")
    at.run()

    assert not at.exception


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
