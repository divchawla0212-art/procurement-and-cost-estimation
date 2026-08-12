# Usage-Based Pricing & Metering — Design

Date: 2026-08-12
Status: draft, pending review

## 1. What this is

A commercial model and the metering machinery to support it, so the procurement
pipeline can be sold to an external client on a **platform fee + prepaid MB
credits** basis.

Four decisions were taken up front and are treated as settled here:

| Decision | Choice |
|---|---|
| Metering unit | **Raw file megabytes ingested** |
| Revenue shape | **Annual platform fee (10 named users) + prepaid MB credit packs** |
| Price anchor | **The analyst labour the pipeline replaces** |
| Currency | **INR** (find-and-replace if the client contracts in AED/USD) |

Two were contested during design and reaffirmed; the reasoning is recorded in
§8 so it does not get relitigated later.

## 2. Measured cost basis

Everything below is measured on `projects/phase4c-shipped-defaults`, the
shipped-defaults reference corpus: **4 vendors, 37 documents, 44.6 MB, 801,022
characters of extracted text.**

### 2.1 Input volume by document class

| class | files | MB | extracted chars | chars per MB |
|---|---|---|---|---|
| drawing | 7 | 18.5 | 45,666 | 2,471 |
| other | 12 | 9.0 | 113,456 | 12,637 |
| quotation | 5 | 7.1 | 242,899 | 34,402 |
| datasheet | 5 | 5.3 | 212,272 | 40,251 |
| spec | 3 | 2.1 | 113,869 | 55,143 |
| deviation | 2 | 1.5 | 6,297 | 4,149 |
| mom | 2 | 1.2 | 65,054 | 54,271 |
| bom | 1 | 0.04 | 1,509 | 65,153 |
| **TOTAL** | **37** | **44.6** | **801,022** | **17,962** |

The 22× spread between `drawing` (2,471 chars/MB) and `bom` (65,153 chars/MB)
is the central weakness of MB metering and is managed in §4.4, not designed
away.

### 2.2 Model spend per tender

Input ≈ 801,022 chars ÷ 4 ≈ **200,000 input tokens**. Output taken as the full
stored corpus (559,120 B ≈ **139,780 tokens**) — deliberately conservative,
since `compliance.json` (236,035 B of it) is derived in Python and never comes
off the wire.

| model | $ / tender | $ / MB | ₹ / MB (@ ₹88) |
|---|---|---|---|
| Sonnet 5, intro rate (expires 2026-08-31) | $1.80 | $0.040 | ₹3.55 |
| Sonnet 5, standard ($3 / $15) | $2.70 | $0.060 | ₹5.33 |
| Opus 5 ($5 / $25) | $4.50 | $0.101 | ₹8.87 |

**Marginal cost of goods is ₹3.5–9 per billable MB.** Every price in §3 is set
against the value delivered, not this number — but this number is what proves
the margin holds under any realistic input.

### 2.3 Worst-case input

The margin risk under MB billing is text-dense, small-byte files, not large
ones. The reference requirements sheet is 276 KB → 25,176 chars = **91,217
chars/MB**, ~5× the corpus average. A pathological 100 MB all-spreadsheet
upload yields ~9.1M chars ≈ 2.3M input tokens ≈ **$12–35 of model spend**
against ₹40,000+ of billed credits. There is no realistic input that inverts
the margin.

Large scanned drawings run the other way — 18.5 MB billed for 45,666 chars of
work — and are pure margin.

## 3. Commercial model

### 3.1 Structure

```
Platform fee    ₹3,00,000 / year   10 named users, hosting, support,
                                   updates, model API account
      +
MB credits      bought in packs, drawn down at ingestion
                1 credit = 1 billable MB
```

Everything that does not call the model is **free and unmetered**: reviewing
extractions, the compliance matrix, the comparative statement, overrides,
feedback, and both XLSX/CSV export routes. These are pure reads of the stored
snapshot (`api/main.py` — every `GET`, plus `PUT .../feedback` and
`PUT .../fx-rates`).

### 3.2 Credit packs

| pack | credits (MB) | ₹ / MB | pack price |
|---|---|---|---|
| Pilot | 150 | — | ₹75,000 (credited against first annual contract) |
| Starter | 250 | ₹600 | ₹1,50,000 |
| Standard | 750 | ₹500 | ₹3,75,000 |
| Scale | 2,000 | ₹400 | ₹8,00,000 |
| Enterprise | 5,000+ | ₹325 | negotiated |

The **rate tier is set by annual committed volume, not by a single purchase**,
and packs stack — a client who commits to 1,080 MB for the year buys at the
Standard rate throughout, drawing down cumulatively, rather than buying a
750 MB pack and a second one at a worse rate. Mid-year expansion re-rates
forward, never retroactively.

