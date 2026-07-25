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
