"""Store tests for the RFQ workflow: identity, scoping and history.

Every reference in this subsystem binds to an immutable id, never to a name —
project names are user-editable at any time. The tests below are the guard on
that: a rename must not orphan a project or its items.
"""
from datetime import date

import pytest

from workflow.models.rfq import Attachment
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

    store.update_project(p.id, {"name": "Haliba Phase 2"})

    renamed = store.get_project(original_id)
    assert renamed is not None, "renaming must not orphan the project"
    assert renamed.id == original_id
    assert renamed.name == "Haliba Phase 2"


def test_rename_of_unknown_project_raises():
    store = make_store()
    with pytest.raises(KeyError):
        store.update_project("prj_missing", {"name": "Anything"})


def test_update_project_replaces_only_the_named_fields():
    """A partial update: a field absent from `changes` is left alone, not
    cleared. That is what makes the route's PATCH partial."""
    store = make_store()
    p = make_project(store)

    updated = store.update_project(p.id, {"name": "Haliba Phase 2", "status": "On Hold"})

    assert updated.name == "Haliba Phase 2"
    assert updated.status == "On Hold"
    assert updated.code == "HAL"
    assert updated.id == p.id
    # Replaced in place. A record appended rather than replaced would leave two.
    assert len(store.list_projects()) == 1


def test_update_project_refuses_to_change_the_id():
    store = make_store()
    p = make_project(store)
    with pytest.raises(ValueError, match="id"):
        store.update_project(p.id, {"id": "prj_somethingelse"})
    assert store.get_project(p.id) is not None


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
    store.update_project(p.id, {"name": "Renamed Entirely"})
    assert len(store.items_for_project(p.id)) == 1


def test_get_item_returns_none_for_an_unknown_id():
    assert make_store().get_item("itm_missing") is None


def test_update_item_replaces_only_the_named_fields():
    store = make_store()
    p = make_project(store)
    item = make_item(store, p.id, "Gas generator")

    updated = store.update_item(item.id, {"qty": 3, "is_long_lead": True})

    assert updated.qty == 3
    assert updated.is_long_lead is True
    assert updated.item_type == "Gas generator"
    assert updated.id == item.id
    assert len(store.items_for_project(p.id)) == 1


def test_update_of_unknown_item_raises():
    store = make_store()
    with pytest.raises(KeyError):
        store.update_item("itm_missing", {"qty": 3})


def test_update_item_refuses_to_move_it_between_projects():
    """An item's parent is not editable. Moving one would silently change which
    project's RFQs are allowed to cover it, so the honest operation is a delete
    and a re-add."""
    store = make_store()
    a = make_project(store)
    b = make_project(store)
    item = make_item(store, a.id, "Cable")

    with pytest.raises(ValueError, match="project_id"):
        store.update_item(item.id, {"project_id": b.id})

    assert store.get_item(item.id).project_id == a.id


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


def freeze_and_prepare(store: WorkflowStore, rfq_id: str) -> None:
    """Satisfy every forward gate up to EVALUATION, so a test about the
    transition mechanic is not also a test of the gates."""
    store.set_technical_package(
        rfq_id,
        revision="Rev. B",
        basis_of_design="basis",
        attachments=[Attachment(doc_code="HAL-PID-001", title="P&ID", revision="Rev. C")],
    )
    store.freeze_package(rfq_id, by="lead.engineer@example.com")
    store.add_shortlist_entry(rfq_id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)
    store.approve_shortlist(rfq_id, by="procurement@example.com")
    store.set_tbe_template(rfq_id, criteria=["Throughput"])
    bid = store.register_bid(rfq_id, vendor_name="Galfar", headline_price_aed=46_200_000)
    store.select_bids(rfq_id, [bid.id], by="client@example.com", rationale="Only compliant bid")


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
    freeze_and_prepare(store, rfq.id)

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
    freeze_and_prepare(store, rfq.id)
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


def seed_rfq(store: WorkflowStore):
    p = make_project(store)
    item = make_item(store, p.id)
    return make_rfq(store, p.id, [item.id])


