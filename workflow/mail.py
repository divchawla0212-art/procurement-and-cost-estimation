import os
import smtplib
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


class MailConfigError(RuntimeError):
    """`MAIL_TRANSPORT=smtp` was asked for and the configuration is incomplete."""


class SmtpImapTransport:
    """Real mail. Only ever constructed by `transport_for` on an explicit flag.

    Named for what it will become — the inbound half arrives with the
    clarification thread and adds `poll`. It sends only, today.
    """

    def __init__(self, host: str, port: int, username: str, password: str, sender: str) -> None:
        self._host, self._port = host, port
        self._username, self._password = username, password
        self._sender = sender

    def send(self, message: OutboundMail) -> SentRef:
        built = build_message(message, self._sender)
        with smtplib.SMTP(self._host, self._port) as server:
            server.starttls()
            server.login(self._username, self._password)
            server.send_message(built)
        return SentRef(message_id=built["Message-ID"], transport="smtp", location=None)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MailConfigError(
            f"MAIL_TRANSPORT=smtp needs {name}. Set it in .env, or unset "
            f"MAIL_TRANSPORT to keep writing to the outbox."
        )
    return value


def transport_for(root: str) -> MailTransport:
    """The one place the environment is read.

    Anything other than an explicit `smtp` leaves the outbox in place. A
    refused SMTP configuration never falls back to the outbox: an operator
    who asked for real mail and silently got the outbox instead believes a
    tender was sent.
    """
    if os.environ.get("MAIL_TRANSPORT", "").strip().lower() not in _TRUTHY:
        return OutboxTransport(root)
    # No try/except around this. Validation happens here, at construction —
    # not at first send, which would put a misconfiguration in front of a
    # buyer who has just pressed Send.
    return SmtpImapTransport(
        host=_required("SMTP_HOST"),
        port=int(os.environ.get("SMTP_PORT", "587")),
        username=_required("SMTP_USERNAME"),
        password=_required("SMTP_PASSWORD"),
        sender=_required("MAIL_FROM"),
    )
