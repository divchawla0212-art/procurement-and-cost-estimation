"""`<ROOT>/workflow.json` — durability for the RFQ workflow.

Phase 1 kept the workflow in memory, so an RFQ raised before a restart was
gone after it. This is the same shape as `auth.json`: one document, one lock,
one atomic write, so projects, items, RFQs and every artifact stay consistent
with each other.

The invariant these tests defend: **the document holds exactly the entities the
store holds** — a round trip neither drops one nor invents one, and that
includes the append-only stage history, which is the audit trail of a retender.
"""
import json
from datetime import date

import pytest

from workflow import persistence
from workflow.models.rfq import Attachment
from workflow.stages import Stage
from workflow.store import WorkflowStore


def populated_store() -> tuple[WorkflowStore, str]:
    store = WorkflowStore()
    project = store.create_project(
        name="Haliba Field Development",
        code="HAL",
        client="Al Dhafra Petroleum",
        location="Haliba field, UAE",
        live_period_start=date(2026, 1, 1),
        live_period_end=date(2029, 12, 31),
    )
    item = store.create_item(
        project_id=project.id,
        item_type="Wellhead tie-in materials",
        description="Wellhead & CGF tie-in materials",
        qty=1,
        uom="lot",
        discipline="Mechanical / piping",
        estimated_value_aed=46_200_000,
        required_on_site=date(2027, 6, 1),
    )
    rfq = store.create_rfq(
        project_id=project.id,
        item_ids=[item.id],
        reference="ADP-RFQ-2026-014",
        package="Wellhead & CGF tie-in materials",
        discipline="Mechanical / piping",
        value_estimate_aed=46_200_000,
    )
    store.set_technical_package(
        rfq.id,
        revision="Rev. B",
        basis_of_design="129 wellhead tie-ins",
        attachments=[Attachment(doc_code="HAL-PID-001", title="P&ID", revision="Rev. C")],
    )
    store.freeze_package(rfq.id, by="lead.engineer@example.com")
    store.add_shortlist_entry(rfq.id, vendor_name="Galfar", prequal_status="Qualified",
                              scope_code_fit=True, included=True)
    store.approve_shortlist(rfq.id, by="procurement@example.com")
    store.set_tbe_template(rfq.id, criteria=["Throughput"], source_rfq_reference="ADP-RFQ-2025-008")
    store.add_vdrl_line(rfq.id, doc_code="GA-001", title="GA", doc_type="Doc", mandatory=True)
    bid = store.register_bid(rfq.id, vendor_name="Petrofac", headline_price_aed=51_400_000)
    store.record_vdrl_receipt(bid.id, doc_code="GA-001", state="unreadable")
    store.select_bids(rfq.id, [bid.id], by="client@example.com", rationale="Only compliant bid")
    return store, rfq.id


def test_an_empty_root_loads_an_empty_store(tmp_path):
    store = persistence.load(str(tmp_path))
    assert store.list_projects() == []
    assert store.list_rfqs() == []


def test_a_round_trip_preserves_every_entity(tmp_path):
    store, rfq_id = populated_store()
    persistence.save(str(tmp_path), store)

    loaded = persistence.load(str(tmp_path))

    assert len(loaded.list_projects()) == 1
    project = loaded.list_projects()[0]
    assert project.name == "Haliba Field Development"
    assert project.live_period_start == date(2026, 1, 1)
    assert len(loaded.items_for_project(project.id)) == 1

    rfq = loaded.get_rfq(rfq_id)
    assert rfq is not None
    assert rfq.reference == "ADP-RFQ-2026-014"
    assert rfq.stage is Stage.SCOPING

    package = loaded.get_technical_package(rfq_id)
    assert package.frozen_by == "lead.engineer@example.com"
    assert package.attachments[0].revision == "Rev. C"

    assert [e.vendor_name for e in loaded.shortlist_for(rfq_id)] == ["Galfar"]
    assert loaded.is_shortlist_approved(rfq_id) is True
    assert loaded.get_tbe_template(rfq_id).source_rfq_reference == "ADP-RFQ-2025-008"
    assert [line.doc_code for line in loaded.vdrl_for(rfq_id)] == ["GA-001"]

    bids = loaded.bids_for(rfq_id)
    assert [b.vendor_name for b in bids] == ["Petrofac"]
    assert [r.state for r in loaded.receipts_for(bids[0].id)] == ["unreadable"]
    assert loaded.get_bid_shortlist(rfq_id).rationale == "Only compliant bid"


