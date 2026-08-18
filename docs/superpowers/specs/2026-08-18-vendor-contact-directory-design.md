# A vendor contact directory, and an address on every invited bidder — design

**Date:** 2026-08-18
**Branch:** `rfq-platform-phase-1`
**Follows:** [`2026-08-15-bid-desk-design.md`](2026-08-15-bid-desk-design.md) §8
**Precedes:** BD-9 — the mail transport, which consumes what this builds

---

## The problem

A shortlist says who is invited. It does not say **where to send the enquiry**.

`Bidder` has no email field. The ADNOC Approved Vendor List export does not
carry one, so the ~1 300-vendor registry built from it has no addresses in it
and never will from that source. `ShortlistEntry` carries a vendor name, a
prequalification snapshot and a scope-fit code, and no way to reach anybody.

The client supplied a second, much smaller spreadsheet — one tab, two columns,
`Vendor` and `Email` — which is exactly the missing half. This design gives that
sheet somewhere to live and puts the address it carries on screen beside the
bidder it belongs to.

## What this builds

- An organisation-wide **vendor contact directory**, name-keyed, held in
  `<ROOT>/bidders.db` beside the registry.
- A parser for the `Vendor` / `Email` sheet.
- Upload and read routes.
- One derived key, `email`, on every shortlist row.
- The upload control and an **Email** column on the RFQ wizard's Shortlisting
  step.

## What this deliberately does not build

**No mail is sent.** There is no transport — §8 of the bid-desk design specifies
one and it is not implemented; there is no `workflow/mail*` module and no
`MAIL_*` key in `.env.example`. This phase populates addresses and shows them.
Sending is BD-9 and gets its own spec.

The sender is decided and recorded but unused here: `rahuljana.business@gmail.com`,
Gmail plus an app password, chosen with "just do the simplest implementation".
The credential is the operator's to place in `.env`. The recipients previously
recorded — `sales@bks-sol.com`, `Naushad@bks-sol.com`, `Anas.salem@etap.com` —
are real external mailboxes, and nothing in this phase can reach them, because
nothing in this phase sends.

Also out: no write to the registry, no bidder created, no approval granted, no
per-RFQ override of the directory, and no editing an address in the browser.

---

## 1. The honesty problem, stated first

The sheet carries **no vendor number**. The registry is keyed `bdr_<vendor
number>` because that is what the AVL export provides and what makes a lookup a
fact rather than a guess. This sheet gives a name and nothing else, so tying one
of its rows to a registry vendor could only be done by comparing names — and
this repository has recorded name matching as a shipped defect twice, in
`add_item_vendor` and again in the draft shortlist.

The resolution is **not** to match names more cleverly. It is to stop pretending
there is a link at all:

- The directory is **keyed by folded vendor name and holds no `vendor_id`.**
  Nothing here resolves to a registry row, so nothing here can attach a real
  company's approvals to a string somebody typed into a spreadsheet.
- The lookup at read time is against the **shortlist entry's own
  `vendor_name`**, folded — not against `vendor_id`. That is the same string the
  screen already shows, so what the reader sees is what was matched.
- Folding is `disciplines.fold` — case-folded, internal whitespace collapsed —
  **and nothing more.** `DANWAY ABU DHABI L.L.C` and `Danway Abu Dhabi LLC` do
  not match, and that is the correct outcome. Widening the fold to strip
  punctuation or corporate suffixes is how an enquiry reaches the wrong company,
  and the failure would be invisible: a plausible near-miss renders identically
  to a real match.
- A vendor with no matching row reads as **no address on file**, never as an
  empty result that looks like success.

The one concession to convenience is a **match count reported at upload** — how
many uploaded names correspond to a registry bidder. It is derived, stored
nowhere, and informational only: an upload that matches nobody is almost always
a spelling problem, and a bare "8 rows stored" hides that.

**Upside of name-keying, worth stating:** a hand-typed shortlist row — a vendor
who is on nobody's register — gets an address too. An id-keyed directory could
never reach them.

## 2. Storage — a table in `bidders.db`

`<ROOT>/bidders.db`, two new tables, behind a new `workflow/contact_db.py`:

