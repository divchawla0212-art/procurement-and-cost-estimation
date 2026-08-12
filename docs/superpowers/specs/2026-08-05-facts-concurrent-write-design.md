# BUG-010 — a run must not write back facts it did not change — design

Status: accepted, 2026-08-05.
Tracker: [`BUGS_TRACKER.md`](../../../BUGS_TRACKER.md) § BUG-010.

BUG-009 serialised `project.json`. `facts.json` has the same defect and none of
the guard. Every production writer spells out the load/modify/save pair at its
own call site, and one of them holds the loaded copy across an LLM extraction:

```python
# procurement/pipeline.py:833, then :942 — an extraction apart
prior_facts = snapshots.load_facts(root, slug, doc.vendor)
base = prior_facts or VendorFacts(vendor=doc.vendor)
...                                   # extract_bid / extract_tech_facts
snapshots.save_facts(root, slug, VendorFacts(
    ...,
    technical_feedback=base.technical_feedback))
```

Both carried fields are correct sequentially — the comment on
`technical_feedback` describes a real defect it fixes. Concurrently they are
how a write is lost: anything written to that vendor's facts during the
extraction is overwritten by `base`.

This needs no narrow window. The gap *is* the extraction, so "did it during the
run" is enough — which is exactly what a reviewer does. Measured 8/8 on Linux,
the deployment target.

Two corrections to the tracker's account, established by reading the code
while designing. Both narrow the defect without excusing it:

- `base` is re-loaded **per document** (`pipeline.py:833` is inside the
  per-document loop), not once per run. The window is one document's
  extraction, not the whole run.
- `project` genuinely is loaded once, at `pipeline.py:618`, so
  `project.fx_rates` at `pipeline.py:939` is the run-start rate for **every**
  document. This is the more dangerous half: it is the one that stores a
  plausible wrong number rather than a blank.

## 0. The writer set is closed

Every production writer of `facts.json`, and the gap each holds:

| site | writes | gap between its read and write |
|---|---|---|
| `pipeline.py:942` (extraction) | whole `VendorFacts` | **one document's LLM extraction** |
| `pipeline.py:243` (`_prune_orphan_facts`) | whole object | short |
| `renormalize.py:65` | whole object | the compute loop |
| `feedback.py:35` | whole object | short |
| `store/migrate.py:47` | whole object | one-shot, at migration |

There is no override-write endpoint — `overrides` is produced only by a run's
`reconcile`. So the concurrent writer set is exactly three: a run, a reviewer's
feedback, and `renormalize`. That closure is what makes an ownership table
writable rather than aspirational; if a fourth authored field arrives later,
this section is the thing to re-check.

## 1. Decisions

### 1.1 `update_facts` — one read-modify-write primitive, mirroring `update_project`

Add to `procurement/store/snapshots.py`:

```python
@contextmanager
def update_facts(root: str, slug: str, vendor: str, *, create: bool = False):
    """Load, mutate, save — atomically with respect to other mutators."""
```

Semantics, each one deliberate:

- **Per-`(slug, vendor)` lock**, keyed on `realpath(root)` + slug + vendor, the
  same shape `_project_lock` uses. Per vendor rather than per project: two
  vendors' facts are separate files with no cross-field rule between them, and
  a per-project lock would serialise a run against itself for no benefit.
- **The re-read happens inside the lock.** This is the whole point. A lock
  around only the save leaves the read outside it and fixes nothing — the same
  sentence `project.py:19-21` already carries.
- **Saves on a clean exit, leaves the file untouched if the body raises.** A
  failed mutation cannot half-write the document.
- **Never bumps `generation`.** See §1.4.
- **A plain `Lock`, not `RLock`.** Re-entering it from one thread would mean an
  inner update saving, then the outer saving its own older copy over the top —
  the exact lost write this exists to prevent, only harder to see. A plain
  `Lock` turns that mistake into a hang the suite catches instead of silent
  corruption. This reasoning is `project.py:23-27`'s, and it is repeated here
  rather than cross-referenced because the next person to add a nested writer
  will be reading this file.
- **`create`** distinguishes the two callers that differ today.
  `create=True` yields a fresh `VendorFacts(vendor=vendor)` when nothing is
  stored — the run's case, today's `prior_facts or VendorFacts(...)`.
  `create=False` (the default) raises `LookupError` — `save_feedback`'s case,
  preserving the error it already raises. It is a parameter rather than a
  pre-check at the call site because a pre-check outside the lock is a TOCTOU:
  the vendor could be pruned between the check and the acquire.

### 1.2 Field ownership: each writer touches only what it produces

