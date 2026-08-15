"""The draft shortlist an **item** owns, before any RFQ exists.

A buyer assembles a vendor selection on the item screen out of the four-source
pool, and it has to still be there after a refresh, a sign-out and a restart.
Carrying it in browser navigation state was the cheap alternative and it is
wrong: this API restarts on every code change and clears sessions when it does,
so a twenty-five vendor selection would go with it.

It is a **draft**, not a shortlist. Nothing here invites anybody. Turning a
draft into invitations is an attributed act with its own per-vendor guards, and
it belongs to the RFQ that eventually covers the item.
"""
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

# The four chips on the item screen's vendor pool.
#
# Deliberately *not* `VendorListSource`, which the two enums otherwise resemble:
# that one's `Client` is the uploaded export's name for the client's own list,
# while this field names the chip the buyer was looking at. Sharing the alias
# would let `Client` in here, where no screen renders it and no reader could say
# what it meant.
DraftShortlistSource = Literal["ADNOC", "Astra", "Manual", "Suggested"]


def new_draft_shortlist_entry_id() -> str:
    return f"dse_{uuid4().hex[:8]}"


class DraftShortlistEntry(BaseModel):
    """One vendor a buyer has picked against an item.

    `vendor_id` is set for a registry row and `None` for a curated one — a
    company somebody typed in or a model suggested has no registry row, and
    attaching one by matching the name is the defect this repository has
    recorded twice.

    `source` records **where the buyer found them**, which is a different fact
    from who approved them and is not derivable afterwards: a registry vendor's
    approvals can be corrected, and a curated entry can be deleted from the
    item's list entirely. Recording it at the moment of the pick is the only way
    to keep it true.

    There is deliberately no `approved_by`, no `client_approved` and no
    `prequal_status`. Those are read live through `vendor_id`, the same rule the
    shortlist row keeps — a stored copy is wrong the moment the registry is
    corrected, and correcting it is the common case.
    """

    id: str = Field(default_factory=new_draft_shortlist_entry_id)
    item_id: str
    vendor_id: str | None = None
    vendor_name: str
    source: DraftShortlistSource
    added_by: str
    added_at: str
