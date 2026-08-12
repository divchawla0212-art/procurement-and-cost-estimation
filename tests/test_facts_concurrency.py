"""BUG-010: concurrent mutations of facts.json must not lose each other.

`save_facts` writes a vendor's whole document, and every mutation used to be a
load-modify-save spelled out at the call site — with an LLM extraction sitting
between the load and the save in the worst of them. Two of those overlapping
means the later save writes back fields it read before the earlier one changed
them: a reviewer's `technical_feedback` discarded by a run finishing in the
same window, or a corrected FX rate's totals overwritten by the run's copy of
the project read minutes earlier.

Tasks 1-5 converted every production writer to `snapshots.update_facts`. Each
of those tasks could only test one writer at a time — that is what per-task
TDD structurally produces — and **every defect BUG-010 names needs two writers
to see**. This file is the matrix that observes them together. One test per
row; each names the invariant it defends and which task owns it.

Two mechanisms force the window, in preference to racing for it:

* **run-side rows** issue the competing write from inside a patched
  `extract_bid`/`extract_tech_facts`. The window *is* the extraction, so this
  is deterministic without any hook at all.
* **short-window rows** (a feedback save against `renormalize`, or against a
  run's own store write) need `_hold_facts_write`, which holds one writer open
  between its read and its write — the `_hold_first_project_write` shape from
  `tests/test_project_concurrency.py`, retargeted at a vendor's facts path.

**Every rate-sensitive row carries a vacuity guard.** The stock mock client
answers `{}` for every schema, so a quotation comes back `currency=""`,
`base_price=0.0`, and `normalize_bid` yields 0.0 at *any* rate — a
rate-sensitive assertion written against it cannot fail whatever the code
does. This was not hypothetical: BUG-010's H2 first measured 0/8 against
exactly such a fixture and the result was worthless. `_assert_rate_sensitive`
fails loudly, with that explanation, before any row asserts an outcome.
"""
import os
import threading

import pytest

from procurement import pipeline
from procurement.feedback import save_feedback
from procurement.models import VendorBid
from procurement.normalize import normalize_bid
from procurement.pipeline import run_ingestion
from procurement.project import create_project, load_project, update_project
from procurement.renormalize import renormalize
from procurement.store import snapshots
from procurement.store.models import Override
from tests.test_pipeline_lifecycle import RoutingClient


# Padding appended to every synthetic fixture body: real quotations and
# datasheets are always well over MIN_EXTRACTABLE_CHARS, and a fixture short
# enough to trip the pipeline's no-readable-text guard would silently turn
# these into tests of the guard rather than of the store writers.
_PAD = (b" This synthetic fixture body is padded with filler prose so its "
        b"character count clears the pipeline's minimum-extractable-text "
        b"guard, letting the extraction logic under test run rather than "
        b"the guard itself.")


def _project(tmp_path, files, *, rates=None, target="USD"):
    """A project with `files` (relative to the vendors dir) already unpacked.

    Written straight into the vendor directories rather than through a ZIP:
    the plan's own snippets omitted `create_project`, and every task on this
    bug tripped over `load_project` raising FileNotFoundError as a result. The
    project record — and its vendor roster, which `_prune_orphan_facts` and
    the stale-vendor sweep both read — is set up here, once.
    """
    root = str(tmp_path)
    create_project(root, "P", target_currency=target)
    vendors = set()
    for rel, body in files.items():
        vendor = rel.split("/")[0]
        vendors.add(vendor)
        path = tmp_path / "p" / "vendors" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body + _PAD)
    with update_project(root, "p") as project:
        project.vendors = sorted(vendors)
        if rates:
            project.fx_rates = dict(rates)
    return root


def _write(tmp_path, rel, body):
    (tmp_path / "p" / "vendors" / rel).write_bytes(body + _PAD)


def _set_rates(root, rates, slug="p"):
    with update_project(root, slug) as project:
        project.fx_rates = dict(rates)


def _docs(root, slug="p"):
    """Documents keyed by basename."""
    return {d.path.rsplit("/", 1)[-1]: d
            for d in snapshots.load_documents(root, slug)}


