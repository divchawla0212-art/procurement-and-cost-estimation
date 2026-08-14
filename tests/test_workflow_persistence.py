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
from pathlib import Path

import pytest

from workflow import bidder_db, clarifications, persistence
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


def test_artifact_ids_survive_a_round_trip(tmp_path):
    """Removal addresses shortlist entries and VDRL lines by id. If the id were
    regenerated on load, every "remove this one" would target a different row
    after a restart — the silent-failure mode a new field on the store has."""
    store, rfq_id = populated_store()
    before_shortlist = [e.id for e in store.shortlist_for(rfq_id)]
    before_vdrl = [line.id for line in store.vdrl_for(rfq_id)]
    assert before_shortlist and before_vdrl

    persistence.save(str(tmp_path), store)
    loaded = persistence.load(str(tmp_path))

    assert [e.id for e in loaded.shortlist_for(rfq_id)] == before_shortlist
    assert [line.id for line in loaded.vdrl_for(rfq_id)] == before_vdrl


def test_removing_an_artifact_does_not_survive_on_disk(tmp_path):
    """`save` replaces the document wholesale, so a removed entry cannot come
    back after a restart."""
    store, rfq_id = populated_store()
    entry = store.shortlist_for(rfq_id)[0]
    persistence.save(str(tmp_path), store)

    store.remove_shortlist_entry(rfq_id, entry.id)
    persistence.save(str(tmp_path), store)

    assert persistence.load(str(tmp_path)).shortlist_for(rfq_id) == []


# -- the bidder registry -----------------------------------------------------
#
# CLAUDE.md names this file's exact failure mode: a field added to
# `WorkflowStore.__init__` without a matching line in *both* `to_document` and
# `from_document` silently fails to survive a restart. `_bidders` is such a
# field, so it is asserted directly rather than only through a screen.


def a_registered_bidder(store: WorkflowStore, **overrides):
    defaults = dict(
        name="Al Munara Switchgear LLC",
        country="United Arab Emirates",
        trade_categories=["Electrical", "Power generation"],
        prequal_status="Approved",
        prequal_expires_on=date(2027, 3, 31),
        turnover_band="AED 50–100m",
        performance_rating=4.2,
        past_awards=6,
    )
    return store.create_bidder(**{**defaults, **overrides})


def test_a_bidder_survives_a_round_trip_with_every_field(tmp_path):
    store, _ = populated_store()
    bidder = a_registered_bidder(store, notes="Preferred for LV frames")
    persistence.save(str(tmp_path), store)

    loaded = persistence.load(str(tmp_path)).get_bidder(bidder.id)
    assert loaded == bidder


def test_the_registry_is_in_the_database_and_not_in_the_document(tmp_path):
    """The registry moved to `bidders.db`. A `bidders` key in the document
    would be a second copy for the first edit to disagree with."""
    store, _ = populated_store()
    a_registered_bidder(store)
    persistence.save(str(tmp_path), store)

    doc = json.loads((tmp_path / "workflow.json").read_text(encoding="utf-8"))
    assert "bidders" not in doc
    assert [b.name for b in bidder_db.list_all(str(tmp_path))] == [
        "Al Munara Switchgear LLC"
    ]


def test_no_derived_value_is_written_to_the_database(tmp_path):
    """`effective_prequal` is computed from the stored expiry and the day it is
    asked on. A column for it would be wrong the next morning."""
    store, _ = populated_store()
    a_registered_bidder(store)
    persistence.save(str(tmp_path), store)

    with bidder_db.connect(str(tmp_path)) as conn:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(bidders)")}
    assert "effective_prequal" not in columns
    assert "approval_caution" not in columns
    assert "invited_count" not in columns


def test_an_empty_database_and_no_key_load_as_an_empty_registry(tmp_path):
    """Both halves absent is a first run, not an error — the same reading a
    missing document gets."""
    store, _ = populated_store()
    persistence.save(str(tmp_path), store)

    loaded = persistence.load(str(tmp_path))
    assert loaded.list_bidders() == []
    assert loaded.list_projects()  # everything else still loaded


def test_a_document_still_carrying_a_bidders_key_keeps_its_registry(tmp_path):
    """The one-way migration. A document written before the move loads with its
    registry intact, and the next save puts it in the database and drops the
    key — so an old store is never read as an emptied one."""
    store, _ = populated_store()
    a_registered_bidder(store)
    path = tmp_path / "workflow.json"
    # A pre-move document: the registry inside the JSON, no database beside it.
    legacy = persistence.to_document(store)
    legacy["bidders"] = [b.model_dump(mode="json") for b in store.list_bidders()]
    path.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = persistence.load(str(tmp_path))
    assert [b.name for b in loaded.list_bidders()] == ["Al Munara Switchgear LLC"]

    persistence.save(str(tmp_path), loaded)
    assert "bidders" not in json.loads(path.read_text(encoding="utf-8"))
    assert bidder_db.count(str(tmp_path)) == 1


def test_a_shortlist_link_survives_a_round_trip(tmp_path):
    store, rfq_id = populated_store()
    bidder = a_registered_bidder(store)
    store.add_shortlist_entry(rfq_id, vendor_id=bidder.id, as_of=date(2026, 8, 13))
    persistence.save(str(tmp_path), store)

    loaded = persistence.load(str(tmp_path))
    linked = [e for e in loaded.shortlist_for(rfq_id) if e.vendor_id]
    assert [e.vendor_id for e in linked] == [bidder.id]
    # The reference still resolves after the restart, which is what makes the
    # delete guard mean anything.
    assert loaded.rfqs_inviting(bidder.id) == ["ADP-RFQ-2026-014"]


def test_no_shortlist_entry_stores_the_approvals_it_reports(tmp_path):
    """The invariant the approval pills own. `approved_by` is served on every
    shortlist row and stored on none of them — a written copy is wrong the
    moment the registry is corrected, and correcting it is the common case.

    Same shape as `test_no_derived_value_is_written_to_the_database` above, and
    the same reason: this is the assertion that fails if anyone ever
    "optimises" the derived value by writing it down.
    """
    store, rfq_id = populated_store()
    bidder = a_registered_bidder(store, approved_by=["ADNOC", "Astra"])
    store.add_shortlist_entry(rfq_id, vendor_id=bidder.id, as_of=date(2026, 8, 13))
    persistence.save(str(tmp_path), store)

    doc = json.loads((tmp_path / "workflow.json").read_text(encoding="utf-8"))
    assert doc["shortlists"], "the fixture must actually write a shortlist entry"
    for entry in doc["shortlists"]:
        assert "approved_by" not in entry
        assert "client_approved" not in entry


# -- the per-item vendor lists -----------------------------------------------
#
# A new collection on `WorkflowStore.__init__`, so this file's named failure
# mode applies directly: no matching line in *both* `to_document` and
# `from_document` and it silently fails to survive a restart. Both directions
# are asserted here rather than only through a screen.


