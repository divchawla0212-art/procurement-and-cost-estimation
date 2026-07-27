import os
import openpyxl
import pytest
from procurement.loaders import read_xlsx_text, read_text, read_pdf_text


def test_read_xlsx_text(tmp_path):
    path = str(tmp_path / "q.xlsx")
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Offer"
    ws["A1"] = "Base Price"; ws["B1"] = 585000
    wb.save(path)
    text = read_xlsx_text(path)
    assert "Base Price" in text and "585000" in text and "Offer" in text


def test_read_text_dispatches_plain(tmp_path):
    p = tmp_path / "note.txt"; p.write_text("hello quote", encoding="utf-8")
    assert "hello quote" in read_text(str(p))


def test_read_pdf_text_uses_llm_fallback_when_thin(tmp_path):
    # A non-PDF byte blob: pdftotext yields nothing and pypdf raises (caught),
    # so the injected LLM fallback must be used. This proves the robustness path
    # without any network or key.
    fake = tmp_path / "scan.pdf"; fake.write_bytes(b"not a real pdf")
    called = {}

    def fb(p):
        called["path"] = p
        return "TRANSCRIBED VENDOR QUOTE TEXT " * 20

    text = read_pdf_text(str(fake), llm_fallback=fb)
    assert "TRANSCRIBED VENDOR QUOTE" in text
    assert called["path"] == str(fake)


def test_read_pdf_text_no_fallback_returns_empty_on_bad_pdf(tmp_path):
    fake = tmp_path / "scan.pdf"; fake.write_bytes(b"not a real pdf")
    assert read_pdf_text(str(fake)).strip() == ""  # never raises


REAL_PDF = "data/procurement-data/ADPOWER/ADP-13158-2024-935.pdf"


@pytest.mark.skipif(not os.path.exists(REAL_PDF), reason="sample data not present")
def test_read_pdf_text_real():
    text = read_pdf_text(REAL_PDF)
    assert len(text) > 200
    assert "Baudouin" in text or "Generator" in text or "QUOTATION" in text.upper()
