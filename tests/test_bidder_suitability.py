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
from datetime import date

from workflow.bidders import Suitability, effective_prequal, evaluate
from workflow.models.bidder import Bidder
from workflow.models.rfq import RfqRecord

TODAY = date(2026, 8, 13)


def a_bidder(**overrides) -> Bidder:
    defaults = dict(
        name="Al Munara Switchgear LLC",
        country="United Arab Emirates",
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