def an_entry(item_id: str, source: str, name: str):
    from datetime import datetime

    from workflow.models.project import ItemVendorEntry

    return ItemVendorEntry(
        item_id=item_id,
        source=source,
        vendor_id=f"bdr_{name.lower()}",
        vendor_name=name,
        trade_categories=["CABLES - LV POWER DISTRIBUTION"],
        uploaded_by="buyer@example.com",
        uploaded_at=datetime(2026, 8, 14, 9, 0),
        source_document="avl.xlsx",
    )


def _uncovered_item(store: WorkflowStore):
    """An item no RFQ covers, so `delete_item` is allowed to run."""
    project = next(iter(store._projects.values()))
    return store.create_item(
        project_id=project.id,
        item_type="HV cable",
        description="11 kV, 3-core",
        qty=1200,
        uom="m",
        discipline="Cables",
        estimated_value_aed=900_000,
    )


def test_setting_one_source_leaves_the_other_alone(tmp_path):
    """A second upload is a correction, so it replaces its own source
    wholesale — and only its own. Appending would leave a vendor dropped from
    the revised export indistinguishable from one still on it."""
    store, _ = populated_store()
    item = _uncovered_item(store)
    store.set_item_vendor_list(item.id, "Client", [an_entry(item.id, "Client", "A")])
    store.set_item_vendor_list(item.id, "Astra", [an_entry(item.id, "Astra", "B")])
    store.set_item_vendor_list(item.id, "Client", [an_entry(item.id, "Client", "C")])

    assert [e.vendor_name for e in store.item_vendor_list(item.id, "Client")] == ["C"]
    assert [e.vendor_name for e in store.item_vendor_list(item.id, "Astra")] == ["B"]


def test_a_vendor_list_survives_a_round_trip(tmp_path):
    store, _ = populated_store()
    item = _uncovered_item(store)
    store.set_item_vendor_list(item.id, "Client", [an_entry(item.id, "Client", "A")])
    persistence.save(str(tmp_path), store)

    loaded = persistence.load(str(tmp_path))

    entries = loaded.item_vendor_list(item.id, "Client")
    assert [e.vendor_name for e in entries] == ["A"]
    assert entries[0].trade_categories == ["CABLES - LV POWER DISTRIBUTION"]
    assert entries[0].uploaded_by == "buyer@example.com"


def test_deleting_an_item_takes_its_vendor_lists(tmp_path):
    """Read from disk on run 2, never from the store that made the change: a
    single-run assertion passes while the document is already wrong."""
    store, _ = populated_store()
    item = _uncovered_item(store)
    store.set_item_vendor_list(item.id, "Client", [an_entry(item.id, "Client", "A")])
    persistence.save(str(tmp_path), store)

    store.delete_item(item.id)
    persistence.save(str(tmp_path), store)

    doc = json.loads((tmp_path / "workflow.json").read_text(encoding="utf-8"))
    assert [e for e in doc["item_vendor_lists"] if e["item_id"] == item.id] == []


def test_deleting_an_item_takes_its_hand_added_vendors_too(tmp_path):
    """The cascade is per item, not per source.

    `delete_item` pops the whole `_item_vendor_lists` entry, so a source added
    later must not need the cascade extending to cover it. Written the moment
    `Manual` and `Suggested` arrived, because the failure it guards is silent:
    an entry pointing at an item that is gone, surviving the restart.
    """
    store, _ = populated_store()
    item = _uncovered_item(store)
    store.set_item_vendor_list(item.id, "Client", [an_entry(item.id, "Client", "A")])
    store.add_item_vendor_entry(item.id, an_entry(item.id, "Manual", "By hand"))
    store.add_item_vendor_entry(item.id, an_entry(item.id, "Suggested", "By model"))
    persistence.save(str(tmp_path), store)

    store.delete_item(item.id)
    persistence.save(str(tmp_path), store)

    doc = json.loads((tmp_path / "workflow.json").read_text(encoding="utf-8"))
    assert [e for e in doc["item_vendor_lists"] if e["item_id"] == item.id] == []


def test_a_curated_entry_survives_a_round_trip(tmp_path):
    """`source` is what decides which operations are legal on a row, so it has
    to come back off disk as it went on. A `Manual` row reloading as `Client`
    would make itself unremovable."""
    store, _ = populated_store()
    item = _uncovered_item(store)
    store.add_item_vendor_entry(item.id, an_entry(item.id, "Suggested", "Model Co"))
    persistence.save(str(tmp_path), store)

    loaded = persistence.load(str(tmp_path))

    entries = loaded.item_vendor_list(item.id, "Suggested")
    assert [e.vendor_name for e in entries] == ["Model Co"]
    assert entries[0].source == "Suggested"


def test_deleting_a_project_takes_every_items_vendor_lists(tmp_path):
    """The cascade reaches through the item cascade. An entry left behind is an
    entry pointing at an item that is gone, and it survives the restart."""
    store = WorkflowStore()
    project = store.create_project(
        name="Ruwais", code="RUU", client="ADNOC", location="Ruwais",
        live_period_start=date(2026, 1, 1), live_period_end=date(2029, 12, 31),
    )
    item = store.create_item(
        project_id=project.id, item_type="HV cable", description="11 kV",
        qty=1, uom="m", discipline="Cables", estimated_value_aed=1,
    )
    store.set_item_vendor_list(item.id, "Client", [an_entry(item.id, "Client", "A")])
    persistence.save(str(tmp_path), store)

    store.delete_project(project.id)
    persistence.save(str(tmp_path), store)

    doc = json.loads((tmp_path / "workflow.json").read_text(encoding="utf-8"))
    assert doc["item_vendor_lists"] == []


def test_a_document_written_before_vendor_lists_loads_as_none(tmp_path):
    """A missing key read correctly, not a migration — which is why `VERSION`
    does not move."""
    store, _ = populated_store()
    persistence.save(str(tmp_path), store)
    path = tmp_path / "workflow.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    del doc["item_vendor_lists"]
    path.write_text(json.dumps(doc), encoding="utf-8")

    loaded = persistence.load(str(tmp_path))

    assert loaded.item_vendor_list(next(iter(loaded._items))) == []


# -- clarifications ----------------------------------------------------------
#
# Two more fields on `WorkflowStore.__init__`, and CLAUDE.md names this file's
# exact failure mode for such a field: no matching line in *both* `to_document`
# and `from_document` and it silently fails to survive a restart. Both
# directions are asserted directly rather than only through a screen.


