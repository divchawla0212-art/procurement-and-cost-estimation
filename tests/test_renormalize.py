import io
import zipfile

from procurement.project import create_project, load_project, save_project, unpack_vendor_zip
from procurement.pipeline import run_ingestion
from procurement.renormalize import renormalize
from procurement.store import events, snapshots
from shared.llm.mock_client import MockLLMClient

_PAD = (b" This synthetic fixture body is padded with filler prose so its "
        b"character count clears the pipeline's minimum-extractable-text guard.")


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
