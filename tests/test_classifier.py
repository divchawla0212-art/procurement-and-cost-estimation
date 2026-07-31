from cost_estimation.config.loader import load_config
from cost_estimation.models.schema import DocType
from cost_estimation.ingestion.classifier import classify_document, classify_sheet

CFG = load_config()


def test_classify_schedule_of_prices():
    doc = classify_document("Section-3 Schedule of Prices- Additional Tie-in Works.xlsx", CFG)
    assert doc.doc_type == DocType.SCHEDULE_OF_PRICES


def test_classify_costing_workbook_with_revision_and_discipline():
    doc = classify_document(
        "GDX-P-26-072 REV-00(1) COSTING A-6 (Electrical BOQs for Unit areas Imp. contractor) Rev.1 RK 20260622.xlsx",
        CFG,
    )
    assert doc.doc_type == DocType.COSTING_WORKBOOK
    assert doc.discipline == "electrical"
    assert doc.revision == "1"


def test_classify_proposal_and_sow():
    assert classify_document("GDX-P-26-072 REV-01 EA T-00935 JEBEL DHANNA.docx", CFG).doc_type == DocType.PROPOSAL_TEMPLATE
    assert classify_document("SOW-30201_50150_LT_HE-H4-000-69-00-002_Rev A.pdf", CFG).doc_type == DocType.SCOPE_OF_WORK


def test_classify_sheet_area_and_discipline():
    disc, area = classify_sheet("TF Main Elec. Equipment", CFG)
    assert area == "TF"
    disc2, area2 = classify_sheet("Sect. 3 D - Electrical", CFG)
    assert disc2 == "electrical"