class _EurQuoter(RoutingClient):
    """RoutingClient quoting EUR at a configurable price, not USD 1000.

    Required, not decorative — see this module's docstring. A foreign currency
    and a non-zero price are the only things that make an FX multiplier
    observable at all. Mirrors `test_pipeline_lifecycle.EurQuotingClient`,
    with the price a parameter so a second run can produce a *different*
    number and staleness becomes visible rather than coincidentally equal.
    """

    def __init__(self, price=1000.0, **kw):
        super().__init__(**kw)
        self.price = price

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        out = super().classify_structure(prompt, output_schema, context_text, images)
        if self._kind(output_schema) == "quotation":
            return {"currency": "EUR", "base_price": self.price,
                    "freight_included": True}
        return out


class _RevisionEurQuoter(_EurQuoter):
    """Prices the document it is shown: EUR 2000 for the marked revision,
    EUR 1000 for anything else. One client can then serve both runs of a
    revision row, so the two runs differ only in which document is live."""

    MARKER = "REVISION TWO"

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        if self._kind(output_schema) == "quotation":
            self.price = 2000.0 if self.MARKER in (context_text or "") else 1000.0
        return super().classify_structure(prompt, output_schema, context_text, images)


def _assert_rate_sensitive(facts, vendor, *, currency="EUR"):
    """THE VACUITY GUARD. Assert this fixture can actually observe a rate.

    A stored `commercial` of `currency=""`, `base_price=0.0` normalizes to
    0.0 at every rate, so a row asserting a converted total against it passes
    no matter what the code under test does. Called before the outcome
    assertion of every rate-sensitive row.
    """
    assert facts is not None, (
        f"vacuous fixture: no facts stored for {vendor} at all, so no "
        "rate-sensitive outcome can be observed")
    assert facts.commercial, (
        f"vacuous fixture: {vendor} has no stored commercial facts, so "
        "normalization never runs and this row cannot fail")
    got = facts.commercial.get("currency")
    assert got == currency, (
        f"vacuous fixture: {vendor} quoted {got!r}, not {currency!r} — a "
        "same-currency bid converts at 1.0 and the FX multiplier is never "
        "observable, so this row cannot fail")
    assert facts.commercial.get("base_price"), (
        f"vacuous fixture: {vendor}'s base_price is "
        f"{facts.commercial.get('base_price')!r} — a zero price normalizes to "
        "0.0 at ANY rate, so this rate-sensitive row cannot fail. This is the "
        "exact fixture that made BUG-010's H2 measure 0/8 for nothing.")


def _hold_facts_write(monkeypatch, vendor, when=None):
    """Hold a vendor's facts.json write open, between its read and its write.

    Hooks `layout.atomic_write_json` rather than `snapshots.save_facts`, for
    `_hold_first_project_write`'s reason one file over: every facts write ends
    up here regardless of which module issued it, so the hook survives the fix
    it is written against. Holding happens *inside* `update_facts`'s lock,
    which is the point — a competing writer that blocks here is the serialised
    behaviour, and one that sails past is the defect.

    `when` picks which write to hold, given the document about to be written;
    the default holds the first for this vendor.

    Returns (in_gap, may_proceed).
    """
    from procurement.store import layout

    real_write = layout.atomic_write_json
    in_gap, may_proceed = threading.Event(), threading.Event()
    held = {"done": False}
    marker = os.path.join("vendors", vendor, "facts.json")

    def holding_write(path, data):
        if (marker in str(path) and not held["done"]
                and (when is None or when(data))):
            held["done"] = True
            in_gap.set()
            assert may_proceed.wait(timeout=60), "test never released the held write"
        return real_write(path, data)

    monkeypatch.setattr(layout, "atomic_write_json", holding_write)
    return in_gap, may_proceed


def _thread(target, errors):
    """A thread whose exception is reported rather than swallowed.

    Task 1 learned this the hard way: a `threading.Barrier` timeout raised
    BrokenBarrierError *inside* the worker, the worker died silently, and the
    test passed regardless of which lock the code under test used. Every
    thread in this file funnels its exception into a list the test asserts is
    empty.
    """
    def run():
        try:
            target()
        except BaseException as exc:            # noqa: BLE001 - reported, not swallowed
            errors.append(exc)
    return threading.Thread(target=run)


