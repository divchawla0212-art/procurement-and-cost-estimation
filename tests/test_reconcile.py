from shared.provenance import ProvenanceRef
from cost_estimation.models.schema import CostItem, RateBuildUp, WorkPackage, SummaryRollup
from cost_estimation.ingestion.reconcile import check_item, check_rollup


def _prov():
    return ProvenanceRef(document_path="c.xlsx", extractor="xlsx")


def _item(qty, price, total):
    return CostItem(code="X", description="d", uom="m", quantity=qty,
                    rate_buildup=RateBuildUp(unit_price=price, provenance=_prov()),
                    total=total, priced=True, provenance=_prov())


def test_item_arithmetic_ok():
    assert check_item(_item(10, 5.0, 50.0)) is None


def test_item_arithmetic_mismatch():
    d = check_item(_item(10, 5.0, 999.0))
    assert d is not None and d.kind == "item_total"
    assert d.expected == 50.0


def test_rollup_matches_summary():
    wp = WorkPackage(name="w", cost_items=[_item(10, 5.0, 50.0), _item(2, 10.0, 20.0)],
                     summary_rollup=SummaryRollup(total_value=70.0))
    assert check_rollup(wp) is None


def test_rollup_mismatch():
    wp = WorkPackage(name="w", cost_items=[_item(10, 5.0, 50.0)],
                     summary_rollup=SummaryRollup(total_value=999.0))
    d = check_rollup(wp)
    assert d is not None and d.kind == "rollup"
