import os
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Protocol
from uuid import uuid4

from workflow.models.mail import OutboundMail, SentRef

# Read the way AUTH_DISABLED is read. Absent, empty, "0", "false" and "no" all
# mean off, so a half-set variable leaves the outbox in place rather than
# enabling real mail on a truthy-looking string.
_TRUTHY = {"smtp"}


class MailTransport(Protocol):
    def send(self, message: OutboundMail) -> SentRef: ...


def build_message(message: OutboundMail, sender: str) -> EmailMessage:
    """One `EmailMessage`, shared by both transports so the outbox holds what
    SMTP would have carried rather than an approximation of it."""
    built = EmailMessage()
    built["From"] = sender
    built["To"] = ", ".join(message.to)
    built["Subject"] = message.subject
    built["Message-ID"] = make_msgid()
    built.set_content(message.body)
    for attachment in message.attachments:
        maintype, _, subtype = attachment.content_type.partition("/")
        built.add_attachment(
            attachment.data,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=attachment.filename,
        )
    return built


class OutboxTransport:
    """The default. Writes a message and sends nothing."""

    def __init__(self, root: str, sender: str = "outbox@localhost") -> None:
        self._dir = os.path.join(root, "outbox")
        self._sender = sender

    def send(self, message: OutboundMail) -> SentRef:
        os.makedirs(self._dir, exist_ok=True)
        built = build_message(message, self._sender)
        # A uuid rather than a timestamp: two sends inside one dispatch land in
        # the same second, and the second would overwrite the first.
        path = os.path.join(self._dir, f"{uuid4().hex}.eml")
        with open(path, "wb") as handle:
            handle.write(bytes(built))
        return SentRef(
            message_id=built["Message-ID"], transport="outbox", location=path
        )


def transport_for(root: str) -> MailTransport:
    """The one place the environment is read.

    Anything other than an explicit `smtp` leaves the outbox in place. Task 2
    adds the SMTP branch here; until then this always answers with the
    outbox, which is the correct behaviour for a half-built feature.
    """
    return OutboxTransport(root)
