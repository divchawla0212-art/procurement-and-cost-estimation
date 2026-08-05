import io
import zipfile
from contextlib import contextmanager

from procurement.project import create_project, load_project, save_project, unpack_vendor_zip
from procurement.pipeline import load_dataset, run_ingestion
from procurement.store.models import VendorFacts
import procurement.renormalize as renormalize_module
from procurement.renormalize import migrate_normalization, renormalize
from procurement.store import events, layout, snapshots
from shared.llm.mock_client import MockLLMClient

_PAD = (b" This synthetic fixture body is padded with filler prose so its "
        b"character count clears the pipeline's minimum-extractable-text guard.")


def _project(root, name, *, target_currency="USD", fx_rates=None):
    project = create_project(root, name, target_currency=target_currency)
    if fx_rates is not None:
        project.fx_rates = fx_rates
        save_project(root, project)
    return project


def _eur_project(tmp_path):
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("EUROVEND/Quotation.txt", b"base price 1000" + _PAD)
    z = tmp_path / "v.zip"
    z.write_bytes(buf.getvalue())
    unpack_vendor_zip(root, "p", str(z))
    return root


def _client():
    return MockLLMClient(response={"currency": "EUR", "base_price": 1000.0,
                                   "freight_included": True})


def _set_rate(root, rates):
    project = load_project(root, "p")
    project.fx_rates = rates
    save_project(root, project)


def test_renormalize_applies_a_rate_set_after_ingestion(tmp_path):
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _client())
    assert snapshots.load_facts(root, "p", "EUROVEND").normalized["normalized_total"] is None

    _set_rate(root, {"EUR": 1.08})
    assert renormalize(root, "p") == 1

    facts = snapshots.load_facts(root, "p", "EUROVEND")
    assert abs(facts.normalized["normalized_total"] - 1080.0) <= 0.01
    assert facts.normalized["normalization_status"] == "ok"


def test_renormalize_makes_no_llm_call(tmp_path):
    """The whole point: conversion is arithmetic over an already-extracted price."""
    root = _eur_project(tmp_path)
    client = _client()
    run_ingestion(root, "p", client)
    calls_after_ingestion = len(client.calls)

    _set_rate(root, {"EUR": 1.08})
    renormalize(root, "p")

    assert len(client.calls) == calls_after_ingestion


def test_renormalize_bumps_generation_exactly_once(tmp_path):
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _client())
    before = load_project(root, "p").generation

    _set_rate(root, {"EUR": 1.08})
    renormalize(root, "p")

    assert load_project(root, "p").generation == before + 1


def test_renormalize_that_changes_nothing_bumps_nothing(tmp_path):
    # Task 4 calls this from the read path. Without this rule, `generation`
    # climbs on every page load.
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _client())
    _set_rate(root, {"EUR": 1.08})
    renormalize(root, "p")
    settled = load_project(root, "p").generation

    assert renormalize(root, "p") == 0
    assert load_project(root, "p").generation == settled


def test_renormalize_reverts_to_unconvertible_when_a_rate_is_withdrawn(tmp_path):
    # A stale number surviving the removal of its rate is the original bug
    # running backwards.
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _client())
    _set_rate(root, {"EUR": 1.08})
    renormalize(root, "p")

    _set_rate(root, {})
    assert renormalize(root, "p") == 1

    facts = snapshots.load_facts(root, "p", "EUROVEND")
    assert facts.normalized["normalized_total"] is None
    assert facts.normalized["normalization_status"] == "no_fx_rate"


def test_renormalize_records_an_event(tmp_path):
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _client())
    _set_rate(root, {"EUR": 1.08})
    renormalize(root, "p")

    actions = [e.action for e in events.read_events(root, "p")]
    assert "fx.renormalized" in actions


def test_renormalize_skips_a_vendor_with_no_commercial_facts(tmp_path):
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _client())
    facts = snapshots.load_facts(root, "p", "EUROVEND")
    facts.commercial = None
    snapshots.save_facts(root, "p", facts)

    assert renormalize(root, "p") == 0
    # skipped, not zeroed: a failed extraction never blanks stored data
    assert snapshots.load_facts(root, "p", "EUROVEND").commercial is None