def rfq_with_a_query(root: str):
    """Run 1 for the rows below: a saved store holding one frozen RFQ, one
    invited bidder and one answered query."""
    with persistence.locked_update(root) as store:
        project, generator, _cable = project_and_items(store)
        rfq = cover_with_rfq(store, project.id, [generator.id])
        store.set_technical_package(
            rfq.id, revision="Rev. A", basis_of_design="2 x 5 MW",
            attachments=[Attachment(doc_code="GEN-DS-01", title="Datasheet",
                                    revision="Rev. A")],
        )
        store.freeze_package(rfq.id, by="lead@example.com")
        entry = store.add_shortlist_entry(
            rfq.id, vendor_name="Al Munara Switchgear LLC", prequal_status="Approved",
            scope_code_fit=True, included=True,
        )
        query = store.raise_query(
            rfq.id, entry.id, question="Confirm the frame size.",
            category="Technical", raised_on=date(2026, 8, 13),
        )
        store.answer_query(rfq.id, query.id, answer="Frame 6.", by="buyer@example.com")
    return rfq.id, entry.id, query.id


def test_a_query_survives_a_round_trip_with_every_field(tmp_path):
    """Compared against the in-memory record, not against a second load — two
    loads of the same broken document agree with each other."""
    store, rfq_id = populated_store()
    entry = store.shortlist_for(rfq_id)[0]
    query = store.raise_query(rfq_id, entry.id, question="Confirm the frame size.",
                              category="Technical", raised_on=date(2026, 8, 13))
    store.answer_query(rfq_id, query.id, answer="Frame 6.", by="buyer@example.com")
    before = store.queries_for(rfq_id)
    persistence.save(str(tmp_path), store)

    assert persistence.load(str(tmp_path)).queries_for(rfq_id) == before
    assert [q.number for q in before] == ["TQ-001"]


def test_an_addendum_survives_a_round_trip_with_every_field(tmp_path):
    root = str(tmp_path)
    rfq_id, _entry_id, _query_id = rfq_with_a_query(root)
    with persistence.locked_update(root) as store:
        draft = store.draft_addendum(
            rfq_id, revision="Rev. B", summary="Frame size corrected.",
            attachments=[Attachment(doc_code="GEN-DS-01", title="Datasheet",
                                    revision="Rev. B")],
            bid_due_date=date(2026, 10, 15),
        )
        store.issue_addendum(rfq_id, draft.id, by="buyer@example.com")

    reloaded = persistence.load(root)
    [addendum] = reloaded.addenda_for(rfq_id)
    assert addendum.number == "ADD-01"
    assert addendum.supersedes_revision == "Rev. A"
    assert addendum.issued_by == "buyer@example.com"
    assert reloaded.current_bid_due_date(rfq_id) == date(2026, 10, 15)
    # The superseded package went with it, so the reloaded store agrees.
    assert reloaded.get_technical_package(rfq_id).revision == "Rev. B"


def test_the_document_carries_both_new_collections(tmp_path):
    root = str(tmp_path)
    rfq_with_a_query(root)
    doc = json.loads(Path(persistence.workflow_path(root)).read_text(encoding="utf-8"))
    assert {"queries", "addenda"} <= set(doc)
    assert doc["queries"][0]["number"] == "TQ-001"
    # Derived, never stored — the load has nothing to keep honest.
    assert "status" not in doc["queries"][0]


def test_a_document_written_before_clarifications_loads_as_an_empty_one(tmp_path):
    """No version bump and no migration: a document with no `queries` key means
    no queries, which is the correct reading of it."""
    root = str(tmp_path)
    rfq_id, _entry_id, _query_id = rfq_with_a_query(root)
    path = Path(persistence.workflow_path(root))
    doc = json.loads(path.read_text(encoding="utf-8"))
    del doc["queries"]
    del doc["addenda"]
    path.write_text(json.dumps(doc), encoding="utf-8")

    loaded = persistence.load(root)
    assert loaded.queries_for(rfq_id) == []
    assert loaded.addenda_for(rfq_id) == []
    assert loaded.list_projects()          # everything else still loaded
    # No migration, so the version never moved to accommodate the new keys.
    assert doc["version"] == 1


def test_clarification_ids_survive_a_round_trip(tmp_path):
    """Every write addresses by id. A regenerated id would make "answer this
    one" hit a different row after a restart — this file's silent failure."""
    root = str(tmp_path)
    rfq_id, _entry_id, query_id = rfq_with_a_query(root)
    with persistence.locked_update(root) as store:
        draft = store.draft_addendum(rfq_id, revision="Rev. B", summary="s",
                                     attachments=[])

    reloaded = persistence.load(root)
    assert [q.id for q in reloaded.queries_for(rfq_id)] == [query_id]
    assert [a.id for a in reloaded.addenda_for(rfq_id)] == [draft.id]


# -- the two-run mutation matrix ---------------------------------------------
#
# Every row below mutates the store between two runs and then reads run 2 *from
# disk*, never from the store object that made the change. That is the whole
# point: a single-run assertion passes while the document is already wrong.
#
# PLAN-TEMPLATE.md's nine required rows all mutate the extraction pipeline — a
# newer document revision, a prompt-version bump, an LLM call failing between
# runs. None of that exists in `workflow.json`. What carries across is their
# shape: each catches state that should have left the store and didn't. These
# rows reproduce that shape against the entities this subsystem mutates.
#
# | mutation between run 1 and run 2      | invariant at risk                          |
# |---------------------------------------|--------------------------------------------|
# | a project holding items is deleted     | _items holds exactly the items of live      |
# |                                        | projects                                    |
# | ...with a second project also holding  | the cascade filters by project_id           |
# | an item covered by an RFQ is deleted   | a refused call writes nothing               |
# | a project holding an RFQ is deleted    | a refused call writes nothing               |
# | a project is patched                   | one record per id, replaced in place        |
# | an item is dated outside the period    | the warning is advisory, not a write barrier|
# | an item is deleted, another is added   | every item_id in every RFQ resolves         |
# | create -> patch -> delete              | the document equals the store               |


def project_and_items(store: WorkflowStore, name: str = "Haliba"):
    project = store.create_project(
        name=name,
        code=name[:3].upper(),
        client="Al Dhafra Petroleum",
        location="Haliba field, UAE",
        live_period_start=date(2026, 1, 1),
        live_period_end=date(2029, 12, 31),
    )
    generator = store.create_item(
        project_id=project.id,
        item_type="Gas generator",
        description="2 x 5 MW containerised",
        qty=2,
        uom="no",
        discipline="Electrical",
        estimated_value_aed=18_000_000,
    )
    cable = store.create_item(
        project_id=project.id,
        item_type="HV cable",
        description="11 kV, 3-core",
        qty=1200,
        uom="m",
        discipline="Electrical",
        estimated_value_aed=900_000,
    )
    return project, generator, cable


def cover_with_rfq(store: WorkflowStore, project_id: str, item_ids: list[str]):
    return store.create_rfq(
        project_id=project_id,
        item_ids=item_ids,
        reference="ADP-RFQ-2026-014",
        package="Power generation",
        discipline="Electrical",
        value_estimate_aed=18_000_000,
    )


