# tests/test_procurement_normalize.py
from procurement.models import VendorBid
from procurement.normalize import normalize_bid


def _close(a, b):
    return abs(a - b) <= 0.01


def test_currency_and_freight():
    bid = VendorBid(vendor="A", currency="EUR", base_price=1000.0,
                    freight_amount=100.0, freight_included=False)
    n = normalize_bid(bid, "USD", {"EUR": 1.1})
    # 1000*1.1 = 1100 base; +100*1.1 = 110 freight -> 1210
    assert _close(n.normalized_total, 1210.0)
    assert n.normalized_currency == "USD"
    kinds = [a.kind for a in n.adjustments]
    assert "currency" in kinds and "freight" in kinds


def test_vat_excluded_same_currency():
    bid = VendorBid(vendor="B", currency="USD", base_price=1050.0,
                    vat_included=True, vat_rate=0.05, freight_included=True)
    n = normalize_bid(bid, "USD", {})
    assert _close(n.normalized_total, 1000.0)  # 1050 / 1.05
    assert any(a.kind == "vat" for a in n.adjustments)


def test_discount_applied():
    bid = VendorBid(vendor="C", currency="USD", base_price=1000.0,
                    freight_included=True, discount_pct=0.1)
    n = normalize_bid(bid, "USD", {})
    assert _close(n.normalized_total, 900.0)


def test_failed_bid_has_no_total():
    bid = VendorBid(vendor="D", extraction_status="failed")
    n = normalize_bid(bid, "USD", {})
    assert n.normalized_total is None
    assert n.extraction_status == "failed"
