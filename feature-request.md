# Bid Desk

**One desk that carries an enquiry from *which vendors* to *which bids are even
admissible*.**

Today the platform can pick vendors and it can compare bids. Between those two
things sits the part a buyer actually spends their week on: getting an enquiry
out to a shortlist, answering what the shortlist asks, and deciding who sent
enough to be considered at all. Bid Desk is that middle.

This document is the **requirements register** — what must be true when the work
is done. The design lives in
[`docs/superpowers/specs/2026-08-15-bid-desk-design.md`](docs/superpowers/specs/2026-08-15-bid-desk-design.md),
the task breakdown in
[`docs/superpowers/plans/2026-08-15-bid-desk-plan.md`](docs/superpowers/plans/2026-08-15-bid-desk-plan.md),
and the running ledger in [`.superpowers/sdd/bid-desk/progress.md`](.superpowers/sdd/bid-desk/progress.md).

Requirements are numbered `BD-n`. They are ordered as they will be built, and
each is finished — implemented, tested, and verified in a browser where it is
visible — before the next is started.

---

## The shape of the change

The enquiry today runs through a five-step wizard whose first step, *Scoping*,
duplicates work the item screen already did, and whose *Shortlisting* step
re-browses the whole bidder registry the buyer just finished filtering. Bid Desk
removes both duplications and adds the three things that were missing: real
documents, a rule for what a bid must contain, and a conversation with the
vendor that does not start from zero every time.

| | before | after |
|---|---|---|
| Vendor pool | registry only, two approval filters | registry **and** curated lists, four filters |
| Shortlist | assembled inside the RFQ, after it exists | assembled against the **item**, before the RFQ exists |
| First wizard step | Scoping | Shortlisting |
| RFQ documents | register lines with no bytes | real files — PDF, XLSX, folders, ZIPs |
| Bid admissibility | not modelled | a nine-category checklist with three must-haves |
| Clarifications | typed in by hand, both sides | retrieved from the MR first, escalated only if genuinely absent |
| Vendor contact | none | email, in a thread |

---

## BD-1 — The vendor pool is all four sources, filtered by four chips

**Requirement.** The *Available vendors* card on the item screen shows vendors
from all four sources the platform holds, and the buyer chooses which sources
count with toggle chips.

- Four chips: `ADNOC`, `Astra`, `Added by hand`, `Suggested`. The first two are
  registry approvals and already exist; the last two are new.
