import io
import zipfile
from procurement.project import create_project, unpack_vendor_zip
from procurement.pipeline import run_ingestion, PROMPT_VERSION_BY_CLASS
from procurement.classify import CLASSIFY_PROMPT_VERSION
from procurement.store import snapshots
from shared.llm.mock_client import MockLLMClient


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, d in entries.items():
            zf.writestr(n, d)
    return buf.getvalue()


# Padding appended to every synthetic fixture body that stands in for a real,
# extractable document: real quotations, datasheets and deviation forms are
# always well over MIN_EXTRACTABLE_CHARS, and a fixture short enough to trip
# the pipeline's no-readable-text guard would silently turn these into tests
# of the guard instead of the extraction logic they mean to exercise. Never
# applied to a drawing/other fixture — those are skipped by classification
# before the guard ever runs, and to any fixture that deliberately tests the
# guard itself.
_PAD = (b" This synthetic fixture body is padded with filler prose so its "
       b"character count clears the pipeline's minimum-extractable-text "
       b"guard, letting the extraction logic under test run rather than "
       b"the guard itself.")


def _project(tmp_path):
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    z = tmp_path / "v.zip"
    z.write_bytes(_zip({
        "KERUI/Quotation of Gas Generator.txt": b"base price 1000" + _PAD,
        "KERUI/01 DataSheet Gas Generator.txt": b"Continuous rating 550 kW" + _PAD,
        "KERUI/02 Detailed Vendor Standard Datasheet.txt": b"H2S tolerance 50 ppm" + _PAD,
        "KERUI/03 Attachment-2 Vendor Deviation Form.txt": b"Clause 4.2.7 deviation" + _PAD,
        "KERUI/15 LAYOUT - KGW550GF-T.txt": b"drawing, not extractable",
        "KERUI/08 Two Years Operation Spares.txt": b"spares list",
    }))
    unpack_vendor_zip(root, "p", str(z))
    return root


class RoutingClient:
    """Returns a shape appropriate to whichever prompt it is handed, and
    records the prompt version used for each call."""
    supports_vision = True

    def __init__(self):
        self.calls = []

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        self.calls.append({"prompt": prompt, "context_text": context_text})
        fields = output_schema.model_fields
        if "facts" in fields:
            return {"facts": [{"parameter": "continuous_rating", "value": 550.0,
                               "unit": "kW", "verbatim": "550 kW"}]}
        if "deviations" in fields:
            return {"deviations": [{"clause_ref": "4.2.7", "statement": "60 Hz",
                                    "disposition": "deviate"}]}
        if "doc_class" in fields:
            return {"doc_class": "other"}
        return {"currency": "USD", "base_price": 1000.0, "freight_included": True}


class FailingOnSchema(RoutingClient):
    """Behaves like RoutingClient, except it raises whenever the model is
    asked for the given output shape (identified by one of its fields) -
    used to simulate a transient extractor failure on a specific document
    class while leaving classification and the other extractors working."""

    def __init__(self, fail_field):
        super().__init__()
        self.fail_field = fail_field

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        if self.fail_field in output_schema.model_fields:
            raise RuntimeError("provider unavailable")
        return super().classify_structure(prompt, output_schema, context_text, images=images)


