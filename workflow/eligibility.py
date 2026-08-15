"""Whether a bid is admissible against what the enquiry asked for — BD-7.

Pure module: no store, no I/O, no clock, like `workflow/bidders.py`. `assess`
reads two already-fetched lists of `RfqDocument` — everything the contractor
issued and everything one bidder returned — and says which mandatory category
is still absent. Routes resolve `issued` and `submitted` at the boundary by
splitting one RFQ's documents on `submitted_by_vendor_id` and call in.

### Category (c) is a rule, not a flag

If the issued package contains a `COMPLIANCE_SHEET` document, the bidder must
return one. If it does not, the bidder must supply a deviation list — which is
the same category, arriving from the other direction. Either way the category
is required *whenever the enquiry has been issued at all* (`issued` is
non-empty); encoding it as "optional unless a compliance sheet was issued"
would let an enquiry that shipped one accept a bid that ignored it, and
encoding it as "optional unless anything was issued" would make it forgettable
on any enquiry that only shipped drawings.

There is no separate "deviation list" member of `EligibilityCategory` — a
returned deviation list satisfies (c) exactly as a returned compliance sheet
does, and telling the two apart would mean reading a submitted document's
content, which is out of scope for this whole plan (`feature-request.md`'s
"What is deliberately not in scope"). `assess` decides which one is expected
by looking only at what `issued` contains, and the reason sentence names
which.

### Never stored

`EligibilityVerdict` is never stored anywhere — not on `RfqDocument`, not on
`ShortlistEntry`, not as a key in `workflow.json` — the same rule
`Suitability` and `missing_client_approval` keep, and for the same reason: a
stored verdict is wrong the moment a late document arrives.
"""
from collections.abc import Sequence

from pydantic import BaseModel, Field

from workflow.models.rfq_document import EligibilityCategory, RfqDocument

#: The three categories that block a bid regardless of what was issued.
#: Category (c) joins these unconditionally once anything has been issued at
#: all — see `assess` — so it is deliberately not listed here as a fourth.
MANDATORY = (
    EligibilityCategory.TECHNICAL_OFFER,
    EligibilityCategory.COMMERCIAL_OFFER,
    EligibilityCategory.TBE_SHEET,
)

#: One phrase per always-mandatory category. Category (c)'s phrase depends on
#: what was issued, so it is built in `_reason` rather than living here.
_LABELS: dict[EligibilityCategory, str] = {
    EligibilityCategory.TECHNICAL_OFFER: "a technical offer",
    EligibilityCategory.COMMERCIAL_OFFER: "a commercial offer",
    EligibilityCategory.TBE_SHEET: "a technical bid evaluation sheet",
}


class EligibilityVerdict(BaseModel):
    """`admissible` is `not missing`, spelled out so a caller does not have to
    know that. `reason` is `None` exactly when `missing` is empty."""

    admissible: bool
    missing: list[EligibilityCategory] = Field(default_factory=list)
    reason: str | None = None


def _reason(missing: list[EligibilityCategory], compliance_sheet_issued: bool) -> str:
    parts = []
    for category in missing:
        if category == EligibilityCategory.COMPLIANCE_SHEET:
            parts.append(
                "a returned compliance sheet"
                if compliance_sheet_issued
                else "a deviation list"
            )
        else:
            parts.append(_LABELS[category])

    if len(parts) == 1:
        joined = parts[0]
    elif len(parts) == 2:
        joined = f"{parts[0]} and {parts[1]}"
    else:
        joined = ", ".join(parts[:-1]) + f", and {parts[-1]}"
    return f"The bid is missing {joined}."


def assess(
    issued: Sequence[RfqDocument],
    submitted: Sequence[RfqDocument],
) -> EligibilityVerdict:
    """`missing` lists **every** absent mandatory category, not the first
    found — a bidder told to send one more thing, who sends it and is then
    told about a second, has been made to do two rounds for no reason.

    Categories that carry no `category` at all (a buyer's upload nobody has
    classified yet) never satisfy anything here — they are not absent from
    `submitted`'s set of categories, they are simply not counted into it.

    Iterating `EligibilityCategory` in its declared order, rather than
    building `MANDATORY` plus an appended (c), is what keeps `missing` in the
    natural a/b/c/e reading order without a second list to keep in sync.
    """
    submitted_categories = {d.category for d in submitted if d.category is not None}
    compliance_sheet_issued = any(
        d.category == EligibilityCategory.COMPLIANCE_SHEET for d in issued
    )
    compliance_sheet_required = bool(issued)

    missing: list[EligibilityCategory] = []
    for category in EligibilityCategory:
        if category in MANDATORY:
            required = True
        elif category == EligibilityCategory.COMPLIANCE_SHEET:
            required = compliance_sheet_required
        else:
            required = False
        if required and category not in submitted_categories:
            missing.append(category)

    if not missing:
        return EligibilityVerdict(admissible=True, missing=[], reason=None)
    return EligibilityVerdict(
        admissible=False,
        missing=missing,
        reason=_reason(missing, compliance_sheet_issued),
    )
