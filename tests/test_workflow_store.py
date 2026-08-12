"""Store tests for the RFQ workflow: identity, scoping and history.

Every reference in this subsystem binds to an immutable id, never to a name —
project names are user-editable at any time. The tests below are the guard on
that: a rename must not orphan a project or its items.
"""
from datetime import date

import pytest

from workflow.stages import Stage
from workflow.store import WorkflowStore


def make_store() -> WorkflowStore:
    return WorkflowStore()


def make_project(store: WorkflowStore):
    return store.create_project(
        name="Haliba Field Development",
        code="HAL",
        client="Al Dhafra Petroleum",
        location="Haliba field, UAE",
        live_period_start=date(2026, 1, 1),
        live_period_end=date(2029, 12, 31),
    )


def test_created_project_gets_a_stable_generated_id():
    store = make_store()
    p = make_project(store)
    assert p.id.startswith("prj_")
    assert store.get_project(p.id) is not None


def test_rename_preserves_the_id():
    store = make_store()
    p = make_project(store)
    original_id = p.id

    store.rename_project(p.id, "Haliba Phase 2")

    renamed = store.get_project(original_id)
    assert renamed is not None, "renaming must not orphan the project"
    assert renamed.id == original_id
    assert renamed.name == "Haliba Phase 2"


def test_rename_of_unknown_project_raises():
    store = make_store()
    with pytest.raises(KeyError):
        store.rename_project("prj_missing", "Anything")


def test_two_projects_may_share_a_name_but_never_an_id():
    store = make_store()
    a = make_project(store)
    b = make_project(store)
    assert a.name == b.name
    assert a.id != b.id


def make_item(store: WorkflowStore, project_id: str, item_type: str = "Generator"):
    return store.create_item(
        project_id=project_id,
        item_type=item_type,
        description=f"{item_type} package",
        qty=2,
        uom="ea",
        discipline="Electrical",
        estimated_value_aed=4_200_000,
        required_on_site=date(2027, 6, 1),
    )


def test_item_belongs_to_its_project_by_id():
    store = make_store()
    p = make_project(store)
    item = make_item(store, p.id)
    assert item.project_id == p.id
    assert item.id.startswith("itm_")


def test_items_for_project_returns_only_that_projects_items():
    store = make_store()
    a = make_project(store)
    b = make_project(store)
    make_item(store, a.id, "Generator")
    make_item(store, a.id, "Cable")
    make_item(store, b.id, "Generator")

    assert len(store.items_for_project(a.id)) == 2
    assert len(store.items_for_project(b.id)) == 1


def test_creating_an_item_under_an_unknown_project_raises():
    store = make_store()
    with pytest.raises(KeyError):
        make_item(store, "prj_missing")


def test_renaming_a_project_does_not_orphan_its_items():
    store = make_store()
    p = make_project(store)
    make_item(store, p.id)
    store.rename_project(p.id, "Renamed Entirely")
    assert len(store.items_for_project(p.id)) == 1


def test_date_inside_live_period_validates():
    store = make_store()
    p = make_project(store)
    assert store.validate_against_live_period(p.id, date(2027, 6, 1)) is None


def test_date_outside_live_period_returns_a_reason():
    store = make_store()
    p = make_project(store)
    reason = store.validate_against_live_period(p.id, date(2030, 1, 1))
    assert reason is not None
    assert "live period" in reason.lower()


def make_rfq(store: WorkflowStore, project_id: str, item_ids: list[str]):
    return store.create_rfq(
        project_id=project_id,
        item_ids=item_ids,
        reference="ADP-RFQ-2026-014",
        package="Wellhead & CGF tie-in materials",
        discipline="Mechanical / piping",
        value_estimate_aed=46_200_000,
    )


def test_new_rfq_starts_at_scoping():
    store = make_store()
    p = make_project(store)
    item = make_item(store, p.id)
    rfq = make_rfq(store, p.id, [item.id])
    assert rfq.stage is Stage.SCOPING
    assert rfq.project_id == p.id
    assert rfq.item_ids == [item.id]


def test_rfq_may_span_several_items():
    store = make_store()
    p = make_project(store)
    a = make_item(store, p.id, "Generator")
    b = make_item(store, p.id, "Cable")
    rfq = make_rfq(store, p.id, [a.id, b.id])
    assert set(rfq.item_ids) == {a.id, b.id}


def test_creation_records_an_opening_history_entry():
    store = make_store()
    p = make_project(store)
    item = make_item(store, p.id)
    rfq = make_rfq(store, p.id, [item.id])
    assert len(rfq.history) == 1
    assert rfq.history[0].from_stage is None
    assert rfq.history[0].to_stage is Stage.SCOPING


def test_allowed_transition_advances_the_stage_and_appends_history():
    store = make_store()
    p = make_project(store)
    item = make_item(store, p.id)
    rfq = make_rfq(store, p.id, [item.id])

    updated = store.transition(rfq.id, Stage.SHORTLISTING, by="amal@example.com")

    assert updated.stage is Stage.SHORTLISTING
    assert len(updated.history) == 2
    assert updated.history[-1].from_stage is Stage.SCOPING
    assert updated.history[-1].by == "amal@example.com"


def test_disallowed_transition_raises_and_leaves_the_stage_untouched():
    store = make_store()
    p = make_project(store)
    item = make_item(store, p.id)
    rfq = make_rfq(store, p.id, [item.id])

    with pytest.raises(ValueError, match="not allowed"):
        store.transition(rfq.id, Stage.ISSUED, by="amal@example.com")

    assert store.get_rfq(rfq.id).stage is Stage.SCOPING


def test_backward_transition_preserves_the_prior_attempt():
    store = make_store()
    p = make_project(store)
    item = make_item(store, p.id)
    rfq = make_rfq(store, p.id, [item.id])
    for target in [
        Stage.SHORTLISTING,
        Stage.ISSUED,
        Stage.CLARIFICATIONS,
        Stage.BIDS_RECEIVED,
        Stage.EVALUATION,
    ]:
        store.transition(rfq.id, target, by="amal@example.com")

    retendered = store.transition(
        rfq.id, Stage.ISSUED, by="amal@example.com", reason="retender — all bids over estimate"
    )

    assert retendered.stage is Stage.ISSUED
    # the first pass through ISSUED is still in the record
    issued_entries = [h for h in retendered.history if h.to_stage is Stage.ISSUED]
    assert len(issued_entries) == 2
    assert issued_entries[-1].reason == "retender — all bids over estimate"


def test_creating_an_rfq_against_a_foreign_item_raises():
    store = make_store()
    a = make_project(store)
    b = make_project(store)
    foreign = make_item(store, b.id)
    with pytest.raises(ValueError, match="does not belong"):
        make_rfq(store, a.id, [foreign.id])
