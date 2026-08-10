# PDF parser quality A/B — measured in review flags

## The question

Does a better PDF parser produce fewer review flags?

"Review flag" is not a metaphor here. `compliance.py` assigns every
requirement × vendor cell one of five verdicts, and `matrix.py` buckets
`review` and `unanswered` together as `needs_human` — the worklist a human has
to clear before an award can be defended. That count is the metric. Anything
that reduces it is worth money; anything that only makes the extracted text
look nicer is not.

## Why the obvious comparison is the wrong one

The request was "pypdf vs LlamaParse". But pypdf is not what this pipeline
reads PDFs with. `read_pdf_text_with_source` runs a chain — `pdftotext -layout`
first, and pypdf only if pdftotext returns under 200 characters, and an LLM
transcription only if both fall short. `pdftotext` is present on the
development workstation, so pypdf almost never runs.

Measured on the two PDFs in this corpus:

| document | pdftotext | pypdf |
|---|---|---|
| KERUI datasheet (14pp) | 80,722 chars / 772 lines | 26,397 chars / 664 lines |
| ADPOWER spec (6pp) | 60,688 chars / 497 lines | 20,595 chars / 461 lines |

pypdf recovers roughly a third of what pdftotext does. A two-arm
pypdf-vs-LlamaParse test would therefore benchmark LlamaParse against a reader
the pipeline does not use, and a LlamaParse win would not justify changing
`loaders.py` — because the thing it beat is not the thing running today.

So the experiment runs **three arms**: pypdf, pdftotext, LlamaParse.

## Corpus

Three documents, from the tracked `processed-data/` tree, one project per arm.

| vendor | file | pages / rows | role |
|---|---|---|---|
| KERUI | `01 DOD-30201-50150-BH-000-16-00-004 DataSheet Gas Generator.pdf` | 14pp | arm-sensitive |
| ADPOWER | `ADN-AEC-ME-SPC-026 MR Gas Genset copy.pdf` | 6pp | arm-sensitive |
| MKON | `DOD-30201-50150-BH-000-16-00-004 DataSheet Gas Generator.xlsx` | 368 rows | control + ground truth |

The MKON file is the reason this corpus is worth more than its size suggests.
It carries the same ADNOC document number as KERUI's PDF —
`DOD-30201-50150-BH-000-16-00-004` — so it is the *same datasheet form*, filled
by a different vendor and submitted in its native spreadsheet format. Its
columns are `Sr.No | DESCRIPTION | Project Requirements | Data Filled By
Vendor`, and `read_xlsx_text` reads them losslessly through openpyxl. No PDF
parser ever touches it.

That gives it two jobs at once, for one read:

- **Ground truth.** Its `DESCRIPTION` column is the parameter list KERUI's PDF
  should also yield. Recall against that list is a hard, deterministic,
  LLM-free number.
- **Control.** Its reader is identical in all three arms, so its `needs_human`
  count must not move between them. Whatever it does move by is the noise
  floor, and no smaller delta on KERUI or ADPOWER may be believed.

ADPOWER's submission is its marked-up copy of the client spec — the document
that `test_real_corpus_coverage.py` records as having produced zero facts in
every run before phase 4. It is in the corpus precisely because it is the case
where parsing quality shows up in routing and in the no-readable-text guard,
not only in extraction.

## Freezing the denominator

Compliance verdicts are computed per requirement × vendor. If the RFQ pass
re-runs per arm, the requirement set can differ between arms, the denominator
moves, and the three flag counts stop being comparable — a smaller count could
mean a better parser or simply fewer requirements.

So: build one **template project**, run the RFQ pass once against the client
documents in `01-client-mr-rfq/`, and produce `requirements.json`. Each arm is
then a `copytree` of that template before its ingest. All three arms score
against a byte-identical requirement set.

## The reader seam

`run_ingestion` reaches the reader through exactly one path for vendor
documents: `_read_and_guard` → `read_text_with_source`. An optional
`pdf_reader: str | None = None` parameter is threaded along that path.

`None` means today's chain, unchanged. Every existing caller and every existing
test therefore keeps its current behaviour, which is what keeps the baseline
test counts honest.

Four readers:

