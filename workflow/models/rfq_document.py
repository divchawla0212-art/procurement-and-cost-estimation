"""A real document against an RFQ — bytes on disk, and the record that names
them.

**One collection serves both halves of the enquiry.** `submitted_by_vendor_id`
is `None` for a document the contractor issues (the enquiry package) and a
vendor id for a document a bidder returns with their bid. Two collections would
mean two sets of rules for the same file, and the adequacy work reads exactly
these rows for both halves.

The bytes live in `workflow/doc_store.py` under a content-addressed layout;
what is stored here is the record. Two records may legitimately point at one
blob — the same bytes uploaded twice — which is why deleting a record deletes
its blob only when no other record still references it.
"""
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class EligibilityCategory(str, Enum):
    """What a bid document is, for the eligibility checklist.

    Defined here rather than beside that checklist because `RfqDocument`
    depends on it and this is the module that gets imported first; the
    checklist imports this enum rather than redefining it, so there is one
    vocabulary and not two that drift.

    The mandatory / conditional distinction between these members is a rule
    about a *submission*, not a property of the category, so it lives with the
    checklist and deliberately not here.
    """

    TECHNICAL_OFFER = "Technical offer"
    COMMERCIAL_OFFER = "Commercial offer"
    COMPLIANCE_SHEET = "Compliance / deviation sheet"
    CLIENT_DATASHEET = "Filled client datasheet"
    TBE_SHEET = "Technical bid evaluation sheet"
    TECHNICAL_DATASHEET = "Technical datasheet"
    DRAWINGS = "Drawings"
    DOCUMENTS = "Documents"
    CATALOGUES = "Catalogues and brochures"


def new_rfq_document_id() -> str:
    return f"rdoc_{uuid4().hex[:8]}"


class RfqDocument(BaseModel):
    """One uploaded file.

    `filename` is the leaf as uploaded and `rel_path` is where it sat inside
    the folder or archive it arrived in — `enquiry/drawings/sld.dwg` against a
    leaf of `sld.dwg`. They are two different facts: the leaf is what the blob
    is stored under, and the path is what tells a reader how the uploader had
    organised it. A plain single-file upload has the filename in both.

    Neither is the blob's storage path. That is derived from `sha256` and
    `filename` through `doc_store.blob_ref`, so nothing here has to be trusted
    as a path.

    `category` is optional because a buyer dragging in forty files has not
    classified them yet, and a category guessed on their behalf would be
    indistinguishable on screen from one somebody chose.
    """

    id: str = Field(default_factory=new_rfq_document_id)
    rfq_id: str
    filename: str
    rel_path: str
    sha256: str
    size_bytes: int
    content_type: str | None = None
    category: EligibilityCategory | None = None
    uploaded_by: str
    uploaded_at: str
    #: `None` means the contractor issued it. A vendor id means a bidder
    #: returned it.
    submitted_by_vendor_id: str | None = None
