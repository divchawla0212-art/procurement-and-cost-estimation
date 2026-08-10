"""The A/B's noise floor: how far the control vendor drifted between arms.

MKON's datasheet is .xlsx, read by openpyxl in every arm, so its text is
byte-identical no matter which PDF reader an arm names. Every cell of its that
changes verdict between arms therefore changed for reasons unrelated to any
parser -- that count is the experiment's noise floor, and a difference between
arms smaller than it means nothing.

Key-free: pure arithmetic over the harness's own result dicts.
"""
from tools.parser_ab import CONTROL_VENDOR, _control_drift


def _arm(**verdicts):
    counts = {"review": 0, "unanswered": 0, "pass": 0, "fail": 0,
              "deviation": 0}
    counts.update(verdicts)
    counts["needs_human"] = counts["review"] + counts["unanswered"]
    return {"flags": {"by_vendor": {CONTROL_VENDOR: counts}}}


def test_no_drift_when_every_verdict_matches():
    arm = _arm(review=122, unanswered=27, **{"pass": 51})
    assert _control_drift([arm, arm]) == 0


def test_a_single_arm_cannot_drift():
    assert _control_drift([_arm(review=5)]) == 0
    assert _control_drift([]) == 0


def test_drift_is_counted_even_when_the_needs_human_total_is_unchanged():
    """The failure this function was rewritten to catch.

    Measured on the real run: MKON scored needs_human=149 in both the pdftotext
    and the pypdf arm, so a control watching only that total reported "no
    drift". Underneath, 25 cells had swapped between `review` and `unanswered`
    and two `pass` cells had become `fail` -- 27 cells in total, on identical
    input. That is larger than any difference the experiment found between
    arms, so reporting it as zero would have turned noise into a finding.
    """
    pdftotext = _arm(review=122, unanswered=27, fail=0, **{"pass": 51})
    pypdf = _arm(review=97, unanswered=52, fail=2, **{"pass": 49})

    both = pdftotext["flags"]["by_vendor"][CONTROL_VENDOR]["needs_human"]
    assert both == pypdf["flags"]["by_vendor"][CONTROL_VENDOR]["needs_human"]

    assert _control_drift([pdftotext, pypdf]) == 27


def test_one_cell_moving_verdict_counts_once_not_twice():
    """A cell changing verdict decrements one count and increments another, so
    the L1 distance double-counts it."""
    before = _arm(review=10, unanswered=0)
    after = _arm(review=9, unanswered=1)
    assert _control_drift([before, after]) == 1


def test_the_worst_pair_is_reported_not_the_first():
    """With three arms the floor is the largest disagreement anywhere, not the
    one that happens to come first."""
    a = _arm(review=10)
    b = _arm(review=9, unanswered=1)
    c = _arm(review=4, unanswered=6)
    assert _control_drift([a, b, c]) == 6
