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


def new_checklist_item_id() -> str:
    return f"cli_{uuid4().hex[:8]}"


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


class ChecklistItem(BaseModel):
    """One line a buyer added to an RFQ's eligibility checklist.

    The nine `EligibilityCategory` members are **not** records of this kind and
    never become ones: they are a fixed vocabulary, stored nowhere per RFQ, so
    "the nine cannot be removed" holds by construction rather than by a guard
    somebody could forget. What lives here is only what a buyer typed for this
    one RFQ.

    `mandatory` reaches `eligibility.assess`, which is why
    `RfqDocument.checklist_item_id` exists: a must-have item with nothing able
    to name it would be unsatisfiable by construction, and a checklist that can
    never be met is worse than one that cannot be extended.
    """

    id: str = Field(default_factory=new_checklist_item_id)
    label: str
    mandatory: bool = False


class TbeTemplate(BaseModel):
    """The RFQ's eligibility checklist — the record `_shortlisting_exit` reads
    to decide that somebody has settled what bidders must return.

    `items` holds **only the buyer's own additions**. The nine categories are
    not copied in: a stored copy of a fixed vocabulary is a second definition
    for the first edit to disagree with, the rule this repository keeps for
    `client_approved` and `approval_caution`.

    The field was `criteria: list[str]` before the checklist existed. A
    document written then reads its strings as non-mandatory items — see
    `persistence.from_document` — so a seeded RFQ keeps what it had.
    """

    rfq_id: str
    items: list[ChecklistItem] = Field(default_factory=list)
    source_rfq_reference: str | None = None


class VdrlLine(BaseModel):
    id: str = Field(default_factory=new_vdrl_line_id)
    rfq_id: str
    doc_code: str
    title: str
    doc_type: str
    mandatory: bool = True


def new_enquiry_send_id() -> str:
    return f"esd_{uuid4().hex[:8]}"


class EnquirySend(BaseModel):
    """One vendor's copy of the enquiry, as it actually went out.

    `to` is stored rather than derived. The `email` key on a shortlist row is
    derived on read, so correcting the contact sheet corrects every shortlist at
    once — this is the opposite and deliberately so. It records something that
    happened, and re-uploading the sheet must not rewrite who a tender reached.
    Frozen for the same reason `ShortlistEntry.prequal_status` is frozen.

    `transport` is here because "this went to the outbox" and "this reached a
    real mailbox" must not be indistinguishable later.
    """

    id: str = Field(default_factory=new_enquiry_send_id)
    rfq_id: str
    shortlist_entry_id: str
    vendor_name: str
    to: list[str]
    sent_at: datetime
    by: str
    message_id: str
    transport: str
