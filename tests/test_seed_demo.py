"""The demo seed.

The invariant these tests defend: **the seeded store holds no RFQ in a stage
its own gates would have refused.** A demo showing an Issued RFQ whose
shortlist was never approved is the product lying to the person being shown it,
which is worse than the demo being smaller. So the check is not "does it look
plausible" but "replay each RFQ's forward history and confirm every gate on
that path would have passed".

The second thing asserted here is that the seed cannot eat real work: it
refuses a store holding any entity unless `--force`.
"""
import json
from datetime import date, timedelta

from workflow import persistence, seed_demo
from workflow.gates import check_gate
from workflow.stages import Stage, is_backward

AS_OF = date(2026, 8, 13)


# -- the content is coherent -------------------------------------------------


def test_the_seed_populates_every_collection_a_demo_shows():
    store = seed_demo.build_demo_store(AS_OF)
    assert len(store.list_bidders()) >= 10
    assert len(store.list_projects()) >= 3
    assert len(store.list_rfqs()) >= 5
    assert any(store.items_for_project(p.id) for p in store.list_projects())


def test_no_rfq_sits_in_a_stage_its_own_gates_would_have_refused():
    """Replay each forward edge the history records and re-check its gate
    against the finished store. A seed that hand-placed a stage would fail
    here rather than in front of a client."""
    store = seed_demo.build_demo_store(AS_OF)
    for rfq in store.list_rfqs():
        for entry in rfq.history:
            if entry.from_stage is None or is_backward(entry.from_stage, entry.to_stage):
                continue
            gate = check_gate(store, rfq.id, entry.from_stage, entry.to_stage)
            assert gate.passed, (
                f"{rfq.reference} claims {entry.from_stage.value} -> "
                f"{entry.to_stage.value} but the gate says: {gate.reason}"
            )


def test_every_rfq_history_starts_at_scoping_and_ends_where_the_rfq_is():
    store = seed_demo.build_demo_store(AS_OF)
    for rfq in store.list_rfqs():
        assert rfq.history[0].from_stage is None
        assert rfq.history[0].to_stage is Stage.SCOPING
        assert rfq.history[-1].to_stage is rfq.stage


def test_the_stages_on_show_span_more_than_one():
    """A demo where every RFQ sits in the same stage demonstrates a list, not
    a workflow."""
    stages = {rfq.stage for rfq in seed_demo.build_demo_store(AS_OF).list_rfqs()}
    assert len(stages) >= 4


def test_every_shortlist_entry_resolves_to_a_registry_bidder():
    """The point of the seed is to show the registry working. A free-text
    entry here would demonstrate the thing it replaced."""
    store = seed_demo.build_demo_store(AS_OF)
    ids = {b.id for b in store.list_bidders()}
    for rfq in store.list_rfqs():
        for entry in store.shortlist_for(rfq.id):
            assert entry.vendor_id in ids, entry.vendor_name


def test_every_item_belongs_to_a_seeded_project_and_every_rfq_to_its_items():
    store = seed_demo.build_demo_store(AS_OF)
    project_ids = {p.id for p in store.list_projects()}
    for project_id in project_ids:
        for item in store.items_for_project(project_id):
            assert item.project_id == project_id
    for rfq in store.list_rfqs():
        assert rfq.project_id in project_ids
        owned = {i.id for i in store.items_for_project(rfq.project_id)}
        assert set(rfq.item_ids) <= owned


def test_no_real_company_is_named():
    """Invented names only. The list is deliberately explicit rather than a
    heuristic — a demo that ships a real vendor's prequalification status is a
    problem no test can pattern-match its way out of."""
    names = {b.name for b in seed_demo.build_demo_store(AS_OF).list_bidders()}
    assert names == set(seed_demo.INVENTED_BIDDER_NAMES)


# -- dates move with `as_of`, ids do not -------------------------------------


def test_the_registry_shows_every_prequalification_state():
    from workflow.bidders import effective_prequal

    store = seed_demo.build_demo_store(AS_OF)
    states = {effective_prequal(b, AS_OF) for b in store.list_bidders()}
    assert {"Approved", "Under review", "Suspended", "Expired"} <= states