```sql
CREATE TABLE IF NOT EXISTS vendor_contacts (
    name_key        TEXT PRIMARY KEY,
    vendor_name     TEXT NOT NULL,
    source_document TEXT NOT NULL,
    uploaded_by     TEXT NOT NULL,
    uploaded_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vendor_contact_emails (
    name_key TEXT NOT NULL REFERENCES vendor_contacts(name_key) ON DELETE CASCADE,
    email    TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (name_key, email)
);
```

**Why that file and not `workflow.json`.** CLAUDE.md states the split rule as
"the registry is in SQLite; everything else is in the JSON document", and gives
four reasons for the registry's move. All four apply here verbatim: this is
reference data, it arrives whole from an export, it is replaced wholesale, and
it is queried by key rather than read whole. It is the same *kind* of thing as
the registry — vendor reference data — which is why it shares the file.

**Why its own table and not a column on `bidders`.** A column would make the
directory a property of the registry, which would mean an AVL reload destroys it
and a vendor absent from the registry can hold no address. Neither is wanted.

`name_key` as the primary key makes ambiguity structurally impossible rather
than guarded against — there cannot be two rows for one folded name, so no query
has to decide which one wins.

`vendor_name` is stored as the sheet wrote it, so a row can be read back against
its source document — the same rule `ItemVendorEntry` keeps.

**`bidder_db.replace_all` does `DELETE FROM bidders` and nothing else**, so
reloading the ADNOC export leaves the directory intact. That is an invariant
with a test on it, not a happy accident: the two sets of tables live in one file
and a future `DROP`-and-recreate in either module would silently take the other
with it.

`fold` is `disciplines.fold` re-exported, exactly as `bidder_db` re-exports it,
so the key written and the key queried cannot drift. When those two disagree the
symptom is silent — a vendor invisible to the very lookup their own row
satisfies.

## 3. The model — `workflow/models/vendor_contact.py`

```python
class VendorContact(BaseModel):
    vendor_name: str
    emails: list[str]          # never empty; a row with no address is refused
    source_document: str
    uploaded_by: str
    uploaded_at: datetime
```

No `id` and no `vendor_id`. The folded name is the identity, and there is no
registry link by design (§1).

## 4. The parser — `workflow/contact_import.py`

`parse_contacts(path) -> list[VendorContact]`. `openpyxl`, no store, no clock —
the uploader and the timestamp are passed in by the caller, the same shape
`item_vendor_lists.entries_for` is written in and for the same reason.

**Columns are located by header, never by position**, the rule `avl_import`
already states: a re-export with a column inserted must not silently read
addresses out of the wrong column. Headers are normalised the way `avl_import`
normalises them (whitespace collapsed, case-folded) and accepted as:

| meaning | accepted headers |
|---|---|
| vendor | `Vendor`, `Vendor Name`, `Name` |
| email | `Email`, `Email ID`, `E-mail`, `Email Address` |

A missing required header raises with the parser's own sentence naming what is
missing and listing what was found — a refusal that does not say what is wrong
with the file leaves the reader nothing to act on.

Three row-level rules:

- **A cell may hold several addresses**, separated by `,` or `;`, all kept in
  order. RFQ distribution routinely goes to more than one person at a vendor,
  and supporting it costs one `re.split`.
- **A malformed address is refused, with its row number.** The check is
  deliberately shallow — exactly one `@`, no whitespace, something either side —
  because a strict RFC validator rejects addresses that work. Refused rather
  than dropped: a silently skipped row is a vendor who never receives the
  enquiry, and nobody finds out until the bid is missing.
- **A row with a vendor and no address is refused.** A contact with no address
  is not a contact, and storing one would create an `emails: []` state that the
  read side would then have to distinguish from "no row at all" for no gain.

**Duplicate rows merge.** Two rows folding to one vendor name become one entry
with the union of their addresses, first spelling of the name kept. This is a
deliberate change from the refusal offered during design: once several addresses
per vendor are supported, two rows for one vendor is not ambiguity, it is two
contacts, and refusing the upload would make the common case an error.

Nothing here creates a bidder, grants an approval or touches the registry.

## 5. The store and persistence

