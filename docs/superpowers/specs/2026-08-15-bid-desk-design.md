# Bid Desk — design

Design for the eleven requirements in [`feature-request.md`](../../../feature-request.md).
Requirement ids (`BD-n`) are load-bearing: every task in the plan names the
requirement it satisfies and the store invariant it owns.

---

## 1. Where this lands in the existing architecture

Everything here is the `workflow/` package and its routes. Nothing in
`procurement/` moves. That separation is deliberate and stated in `CLAUDE.md`:
the snapshot store has `generation`, id-addressed `field_path` and orphan
pruning; the workflow store has none of those, and keeping them apart stops a
reader assuming one set of rules covers both.

Three stores are in play, and Bid Desk adds a fourth kind of thing to the
picture — bytes.

| store | holds | why |
|---|---|---|
| `<ROOT>/workflow.json` | RFQs, items, shortlists, clarifications, **and every new collection below** | one document, one lock, one atomic write |
| `<ROOT>/bidders.db` | the bidder registry | reference data, ~1 300 rows, queried by attribute |
| `<ROOT>/rfq-docs/` | **new** — enquiry and bid document bytes | files are not records |
| `<ROOT>/mr-index.db` | **new** — retrievable MR passages | derived, disposable, rebuildable |

The rule for choosing: `bidders.db` is for large attribute-queried reference
data. `workflow.json` is for everything else. New collections here are tens of
rows per RFQ, so they go in the document. `mr-index.db` is neither — it is a
**derived index**, disposable in the same sense as `index/store.db`, and it may
be deleted and rebuilt from the stored documents at any time.

---

## 2. BD-1 — the four-source vendor pool

### The category error to avoid

`ADNOC` and `Astra` are **registry approvals**, ANDed, answered by
`GET /bidders/available` out of SQLite. `Manual` and `Suggested` are
**item-scoped curated lists**, held in `workflow.json`'s flat
`item_vendor_lists` array. They are different universes and must not be folded
into one query parameter — a fifth `approver=Manual` would be a lie, because
nobody approved anything.

### The design

The card becomes a **union view assembled in the browser** out of data the
screen already holds.

```
                 registry half                    curated half
        ┌────────────────────────────┐   ┌──────────────────────────┐
chips   │  [ADNOC]  [Astra]          │   │ [Added by hand] [Suggested]│
        │  AND across ticked         │   │ OR — each appends rows   │
semantics  one query, server-side    │   │ filter of a list already │
        │  GET /bidders/available    │   │ on the page              │
        └────────────────────────────┘   └──────────────────────────┘
                        ↓                              ↓
                     ┌──────────────────────────────────┐
                     │  one table, every row source-tagged│
                     └──────────────────────────────────┘
```

- Registry rows come from the existing endpoint, unchanged. If **both** registry
  chips are unticked, the endpoint is **not called at all** — it refuses an
  empty approver list with a 422, and that refusal is correct; the browser
  simply contributes no registry rows.
- Curated rows come from `project.item_vendor_lists[itemId]`, already fetched by
  `ItemDetail`. Filtering them is a `.filter()`, not a request.
- Search (`query`) applies across the union.
- Every row carries a source badge. `PoolRow` is the union type:

```ts
type PoolRow =
  | { kind: 'registry'; bidder: AvailableBidder }
  | { kind: 'curated';  entry: ItemVendorEntry }
```

The discriminant is what keeps the shortlist call correct: a registry row
invites by `vendor_id`, a curated row by `vendor_name` with no id — the two
paths that already exist.

### No deduplication

If *Al Munara Cables* is in the registry **and** somebody typed it by hand, two
rows appear. This is not a defect to be tidied later. `CLAUDE.md` records name
matching as a shipped defect twice, and the curated-row rule is explicit: a
curated row never links to the registry, because a match would silently attach a
real company's approvals to whatever somebody typed. Merging the rows in the
view is that same defect wearing a different hat.

### At least one chip

The existing guard — the last registry chip cannot be unticked — becomes: **the
last chip of any kind** cannot be unticked. Four unticked chips is not a query,
it is an empty screen that looks like "no vendor qualifies".

---

## 3. BD-2 — the item-owned draft shortlist

### Why it is persisted, not carried

