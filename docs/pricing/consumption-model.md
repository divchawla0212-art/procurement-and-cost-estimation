# Consumption model — megabytes to credits to dollars

How much a customer consumes, what they pay, and what it costs us — for any
weekly document volume. Worked at **500 MB/week** throughout.

Every constant is marked **measured** or **estimated**. The measured ones come
from replaying a real tender through the production code paths; see
[`README.md`](README.md) for how to re-run them.

---

## The answer

500 MB/week of source documents, priced at the Growth band ($0.105/credit):

| Routing | Credits/week | Credits/year | Customer $/week | Customer $/year | AWS $/week | AWS $/year |
|---|---|---|---|---|---|---|
| **Sonnet 5 only** | **1,070** | 55,500 | **$112** | **$5,830** | **$18** | **$960** |
| 25% of tokens on Opus | 1,245 | 64,700 | $131 | $6,790 | $18 | $960 |
| 50% of tokens on Opus | 1,422 | 74,000 | $149 | $7,770 | $18 | $960 |
| All Opus 5 | 1,779 | 92,500 | $187 | $9,710 | $18 | $960 |

**Rule of thumb: 2.1 credits per MB.** At the Growth band that is about
**22 cents per MB**.

AWS does not move across the rows. Compute is dominated by being up 24/7, not
by what is processed, so the routing choice changes the Claude line only — and
at this volume the box is 96% idle. See **Scaling** below before adding capacity.

### Price per MB by band

| Band | $/credit | $/MB | 500 MB/week |
|---|---|---|---|
| Pay-as-you-go top-up | $0.150 | $0.32 | $160/wk · $8,320/yr |
| Starter bundle | $0.125 | $0.26 | $133/wk · $6,940/yr |
| **Growth bundle** | **$0.105** | **$0.22** | **$112/wk · $5,830/yr** |
| Scale bundle | $0.090 | $0.19 | $96/wk · $4,990/yr |

### Which plan does 500 MB/week fit

| Plan | Allowance | Covers | Verdict at 500 MB/wk |
|---|---|---|---|
| Starter | 24,000 cr/yr (460/wk) | ~215 MB/week | exhausted in ~5 months |
| **Growth** | 72,000 cr/yr (1,385/wk) | ~650 MB/week | **fits, ~25% headroom** |
| Scale | 180,000 cr/yr (3,460/wk) | ~1.6 GB/week | over-provisioned |

Routing half the tokens to Opus takes this account to 1,422 credits/week and
all but exhausts Growth. **Opus routing is what pushes this account to Scale,
not volume.**

---

## How it was calculated

Six steps, source bytes to credits. Substitute your own `MB_PER_WEEK` at
step 1 and the rest follows.

### Step 1 — source megabytes to extracted text

Not every megabyte of PDF is text. Born-digital PDFs in the reference corpus
yielded 818,229 characters from 44.3 MB.

```
CHARS_PER_MB = 818,229 / 44.3 = 18,470          # measured
text_chars   = MB_PER_WEEK × CHARS_PER_MB
             = 500 × 18,470 = 9,235,000
```

> **This is the assumption most likely to be wrong for a new customer.**
> Scanned documents yield almost no extractable text and cost near nothing;
> spreadsheets and plain text can run several times higher. Treat 18,470 as a
> planning rate, not a contractual one.

### Step 2 — extracted text to characters actually sent

More is sent than is read: each chunk re-sends its prompt, and quotations take
a second pass through the technical extractor.

```
INPUT_RATIO = 1,197,552 / 818,229 = 1.4636      # measured
input_chars = text_chars × INPUT_RATIO
            = 9,235,000 × 1.4636 = 13,516,300
```

### Step 3 — characters to tokens

Calibrated with `count_tokens` against synthetic text reproducing the corpus's
exact layout geometry — **62% of the extracted text is whitespace padding**
from `pdftotext` preserving column layout, which tokenises very differently
from prose. A generic 4-chars-per-token rule is wrong here by enough to change
the answer.

```
CPT_IN  = 3.41                                   # measured
CPT_OUT = 2.06                                   # measured
input_tokens = 13,516,300 / 3.41 = 3,963,700
```

### Step 4 — output tokens

Output is taken from what the run actually stored. `ask_each_chunk` uses
`items.extend` with no de-duplication, so stored output is close to what the
model emitted rather than a compressed summary of it.

```
OUTPUT_RATIO     = 217,819 / 818,229 = 0.2662    # measured
OUTPUT_ALLOWANCE = 1.15                          # estimated — empty/failed calls + envelope
output_chars  = 9,235,000 × 0.2662 × 1.15 = 2,827,300
output_tokens = 2,827,300 / 2.06 = 1,372,500
```

