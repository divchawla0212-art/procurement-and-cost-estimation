"""BUG-005 Task 8 -- the two-run mutation matrix.

Per-task TDD (Tasks 1-7) structurally produces single-run, single-module
tests: every phase-2 defect that survived to final review needed two runs or
two modules to see it. Each test below drives the real store through two
observed states -- run 1, a mutation, run 2 (or a second read) -- through the
actual production entry points (procurement.pipeline.run_ingestion,
procurement.renormalize.renormalize, and the real `/fx-rates` API route),
never by hand-writing the intermediate `normalized` dict a task's own unit
tests already exercise in isolation.

Four of the plan template's nine pre-declared rows are deliberately absent:
prompt-version bumps, cross-class cache invalidation, and the
omitted-optional-array row all concern the extraction cache, which this plan
never touches -- no prompt, extractor, or provider change appears anywhere in
it. Their existing coverage in tests/test_pipeline_incremental.py stands
unchanged; see test_pipeline_incremental_suite_still_passes below for the
explicit confirmation this file's docstring promises rather than asserts.
"""
import os

from fastapi.testclient import TestClient

from procurement.models import VendorBid
from procurement.normalize import normalize_bid
from procurement.pipeline import has_results, load_dataset, run_ingestion
from procurement.project import (create_project, load_project, save_project,
                                 unpack_vendor_zip)
from procurement.renormalize import renormalize
from procurement.statement import build_statement
from procurement.store import events, layout, snapshots
from shared.llm.mock_client import MockLLMClient

_PAD = (b" This synthetic fixture body is padded with filler prose so its "
        b"character count clears the pipeline's minimum-extractable-text "
        b"guard, letting the extraction logic under test run rather than "
        b"the guard itself.")


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _eur_project(tmp_path, target_currency="USD", fx_rates=None):
    """One vendor, one EUR quotation, ready for `run_ingestion`."""
    root = str(tmp_path)
    create_project(root, "P", target_currency=target_currency)
    if fx_rates is not None:
        project = load_project(root, "p")
        project.fx_rates = fx_rates
        save_project(root, project)
    z = tmp_path / "v.zip"
    z.write_bytes(_zip_bytes({"EUROVEND/Quotation.txt": b"base price 1000" + _PAD}))
    unpack_vendor_zip(root, "p", str(z))
    return root


def _eur_client(base_price=1000.0):
    return MockLLMClient(response={"currency": "EUR", "base_price": base_price,
                                   "freight_included": True})


class _Boom:
    """A provider client that always fails, tracking its own call count so a
    test can prove it either was or was not invoked."""
    supports_vision = True

    def __init__(self):
        self.calls: list = []

    def classify_structure(self, *a, **k):
        self.calls.append((a, k))
        raise RuntimeError("simulated provider failure")


def _api_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return TestClient(api_main.app)


def _event_actions(root, slug):
    return [e.action for e in events.read_events(root, slug)]


# --- Row 1: an FX rate is added, no `force` -- Task 3 -----------------------

def test_adding_a_rate_updates_totals_with_no_llm_call_and_one_generation_bump(
        tmp_path, monkeypatch):
    """Invariant at risk: Task 3's renormalize is the only thing allowed to
    move a stored total in response to a rate change -- no re-extraction, no
    LLM call, exactly one generation bump."""
    api = _api_client(tmp_path, monkeypatch)
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _eur_client())
    assert snapshots.load_facts(root, "p", "EUROVEND").normalized["normalized_total"] is None
    generation_before = load_project(root, "p").generation
    events_before = _event_actions(root, "p")

    res = api.put("/api/projects/p/fx-rates", json={"rates": {"EUR": 1.08}})

    assert res.status_code == 200
    facts = snapshots.load_facts(root, "p", "EUROVEND")
    assert abs(facts.normalized["normalized_total"] - 1080.0) <= 0.01
    assert facts.normalized["normalization_status"] == "ok"
    assert load_project(root, "p").generation == generation_before + 1
    # No extraction ran: the only new event is renormalize's own.
    new_actions = _event_actions(root, "p")[len(events_before):]
    assert new_actions == ["fx.renormalized"]
    # And a plain re-run afterward makes no LLM calls either -- the totals
    # were already current before this run started.
    second = _eur_client()
    run_ingestion(root, "p", second)
    assert second.calls == []


