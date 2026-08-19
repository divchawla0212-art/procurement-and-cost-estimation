"""The enquiry mail's text body — BD-9 follow-up.

The body is built from the same vocabulary and the same rule the eligibility
gate enforces, never from a copy of either. That is what these tests pin: a
checklist typed into a string literal would read correctly on the day it was
written and drift the first time a category is renamed or the mandatory set
changes, and the vendor reading it would be told to send the wrong things.
"""
from datetime import date

import pytest

from workflow import eligibility
from workflow.enquiry_body import build_body
from workflow.models.project import Item, Project
from workflow.models.rfq import RfqRecord, TechnicalPackage
from workflow.models.rfq_document import EligibilityCategory as C
from workflow.models.rfq_document import RfqDocument

THE_ESTIMATE = 900000


def a_project() -> Project:
    return Project(
        id="prj_1",
        name="Haliba Field Development",
        code="HAL",
        client="ADNOC",
        location="Abu Dhabi",
        live_period_start=date(2026, 1, 1),
        live_period_end=date(2027, 1, 1),
    )


def an_item() -> Item:
    return Item(
        id="itm_1",
        project_id="prj_1",
        item_type="HV cable package",
        description="33kV XLPE power cable, 1200 m",
        qty=1200,
        uom="m",
        discipline="Cables",
        estimated_value_aed=THE_ESTIMATE,
        required_on_site=date(2026, 11, 30),
    )


def an_rfq() -> RfqRecord:
    return RfqRecord(
        id="rfq_1",
        project_id="prj_1",
        item_ids=["itm_1"],
        reference="ADP-RFQ-2026-014",
        discipline="Cables",
        value_estimate_aed=THE_ESTIMATE,
        package="HV cable package",
    )


def issued(category: C | None = None, filename: str = "doc.pdf") -> RfqDocument:
    return RfqDocument(
        rfq_id="rfq_1",
        filename=filename,
        rel_path=filename,
        sha256="a" * 64,
        size_bytes=10,
        content_type="application/pdf",
        uploaded_by="buyer@adp.ae",
        uploaded_at="2026-08-19T00:00:00Z",
        category=category,
    )


def body(**over) -> str:
    fields = dict(
        rfq=an_rfq(),
        project=a_project(),
        items=[an_item()],
        issued=[issued(C.TECHNICAL_DATASHEET, "Technical Datasheet.pdf")],
        technical_package=None,
    )
    fields.update(over)
    return build_body(**fields)


# -- the checklist comes from the vocabulary, not from a literal ---------------


def test_the_checklist_lists_every_category_in_the_declared_order():
    """All nine, lettered a-i. Asserted against the enum rather than against
    nine strings, so renaming a member moves the body with it."""
    text = body()
    positions = [text.index(c.value) for c in EligibilityCategoryOrder()]
    assert positions == sorted(positions), "categories are out of declaration order"
    for letter, category in zip("abcdefghi", EligibilityCategoryOrder()):
        assert f"{letter}. {category.value}" in text


def EligibilityCategoryOrder():
    return list(C)


def test_there_are_exactly_nine_lettered_lines():
    """If a tenth category is ever added, this fails rather than silently
    lettering it `j.` off the end of the alphabet the body assumes."""
    text = body()
    lettered = [line for line in text.splitlines() if _is_checklist_line(line)]
    assert len(lettered) == len(list(C)) == 9


def _is_checklist_line(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) > 2 and stripped[0].isalpha() and stripped[1:3] == ". "


# -- "must have" is the gate's answer, never a second list --------------------


def test_the_must_have_marks_are_exactly_what_the_gate_would_demand():
    """The load-bearing one. `assess` with nothing submitted returns precisely
    the required set, so the body and the gate cannot disagree about what
    blocks a bid."""
    docs = [issued(C.COMPLIANCE_SHEET)]
    text = body(issued=docs)
    required = set(eligibility.assess(issued=docs, submitted=[]).missing)

    for category in C:
        line = _line_for(text, category)
        if category in required:
            assert "(must have)" in line, f"{category.value} should be marked"
        else:
            assert "(must have)" not in line, f"{category.value} must not be marked"


