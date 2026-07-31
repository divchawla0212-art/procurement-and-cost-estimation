import os
import glob
import pytest
from procurement.extract import extract_bid
from shared.llm.factory import get_client

VENDOR_DIR = "data/procurement-data/ADPOWER"


@pytest.mark.skipif(
    not (os.getenv("ANTHROPIC_API_KEY") and os.path.isdir(VENDOR_DIR)),
    reason="needs ANTHROPIC_API_KEY and sample data",
)
def test_live_extract_adpower(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    files = glob.glob(os.path.join(VENDOR_DIR, "*"))
    bid = extract_bid("ADPOWER", files, get_client())
    assert bid.extraction_status == "ok"
    assert bid.base_price > 0
    assert bid.currency  # e.g. EUR
