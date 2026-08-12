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

> **Scope: this section models LLM running cost only.** Hosting, storage,
> support, onboarding and amortised development are deliberately deferred to a
> later revision. That is a sequencing choice, not a claim that model spend
> dominates — it does not. At Base volume, model spend is roughly **1% of
> contract value**, and it only becomes the largest single cost line above
> ~10,800 MB/year (≈240 tenders, about one per working day), which is where it
> would overtake a single ₹96,000/yr support line. Treat §2 as the first row of
> a cost model, not the whole of it, and do not set a price from it alone.

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

### 2.2 Call structure — where the tokens actually go

Read out of `chunking.py`, `extract_tech.py`, `extract_requirements.py` and
`extract.py` rather than assumed:

| path | budget | behaviour |
|---|---|---|
| `chunk_on_lines` | — | **No overlap, by design** — "overlap duplicates the records the chunk carries". No document text is ever sent twice. |
| `TECH_CHUNK_CHARS` | 12,000 | 28 documents, ~39 chunks |
| `REQUIREMENTS_CHUNK_CHARS` | 8,000 | 1 document, 4 chunks |
| `extract.py:25` | unchunked | quotations sent whole |
| classification | — | 3 of 37 documents; the other 34 resolve by rule, free |

That is roughly **55 model calls per tender.** The only duplicated input is the
prompt itself, re-sent once per call at ~400 tokens — about **+22,000 tokens,
an 11% overhead** on top of the document text.

Input is therefore ≈ 200,250 (text) + 22,000 (prompt re-sends) ≈ **222,000
tokens**.

### 2.3 Model spend per tender

Output is given as a range. The low end is what the model actually emits —
`facts.json` (222,335 B) + `requirements.json` (70,912 B) ≈ **73,312 tokens**.
The high end (**139,780 tokens**) adds `compliance.json` and `documents.json`,
which are derived in Python and never come off the wire; it is carried as a
safety margin only.

| model | $ / tender | ₹ / MB (@ ₹88) | ₹ / yr @ Base (1,080 MB) |
|---|---|---|---|
| Haiku 4.5 ($1 / $5) | $0.59 – 0.92 | ₹1.16 – 1.82 | ₹1,253 – 1,966 |
| Sonnet 5, intro (expires 2026-08-31) | $1.18 – 1.84 | ₹2.32 – 3.63 | ₹2,506 – 3,920 |
| Sonnet 5, standard ($3 / $15) | $1.77 – 2.76 | ₹3.48 – 5.45 | ₹3,758 – 5,886 |
| **Opus 5 ($5 / $25)** | **$2.94 – 4.61** | **₹5.81 – 9.09** | **₹6,275 – 9,817** |

**Marginal cost of goods is ₹1.2–9.1 per billable MB depending on model, and
₹5.8–9.1 on Opus 5.** Every price in §3 is set against the value delivered, not
against this number — but this is what proves the margin holds under any
realistic input.

### 2.4 Cost levers — and why there is only one

The usual optimisations are unavailable here. This was checked, not assumed:

- **Prompt caching does not apply.** Opus 5's minimum cacheable prefix is 512
  tokens. The prompt library spans 194–1,292 tokens, and only
  `requirements_v4` (1,292 tok, 4 calls per tender) clears the bar. Best case
  saves ~5,000 tokens per tender — immaterial.
- **No chunk overlap exists to remove.** Already eliminated by design (§2.2).
- **Input is document text**, unique per document. Nothing to deduplicate
  beyond the `content_sha256` skip already in `pipeline.py` (§4.2).

Model choice is therefore the entire lever, and it spans **₹1,253 → ₹9,817 per
year** — a total range of ₹8,564, or **1.6% of the ₹5,40,000 credit line** at
Base volume.

> **Never trade extraction accuracy for model cost.** Moving Opus 5 → Haiku 4.5
> saves ~₹8,500 a year. One missed deviation on one tender costs the client more
> than that, and costs you the account. The store invariants in `CLAUDE.md`
> exist because wrong extractions corrupt award decisions; they must not be
> undermined to save a rounding error.

### 2.5 Worst-case input

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
| Sonnet 5 intro pricing ends 2026-08-31 | low | Only bites if running Sonnet 5: COGS rises ~50%, ₹3,920 → ₹5,886/yr at Base. Margin stays ~98%. No price change needed. |
| Pressure to downgrade the model to save cost | medium | §2.4. The entire Opus 5 → Haiku 4.5 saving is ₹8,500/yr. Refuse it; the accuracy risk is not priced in the client's favour. |
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

**Value anchor over cost-plus.** Marginal cost is ₹5.8–9.1 per MB on Opus 5.
Cost-plus at a 10× multiple would cap a tender near ₹4,000 against ₹1,00,000 of
displaced labour, and pure-LLM breakeven sits at ~₹9/MB against a ₹500/MB price
— **55× headroom**. The pricing is deliberately not cost-related.

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