- The two registry chips keep their current meaning exactly — **AND**, not OR.
  One approval is the common case and precisely what is being excluded (1 346
  vendors on the client's list against 111 on both).
- The two curated chips are **additive**. Ticking `Added by hand` appends this
  item's hand-added vendors to the table; unticking removes them. They are not
  ANDed with anything — a hand-added company has no registry row to approve it.
- Every row states which source it came from. A company appearing both in the
  registry and on a curated list appears **twice**, once per source.
  Deduplicating by name is forbidden: this repository has recorded name matching
  as a shipped defect twice, and silently merging a real approved vendor with a
  hand-typed string is exactly that defect.
- At least one chip must stay ticked. All four unticked is not a query.

**Done when:** a buyer can untick both registry chips, tick `Suggested`, and see
only the model's suggestions; and the ADNOC/Astra intersection is unchanged from
today when the two curated chips are off.

## BD-2 — A shortlist is assembled against the item, before any RFQ exists

**Requirement.** Ticking vendors in the pool and choosing *Shortlist selected*
builds a **draft shortlist owned by the item**, not by an RFQ.

- The draft survives a page refresh, a sign-out and a server restart. A
  twenty-five vendor selection lost to a reload is the failure this requirement
  exists to prevent.
- A vendor can be added from any of the four sources. Registry vendors carry
  their `vendor_id`; curated vendors carry a name and no id, exactly as a
  hand-typed shortlist entry does today.
- Removing a vendor from the draft is by **id**, never by name or position.
- *Shortlist selected* leads to the shortlisting screen, which shows the draft.
  It does **not** create an RFQ.

**Done when:** a buyer selects vendors on the item screen, closes the browser,
returns, and finds the same draft waiting.

## BD-3 — The `RFQs covering this item` card is removed from the item screen

**Requirement.** That card and its `Raise RFQ` button come off the item screen.
Raising an RFQ happens on the RFQ screen, after the shortlist exists.

The item screen keeps a plain, non-editing statement of which RFQs already cover
the item, so the fact is not lost — but no form, and no way in.

## BD-4 — Scoping is removed from the enquiry

**Requirement.** `Stage.SCOPING` no longer exists. An RFQ begins at
`Shortlisting`.

- The stage machine drops from nine stages to eight.
- Any RFQ already stored at `Scoping` moves to `Shortlisting` on load, with an
  appended history entry recording the move. History is append-only; nothing is
  rewritten.
- The technical-package editor does not vanish — it moves into BD-6, where it
  becomes a set of real uploaded documents rather than a table of register
  lines.

## BD-5 — The shortlisting step is reworked

**Requirement.** The shortlisting step shows the shortlist and the controls that
act on it, and nothing else.

- The **Registry** section is removed. The buyer has already chosen their
  vendors on the item screen; re-browsing 1 346 rows here is the redundancy this
  requirement removes.
- **`Approve shortlist` moves to the top of the step**, above the vendor table,
  where the person approving can see it without scrolling past a candidate list
  that no longer exists.
- The step's own status — approved / not approved, and the warning that editing
  the vendors re-opens approval — sits with the button.
- Adding a one-off vendor by hand stays, behind its disclosure. It is the escape
  hatch for a vendor nobody has registered anywhere.

## BD-6 — Raising the RFQ means uploading real documents

**Requirement.** The step after shortlisting is *Raise RFQ*, and it accepts the
enquiry documents as files.

- Accepts **PDF, XLSX/XLS, DOCX, images, ZIP archives, and whole folders**, and
  any combination of them in one action. Multi-select is required, not optional.
- A ZIP is expanded and its members stored individually. Archive paths are
  guarded against traversal.
- A folder upload preserves its relative paths.
- Bytes are stored on disk under a per-RFQ directory, content-addressed so the
  same document uploaded twice does not become two copies.
- The storage call sits behind one interface with a local-filesystem
  implementation. **S3 is the intended production backing and is explicitly not
  built now** — the interface is what makes that a later swap rather than a
  rewrite.
- Every stored document records who uploaded it, when, its size, and its
  checksum.

## BD-7 — Every RFQ carries an eligibility checklist

**Requirement.** Nine categories, three of them mandatory. A bid missing a
mandatory category is refused.

| # | Category | Mandatory |
|---|---|---|
| a | Technical offer | **yes** |
| b | Commercial offer | **yes** |
| c | Compliance to MR / specification, or a deviation sheet | conditional — see below |
| d | Filled client datasheet | no |
| e | Technical bid evaluation sheet, filled in | **yes** |
| f | Technical datasheet | no |
| g | Drawings | no |
| h | Documents | no |
| i | Catalogues and brochures | no |

- **Category (c) is conditional.** If the enquiry package issued to bidders
  contains a compliance or no-deviation sheet, the bidder must return that sheet
  filled in. If it does not, the bidder must supply a deviation list — or state
  that they have no deviations. One of the two is always required; which one
  depends on what was issued.
- The checklist is attached to **every** RFQ, not opted into.
- A bid missing any mandatory category is **rejected outright**, and the refusal
  names every category that is missing — not the first one found.
- The verdict is **computed at read time from the documents present**, never
  stored. A stored verdict is wrong the moment a late document arrives.

## BD-8 — Clarifications answer themselves from the MR where they can

**Requirement.** A vendor's question is answered from the Material Requisition
if the answer is in it, and escalated to the contractor only if it genuinely is
not.

- The MR and any supporting documents issued with the RFQ are **ingested into a
  retrievable index** when the RFQ is issued.
- An incoming question is matched against that index. If a passage answers it,
  the draft answer is produced **with a citation to the passage it came from**.
- **Code decides, not the model.** An answer is only offered automatically when
  the model cites passages that are actually in the retrieved set. An
  uncited answer is treated as no answer and escalated. This is the existing
  house rule — the model reads, code decides — applied to retrieval.
- Anything not answered from the index goes to the contractor, who can type an
  answer, attach documents, or both.
- Question and answer live in one **thread** per query, visible to both sides.
- Questions arrive in more than one shape and all are accepted: a filled
  spreadsheet of queries, a plain email body, or typed directly into the screen.

## BD-9 — Contractor and vendor correspond by email

**Requirement.** The thread in BD-8 is carried over email.

- Outbound and inbound mail sit behind one transport interface.
- **No paid service is required.** The finding is recorded in the design: SMTP
  for sending and IMAP for receiving work against any ordinary mailbox at no
  cost, and the free tiers of the transactional providers are an upgrade path
  rather than a prerequisite. A paid plan becomes worth discussing only at
  volumes or deliverability requirements this does not yet have — that decision
  is flagged, not taken.
- Addresses are configuration. Mock addresses now; real ones by changing config,
  not code.
- Nothing is sent to a real address without an explicit, configured opt-in. The
  default transport writes to a local outbox.

## BD-10 — A worked mock exchange ships with it

**Requirement.** Two mock parties — one contractor, one vendor — with a seeded
correspondence, so the thread can be seen working without wiring a mailbox.

Mock entities are invented and say so. Per the existing rule, an invented vendor
may carry Astra's approval but **never the client's**: a fabricated company shown
as approved by ADNOC is indistinguishable on screen from a real one.

## BD-11 — Bid adequacy is continuous, and the column is called *Vendor List*

**Requirement.** As documents arrive from bidders — over days, with no deadline
— each bidder's status is recomputed and shown.

- Documents are **not** read, parsed or interpreted. The only question is
  whether the required categories are present.
- Status moves from *not enough information provided* to *all necessary
  information provided* as submissions complete, and back if a document is
  withdrawn.
- Recomputation happens on every submission. There is no sweep job and no stored
  verdict to go stale.
- **The column is labelled `Vendor List`.** This is a direct client requirement
  and is not to be renamed to something that reads better.

---

## Decisions taken without asking

These were judgement calls made to keep moving. Each is argued in the design.

1. **No name-based deduplication anywhere**, including between a registry vendor
   and an identically-named hand-added one. Both rows show, each labelled.
2. **Retrieval is lexical (BM25) before it is semantic.** It needs no embedding
   provider, so the test suite stays key-free — which CI requires. An embedding
   backend is an interface swap once a provider is chosen.
3. **The draft shortlist is persisted server-side**, not carried in browser
   navigation state. Losing a selection to a refresh is not acceptable.
4. **Email defaults to a local outbox.** Sending real mail is opt-in
   configuration, so no test and no demo can post to a real person.
5. **Scoping's technical package is not deleted, it is relocated** into BD-6 and
   backed by real files.

## What is deliberately not in scope

- Reading, parsing or extracting from bid documents. BD-11 checks presence only;
  the existing `procurement/` pipeline is what reads documents, and wiring the
  two together is its own phase.
- S3. The interface is built; the S3 implementation is not.
- Inbound email on a dedicated domain with webhook parsing. IMAP polling covers
  the requirement without a provider contract.
