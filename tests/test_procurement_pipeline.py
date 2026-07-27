import io
import zipfile
from procurement.project import create_project, unpack_vendor_zip
from procurement.pipeline import run_ingestion, load_dataset
from shared.llm.mock_client import MockLLMClient


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, d in entries.items():
            zf.writestr(n, d)
    return buf.getvalue()


def test_run_ingestion_end_to_end(tmp_path):
    create_project(str(tmp_path), "Proj", target_currency="USD")
    z = tmp_path / "v.zip"
    # .txt quotes so read_text does not attempt PDF parsing; the mock ignores content.
    z.write_bytes(_zip({
        "KERUI/Quotation.txt": b"base price 1000",
        "ADPOWER/Quotation.txt": b"base price 1000",
    }))
    unpack_vendor_zip(str(tmp_path), "proj", str(z))
    client = MockLLMClient(response={"currency": "USD", "base_price": 1000.0,
                                     "freight_included": True})
    result = run_ingestion(str(tmp_path), "proj", client)

    assert len(result["comparison"]["rows"]) == 2
    assert all(r["extraction_status"] == "ok" for r in result["comparison"]["rows"])
    # persisted
    ds = load_dataset(str(tmp_path), "proj")
    assert ds is not None and len(ds["bids"]) == 2