def _line_for(text: str, category: C) -> str:
    return next(line for line in text.splitlines() if category.value in line)


def test_the_three_always_mandatory_categories_are_marked():
    text = body()
    for category in eligibility.MANDATORY:
        assert "(must have)" in _line_for(text, category)


def test_an_optional_category_is_never_marked():
    text = body()
    assert "(must have)" not in _line_for(text, C.CATALOGUES)
    assert "(must have)" not in _line_for(text, C.DRAWINGS)


# -- category (c) is a rule with two readings, and the body states which ------


def test_a_compliance_sheet_in_the_package_asks_for_it_back_filled_in():
    text = body(issued=[issued(C.COMPLIANCE_SHEET, "Compliance Sheet.xlsx")])
    line = _line_for(text, C.COMPLIANCE_SHEET)
    assert "return it filled in" in line
    assert "deviation list" not in line


def test_no_compliance_sheet_in_the_package_asks_for_a_deviation_list():
    text = body(issued=[issued(C.TECHNICAL_DATASHEET)])
    line = _line_for(text, C.COMPLIANCE_SHEET)
    assert "deviation list" in line
    assert "return it filled in" not in line


def test_an_enquiry_carrying_no_documents_does_not_demand_the_compliance_sheet():
    """`assess` requires (c) only once something has been issued. With an empty
    package the body must not mark it either, or a vendor is told a document is
    mandatory that nothing will actually block them for."""
    text = body(issued=[])
    assert "(must have)" not in _line_for(text, C.COMPLIANCE_SHEET)


# -- the project and its details ----------------------------------------------


def test_the_body_names_the_project_its_code_and_the_client():
    text = body()
    assert "Haliba Field Development" in text
    assert "HAL" in text
    assert "ADNOC" in text
    assert "Abu Dhabi" in text


def test_the_body_names_the_enquiry_and_the_discipline():
    text = body()
    assert "ADP-RFQ-2026-014" in text
    assert "Cables" in text


def test_the_body_describes_every_item_the_enquiry_covers():
    text = body()
    assert "HV cable package" in text
    assert "33kV XLPE power cable, 1200 m" in text
    assert "1200 m" in text


def test_a_package_revision_is_named_when_there_is_one():
    package = TechnicalPackage(rfq_id="rfq_1", revision="Rev. B", basis_of_design="x")
    assert "Rev. B" in body(technical_package=package)


def test_a_missing_project_does_not_break_the_body():
    """`get_project` returns `None` for a project that has gone. A tender that
    cannot be described is still a tender that must go out."""
    text = body(project=None)
    assert "ADP-RFQ-2026-014" in text
    assert "Technical offer" in text


# -- the attachments are named ------------------------------------------------


def test_the_body_lists_every_attached_document_by_name():
    docs = [
        issued(C.TECHNICAL_DATASHEET, "ADP-RFQ-2026-014 Technical Datasheet.pdf"),
        issued(None, "ADP-RFQ-2026-014 Scope of Work.pdf"),
    ]
    text = body(issued=docs)
    assert "ADP-RFQ-2026-014 Technical Datasheet.pdf" in text
    assert "ADP-RFQ-2026-014 Scope of Work.pdf" in text


def test_an_enquiry_with_no_documents_says_so_rather_than_showing_an_empty_list():
    text = body(issued=[])
    assert "No documents" in text or "no documents" in text


# -- what must never travel ---------------------------------------------------


def test_the_body_never_discloses_the_estimate():
    """The single most damaging thing this body could carry. `value_estimate_aed`
    is the contractor's own budget for the item; putting it in front of the
    bidders being asked to price it destroys the competition the enquiry
    exists to create. Every rendering of the number is checked, not just the
    bare digits."""
    text = body()
    for rendering in ("900000", "900,000", "900 000", "9,00,000"):
        assert rendering not in text, f"the estimate leaked as {rendering!r}"


def test_the_body_carries_no_other_vendors_name():
    """The one-message-per-vendor rule reaches into the body too: a shortlist
    is not an input to this function at all, so there is nothing here that
    could name a competitor."""
    import inspect

    signature = inspect.signature(build_body)
    assert "shortlist" not in signature.parameters
    assert "recipients" not in signature.parameters
