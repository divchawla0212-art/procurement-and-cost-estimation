"""`run_ingestion(pdf_reader=...)` reaches the bids, and only the bids.

The arm must apply to vendor documents, because that is what the A/B compares,
and must *not* apply to the client's RFQ documents, because the requirement set
they produce is the denominator of the metric: compliance is counted per
requirement x vendor, so an arm that could re-read the client spec could move
the denominator and make the three arms' flag counts incomparable.
"""
import io
import zipfile

import pytest

from procurement import loaders, pipeline
from procurement.pipeline import run_ingestion
from procurement.project import create_project, unpack_vendor_zip
from procurement.store import snapshots
from shared.llm.mock_client import MockLLMClient

_PAD = (" This synthetic fixture body is padded with filler prose so its "
        "character count clears the pipeline's minimum-extractable-text "
        "guard, letting the reader selection under test run rather than "
        "the guard itself. ") * 3


@pytest.fixture
def stub_readers(monkeypatch):
    """Two readers that are trivially told apart in the stored text_source."""
    monkeypatch.setattr(loaders, "_pdftotext", lambda p: "FROM-PDFTOTEXT" + _PAD)
    monkeypatch.setattr(loaders, "_pypdf", lambda p: "FROM-PYPDF" + _PAD)


def _project(tmp_path):
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("KERUI/Quotation of Gas Generator.pdf", b"%PDF-1.4 stub")
    z = tmp_path / "v.zip"
    z.write_bytes(buf.getvalue())
    unpack_vendor_zip(root, "p", str(z))

    rdir = tmp_path / "p" / "requirements"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "MR Gas Genset Specification.pdf").write_bytes(b"%PDF-1.4 stub")
    return root


def _source_by_kind(root):
    """(vendor document text_source, rfq document text_source)."""
    docs = snapshots.load_documents(root, "p")
    vendor = next(d for d in docs if d.vendor is not None)
    rfq = next(d for d in docs if d.vendor is None)
    return vendor.text_source or "", rfq.text_source or ""


def test_arm_reads_vendor_documents_with_the_named_reader(stub_readers, tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", MockLLMClient(response={}), pdf_reader="pypdf")
    vendor_source, _rfq = _source_by_kind(root)
    assert vendor_source.startswith("pypdf:")


def test_arm_never_reaches_the_rfq_side(stub_readers, tmp_path):
    """The denominator must not move between arms."""
    root = _project(tmp_path)
    run_ingestion(root, "p", MockLLMClient(response={}), pdf_reader="pypdf")
    _vendor, rfq_source = _source_by_kind(root)
    assert rfq_source.startswith("pdftotext:"), (
        "the client's RFQ documents must be read by the default chain in every "
        "arm, or the requirement set differs between arms")


def test_default_run_is_unchanged_on_both_sides(stub_readers, tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", MockLLMClient(response={}))
    vendor_source, rfq_source = _source_by_kind(root)
    assert vendor_source.startswith("pdftotext:")
    assert rfq_source.startswith("pdftotext:")