The obvious cheap version is React Router navigation state. It is wrong: a
refresh, a mis-click on Back, or the API restarting (which this app does on every
code change, and which clears sessions) loses a selection that may have taken
real work to assemble. The requirement says the draft survives all three.

### Model

New collection in `workflow.json`, following the `item_vendor_lists` pattern
exactly — flat array on disk, grouped by owner in memory.

```python
class DraftShortlistEntry(BaseModel):
    id: str                       # dse_<hex>, from a default_factory
    item_id: str
    vendor_id: str | None         # registry rows only; None for curated
    vendor_name: str
    source: VendorPoolSource      # "ADNOC" | "Astra" | "Manual" | "Suggested"
    added_by: str
    added_at: str
```

`source` records **where the buyer found them**, which is a different fact from
who approved them and is not derivable later — a registry vendor's approvals can
change, and a curated entry can be deleted from the item's list. Recording it at
selection time is the only way to keep it true.

`vendor_name` on a registry row is a **snapshot**, the same rule
`ShortlistEntry` already keeps. Approvals are re-derived on read; identity is
frozen at selection.

### Store methods

```
add_draft_shortlist_entry(item_id, entry)   -> DraftShortlistEntry
remove_draft_shortlist_entry(item_id, id)   -> None      # by id, never name
draft_shortlist(item_id)                    -> list[DraftShortlistEntry]
adopt_draft_shortlist(rfq_id, item_id)      -> int       # BD-6 hand-off
clear_draft_shortlist(item_id)              -> None
```

All five are reads-that-gate-writes or writes, so every one runs inside
`persistence.locked_update` from its route — the same rule as the auth store,
for the fifth time in this repository.

**Duplicate guard.** Adding a vendor already in the draft is a no-op, not a
second row, and the decision is made **inside the lock**. Two concurrent adds of
the same vendor would otherwise both read "not there yet". This is the `grant`
shape from `api/auth/store.py`.

### Cascade

`delete_item` already pops the whole item vendor list; it must pop the draft
too. The invariant: **`workflow.json` holds exactly the draft entries whose item
still exists.** A draft pointing at a deleted item is an orphan that survives a
restart — this is `CLAUDE.md`'s C1 family, and it is the reason the plan makes
someone own it.

### Adoption

`adopt_draft_shortlist(rfq_id, item_id)` copies draft entries into real
`ShortlistEntry` rows when the RFQ is raised (BD-6). It does **not** clear the
draft: the item may be covered by a second RFQ later, and silently emptying a
buyer's basket because one RFQ consumed it is a surprise. Adoption is idempotent
— a vendor already on the RFQ's shortlist is skipped, not duplicated.

Registry entries adopt through the `vendor_id` path, so the shortlist row's
`vendor_name`, `prequal_status` and `scope_code_fit` are derived from the
registry at adoption time and **not** copied from the draft. That is the
existing rule: with a `vendor_id`, `add_shortlist_entry` ignores what the caller
sent.

---

## 4. BD-4 — removing Scoping

### The blast radius

`Stage.SCOPING` appears in: `workflow/stages.py` (enum, `STAGE_ORDER`,
`TRANSITIONS`), `workflow/gates.py` (`_scoping_exit`, `_GATES`),
`workflow/models/rfq.py:32` (the default), `workflow/store.py:482` (seed
history), `web/src/pages/RfqWizard.tsx:41` (`WIZARD_STAGES`),
`web/src/routes.tsx:228` (`WIZARD_RANGE`), and `tests/test_workflow_stages.py`
(which asserts *nine* stages in order).

### The migration

Stored RFQs sitting at `Scoping` cannot be left there — the stage would not
parse. `workflow/persistence.py` gains a load-time migration:

> An RFQ whose stored stage is `Scoping` loads at `Shortlisting`, with a history
> entry appended recording `Scoping → Shortlisting`, `by: "system"`, reason
> `"Scoping was removed from the process."`

Appending, never rewriting: history is append-only, and the second pass through
a stage is a second entry. The migration is **on load and idempotent** — an
already-migrated document has no `Scoping` to find.

`VERSION` in `persistence.py` stays at 1. It has not moved for any previous
additive change, and this is a value migration, not a shape change.

### What happens to the technical package

