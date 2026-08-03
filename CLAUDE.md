# CLAUDE.md

Two Python packages over one shared LLM layer:

- **`procurement/`** — the tender comparison pipeline. Ingests a ZIP of vendor
  folders, classifies each document, resolves revision lineage, routes each
  document to a per-class extractor, and stores the result per vendor.
- **`cost_estimation/`** — the older costing-sheet ingestion CLI (`cost-est`).
- **`shared/llm/`** — provider clients behind one `classify_structure` interface,
  plus the versioned prompt files in `shared/llm/prompts/`.
- **`portal/app.py`** — Streamlit UI over `procurement`.

## Running things

```bash
python -m pytest
```

Run from the repo root. Tests are key-free — they use `shared/llm/mock_client.py`,
and no test may require `ANTHROPIC_API_KEY`.

**Two tests fail on a developer workstation, and neither is yours to fix in
passing.**

The first is `tests/test_portal_app.py::test_missing_api_key_does_not_block_creation`,
which fails for anyone with a populated `.env`. `portal/app.py` calls
`load_dotenv()`, which repopulates `ANTHROPIC_API_KEY` after that test's
`monkeypatch.delenv`. `load_dotenv()` resolves the file relative to
`portal/app.py`, not to your working directory, so running pytest from
somewhere else does not dodge it — only the absence of a `.env` does.

The second is `tests/test_real_corpus_coverage.py::test_no_vendor_in_a_multi_vendor_store_has_zero_technical_facts`,
and it is **a live finding, not flake**. It reads the newest multi-vendor store
under `projects/` and fails on AESL, whose only bid document is a
220k-character techno-commercial proposal that classifies as `quotation`.
`VENDOR_ROUTE` sends each `doc_class` to exactly one extractor, so that
document yields commercial facts and no technical ones, and AESL cannot be
checked against a single requirement. This is the phase-4 motivating defect
(ADPOWER contributing zero technical facts) recurring on a different route.
**Fix the routing, not the test** — and re-measure the row below afterwards.

There are therefore **two** baselines, and both are correct:

| where | baseline |
|---|---|
| a developer workstation, `.env`, `data/` and an ingested multi-vendor `projects/` present | **753 passed, 3 skipped, 2 failed** |
| CI, and any clean checkout | **748 passed, 10 skipped, 0 failed** |

Anything else is a real regression.

CI being green is not luck. With no `.env` the key stays deleted, the advisory
warning fires, and the portal test passes. With no `projects/` all four
`test_real_corpus_coverage.py` tests skip on their module-level guard, which is
what turns the AESL failure into a skip. The remaining three extra skips are
not credential failures: they are the tests guarded on the untracked `data/`
sample directory, which a workstation has and a fresh checkout does not.

So the CI row is the workstation row with the portal failure turned into a
pass, the four corpus-coverage results (3 passed, 1 failed here) turned into
skips, and the three `data/` passes turned into skips —
`748 = 753 + 1 - 3 - 3`, `10 = 3 + 4 + 3`, `0 = 2 - 1 - 1`; 758 tests either
way. When the counts move, measure the workstation row and derive the CI row
from it; editing the two rows independently is how they drift apart.

The workstation row also depends on what your untracked `projects/` holds:
`test_real_corpus_coverage.py` asserts against the newest store there with more
than one vendor, so it is a coverage instrument for the live corpus, not a
fixture-backed unit test.

CI runs that same command on every pull request into `main`, via
[`.github/workflows/tests.yml`](.github/workflows/tests.yml) — Ubuntu, Python
3.12, no provider secrets. Keep it that way: a test that needs a key belongs
behind a skip guard, not behind a repository secret.

The portal runs via the `procurement-portal` entry in `.claude/launch.json` —
use the preview tooling, not a bare `streamlit run`.

## Store invariants — violating these corrupts award decisions

The snapshots under `projects/<slug>/store/` are the **only** authoritative
store; `index/store.db` is derived and disposable.

- Every write goes through `procurement/store/snapshots.py`. Never hand-roll a
  snapshot write.
- `generation` bumps **once per write transaction**, never once per file.
- `field_path` addresses list members by **id**, never by index — list order is
  not stable across re-extractions, and `overrides.py` rejects index selectors
  outright.
- **A stored collection contains exactly the records of its currently-live
  sources — no more.** Facts of superseded, reclassified or deleted documents
  must be pruned, not merely skipped on re-extraction. This is the invariant
  phase 2 shipped broken; see `_prune_orphan_facts` in `procurement/pipeline.py`.
- Missing data is never coerced to a passing or zero value. An unfound parameter
  is omitted, not emitted as `0` or `""`.
- **Arithmetic stays in Python.** Extractors capture numbers and units verbatim;
  the model reads, code decides. Never ask an extractor whether a vendor complies.
- A failed extraction never blanks previously-good stored data, and always
  records why in `DocumentRecord.notes`.

## Planning convention

Design specs live in `docs/superpowers/specs/`, implementation plans in
`docs/superpowers/plans/`, and the per-phase execution ledger in
`.superpowers/sdd/<phase>/progress.md`.

**Before writing a plan for phase 3 or 4, read
[`docs/superpowers/PLAN-TEMPLATE.md`](docs/superpowers/PLAN-TEMPLATE.md).** It is
not boilerplate: phase 2 shipped seven defects that survived per-task TDD and
seven honest per-task reviews, because every one of them needed two runs or two
modules to see. The template's three rules — a named store invariant per task, a
two-run mutation matrix on the integration task, and reference code treated as
intent rather than paste-able — are the structural fix. Phase 3 is more exposed
than phase 2, not less.
