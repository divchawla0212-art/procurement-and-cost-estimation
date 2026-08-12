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