It survives, relocated. `TechnicalPackage` keeps `revision`, `basis_of_design`,
`frozen_at`, `frozen_by`. What changes is `attachments`: today a list of
`Attachment` register lines carrying no bytes. Under BD-6 the package gains
`documents: list[str]` — ids into the new `RfqDocument` collection.

`Attachment` is **not deleted**: `Addendum.attachments` still uses it, and an
addendum that supersedes a package must be able to describe what it supersedes.
The freeze rule — refuse while any attachment lacks a definite revision — moves
to the new step unchanged.

---

## 5. BD-6 — real documents

### Layout

```
<ROOT>/rfq-docs/<rfq_id>/<sha256[:2]>/<sha256>/<original-filename>
```

Content-addressed under a two-character fan-out. Uploading the same bytes twice
writes once. The original filename is kept as the leaf so a human browsing the
directory sees names, not hashes — and two different files with the same name
cannot collide, because they differ before the leaf.

New module `workflow/doc_store.py`, modelled on `procurement/store/layout.py`:
stdlib-only, path helpers plus an atomic write, no imports from `workflow.*` so
it cannot become circular.

### The blob interface

```python
class BlobStore(Protocol):
    def put(self, rfq_id: str, filename: str, data: BinaryIO) -> BlobRef: ...
    def open(self, ref: BlobRef) -> BinaryIO: ...
    def delete(self, ref: BlobRef) -> None: ...
```

`LocalBlobStore` is the only implementation built. `BlobRef` carries
`sha256`, `size` and the storage-relative path — never an absolute path, so the
same record is valid after the root moves, and so an S3 implementation has
somewhere to put a key.

**S3 is not built.** The interface exists so that adding it is a new class and a
config switch. Building an S3 backend now would mean a boto3 dependency, a
credential path and a CI story for none of which there is a requirement yet.

### The upload route

```
POST /api/workflow/rfqs/{rfq_id}/documents
     files: list[UploadFile]
     paths: list[str] | None      # webkitRelativePath, positional
     category: EligibilityCategory | None
```

- `list[UploadFile]` is what makes multi-file work; the existing three upload
  routes all take a single `file` and none of them is a model for this.
- A `.zip` member is expanded and each entry stored individually, guarded
  against traversal. The guard is **extracted from
  `procurement/project.py:141-143` into a shared helper** rather than copied —
  a second copy of a security check is a second thing to forget to fix.
- A folder upload arrives as many files plus their relative paths. Paths are
  sanitised the same way archive members are.
- Rejected: absolute paths, `..` segments, empty names, and members that would
  land outside the RFQ's directory. Each refusal names the offending entry.

### The record

```python
class RfqDocument(BaseModel):
    id: str                       # rdoc_<hex>
    rfq_id: str
    filename: str                 # leaf name as uploaded
    rel_path: str                 # path within a folder/zip, or just filename
    sha256: str
    size_bytes: int
    content_type: str | None
    category: EligibilityCategory | None
    uploaded_by: str
    uploaded_at: str
    submitted_by_vendor_id: str | None   # None = issued by the contractor
```

`submitted_by_vendor_id` is what makes one collection serve both halves: the
enquiry package the contractor issues (`None`) and the bid documents a vendor
returns (a vendor id). BD-11 reads exactly the same rows.

**Store invariant:** `<ROOT>/rfq-docs/<rfq_id>/` contains exactly the blobs
referenced by live `RfqDocument` records for that RFQ — no orphans. Deleting a
document deletes its blob **only when no other record shares the hash**, because
content addressing means two records legitimately point at one blob.

---

## 6. BD-7 — the eligibility checklist

`workflow/eligibility.py` is **pure** — no store, no I/O, no clock — in the same
way `workflow/bidders.py` is. Routes resolve inputs at the boundary and call in.

```python
class EligibilityCategory(str, Enum):
    TECHNICAL_OFFER      = "Technical offer"                 # a  mandatory
    COMMERCIAL_OFFER     = "Commercial offer"                # b  mandatory
    COMPLIANCE_SHEET     = "Compliance / deviation sheet"    # c  conditional
    CLIENT_DATASHEET     = "Filled client datasheet"         # d
    TBE_SHEET            = "Technical bid evaluation sheet"  # e  mandatory
    TECHNICAL_DATASHEET  = "Technical datasheet"             # f
    DRAWINGS             = "Drawings"                        # g
    DOCUMENTS            = "Documents"                       # h
    CATALOGUES           = "Catalogues and brochures"        # i

MANDATORY = (TECHNICAL_OFFER, COMMERCIAL_OFFER, TBE_SHEET)
```