def _during_extraction(monkeypatch, action):
    """Run `action()` from inside the run's quotation extraction.

    The run-side forcing mechanism. No hook into the store is needed: the
    window BUG-010 describes *is* the extraction, and `extract_bid` runs
    inside it, before the run takes the vendor lock. Deterministic, and it
    matches the shape Task 4's tests already use.
    """
    real_extract = pipeline.extract_bid

    def extract_then_act(*a, **kw):
        result = real_extract(*a, **kw)
        action()
        return result

    monkeypatch.setattr(pipeline, "extract_bid", extract_then_act)


def _normalized_matches_project(root, slug="p"):
    """The §2 invariant, as a predicate: every vendor's stored `normalized`
    equals what `normalize_bid` produces from its stored `commercial` and the
    project's *current* settings. Returns a list of complaints."""
    project = load_project(root, slug)
    bad = []
    for vendor in snapshots.list_fact_vendors(root, slug):
        facts = snapshots.load_facts(root, slug, vendor)
        if facts is None or not facts.commercial:
            continue
        want = normalize_bid(VendorBid.model_validate(facts.commercial),
                             project.target_currency, project.fx_rates).model_dump()
        if facts.normalized != want:
            bad.append(f"{vendor}: stored "
                       f"{(facts.normalized or {}).get('normalized_total')!r}, "
                       f"current settings give {want.get('normalized_total')!r}")
    return bad


# ---------------------------------------------------------------------------
# H1 — a reviewer's note written while a run is extracting
# ---------------------------------------------------------------------------

def test_feedback_written_during_a_run_survives_the_runs_saves(tmp_path, monkeypatch):
    """Invariant: the run never assigns `technical_feedback` (Task 4).

    The reported case. `run_ingestion` read a vendor's facts, spent minutes in
    an LLM call, and wrote the whole document back — including a
    `technical_feedback` copied from before the call. A note saved in that
    window was silently gone by the time the run finished.
    """
    root = _project(tmp_path, {"KERUI/Quotation.txt": b"base price 1000"})
    run_ingestion(root, "p", _EurQuoter())      # so the vendor has facts to note on

    _during_extraction(monkeypatch, lambda: save_feedback(
        root, "p", "KERUI", "rating is quoted at 60Hz", "reviewer check"))
    run_ingestion(root, "p", _EurQuoter(price=2000.0), force=True)

    stored = snapshots.load_facts(root, "p", "KERUI")
    assert stored.technical_feedback == "rating is quoted at 60Hz", (
        "the run discarded a note saved during its own extraction: stored "
        f"{stored.technical_feedback!r}")
    assert stored.commercial["base_price"] == 2000.0, \
        "the run's own extraction result did not land"


# ---------------------------------------------------------------------------
# H2 — an FX rate written while a run is extracting
# ---------------------------------------------------------------------------

def test_an_fx_rate_set_during_a_run_leaves_no_stale_normalized(tmp_path, monkeypatch):
    """Invariant: the run normalizes against the project as it is NOW (Task 4).

    No rate at all before the run, so every vendor's `normalized` would come
    back `no_fx_rate` if the run normalized against the copy of the project it
    read at its start. Asserted over *every* vendor, not just the one whose
    extraction set the rate.
    """
    root = _project(tmp_path, {"KERUI/Quotation.txt": b"base price 1000",
                               "ADPOWER/Quotation.txt": b"base price 1000"})
    assert not load_project(root, "p").fx_rates, "the row needs to start with no rate"

    _during_extraction(monkeypatch, lambda: _set_rates(root, {"EUR": 1.08}))
    run_ingestion(root, "p", _EurQuoter())

    for vendor in ("KERUI", "ADPOWER"):
        _assert_rate_sensitive(snapshots.load_facts(root, "p", vendor), vendor)
    assert load_project(root, "p").fx_rates == {"EUR": 1.08}, \
        "the mid-run rate write was itself lost — this row would then be vacuous"
    assert not _normalized_matches_project(root), \
        f"a vendor's normalized is stale against the project's stored rate: " \
        f"{_normalized_matches_project(root)}"
    for vendor in ("KERUI", "ADPOWER"):
        stored = snapshots.load_facts(root, "p", vendor)
        assert abs(stored.normalized["normalized_total"] - 1080.0) < 0.01, \
            f"{vendor} normalized at the wrong rate: {stored.normalized}"


