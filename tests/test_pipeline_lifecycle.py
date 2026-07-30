"""Two-run lifecycle tests.

Every defect that survived phase 2's per-task reviews was a lifecycle defect:
invisible inside a single run of a single module, and only observable when the
corpus mutates between two runs. Single-run tests structurally cannot catch
them, so this file is organised as a mutation matrix — each test performs one
mutation between run 1 and run 2 and asserts what the store must look like
afterwards.
"""
import io
import zipfile

from procurement.pipeline import run_ingestion, load_dataset
from procurement.store import events, snapshots
from shared.llm.mock_client import MockLLMClient       # noqa: F401  (fixtures)
from procurement.project import create_project, unpack_vendor_zip


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, d in entries.items():
            zf.writestr(n, d)
    return buf.getvalue()


def _project(tmp_path, entries):
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    z = tmp_path / "v.zip"
    z.write_bytes(_zip(entries))
    unpack_vendor_zip(root, "p", str(z))
    return root


def _write(tmp_path, vendor, name, body):
    (tmp_path / "p" / "vendors" / vendor / name).write_bytes(body)


def _docs(root, slug="p"):
    """Documents keyed by basename."""
    return {d.path.rsplit("/", 1)[-1]: d for d in snapshots.load_documents(root, slug)}


def _actions(root, slug="p"):
    return [e.action for e in events.read_events(root, slug)]


class RoutingClient:
    """Answers whichever output shape it is handed, and records the kind of
    every call so a test can assert what a run actually paid for.

    `fail_kind` makes it raise for one shape only, so a transient failure can be
    simulated for classification or for a single extractor while the rest of the
    run stays healthy.
    """
    supports_vision = True

    def __init__(self, doc_class="other", fail_kind=None):
        self.calls: list[str] = []
        self.doc_class = doc_class
        self.fail_kind = fail_kind

    @staticmethod
    def _kind(schema):
        for field in ("facts", "deviations", "doc_class"):
            if field in schema.model_fields:
                return field
        return "quotation"

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        kind = self._kind(output_schema)
        self.calls.append(kind)
        if kind == self.fail_kind:
            raise RuntimeError("provider unavailable")
        if kind == "facts":
            return {"facts": [{"parameter": "continuous_rating", "value": 550.0,
                               "unit": "kW", "verbatim": "550 kW"}]}
        if kind == "deviations":
            return {"deviations": [{"clause_ref": "4.2.7", "statement": "60 Hz",
                                    "disposition": "deviate"}]}
        if kind == "doc_class":
            return {"doc_class": self.doc_class}
        return {"currency": "USD", "base_price": 1000.0, "freight_included": True}


# --------------------------------------------------------------------------
# C1 - facts of documents that stopped being extractable must be pruned
# --------------------------------------------------------------------------

def test_a_newer_revision_prunes_the_obsolete_datasheets_facts(tmp_path):
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/01 DataSheet A.txt": b"Continuous rating 550 kW",
    })
    run_ingestion(root, "p", RoutingClient())
    old_id = _docs(root)["01 DataSheet A.txt"].doc_id
    assert [f["doc_id"] for f in snapshots.load_facts(root, "p", "KERUI").technical] == [old_id]

    _write(tmp_path, "KERUI", "01 DataSheet A Rev1.txt", b"Continuous rating 600 kW")
    run_ingestion(root, "p", RoutingClient())

    docs = _docs(root)
    new_id = docs["01 DataSheet A Rev1.txt"].doc_id
    assert docs["01 DataSheet A.txt"].superseded_by == new_id
    technical = snapshots.load_facts(root, "p", "KERUI").technical
    assert [f["doc_id"] for f in technical] == [new_id], \
        "the withdrawn revision's facts must not survive alongside the current ones"


