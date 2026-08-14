"""Whether a bidder may be invited to a given RFQ, and why not.

Two rules are load-bearing here and are each asserted directly.

**`Expired` is computed, never stored.** There is no such member of
`PrequalStatus`. A stored expiry flag is wrong the day after it is written and
needs a sweep job nobody has written to stay honest; a computed one is right
whenever it is read. Every test here passes `as_of` explicitly for that reason
— a function that read the clock could not be asserted at a boundary without
freezing it.

**A blocker names the whole criterion.** `gates.py` states that rule and the
phase 1 plan shipped a message that broke it. A reader told only "not approved"
has to guess whether renewing would help; one told the date it lapsed does not.
"""
import importlib
from datetime import date

from workflow.bidders import (
    Suitability,
    client_approved,
    effective_prequal,
    evaluate,
    missing_client_approval,
)
from workflow.models.bidder import ADNOC, ASTRA, Bidder
from workflow.models.rfq import RfqRecord

TODAY = date(2026, 8, 13)


def a_bidder(**overrides) -> Bidder:
    defaults = dict(
        name="Al Munara Switchgear LLC",
        country="United Arab Emirates",
        # On the client's list by default. Without this the happy-path bidder
        # is one nobody has approved, and every "nothing to say" assertion in
        # this file would be asserting the wrong thing.
        approved_by=[ADNOC],
        trade_categories=["Electrical"],
        prequal_status="Approved",
        prequal_expires_on=date(2027, 3, 31),
    )
    return Bidder(**{**defaults, **overrides})


def an_rfq(**overrides) -> RfqRecord:
    defaults = dict(
        reference="ADP-RFQ-2026-014",
        project_id="prj_demo",
        item_ids=[],
        package="LV switchgear",
        discipline="Electrical",
        value_estimate_aed=4_000_000,
    )
    return RfqRecord(**{**defaults, **overrides})


# -- the happy path ----------------------------------------------------------


def test_an_approved_in_scope_bidder_is_eligible_with_nothing_to_say():
    result = evaluate(a_bidder(), an_rfq(), TODAY)
    assert result == Suitability(
        eligible=True,
        scope_fit=True,
        effective_prequal="Approved",
        blockers=[],
        cautions=[],
    )


# -- blockers ----------------------------------------------------------------


def test_a_bidder_on_hold_is_blocked_and_the_reason_is_named():
    bidder = a_bidder(on_hold=True, hold_reason="Unresolved commercial dispute on HAL-19")
    result = evaluate(bidder, an_rfq(), TODAY)
    assert not result.eligible
    assert any("Unresolved commercial dispute on HAL-19" in b for b in result.blockers)


def test_a_hold_with_no_recorded_reason_still_says_so():
    result = evaluate(a_bidder(on_hold=True), an_rfq(), TODAY)
    assert not result.eligible
    # Never a bare "on hold." with nothing after it — the sentence has to be
    # complete even when the data behind it is not.
    assert any("no reason recorded" in b for b in result.blockers)


def test_an_unapproved_prequalification_blocks_with_a_sentence_of_its_own():
    """One sentence per state rather than one template with the status dropped
    in. The template read "is not qualified, not approved", which stutters and
    buries what the reader would have to do about it."""
    expected = {
        "Under review": "still under review",
        "Suspended": "suspended and cannot be invited",
        "Not qualified": "declined at prequalification",
    }
    for status, phrase in expected.items():
        result = evaluate(a_bidder(prequal_status=status), an_rfq(), TODAY)
        assert not result.eligible, status
        assert any(phrase in b for b in result.blockers), status
        # And every one of them names who it is about.
        assert all("Al Munara Switchgear LLC" in b for b in result.blockers), status


def test_an_expired_prequalification_blocks_and_names_the_date():
    bidder = a_bidder(prequal_expires_on=date(2026, 4, 30))
    result = evaluate(bidder, an_rfq(), TODAY)
    assert not result.eligible
    assert result.effective_prequal == "Expired"
    assert any("2026-04-30" in b for b in result.blockers)


def test_both_a_hold_and_a_lapse_are_reported_together():
    """One blocker at a time would send the reader round the loop twice."""
    bidder = a_bidder(on_hold=True, hold_reason="Audit", prequal_expires_on=date(2026, 1, 1))
    result = evaluate(bidder, an_rfq(), TODAY)
    assert len(result.blockers) == 2


