import io
import zipfile
from procurement.project import create_project, unpack_vendor_zip, load_project
from procurement.pipeline import run_ingestion, inventory_documents, load_dataset
from procurement.store import events, snapshots
from shared.llm.mock_client import MockLLMClient


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, d in entries.items():
            zf.writestr(n, d)
    return buf.getvalue()


def _project(tmp_path):
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    z = tmp_path / "v.zip"
    z.write_bytes(_zip({
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/BOM.txt": b"bill of materials",
        "MKON/Quotation.txt": b"base price 900",
    }))
    unpack_vendor_zip(root, "p", str(z))
    return root


def _client():
    return MockLLMClient(response={"currency": "USD", "base_price": 1000.0,
                                   "freight_included": True})


def test_inventory_records_every_vendor_file(tmp_path):
    root = _project(tmp_path)
    docs = inventory_documents(root, "p")
    assert len(docs) == 3
    assert all(d.content_sha256 for d in docs)
    assert {d.vendor for d in docs} == {"KERUI", "MKON"}


def test_first_run_extracts_one_document_per_vendor(tmp_path):
    root = _project(tmp_path)
    client = _client()
    run_ingestion(root, "p", client)
    assert len(client.calls) == 2                 # the quote only, not the BOM
    docs = {d.path: d for d in snapshots.load_documents(root, "p")}
    assert sum(1 for d in docs.values() if d.extraction_status == "ok") == 2
    assert sum(1 for d in docs.values() if d.extraction_status == "skipped") == 1


def test_rerun_with_no_changes_makes_zero_llm_calls(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    second = _client()
    run_ingestion(root, "p", second)
    assert second.calls == []


def test_touching_one_document_reextracts_exactly_that_one(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    (tmp_path / "p" / "vendors" / "KERUI" / "Quotation.txt").write_bytes(b"base price 2000")
    second = _client()
    run_ingestion(root, "p", second)
    assert len(second.calls) == 1


def test_force_reextracts_everything(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    second = _client()
    run_ingestion(root, "p", second, force=True)
    assert len(second.calls) == 2


def test_facts_are_stored_per_vendor(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    kerui = snapshots.load_facts(root, "p", "KERUI")
    assert kerui.commercial["base_price"] == 1000.0
    assert kerui.normalized["normalized_total"] == 1000.0


def test_run_appends_events(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    actions = [e.action for e in events.read_events(root, "p")]
    assert actions[0] == "run.started" and actions[-1] == "run.finished"
    assert "document.extracted" in actions
    assert "document.skipped" in actions


def test_status_reports_failures_instead_of_always_done(tmp_path):
    root = _project(tmp_path)
    # a client that always raises -> every extraction fails
    class Boom:
        supports_vision = True

        def __init__(self):
            self.calls = []      # instance attribute: a mutable class attribute
                                 # would be shared across every instance

        def classify_structure(self, *a, **k):
            raise RuntimeError("no")

    run_ingestion(root, "p", Boom())
    assert load_project(root, "p").status == "failed"


def test_partial_failure_is_reported_distinctly(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    # corrupt one vendor's quote so only it fails on the next forced run
    (tmp_path / "p" / "vendors" / "MKON" / "Quotation.txt").write_bytes(b"x")

    class OneBad:
        supports_vision = True

        def __init__(self):
            self.calls = []

        def classify_structure(self, prompt, schema, context_text, images=None):
            self.calls.append(context_text)
            if context_text.strip() == "x":
                raise RuntimeError("unreadable")
            return {"currency": "USD", "base_price": 1000.0, "freight_included": True}

    run_ingestion(root, "p", OneBad(), force=True)
    assert load_project(root, "p").status == "done_with_failures"


def test_status_is_failed_when_nothing_extracted_and_nothing_failed(tmp_path):
    # no vendors were ever unpacked -> no quote is ever selected -> the run
    # extracts nothing and fails nothing; "done" would be a lie here.
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    run_ingestion(root, "p", _client())
    assert load_project(root, "p").status == "failed"
    assert load_dataset(root, "p") is None


def test_status_is_done_on_a_clean_run(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    assert load_project(root, "p").status == "done"
