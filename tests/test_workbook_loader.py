import openpyxl
from tests.fixtures.make_mini_boq import make
from cost_estimation.ingestion.workbook_loader import SheetLayout, read_items


def test_read_items_splits_priced_and_blank(tmp_path):
    path = make(str(tmp_path / "mini.xlsx"))
    ws = openpyxl.load_workbook(path, data_only=True)["Sect. 3 D - Electrical"]
    layout = SheetLayout(header_row=7, columns={"code": 2, "description": 3, "uom": 5, "quantity": 6, "unit_price": 11, "total": 12})
    items = read_items(ws, layout, path, "Sect. 3 D - Electrical")

    assert len(items) == 2  # SUBTOTAL row skipped (no code)
    priced = next(i for i in items if i.code == "E.05.01")
    assert priced.priced is True
    assert priced.total == 50.0
    assert priced.rate_buildup.unit_price == 5.0
    assert priced.provenance.cell == "B8"

    blank = next(i for i in items if i.code == "E.05.02")
    assert blank.priced is False
    assert blank.rate_buildup is None
    assert blank.quantity == 20
