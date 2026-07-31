from procurement.quote_select import pick_quote


def test_picks_quotation_over_bom_and_datasheet():
    files = [
        "/v/MKON/Quotation-MKON.pdf",
        "/v/MKON/Attachment-2 Vendor Deviation Form.pdf",
        "/v/MKON/DOD-30201 DataSheet Gas Generator.xlsx",
        "/v/MKON/Attachment-7 Load List.pdf",
    ]
    assert pick_quote(files) == "/v/MKON/Quotation-MKON.pdf"


def test_prefers_techno_commercial_proposal():
    files = [
        "/v/AESL/Techno Commercial proposal AESL-GTC-60808 - REV00.pdf",
    ]
    assert pick_quote(files).endswith("REV00.pdf")


def test_returns_none_for_empty():
    assert pick_quote([]) is None
