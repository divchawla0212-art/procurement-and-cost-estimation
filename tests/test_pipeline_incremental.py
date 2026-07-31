import io
import shutil
import zipfile
from procurement.project import create_project, unpack_vendor_zip, load_project
from procurement.pipeline import run_ingestion, inventory_documents, load_dataset
from procurement.store import events, snapshots
from procurement.store.models import Override
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


def test_load_dataset_migrates_a_legacy_project_without_a_paid_rerun(tmp_path):
    """A pre-store project must show its results on render, not only after the
    user clicks 'Run ingestion' and pays for a full round of LLM calls."""
    import json as _json
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    project = load_project(root, "p")
    project.vendors = ["KERUI", "MKON"]
    from procurement.project import save_project
    save_project(root, project)
    with open(tmp_path / "p" / "dataset.json", "w", encoding="utf-8") as fh:
        _json.dump({
            "bids": [{"vendor": "KERUI", "base_price": 1000.0, "currency": "USD"},
                     {"vendor": "MKON", "base_price": 900.0, "currency": "USD"}],
            "normalized": [{"vendor": "KERUI", "normalized_currency": "USD",
                            "normalized_total": 1000.0},
                           {"vendor": "MKON", "normalized_currency": "USD",
                            "normalized_total": 900.0}],
            "comparison": {"target_currency": "USD", "rows": []},
        }, fh)

    dataset = load_dataset(root, "p")
    assert dataset is not None, "a legacy project must not lose its results"
    assert {b["vendor"] for b in dataset["bids"]} == {"KERUI", "MKON"}
    assert len(dataset["comparison"]["rows"]) == 2


def _override(path, value, extracted):
    return Override(field_path=path, value=value, extracted_value=extracted,
                    author="rahul", at="2026-07-29T10:00:00Z", reason="quote revision by email")


def test_override_survives_rerun_and_flows_into_normalized(tmp_path):
    """The exit criterion: a human correction written into facts.json is still
    the value the comparison shows after a fresh, disagreeing extraction."""
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())

    facts = snapshots.load_facts(root, "p", "KERUI")
    assert facts.commercial["base_price"] == 1000.0
    # spec-conformant path: dotted, relative to the snapshot root (VendorFacts)
    facts.overrides = [_override("commercial.base_price", 1500.0, 1000.0)]
    snapshots.save_facts(root, "p", facts)

    disagreeing = MockLLMClient(response={"currency": "USD", "base_price": 2000.0,
                                          "freight_included": True})
    run_ingestion(root, "p", disagreeing, force=True)

    after = snapshots.load_facts(root, "p", "KERUI")
    assert after.commercial["base_price"] == 1500.0, "the human correction must win"
    assert after.normalized["normalized_total"] == 1500.0, \
        "and must flow through normalization into the comparison"
    assert len(after.overrides) == 1
    assert after.overrides[0].conflict is True
    assert after.overrides[0].extracted_value == 2000.0
    assert after.overrides[0].value == 1500.0

    dataset = load_dataset(root, "p")
    kerui = next(b for b in dataset["bids"] if b["vendor"] == "KERUI")
    assert kerui["base_price"] == 1500.0


def test_override_conflict_is_recorded_as_an_event(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    facts = snapshots.load_facts(root, "p", "KERUI")
    facts.overrides = [_override("commercial.base_price", 1500.0, 1000.0)]
    snapshots.save_facts(root, "p", facts)

    disagreeing = MockLLMClient(response={"currency": "USD", "base_price": 2000.0,
                                          "freight_included": True})
    run_ingestion(root, "p", disagreeing, force=True)
    assert "override.conflicted" in [e.action for e in events.read_events(root, "p")]


def test_a_second_vendor_zip_adds_to_the_project_rather_than_replacing_it(tmp_path):
    """Vendors arrive one archive at a time, and the earlier ones must survive.

    A buyer who uploads ADPOWER.zip and then MKON.zip has withdrawn nothing —
    both folders are on disk. Deriving the roster from the last archive alone
    evicted every earlier vendor from `project.vendors`, so `inventory_documents`
    never walked their files (they were not extracted, merely absent) and the
    stale-vendor sweep then deleted whatever an earlier run had stored for them.
    Withdrawal is removing the folder — the test below — never uploading another.
    """
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")

    z1 = tmp_path / "adpower.zip"
    z1.write_bytes(_zip({"ADPOWER/Quotation.txt": b"base price 1200"}))
    unpack_vendor_zip(root, "p", str(z1))

    z2 = tmp_path / "mkon.zip"
    z2.write_bytes(_zip({"MKON/Quotation.txt": b"base price 900"}))
    returned = unpack_vendor_zip(root, "p", str(z2))

    assert load_project(root, "p").vendors == ["ADPOWER", "MKON"]
    assert returned == ["ADPOWER", "MKON"], \
        "the portal prints this list back as the project's roster"
    assert {d.vendor for d in inventory_documents(root, "p")} == {"ADPOWER", "MKON"}

    run_ingestion(root, "p", _client())

    assert snapshots.list_fact_vendors(root, "p") == ["ADPOWER", "MKON"], \
        "the earlier archive's vendor must still be extracted"
    assert sorted(r["vendor"] for r in
                  load_dataset(root, "p")["comparison"]["rows"]) == ["ADPOWER", "MKON"]
    assert "vendor.pruned" not in [e.action for e in events.read_events(root, "p")], \
        "a vendor whose folder is still on disk was never withdrawn"


def test_withdrawn_vendor_is_dropped_from_the_store_and_the_comparison(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    assert set(snapshots.list_fact_vendors(root, "p")) == {"KERUI", "MKON"}

    # the buyer withdraws MKON and re-uploads only the remaining vendor
    shutil.rmtree(tmp_path / "p" / "vendors" / "MKON")
    z2 = tmp_path / "v2.zip"
    z2.write_bytes(_zip({"KERUI/Quotation.txt": b"base price 1000",
                         "KERUI/BOM.txt": b"bill of materials"}))
    unpack_vendor_zip(root, "p", str(z2))
    assert load_project(root, "p").vendors == ["KERUI"]

    run_ingestion(root, "p", _client())

    assert snapshots.list_fact_vendors(root, "p") == ["KERUI"], \
        "a withdrawn vendor's facts must not survive on disk"
    dataset = load_dataset(root, "p")
    assert [b["vendor"] for b in dataset["bids"]] == ["KERUI"]
    assert [r["vendor"] for r in dataset["comparison"]["rows"]] == ["KERUI"]
