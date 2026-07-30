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
    changing lie, not a rendering detail. bool is excluded deliberately:
    `isinstance(True, int)` is True, and a flag must not read as a price."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _priced_rows(project, facts) -> list[StatementRow]:
    """The priced rows (base scope, optionals, freight, VAT, discount, FINAL
    VALUE) plus the two value rows and the Normalised row that sit among
    them in the row order (spec §4 table). All of the statement's arithmetic
    lives here; build_statement itself does none."""
    rows: dict[str, StatementRow] = {}

    def cell(key, label, kind, vendor, **kw):
        row = rows.setdefault(key, StatementRow(key=key, label=label, kind=kind))
        row.cells[vendor] = StatementCell(**kw)

    def opt_cell(key, label, vendor, **kw):
        """Optional rows alone can be written twice for one vendor: two items
        whose descriptions normalise to the same key share a cell. Overwriting
        would print the second line while the column sum contains both, so the
        printed sheet would not add up to its own FINAL VALUE. Merge instead —
        qty and unit price are dropped, since they no longer describe one line."""
        row = rows.setdefault(key, StatementRow(key=key, label=label, kind="priced"))
        prior = row.cells.get(vendor)
        if prior is None:
            row.cells[vendor] = StatementCell(**kw)
            return
        totals = [t for t in (prior.total, kw.get("total")) if t is not None]
        total = sum(totals) if totals else None
        note = prior.note or kw.get("note")
        if total is not None and note == "included in base price":
            # One merged line was stated as already inside the base price and
            # another was priced separately. Keeping the note beside a real
            # total would claim the same scope is both inside the base and
            # added to it — while that total sits in FINAL VALUE.
            note = "part stated as included in base price"
        row.cells[vendor] = StatementCell(total=total, note=note)

    order: list[str] = []           # optional-row keys, first-seen across columns
    sums: dict[str, dict] = {}

    for vendor in project.vendors:
        f = facts.get(vendor)
        c = (f.commercial if f else None) or {}
        # `or None` is the load-bearing half. BidExtraction.base_price defaults
        # to 0.0, so an extraction that succeeded and found no price stores
        # exactly what one that found zero would — and `_money` cannot tell
        # them apart. No vendor quotes a zero base scope, so 0.0 here means
        # unknown; taken literally it prints a 0.00 FINAL VALUE that sorts to
        # the top of the award screen as the cheapest bid.
        base = _money(c.get("base_price")) or None
        if base is not None:
            cell("base_scope", "Base scope", "priced", vendor, total=base)

        options = 0.0
        unpriced = 0
        for item in c.get("optional_items", []):
            key = f"opt:{_opt_key(item.get('description', ''))}"
            if key not in order:
                order.append(key)
            if item.get("included_in_base"):
                # Its scope is still worth showing — the note — but never its
                # total, or the base price would be counted twice: once as
                # part of base_price, once again as this line item.
                opt_cell(key, item.get("description", ""), vendor,
                         note="included in base price")
                continue
            qty = _money(item.get("qty"))
            unit_price = _money(item.get("unit_price"))
            total = _money(item.get("total"))
            if total is None and qty is not None and unit_price is not None:
                # Arithmetic stays in Python: OptionalItem.total is nullable,
                # and when the components are stored the line is priced.
                # Dropping it would understate this column against a vendor
                # who happened to state the same scope's total outright.
                total = qty * unit_price
            opt_cell(key, item.get("description", ""), vendor,
                     qty=qty, unit_price=unit_price, total=total)
            if total is None:
                unpriced += 1       # cannot join the sum — FINAL VALUE says so
            else:
                options += total

        freight = None
        if not c.get("freight_included"):
            # No `or None` here, unlike base: a zero freight contributes zero
            # to the column either way, so the `if freight` guard below is the
            # whole of it. Freight's zero is not award-changing; base's is.
            freight = _money(c.get("freight_amount"))
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
                        "pct": pct or 0.0, "unpriced": unpriced,
                        "has_prices": base is not None}

        normalized = f.normalized if f else None
        # The same invented zero as base_price, one row down and worse:
        # normalize_bid derives its total FROM base_price, so a vendor who
        # stated no price normalises to 0.0 — and spec §5(c) makes Normalised
        # THE row compared across columns, where a 0.00 beside a real bid
        # reads as the cheapest offer. The `base is not None` gate is the
        # honest form of it: a normalised total derived from an unknown base
        # is not a figure, whatever normalize_bid returned.
        norm_total = _money((normalized or {}).get("normalized_total")) or None
        if norm_total is not None and base is not None:
            cell("normalised",
                 f"Normalised (ex-VAT, ex-options, {project.target_currency})",
                 "priced", vendor, total=norm_total)

    for vendor in project.vendors:
        s = sums.get(vendor, {})
        if not s.get("has_prices"):
            # Blank, never a zeroed or partial total. But a column showing
            # priced options and freight with an empty FINAL VALUE and no
            # reason invites the reader to add it up themselves, which is the
            # partial sum this branch exists to refuse.
            if s.get("options") or s.get("freight"):
                cell("final_value", "FINAL VALUE", "priced", vendor,
                     note="base price not stated")
            continue
        pre_vat = s["base"] + s["options"] + s["freight"]
        # Rule (a): the discount base excludes VAT — computed here, on the
        # unrounded pre_vat, and carried unrounded into final_value below.
        discount_raw = pre_vat * s["pct"]
        if s["pct"]:
            cell("discount_amount", "Discount Amount", "priced", vendor,
                 total=round(discount_raw, 2))
        # The displayed cells are each rounded independently, so adding the
        # printed column by hand can land a cent away from the printed FINAL
        # VALUE. That is deliberate: the reference sheet is the authority and
        # it sums the unrounded components. Do not "fix" it by summing the
        # rounded cells — that reproduces neither the sheet nor the tests.
        n = s["unpriced"]
        cell("final_value", "FINAL VALUE", "priced", vendor,
             total=round(pre_vat + s["vat_raw"] - discount_raw, 2),
             note=(f"excludes {n} option(s) with no stated price" if n else None))

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
        # is never coerced" rule prescribes for this column header. The
        # trailing `or None` is what actually enforces it: BidExtraction.
        # currency defaults to "", so a successful extraction that found no
        # currency reaches here as "" rather than as a missing key.
        commercial = (f.commercial or {}) if f else {}
        statement.currencies[vendor] = commercial.get("currency") or None
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
