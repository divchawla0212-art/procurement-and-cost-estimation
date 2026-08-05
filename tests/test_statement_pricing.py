from procurement.models import OptionalItem, VendorBid
from procurement.normalize import normalize_bid
from procurement.project import load_project, save_project
from procurement.statement import build_statement
from procurement.store import snapshots
from procurement.store.models import VendorFacts

from tests.test_pipeline_vocabulary import _project


def project_vendors_extended(root, name):
    """Load the project, append `name` to vendors, save. Defined locally
    rather than imported: tests/test_phase3_lifecycle.py has a `_set_vendors`
    of the same shape, and duplicating four lines beats coupling two suites."""
    project = load_project(root, "p")
    project.vendors = list(project.vendors) + [name]
    save_project(root, project)


def _stored(root, bid):
    """Store a bid the way pipeline.py does — `commercial` alongside the
    `normalize_bid` output, not a hand-written `normalized` dict. Every
    defect this file was extended for hid behind a hand-written one: the
    schema's non-null defaults only surface on the real shape."""
    snapshots.save_facts(root, "p", VendorFacts(
        vendor=bid.vendor, commercial=bid.model_dump(),
        normalized=normalize_bid(bid, "USD", {}).model_dump()))


def _cell(statement, key, vendor):
    row = next((r for r in statement.rows if r.key == key), None)
    return row.cells.get(vendor) if row else None


def _facts(root, vendor, **commercial):
    body = {"currency": "USD", "base_price": 0.0, "vat_included": False,
            "vat_rate": 0.0, "freight_amount": 0.0, "freight_included": False,
            "discount_pct": 0.0, "optional_items": []}
    body.update(commercial)
    snapshots.save_facts(root, "p", VendorFacts(
        vendor=vendor, commercial=body,
        normalized={"vendor": vendor, "normalized_currency": "USD",
                    "normalized_total": 999.0, "adjustments": [],
                    "extraction_status": "ok"}))


def test_final_value_is_the_column_sum_including_options(tmp_path):
    """The reference sheet's KERUI column: 1170000 + 83000 + 17000 + 30000."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1170000.0, optional_items=[
        {"description": "PEMS for Exhaust - Optional", "qty": 2,
         "unit_price": 41500.0, "total": 83000.0},
        {"description": "Tools - Optional", "qty": 1,
         "unit_price": 17000.0, "total": 17000.0},
        {"description": "Two Years Spare Parts - Optional", "qty": 2,
         "unit_price": 15000.0, "total": 30000.0}])

    statement = build_statement(root, "p")
    assert _cell(statement, "final_value", "KERUI").total == 1300000.0


def test_the_discount_excludes_vat_from_its_base(tmp_path):
    """Rule (a), on the reference sheet's ADPOWER numbers."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1194196.92, vat_rate=0.05, discount_pct=0.10)

    statement = build_statement(root, "p")
    assert _cell(statement, "vat", "KERUI").total == 59709.85
    assert _cell(statement, "discount_amount", "KERUI").total == 119419.69
    assert _cell(statement, "final_value", "KERUI").total == 1134487.07


def test_a_vat_inclusive_price_is_not_taxed_twice(tmp_path):
    """Rule (b). The row is annotated, contributes zero, and says why."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1000.0, vat_rate=0.05, vat_included=True)

    statement = build_statement(root, "p")
    assert _cell(statement, "vat", "KERUI").total is None
    assert _cell(statement, "vat", "KERUI").note == "included"
    assert _cell(statement, "final_value", "KERUI").total == 1000.0


def test_an_option_included_in_base_contributes_nothing(tmp_path):
    """MKON's 'This has been included in Base Price.' cell."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1000.0, optional_items=[
        {"description": "Two Years Spare Parts", "total": 30000.0,
         "included_in_base": True}])

    statement = build_statement(root, "p")
    cell = _cell(statement, "opt:two years spare parts", "KERUI")
    assert cell.total is None
    assert cell.note == "included in base price"
    assert _cell(statement, "final_value", "KERUI").total == 1000.0