### Step 5 — tokens to credits

```
TOKENS_PER_CREDIT = 5,000                        # decision, not measurement
total_tokens = 3,963,700 + 1,372,500 = 5,336,200
credits      = 5,336,200 / 5,000 = 1,067  ->  ~1,070 per week
```

The 5,000-token unit is chosen so one tender lands on ~100 credits, which a
buyer can hold in their head. It is arbitrary in cost terms.

### Step 6 — the Opus multiplier

Credits are defined against Sonnet 5. Opus 5 costs 1.667× per token at this
workload's 74% input / 26% output mix, so it burns 1.667× the credits.

```
credits_opus = credits × (1 - opus_share + opus_share × 1.667)
50% share:     1,067 × 1.333 = 1,422
```

Because the multiplier is baked into the credit definition, moving a customer
to Opus charges them for it automatically rather than quietly eating the cost.

---

## What it costs us

### Claude API

```
Sonnet 5:  3.9637M × $3/M  +  1.3725M × $15/M  =  $11.89 + $20.59 = $32.48/week
```

| Routing | Clean | +15% failures (typical) | +30% (bad) |
|---|---|---|---|
| Sonnet 5 only | $32 | **$37** | $42 |
| 25% of tokens on Opus | $38 | $44 | $49 |
| 50% of tokens on Opus | $43 | $50 | $56 |
| All Opus 5 | $54 | $62 | $70 |

The Batch API halves every figure above. Ingestion already runs as a background
job, so the only cost is asynchronous turnaround.

#### Why failures deserve a line of their own

`CHUNK_ATTEMPTS = 2` — one retry, no backoff. Three details make the overhead
larger than a simple retry rate suggests:

1. **Truncation at the 8192-token ceiling raises**, so it burns a fully billed
   call *then* retries — and it is **deterministic**, so both attempts fail.
2. **The merge is all-or-nothing.** One chunk failing twice discards the whole
   document's work after every one of its chunks has been paid for. The
   220,933-character proposal is ~19 chunks, i.e. 19 chances to lose all 19.
3. **Re-extraction caches only on `extraction_status == "ok"`**, so a failed
   document is re-attempted at full cost on *every* subsequent run.

A fact-dense chunk that truncates therefore costs money weekly, forever, and
produces nothing. That is the leak worth fixing — raise `LLM_MAX_TOKENS` or
lower the chunk budget. Opus does **not** fix it; same ceiling.

### AWS — estimated, not measured

| Line | $/month |
|---|---|
| EC2 `t3.large` (2 vCPU / 8 GB), on-demand | $61 |
| EBS gp3, 100 GB | $8 |
| Snapshots / backup | $3 |
| S3 document archive (~26 GB/yr accumulating) | $1 |
| CloudWatch logs | $4 |
| Route 53, egress, misc | $3 |
| **Lean total** | **~$80** |
| Add ALB + second instance for redundancy | +$95 |
| **Production total** | **~$175** |

Band **$80–180/month** = **$18–42/week**; the tables above use **$18/week**,
the lean single-instance figure — which is the right configuration here (see
**Scaling**). A 1-year Savings Plan takes ~35% off the EC2 line. Egress is
negligible — about 13 MB/week goes to the Anthropic API, and AWS's first
100 GB/month outbound is free.

### Together

| | Sonnet only | All Opus |
|---|---|---|
| Claude API (with failures) | $37/wk | $62/wk |
| AWS | $18/wk | $18/wk |
| **Total cost to serve** | **$55/wk** | **$80/wk** |
| Credit-line revenue | $112/wk | $187/wk |

Plus the platform fee, which is where the fixed cost is actually recovered.

---

## Three decisions embedded in these numbers

**Failed and retried calls do not consume customer credits.** Every credit
figure here is the clean number. Metering deterministic truncation failures to
a customer means billing them weekly, forever, for a document that never
processed — and it removes our own incentive to fix it. The +15–30% failure
overhead stays on our side.

**AWS is not charged against the credit line.** At $960/year it is covered
many times over by the platform fee. Allocating it to credits would make the
credit margin look far worse than it is, and would wrongly imply that AWS
scales with usage.

**The Opus multiplier over-recovers.** All-Opus adds $3,880/yr in credit
revenue against roughly $1,300/yr of real API cost and nothing in AWS. Offering
Opus as a customer-selectable quality tier is therefore *more* profitable than
Sonnet — worth knowing before deciding whether it is a paid upgrade or a silent
default.

