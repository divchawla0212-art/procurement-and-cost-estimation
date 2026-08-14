"""The bidder registry in SQLite.

Two things are worth asserting here and nothing else is: that a bidder survives
the round trip *whole* — including the three list-valued fields that are child
tables rather than columns — and that the query answers exactly what
`bidders.client_approved` answers in Python. If those two hold, moving the
registry out of `workflow.json` changed where it lives and nothing else.
"""
import os
from datetime import date

from workflow import bidder_db
from workflow.bidders import client_approved
from workflow.models.bidder import ADNOC, ASTRA, Bidder


def a_bidder(**overrides) -> Bidder:
    defaults = dict(
        id="bdr_10000238",
        name="Al Munara Switchgear LLC",
        country="United Arab Emirates",
        approved_by=[ADNOC],
        trade_categories=["SWITCHGEARS - LV -415V"],
        prequal_status="Approved",
        prequal_expires_on=date(2027, 3, 31),
        represented_manufacturers=["SCHNEIDER ELECTRIC"],
    )
    return Bidder(**{**defaults, **overrides})


def test_a_missing_database_reads_as_an_empty_registry(tmp_path):
    """A first run, not an error — the same reading `persistence.load` gives a
    missing document."""
    assert bidder_db.list_all(str(tmp_path)) == []
    assert bidder_db.count(str(tmp_path)) == 0


def test_a_bidder_survives_the_round_trip_whole(tmp_path):
    root = str(tmp_path)
    original = a_bidder(
        on_hold=True,
        hold_reason="Unresolved dispute on HAL-19",
        turnover_band="AED 50–100m",
        performance_rating=4.4,
        past_awards=7,
        notes="Consistent on LV frames.",
        approved_by=[ADNOC, ASTRA],
        trade_categories=["SWITCHGEARS - LV -415V", "CABLES - LV POWER DISTRIBUTION"],
        represented_manufacturers=["SCHNEIDER ELECTRIC", "ABB"],
    )
    bidder_db.replace_all(root, [original])

    (restored,) = bidder_db.list_all(root)
    # Every field, not a sample: the list-valued three are child tables, and a
    # column quietly dropped from the insert would still pass a spot check.
    assert restored == original


def test_a_bidder_with_no_lists_round_trips_too(tmp_path):
    """The AVL import leaves country, expiry, hold and rating empty on every
    row it creates, so the all-empty case is the common one, not the edge."""
    root = str(tmp_path)
    sparse = Bidder(id="bdr_1", name="A 2 Z OFFICE FURNITURE - L L C",
                    approved_by=[], trade_categories=[], represented_manufacturers=[])
    bidder_db.replace_all(root, [sparse])
    assert bidder_db.list_all(root) == [sparse]


def test_replace_all_leaves_exactly_what_it_was_given(tmp_path):
    """Wholesale replacement, the same invariant `persistence.save` has: a
    bidder removed in memory cannot survive on disk."""
    root = str(tmp_path)
    bidder_db.replace_all(root, [a_bidder(id="bdr_1"), a_bidder(id="bdr_2")])
    bidder_db.replace_all(root, [a_bidder(id="bdr_2")])

    assert [b.id for b in bidder_db.list_all(root)] == ["bdr_2"]


def test_replacing_a_bidder_does_not_leave_its_old_child_rows(tmp_path):
    """The cascade. Without `PRAGMA foreign_keys = ON` the delete leaves
    orphans, and the next read hands back a vendor registered for a product
    group it no longer carries."""
    root = str(tmp_path)
    bidder_db.replace_all(root, [a_bidder(trade_categories=["SWITCHGEARS - LV -415V"])])
    bidder_db.replace_all(root, [a_bidder(trade_categories=["UMBILICAL CABLE"])])

    (only,) = bidder_db.list_all(root)
    assert only.trade_categories == ["UMBILICAL CABLE"]


def test_the_registry_is_ordered_by_name_case_insensitively(tmp_path):
    root = str(tmp_path)
    bidder_db.replace_all(root, [
        a_bidder(id="bdr_1", name="zenith Piping"),
        a_bidder(id="bdr_2", name="Al Munara"),
        a_bidder(id="bdr_3", name="Marjan"),
    ])
    assert [b.name for b in bidder_db.list_all(root)] == [
        "Al Munara", "Marjan", "zenith Piping",
    ]


# -- the query ---------------------------------------------------------------


def test_the_approved_query_returns_only_the_approvers_vendors(tmp_path):
    root = str(tmp_path)
    bidder_db.replace_all(root, [
        a_bidder(id="bdr_1", name="Al Munara", approved_by=[ADNOC]),
        a_bidder(id="bdr_2", name="Silverdune", approved_by=[ASTRA]),
    ])
    found = bidder_db.client_approved_rows(root, ADNOC)
    assert [b.name for b in found] == ["Al Munara"]