def test_differently_worded_options_get_their_own_rows(tmp_path):
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1.0, optional_items=[
        {"description": "Tools - Optional", "total": 10.0}])
    project_vendors_extended(root, "MKON")
    _facts(root, "MKON", base_price=1.0, optional_items=[
        {"description": "Special tooling", "total": 20.0}])

    keys = [r.key for r in build_statement(root, "p").rows if r.key.startswith("opt:")]
    assert keys == ["opt:tools", "opt:special tooling"]


def test_a_vendor_with_no_prices_shows_blanks_not_zeros(tmp_path):
    """The single most important assertion in this file."""
    root = _project(tmp_path)
    snapshots.save_facts(root, "p", VendorFacts(vendor="KERUI"))

    statement = build_statement(root, "p")
    for key in ("base_scope", "freight", "vat", "final_value", "normalised"):
        cell = _cell(statement, key, "KERUI")
        assert cell is None or cell.total is None, key


def test_the_normalised_row_is_normalize_bids_figure_verbatim(tmp_path):
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1170000.0, optional_items=[
        {"description": "Tools", "total": 17000.0}])

    statement = build_statement(root, "p")
    assert _cell(statement, "normalised", "KERUI").total == 999.0
    assert _cell(statement, "final_value", "KERUI").total == 1187000.0


def test_a_zero_base_price_is_unknown_not_free(tmp_path):
    """BidExtraction.base_price defaults to 0.0, so an extraction that
    succeeded and found no price stores the same 0.0 as one that found zero.
    No vendor quotes a zero base scope, so 0.0 means unknown — and a 0.00
    FINAL VALUE would sort to the top of the award screen as the cheapest
    bid. This is the production shape `commercial=None` never exercises."""
    root = _project(tmp_path)
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="KERUI", commercial=VendorBid(vendor="KERUI").model_dump()))

    statement = build_statement(root, "p")
    for key in ("base_scope", "final_value"):
        cell = _cell(statement, key, "KERUI")
        assert cell is None or cell.total is None, key


def test_an_option_priced_only_by_qty_and_unit_price_still_counts(tmp_path):
    """OptionalItem.total is nullable. When qty and unit price are both
    stored, the total is derivable — arithmetic stays in Python, so derive
    it rather than dropping the line out of the column sum."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1000.0, optional_items=[
        {"description": "Tools", "qty": 2, "unit_price": 500.0}])

    statement = build_statement(root, "p")
    assert _cell(statement, "opt:tools", "KERUI").total == 1000.0
    assert _cell(statement, "final_value", "KERUI").total == 2000.0


def test_an_option_with_no_price_at_all_is_flagged_on_the_final_value(tmp_path):
    """It cannot join the sum, but the sum must say so. Silently omitting it
    understates the column against vendors who did price the same scope."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1000.0, optional_items=[
        {"description": "Tools"}])

    statement = build_statement(root, "p")
    assert _cell(statement, "opt:tools", "KERUI").total is None
    final = _cell(statement, "final_value", "KERUI")
    assert final.total == 1000.0
    assert final.note == "excludes 1 option(s) with no stated price"


def test_two_options_that_normalise_alike_reconcile_with_the_column(tmp_path):
    """'Tools - Optional' and 'Tools' collapse to one key. Printing one and
    summing both leaves a column a reviewer cannot add up by hand."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1000.0, optional_items=[
        {"description": "Tools - Optional", "total": 17000.0},
        {"description": "Tools", "total": 5000.0}])

    statement = build_statement(root, "p")
    assert _cell(statement, "opt:tools", "KERUI").total == 22000.0
    assert _cell(statement, "final_value", "KERUI").total == 23000.0


def test_the_column_sums_base_options_freight_vat_and_discount_together(tmp_path):
    """Every priced component in one column, so that a defect in any single
    one of them changes FINAL VALUE. Each of base, options and freight must
    be inside the discount base and outside the VAT base."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1000000.0, freight_amount=20000.0,
           vat_rate=0.05, discount_pct=0.10, optional_items=[
               {"description": "PEMS", "total": 100000.0},
               {"description": "Tools", "qty": 2, "unit_price": 25000.0}])

    statement = build_statement(root, "p")
    assert _cell(statement, "freight", "KERUI").total == 20000.0
    assert _cell(statement, "vat", "KERUI").total == 50000.0        # base only
    assert _cell(statement, "discount_amount", "KERUI").total == 117000.0
    assert _cell(statement, "final_value", "KERUI").total == 1103000.0


