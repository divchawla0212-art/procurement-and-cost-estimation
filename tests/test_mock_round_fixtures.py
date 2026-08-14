"""The mock clarification-round fixtures, and the one decision behind a resume.

`tools/mock_clarification_round.py` is an infrastructure check played over
HTTP against a live app, and testing *that* here would mean mocking away the
very thing it exists to exercise. Two things about it are nonetheless
answerable on disk for nothing, and both fail expensively when left to a live
run:

**The fixtures.** A typo in one surfaces nine calls into a round, on a running
server, as a 422 naming a field rather than the file that got it wrong.

**`_already_done`.** Pure, and the one piece of judgement in `--resume`. It is
imported rather than reimplemented, because a copy here would agree with a
wrong original.

Three tests carry real weight. `test_a_registered_vendor_is_scoped_to_the_rfqs_own_discipline`
— trade categories match whole-string and case-folded, so a near miss (a
plural, a doubled space, a group description that reads right but is not in
`workflow/disciplines.py`) renders as a shortlist of vendors who do not do this
work, and looks exactly like one that matches.
`test_no_invented_vendor_claims_the_clients_approval` and
`test_a_withdrawn_query_is_not_answered_again` both guard defects that shipped;
each was verified by reinstating the defect, not by reading the assertion.
"""
import json
import pathlib

import pytest

from tools.mock_clarification_round import _already_done
from workflow import disciplines
from workflow.bidders import CLIENT_APPROVER
from workflow.models.bidder import Bidder

FIXTURES = sorted(
    (pathlib.Path(__file__).resolve().parents[1] / "tools").glob(
        "mock_clarification_round*.json"
    )
)

# The actions `play` knows. Kept here rather than imported from the tool so
# that deleting a branch there fails this test instead of silently making a
# fixture step unreachable.
ACTIONS = {
    "raise_query",
    "answer_query",
    "withdraw_query",
    "draft_addendum",
    "issue_addendum",
    "expect_gate",
    "transition",
}


def _load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_there_are_fixtures_to_check():
    # Guards the glob itself: a renamed file would otherwise turn every
    # parametrised test below into zero tests and a green run.
    assert len(FIXTURES) >= 2


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
class TestFixture:
    def test_it_parses_and_has_the_three_sections(self, path):
        fixture = _load(path)
        assert {"rfq", "vendors", "exchange"} <= set(fixture)

    def test_every_step_names_an_action_the_tool_plays(self, path):
        unknown = {
            s["action"] for s in _load(path)["exchange"] if s["action"] not in ACTIONS
        }
        assert not unknown, f"{path.name} uses actions the tool does not play: {unknown}"

    def test_step_refs_are_unique(self, path):
        # `play` keys its query and addendum lookups by ref, so a duplicate
        # silently makes a later step answer the wrong question.
        refs = [s["ref"] for s in _load(path)["exchange"]]
        assert len(refs) == len(set(refs))

    def test_every_step_refers_backwards_only(self, path):
        # A step naming a ref raised later would KeyError mid-round, after it
        # had already written to the store.
        seen: set[str] = set()
        for step in _load(path)["exchange"]:
            for key in ("query", "addendum"):
                if key in step:
                    assert step[key] in seen, f"{step['ref']} names {step[key]} too early"
            for earlier in step.get("arising_from", []):
                assert earlier in seen, f"{step['ref']} arises from {earlier} too early"
            seen.add(step["ref"])

    def test_the_round_needs_three_vendors_and_every_slot_is_used(self, path):
        fixture = _load(path)
        slots = {v["slot"] for v in fixture["vendors"]}
        assert len(slots) == len(fixture["vendors"]) == 3
        asked = {s["vendor"] for s in fixture["exchange"] if s["action"] == "raise_query"}
        assert asked == slots, f"{path.name}: slots with no query: {slots - asked}"

    def test_a_registry_block_is_a_valid_bidder_that_says_it_is_invented(self, path):
        for vendor in _load(path)["vendors"]:
            registry = vendor.get("registry")
            if registry is None:
                continue
            bidder = Bidder(**registry)
            assert bidder.name == vendor["name"], (
                "the script matches an existing bidder by name, so the two "
                "spellings must agree or a second run registers a duplicate"
            )
            # The label has to reach the product, not just this file's comment
            # block: a row carrying a turnover band and a rating reads as a
            # record of a real company.
            assert bidder.notes and "invented" in bidder.notes.casefold()

    def test_no_invented_vendor_claims_the_clients_approval(self, path):
        """The one this file exists for.

        A `registry` block describes a company that does not exist, so it may
        state an internal qualification and may never state the client's. An
        ADNOC approval is a fact about a row in ADNOC's export; asserting it
        here produces a shortlist that reads *approved by ADNOC* for a company
        ADNOC has never heard of, and nothing on the screen distinguishes that
        from a vendor genuinely on the list. This shipped once — two of the
        genset cast carried it — and it is the reason the real vendors moved
        to `also_invited`.
        """
        for vendor in _load(path)["vendors"]:
            registry = vendor.get("registry")
            if registry is None:
                continue
            assert CLIENT_APPROVER not in registry.get("approved_by", []), (
                f"{vendor['name']} is invented and claims {CLIENT_APPROVER} "
                f"approval. Approvals that are read rather than asserted belong "
                f"in `also_invited`, which names companies the export holds."
            )

    def test_an_also_invited_vendor_is_only_a_name(self, path):
        """No profile, and no query.

        These are real companies. A turnover band or a rating written beside
        one would be this file inventing a fact about somebody real, and a
        query attributed to one would be this file inventing something they
        said — which is worse, because the round these questions are modelled
        on actually happened.
        """
        fixture = _load(path)
        also = fixture.get("also_invited", [])
        assert all(isinstance(n, str) and n.strip() for n in also)
        assert len(set(also)) == len(also), "the same company invited twice"
        slots = {v["name"] for v in fixture["vendors"]}
        assert not (set(also) & slots), (
            "a company cannot both raise the round's questions and be one of "
            "the vendors that only receives it"
        )

    def test_a_registered_vendor_is_scoped_to_the_rfqs_own_discipline(self, path):
        fixture = _load(path)
        discipline = fixture["rfq"]["discipline"]
        for vendor in fixture["vendors"]:
            registry = vendor.get("registry")
            if registry is None:
                continue
            assert disciplines.covering(discipline, registry["trade_categories"]), (
                f"{vendor['name']} is registered for "
                f"{registry['trade_categories']}, none of which covers "
                f"{discipline!r}. Whole-string and case-folded — a near miss "
                f"here shortlists vendors who do not do this work."
            )