def test_an_fx_rate_corrected_during_a_run_stores_the_corrected_total(
        tmp_path, monkeypatch):
    """Invariant: the run normalizes against the project as it is NOW (Task 4).

    The severity half of H2, and the reason it is S1 rather than S2. With no
    rate at all the store is left visibly blank; with a rate *corrected*
    mid-run from 1.08 to 1.15 the store keeps a total that is merely wrong —
    1080.0, a plausible number nobody would query, in a document that decides
    an award.
    """
    root = _project(tmp_path, {"KERUI/Quotation.txt": b"base price 1000"},
                    rates={"EUR": 1.08})
    run_ingestion(root, "p", _EurQuoter())
    stored = snapshots.load_facts(root, "p", "KERUI")
    _assert_rate_sensitive(stored, "KERUI")
    assert abs(stored.normalized["normalized_total"] - 1080.0) < 0.01, \
        f"the row's premise did not hold: {stored.normalized}"

    # The buyer corrects the rate while the re-extraction is in flight.
    _during_extraction(monkeypatch, lambda: _set_rates(root, {"EUR": 1.15}))
    run_ingestion(root, "p", _EurQuoter(), force=True)

    stored = snapshots.load_facts(root, "p", "KERUI")
    _assert_rate_sensitive(stored, "KERUI")
    total = stored.normalized["normalized_total"]
    assert abs(total - 1150.0) < 0.01, (
        f"stored {total}, want 1150.0 — a stale rate leaves a plausible wrong "
        "number, not a blank")


# ---------------------------------------------------------------------------
# The reverse direction — the run's write is the one at risk
# ---------------------------------------------------------------------------

def test_a_feedback_write_in_flight_does_not_undo_the_runs_extraction(
        tmp_path, monkeypatch):
    """Invariant: `update_facts` re-reads INSIDE the lock (Task 1), and
    `save_feedback` writes only `technical_feedback` (Task 2).

    The mirror of H1: here the note is the earlier writer and the run's result
    is what must survive. `save_feedback` is issued while the run is held
    between its own read and its own write, so a `load_facts` outside the lock
    would have the note's save write back a pre-run copy of `commercial` —
    losing an extraction that had already succeeded.
    """
    root = _project(tmp_path, {"KERUI/Quotation.txt": b"base price 1000"},
                    rates={"EUR": 1.08})
    run_ingestion(root, "p", _EurQuoter())          # commercial = EUR 1000

    in_gap, may_proceed = _hold_facts_write(monkeypatch, "KERUI")
    errors: list = []
    runner = _thread(lambda: run_ingestion(root, "p", _EurQuoter(price=2000.0),
                                           force=True), errors)
    runner.start()
    try:
        assert in_gap.wait(timeout=60), "the run never reached its facts write"
        noter = _thread(lambda: save_feedback(root, "p", "KERUI",
                                              "priced ex-works", "reviewer check"),
                        errors)
        noter.start()
        # A bounded chance to land *while the run is held*. Unserialised it
        # completes here and the run's pending write is written over a copy
        # that predates it. Serialised it blocks on the vendor lock, this join
        # times out, and it applies afterwards instead. Either way the run is
        # released next, so this cannot deadlock.
        noter.join(timeout=2)
        may_proceed.set()
        noter.join(timeout=60)
    finally:
        may_proceed.set()
        runner.join(timeout=60)

    assert not errors, f"a writer raised: {errors}"
    stored = snapshots.load_facts(root, "p", "KERUI")
    _assert_rate_sensitive(stored, "KERUI")
    assert stored.commercial["base_price"] == 2000.0, (
        "the concurrent feedback save wrote back a pre-run copy of commercial: "
        f"stored {stored.commercial['base_price']}, the run extracted 2000.0")
    assert stored.technical_feedback == "priced ex-works", \
        "the note itself was lost"