def test_deleting_a_project_does_not_leave_its_items_on_disk(tmp_path):
    """Row 1. An item pruned only in memory reappears on the next load — this
    is the orphaned-facts defect in its new home."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        project, generator, cable = project_and_items(store)

    with persistence.locked_update(root) as store:
        store.delete_project(project.id)

    reloaded = persistence.load(root)          # run 2: from disk
    assert reloaded.get_project(project.id) is None
    assert reloaded.get_item(generator.id) is None
    assert reloaded.get_item(cable.id) is None
    assert persistence.to_document(reloaded)["items"] == []


def test_deleting_one_project_leaves_the_others_items_on_disk(tmp_path):
    """Row 2. A cascade that forgot to filter by project_id would empty both."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        doomed, doomed_gen, doomed_cable = project_and_items(store, "Haliba")
        kept, kept_gen, kept_cable = project_and_items(store, "Bab")

    with persistence.locked_update(root) as store:
        store.delete_project(doomed.id)

    reloaded = persistence.load(root)
    assert reloaded.get_item(doomed_gen.id) is None
    assert reloaded.get_item(doomed_cable.id) is None
    assert reloaded.get_item(kept_gen.id) is not None
    assert reloaded.get_item(kept_cable.id) is not None
    assert len(reloaded.items_for_project(kept.id)) == 2


def test_a_refused_item_delete_leaves_the_document_untouched(tmp_path):
    """Row 3. `locked_update` writes only on a clean exit, so the refusal must
    not have touched the file at all — not "written the same content back"."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        project, generator, _cable = project_and_items(store)
        cover_with_rfq(store, project.id, [generator.id])

    before = Path(persistence.workflow_path(root)).read_bytes()

    with pytest.raises(ValueError):
        with persistence.locked_update(root) as store:
            store.delete_item(generator.id)

    assert Path(persistence.workflow_path(root)).read_bytes() == before
    assert persistence.load(root).get_item(generator.id) is not None


def test_a_refused_project_delete_leaves_the_document_untouched(tmp_path):
    """Row 4. Nothing half-lands: the guard raises before the cascade runs, and
    the block never reaches `save`."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        project, generator, cable = project_and_items(store)
        cover_with_rfq(store, project.id, [generator.id])

    before = Path(persistence.workflow_path(root)).read_bytes()

    with pytest.raises(ValueError):
        with persistence.locked_update(root) as store:
            store.delete_project(project.id)

    assert Path(persistence.workflow_path(root)).read_bytes() == before
    reloaded = persistence.load(root)
    assert reloaded.get_project(project.id) is not None
    assert reloaded.get_item(generator.id) is not None
    assert reloaded.get_item(cable.id) is not None
    assert len(reloaded.list_rfqs()) == 1


def test_a_patched_project_survives_as_exactly_one_record(tmp_path):
    """Row 5. An update that inserted under a fresh id rather than replacing in
    place would leave two projects here, and the reader would see a duplicate
    that no screen can delete."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        project, _generator, _cable = project_and_items(store)

    with persistence.locked_update(root) as store:
        store.update_project(project.id, {"name": "Haliba Phase 2", "status": "On Hold"})

    reloaded = persistence.load(root)
    assert len(reloaded.list_projects()) == 1
    survivor = reloaded.get_project(project.id)
    assert survivor.name == "Haliba Phase 2"
    assert survivor.status == "On Hold"
    assert survivor.code == "HAL"          # never sent, so never changed
    assert len(reloaded.items_for_project(project.id)) == 2


def test_an_item_dated_outside_the_live_period_still_persists(tmp_path):
    """Row 6. The caution is advisory. A validator that raised instead would
    lose the edit, and the store would disagree with what the user was told."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        project, generator, _cable = project_and_items(store)

    with persistence.locked_update(root) as store:
        store.update_item(generator.id, {"required_on_site": date(2030, 6, 1)})
        # The same call the route makes to build its warning.
        warning = store.validate_against_live_period(project.id, date(2030, 6, 1))
        assert warning is not None

    assert persistence.load(root).get_item(generator.id).required_on_site == date(2030, 6, 1)


def test_a_deleted_item_leaves_no_dangling_reference_in_any_rfq(tmp_path):
    """Row 7. The RFQ covers the cable only, so deleting the generator is
    allowed — and afterwards no RFQ may name an item that is gone."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        project, generator, cable = project_and_items(store)
        cover_with_rfq(store, project.id, [cable.id])

    with persistence.locked_update(root) as store:
        store.delete_item(generator.id)
        added = store.create_item(
            project_id=project.id,
            item_type="Transformer",
            description="11/0.415 kV",
            qty=1,
            uom="no",
            discipline="Electrical",
            estimated_value_aed=2_400_000,
        )

    # And the covered item is still undeletable — without this the row only
    # exercises the easy half, and dropping the guard entirely would leave it
    # green while RFQs ended up naming items that no longer exist.
    with pytest.raises(ValueError):
        with persistence.locked_update(root) as store:
            store.delete_item(cable.id)

    reloaded = persistence.load(root)
    assert reloaded.get_item(generator.id) is None
    assert reloaded.get_item(added.id) is not None
    assert reloaded.get_item(cable.id) is not None
    live = {i.id for i in reloaded.items_for_project(project.id)}
    for rfq in reloaded.list_rfqs():
        assert set(rfq.item_ids) <= live, "an RFQ names an item that no longer exists"


def test_the_document_equals_the_store_after_create_patch_delete(tmp_path):
    """Row 8. The end-to-end shape: whatever sequence ran, what is on disk is
    what the store holds — no more, no fewer."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        project, generator, cable = project_and_items(store)

    with persistence.locked_update(root) as store:
        store.update_item(generator.id, {"qty": 3})
        store.delete_item(cable.id)

    on_disk = json.loads(Path(persistence.workflow_path(root)).read_text(encoding="utf-8"))
    assert persistence.to_document(persistence.load(root)) == on_disk
    assert [i["id"] for i in on_disk["items"]] == [generator.id]
    assert on_disk["items"][0]["qty"] == 3
    assert [p["id"] for p in on_disk["projects"]] == [project.id]


# -- the registry's rows of the mutation matrix -------------------------------
#
# The rows above cover the project -> item hierarchy. These cover the collection
# this change adds, in the same shape: mutate between two runs, then read run 2
# *from disk*, never from the store object that made the change.
#
# | mutation between run 1 and run 2       | invariant at risk                  |
# |----------------------------------------|------------------------------------|
# | a bidder is created, then deleted      | the document holds exactly the     |
# |                                        | store's bidders                    |
# | a shortlisted bidder is deleted        | no delete under a live reference   |
# | the reference is removed, then deleted | ...and the guard releases          |
# | an approval lapses between the runs    | "Expired" is never stored          |
# | a bidder is suspended after invitation | the entry's snapshot is of the     |
# |                                        | moment it was added                |
# | trade categories edited to not match   | ...including scope_code_fit        |
# | a linked invitation after approval     | approval cannot outlive an edit    |
# | a bidder is renamed                    | entries bind by id, not by name    |
# | two invitations of the same bidder     | the store does not silently dedupe |


