"""What state a clarification query is in, and what number the next one gets.

A pure module: no store, no I/O, no clock. The same arrangement as
`workflow/bidders.py`, and for the same reason — the answer is a function of the
record's own fields, so it is assertable from both sides without building a
store or freezing a clock.
"""
import re

from workflow.models.clarification import Addendum, ClarificationQuery, QueryCategory

_CATEGORY_PREFIX: dict[str, str] = {"Technical": "TQ", "Commercial": "CQ"}
_QUERY_WIDTH = 3
_ADDENDUM_WIDTH = 2


def state(query: ClarificationQuery) -> str:
    """`withdrawn_at` is checked first on purpose.

    Withdrawal is the later act and the one that takes a query out of the round,
    so a query answered and then withdrawn reads as Withdrawn rather than as
    Answered. Reversing these two lines would leave the exit gate counting a
    dead question as satisfied.
    """
    if query.withdrawn_at is not None:
        return "Withdrawn"
    if query.answer is not None:
        return "Answered"
    return "Open"


def is_open(query: ClarificationQuery) -> bool:
    return state(query) == "Open"


def is_circulated(query: ClarificationQuery) -> bool:
    """Silence means circulated. There is no third state, and no `circulate`
    boolean — a boolean would make a restricted answer indistinguishable from an
    oversight."""
    return query.restricted_reason is None


def is_draft(addendum: Addendum) -> bool:
    return addendum.issued_at is None


def _highest(numbers: list[str], prefix: str) -> int:
    """One past the highest, never one past the count.

    Numbers are never reused: a withdrawn query keeps its number and stays in
    the register, so a count-based sequence would hand a bidder a number that
    another bidder was already quoted in writing.

    A number that does not parse is ignored rather than fatal. It is not
    evidence that the sequence restarted, and a register that cannot be added to
    because one legacy row is odd is worse than one that skips it.
    """
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    found = [int(m.group(1)) for n in numbers if (m := pattern.match(n))]
    return max(found, default=0)


def next_query_number(
    existing: list[ClarificationQuery], category: QueryCategory
) -> str:
    prefix = _CATEGORY_PREFIX[category]
    same = [q.number for q in existing if q.category == category]
    return f"{prefix}-{_highest(same, prefix) + 1:0{_QUERY_WIDTH}d}"


def next_addendum_number(existing: list[Addendum]) -> str:
    """Two digits rather than three: a tender that runs to a hundred addenda has
    a problem no padding fixes, and the narrower field is what buyers write on
    the documents."""
    highest = _highest([a.number for a in existing], "ADD")
    return f"ADD-{highest + 1:0{_ADDENDUM_WIDTH}d}"