def test_technical_package_stores_attachments_at_revisions():
    store = make_store()
    rfq = seed_rfq(store)
    store.set_technical_package(
        rfq.id,
        revision="Rev. B",
        basis_of_design="129 wellhead tie-ins, CGF, 16in export line to ASAB",
        attachments=[Attachment(doc_code="HAL-PID-001", title="P&ID export line", revision="Rev. C")],
    )
    pkg = store.get_technical_package(rfq.id)
    assert pkg.revision == "Rev. B"
    assert pkg.attachments[0].doc_code == "HAL-PID-001"
    assert pkg.frozen_at is None


def test_freezing_a_package_records_who_and_when():
    store = make_store()
    rfq = seed_rfq(store)
    store.set_technical_package(
        rfq.id,
        revision="Rev. B",
        basis_of_design="basis",
        attachments=[Attachment(doc_code="HAL-PID-001", title="P&ID", revision="Rev. C")],
    )
    frozen = store.freeze_package(rfq.id, by="lead.engineer@example.com")
    assert frozen.frozen_at is not None
    assert frozen.frozen_by == "lead.engineer@example.com"


def test_freezing_is_blocked_when_an_attachment_has_no_revision():
    store = make_store()
    rfq = seed_rfq(store)
    store.set_technical_package(
        rfq.id,
        revision="Rev. B",
        basis_of_design="basis",
        attachments=[Attachment(doc_code="HAL-PID-001", title="P&ID", revision=None)],
    )
    with pytest.raises(ValueError, match="definite revision"):
        store.freeze_package(rfq.id, by="lead.engineer@example.com")


def test_shortlist_entries_record_inclusion_and_override():
    store = make_store()
    rfq = seed_rfq(store)
    store.add_shortlist_entry(rfq.id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)
    store.add_shortlist_entry(rfq.id, vendor_name="OQC", prequal_status="Under review",
                              scope_code_fit=False, included=False)
    entries = store.shortlist_for(rfq.id)
    assert len(entries) == 2
    assert [e.included for e in entries] == [True, False]


def test_shortlist_approval_is_recorded():
    store = make_store()
    rfq = seed_rfq(store)
    store.add_shortlist_entry(rfq.id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)
    assert store.is_shortlist_approved(rfq.id) is False
    store.approve_shortlist(rfq.id, by="procurement@example.com")
    assert store.is_shortlist_approved(rfq.id) is True


def test_approving_an_empty_shortlist_is_rejected():
    store = make_store()
    rfq = seed_rfq(store)
    with pytest.raises(ValueError, match="empty shortlist"):
        store.approve_shortlist(rfq.id, by="procurement@example.com")


def test_tbe_template_records_its_source_when_pulled_from_a_past_project():
    store = make_store()
    rfq = seed_rfq(store)
    store.set_tbe_template(rfq.id, criteria=["Throughput", "Materials", "Delivery"],
                           source_rfq_reference="ADP-RFQ-2025-008")
    tbe = store.get_tbe_template(rfq.id)
    assert tbe.criteria == ["Throughput", "Materials", "Delivery"]
    assert tbe.source_rfq_reference == "ADP-RFQ-2025-008"


def test_vdrl_lines_accumulate_for_an_rfq():
    store = make_store()
    rfq = seed_rfq(store)
    store.add_vdrl_line(rfq.id, doc_code="RFQ-014-GA-001", title="Skid GA drawing",
                        doc_type="GA Drawing", mandatory=True)
    store.add_vdrl_line(rfq.id, doc_code="RFQ-014-DS-001", title="Datasheet",
                        doc_type="Datasheet", mandatory=True)
    assert len(store.vdrl_for(rfq.id)) == 2


def test_artifacts_reject_an_unknown_rfq():
    """Every artifact writer binds to a live RFQ id — no orphan artifacts."""
    store = make_store()
    with pytest.raises(KeyError):
        store.add_vdrl_line("rfq_missing", doc_code="GA", title="GA", doc_type="Doc")
    with pytest.raises(KeyError):
        store.add_shortlist_entry("rfq_missing", vendor_name="Galfar",
                                  prequal_status="Qualified", scope_code_fit=True, included=True)
    with pytest.raises(KeyError):
        store.set_tbe_template("rfq_missing", criteria=["Throughput"])
    with pytest.raises(KeyError):
        store.set_technical_package("rfq_missing", revision="Rev. A",
                                    basis_of_design="basis", attachments=[])


