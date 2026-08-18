# RFQ Enquiry Email Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send an RFQ's documents to its shortlisted vendors by email, one message per vendor, with an outbox in front of it so nothing reaches a real mailbox by accident.

**Architecture:** One `MailTransport` protocol with two implementations — `OutboxTransport` writing `.eml` files, the default; `SmtpImapTransport` constructed only on an explicit `MAIL_TRANSPORT=smtp`. A pure `workflow/enquiry.py` resolves each shortlisted vendor to addresses or to a skip reason and builds one message per vendor. A new `EnquirySend` collection in `workflow.json` records what actually went out. Two routes: a preview that stores nothing, and a send that records.

**Tech Stack:** Python 3.12, stdlib `email.message.EmailMessage` / `smtplib`, Pydantic v2, FastAPI, React + Vitest.

**Spec:** [`docs/superpowers/specs/2026-08-19-rfq-enquiry-email-design.md`](../specs/2026-08-19-rfq-enquiry-email-design.md)

## Global Constraints

- **Both test baselines stay honest.** `python -m pytest` from the repo root; every new test is key-free and none may require an SMTP host or `ANTHROPIC_API_KEY`. Web suite is `npm test` under `web/`.
- **The working tree already has 10 pre-existing failures** from an in-flight `TbeTemplate.criteria` → `items` change (7 in `test_eligibility.py`, 1 in `test_vendor_contact_lifecycle.py`, 1 in `test_workflow_endpoints.py`, 1 in `test_workflow_store.py`). They are **not** this phase's to fix. Measure against that list, not against zero.
- **Every write, and every read that gates one, happens inside `persistence.locked_update`.** A check in the route and a write in the store are two critical sections.
- **`workflow.json` holds exactly the entities the store holds.** Any field added to `WorkflowStore.__init__` needs a line in **both** `to_document` and `from_document`.
- **Nothing invents an address.** A vendor with no contact is skipped, never guessed at.
- **No test, no seed and no default configuration may send mail to a real address.**
- **The SMTP credential is never handled, defaulted or committed.** `.env.example` keys are added empty.
- `MAIL_TRANSPORT` is read exactly the way `AUTH_DISABLED` is: absent, empty, `0` and `false` all mean off.

> **Reference code below is intent, not paste-able.** It shows the shape and the reasoning, and it has not been executed. Read it, then write the implementation against the test.
>
> Load-bearing throughout: the falsy-flag reading, the one-message-per-vendor rule, the three-and-only-three skip conditions, and the `shortlist_entry_id` key. Illustrative: exact filenames in the outbox, the exact wording of skip sentences, header ordering.

---

### Task 1: The transport protocol and the outbox

**Files:**
- Create: `workflow/models/mail.py`
- Create: `workflow/mail.py`
- Test: `tests/test_mail_transport.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `OutboundMail(to: list[str], subject: str, body: str, attachments: list[MailAttachment])`
  - `MailAttachment(filename: str, content_type: str, data: bytes)`
  - `SentRef(message_id: str, transport: str, location: str | None)`
  - `class MailTransport(Protocol): def send(self, message: OutboundMail) -> SentRef: ...`
  - `OutboxTransport(root: str)` implementing it
  - `transport_for(root: str) -> MailTransport`
- **Store invariant owned:** none in `workflow.json` — this task writes no entry there. It owns the filesystem one: **`<ROOT>/outbox/` contains exactly one `.eml` per send made through `OutboxTransport`, and each is a parseable RFC 5322 message carrying the same recipients and attachments as the `OutboundMail` that produced it.**

The outbox writes a real message rather than a summary. A summary would prove nothing about what SMTP would have carried, and this file is the only artefact any test or demo ever inspects.

`transport_for` is the **one** place that reads the environment. Routes never construct a transport, so there is no second place for the default to be got wrong.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mail_transport.py
import os
from email import message_from_bytes

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
        parsed = message_from_bytes(handle.read())
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_mail_transport.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'workflow.mail'`

- [ ] **Step 3: Write the minimal implementation**

Reference — the falsy reading and the per-send filename are load-bearing; the timestamp format is illustrative.

```python
# workflow/models/mail.py
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
```

