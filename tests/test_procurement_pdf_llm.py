import base64
from procurement.pdf_llm import _build_document_block


def test_build_document_block_encodes_pdf():
    block = _build_document_block(b"hello-pdf")
    assert block["type"] == "document"
    assert block["source"]["type"] == "base64"
    assert block["source"]["media_type"] == "application/pdf"
    assert base64.standard_b64decode(block["source"]["data"]) == b"hello-pdf"