def test_freight_the_vendor_included_is_not_added_again(tmp_path):
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1000.0, freight_amount=20000.0,
           freight_included=True)

    statement = build_statement(root, "p")
    assert _cell(statement, "freight", "KERUI") is None
    assert _cell(statement, "final_value", "KERUI").total == 1000.0


def test_the_rows_render_in_the_specs_order(tmp_path):
    """Priced rows ahead of the text rows, optionals inside the priced
    block, FINAL VALUE after the discount that feeds it."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1000.0, freight_amount=50.0,
           vat_rate=0.05, discount_pct=0.10,
           optional_items=[{"description": "Tools", "total": 10.0}])

    assert [r.key for r in build_statement(root, "p").rows] == [
        "base_scope", "opt:tools", "freight", "vat", "vat_included",
        "discount_pct", "discount_amount", "final_value", "normalised",
        "engine_make", "delivery_time", "delivery_terms", "payment_terms",
        "quotation_file", "technical_feedback"]


def test_the_value_rows_are_values_not_prices(tmp_path):
    """`vat_included` and `discount_pct` describe the column; they are not
    money and must never be summed into it."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1000.0, discount_pct=0.10)

    statement = build_statement(root, "p")
    for key, text in (("vat_included", "NO"), ("discount_pct", "10%")):
        row = next(r for r in statement.rows if r.key == key)
        assert row.kind == "value", key
        assert row.cells["KERUI"].text == text
        assert row.cells["KERUI"].total is None


def test_an_unknown_currency_is_none_not_blank(tmp_path):
    """BidExtraction.currency defaults to "", which the Statement field
    declares it never carries."""
    root = _project(tmp_path)
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="KERUI", commercial=VendorBid(vendor="KERUI").model_dump()))

    assert build_statement(root, "p").currencies["KERUI"] is None


def test_the_normalised_row_does_not_print_an_invented_zero(tmp_path):
    """normalize_bid derives its total FROM base_price, so a vendor who
    stated no price normalises to 0.0 — and spec §5(c) makes Normalised THE
    row compared across columns. A 0.00 there beside a real bid reads as the
    cheapest offer on the sheet."""
    root = _project(tmp_path)
    project_vendors_extended(root, "MKON")
    _stored(root, VendorBid(vendor="KERUI", currency="USD", base_price=1170000.0))
    _stored(root, VendorBid(vendor="MKON"))       # succeeded, found nothing

    statement = build_statement(root, "p")
    assert statement.statuses["MKON"] == "ok"     # not a failed extraction
    assert _cell(statement, "normalised", "KERUI").total == 1170000.0
    cell = _cell(statement, "normalised", "MKON")
    assert cell is None or cell.total is None


def test_a_normalised_total_outliving_its_commercial_terms_is_not_shown(tmp_path):
    """pipeline.py carries the prior `normalized` forward whenever the fresh
    run resolves no commercial terms, so this pairing is reachable without
    any pruning bug. The figure was derived from a base price the store no
    longer holds; printing it puts a number in the comparison row that
    nothing on the sheet backs."""
    root = _project(tmp_path)
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="KERUI", commercial=None,
        normalized={"vendor": "KERUI", "normalized_currency": "USD",
                    "normalized_total": 999.0, "adjustments": [],
                    "extraction_status": "ok"}))

    statement = build_statement(root, "p")
    cell = _cell(statement, "normalised", "KERUI")
    assert cell is None or cell.total is None