### Category (c) is a rule, not a flag

> If the issued package contains a `COMPLIANCE_SHEET` document, the bidder must
> return one. If it does not, the bidder must supply a deviation list — which is
> the same category, arriving from the other direction.

Either way the category is required; what differs is **what satisfies it**. So
(c) is mandatory *whenever the enquiry has been issued at all*, and the reason
sentence names which of the two is expected. Encoding it as "optional unless
issued" would let an enquiry that shipped a compliance sheet accept a bid that
ignored it.

### The verdict is computed

```python
def assess(
    issued: Sequence[RfqDocument],
    submitted: Sequence[RfqDocument],
) -> EligibilityVerdict
```

`EligibilityVerdict` carries `admissible: bool`, `missing:
list[EligibilityCategory]`, and `reason: str | None`. Never stored — the same
rule as `Suitability` and `missing_client_approval`, and for the same reason: a
stored verdict is wrong the moment a late document arrives, and BD-11 is
entirely about late documents.

`missing` lists **every** absent mandatory category, not the first found. A
bidder told to send one more thing, who sends it and is then told about a
second, has been made to do two rounds for no reason.

---

## 7. BD-8 — retrieval over the MR

### Why lexical, not embeddings

`CLAUDE.md` requires the test suite to be key-free: no test may need
`ANTHROPIC_API_KEY`, and CI runs with no provider secrets. Embeddings need a
provider — and **Anthropic does not offer an embedding endpoint at all**, so
"use the model we already use" is not available. The alternatives are a second
provider (OpenAI/Gemini/Bedrock, so a key in CI or a skip guard over the whole
feature) or a local sentence-transformers model (a large dependency and a model
download in CI).

BM25 over tokenised passages needs neither. It is pure Python, it is
deterministic, it tests without a key, and for the actual query shape here —
a vendor quoting specification vocabulary back at us — term overlap is a strong
signal.

The interface is what matters:

```python
class PassageIndex(Protocol):
    def add(self, rfq_id: str, passages: Iterable[Passage]) -> None: ...
    def search(self, rfq_id: str, query: str, k: int) -> list[Hit]: ...
```

`Bm25Index` now, `EmbeddingIndex` later, same two methods. Swapping is a config
line.

### The pipeline

```
MR uploaded (BD-6)
   └─► read_text_with_source()          ← existing procurement/loaders.py
         └─► split into passages         ← existing procurement/chunking.py idiom
               └─► Bm25Index.add()       ← <ROOT>/mr-index.db
                                              │
vendor question ──────────────────────────────┤
   └─► Bm25Index.search(k=6)                   │
         └─► LLM: "answer ONLY from these passages, cite the ids you used"
               └─► ┌ cited ids ⊆ retrieved ids ─► draft answer, with citations
                   └ otherwise               ─► escalate to the contractor
```

### Code decides, not the model

The model is asked for an answer **and the passage ids it used**. It is never
asked "should this be escalated?" — that verdict is computed:

```python
supported = bool(cited) and set(cited) <= {h.passage_id for h in hits}
```

An empty citation list, or a hallucinated id, means **not answered** regardless
of how confident the prose sounds. This is the existing house rule applied to
retrieval, and it is the one thing in BD-8 that must not be softened: a
confidently wrong auto-answer sent to a bidder becomes a contractual position.

The prompt is a versioned file, `shared/llm/prompts/mr_answer_v1.txt`, so what
is asked is a reviewable diff. A test reads that file for the words that would
turn it into a verdict request — the same guard
`test_the_model_is_never_asked_for_a_verdict` already applies to vendor
suggestions.

### Question intake

Three shapes, one normaliser producing `IncomingQuery`:

| shape | reader |
|---|---|
| typed into the screen | direct |
| spreadsheet of queries | `openpyxl` via existing `read_xlsx_text` |
| plain email body | the mail transport's parsed body |

