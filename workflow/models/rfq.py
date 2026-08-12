from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from workflow.stages import Stage


def new_rfq_id() -> str:
    return f"rfq_{uuid4().hex[:8]}"


class StageTransition(BaseModel):
    """One entry in an RFQ's stage history. History is append-only: a backward
    transition adds an entry, it never rewrites or removes an earlier one."""

    from_stage: Stage | None
    to_stage: Stage
    at: datetime
    by: str
    reason: str | None = None


class RfqRecord(BaseModel):
    id: str = Field(default_factory=new_rfq_id)
    reference: str
    project_id: str
    item_ids: list[str]
    package: str
    discipline: str
    value_estimate_aed: int
    stage: Stage = Stage.SCOPING
    history: list[StageTransition] = Field(default_factory=list)


class Attachment(BaseModel):
    doc_code: str
    title: str
    revision: str | None = None


class TechnicalPackage(BaseModel):
    rfq_id: str
    revision: str
    basis_of_design: str
    attachments: list[Attachment] = Field(default_factory=list)
    frozen_at: datetime | None = None
    frozen_by: str | None = None


class ShortlistEntry(BaseModel):
    """`override_by` and `override_reason` exist so that including a vendor
    against the prequal signal is a positive, attributed act rather than a
    silent edit."""

    rfq_id: str
    vendor_name: str
    prequal_status: str
    scope_code_fit: bool
    included: bool
    override_by: str | None = None
    override_reason: str | None = None


class TbeTemplate(BaseModel):
    rfq_id: str
    criteria: list[str]
    source_rfq_reference: str | None = None


class VdrlLine(BaseModel):
    rfq_id: str
    doc_code: str
    title: str
    doc_type: str
    mandatory: bool = True
