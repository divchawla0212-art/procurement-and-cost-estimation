from pydantic import BaseModel


class MailAttachment(BaseModel):
    filename: str
    content_type: str
    data: bytes


class OutboundMail(BaseModel):
    """One message to one vendor.

    `to` is that vendor's addresses and nobody else's. A message carrying two
    vendors' addresses discloses the bidder list to competitors, which is a
    tender that has to be re-run — see `enquiry.py` for where that is enforced.
    """

    to: list[str]
    subject: str
    body: str
    attachments: list[MailAttachment] = []


class SentRef(BaseModel):
    """What the transport did, recorded so a reply can be matched to it later.

    `transport` is here because "this went to the outbox" and "this reached a
    real mailbox" must not be indistinguishable in the stored record.
    """

    message_id: str
    transport: str
    location: str | None = None
