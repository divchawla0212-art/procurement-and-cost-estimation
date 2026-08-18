# Sending the enquiry by email — design

**Date:** 2026-08-19
**Phase:** BD-9, first half — outbound only
**Status:** design, not built

The RFQ's documents reach the shortlisted vendors as email. One transport
interface, an outbox by default, and real mail only when somebody has said so
in the environment.

## Scope

BD-9's requirement in [`feature-request.md`](../../../feature-request.md) reads
*"the thread in BD-8 is carried over email"*, and BD-8 is not built. This phase
takes the half that is reachable now, and that the parts already shipped were
built for: **the enquiry going out**. The RFQ documents exist (BD-6), the
vendor-contact directory exists and puts an address on every shortlist row, and
the shortlist is approved before an RFQ can be issued. Nothing else is needed to
send a tender.

The clarification thread — inbound mail, `poll`, IMAP, `In-Reply-To` threading
against a `ClarificationQuery` — is the second half and gets its own spec. It is
named here only where this phase has to leave room for it.

## What this deliberately does not build

- **No inbound mail.** No `poll` on the protocol, no IMAP client, no parsing of
  a reply. A protocol method with no implementation and no caller is dead
  weight, and the shape of inbound is a decision belonging to the phase that
  reads it.
- **No stage transition.** Sending does not move the RFQ. It is already at
  Issued when it happens, and `_issued_exit` guards the TBE template rather than
  the send.
- **No bulk send across RFQs**, no scheduling, no retry queue, no bounce
  handling, no delivery receipts. A send either happened, or is recorded as
  skipped with a reason.
- **No editing an address at send time.** The directory is the source. A wrong
  address is fixed by re-uploading the sheet, which is one place rather than
  two.
- **No transactional provider.** Resend, Brevo and SendGrid have free tiers that
  would serve and would buy deliverability. They remain the upgrade path the
  bid-desk design flagged. `smtplib` against an ordinary mailbox costs nothing
  and is enough at this volume.

---

## 1. The hazard, stated first

`VendorContact`'s own docstring names it:

> A wrong match here mails a tender to the wrong company while rendering
> identically to a right one.

Addresses are keyed on the **folded vendor name**. There is no `vendor_id` in
the directory, because the contact sheet carries no vendor number and matching
on a name is a defect this repository has recorded twice. `store.emails_for`
folds through `disciplines.fold` and does nothing else, so `L.L.C` against `LLC`
is a miss.

That produces two failure directions, and they are not symmetric.

**A miss is safe and visible.** The vendor resolves to no address, is skipped,
and the buyer is told. Nothing goes anywhere.

**A collision is neither.** Two suppliers sharing a folded trading name resolve
to one contact, and the enquiry reaches a company that was never shortlisted —
while looking exactly like a correct send on screen. No amount of care inside
the mail code prevents this, because the mail code is handed an address that is
already wrong.

The answer is not to widen or narrow the matching. It is that **a human sees the
resolved addresses before anything leaves**. That is section 5.

## 2. Transport — `workflow/mail.py`

```python
class MailTransport(Protocol):
    def send(self, message: OutboundMail) -> SentRef: ...
```

Two implementations.

`OutboxTransport` is **the default**. It writes an `.eml` under
`<ROOT>/outbox/` and sends nothing. Every test, every seed and every demo uses
it. The file is a real RFC 5322 message built by `email.message.EmailMessage`,
so what the outbox holds is what SMTP would have carried — an outbox that wrote
a summary instead would prove nothing about the message.

`SmtpImapTransport` sends over `smtplib` with STARTTLS. It is **constructed only
when `MAIL_TRANSPORT=smtp` is explicitly set**. Absent, empty, `0` or `false`
leaves the outbox in place — read exactly the way `AUTH_DISABLED` is read, and
for the same reason.

The safety property, which section 9 states as a test:

> No test, no seed and no default configuration can send mail to a real address.
> Reaching a real mailbox takes a deliberate environment change.

`transport_for(root)` is the one place that reads the environment and returns
one or the other. Routes never construct a transport, so there is no second
place for the default to be got wrong.

## 3. One message per vendor

