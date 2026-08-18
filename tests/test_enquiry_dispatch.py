"""The dispatch: resolve each shortlisted vendor to addresses or a skip
reason, build one message per vendor, send it, and record what happened.

Key-free and fixture-free — everything here is built with `WorkflowStore` and
`LocalBlobStore` directly, the same shape `test_rfq_documents.py` uses.
"""
import io
from datetime import date

import pytest

from workflow import doc_store, enquiry
from workflow.models.mail import OutboundMail, SentRef
from workflow.models.rfq_document import RfqDocument
from workflow.models.vendor_contact import VendorContact
from workflow.store import WorkflowStore


class RecordingTransport:
    """Captures what would be sent. Real enough to assert against, and it
    reaches nobody."""

    def __init__(self):
        self.sent: list[OutboundMail] = []

    def send(self, message: OutboundMail) -> SentRef:
        self.sent.append(message)
        return SentRef(
            message_id=f"<{len(self.sent)}@test>", transport="outbox",
            location=f"/tmp/{len(self.sent)}.eml",
        )


# -- fixtures -----------------------------------------------------------------


def _rfq(store: WorkflowStore) -> str:
    project = store.create_project(
        name="Test Project",
        code="TP-1",
        client="ADNOC",
        location="Abu Dhabi",
        live_period_start=date(2026, 1, 1),
        live_period_end=date(2026, 12, 31),
    )
    item = store.create_item(
        project_id=project.id,
        item_type="Equipment",
        description="Pumps",
        qty=1,
        uom="No",
        discipline="Mechanical",
        estimated_value_aed=100_000,
    )
    rfq = store.create_rfq(
        project_id=project.id,
        item_ids=[item.id],
        reference="RFQ-1",
        package="Pumps package",
        discipline="Mechanical",
        value_estimate_aed=100_000,
    )
    return rfq.id


def _add_enquiry_document(store, blobs, rfq_id, filename="enquiry.pdf", data=b"%PDF-1.4 enquiry"):
    ref = blobs.put(rfq_id, filename, io.BytesIO(data))
    store.add_rfq_document(RfqDocument(
        rfq_id=rfq_id,
        filename=filename,
        rel_path=filename,
        sha256=ref.sha256,
        size_bytes=ref.size,
        content_type="application/pdf",
        uploaded_by="buyer@adp.ae",
        uploaded_at="2026-08-19T09:00:00Z",
        submitted_by_vendor_id=None,
    ))


@pytest.fixture
def two_vendor_rfq(tmp_path):
    store = WorkflowStore()
    rfq_id = _rfq(store)
    store.set_vendor_contacts([
        VendorContact(
            vendor_name="Galfar", emails=["sales@galfar.example"],
            source_document="sheet.xlsx", uploaded_by="buyer@adp.ae",
            uploaded_at="2026-08-19T09:00:00Z",
        ),
        VendorContact(
            vendor_name="OQC", emails=["sales@oqc.example"],
            source_document="sheet.xlsx", uploaded_by="buyer@adp.ae",
            uploaded_at="2026-08-19T09:00:00Z",
        ),
    ])
    store.add_shortlist_entry(
        rfq_id, vendor_name="Galfar", prequal_status="Qualified", scope_code_fit=True,
    )
    store.add_shortlist_entry(
        rfq_id, vendor_name="OQC", prequal_status="Qualified", scope_code_fit=True,
    )

    blobs = doc_store.LocalBlobStore(str(tmp_path))
    _add_enquiry_document(store, blobs, rfq_id)

    return store, blobs, rfq_id


@pytest.fixture
def rfq_with_an_unreachable_vendor(tmp_path):
    store = WorkflowStore()
    rfq_id = _rfq(store)
    store.add_shortlist_entry(
        rfq_id, vendor_name="Nowhere Trading", prequal_status="Qualified", scope_code_fit=True,
    )
    blobs = doc_store.LocalBlobStore(str(tmp_path))
    _add_enquiry_document(store, blobs, rfq_id)
    return store, blobs, rfq_id


@pytest.fixture
def rfq_with_a_returned_bid(tmp_path):
    store = WorkflowStore()
    rfq_id = _rfq(store)
    store.set_vendor_contacts([
        VendorContact(
            vendor_name="Galfar", emails=["sales@galfar.example"],
            source_document="sheet.xlsx", uploaded_by="buyer@adp.ae",
            uploaded_at="2026-08-19T09:00:00Z",
        ),
    ])
    store.add_shortlist_entry(
        rfq_id, vendor_name="Galfar", prequal_status="Qualified", scope_code_fit=True,
    )

    blobs = doc_store.LocalBlobStore(str(tmp_path))
    _add_enquiry_document(store, blobs, rfq_id)

    bid_ref = blobs.put(rfq_id, "galfar-bid.pdf", io.BytesIO(b"%PDF-1.4 galfar bid"))
    store.add_rfq_document(RfqDocument(
        rfq_id=rfq_id,
        filename="galfar-bid.pdf",
        rel_path="galfar-bid.pdf",
        sha256=bid_ref.sha256,
        size_bytes=bid_ref.size,
        content_type="application/pdf",
        uploaded_by="galfar@vendor.example",
        uploaded_at="2026-08-19T09:05:00Z",
        submitted_by_vendor_id="some-vendor-id",
    ))
    return store, blobs, rfq_id


