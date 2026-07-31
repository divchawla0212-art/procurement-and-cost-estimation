"""Render a `Statement` as the one-page HTML sheet a reviewer already reads.

Streamlit's grid cannot merge a cell across the three Qty/Unit/Total
sub-columns, and it gives only two header levels where the reference sheet
has three (vendor · revision · sub-columns). Spec §7 answered that by
splitting into two stacked `st.dataframe`s; this module answers it by
emitting the single merged table instead, which is what the reference sheet
actually is. Nothing here computes: every number arrives from
`build_statement`, already rounded.
"""
import html

from procurement.statement import Statement

# One band per column, cycled. Deliberately assigned by position rather than
# by vendor name: a stable hash of the name would recolour every column the
# moment a vendor is renamed, and position is what the reader scans by.
_BANDS = [("#6b4f1d", "#fffde7"), ("#c62828", "#ffebee"), ("#1b5e20", "#e8f5e9"),
          ("#283593", "#e8eaf6"), ("#6a1b9a", "#f3e5f5"), ("#00695c", "#e0f2f1")]

_SUBS = ("Qty", "Unit Price", "Total Price")

_CSS = """
<style>
.cs-wrap { overflow-x: auto; background: #fff; padding: 10px;
           border-radius: 6px; }
.cs { border-collapse: collapse; font-family: Calibri, Arial, sans-serif;
      font-size: 13px; width: max-content; min-width: 100%; }
/* `color` is not optional. Every background here is a light spreadsheet
   tint, and the host page may be a dark theme whose inherited font colour
   would be near-white — light text on pale yellow is invisible. This sheet
   is a document, not app chrome, so it commits to its own light palette
   rather than following the theme. */
.cs th, .cs td { border: 1px solid #9e9e9e; padding: 3px 8px; white-space: nowrap;
                 color: #1a1a1a; }
.cs .desc { text-align: left; min-width: 260px; position: sticky; left: 0;
            background: #e0e0e0; font-weight: 600; z-index: 2; color: #1a1a1a; }
.cs thead .desc { background: #d5d5d5; }
.cs .vendor { color: #fff; font-weight: 700; text-align: center; letter-spacing: .3px; }
.cs .rev { font-weight: 700; text-align: center; }
.cs .sub { font-weight: 700; text-align: center; background: #fff9c4; }
.cs .num { text-align: right; font-variant-numeric: tabular-nums; }
.cs .mid { text-align: center; }
/* max-width, not just white-space:normal. The table is `width: max-content`
   so it grows to the longest line: without a cap, one vendor's five-line
   payment terms stretch every column off-screen. */
.cs .note { text-align: center; font-style: italic; color: #424242;
            white-space: normal; min-width: 160px; max-width: 340px; }
.cs .final td, .cs .final th { font-weight: 700; font-size: 14px; }
.cs .band td { font-weight: 700; }
.cs .hot { color: #c62828; }
.cs .cool { color: #1a237e; }
.cs .missing { color: #9e9e9e; }
.cs caption { caption-side: top; font-weight: 700; font-size: 18px;
              padding: 6px 0 10px; letter-spacing: .5px; color: #1a1a1a; }
.cs-empty { background: #fff3e0; border: 1px solid #e6a23c; color: #6d4c00;
            padding: 10px 12px; border-radius: 6px; margin-bottom: 10px;
            font-family: Calibri, Arial, sans-serif; font-size: 13px; }
</style>
"""


def _money(value: float | None) -> str:
    """Thousands separators, two decimals, and a dash for nothing. The dash
    is the reference sheet's own convention for a cell that does not apply —
    distinct from a genuinely empty cell, which stays empty."""
    return f"{value:,.2f}" if value is not None else ""


