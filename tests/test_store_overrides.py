import pytest
from procurement.store import overrides as ov
from procurement.store.models import Override


def _rec():
    return {
        "commercial": {"base_price": 1000.0, "currency": "USD"},
        "technical": [{"fact_id": "f-1", "parameter": "h2s", "value": 40.0},
                      {"fact_id": "f-2", "parameter": "kw", "value": 550.0}],
    }


def _o(path, value, extracted=None):
    return Override(field_path=path, value=value, extracted_value=extracted,
                    author="rahul", at="2026-07-29T10:00:00Z", reason="r")


def test_get_by_dotted_path():
    assert ov.get_by_path(_rec(), "commercial.base_price") == 1000.0


def test_get_by_id_selector():
    assert ov.get_by_path(_rec(), "technical[f-2].value") == 550.0


def test_get_missing_path_returns_none():
    assert ov.get_by_path(_rec(), "commercial.nope") is None
    assert ov.get_by_path(_rec(), "technical[f-9].value") is None


def test_index_selectors_are_rejected():
    with pytest.raises(ValueError):
        ov.get_by_path(_rec(), "technical[0].value")


def test_apply_override_wins_over_extracted_value():
    out = ov.apply_overrides(_rec(), [_o("commercial.base_price", 1200.0, 1000.0)])
    assert out["commercial"]["base_price"] == 1200.0


def test_apply_override_survives_list_reordering():
    rec = _rec()
    rec["technical"].reverse()          # order changed by re-extraction
    out = ov.apply_overrides(rec, [_o("technical[f-1].value", 55.0, 40.0)])
    by_id = {f["fact_id"]: f for f in out["technical"]}
    assert by_id["f-1"]["value"] == 55.0
    assert by_id["f-2"]["value"] == 550.0


def test_reconcile_flags_conflict_when_extraction_moved():
    fresh = _rec()
    fresh["commercial"]["base_price"] = 1100.0      # re-extraction disagrees
    out = ov.reconcile([_o("commercial.base_price", 1200.0, 1000.0)], fresh)
    assert out[0].conflict is True
    assert out[0].value == 1200.0                   # override still wins
    assert out[0].extracted_value == 1100.0         # and records what changed


def test_reconcile_clears_conflict_when_extraction_agrees_again():
    prior = _o("commercial.base_price", 1200.0, 1000.0)
    prior.conflict = True
    fresh = _rec()                                   # back to 1000.0
    out = ov.reconcile([prior], fresh)
    assert out[0].conflict is False


def test_reconcile_leaves_untouched_paths_alone():
    fresh = _rec()
    out = ov.reconcile([_o("commercial.base_price", 1200.0, 1000.0)], fresh)
    assert out[0].conflict is False