---

## Scaling

**The pipeline is fully sequential.** `ask_each_chunk` is a serial `for` loop;
there is no thread pool, queue or worker anywhere in `procurement/`. And
`api/main.py` calls `run_ingestion` **inside the HTTP request handler**, holding
the request open for the whole run behind an in-process per-project lock.

One tender is 102 sequential calls at roughly 20s each — about **34 minutes** of
wall clock.

### Utilisation at 500 MB/week

```
11.3 tenders/week x 34 min = 6.4 hours of work per week, out of 168
                           = 3.8% utilised
```

A single `t3.large` saturates somewhere around **9–13 GB/week**. There is roughly
20x headroom before capacity is the constraint.

### Why autoscaling does not help yet

| Reason | Detail |
|---|---|
| CPU never rises | ~99% of a run is waiting on the Anthropic API, so a `CPUUtilization` scaling policy never triggers |
| Instances do not speed up a tender | 102 calls are sequential; a second node helps only with *concurrent* tenders |
| The lock is in-process | `_run_lock` does not hold across instances — two nodes could ingest the same project |
| State is on local disk | JSON snapshot stores and SQLite need EFS or a store migration before any node can be added |
| ALB idle timeout is 60s | A 34-minute blocking request dies behind a load balancer |

Scaling the ingest tier to zero genuinely cuts compute — 6.4 hrs/week of Fargate
is about **$2.70/month** — but ALB and EFS add ~$33/month of fixed overhead to
get there. **Crossover is around 6 GB/week**, roughly 12x current volume.

### The order worth doing it in

1. **Batch API.** Halves the Claude line and removes wall-clock pressure
   entirely. Ingestion is already a background-shaped job.
2. **Parallelise chunks within a run.** The single biggest latency lever — 102
   sequential calls at concurrency 8 turns 34 minutes into about 4, with an
   identical token count and therefore identical cost.
3. **Move ingestion out of the request.** Required before any horizontal
   scaling, and it fixes the ALB-timeout problem on its own.
4. **Autoscale.** Not before ~6 GB/week.

---

## Constants, in one place

Change one of these and everything above moves.

| Constant | Value | Source |
|---|---|---|
| `CHARS_PER_MB` | 18,470 | measured — 818,229 chars / 44.3 MB |
| `INPUT_RATIO` | 1.4636 | measured — prompts + chunk overhead |
| `OUTPUT_RATIO` | 0.2662 | measured — stored compact JSON |
| `OUTPUT_ALLOWANCE` | 1.15 | **estimated** |
| `CPT_IN` | 3.41 | measured — `count_tokens`, synthetic layout |
| `CPT_OUT` | 2.06 | measured — `count_tokens`, JSON shape |
| `TOKENS_PER_CREDIT` | 5,000 | decision |
| Sonnet 5 | $3 / $15 per Mtok | **list price — goes stale** |
| Opus 5 | $5 / $25 per Mtok | **list price — goes stale** |
| Opus multiplier | 1.667× | derived at the 74/26 mix |
| Failure overhead | +15% typical | **estimated** |
| AWS | $80–180/month | **estimated** |

### Re-deriving them

```bash
python docs/pricing/measure_workload.py [project-slug]   # CHARS_PER_MB, INPUT_RATIO, OUTPUT_RATIO
python docs/pricing/calibrate_tokens.py [project-slug]   # CPT_IN, CPT_OUT
python docs/pricing/estimate_cost.py                     # cost basis, credit unit
python docs/pricing/price_tiers.py                       # bands, plans, sensitivity
```

The first two need an ingested multi-vendor tender under `projects/` and the
later `procurement/` modules — see [`README.md`](README.md) for which branch
they run on.

---

## What would change the answer most

1. **A customer whose documents are scanned rather than born-digital.**
   `CHARS_PER_MB` collapses, and so does their bill. The per-MB rate is the
   weakest number here — quote it as planning, not contract.
2. **Model price changes.** Both list prices above are hand-maintained copies.
   Check them before quoting anything.
3. **Turning on the LLM vision fallback for scanned PDFs.** Production passes
   `pdf_fallback=None` today, so unreadable drawings cost nothing. Enabling it
   prices images instead of text and breaks every ratio on this page.
4. **Adopting the Batch API.** Halves the Claude line with no change to what the
   customer pays.
5. **Nothing here can be billed yet.** There is no token accounting anywhere in
   `shared/llm/` or `procurement/`. The meter these figures assume does not
   exist.
