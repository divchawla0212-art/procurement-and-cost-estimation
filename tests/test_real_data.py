import os
import openpyxl
import pytest
from cost_estimation.config.loader import load_config
from cost_estimation.ingestion.workbook_loader import SheetLayout, read_items
from cost_estimation.ingestion.reconcile import check_item

DATA = "data/cost-estimation-data/Section-3 Schedule of Prices- Additional Tie-in Works.xlsx"


@pytest.mark.skipif(not os.path.exists(DATA), reason="sample data not present")
def test_real_schedule_of_prices_electrical_parses_and_reconciles():
    ws = openpyxl.load_workbook(DATA, data_only=True)["Sect. 3 D - Electrical"]
    layout = SheetLayout(header_row=7, columns={
        "code": 2, "description": 3, "uom": 5, "quantity": 6, "unit_price": 11, "total": 12})
    items = read_items(ws, layout, DATA, "Sect. 3 D - Electrical")

    assert len(items) > 0
    # Blank-priced template: every priced item must still reconcile arithmetically.
    for item in items:
        assert check_item(item) is None
