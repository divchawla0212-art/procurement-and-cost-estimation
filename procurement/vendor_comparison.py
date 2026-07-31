"""Read a prepared vendor-comparison workbook — no extraction, no store.

This deliberately sits outside the pipeline. The workbook is already the
reviewed artefact: a human mapped each MR line item to each vendor's response
and recorded a verdict per vendor. Re-deriving any of that with an LLM would
replace a reviewed judgement with a guess, so this module only reads.

Nothing here decides compliance and nothing here computes a score. The
`status` on a row is the reviewer's word, carried through verbatim; the tallies
are counts of those words, which is arithmetic Python may do.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

MATRIX_SHEET = "Compliance Matrix"
SUMMARY_SHEET = "Executive Summary"

# The one verdict that reads as green. Everything else — a furnished value, a
# qualified yes, a refusal, silence — is the pile that still needs a human, and
# the reviewer's own wording for it is preserved rather than collapsed.
COMPLIED = "Confirmed"

# Column order in `Compliance Matrix`, addressed by header text rather than by
# position so an inserted column does not silently shift every verdict one
# vendor to the left.
_HEADERS = ("SL No", "Category", "Description", "Project Requirement")


@dataclass
class VendorCell:
    vendor: str
    response: str
    status: str

    @property
    def complies(self) -> bool:
        return self.status.strip().lower() == COMPLIED.lower()


@dataclass
class MatrixRow:
    sl_no: str
    category: str
    description: str
    requirement: str
    cells: list[VendorCell] = field(default_factory=list)
    notes: str = ""

    @property
    def is_section(self) -> bool:
        """A band row: the reviewer left every vendor verdict blank."""
        return not any(c.status.strip() for c in self.cells)


@dataclass
class Comparison:
    title: str
    subtitle: str
    vendors: list[str]
    rows: list[MatrixRow]
    summary: list[str] = field(default_factory=list)

    @property
    def items(self) -> list[MatrixRow]:
        return [r for r in self.rows if not r.is_section]

    def tally(self, vendor_index: int) -> tuple[int, int]:
        """(complied, total scorable) for one vendor."""
        items = self.items
        return sum(1 for r in items if r.cells[vendor_index].complies), len(items)


def _clean(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def load_comparison(path: str) -> Comparison:
    """Load the workbook. Raises if it is not a comparison workbook."""
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        if MATRIX_SHEET not in wb.sheetnames:
            raise ValueError(f"{os.path.basename(path)} has no '{MATRIX_SHEET}' sheet")
        grid = [list(r) for r in wb[MATRIX_SHEET].iter_rows(values_only=True)]
        summary = _read_summary(wb)
    finally:
        wb.close()

    header_at = _find_header(grid)
    if header_at is None:
        raise ValueError(f"{os.path.basename(path)} has no matrix header row")

    header = [_clean(c) for c in grid[header_at]]
    vendors, pairs = _vendor_columns(header)
    notes_at = header.index("Notes") if "Notes" in header else None

    rows = []
    for raw in grid[header_at + 1:]:
        values = [_clean(c) for c in raw]
        if not any(values):
            continue
        rows.append(MatrixRow(
            sl_no=_at(values, 0), category=_at(values, 1),
            description=_at(values, 2), requirement=_at(values, 3),
            cells=[VendorCell(vendors[i], _at(values, resp), _at(values, stat))
                   for i, (resp, stat) in enumerate(pairs)],
            notes=_at(values, notes_at) if notes_at is not None else ""))

    title = _clean(grid[0][0]) if grid else ""
    subtitle = _clean(grid[1][0]) if len(grid) > 1 else ""
    return Comparison(title=title, subtitle=subtitle, vendors=vendors,
                      rows=rows, summary=summary)


def _at(values: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(values):
        return ""
    return values[idx]


def _find_header(grid: list[list]) -> int | None:
    for i, row in enumerate(grid):
        cells = [_clean(c) for c in row]
        if all(h in cells for h in _HEADERS):
            return i
    return None


def _vendor_columns(header: list[str]) -> tuple[list[str], list[tuple[int, int]]]:
    """Pair each '<vendor> Response' column with its '<letter> Status' column.

    The workbook names them 'Vendor A — KAN (Italy) Response' / 'A Status', so
    the response header carries the label and the status header carries only
    the letter. Pairing by order of appearance is what keeps a vendor's verdict
    attached to that vendor's response.
    """
    vendors: list[str] = []
    pairs: list[tuple[int, int]] = []
    response_cols = [i for i, h in enumerate(header) if h.endswith("Response")]
    status_cols = [i for i, h in enumerate(header) if h.endswith("Status")]
    for resp, stat in zip(response_cols, status_cols):
        label = header[resp]
        for suffix in (" Response",):
            if label.endswith(suffix):
                label = label[: -len(suffix)]
        vendors.append(label.strip())
        pairs.append((resp, stat))
    return vendors, pairs


def _read_summary(wb) -> list[str]:
    if SUMMARY_SHEET not in wb.sheetnames:
        return []
    lines = []
    for row in wb[SUMMARY_SHEET].iter_rows(values_only=True):
        text = _clean(row[0] if row else None)
        if text:
            lines.append(text)
    return lines
