from datetime import datetime
from typing import Union
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from workflow.stages import RETIRED_STAGES, Stage


def new_rfq_id() -> str:
    return f"rfq_{uuid4().hex[:8]}"


class StageTransition(BaseModel):
    """One entry in an RFQ's stage history. History is append-only: a backward
    transition adds an entry, it never rewrites or removes an earlier one.

    Both stage fields are `Stage | str`, and the `str` half is not laxness. A
    stage can be **retired** — `Scoping` was — and every RFQ raised before that
    happened names it in the entry recording its own creation. Those entries
    are a record of what happened; restating them as the stage that replaced
    the retired one would be inventing a fact about an RFQ somebody actually
    raised, and dropping them would lose it. So a retired label is kept
    verbatim, as the string it is, and only a label in `RETIRED_STAGES` is
    admitted that way — anything else is still a validation error, which is
    what stops a typo loading as a stage nothing can transition out of.

    `from_stage is None` keeps its one meaning: this entry records the RFQ's
    creation, so there was no stage before it.
    """

    from_stage: Union[Stage, str, None] = Field(default=None, union_mode="left_to_right")
    to_stage: Union[Stage, str] = Field(union_mode="left_to_right")
    at: datetime
    by: str
    reason: str | None = None

    @field_validator("from_stage", "to_stage", mode="before")
    @classmethod
    def _resolve_stage(cls, value: object) -> object:
        """A live stage resolves to its member; a retired one stays a string.

        The declared union alone would not do this: pydantic's smart mode
        matches `str` strictly against `"Issued"` and would hand back a plain
        string, and every `is Stage.ISSUED` in this repository would quietly
        stop being true. So the resolution is explicit here, and the union is
        pinned left-to-right behind it.
        """
        if value is None or isinstance(value, Stage):
            return value
        if isinstance(value, str) and value in RETIRED_STAGES:
            return value
        # Not a member and not retired: still an error, exactly as before.
        return Stage(value)


class RfqRecord(BaseModel):
    id: str = Field(default_factory=new_rfq_id)
    reference: str
    project_id: str
    item_ids: list[str]
    package: str
    discipline: str
    value_estimate_aed: int
    stage: Stage = Stage.SHORTLISTING
    history: list[StageTransition] = Field(default_factory=list)


class Attachment(BaseModel):
    doc_code: str
    title: str
    revision: str | None = None


class TechnicalPackage(BaseModel):
    """What vendors bid against, at one revision, frozen when it is settled.

    Two lists, and they are not alternatives. `attachments` is the register:
    document codes, titles and revisions describing what the package consists
    of, carrying no bytes. `documents` holds ids into the `RfqDocument`
    collection — the actual files, uploaded against this RFQ. The register is
    kept because `Addendum.attachments` uses the same record, and an addendum
    that supersedes a package has to be able to describe what it supersedes.

    `documents` is **rebuilt from the records** by the store rather than
    accumulated here, so the two cannot disagree: a document removed from the
    collection is off this list in the same call. It is the store, not this
    model, that decides which records belong — the contractor's own, never a
    vendor's submission.
    """

    rfq_id: str
    revision: str
    basis_of_design: str
    attachments: list[Attachment] = Field(default_factory=list)
    documents: list[str] = Field(default_factory=list)
    frozen_at: datetime | None = None
    frozen_by: str | None = None


def new_shortlist_entry_id() -> str:
    return f"sle_{uuid4().hex[:8]}"


def new_vdrl_line_id() -> str:
    return f"vdl_{uuid4().hex[:8]}"


class ShortlistEntry(BaseModel):
    """`override_by` and `override_reason` exist so that including a vendor
    against the prequal signal is a positive, attributed act rather than a
    silent edit.

    `id` exists so removal can address a member by identity. List order is not
    a contract — the same rule the procurement store states as "by id, never
    by index" — and a vendor name is user-supplied text, not a key.

    `vendor_id` links to the registry when the vendor came from it, and is
    `None` for a genuine one-off typed in by hand. The three fields beside it
    are a **snapshot**, not a mirror: they record what the registry said at the
    moment somebody decided to invite this vendor, which is a different fact
    from what the registry says today and is the one an audit needs. That is
    why a later rename or suspension does not rewrite them."""

    id: str = Field(default_factory=new_shortlist_entry_id)
    rfq_id: str
    vendor_id: str | None = None
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
    id: str = Field(default_factory=new_vdrl_line_id)
    rfq_id: str
    doc_code: str
    title: str
    doc_type: str
    mandatory: bool = True
