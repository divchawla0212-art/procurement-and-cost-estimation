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
from workflow.models.rfq_document import EligibilityCategory, RfqDocument
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


def _add_enquiry_document(
    store, blobs, rfq_id, filename="enquiry.pdf", data=b"%PDF-1.4 enquiry",
    # Unambiguously in the past. It was "2026-08-19T09:00:00Z", which is a few
    # hours *ahead* of the wall clock on the day these tests were written --
    # harmless until the audience rules started comparing a document's stamp
    # against a send's `datetime.now()`, at which point the fixture's meaning
    # depended on the hour the suite happened to run.
    uploaded_at="2026-08-01T09:00:00Z",
):
    ref = blobs.put(rfq_id, filename, io.BytesIO(data))
    store.add_rfq_document(RfqDocument(
        rfq_id=rfq_id,
        filename=filename,
        rel_path=filename,
        sha256=ref.sha256,
        size_bytes=ref.size,
        content_type="application/pdf",
        uploaded_by="buyer@adp.ae",
        uploaded_at=uploaded_at,
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


# -- the body: what every bidder is actually told ------------------------------


def test_the_body_carries_the_eligibility_checklist(two_vendor_rfq):
    """The checklist reaches the vendor, not just the module that builds it.
    Asserted against the vocabulary rather than against nine strings, so this
    follows a rename instead of pinning the wording of the day."""
    store, blobs, rfq_id = two_vendor_rfq
    transport = RecordingTransport()

    enquiry.dispatch(store, blobs, transport, rfq_id, by="buyer@adp.ae")

    assert transport.sent
    for message in transport.sent:
        for category in EligibilityCategory:
            assert category.value in message.body


def test_the_body_names_the_project_and_the_enquiry(two_vendor_rfq):
    store, blobs, rfq_id = two_vendor_rfq
    transport = RecordingTransport()

    enquiry.dispatch(store, blobs, transport, rfq_id, by="buyer@adp.ae")

    rfq = store.get_rfq(rfq_id)
    project = store.get_project(rfq.project_id)
    for message in transport.sent:
        assert rfq.reference in message.body
        assert project.name in message.body


def test_the_body_names_every_attached_document(two_vendor_rfq):
    """Item 1 of the change: the datasheet rides along *and* is named, so a
    reader can tell at a glance whether anything failed to arrive."""
    store, blobs, rfq_id = two_vendor_rfq
    transport = RecordingTransport()

    enquiry.dispatch(store, blobs, transport, rfq_id, by="buyer@adp.ae")

    names = {a.filename for a in transport.sent[0].attachments}
    assert names, "fixture sends no attachments, so this asserts nothing"
    for message in transport.sent:
        for name in names:
            assert name in message.body


def test_every_vendor_receives_an_identical_body(two_vendor_rfq):
    """The one-message-per-vendor rule reaching into the content. A body built
    inside the loop could vary per recipient; one built once cannot, and a
    body that differs per vendor is how a competitor's name leaks into prose
    that no address assertion would ever catch."""
    store, blobs, rfq_id = two_vendor_rfq
    transport = RecordingTransport()

    enquiry.dispatch(store, blobs, transport, rfq_id, by="buyer@adp.ae")

    assert len({message.body for message in transport.sent}) == 1


def test_no_body_discloses_the_value_estimate(two_vendor_rfq):
    """The estimate is the contractor's budget for the work. A bidder who reads
    it knows the number to beat, and the tender is over."""
    store, blobs, rfq_id = two_vendor_rfq
    transport = RecordingTransport()

    enquiry.dispatch(store, blobs, transport, rfq_id, by="buyer@adp.ae")

    estimate = store.get_rfq(rfq_id).value_estimate_aed
    assert estimate, "fixture has no estimate, so this asserts nothing"
    for message in transport.sent:
        for rendering in (str(estimate), f"{estimate:,}"):
            assert rendering not in message.body


# -- sending the enquiry a second time, deliberately ---------------------------


def test_a_resend_reaches_the_vendors_already_sent_to(two_vendor_rfq):
    """The point of the flag. Without it the second dispatch reaches nobody —
    which is what `test_a_second_dispatch_reaches_only_the_newly_shortlisted`
    asserts, and that test is what proves this one is not vacuous."""
    store, blobs, rfq_id = two_vendor_rfq
    first = RecordingTransport()
    enquiry.dispatch(store, blobs, first, rfq_id, by="buyer@adp.ae")
    assert len(first.sent) == 2

    again = RecordingTransport()
    result = enquiry.dispatch(
        store, blobs, again, rfq_id, by="buyer@adp.ae", audience=enquiry.EVERYONE,
    )

    assert len(again.sent) == 2
    assert len(result.sent) == 2
    assert result.skipped == []


def test_a_resend_appends_a_second_record_per_vendor(two_vendor_rfq):
    """One record per send. Four records after two dispatches over two vendors
    — not two updated in place, which would destroy the first send's date."""
    store, blobs, rfq_id = two_vendor_rfq
    enquiry.dispatch(store, blobs, RecordingTransport(), rfq_id, by="buyer@adp.ae")
    enquiry.dispatch(
        store, blobs, RecordingTransport(), rfq_id, by="buyer@adp.ae",
        audience=enquiry.EVERYONE,
    )

    records = store.enquiry_sends_for(rfq_id)
    assert len(records) == 4
    assert len({r.shortlist_entry_id for r in records}) == 2


def test_sending_to_everyone_still_skips_a_vendor_with_no_address(
    rfq_with_an_unreachable_vendor,
):
    """The widest audience waives exactly one of the three skip conditions. A
    vendor with no address on file is unreachable however many times you ask,
    and its `OUTDATED` sibling asserts the same for the middle audience."""
    store, blobs, rfq_id = rfq_with_an_unreachable_vendor

    result = enquiry.dispatch(
        store, blobs, RecordingTransport(), rfq_id, by="buyer@adp.ae",
        audience=enquiry.EVERYONE,
    )

    assert [s.vendor_name for s in result.skipped] == ["Nowhere Trading"]


def test_the_preview_shows_the_resend_recipients_rather_than_skips(two_vendor_rfq):
    """The buyer confirms the list that will actually go. A preview built
    without the flag would show two 'already sent' rows and then the send would
    mail them anyway — a confirmation screen that lies about its own action."""
    store, blobs, rfq_id = two_vendor_rfq
    enquiry.dispatch(store, blobs, RecordingTransport(), rfq_id, by="buyer@adp.ae")

    plain = enquiry.preview(store, blobs, rfq_id)
    assert all(r.to is None for r in plain)

    again = enquiry.preview(store, blobs, rfq_id, audience=enquiry.EVERYONE)
    assert all(r.to is not None for r in again)
    assert all(r.skip_reason is None for r in again)


# -- who the enquiry goes to: unsent, outdated, or everyone --------------------
#
# The three audiences are deliberately **nested**: unsent ⊆ outdated ⊆ all. A
# vendor never sent to has certainly not seen the latest documents, so
# "outdated" includes them rather than being a disjoint third bucket a buyer
# has to remember to run as well.


def test_the_default_audience_is_the_vendors_never_sent_to(two_vendor_rfq):
    store, blobs, rfq_id = two_vendor_rfq
    enquiry.dispatch(store, blobs, RecordingTransport(), rfq_id, by="buyer@adp.ae")

    rows = enquiry.preview(store, blobs, rfq_id)
    assert all(r.to is None for r in rows)
    assert all("already been sent" in r.skip_reason for r in rows)


def test_nobody_is_outdated_until_a_document_is_added(two_vendor_rfq):
    """The whole point of the option. Right after a send everybody holds the
    current package, so asking for the outdated ones must reach nobody —
    otherwise the control is just `all` under a friendlier name.

    The package is stamped in the past rather than left at the fixture's fixed
    hour, because `sent_at` is the store's own `datetime.now()` and cannot be
    set: pinning only one side of the comparison races the wall clock.
    """
    store, blobs, rfq_id = two_vendor_rfq
    enquiry.dispatch(store, blobs, RecordingTransport(), rfq_id, by="buyer@adp.ae")

    rows = enquiry.preview(store, blobs, rfq_id, audience=enquiry.OUTDATED)
    assert all(r.to is None for r in rows)
    assert all("latest documents" in r.skip_reason for r in rows)


def test_a_document_added_after_a_send_makes_that_vendor_outdated(two_vendor_rfq):
    store, blobs, rfq_id = two_vendor_rfq
    enquiry.dispatch(store, blobs, RecordingTransport(), rfq_id, by="buyer@adp.ae")

    _add_enquiry_document(
        store, blobs, rfq_id, filename="addendum.pdf", data=b"%PDF new",
        uploaded_at="2099-01-01T00:00:00Z",
    )

    rows = enquiry.preview(store, blobs, rfq_id, audience=enquiry.OUTDATED)
    assert all(r.to is not None for r in rows)
    assert all(r.skip_reason is None for r in rows)


def test_dispatching_to_the_outdated_sends_the_new_document(two_vendor_rfq):
    store, blobs, rfq_id = two_vendor_rfq
    enquiry.dispatch(store, blobs, RecordingTransport(), rfq_id, by="buyer@adp.ae")
    _add_enquiry_document(
        store, blobs, rfq_id, filename="addendum.pdf", data=b"%PDF new",
        uploaded_at="2099-01-01T00:00:00Z",
    )

    again = RecordingTransport()
    result = enquiry.dispatch(
        store, blobs, again, rfq_id, by="buyer@adp.ae", audience=enquiry.OUTDATED,
    )

    assert len(result.sent) == 2
    for message in again.sent:
        assert "addendum.pdf" in {a.filename for a in message.attachments}
        assert "addendum.pdf" in message.body


def test_a_vendor_never_sent_to_counts_as_outdated(two_vendor_rfq):
    """unsent ⊆ outdated. A buyer who adds a document and asks for the vendors
    behind on it must not silently miss the one who was shortlisted late and
    has had nothing at all."""
    store, blobs, rfq_id = two_vendor_rfq
    rows = enquiry.preview(store, blobs, rfq_id, audience=enquiry.OUTDATED)

    assert all(r.to is not None for r in rows)


def test_everyone_reaches_the_up_to_date_as_well(two_vendor_rfq):
    """outdated ⊆ all. Nobody has a new document here, so `outdated` reaches
    nobody while `all` still reaches both — which is what tells the two apart."""
    store, blobs, rfq_id = two_vendor_rfq
    enquiry.dispatch(store, blobs, RecordingTransport(), rfq_id, by="buyer@adp.ae")

    outdated = enquiry.preview(store, blobs, rfq_id, audience=enquiry.OUTDATED)
    everyone = enquiry.preview(store, blobs, rfq_id, audience=enquiry.EVERYONE)

    assert all(r.to is None for r in outdated)
    assert all(r.to is not None for r in everyone)


def test_an_outdated_send_still_skips_a_vendor_with_no_address(
    rfq_with_an_unreachable_vendor,
):
    """Audience widens who is *asked for*; it never invents a way to reach
    somebody the directory has no address for."""
    store, blobs, rfq_id = rfq_with_an_unreachable_vendor

    result = enquiry.dispatch(
        store, blobs, RecordingTransport(), rfq_id, by="buyer@adp.ae",
        audience=enquiry.OUTDATED,
    )

    assert [s.vendor_name for s in result.skipped] == ["Nowhere Trading"]


def test_an_unknown_audience_is_refused_rather_than_read_as_the_default():
    """A typo must not quietly mean `unsent`. Reaching nobody looks identical
    on screen to 'everybody is up to date', which is the failure the approver
    filter's 422 already exists to prevent."""
    with pytest.raises(ValueError) as exc:
        enquiry.check_audience("evryone")
    assert "evryone" in str(exc.value)


def test_the_enquiry_can_be_sent_as_many_times_as_the_sender_wants(two_vendor_rfq):
    """No cap, and nothing about the RFQ has to change between sends.

    `EVERYONE` is not "send a second time" — it is "send now, again", and it
    holds on the fifth press exactly as on the first. Each pass appends one
    record per vendor, so the log grows rather than being overwritten, and
    nobody is ever skipped for having been reached already.
    """
    store, blobs, rfq_id = two_vendor_rfq

    for attempt in range(5):
        transport = RecordingTransport()
        result = enquiry.dispatch(
            store, blobs, transport, rfq_id, by="buyer@adp.ae",
            audience=enquiry.EVERYONE,
        )
        assert len(transport.sent) == 2, f"pass {attempt} reached {len(transport.sent)}"
        assert result.skipped == []

    assert len(store.enquiry_sends_for(rfq_id)) == 10


def test_repeated_sending_needs_no_change_to_the_rfq(two_vendor_rfq):
    """The distinction the buyer asked about. `OUTDATED` is gated on the
    package changing; `EVERYONE` is not gated on anything, so an unchanged RFQ
    can still go out again."""
    store, blobs, rfq_id = two_vendor_rfq
    enquiry.dispatch(store, blobs, RecordingTransport(), rfq_id, by="buyer@adp.ae")

    # Nothing added, nothing edited.
    stale = enquiry.preview(store, blobs, rfq_id, audience=enquiry.OUTDATED)
    assert all(r.to is None for r in stale)

    everyone = enquiry.preview(store, blobs, rfq_id, audience=enquiry.EVERYONE)
    assert all(r.to is not None for r in everyone)
