import json
import os
from procurement.project import create_project
from procurement.store import migrate, snapshots


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