def rfq_with_a_bidder(root: str):
    """Run 1 for the rows below: a saved store holding one RFQ, one bidder and
    one invitation linking them."""
    with persistence.locked_update(root) as store:
        project, generator, _cable = project_and_items(store)
        rfq = cover_with_rfq(store, project.id, [generator.id])
        bidder = a_registered_bidder(store)
        entry = store.add_shortlist_entry(
            rfq.id, vendor_id=bidder.id, as_of=date(2026, 8, 13)
        )
    return rfq.id, bidder.id, entry.id


def test_a_deleted_bidder_does_not_survive_on_disk(tmp_path):
    """Row 1. `save` replaces the document wholesale, so a bidder pruned in
    memory cannot reappear after a restart."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        bidder = a_registered_bidder(store)

    with persistence.locked_update(root) as store:
        store.delete_bidder(bidder.id)

    assert persistence.load(root).get_bidder(bidder.id) is None


def test_a_shortlisted_bidder_survives_a_refused_delete_intact(tmp_path):
    """Row 2. The refusal is a read that gates a write, so it happens inside
    the lock — and `locked_update` writes only on a clean exit, so the raise
    leaves the document exactly as it was."""
    root = str(tmp_path)
    rfq_id, bidder_id, entry_id = rfq_with_a_bidder(root)

    with pytest.raises(ValueError, match="ADP-RFQ-2026-014"):
        with persistence.locked_update(root) as store:
            store.delete_bidder(bidder_id)

    reloaded = persistence.load(root)
    assert reloaded.get_bidder(bidder_id) is not None
    assert [e.id for e in reloaded.shortlist_for(rfq_id)] == [entry_id]


def test_removing_the_invitation_releases_the_bidder_across_a_restart(tmp_path):
    """Row 3. The guard has to release as well as hold, and it has to agree
    with what is on disk — the reference it reads is the reloaded one."""
    root = str(tmp_path)
    rfq_id, bidder_id, entry_id = rfq_with_a_bidder(root)

    with persistence.locked_update(root) as store:
        store.remove_shortlist_entry(rfq_id, entry_id)
    with persistence.locked_update(root) as store:
        store.delete_bidder(bidder_id)

    reloaded = persistence.load(root)
    assert reloaded.get_bidder(bidder_id) is None
    assert reloaded.shortlist_for(rfq_id) == []


def _stored_bidder(root: str, bidder_id: str) -> dict:
    """The bidder's row as SQLite holds it, for the byte-for-byte comparisons.

    The parent row only — the child tables are asserted through `list_all`,
    which is what any reader actually gets back.
    """
    with bidder_db.connect(root) as conn:
        row = conn.execute(
            "SELECT * FROM bidders WHERE id = ?", (bidder_id,)
        ).fetchone()
        return dict(row)


def test_an_approval_lapsing_between_runs_changes_no_stored_byte(tmp_path):
    """Row 4. The whole reason `Expired` is derived: nothing has to be written
    for a lapse to take effect, and nothing is."""
    from workflow.bidders import effective_prequal

    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        bidder = a_registered_bidder(store, prequal_expires_on=date(2026, 9, 30))
    before = _stored_bidder(root, bidder.id)

    # Run 2 mutates something else entirely; the clock is what moved.
    with persistence.locked_update(root) as store:
        store.update_bidder(bidder.id, {"notes": "Chased the renewal"})

    reloaded = persistence.load(root).get_bidder(bidder.id)
    assert reloaded.prequal_status == "Approved"
    assert reloaded.prequal_expires_on == date(2026, 9, 30)
    assert effective_prequal(reloaded, date(2026, 9, 30)) == "Approved"
    assert effective_prequal(reloaded, date(2026, 10, 1)) == "Expired"
    # The stored record is untouched but for the one field that was edited.
    after = _stored_bidder(root, bidder.id)
    assert {**before, "notes": "Chased the renewal"} == after


def test_suspending_a_bidder_does_not_rewrite_an_invitation_already_recorded(tmp_path):
    """Row 5. The entry records what was true when the decision was made. A
    later suspension is a new fact, not a correction of the old one — but the
    *next* invitation is refused without an override."""
    root = str(tmp_path)
    rfq_id, bidder_id, _entry_id = rfq_with_a_bidder(root)

    with persistence.locked_update(root) as store:
        store.update_bidder(bidder_id, {"prequal_status": "Suspended"})

    reloaded = persistence.load(root)
    assert [e.prequal_status for e in reloaded.shortlist_for(rfq_id)] == ["Approved"]
    with pytest.raises(ValueError, match="suspended"):
        reloaded.add_shortlist_entry(
            rfq_id, vendor_id=bidder_id, as_of=date(2026, 8, 13)
        )


def test_narrowing_trade_categories_does_not_rewrite_a_recorded_scope_fit(tmp_path):
    """Row 6. Same rule as row 5, on the other snapshotted field."""
    root = str(tmp_path)
    rfq_id, bidder_id, _entry_id = rfq_with_a_bidder(root)

    with persistence.locked_update(root) as store:
        store.update_bidder(bidder_id, {"trade_categories": ["Marine"]})

    reloaded = persistence.load(root)
    assert [e.scope_code_fit for e in reloaded.shortlist_for(rfq_id)] == [True]
    # A fresh invitation records the mismatch — and is still allowed, because a
    # scope mismatch is a caution rather than a refusal.
    fresh = reloaded.add_shortlist_entry(
        rfq_id, vendor_id=bidder_id, as_of=date(2026, 8, 13)
    )
    assert fresh.scope_code_fit is False


def test_a_linked_invitation_after_approval_revokes_it_on_disk(tmp_path):
    """Row 7. Approval is of a specific set of vendors. Linking does not exempt
    an edit from that, and the revocation has to survive the restart —
    otherwise a reload would re-approve a shortlist nobody approved."""
    root = str(tmp_path)
    rfq_id, _bidder_id, _entry_id = rfq_with_a_bidder(root)
    with persistence.locked_update(root) as store:
        store.approve_shortlist(rfq_id, by="procurement@example.com")
    assert persistence.load(root).is_shortlist_approved(rfq_id)

    with persistence.locked_update(root) as store:
        second = store.create_bidder(
            name="Northwind Valve Works", country="Oman",
            trade_categories=["Electrical"], prequal_status="Approved",
        )
        store.add_shortlist_entry(rfq_id, vendor_id=second.id, as_of=date(2026, 8, 13))

    assert not persistence.load(root).is_shortlist_approved(rfq_id)


def test_renaming_a_bidder_leaves_the_invitation_resolving_by_id(tmp_path):
    """Row 8. A vendor name is user-supplied text and two companies can share
    one, so the link is by id — and the recorded name stays the one that was
    true when the invitation went out."""
    root = str(tmp_path)
    rfq_id, bidder_id, _entry_id = rfq_with_a_bidder(root)

    with persistence.locked_update(root) as store:
        store.update_bidder(bidder_id, {"name": "Al Munara Electrical Industries LLC"})

    reloaded = persistence.load(root)
    entry = reloaded.shortlist_for(rfq_id)[0]
    assert entry.vendor_id == bidder_id
    assert entry.vendor_name == "Al Munara Switchgear LLC"
    # The guard still finds it, which is what makes the link a real reference.
    assert reloaded.rfqs_inviting(bidder_id) == ["ADP-RFQ-2026-014"]


def test_two_invitations_of_one_bidder_both_survive(tmp_path):
    """Row 9. A duplicate is a user error the screen can show. Collapsing it
    here would make a removal ambiguous, and the document must hold exactly
    what the store holds — including the mistake."""
    root = str(tmp_path)
    rfq_id, bidder_id, first_id = rfq_with_a_bidder(root)

    with persistence.locked_update(root) as store:
        second = store.add_shortlist_entry(
            rfq_id, vendor_id=bidder_id, as_of=date(2026, 8, 13)
        )

    reloaded = persistence.load(root)
    assert [e.id for e in reloaded.shortlist_for(rfq_id)] == [first_id, second.id]
    # Removing one leaves the other still holding the bidder.
    with persistence.locked_update(root) as store:
        store.remove_shortlist_entry(rfq_id, first_id)
    with pytest.raises(ValueError):
        with persistence.locked_update(root) as store:
            store.delete_bidder(bidder_id)


# -- the clarification round rows of the mutation matrix -----------------------
#
# Same shape as the rows above: mutate between two runs, then read run 2 *from
# disk*, never from the store object that made the change.
#
# | mutation between run 1 and run 2        | invariant at risk                  |
# |-----------------------------------------|------------------------------------|
# | a query is raised, its bidder removed   | every raised_by_entry_id resolves  |
# | a query is withdrawn, a new one raised  | numbers are never reused           |
# | a restricted answer is re-answered      | circulation is one field, not two  |
# | an addendum is drafted, then issued     | the package equals the latest one   |
# | a stale second draft is issued          | the check runs inside the lock     |
# | a draft addendum is deleted             | the document equals the store      |
# | a transition with an open query         | no RFQ passes the gate with one    |
# | raise -> answer -> withdraw -> issue    | the document equals the store      |


def test_a_bidder_who_asked_survives_a_refused_removal_intact(tmp_path):
    """Row 1. The guard is a read that gates a write, so it happens inside the
    lock - and locked_update writes only on a clean exit, so the raise leaves
    the document exactly as it was."""
    root = str(tmp_path)
    rfq_id, entry_id, query_id = rfq_with_a_query(root)
    before = Path(persistence.workflow_path(root)).read_bytes()

    with pytest.raises(ValueError, match="TQ-001"):
        with persistence.locked_update(root) as store:
            store.remove_shortlist_entry(rfq_id, entry_id)

    assert Path(persistence.workflow_path(root)).read_bytes() == before
    reloaded = persistence.load(root)
    assert [e.id for e in reloaded.shortlist_for(rfq_id)] == [entry_id]
    live = {e.id for e in reloaded.shortlist_for(rfq_id)}
    assert all(q.raised_by_entry_id in live for q in reloaded.queries_for(rfq_id))
    assert [q.id for q in reloaded.queries_for(rfq_id)] == [query_id]


def test_a_number_already_on_disk_is_never_handed_out_again(tmp_path):
    """Row 2. Numbering reads what is on disk, and it has to read the *highest*
    rather than the count.

    The gap is the whole point. While the sequence is dense the two rules agree,
    so withdrawing TQ-001 and raising another proves nothing — both give
    TQ-002. `workflow.json` is documented as human-readable and gets
    hand-inspected, so a register loaded with TQ-001 and TQ-003 is reachable,
    and counting would answer TQ-003: a number a bidder already holds in
    writing.
    """
    root = str(tmp_path)
    rfq_id, entry_id, query_id = rfq_with_a_query(root)

    with persistence.locked_update(root) as store:
        store.withdraw_query(rfq_id, query_id, reason="Duplicate.", by="b@example.com")

    # The gap, introduced the way a real one would arrive: in the document.
    path = Path(persistence.workflow_path(root))
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["queries"][0]["number"] = "TQ-003"
    path.write_text(json.dumps(doc), encoding="utf-8")

    with persistence.locked_update(root) as store:
        raised = store.raise_query(rfq_id, entry_id, question="Second question.",
                                   category="Technical", raised_on=date(2026, 8, 14))

    numbers = [q.number for q in persistence.load(root).queries_for(rfq_id)]
    assert raised.number == "TQ-004"
    assert sorted(numbers) == ["TQ-003", "TQ-004"]
    assert len(numbers) == len(set(numbers))


def test_lifting_a_restriction_stores_null_not_an_empty_string(tmp_path):
    """Row 3. Circulation is one field with two readings, and "" is neither -
    it would round-trip as restricted with no reason, which is the record the
    whole rule exists to prevent."""
    root = str(tmp_path)
    rfq_id, _entry_id, query_id = rfq_with_a_query(root)

    with persistence.locked_update(root) as store:
        store.answer_query(rfq_id, query_id, answer="Provisionally yes.",
                           by="buyer@example.com",
                           restricted_reason="Commercially sensitive.")
    with persistence.locked_update(root) as store:
        store.answer_query(rfq_id, query_id, answer="Confirmed.", by="lead@example.com")

    doc = json.loads(Path(persistence.workflow_path(root)).read_text(encoding="utf-8"))
    [record] = doc["queries"]
    assert record["restricted_reason"] is None
    assert clarifications.is_circulated(persistence.load(root).queries_for(rfq_id)[0])


def test_issuing_an_addendum_moves_the_stored_package_too(tmp_path):
    """Row 4. Two collections change in one call. An issue that stamped the
    addendum but not the package would leave the reloaded store claiming a
    revision no bidder holds."""
    root = str(tmp_path)
    rfq_id, _entry_id, _query_id = rfq_with_a_query(root)

    with persistence.locked_update(root) as store:
        draft = store.draft_addendum(
            rfq_id, revision="Rev. B", summary="Corrected.",
            attachments=[Attachment(doc_code="GEN-DS-01", title="Datasheet",
                                    revision="Rev. B")],
        )
    with persistence.locked_update(root) as store:
        store.issue_addendum(rfq_id, draft.id, by="buyer@example.com")

    reloaded = persistence.load(root)
    [issued] = reloaded.addenda_for(rfq_id)
    assert issued.issued_at is not None
    assert reloaded.get_technical_package(rfq_id).revision == issued.revision
    assert issued.supersedes_revision == "Rev. A"


def test_a_refused_stale_issue_leaves_the_document_untouched(tmp_path):
    """Row 5. The check that would be lost by moving it into the route: two
    drafts cut against Rev. A, both reading "current is Rev. A" outside a lock,
    would both write and the second would undo the first."""
    root = str(tmp_path)
    rfq_id, _entry_id, _query_id = rfq_with_a_query(root)
    with persistence.locked_update(root) as store:
        first = store.draft_addendum(rfq_id, revision="Rev. B", summary="s",
                                     attachments=[])
        second = store.draft_addendum(rfq_id, revision="Rev. B2", summary="s",
                                      attachments=[])
        store.issue_addendum(rfq_id, first.id, by="buyer@example.com")

    before = Path(persistence.workflow_path(root)).read_bytes()
    with pytest.raises(ValueError, match="Rev. B"):
        with persistence.locked_update(root) as store:
            store.issue_addendum(rfq_id, second.id, by="buyer@example.com")

    assert Path(persistence.workflow_path(root)).read_bytes() == before
    reloaded = persistence.load(root)
    assert reloaded.get_technical_package(rfq_id).revision == "Rev. B"
    # Both survive: the refusal is not a delete, and the stale draft is still
    # there to be redrafted against the current revision.
    assert {a.id for a in reloaded.addenda_for(rfq_id)} == {first.id, second.id}


def test_a_deleted_draft_addendum_does_not_survive_on_disk(tmp_path):
    """Row 6. save replaces the document wholesale, so a draft pruned in memory
    cannot reappear - and the issued one beside it must not go with it."""
    root = str(tmp_path)
    rfq_id, _entry_id, _query_id = rfq_with_a_query(root)
    with persistence.locked_update(root) as store:
        keep = store.draft_addendum(rfq_id, revision="Rev. B", summary="keep",
                                    attachments=[])
        store.issue_addendum(rfq_id, keep.id, by="buyer@example.com")
        doomed = store.draft_addendum(rfq_id, revision="Rev. C", summary="doomed",
                                      attachments=[])

    with persistence.locked_update(root) as store:
        store.delete_addendum(rfq_id, doomed.id)

    reloaded = persistence.load(root)
    assert [a.id for a in reloaded.addenda_for(rfq_id)] == [keep.id]
    on_disk = json.loads(Path(persistence.workflow_path(root)).read_text(encoding="utf-8"))
    assert [a["id"] for a in on_disk["addenda"]] == [keep.id]


def test_an_rfq_cannot_reach_bids_received_over_an_open_query(tmp_path):
    """Row 7. The gate reads stored state, so a store rebuilt from disk has to
    refuse exactly what the live one did."""
    root = str(tmp_path)
    rfq_id, entry_id, query_id = rfq_with_a_query(root)
    with persistence.locked_update(root) as store:
        store.approve_shortlist(rfq_id, by="procurement@example.com")
        store.set_tbe_template(rfq_id, criteria=["Throughput"])
        store.transition(rfq_id, Stage.SHORTLISTING, by="buyer@example.com")
        store.transition(rfq_id, Stage.ISSUED, by="buyer@example.com")
        store.transition(rfq_id, Stage.CLARIFICATIONS, by="buyer@example.com")
        # Re-open the one query the fixture answered.
        store.raise_query(rfq_id, entry_id, question="A second, open question.",
                          category="Commercial", raised_on=date(2026, 8, 14))

    with pytest.raises(ValueError, match="CQ-001"):
        with persistence.locked_update(root) as store:
            store.transition(rfq_id, Stage.BIDS_RECEIVED, by="buyer@example.com")

    assert persistence.load(root).get_rfq(rfq_id).stage is Stage.CLARIFICATIONS
    assert query_id  # the answered one is untouched by any of this


def test_the_document_equals_the_store_after_the_whole_round(tmp_path):
    """Row 8. The end-to-end shape: whatever sequence ran, what is on disk is
    what the store holds - no more, no fewer."""
    root = str(tmp_path)
    rfq_id, entry_id, query_id = rfq_with_a_query(root)

    with persistence.locked_update(root) as store:
        second = store.raise_query(rfq_id, entry_id, question="Second.",
                                   category="Commercial", raised_on=date(2026, 8, 14))
        store.withdraw_query(rfq_id, second.id, reason="Duplicate.", by="b@example.com")
        draft = store.draft_addendum(
            rfq_id, revision="Rev. B", summary="Corrected.",
            attachments=[Attachment(doc_code="GEN-DS-01", title="Datasheet",
                                    revision="Rev. B")],
            arising_from_query_ids=[query_id], bid_due_date=date(2026, 11, 1),
        )
        store.issue_addendum(rfq_id, draft.id, by="buyer@example.com")

    on_disk = json.loads(Path(persistence.workflow_path(root)).read_text(encoding="utf-8"))
    assert persistence.to_document(persistence.load(root)) == on_disk
    assert [q["number"] for q in on_disk["queries"]] == ["TQ-001", "CQ-001"]
    assert [a["number"] for a in on_disk["addenda"]] == ["ADD-01"]
    assert persistence.load(root).current_bid_due_date(rfq_id) == date(2026, 11, 1)


# -- the client-approval rows of the mutation matrix --------------------------
#
# Same shape again: mutate between two runs, then read run 2 *from disk*. What
# is new here is that the value under test is never written at all, so every
# row below is as much about what the document does *not* contain as about
# what the reloaded store computes.
#
# PLAN-TEMPLATE.md's nine rows describe the extraction pipeline — sibling
# revisions, prompt-version bumps, transient and permanent LLM failures, an
# omitted optional array in a model response. None of those has a counterpart
# in a subsystem that stores nothing and calls no model, so five of the nine
# are deliberately absent rather than fabricated. The four with a real
# analogue are rows 1-4; rows 5 and 6 are specific to this change.
#
# | mutation between run 1 and run 2        | invariant at risk                  |
# |-----------------------------------------|------------------------------------|
# | the client approver is patched away     | a patch changes the next read with |
# |                                         | no second write                    |
# | the client approver is patched in       | ...and in the other direction      |
# | a bidder is created, saved, reloaded    | nothing derived was persisted      |
# | a document written before this change   | no migration is needed             |
# | a cautioned bidder is shortlisted       | the caution never lands on the     |
# |                                         | ShortlistEntry snapshot            |
# | ...and then a delete is attempted       | the delete guard is unaffected     |


def test_removing_the_client_approver_changes_the_next_read_with_no_second_write(tmp_path):
    """Row 1. Derived, never stored: the only edit is to `approved_by`, and the
    caution the *reloaded* store computes has already followed it. A cached
    copy anywhere would still be reading the old answer here."""
    from workflow.bidders import missing_client_approval

    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        bidder = a_registered_bidder(store, approved_by=["ADNOC", "Astra"])
    assert missing_client_approval(persistence.load(root).get_bidder(bidder.id)) is None

    # The one write this feature ever needs — of the source field, not of the
    # sentence derived from it.
    with persistence.locked_update(root) as store:
        store.update_bidder(bidder.id, {"approved_by": ["Astra"]})

    reloaded = persistence.load(root)                      # run 2: from disk
    assert missing_client_approval(reloaded.get_bidder(bidder.id)) == (
        "Al Munara Switchgear LLC is not on the ADNOC Approved Vendor List "
        "— approved by Astra only."
    )


def test_adding_the_client_approver_clears_the_caution_across_a_restart(tmp_path):
    """Row 2. The same rule in the direction that matters operationally: a
    vendor who gets onto the client's list is cleared by correcting one field,
    with nothing to re-save and no sweep to run."""
    from workflow.bidders import missing_client_approval

    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        bidder = a_registered_bidder(store, approved_by=["Astra"])
    assert missing_client_approval(persistence.load(root).get_bidder(bidder.id)) is not None

    with persistence.locked_update(root) as store:
        store.update_bidder(bidder.id, {"approved_by": ["Astra", "ADNOC"]})

    reloaded = persistence.load(root)
    assert missing_client_approval(reloaded.get_bidder(bidder.id)) is None


def test_no_derived_caution_is_ever_written_to_the_document(tmp_path):
    """Row 3. The invariant Task 1 owns: `Bidder`'s persisted field set is
    exactly what it was, so the sentence exists only at read time. Reading the
    raw text *is* the assertion here — `to_document` agreeing with itself would
    not catch a key both halves of the round trip knew about."""
    from workflow.bidders import missing_client_approval

    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        cautioned = a_registered_bidder(store, approved_by=["Astra"])
        approved = a_registered_bidder(
            store, name="Northwind Valve Works", approved_by=["ADNOC"]
        )
    live = missing_client_approval(persistence.load(root).get_bidder(cautioned.id))

    raw = Path(persistence.workflow_path(root)).read_text(encoding="utf-8")
    assert "approval_caution" not in raw
    # Not just the key: the sentence itself must appear nowhere in the file,
    # under any spelling a well-meaning cache might have chosen.
    assert "Approved Vendor List" not in raw

    reloaded = persistence.load(root)
    assert missing_client_approval(reloaded.get_bidder(cautioned.id)) == live
    assert "approved by Astra only." in live
    assert missing_client_approval(reloaded.get_bidder(approved.id)) is None


def test_a_document_written_before_this_change_needs_no_migration(tmp_path):
    """Row 4. An existing registry has bidders that never carried anything
    approval-derived, because there was nothing to carry. Every one of them
    loads, and each computes its caution from `approved_by` alone."""
    from workflow.bidders import missing_client_approval

    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        project, generator, _cable = project_and_items(store)
        cover_with_rfq(store, project.id, [generator.id])

    # Hand-written the way a pre-change document really looks: bidder records
    # carrying only the fields the model has always declared.
    path = Path(persistence.workflow_path(root))
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["bidders"] = [
        {"id": "bdr_onlist", "name": "On The List LLC", "approved_by": ["ADNOC"]},
        {"id": "bdr_ourslc", "name": "Ours Only LLC", "approved_by": ["Astra"]},
        {"id": "bdr_nobody", "name": "Nobody Approved LLC", "approved_by": []},
    ]
    path.write_text(json.dumps(doc), encoding="utf-8")

    loaded = persistence.load(root)
    assert len(loaded.list_bidders()) == 3
    assert loaded.list_projects()          # everything else still loaded
    assert missing_client_approval(loaded.get_bidder("bdr_onlist")) is None
    assert "approved by Astra only." in missing_client_approval(
        loaded.get_bidder("bdr_ourslc")
    )
    assert "has no approval recorded." in missing_client_approval(
        loaded.get_bidder("bdr_nobody")
    )
    # No migration, so the version never moved.
    assert doc["version"] == 1


def test_a_caution_never_lands_on_the_shortlist_snapshot(tmp_path):
    """Row 5. The invariant Task 2 owns. `ShortlistEntry` snapshots
    `vendor_name`, `prequal_status` and `scope_code_fit` and nothing else, so an
    invitation issued while the bidder was off the client's list leaves no trace
    of that once the bidder gets onto it."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        project, generator, _cable = project_and_items(store)
        rfq = cover_with_rfq(store, project.id, [generator.id])
        bidder = a_registered_bidder(store, approved_by=["Astra"])
        entry = store.add_shortlist_entry(
            rfq.id, vendor_id=bidder.id, as_of=date(2026, 8, 13)
        )
    before = json.loads(Path(persistence.workflow_path(root)).read_text(encoding="utf-8"))
    [stored_entry] = before["shortlists"]

    with persistence.locked_update(root) as store:
        store.update_bidder(bidder.id, {"approved_by": ["Astra", "ADNOC"]})

    raw = Path(persistence.workflow_path(root)).read_text(encoding="utf-8")
    assert "Approved Vendor List" not in raw
    reloaded = persistence.load(root)
    [survivor] = reloaded.shortlist_for(rfq.id)
    assert survivor.id == entry.id
    assert survivor.vendor_name == "Al Munara Switchgear LLC"
    assert survivor.prequal_status == "Approved"
    # The entry is byte-for-byte what it was: the mutation touched the bidder.
    assert json.loads(raw)["shortlists"] == [stored_entry]


def test_the_delete_guard_is_unaffected_by_the_caution(tmp_path):
    """Row 6. A caution is not a blocker in either direction — it neither
    permits a delete the guard refuses nor stands in for the refusal. The
    sentence naming the RFQ is still what comes back."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        project, generator, _cable = project_and_items(store)
        rfq = cover_with_rfq(store, project.id, [generator.id])
        bidder = a_registered_bidder(store, approved_by=["Astra"])
        entry = store.add_shortlist_entry(
            rfq.id, vendor_id=bidder.id, as_of=date(2026, 8, 13)
        )
    before = Path(persistence.workflow_path(root)).read_bytes()

    with pytest.raises(ValueError, match="ADP-RFQ-2026-014") as refusal:
        with persistence.locked_update(root) as store:
            store.delete_bidder(bidder.id)
    assert "Approved Vendor List" not in str(refusal.value)

    assert Path(persistence.workflow_path(root)).read_bytes() == before
    reloaded = persistence.load(root)
    assert reloaded.get_bidder(bidder.id) is not None
    assert [e.id for e in reloaded.shortlist_for(rfq.id)] == [entry.id]