`WorkflowStore` gains one field:

```python
self._vendor_contacts: dict[str, VendorContact] = {}   # keyed by fold(vendor_name)
```

with `set_vendor_contacts(contacts)` (wholesale replacement) and
`emails_for(vendor_name) -> list[str] | None`.

**This is the second field exempt from the `to_document` / `from_document`
rule**, and the exemption must be stated as loudly as the first. CLAUDE.md says
any field added to `WorkflowStore.__init__` needs a matching line in both, or it
silently fails to survive a restart. `_bidders` is exempt because the registry
lives in SQLite; `_vendor_contacts` is exempt for exactly the same reason. **The
document must have no `vendor_contacts` key** — a key there would be a second
copy for the first edit to disagree with, which is the argument that moved the
registry out.

`persistence.load` hydrates from `contact_db.list_all`; `locked_update` writes
back through `contact_db.replace_all` **only when the directory changed**,
compared against a shallow copy taken at load — the mechanism already in place
for the registry, and it works for the same reason: replacement rebuilds the
dict rather than mutating a stored model in place.

`WorkflowStore` still knows nothing about disk. All of the I/O is in
`persistence` and `contact_db`.

## 6. Routes

Both on the existing `/api/workflow` router, both requiring a session — workflow
routes are not on `PUBLIC_PATHS` and must not be.

**`POST /vendor-contacts`** — multipart, one `.xlsx`.

Parse **outside** the lock into a temporary file that is removed either way, and
write **inside** it. That is the shape `/vendor-list` already uses, and the
reason is the same: parsing is pure CPU over a file, and holding the store lock
through it blocks every other writer.

The file itself is not kept. The entries are the record and `source_document`
holds the filename — the rule `/vendor-list` and `/rfqs/extract` both keep.

Answers with a summary:

```json
{"parsed": 9, "stored": 8, "addresses": 8, "matched": 6}
```

`parsed` counts sheet rows read, `stored` counts entries after the merge,
`addresses` counts distinct addresses, and `matched` counts stored names that
correspond to a registry bidder — folded name against `bidders.name_key`, which
is already indexed, computed for this response and kept nowhere. Nothing else in
the system consults it, and no stored field is written from it (§1). Counts
rather than a tick, for the reason the
vendor-list upload reports its own: an empty or half-size result is a narrowing
or a spelling problem, not a failure, and only the numbers say which.

**`GET /vendor-contacts`** — the whole directory plus `count` and the upload's
provenance, so the screen can say who loaded it and when.

Any signed-in user may upload. This matches the item vendor-list upload, which
is not admin-gated; the directory is procurement's own working data, not
accounts.

## 7. The derived read — `_shortlist_payload`

One key added:

```python
"email": store.emails_for(entry.vendor_name),   # list[str] | None
```

**Derived on read, stored nowhere.** No `email` field on `ShortlistEntry`, no
key for it in `workflow.json`. This is the third instance of the rule that
already governs `client_approved` and `approved_by` on the same record, and the
reason is unchanged: a copy taken at invitation is wrong the moment the
directory is corrected, and correcting it is the common case. Re-upload the
sheet and every shortlist in the system is right, with nothing rewritten.

Two states, not three. `None` means no directory row for this vendor's folded
name. There is no `[]`, because §4 refuses a row with no address — so the read
side never has to distinguish "we hold a contact with no way to reach them" from
"we hold no contact", and neither does the screen.

The lookup is keyed on `vendor_name`, **not** `vendor_id` (§1). A registry-linked
row and a hand-typed row resolve the same way, which is what lets an
unregistered vendor have an address at all.

## 8. The screen — the Shortlisting step

Above the Invited bidders table: an **Add vendor email list** control (`.xlsx`,
the `.sr-only` input behind a button, matching `VendorListUpload`), one status
line reading the counts back, and a caption naming what the directory is:

> Organisation-wide · 8 vendors · uploaded by … on …

The caption is load-bearing, not decoration. The control sits inside one RFQ but
the directory is shared, and a control that looks per-RFQ while behaving
globally invites a buyer to overwrite everyone else's addresses believing they
are editing their own enquiry.