# --- Row 2: an FX rate is changed -- Task 3 ---------------------------------

def test_changing_a_rate_follows_the_new_rate_not_the_old(tmp_path, monkeypatch):
    """Invariant at risk: renormalize must recompute from the CURRENT rate,
    never leave the previous run's converted total sitting in the store."""
    api = _api_client(tmp_path, monkeypatch)
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _eur_client())
    # Unconverted before any rate exists -- pins the starting point a
    # defaulting defect (fx_rates.get(currency, 1.0)) would get wrong too,
    # not just the transition below.
    assert snapshots.load_facts(root, "p", "EUROVEND").normalized["normalized_total"] is None
    api.put("/api/projects/p/fx-rates", json={"rates": {"EUR": 1.08}})
    first = snapshots.load_facts(root, "p", "EUROVEND").normalized["normalized_total"]
    assert abs(first - 1080.0) <= 0.01

    res = api.put("/api/projects/p/fx-rates", json={"rates": {"EUR": 1.20}})

    assert res.status_code == 200
    second = snapshots.load_facts(root, "p", "EUROVEND").normalized["normalized_total"]
    assert abs(second - 1200.0) <= 0.01
    assert abs(second - first) > 1.0, "the old rate's total must not survive"


# --- Row 3: an FX rate is withdrawn -- Task 3 -------------------------------

def test_withdrawing_a_rate_reverts_the_total_to_unconvertible(tmp_path, monkeypatch):
    """Invariant at risk: a stale converted number surviving the removal of
    its rate is the original BUG-005 defect running backwards."""
    api = _api_client(tmp_path, monkeypatch)
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _eur_client())
    api.put("/api/projects/p/fx-rates", json={"rates": {"EUR": 1.08}})
    assert snapshots.load_facts(root, "p", "EUROVEND").normalized["normalized_total"] is not None

    res = api.put("/api/projects/p/fx-rates", json={"rates": {}})

    assert res.status_code == 200
    facts = snapshots.load_facts(root, "p", "EUROVEND")
    assert facts.normalized["normalized_total"] is None
    assert facts.normalized["normalization_status"] == "no_fx_rate"


# --- Row 4: an FX rate is set to 0 via the API -- Task 2 --------------------

def test_setting_a_rate_to_zero_is_rejected_and_stored_totals_are_unchanged(
        tmp_path, monkeypatch):
    """Invariant at risk: two layers, both load-bearing. Task 2's 422 gate at
    the API stops a non-positive rate from ever being written; Task 1's own
    `_usable_rate` guard inside normalize.py is the defence in depth for a
    rate that reached the store some other way (a legacy project, a direct
    mutation) -- a bypass of either would let a rate of 0 zero out a vendor's
    total and sort it first as the cheapest bid."""
    api = _api_client(tmp_path, monkeypatch)
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _eur_client())
    api.put("/api/projects/p/fx-rates", json={"rates": {"EUR": 1.08}})
    before = snapshots.load_facts(root, "p", "EUROVEND").normalized

    res = api.put("/api/projects/p/fx-rates", json={"rates": {"EUR": 0}})

    assert res.status_code == 422
    assert "EUR" in res.json()["detail"]
    after = snapshots.load_facts(root, "p", "EUROVEND").normalized
    assert after == before
    assert load_project(root, "p").fx_rates == {"EUR": 1.08}

    # Defence in depth: a 0 rate that reached the store by some other route
    # (bypassing the API entirely) must still be refused by normalize.py
    # itself, not silently applied by renormalize.
    project = load_project(root, "p")
    project.fx_rates = {"EUR": 0}
    save_project(root, project)
    assert renormalize(root, "p") == 1
    bypassed = snapshots.load_facts(root, "p", "EUROVEND").normalized
    assert bypassed["normalized_total"] is None
    assert bypassed["normalization_status"] == "no_fx_rate"


# --- Row 5: a newer revision of an already-extracted quotation arrives -- Task 1