| field | owner | the run's relationship |
|---|---|---|
| `commercial`, `technical`, `deviations`, `overrides`, `quotation_doc_id` | the run | produces |
| `technical_feedback` | the reviewer (`save_feedback`) | **never modifies — only carried today** |
| `normalized` | derived; written by both the run and `renormalize` | derives (see §1.3) |

| writer | writes | must not touch |
|---|---|---|
| run — extraction | `commercial`, `technical`, `deviations`, `overrides`, `quotation_doc_id`, `normalized` | `technical_feedback` |
| run — prune | `technical`, `deviations`, and — only when the stored quotation is no longer live — `commercial`, `normalized` and `quotation_doc_id`, all set to `None` together | `technical_feedback`, `overrides` (left for the next run to re-reconcile, `pipeline.py:239-242`) |
| `save_feedback` | `technical_feedback` | everything else |
| `renormalize` | `normalized` | everything else |

The carried-field defect disappears **structurally**, not by a merge rule: the
run mutates the object it re-read inside the lock and never assigns
`technical_feedback` at all, so there is nothing to carry and nothing to lose.
The comment at `pipeline.py:950-951` that explains the carry is deleted with the
carry — its sequential concern is now satisfied by not rebuilding the object.

This replaces `VendorFacts(...)` construction at `pipeline.py:942` with mutation
of the yielded object. The construction is what made the defect invisible: a
constructor demands a value for every field, so every field the run does not own
had to be sourced from somewhere, and `base` was the only place to get it.

### 1.3 `normalized` gets a freshness rule, not a merge rule

`normalized` is the one field two writers legitimately produce. It is *derived*,
not authored, so it needs no merge: it needs to be computed from current inputs.

**Rule: whoever writes `normalized` computes it inside the lock, from a project
re-read inside the lock.** Never from a copy loaded earlier.

For the run this means `pipeline.py:937-941`'s `project.target_currency` and
`project.fx_rates` — a copy from `pipeline.py:618`, possibly minutes stale —
are re-read per save. That is one extra JSON read per document, against an LLM
call; the cost is not worth discussing.

This makes the run and `renormalize` agree by construction: whichever lands
last computed from the same current state, so there is no last-writer-wins
hazard left to reason about.

**Convergence, since a rate can change mid-run and different documents then
compute at different rates.** The `PUT /fx-rates` route saves the rate and then
calls `renormalize`, which rewrites *every* vendor with stored `commercial`.
So for a rate changed at T:
- a vendor the run saved before T was computed at the old rate, and
  `renormalize` corrects it at T;
- a vendor the run saves after T re-reads the project and computes at the new
  rate.

Either way the store converges, and the invariant in §2 holds once both have
finished. The transient disagreement *during* a run is not new and not
observable as a wrong award: the comparison is read after the run completes.

### 1.4 `update_facts` never bumps `generation`

`generation` bumps once per write *transaction*, never once per file
(CLAUDE.md). `run_ingestion` wraps its entire extraction in a single
`snapshots.transaction` at `pipeline.py:706`, and the per-document save sits
inside it. If `update_facts` bumped, a 12-document run would bump 12 times and
break the invariant outright.

So `update_facts` is exactly as generation-silent as `save_facts` is today, and
`transaction` remains the only thing that bumps. Callers keep their existing
`transaction` wrappers unchanged.

### 1.5 Lock ordering: the project lock is never taken while a facts lock is held

Two locks now exist. The rule that keeps them from deadlocking:

> Never acquire the project lock while holding a facts lock.

This holds today without any code moving, and the reason is worth recording
because it is load-bearing and non-obvious:

- `transaction` **yields first and bumps at exit** (`snapshots.py:40-41`), so no
  project lock is held during the body that writes facts. `renormalize` and
  `save_feedback` both write facts inside a `transaction` body; both are safe.
- The one `update_project` in a run (`pipeline.py:1004`) runs *after* the
  transaction closes, and its body writes only `status`.
- No `update_project` body anywhere writes facts.

The plan must not reorder `transaction`'s bump to the front, and must not add a
`bump_generation` call inside an `update_facts` body. Both would create the
cycle.

### 1.6 `save_facts` stays, and says what it is for

`save_facts` remains — `store/migrate.py` writes a fresh object per vendor, and
roughly thirty tests construct facts directly. It is not deprecated or removed.

Its docstring gains the sentence `project.py:47-49` already carries for
`save_project`: a plain `save_facts` is right for a first write of facts that do
not exist yet; what must not come back is the load/save pair spelled out at a
call site — **that pair is the defect**. This is the only defence against a
sixth writer reintroducing BUG-010, and it is a comment, so it is a weak one;
§3's reverse-direction test is the one that actually catches a regression.

### 1.7 All five production writers convert

