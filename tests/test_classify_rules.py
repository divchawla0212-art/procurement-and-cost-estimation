import pytest
from procurement.classify import classify_by_rules, DOC_CLASSES


@pytest.mark.parametrize("filename,expected", [
    # real filenames from data/procurement-data and processed-data
    ("Quotation of Gas Generator ZDG2024110701.pdf", "quotation"),
    ("Quotation-MKON.pdf", "quotation"),
    ("Techno Commercial proposal AESL-GTC-60808 - 22102024 -REV00.pdf", "quotation"),
    ("01 DOD-30201-50150-BH-000-16-00-004 DataSheet Gas Generator.pdf", "datasheet"),
    ("02 Detailed Vendor Standard Datasheet of all components.pdf", "datasheet"),
    ("Attachment-1 DataSheet Gas Generator commented.pdf", "datasheet"),
    ("03 Attachment-2 Vendor Deviation Form.pdf", "deviation"),
    ("Attachment-2 Vendor Deviation Form.pdf", "deviation"),
    ("BOM.pdf", "bom"),
    ("00 MOM 20241111 ASTRA ADPOWER.pdf", "mom"),
    ("ADN-AEC-ME-SPC-026 MR Gas Genset copy.pdf", "spec"),
    ("13 Gas Generator PID Commented.pdf", "drawing"),
    ("15 LAYOUT - KGW550GF-T.pdf", "drawing"),
    ("17 Outline Diagrams && General Arrangement Diagrams(1).pdf", "drawing"),
    ("18 Name Plate drawings.pdf", "drawing"),
    ("20 Single Line Diagrams.pdf", "drawing"),
    ("21 System Architecture.pdf", "drawing"),
    ("04 Consumption List.pdf", "other"),
    ("06 Operation Special tools.pdf", "other"),
    ("07 Power Auxiliary List.pdf", "other"),
    ("08 Two Years Operation Spares.pdf", "other"),
    ("11 Attachment-4 Applicable Codes and Standards Pending.pdf", "other"),
    ("Attachment-7 Load List.pdf", "other"),
    ("2011 gas quality for MAN gas engines with MAN aftertreatment system.pdf", "other"),
])
def test_rules_classify_real_filenames(filename, expected):
    assert classify_by_rules(f"/proj/vendors/KERUI/{filename}") == expected


@pytest.mark.parametrize("filename", [
    # no keyword a rule can trust -> defer to the LLM pass
    "ADP-13158-2024-935.pdf",
    "ADP-13158-2024-935(Rev1).pdf",
    "HSD 230.pdf",
])
def test_rules_defer_when_not_confident(filename):
    assert classify_by_rules(f"/proj/vendors/ADPOWER/{filename}") is None


def test_datasheet_beats_the_attachment_prefix():
    # "Attachment-1 DataSheet ..." must not be swallowed by a generic rule
    assert classify_by_rules("Attachment-1 DataSheet Gas Generator commented.pdf") == "datasheet"


def test_classification_is_case_insensitive():
    assert classify_by_rules("BILL OF MATERIAL.PDF") == "bom"


def test_every_rule_result_is_a_known_class():
    for name in ["Quotation.pdf", "Datasheet.pdf", "Deviation Form.pdf", "BOM.pdf",
                 "MOM notes.pdf", "MR spec.pdf", "Layout.pdf", "Spares list.pdf"]:
        result = classify_by_rules(name)
        assert result is None or result in DOC_CLASSES


@pytest.mark.parametrize("filename,not_expected", [
    # Word-boundary tests: prove that short keywords don't match inside larger words
    ("Bombay Office Quotation.pdf", "bom"),  # "bom" inside "Bombay", should be quotation
    ("XX Moment of Inertia Calculation.pdf", "mom"),  # "mom" inside "Moment", should be None
    ("Torque and Moment Report.pdf", "mom"),  # "mom" inside "Moment", should be None
    ("Stupid Question Log.pdf", "drawing"),  # "pid" inside "stupid", should be None
])
def test_word_boundary_prevents_false_matches(filename, not_expected):
    """Verify that short keywords require word boundaries and don't match inside larger words."""
    result = classify_by_rules(filename)
    assert result != not_expected


@pytest.mark.parametrize("filename,expected", [
    # Underscore-delimited keywords: prove lookarounds treat underscore as a delimiter
    ("Copy of ADN-AEC-ME-SPC-026_MR_Gas_Genset.xlsx", "spec"),  # Real client file with underscore-delimited MR
    ("vendor_bom_rev2.pdf", "bom"),  # underscore-delimited bom
    ("genset_spec_final.pdf", "spec"),  # underscore-delimited spec
    ("some_bombay_office_quotation.pdf", "quotation"),  # Verify underscore didn't loosen bom matching
])
def test_underscore_delimited_keywords_match(filename, expected):
    """Verify that underscore-delimited keywords correctly match and don't reintroduce false positives."""
    assert classify_by_rules(filename) == expected
