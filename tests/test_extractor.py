import openpyxl
from tests.fixtures.make_mini_boq import make
from cost_estimation.config.loader import load_config
from cost_estimation.ingestion.extractor import ingest_workbook
from shared.llm.mock_client import MockLLMClient


def test_ingest_workbook_builds_workpackage(tmp_path):
    path = make(str(tmp_path / "COSTING mini Electrical.xlsx"))
    client = MockLLMClient(response={"header_row": 7, "columns": {
        "code": 2, "description": 3, "uom": 5, "quantity": 6, "unit_price": 11, "total": 12}})
    doc, packages = ingest_workbook(path, client, load_config())

    assert doc.doc_type.value == "costing_workbook"
    assert len(packages) == 1
    wp = packages[0]
    assert wp.discipline == "electrical"
    assert len(wp.cost_items) == 2


def test_ingest_workbook_attaches_summary_rollup(tmp_path):
    """End-to-end: ingest_workbook must locate a 'Summary' sheet, parse it,
    and attach the matching SummaryRollup onto the work package it built —
    exercising the wiring in extractor.py itself, not just parse_summary/
    attach_rollups in isolation."""
    path = str(tmp_path / "COSTING mini with Summary.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sect. 3 D - Electrical"
    ws["B7"] = "Item"; ws["C7"] = "Description"; ws["E7"] = "UoM"
    ws["F7"] = "Total Quantity (Q)"; ws["K7"] = "Unit Price"; ws["L7"] = "TOTAL"
    ws["B8"] = "E.05.01"; ws["C8"] = "LV cable"; ws["E8"] = "m"
    ws["F8"] = 10; ws["K8"] = 5.0; ws["L8"] = 50.0

    summary = wb.create_sheet("Summary")
    summary["E2"] = "Total Manhours"; summary["F2"] = "Materials"; summary["G2"] = "Consumables"
    summary["H2"] = "Installation"; summary["J2"] = "Total Value (USD)"
    # Label matches the data sheet's title, so attach_rollups matches by
    # normalized name (mirrors _mini_summary in tests/test_summary_parser.py).
    summary["C3"] = "Sect. 3 D - Electrical"; summary["J3"] = 99999.0
    wb.save(path)

    client = MockLLMClient(response={"header_row": 7, "columns": {
        "code": 2, "description": 3, "uom": 5, "quantity": 6, "unit_price": 11, "total": 12}})
    doc, packages = ingest_workbook(path, client, load_config())

    assert len(packages) == 1
    wp = packages[0]
    assert wp.summary_rollup is not None
    assert wp.summary_rollup.total_value == 99999.0