Including the three whose windows are short. A short window is still a window —
`save_feedback` and `renormalize` hold their own load/modify/save spans, and a
settings write clobbering what a run just extracted is the same defect from the
other side. The tracker records that direction as **never measured**; §3
measures it.

`store/migrate.py:47` converts too, though it is a one-shot: it runs from the
read path via `migrate_normalization`, which means two concurrent page loads can
enter it, and "one-shot" describes intent rather than a guarantee.

## 2. Store invariants this touches

- **A run writes back only what it produced.** After a run's per-document save,
  every field the run does not own holds the value stored at the moment of the
  save — not the value read before the extraction began. `technical_feedback`
  is the field this is measured on, because it is the one the run never
  modifies at all.
- **Stored `normalized` reflects the project's current settings.**
  `renormalize`'s existing invariant, extended to the run: after either
  completes, every vendor with stored `commercial` has a stored `normalized`
  exactly equal to `normalize_bid(that commercial, the project's current
  target_currency and fx_rates)`.
- **`generation` bumps once per write transaction, never once per file** —
  unchanged, and §1.4 is what preserves it.
- **A failed extraction never blanks previously-good stored data** — unchanged.
  The per-branch guards at `pipeline.py:850-885` are untouched; this design
  changes where the object comes from, not which branch writes what.
- **Missing data is never coerced to a passing or zero value** — unchanged, and
  §3's vacuity guard is what keeps the tests honest about it.

## 3. Testing

New file `tests/test_facts_concurrency.py`, beside `tests/test_project_concurrency.py`.
No forcing hook is needed for the run-side rows: the window is the extraction,
wide enough to hit by issuing the write during a run with latency injected into
`classify_structure`. The short-window rows do need one, the way BUG-009's tests
did.

Rows, each named for the invariant it defends:

| row | mutation | assert |
|---|---|---|
| H1 | `PUT /vendors/K/feedback` during a run | the note survives the run's saves |
| H2 | `PUT /fx-rates` (no prior rate) during a run | no vendor's stored `normalized` is stale against the project's stored rate |
| H2-severity | a rate that already exists is **corrected** (1.08 → 1.15) mid-run | stored total is 1150, not 1080 — a wrong *number*, not a blank, is what makes this S1 |
| reverse | a run finishes while a feedback write is in flight | the extraction result survives; the note survives |
| reverse-fx | `renormalize` and a run's save race | the invariant in §2 holds afterwards |
| generation | a run with N documents | `generation` advances by exactly 1 (§1.4's regression guard) |
| absent vendor | `save_feedback` for a vendor with no stored facts | still raises `LookupError`, and writes nothing |
| nested | `update_facts` re-entered for one vendor on one thread | hangs/fails rather than silently losing a write (§1.1) |

**Vacuity guard — mandatory, and the reason is on the record.** H2 first measured
0/8 and that result was worthless: the stock mock returns `{}` for every schema,
so commercial facts came back `currency=""`, `base_price=0.0`, and
`normalize_bid` yields `0.0` whatever the rate is. The fixture could not have
detected the defect it was written to find. Every rate-sensitive row must assert
its fixture holds at least one vendor with a non-zero, rate-sensitive price
**before** asserting the outcome, and fail loudly if not. A concurrency probe
that cannot fail is indistinguishable from one that passes.

**Mutation verification.** Each row's defect is reinstated one at a time and
confirmed to fail only its intended row: restore the `technical_feedback` carry;
restore `project.fx_rates` from the run-start copy; move the re-read outside the
lock; bump `generation` inside `update_facts`. A row that still passes with its
defect reinstated is not testing what it claims.

**Baselines.** Re-measure the workstation row and derive the CI row from it
(`workstation − 4 corpus-coverage − 3 data/`). Do not edit the two rows
independently.

## 4. Out of scope

- **Cross-process locking.** These are in-process `threading.Lock`s, so a
  second uvicorn worker reintroduces both BUG-009 and BUG-010. This matches the
  existing BUG-009 fix rather than diverging from it. Stated here rather than
  hidden: if multi-worker deployment is ever considered, it needs its own
  tracker entry and a real file lock — this design does not pretend to cover it.
- **`documents.json`, `requirements.json`, `compliance.json`.** Each is written
  wholesale by a run inside the same transaction, and no other writer exists for
  them today. Not audited here; if one is added, it inherits this problem.
- **Rollback.** `transaction` is not a rollback and does not become one. A body
  that raises half way still leaves earlier files written and only withholds the
  bump (`snapshots.py:30-38`). Unchanged.
- **The severity variant's UI.** Nothing warns a reader that a comparison was
  read mid-run. Out of scope; the fix makes the stored value correct, which is
  the S1.