def test_every_document_is_classified(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    by_name = {d.path.rsplit("/", 1)[-1]: d for d in snapshots.load_documents(root, "p")}
    assert by_name["Quotation of Gas Generator.txt"].doc_class == "quotation"
    assert by_name["01 DataSheet Gas Generator.txt"].doc_class == "datasheet"
    assert by_name["03 Attachment-2 Vendor Deviation Form.txt"].doc_class == "deviation"
    assert by_name["15 LAYOUT - KGW550GF-T.txt"].doc_class == "drawing"
    assert by_name["08 Two Years Operation Spares.txt"].doc_class == "other"
    assert all(d.classified_by == "rule" for d in by_name.values())


def test_classification_records_the_prompt_version_used(tmp_path):
    # Every document here is decided by a filename rule, never the model, but
    # the cache-gating addition must still stamp classified_with so a later
    # bump of CLASSIFY_PROMPT_VERSION invalidates the cached rule-decided
    # classification too, not just LLM-decided ones.
    root = _project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    by_name = {d.path.rsplit("/", 1)[-1]: d for d in snapshots.load_documents(root, "p")}
    doc = by_name["Quotation of Gas Generator.txt"]
    assert doc.classified_by == "rule"
    assert doc.classified_with == CLASSIFY_PROMPT_VERSION == "doc_class_v1"


def test_datasheets_and_deviations_produce_stored_facts(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    facts = snapshots.load_facts(root, "p", "KERUI")
    assert facts.commercial["base_price"] == 1000.0
    assert len(facts.technical) == 2          # one fact from each of two datasheets
    assert len({f["doc_id"] for f in facts.technical}) == 2   # from distinct documents
    assert len({f["fact_id"] for f in facts.technical}) == 2  # ids do not collide
    assert len(facts.deviations) == 1
    assert facts.deviations[0]["disposition"] == "deviate"


def test_short_drawing_and_other_fixtures_fail_the_text_guard_not_the_route(tmp_path):
    # Formerly "skipped, not extracted": drawing and other now route to the
    # technical extractor (VENDOR_ROUTE), so these two no longer stop at the
    # routing gate. They still don't produce facts here, but for an unrelated
    # reason - their fixture bodies are the two short, unpadded strings in
    # _project, which trip the MIN_EXTRACTABLE_CHARS guard exactly as any
    # other too-short document would, and land on "failed" (the guard's
    # status), never "skipped" (the routing gate's).
    root = _project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    by_name = {d.path.rsplit("/", 1)[-1]: d for d in snapshots.load_documents(root, "p")}
    assert by_name["15 LAYOUT - KGW550GF-T.txt"].extraction_status == "failed"
    assert by_name["08 Two Years Operation Spares.txt"].extraction_status == "failed"
    assert "no readable text" in by_name["15 LAYOUT - KGW550GF-T.txt"].notes
    assert "no readable text" in by_name["08 Two Years Operation Spares.txt"].notes
    # neither document's doc_class is rewritten by routing
    assert by_name["15 LAYOUT - KGW550GF-T.txt"].doc_class == "drawing"
    assert by_name["08 Two Years Operation Spares.txt"].doc_class == "other"


def test_rerun_with_no_changes_makes_zero_llm_calls(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    second = RoutingClient()
    run_ingestion(root, "p", second)
    assert second.calls == []


def test_touching_one_datasheet_reextracts_only_that_document(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    (tmp_path / "p" / "vendors" / "KERUI"
     / "01 DataSheet Gas Generator.txt").write_bytes(b"Continuous rating 600 kW" + _PAD)
    second = RoutingClient()
    run_ingestion(root, "p", second)
    assert len(second.calls) == 1


def test_facts_from_untouched_datasheets_survive_a_partial_reextraction(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    before = snapshots.load_facts(root, "p", "KERUI")
    touched_doc = next(d.doc_id for d in snapshots.load_documents(root, "p")
                       if d.path.endswith("01 DataSheet Gas Generator.txt"))
    survivor = next(f for f in before.technical if f["doc_id"] != touched_doc)

    (tmp_path / "p" / "vendors" / "KERUI"
     / "01 DataSheet Gas Generator.txt").write_bytes(b"Continuous rating 600 kW" + _PAD)
    run_ingestion(root, "p", RoutingClient())

    after = snapshots.load_facts(root, "p", "KERUI")
    assert len(after.technical) == len(before.technical)   # no duplication, no loss
    # the untouched datasheet's fact is preserved byte-for-byte
    assert survivor in after.technical
    # and the re-extracted document still contributes exactly one fact
    assert len([f for f in after.technical if f["doc_id"] == touched_doc]) == 1


def test_superseded_document_is_not_extracted(tmp_path):
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    z = tmp_path / "v.zip"
    z.write_bytes(_zip({
        "ADPOWER/Quotation ADP-935.txt": b"base price 1000" + _PAD,
        "ADPOWER/Quotation ADP-935(Rev1).txt": b"base price 1100" + _PAD,
    }))
    unpack_vendor_zip(root, "p", str(z))
    run_ingestion(root, "p", RoutingClient())
    docs = {d.path.rsplit("/", 1)[-1]: d for d in snapshots.load_documents(root, "p")}
    old = docs["Quotation ADP-935.txt"]
    new = docs["Quotation ADP-935(Rev1).txt"]
    assert old.superseded_by == new.doc_id
    assert old.extraction_status == "skipped"
    assert "superseded" in old.notes
    assert new.extraction_status == "ok"


def test_failed_datasheet_reextraction_preserves_prior_facts(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    before = snapshots.load_facts(root, "p", "KERUI")
    touched_doc = next(d.doc_id for d in snapshots.load_documents(root, "p")
                       if d.path.endswith("01 DataSheet Gas Generator.txt"))
    before_fact = next(f for f in before.technical if f["doc_id"] == touched_doc)

    (tmp_path / "p" / "vendors" / "KERUI"
     / "01 DataSheet Gas Generator.txt").write_bytes(b"Continuous rating 600 kW" + _PAD)
    run_ingestion(root, "p", FailingOnSchema("facts"))

    after = snapshots.load_facts(root, "p", "KERUI")
    # the previously-good fact for the now-failing document must survive,
    # byte-for-byte, rather than being wiped by the empty result of the
    # failed re-extraction
    assert before_fact in after.technical
    assert len(after.technical) == len(before.technical)
    docs = {d.doc_id: d for d in snapshots.load_documents(root, "p")}
    assert docs[touched_doc].extraction_status == "failed"


def test_failed_deviation_reextraction_preserves_prior_facts(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    before = snapshots.load_facts(root, "p", "KERUI")
    touched_doc = next(d.doc_id for d in snapshots.load_documents(root, "p")
                       if d.path.endswith("03 Attachment-2 Vendor Deviation Form.txt"))
    before_deviation = next(d for d in before.deviations if d["doc_id"] == touched_doc)

    (tmp_path / "p" / "vendors" / "KERUI"
     / "03 Attachment-2 Vendor Deviation Form.txt").write_bytes(b"Clause 9.9.9 deviation revised")
    run_ingestion(root, "p", FailingOnSchema("deviations"))

    after = snapshots.load_facts(root, "p", "KERUI")
    assert before_deviation in after.deviations
    assert len(after.deviations) == len(before.deviations)
    docs = {d.doc_id: d for d in snapshots.load_documents(root, "p")}
    assert docs[touched_doc].extraction_status == "failed"


def test_failed_quotation_extraction_records_notes(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", FailingOnSchema("currency"))
    docs = {d.path.rsplit("/", 1)[-1]: d for d in snapshots.load_documents(root, "p")}
    quote = docs["Quotation of Gas Generator.txt"]
    assert quote.extraction_status == "failed"
    assert quote.notes
    assert "provider unavailable" in quote.notes


def test_prompt_versions_are_per_class():
    assert PROMPT_VERSION_BY_CLASS["quotation"] == "bid_extract_v1"
    assert PROMPT_VERSION_BY_CLASS["datasheet"] == "tech_facts_v1"
    assert PROMPT_VERSION_BY_CLASS["deviation"] == "deviation_v1"


def test_a_document_with_no_readable_text_is_failed_with_a_reason(tmp_path):
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    z = tmp_path / "v.zip"
    z.write_bytes(_zip({
        "KERUI/Quotation of Gas Generator.txt": b"base price 1000" + _PAD,
        "KERUI/01 DataSheet Gas Generator.txt": b"x",     # 1 char: unreadable
        # 21 chars: squarely inside the 1-42 range the live KERUI scanned
        # drawings actually yield (see loaders.MIN_EXTRACTABLE_CHARS's
        # comment) — the 1-char extreme above would not by itself catch a
        # threshold set too low to guard this middle of the range.
        "KERUI/02 Detailed Vendor Standard Datasheet.txt": b"drawing, no real text",
    }))
    unpack_vendor_zip(root, "p", str(z))
    client = RoutingClient()
    run_ingestion(root, "p", client)

    docs = {d.path.rsplit("/", 1)[-1]: d for d in snapshots.load_documents(root, "p")}
    doc = docs["01 DataSheet Gas Generator.txt"]
    assert doc.extraction_status == "failed"
    assert "no readable text" in doc.notes
    assert doc.text_source is not None

    scanned = docs["02 Detailed Vendor Standard Datasheet.txt"]
    assert scanned.extraction_status == "failed"
    assert "no readable text" in scanned.notes
    assert scanned.text_source is not None

    # the guard fires before the model is asked, so no facts prompt was sent
    assert not any("facts" in c.get("prompt", "") for c in client.calls)


def test_every_extracted_document_records_its_text_source(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    for doc in snapshots.load_documents(root, "p"):
        if doc.vendor and doc.extraction_status in ("ok", "failed"):
            assert doc.text_source, f"{doc.path} has no text_source"


def _wide_project(tmp_path):
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    z = tmp_path / "v.zip"
    z.write_bytes(_zip({
        "ADPOWER/ADP-13158-2024-935.txt": b"quotation, base price 1110836 AED" + _PAD,
        # ADPOWER's marked-up copy of the client MR: their compliance response
        "ADPOWER/ADN-AEC-ME-SPC-026 MR Gas Genset copy.txt":
            b"Continuous rating 525 kW offered. Insulation Class F." + _PAD,
        "ADPOWER/BOM.txt": b"Bill of material: engine MAN, alternator Stamford" + _PAD,
        "ADPOWER/09 Attachment-1 International Codes and Standards.txt":
            b"IEC 60034-1 complied. ISO 8528 complied." + _PAD,
    }))
    unpack_vendor_zip(root, "p", str(z))
    return root


def test_a_vendors_marked_up_spec_contributes_technical_facts(tmp_path):
    root = _wide_project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    facts = snapshots.load_facts(root, "p", "ADPOWER")
    doc_ids = {f["doc_id"] for f in facts.technical}
    spec = next(d for d in snapshots.load_documents(root, "p")
                if d.path.endswith("MR Gas Genset copy.txt"))
    assert spec.doc_class == "spec"          # classification is NOT rewritten
    assert spec.extraction_status == "ok"
    assert spec.doc_id in doc_ids


def test_bom_and_other_documents_contribute_technical_facts(tmp_path):
    root = _wide_project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    docs = {d.path.rsplit("/", 1)[-1]: d for d in snapshots.load_documents(root, "p")}
    assert docs["BOM.txt"].extraction_status == "ok"
    assert docs["09 Attachment-1 International Codes and Standards.txt"].extraction_status == "ok"
    facts = snapshots.load_facts(root, "p", "ADPOWER")
    assert {docs["BOM.txt"].doc_id,
            docs["09 Attachment-1 International Codes and Standards.txt"].doc_id
            } <= {f["doc_id"] for f in facts.technical}


def test_the_quotation_is_still_the_only_source_of_commercial_terms(tmp_path):
    root = _wide_project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    facts = snapshots.load_facts(root, "p", "ADPOWER")
    quote = next(d for d in snapshots.load_documents(root, "p")
                 if d.path.endswith("ADP-13158-2024-935.txt"))
    assert facts.quotation_doc_id == quote.doc_id


def test_a_document_that_raises_on_read_is_failed_not_crashed(tmp_path):
    """The other half of the guard: `read_text_with_source` doesn't only
    return short text for an unreadable document, it can raise outright (a
    legacy binary .doc is the one reader in loaders.py that always does). The
    vendor loop's `except Exception` around that read must catch it, mark the
    document failed with the reason, and let the run continue rather than
    propagating — nothing in the suite exercised this branch before."""
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    z = tmp_path / "v.zip"
    z.write_bytes(_zip({
        "KERUI/Quotation of Gas Generator.txt": b"base price 1000" + _PAD,
        # filename rule alone decides "datasheet" (classify_by_rules never
        # reads content), so this reaches the vendor loop's text read, where
        # read_text_with_source raises ValueError on legacy binary .doc.
        "KERUI/01 DataSheet Gas Generator.doc": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64,
    }))
    unpack_vendor_zip(root, "p", str(z))
    client = RoutingClient()
    run_ingestion(root, "p", client)

    docs = {d.path.rsplit("/", 1)[-1]: d for d in snapshots.load_documents(root, "p")}
    doc = docs["01 DataSheet Gas Generator.doc"]
    assert doc.extraction_status == "failed"
    assert doc.notes and "unreadable document" in doc.notes
    assert doc.text_source == "unreadable:0chars"
    # the guard fires before the model is asked, so no facts prompt was sent
    assert not any("facts" in c.get("prompt", "") for c in client.calls)
    # the run itself must not have aborted: the quotation beside it still
    # reached the extractor and succeeded
    quote = docs["Quotation of Gas Generator.txt"]
    assert quote.extraction_status == "ok"
