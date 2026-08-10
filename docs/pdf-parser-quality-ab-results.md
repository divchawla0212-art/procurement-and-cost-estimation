# PDF parser quality A/B — findings

Design: [`docs/superpowers/specs/2026-08-11-pdf-parser-quality-ab-design.md`](superpowers/specs/2026-08-11-pdf-parser-quality-ab-design.md)
Harness: `python -m tools.parser_ab`

The question was whether a better PDF parser produces fewer review flags —
`review` and `unanswered` compliance verdicts, which `matrix.py` buckets as
`needs_human`: the worklist a person must clear before an award can be
defended.

Three arms — **pypdf**, **pdftotext**, **LlamaParse** — over three documents,
scored against one frozen 200-requirement set.

> **Answer: no.** Not on this corpus. All three arms produced review-flag counts
> within 12 cells of each other, against a **27-cell noise floor** measured on a
> control document whose text is byte-identical in every arm. The readers
> recover the same content and differ only in how they arrange it. Keep the
> current chain; re-test on documents whose text layer actually fails.

---

## The deterministic result

This half needs no model, no key and no network, and runs in CI
(`tests/test_datasheet_row_recall.py`).

MKON submitted ADNOC datasheet `DOD-30201-50150-BH-000-16-00-004` as `.xlsx`;
KERUI submitted **the same form**, filled in, as a 14-page PDF. openpyxl reads
the spreadsheet losslessly, so its `DESCRIPTION` column is ground truth for what
a parser should recover from the PDF: **236 scorable parameter names**.

| reader | contiguous | all words present | content chars |
|---|---|---|---|
| pypdf | **236/236 (100%)** | 236/236 (100%) | 20,855 |
| pdftotext | 216/236 (91.5%) | 235/236 (99.6%) | 20,855 |

**pdftotext lost exactly one parameter.** The other 19 it emitted in pieces:
`-layout` renders a tall wrapped table cell beside its neighbours, splicing the
row number and the requirement column into the middle of a long description —

```
        Gas Gensets Operation: Parallel
1.14 Generator Control Relay shall have provision of ... from one   Compliance Required
        unit to the other after defined time
```

That is why two recall numbers are reported rather than one. They agree when a
reader lost content and diverge when it only rearranged it.

Which failure hurts the extractor more is genuinely unsettled. The pipeline's
own `_docx_lines` argues for keeping a row's cells together — splitting a clause
from its unit invites the model to pair the wrong number with the wrong unit —
and by that argument pdftotext's interleaving is a *feature*.

---

## Two counting traps, both of which produced a wrong answer first

Neither of these is incidental. Each one, taken at face value, points at the
opposite conclusion from the truth.

**1. `-layout` padding.** pdftotext returns 80,722 characters on the KERUI
datasheet against pypdf's 26,397 — "three times as much". Strip the whitespace
and both return **exactly the same 20,855 alphanumeric characters**. The gap is
column padding, not recovered text. The first draft of the design spec used that
3× figure as its justification for the third arm.

**2. HTML table markup.** LlamaParse's `agentic` tier emits HTML tables, not the
pipe-delimited markdown the design assumed. `<table><tbody><tr><td>` normalizes
to `tabletbodytrtd`, so alphanumeric length credits the arm for its own markup.
On the ADPOWER spec that reads as 23,997 characters against pdftotext's 17,540 —
**+37%**. With tags stripped: 17,721 against 17,540, **about 1%**.

`content_length()` — alphanumerics with markup stripped — is the only character
count compared across arms, and both traps are pinned by tests so no future
floor can be built on raw yield.

---

## Defects found while building this

| # | defect | consequence |
|---|---|---|
| 1 | `LLM_TEMPERATURE=0` 400s on `claude-sonnet-5` (`temperature` is deprecated for this model) | every extraction in every arm failed |
| 2 | that failure reported `needs_human=0` for all arms plus "control holds" | a total failure rendered as a flawless result |
| 3 | LlamaParse `parse()` needs `expand=["markdown"]` | without it the arm reads every document as empty, silently |
| 4 | `result.markdown` is a `Markdown` object with `.pages`, not a string | `AttributeError` on first real call |
| 5 | `pdftotext` silently returns `""` for paths over 260 chars (Windows `MAX_PATH`) | a readable PDF recorded as *"scanned or drawing-only document"* |
| 6 | the control check compared `needs_human` totals only | 27 cells of drift reported as "control holds" — see below |

Defect 2 is the one worth dwelling on: a metric that reads best when the
pipeline is most broken is worse than no metric. The harness now refuses to
report a zero-requirement run and marks any zero-fact arm as failed in the table
itself.

Defect 5 is a **production** bug, not an artifact of the experiment — it is
filed separately. The default reader chain partially masks it, because
`read_pdf_text_with_source` falls through to pypdf when pdftotext comes up
short; the wrong "scanned or drawing-only" diagnosis still lands whenever pypdf
is also thin, and any caller pinning a single reader gets it directly.

---

## Determinism: one control was lost, the other earned its place

The design specified three defences against reading run-to-run noise as signal:
pinned model and prompt version, pinned temperature, and the MKON control.

**Temperature is unavailable on Claude 5** — see defect 1. `LLM_TEMPERATURE`
remains in the codebase for providers that accept one, defaulting to unset,
which sends exactly what production has always sent. But for this experiment it
is gone, which leaves MKON as the only defence.

That is precisely why MKON is in a three-document corpus. Its `.xlsx` is read by
openpyxl in every arm, so its `needs_human` count *must* be identical across
arms; whatever it drifts by is the noise floor, and no smaller difference
between arms means anything.

