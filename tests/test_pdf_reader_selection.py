"""The `pdf_reader` seam that lets the parser A/B run one reader per arm.

The load-bearing property here is *purity*: an arm named `pypdf` must read with
pypdf and nothing else. The default chain in `read_pdf_text_with_source` exists
to rescue a thin read, which is right in production and fatal in an experiment
-- a pypdf arm that falls through to pdftotext reports pdftotext's numbers
under pypdf's name, and the better the parser under test, the less any fallback
fires, so every arm converges and the experiment reads "no difference"
regardless of the truth.
"""
import pytest

from procurement import loaders
from procurement.loaders import (read_pdf_text_with_source,
                                 read_text_with_source)


@pytest.fixture
def stub_readers(monkeypatch):
    """pdftotext succeeds, pypdf returns nothing -- the shape that makes a
    silent fallback visible. Real PDFs cannot be relied on to produce it."""
    monkeypatch.setattr(loaders, "_pdftotext", lambda p: "PDFTOTEXT " * 60)
    monkeypatch.setattr(loaders, "_pypdf", lambda p: "")


def test_default_is_unchanged_and_still_falls_back(stub_readers, tmp_path):
    """`pdf_reader=None` is production's chain, untouched. Guarding this is
    what lets the seam land without moving any existing test."""
    pdf = tmp_path / "d.pdf"; pdf.write_bytes(b"%PDF-1.4")
    text, source = read_pdf_text_with_source(str(pdf))
    assert source == "pdftotext"
    assert "PDFTOTEXT" in text


def test_pypdf_arm_does_not_fall_back_to_pdftotext(stub_readers, tmp_path):
    """The failure this whole module exists to prevent."""
    pdf = tmp_path / "d.pdf"; pdf.write_bytes(b"%PDF-1.4")
    text, source = read_pdf_text_with_source(str(pdf), pdf_reader="pypdf")
    assert source == "pypdf"
    assert text.strip() == ""
    assert "PDFTOTEXT" not in text


def test_pure_arm_ignores_the_llm_fallback(stub_readers, tmp_path):
    """An LLM transcription rescue would make the experiment measure Claude's
    vision rather than the parser, and would fire *least* for the best parser."""
    pdf = tmp_path / "d.pdf"; pdf.write_bytes(b"%PDF-1.4")
    called = []

    def fb(path):
        called.append(path)
        return "TRANSCRIBED " * 40

    text, source = read_pdf_text_with_source(str(pdf), llm_fallback=fb,
                                             pdf_reader="pypdf")
    assert called == [], "a pure arm must never reach the LLM fallback"
    assert text.strip() == ""
    assert source == "pypdf"


def test_pdftotext_arm_reads_with_pdftotext(stub_readers, tmp_path):
    pdf = tmp_path / "d.pdf"; pdf.write_bytes(b"%PDF-1.4")
    text, source = read_pdf_text_with_source(str(pdf), pdf_reader="pdftotext")
    assert source == "pdftotext"
    assert "PDFTOTEXT" in text


def test_read_text_with_source_threads_the_reader(stub_readers, tmp_path):
    """The pipeline reaches the reader through `read_text_with_source`, so the
    parameter has to survive that hop or the arm silently reverts to default."""
    pdf = tmp_path / "d.pdf"; pdf.write_bytes(b"%PDF-1.4")
    text, source = read_text_with_source(str(pdf), pdf_reader="pypdf")
    assert source == "pypdf"
    assert text.strip() == ""


def test_non_pdf_ignores_the_reader_argument(tmp_path):
    """MKON's datasheet is .xlsx and is the experiment's control: openpyxl must
    read it identically in every arm, or the control measures the arm."""
    import openpyxl
    path = str(tmp_path / "d.xlsx")
    wb = openpyxl.Workbook(); ws = wb.active; ws["A1"] = "Rated output"
    wb.save(path)
    for arm in (None, "pypdf", "pdftotext", "llamaparse"):
        text, source = read_text_with_source(path, pdf_reader=arm)
        assert source == "xlsx"
        assert "Rated output" in text


def test_unknown_reader_is_an_error(tmp_path):
    """A typo'd arm name must not silently become the default chain and report
    a fourth set of numbers under a name nobody implemented."""
    pdf = tmp_path / "d.pdf"; pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(ValueError, match="unknown pdf_reader"):
        read_pdf_text_with_source(str(pdf), pdf_reader="pdfplumber")


def test_llamaparse_without_the_dependency_raises(monkeypatch, tmp_path):
    """Loudly, not as an empty string. An unconfigured LlamaParse arm that
    returned "" would be indistinguishable from a LlamaParse arm that read the
    document and found nothing -- and would libel the parser under test."""
    monkeypatch.delenv("LLAMA_CLOUD_API_KEY", raising=False)
    pdf = tmp_path / "d.pdf"; pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(RuntimeError, match="LLAMA_CLOUD_API_KEY"):
        read_pdf_text_with_source(str(pdf), pdf_reader="llamaparse")
