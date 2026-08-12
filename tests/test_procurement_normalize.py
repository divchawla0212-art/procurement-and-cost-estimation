# tests/test_procurement_normalize.py
import pytest
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


def _eur_bid():
    return VendorBid(vendor="A", currency="EUR", base_price=1000.0, freight_included=True)


def test_missing_fx_rate_leaves_the_bid_unconverted():
    """Replaces test_missing_fx_rate_is_surfaced, which asserted the 1:1 assumption."""
    n = normalize_bid(_eur_bid(), "USD", {})
    assert n.normalized_total is None
    assert n.normalization_status == "no_fx_rate"


def test_an_unconvertible_bid_records_no_adjustments():
    # A list of adjustments leading to no total describes arithmetic that did
    # not happen.
    assert normalize_bid(_eur_bid(), "USD", {}).adjustments == []


def test_unconvertible_is_not_the_same_as_a_failed_extraction():
    # Both produce normalized_total=None, and they send the user to different
    # screens: one needs a rate, the other needs the document re-read.
    n = normalize_bid(_eur_bid(), "USD", {})
    assert n.extraction_status == "ok"
    assert n.normalization_status == "no_fx_rate"

    failed = normalize_bid(VendorBid(vendor="B", extraction_status="failed"), "USD", {})
    assert failed.extraction_status == "failed"
    assert failed.normalization_status == "ok"


@pytest.mark.parametrize("rate", [0.0, -1.08])
def test_a_non_positive_rate_is_treated_as_missing(rate):
    # A rate of 0 converts the bid to 0.0 and sorts it first -- the same defect
    # in different clothing.
    n = normalize_bid(_eur_bid(), "USD", {"EUR": rate})
    assert n.normalized_total is None
    assert n.normalization_status == "no_fx_rate"


def test_a_configured_rate_still_converts():
    n = normalize_bid(_eur_bid(), "USD", {"EUR": 1.08})
    assert abs(n.normalized_total - 1080.0) <= 0.01
    assert n.normalization_status == "ok"


def test_a_bid_already_in_the_target_currency_is_never_flagged():
    bid = VendorBid(vendor="A", currency="USD", base_price=1000.0, freight_included=True)
    n = normalize_bid(bid, "USD", {})
    assert abs(n.normalized_total - 1000.0) <= 0.01
    assert n.normalization_status == "ok"


def test_a_bid_with_no_extracted_currency_keeps_todays_behaviour():
    # Pinned deliberately: an empty currency is treated as the target currency,
    # as it is today. Out of scope for BUG-005; this test makes a future change
    # to it deliberate.
    bid = VendorBid(vendor="A", currency="", base_price=1000.0, freight_included=True)
    n = normalize_bid(bid, "USD", {})
    assert abs(n.normalized_total - 1000.0) <= 0.01
    assert n.normalization_status == "ok"
