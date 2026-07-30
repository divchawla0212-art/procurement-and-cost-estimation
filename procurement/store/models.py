import hashlib
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
    classified_with: str | None = None
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
    # VendorBid dump from the quotation, with any human overrides already
    # applied - this is the resolved value, not the raw extraction. The raw
    # value each override replaced is kept in `overrides[].extracted_value`.
    commercial: dict | None = None
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


def fact_id_for(doc_id: str, parameter: str) -> str:
    """Stable across re-extraction: same document + same parameter -> same id.
    That is what lets a human override on technical[<id>].value survive a
    re-run, since list order is not stable and index paths are forbidden."""
    key = f"{doc_id}:{parameter.strip().lower()}"
    return "f-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


def deviation_id_for(doc_id: str, clause_ref: str | None, statement: str) -> str:
    key = f"{doc_id}:{(clause_ref or statement).strip().lower()}"
    return "v-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


class FactRecord(BaseModel):
    """One technical parameter as stated by a vendor document. Values are kept
    exactly as written, with the unit alongside — conversion and comparison
    happen in phase 3, in Python, never in the model."""
    fact_id: str
    parameter: str
    value: str | float | None = None
    unit: str | None = None
    verbatim: str | None = None      # the source sentence, for provenance
    doc_id: str


class DeviationRecord(BaseModel):
    """One entry from a vendor deviation form."""
    deviation_id: str
    clause_ref: str | None = None
    statement: str
    disposition: str = "noted"       # comply | deviate | noted
    doc_id: str