# -- the expiry boundary, from both sides ------------------------------------


def test_a_prequalification_expiring_today_has_not_expired():
    bidder = a_bidder(prequal_expires_on=TODAY)
    assert effective_prequal(bidder, TODAY) == "Approved"
    assert evaluate(bidder, an_rfq(), TODAY).eligible


def test_a_prequalification_that_expired_yesterday_has_expired():
    bidder = a_bidder(prequal_expires_on=date(2026, 8, 12))
    assert effective_prequal(bidder, TODAY) == "Expired"


def test_no_expiry_date_never_expires():
    """An open-ended approval is a real thing; absence is not "expired"."""
    assert effective_prequal(a_bidder(prequal_expires_on=None), TODAY) == "Approved"


def test_expiry_does_not_rescue_an_unapproved_status():
    bidder = a_bidder(prequal_status="Suspended", prequal_expires_on=date(2020, 1, 1))
    # Suspended is the fact worth reporting; "Expired" would read as though
    # renewing were the remedy.
    assert effective_prequal(bidder, TODAY) == "Suspended"


# -- the 30-day caution, from both sides -------------------------------------


def test_expiring_in_exactly_thirty_days_is_a_caution_not_a_blocker():
    bidder = a_bidder(prequal_expires_on=date(2026, 9, 12))
    result = evaluate(bidder, an_rfq(), TODAY)
    assert result.eligible
    assert any("2026-09-12" in c for c in result.cautions)


def test_expiring_in_thirty_one_days_is_not_yet_a_caution():
    bidder = a_bidder(prequal_expires_on=date(2026, 9, 13))
    assert evaluate(bidder, an_rfq(), TODAY).cautions == []


def test_an_already_expired_bidder_is_not_also_cautioned():
    """The blocker says it. Saying it twice in two registers is noise."""
    result = evaluate(a_bidder(prequal_expires_on=date(2026, 1, 1)), an_rfq(), TODAY)
    assert result.cautions == []


# -- scope fit ---------------------------------------------------------------


def test_a_category_matching_the_discipline_is_a_fit():
    bidder = a_bidder(trade_categories=["Instrumentation", "Electrical"])
    assert evaluate(bidder, an_rfq(discipline="Electrical"), TODAY).scope_fit


def test_a_category_matching_the_package_is_also_a_fit():
    bidder = a_bidder(trade_categories=["LV switchgear"])
    assert evaluate(bidder, an_rfq(package="LV switchgear", discipline="Electrical"), TODAY).scope_fit


def test_matching_ignores_case_and_surrounding_space():
    bidder = a_bidder(trade_categories=["  eLeCtRiCaL "])
    assert evaluate(bidder, an_rfq(discipline="Electrical"), TODAY).scope_fit


def test_a_partial_word_is_not_a_match():
    """Whole-string matching, not substring. `classify._RULES` shipped false
    matches from substrings, and "Electric" is a different trade from
    "Electrical Testing"."""
    bidder = a_bidder(trade_categories=["Electric"])
    assert not evaluate(bidder, an_rfq(discipline="Electrical"), TODAY).scope_fit


def test_a_scope_mismatch_cautions_but_does_not_block():
    """Inviting a bidder moving into a discipline is a real decision, and
    `scope_code_fit` exists to record that it was made knowingly."""
    bidder = a_bidder(trade_categories=["Civil"])
    result = evaluate(bidder, an_rfq(), TODAY)
    assert result.eligible
    assert not result.scope_fit
    assert any("Electrical" in c for c in result.cautions)


def test_a_bidder_with_no_categories_at_all_is_a_mismatch():
    result = evaluate(a_bidder(trade_categories=[]), an_rfq(), TODAY)
    assert not result.scope_fit
    assert result.eligible


def test_an_rfq_discipline_reaches_the_product_groups_behind_it():
    """The same expansion `client_approved` does, for the same reason.

    Without it the two disagree: the item screen lists every vendor registered
    for a cable product group, and the candidate list beside the RFQ raised
    from that item cautions each of them for scope — because no vendor is
    registered for a category literally called "Cables". Two answers to one
    question, from one vocabulary, is the bug.
    """
    bidder = a_bidder(trade_categories=["CABLES - LV POWER DISTRIBUTION"])
    assert evaluate(bidder, an_rfq(discipline="Cables"), TODAY).scope_fit


