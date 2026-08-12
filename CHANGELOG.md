# Changelog

What has changed, newest first. Bug IDs are the ones in
[`BUGS_TRACKER.md`](BUGS_TRACKER.md) — that file carries the reproduction, the
severity and the spec/plan links; this one is the short version.

## Unreleased

Everything below is on `fix-tracked-bugs-001-004` and not yet in `main`.

### Fixed

- **BUG-010** (S1) — a run held a vendor's `facts.json` across its whole LLM
  extraction and saved it back over anything written meanwhile: a reviewer's
  note, or a corrected FX rate's totals. Every writer now goes through
  `snapshots.update_facts`, which re-reads under a per-vendor lock and writes
  only the fields it owns; the lock never spans an LLM call.
  `1a6bc60`, `797bb19`, `639af24`, `867bc7b`, `8ee8890`, `9ec3043`
- **BUG-005** (S1) — a currency with no configured FX rate was converted at
  1.0, so a EUR bid was ranked as if €1 = $1 and a close two-vendor ranking
  inverted. `normalize_bid` now refuses to convert without a usable rate and
  omits `normalized_total` instead of emitting a plausible wrong number; the
  reason travels to the comparison, the statement and the exports.
  `3dc1e5f`, `6ba4338`, `9879106`, `2ee8309`, `cdca14c`
- **BUG-004** (S1) — with no `LLM_PROVIDER` set, ingestion silently fell back
  to the **mock** provider and stored fabricated facts as `ok`. There is no
  implicit default any more: an unconfigured `POST /ingest` returns 400 and
  leaves the store untouched, and `mock` is reachable only when named.
  `dfc3f37`, `9d3ba95`, `cddbea1`
- **BUG-009** (S3) — every `project.json` writer was an unguarded
  read-modify-write, so a `PUT /fx-rates` that returned 200 was discarded by a
  run finishing in the same window. All mutations are serialised now.
  `d2bfefe`
- **BUG-008** (S3) — nothing serialised two ingestion runs, and
  `atomic_write_json` shared one fixed `.tmp` name, so concurrent writers
  interleaved into a single snapshot. Runs are now one-per-project (409 on the
  second) and the temp name is per-writer. `5080a2c`, `3d4d5f7`
- **BUG-002** (S3) — `run_ingestion(force=True)` existed but was unreachable
  from the API and the UI. `f095220`, `7f066e4`
- **BUG-001** (S3) — the compliance matrix and comparative statement were
  reachable before ingestion had produced anything; both are now gated on
  `has_results`, with `status` driving only the banners. `88f0403`, `d0f5abb`
- **BUG-003** (S4) — empty states still told the user to run ingestion "in the
  Streamlit portal", which no longer exists. `d3cfa61`

### Added

- **Force full re-extraction** — accepted in the `POST /ingest` payload
  (default `false`) and exposed as a separate, confirm-gated button that
  appears only once a project has results. `f095220`
- **`renormalize`** — re-applies changed FX rates to stored facts with no LLM
  call and a single generation bump, plus a one-shot store v1→v2 migration
  (`migrate_normalization`) that backfills normalization status.
  `587227e`, `cb8e673`
- **A dated FX suggestion** — setup prefills `EUR 1.08` labelled "as of
  2026-08-05 — check before saving" when no rate is set and the target is USD.
  Nothing is stored until a human clicks Save, so the setup checklist step
  stays live. `682f8e1`
- **`run.started` provenance** — the event now names the client class, so a
  deliberate `MockLLMClient` run is visible in the log. `dfc3f37`
- **`snapshots.update_facts`** — a locked read-modify-write for `facts.json`,
  the primitive BUG-010 was fixed with, plus the bare `facts_lock` for the one
  writer whose write is conditional on the facts being absent. `1a6bc60`
- **A front-end test suite** — non-watching vitest + testing-library, wired
  into CI as its own job. `042e8fa`, `363ed3d`
- **Docker: a bundled sample project** — the image ships `gas-14`, an ingested
  three-vendor project, copied into the store volume on start and never
  overwriting an existing project of the same slug. Also a movable host port
  (`PROCUREMENT_HOST_PORT`) for hosts where WinNAT has reserved 8000.
  `c1c7fd2`

### Changed

- `has_results` is answered without building a full comparison. `1e47c35`

### Removed

- The Streamlit portal, keeping its one write. The web app is the single front
  end. `028e33f`

### Still open

- **BUG-007** (S3) — a partially-failed run offers only project-wide retry,
  never one document or one vendor. Blocked on nothing now that BUG-008 has
  shipped, but it inherits BUG-006's synchronous-vs-background decision.
- **BUG-006** (S3) — extraction reports no live progress: a 12-minute run shows
  one static banner while the per-document events that would fill it are
  already on disk.