def test_a_newer_revision_arriving_replaces_the_superseded_totals(tmp_path):
    """Invariant at risk: the new revision's facts must be normalized and
    stored; nothing from the superseded document's extraction may survive
    under the vendor's key."""
    root = _eur_project(tmp_path, fx_rates={"EUR": 1.08})
    run_ingestion(root, "p", _eur_client(base_price=1000.0))
    before = snapshots.load_facts(root, "p", "EUROVEND")
    assert abs(before.normalized["normalized_total"] - 1080.0) <= 0.01
    old_doc_id = before.quotation_doc_id

    # The newer revision arrives directly in the vendor's source folder --
    # normalised_base() groups "Quotation.txt" and "Quotation(Rev1).txt" as
    # the same document identity (see procurement/revisions.py).
    rev_path = os.path.join(root, "p", "vendors", "EUROVEND", "Quotation(Rev1).txt")
    with open(rev_path, "wb") as fh:
        fh.write(b"base price 2000" + _PAD)

    run_ingestion(root, "p", _eur_client(base_price=2000.0))

    after = snapshots.load_facts(root, "p", "EUROVEND")
    assert after.commercial["base_price"] == 2000.0
    assert abs(after.normalized["normalized_total"] - 2160.0) <= 0.01
    assert after.quotation_doc_id != old_doc_id, \
        "the link must move to the new revision, not stay on the superseded one"

    docs = {d.doc_id: d for d in snapshots.load_documents(root, "p")}
    assert docs[old_doc_id].superseded_by == after.quotation_doc_id
    assert docs[after.quotation_doc_id].superseded_by is None


# --- Row 6: a document is deleted from its source folder -- Task 3 ---------

def test_deleting_the_source_document_prunes_facts_and_renormalize_does_not_restore_them(
        tmp_path):
    """Invariant at risk: pruning (Task 3's predecessor invariant, phase 2's
    own defect) must remove the vendor's commercial facts, and renormalize --
    now called from the read path on every project -- must never resurrect a
    total for a vendor that no longer has commercial facts to derive one
    from."""
    root = _eur_project(tmp_path, fx_rates={"EUR": 1.08})
    run_ingestion(root, "p", _eur_client(base_price=1000.0))
    before = snapshots.load_facts(root, "p", "EUROVEND")
    assert before.normalized["normalized_total"] is not None

    os.remove(os.path.join(root, "p", "vendors", "EUROVEND", "Quotation.txt"))
    run_ingestion(root, "p", _eur_client())

    after = snapshots.load_facts(root, "p", "EUROVEND")
    assert after.commercial is None, "the deleted document's facts must be pruned"
    assert after.normalized is None, "never a stale number, and never a fabricated status"

    assert renormalize(root, "p") == 0
    still = snapshots.load_facts(root, "p", "EUROVEND")
    assert still.commercial is None
    assert still.normalized is None, \
        "renormalize must skip a vendor with no commercial facts, not resurrect one"


# --- Row 7: a second upload carries a sibling revision -- Task 1 -----------

def test_a_second_upload_with_a_sibling_revision_keeps_lineage_and_the_right_total(
        tmp_path):
    """Invariant at risk: lineage must survive a document arriving through a
    second, separate `unpack_vendor_zip` call (not a direct file write, as in
    the newer-revision row above), and the surviving revision's total must be
    the one actually stored."""
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    project = load_project(root, "p")
    project.fx_rates = {"EUR": 1.08}
    save_project(root, project)

    z1 = tmp_path / "v1.zip"
    z1.write_bytes(_zip_bytes({"EUROVEND/Quotation.txt": b"base price 1000" + _PAD}))
    unpack_vendor_zip(root, "p", str(z1))
    run_ingestion(root, "p", _eur_client(base_price=1000.0))
    first = snapshots.load_facts(root, "p", "EUROVEND")
    assert abs(first.normalized["normalized_total"] - 1080.0) <= 0.01

    z2 = tmp_path / "v2.zip"
    z2.write_bytes(_zip_bytes({"EUROVEND/Quotation(Rev1).txt": b"base price 2000" + _PAD}))
    returned = unpack_vendor_zip(root, "p", str(z2))
    assert returned == ["EUROVEND"], "the sibling revision must not add a phantom vendor"

    run_ingestion(root, "p", _eur_client(base_price=2000.0))

    second = snapshots.load_facts(root, "p", "EUROVEND")
    assert second.commercial["base_price"] == 2000.0
    assert abs(second.normalized["normalized_total"] - 2160.0) <= 0.01

    docs = {d.doc_id: d for d in snapshots.load_documents(root, "p")}
    old = next(d for d in docs.values() if d.path.endswith("Quotation.txt"))
    new = next(d for d in docs.values() if d.path.endswith("Quotation(Rev1).txt"))
    assert old.superseded_by == new.doc_id
    assert new.supersedes == old.doc_id
    assert new.superseded_by is None


