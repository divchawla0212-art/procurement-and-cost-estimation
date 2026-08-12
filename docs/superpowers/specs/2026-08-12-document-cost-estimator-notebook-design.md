# Document Cost Estimator Notebook — Design

Date: 2026-08-12
Status: draft, pending review

## 1. What this is

A notebook that takes one document — PDF, XLSX, DOCX, plain text, a folder of
them, or a vendor ZIP — runs it through the real ingestion pipeline, and
reports two things:

| number | where it comes from |
|---|---|
| **extracted characters and raw MB** | `procurement/loaders.py`, the same readers production uses |
| **cost in USD** | token counts measured off the live Anthropic responses, priced at the Sonnet 5 rate card |

**Billed price and margin were in the first draft and have been removed.** This
is a cost instrument, not a quoting tool. The metering contract in
[`2026-08-12-usage-based-pricing-design.md`](2026-08-12-usage-based-pricing-design.md)
§4 is unaffected and remains the authority on what a client pays; nothing here
restates or second-guesses it.

**Sonnet only.** `claude-sonnet-5` is the only model the notebook runs or
prices. The rate card carries two rows for it — the introductory rate, live
until 2026-08-31, and the standard rate that follows — because a quote written
today has to survive that date.

## 2. Scope

In: measuring one ingestion run's characters, bytes, tokens, cost and price.

Out: changing anything in `procurement/`, `shared/` or `api/`. The notebook is
a read-only instrument over the shipped pipeline. In particular it does **not**
implement §5.4 of the pricing spec — see §5 below for how it gets token counts
without that refactor.

## 3. Input handling

One config variable, `INPUT_PATH`, accepting three shapes:

| shape | staged as |
|---|---|
| a single file | one vendor folder holding that file |
| a directory | one vendor folder, copied recursively |
| a `.zip` | `procurement.project.unpack_vendor_zip`, which may create several vendors |

Everything lands on the **vendor** side of a scratch project. The RFQ side is
left empty: without a requirement set the compliance matrix has nothing to
compare against, but classification, routing and extraction — the passes that
cost money — all run exactly as they do in production. Measuring the cost of
processing a document does not need the document it will be compared to.

**The scratch project is built under the session scratchpad, never under
`projects/`.** `tests/test_real_corpus_coverage.py` asserts against the newest
multi-vendor store in `projects/`, so a throwaway two-file project written
there would silently become the corpus that test measures. This is the single
hardest constraint on the notebook and the reason the root is a config
variable rather than the obvious default.

## 4. The two passes

### 4.1 Free pass — no API calls

For every staged file:

- raw bytes, and MB at the configured `BYTES_PER_MB`
- `read_text_with_source()` → character count and the reader that won
  (`pdftotext`, `pypdf`, `xlsx`, `docx`, `text`), with `llm_fallback=None` so
  the LLM transcription path cannot fire and cannot charge
- a predicted `failed` for anything under `loaders.MIN_EXTRACTABLE_CHARS`
- chunk count at `TECH_CHUNK_CHARS` (12,000) and `REQUIREMENTS_CHUNK_CHARS`
  (8,000), read from `procurement.chunking`, not hardcoded

From those, a projected bill: input tokens at chars ÷ 4, output tokens at the
corpus-measured ratio of 139,780 output tokens per 801,022 characters
(0.1745 output tokens per character, pricing spec §2.2).

The projection is an estimate and is labelled as one. Its job is to let the
reader see the spend before authorising it, not to be right to the cent.

### 4.2 Paid pass — the real pipeline

`run_ingestion(root, slug, client)` — classify, resolve lineage, route,
extract — against a scratch project, with a usage-recording client. Gated on
`DRY_RUN`, which defaults to `True`: the notebook run-all is free until the
reader changes one flag.

## 5. Measuring tokens without touching shipped code

`AnthropicClient.classify_structure` reads `message.usage` and discards it.
Every other provider client does the same. Fixing that is §5.4 of the pricing
spec and is deliberately out of scope here.

The notebook therefore records usage one layer lower. `anthropic_client.py`
constructs `anthropic.Anthropic(...)` **inside** `classify_structure`, so the
symbol resolves at call time; replacing `anthropic.Anthropic` with a factory
that returns a recording proxy captures every `messages.create` the run makes
— including `pdf_llm.transcribe_pdf`, which constructs its own client the same
way and whose transcription calls are real spend that belongs in the total.

The proxy delegates by `__getattr__` and returns the SDK's own message object
unchanged, so nothing downstream can tell the difference. It records, per
call: model, input tokens, output tokens, cache-creation tokens, cache-read
tokens, and `stop_reason`.

This is a notebook-local patch installed by a context manager and removed on
exit. It is not a substitute for §5.4 and the notebook says so.

## 6. Cost of goods

```
Sonnet 5, introductory (through 2026-08-31)   $2.00 / $10.00 per MTok
Sonnet 5, standard                            $3.00 / $15.00 per MTok
```

Cache-read tokens bill at 0.1× the input rate, cache-creation at 1.25×. Both
are reported even when zero, because a zero cache-read count across a
multi-chunk document is itself a finding.

Costs are reported in USD only. `BYTES_PER_MB` remains a config constant,
defaulting to 1,048,576, because the per-document table still reports MB and it
should match what a file browser shows on disk.

## 7. Output

One per-document table:

`path | class | reader | bytes | MB | chars | chars/MB | status`

and one run summary: measured input/output/cache tokens, the request count, and
USD cost at both Sonnet rows.

## 8. What this notebook cannot tell you

- **It reports cost, not price.** Nothing here says what a client should be
  charged for this document. That is the pricing spec's §4, deliberately left
  where it lives.
- **Token counts are the run's, not the document's.** Classification of a
  cached document is skipped, so a second run of the same bytes measures
  almost nothing. Re-stage from scratch for a clean measurement.
- **Only Anthropic is measured, because only Anthropic is run.** The notebook
  constructs `AnthropicClient(model=MODEL)` directly rather than resolving
  `LLM_PROVIDER`, so `MODEL` is the whole story. Pointing it at another
  provider means teaching the recorder that provider's SDK first.
