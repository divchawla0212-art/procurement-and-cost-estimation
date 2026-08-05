# BUG-005 — a missing FX rate must not be assumed — design

Status: accepted, 2026-08-05.
Tracker: [`BUGS_TRACKER.md`](../../../BUGS_TRACKER.md) § BUG-005.

`Project.fx_rates` defaults to `{}` and nothing requires it be filled in.
`normalize_bid` responds to an unconfigured currency by converting at 1.0
(`procurement/normalize.py:11`) and the bid is then ranked on that number:

```
EUROVEND  EUR 1000  ->  normalized_total = 1000.0   (sorted first, "cheapest")
USVEND    USD 1050  ->  normalized_total = 1050.0
```

At a true rate of 1.08 the EUR bid is $1080 — the *more* expensive of the two.
The comparison hands the buyer a reversed ranking with no error, no failed
status, and `extraction_status: ok` on both rows. That is CLAUDE.md's "missing
data is never coerced to a passing or zero value", with 1.0 as the one
multiplier that looks like a successful conversion.

The reported ask was narrower — seed a default EUR rate of 1.08. That is
included (§1.4), but it is a convenience, not the fix: seeding EUR leaves GBP,
JPY, INR and every other currency on the same 1.0 assumption.

A second defect surfaced while designing, and it constrains everything else.
FX rates are baked into the store **at ingestion time**. Measured, not inferred:

```
run 1 (no fx rates configured)          normalized_total = 1000.0
fx_rates set to {"EUR": 1.08}
run 2 (plain re-run, all cache hits)    normalized_total = 1000.0   <- no effect
run 3 (force=True)                      normalized_total = 1080.0
```

A fully-cached document `continue`s at `procurement/pipeline.py:811`, before
the `normalize_bid` call at line 935, so a plain re-run never re-normalizes.
Only `force=True` does, and per BUG-002 that "re-spends the full LLM cost of the
project". Fixing §1.1 without §1.2 would therefore trade a wrong number for a
blank one that costs money to fill in.

---

## 1. Decisions

### 1.1 An unconfigured currency is unconvertible, not 1:1

`normalize_bid` returns `normalized_total=None` and
`normalization_status="no_fx_rate"` when the bid's currency is foreign to the
target and has no usable rate. It does not convert, and it does not guess.

It returns at that point, with an **empty `adjustments` list**. The VAT,
freight and discount steps all operate on a converted figure, and a list of
adjustments leading to no total describes arithmetic that did not happen. The
existing `"no FX rate for EUR; assumed 1.0"` adjustment goes away with the
assumption that produced it — `normalization_status` now carries that meaning
in a form code can branch on, rather than prose a human has to read.

This follows a precedent already accepted in this codebase rather than
inventing one. `procurement/statement.py:252-260` already refuses to print a
normalized total from an unknown base, for exactly this reason — its comment
reads "a 0.00 beside a real bid reads as the cheapest offer". `normalize_bid`
is the layer that did not get the memo.

Nothing downstream needs a new sorting rule: `procurement/compare.py:21`
already sorts `normalized_total is None` rows last, and
`tests/test_procurement_compare.py:14` already exercises that path for failed
extractions.

**"No usable rate" includes a non-positive one.** `PUT /api/projects/{slug}/fx-rates`
validates that a rate is a *number* (`api/main.py:373-379`), not that it is
positive. A rate of `0` converts a bid to 0.0 and sorts it first — the same
defect in different clothing. So:

- the endpoint rejects a rate `<= 0` with 422, naming the currency, and
- a non-positive rate already in a store is treated as missing, not applied.

### 1.2 A rate entered after ingestion takes effect without a re-extraction

New `renormalize(root, slug)` recomputes every vendor's stored `normalized`
from their stored `commercial` facts and the project's current rates.

Currency conversion is arithmetic over a price that has already been
extracted. CLAUDE.md: "Arithmetic stays in Python. Extractors capture numbers
and units verbatim; the model reads, code decides." Re-running it needs no
document, no prompt and no provider — so it must not cost an LLM call.

