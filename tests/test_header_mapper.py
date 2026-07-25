import openpyxl
from tests.fixtures.make_mini_boq import make
from cost_estimation.config.loader import load_config
from cost_estimation.ingestion.header_mapper import sheet_preview, map_sheet
from shared.llm.mock_client import MockLLMClient


def test_sheet_preview_includes_header_tokens(tmp_path):
    path = make(str(tmp_path / "mini.xlsx"))
    ws = openpyxl.load_workbook(path, data_only=True).active
    preview = sheet_preview(ws)
    assert "Total Quantity (Q)" in preview
    assert "R7" in preview


def test_map_sheet_returns_layout_from_client(tmp_path):
    path = make(str(tmp_path / "mini.xlsx"))
    ws = openpyxl.load_workbook(path, data_only=True).active
    canned = {"header_row": 7, "columns": {"code": 2, "quantity": 6, "unit_price": 11, "total": 12}}
    client = MockLLMClient(response=canned)
    layout = map_sheet(ws, client, load_config(), "Sect. 3 D - Electrical")
    assert layout.header_row == 7
    assert layout.columns["quantity"] == 6
    assert "Sect. 3 D - Electrical" not in client.last_call["prompt"]  # prompt is generic
