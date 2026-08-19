"""The text a bidder actually reads — the enquiry mail's body.

Pure module: no store, no I/O, no clock, like `workflow/bidders.py` and
`workflow/eligibility.py`. `dispatch` resolves the project, the items and the
issued documents at the boundary and calls in, so every sentence here is
testable against plain records.

### The checklist is asked for, not written down

The nine returnables the body lists come from `workflow/checklist.py`, which
builds them out of `EligibilityCategory` and `eligibility.assess`. This module
formats those rows and does not decide any of them — the same rows are served
to the browser, so what a buyer reads on screen and what a vendor reads in the
mail cannot drift.

That matters more than it looks. A checklist typed into a string literal reads
correctly on the day it is written, and then a category is renamed, or (c)'s
rule changes, and the enquiry keeps telling vendors to send the old thing while
the gate keeps rejecting them for the new one. The bidder cannot see the gate;
this body is the only description of it they will ever get, so the two are
wired to one definition rather than kept in step by hand.

Asking `assess` also means the buyer's own checklist additions come along free
the moment `extra_items` lands in that function — this module needs no edit for
it.

### What deliberately never travels

**The estimate.** `RfqRecord.value_estimate_aed` and `Item.estimated_value_aed`
are the contractor's own budget for the work. Putting either in front of the
bidders being asked to price it tells every one of them the number to beat, and
there is no recovering the competition afterwards. `build_body` is not given a
shortlist either, so — like `dispatch`'s one-message-per-vendor rule, which
this body sits inside — there is nothing here that could name a competitor.
"""
from collections.abc import Sequence

from workflow import checklist
from workflow.models.project import Item, Project
from workflow.models.rfq import ChecklistItem, RfqRecord, TechnicalPackage
from workflow.models.rfq_document import RfqDocument

def _detail_rows(
    rfq: RfqRecord,
    project: Project | None,
    items: Sequence[Item],
    technical_package: TechnicalPackage | None,
) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if project is not None:
        rows.append(("Project", f"{project.name} ({project.code})"))
        rows.append(("Client", project.client))
        rows.append(("Location", project.location))
    rows.append(("Enquiry", rfq.reference))
    rows.append(("Package", rfq.package))
    rows.append(("Discipline", rfq.discipline))
    if technical_package is not None:
        rows.append(("Package revision", technical_package.revision))
    for item in items:
        # Quantity and description, never `estimated_value_aed` -- see the
        # module docstring.
        rows.append((
            "Item",
            f"{item.item_type} — {item.description} ({_qty(item)} {item.uom})",
        ))
        if item.required_on_site is not None:
            rows.append(("Required on site", item.required_on_site.isoformat()))
    return rows


def _qty(item: Item) -> str:
    """`1200.0 m` reads as a rounding artefact to somebody pricing cable."""
    return str(int(item.qty)) if float(item.qty).is_integer() else str(item.qty)


def build_body(
    *,
    rfq: RfqRecord,
    project: Project | None,
    items: Sequence[Item],
    issued: Sequence[RfqDocument],
    technical_package: TechnicalPackage | None = None,
    extra_items: Sequence[ChecklistItem] = (),
) -> str:
    """The whole message body, as plain text.

    `project` is optional because `get_project` can answer `None`, and an
    enquiry that cannot name its project is still an enquiry that has to go
    out — the reference and the checklist are what a bidder cannot do without.
    """
    rows = _detail_rows(rfq, project, items, technical_package)
    width = max(len(label) for label, _ in rows)

    parts = [
        f"Enquiry {rfq.reference}",
        "",
        "You are invited to submit an offer against the enquiry below. The "
        "documents that define the scope are attached to this message.",
        "",
    ]
    parts += [f"  {label.ljust(width)}   {value}" for label, value in rows]
    parts += ["", _attachment_block(issued), ""]
    parts += ["Eligibility checklist — your offer must include:", ""]
    parts += [checklist.as_text(row) for row in checklist.rows(issued, extra_items)]
    parts += [
        "",
        "Lines marked (must have) are required for your bid to be evaluated at "
        "all. Please quote the enquiry reference above on everything you "
        "return, and submit your offer by the stated due date.",
    ]
    return "\n".join(parts)


def _attachment_block(issued: Sequence[RfqDocument]) -> str:
    if not issued:
        # Said rather than shown as an empty list: a heading with nothing under
        # it reads as a mail that lost its attachments in transit.
        return (
            "No documents are attached to this enquiry. Please request the "
            "scope documents before quoting."
        )
    names = "\n".join(f"  - {d.filename}" for d in issued)
    count = f"{len(issued)} document" + ("s" if len(issued) != 1 else "")
    return f"Attached to this enquiry ({count}):\n{names}"
