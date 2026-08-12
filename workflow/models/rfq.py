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
