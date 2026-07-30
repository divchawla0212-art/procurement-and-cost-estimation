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
    # Fingerprint of the `auto` requirement vocabulary this document's facts
    # were extracted under. Part of the datasheet cache key: the vocabulary is
    # an input to the tech_facts_v1 prompt, so a requirement edit that changes
    # it must re-ask the datasheets exactly once. None for every other class.
    vocabulary_sha: str | None = None


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
    """Stable across re-extraction for a given (document, clause_ref, statement) triple.
    Both clause_ref and statement participate in the key. Trade-off: if the model
    rewords a statement on later extraction, the id shifts and an override orphans.
    That is strictly better than an override silently retargeting the wrong row."""
    key = f"{doc_id}:{(clause_ref or '').strip().lower()}:{statement.strip().lower()}"
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


def req_id_for(source_doc_id: str, clause_ref: str) -> str:
    """Stable across re-extraction: same document + same printed clause ref ->
    same id. That is what lets an override on requirements[<id>].value survive
    a re-run, since list order is not stable and index paths are forbidden."""
    key = f"{source_doc_id}:{(clause_ref or '').strip().lower()}"
    return "r-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


def amendment_id_for(source_doc_id: str, clause_ref: str | None, text: str) -> str:
    """All three participate. Clause ref alone collides whenever one meeting
    changes the same clause twice; text alone collides across documents."""
    key = f"{source_doc_id}:{(clause_ref or '').strip().lower()}:{text.strip().lower()}"
    return "a-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


class RequirementRecord(BaseModel):
    """One clause of the RFQ, with amendments already applied.

    `base_body` holds the as-extracted body whenever an amendment changed it,
    so re-applying amendments is idempotent across runs and withdrawing the
    MOM restores the original clause exactly.
    """
    req_id: str
    clause_ref: str
    text: str
    category: str = "technical"      # technical|commercial|documentation|testing|codes
    checkability: str = "judgement"  # auto|judgement
    parameter: str | None = None     # auto only
    operator: str | None = None      # >= | <= | == | in
    value: str | float | list | None = None
    unit: str | None = None
    source_doc_id: str
    amended_by: str | None = None    # amendment_id, or None
    base_body: dict | None = None    # pre-amendment body, for audit and revert
    withdrawn: bool = False          # a MOM removed the clause; kept for audit


class Amendment(BaseModel):
    """A change a MOM makes to a clause. Stored separately from the
    requirement so both the original and the meeting's change stay visible."""
    amendment_id: str
    clause_ref: str | None = None
    req_id: str | None = None        # resolved target; None means unmatched
    text: str
    parameter: str | None = None
    operator: str | None = None
    value: str | float | list | None = None
    unit: str | None = None
    action: str = "modify"           # modify | withdraw
    source_doc_id: str


class RequirementSet(BaseModel):
    requirements: list[RequirementRecord] = []
    amendments: list[Amendment] = []
    overrides: list[Override] = []


class ComplianceResult(BaseModel):
    """One cell of the requirement x vendor matrix. Lives here rather than in
    compliance.py so snapshots.py can serialise it without an import cycle."""
    req_id: str
    vendor: str
    verdict: str                     # pass|fail|deviation|unanswered|review
    fact_id: str | None = None
    doc_id: str | None = None
    rationale: str = ""
    evaluated_at: str