@pytest.fixture
def rfq_with_a_huge_package(tmp_path, monkeypatch):
    # Never write 25 MB to disk for this test: shrink the cap instead, well
    # below the enquiry document's actual size.
    monkeypatch.setattr(enquiry, "MAX_ATTACHMENT_BYTES", 10)

    store = WorkflowStore()
    rfq_id = _rfq(store)
    store.set_vendor_contacts([
        VendorContact(
            vendor_name="Galfar", emails=["sales@galfar.example"],
            source_document="sheet.xlsx", uploaded_by="buyer@adp.ae",
            uploaded_at="2026-08-19T09:00:00Z",
        ),
    ])
    store.add_shortlist_entry(
        rfq_id, vendor_name="Galfar", prequal_status="Qualified", scope_code_fit=True,
    )

    blobs = doc_store.LocalBlobStore(str(tmp_path))
    _add_enquiry_document(store, blobs, rfq_id, data=b"%PDF-1.4 well over the ten byte cap")
    return store, blobs, rfq_id


# -- tests ----------------------------------------------------------------


def test_each_vendor_gets_their_own_message(two_vendor_rfq):
    """Asserted across the whole dispatch, not on one message: a per-message
    assertion passes against a loop that sends the same all-recipients message
    twice."""
    store, blobs, rfq_id = two_vendor_rfq
    transport = RecordingTransport()

    enquiry.dispatch(store, blobs, transport, rfq_id, by="buyer@adp.ae")

    assert len(transport.sent) == 2
    for message in transport.sent:
        assert len(message.to) >= 1
    everyone = [address for m in transport.sent for address in m.to]
    assert len(everyone) == len(set(everyone))


def test_no_message_carries_two_vendors_addresses(two_vendor_rfq):
    store, blobs, rfq_id = two_vendor_rfq
    transport = RecordingTransport()

    enquiry.dispatch(store, blobs, transport, rfq_id, by="buyer@adp.ae")

    galfar = {"sales@galfar.example"}
    oqc = {"sales@oqc.example"}
    for message in transport.sent:
        addresses = set(message.to)
        assert addresses <= galfar or addresses <= oqc


def test_a_vendor_with_no_address_is_skipped_and_named(rfq_with_an_unreachable_vendor):
    store, blobs, rfq_id = rfq_with_an_unreachable_vendor
    transport = RecordingTransport()

    result = enquiry.dispatch(store, blobs, transport, rfq_id, by="buyer@adp.ae")

    assert [s.vendor_name for s in result.skipped] == ["Nowhere Trading"]
    assert "no address" in result.skipped[0].reason.lower()
    assert transport.sent == []


def test_no_vendors_bid_is_ever_attached_to_an_enquiry(rfq_with_a_returned_bid):
    """The enquiry carries the contractor's documents only. Attaching a bid
    would put one bidder's pricing in front of another."""
    store, blobs, rfq_id = rfq_with_a_returned_bid
    transport = RecordingTransport()

    enquiry.dispatch(store, blobs, transport, rfq_id, by="buyer@adp.ae")

    names = [a.filename for m in transport.sent for a in m.attachments]
    assert "enquiry.pdf" in names
    assert "galfar-bid.pdf" not in names


def test_a_package_over_the_cap_skips_the_vendor_rather_than_failing(rfq_with_a_huge_package):
    """The reason names the cap that actually applied. The fixture shrinks
    `enquiry.MAX_ATTACHMENT_BYTES` well below the package's real size rather
    than writing 25 MB to disk, so the assertion has to track the same
    constant rather than hardcode the production value of 25 (MB) — a
    hardcoded "25" would pass against a reason that misstates the limit it
    enforces."""
    store, blobs, rfq_id = rfq_with_a_huge_package
    transport = RecordingTransport()

    result = enquiry.dispatch(store, blobs, transport, rfq_id, by="buyer@adp.ae")

    assert transport.sent == []
    assert f"{enquiry.MAX_ATTACHMENT_BYTES} bytes" in result.skipped[0].reason


def test_a_second_dispatch_reaches_only_the_newly_shortlisted(two_vendor_rfq):
    store, blobs, rfq_id = two_vendor_rfq
    first = RecordingTransport()
    enquiry.dispatch(store, blobs, first, rfq_id, by="buyer@adp.ae")

    second = RecordingTransport()
    result = enquiry.dispatch(store, blobs, second, rfq_id, by="buyer@adp.ae")

    assert second.sent == []
    assert len(result.skipped) == 2
    assert all("already" in s.reason.lower() for s in result.skipped)


def test_preview_stores_nothing(two_vendor_rfq):
    store, blobs, rfq_id = two_vendor_rfq

    rows = enquiry.preview(store, blobs, rfq_id)

    assert len(rows) == 2
    assert store.enquiry_sends_for(rfq_id) == []


def test_preview_shows_the_reason_a_vendor_would_be_skipped(rfq_with_an_unreachable_vendor):
    """This is the whole point of the preview: a wrong name match shows as an
    address, and a miss shows as a sentence, before anything leaves."""
    store, blobs, rfq_id = rfq_with_an_unreachable_vendor

    rows = enquiry.preview(store, blobs, rfq_id)

    assert rows[0].to is None
    assert "no address" in rows[0].skip_reason.lower()
