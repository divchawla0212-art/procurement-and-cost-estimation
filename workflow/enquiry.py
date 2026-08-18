"""Dispatching the enquiry: one message per shortlisted vendor.

**One message per vendor, always.** A single message addressed to the whole
shortlist discloses every bidder's identity to their competitors, and a
tender that leaks it has to be re-run. `dispatch` builds a fresh
`OutboundMail` inside the per-vendor loop — never one built outside it and
mutated per iteration, which is how every bidder would end up on one `To:`
line.

**Only the contractor's documents travel.** `RfqDocument.submitted_by_vendor_id`
is `None` for a document the contractor issued and a vendor id for one a
bidder returned; attaching the latter would put one bidder's pricing in front
of another, so `_attachments` skips it.

**Exactly three conditions are absorbed into a skip, and nothing else is:**
no address on file, the package over the cap, or this vendor already sent to.
Anything else — a broken transport, a store error — propagates. A blanket
`except Exception` here would report a transport misconfiguration as "vendors
skipped", sending a buyer to fix the contact directory when the mailer is
what is actually broken.

`preview` stores nothing; it resolves the same rows `dispatch` would act on
and returns them. `dispatch` re-resolves rather than trusting anything a
caller hands back — a recipient list posted from a browser is one a stale tab
can edit.
"""
from dataclasses import dataclass

from workflow import doc_store
from workflow.mail import MailTransport
from workflow.models.mail import MailAttachment, OutboundMail
from workflow.models.rfq import EnquirySend, ShortlistEntry

#: Gmail's limit. Skipping a vendor over this is a recoverable event with a
#: named reason — sending would just fail at the transport, less legibly.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


@dataclass
class SkippedRecipient:
    vendor_name: str
    reason: str


@dataclass
class PreviewRecipient:
    shortlist_entry_id: str
    vendor_name: str
    to: list[str] | None
    skip_reason: str | None


@dataclass
class EnquiryDispatch:
    sent: list[EnquirySend]
    skipped: list[SkippedRecipient]


def _format_bytes(n: int) -> str:
    """Human-readable, and correct at any scale a cap might be set to —
    including a test's monkeypatched cap of a few bytes, which `n // MB` would
    silently round down to "0 MB"."""
    if n >= 1024 * 1024:
        return f"{n // (1024 * 1024)} MB"
    return f"{n} bytes"


def _attachments(store, blobs, rfq_id: str) -> list[MailAttachment]:
    """The contractor's half of the enquiry, and only that half."""
    out: list[MailAttachment] = []
    for document in store.rfq_documents(rfq_id):
        if document.submitted_by_vendor_id is not None:
            continue
        # `RfqDocument` carries no `blob_ref` field -- it carries the three
        # leaves the ref is built from, so no path is stored for a moved root
        # to invalidate. Build it here rather than assuming an attribute.
        ref = doc_store.blob_ref(
            document.rfq_id, document.sha256, document.size_bytes, document.filename
        )
        with blobs.open(ref) as handle:
            out.append(MailAttachment(
                filename=document.filename,
                content_type=document.content_type or "application/octet-stream",
                data=handle.read(),
            ))
    return out


def _resolve(store, blobs, rfq_id: str):
    """One row per included shortlist entry, resolved to either addresses or
    a skip reason, plus the attachments those addresses would be sent. Shared
    by `preview` and `dispatch` so the two cannot disagree about who would be
    reached.

    Returns `(rows, attachments)`, where each row is
    `(ShortlistEntry, list[str] | None, str | None)` — addresses xor a reason,
    never both, never neither.
    """
    already = store.already_sent_entry_ids(rfq_id)
    attachments = _attachments(store, blobs, rfq_id)
    total = sum(len(a.data) for a in attachments)

    rows: list[tuple[ShortlistEntry, list[str] | None, str | None]] = []
    for entry in store.shortlist_for(rfq_id):
        if not entry.included:
            continue
        if entry.id in already:
            rows.append((
                entry, None,
                f"{entry.vendor_name} has already been sent this enquiry.",
            ))
        elif total > MAX_ATTACHMENT_BYTES:
            rows.append((
                entry, None,
                f"The package is {_format_bytes(total)}, over the "
                f"{_format_bytes(MAX_ATTACHMENT_BYTES)} limit.",
            ))
        else:
            addresses = store.emails_for(entry.vendor_name)
            if addresses is None:
                rows.append((
                    entry, None,
                    f"No address on file for {entry.vendor_name}. Upload a "
                    f"contact sheet naming them exactly as the shortlist does.",
                ))
            else:
                rows.append((entry, addresses, None))
    return rows, attachments


def preview(store, blobs, rfq_id: str) -> list[PreviewRecipient]:
    """Resolve who a dispatch would reach and why anybody else would be
    skipped. Stores nothing."""
    rows, _attachments = _resolve(store, blobs, rfq_id)
    return [
        PreviewRecipient(
            shortlist_entry_id=entry.id,
            vendor_name=entry.vendor_name,
            to=addresses,
            skip_reason=reason,
        )
        for entry, addresses, reason in rows
    ]


def dispatch(
    store, blobs, transport: MailTransport, rfq_id: str, by: str,
) -> EnquiryDispatch:
    """Send the enquiry to every reachable, not-yet-sent shortlist entry, one
    message each, and record what happened.

    Re-resolves via `_resolve` rather than trusting anything a caller passes
    in — a recipient list posted back from a browser is one a stale tab can
    edit.
    """
    rows, attachments = _resolve(store, blobs, rfq_id)

    sent: list[EnquirySend] = []
    skipped: list[SkippedRecipient] = []
    rfq = store.get_rfq(rfq_id)
    subject = f"Enquiry: {rfq.reference}" if rfq is not None else f"Enquiry: {rfq_id}"

    for entry, addresses, reason in rows:
        if reason is not None:
            skipped.append(SkippedRecipient(vendor_name=entry.vendor_name, reason=reason))
            continue

        # Built fresh, inside the loop, addressed to this vendor and nobody
        # else -- see the module docstring for why that is not negotiable.
        message = OutboundMail(
            to=addresses,
            subject=subject,
            body=(
                "Please find attached our enquiry documents. Kindly submit "
                "your offer by the stated due date."
            ),
            attachments=attachments,
        )
        ref = transport.send(message)
        record = store.record_enquiry_send(
            rfq_id,
            shortlist_entry_id=entry.id,
            vendor_name=entry.vendor_name,
            to=addresses,
            ref=ref,
            by=by,
        )
        sent.append(record)

    return EnquiryDispatch(sent=sent, skipped=skipped)
