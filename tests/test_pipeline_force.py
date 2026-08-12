"""BUG-002's two-run mutation matrix (task-5-brief.md Step 4b).

`run_ingestion(force=True)` bypasses the per-document extraction cache, but
`_prune_orphan_facts`, the generation bump and the stale-vendor sweep are not
conditioned on `force` at all - they run unconditionally, every run. The risk
this file guards is that a forced run's widest-blast-radius write (re-extract
everything) could interact badly with those unconditional invariants: pruning
skipped because "force" short-circuited something upstream of it, generation
bumping more than once because a forced re-extraction touches more code paths,
or a stale-vendor sweep quietly gated on `not force`.

Every test below is two runs against one project, the second forced unless the
row says otherwise (row 2 is the control: force must stay opt-in). Reuses the
fixtures and `RoutingClient` from test_pipeline_lifecycle.py, which already
establishes this file's two-run-matrix convention for the *unforced* case;
this file is its forced counterpart.
"""
import shutil

from procurement.pipeline import run_ingestion
from procurement.project import load_project, save_project
from procurement.store import events, snapshots

from tests.test_pipeline_lifecycle import (RoutingClient, _docs, _PAD,
                                           _project, _write)


# --------------------------------------------------------------------------
# Row 1 - a forced run re-extracts rather than serving the cache
# --------------------------------------------------------------------------

def test_row1_nothing_changed_forced_rerun_reextracts_every_live_document(tmp_path):
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/01 DataSheet A.txt": b"Continuous rating 550 kW",
    })
    # A requirements/ document too: `force` is consulted a second time, in
    # `_run_rfq_pass`'s own `unchanged` check (pipeline.py's RFQ pass), which
    # governs requirements/amendments feeding the compliance vocabulary. A
    # vendor-only fixture would leave that half of the bypass completely
    # unexercised by this row. "SPC-" matches classify_by_rules' "spec" rule,
    # so no LLM doc_class call is needed and RoutingClient's default
    # (quotation-shaped) response is fine here: extract_requirements treats a
    # response with no "requirements" key as zero clauses, status "ok" - which
    # still logs `document.extracted`, exactly what this row checks for.
    rdir = tmp_path / "p" / "requirements"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "SPC-001 Requirements.txt").write_bytes(b"4.2.7 H2S at least 50 ppm" + _PAD)

    run_ingestion(root, "p", RoutingClient())
    events_before = len(events.read_events(root, "p"))

    run_ingestion(root, "p", RoutingClient(), force=True)

    run2 = events.read_events(root, "p")[events_before:]
    doc_events = {e.target: e.action for e in run2
                  if e.action in ("document.extracted", "document.skipped")}
    # No vendor filter: a live RFQ document (vendor is None) must be checked
    # here too, or the RFQ half of the bypass goes untested by this row.
    live_doc_ids = {d.doc_id for d in snapshots.load_documents(root, "p")
                    if d.superseded_by is None}
    assert live_doc_ids, "fixture sanity: there is something to check"
    for doc_id in live_doc_ids:
        assert doc_events.get(doc_id) == "document.extracted", \
            f"{doc_id} was not re-extracted under force"


# --------------------------------------------------------------------------
# Row 2 - the control: force is opt-in and did not become the default
# --------------------------------------------------------------------------

def test_row2_nothing_changed_unforced_rerun_still_skips(tmp_path):
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/01 DataSheet A.txt": b"Continuous rating 550 kW",
    })
    run_ingestion(root, "p", RoutingClient())
    events_before = len(events.read_events(root, "p"))

    run_ingestion(root, "p", RoutingClient())  # no force

    run2 = events.read_events(root, "p")[events_before:]
    doc_events = {e.target: e.action for e in run2
                  if e.action in ("document.extracted", "document.skipped")}
    live_doc_ids = {d.doc_id for d in snapshots.load_documents(root, "p")
                    if d.vendor is not None and d.superseded_by is None}
    assert live_doc_ids
    for doc_id in live_doc_ids:
        assert doc_events.get(doc_id) == "document.skipped", \
            f"{doc_id} was re-extracted even though nothing changed and " \
            "force was not set"


# --------------------------------------------------------------------------
# Row 3 - a newer revision's arrival still prunes the superseded doc's facts
# --------------------------------------------------------------------------

def test_row3_a_newer_revision_prunes_the_superseded_docs_facts_under_force(tmp_path):
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/01 DataSheet A.txt": b"Continuous rating 550 kW",
    })
    run_ingestion(root, "p", RoutingClient())
    old_id = _docs(root)["01 DataSheet A.txt"].doc_id

    _write(tmp_path, "KERUI", "01 DataSheet A Rev1.txt", b"Continuous rating 600 kW")
    run_ingestion(root, "p", RoutingClient(), force=True)

    technical = snapshots.load_facts(root, "p", "KERUI").technical
    assert technical, "fixture sanity: the surviving revision must have facts"
    assert all(f["doc_id"] != old_id for f in technical), \
        "the superseded revision's facts must not survive a forced run"


# --------------------------------------------------------------------------
# Row 4 - a document deleted from its vendor folder leaves no trace, forced
# --------------------------------------------------------------------------