def test_renormalize_racing_a_runs_save_leaves_the_section_2_invariant_intact(
        tmp_path, monkeypatch):
    """Invariant: `renormalize` recomputes INSIDE the lock and writes only
    `normalized` (Task 3), against a run's save that is under it (Task 4).

    Constructed so `renormalize`'s pre-pass value is *wrong* by the time it
    takes the lock: the pre-pass sees the old EUR 1000 quotation, while the
    run is about to store a corrected EUR 2000 one. Writing the pre-pass's
    1150.0 back would leave a store whose `normalized` contradicts its own
    `commercial` — the §2 invariant, violated by arithmetic rather than by an
    LLM.
    """
    root = _project(tmp_path, {"KERUI/Quotation.txt": b"base price 1000"},
                    rates={"EUR": 1.08})
    run_ingestion(root, "p", _EurQuoter())
    _assert_rate_sensitive(snapshots.load_facts(root, "p", "KERUI"), "KERUI")
    _set_rates(root, {"EUR": 1.15})          # the buyer corrects the rate

    in_gap, may_proceed = _hold_facts_write(monkeypatch, "KERUI")
    errors: list = []
    runner = _thread(lambda: run_ingestion(root, "p", _EurQuoter(price=2000.0),
                                           force=True), errors)
    runner.start()
    try:
        assert in_gap.wait(timeout=60), "the run never reached its facts write"
        renorm = _thread(lambda: renormalize(root, "p"), errors)
        renorm.start()
        renorm.join(timeout=2)               # blocks on the vendor lock
        may_proceed.set()
        renorm.join(timeout=60)
    finally:
        may_proceed.set()
        runner.join(timeout=60)

    assert not errors, f"a writer raised: {errors}"
    stored = snapshots.load_facts(root, "p", "KERUI")
    _assert_rate_sensitive(stored, "KERUI")
    assert stored.commercial["base_price"] == 2000.0, (
        "renormalize wrote back a pre-run copy of commercial: stored "
        f"{stored.commercial['base_price']}, the run extracted 2000.0")
    complaints = _normalized_matches_project(root)
    assert not complaints, f"§2 invariant violated after both finished: {complaints}"
    assert abs(stored.normalized["normalized_total"] - 2300.0) < 0.01, \
        f"want 2000 EUR @ 1.15 = 2300.0, stored {stored.normalized}"


# ---------------------------------------------------------------------------
# Failure paths — a failed extraction must blank nothing, note included
# ---------------------------------------------------------------------------

def test_a_failed_re_extraction_with_feedback_in_flight_blanks_nothing(
        tmp_path, monkeypatch):
    """Invariant: a failed extraction never blanks previously-good stored
    data, and the run never assigns `technical_feedback` (Task 4).

    Two things could go wrong at once here, which is why the row exists: the
    failure branch could write a half-good record over good data, and the
    save it does make could carry a stale note back over one written during
    the very extraction that failed.
    """
    root = _project(tmp_path, {"KERUI/Quotation.txt": b"base price 1000"},
                    rates={"EUR": 1.08})
    run_ingestion(root, "p", _EurQuoter())
    good = snapshots.load_facts(root, "p", "KERUI")
    _assert_rate_sensitive(good, "KERUI")

    _during_extraction(monkeypatch, lambda: save_feedback(
        root, "p", "KERUI", "check the freight terms", "reviewer check"))
    run_ingestion(root, "p", _EurQuoter(fail_kind="quotation"), force=True)

    stored = snapshots.load_facts(root, "p", "KERUI")
    _assert_rate_sensitive(stored, "KERUI")
    assert stored.commercial == good.commercial, \
        f"a failed re-extraction blanked good commercial facts: {stored.commercial}"
    assert stored.normalized == good.normalized, \
        f"a failed re-extraction blanked good normalized totals: {stored.normalized}"
    assert stored.technical_feedback == "check the freight terms", (
        "the failing run's save discarded a note written during its own "
        f"extraction: {stored.technical_feedback!r}")
    assert _docs(root)["Quotation.txt"].extraction_status == "failed", \
        "the row's premise did not hold: the extraction was supposed to fail"