---

## What was built

| file | purpose |
|---|---|
| `procurement/loaders.py` | `pdf_reader` selects one **pure** reader — no chain, no LLM fallback |
| `procurement/llamaparse_reader.py` | LlamaParse arm; optional dependency, SHA-keyed cache |
| `procurement/pipeline.py` | threads `pdf_reader` to the **vendor side only** |
| `shared/llm/anthropic_client.py` | `LLM_TEMPERATURE`, opt-in |
| `tools/datasheet_ground_truth.py` | the ground-truth scorer |
| `tools/parser_ab.py` | the harness |

Two properties are load-bearing and both are pinned by tests:

- **Arms are pure.** A pypdf arm that could fall through to pdftotext would
  report pdftotext's numbers under pypdf's name — and because a fallback fires
  *least* for the best parser, every arm would converge and the experiment would
  read "no difference" whatever the truth.
- **The arm never touches the RFQ side.** Compliance is counted per requirement
  × vendor, so the requirement set is the metric's denominator. Verified by
  mutation: threading the arm into `_run_rfq_pass` turns
  `test_arm_never_reaches_the_rfq_side` red on its own assertion, and only it.

`llama-cloud>=1.0` is an optional extra, never a core dependency — CI installs
the core set and must stay key-free. Note that both `llama-parse` and
`llama-cloud-services` are deprecated; the latter's maintenance ended
2026-05-01.

---

## Review-flag counts

One run per arm, `claude-sonnet-5`, 200 requirements × 3 vendors = 600 cells per
arm. No document failed to extract in any arm.

| arm | needs_human | review | unanswered | facts |
|---|---|---|---|---|
| pdftotext | 430 | 348 | 82 | 464 |
| pypdf | 430 | 311 | 119 | 406 |
| LlamaParse | **426** | 325 | 101 | **485** |

Per vendor:

| arm | ADPOWER | KERUI | MKON *(control)* |
|---|---|---|---|
| pdftotext | 113 | 168 | 149 |
| pypdf | 117 | 164 | 149 |
| LlamaParse | 113 | **156** | 157 |

### The answer: no arm is distinguishable from any other

**The noise floor is 27 cells.** MKON's datasheet is `.xlsx`, read by openpyxl
in every arm — byte-identical input, no PDF parser involved — and 27 of its 200
cells still changed verdict between the pdftotext and pypdf arms. That is pure
run-to-run model variance.

Every difference the experiment found is smaller than that:

| comparison | difference | vs. 27-cell floor |
|---|---|---|
| totals (430 / 430 / 426) | 4 cells | noise |
| ADPOWER (113 / 117 / 113) | 4 cells | noise |
| KERUI (168 / 164 / **156**) | 12 cells | noise |

The tempting reading is that LlamaParse wins: 156 review flags on KERUI against
pdftotext's 168, a 12-cell improvement, plus the most facts extracted (485).
**That is less than half the noise floor** — and the control vendor itself moved
8 cells (149 → 157) on input that did not change at all. The apparent win is not
credible on one run.

### Why the control nearly failed to catch this

MKON scored `needs_human=149` in *both* the pdftotext and pypdf arms — a
perfectly stable headline. Underneath it, 25 cells had swapped between `review`
and `unanswered` and two `pass` cells had become `fail`.

A control watching only the total would have reported "no drift" and licensed
the 12-cell KERUI difference as a real finding. The check now compares the
entire verdict vector (`tools/parser_ab.py::_control_drift`,
`tests/test_parser_ab_control.py`).

### Row recall explains the flag result

| arm | contiguous | all words | content chars | raw chars |
|---|---|---|---|---|
| pypdf | **236/236** | 236/236 | 20,855 | 26,398 |
| pdftotext | 216/236 | 235/236 | 20,855 | 80,784 |
| LlamaParse | 209/236 | 235/236 | **20,339** | 66,351 |

All three recover essentially the same content — 235–236 of 236 parameters once
word order is ignored — and pypdf and pdftotext recover *character-for-character
identical* amounts. They differ only in arrangement. So indistinguishable flag
counts are the expected result, not a surprise: on a born-digital PDF with an
intact text layer there is nothing for a better parser to recover.

Notably, LlamaParse's `agentic` tier recovers slightly **less** content (20,339)
than either free reader, at roughly 30 credits per page.

---

## Recommendation

**Do not switch readers on this evidence.** Keep the current chain.

The corpus is the reason, and it is a limitation of the experiment rather than a
verdict on LlamaParse: both PDFs here are born-digital with intact text layers,
which is precisely the case where parser choice cannot matter. A hosted parser
earns its cost on scans, photographed pages, and rotated or multi-column
layouts — none of which are in this three-document corpus. The five KERUI
drawings that yield 1–42 characters are exactly that case, and were excluded by
design.

Two follow-ups, in order of value:

1. **Re-run on documents where the text layer fails.** The KERUI drawings, or
   any scan currently reaching the `llm:` transcription fallback. That is where
   a 30-credit-per-page parser could pay for itself, and this experiment says
   nothing about it.
2. **Then, if a difference appears, run three arms × three runs** — the
   distribution deferred at design time. It is now clearly necessary rather than
   speculative: with a 27-cell noise floor and effects under 12 cells, a single
   run per arm cannot resolve anything on this corpus. Note that temperature
   pinning is unavailable on Claude 5, so repetition is the only route to a
   confidence interval.

The deterministic half of this work has standing value regardless: it is
key-free, runs in CI, and would catch a parser regression that halved what the
pipeline can see.
