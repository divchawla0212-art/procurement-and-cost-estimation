import json
import os
from contextlib import contextmanager
from unittest import mock

import pytest
from procurement.project import create_project
from procurement.store import migrate, snapshots
from procurement.store.models import VendorFacts


def _write_dataset(root, slug):
    path = os.path.join(root, slug, "dataset.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "bids": [{"vendor": "KERUI", "base_price": 1000.0, "currency": "USD"},
                     {"vendor": "MKON", "base_price": 900.0, "currency": "EUR"}],
            "normalized": [{"vendor": "KERUI", "normalized_total": 1000.0},
                           {"vendor": "MKON", "normalized_total": 972.0}],
            "comparison": {"target_currency": "USD", "rows": []},
        }, fh)
    return path


def test_migrates_bids_and_normalized_into_facts(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    _write_dataset(root, "p")
    assert migrate.migrate_dataset_json(root, "p") is True

    kerui = snapshots.load_facts(root, "p", "KERUI")
    assert kerui.commercial["base_price"] == 1000.0
    assert kerui.normalized["normalized_total"] == 1000.0
    assert snapshots.load_facts(root, "p", "MKON").normalized["normalized_total"] == 972.0


def test_migration_bumps_generation_once(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    _write_dataset(root, "p")
    before = snapshots.get_generation(root, "p")
    migrate.migrate_dataset_json(root, "p")
    assert snapshots.get_generation(root, "p") == before + 1


def test_migration_is_idempotent(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    _write_dataset(root, "p")
    assert migrate.migrate_dataset_json(root, "p") is True
    gen = snapshots.get_generation(root, "p")
    assert migrate.migrate_dataset_json(root, "p") is False
    assert snapshots.get_generation(root, "p") == gen


def test_migration_leaves_the_source_file_alone(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    path = _write_dataset(root, "p")
    migrate.migrate_dataset_json(root, "p")
    assert os.path.exists(path)


def test_no_dataset_is_not_an_error(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    assert migrate.migrate_dataset_json(root, "p") is False


def test_partial_migration_is_retried_rather_than_reported_as_done(tmp_path, monkeypatch):
    """A write set is not atomic as a set: if the second vendor's save fails,
    the first is already on disk. Idempotence must key on a marker written at
    the end, not on 'some vendor has facts' - otherwise the retry declares the
    project migrated and the lost vendor is silently dropped forever."""
    root = str(tmp_path)
    create_project(root, "P")
    _write_dataset(root, "p")
    real_save = snapshots.save_facts
    seen = []

    def flaky(r, s, facts):
        seen.append(facts.vendor)
        if len(seen) == 2:
            raise OSError("disk full")
        return real_save(r, s, facts)

    monkeypatch.setattr(migrate.snapshots, "save_facts", flaky)
    before = snapshots.get_generation(root, "p")
    with pytest.raises(OSError):
        migrate.migrate_dataset_json(root, "p")
    assert snapshots.get_generation(root, "p") == before   # transaction did not commit
    assert snapshots.list_fact_vendors(root, "p") == ["KERUI"]   # partial state on disk

    monkeypatch.setattr(migrate.snapshots, "save_facts", real_save)
    assert migrate.migrate_dataset_json(root, "p") is True, \
        "a partially migrated project must be retried, not reported as already done"
    assert snapshots.load_facts(root, "p", "MKON").normalized["normalized_total"] == 972.0
    assert snapshots.load_facts(root, "p", "KERUI").commercial["base_price"] == 1000.0
    assert migrate.migrate_dataset_json(root, "p") is False   # now genuinely done


def test_retry_does_not_clobber_facts_already_in_the_store(tmp_path, monkeypatch):
    """Facts written after a partial migration (e.g. by an ingestion run) are
    newer than dataset.json; the retry must not overwrite them."""
    root = str(tmp_path)
    create_project(root, "P")
    _write_dataset(root, "p")
    real_save = snapshots.save_facts
    seen = []

    def flaky(r, s, facts):
        seen.append(facts.vendor)
        if len(seen) == 2:
            raise OSError("disk full")
        return real_save(r, s, facts)

    monkeypatch.setattr(migrate.snapshots, "save_facts", flaky)
    with pytest.raises(OSError):
        migrate.migrate_dataset_json(root, "p")
    monkeypatch.setattr(migrate.snapshots, "save_facts", real_save)

    kerui = snapshots.load_facts(root, "p", "KERUI")
    kerui.commercial["base_price"] = 1234.0          # corrected after the crash
    snapshots.save_facts(root, "p", kerui)

    migrate.migrate_dataset_json(root, "p")
    assert snapshots.load_facts(root, "p", "KERUI").commercial["base_price"] == 1234.0


def test_migration_does_not_overwrite_facts_a_run_stored_meanwhile(tmp_path):
    """The check-then-write TOCTOU: facts appearing between the existence
    check and the write must win -- they are newer than dataset.json by
    construction. Before this fix, `migrate_dataset_json` ran its
    `load_facts(...) is not None` check before taking any lock, so a run
    that stored real facts in that window was silently overwritten by
    legacy data a moment later."""
    root = str(tmp_path)
    create_project(root, "P")
    _write_dataset(root, "p")

    real_lock = snapshots.facts_lock

    @contextmanager
    def store_then_lock(root_, slug_, vendor_):
        if vendor_ == "KERUI":
            snapshots.save_facts(root_, slug_, VendorFacts(
                vendor=vendor_,
                commercial={"vendor": vendor_, "base_price": 999.0}))
        with real_lock(root_, slug_, vendor_):
            yield

    with mock.patch.object(migrate.snapshots, "facts_lock", store_then_lock):
        migrate.migrate_dataset_json(root, "p")

    assert snapshots.load_facts(root, "p", "KERUI").commercial["base_price"] == 999.0