def test_a_failure_then_a_success_with_feedback_in_flight_recovers_and_keeps_the_note(
        tmp_path, monkeypatch):
    """Invariant: the run never assigns `technical_feedback`, and a recovery
    run's own result must land (Task 4).

    The provider is out on run 1 and back on run 2. `normalized_total` has to
    move from absent to a real converted figure, the document's status from
    `failed` to `ok`, and a note written during the recovering extraction has
    to be there afterwards.
    """
    root = _project(tmp_path, {"KERUI/Quotation.txt": b"base price 1000"},
                    rates={"EUR": 1.08})
    run_ingestion(root, "p", _EurQuoter(fail_kind="quotation"))
    failed = snapshots.load_facts(root, "p", "KERUI")
    assert failed is not None, "the failing run stored no facts record at all"
    assert not failed.commercial, \
        f"the row's premise did not hold: run 1 stored commercial {failed.commercial}"
    assert failed.normalized is None, \
        f"the row's premise did not hold: run 1 stored a total {failed.normalized}"

    with snapshots.update_facts(root, "p", "KERUI") as f:
        f.technical_feedback = "vendor confirmed the outage"

    _during_extraction(monkeypatch, lambda: save_feedback(
        root, "p", "KERUI", "vendor re-sent the quotation", "reviewer check"))
    run_ingestion(root, "p", _EurQuoter(), force=True)

    stored = snapshots.load_facts(root, "p", "KERUI")
    _assert_rate_sensitive(stored, "KERUI")
    assert abs(stored.normalized["normalized_total"] - 1080.0) < 0.01, \
        f"the recovery run's total did not land: {stored.normalized}"
    assert stored.technical_feedback == "vendor re-sent the quotation", (
        "the recovery run carried a pre-extraction copy of the note back over "
        f"one written during it: {stored.technical_feedback!r}")
    assert _docs(root)["Quotation.txt"].extraction_status == "ok", \
        "the document did not recover to ok"


# ---------------------------------------------------------------------------
# Pruning — Task 5's collections, under a concurrent writer
# ---------------------------------------------------------------------------

_DATASHEET = "01 DataSheet Gas Generator.txt"


def test_a_deleted_document_prunes_facts_but_not_the_note_or_the_overrides(
        tmp_path, monkeypatch):
    """Invariant: a stored collection holds exactly the records of its
    currently-live sources (Task 5) — and the prune's save owns *only* those
    collections.

    `_prune_orphan_facts` was a load-modify-save outside any lock, so a note
    written while the run extracted was gone by the time the prune wrote. The
    overrides half is asserted here rather than only named: the prune writes
    `technical`, `deviations` and (when the quotation link dies) `commercial`,
    and must leave `overrides` exactly as it found them — re-reconciling there
    would compare against already-resolved values and corrupt
    `extracted_value`.
    """
    root = _project(tmp_path, {"KERUI/Quotation.txt": b"base price 1000",
                               f"KERUI/{_DATASHEET}": b"Continuous rating 550 kW"},
                    rates={"EUR": 1.08})
    run_ingestion(root, "p", _EurQuoter())
    doc_id = _docs(root)[_DATASHEET].doc_id
    stored = snapshots.load_facts(root, "p", "KERUI")
    assert any(f.get("doc_id") == doc_id for f in stored.technical), \
        "the row's premise did not hold: the datasheet produced no fact to prune"

    # A human correction on a field the run re-extracts every time, recorded
    # against the value the extraction produces — so `reconcile` agrees with
    # it and it must come back byte-identical, conflict flag included.
    override = Override(field_path="commercial.freight_included", value=False,
                        extracted_value=True, author="buyer", at="2026-08-05T00:00:00Z",
                        reason="freight quoted separately in the cover letter")
    with snapshots.update_facts(root, "p", "KERUI") as f:
        f.overrides = [override]
    before = snapshots.load_facts(root, "p", "KERUI").overrides[0].model_dump()

    os.remove(tmp_path / "p" / "vendors" / "KERUI" / _DATASHEET)
    _during_extraction(monkeypatch, lambda: save_feedback(
        root, "p", "KERUI", "datasheet withdrawn by the vendor", "reviewer check"))
    run_ingestion(root, "p", _EurQuoter(), force=True)

    stored = snapshots.load_facts(root, "p", "KERUI")
    assert not any(f.get("doc_id") == doc_id for f in stored.technical), \
        "the deleted datasheet's facts were not pruned"
    assert stored.technical_feedback == "datasheet withdrawn by the vendor", (
        "the prune carried a stale copy back over a note written during the "
        f"run: {stored.technical_feedback!r}")
    assert len(stored.overrides) == 1, \
        f"the override did not survive the prune: {stored.overrides}"
    assert stored.overrides[0].model_dump() == before, (
        "the prune disturbed the override it must leave alone: "
        f"{stored.overrides[0].model_dump()} != {before}")


