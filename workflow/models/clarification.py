"""A bidder's question, and the amendment an answer sometimes forces.

Neither model carries a `status` field. State is a function of the timestamps
below and is computed in `workflow/clarifications.py`, for the same reason
`PrequalStatus` has no `"Expired"` member: a stored status is a fourth thing
that has to agree with three fields that already say it, and the first write
that updates one and not the other makes the register lie with nothing to
sweep it.
"""
from datetime import date, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from workflow.models.rfq import Attachment

QueryCategory = Literal["Technical", "Commercial"]


def new_query_id() -> str:
    return f"clq_{uuid4().hex[:8]}"


def new_addendum_id() -> str:
    return f"add_{uuid4().hex[:8]}"


class ClarificationQuery(BaseModel):
    """One question from one invited bidder, and what was answered.

    `raised_by_entry_id` addresses a `ShortlistEntry`, not a registry bidder: a
    query is raised against *this* RFQ by somebody invited to *this* RFQ, and a
    `vendor_id` would also name companies who were never invited.

    `raised_by_name` beside it is a **snapshot**, for the identical reason
    `ShortlistEntry` snapshots `vendor_name` — the register records who asked at
    the time they asked, and a later rename does not rewrite it.

    `restricted_reason` is the whole circulation model. `None` means the answer
    went to the entire included shortlist. A reason means it did not, and who
    decided that. There is deliberately no `circulate: bool`: a boolean makes a
    restricted answer indistinguishable from an oversight, and the register
    stops being evidence of fair treatment.
    """

    id: str = Field(default_factory=new_query_id)
    rfq_id: str
    number: str
    raised_by_entry_id: str
    raised_by_name: str
    raised_on: date
    category: QueryCategory
    question: str
    answer: str | None = None
    answered_by: str | None = None
    answered_at: datetime | None = None
    restricted_reason: str | None = None
    withdrawn_reason: str | None = None
    withdrawn_by: str | None = None
    withdrawn_at: datetime | None = None


class Addendum(BaseModel):
    """A numbered amendment to an issued RFQ.

    `supersedes_revision` is recorded by the store at draft time, not supplied
    by the caller — a caller-supplied "what I am superseding" is a claim, and
    the store already knows the answer. Together with `revision` it makes the
    addenda list the package's revision trail, so no new history mechanism is
    needed and `RfqRecord.history` stays what it is: stage transitions, which an
    addendum is not.

    `arising_from_query_ids` may be empty. A buyer-initiated addendum — a client
    change, a corrected datasheet — is legitimate, and requiring a query to
    justify one would only produce fabricated queries.
    """

    id: str = Field(default_factory=new_addendum_id)
    rfq_id: str
    number: str
    supersedes_revision: str
    revision: str
    summary: str
    attachments: list[Attachment] = Field(default_factory=list)
    arising_from_query_ids: list[str] = Field(default_factory=list)
    bid_due_date: date | None = None
    issued_at: datetime | None = None
    issued_by: str | None = None
