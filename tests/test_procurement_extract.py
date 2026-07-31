from procurement.extract import extract_bid
from procurement.models import VendorBid
from shared.llm.mock_client import MockLLMClient


def test_extract_bid_from_mock(tmp_path):
    # Use .txt so read_text does not attempt PDF parsing; the mock ignores content.
    quote = tmp_path / "Quotation-X.txt"; quote.write_text("Base price USD 585000, FCA")
    other = tmp_path / "BOM-notes.txt"; other.write_text("bom line items")
    canned = {
        "currency": "USD", "base_price": 585000.0, "vat_included": False,
        "freight_included": False, "discount_pct": 0.0, "delivery_terms": "FCA",
        "engine_make": "Waukesha",
    }
    client = MockLLMClient(response=canned)
    bid = extract_bid("KERUI", [str(quote), str(other)], client)
    assert isinstance(bid, VendorBid)
    assert bid.extraction_status == "ok"
    assert bid.vendor == "KERUI"
    assert bid.base_price == 585000.0
    assert bid.source_document.endswith("Quotation-X.txt")
    assert bid.provenance is not None


def test_extract_bid_no_quote_is_failed():
    client = MockLLMClient(response={})
    bid = extract_bid("AESL", [], client)
    assert bid.extraction_status == "failed"
    assert "no quote" in (bid.notes or "").lower()


def test_extract_bid_exception_in_classification(tmp_path):
    # Test graceful failure when LLM raises exception - verifies exception branch
    quote = tmp_path / "Quotation-X.txt"; quote.write_text("Base price USD 585000, FCA")

    class BoomClient:
        """Fake client that raises exception in classify_structure"""
        def classify_structure(self, *args, **kwargs):
            raise RuntimeError("boom")

    client = BoomClient()
    bid = extract_bid("VENDOR", [str(quote)], client)
    assert bid.extraction_status == "failed"
    assert "extraction error" in (bid.notes or "").lower()
    assert bid.provenance is not None
    assert bid.provenance.document_path.endswith("Quotation-X.txt")
