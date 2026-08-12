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
import os

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
    # Every reading the verdict was computed from. More than one means the cell
    # was not auto-decided; the rationale names them in prose.
    candidate_fact_ids: list[str] = []
    # The same evidence named by the file a reviewer can open. `doc_id` is a
    # content hash and says nothing to a human reading a spreadsheet.
    # None, never a placeholder: a cell that cites nothing — a judgement row, a
    # silent `unanswered` — must not be attributed to a document that did not
    # produce it, and neither must a `doc_id` with no matching record.
    doc_name: str | None = None
    # Every distinct document behind the candidate readings, in reading order.
    # More than one is the case this exists for: a parameter stated twice with
    # different values is a `review` precisely because two documents disagree,
    # and naming only the first would hide that.
    candidate_doc_names: list[str] = []


class MatrixRow(BaseModel):
    req_id: str
    clause_ref: str
    text: str
    checkability: str               # auto|stated|judgement
    parameter: str | None = None
    operator: str | None = None
    value: str | float | list | None = None
    unit: str | None = None
    cells: dict[str, MatrixCell] = {}


class Coverage(BaseModel):
    """`auto_cells` and `stated_cells` are separate denominators - they measure
    different things, arithmetic settling versus text matching settling - but
    `by_verdict` tallies both tiers together. A percentage over every cell,
    including `judgement`, would be dominated by `review` - four to six times
    as many on every real store - and would say nothing about extraction."""
    auto_cells: int = 0
    stated_cells: int = 0           # text-matched, counted separately: the
                                    # unanswered_silent/refused split below is
                                    # only meaningful against `auto`
    by_verdict: dict[str, int] = {}
    unanswered_silent: int = 0      # no vendor document stated the parameter
    unanswered_refused: int = 0     # the fact was found; the comparison was not


class ComplianceMatrix(BaseModel):
    vendors: list[str] = []
    rows: list[MatrixRow] = []
    coverage: Coverage = Coverage()


def _doc_names(root: str, slug: str, vendors: list[str]) -> tuple[dict, dict]:
    """`doc_id -> filename` and `(vendor, fact_id) -> doc_id`.

    Two reads that `build_matrix` would otherwise repeat per cell. The fact map
    is keyed by vendor because `fact_id` is only unique within one vendor's
    facts, and crossing that boundary would attribute one vendor's document to
    another's verdict.

    The basename, not the stored path: a reviewer recognises `datasheet-rev-C.pdf`,
    not `vendors/KERUI/technical/datasheet-rev-C.pdf`. Two files sharing a
    basename inside one vendor folder would read alike here — the Detail sheet
    keeps `Doc id` beside the name for exactly that case.
    """
    by_doc = {d.doc_id: os.path.basename(d.path)
              for d in snapshots.load_documents(root, slug)}
    by_fact = {}
    for vendor in vendors:
        stored = snapshots.load_facts(root, slug, vendor)
        for fact in (stored.technical if stored else []):
            if fact.get("fact_id"):
                by_fact[(vendor, fact["fact_id"])] = fact.get("doc_id")
    return by_doc, by_fact


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
    by_doc, by_fact = _doc_names(root, slug, vendors)

    def named(vendor: str, result) -> tuple[str | None, list[str]]:
        primary = by_doc.get(result.doc_id) if result.doc_id else None
        # dict.fromkeys, not a set: reading order is what the rationale cites,
        # and a set would reorder the two documents a `review` is comparing.
        candidates = list(dict.fromkeys(
            name for name in
            (by_doc.get(by_fact.get((vendor, fid)))
             for fid in result.candidate_fact_ids)
            if name))
        return primary, candidates

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
                doc_name, candidate_doc_names = named(vendor, result)
                cells[vendor] = MatrixCell(
                    vendor=vendor, verdict=result.verdict,
                    group=GROUPS.get(result.verdict, "needs_human"),
                    rationale=result.rationale, fact_id=result.fact_id,
                    doc_id=result.doc_id,
                    candidate_fact_ids=result.candidate_fact_ids,
                    doc_name=doc_name,
                    candidate_doc_names=candidate_doc_names)
            if requirement.checkability == "auto":
                _tally(coverage, cells[vendor])
            elif requirement.checkability == "stated":
                coverage.stated_cells += 1
                coverage.by_verdict[cells[vendor].verdict] = (
                    coverage.by_verdict.get(cells[vendor].verdict, 0) + 1)

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


def bound(row: MatrixRow) -> str:
    """The requirement's checkable bound, or its clause text.

    Lives here rather than in a view because it is a property of a `MatrixRow`,
    read by the exporter as a plain function rather than tied to any one
    front end. `web/src/constants.ts` holds the same rule for the browser;
    that copy is unavoidable since JS cannot import this module.
    """
    if row.checkability == "stated":
        return (f"{row.parameter} = {row.value}" if row.value is not None
                else f"{row.parameter} (must be stated)")
    if row.checkability != "auto":
        return row.text
    value = row.value if not isinstance(row.value, list) else "..".join(
        str(v) for v in row.value)
    return f"{row.parameter} {row.operator} {value} {row.unit or ''}".strip()


def rows_in_group(matrix: ComplianceMatrix, group: str) -> list[MatrixRow]:
    """Rows with at least one cell in `group`.

    Any, not all: one vendor failing a requirement the others meet is exactly
    the row a reviewer needs on the Not matched worklist, and requiring every
    vendor to agree would hide it.
    """
    return [row for row in matrix.rows
            if any(cell.group == group for cell in row.cells.values())]