```python
# workflow/mail.py
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
    adds the SMTP branch; until then this always answers with the outbox, which
    is the correct behaviour for a half-built feature.
    """
    if os.environ.get("MAIL_TRANSPORT", "").strip().lower() not in _TRUTHY:
        return OutboxTransport(root)
    return OutboxTransport(root)  # replaced in Task 2
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_mail_transport.py -q`
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add workflow/mail.py workflow/models/mail.py tests/test_mail_transport.py
git commit -m "feat: one transport interface, and an outbox that sends nothing"
```

---

### Task 2: The SMTP transport, behind an explicit opt-in

**Files:**
- Modify: `workflow/mail.py`
- Modify: `.env.example`
- Test: `tests/test_mail_transport.py` (extend)

**Interfaces:**
- Consumes: `MailTransport`, `OutboundMail`, `SentRef`, `build_message`, `transport_for` from Task 1.
- Produces:
  - `SmtpImapTransport(host: str, port: int, username: str, password: str, sender: str)`
  - `MailConfigError(RuntimeError)`
  - `transport_for` now returning `SmtpImapTransport` when `MAIL_TRANSPORT=smtp`
- **Store invariant owned:** none in `workflow.json`. It owns the configuration one: **no environment state other than an explicit `MAIL_TRANSPORT=smtp` plus a complete SMTP configuration ever yields a transport that can reach a real mailbox; an incomplete one raises at construction rather than at first send.**

A misconfiguration that surfaces only when a buyer presses Send is one that surfaces in front of a customer. `transport_for` therefore validates eagerly.

The SMTP send path itself is **not** unit-tested against a live server — there is none, and the suite must stay key-free. What is tested is the branch selection and the validation, which is where the safety property lives.

- [ ] **Step 1: Write the failing tests**

```python
# appended to tests/test_mail_transport.py
from workflow.mail import MailConfigError, SmtpImapTransport


def _smtp_env(monkeypatch, **over):
    values = {
        "MAIL_TRANSPORT": "smtp",
        "MAIL_FROM": "rahuljana.business@gmail.com",
        "SMTP_HOST": "smtp.gmail.com",
        "SMTP_PORT": "587",
        "SMTP_USERNAME": "rahuljana.business@gmail.com",
        "SMTP_PASSWORD": "app-password",
    }
    values.update(over)
    for key, value in values.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_an_explicit_smtp_flag_selects_the_real_transport(tmp_path, monkeypatch):
    _smtp_env(monkeypatch)
    assert isinstance(transport_for(str(tmp_path)), SmtpImapTransport)


def test_smtp_without_a_sender_is_refused_at_construction(tmp_path, monkeypatch):
    """Not at first send. A half-configured mailer must fail at launch, not in
    front of a buyer who has just pressed Send."""
    _smtp_env(monkeypatch, MAIL_FROM=None)
    with pytest.raises(MailConfigError, match="MAIL_FROM"):
        transport_for(str(tmp_path))


