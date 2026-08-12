# CLAUDE.md

Two Python packages over one shared LLM layer:

- **`procurement/`** — the tender comparison pipeline. Ingests a ZIP of vendor
  folders, classifies each document, resolves revision lineage, routes each
  document to a per-class extractor, and stores the result per vendor.
- **`cost_estimation/`** — the older costing-sheet ingestion CLI (`cost-est`).
- **`shared/llm/`** — provider clients behind one `classify_structure` interface,
  plus the versioned prompt files in `shared/llm/prompts/`.
- **`api/main.py` + `web/`** — FastAPI and the React SPA it serves: the single
  front end, covering setup, ingestion and review.

## Running things

```bash
python -m pytest
```

Run from the repo root. Tests are key-free — they use `shared/llm/mock_client.py`,
and no test may require `ANTHROPIC_API_KEY`.

There are **two** baselines, and both are correct — they differ only in which
untracked fixture directories are present, never in pass/fail:

| where | baseline |
|---|---|
| a developer workstation, `data/` and an ingested multi-vendor `projects/` present | **958 passed, 3 skipped, 0 failed** |
| CI, and any clean checkout | **951 passed, 10 skipped, 0 failed** |

Anything else is a real regression.

CI being green is not luck. With no `projects/` all four
`test_real_corpus_coverage.py` tests skip on their module-level guard, and the
three tests guarded on the untracked `data/` sample directory skip too. The
three skips a workstation already shows are credential guards
(`test_anthropic_client.py`, `test_bedrock_client.py`,
`test_procurement_real_data.py`) and skip in both places.

So the CI row is the workstation row with the four corpus-coverage passes and
the three `data/` passes turned into skips — `951 = 958 - 4 - 3`,
`10 = 3 + 4 + 3`; 961 tests either way. When the counts move, measure the
workstation row and derive the CI row from it; editing the two rows
independently is how they drift apart.

**These numbers ran the derivation backwards, because they had to.** The
checkout that produced them had neither `data/` nor a fixture-bearing
`projects/`, so the workstation row was not measurable there — only the
clean/CI row was, at **951 passed, 10 skipped**. The 958/3 workstation figure
above was *derived* from that measurement by adding the same 7 back
(`958 = 951 + 4 + 3`, `3 = 10 - 4 - 3`), the reverse of the normal direction.
The arithmetic reconciles either way, since it's the same equation read
backwards, but reconciling is not the same as measuring: **958/3 is an
assumption that the four corpus-coverage tests and the three `data/`-guarded
tests would all still pass on a fixture-bearing checkout, not a confirmed
result.** The 951/10 row is the one that was actually run, and the one to
trust without qualification. If you have `data/` and a multi-vendor
`projects/` and get something other than 958/3, that is not necessarily a
regression — it may just mean the derived row was wrong and this note was
overdue for a real measurement; re-run here and update both rows from *that*
one, the normal way described above.

**There is no longer a workstation-only failure.** Until the Streamlit portal
was removed, `tests/test_portal_app.py::test_missing_api_key_does_not_block_creation`
failed for anyone with a populated `.env`, because `portal/app.py` called
`load_dotenv()` and repopulated the key that the test had just deleted. That
test went with the portal, so a populated `.env` no longer changes the count. A
red workstation run is now always a real regression — do not go looking for the
old excuse.

The workstation row also depends on what your untracked `projects/` holds:
`test_real_corpus_coverage.py` asserts against the newest store there with more
than one vendor, so it is a coverage instrument for the live corpus, not a
fixture-backed unit test. **It holds at shipped defaults** — measured on
`projects/phase4c-shipped-defaults`, ingested with no `LLM_MAX_TOKENS` and no
chunk-budget override. Both extractors that read a whole document split their
input on line boundaries (`procurement/chunking.py`, budgeted by
`TECH_CHUNK_CHARS` and `REQUIREMENTS_CHUNK_CHARS`) and merge all-or-nothing, so
a quotation-only vendor's 220k-character proposal no longer overruns the 8192
output ceiling and lands its vendor back at zero facts. If a floor goes red,
chunk further or fix the routing — never lower the floor, and never green it
with an environment override, which makes the instrument assert something
weaker than the sentence it reads as.

CI runs that same command on every pull request into `main`, via
[`.github/workflows/tests.yml`](.github/workflows/tests.yml) — Ubuntu, Python
3.12, no provider secrets. Keep it that way: a test that needs a key belongs
behind a skip guard, not behind a repository secret.

The app runs via the `procurement-api` and `enterprise-web` entries in
`.claude/launch.json` — use the preview tooling, not bare `uvicorn` / `npm run
dev`.

## Store invariants — violating these corrupts award decisions

There are **two** authoritative stores, with separate rules. For project data:
the snapshots under `projects/<slug>/store/`; `index/store.db` is derived and
disposable. For accounts: `<ROOT>/auth.json` — see the section after this one.

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

## Auth invariants — `<ROOT>/auth.json`

Users, sessions and grants live in one document so a single lock and a single
atomic write keep all three consistent. Written **only** from
`api/auth/store.py`; routes never touch the file.

- Every write is a read-modify-write inside `locked_update`. **And so is every
  decision that gates one.** A check made in the caller and a write made in
  the store are two critical sections, not one: two admins deleting each other
  concurrently each read "two admins, fine" and both writes land on zero
  admins. Both guards this subsystem shipped wrong were *reads* outside the
  lock, not writes — `store.delete_user` and `store.grant` show the shape.
- `grants` holds exactly the grants whose user still exists. `delete_user`
  cascades to grants and sessions in the same write. (A grant whose *project*
  is gone is deliberately tolerated and inert — `list_projects` filters
  against disk.)
- Sessions persist `sha256(token)` only. `password_hash` is never a field on
  `User`, so it cannot reach a response body; `store.password_hash_for` is the
  one reader of the digest.
- Roles are `admin` and `reviewer`, and **no route changes a role** — it is
  set once at creation. Signup always creates a reviewer.
- A project slug is matched against `list_projects` (which reads `os.listdir`),
  never by asking the filesystem whether a path exists. Windows and macOS
  resolve paths case-insensitively; CI does not, so that class of bug cannot
  fail on CI.

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