**A single message addressed to the whole shortlist discloses every bidder's
identity to their competitors.** That is a real procurement failure, not a
tidiness preference: who else was invited is commercially significant, and a
tender that leaks it is a tender that has to be re-run.

So each vendor gets their own `OutboundMail`, carrying only their own addresses.
No `Cc` and no `Bcc` spanning vendors. `test_no_message_carries_two_vendors_addresses`
is what holds it, and it asserts across the whole dispatch rather than on one
message — a per-message assertion passes happily against a loop that sends the
same all-recipients message N times.

## 4. What is attached

The contractor's own documents: `submitted_by_vendor_id is None`, the same
filter the Issued step already draws its list from. A vendor's returned bid can
therefore never be attached to an outgoing enquiry, which is the failure that
would put one bidder's pricing in front of another.

Bytes come from `doc_store` through `BlobRef`, never from a path built at the
call site.

**Total attachment size is capped at 25 MB**, Gmail's limit. A vendor whose
package exceeds it is skipped with a reason naming the size, rather than the
send failing inside `smtplib` with a message nobody can act on. The package is
the same for every vendor, so the cap either fits for all of them or for none —
but it is reported per vendor, because that is the shape the result already has.

## 5. Two calls: preview, then send

```
POST /rfqs/{rfq_id}/enquiry/preview   -> resolves; stores nothing
POST /rfqs/{rfq_id}/enquiry/send      -> sends; records
```

The preview resolves every shortlisted vendor to either the addresses the
enquiry would go to, or the sentence saying why they would be skipped. **It
stores nothing** — the rule `/rfqs/extract` and `/vendor-suggestions` keep, for
the same reason: a buyer who looks and decides not to send should leave nothing
behind.

This is what makes "confirm the recipient list each time" real rather than a
checkbox. The environment variable decides whether mail leaves at all; it says
nothing about who it reaches. Only a human reading the resolved addresses can
catch section 1's collision, and they can only read them if something shows
them first.

The send route **re-resolves rather than trusting what the preview returned**. A
recipient list posted back from the browser is one a stale tab — or an attacker
— can edit, and the directory is the source of truth.

## 6. The record, and why this one is stored

`EnquirySend`, one per vendor per RFQ:

| field | why |
|---|---|
| `rfq_id`, `shortlist_entry_id` | what was sent, and to whom on the shortlist |
| `vendor_name` | as it was then |
| `to` | **the addresses actually used** |
| `sent_at`, `by` | an attributed act |
| `sent_ref` | the transport's own handle — `Message-ID`, or the outbox path |
| `transport` | `outbox` or `smtp` |

The `email` key on a shortlist row is **derived on read**, so correcting the
sheet corrects every shortlist at once. This record is the exact opposite and
deliberately so: it records something that happened. Re-uploading the contact
sheet must not rewrite who a tender was sent to. It is frozen for the same
reason `ShortlistEntry.prequal_status` is frozen — that row is an audit trail,
and an audit trail that updates itself is not one.

`transport` is on the record because *"this went to the outbox"* and *"this
reached a real mailbox"* must not be indistinguishable later. A demo run and a
real tender leave the same shape of record otherwise.

### Invariants

- **`workflow.json` holds exactly the `EnquirySend` records whose RFQ still
  exists.** Held at both ends, like `RfqDocument` (I-E): the store refuses a
  send against an unknown RFQ, and `persistence.from_document` drops a record
  whose RFQ has gone.
- **A vendor is sent to once.** The key is `shortlist_entry_id`. A second press
  of Send skips whoever already has a record and sends to whoever does not — so
  a vendor shortlisted after the first send gets the enquiry, and nobody gets a
  duplicate tender from a double click. Re-sending to somebody already sent is a
  separate, deliberate act and is **not built in this phase**.
- **Removing a shortlist entry does not remove its send record.** The mail was
  sent; deleting the record would falsify that. This is the append-only rule the
  stage history keeps, arriving in a second place.
- **Every write, and every decision that gates one, happens inside
  `persistence.locked_update`.** The "has this vendor already been sent to"
  check is a read that gates a write, so it lives in the store method and not in
  the route — the sixth time this repository has needed that rule.

