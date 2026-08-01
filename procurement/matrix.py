"""Build the compliance matrix — a read-only view over the store.

This module never writes. It opens no transaction and touches no snapshot
path; `build_matrix` called twice leaves `generation` unchanged. See
docs/superpowers/specs/2026-07-31-compliance-screen-design.md.

**It never reads a rationale.** `unanswered` decomposes into "the vendor never
stated it" and "the fact was found and the comparison was refused" on
`ComplianceResult.fact_id`, which `compliance.evaluate` sets only when a fact
matched. Splitting on the rationale text would put the wording of every message
in `units.py` on the critical path of a number a reviewer acts on.
"""
from pydantic import BaseModel

from procurement.project import load_project
from procurement.store import snapshots

# The three groups a reviewer sorts by, per the spec's section 4. `deviation`
# sits with `fail`: the action is the same - decide whether to accept it - and
# a declared deviation is the most informative negative verdict in the matrix,
# not a softer one.
GROUPS = {"fail": "not_matched", "deviation": "not_matched",
          "unanswered": "needs_human", "review": "needs_human",
          "pass": "matched"}
GROUP_ORDER = ("not_matched", "needs_human", "matched")

# What a vendor gets when no cell was stored for it at all. Never a verdict:
# the pipeline not having produced a cell is not the vendor having answered.
_ABSENT = "no verdict was computed for this vendor"


class MatrixCell(BaseModel):
    vendor: str
    verdict: str                    # pass|fail|deviation|unanswered|review
    group: str                      # not_matched|needs_human|matched
    rationale: str = ""
    fact_id: str | None = None
    doc_id: str | None = None


class MatrixRow(BaseModel):
    req_id: str
    clause_ref: str
    text: str
    checkability: str               # auto|judgement
    parameter: str | None = None
    operator: str | None = None
    value: str | float | list | None = None
    unit: str | None = None
    cells: dict[str, MatrixCell] = {}


class Coverage(BaseModel):
    """Counted over `auto` cells only. A percentage over every cell would be
    dominated by `review` - four to six times as many on every real store -
    and would say nothing about extraction."""
    auto_cells: int = 0
    by_verdict: dict[str, int] = {}
    unanswered_silent: int = 0      # no vendor document stated the parameter
    unanswered_refused: int = 0     # the fact was found; the comparison was not


class ComplianceMatrix(BaseModel):
    vendors: list[str] = []
    rows: list[MatrixRow] = []
    coverage: Coverage = Coverage()


def build_matrix(root: str, slug: str) -> ComplianceMatrix:
    """One row per live requirement, one cell per (requirement, vendor)."""
    reqset = snapshots.load_requirements(root, slug)
    live = [r for r in reqset.requirements if not r.withdrawn]
    # Vendors come from the project, matching `evaluate_project`: a vendor
    # whose extraction produced nothing must still hold a column, because a
    # missing column reads as "did not offer".
    vendors = load_project(root, slug).vendors
    stored = {(c.req_id, c.vendor): c
              for c in snapshots.load_compliance(root, slug)}

    rows, coverage = [], Coverage()
    for requirement in live:
        cells = {}
        for vendor in vendors:
            result = stored.get((requirement.req_id, vendor))
            if result is None:
                cells[vendor] = MatrixCell(vendor=vendor, verdict="unanswered",
                                           group=GROUPS["unanswered"],
                                           rationale=_ABSENT)
            else:
                cells[vendor] = MatrixCell(
                    vendor=vendor, verdict=result.verdict,
                    group=GROUPS.get(result.verdict, "needs_human"),
                    rationale=result.rationale, fact_id=result.fact_id,
                    doc_id=result.doc_id)
            if requirement.checkability == "auto":
                _tally(coverage, cells[vendor])

        rows.append(MatrixRow(
            req_id=requirement.req_id, clause_ref=requirement.clause_ref,
            text=requirement.text, checkability=requirement.checkability,
            parameter=requirement.parameter, operator=requirement.operator,
            value=requirement.value, unit=requirement.unit, cells=cells))

    return ComplianceMatrix(vendors=list(vendors), rows=rows, coverage=coverage)


def _tally(coverage: Coverage, cell: MatrixCell) -> None:
    coverage.auto_cells += 1
    coverage.by_verdict[cell.verdict] = coverage.by_verdict.get(cell.verdict, 0) + 1
    if cell.verdict == "unanswered":
        if cell.fact_id is None:
            coverage.unanswered_silent += 1
        else:
            coverage.unanswered_refused += 1


def rows_in_group(matrix: ComplianceMatrix, group: str) -> list[MatrixRow]:
    """Rows with at least one cell in `group`.

    Any, not all: one vendor failing a requirement the others meet is exactly
    the row a reviewer needs on the Not matched worklist, and requiring every
    vendor to agree would hide it.
    """
    return [row for row in matrix.rows
            if any(cell.group == group for cell in row.cells.values())]
