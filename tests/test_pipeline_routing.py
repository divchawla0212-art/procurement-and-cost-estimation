import io
import zipfile
from procurement import pipeline
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


_PROPOSAL = "Techno Commercial proposal AESL-GTC-60808 - REV00.txt"
_PROPOSAL_BODY = b"Base price 1840000 USD. Continuous rating 550 kW at site."


def _quotation_only_project(tmp_path, body=_PROPOSAL_BODY + _PAD):
    """One vendor, one document, and that document a quotation.

    AESL's actual submission in the live corpus, and the shape the secondary
    route exists for: nothing else the vendor sent reaches the technical
    extractor, so without it they are checkable against no requirement at all.
    """
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    z = tmp_path / "v.zip"
    z.write_bytes(_zip({f"AESL/{_PROPOSAL}": body}))
    unpack_vendor_zip(root, "p", str(z))
    return root


def _only_doc(root, vendor="AESL"):
    return next(d for d in snapshots.load_documents(root, "p") if d.vendor == vendor)


def test_a_quotation_only_vendor_contributes_technical_facts(tmp_path):
    root = _quotation_only_project(tmp_path)
    client = RoutingClient()
    run_ingestion(root, "p", client)

    doc = _only_doc(root)
    facts = snapshots.load_facts(root, "p", "AESL")
    # the fix: the proposal's technical content is read by something
    assert {f["doc_id"] for f in facts.technical} == {doc.doc_id}
    # and the three guarantees it must not break - the technical pass is
    # additional, never a replacement
    assert doc.doc_class == "quotation"          # routing never rewrites it
    assert facts.quotation_doc_id == doc.doc_id  # still the selected quotation
    assert facts.commercial["base_price"] == 1000.0
    assert doc.extraction_status == "ok"


def test_a_vendor_with_a_datasheet_does_not_double_extract_its_quotation(tmp_path):
    root = _project(tmp_path)          # KERUI: a quotation and two datasheets
    client = RoutingClient()
    run_ingestion(root, "p", client)

    quote = next(d for d in snapshots.load_documents(root, "p")
                 if d.path.endswith("Quotation of Gas Generator.txt"))
    facts = snapshots.load_facts(root, "p", "KERUI")
    assert quote.doc_id not in {f["doc_id"] for f in facts.technical}
    # asserted on the calls, not only on the stored facts: the secondary route
    # is bounded by need, so the point is that the model is never asked about
    # this document's parameters - not that its answer is thrown away.
    tech_calls = [c for c in client.calls if "facts" in c["prompt"]]
    assert len(tech_calls) == 2                  # the two datasheets, no more
    assert not any("base price 1000" in c["context_text"] for c in tech_calls)


def test_a_datasheet_arriving_later_withdraws_the_quotations_technical_facts(tmp_path):
    root = _quotation_only_project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    quote = _only_doc(root)
    assert {f["doc_id"] for f in snapshots.load_facts(root, "p", "AESL").technical} \
        == {quote.doc_id}

    (tmp_path / "p" / "vendors" / "AESL"
     / "01 DataSheet Gas Generator.txt").write_bytes(b"Continuous rating 550 kW" + _PAD)
    run_ingestion(root, "p", RoutingClient())

    datasheet = next(d for d in snapshots.load_documents(root, "p")
                     if d.path.endswith("01 DataSheet Gas Generator.txt"))
    facts = snapshots.load_facts(root, "p", "AESL")
    # the condition stopped holding, so the facts the quotation contributed are
    # pruned - not merely left unrefreshed by an extractor nothing re-runs
    assert {f["doc_id"] for f in facts.technical} == {datasheet.doc_id}
    # the quotation is still the quotation, and its commercial terms stand
    assert facts.quotation_doc_id == quote.doc_id
    assert facts.commercial["base_price"] == 1000.0


def test_the_two_passes_of_one_document_cache_independently(tmp_path, monkeypatch):
    # Both passes read the same bytes, so one cache key cannot serve them: a
    # technical prompt bump has to re-ask the technical pass without re-asking
    # (and re-paying for) the quotation one, and each has to persist what it
    # was answered under or the bump re-fires on every run forever.
    root = _quotation_only_project(tmp_path)
    run_ingestion(root, "p", RoutingClient())
    second = RoutingClient()
    run_ingestion(root, "p", second)
    assert second.calls == []                # both passes cached

    monkeypatch.setitem(pipeline.PROMPT_VERSION_BY_CLASS, "datasheet", "tech_facts_v2")
    third = RoutingClient()
    run_ingestion(root, "p", third)
    assert ["facts" in c["prompt"] for c in third.calls] == [True]

    doc = _only_doc(root)
    facts = snapshots.load_facts(root, "p", "AESL")
    assert doc.prompt_version == "bid_extract_v1"          # the primary stands
    assert doc.secondary_prompt_version == "tech_facts_v2"
    assert facts.commercial["base_price"] == 1000.0
    assert {f["doc_id"] for f in facts.technical} == {doc.doc_id}

    fourth = RoutingClient()
    run_ingestion(root, "p", fourth)
    assert fourth.calls == [], "the bumped version did not persist"


