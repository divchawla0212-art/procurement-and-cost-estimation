"""Render a `Comparison` as the marked-up matrix a reviewer scans.

Green means the reviewer recorded "Confirmed" against that vendor for that line
item. Yellow means anything else — a furnished value, a qualified yes, a
refusal, or silence. Yellow is not a finding; it is the pile that still needs a
human, and each cell keeps the reviewer's own wording so the reader can see
*which* kind of yellow it is.

Nothing here decides a verdict. Every status arrives from the workbook.
"""
import html

from procurement.vendor_comparison import Comparison, MatrixRow

_CSS = """
<style>
.vc-wrap { overflow-x: auto; background: #fff; padding: 12px; border-radius: 6px; }
.vc { border-collapse: collapse; font-family: Calibri, Arial, sans-serif;
      font-size: 12.5px; width: 100%; table-layout: fixed; }
/* `color` is set on every cell on purpose. The row tints are pale and the host
   page may be a dark theme whose inherited near-white text would be invisible
   on them. This table is a document, so it carries its own light palette. */
.vc th, .vc td { border: 1px solid #9e9e9e; padding: 4px 7px; vertical-align: top;
                 text-align: left; color: #1a1a1a; word-wrap: break-word; }
.vc thead th { background: #d5d5d5; font-weight: 700; }
.vc thead th.va { background: #cfd8dc; }
.vc thead th.vb { background: #d7ccc8; }
.vc .sl { width: 46px; white-space: nowrap; font-weight: 600; }
.vc .desc { width: 17%; font-weight: 600; }
.vc .req { width: 26%; color: #424242; }
.vc .resp { width: 15%; }
.vc .stat { width: 82px; font-weight: 700; white-space: nowrap; }
.vc .note { width: 13%; font-style: italic; color: #5d4037; }
.vc td.ok { background: #e6f4ea; }
.vc td.review { background: #fff8e1; }
.vc td.none { color: #8d6e00; font-style: italic; }
.vc tr.band td { background: #cfd8dc; font-weight: 700; text-transform: uppercase;
                 letter-spacing: .4px; }
.vc-tally { font-family: Calibri, Arial, sans-serif; font-size: 13px; color: #1a1a1a;
            margin: 0 0 10px; }
.vc-tally b { font-size: 15px; }
.vc-key { display: inline-block; width: 11px; height: 11px; border: 1px solid #9e9e9e;
          vertical-align: -1px; margin-right: 4px; }
</style>
"""


def _cell(text: str) -> str:
    """Escape first, then restore the line breaks the source cell had."""
    return html.escape(text).replace("\n", "<br>")


def _tally_line(comparison: Comparison) -> str:
    parts = [
        f'<span class="vc-key" style="background:#e6f4ea"></span>Complied &nbsp;'
        f'<span class="vc-key" style="background:#fff8e1"></span>Needs review'
    ]
    for i, vendor in enumerate(comparison.vendors):
        complied, total = comparison.tally(i)
        parts.append(f"{html.escape(vendor)}: <b>{complied}</b> / {total} complied")
    return f'<p class="vc-tally">{" &nbsp;&middot;&nbsp; ".join(parts)}</p>'


def _row_html(row: MatrixRow, span: int) -> str:
    if row.is_section:
        label = row.description or row.category
        return (f'<tr class="band"><td class="sl">{_cell(row.sl_no)}</td>'
                f'<td colspan="{span}">{_cell(label)}</td></tr>')

    cells = [f'<td class="sl">{_cell(row.sl_no)}</td>',
             f'<td class="desc">{_cell(row.description)}</td>',
             f'<td class="req">{_cell(row.requirement)}</td>']
    for vc in row.cells:
        tint = "ok" if vc.complies else "review"
        response = (_cell(vc.response) if vc.response.strip()
                    else '<i>no response</i>')
        cells.append(f'<td class="resp {tint}">{response}</td>')
        cells.append(f'<td class="stat {tint}">{_cell(vc.status)}</td>')
    cells.append(f'<td class="note">{_cell(row.notes)}</td>')
    return f'<tr>{"".join(cells)}</tr>'


def comparison_to_html(comparison: Comparison) -> str:
    span = 2 + 2 * len(comparison.vendors) + 1
    head = ['<th class="sl">SL</th>', '<th class="desc">Description</th>',
            '<th class="req">Project requirement</th>']
    for i, vendor in enumerate(comparison.vendors):
        klass = "va" if i % 2 == 0 else "vb"
        head.append(f'<th class="resp {klass}">{html.escape(vendor)}</th>')
        head.append(f'<th class="stat {klass}">Status</th>')
    head.append('<th class="note">Notes</th>')

    rows = "\n".join(_row_html(r, span) for r in comparison.rows)
    return "\n".join([
        _CSS,
        _tally_line(comparison),
        '<div class="vc-wrap"><table class="vc">',
        f'<thead><tr>{"".join(head)}</tr></thead>',
        f"<tbody>{rows}</tbody>",
        "</table></div>",
    ])


def comparison_to_page(comparison: Comparison) -> str:
    """The same table as a standalone file, for download.

    Self-contained on purpose: the downloaded sheet gets circulated, and a
    stylesheet reference back to the portal would render it unreadable on any
    machine that cannot reach it.
    """
    return "\n".join([
        "<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Vendor compliance comparison</title>",
        "</head>",
        '<body style="margin:0;padding:18px;background:#fff">',
        comparison_to_html(comparison),
        "</body></html>",
    ])