def test_deleting_a_datasheet_prunes_its_facts_and_leaves_the_others(tmp_path):
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/01 DataSheet A.txt": b"Continuous rating 550 kW",
        "KERUI/02 DataSheet B.txt": b"Continuous rating 700 kW",
    })
    run_ingestion(root, "p", RoutingClient())
    before = snapshots.load_facts(root, "p", "KERUI")
    assert len(before.technical) == 2
    kept_id = _docs(root)["01 DataSheet A.txt"].doc_id
    survivor = next(f for f in before.technical if f["doc_id"] == kept_id)

    (tmp_path / "p" / "vendors" / "KERUI" / "02 DataSheet B.txt").unlink()
    run_ingestion(root, "p", RoutingClient())

    after = snapshots.load_facts(root, "p", "KERUI")
    assert [f["doc_id"] for f in after.technical] == [kept_id]
    assert survivor in after.technical, "the surviving datasheet's fact is untouched"
    assert "facts.pruned" in _actions(root), "pruning must be visible in the audit trail"


def test_a_deviation_form_deleted_with_the_whole_vendor_folder_leaves_no_facts(tmp_path):
    # the vendor keeps a document, so it still exists and still gets a loop
    # iteration - but the deviation form it used to have is gone
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/03 Vendor Deviation Form.txt": b"Clause 4.2.7 deviation",
    })
    run_ingestion(root, "p", RoutingClient())
    assert len(snapshots.load_facts(root, "p", "KERUI").deviations) == 1

    (tmp_path / "p" / "vendors" / "KERUI" / "03 Vendor Deviation Form.txt").unlink()
    run_ingestion(root, "p", RoutingClient())
    assert snapshots.load_facts(root, "p", "KERUI").deviations == []


def test_a_transiently_failed_datasheet_keeps_its_previously_stored_facts(tmp_path):
    # the counterpart to the prune: "failed" means the document is still there
    # and still routed to an extractor, so pruning must not treat a provider
    # outage as the document having gone away
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/01 DataSheet A.txt": b"Continuous rating 550 kW",
    })
    run_ingestion(root, "p", RoutingClient())
    before = snapshots.load_facts(root, "p", "KERUI").technical

    _write(tmp_path, "KERUI", "01 DataSheet A.txt", b"Continuous rating 600 kW")
    run_ingestion(root, "p", RoutingClient(fail_kind="facts"))

    assert snapshots.load_facts(root, "p", "KERUI").technical == before


# --------------------------------------------------------------------------
# C2 - the unchanged fast path must not discard fresh classification/lineage
# --------------------------------------------------------------------------

def test_a_classify_prompt_version_bump_persists_and_costs_one_run(tmp_path, monkeypatch):
    root = _project(tmp_path, {"ADPOWER/ADP-13158-2024-935.txt": b"base price 1000"})
    first = RoutingClient(doc_class="quotation")
    run_ingestion(root, "p", first)
    assert first.calls.count("doc_class") == 1
    assert _docs(root)["ADP-13158-2024-935.txt"].classified_with == "doc_class_v1"

    monkeypatch.setattr("procurement.pipeline.CLASSIFY_PROMPT_VERSION", "doc_class_v2")
    second = RoutingClient(doc_class="quotation")
    run_ingestion(root, "p", second)
    assert second.calls == ["doc_class"], "the bump re-classifies but must not re-extract"
    assert _docs(root)["ADP-13158-2024-935.txt"].classified_with == "doc_class_v2", \
        "the new classifier version must be written back, or every later run re-classifies"

    third = RoutingClient(doc_class="quotation")
    run_ingestion(root, "p", third)
    assert third.calls == [], "the bump must cost exactly one run, not every run forever"