def test_a_failed_secondary_pass_keeps_the_primary_result_and_records_why(tmp_path):
    root = _quotation_only_project(tmp_path)
    run_ingestion(root, "p", FailingOnSchema("facts"))

    doc = _only_doc(root)
    facts = snapshots.load_facts(root, "p", "AESL")
    # the quotation was read and stored despite the technical pass failing
    assert doc.extraction_status == "ok"
    assert facts.commercial["base_price"] == 1000.0
    assert facts.quotation_doc_id == doc.doc_id
    # and the failure is recorded rather than swallowed
    assert doc.secondary_status == "failed"
    assert "provider unavailable" in doc.secondary_notes
    assert facts.technical == []

    # the failure is not cached as an answer: the next run re-asks the
    # technical pass, and only it
    second = RoutingClient()
    run_ingestion(root, "p", second)
    assert ["facts" in c["prompt"] for c in second.calls] == [True]
    assert snapshots.load_facts(root, "p", "AESL").technical


class TalkativeBidFailingOnFacts(FailingOnSchema):
    """A quotation extraction that succeeds *and* records a note, while the
    technical pass fails. `VendorBid.notes` is the vendor's own commercial
    prose - AESL's real record carries a payment-terms paragraph there - and
    the pipeline copies it onto `DocumentRecord.notes`, so this is the shape
    where a secondary failure has something it must not overwrite."""

    def __init__(self):
        super().__init__("facts")

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        response = super().classify_structure(prompt, output_schema,
                                              context_text, images=images)
        if "currency" in output_schema.model_fields:
            response = dict(response, notes="Prices exclude VAT.")
        return response


def test_a_failed_secondary_records_its_reason_in_notes_beside_the_primarys(tmp_path):
    # `notes` is the field every reader already knows about - the document
    # panel renders it only for a document whose status is not `ok`, and a
    # dual-routed document stays `ok` on a secondary failure, so a reason
    # recorded solely on `secondary_notes` is invisible to every existing
    # reader while a whole document went unread.
    root = _quotation_only_project(tmp_path)
    run_ingestion(root, "p", TalkativeBidFailingOnFacts())

    doc = _only_doc(root)
    assert doc.extraction_status == "ok"
    assert doc.secondary_status == "failed"
    # the secondary's reason is named
    assert "provider unavailable" in doc.notes
    assert "datasheet" in doc.notes
    # and the primary's own note survives it
    assert "Prices exclude VAT." in doc.notes
    # the structured copy stays beside it
    assert "provider unavailable" in doc.secondary_notes


def test_a_permanently_failing_secondary_does_not_grow_the_note(tmp_path):
    # The merged note is rebuilt from the primary's half each run, never
    # appended to: the primary is a cache hit from run 2 on, so a note that
    # were appended to would gain one copy of the reason per run, forever.
    root = _quotation_only_project(tmp_path)
    run_ingestion(root, "p", TalkativeBidFailingOnFacts())
    first = _only_doc(root).notes
    run_ingestion(root, "p", TalkativeBidFailingOnFacts())
    second = _only_doc(root).notes
    assert second == first
    assert second.count("provider unavailable") == 1
    assert second.count("Prices exclude VAT.") == 1

    # and once the pass succeeds, the stale reason is gone rather than stuck
    # to the record forever
    run_ingestion(root, "p", RoutingClient())
    doc = _only_doc(root)
    assert doc.secondary_status == "ok"
    assert doc.notes == "Prices exclude VAT."
    assert snapshots.load_facts(root, "p", "AESL").technical


def test_a_quotation_below_the_text_guard_gets_no_secondary_route(tmp_path):
    # Task 1's guard fires ahead of every extraction, primary or secondary: an
    # empty string is never handed to a prompt, and the document is `failed`
    # with its reason recorded rather than `skipped`.
    root = _quotation_only_project(tmp_path, body=b"scanned proposal")
    client = RoutingClient()
    run_ingestion(root, "p", client)

    doc = _only_doc(root)
    assert doc.extraction_status == "failed"
    assert "no readable text" in doc.notes
    assert doc.text_source
    assert not any("facts" in c["prompt"] for c in client.calls)
    assert snapshots.load_facts(root, "p", "AESL") is None


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
