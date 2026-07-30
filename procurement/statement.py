"""Build the comparative statement — a read-only view over the store.

This module never writes. It opens no transaction and touches no snapshot
path; `build_statement` called twice leaves `generation` unchanged. See
docs/superpowers/specs/2026-07-31-comparative-statement-dashboard-design.md.
"""
from pydantic import BaseModel

from procurement.project import load_project
from procurement.store import snapshots


class StatementCell(BaseModel):
    qty: float | None = None
    unit_price: float | None = None
    total: float | None = None
    text: str | None = None
    note: str | None = None


class StatementRow(BaseModel):
    key: str
    label: str
    kind: str                       # priced | value | text
    cells: dict[str, StatementCell] = {}


class Statement(BaseModel):
    project: str
    currency: str                   # target currency; labels the Normalised row
    generation: int
    vendors: list[str] = []
    revisions: dict[str, str | None] = {}
    currencies: dict[str, str] = {}     # vendor -> its quoted currency
    statuses: dict[str, str] = {}       # vendor -> ok | failed | missing
    rows: list[StatementRow] = []


# (key, label) pairs read straight off VendorFacts.commercial. Order here is
# the order the text rows render in — Task 4 inserts its priced rows ahead of
# all of these, Task 5 fills the last one (`technical_feedback`) in place.
_TEXT_ROWS = [("engine_make", "Engine Make"),
              ("delivery_time", "Delivery Time"),
              ("delivery_terms", "Delivery Terms"),
              ("payment_terms", "Payment Terms")]


def _live_quote(vendor_facts, docs):
    """The DocumentRecord that produced this vendor's commercial terms, or
    None. `quotation_doc_id` can name a document no longer in `documents.json`
    (see task 2's pruning edge cases), so this always guards the lookup
    rather than assuming the id resolves."""
    if vendor_facts is None or vendor_facts.quotation_doc_id is None:
        return None
    return docs.get(vendor_facts.quotation_doc_id)


def build_statement(root: str, slug: str) -> Statement:
    project = load_project(root, slug)
    docs = {d.doc_id: d for d in snapshots.load_documents(root, slug)}
    # Columns come from project.vendors, never snapshots.list_fact_vendors:
    # a vendor with no extractable document must still get a column (INV-S2),
    # and a vendor the project no longer has must not get one.
    facts = {v: snapshots.load_facts(root, slug, v) for v in project.vendors}

    statement = Statement(project=project.name, currency=project.target_currency,
                          generation=project.generation, vendors=list(project.vendors))

    for vendor in project.vendors:
        f = facts[vendor]
        quote = _live_quote(f, docs)
        statement.revisions[vendor] = quote.revision_label if quote else None
        statement.currencies[vendor] = (f.commercial or {}).get("currency", "") if f else ""
        # missing: no facts snapshot at all. failed: facts exist but the
        # quotation never produced commercial terms. Conflating the two would
        # tell a reviewer a vendor never bid when the extraction actually broke.
        statement.statuses[vendor] = ("missing" if f is None
                                      else "ok" if f.commercial else "failed")

    for key, label in _TEXT_ROWS:
        row = StatementRow(key=key, label=label, kind="text")
        for vendor in project.vendors:
            f = facts[vendor]
            value = (f.commercial or {}).get(key) if f else None
            if value:                       # absent, blank, and 0 all mean "unknown"
                row.cells[vendor] = StatementCell(text=str(value))
        statement.rows.append(row)

    quote_row = StatementRow(key="quotation_file", label="Vendor Quotation File Name",
                             kind="text")
    for vendor in project.vendors:
        quote = _live_quote(facts[vendor], docs)
        if quote:
            quote_row.cells[vendor] = StatementCell(text=quote.path.rsplit("/", 1)[-1])
    statement.rows.append(quote_row)

    # Task 5 fills this row's cells from VendorFacts.technical_feedback; the
    # empty row still needs to exist so the ordering contract holds.
    statement.rows.append(StatementRow(key="technical_feedback",
                                       label="Technical Feedback", kind="text"))
    return statement
