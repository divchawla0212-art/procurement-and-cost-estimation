from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

ReceiptState = Literal["received", "not_received", "unreadable"]


def new_bid_id() -> str:
    return f"bid_{uuid4().hex[:8]}"


class Bid(BaseModel):
    id: str = Field(default_factory=new_bid_id)
    rfq_id: str
    vendor_name: str
    received_at: datetime
    headline_price_aed: int
    currency: str = "AED"


class VdrlReceipt(BaseModel):
    """Receipt of one VDRL line item for one bid. `unreadable` is deliberately
    distinct from `not_received` — a corrupt file is a delivery failure to
    chase, not a refusal to submit."""

    bid_id: str
    doc_code: str
    state: ReceiptState
    revision: str | None = None


class BidShortlist(BaseModel):
    """Which bids go forward to evaluation, chosen by a named person who gave a
    reason. Selection narrows what is evaluated; the bids not selected stay in
    the register."""

    rfq_id: str
    selected_bid_ids: list[str]
    selected_by: str
    rationale: str
    at: datetime