def test_the_package_is_expanded_too():
    bidder = a_bidder(trade_categories=["GENERATOR POWER-OTHERS"])
    result = evaluate(
        bidder, an_rfq(package="Generators", discipline="Electrical"), TODAY
    )
    assert result.scope_fit


def test_an_unknown_discipline_still_matches_itself():
    """Expansion is additive, never a replacement. An RFQ scoped straight to a
    product group description — which is what the export itself speaks — has to
    keep working, and so does one carrying free text from before the vocabulary
    existed."""
    bidder = a_bidder(trade_categories=["SWITCHGEARS - LV -415V"])
    assert evaluate(
        bidder, an_rfq(discipline="SWITCHGEARS - LV -415V"), TODAY
    ).scope_fit


def test_expansion_does_not_widen_a_family_to_its_accessories():
    """`disciplines.py` keeps cable trays and glands out of `Cables` on purpose.
    Expanding here must not quietly undo that — an RFQ for cable that returned
    tray fabricators as in-scope is a worse answer than a short one."""
    bidder = a_bidder(trade_categories=["CABLE TRAYS & ACCESSORIES"])
    assert not evaluate(bidder, an_rfq(discipline="Cables"), TODAY).scope_fit


# -- the client approval gap -------------------------------------------------
#
# Derived from `approved_by`, for the same reason `Expired` is derived from
# `prequal_expires_on`: a stored copy is wrong the moment the list is edited.


def test_a_client_approved_bidder_has_no_gap():
    assert missing_client_approval(a_bidder(approved_by=[ADNOC])) is None


def test_holding_both_approvals_is_still_no_gap():
    assert missing_client_approval(a_bidder(approved_by=[ADNOC, ASTRA])) is None


def test_an_astra_only_bidder_names_what_it_holds_instead():
    gap = missing_client_approval(a_bidder(approved_by=[ASTRA]))
    assert gap == (
        "Al Munara Switchgear LLC is not on the ADNOC Approved Vendor List "
        "— approved by Astra only."
    )


def test_a_bidder_with_no_approval_at_all_says_so_differently():
    """One rule, two sentences. "We approved them, the client has not" and
    "nobody has approved them" call for different actions, so they must not
    render identically."""
    gap = missing_client_approval(a_bidder(approved_by=[]))
    assert gap == (
        "Al Munara Switchgear LLC is not on the ADNOC Approved Vendor List "
        "and has no approval recorded."
    )


def test_the_rule_is_absence_of_the_client_not_presence_of_astra():
    """A bidder on some third party's list, and on neither of ours, is caught
    by the same rule — the predicate is 'the client approver is absent', which
    cannot be sidestepped by leaving `approved_by` empty or filling it with
    something else."""
    gap = missing_client_approval(a_bidder(approved_by=["Some Other Operator"]))
    assert gap is not None
    assert "approved by Some Other Operator only." in gap


def test_the_pure_module_does_not_import_a_spreadsheet_library():
    """`bidders.py` promises no I/O. Naming the client approver must not be
    what breaks that promise."""
    import sys

    for module in ("workflow.bidders", "workflow.models.bidder", "openpyxl"):
        sys.modules.pop(module, None)
    importlib.import_module("workflow.bidders")
    assert "openpyxl" not in sys.modules


def test_a_bidder_off_the_client_list_is_cautioned_but_still_eligible():
    """The whole decision this feature turns on. Asserted as `eligible is True`
    directly, not inferred from an empty `blockers` list, because those are two
    different claims and only one of them is the promise made to the user."""
    result = evaluate(a_bidder(approved_by=[ASTRA]), an_rfq(), TODAY)
    assert result.eligible is True
    assert result.blockers == []
    assert result.cautions == [
        "Al Munara Switchgear LLC is not on the ADNOC Approved Vendor List "
        "— approved by Astra only."
    ]


def test_a_bidder_with_no_approval_is_cautioned_but_still_eligible():
    result = evaluate(a_bidder(approved_by=[]), an_rfq(), TODAY)
    assert result.eligible is True
    assert result.blockers == []
    assert any("has no approval recorded." in c for c in result.cautions)


