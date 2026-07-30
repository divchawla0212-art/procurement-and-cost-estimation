from procurement.pipeline import run_ingestion
from procurement.store import snapshots

from tests.test_pipeline_rfq import RfqClient
from tests.test_pipeline_vocabulary import _project

_QUOTE = "Quotation.txt"


def _note(root, vendor, text):
    facts = snapshots.load_facts(root, "p", vendor)
    facts.technical_feedback = text
    snapshots.save_facts(root, "p", facts)


def test_a_note_survives_a_forced_reextraction(tmp_path):
    """INV-S3. Touching the quotation forces run 2 to re-extract KERUI, which
    rebuilds VendorFacts from scratch — the note must be carried, not dropped."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    _note(root, "KERUI", "FULLY COMPLIED")

    (tmp_path / "p" / "vendors" / "KERUI" / _QUOTE).write_text(
        "base price 2000", encoding="utf-8")
    second = RfqClient()
    run_ingestion(root, "p", second)

    # RfqClient's "bid" answer is fixed regardless of context_text (see
    # tests/test_pipeline_rfq.py), so the re-extraction can't be proven by the
    # resulting price the way the brief's draft assumed — proven by call
    # count instead, the same signal test_pipeline_incremental.py uses.
    assert "bid" in second.calls, "the re-extraction did not happen"
    facts = snapshots.load_facts(root, "p", "KERUI")
    assert facts.technical_feedback == "FULLY COMPLIED"


def test_the_facts_record_which_document_priced_them(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())

    facts = snapshots.load_facts(root, "p", "KERUI")
    quote = next(d for d in snapshots.load_documents(root, "p")
                 if d.path.endswith(_QUOTE))
    assert facts.quotation_doc_id == quote.doc_id


def test_a_failed_requotation_keeps_the_note_and_the_link(tmp_path):
    """A provider outage on run 2 must not blank either field."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    _note(root, "KERUI", "FULLY COMPLIED")
    before = snapshots.load_facts(root, "p", "KERUI").quotation_doc_id

    (tmp_path / "p" / "vendors" / "KERUI" / _QUOTE).write_text(
        "base price 2000", encoding="utf-8")
    run_ingestion(root, "p", RfqClient(fail_on=("bid",)))

    facts = snapshots.load_facts(root, "p", "KERUI")
    assert facts.technical_feedback == "FULLY COMPLIED"
    assert facts.quotation_doc_id == before