class TestResumeSkipsWhatIsAlreadyStored:
    """`_already_done` decides what a `--resume` replays.

    It is pure and it reads state rather than counting steps, so it is worth
    testing here even though the tool around it is not: getting it wrong does
    not fail loudly, it re-sends a mutation the server then refuses, and the
    round dies somewhere unrelated to the mistake.
    """

    def test_an_unraised_query_is_not_skipped(self):
        assert not _already_done({"action": "raise_query", "ref": "Q1"}, {}, {})

    def test_a_raised_query_is_skipped(self):
        step = {"action": "raise_query", "ref": "Q1"}
        assert _already_done(step, {"Q1": {"number": "TQ-001"}}, {})

    def test_an_open_query_still_needs_its_answer(self):
        step = {"action": "answer_query", "query": "Q1"}
        assert not _already_done(step, {"Q1": {"state": "Open"}}, {})

    def test_an_answered_query_is_not_answered_twice(self):
        step = {"action": "answer_query", "query": "Q1"}
        assert _already_done(step, {"Q1": {"state": "Answered"}}, {})

    def test_a_withdrawn_query_is_not_answered_again(self):
        """The one that shipped broken.

        The fixture answers TQ-004 and then withdraws it. Withdrawal is
        checked before an answer, so the stored query reads **Withdrawn**, not
        Answered — and a check written for "Answered" sent the answer a second
        time, which the server refuses with a 409. A resume of a completed
        round died on the single query the round withdraws.
        """
        step = {"action": "answer_query", "query": "Q6"}
        assert _already_done(step, {"Q6": {"state": "Withdrawn"}}, {})

    def test_a_query_the_resume_never_matched_is_not_assumed_done(self):
        # Absent from the map means the register did not hold it, which is the
        # opposite of done.
        assert not _already_done({"action": "answer_query", "query": "Q9"}, {}, {})

    def test_withdrawal_is_skipped_only_once_it_has_happened(self):
        step = {"action": "withdraw_query", "query": "Q6"}
        assert not _already_done(step, {"Q6": {"state": "Answered"}}, {})
        assert _already_done(step, {"Q6": {"state": "Withdrawn"}}, {})

    def test_a_draft_addendum_is_issued_but_an_issued_one_is_not(self):
        step = {"action": "issue_addendum", "addendum": "ADD1"}
        assert not _already_done(step, {}, {"ADD1": {"draft": True}})
        assert _already_done(step, {}, {"ADD1": {"draft": False}})

    @pytest.mark.parametrize("action", ["expect_gate", "transition"])
    def test_a_reading_is_never_skipped_here(self, action):
        # Gates are re-read on purpose, and a transition decides for itself by
        # comparing the RFQ's stage — neither is knowable from the two maps.
        assert not _already_done({"action": action, "ref": "G1"}, {}, {})
