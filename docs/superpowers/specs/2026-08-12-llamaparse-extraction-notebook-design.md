# LlamaParse Extraction Notebook — Design

Date: 2026-08-12
Status: approved

## 1. What this is

A notebook that takes one document — PDF or XLSX — parses it with **LlamaParse
only**, and shows both halves of the result: the markdown the parser produced,
and the technical facts, deviations and commercial fields the shipped
`procurement` extractors pull out of that markdown.

It is a sibling of
[`document_cost_estimator.ipynb`](../../../notebooks/document_cost_estimator.ipynb),
which measures what a document *costs*. This one shows what a document
*yields* when the hosted parser reads it. It reuses that notebook's scratch
staging and usage recorder verbatim; the parser wiring is what is new.

## 2. Scope

In: parsing PDFs and spreadsheets through LlamaParse, running the real
ingestion over the result, and displaying the extracted facts.

Out: changing anything in `procurement/`, `shared/` or `api/`. Every deviation
from shipped behaviour is a notebook-local patch installed and removed by a
context manager. Also out: deciding whether LlamaParse is better than
`pdftotext`. That question was answered — with a negative result — in
[`2026-08-11-pdf-parser-quality-ab-design.md`](2026-08-11-pdf-parser-quality-ab-design.md)
and [`../pdf-parser-quality-ab-results.md`](../pdf-parser-quality-ab-results.md).
This notebook is an instrument for looking at one document, not a re-run of
that experiment, and it must not be read as re-opening it.

## 3. The reader patch

`loaders.read_text_with_source` consults its `pdf_reader` argument **only for
`.pdf`** — that narrowness is deliberate and load-bearing, because it is what
made the `.xlsx` document a control in the A/B. `.xlsx` is hard-routed to
`read_xlsx_text`/openpyxl in every arm.

So "LlamaParse for spreadsheets too" cannot be had from the shipped switch. Two
patches would achieve it and only one is honest:

| patch | works? | `text_source` records |
|---|---|---|
| `loaders.read_xlsx_text` | yes — `read_text_with_source` resolves it as a module global at call time | `"xlsx"` — a lie, for text LlamaParse produced |
| `procurement.pipeline.read_text_with_source` | yes — `pipeline.py:26-27` binds it at module scope | `"llamaparse"` |

The second is what the notebook installs. The label matters more than it looks:
`text_source` is the only field that distinguishes "this datasheet genuinely
states nothing" from "we read this with the wrong reader", and a run whose
store says `xlsx` while a hosted parser did the reading has destroyed exactly
that distinction.

The wrapper:

```python
def llamaparse_read(path, llm_fallback=None, pdf_reader=None):
    if Path(path).suffix.lower() in {".pdf", ".xlsx", ".xls"}:
        return llamaparse_reader.parse_pdf(path, tier=TIER), "llamaparse"
    return loaders.read_text_with_source(path, llm_fallback=llm_fallback,
                                         pdf_reader=pdf_reader)
```

Three properties of it are decisions, not incidentals:

- **`.docx` and `.txt` fall through** to the shipped chain. A folder or vendor
  `.zip` with mixed content still ingests, and only the two formats the request
  named reach the hosted parser.
- **No `llm_fallback` is passed on the LlamaParse arm.** If the parser fails it
  raises, `_read_and_guard` catches it, and the document is recorded `failed`
  with the reason in `notes`. A silent rescue by the LLM transcription path
  would report another reader's text under LlamaParse's name — the same trap
  `read_pdf_text_with_source` documents for the A/B arms.
- **`parse_pdf` is a misnomer and is called anyway.** Nothing in it is
  PDF-specific: it uploads bytes and asks LlamaCloud for markdown. Calling it
  for spreadsheets from the notebook is preferable to adding a `parse_any`
  alias to shipped code for one notebook's benefit. The notebook says so at the
  call site.

PDFs additionally get `pdf_reader="llamaparse"` passed into `run_ingestion`, so
the arm is selected by the shipped mechanism wherever the shipped mechanism
reaches.