def test_the_approved_query_narrows_to_the_product_groups_given(tmp_path):
    root = str(tmp_path)
    bidder_db.replace_all(root, [
        a_bidder(id="bdr_1", name="Ras Dana",
                 trade_categories=["CABLES - MV (UP TO 33KV)POWER TRANSMISSION"]),
        a_bidder(id="bdr_2", name="Falcon Bay",
                 trade_categories=["GENERATOR POWER-DIESEL ENGINE DRIVEN"]),
    ])
    found = bidder_db.client_approved_rows(
        root, ADNOC, ["CABLES - MV (UP TO 33KV)POWER TRANSMISSION", "UMBILICAL CABLE"]
    )
    assert [b.name for b in found] == ["Ras Dana"]


def test_a_vendor_matching_several_groups_is_returned_once(tmp_path):
    """`DISTINCT`. A join over a child table repeats the parent row per match,
    and a discipline is a family of eleven groups."""
    root = str(tmp_path)
    bidder_db.replace_all(root, [
        a_bidder(id="bdr_1", name="Ras Dana", trade_categories=[
            "CABLES - MV (UP TO 33KV)POWER TRANSMISSION",
            "CABLES - LV POWER DISTRIBUTION",
            "UMBILICAL CABLE",
        ]),
    ])
    found = bidder_db.client_approved_rows(root, ADNOC, [
        "CABLES - MV (UP TO 33KV)POWER TRANSMISSION",
        "CABLES - LV POWER DISTRIBUTION",
        "UMBILICAL CABLE",
    ])
    assert len(found) == 1


def test_the_query_folds_case_and_padding_the_same_way_python_does(tmp_path):
    """The stored key and the query parameter go through one function. If they
    ever disagreed a vendor would be invisible to the filter its own row
    matches."""
    root = str(tmp_path)
    bidder_db.replace_all(root, [
        a_bidder(trade_categories=["  switchgears - LV -415V "], approved_by=["adnoc"]),
    ])
    assert bidder_db.client_approved_rows(root, "ADNOC", ["SWITCHGEARS - LV -415V"])


def test_an_empty_product_group_list_matches_nobody(tmp_path):
    """Distinct from `None`, which means "do not narrow". An empty family is a
    filter that nothing satisfies, and answering it with the whole list would
    be the opposite of what was asked."""
    root = str(tmp_path)
    bidder_db.replace_all(root, [a_bidder()])
    assert bidder_db.client_approved_rows(root, ADNOC, []) == []
    assert len(bidder_db.client_approved_rows(root, ADNOC, None)) == 1


def test_the_query_agrees_with_the_python_one(tmp_path):
    """The two must not drift: `client_approved` still runs over an in-memory
    registry for the candidate list, and this runs in SQL for the vendor
    screens. Same roster, same answer."""
    root = str(tmp_path)
    roster = [
        a_bidder(id="bdr_1", name="Ras Dana", trade_categories=["UMBILICAL CABLE"]),
        a_bidder(id="bdr_2", name="Silverdune", approved_by=[ASTRA],
                 trade_categories=["UMBILICAL CABLE"]),
        a_bidder(id="bdr_3", name="Falcon Bay", trade_categories=["GENERATOR POWER-OTHERS"]),
    ]
    bidder_db.replace_all(root, roster)

    assert [b.name for b in bidder_db.client_approved_rows(root, ADNOC)] == [
        b.name for b in client_approved(roster)
    ]
    assert [
        b.name for b in bidder_db.client_approved_rows(root, ADNOC, ["UMBILICAL CABLE"])
    ] == [b.name for b in client_approved(roster, "Cables") if "UMBILICAL CABLE" in b.trade_categories]


def test_the_distinct_product_groups_are_listed(tmp_path):
    root = str(tmp_path)
    bidder_db.replace_all(root, [
        a_bidder(id="bdr_1", trade_categories=["UMBILICAL CABLE", "GLAND -CABLE"]),
        a_bidder(id="bdr_2", trade_categories=["UMBILICAL CABLE"]),
    ])
    assert bidder_db.product_group_names(root) == ["GLAND -CABLE", "UMBILICAL CABLE"]


# -- what the store writes, and when -----------------------------------------


def test_a_write_that_leaves_the_registry_alone_does_not_rewrite_it(tmp_path):
    """Rewriting 1 346 rows costs most of the time an RFQ transition takes, and
    a transition does not touch the registry. Asserted through the file's
    modification count rather than a timer, which would be flaky."""
    from workflow import persistence

    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        store.create_bidder(**a_bidder().model_dump(exclude={"id"}))
        store.create_project(
            project_id="prj_1", name="Haliba", code="HAL", client="ADP",
            location="UAE", live_period_start=date(2026, 1, 1),
            live_period_end=date(2029, 12, 31),
        )

    stamp = os.path.getmtime(bidder_db.db_path(root))
    with persistence.locked_update(root) as store:
        store.update_project("prj_1", {"status": "On Hold"})

    assert os.path.getmtime(bidder_db.db_path(root)) == stamp
    # And the registry is still there to be read.
    assert bidder_db.count(root) == 1