def test_a_newer_revision_arriving_while_a_rate_is_corrected(tmp_path, monkeypatch):
    """Invariant: the superseded revision's facts are pruned (Task 5) *and*
    the survivor is normalized against the project as it is NOW (Task 4).

    Both halves at once, because each can hide the other: a stale rate leaves
    a plausible total on the right document, and a failed prune leaves a
    correct total on the wrong one.
    """
    root = _project(tmp_path, {"KERUI/Quotation.txt": b"base price 1000"},
                    rates={"EUR": 1.08})
    run_ingestion(root, "p", _RevisionEurQuoter())
    stored = snapshots.load_facts(root, "p", "KERUI")
    _assert_rate_sensitive(stored, "KERUI")
    assert abs(stored.normalized["normalized_total"] - 1080.0) < 0.01, \
        f"the row's premise did not hold: {stored.normalized}"
    old_id = _docs(root)["Quotation.txt"].doc_id

    _write(tmp_path, "KERUI/Quotation Rev2.txt",
           b"base price 2000. " + _RevisionEurQuoter.MARKER.encode())
    _during_extraction(monkeypatch, lambda: _set_rates(root, {"EUR": 1.15}))
    run_ingestion(root, "p", _RevisionEurQuoter())

    docs = _docs(root)
    new_id = docs["Quotation Rev2.txt"].doc_id
    assert docs["Quotation.txt"].superseded_by == new_id, \
        "the row's premise did not hold: lineage did not supersede the old quotation"

    stored = snapshots.load_facts(root, "p", "KERUI")
    _assert_rate_sensitive(stored, "KERUI")
    assert stored.quotation_doc_id == new_id, \
        f"the commercial link still names {stored.quotation_doc_id}, not the revision"
    assert stored.quotation_doc_id != old_id
    assert stored.commercial["base_price"] == 2000.0, \
        f"the superseded revision's price survived: {stored.commercial['base_price']}"
    total = stored.normalized["normalized_total"]
    assert abs(total - 2300.0) < 0.01, (
        f"want the revision's 2000 EUR at the corrected 1.15 = 2300.0, stored "
        f"{total} (1080.0 = the superseded total, 2160.0 = the revision at the "
        "stale rate, 1150.0 = the superseded price at the new rate)")
    assert not _normalized_matches_project(root)


# ---------------------------------------------------------------------------
# The primitive itself — Task 1's guarantees, under two writers
# ---------------------------------------------------------------------------

