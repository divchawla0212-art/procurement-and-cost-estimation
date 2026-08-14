from datetime import date, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

ProjectStatus = Literal["Active", "On Hold", "Closed"]


def new_project_id() -> str:
    return f"prj_{uuid4().hex[:8]}"


class Project(BaseModel):
    """A project is the top-level container. `name` is user-editable at any
    time; `id` never changes, and every reference elsewhere binds to `id`."""

    id: str = Field(default_factory=new_project_id)
    name: str
    code: str
    client: str
    location: str
    live_period_start: date
    live_period_end: date
    currency: str = "AED"
    status: ProjectStatus = "Active"


def new_item_id() -> str:
    return f"itm_{uuid4().hex[:8]}"


class Item(BaseModel):
    """A procurement item within a project. `item_type` references the shared
    catalogue so cross-project questions do not rely on free-text matching."""

    id: str = Field(default_factory=new_item_id)
    project_id: str
    item_type: str
    description: str
    qty: float
    uom: str
    discipline: str
    estimated_value_aed: int
    required_on_site: date | None = None
    is_long_lead: bool = False


def new_item_vendor_entry_id() -> str:
    return f"ive_{uuid4().hex[:8]}"


#: Which of the two uploads an entry came from. The client's Approved Vendor
#: List, and Astra's subset of it. A literal rather than free text because the
#: source decides which list a re-upload replaces, and a typo would quietly
#: create a third one.
VendorListSource = Literal["Client", "Astra"]


class ItemVendorEntry(BaseModel):
    """One vendor on one item's list, from one of the two uploads.

    `vendor_id` is the export's own vendor number as `bdr_<number>` — the key
    `avl_import` already assigns, and the same one the registry was built with
    — so matching is an exact lookup rather than a name comparison. `None`
    means the export named a vendor the registry does not hold: a real,
    reportable state, and not the same as "unapproved".

    `vendor_name` is stored as the export wrote it, so a row can be read back
    against its source document.

    There is deliberately **no** `approved_by` and no copy of the linked
    bidder's prequalification: those are read live through `vendor_id`, the
    same rule `client_approved` keeps on a shortlist row. A copy would be wrong
    the moment the registry is corrected, and correcting it is the common case.

    `trade_categories` holds only the groups relevant to the item this entry
    belongs to, not the vendor's full list — a cable supplier who also sells
    valves is on a cable item's list *for cables*.
    """

    id: str = Field(default_factory=new_item_vendor_entry_id)
    item_id: str
    source: VendorListSource
    vendor_id: str | None
    vendor_name: str
    trade_categories: list[str] = Field(default_factory=list)
    uploaded_by: str
    uploaded_at: datetime
    source_document: str