def test_one_bidder_is_expired_relative_to_as_of_whenever_it_is_run():
    for as_of in (date(2026, 8, 13), date(2027, 5, 1), date(2030, 1, 1)):
        store = seed_demo.build_demo_store(as_of)
        expired = [
            b for b in store.list_bidders()
            if b.prequal_status == "Approved"
            and b.prequal_expires_on is not None
            and b.prequal_expires_on < as_of
        ]
        assert expired, as_of


def test_one_bidder_is_expiring_inside_thirty_days_whenever_it_is_run():
    for as_of in (date(2026, 8, 13), date(2027, 5, 1)):
        store = seed_demo.build_demo_store(as_of)
        soon = [
            b for b in store.list_bidders()
            if b.prequal_status == "Approved"
            and b.prequal_expires_on is not None
            and as_of <= b.prequal_expires_on <= as_of + timedelta(days=30)
        ]
        assert soon, as_of


def test_ids_are_stable_across_builds_and_across_as_of():
    """A demo link and a screenshot have to survive a reseed."""
    first = seed_demo.build_demo_store(AS_OF)
    second = seed_demo.build_demo_store(date(2027, 1, 1))
    assert [b.id for b in first.list_bidders()] == [b.id for b in second.list_bidders()]
    assert [r.id for r in first.list_rfqs()] == [r.id for r in second.list_rfqs()]


def test_shortlist_entry_ids_are_stable_too():
    """Removal addresses an entry by id, so an unstable one would make every
    "remove this row" in a rehearsed demo hit a different row."""
    first = seed_demo.build_demo_store(AS_OF)
    second = seed_demo.build_demo_store(AS_OF)
    for rfq in first.list_rfqs():
        assert [e.id for e in first.shortlist_for(rfq.id)] == [
            e.id for e in second.shortlist_for(rfq.id)
        ]


# -- writing, and refusing to ------------------------------------------------


def test_main_writes_a_document_the_loader_accepts(tmp_path):
    assert seed_demo.main(["--root", str(tmp_path), "--as-of", "2026-08-13"]) == 0

    loaded = persistence.load(str(tmp_path))
    assert len(loaded.list_bidders()) >= 10
    doc = json.loads((tmp_path / "workflow.json").read_text(encoding="utf-8"))
    assert doc["version"] == 1


def test_main_refuses_a_store_that_holds_anything(tmp_path):
    store = persistence.load(str(tmp_path))
    store.create_project(
        name="Real work", code="REA", client="A client", location="Somewhere",
        live_period_start=date(2026, 1, 1), live_period_end=date(2027, 1, 1),
    )
    persistence.save(str(tmp_path), store)

    assert seed_demo.main(["--root", str(tmp_path)]) != 0

    # And wrote nothing. A demo loader that silently replaces real work is a
    # data-loss bug with a friendly name.
    assert [p.name for p in persistence.load(str(tmp_path)).list_projects()] == ["Real work"]


def test_force_overwrites(tmp_path):
    store = persistence.load(str(tmp_path))
    store.create_project(
        name="Real work", code="REA", client="A client", location="Somewhere",
        live_period_start=date(2026, 1, 1), live_period_end=date(2027, 1, 1),
    )
    persistence.save(str(tmp_path), store)

    assert seed_demo.main(["--root", str(tmp_path), "--force"]) == 0
    assert "Real work" not in {p.name for p in persistence.load(str(tmp_path)).list_projects()}


def test_seeding_an_empty_root_needs_no_force(tmp_path):
    assert seed_demo.main(["--root", str(tmp_path)]) == 0


def test_a_seeded_store_round_trips_unchanged(tmp_path):
    """Written through `persistence.save`, so the seed cannot emit a document
    the loader would reject — and cannot drift from the store's own shape."""
    built = seed_demo.build_demo_store(AS_OF)
    persistence.save(str(tmp_path), built)
    loaded = persistence.load(str(tmp_path))

    assert persistence.to_document(loaded) == persistence.to_document(built)