| name | behaviour |
|---|---|
| `None` (default) | `pdftotext → pypdf → LLM`, exactly as today |
| `"pdftotext"` | pdftotext only |
| `"pypdf"` | pypdf only |
| `"llamaparse"` | LlamaParse, `parse_mode` accurate, `result_type="markdown"` |

**Arms are pure — no fallback, and no LLM transcription.** This is the single
most load-bearing decision in the design. If the pypdf arm could fall back to
pdftotext it would silently become the pdftotext arm and report its numbers.
If any arm could fall back to LLM transcription, the experiment would be
measuring Claude's vision rather than the parser, and the better the parser
under test, the *less* the fallback would fire — so the arms would converge
toward each other and the experiment would read as "no difference" no matter
what was true.

`MIN_EXTRACTABLE_CHARS` still applies. A pure arm that recovers nothing records
`extraction_status = "failed"` with a reason in `notes`, which is a result to
report, not an error to rescue.

LlamaParse lives in its own module, `procurement/llamaparse_reader.py`, so an
environment without the optional dependency cannot fail to import `loaders`. It
caches parsed output to disk keyed by file SHA-256, so re-running an arm does
not re-bill and does not re-introduce parser-side variance.

Markdown rather than plain text for the LlamaParse arm: markdown renders table
rows as pipe-delimited lines, which is the shape `chunking.py` already splits on
and the same shape `read_xlsx_text` produces for the MKON sheet. Handing
LlamaParse's own strength back as plain text would handicap the arm under test.

## What is measured

Per arm, after `run_ingestion`, from `build_matrix` and
`build_extraction_status`:

- **`needs_human` cell count** — the headline, split into `review` vs
  `unanswered`
- the same, per vendor, so KERUI and ADPOWER move independently
- **MKON's count as the noise gauge**
- supporting counters: `extracted` / `failed` / `skipped`, `fact_count`, and
  `text_source` characters per document

## Row recall — the deterministic half

Read column B (`DESCRIPTION`) of the MKON sheet for every row carrying a
`Sr.No`, giving roughly 360 parameter names. For each arm's KERUI text, count
how many of those names appear, normalized with `compliance.py`'s existing
`_norm`.

No LLM, no key, no network. This is the number that explains *why* an arm wins
rather than merely that it did, and it is the only part of the experiment that
can run in CI.

## Non-determinism

Extraction is a model call, so the same text can yield different facts run to
run, and therefore different verdict counts. Three controls, in order of how
much they cost:

1. Model and prompt version pinned identically across arms.
2. `LLM_TEMPERATURE` pinned to 0 by the harness.
3. MKON's drift read as the noise floor.

`AnthropicClient.classify_structure` does not currently accept a temperature —
it calls `messages.create` with model, max_tokens, tools and messages only.
Rather than change what every caller does as a side effect of an experiment,
temperature is read from an `LLM_TEMPERATURE` environment variable that
defaults to unset, and unset means today's behaviour exactly.

One run per arm. A three-run distribution per arm is a worthwhile follow-up,
but starting there spends triple before establishing that noise is even a
problem — and the MKON control is what tells us whether it is.

## Deliverables

| file | kind |
|---|---|
| `procurement/loaders.py` | `pdf_reader` parameter, pure readers, registry |
| `procurement/llamaparse_reader.py` | new; optional dependency, SHA-keyed cache |
| `procurement/pipeline.py` | thread `pdf_reader` through `run_ingestion` |
| `shared/llm/anthropic_client.py` | `LLM_TEMPERATURE`, defaulting to today |
| `tools/parser_ab.py` | the harness — needs keys, never runs in CI |
| `tests/test_pdf_reader_selection.py` | key-free: registry, purity, clean skip |
| `tests/test_datasheet_row_recall.py` | key-free, deterministic row recall |
| `.env.example` | `LLAMA_CLOUD_API_KEY` |
| `pyproject.toml` | `llama-parse` as an optional extra, not a core dependency |

`llama-parse` is an optional extra deliberately. CI installs the core
dependency set and must stay key-free per CLAUDE.md; a core dependency on a
hosted parser would put a network service on the critical path of every
checkout.

## Out of scope

- Changing the default reader chain. This experiment produces the evidence for
  that decision; it does not pre-empt it.
- LlamaParse `fast` vs `accurate` as a fourth arm. Worth running once an
  accurate-mode win is established.
- A three-run distribution per arm.