def test_vdrl_summary_counts_received_against_required():
    store = make_store()
    rfq = seed_rfq(store)
    for code in ["GA-001", "DS-001", "TP-001"]:
        store.add_vdrl_line(rfq.id, doc_code=code, title=code, doc_type="Doc", mandatory=True)
    bid = store.register_bid(rfq.id, vendor_name="Petrofac", headline_price_aed=51_400_000)

    store.record_vdrl_receipt(bid.id, doc_code="GA-001", state="received", revision="Rev. A")
    store.record_vdrl_receipt(bid.id, doc_code="DS-001", state="received", revision="Rev. A")
    store.record_vdrl_receipt(bid.id, doc_code="TP-001", state="not_received")

    received, required = store.vdrl_summary(bid.id)
    assert (received, required) == (2, 3)
    assert store.missing_vdrl_lines(bid.id) == ["TP-001"]


def test_unreadable_is_distinct_from_not_received():
    store = make_store()
    rfq = seed_rfq(store)
    store.add_vdrl_line(rfq.id, doc_code="GA-001", title="GA", doc_type="Doc", mandatory=True)
    bid = store.register_bid(rfq.id, vendor_name="Petrofac", headline_price_aed=51_400_000)
    store.record_vdrl_receipt(bid.id, doc_code="GA-001", state="unreadable")

    received, required = store.vdrl_summary(bid.id)
    assert received == 0, "an unreadable document is not a received document"
    assert store.missing_vdrl_lines(bid.id) == ["GA-001"]
    # but the distinction survives in the record — a corrupt file is chased,
    # a missing one is escalated
    assert [r.state for r in store.receipts_for(bid.id)] == ["unreadable"]


def test_optional_vdrl_lines_do_not_count_against_a_bid():
    store = make_store()
    rfq = seed_rfq(store)
    store.add_vdrl_line(rfq.id, doc_code="GA-001", title="GA", doc_type="Doc", mandatory=True)
    store.add_vdrl_line(rfq.id, doc_code="OPT-001", title="Nice to have",
                        doc_type="Doc", mandatory=False)
    bid = store.register_bid(rfq.id, vendor_name="Petrofac", headline_price_aed=51_400_000)
    store.record_vdrl_receipt(bid.id, doc_code="GA-001", state="received", revision="Rev. A")

    assert store.vdrl_summary(bid.id) == (1, 1)
    assert store.missing_vdrl_lines(bid.id) == []


def test_selection_records_who_and_why():
    store = make_store()
    rfq = seed_rfq(store)
    a = store.register_bid(rfq.id, vendor_name="Galfar", headline_price_aed=46_200_000)
    store.register_bid(rfq.id, vendor_name="Petrofac", headline_price_aed=51_400_000)

    store.select_bids(rfq.id, [a.id], by="client@example.com", rationale="Lowest compliant bid")

    shortlist = store.get_bid_shortlist(rfq.id)
    assert shortlist.selected_bid_ids == [a.id]
    assert shortlist.selected_by == "client@example.com"
    assert shortlist.rationale == "Lowest compliant bid"


def test_non_selected_bids_are_retained():
    """Selection narrows what is evaluated; it never deletes what was bid."""
    store = make_store()
    rfq = seed_rfq(store)
    a = store.register_bid(rfq.id, vendor_name="Galfar", headline_price_aed=46_200_000)
    b = store.register_bid(rfq.id, vendor_name="Petrofac", headline_price_aed=51_400_000)

    store.select_bids(rfq.id, [a.id], by="client@example.com", rationale="Lowest compliant bid")

    assert {bid.id for bid in store.bids_for(rfq.id)} == {a.id, b.id}


def test_selection_without_a_rationale_is_rejected():
    store = make_store()
    rfq = seed_rfq(store)
    bid = store.register_bid(rfq.id, vendor_name="Galfar", headline_price_aed=46_200_000)
    with pytest.raises(ValueError, match="rationale"):
        store.select_bids(rfq.id, [bid.id], by="client@example.com", rationale="")


