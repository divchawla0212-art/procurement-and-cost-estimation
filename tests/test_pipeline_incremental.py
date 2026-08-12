import io
import os
import shutil
import zipfile
from contextlib import contextmanager

from procurement.project import (create_project, unpack_vendor_zip,
                                 load_project, save_project)
from procurement.pipeline import run_ingestion, inventory_documents, load_dataset
from procurement.store import events, snapshots
from procurement.store.models import Override
from shared.llm.mock_client import MockLLMClient

from tests.test_pipeline_rfq import RfqClient      # the schema-aware stub


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, d in entries.items():
            zf.writestr(n, d)
    return buf.getvalue()


# Padding appended to every synthetic fixture body that stands in for a real,
# extractable document: real quotations are always well over
# MIN_EXTRACTABLE_CHARS, and a fixture short enough to trip the pipeline's
# no-readable-text guard would silently turn these into tests of the guard
# instead of the caching/status logic they mean to exercise.
_PAD = (b" This synthetic fixture body is padded with filler prose so its "
       b"character count clears the pipeline's minimum-extractable-text "
       b"guard, letting the extraction logic under test run rather than "
       b"the guard itself.")


def _project(tmp_path):
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    z = tmp_path / "v.zip"
    z.write_bytes(_zip({
        "KERUI/Quotation.txt": b"base price 1000" + _PAD,
        # Deliberately left unpadded: BOM now routes to the technical
        # extractor (VENDOR_ROUTE), so this file no longer stops at the
        # routing gate - it is short enough to fail the no-readable-text
        # guard instead, before any LLM call is made. See
        # test_first_run_extracts_one_document_per_vendor below.
        "KERUI/BOM.txt": b"bill of materials",
        "MKON/Quotation.txt": b"base price 900" + _PAD,
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
    # Two quotations, plus a technical pass on each: neither vendor has a
    # document that *reaches* the technical extractor, so both quotations pick
    # up the secondary route. KERUI's BOM is routed there by its class, but its
    # unpadded body fails the no-readable-text guard, so it never arrives - and
    # a document that contributed nothing must not withdraw the one route that
    # gives an under-documented vendor any technical fact at all.
    assert len(client.calls) == 4
    docs = {d.path: d for d in snapshots.load_documents(root, "p")}
    assert sum(1 for d in docs.values() if d.extraction_status == "ok") == 2
    # BOM.txt is now routed (VENDOR_ROUTE sends "bom" to the technical
    # extractor), so it no longer stops at the routing gate as "skipped" -
    # its unpadded, sub-guard-length body fails the no-readable-text check
    # instead, before any LLM call, so the call count above is unaffected.
    assert sum(1 for d in docs.values() if d.extraction_status == "failed") == 1


def test_rerun_with_no_changes_makes_zero_llm_calls(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    second = _client()
    run_ingestion(root, "p", second)
    assert second.calls == []


def test_touching_one_document_reextracts_exactly_that_one(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    (tmp_path / "p" / "vendors" / "KERUI" / "Quotation.txt").write_bytes(b"base price 2000" + _PAD)
    second = _client()
    run_ingestion(root, "p", second)
    # both passes of that one document: its bytes changed, so the quotation
    # cache and the secondary technical cache are stale together
    assert len(second.calls) == 2


def test_force_reextracts_everything(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    second = _client()
    run_ingestion(root, "p", second, force=True)
    assert len(second.calls) == 4      # both passes of both quotations, as run 1


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
    # BOM.txt is now routed rather than gate-skipped (see _project's comment),
    # so it records "document.unreadable" - the guard's event - not
    # "document.skipped", which nothing in this fixture triggers any more.
    assert "document.unreadable" in actions


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
    # Not _project: that fixture's BOM.txt is deliberately unreadable (see its
    # comment) to exercise the no-readable-text guard elsewhere in this file,
    # which would make this run "done_with_failures" and defeat the point of
    # this test. A genuinely clean run needs a fixture with nothing to fail.
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    z = tmp_path / "v.zip"
    z.write_bytes(_zip({
        "KERUI/Quotation.txt": b"base price 1000" + _PAD,
        "MKON/Quotation.txt": b"base price 900" + _PAD,
    }))
    unpack_vendor_zip(root, "p", str(z))
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
    z1.write_bytes(_zip({"ADPOWER/Quotation.txt": b"base price 1200" + _PAD}))
    unpack_vendor_zip(root, "p", str(z1))

    z2 = tmp_path / "mkon.zip"
    z2.write_bytes(_zip({"MKON/Quotation.txt": b"base price 900" + _PAD}))
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
    z2.write_bytes(_zip({"KERUI/Quotation.txt": b"base price 1000" + _PAD,
                         "KERUI/BOM.txt": b"bill of materials"}))
    unpack_vendor_zip(root, "p", str(z2))
    assert load_project(root, "p").vendors == ["KERUI"]

    run_ingestion(root, "p", _client())

    assert snapshots.list_fact_vendors(root, "p") == ["KERUI"], \
        "a withdrawn vendor's facts must not survive on disk"
    dataset = load_dataset(root, "p")
    assert [b["vendor"] for b in dataset["bids"]] == ["KERUI"]
    assert [r["vendor"] for r in dataset["comparison"]["rows"]] == ["KERUI"]


# --- has_results: the cheap form of `bool(load_dataset(...))` -----------------
#
# `_project_summary` needs one bit — "is there an extraction to open?" — for
# every project in the dashboard listing. Answering it with
# `bool(load_dataset(...))` made the listing O(projects x vendors) full fact
# loads plus a discarded price comparison per project. `has_results` answers the
# same question from the store's shape alone, so each test below pins it against
# the `load_dataset` truthiness it replaces: the two must never disagree.


def test_has_results_is_false_before_anything_is_extracted(tmp_path):
    from procurement.pipeline import has_results
    root = _project(tmp_path)

    assert has_results(root, "p") is False
    assert load_dataset(root, "p") is None


def test_has_results_is_true_once_the_store_holds_facts(tmp_path):
    from procurement.pipeline import has_results
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())

    assert has_results(root, "p") is True
    assert bool(load_dataset(root, "p")) is True


def test_has_results_ignores_facts_of_a_vendor_the_project_no_longer_lists(tmp_path):
    """The `project.vendors` filter is part of the predicate, not decoration.

    `load_dataset` returns None when every stored fact vendor has left the
    project's roster, so `has_results` must too -- reading the store's vendor
    directory alone would answer True and offer a review screen with nothing
    behind it.
    """
    from procurement.project import save_project
    from procurement.pipeline import has_results
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    project = load_project(root, "p")
    project.vendors = ["GHOST"]
    save_project(root, project)

    assert load_dataset(root, "p") is None
    assert has_results(root, "p") is False


def test_has_results_trusts_the_store_when_the_project_records_no_vendors(tmp_path):
    """An empty `project.vendors` means "not recorded", not "none".

    A migrated project can hold facts while listing no vendors, so `load_dataset`
    deliberately skips its filter in that case and `has_results` must skip it on
    the same condition.
    """
    from procurement.project import save_project
    from procurement.pipeline import has_results
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    project = load_project(root, "p")
    project.vendors = []
    save_project(root, project)

    assert bool(load_dataset(root, "p")) is True
    assert has_results(root, "p") is True


def test_has_results_does_not_build_the_price_comparison(tmp_path, monkeypatch):
    from procurement import pipeline
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())

    def _refuse(*args, **kwargs):
        raise AssertionError(
            "has_results built a price comparison it then threw away")

    monkeypatch.setattr(pipeline, "build_comparison", _refuse)

    assert pipeline.has_results(root, "p") is True


# --- BUG-010: the prune save under the vendor lock -----------------------
#
# _prune_orphan_facts used to load-modify-save a vendor's facts outside any
# lock, so a run's prune could discard a reviewer's technical_feedback (or a
# concurrent writer's fresher fields) written in the window between its read
# and its write. It is converted to snapshots.update_facts.

_DATASHEET = "01 DataSheet Gas Generator.txt"


def _project_with_datasheet(tmp_path):
    """One vendor, one quotation and one datasheet -- so a run produces
    stored `technical` facts keyed to the datasheet's doc_id, which can then
    be orphaned by deleting just that file (the vendor itself stays live)."""
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    vdir = tmp_path / "p" / "vendors" / "KERUI"
    vdir.mkdir(parents=True)
    (vdir / "Quotation.txt").write_bytes(b"base price 1000" + _PAD)
    (vdir / _DATASHEET).write_bytes(b"H2S up to 70 ppm" + _PAD)
    project = load_project(root, "p")
    project.vendors = ["KERUI"]
    save_project(root, project)
    return root


def test_pruning_does_not_disturb_feedback_or_overrides(tmp_path):
    root = _project_with_datasheet(tmp_path)
    run_ingestion(root, "p", RfqClient())

    stored = snapshots.load_facts(root, "p", "KERUI")
    assert stored.technical, "the datasheet must have produced a technical fact to prune"
    doc_id = next(d.doc_id for d in snapshots.load_documents(root, "p")
                  if d.path.endswith(_DATASHEET))

    # A human correction, recorded against the value the extraction produces,
    # so `reconcile` agrees with it and it must come back byte-identical --
    # conflict flag and extracted_value included. The prune writes `technical`,
    # `deviations` and (when the quotation link dies) `commercial`; `overrides`
    # is one of the fields it must leave exactly as it found them.
    override = Override(field_path="commercial.freight_included",
                        value=not stored.commercial["freight_included"],
                        extracted_value=stored.commercial["freight_included"],
                        author="buyer", at="2026-08-05T00:00:00Z",
                        reason="freight quoted separately in the cover letter")
    with snapshots.update_facts(root, "p", "KERUI") as f:
        f.technical_feedback = "a reviewer's note"
        f.overrides = [override]
    before = snapshots.load_facts(root, "p", "KERUI").overrides[0].model_dump()

    os.remove(tmp_path / "p" / "vendors" / "KERUI" / _DATASHEET)
    run_ingestion(root, "p", RfqClient(), force=True)

    stored = snapshots.load_facts(root, "p", "KERUI")
    assert stored.technical_feedback == "a reviewer's note", \
        "pruning must not carry a stale copy back over the reviewer's note"
    assert not any(f.get("doc_id") == doc_id for f in stored.technical), \
        "the deleted datasheet's fact must actually be pruned"
    # The overrides half of this test's name, which used to go unasserted.
    assert len(stored.overrides) == 1, \
        f"pruning dropped the reviewer's override: {stored.overrides}"
    assert stored.overrides[0].model_dump() == before, \
        f"pruning disturbed the override: {stored.overrides[0].model_dump()} != {before}"


def test_pruning_skips_a_vendor_whose_facts_vanish_between_precheck_and_lock(
        tmp_path, monkeypatch):
    """A vendor whose facts vanish (e.g. a concurrent run's stale-vendor
    sweep, further down this same transaction on a different run) between
    the prune's advisory pre-check and its own lock acquire must be skipped,
    not crash the run and not be resurrected. `update_facts(create=False)` --
    the prune's call, unlike the extraction save's `create=True` a few lines
    up in pipeline.py -- raises LookupError for exactly this case.
    """
    root = _project_with_datasheet(tmp_path)
    run_ingestion(root, "p", RfqClient())
    assert snapshots.load_facts(root, "p", "KERUI").technical

    os.remove(tmp_path / "p" / "vendors" / "KERUI" / _DATASHEET)

    from procurement import pipeline as pipeline_module
    real_update = snapshots.update_facts
    deleted = []

    @contextmanager
    def delete_then_update(root_, slug_, vendor_, **kw):
        # Only the prune's own call is create=False (default); the
        # extraction save a few lines up always passes create=True. That
        # distinguishes "the prune is about to take its lock" from every
        # other update_facts call this run makes, without needing to count
        # calls or inspect a stack.
        if vendor_ == "KERUI" and not kw.get("create", False) and not deleted:
            deleted.append(True)
            snapshots.delete_facts(root_, slug_, vendor_)
        with real_update(root_, slug_, vendor_, **kw) as f:
            yield f

    monkeypatch.setattr(pipeline_module.snapshots, "update_facts", delete_then_update)

    run_ingestion(root, "p", RfqClient(), force=True)   # must not raise

    assert snapshots.load_facts(root, "p", "KERUI") is None, \
        "a vendor whose facts vanished mid-prune must stay absent, not be resurrected"