@pytest.mark.parametrize("missing", ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD"])
def test_smtp_missing_any_credential_is_refused_and_named(tmp_path, monkeypatch, missing):
    _smtp_env(monkeypatch, **{missing: None})
    with pytest.raises(MailConfigError, match=missing):
        transport_for(str(tmp_path))


def test_a_refused_configuration_never_falls_back_to_the_outbox(tmp_path, monkeypatch):
    """Falling back would be worse than failing: an operator who asked for real
    mail and silently got an outbox believes a tender was sent."""
    _smtp_env(monkeypatch, SMTP_HOST=None)
    with pytest.raises(MailConfigError):
        transport_for(str(tmp_path))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_mail_transport.py -q`
Expected: FAIL — `ImportError: cannot import name 'MailConfigError'`

- [ ] **Step 3: Write the minimal implementation**

Reference — the eager validation and the no-fallback rule are load-bearing; the STARTTLS call sequence is the standard one and is illustrative.

```python
# added to workflow/mail.py
import smtplib


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
    if os.environ.get("MAIL_TRANSPORT", "").strip().lower() not in _TRUTHY:
        return OutboxTransport(root)
    # No try/except around this. A refused configuration must not fall back to
    # the outbox: an operator who asked for real mail and silently got an
    # outbox believes a tender was sent.
    return SmtpImapTransport(
        host=_required("SMTP_HOST"),
        port=int(os.environ.get("SMTP_PORT", "587")),
        username=_required("SMTP_USERNAME"),
        password=_required("SMTP_PASSWORD"),
        sender=_required("MAIL_FROM"),
    )
```

`.env.example` gains, all empty, with a comment saying the outbox is the default:

```
# Mail. Unset (or empty/0/false) keeps every send in <ROOT>/outbox/ as a .eml
# and reaches nobody. MAIL_TRANSPORT=smtp is the deliberate opt-in to real mail.
MAIL_TRANSPORT=
MAIL_FROM=
SMTP_HOST=
SMTP_PORT=
SMTP_USERNAME=
SMTP_PASSWORD=
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_mail_transport.py -q`
Expected: PASS, 15 tests

- [ ] **Step 5: Commit**

```bash
git add workflow/mail.py .env.example tests/test_mail_transport.py
git commit -m "feat: real mail, and the one flag that turns it on"
```

---

### Task 3: The `EnquirySend` record, its store methods and its persistence

**Files:**
- Modify: `workflow/models/rfq.py` (add `EnquirySend`)
- Modify: `workflow/store.py`
- Modify: `workflow/persistence.py:132` (`to_document`) and `:236` (`from_document`)
- Test: `tests/test_enquiry_sends.py`

**Interfaces:**
- Consumes: `SentRef` from Task 1.
- Produces:
  - `EnquirySend(id, rfq_id, shortlist_entry_id, vendor_name, to: list[str], sent_at: datetime, by: str, message_id: str, transport: str)`
  - `WorkflowStore.record_enquiry_send(...) -> EnquirySend`
  - `WorkflowStore.enquiry_sends_for(rfq_id: str) -> list[EnquirySend]`
  - `WorkflowStore.already_sent_entry_ids(rfq_id: str) -> set[str]`
- **Store invariant owned:** **`workflow.json` holds exactly the `EnquirySend` records whose RFQ still exists, and an RFQ holds at most one record per `shortlist_entry_id`.**

Both halves are enforced where `RfqDocument`'s equivalent is (I-E): `record_enquiry_send` refuses an unknown RFQ, and `from_document` drops a record whose RFQ has gone.

The duplicate check lives **in the store method**, never in the route. It is a read that gates a write, and a check in the caller with the write in the store is two critical sections — the sixth time this repository has needed that rule.

**Removing a shortlist entry does not remove its send record.** The mail was sent; deleting the record would falsify that. Do not extend `remove_shortlist_entry`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_enquiry_sends.py
import pytest

from workflow.models.mail import SentRef
from workflow.store import WorkflowStore


def a_store():
    """One project, one item, one RFQ, one shortlisted vendor."""
    store = WorkflowStore()
    project = store.create_project(
        name="Haliba", code="HAL", client="Al Dhafra Petroleum",
        location="Haliba field, UAE",
        live_period_start=__import__("datetime").date(2026, 1, 1),
        live_period_end=__import__("datetime").date(2029, 12, 31),
    )
    item = store.create_item(
        project_id=project.id, item_type="Cable", description="HV cable",
        qty=1, uom="lot", discipline="Electrical", estimated_value_aed=1_000,
    )
    rfq = store.create_rfq(
        project_id=project.id, item_ids=[item.id], reference="ADP-RFQ-2026-014",
        package="HV cable", discipline="Electrical", value_estimate_aed=1_000,
    )
    entry = store.add_shortlist_entry(
        rfq.id, vendor_name="Galfar", prequal_status="Qualified",
        scope_code_fit=True, included=True,
    )
    return store, rfq.id, entry.id


def a_ref(message_id="<a@example.com>"):
    return SentRef(message_id=message_id, transport="outbox", location="/tmp/a.eml")


def test_a_send_is_recorded_against_its_rfq_and_shortlist_entry():
    store, rfq_id, entry_id = a_store()

    record = store.record_enquiry_send(
        rfq_id, shortlist_entry_id=entry_id, vendor_name="Galfar",
        to=["sales@example.com"], ref=a_ref(), by="buyer@adp.ae",
    )

    assert record.rfq_id == rfq_id
    assert record.shortlist_entry_id == entry_id
    assert record.to == ["sales@example.com"]
    assert record.transport == "outbox"
    assert [r.id for r in store.enquiry_sends_for(rfq_id)] == [record.id]


def test_a_send_against_an_unknown_rfq_is_refused():
    store, _, entry_id = a_store()
    with pytest.raises(KeyError, match="Unknown RFQ"):
        store.record_enquiry_send(
            "rfq_nope", shortlist_entry_id=entry_id, vendor_name="Galfar",
            to=["sales@example.com"], ref=a_ref(), by="buyer@adp.ae",
        )


def test_the_same_shortlist_entry_cannot_be_sent_to_twice():
    """The duplicate guard is in the store, not the route: it is a read that
    gates a write, and splitting them makes two critical sections."""
    store, rfq_id, entry_id = a_store()
    store.record_enquiry_send(
        rfq_id, shortlist_entry_id=entry_id, vendor_name="Galfar",
        to=["sales@example.com"], ref=a_ref(), by="buyer@adp.ae",
    )

    with pytest.raises(ValueError, match="already"):
        store.record_enquiry_send(
            rfq_id, shortlist_entry_id=entry_id, vendor_name="Galfar",
            to=["sales@example.com"], ref=a_ref("<b@example.com>"), by="buyer@adp.ae",
        )


def test_already_sent_entry_ids_answers_what_the_dispatch_will_skip():
    store, rfq_id, entry_id = a_store()
    assert store.already_sent_entry_ids(rfq_id) == set()

    store.record_enquiry_send(
        rfq_id, shortlist_entry_id=entry_id, vendor_name="Galfar",
        to=["sales@example.com"], ref=a_ref(), by="buyer@adp.ae",
    )
    assert store.already_sent_entry_ids(rfq_id) == {entry_id}


def test_removing_the_shortlist_entry_leaves_its_send_record():
    """The mail was sent. Deleting the record would falsify that — the
    append-only rule the stage history keeps, in a second place."""
    store, rfq_id, entry_id = a_store()
    store.record_enquiry_send(
        rfq_id, shortlist_entry_id=entry_id, vendor_name="Galfar",
        to=["sales@example.com"], ref=a_ref(), by="buyer@adp.ae",
    )

    store.remove_shortlist_entry(rfq_id, entry_id)

    assert len(store.enquiry_sends_for(rfq_id)) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_enquiry_sends.py -q`
Expected: FAIL — `AttributeError: 'WorkflowStore' object has no attribute 'record_enquiry_send'`

- [ ] **Step 3: Write the minimal implementation**

Reference — the `KeyError` / `ValueError` split matters (the route maps them to 404 and 409), and so does keying on `shortlist_entry_id`. The id prefix is illustrative.

```python
# workflow/models/rfq.py
def new_enquiry_send_id() -> str:
    return f"esd_{uuid4().hex[:8]}"


class EnquirySend(BaseModel):
    """One vendor's copy of the enquiry, as it actually went out.

    `to` is stored rather than derived. The `email` key on a shortlist row is
    derived on read, so correcting the contact sheet corrects every shortlist at
    once — this is the opposite and deliberately so. It records something that
    happened, and re-uploading the sheet must not rewrite who a tender reached.
    Frozen for the same reason `ShortlistEntry.prequal_status` is frozen.

    `transport` is here because "this went to the outbox" and "this reached a
    real mailbox" must not be indistinguishable later.
    """

    id: str = Field(default_factory=new_enquiry_send_id)
    rfq_id: str
    shortlist_entry_id: str
    vendor_name: str
    to: list[str]
    sent_at: datetime
    by: str
    message_id: str
    transport: str
```

```python
# workflow/store.py — beside the other per-RFQ collections
# in __init__:
#     self._enquiry_sends: dict[str, list[EnquirySend]] = {}

    def record_enquiry_send(
        self, rfq_id: str, *, shortlist_entry_id: str, vendor_name: str,
        to: list[str], ref: SentRef, by: str,
    ) -> EnquirySend:
        """Both reads that gate this write are here, not in the route."""
        if rfq_id not in self._rfqs:
            raise KeyError(f"Unknown RFQ: {rfq_id}")
        if shortlist_entry_id in self.already_sent_entry_ids(rfq_id):
            raise ValueError(
                f"{vendor_name} has already been sent this enquiry."
            )
        record = EnquirySend(
            rfq_id=rfq_id, shortlist_entry_id=shortlist_entry_id,
            vendor_name=vendor_name, to=list(to),
            sent_at=datetime.now(timezone.utc), by=by,
            message_id=ref.message_id, transport=ref.transport,
        )
        self._enquiry_sends.setdefault(rfq_id, []).append(record)
        return record

    def enquiry_sends_for(self, rfq_id: str) -> list[EnquirySend]:
        return list(self._enquiry_sends.get(rfq_id, []))

    def already_sent_entry_ids(self, rfq_id: str) -> set[str]:
        return {r.shortlist_entry_id for r in self._enquiry_sends.get(rfq_id, [])}
```

```python
# workflow/persistence.py — to_document, beside "rfq_documents"
        "enquiry_sends": [
            r.model_dump(mode="json")
            for records in store._enquiry_sends.values()
            for r in records
        ],

# from_document, beside the rfq_documents loop. Dropped rather than tolerated,
# for the reason the document loop states: a record pointing at an RFQ that is
# gone can be reached by no screen and no route.
    for record in doc.get("enquiry_sends", []):
        send = EnquirySend(**record)
        if send.rfq_id not in store._rfqs:
            continue
        store._enquiry_sends.setdefault(send.rfq_id, []).append(send)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_enquiry_sends.py -q`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add workflow/models/rfq.py workflow/store.py workflow/persistence.py tests/test_enquiry_sends.py
git commit -m "feat: what went to whom, recorded where re-uploading a sheet cannot rewrite it"
```

---

### Task 4: The dispatch — resolve, skip, build one message per vendor

**Files:**
- Create: `workflow/enquiry.py`
- Test: `tests/test_enquiry_dispatch.py`

**Interfaces:**
- Consumes: `OutboundMail`, `MailAttachment`, `MailTransport` (Task 1); `record_enquiry_send`, `already_sent_entry_ids` (Task 3); `store.emails_for`, `store.shortlist_for`, `store.rfq_documents`, `doc_store.LocalBlobStore.open`.
- Produces:
  - `SkippedRecipient(vendor_name: str, reason: str)`
  - `PreviewRecipient(shortlist_entry_id: str, vendor_name: str, to: list[str] | None, skip_reason: str | None)`
  - `EnquiryDispatch(sent: list[EnquirySend], skipped: list[SkippedRecipient])`
  - `preview(store, blobs, rfq_id) -> list[PreviewRecipient]`
  - `dispatch(store, blobs, transport, rfq_id, by) -> EnquiryDispatch`
  - `MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024`
- **Store invariant owned:** **after a dispatch, an RFQ's `EnquirySend` records name exactly the shortlist entries that were sent to on this run plus those sent on earlier runs — never a skipped entry, and never an entry twice.**

`preview` stores nothing. `dispatch` re-resolves rather than trusting anything a caller passes, because a recipient list posted back from a browser is one a stale tab can edit.

Exactly **three** conditions are absorbed into `skipped`: no address, package over the cap, already sent. Anything else propagates — a blanket `except Exception` would report a transport misconfiguration as "vendors skipped", sending a buyer to fix the directory when the mailer is broken.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_enquiry_dispatch.py
import pytest

from workflow import enquiry
from workflow.models.mail import OutboundMail, SentRef


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
    store, blobs, rfq_id = rfq_with_a_huge_package
    transport = RecordingTransport()

    result = enquiry.dispatch(store, blobs, transport, rfq_id, by="buyer@adp.ae")

    assert transport.sent == []
    assert "25" in result.skipped[0].reason


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
```

Fixtures live in the same file and use these exact APIs:

```python
from workflow import doc_store
from workflow.models.rfq_document import RfqDocument
from workflow.models.vendor_contact import VendorContact

# The directory is name-keyed and folded. `set_vendor_contacts` replaces it
# wholesale and returns the count; `emails_for` is what the dispatch reads.
store.set_vendor_contacts([
    VendorContact(vendor_name="Galfar", emails=["sales@galfar.example"]),
    VendorContact(vendor_name="OQC", emails=["sales@oqc.example"]),
])

# A document: put the bytes through the blob store, then record it. The record
# carries the three leaves, not a path.
blobs = doc_store.LocalBlobStore(str(tmp_path))
ref = blobs.put(rfq_id, "enquiry.pdf", io.BytesIO(b"%PDF-1.4 enquiry"))
store.add_rfq_document(RfqDocument(
    rfq_id=rfq_id, filename="enquiry.pdf", rel_path="enquiry.pdf",
    sha256=ref.sha256, size_bytes=ref.size, content_type="application/pdf",
    uploaded_by="buyer@adp.ae", uploaded_at="2026-08-19T09:00:00Z",
    submitted_by_vendor_id=None,
))
```

Check `doc_store.BlobRef`'s field names against `workflow/doc_store.py:48` before
writing the fixture — this block is intent, and `put` returning `sha256`/`size`
is the part to verify rather than assume.

`rfq_with_a_huge_package` must not write 25 MB to disk. Monkeypatch
`enquiry.MAX_ATTACHMENT_BYTES` down to a few bytes instead — a test that writes
26 MB per run is a test somebody deletes.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_enquiry_dispatch.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'workflow.enquiry'`

- [ ] **Step 3: Write the minimal implementation**

Reference — the three-and-only-three skip conditions and the per-vendor loop are load-bearing; the subject and body wording are illustrative.

```python
# workflow/enquiry.py
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # Gmail's limit


def _attachments(store, blobs, rfq_id) -> list[MailAttachment]:
    """The contractor's half of the enquiry, and only that half."""
    out = []
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


def _rows(store, blobs, rfq_id):
    """One row per included shortlist entry, resolved. Shared by preview and
    dispatch so the two cannot disagree about who would be reached."""
    already = store.already_sent_entry_ids(rfq_id)
    attachments = _attachments(store, blobs, rfq_id)
    total = sum(len(a.data) for a in attachments)
    for entry in store.shortlist_for(rfq_id):
        if not entry.included:
            continue
        if entry.id in already:
            yield entry, None, f"{entry.vendor_name} has already been sent this enquiry."
        elif total > MAX_ATTACHMENT_BYTES:
            yield entry, None, (
                f"The package is {total // (1024 * 1024)} MB, over the 25 MB limit."
            )
        else:
            addresses = store.emails_for(entry.vendor_name)
            if addresses is None:
                yield entry, None, (
                    f"No address on file for {entry.vendor_name}. Upload a contact "
                    f"sheet naming them exactly as the shortlist does."
                )
            else:
                yield entry, addresses, None
```

`preview` maps those rows to `PreviewRecipient` and returns. `dispatch` walks the same rows, and for each resolved one builds an `OutboundMail` **inside the loop** — a message built once outside it and mutated per vendor is how every bidder ends up on one `To:` line — sends it, and calls `store.record_enquiry_send`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_enquiry_dispatch.py -q`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add workflow/enquiry.py tests/test_enquiry_dispatch.py
git commit -m "feat: one enquiry per vendor, and a sentence for everyone it could not reach"
```

---

### Task 5: The two routes

**Files:**
- Modify: `api/workflow_routes.py`
- Test: `tests/test_enquiry_endpoints.py`

**Interfaces:**
- Consumes: `enquiry.preview`, `enquiry.dispatch`, `mail.transport_for` (Tasks 1, 2, 4).
- Produces:
  - `POST /api/workflow/rfqs/{rfq_id}/enquiry/preview` -> `{"recipients": [...], "transport": "outbox" | "smtp"}`
  - `POST /api/workflow/rfqs/{rfq_id}/enquiry/send` -> `{"sent": [...], "skipped": [...]}`
- **Store invariant owned:** **the preview route leaves `workflow.json` byte-for-byte unchanged.**

That invariant gets its own assertion, comparing the file before and after — the only assertion that catches a convenience write nobody meant to add. It is the same test `/rfqs/extract` carries.

Both routes are **not** on `middleware.PUBLIC_PATHS` and must not be. `test_auth_middleware.py`'s route sweep needs the new path parameters added to its probe substitutions.

`preview` sends `transport` alongside so the browser can say *"this will write to the outbox"* versus *"this will reach real mailboxes"* without inferring it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_enquiry_endpoints.py

def test_the_preview_route_stores_nothing(client, tmp_root, an_rfq_with_a_shortlist):
    """Byte-for-byte, which is the only assertion that catches a convenience
    write nobody meant to add."""
    path = os.path.join(tmp_root, "workflow.json")
    with open(path, "rb") as handle:
        before = handle.read()

    response = client.post(f"/api/workflow/rfqs/{an_rfq_with_a_shortlist}/enquiry/preview")

    assert response.status_code == 200
    with open(path, "rb") as handle:
        assert handle.read() == before


def test_the_preview_names_the_transport_it_would_use(client, an_rfq_with_a_shortlist):
    body = client.post(
        f"/api/workflow/rfqs/{an_rfq_with_a_shortlist}/enquiry/preview"
    ).json()
    assert body["transport"] == "outbox"


def test_an_unknown_rfq_is_a_404(client):
    assert client.post("/api/workflow/rfqs/rfq_nope/enquiry/preview").status_code == 404
    assert client.post("/api/workflow/rfqs/rfq_nope/enquiry/send").status_code == 404


def test_sending_records_who_sent_it_from_the_session(client, an_rfq_with_a_shortlist):
    """`by` comes from the session and is never an input — a caller could
    otherwise sign somebody else's name to a tender."""
    body = client.post(
        f"/api/workflow/rfqs/{an_rfq_with_a_shortlist}/enquiry/send"
    ).json()
    assert body["sent"][0]["by"] == ADMIN_EMAIL


def test_the_send_route_returns_both_halves(client, an_rfq_with_a_mixed_shortlist):
    body = client.post(
        f"/api/workflow/rfqs/{an_rfq_with_a_mixed_shortlist}/enquiry/send"
    ).json()
    assert body["sent"] and body["skipped"]
    assert "reason" in body["skipped"][0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_enquiry_endpoints.py -q`
Expected: FAIL — 404 on every route

- [ ] **Step 3: Write the minimal implementation**

Reference — `by=user.email` and the `locked_update` placement are load-bearing.

```python
@router.post("/rfqs/{rfq_id}/enquiry/preview")
def preview_enquiry(rfq_id: str, user: User = Depends(current_user)) -> dict:
    store = _read()
    if store.get_rfq(rfq_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown RFQ: {rfq_id}")
    blobs = doc_store.LocalBlobStore(_root())
    rows = enquiry.preview(store, blobs, rfq_id)
    return {
        "recipients": [r.model_dump(mode="json") for r in rows],
        "transport": "smtp" if isinstance(
            mail.transport_for(_root()), mail.SmtpImapTransport
        ) else "outbox",
    }


@router.post("/rfqs/{rfq_id}/enquiry/send")
def send_enquiry(rfq_id: str, user: User = Depends(current_user)) -> dict:
    transport = mail.transport_for(_root())          # outside the lock: it does no I/O
    blobs = doc_store.LocalBlobStore(_root())
    try:
        with persistence.locked_update(_root()) as store:
            result = enquiry.dispatch(
                store, blobs, transport, rfq_id, by=user.email
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result.model_dump(mode="json")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_enquiry_endpoints.py tests/test_auth_middleware.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/workflow_routes.py tests/test_enquiry_endpoints.py tests/test_auth_middleware.py
git commit -m "feat: look before you send, on two routes"
```

---

### Task 6: The browser — preview, confirm, send

**Files:**
- Modify: `web/src/api.ts`, `web/src/types.ts`
- Create: `web/src/pages/wizard/SendEnquiry.tsx`
- Modify: `web/src/pages/wizard/RaiseRfqStep.tsx` (render it under the TBE editor)
- Test: `web/src/pages/wizard/SendEnquiry.test.tsx`

**Interfaces:**
- Consumes: both routes from Task 5.
- Produces: `previewEnquiry(rfqId)`, `sendEnquiry(rfqId)` in `api.ts`; `<SendEnquiry data={...} run={...} busy={...} />`.
- **Store invariant owned:** none — this task writes nothing. It owns the interaction one: **the send control is unreachable until a preview has been fetched and is on screen.**

That is what your "confirm the recipient list each time" decision buys, and it is the only defence against section 1's name collision. A send button that works without a preview makes the rest of this phase decorative.

The card must say **which transport** it will use, prominently, and in different words for the two cases. "Will write to the outbox" and "Will send real email to N vendors" reading identically is how a real tender goes out during a demo.

- [ ] **Step 1: Write the failing tests**

```tsx
it('offers no send control before a preview has been fetched', () => {
  render(<SendEnquiry {...props()} />)
  expect(screen.queryByRole('button', { name: /^Send/ })).not.toBeInTheDocument()
})

it('lists every resolved address before sending', async () => {
  vi.mocked(previewEnquiry).mockResolvedValue({
    transport: 'outbox',
    recipients: [
      { shortlist_entry_id: 'sle_1', vendor_name: 'Galfar', to: ['sales@galfar.example'], skip_reason: null },
    ],
  })
  render(<SendEnquiry {...props()} />)

  fireEvent.click(screen.getByRole('button', { name: /Preview recipients/ }))

  expect(await screen.findByText('sales@galfar.example')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /^Send/ })).toBeInTheDocument()
})

it('says the outbox and real mail in different words', async () => {
  // The two must not read identically — that is how a real tender goes out
  // during a demo.
  vi.mocked(previewEnquiry).mockResolvedValue({ transport: 'smtp', recipients: [] })
  render(<SendEnquiry {...props()} />)
  fireEvent.click(screen.getByRole('button', { name: /Preview recipients/ }))
  expect(await screen.findByText(/real email/i)).toBeInTheDocument()
})

it('renders every skipped vendor with its reason, not a count', async () => {
  vi.mocked(sendEnquiry).mockResolvedValue({
    sent: [],
    skipped: [{ vendor_name: 'Nowhere Trading', reason: 'No address on file for Nowhere Trading.' }],
  })
  // ...preview, then send
  expect(await screen.findByText(/No address on file for Nowhere Trading/)).toBeInTheDocument()
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npx vitest run src/pages/wizard/SendEnquiry.test.tsx`
Expected: FAIL — module not found

- [ ] **Step 3: Write the component**

```tsx
export function SendEnquiry({ data, run, busy }: Omit<StepProps, 'tick'>) {
  const [preview, setPreview] = useState<EnquiryPreview | null>(null)
  const [result, setResult] = useState<EnquiryDispatch | null>(null)

  // The send control is rendered only inside this branch. Not disabled outside
  // it -- absent. A disabled button that becomes enabled on some other state
  // change is one refactor away from being reachable without a preview, which
  // is the whole guard this card exists for.
  return (
    <>
      <h3>Send the enquiry</h3>
      {preview === null ? (
        <button type="button" className="btn" disabled={busy}
          onClick={() => run(async () => setPreview(await previewEnquiry(data.rfq.id)))}>
          Preview recipients
        </button>
      ) : (
        <>
          <p className={preview.transport === 'smtp' ? 'warn' : 'muted'}>
            {preview.transport === 'smtp'
              ? `This sends real email to ${preview.recipients.filter((r) => r.to).length} vendors.`
              : 'This writes to the outbox. Nobody receives anything.'}
          </p>
          <div className="table-scroll">
            <table className="table">
              <thead><tr><th scope="col">Vendor</th><th scope="col">Goes to</th></tr></thead>
              <tbody>
                {preview.recipients.map((r) => (
                  <tr key={r.shortlist_entry_id}>
                    <td>{r.vendor_name}</td>
                    <td>
                      {r.to === null
                        ? <span className="warn">{r.skip_reason}</span>
                        : r.to.map((a) => <div key={a}>{a}</div>)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button type="button" className="btn" disabled={busy}
            onClick={() => run(async () => setResult(await sendEnquiry(data.rfq.id)))}>
            Send to {preview.recipients.filter((r) => r.to).length} vendors
          </button>
        </>
      )}
      {result?.skipped.length ? (
        <ul>{result.skipped.map((s) => <li key={s.vendor_name} className="warn">{s.reason}</li>)}</ul>
      ) : null}
    </>
  )
}
```

The two transport sentences are load-bearing and must not be unified into one
string with an interpolated word. The table markup is illustrative, except the
`.table-scroll` wrapper, which `table-scroll.test.ts` asserts structurally.

- [ ] **Step 4: Run the tests, the whole web suite, and the type check**

Run: `cd web && npx vitest run && npx tsc -b --noEmit`
Expected: PASS; type check clean

- [ ] **Step 4b: Launch the app and measure**

`.\run.ps1`, sign in, open an RFQ at Issued, press Preview. jsdom applies no stylesheet and does no layout — every rendering defect this repository has recorded was found this way. Check the recipient table sits in a `.table-scroll` wrapper and that the document does not scroll sideways.

- [ ] **Step 5: Commit**

```bash
git add web/src
git commit -m "feat: read the recipients, then send"
```

---

### Task 7: Integration, and the two-run mutation matrix

**Files:**
- Modify: `tests/test_workflow_persistence.py`
- Modify: `CLAUDE.md`
- Modify: `.superpowers/sdd/bid-desk/progress.md`

**Interfaces:**
- Consumes: everything above.
- Produces: no new API.
- **Store invariant owned:** the two owned in Tasks 3 and 4, defended here across a **reload**. Nothing new is claimed — this task proves what the others asserted survives a round trip through disk.

**On PLAN-TEMPLATE.md's nine required rows.** They all mutate the extraction pipeline — a newer document revision, a prompt-version bump, an LLM call failing between runs. This subsystem calls no model and runs no extraction, so seven of the nine have no analogue here. They are **deliberately absent rather than fabricated**, the same treatment `test_workflow_persistence.py` already documents above its existing rows. The two that do carry across are kept, in their real shape. Say so in a comment above the table; a matrix that silently drops rows reads as one that covered them.

- [ ] **Step 1: Write the mutation matrix**

| mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|
| a send is recorded, then the store is reloaded | `workflow.json` holds exactly the `EnquirySend` records whose RFQ still exists | run 2 reads one record, with the same `to` and `transport` |
| a send is recorded, then its shortlist entry is removed | removing a shortlist entry leaves its send record | run 2 still holds the record |
| a second vendor is shortlisted between the runs | an RFQ holds at most one record per `shortlist_entry_id` | run 2's dispatch sends to the new vendor only |
| a dispatch runs twice across a reload | *(same)* — this is the row a single run cannot see | run 2 sends **nothing**; both vendors skipped as already sent |
| a record's RFQ is absent from the document | records whose RFQ has gone are dropped on load | the store loads with no sends and does not raise |
| a vendor's address is corrected between the runs | `to` is frozen, never derived | run 2's stored record still holds the **old** address |
| **[analogue]** a document is added to the RFQ between runs | attachments are read live, not frozen | run 2's dispatch to a new vendor carries both documents |
| **[analogue]** a dispatch fails on run 1 and succeeds on run 2 | a transient failure is not cached as a permanent answer | the failed vendor has no record after run 1, and is sent to on run 2 |

The fourth row is the load-bearing one. The already-sent check reads records that on run 2 came off disk, so an `enquiry_sends` key lost in serialization reads as *"nobody has been sent to"* and mails the whole shortlist a duplicate tender. No single-run test can see it.

The sixth row defends the design's central storage decision against the obvious "optimisation" of deriving `to` from the directory at read time.

- [ ] **Step 2: Run the matrix to verify each row fails against its own defect**

Reinstate each defect one at a time — drop the `enquiry_sends` key from `to_document`; make `remove_shortlist_entry` cascade; derive `to` on read — and confirm **the intended row fails and nothing else does**. A row that still passes with its defect reinstated is not testing what it claims.

- [ ] **Step 3: Run both suites**

Run: `python -m pytest -q` then `cd web && npm test`
Expected: the 10 pre-existing failures named in Global Constraints and no others; web green.

- [ ] **Step 4: Update the docs**

`CLAUDE.md` gains the mail invariants under the RFQ workflow section: the outbox default and how the flag is read, one message per vendor, `to` stored rather than derived, the three skip conditions, and the append-only send record. Record the measured test counts — do not derive them.

`.superpowers/sdd/bid-desk/progress.md`: mark T9 partially done, naming the outbound half only. **The ledger is currently stale** — T5, T6 and T7 read "not started" while `CLAUDE.md` records BD-5 and BD-6 as shipped. Correct those rows too, or note the discrepancy rather than adding a row to a table nobody can trust.

- [ ] **Step 5: Commit**

```bash
git add tests/test_workflow_persistence.py CLAUDE.md .superpowers/sdd/bid-desk/progress.md
git commit -m "test: eight rows the second run can see, and what the first cannot"
```