- One `snapshots.transaction(root, slug)` wraps the whole pass, so
  `generation` bumps **once** for the operation, not once per vendor.
- **A pass that changes nothing opens no transaction and bumps nothing.**
  `transaction()` bumps `generation` on any body that completes, whether or not
  it wrote (`snapshots.py:38-39`), so the recompute happens first and the
  transaction opens only if some vendor's total actually moved. Without this
  the §1.5 read-path caller would advance `generation` on every page load.
- Every write goes through `snapshots.save_facts`. No hand-rolled writes.
- A vendor whose facts hold no `commercial` record is skipped, not zeroed.
- An `fx.renormalized` event records the run, with the count of vendors whose
  totals changed.

Ingestion keeps normalizing inline as it does today (`pipeline.py:935`); it
already has the current rates in hand and needs no second pass.

`PUT /api/projects/{slug}/fx-rates` calls it after saving the project, so
entering a rate updates the comparison in the same request.

### 1.3 The reason travels with the row

`normalization_status: str = "ok"` is added to `NormalizedBid` and carried onto
`ComparisonRow` by `build_comparison`. The default makes every snapshot
already on disk parse unchanged.

The currency is not duplicated onto the status — `ComparisonRow.currency` and
`VendorFacts.commercial` already carry it, and a second copy is a second thing
to keep in sync.

Consumers:

| surface | change |
|---|---|
| `procurement/compare.py` | carry the field onto the row; sorting already correct |
| `procurement/statement.py` | none expected — already gates on a present total; verify |
| `procurement/export.py` | carry the reason, so an exported sheet is not silently blank |
| `procurement/store/index.py` | none — already stores a `None` total |
| `web/` | render `EUR 1000 — no rate set` in place of an empty cell |

Distinguishing this from a failed extraction is the point: "we could not read
the document" and "we read it fine and have no rate" send the user to two
different screens.

### 1.4 The EUR default is a form suggestion, not stored data

The setup screen prefills one editable row — `EUR`, `1.08`, labelled with its
as-of date — when **both** hold:

- the project has no rates yet, and
- `target_currency` is `USD`.

1.08 is a EUR→USD rate. On a GBP- or INR-denominated project it is simply
wrong, and pre-filling it there would make it *less* likely to be questioned
than an empty field.

Nothing reaches `project.fx_rates` until the user saves. This matters beyond
tidiness: `Object.keys(setup.fx_rates).length > 0` is one of the four steps in
the setup checklist (`web/src/pages/Setup.tsx:151-157`), and `activeIndex` is
the first incomplete one. Seeding the store at creation would mark FX rates
done before anyone looked, retiring the very prompt that gets real rates
entered. A suggestion in the form leaves the step incomplete until a human
commits to it, and needs no new `Project` field to track that.

The rate is a dated snapshot, not a live quote. The label says so.

### 1.5 Existing stores are re-normalized once

Projects ingested before this change hold totals computed under the 1.0
assumption — including the sample project baked into the Docker image. §1.2
only fires when rates change, so a project sitting at no-rates would keep its
wrong numbers indefinitely.

A one-shot migration runs `renormalize` on first read for any project whose
`store_version` is behind `layout.STORE_VERSION`, then stamps it current.

`layout.STORE_VERSION` goes 1 → 2 and stays the single source of truth — it is
already what `migrate.migrate_dataset_json` writes into its marker
(`migrate.py:56`). The literal `2` appears in no other file.

Two corrections this needs, both found by reading rather than assuming:

- **`Project.store_version` is currently dormant** — declared at
  `models.py:85` and written or read by nothing. This design gives it its first
  real job, so `create_project` must start stamping new projects with
  `layout.STORE_VERSION`. Otherwise every newly created project reads as
  "needs migrating" forever, and `tests/test_store_models.py:40` — which
  asserts the default is 1 — becomes a statement about the wrong thing.
- **The migration must not bump `generation` when it changes nothing.** An
  empty or already-correct project runs the recompute, finds no change, opens
  no transaction, and only stamps the version. This is the §1.2 rule, and it is
  load-bearing here: this caller sits on the read path.