def test_a_merged_option_never_claims_to_be_both_included_and_priced(tmp_path):
    """One line stated as already inside the base price, another priced
    separately, both normalising to one key. Carrying the 'included' note
    onto the merged cell would claim the scope is inside the base AND added
    to it — while its total is in FINAL VALUE."""
    root = _project(tmp_path)
    _stored(root, VendorBid(vendor="KERUI", currency="USD", base_price=1000.0,
                            optional_items=[
                                OptionalItem(description="Tools - Optional",
                                             included_in_base=True),
                                OptionalItem(description="Tools", total=5000.0)]))

    statement = build_statement(root, "p")
    cell = _cell(statement, "opt:tools", "KERUI")
    assert cell.total == 5000.0
    assert cell.note != "included in base price"
    assert _cell(statement, "final_value", "KERUI").total == 6000.0


def test_an_included_option_is_not_counted_as_unpriced(tmp_path):
    """It has no total by design, not by omission — the column excludes
    nothing and must not say it does."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1000.0, optional_items=[
        {"description": "Spares", "total": 30000.0, "included_in_base": True}])

    assert _cell(build_statement(root, "p"), "final_value", "KERUI").note is None


def test_one_vendors_unpriced_option_does_not_annotate_anothers_column(tmp_path):
    root = _project(tmp_path)
    project_vendors_extended(root, "MKON")
    _facts(root, "KERUI", base_price=1000.0,
           optional_items=[{"description": "Tools"}])
    _facts(root, "MKON", base_price=2000.0,
           optional_items=[{"description": "Tools", "total": 10.0}])

    statement = build_statement(root, "p")
    assert _cell(statement, "final_value", "KERUI").note is not None
    assert _cell(statement, "final_value", "MKON").note is None


def test_a_flag_is_not_a_price(tmp_path):
    """isinstance(True, int) is True, so an unguarded _money would read a
    stray bool as 1.0 and print it in a money column."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=True)

    cell = _cell(build_statement(root, "p"), "base_scope", "KERUI")
    assert cell is None or cell.total is None


def test_a_column_with_options_but_no_base_says_why_it_has_no_total(tmp_path):
    """Blank beats a partial sum, but a reviewer looking at a visible price
    and an empty FINAL VALUE deserves the reason. Options ALONE must trigger
    it — a test supplying options and freight together cannot tell this
    condition apart from one that demands both."""
    root = _project(tmp_path)
    _facts(root, "KERUI", optional_items=[{"description": "PEMS", "total": 83000.0}])

    final = _cell(build_statement(root, "p"), "final_value", "KERUI")
    assert final.total is None
    assert final.note == "base price not stated"


def test_a_column_with_freight_but_no_base_says_why_it_has_no_total(tmp_path):
    """The other half of the disjunction — freight alone, no options."""
    root = _project(tmp_path)
    _facts(root, "KERUI", freight_amount=17500.0)

    final = _cell(build_statement(root, "p"), "final_value", "KERUI")
    assert final.total is None
    assert final.note == "base price not stated"


def test_a_column_with_only_an_unpriced_option_still_says_why(tmp_path):
    """The option row is blank and so is FINAL VALUE. Without a note the
    column offers a reviewer nothing to distinguish 'no bid' from 'bid whose
    numbers we could not read'."""
    root = _project(tmp_path)
    _facts(root, "KERUI", optional_items=[{"description": "PEMS"}])

    final = _cell(build_statement(root, "p"), "final_value", "KERUI")
    assert final.total is None
    assert "base price not stated" in final.note
    assert "excludes 1 option(s) with no stated price" in final.note


def test_a_vendor_who_stated_nothing_gets_no_final_value_cell_at_all(tmp_path):
    """There is nothing to explain: no price, no option, no freight. An
    explanatory blank here would assert a bid that was never made."""
    root = _project(tmp_path)
    _stored(root, VendorBid(vendor="KERUI"))

    assert _cell(build_statement(root, "p"), "final_value", "KERUI") is None


def test_a_freight_of_zero_is_not_a_freight_charge(tmp_path):
    """freight_amount defaults to 0.0, so a quotation silent on freight
    stores the same value as one quoting free delivery. Emitting the row
    would put a 0.00 in a money column on every vendor in the corpus. Built
    from the pipeline's shape — commercial=None reaches the guard as None
    and cannot tell a `if freight` guard from a `if freight is not None`."""
    root = _project(tmp_path)
    _stored(root, VendorBid(vendor="KERUI", currency="USD", base_price=1000.0))

    assert _cell(build_statement(root, "p"), "freight", "KERUI") is None


