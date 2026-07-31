from pathlib import Path
from shared.provenance import ProvenanceRef
from procurement.models import BidExtraction, VendorBid
from procurement.quote_select import pick_quote
from procurement.loaders import read_text

_PROMPT = Path(__file__).parents[1] / "shared" / "llm" / "prompts" / "bid_extract_v1.txt"


def extract_bid(vendor: str, files: list[str], client, pdf_fallback=None) -> VendorBid:
    quote = pick_quote(files)
    if quote is None:
        return VendorBid(vendor=vendor, extraction_status="failed",
                         notes="no quote document found in vendor folder")
    try:
        text = read_text(quote, llm_fallback=pdf_fallback)
        prompt = _PROMPT.read_text(encoding="utf-8")
        result = client.classify_structure(prompt, BidExtraction, text)
        extraction = BidExtraction.model_validate(result)
        return VendorBid(
            vendor=vendor,
            source_document=quote,
            provenance=ProvenanceRef(document_path=quote, extractor="anthropic:bid_extract_v1"),
            extraction_status="ok",
            **extraction.model_dump(),
        )
    except Exception as exc:  # graceful per-vendor failure
        return VendorBid(vendor=vendor, source_document=quote,
                         provenance=ProvenanceRef(document_path=quote, extractor="anthropic:bid_extract_v1"),
                         extraction_status="failed", notes=f"extraction error: {exc}")
