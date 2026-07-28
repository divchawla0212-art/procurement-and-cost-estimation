from typing import Any
from pydantic import BaseModel


class Override(BaseModel):
    """A human correction. Survives re-extraction; `conflict` is set when a
    later extraction disagreed with `extracted_value`."""
    field_path: str
    value: Any
    extracted_value: Any = None
    author: str
    at: str
    reason: str
    conflict: bool = False


class DocumentRecord(BaseModel):
    doc_id: str
    path: str                       # relative to the project dir, posix separators
    vendor: str | None = None       # None for RFQ documents
    doc_class: str = "unclassified"
    classified_by: str | None = None
    revision_label: str | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    content_sha256: str
    text_source: str | None = None
    extraction_status: str = "pending"   # pending | ok | failed | skipped
    notes: str | None = None
    extracted_at: str | None = None
    extractor: str | None = None
    prompt_version: str | None = None


class VendorFacts(BaseModel):
    vendor: str
    commercial: dict | None = None      # BidExtraction dump, from the quotation
    normalized: dict | None = None      # NormalizedBid dump
    technical: list[dict] = []          # populated in phase 2
    deviations: list[dict] = []         # populated in phase 2
    overrides: list[Override] = []


class Event(BaseModel):
    at: str
    run_id: str
    actor: str                          # "pipeline" or a username
    action: str
    target: str | None = None
    detail: dict = {}