The Invited bidders table gains an **Email** column. `null` renders as an
em-dash with a muted *No address on file* — honest, because the directory was in
fact consulted, unlike the curated vendor cards which carry no Registry column
precisely because nothing looks those names up.

Several addresses render as several lines in the cell. The table goes inside a
`.table-scroll` wrapper, which `table-scroll.test.ts` scans the source for.

No editing an address in the browser. Correcting one means re-uploading the
corrected sheet — the rule every uploaded source in this repository keeps, and
the reason is that a row edited in place no longer matches any document a
re-upload reproduces.

---

## 9. Store invariants this phase introduces

Named here so the plan can assign each to exactly one task (PLAN-TEMPLATE Rule 1).

- **V-A.** `vendor_contacts` holds exactly the vendors of the most recent
  upload — no more. A previous upload's vendor cannot survive a later one.
- **V-B.** `vendor_contact_emails` holds exactly the addresses of the rows in
  `vendor_contacts`; no address outlives its vendor.
- **V-C.** `workflow.json` has **no** `vendor_contacts` key, in any document
  this phase writes.
- **V-D.** Reloading the registry (`bidder_db.replace_all`) leaves
  `vendor_contacts` and `vendor_contact_emails` exactly as they were.
- **V-E.** Every stored `name_key` equals `fold(vendor_name)` of its own row —
  the key written and the key queried are the same function.
- **V-F.** No `ShortlistEntry` stores the address it reports.

## 10. Testing strategy

Both baselines move. The workstation row is **measured** and the CI row derived
from it by the documented subtraction — never edited independently. Nothing in
this phase reads a fixture directory, calls a provider, or touches the three
files carrying `needs_real_avl`, so the AVL gate stays at eleven and is not
re-measured, which is the condition CLAUDE.md sets.

Python, by module: the parser (header location, the accepted header spellings,
multi-address cells, the malformed-address refusal naming its row, the
no-address refusal, the duplicate merge, the missing-column sentence);
`contact_db` (wholesale replacement, the folded lookup, survival of an AVL
reload); persistence (hydration, the change-detected write, and the absent
document key); the two routes (the summary counts, a 422 on a workbook with no
email column, the session requirement); and `_shortlist_payload`'s two states.

Web: the upload control, the counts line, the Email column, the *No address on
file* rendering, and the organisation-wide caption, in
`ShortlistingStep.test.tsx`.

**Two-run rows** (PLAN-TEMPLATE Rule 2), because this repository's surviving
defects have all needed two runs or two modules to see:

| mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|
| a second sheet is uploaded, dropping a vendor run 1 held | V-A | the dropped vendor is gone, not merged |
| a second sheet is uploaded, dropping one address of a vendor it keeps | V-B | the dropped address is gone |
| the store is saved and reloaded between the upload and the lookup | V-E | the folded lookup still resolves — the key came off disk, not out of the call that wrote it |
| the ADNOC export is reloaded between runs | V-D | every contact and address survives |
| a shortlist entry is added on run 2 for a vendor uploaded on run 1 | V-F | the address appears with nothing having rewritten the shortlist |
| a vendor's `approved_by` is edited between runs | V-F | the address is unchanged and still derived |
| a document written on run 1 is loaded on run 2 | V-C | no `vendor_contacts` key, before or after |

Five of PLAN-TEMPLATE's nine required rows have no analogue here and are
**deliberately absent rather than fabricated** — this subsystem calls no model,
so the four rows about LLM failure and prompt-version bumps have nothing to
mutate, and there is no revision lineage for the superseded-document row. The
plan says so where the matrix sits, rather than leaving a reader to wonder.

**Verify the matrix is real.** Before the integration task is done, reinstate
each defect one at a time and confirm the intended row fails and nothing else
does. V-F in particular asserts an **absence**, so it passes the moment it is
written; it is verified the way this repository has verified two absence
assertions before it — by adding `email` to `ShortlistEntry` and watching it go
red.

## 11. What comes next

BD-9 consumes this. `emails_for` is the whole interface it needs on the
recipient side; the sender, the transport and the opt-in are that phase's, along
with the rule that no test, seed or default configuration can reach a real
mailbox.