**Both of `pipeline.py`'s bindings are patched, not just one.** Line 26-27
imports `read_text` alongside `read_text_with_source`, and `_classify_pass`
uses the former for the 500-character head it shows the classifier. Leaving it
unpatched would classify a spreadsheet on openpyxl's rendering and extract it
from LlamaParse's — two different readings of one document inside a single run,
for no gain. The second patch costs nothing: `parse_pdf` caches to disk, the
parse pass has already warmed that cache for every staged file, and the
classifier's read is served from it.

**Extraction really does see the LlamaParse text, not just classification.**
The pipeline reads each document once in `_read_and_guard` and passes `text=`
down to every extractor; the extractors' own `read_text` import is the path not
taken on this route.

## 4. Cells

1. **Config** — `INPUT_PATH`, `TIER` (default `"agentic"`, the tier
   `llamaparse_reader.DEFAULT_TIER` already names), `MODEL`, `RUN_EXTRACT`,
   `OUTPUT_DIR`, `SCRATCH_ROOT`. Pins `LLAMAPARSE_CACHE_DIR` to an absolute
   path under the repo root: its default is relative to the working directory,
   so a notebook run from `notebooks/` would miss a cache written from the repo
   root and re-bill every file.
2. **Setup** — repo root discovery, `.env`, imports, and a check that
   `LLAMA_CLOUD_API_KEY` is present.
3. **Stage** — a throwaway project under the system temp directory, everything
   on the vendor side, with the guard that refuses `projects/`.
   `tests/test_real_corpus_coverage.py` asserts against the newest
   multi-vendor store there, so a two-file scratch project written into
   `projects/` would silently become the corpus that test measures. The RFQ
   side stays empty: without a requirement set there is no compliance matrix,
   but classification, routing and extraction all run as they do in production.
4. **Parse pass** — LlamaParse each staged file; report characters, cache
   hit/miss, and chunk counts at `TECH_CHUNK_CHARS` and
   `REQUIREMENTS_CHUNK_CHARS` read from `procurement.chunking`. Show a markdown
   preview and write a copy to `OUTPUT_DIR/<stem>.md`, so what the extractor
   will see is readable as a file rather than only as a cell output.
5. **Extract pass** — `run_ingestion(..., pdf_reader="llamaparse")` under the
   patch, wrapped in the cost estimator's usage recorder. Gated on
   `RUN_EXTRACT`; setting it `False` leaves a parse-only instrument.
6. **Facts** — a documents table (`class | text_source | status | notes`), the
   technical `FactRecord`s as a DataFrame
   (`parameter | value | unit | verbatim | doc`), deviations, and
   commercial/normalized when a quotation routed. Facts also written to
   `OUTPUT_DIR/facts.csv`. Missing values stay missing: an unfound parameter is
   absent, never `0` or `""`.
7. **Self-check** — asserts every staged PDF/XLSX came back with a
   `text_source` beginning `llamaparse:`. If the patch fails to take, the
   notebook must say so loudly rather than present openpyxl's output as
   LlamaParse's. This is the notebook's own test, and it is the reason the
   honest-label patch was chosen in §3: with the `read_xlsx_text` patch there
   would be nothing to assert against.
8. **Cleanup** — drops the scratch project, keeps the parse cache and
   `OUTPUT_DIR`.

## 5. What it will not report

**LlamaParse cost in dollars.** LlamaParse bills per page by tier, and
`parse_pdf` returns only text — it reads `result.markdown.pages` and discards
the page count before any caller can see it, and the on-disk cache stores text
alone. Rather than infer a page count from the markdown or hardcode a credit
rate, the notebook reports whether each parse was **billable** (a cache miss)
and leaves the credit arithmetic to the LlamaCloud dashboard.

The Anthropic side is different and is reported in USD, because the machinery
already exists: the usage recorder measures tokens off the live responses and
the Sonnet 5 rate card is next door in the cost estimator.

## 6. What this notebook cannot tell you

- **Whether LlamaParse is the right parser.** See §2. The A/B answered that,
  negatively, on a three-document corpus with a control.
- **Anything about compliance.** The RFQ side is empty by construction, so
  every requirement × vendor verdict is missing rather than computed.
- **The cost of a second run.** Both caches — LlamaParse's, keyed on content
  hash and tier, and the pipeline's, keyed on `content_sha256` — make a repeat
  run of the same bytes nearly free and nearly silent. Re-stage from scratch,
  or change `TIER`, for a clean measurement.