A spreadsheet with no recognisable query column is a **refusal that says so**,
never an empty import — the distinction between an outage and "nothing found"
that this repository keeps everywhere.

---

## 8. BD-9 / BD-10 — email

### The finding, stated plainly

**No paid service is required.**

| need | free path | cost |
|---|---|---|
| send | `smtplib` + STARTTLS against any mailbox (Gmail app password, Outlook, a company SMTP host) | none |
| receive | `imaplib` polling that mailbox | none |
| threading | RFC 5322 `Message-ID` / `In-Reply-To` / `References` | none |
| attachments | `email.message.EmailMessage` | none |

Transactional providers (Resend 3 000/month, Brevo 300/day, SendGrid 100/day)
have free tiers that would also serve, and buy deliverability and inbound
webhook parsing. **They are an upgrade path, not a prerequisite.** A paid plan
becomes worth discussing only if volume exceeds a free tier or a dedicated
inbound domain is wanted — flagged here, not decided.

### Transport

```python
class MailTransport(Protocol):
    def send(self, message: OutboundMail) -> SentRef: ...
    def poll(self) -> list[InboundMail]: ...
```

- `OutboxTransport` — **the default.** Writes `.eml` files to
  `<ROOT>/outbox/`. Sends nothing. Every test and every demo uses it.
- `SmtpImapTransport` — real mail, and it is only constructed when
  `MAIL_TRANSPORT=smtp` is explicitly set. Absent, empty, `0` or `false` leaves
  the outbox in place, exactly as `AUTH_DISABLED` is read.

The safety property: **no test, no seed and no default configuration can send
mail to a real address.** Reaching a real mailbox takes a deliberate
environment change, the same shape as the auth bypass.

Threading: each `ClarificationQuery` owns a `thread_id`, and every message
carries `In-Reply-To` so a vendor's reply lands against the right query rather
than in a general inbox.

---

## 9. BD-11 — continuous adequacy

There is no daemon and no sweep job. Status is a **derived read**, exactly like
`Suitability`, `missing_client_approval` and `client_approved` before it:

```
GET /api/workflow/rfqs/{rfq_id}/vendor-list
   → for each shortlisted bidder:
        submitted = [d for d in documents if d.submitted_by_vendor_id == b.id]
        verdict   = eligibility.assess(issued, submitted)
```

"Stays alive" is satisfied by recomputation-on-read, not by a process. A
document uploaded at 02:00 changes the answer at 02:00 with nothing scheduled,
and there is no stored status to go stale. The absence of a sweep job is the
same argument `PrequalStatus` already makes about expiry.

**The column is labelled `Vendor List`.** A test asserts the literal string,
because it is a client requirement and "Bidders" or "Submissions" would read
better to a developer — which is exactly how it would get renamed.

---

## 10. Testing strategy

Per `docs/superpowers/PLAN-TEMPLATE.md`, whose three rules exist because phase 2
shipped seven defects that survived per-task TDD:

1. **Every task names the store invariant it owns** — a sentence about stored
   state, saying "exactly", assertable in one assertion over a loaded document.
2. **The integration task carries a two-run mutation matrix.** The defects that
   matter here need two runs to see: a draft adopted twice, a document uploaded
   twice, a bidder who completes their submission after being judged
   inadmissible, an MR re-issued at a new revision leaving stale passages
   indexed.
3. **Reference code is intent, not paste-able.**

Both suites must stay green at their documented baselines: `1694 passed, 3
skipped` on a workstation, and the web suite as it stands. Every new Python test
must run without a provider key.

## 11. Sequencing

BD-1 → BD-2 → BD-3 → BD-4 → BD-5 → BD-6 → BD-7 → BD-8 → BD-9 → BD-10 → BD-11.

The order is not arbitrary. BD-2 needs BD-1's pool to select from. BD-5 cannot
lose the Registry section until BD-2 gives the buyer somewhere else to choose
vendors. BD-7 needs BD-6's documents to check for. BD-11 is BD-7 read over BD-6
rows, so it is last and small — most of its work is done by the time it starts.

BD-8 is the largest single item and the one most likely to be reached with the
least time. It is sequenced after everything that has a visible screen, so that
what lands is a working desk rather than a half-built retriever.