def test_a_sibling_revision_arriving_later_keeps_the_lineage_pointer(tmp_path):
    # vendors uploading in two batches is normal; the newest document's
    # `supersedes` is computed on run 2 and must survive the extraction cache
    root = _project(tmp_path, {"ADPOWER/Quotation ADP-935(Rev1).txt": b"base price 1100"})
    run_ingestion(root, "p", RoutingClient())
    assert _docs(root)["Quotation ADP-935(Rev1).txt"].extraction_status == "ok"

    _write(tmp_path, "ADPOWER", "Quotation ADP-935.txt", b"base price 1000")
    run_ingestion(root, "p", RoutingClient())

    docs = _docs(root)
    old, new = docs["Quotation ADP-935.txt"], docs["Quotation ADP-935(Rev1).txt"]
    assert old.superseded_by == new.doc_id and old.extraction_status == "skipped"
    assert new.supersedes == old.doc_id, \
        "the cached newest document must keep the lineage pointer this run computed"


def test_the_unchanged_fast_path_keeps_the_cached_extraction_metadata(tmp_path):
    root = _project(tmp_path, {"KERUI/Quotation.txt": b"base price 1000"})
    run_ingestion(root, "p", RoutingClient())
    before = _docs(root)["Quotation.txt"]

    run_ingestion(root, "p", RoutingClient())
    after = _docs(root)["Quotation.txt"]
    assert (after.extraction_status, after.prompt_version, after.extractor,
            after.extracted_at) == (before.extraction_status, before.prompt_version,
                                    before.extractor, before.extracted_at)


# --------------------------------------------------------------------------
# I1 - a transient classification failure must not be cached forever
# --------------------------------------------------------------------------

def test_a_failed_classification_is_retried_on_the_next_run(tmp_path):
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/ADP-13158-2024-935.txt": b"Continuous rating 550 kW",
    })
    run_ingestion(root, "p", RoutingClient(fail_kind="doc_class"))
    degraded = _docs(root)["ADP-13158-2024-935.txt"]
    assert (degraded.doc_class, degraded.classified_by) == ("other", "llm-failed")

    second = RoutingClient(doc_class="datasheet")
    run_ingestion(root, "p", second)

    retried = _docs(root)["ADP-13158-2024-935.txt"]
    assert "doc_class" in second.calls, "a provider blip must not be cached as an answer"
    assert (retried.doc_class, retried.classified_by) == ("datasheet", "llm")


# --------------------------------------------------------------------------
# I2 - a vendor whose quotation the classifier misses must not vanish
# --------------------------------------------------------------------------

def test_a_vendor_with_no_recognised_quotation_still_reaches_the_comparison(tmp_path):
    root = _project(tmp_path, {"ADPOWER/ADP-13158-2024-935.txt": b"base price 1000"})
    run_ingestion(root, "p", RoutingClient(doc_class="other"))

    doc = _docs(root)["ADP-13158-2024-935.txt"]
    assert doc.doc_class == "other", "routing must not rewrite what the classifier decided"
    assert doc.extraction_status == "ok"
    assert "vendor.quotation_inferred" in _actions(root), "the inference must be auditable"
    dataset = load_dataset(root, "p")
    assert [b["vendor"] for b in dataset["bids"]] == ["ADPOWER"]


def test_a_vendor_with_nothing_that_could_be_a_quotation_says_so(tmp_path):
    # only a datasheet: another extractor already claims it, so inferring a
    # quotation from it would trade technical facts for a guessed price
    root = _project(tmp_path, {"KERUI/01 DataSheet A.txt": b"Continuous rating 550 kW"})
    run_ingestion(root, "p", RoutingClient())

    assert "vendor.no_quotation" in _actions(root)
    facts = snapshots.load_facts(root, "p", "KERUI")
    assert facts.commercial is None and len(facts.technical) == 1


# --------------------------------------------------------------------------
# I3 / I4 - a failed extraction must preserve and explain itself
# --------------------------------------------------------------------------