# --- Row 8: an LLM call fails on run 1, succeeds on run 2 -- Task 1 --------

def test_extraction_failing_then_succeeding_moves_the_total_from_none_to_real(
        tmp_path):
    """Invariant at risk: `normalized_total` must go None -> a real number
    once extraction actually succeeds, with `normalization_status` reading
    "ok" -- not stuck, not silently reporting a missing rate for a bid that
    was never read."""
    root = _eur_project(tmp_path, fx_rates={"EUR": 1.08})

    run_ingestion(root, "p", _Boom())
    failed = snapshots.load_facts(root, "p", "EUROVEND")
    assert failed.commercial is None
    assert failed.normalized is None
    docs = snapshots.load_documents(root, "p")
    assert any(d.extraction_status == "failed" for d in docs if d.vendor == "EUROVEND")

    # A previously-failed document is retried without `force` -- the cache
    # key requires `prior.extraction_status == "ok"` (pipeline.py's
    # `unchanged` guard), which a failed prior run never satisfies.
    run_ingestion(root, "p", _eur_client(base_price=1000.0))

    recovered = snapshots.load_facts(root, "p", "EUROVEND")
    assert recovered.commercial["base_price"] == 1000.0
    assert abs(recovered.normalized["normalized_total"] - 1080.0) <= 0.01
    assert recovered.normalized["normalization_status"] == "ok"


# --- Row 9: an LLM call fails on both runs -- Task 1 ------------------------

def test_extraction_failing_on_both_runs_stays_none_and_distinguishable_from_no_fx_rate(
        tmp_path):
    """Invariant at risk: the two blanks -- "extraction never succeeded" and
    "extraction succeeded but no rate was configured" -- must stay
    distinguishable. In the real store this vendor's `normalized` simply
    stays None across both runs (commercial facts never existed for
    `normalize_bid` to run against, and `renormalize` skips a vendor with no
    commercial facts rather than manufacturing a status for it). The second
    assertion pins the underlying contract this store path never happens to
    exercise: were a failed `VendorBid` ever handed to `normalize_bid`
    directly, it must come back `normalization_status="ok"`, not
    "no_fx_rate" -- Task 1's own boundary, restated here as the reason the
    store-level behaviour above is safe rather than accidental."""
    root = _eur_project(tmp_path, fx_rates={"EUR": 1.08})

    run_ingestion(root, "p", _Boom())
    run_ingestion(root, "p", _Boom())

    facts = snapshots.load_facts(root, "p", "EUROVEND")
    assert facts.commercial is None
    assert facts.normalized is None
    docs = [d for d in snapshots.load_documents(root, "p") if d.vendor == "EUROVEND"]
    assert docs and all(d.extraction_status == "failed" for d in docs)

    assert renormalize(root, "p") == 0
    assert snapshots.load_facts(root, "p", "EUROVEND").normalized is None

    failed_bid = VendorBid(vendor="EUROVEND", extraction_status="failed")
    would_be = normalize_bid(failed_bid, "USD", {})
    assert would_be.normalized_total is None
    assert would_be.extraction_status == "failed"
    assert would_be.normalization_status == "ok", \
        "a failed extraction must never be mistaken for a missing rate"


# --- Row 10: a re-extraction fails after a successful one -- Task 1 --------