def test_a_new_project_is_stamped_at_the_current_store_version(tmp_path):
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    assert load_project(root, "p").store_version == layout.STORE_VERSION


def test_migration_renormalizes_a_project_left_at_an_older_version(tmp_path):
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _client())
    # a store written before this change: a rate configured, but the stored
    # total still computed under the 1.0 assumption
    _set_rate(root, {"EUR": 1.08})
    facts = snapshots.load_facts(root, "p", "EUROVEND")
    facts.normalized = {"vendor": "EUROVEND", "normalized_currency": "USD",
                        "normalized_total": 1000.0, "adjustments": [],
                        "extraction_status": "ok"}
    snapshots.save_facts(root, "p", facts)
    project = load_project(root, "p")
    project.store_version = 1
    save_project(root, project)

    assert migrate_normalization(root, "p") is True

    facts = snapshots.load_facts(root, "p", "EUROVEND")
    assert abs(facts.normalized["normalized_total"] - 1080.0) <= 0.01
    assert load_project(root, "p").store_version == layout.STORE_VERSION


def test_migration_is_idempotent(tmp_path):
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _client())
    project = load_project(root, "p")
    project.store_version = 1
    save_project(root, project)

    assert migrate_normalization(root, "p") is True
    generation = load_project(root, "p").generation
    assert migrate_normalization(root, "p") is False
    assert load_project(root, "p").generation == generation


def test_reading_a_project_repeatedly_does_not_advance_generation(tmp_path):
    """The read-path hazard. Nothing else in the suite would catch this."""
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _client())
    load_dataset(root, "p")
    settled = load_project(root, "p").generation

    for _ in range(3):
        load_dataset(root, "p")

    assert load_project(root, "p").generation == settled


def test_renormalize_writes_only_normalized(tmp_path):
    root = str(tmp_path)
    _project(root, "p", target_currency="USD", fx_rates={"EUR": 1.08})
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="K",
        commercial={"vendor": "K", "currency": "EUR", "base_price": 1000.0,
                    "freight_included": True},
        technical=[{"doc_id": "d1", "parameter": "flow"}],
        technical_feedback="a reviewer's note",
        quotation_doc_id="d1"))

    assert renormalize(root, "p") == 1

    stored = snapshots.load_facts(root, "p", "K")
    assert abs(stored.normalized["normalized_total"] - 1080.0) < 0.01
    assert stored.technical_feedback == "a reviewer's note"
    assert stored.technical == [{"doc_id": "d1", "parameter": "flow"}]
    assert stored.quotation_doc_id == "d1"


def test_renormalize_recomputes_from_facts_read_inside_the_lock(tmp_path, monkeypatch):
    """The pre-pass is a candidate list, not the value to write. If the stored
    commercial changes between the pre-pass and the write, the value written
    must follow the NEW commercial, not the one the pre-pass saw."""
    root = str(tmp_path)
    _project(root, "p", target_currency="USD", fx_rates={"EUR": 1.0})
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="K", commercial={"vendor": "K", "currency": "EUR",
                                "base_price": 1000.0, "freight_included": True}))

    real_update = snapshots.update_facts
    swapped = []

    @contextmanager
    def swap_then_update(root_, slug_, vendor_, **kw):
        if not swapped:
            swapped.append(True)
            # a concurrent run stores a different price, then we take the lock
            snapshots.save_facts(root_, slug_, VendorFacts(
                vendor=vendor_, commercial={"vendor": vendor_, "currency": "EUR",
                                            "base_price": 2000.0,
                                            "freight_included": True}))
        with real_update(root_, slug_, vendor_, **kw) as f:
            yield f

    monkeypatch.setattr(snapshots, "update_facts", swap_then_update)
    monkeypatch.setattr(renormalize_module.snapshots, "update_facts", swap_then_update)
    renormalize(root, "p")

    stored = snapshots.load_facts(root, "p", "K")
    assert abs(stored.normalized["normalized_total"] - 2000.0) < 0.01, \
        "wrote a total derived from the commercial the pre-pass saw"
