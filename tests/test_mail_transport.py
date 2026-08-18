import os
from email import message_from_bytes, policy

import pytest

from workflow.mail import OutboxTransport, transport_for
from workflow.models.mail import MailAttachment, OutboundMail


def a_message(**over) -> OutboundMail:
    fields = dict(
        to=["sales@example.com"],
        subject="Enquiry ADP-RFQ-2026-014",
        body="Please find the enquiry attached.",
        attachments=[
            MailAttachment(
                filename="datasheet.pdf",
                content_type="application/pdf",
                data=b"%PDF-1.4 fake",
            )
        ],
    )
    fields.update(over)
    return OutboundMail(**fields)


def test_the_default_transport_is_the_outbox(tmp_path, monkeypatch):
    """The safety property, asserted directly.

    This is the test that fails if somebody inverts the default — the single
    change that would turn a demo into a real send.
    """
    monkeypatch.delenv("MAIL_TRANSPORT", raising=False)
    assert isinstance(transport_for(str(tmp_path)), OutboxTransport)


@pytest.mark.parametrize("value", ["", "0", "false", "False", "no"])
def test_an_unset_or_falsy_flag_leaves_the_outbox_in_place(tmp_path, monkeypatch, value):
    """`AUTH_DISABLED` is read this way and the two must not drift."""
    monkeypatch.setenv("MAIL_TRANSPORT", value)
    assert isinstance(transport_for(str(tmp_path)), OutboxTransport)


def test_the_outbox_writes_a_parseable_message_and_sends_nothing(tmp_path):
    transport = OutboxTransport(str(tmp_path))

    ref = transport.send(a_message())

    assert ref.transport == "outbox"
    assert ref.location is not None
    with open(ref.location, "rb") as handle:
        # `policy.default` is required for `iter_attachments()` to exist at
        # all: the top-level `message_from_bytes` defaults to `compat32`,
        # whose message_factory is the legacy `Message` class. This is
        # independent of what `OutboxTransport` writes — it is a property of
        # how the bytes are re-parsed.
        parsed = message_from_bytes(handle.read(), policy=policy.default)
    assert parsed["To"] == "sales@example.com"
    assert parsed["Subject"] == "Enquiry ADP-RFQ-2026-014"
    names = [part.get_filename() for part in parsed.iter_attachments()]
    assert names == ["datasheet.pdf"]


def test_every_send_gets_its_own_file(tmp_path):
    """Two sends must not collide on one filename — the second would overwrite
    the record of the first, and the outbox is the only artefact a demo has."""
    transport = OutboxTransport(str(tmp_path))

    first = transport.send(a_message(to=["a@example.com"]))
    second = transport.send(a_message(to=["b@example.com"]))

    assert first.location != second.location
    assert len(os.listdir(os.path.join(str(tmp_path), "outbox"))) == 2


def test_each_message_carries_its_own_message_id(tmp_path):
    transport = OutboxTransport(str(tmp_path))

    first = transport.send(a_message())
    second = transport.send(a_message())

    assert first.message_id != second.message_id
