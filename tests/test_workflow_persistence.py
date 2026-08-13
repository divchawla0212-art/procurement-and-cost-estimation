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


def test_the_document_carries_a_bidders_collection(tmp_path):
    store, _ = populated_store()
    a_registered_bidder(store)
    persistence.save(str(tmp_path), store)

    doc = json.loads((tmp_path / "workflow.json").read_text(encoding="utf-8"))
    assert doc["bidders"][0]["name"] == "Al Munara Switchgear LLC"
    # Derived, never stored — the load has nothing to keep honest.
    assert "effective_prequal" not in doc["bidders"][0]


def test_a_document_written_before_the_registry_loads_as_an_empty_one(tmp_path):
    """No version bump and no migration: a document with no `bidders` key means
    no bidders, which is the correct reading of it."""
    store, _ = populated_store()
    persistence.save(str(tmp_path), store)
    path = tmp_path / "workflow.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    del doc["bidders"]
    path.write_text(json.dumps(doc), encoding="utf-8")

    loaded = persistence.load(str(tmp_path))
    assert loaded.list_bidders() == []
    assert loaded.list_projects()  # everything else still loaded


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


def test_an_approval_lapsing_between_runs_changes_no_stored_byte(tmp_path):
    """Row 4. The whole reason `Expired` is derived: nothing has to be written
    for a lapse to take effect, and nothing is."""
    from workflow.bidders import effective_prequal

    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        bidder = a_registered_bidder(store, prequal_expires_on=date(2026, 9, 30))
    before = json.loads(Path(persistence.workflow_path(root)).read_text(encoding="utf-8"))

    # Run 2 mutates something else entirely; the clock is what moved.
    with persistence.locked_update(root) as store:
        store.update_bidder(bidder.id, {"notes": "Chased the renewal"})

    reloaded = persistence.load(root).get_bidder(bidder.id)
    assert reloaded.prequal_status == "Approved"
    assert reloaded.prequal_expires_on == date(2026, 9, 30)
    assert effective_prequal(reloaded, date(2026, 9, 30)) == "Approved"
    assert effective_prequal(reloaded, date(2026, 10, 1)) == "Expired"
    # The stored record is untouched but for the one field that was edited.
    after = json.loads(Path(persistence.workflow_path(root)).read_text(encoding="utf-8"))
    assert {**before["bidders"][0], "notes": "Chased the renewal"} == after["bidders"][0]


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
