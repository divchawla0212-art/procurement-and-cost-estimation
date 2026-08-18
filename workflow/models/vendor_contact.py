"""Where to send an enquiry, for one vendor."""
from datetime import datetime

from pydantic import BaseModel


class VendorContact(BaseModel):
    """One vendor's addresses, as a supplied contact sheet gave them.

    **No `vendor_id`, and no `id`.** The folded vendor name is the identity.
    The sheet this comes from carries no vendor number, so a registry link
    could only be made by comparing names — and this repository has recorded
    name matching as a shipped defect twice. A wrong match here mails a tender
    to the wrong company while rendering identically to a right one, so there
    is deliberately nothing here to match with.

    `vendor_name` is kept as the sheet wrote it, so a row can be read back
    against its source document — the same rule `ItemVendorEntry` keeps.

    `emails` is **never empty**. A row with a vendor and no address is refused
    at parse, which is what lets the read side answer either a list or `None`
    and never have to distinguish "a contact with no way to reach them" from
    "no contact at all".
    """

    vendor_name: str
    emails: list[str]
    source_document: str
    uploaded_by: str
    uploaded_at: datetime
