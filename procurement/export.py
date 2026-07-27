import io
import csv
import openpyxl
from procurement.models import ComparisonTable

_HEADERS = ["vendor", "currency", "raw_base_price", "normalized_total",
            "delivery_terms", "delivery_time", "payment_terms", "engine_make",
            "extraction_status"]


def comparison_to_rows(table: ComparisonTable) -> list[dict]:
    return [{h: getattr(r, h) for h in _HEADERS} for r in table.rows]


def comparison_to_csv_str(table: ComparisonTable) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_HEADERS)
    writer.writeheader()
    for row in comparison_to_rows(table):
        writer.writerow(row)
    return buf.getvalue()


def comparison_to_xlsx_bytes(table: ComparisonTable) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Comparison"
    ws.append(_HEADERS)
    for row in comparison_to_rows(table):
        ws.append([row[h] for h in _HEADERS])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