def test_a_registry_edit_is_written(tmp_path):
    from workflow import persistence

    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        created = store.create_bidder(**a_bidder().model_dump(exclude={"id"}))

    with persistence.locked_update(root) as store:
        store.update_bidder(created.id, {"notes": "Chased the renewal"})

    (stored,) = bidder_db.list_all(root)
    assert stored.notes == "Chased the renewal"


def test_a_deleted_bidder_leaves_the_database(tmp_path):
    from workflow import persistence

    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        created = store.create_bidder(**a_bidder().model_dump(exclude={"id"}))
    with persistence.locked_update(root) as store:
        store.delete_bidder(created.id)

    assert bidder_db.list_all(root) == []


# -- both approvals ----------------------------------------------------------


def test_available_means_every_approval_not_any_of_them(tmp_path):
    """ADNOC says the client will accept them, Astra says we will. An `IN`
    clause would return everyone with either, which is the whole registry."""
    root = str(tmp_path)
    bidder_db.replace_all(root, [
        a_bidder(id="bdr_both", name="Ras Dana", approved_by=[ADNOC, ASTRA]),
        a_bidder(id="bdr_adnoc", name="Al Munara", approved_by=[ADNOC]),
        a_bidder(id="bdr_astra", name="Silverdune", approved_by=[ASTRA]),
        a_bidder(id="bdr_none", name="Blue Harbour", approved_by=[]),
    ])
    found = bidder_db.approved_by_all(root, [ADNOC, ASTRA])
    assert [b.name for b in found] == ["Ras Dana"]


def test_both_approvals_survive_the_product_group_join(tmp_path):
    """The join multiplies approval rows: a vendor in four cable groups repeats
    each approval four times, and a plain count would let one approval pass."""
    root = str(tmp_path)
    bidder_db.replace_all(root, [
        a_bidder(id="bdr_1", name="Al Munara", approved_by=[ADNOC], trade_categories=[
            "CABLES - LV POWER DISTRIBUTION",
            "CABLES - FIBER OPTICS",
            "UMBILICAL CABLE",
        ]),
    ])
    found = bidder_db.approved_by_all(root, [ADNOC, ASTRA], [
        "CABLES - LV POWER DISTRIBUTION", "CABLES - FIBER OPTICS", "UMBILICAL CABLE",
    ])
    assert found == []


def test_an_available_vendor_is_returned_once_per_discipline_not_per_group(tmp_path):
    root = str(tmp_path)
    bidder_db.replace_all(root, [
        a_bidder(id="bdr_1", name="Ras Dana", approved_by=[ADNOC, ASTRA],
                 trade_categories=["CABLES - FIBER OPTICS", "UMBILICAL CABLE"]),
    ])
    found = bidder_db.approved_by_all(
        root, [ADNOC, ASTRA], ["CABLES - FIBER OPTICS", "UMBILICAL CABLE"]
    )
    assert len(found) == 1


def test_recording_an_approval_leaves_the_others_alone(tmp_path):
    root = str(tmp_path)
    bidder_db.replace_all(root, [a_bidder(id="bdr_1", approved_by=[ADNOC])])

    assert bidder_db.add_approval(root, ["bdr_1"], ASTRA) == 1

    (stored,) = bidder_db.list_all(root)
    # Appended, so the client's own approval keeps its place at the front.
    assert stored.approved_by == [ADNOC, ASTRA]


def test_recording_an_approval_twice_changes_nothing(tmp_path):
    root = str(tmp_path)
    bidder_db.replace_all(root, [a_bidder(id="bdr_1", approved_by=[ADNOC])])
    bidder_db.add_approval(root, ["bdr_1"], ASTRA)
    bidder_db.add_approval(root, ["bdr_1"], ASTRA)

    (stored,) = bidder_db.list_all(root)
    assert stored.approved_by == [ADNOC, ASTRA]


def test_an_approval_for_an_unknown_vendor_creates_nobody(tmp_path):
    """The Astra file says who approved whom, not who exists. Inventing a
    nameless vendor from a number in a second export is the embellishment
    `avl_import` exists to refuse."""
    root = str(tmp_path)
    bidder_db.replace_all(root, [a_bidder(id="bdr_1")])

    assert bidder_db.add_approval(root, ["bdr_1", "bdr_nope"], ASTRA) == 1
    assert [b.id for b in bidder_db.list_all(root)] == ["bdr_1"]
