from procurement.models import VendorBid, NormalizedBid
from procurement.compare import build_comparison


def test_build_comparison_sorts_and_includes_failed():
    bids = [
        VendorBid(vendor="A", currency="USD", base_price=1200.0, delivery_terms="FCA"),
        VendorBid(vendor="B", currency="USD", base_price=1000.0, delivery_terms="CIF"),
        VendorBid(vendor="C", extraction_status="failed"),
    ]
    norm = [
        NormalizedBid(vendor="A", normalized_currency="USD", normalized_total=1200.0),
        NormalizedBid(vendor="B", normalized_currency="USD", normalized_total=1000.0),
        NormalizedBid(vendor="C", normalized_currency="USD", normalized_total=None, extraction_status="failed"),
    ]
    table = build_comparison(bids, norm, "USD")
    assert [r.vendor for r in table.rows] == ["B", "A", "C"]
    assert table.rows[0].normalized_total == 1000.0
    assert table.rows[-1].extraction_status == "failed"
    assert table.rows[1].delivery_terms == "FCA"
