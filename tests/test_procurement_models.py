from shared.provenance import ProvenanceRef
from procurement.models import (
    OptionalItem, BidExtraction, VendorBid, NormalizedBid,
    NormalizationAdjustment, ComparisonRow, ComparisonTable, Project,
)


def test_vendor_bid_composes_extraction_plus_metadata():
    bid = VendorBid(
        vendor="ADPOWER", currency="EUR", base_price=1000.0,
        vat_included=True, vat_rate=0.05, freight_amount=100.0,
        source_document="q.pdf",
        provenance=ProvenanceRef(document_path="q.pdf", extractor="anthropic:v1"),
    )
    assert bid.vendor == "ADPOWER"
    assert bid.base_price == 1000.0
    assert bid.extraction_status == "ok"
    assert VendorBid.model_validate(bid.model_dump()).currency == "EUR"


def test_failed_bid_constructs_with_defaults():
    bid = VendorBid(vendor="AESL", extraction_status="failed", notes="no quote found")
    assert bid.extraction_status == "failed"
    assert bid.base_price == 0.0 and bid.currency == ""


def test_comparison_table_holds_rows():
    t = ComparisonTable(target_currency="USD", rows=[
        ComparisonRow(vendor="A", currency="USD", raw_base_price=100.0,
                      normalized_total=100.0, delivery_terms=None, delivery_time=None,
                      payment_terms=None, engine_make=None, extraction_status="ok")
    ])
    assert t.rows[0].vendor == "A"