def test_the_cautions_read_prequalification_then_approval_then_scope():
    """Order is fixed, not incidental — the list is rendered in order, and a
    later edit that appends in the wrong place silently reorders a screen."""
    bidder = a_bidder(
        approved_by=[ASTRA],
        prequal_expires_on=date(2026, 9, 1),   # inside the caution window
        trade_categories=["Mechanical"],        # mismatches the rfq's discipline
    )
    result = evaluate(bidder, an_rfq(), TODAY)
    assert len(result.cautions) == 3
    assert "expires on" in result.cautions[0]
    assert "Approved Vendor List" in result.cautions[1]
    assert "Not registered for" in result.cautions[2]


def test_a_blocked_bidder_off_the_client_list_reports_both_separately():
    """The gap never migrates into `blockers`, even when the bidder has real
    ones — otherwise it would start demanding an override_reason."""
    bidder = a_bidder(approved_by=[ASTRA], on_hold=True, hold_reason="NCRs open")
    result = evaluate(bidder, an_rfq(), TODAY)
    assert result.eligible is False
    assert any("NCRs open" in b for b in result.blockers)
    assert not any("Approved Vendor List" in b for b in result.blockers)
    assert any("Approved Vendor List" in c for c in result.cautions)


# -- the client's approved vendor list ----------------------------------------
#
# One master list, because everything the product knows about who ADNOC has
# approved came from one import of one export. `discipline` narrows it when a
# caller wants one product group; it is off by default.


def test_client_approved_returns_everybody_on_the_clients_list():
    switchgear = a_bidder(name="Al Munara", trade_categories=["SWITCHGEARS - LV -415V"])
    valves = a_bidder(name="Northwind", trade_categories=['VALVES - BALL - API 6D'])
    found = client_approved([switchgear, valves])
    assert [b.name for b in found] == ["Al Munara", "Northwind"]


def test_client_approved_excludes_a_bidder_off_the_clients_list():
    """The whole filter, and the reason the function exists rather than the
    screen doing it: a vendor Astra approved is not one the client did."""
    ours = a_bidder(name="Silverdune", approved_by=[ASTRA])
    theirs = a_bidder(name="Al Munara", approved_by=[ADNOC])
    assert [b.name for b in client_approved([ours, theirs])] == ["Al Munara"]


def test_client_approved_orders_by_name():
    """Registry insertion order means nothing to somebody scanning for a
    company, and the AVL's own order is the order of its first mention."""
    roster = [
        a_bidder(name="zenith Piping"),
        a_bidder(name="Al Munara"),
        a_bidder(name="Marjan"),
    ]
    found = client_approved(roster)
    assert [b.name for b in found] == ["Al Munara", "Marjan", "zenith Piping"]


def test_client_approved_narrows_to_one_product_group_when_asked():
    switchgear = a_bidder(name="Al Munara", trade_categories=["SWITCHGEARS - LV -415V"])
    valves = a_bidder(name="Northwind", trade_categories=['VALVES - BALL - API 6D'])
    found = client_approved([switchgear, valves], "SWITCHGEARS - LV -415V")
    assert [b.name for b in found] == ["Al Munara"]


def test_narrowing_matches_the_whole_label_not_a_substring():
    """Same rule as `_matches_scope`, and for the same reason: "VALVES - BALL"
    answering for "VALVES - BALL - API 6D" is a different trade whose bid gets
    thrown out at TBE."""
    near = a_bidder(name="Northwind", trade_categories=["VALVES - BALL"])
    assert client_approved([near], 'VALVES - BALL - API 6D - UP TO 12"') == []


def test_narrowing_ignores_case_and_padding():
    """A re-exported AVL pads and recapitalises cells; a discipline is typed by
    hand. Neither should decide whether a vendor appears."""
    bidder = a_bidder(name="Al Munara", trade_categories=["  switchgears - LV -415V "])
    found = client_approved([bidder], "SWITCHGEARS - LV -415V")
    assert [b.name for b in found] == ["Al Munara"]


def test_a_blank_discipline_narrows_nothing():
    """Not "matches nobody". The master list is the default answer, so an
    absent filter has to leave it whole."""
    bidder = a_bidder(name="Al Munara", trade_categories=["SWITCHGEARS - LV -415V"])
    assert [b.name for b in client_approved([bidder], "")] == ["Al Munara"]
    assert [b.name for b in client_approved([bidder], "   ")] == ["Al Munara"]
    assert [b.name for b in client_approved([bidder], None)] == ["Al Munara"]