def test_selecting_a_bid_from_another_rfq_is_rejected():
    store = make_store()
    mine = seed_rfq(store)
    theirs = seed_rfq(store)
    foreign = store.register_bid(theirs.id, vendor_name="Galfar", headline_price_aed=1)
    with pytest.raises(ValueError, match="does not belong"):
        store.select_bids(mine.id, [foreign.id], by="client@example.com", rationale="Cheapest")


def test_evaluation_gate_blocks_until_bids_are_selected():
    store = make_store()
    rfq = seed_rfq(store)
    from workflow.gates import check_gate
    result = check_gate(store, rfq.id, Stage.BIDS_RECEIVED, Stage.EVALUATION)
    assert result.passed is False
    assert "select" in result.reason.lower()


# -- editing artifacts: the wizard needs add *and* remove ---------------------

def test_shortlist_entries_carry_a_stable_id():
    """Removal addresses a member by id, never by position: two vendors can be
    added, one removed, and the list order is not a contract."""
    store = make_store()
    rfq = seed_rfq(store)
    a = store.add_shortlist_entry(rfq.id, vendor_name="Galfar", prequal_status="Qualified",
                                  scope_code_fit=True, included=True)
    b = store.add_shortlist_entry(rfq.id, vendor_name="OQC", prequal_status="Under review",
                                  scope_code_fit=False, included=False)
    assert a.id.startswith("sle_")
    assert a.id != b.id


def test_removing_a_shortlist_entry_leaves_the_others():
    store = make_store()
    rfq = seed_rfq(store)
    a = store.add_shortlist_entry(rfq.id, vendor_name="Galfar", prequal_status="Qualified",
                                  scope_code_fit=True, included=True)
    b = store.add_shortlist_entry(rfq.id, vendor_name="OQC", prequal_status="Qualified",
                                  scope_code_fit=True, included=True)

    store.remove_shortlist_entry(rfq.id, a.id)

    assert [e.id for e in store.shortlist_for(rfq.id)] == [b.id]


def test_removing_an_unknown_shortlist_entry_raises():
    store = make_store()
    rfq = seed_rfq(store)
    with pytest.raises(KeyError):
        store.remove_shortlist_entry(rfq.id, "sle_missing")


def test_changing_the_shortlist_revokes_its_approval():
    """Approval is of a specific list of vendors. Letting it survive an edit
    would mean a vendor could be swapped in *after* procurement signed off, and
    the RFQ would still issue as approved."""
    store = make_store()
    rfq = seed_rfq(store)
    entry = store.add_shortlist_entry(rfq.id, vendor_name="Galfar", prequal_status="Qualified",
                                      scope_code_fit=True, included=True)
    store.approve_shortlist(rfq.id, by="procurement@example.com")
    assert store.is_shortlist_approved(rfq.id) is True

    store.add_shortlist_entry(rfq.id, vendor_name="Petrofac", prequal_status="Qualified",
                              scope_code_fit=True, included=True)
    assert store.is_shortlist_approved(rfq.id) is False, "adding a vendor must re-open approval"

    store.approve_shortlist(rfq.id, by="procurement@example.com")
    store.remove_shortlist_entry(rfq.id, entry.id)
    assert store.is_shortlist_approved(rfq.id) is False, "removing a vendor must re-open approval"


def test_vdrl_lines_carry_a_stable_id_and_can_be_removed():
    store = make_store()
    rfq = seed_rfq(store)
    a = store.add_vdrl_line(rfq.id, doc_code="GA-001", title="GA", doc_type="Doc")
    b = store.add_vdrl_line(rfq.id, doc_code="DS-001", title="Datasheet", doc_type="Doc")
    assert a.id.startswith("vdl_")

    store.remove_vdrl_line(rfq.id, a.id)

    assert [line.id for line in store.vdrl_for(rfq.id)] == [b.id]


def test_removing_an_unknown_vdrl_line_raises():
    store = make_store()
    rfq = seed_rfq(store)
    with pytest.raises(KeyError):
        store.remove_vdrl_line(rfq.id, "vdl_missing")


