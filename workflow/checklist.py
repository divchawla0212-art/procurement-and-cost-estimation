"""The eligibility checklist, as rows — one definition, three readers.

Pure module: no store, no I/O, no clock, like `bidders.py` and `eligibility.py`.

The nine returnables **are** `EligibilityCategory`, iterated in its declared
order and lettered from that position, and `mandatory` comes from
`eligibility.assess(issued, submitted=[])` — with nothing submitted, `missing`
*is* the required set. Neither the labels nor the mandatory rule is copied here.

Three things read these rows and none of them may build their own: the enquiry
mail's text body (`enquiry_body`), the RFQ payload the browser renders
(`api/workflow_routes.py`), and the gate that judges a returned bid
(`eligibility.assess`, which is the source rather than a reader). A checklist
typed into a screen would keep telling a buyer one thing while the mail told a
vendor another and the gate enforced a third.

Only category (c)'s **wording** lives here, because the rule reads two ways and
whoever is reading has to be told which: a compliance sheet was issued and must
come back filled in, or none was and a deviation list is wanted instead. The
rule deciding *whether* (c) applies at all stays in `assess`, which is why an
enquiry carrying no documents does not mark it.
"""
from collections.abc import Sequence
from dataclasses import dataclass

from workflow import eligibility
from workflow.models.rfq import ChecklistItem
from workflow.models.rfq_document import EligibilityCategory, RfqDocument

#: a. b. c. … Lettering is positional, so a member added to
#: `EligibilityCategory` is lettered by this rather than by a second list
#: somebody has to remember to extend. Long enough to carry the buyer's own
#: additions after the nine.
_LETTERS = "abcdefghijklmnopqrstuvwxyz"

#: How a category is described beyond its own name. Absent means the name says
#: it. (c) is not here — its note depends on what was issued.
_NOTES: dict[EligibilityCategory, str] = {
    EligibilityCategory.CLIENT_DATASHEET: (
        "issued with this enquiry, to be returned filled in"
    ),
    EligibilityCategory.TBE_SHEET: "to be returned filled in",
}


@dataclass(frozen=True)
class ChecklistRow:
    """`letter` is presentation, but it is computed once here rather than in
    each reader — three renderers numbering the same list independently is
    three chances to disagree about what "(c)" refers to, and (c) is the one
    everybody cites."""

    letter: str
    label: str
    mandatory: bool
    note: str | None
    #: `None` for the nine, the item's id for a buyer's own addition. What tells
    #: a screen which rows it may offer to remove.
    item_id: str | None = None


def _compliance_note(compliance_sheet_issued: bool) -> str:
    if compliance_sheet_issued:
        return (
            "a compliance sheet is attached to this enquiry; return it filled "
            "in alongside your other documents"
        )
    return (
        "no compliance sheet is attached; send a deviation list stating any "
        "deviations, or confirm that you have none"
    )


def rows(
    issued: Sequence[RfqDocument],
    extra_items: Sequence[ChecklistItem] = (),
) -> list[ChecklistRow]:
    """The nine, then whatever the buyer added for this RFQ.

    `extra_items` is `TbeTemplate.items`, which holds **only** the buyer's own
    additions — the nine are never copied in there, so appending is right and
    de-duplicating would be solving a problem the model already prevents.
    """
    required = set(eligibility.assess(issued=issued, submitted=[]).missing)
    compliance_sheet_issued = any(
        d.category == EligibilityCategory.COMPLIANCE_SHEET for d in issued
    )

    out: list[ChecklistRow] = []
    for letter, category in zip(_LETTERS, EligibilityCategory):
        note = (
            _compliance_note(compliance_sheet_issued)
            if category == EligibilityCategory.COMPLIANCE_SHEET
            else _NOTES.get(category)
        )
        out.append(ChecklistRow(
            letter=letter,
            label=category.value,
            mandatory=category in required,
            note=note,
        ))

    for letter, item in zip(_LETTERS[len(list(EligibilityCategory)):], extra_items):
        out.append(ChecklistRow(
            letter=letter,
            label=item.label,
            mandatory=item.mandatory,
            note=None,
            item_id=item.id,
        ))
    return out


def as_text(row: ChecklistRow) -> str:
    """One row as the enquiry mail prints it."""
    mark = " (must have)" if row.mandatory else ""
    text = f"  {row.letter}. {row.label}{mark}"
    return f"{text} — {row.note}" if row.note else text