def test_a_failed_re_extraction_never_blanks_the_previously_good_total(tmp_path):
    """Invariant at risk: a failed re-extraction must not blank data a
    previous, successful run already stored -- CLAUDE.md's store invariant in
    the specific shape BUG-005 touches."""
    root = _eur_project(tmp_path, fx_rates={"EUR": 1.08})
    run_ingestion(root, "p", _eur_client(base_price=1000.0))
    good = snapshots.load_facts(root, "p", "EUROVEND")
    assert abs(good.normalized["normalized_total"] - 1080.0) <= 0.01

    # `force=True`: the document's bytes are unchanged and its prior status is
    # "ok", so without `force` the pipeline's cache would skip re-extraction
    # entirely and this row would test nothing.
    run_ingestion(root, "p", _Boom(), force=True)

    after = snapshots.load_facts(root, "p", "EUROVEND")
    assert after.commercial == good.commercial
    assert after.normalized["normalized_total"] == good.normalized["normalized_total"]
    docs = [d for d in snapshots.load_documents(root, "p") if d.vendor == "EUROVEND"]
    assert any(d.extraction_status == "failed" for d in docs), \
        "the re-extraction must actually have been attempted and failed, not skipped"


# --- Row 11: the project is read repeatedly -- Task 4 ----------------------

def test_reading_the_project_repeatedly_through_both_read_paths_never_advances_generation(
        tmp_path):
    """Invariant at risk: Task 4's migration runs from `_live_fact_vendors`,
    the shared front half of both `has_results` and `load_dataset`. A version
    that always opened a transaction (rather than only when something
    changed) would advance `generation` on every page load, forever -- this
    exercises both read entry points together, interleaved, which neither
    test_renormalize.py nor a single-entry-point test can catch.

    Two distinct no-ops are chained here, deliberately. `migrate_normalization`
    itself is a one-shot -- once `store_version` reaches `layout.STORE_VERSION`
    it returns before ever calling `renormalize` again, so looping only
    `has_results`/`load_dataset` after the first migration would never re-enter
    `renormalize` at all and could not catch a defect living inside it. The
    direct `renormalize` calls below close that gap: they are what a future
    read-path caller invoking it more eagerly would do, and they are the only
    way this row can observe `renormalize`'s own "nothing changed, no
    transaction" contract rather than merely `migrate_normalization`'s."""
    root = _eur_project(tmp_path, fx_rates={"EUR": 1.08})
    run_ingestion(root, "p", _eur_client(base_price=1000.0))

    # Force a pre-BUG-005 store state so the first read must actually migrate.
    project = load_project(root, "p")
    project.store_version = 1
    save_project(root, project)

    assert has_results(root, "p") is True
    settled = load_project(root, "p").generation
    assert load_project(root, "p").store_version == layout.STORE_VERSION

    for _ in range(2):
        load_dataset(root, "p")
        has_results(root, "p")

    assert load_project(root, "p").generation == settled

    for _ in range(3):
        assert renormalize(root, "p") == 0
        assert load_project(root, "p").generation == settled


# --- Row 12: a rate is withdrawn, then the statement is rebuilt -- Task 6 --

def test_withdrawing_a_rate_then_rebuilding_the_statement_keeps_the_normalised_row(
        tmp_path, monkeypatch):
    """Invariant at risk: Task 6's contract on the screen the buyer actually
    reads. A dropped Normalised row reads as "nothing to compare here"; the
    row must survive with a note naming the currency instead."""
    api = _api_client(tmp_path, monkeypatch)
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _eur_client(base_price=1000.0))
    api.put("/api/projects/p/fx-rates", json={"rates": {"EUR": 1.08}})

    statement = build_statement(root, "p")
    row = next(r for r in statement.rows if r.key == "normalised")
    assert row.cells["EUROVEND"].total is not None

    api.put("/api/projects/p/fx-rates", json={"rates": {}})

    rebuilt = build_statement(root, "p")
    row = next(r for r in rebuilt.rows if r.key == "normalised")
    cell = row.cells["EUROVEND"]
    assert cell.total is None
    assert "EUR" in cell.note


# --- Confirmation the four N/A rows' existing coverage is unaffected -------

def test_pipeline_incremental_suite_still_passes():
    """Not a duplicate of test_pipeline_incremental.py's rows -- a marker
    that this file's docstring is an honest claim, not an assumption. Run
    alongside the full suite; if this module or the four N/A rows'
    behaviour ever regress, `python -m pytest tests/test_pipeline_incremental.py`
    is where that would show up, not here."""
    import tests.test_pipeline_incremental  # noqa: F401 -- import is the check