def test_a_genuine_zero_normalises_to_zero_and_is_shown(tmp_path):
    """A 100% discount really does normalise to nothing. The Normalised row
    is blanked by an unknown BASE, not by a zero total — FINAL VALUE prints
    0.00 for this same bid, and the two rows must not disagree."""
    root = _project(tmp_path)
    _stored(root, VendorBid(vendor="KERUI", currency="USD", base_price=1000.0,
                            discount_pct=1.0))

    statement = build_statement(root, "p")
    assert _cell(statement, "final_value", "KERUI").total == 0.0
    assert _cell(statement, "normalised", "KERUI").total == 0.0


def test_an_unconvertible_bid_keeps_its_normalised_row_with_a_reason(tmp_path):
    """BUG-005 §1.3. A dropped row reads as 'nothing to compare here'; the
    buyer needs to know a rate is missing, not that the vendor is silent."""
    root = _project(tmp_path)
    project_vendors_extended(root, "EUROVEND")
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="EUROVEND", commercial={"currency": "EUR", "base_price": 1000.0},
        normalized={"vendor": "EUROVEND", "normalized_currency": "USD",
                    "normalized_total": None, "adjustments": [],
                    "extraction_status": "ok", "normalization_status": "no_fx_rate"}))

    statement = build_statement(root, "p")

    row = next(r for r in statement.rows if r.key == "normalised")
    cell = row.cells["EUROVEND"]
    assert cell.total is None
    assert "EUR" in cell.note


def test_no_fx_rate_with_an_unstated_base_gets_no_normalised_cell(tmp_path):
    """Fix round 2 review finding. _usable_rate returns "no_fx_rate" from
    currency/fx_rates alone, before it ever looks at base_price — currency
    and base_price are independent fields. A vendor who never stated a price
    (base_price stays the 0.0 unknown sentinel) must not be told a rate is
    the problem: setting one would not fill this cell, because base is still
    unknown. That vendor falls through to the pre-existing unknown-base
    path (no cell at all), the same as any other vendor with no base."""
    root = _project(tmp_path)
    project_vendors_extended(root, "EUROVEND")
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="EUROVEND", commercial={"currency": "EUR", "base_price": 0.0},
        normalized={"vendor": "EUROVEND", "normalized_currency": "USD",
                    "normalized_total": None, "adjustments": [],
                    "extraction_status": "ok", "normalization_status": "no_fx_rate"}))

    statement = build_statement(root, "p")

    cell = _cell(statement, "normalised", "EUROVEND")
    assert cell is None or cell.total is None
    assert cell is None or "no FX rate" not in (cell.note or "")


def test_a_flag_is_not_a_rate(tmp_path):
    """`Override.value` is an unvalidated Any, so a bool or a string can
    reach vat_rate and discount_pct. True read as a rate means a 100%
    discount and a 0.00 FINAL VALUE — the cheapest bid on the screen — and
    a string crashes build_statement outright."""
    root = _project(tmp_path)
    project_vendors_extended(root, "MKON")
    _facts(root, "KERUI", base_price=1000.0, discount_pct=True, vat_rate=True)
    _facts(root, "MKON", base_price=1000.0, discount_pct="10%")

    statement = build_statement(root, "p")
    for vendor in ("KERUI", "MKON"):
        assert _cell(statement, "final_value", vendor).total == 1000.0
        # 0.00, not absent: an unusable rate is no discount, and the row is
        # always emitted for a priced column. What matters is that `True`
        # did not become a 100% discount taking FINAL VALUE to zero.
        assert _cell(statement, "discount_amount", vendor).total == 0.0
        assert _cell(statement, "discount_pct", vendor) is None
    assert _cell(statement, "vat", "KERUI") is None


def test_building_twice_writes_nothing(tmp_path):
    """INV-S1."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1000.0)
    before = snapshots.get_generation(root, "p")

    build_statement(root, "p")
    build_statement(root, "p")

    assert snapshots.get_generation(root, "p") == before
