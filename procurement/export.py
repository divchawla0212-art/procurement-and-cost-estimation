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


def _statement_value(cell) -> str:
    """One rule, one place. The note is composed with the value, never chosen
    against it: `cell.text or cell.note` would drop the compliance tally for
    every vendor that also has a reviewer's note — the exact pairing the
    statement exists to show — and would strip FINAL VALUE's "base price not
    stated" off a blank cell, leaving an empty total with no reason."""
    if cell is None:
        return ""
    parts = []
    if cell.total is not None:
        parts.append(f"{cell.total:,.2f}")
    parts.extend(p for p in (cell.text, cell.note) if p)
    return " — ".join(parts)


def statement_to_rows(statement) -> list[dict]:
    """Flat rows for CSV/XLSX: Description plus one column per vendor.

    The three Qty/Unit/Total sub-columns collapse to the total here — the
    export is a record of the comparison, and a spreadsheet reader can open
    the source quotation for the breakdown.
    """
    rows = []
    for row in statement.rows:
        if not row.cells:
            continue
        out = {"Description": row.label}
        for vendor in statement.vendors:
            out[vendor] = _statement_value(row.cells.get(vendor))
        rows.append(out)
    return rows


def statement_to_csv_str(statement) -> str:
    rows = statement_to_rows(statement)
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def statement_to_xlsx_bytes(statement) -> bytes:
    rows = statement_to_rows(statement)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Comparative Statement"
    if rows:
        headers = list(rows[0].keys())
        ws.append(headers)
        for row in rows:
            ws.append([row[h] for h in headers])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