def test_a_run_over_many_documents_with_writers_throughout_bumps_generation_once(
        tmp_path):
    """Invariant: `generation` bumps once per write transaction, never once
    per file (Task 1).

    `update_facts` deliberately does not bump — `transaction` does, and
    `run_ingestion` wraps its whole extraction in exactly one. Four documents
    across two vendors, with a competing `update_facts` write issued on every
    extractor call, and the counter still has to advance by exactly 1.
    """
    root = _project(tmp_path, {
        "KERUI/Quotation.txt": b"base price 1000",
        f"KERUI/{_DATASHEET}": b"Continuous rating 550 kW",
        "ADPOWER/Quotation.txt": b"base price 1000",
        f"ADPOWER/{_DATASHEET}": b"Continuous rating 700 kW",
    })
    run_ingestion(root, "p", RoutingClient())
    assert len(snapshots.load_documents(root, "p")) == 4, \
        "the row's premise did not hold: four documents must reach an extractor"

    writes = {"n": 0}

    def write_alongside(vendor):
        writes["n"] += 1
        with snapshots.update_facts(root, "p", vendor, create=True) as f:
            f.technical_feedback = f"reviewer note {writes['n']}"

    real_bid, real_tech = pipeline.extract_bid, pipeline.extract_tech_facts

    def bid(vendor, files, client, **kw):
        result = real_bid(vendor, files, client, **kw)
        write_alongside(vendor)
        return result

    def tech(doc_id, path, client, **kw):
        result = real_tech(doc_id, path, client, **kw)
        write_alongside("KERUI" if "KERUI" in str(path) else "ADPOWER")
        return result

    before = snapshots.get_generation(root, "p")
    try:
        pipeline.extract_bid, pipeline.extract_tech_facts = bid, tech
        run_ingestion(root, "p", RoutingClient(), force=True)
    finally:
        pipeline.extract_bid, pipeline.extract_tech_facts = real_bid, real_tech
    after = snapshots.get_generation(root, "p")

    assert writes["n"] >= 4, \
        f"the row's premise did not hold: only {writes['n']} concurrent writes were issued"
    assert after - before == 1, (
        f"generation advanced by {after - before} over one run with "
        f"{writes['n']} concurrent facts writes; a transaction bumps exactly once")


def test_feedback_for_a_vendor_with_no_stored_facts_writes_nothing(tmp_path):
    """Invariant: `update_facts(create=False)` refuses an unknown vendor, and
    a refused note leaves the store untouched (Tasks 1, 2).

    The refusal is raised from inside the transaction on purpose — a check at
    `save_feedback`'s call site would be a TOCTOU, since the vendor can be
    pruned between the check and the lock acquire. So the transaction opens,
    and the proof that this costs nothing is that `generation` does not move
    and no facts file appears.
    """
    root = _project(tmp_path, {"KERUI/Quotation.txt": b"base price 1000"})
    run_ingestion(root, "p", RoutingClient())
    before = snapshots.get_generation(root, "p")
    vendors_before = snapshots.list_fact_vendors(root, "p")

    with pytest.raises(LookupError):
        save_feedback(root, "p", "NOBODY", "a note", "reviewer check")

    assert snapshots.get_generation(root, "p") == before, \
        "a refused note bumped generation"
    assert snapshots.list_fact_vendors(root, "p") == vendors_before, \
        "a refused note created a facts snapshot for an unknown vendor"
    assert snapshots.load_facts(root, "p", "NOBODY") is None


def test_update_facts_re_entered_for_one_vendor_never_silently_loses_a_write(
        tmp_path):
    """Invariant: `update_facts` takes a plain Lock, not an RLock (Task 1).

    Re-entry must block or raise; what it must never do is succeed. An RLock
    would let the inner body save and the outer then save its own older copy
    over the top — precisely the lost write this primitive exists to prevent,
    only harder to see because it happens inside one call stack.

    Run on a daemon worker so a deadlock shows up as a timeout rather than
    hanging the suite. The lock is keyed on this test's own tmp_path, so a
    worker left blocked on it cannot affect anything else.
    """
    root = _project(tmp_path, {"KERUI/Quotation.txt": b"base price 1000"})
    run_ingestion(root, "p", RoutingClient())
    with snapshots.update_facts(root, "p", "KERUI") as f:
        f.technical_feedback = "before"

    outcome: list = []

    def nested():
        try:
            with snapshots.update_facts(root, "p", "KERUI") as outer:
                outer.technical_feedback = "outer"
                with snapshots.update_facts(root, "p", "KERUI") as inner:
                    inner.technical_feedback = "inner"
            outcome.append("completed")
        except BaseException as exc:            # noqa: BLE001 - reported
            outcome.append(exc)

    worker = threading.Thread(target=nested, daemon=True)
    worker.start()
    worker.join(timeout=5)

    if worker.is_alive():
        # Blocked, which is the documented behaviour of a plain Lock. Neither
        # write landed, so nothing was lost silently.
        assert snapshots.load_facts(root, "p", "KERUI").technical_feedback == "before"
        return
    assert outcome and isinstance(outcome[0], BaseException), (
        "re-entering update_facts completed silently — one of the two writes "
        f"was dropped without anyone noticing: {outcome}")