def _qty(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def _cell_text(cell) -> str:
    """The note is composed with the value, never chosen against it. A cell
    carrying `total=None, note="base price not stated"` must show its reason,
    and a tally must not vanish because a reviewer also wrote a note."""
    if cell is None:
        return ""
    parts = [p for p in (cell.text, cell.note) if p]
    return " — ".join(_esc(p) for p in parts)


def _priced_cells(cell) -> str:
    """The three sub-columns of one priced cell.

    A cell with no total but a note spans all three: `included in base price`
    and `base price not stated` are sentences about the row, not numbers in
    the Total column. This is the merge `st.dataframe` cannot express.
    """
    if cell is None:
        return '<td></td><td></td><td></td>'
    if cell.total is None:
        note = _cell_text(cell)
        return (f'<td colspan="3" class="note">{note}</td>' if note
                else '<td></td><td></td><td></td>')
    return (f'<td class="num">{_qty(cell.qty)}</td>'
            f'<td class="num">{_money(cell.unit_price)}</td>'
            f'<td class="num">{_money(cell.total)}</td>')


def _header(statement: Statement) -> str:
    vendors = statement.vendors
    name_row, rev_row, sub_row = [], [], []
    for i, vendor in enumerate(vendors):
        dark, light = _BANDS[i % len(_BANDS)]
        currency = statement.currencies.get(vendor) or statement.currency
        status = statement.statuses.get(vendor)
        flag = "" if status == "ok" else f" ({_esc(status)})"
        name_row.append(f'<th colspan="3" class="vendor" style="background:{dark}">'
                        f'{_esc(vendor)}{flag}</th>')
        # A vendor whose quotation carries no revision label shows nothing,
        # not "Rev.0" — that would assert an original where none was found.
        rev = statement.revisions.get(vendor)
        rev_row.append(f'<th colspan="3" class="rev" style="background:{light}">'
                       f'{_esc(rev) if rev else ""}</th>')
        for sub in _SUBS:
            label = f"{sub}<br>({_esc(currency)})" if sub != "Qty" else sub
            sub_row.append(f'<th class="sub">{label}</th>')
    return (f'<tr><th rowspan="3" class="desc">Description</th>{"".join(name_row)}</tr>'
            f'<tr>{"".join(rev_row)}</tr>'
            f'<tr>{"".join(sub_row)}</tr>')


def _row(statement: Statement, row) -> str:
    vendors = statement.vendors
    classes = ["final"] if row.key == "final_value" else []
    if row.kind == "value":
        classes.append("band")
    cells = []
    for i, vendor in enumerate(vendors):
        _, light = _BANDS[i % len(_BANDS)]
        cell = row.cells.get(vendor)
        if row.kind == "priced":
            body = _priced_cells(cell)
            # style each of the three, so the band survives the colspan case
            cells.append(body.replace('<td', f'<td style="background:{light}"'))
        else:
            tint = "#eeeeee" if row.kind == "value" else light
            text = _cell_text(cell)
            klass = "mid" if row.kind == "value" else "note"
            if row.key == "final_value":
                klass += " cool"
            cells.append(f'<td colspan="3" class="{klass}" '
                         f'style="background:{tint}">{text}</td>')
    css = f' class="{" ".join(classes)}"' if classes else ""
    return f'<tr{css}><th class="desc">{_esc(row.label)}</th>{"".join(cells)}</tr>'


def _final_row(statement: Statement, row) -> str:
    """FINAL VALUE is the row the eye lands on, so it is the one row that
    formats its own cells: the highest column is flagged, because a reviewer
    comparing five columns of seven digits should not have to do it by eye.
    A column with no total is not a candidate — an unknown price is not a
    cheap one, and that is the whole point of blanking it."""
    totals = {v: c.total for v, c in row.cells.items() if c.total is not None}
    # Only when there is something to compare against. A single-vendor
    # project has no "most expensive" column, and colouring its one bid red
    # invents a judgement out of a comparison that was never made.
    highest = max(totals, key=totals.get) if len(totals) > 1 else None
    cells = []
    for i, vendor in enumerate(statement.vendors):
        _, light = _BANDS[i % len(_BANDS)]
        cell = row.cells.get(vendor)
        tone = "hot" if vendor == highest else "cool"
        if cell is None or cell.total is None:
            body = _cell_text(cell)
            cells.append(f'<td colspan="3" class="note" '
                         f'style="background:{light}">{body}</td>')
            continue
        note = f' title="{_esc(cell.note)}"' if cell.note else ""
        mark = " *" if cell.note else ""
        cells.append(f'<td colspan="3" class="num {tone}" '
                     f'style="background:{light}"{note}>'
                     f'{_money(cell.total)}{mark}</td>')
    return (f'<tr class="final"><th class="desc">{_esc(row.label)}</th>'
            f'{"".join(cells)}</tr>')


def statement_to_html(statement: Statement) -> str:
    """The whole sheet, self-contained. Safe to inject: every value that came
    from a vendor document goes through `html.escape`."""
    if not statement.vendors:
        return "<p><em>No vendors in this project yet.</em></p>"

    # A vendor whose quotation yielded no price at all produces no priced
    # cells, so the sheet draws two attribute rows and looks broken. It is
    # not broken — it is refusing to print a zero — but a reader cannot tell
    # those apart from the table alone, so say it in words above the table.
    priced = {v for row in statement.rows if row.kind == "priced"
              for v, c in row.cells.items() if c.total is not None}
    silent = [v for v in statement.vendors if v not in priced]
    banner = ""
    if silent:
        names = ", ".join(_esc(v) for v in silent)
        banner = (f'<div class="cs-empty"><strong>No prices extracted for '
                  f'{names}.</strong> The quotation was read but no base price '
                  f'was found, so every priced row is blank rather than zero. '
                  f'Re-run ingestion, or open the vendor\'s quotation to check '
                  f'it states a price in a form the extractor can read.</div>')

    body = []
    for row in statement.rows:
        if not row.cells and row.kind != "priced":
            continue                    # an empty text row is noise, not data
        body.append(_final_row(statement, row) if row.key == "final_value"
                    else _row(statement, row))
    return (f'{_CSS}{banner}<div class="cs-wrap"><table class="cs">'
            f'<caption>COMPARATIVE STATEMENT — {_esc(statement.project)}</caption>'
            f'<thead>{_header(statement)}</thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')
