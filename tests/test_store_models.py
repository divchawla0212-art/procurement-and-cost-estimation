from procurement.store.models import DocumentRecord, Override, Event, VendorFacts
from procurement.models import Project


def test_document_record_defaults():
    d = DocumentRecord(doc_id="abc123def456", path="vendors/KERUI/Q.pdf",
                       vendor="KERUI", content_sha256="0" * 64)
    assert d.doc_class == "unclassified"
    assert d.extraction_status == "pending"
    assert d.superseded_by is None


def test_override_defaults_to_no_conflict():
    o = Override(field_path="commercial.base_price", value=1200.0,
                 extracted_value=1000.0, author="rahul",
                 at="2026-07-29T10:00:00Z", reason="typo in quote")
    assert o.conflict is False


def test_vendor_facts_round_trips_through_json():
    f = VendorFacts(vendor="KERUI", commercial={"base_price": 1000.0},
                    overrides=[Override(field_path="commercial.base_price",
                                        value=1200.0, author="rahul",
                                        at="2026-07-29T10:00:00Z",
                                        reason="corrected")])
    restored = VendorFacts.model_validate(f.model_dump())
    assert restored.overrides[0].value == 1200.0
    assert restored.technical == [] and restored.deviations == []


def test_event_carries_run_id_and_actor():
    e = Event(at="2026-07-29T10:00:00Z", run_id="r1", actor="pipeline",
              action="document.extracted", target="abc123def456")
    assert e.detail == {}


def test_project_gains_generation_and_store_version():
    p = Project(name="P", slug="p", created_at="2026-07-29T00:00:00Z")
    assert p.generation == 0
    assert p.store_version == 1