Credits are valid **12 months** from purchase and roll over once if a renewal
is signed before expiry. Unused credits are not refundable in cash.

### 3.3 The anchor, stated as the pitch

> A four-vendor tender pack takes your engineer roughly four days to turn into
> a defensible comparative statement — call it ₹1,00,000 of loaded cost, and it
> is the fourth day where the deviations get missed. This pack is 44.6 MB. At
> the Standard rate that is ₹22,300, and it lands in under an hour with every
> number traced back to the page it came from.

That is **~22% of the labour it displaces**, which is the band that survives a
procurement committee.

### 3.4 Volume scenarios

Assuming a 45 MB average tender pack:

| scenario | tenders / yr | MB / yr | credits | + platform | total / yr | replaced labour |
|---|---|---|---|---|---|---|
| Light | 12 | 540 | ₹2,70,000 | ₹3,00,000 | **₹5,70,000** | ~48 days ≈ ₹12,00,000 |
| Base | 24 | 1,080 | ₹5,40,000 | ₹3,00,000 | **₹8,40,000** | ~96 days ≈ ₹24,00,000 |
| Heavy | 48 | 2,160 | ₹8,64,000 | ₹3,00,000 | **₹11,64,000** | ~192 days ≈ ₹48,00,000 |

Gross margin on the credit line is **~98%** at every tier. The platform fee is
sized at roughly 35–55% of expected consumption so that a quiet quarter does
not zero out revenue.

## 4. Metering rules — the billing contract

These rules are the product. They must be stated in the contract verbatim and
implemented exactly, because every one of them is a dispute the client will
eventually raise.

### 4.1 What is billable

A **billable MB** is one megabyte of the raw bytes of a file that the pipeline
accepted, classified, and successfully extracted, in a given ingestion run.

### 4.2 Never billed

- **Byte-identical re-ingestion.** `pipeline.py` already skips re-extraction
  when `prior.content_sha256 == doc.content_sha256` (lines 303, 403, 520, 770,
  780). If the pipeline skips the work, the meter skips the debit. The client
  never pays twice for the same bytes.
- **Failed extraction.** Any document ending with
  `extraction_status != "ok"` is credited back in full. The client pays for
  facts, not for attempts.
- **Superseded / reclassified documents** already in the store — no new bytes,
  no new charge.
- **Any read path.** Review, matrix, statement, overrides, exports, re-renders.
- **Re-running a run that failed as a whole.** Idempotent on `run_id`.

### 4.3 Rounding

Billable MB is summed across the run in bytes, then rounded **up to the nearest
whole MB** once, at run level — never per document. Per-document rounding on a
37-document pack would silently add up to ~18 MB of phantom volume.

### 4.4 Floor and ceiling — the fix for the 22× spread

The raw-MB unit makes revenue track file weight rather than analyst effort. A
lean 15 MB tender is the same work as a drawing-heavy 90 MB one. Rather than
change the unit, bound it:

- **Minimum 20 billable MB per ingestion run.**
- **Maximum 120 billable MB per ingestion run.**

Both are stated on the price list as *"billed in the range 20–120 MB per tender
ingestion"*. This keeps `₹X per MB` as the headline the client asked for, while bounding
per-tender revenue to a fixed **6:1 range (₹10,000–₹60,000 at the Standard
rate)** regardless of how the client's vendors happen to compress their PDFs.
Unbounded, the same pack of work can vary by far more than that — the
reference corpus alone spans 0.04 MB to 18.5 MB across documents doing
comparable work. The ceiling also caps the client's exposure on a
drawing-heavy pack, which is the objection that would otherwise surface in
month two.

### 4.5 Quote before spend

`POST /api/projects/{slug}/vendors` and `.../requirements` receive the upload
and can compute exact billable MB **before any model call is made**. The client
is shown the quote and the resulting balance, and must have sufficient credits
before `POST .../ingest` will start.

## 5. Architecture

### 5.1 New module: `billing/`

Billing is **tenant-level, not project-level**. It must not live in
`projects/<slug>/store/`, which is the authoritative extraction snapshot and is
governed by invariants that have nothing to do with money.

```
billing/
  ledger.py      append-only entry log; balance is derived, never stored
  meter.py       bytes -> billable MB, applying §4.2-4.4
  quote.py       pre-ingestion estimate from an uploaded archive
  models.py      LedgerEntry, Account, CreditPack, Quote
  store.py       the only writer; mirrors snapshots.py discipline
```

### 5.2 The ledger

Append-only, one file per account, JSONL. Balance is the fold of the log — it
is never written down, so it cannot drift.

