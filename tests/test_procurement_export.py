import io
import openpyxl
from procurement.models import ComparisonRow, ComparisonTable
from procurement.export import comparison_to_rows, comparison_to_xlsx_bytes, comparison_to_csv_str


def _table():
    return ComparisonTable(target_currency="USD", rows=[
        ComparisonRow(vendor="B", currency="USD", raw_base_price=1000.0, normalized_total=1000.0,
                      delivery_terms="CIF", delivery_time="24w", payment_terms="LC",
                      engine_make="MAN", extraction_status="ok"),
    ])


def test_rows_and_csv():
    rows = comparison_to_rows(_table())
    assert rows[0]["vendor"] == "B" and rows[0]["normalized_total"] == 1000.0
    csv = comparison_to_csv_str(_table())
    assert "vendor" in csv and "MAN" in csv


def test_xlsx_bytes_roundtrip():
    data = comparison_to_xlsx_bytes(_table())
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb.active
    assert ws["A1"].value == "vendor"
    assert any(cell.value == "B" for row in ws.iter_rows() for cell in row)
