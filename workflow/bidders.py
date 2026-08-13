"""Whether a bidder may be invited to a given RFQ, and why not.

A pure module: no store, no I/O, no clock. `as_of` is a parameter because both
expiry boundaries have to be assertable from both sides, and because the answer
genuinely depends on when you ask — which is exactly why it is computed here
rather than stored on the `Bidder`.

The distinction the rules turn on:

- A **blocker** is somebody's explicit refusal — a hold, a lapsed or withheld
  prequalification. Overriding one has to be a deliberate, attributed act, so
  the store refuses a linked shortlist entry that carries no `override_reason`.
- A **caution** is a fact worth seeing that decides nothing. A bidder moving
  into a new discipline is a legitimate invitation, and `ShortlistEntry`
  already has `scope_code_fit` to record that the mismatch was known.

Every blocker names the whole criterion rather than the nearer half of it —
`gates.py` states that rule, and the phase 1 plan shipped a message that broke
it. "Not approved" leaves a reader to guess whether renewal is the remedy; the
date it lapsed does not.
"""
from datetime import date, timedelta

from pydantic import BaseModel, Field

from workflow.models.bidder import Bidder
from workflow.models.rfq import RfqRecord

# How long before a lapse is worth mentioning. Long enough that a renewal can
# realistically be chased before the RFQ issues.
EXPIRY_CAUTION_DAYS = 30

# One sentence per state rather than one template with the status dropped into
# it. A template produced "Prequalification for X is not qualified, not
# approved", which reads as a stutter and buries what the reader has to do
# about it. Each of these says what is true and what would change it.
_STATUS_BLOCKERS = {
    "Under review": (
        "Prequalification for {name} is still under review, so they have not "
        "been approved to bid yet."
    ),
    "Suspended": (
        "{name} is suspended and cannot be invited until the suspension is lifted."
    ),
    "Not qualified": (
        "{name} was declined at prequalification and is not approved to bid."
    ),
}


class Suitability(BaseModel):
    """`eligible` is `not blockers`, spelled out so a caller does not have to
    know that. `effective_prequal` may be "Expired", which no stored status
    ever is."""

    eligible: bool
    scope_fit: bool
    effective_prequal: str
    blockers: list[str] = Field(default_factory=list)
    cautions: list[str] = Field(default_factory=list)


def effective_prequal(bidder: Bidder, as_of: date) -> str:
    """The stored status, unless an approval has run out.

    Expiry only rewrites `Approved`. A suspension that also happens to carry a
    stale date is still a suspension: reporting it as "Expired" would read as
    though renewing were the remedy, when the remedy is lifting the suspension.

    A missing date is open-ended, not expired — absence of an end is not an end.
    Expiry is `<`, not `<=`: an approval valid *through* its stated date is
    still valid on that date.
    """
    if bidder.prequal_status != "Approved":
        return bidder.prequal_status
    if bidder.prequal_expires_on is not None and bidder.prequal_expires_on < as_of:
        return "Expired"
    return "Approved"


def _matches_scope(bidder: Bidder, rfq: RfqRecord) -> bool:
    """Whole-string, case-folded, whitespace-trimmed.

    Substring matching is deliberately not used. It is how `classify._RULES`
    shipped false matches, and here it would make "Electric" answer for
    "Electrical Testing" — a different trade, and one whose bid would be
    thrown out at TBE.
    """
    wanted = {rfq.discipline.strip().casefold(), rfq.package.strip().casefold()}
    return any(c.strip().casefold() in wanted for c in bidder.trade_categories)


def evaluate(bidder: Bidder, rfq: RfqRecord, as_of: date) -> Suitability:
    blockers: list[str] = []
    cautions: list[str] = []

    if bidder.on_hold:
        blockers.append(
            f"{bidder.name} is on hold: {bidder.hold_reason or 'no reason recorded'}."
        )

    status = effective_prequal(bidder, as_of)
    if status == "Expired":
        blockers.append(
            f"Prequalification lapsed on {bidder.prequal_expires_on.isoformat()} "
            f"and must be renewed before {bidder.name} can be invited."
        )
    elif status != "Approved":
        blockers.append(_STATUS_BLOCKERS[status].format(name=bidder.name))
    elif bidder.prequal_expires_on is not None:
        # Only worth saying while it is still true. An already-lapsed approval
        # has a blocker saying so; repeating it as a caution is noise.
        if bidder.prequal_expires_on <= as_of + timedelta(days=EXPIRY_CAUTION_DAYS):
            cautions.append(
                f"Prequalification expires on "
                f"{bidder.prequal_expires_on.isoformat()}, within "
                f"{EXPIRY_CAUTION_DAYS} days — renew it before bids are due."
            )

    scope_fit = _matches_scope(bidder, rfq)
    if not scope_fit:
        # Short on purpose: on a registry of any size this sentence appears
        # against most candidates, and a paragraph repeated twelve times is
        # what stops the blockers being read at all.
        cautions.append(
            f"Not registered for {rfq.discipline} — inviting them records a "
            f"scope mismatch."
        )

    return Suitability(
        eligible=not blockers,
        scope_fit=scope_fit,
        effective_prequal=status,
        blockers=blockers,
        cautions=cautions,
    )