```python
@dataclass(frozen=True)
class LedgerEntry:
    at: str              # ISO-8601 UTC
    account_id: str
    kind: str            # purchase | reserve | commit | release | refund | expire
    megabytes: int       # signed; +credit, -debit
    run_id: str | None   # idempotency key, from store/events.jsonl
    project_slug: str | None
    doc_id: str | None   # set on per-document refunds
    note: str            # why, in words, for the invoice line
```

Three-phase debit, so a crashed run never silently eats credits:

1. `reserve` at the start of `/ingest` for the full quoted MB.
2. `commit` on success, for the MB actually extracted `ok`.
3. `release` for the difference, and `refund` per document for anything that
   came back `extraction_status != "ok"`.

Every entry carries `run_id`. Replaying a run is a no-op.

### 5.3 API changes

| endpoint | change |
|---|---|
| `POST /api/projects/{slug}/vendors` | returns `{billable_mb, quote_inr, balance_after}` |
| `POST /api/projects/{slug}/requirements` | same |
| `POST /api/projects/{slug}/ingest` | **402 Payment Required** if balance < quote; reserves before starting |
| `GET /api/account` | new — balance, ledger, expiry, current pack |
| `GET /api/account/usage` | new — per-project, per-run consumption |

### 5.4 Margin telemetry — non-negotiable

`shared/llm/interface.py` currently returns a bare `dict` from
`classify_structure` and discards `usage` entirely. Every provider client in
`shared/llm/` throws away the token counts the API already returns.

Billing does **not** depend on tokens — but margin does. Without this you are
selling a fixed price against an unmeasured cost, and the first prompt change
or model swap moves your COGS invisibly. `classify_structure` must return
usage alongside the parsed structure, and the run must record aggregate input /
output / cache-read tokens.

This is internal telemetry. It is never shown to the client and never affects a
debit.

## 6. Invariants this design must respect

From `CLAUDE.md`, and each one has a specific consequence here:

- **Every store write goes through `snapshots.py`.** Billing therefore gets its
  own writer in `billing/store.py` and does not touch the project snapshot at
  all. No billing field is ever added to `DocumentRecord`.
- **Missing data is never coerced to a passing or zero value.** A meter read
  that cannot determine file size raises; it does not debit 0 and it does not
  debit a guess.
- **Arithmetic stays in Python.** All billing arithmetic is Python. The model is
  never asked anything about money, volume, or entitlement.
- **A failed extraction never blanks previously-good data, and records why in
  `DocumentRecord.notes`.** The refund path in §4.2 keys off exactly that
  status field, so the two stay consistent by construction.
- **Tests stay key-free.** All billing tests run against `mock_client.py`; no
  billing test may require `ANTHROPIC_API_KEY`.

## 7. Risks

| risk | severity | mitigation |
|---|---|---|
| Sonnet 5 intro pricing ends 2026-08-31 | low | COGS rises $1.80 → $2.70/tender; margin stays ~98%. No price change needed. |
| Client disputes MB on a drawing-heavy pack | **high** | §4.4 ceiling caps their exposure; quote shown pre-spend (§4.5). |
| Client re-uploads to game the meter | low | `content_sha256` dedup makes re-upload free, so there is nothing to game. |
| Model swap silently erodes margin | **high** | §5.4 telemetry. This is why it is non-negotiable. |
| Prepaid credits are deferred revenue | medium | Recognise on `commit`, not on `purchase`. Flag to whoever books it. |
| No auth exists on any endpoint today | **high** | An account cannot be metered before it can be identified. Prerequisite work, not part of this spec. |

## 8. Decisions revisited during design, and reaffirmed

Recorded so they are not reopened without new information.

**Raw MB over page count or extracted characters.** Page count is the industry
unit (Textract, Document AI, LlamaParse) and would have removed the 22× spread;
extracted characters would have tracked cost almost exactly. Raw MB was chosen
for client-verifiability — the client can read the number off their own file
browser before they upload, which neither alternative allows. §4.4 bounds the
resulting variance.

**Value anchor over cost-plus.** Marginal cost is ₹3.5–9 per MB. Cost-plus at a
10× multiple would cap a tender near ₹2,500 against ₹1,00,000 of displaced
labour. The pricing is deliberately not cost-related.

## 9. Out of scope

- Authentication and tenant identity (prerequisite — see §7).
- Payment gateway / invoicing integration. The ledger is the source of truth;
  how money arrives is a separate concern.
- Share-of-savings pricing. This is the upgrade path once two years of outcome
  data exist, and it needs the client to open contract values.
- Per-seat metering. The platform fee covers 10 named users flat; seat expansion
  pricing is a commercial conversation, not a metering feature.
- Multi-currency. INR only; the ledger stores MB, and price-per-MB lives in the
  pack definition, so currency is a presentation concern when it arrives.
