from datetime import date
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

# There is deliberately no "Expired" member. Expiry is a function of
# `prequal_expires_on` and the date you ask on, so storing it would mean
# storing a value that is wrong the day after it is written — and staying
# honest would need a sweep job nobody has written. `workflow.bidders`
# computes it instead. This is the same choice `_item_payload` makes for the
# live-period warning: derive at the boundary, never persist.
PrequalStatus = Literal["Approved", "Under review", "Suspended", "Not qualified"]


def new_bidder_id() -> str:
    return f"bdr_{uuid4().hex[:8]}"


class Bidder(BaseModel):
    """A company that may be invited to bid, held once for the whole
    organisation rather than retyped per RFQ.

    Prequalification is a company-level fact with a validity period, which is
    why it lives here and not on `ShortlistEntry`. What a shortlist entry keeps
    is the *snapshot* — what the registry said at the moment somebody decided
    to invite this bidder — and that is a different fact from what the registry
    says today.

    `past_awards` is history from before this platform and is deliberately not
    conflated with activity inside it. In-system participation is counted, not
    remembered: the roster derives `invited_count` from the shortlist entries
    that reference the bidder. One number is somebody's recollection and the
    other is a fact this system holds, and averaging them would produce
    neither.
    """

    id: str = Field(default_factory=new_bidder_id)
    name: str
    # Optional because the one bulk source of bidders — an ADNOC Approved
    # Vendor List export — does not carry it. The only country in that sheet
    # is the *manufacturer's*, and copying it across would record a UAE
    # supplier as Indian because their principal is. Unknown is unknown.
    country: str | None = None
    currency: str = "AED"
    # Which organisations have approved this bidder: an ADNOC AVL listing, an
    # internal one, or both. A list rather than a flag because the same
    # company is commonly on several, and which one matters depends on whose
    # project the RFQ is for.
    approved_by: list[str] = Field(default_factory=list)
    # Matched against an RFQ's `discipline` and `package`, whole-string and
    # case-folded. See `workflow.bidders.evaluate` for why not substrings.
    trade_categories: list[str] = Field(default_factory=list)
    prequal_status: PrequalStatus = "Under review"
    prequal_expires_on: date | None = None
    on_hold: bool = False
    hold_reason: str | None = None
    turnover_band: str | None = None
    performance_rating: float | None = None
    past_awards: int = 0
    # The OEMs this vendor is listed as representing. Often the real
    # differentiator between two suppliers of the same product group.
    represented_manufacturers: list[str] = Field(default_factory=list)
    notes: str | None = None