## 7. Result shape

`EnquiryDispatch` carries the count **and** every skipped vendor with its own
sentence, modelled on `DraftAdoption`:

```python
class SkippedRecipient(BaseModel):
    vendor_name: str
    reason: str


class EnquiryDispatch(BaseModel):
    sent: list[EnquirySend]
    skipped: list[SkippedRecipient]
```

A count alone is silent about who did not get the tender, and a buyer whose
enquiry quietly reached four vendors out of six has nothing to act on. Same rule
as *a gate never returns a bare `False`*.

Exactly three conditions are absorbed into `skipped`, and all three are named:

1. no address in the directory,
2. the package exceeds the size cap,
3. this vendor has already been sent to.

Anything else propagates. A blanket `except Exception` here would swallow a
transport misconfiguration and report it as "vendors skipped", which reads on
screen as a directory problem and sends a buyer to fix the wrong thing.

## 8. Configuration

Added to `.env.example`, all **empty**:

```
MAIL_TRANSPORT=
MAIL_FROM=
SMTP_HOST=
SMTP_PORT=
SMTP_USERNAME=
SMTP_PASSWORD=
```

The sender decided on 2026-08-18 is `rahuljana.business@gmail.com`, Gmail plus
an app password. **The credential is the operator's to place in `.env`.** It is
not committed, not defaulted, and not handled by anyone building this.

`MAIL_FROM` is required when `MAIL_TRANSPORT=smtp`. Missing, the factory raises
at construction rather than at the first send — a misconfiguration that surfaces
only when a buyer presses Send is one that surfaces in front of a customer.

The three mailboxes recorded on 2026-08-16 — `sales@bks-sol.com`,
`Naushad@bks-sol.com`, `Anas.salem@etap.com` — are **not wired in as
recipients**. Recipients come from the directory and from nowhere else. Those
addresses receive mail only if they are in an uploaded contact sheet, which is
the same door every other vendor's address comes through.

## 9. Testing

Every test uses `OutboxTransport`. The suite stays key-free, and no test may
require an SMTP host.

The load-bearing ones:

- **`test_the_default_transport_is_the_outbox`** — asserts the safety property
  directly, with no `MAIL_TRANSPORT` in the environment. This is what fails if
  somebody inverts the default, which is the single change that would turn a
  demo into a real send.
- **`test_an_unset_or_falsy_flag_leaves_the_outbox_in_place`** — absent, empty,
  `0` and `false`, one case each. `AUTH_DISABLED` is read this way and the two
  must not drift.
- **`test_no_message_carries_two_vendors_addresses`** — asserted across the
  whole dispatch, per section 3.
- **`test_a_vendor_with_no_address_is_skipped_and_named`** — the miss direction
  of section 1, and that it is reported rather than dropped.
- **`test_no_vendors_bid_is_ever_attached_to_an_enquiry`** — a document with
  `submitted_by_vendor_id` set, asserted absent from the message.
- **`test_a_second_send_reaches_only_the_newly_shortlisted`** — a **two-run**
  test, and the one a single run cannot see: the already-sent check reads
  records that on run 2 came off disk, so an `enquiry_sends` key lost in
  serialization reads as "nobody has been sent to" and mails the whole shortlist
  a duplicate.
- **`test_removing_a_shortlist_entry_leaves_its_send_record`** — the append-only
  rule of section 6.
- A **two-run mutation matrix** row in `test_workflow_persistence.py`, per
  [`PLAN-TEMPLATE.md`](../PLAN-TEMPLATE.md).

The browser side asserts that the preview renders every resolved address, that
the send control is not reachable without having previewed, and that the skipped
list is rendered rather than counted.

## 10. What this leaves for the next phase

Inbound. `poll`, an IMAP client, and threading a reply back to the
`ClarificationQuery` it answers through `In-Reply-To` and `References`. The
`SentRef` recorded here is what a reply will be matched against, which is why it
is stored now rather than discarded.

Also left: re-sending to a vendor already sent to, an addendum going out as a
follow-up, and bounce handling. Each is a deliberate act with its own
attribution, and none of them is *sending the enquiry*.
