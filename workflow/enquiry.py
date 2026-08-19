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
The third is waived by `resend`, which is a deliberate act a buyer opts into:
the enquiry goes out again to everybody, and each vendor gets a *second*
`EnquirySend` record rather than the first being overwritten.
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
from datetime import datetime, timezone

from workflow import doc_store
from workflow.enquiry_body import build_body
from workflow.mail import MailTransport
from workflow.models.mail import MailAttachment, OutboundMail
from workflow.models.rfq import EnquirySend, ShortlistEntry
from workflow.models.rfq_document import RfqDocument

#: Gmail's limit. Skipping a vendor over this is a recoverable event with a
#: named reason — sending would just fail at the transport, less legibly.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

#: Who a dispatch is aimed at. The three are deliberately **nested** —
#: `UNSENT` ⊆ `OUTDATED` ⊆ `EVERYONE` — so a buyer who adds a document and asks
#: for the vendors behind on it does not silently miss the one shortlisted
#: late who has had nothing at all. A disjoint "only the stale ones" bucket
#: would need running twice to cover the shortlist, and the second run is the
#: one people forget.
UNSENT = "unsent"
OUTDATED = "outdated"
EVERYONE = "all"
AUDIENCES = (UNSENT, OUTDATED, EVERYONE)


def check_audience(audience: str) -> str:
    """Refuse an unknown audience rather than reading it as the default.

    A typo that fell back to `UNSENT` would reach nobody on an RFQ already
    sent, and "nobody" reads on screen exactly like "everybody is up to date".
    Same reasoning as the 422 on `GET /bidders/available`'s approver filter.
    """
    if audience not in AUDIENCES:
        raise ValueError(
            f"Unknown audience: {audience}. Use one of {', '.join(AUDIENCES)}."
        )
    return audience


def _as_utc(moment: datetime) -> datetime:
    """A naive timestamp is read as UTC rather than compared against an aware
    one, which raises. Records written by this repository are always aware;
    this is for anything hand-edited into the document."""
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _newest_document_at(documents) -> datetime | None:
    """When the enquiry package last changed. `None` when it holds nothing —
    with no documents there is nothing to be behind on, so nobody is outdated.
    """
    moments = []
    for document in documents:
        try:
            moments.append(_as_utc(datetime.fromisoformat(document.uploaded_at)))
        except (TypeError, ValueError):
            # An unparseable stamp must not silently read as "no documents",
            # which would report every vendor as up to date. Treated as
            # infinitely new instead, so the buyer is told to re-send.
            return datetime.max.replace(tzinfo=timezone.utc)
    return max(moments) if moments else None


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


def _contractor_documents(store, rfq_id: str) -> list[RfqDocument]:
    """The contractor's half of the enquiry, and only that half.

    One definition, read twice: `_attachments` turns these into bytes, and the
    body names them and reads their categories. Filtering in two places is how
    a vendor's returned bid ends up described in a mail to their competitor.
    """
    return [
        d for d in store.rfq_documents(rfq_id) if d.submitted_by_vendor_id is None
    ]


def _attachments(store, blobs, rfq_id: str) -> list[MailAttachment]:
    """The contractor's documents, as bytes to attach."""
    out: list[MailAttachment] = []
    for document in _contractor_documents(store, rfq_id):
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


def _resolve(store, blobs, rfq_id: str, audience: str = UNSENT):
    """One row per included shortlist entry, resolved to either addresses or
    a skip reason, plus the attachments those addresses would be sent. Shared
    by `preview` and `dispatch` so the two cannot disagree about who would be
    reached.

    Returns `(rows, attachments)`, where each row is
    `(ShortlistEntry, list[str] | None, str | None)` — addresses xor a reason,
    never both, never neither.
    """
    check_audience(audience)
    last_send = store.last_send_by_entry(rfq_id)
    documents = _contractor_documents(store, rfq_id)
    newest = _newest_document_at(documents)
    attachments = _attachments(store, blobs, rfq_id)
    total = sum(len(a.data) for a in attachments)

    rows: list[tuple[ShortlistEntry, list[str] | None, str | None]] = []
    for entry in store.shortlist_for(rfq_id):
        if not entry.included:
            continue
        covered = _already_covered(entry, last_send.get(entry.id), audience, newest)
        if covered is not None:
            rows.append((entry, None, covered))
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


def _enquiry_body(store, rfq, rfq_id: str) -> str:
    """Resolve what the body describes and hand it to the pure builder.

    The store lookups live here rather than in `enquiry_body`, so that module
    stays testable against plain records — the same split `bidders.py` and
    `eligibility.py` keep. An item that has since been deleted is dropped
    rather than rendered as a blank line.
    """
    items = [store.get_item(item_id) for item_id in rfq.item_ids]
    return build_body(
        rfq=rfq,
        project=store.get_project(rfq.project_id),
        items=[item for item in items if item is not None],
        issued=_contractor_documents(store, rfq_id),
        technical_package=store.get_technical_package(rfq_id),
    )


def _already_covered(entry, sent_at, audience: str, newest) -> str | None:
    """Why this vendor is not in scope for *this* dispatch, or `None`.

    Only ever about what they have already received — the address and the
    package-size checks are separate and still apply on top.
    """
    if sent_at is None or audience == EVERYONE:
        return None
    if audience == UNSENT:
        return f"{entry.vendor_name} has already been sent this enquiry."
    # OUTDATED: in scope only if the package has changed since they saw it.
    if newest is not None and _as_utc(sent_at) < newest:
        return None
    return (
        f"{entry.vendor_name} already has the latest documents "
        f"(sent {_as_utc(sent_at).date().isoformat()})."
    )


def preview(store, blobs, rfq_id: str, audience: str = UNSENT) -> list[PreviewRecipient]:
    """Resolve who a dispatch would reach and why anybody else would be
    skipped. Stores nothing.

    `audience` must be the same value the `dispatch` that follows will use, or
    the buyer confirms a list that is not the one that goes out.
    """
    rows, _attachments = _resolve(store, blobs, rfq_id, audience)
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
    audience: str = UNSENT,
) -> EnquiryDispatch:
    """Send the enquiry to every reachable, not-yet-sent shortlist entry, one
    message each, and record what happened.

    Re-resolves via `_resolve` rather than trusting anything a caller passes
    in — a recipient list posted back from a browser is one a stale tab can
    edit.
    """
    rows, attachments = _resolve(store, blobs, rfq_id, audience)

    sent: list[EnquirySend] = []
    skipped: list[SkippedRecipient] = []
    rfq = store.get_rfq(rfq_id)
    subject = f"Enquiry: {rfq.reference}" if rfq is not None else f"Enquiry: {rfq_id}"
    # Built once, before the loop. Every bidder is asked for the same things,
    # and a body assembled per vendor is a body that could *differ* per vendor
    # -- the same argument that puts one recipient on each message.
    #
    # Empty only when the RFQ is gone, in which case `rows` is empty too and
    # nothing is sent; the route refuses an unknown RFQ before reaching here.
    body = _enquiry_body(store, rfq, rfq_id) if rfq is not None else ""

    for entry, addresses, reason in rows:
        if reason is not None:
            skipped.append(SkippedRecipient(vendor_name=entry.vendor_name, reason=reason))
            continue

        # Built fresh, inside the loop, addressed to this vendor and nobody
        # else -- see the module docstring for why that is not negotiable.
        message = OutboundMail(
            to=addresses,
            subject=subject,
            body=body,
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
            resend=audience != UNSENT,
        )
        sent.append(record)

    return EnquiryDispatch(sent=sent, skipped=skipped)
