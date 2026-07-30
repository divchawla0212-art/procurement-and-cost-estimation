"""Build the comparative statement — a read-only view over the store.

This module never writes. It opens no transaction and touches no snapshot
path; `build_statement` called twice leaves `generation` unchanged. See
docs/superpowers/specs/2026-07-31-comparative-statement-dashboard-design.md.
"""
import re

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
    currencies: dict[str, str | None] = {}   # vendor -> its quoted currency, or
                                              # None when unknown — never ""
    statuses: dict[str, str] = {}       # vendor -> ok | failed | missing
    rows: list[StatementRow] = []


# (key, label) pairs read straight off VendorFacts.commercial. Order here is
# the order the text rows render in — Task 4 inserts its priced rows ahead of
# all of these, Task 5 fills the last one (`technical_feedback`) in place.
_TEXT_ROWS = [("engine_make", "Engine Make"),
              ("delivery_time", "Delivery Time"),
              ("delivery_terms", "Delivery Terms"),
              ("payment_terms", "Payment Terms")]


_OPTIONAL_TAIL = re.compile(r"[\s\-–—]*\(?optional\)?\s*$", re.I)


def _opt_key(description: str) -> str:
    """Group by normalised description. The load-bearing part is that
    grouping is exact-after-normalisation and never fuzzy — two vendors
    wording the same scope differently must get two rows, not one merged
    row that silently equates scopes that are not equal (spec §4)."""
    return _OPTIONAL_TAIL.sub("", " ".join(description.split())).strip().lower()


def _money(value) -> float | None:
    """None unless this is a real number. '' and None both mean unknown, and
    unknown must never become 0.0 — a zero in a price column is an award-
    changing lie, not a rendering detail."""
    return float(value) if isinstance(value, (int, float)) else None


def _priced_rows(project, facts) -> list[StatementRow]:
    """The priced rows (base scope, optionals, freight, VAT, discount, FINAL
    VALUE) plus the two value rows and the Normalised row that sit among
    them in the row order (spec §4 table). All of the statement's arithmetic
    lives here; build_statement itself does none."""
    rows: dict[str, StatementRow] = {}

    def cell(key, label, kind, vendor, **kw):
        row = rows.setdefault(key, StatementRow(key=key, label=label, kind=kind))
        row.cells[vendor] = StatementCell(**kw)

    order: list[str] = []           # optional-row keys, first-seen across columns
    sums: dict[str, dict] = {}

    for vendor in project.vendors:
        f = facts.get(vendor)
        c = (f.commercial if f else None) or {}
        base = _money(c.get("base_price"))
        if base is not None:
            cell("base_scope", "Base scope", "priced", vendor, total=base)

        options = 0.0
        for item in c.get("optional_items", []):
            key = f"opt:{_opt_key(item.get('description', ''))}"
            if key not in order:
                order.append(key)
            if item.get("included_in_base"):
                # Its scope is still worth showing — the note — but never its
                # total, or the base price would be counted twice: once as
                # part of base_price, once again as this line item.
                cell(key, item.get("description", ""), "priced", vendor,
                     note="included in base price")
                continue
            total = _money(item.get("total"))
            cell(key, item.get("description", ""), "priced", vendor,
                 qty=_money(item.get("qty")), unit_price=_money(item.get("unit_price")),
                 total=total)
            options += total or 0.0

        freight = None
        if not c.get("freight_included"):
            freight = _money(c.get("freight_amount")) or None
            if freight:
                cell("freight", "Freight Charges", "priced", vendor, total=freight)

        included = c.get("vat_included")
        if included is not None:
            cell("vat_included", "VAT Included", "value", vendor,
                 text="YES" if included else "NO")

        pct = c.get("discount_pct")
        if isinstance(pct, (int, float)):
            cell("discount_pct", "Discount %", "value", vendor,
                 text=f"{pct * 100:g}%")

        rate = c.get("vat_rate") or 0.0
        # vat_raw is the UNROUNDED base*rate — it must feed final_value's
        # sum below. Rounding it first, then summing, then rounding again
        # loses a cent on numbers like ADPOWER's (rounded VAT + rounded
        # discount does not equal the reference sheet's FINAL VALUE); only
        # the displayed cell is rounded.
        vat_raw = None
        if included:
            cell("vat", "VAT", "priced", vendor, note="included")
        elif rate and base is not None:
            vat_raw = base * rate
            cell("vat", "VAT", "priced", vendor, total=round(vat_raw, 2))

        sums[vendor] = {"base": base, "options": options,
                        "freight": freight or 0.0, "vat_raw": vat_raw or 0.0,
                        "pct": pct or 0.0, "has_prices": base is not None}

        normalized = f.normalized if f else None
        norm_total = _money((normalized or {}).get("normalized_total"))
        if norm_total is not None:
            cell("normalised",
                 f"Normalised (ex-VAT, ex-options, {project.target_currency})",
                 "priced", vendor, total=norm_total)

    for vendor in project.vendors:
        s = sums.get(vendor, {})
        if not s.get("has_prices"):
            continue                    # blank column, not a zeroed one
        pre_vat = s["base"] + s["options"] + s["freight"]
        # Rule (a): the discount base excludes VAT — computed here, on the
        # unrounded pre_vat, and carried unrounded into final_value below.
        discount_raw = pre_vat * s["pct"]
        if s["pct"]:
            cell("discount_amount", "Discount Amount", "priced", vendor,
                 total=round(discount_raw, 2))
        cell("final_value", "FINAL VALUE", "priced", vendor,
             total=round(pre_vat + s["vat_raw"] - discount_raw, 2))

    ordered = (["base_scope"] + order +
               ["freight", "vat", "vat_included", "discount_pct",
                "discount_amount", "final_value", "normalised"])
    return [rows[k] for k in ordered if k in rows]


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
        # Unknown must stay None, not "" — the fix CLAUDE.md's "missing data
        # is never coerced" rule prescribes for this column header.
        statement.currencies[vendor] = (f.commercial or {}).get("currency") if f else None
        # missing: no facts snapshot at all. failed: facts exist but the
        # quotation never produced commercial terms. Conflating the two would
        # tell a reviewer a vendor never bid when the extraction actually broke.
        statement.statuses[vendor] = ("missing" if f is None
                                      else "ok" if f.commercial else "failed")

    # Priced rows go ahead of the text rows (task 3's ordering contract).
    statement.rows.extend(_priced_rows(project, facts))

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