Keying completion on a stamp written **inside** the transaction body is
deliberate. `snapshots.transaction` is explicitly not a rollback: a body that
raises leaves its earlier writes on disk and only withholds the generation
bump (`procurement/store/snapshots.py:28-37`). A half-finished migration must
therefore be retried, not declared complete — the same reasoning
`migrate.migrate_dataset_json` documents for its own marker.

Unlike that migration, this one is **idempotent by construction**: it derives
every value from `commercial` facts it never modifies, so a retry after a
partial run recomputes the same answers and cannot lose data.

It hangs off `_live_fact_vendors` in `procurement/pipeline.py`, beside the
existing `migrate_dataset_json` call, which is already the read path for both
`load_dataset` and `has_results`.

---

## 2. Store invariants this touches

Named explicitly, per CLAUDE.md's planning convention:

- **Every write goes through `procurement/store/snapshots.py`.** `renormalize`
  and the §1.5 migration use `save_facts` inside `transaction`; neither writes
  a snapshot file directly.
- **`generation` bumps once per write transaction, never once per file.** One
  `transaction` wraps the whole re-normalization pass however many vendors it
  rewrites.
- **Missing data is never coerced to a passing or zero value.** This is the
  invariant the bug violates and §1.1 restores. `None`, never `0.0`.
- **A failed extraction never blanks previously-good stored data.**
  `renormalize` reads `commercial` and writes only `normalized`; a vendor with
  no commercial facts is skipped rather than written empty.
- **A stored collection contains exactly the records of its currently-live
  sources.** Re-normalization adds and removes no vendors, so orphan pruning is
  untouched — and a test asserts it stays that way.

---

## 3. Testing

The load-bearing test is a **two-run matrix**, which is the shape PLAN-TEMPLATE
requires on an integration task and the only shape that catches this defect:

| # | run | rates at the time | expected |
|---|---|---|---|
| 1 | ingest | none | EUR bid: total `None`, status `no_fx_rate` |
| 2 | set EUR 1.08 | — | total `1080.0`, **no `force`, no LLM call** |
| 3 | set EUR 0 | — | 422; stored totals unchanged |

Row 2 asserts on the mock client's call count, not just the number: the point
of §1.2 is that the update is free, and a test that only checks the total would
pass if it were secretly re-extracting. It also asserts `generation` advanced
exactly once.

Unit coverage alongside it:

- an unconvertible bid sorts last and keeps its reason through
  `build_comparison`, and carries no adjustments
- a non-positive stored rate is treated as missing, not applied
- a vendor with no commercial facts is skipped by `renormalize`, not zeroed
- the §1.5 migration is idempotent — running it twice changes nothing the
  second time — and a project already at the current version is not rewritten
- **reading a project repeatedly does not advance `generation`.** Two
  successive `load_dataset` calls on a migrated project leave it unchanged.
  This is the regression test for the read-path hazard in §1.5: without the
  no-change-no-transaction rule, `generation` climbs on every page load, and
  nothing else in the suite would notice
- web: the prefill appears only when rates are empty **and** target is USD, is
  not saved automatically, and the checklist step stays incomplete until it is

`tests/test_procurement_normalize.py::test_missing_fx_rate_is_surfaced`
currently asserts the 1:1 behaviour as intended
(`assert abs(n.normalized_total - 1000.0) <= 0.01  # assumed 1:1`). It is a
characterization test pinning the defect, so it is **rewritten**, not extended.
Expect it red before it is touched, and read it as the specification it
currently is.

---

## 4. Out of scope

- **Live FX rates from a provider.** The rate stays a human-entered, dated
  value. Fetching quotes is a different feature with a network dependency, and
  CI is key-free.
- **Multi-hop conversion** (EUR→GBP via USD). Rates are per-currency against
  the target, as today.
- **Re-normalizing on a `target_currency` change.** Changing the target is not
  reachable from the UI today; if it becomes reachable it needs the same
  treatment and should reuse `renormalize`.
