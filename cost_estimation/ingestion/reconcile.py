from pydantic import BaseModel
from cost_estimation.models.schema import CostItem, WorkPackage

TOL = 0.01


class Discrepancy(BaseModel):
    kind: str
    location: str
    expected: float
    actual: float


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= TOL * max(1.0, abs(b))


def check_item(item: CostItem) -> Discrepancy | None:
    if not item.priced or item.rate_buildup is None or item.quantity is None or item.total is None:
        return None
    expected = item.quantity * item.rate_buildup.unit_price
    # Tolerance anchored to expected value: _close(actual, expected) scales by expected
    if _close(item.total, expected):
        return None
    return Discrepancy(kind="item_total", location=item.code, expected=expected, actual=item.total)


def check_rollup(wp: WorkPackage) -> Discrepancy | None:
    if wp.summary_rollup is None:
        return None
    total = sum(i.total for i in wp.cost_items if i.total is not None)
    expected = wp.summary_rollup.total_value
    if _close(total, expected):
        return None
    return Discrepancy(kind="rollup", location=wp.name, expected=expected, actual=total)