def test_a_round_trip_preserves_the_whole_stage_history(tmp_path):
    """A retender walks the same edge twice. Losing either entry in the round
    trip would erase the record of the first attempt (spec C5-R4)."""
    store, rfq_id = populated_store()
    for target in [Stage.SHORTLISTING, Stage.ISSUED, Stage.CLARIFICATIONS,
                   Stage.BIDS_RECEIVED, Stage.EVALUATION]:
        store.transition(rfq_id, target, by="amal@example.com")
    store.transition(rfq_id, Stage.ISSUED, by="amal@example.com", reason="retender")
    persistence.save(str(tmp_path), store)

    history = persistence.load(str(tmp_path)).get_rfq(rfq_id).history
    assert len(history) == 7
    assert [h.to_stage for h in history if h.to_stage is Stage.ISSUED] == [
        Stage.ISSUED,
        Stage.ISSUED,
    ]
    assert history[-1].reason == "retender"
    assert history[0].from_stage is None


def test_a_reloaded_store_still_enforces_its_gates(tmp_path):
    """Durability must not smuggle a bypass in: the gate reads stored state, so
    a store rebuilt from disk has to refuse exactly what the live one did."""
    store = WorkflowStore()
    project = store.create_project(
        name="P", code="P", client="C", location="L",
        live_period_start=date(2026, 1, 1), live_period_end=date(2029, 12, 31),
    )
    item = store.create_item(project_id=project.id, item_type="T", description="d",
                             qty=1, uom="ea", discipline="E", estimated_value_aed=1)
    rfq = store.create_rfq(project_id=project.id, item_ids=[item.id], reference="R",
                           package="P", discipline="E", value_estimate_aed=1)
    persistence.save(str(tmp_path), store)

    loaded = persistence.load(str(tmp_path))
    with pytest.raises(ValueError, match="frozen"):
        loaded.transition(rfq.id, Stage.SHORTLISTING, by="amal@example.com")


def test_saving_replaces_rather_than_accumulates(tmp_path):
    """The document holds exactly what the store holds — a second save of a
    smaller store must not leave the first save's extra RFQ behind."""
    store, _ = populated_store()
    persistence.save(str(tmp_path), store)
    persistence.save(str(tmp_path), WorkflowStore())

    reloaded = persistence.load(str(tmp_path))
    assert reloaded.list_rfqs() == []
    assert reloaded.list_projects() == []


def test_locked_update_persists_on_a_clean_exit(tmp_path):
    with persistence.locked_update(str(tmp_path)) as store:
        store.create_project(
            name="Haliba", code="HAL", client="ADP", location="UAE",
            live_period_start=date(2026, 1, 1), live_period_end=date(2029, 12, 31),
        )

    assert len(persistence.load(str(tmp_path)).list_projects()) == 1


def test_locked_update_writes_nothing_when_the_block_raises(tmp_path):
    """A caller that raises leaves the document exactly as it was — the same
    rule `auth.store.locked_update` follows."""
    with persistence.locked_update(str(tmp_path)) as store:
        store.create_project(
            name="Keep me", code="K", client="C", location="L",
            live_period_start=date(2026, 1, 1), live_period_end=date(2029, 12, 31),
        )

    with pytest.raises(RuntimeError):
        with persistence.locked_update(str(tmp_path)) as store:
            store.create_project(
                name="Discard me", code="D", client="C", location="L",
                live_period_start=date(2026, 1, 1), live_period_end=date(2029, 12, 31),
            )
            raise RuntimeError("boom")

    names = [p.name for p in persistence.load(str(tmp_path)).list_projects()]
    assert names == ["Keep me"]


def test_a_missing_document_is_not_an_error_but_a_corrupt_one_is(tmp_path):
    """Absence is a first run. A truncated file is a real failure and must not
    be silently read as "no RFQs" — that would look identical to data loss."""
    assert persistence.load(str(tmp_path)).list_rfqs() == []

    (tmp_path / "workflow.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        persistence.load(str(tmp_path))


def test_the_document_is_human_readable_json(tmp_path):
    """It sits next to auth.json and gets hand-inspected for the same reasons."""
    store, _ = populated_store()
    persistence.save(str(tmp_path), store)

    doc = json.loads((tmp_path / "workflow.json").read_text(encoding="utf-8"))
    assert doc["version"] == 1
    assert {"projects", "items", "rfqs", "packages", "shortlists",
            "shortlist_approvals", "tbe", "vdrl", "bids", "receipts",
            "bid_shortlists"} <= set(doc)
    assert doc["rfqs"][0]["reference"] == "ADP-RFQ-2026-014"