def test_row4_a_deleted_document_leaves_no_fact_and_no_record_under_force(tmp_path):
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/01 DataSheet A.txt": b"Continuous rating 550 kW",
        "KERUI/02 DataSheet B.txt": b"Continuous rating 700 kW",
    })
    run_ingestion(root, "p", RoutingClient())
    deleted_id = _docs(root)["02 DataSheet B.txt"].doc_id

    (tmp_path / "p" / "vendors" / "KERUI" / "02 DataSheet B.txt").unlink()
    run_ingestion(root, "p", RoutingClient(), force=True)

    technical = snapshots.load_facts(root, "p", "KERUI").technical
    assert technical, "fixture sanity: the surviving datasheet must have facts"
    assert all(f.get("doc_id") != deleted_id for f in technical), \
        "no stored fact may cite the deleted doc_id"
    assert deleted_id not in {d.doc_id for d in snapshots.load_documents(root, "p")}, \
        "no DocumentRecord for the deleted document may survive"


# --------------------------------------------------------------------------
# Row 5 - a removed vendor folder is still swept under force
# --------------------------------------------------------------------------

def test_row5_a_removed_vendor_folder_is_still_swept_under_force(tmp_path):
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "MKON/Quotation.txt": b"base price 900",
    })
    run_ingestion(root, "p", RoutingClient())
    assert set(snapshots.list_fact_vendors(root, "p")) == {"KERUI", "MKON"}

    shutil.rmtree(tmp_path / "p" / "vendors" / "MKON")
    project = load_project(root, "p")
    project.vendors = [v for v in project.vendors if v != "MKON"]
    save_project(root, project)

    run_ingestion(root, "p", RoutingClient(), force=True)

    assert snapshots.list_fact_vendors(root, "p") == ["KERUI"], \
        "a vendor no longer in the project must lose its facts collection " \
        "even on a forced run"


# --------------------------------------------------------------------------
# Row 6 - a reclassified document's facts live under exactly one extractor
# --------------------------------------------------------------------------

def test_row6_a_reclassified_document_ends_up_under_exactly_one_route_under_force(tmp_path):
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/AMB-1.txt": b"Continuous rating 550 kW",
    })
    run_ingestion(root, "p", RoutingClient(doc_class="datasheet"))
    doc_id = _docs(root)["AMB-1.txt"].doc_id
    before = snapshots.load_facts(root, "p", "KERUI")
    assert any(f["doc_id"] == doc_id for f in before.technical)
    assert not any(d["doc_id"] == doc_id for d in before.deviations)

    # same path, changed content: forces reclassification rather than a
    # classification-cache hit (see test_pipeline_lifecycle's own comment on
    # this pattern)
    _write(tmp_path, "KERUI", "AMB-1.txt", b"Clause 4.2.7 deviation")
    run_ingestion(root, "p", RoutingClient(doc_class="deviation"), force=True)

    after = snapshots.load_facts(root, "p", "KERUI")
    technical_hits = [f for f in after.technical if f["doc_id"] == doc_id]
    deviation_hits = [d for d in after.deviations if d["doc_id"] == doc_id]
    assert deviation_hits and not technical_hits, \
        "the document's facts must come from exactly one extractor - the " \
        "route it is classified under now, not the one it used to be"


# --------------------------------------------------------------------------
# Row 7 - a forced run's failed extraction never blanks previously-good data
# --------------------------------------------------------------------------

def test_row7_a_failed_forced_reextraction_keeps_prior_good_facts_and_records_why(
    tmp_path,
):
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/01 DataSheet A.txt": b"Continuous rating 550 kW",
    })
    run_ingestion(root, "p", RoutingClient())
    before = snapshots.load_facts(root, "p", "KERUI").technical
    assert before, "fixture sanity: run 1 must have stored a technical fact"

    # Bytes untouched, deliberately: only `force` can trigger the re-attempt
    # here. Without it the unchanged fast path carries `extraction_status ==
    # "ok"` forward from cache and the failing client is never even called -
    # that is what makes this row test the failure path *under force*, rather
    # than restating test_pipeline_lifecycle.py's unforced failure-preservation
    # test with a no-op argument appended.
    run_ingestion(root, "p", RoutingClient(fail_kind="facts"), force=True)

    after = snapshots.load_facts(root, "p", "KERUI")
    assert after.technical == before, \
        "a failed forced re-extraction must not blank previously-good facts"
    doc = _docs(root)["01 DataSheet A.txt"]
    assert doc.extraction_status == "failed"
    assert doc.notes and "provider unavailable" in doc.notes, \
        "DocumentRecord.notes must record why the forced re-extraction failed"


# --------------------------------------------------------------------------
# Row 8 - generation bumps exactly once per forced run, never once per file
# --------------------------------------------------------------------------

def test_row8_generation_bumps_exactly_once_per_forced_run(tmp_path):
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/01 DataSheet A.txt": b"Continuous rating 550 kW",
    })
    run_ingestion(root, "p", RoutingClient())
    gen1 = snapshots.get_generation(root, "p")

    run_ingestion(root, "p", RoutingClient(), force=True)
    gen2 = snapshots.get_generation(root, "p")
    assert gen2 - gen1 == 1, "a forced run bumped generation something other than once"

    run_ingestion(root, "p", RoutingClient(), force=True)
    gen3 = snapshots.get_generation(root, "p")
    assert gen3 - gen2 == 1, \
        "a second forced run bumped generation something other than once"