def test_a_frozen_technical_package_cannot_be_replaced():
    """Freezing is the whole point of the Scoping gate: vendors bid against a
    fixed revision. A later edit would move the goalposts under bids already
    invited against it."""
    store = make_store()
    rfq = seed_rfq(store)
    store.set_technical_package(
        rfq.id, revision="Rev. B", basis_of_design="basis",
        attachments=[Attachment(doc_code="HAL-PID-001", title="P&ID", revision="Rev. C")],
    )
    store.freeze_package(rfq.id, by="lead.engineer@example.com")

    with pytest.raises(ValueError, match="frozen"):
        store.set_technical_package(
            rfq.id, revision="Rev. C", basis_of_design="changed", attachments=[],
        )

    assert store.get_technical_package(rfq.id).revision == "Rev. B"


def test_an_unfrozen_package_can_still_be_edited():
    store = make_store()
    rfq = seed_rfq(store)
    store.set_technical_package(rfq.id, revision="Rev. A", basis_of_design="first",
                                attachments=[])
    store.set_technical_package(rfq.id, revision="Rev. B", basis_of_design="second",
                                attachments=[])
    assert store.get_technical_package(rfq.id).revision == "Rev. B"


def test_freezing_twice_is_refused():
    store = make_store()
    rfq = seed_rfq(store)
    store.set_technical_package(rfq.id, revision="Rev. B", basis_of_design="basis",
                                attachments=[])
    store.freeze_package(rfq.id, by="lead.engineer@example.com")
    with pytest.raises(ValueError, match="already frozen"):
        store.freeze_package(rfq.id, by="someone.else@example.com")


# -- deletion, and its two referential guards --------------------------------
#
# Both guards are reads that gate a write, so they live here in the store —
# the route runs the whole method inside `persistence.locked_update`. A check
# made in the caller and a write made here would be two critical sections, and
# two concurrent deletes could each read "safe".


def test_deleting_an_item_covered_by_an_rfq_is_refused_and_names_the_rfq():
    store = make_store()
    p = make_project(store)
    item = make_item(store, p.id)
    make_rfq(store, p.id, [item.id])

    with pytest.raises(ValueError) as exc:
        store.delete_item(item.id)

    # The reason must name what blocks it. A bare refusal leaves the user with
    # nothing to act on — the same rule the stage gates already follow.
    assert "ADP-RFQ-2026-014" in str(exc.value)
    assert store.get_item(item.id) is not None


def test_deleting_an_item_no_rfq_covers_removes_it():
    store = make_store()
    p = make_project(store)
    item = make_item(store, p.id)

    store.delete_item(item.id)

    assert store.get_item(item.id) is None
    assert store.items_for_project(p.id) == []


def test_deleting_an_unknown_item_raises():
    with pytest.raises(KeyError):
        make_store().delete_item("itm_missing")


def test_deleting_a_project_removes_its_items_in_the_same_call():
    """The cascade is the whole point: `save` replaces the document wholesale,
    so an item left behind here is an item pointing at a project that is gone —
    and it persists."""
    store = make_store()
    p = make_project(store)
    generator = make_item(store, p.id, "Generator")
    cable = make_item(store, p.id, "Cable")

    store.delete_project(p.id)

    assert store.get_project(p.id) is None
    assert store.get_item(generator.id) is None
    assert store.get_item(cable.id) is None


def test_deleting_a_project_leaves_another_projects_items_alone():
    store = make_store()
    doomed = make_project(store)
    kept = make_project(store)
    doomed_item = make_item(store, doomed.id)
    kept_item = make_item(store, kept.id)

    store.delete_project(doomed.id)

    assert store.get_item(doomed_item.id) is None
    assert store.get_item(kept_item.id) is not None
    assert store.get_project(kept.id) is not None


def test_deleting_a_project_holding_an_rfq_is_refused_and_names_it():
    store = make_store()
    p = make_project(store)
    item = make_item(store, p.id)
    make_rfq(store, p.id, [item.id])

    with pytest.raises(ValueError) as exc:
        store.delete_project(p.id)

    assert "ADP-RFQ-2026-014" in str(exc.value)
    # Nothing half-deleted: the guard raises before anything is removed.
    assert store.get_project(p.id) is not None
    assert store.get_item(item.id) is not None


def test_deleting_an_unknown_project_raises():
    with pytest.raises(KeyError):
        make_store().delete_project("prj_missing")
