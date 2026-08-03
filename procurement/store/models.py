import hashlib
from typing import Any
from pydantic import BaseModel

from procurement import units


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
    # were extracted under. Part of the technical cache key: the vocabulary is
    # an input to the tech_facts_v1 prompt, so a requirement edit that changes
    # it must re-ask the datasheets exactly once. None for every document that
    # does not reach the technical extractor, by either route.
    vocabulary_sha: str | None = None
    # A vendor document can feed a *second* extractor when its vendor's
    # document set leaves the first one insufficient — see
    # SECONDARY_VENDOR_ROUTE in pipeline.py, where a quotation also feeds the
    # technical extractor for a vendor no datasheet of whose reaches it. That
    # pass is cached, succeeds and fails on its own: both passes read the same
    # bytes but ask different prompts, so one status and one prompt version
    # cannot describe both. Folding a failed technical pass into
    # `extraction_status` would evict the good quotation extraction from the
    # cache and re-ask it, at cost, on every run thereafter.
    secondary_status: str | None = None          # ok | failed; None: no second route
    secondary_notes: str | None = None
    secondary_prompt_version: str | None = None


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
    # The document whose extraction produced `commercial`. Stored rather than
    # re-derived: choosing it runs a classified-then-pick_quote fallback in
    # pipeline.py that would drift if a second copy existed.
    quotation_doc_id: str | None = None
    # A reviewer's judgement, not an extracted value and not an Override —
    # reconcile() flags any field_path absent from the extracted view, so a
    # synthetic override path would raise a false conflict on every run.
    technical_feedback: str | None = None


class Event(BaseModel):
    at: str
    run_id: str
    actor: str                          # "pipeline" or a username
    action: str
    target: str | None = None
    detail: dict = {}


def fact_id_for(doc_id: str, parameter: str,
                value: str | float | None, unit: str | None) -> str:
    """Stable across re-extraction for a given (document, parameter, value, unit).

    All four participate, for the reason `deviation_id_for` gives: if a later
    extraction rewords the value the id shifts and an override orphans, which is
    strictly better than an override silently retargeting a different reading of
    the same parameter. A datasheet that states one parameter twice with
    different numbers is stating two facts, and they must be addressable apart.

    The value is normalised through `units.pure_number` so that 525.0 and "525"
    are one id. The unit is folded to a stripped, lowercased spelling but never
    converted: "525 kW" and "525000 W" are two readings as printed, and whether
    they mean one quantity is the compliance layer's question, not the store's.
    """
    number = units.pure_number(value)
    v = repr(number) if number is not None else str(value).strip().lower()
    u = (unit or "").strip().lower()
    key = f"{doc_id}:{parameter.strip().lower()}:{v}:{u}"
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
    checkability: str = "judgement"  # auto|stated|judgement
    parameter: str | None = None     # auto|stated
    # >= | <= | == | in (a set of permitted values) | between (a stated range,
    # whose value is exactly two bounds)
    operator: str | None = None
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
    # Why req_id is None, in words a reviewer can act on: "the requirements are
    # incomplete" and "say which document you meant" need different responses.
    unresolved_reason: str | None = None


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
    # Every reading the cell was computed from, including the one named by
    # fact_id. Empty when the deciding reading has exactly one member;
    # non-empty whenever more than one fact contributed - whether from one
    # reading with equivalent restatements (e.g. 700 kW and 700000 W) or from
    # a genuine multiplicity that escalated to `review`. Defaults to [] so
    # every snapshot written before this field existed still validates.
    candidate_fact_ids: list[str] = []
    rationale: str = ""
    evaluated_at: str
