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


def test_building_twice_writes_nothing(tmp_path):
    """INV-S1."""
    root = _project(tmp_path)
    _facts(root, "KERUI", base_price=1000.0)
    before = snapshots.get_generation(root, "p")

    build_statement(root, "p")
    build_statement(root, "p")

    assert snapshots.get_generation(root, "p") == before