def test_a_failed_quotation_reextraction_keeps_the_stored_commercial_record(tmp_path):
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/01 DataSheet A.txt": b"Continuous rating 550 kW",
    })
    run_ingestion(root, "p", RoutingClient())
    before = snapshots.load_facts(root, "p", "KERUI").commercial
    assert before["base_price"] == 1000.0

    _write(tmp_path, "KERUI", "Quotation.txt", b"base price 2000")
    run_ingestion(root, "p", RoutingClient(fail_kind="quotation"))

    after = snapshots.load_facts(root, "p", "KERUI")
    assert after.commercial == before, \
        "a failed overwrite must not publish a half-blank record into the comparison"
    assert after.technical, "and must not take the vendor's technical facts with it"
    doc = _docs(root)["Quotation.txt"]
    assert doc.extraction_status == "failed" and "provider unavailable" in doc.notes


def test_a_failed_datasheet_extraction_records_why(tmp_path):
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/01 DataSheet A.txt": b"Continuous rating 550 kW",
    })
    run_ingestion(root, "p", RoutingClient(fail_kind="facts"))
    doc = _docs(root)["01 DataSheet A.txt"]
    assert doc.extraction_status == "failed"
    assert doc.notes and "provider unavailable" in doc.notes, \
        "a document retried on every run must say why it keeps failing"


def test_a_failed_deviation_extraction_records_why(tmp_path):
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/03 Vendor Deviation Form.txt": b"Clause 4.2.7 deviation",
    })
    run_ingestion(root, "p", RoutingClient(fail_kind="deviations"))
    doc = _docs(root)["03 Vendor Deviation Form.txt"]
    assert doc.extraction_status == "failed"
    assert doc.notes and "provider unavailable" in doc.notes


def test_a_successful_extraction_leaves_no_stale_failure_note(tmp_path):
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/01 DataSheet A.txt": b"Continuous rating 550 kW",
    })
    run_ingestion(root, "p", RoutingClient(fail_kind="facts"))
    assert _docs(root)["01 DataSheet A.txt"].notes

    _write(tmp_path, "KERUI", "01 DataSheet A.txt", b"Continuous rating 600 kW")
    run_ingestion(root, "p", RoutingClient())
    assert _docs(root)["01 DataSheet A.txt"].notes is None


# --------------------------------------------------------------------------
# Behaviours previously verified only by code inspection
# --------------------------------------------------------------------------

def test_spec_and_mom_documents_are_skipped_not_extracted(tmp_path):
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/05 MOM 20241111.txt": b"minutes of the kickoff meeting",
        "KERUI/SPC-1234 Scope.txt": b"technical specification",
    })
    client = RoutingClient()
    run_ingestion(root, "p", client)

    docs = _docs(root)
    for name, expected in (("05 MOM 20241111.txt", "mom"), ("SPC-1234 Scope.txt", "spec")):
        assert docs[name].doc_class == expected
        assert docs[name].extraction_status == "skipped"
        assert "not extracted" in docs[name].notes
    assert client.calls == ["quotation"], "neither may cost an extraction call"


def test_a_prompt_version_bump_reextracts_only_that_document_class(tmp_path, monkeypatch):
    # behaviour 3 is per-class caching. Asserting the dict literal would still
    # pass if routing collapsed to a single version, so bump one class and
    # check that exactly one class re-extracts.
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/01 DataSheet A.txt": b"Continuous rating 550 kW",
        "KERUI/03 Vendor Deviation Form.txt": b"Clause 4.2.7 deviation",
    })
    run_ingestion(root, "p", RoutingClient())

    monkeypatch.setattr("procurement.pipeline.PROMPT_VERSION_BY_CLASS",
                        {"quotation": "bid_extract_v1", "datasheet": "tech_facts_v2",
                         "deviation": "deviation_v1"})
    second = RoutingClient()
    run_ingestion(root, "p", second)

    assert second.calls == ["facts"], \
        "only the datasheet's version moved, so only the datasheet may be re-extracted"
    docs = _docs(root)
    assert docs["01 DataSheet A.txt"].prompt_version == "tech_facts_v2"
    assert docs["Quotation.txt"].prompt_version == "bid_extract_v1"
    assert docs["03 Vendor Deviation Form.txt"].prompt_version == "deviation_v1"
