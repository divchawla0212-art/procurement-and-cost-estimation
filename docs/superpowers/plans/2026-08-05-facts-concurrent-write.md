# BUG-010 — facts.json concurrent writes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** No writer of `facts.json` may write back a field it read before another writer could have changed it.

**Architecture:** One new primitive — `snapshots.update_facts`, a per-`(slug, vendor)` locked read-modify-write context manager mirroring `project.update_project` from BUG-009. Every production writer converts to it and mutates only the fields it owns. The one contended field, `normalized`, is derived rather than authored, so it gets a freshness rule instead of a merge rule: whoever writes it computes it inside the lock from a project re-read there.

**Tech Stack:** Python 3.12, `threading.Lock`, pydantic models, pytest.

Spec: [`docs/superpowers/specs/2026-08-05-facts-concurrent-write-design.md`](../specs/2026-08-05-facts-concurrent-write-design.md).
Tracker: [`BUGS_TRACKER.md`](../../../BUGS_TRACKER.md) § BUG-010.

## Global Constraints

Every task's requirements implicitly include these. Each is copied from the spec.

- **`update_facts` never bumps `generation`.** `generation` bumps once per write transaction, never once per file. `run_ingestion` wraps its whole extraction in one `transaction` (`pipeline.py:706`); a bump per file would make a 12-document run bump 12 times.
- **Never acquire the project lock while holding a facts lock.** This holds today only because `transaction` yields first and bumps at exit (`snapshots.py:40-41`). Do not reorder that bump, and do not call `bump_generation` or `update_project` inside an `update_facts` body.
- **A plain `Lock`, never an `RLock`.** Re-entry would mean an inner update saving and the outer then saving its own older copy over the top — the exact lost write this prevents. A plain `Lock` turns that into a hang the suite catches.
- **Missing data is never coerced to a passing or zero value.** An unfound parameter is omitted, not emitted as `0` or `""`.
- **A failed extraction never blanks previously-good stored data.** The per-branch guards at `pipeline.py:850-885` keep their exact semantics through the restructure.
- **Every concurrency test carries a vacuity guard.** BUG-010's H2 first measured 0/8 and that result was worthless: the stock mock returns `{}`, so `base_price=0.0` and `0.0 × any_rate = 0.0`. A rate-sensitive test must assert its fixture holds a non-zero, rate-sensitive price *before* asserting the outcome.
- **Locks are in-process.** A second uvicorn worker reintroduces this bug. Out of scope, matching BUG-009; do not add a file lock in this plan.

## File Structure

| file | responsibility after this plan |
|---|---|
| `procurement/store/snapshots.py` | owns `facts_lock` and `update_facts`; `save_facts` stays for first writes |
| `procurement/feedback.py` | writes `technical_feedback` only |
| `procurement/renormalize.py` | writes `normalized` only |
| `procurement/pipeline.py` | extraction save and prune save both mutate under the lock |
| `procurement/store/migrate.py` | conditional first write, under `facts_lock` |
| `tests/test_facts_concurrency.py` | new — the matrix, beside `tests/test_project_concurrency.py` |

## Test helpers the tasks assume

These are named in the test code below and do not exist yet. Prefer the
equivalent already in the target test file over writing a new one — several of
these files have a local project fixture whose name differs.

| helper | task | what it must do |
|---|---|---|
| `_project(root, slug, *, target_currency, fx_rates)` | 3 | create a project with those settings; `tests/test_renormalize.py` already has one — use it |
| `_project_with_one_vendor_doc(tmp_path, monkeypatch)` | 4 | a project, one vendor, one quotation document on disk, mock provider; returns `(root, slug)` |
| `_project_with_two_docs(tmp_path, monkeypatch)` | 5 | as above with two documents, so deleting one leaves a live vendor |
| `_set_rates(root, slug, rates)` | 4 | write `fx_rates` through `update_project`, the way `PUT /fx-rates` does — not a raw `save_project`, or the test bypasses the code path it is about |
| `_make_the_mock_quote_eur_1000(monkeypatch)` | 4, 6 | patch the mock client so the quotation schema returns `currency="EUR"`, `base_price=1000.0`. **Mandatory** — the stock mock returns `{}`, and `0.0 × any_rate = 0.0` |
| `_delete_one_source_document(root, slug)` | 5 | remove one vendor file from disk; returns its `doc_id` |
| `_DELETED_DOC` | 5 | the `doc_id` the helper above removed |
| `_legacy_dataset_json(root, slug, *, vendor, base_price)` | 5 | write a pre-store `dataset.json` with one bid and no migration marker; `tests/test_store_migrate.py` has the shape already |

---

### Task 1: `facts_lock` and `update_facts`

**Files:**
- Modify: `procurement/store/snapshots.py`
- Test: `tests/test_store_snapshots.py`

**Interfaces:**
- Consumes: `load_facts`, `save_facts`, `VendorFacts`
- Produces:
  - `facts_lock(root: str, slug: str, vendor: str)` — context manager, yields nothing
  - `update_facts(root: str, slug: str, vendor: str, *, create: bool = False)` — context manager, yields `VendorFacts`
- **Store invariant owned:** a write made through `update_facts` contains, for every field the body did not assign, the value stored at the moment the lock was acquired — never a value read before it.

`facts_lock` is public because `migrate.py` needs the lock without the read-modify-write: its rule is "write only if this vendor has no facts yet", a conditional *first* write, and expressing that through `update_facts` would change the skip rule. Task 5 uses it. Nothing else should.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_store_snapshots.py
import threading
import pytest
from procurement.store import snapshots
from procurement.store.models import VendorFacts


def test_update_facts_writes_only_what_the_body_assigned(tmp_path):
    """A field the body never touches keeps the value stored when the lock was
    taken -- this is the invariant BUG-010 broke."""
    root = str(tmp_path)
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="K", commercial={"vendor": "K", "base_price": 100.0},
        technical_feedback="a reviewer's note"))

    with snapshots.update_facts(root, "p", "K") as facts:
        facts.commercial = {"vendor": "K", "base_price": 200.0}

    stored = snapshots.load_facts(root, "p", "K")
    assert stored.commercial["base_price"] == 200.0
    assert stored.technical_feedback == "a reviewer's note"


def test_update_facts_re_reads_inside_the_lock(tmp_path):
    """Two threads, each mutating a different field. Without the re-read
    happening inside the lock, the slower one writes the other's field back to
    its pre-lock value."""
    root = str(tmp_path)
    snapshots.save_facts(root, "p", VendorFacts(vendor="K"))
    start = threading.Barrier(2)

    def set_feedback():
        start.wait()
        with snapshots.update_facts(root, "p", "K") as f:
            f.technical_feedback = "note"

    def set_commercial():
        start.wait()
        with snapshots.update_facts(root, "p", "K") as f:
            f.commercial = {"vendor": "K", "base_price": 5.0}

    threads = [threading.Thread(target=set_feedback),
               threading.Thread(target=set_commercial)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stored = snapshots.load_facts(root, "p", "K")
    assert stored.technical_feedback == "note"
    assert stored.commercial["base_price"] == 5.0


def test_update_facts_refuses_an_unknown_vendor_by_default(tmp_path):
    root = str(tmp_path)
    with pytest.raises(LookupError):
        with snapshots.update_facts(root, "p", "NOBODY"):
            pass
    assert snapshots.load_facts(root, "p", "NOBODY") is None


def test_update_facts_creates_when_asked(tmp_path):
    root = str(tmp_path)
    with snapshots.update_facts(root, "p", "K", create=True) as facts:
        facts.commercial = {"vendor": "K", "base_price": 1.0}
    assert snapshots.load_facts(root, "p", "K").commercial["base_price"] == 1.0


def test_a_raising_body_writes_nothing(tmp_path):
    root = str(tmp_path)
    snapshots.save_facts(root, "p", VendorFacts(vendor="K",
                                                technical_feedback="before"))
    with pytest.raises(RuntimeError):
        with snapshots.update_facts(root, "p", "K") as facts:
            facts.technical_feedback = "after"
            raise RuntimeError("boom")
    assert snapshots.load_facts(root, "p", "K").technical_feedback == "before"


def test_update_facts_does_not_bump_generation(tmp_path):
    """generation bumps once per transaction, never once per file."""
    root = str(tmp_path)
    snapshots.save_facts(root, "p", VendorFacts(vendor="K"))
    before = snapshots.get_generation(root, "p")
    for i in range(3):
        with snapshots.update_facts(root, "p", "K") as facts:
            facts.quotation_doc_id = f"d{i}"
    assert snapshots.get_generation(root, "p") == before


def test_two_vendors_do_not_serialise_against_each_other(tmp_path):
    """The lock is per vendor, not per project: a run must not block itself."""
    root = str(tmp_path)
    for v in ("A", "B"):
        snapshots.save_facts(root, "p", VendorFacts(vendor=v))
    both_inside = threading.Barrier(2, timeout=5)

    def hold(vendor):
        with snapshots.update_facts(root, "p", vendor) as f:
            both_inside.wait()          # deadlocks if one lock covers both
            f.quotation_doc_id = vendor

    threads = [threading.Thread(target=hold, args=(v,)) for v in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert all(not t.is_alive() for t in threads)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_store_snapshots.py -v`
Expected: FAIL — `AttributeError: module 'procurement.store.snapshots' has no attribute 'update_facts'`.

- [ ] **Step 3: Write the implementation**

> **Reference code below is intent, not paste-able.** It shows the shape and the reasoning, and it has not been executed. Read it, then write the implementation against the test.
>
> **Load-bearing:** the re-read happening *inside* the lock; the plain `Lock`; `save` only on a clean exit; no `generation` bump; the lock key including `realpath(root)`. **Illustrative:** the dict-of-locks structure and the exact key string — mirror whatever `project.py:32-35` does today.

```python
# procurement/store/snapshots.py
import threading

# Per-vendor read-modify-write serialisation (BUG-010). facts.json had
# BUG-009's defect and none of its guard: pipeline.py held a vendor's facts
# across an entire LLM extraction and then wrote them back, discarding a
# reviewer's note or a corrected rate's totals written meanwhile.
#
# Per vendor, not per project: two vendors' facts are separate files with no
# cross-field rule between them, and a per-project lock would serialise a run
# against itself for nothing.
#
# A plain Lock, not an RLock, for project.py:23-27's reason -- re-entering it
# would mean an inner update saving and the outer then saving its own older
# copy over the top, which is the lost write this exists to prevent, only
# harder to see. Nothing nests today; keep it that way.
_FACTS_LOCKS: dict[str, threading.Lock] = {}
_FACTS_LOCKS_GUARD = threading.Lock()


def _facts_lock_for(root: str, slug: str, vendor: str) -> threading.Lock:
    key = os.path.join(os.path.realpath(root), slug, vendor)
    with _FACTS_LOCKS_GUARD:
        return _FACTS_LOCKS.setdefault(key, threading.Lock())


@contextmanager
def facts_lock(root: str, slug: str, vendor: str):
    """Serialise against other writers of this vendor's facts.

    For the one caller whose write is conditional on the facts being absent
    (`migrate.migrate_dataset_json`) and so cannot express itself as a
    read-modify-write. Every other writer wants `update_facts`.
    """
    with _facts_lock_for(root, slug, vendor):
        yield


@contextmanager
def update_facts(root: str, slug: str, vendor: str, *, create: bool = False):
    """Load, mutate, save - atomically with respect to other mutators.

    Mutate the yielded object and assign ONLY the fields your caller owns:
    every field you leave alone keeps whatever is stored now, which is the
    point. Saved on a clean exit, left untouched if the body raises.

    `create=True` yields a fresh VendorFacts when none is stored; the default
    raises LookupError. It is a parameter rather than a check at the call site
    because a check outside the lock is a TOCTOU - the vendor can be pruned
    between the check and the acquire.

    Does NOT bump `generation`: that is `transaction`'s job, once per write
    transaction rather than once per file. Do not call `transaction`,
    `bump_generation` or `update_project` from inside this body - the project
    lock must never be taken while a facts lock is held.
    """
    with _facts_lock_for(root, slug, vendor):
        facts = load_facts(root, slug, vendor)
        if facts is None:
            if not create:
                raise LookupError(f"no facts stored for {vendor}")
            facts = VendorFacts(vendor=vendor)
        yield facts
        save_facts(root, slug, facts)
```

Also extend `save_facts`'s docstring:

```python
def save_facts(root: str, slug: str, facts: VendorFacts) -> None:
    """Write a vendor's facts wholesale.

    Right for a first write, and for tests that construct facts directly. What
    must not come back is the load/modify/save pair spelled out at a call site
    - that pair is BUG-010, and `update_facts` is the replacement.
    """
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_store_snapshots.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add procurement/store/snapshots.py tests/test_store_snapshots.py
git commit -m "feat(store): update_facts, a locked read-modify-write for facts.json (BUG-010)"
```

---

### Task 2: `save_feedback` writes only `technical_feedback`

**Files:**
- Modify: `procurement/feedback.py:30-35`
- Test: `tests/test_statement_feedback.py`

**Interfaces:**
- Consumes: `snapshots.update_facts` (Task 1)
- Produces: no signature change — `save_feedback(root, slug, vendor, text, reason, actor="api") -> None`
- **Store invariant owned:** `technical_feedback` is the only field `save_feedback` changes; a vendor's `commercial`, `normalized`, `technical`, `deviations`, `overrides` and `quotation_doc_id` are byte-identical across it.

The smallest conversion, done first because it fixes the reverse direction of H1 and establishes the pattern the next three tasks follow. Today's `load_facts` → mutate → `save_facts` (`feedback.py:30-35`) is a short window, but a short window is still a window.

The `LookupError` for an unknown vendor moves from an explicit `if facts is None` into `update_facts`'s `create=False` default. Both refusals must still raise **before** the transaction opens, so a rejected note leaves the store untouched rather than rolled back — `transaction` is not a rollback.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_statement_feedback.py
def test_saving_feedback_leaves_every_other_field_untouched(tmp_path):
    root = str(tmp_path)
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="K",
        commercial={"vendor": "K", "currency": "EUR", "base_price": 1000.0},
        normalized={"vendor": "K", "normalized_total": 1080.0},
        technical=[{"doc_id": "d1", "parameter": "flow"}],
        quotation_doc_id="d1"))
    before = snapshots.load_facts(root, "p", "K").model_dump()

    save_feedback(root, "p", "K", "not compliant", reason="site visit")

    after = snapshots.load_facts(root, "p", "K").model_dump()
    assert after.pop("technical_feedback") == "not compliant"
    before.pop("technical_feedback")
    assert after == before


def test_feedback_for_an_unknown_vendor_still_raises_and_writes_nothing(tmp_path):
    root = str(tmp_path)
    generation_before = snapshots.get_generation(root, "p")
    with pytest.raises(LookupError):
        save_feedback(root, "p", "NOBODY", "x", reason="y")
    assert snapshots.load_facts(root, "p", "NOBODY") is None
    assert snapshots.get_generation(root, "p") == generation_before
```

- [ ] **Step 2: Run to verify the first fails**

Run: `python -m pytest tests/test_statement_feedback.py -v`
Expected: the untouched-fields test FAILS only if a field diverges. It may pass against today's code, because `save_feedback` reads and writes back the whole object in one short span — it is a **regression guard**, not red/green proof. The red/green proof for this task is the concurrent row in Task 6. Record that honestly rather than claiming a red run you did not get.

- [ ] **Step 3: Write the implementation**

> **Reference code below is intent, not paste-able.** **Load-bearing:** both refusals raising before `transaction` opens, and `technical_feedback` being the only assignment. **Illustrative:** everything else — the event append is unchanged from today.

```python
# procurement/feedback.py
def save_feedback(root: str, slug: str, vendor: str, text: str, reason: str,
                  actor: str = "api") -> None:
    if not (reason or "").strip():
        raise ValueError("a feedback note needs a reason")
    with snapshots.transaction(root, slug):
        # Only this field. Everything else is the run's or renormalize's, and
        # writing it back from a copy read a moment ago is BUG-010.
        with snapshots.update_facts(root, slug, vendor) as facts:
            facts.technical_feedback = text
        events.append_event(root, slug, Event(
            at=_now(), run_id=actor, actor=actor,
            action="facts.feedback_edited", target=vendor,
            detail={"reason": reason.strip(), "text": text}))
```

**Watch the refusal ordering.** Today the `LookupError` is raised before `transaction` opens. In the shape above it is raised *inside* the transaction, by `update_facts`. That is still correct — the body raises, so no bump happens and no file is written — but the docstring at `feedback.py:21-23` says "both refusals raise before the transaction opens" and would become false. Either keep that sentence true by probing existence first (rejected: TOCTOU) or **update the docstring to say the note refusal raises inside the transaction and is safe because the bump is withheld**. Take the second; the test above asserts the generation is unchanged.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_statement_feedback.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add procurement/feedback.py tests/test_statement_feedback.py
git commit -m "fix(feedback): write only technical_feedback, under the vendor lock (BUG-010)"
```

---

### Task 3: `renormalize` writes only `normalized`, computed inside the lock

**Files:**
- Modify: `procurement/renormalize.py:42-70`
- Test: `tests/test_renormalize.py`

**Interfaces:**
- Consumes: `snapshots.update_facts` (Task 1)
- Produces: no signature change — `renormalize(root, slug) -> int`, returning the number of vendors whose stored `normalized` changed
- **Store invariant owned:** after `renormalize` returns, every vendor with stored `commercial` facts has a stored `normalized` exactly equal to `normalize_bid(those facts, the project's current target_currency and fx_rates)`, and no other field of any vendor changed.

Two properties of today's code must survive, and they pull in opposite directions:

1. **No transaction when nothing changed.** `renormalize` is called from the read path via `migrate_normalization`, so opening a transaction unconditionally would advance `generation` on every page load, forever. That is why the compute pass runs first (`renormalize.py:44-47`). Task 4 of the FX plan exists entirely because of this.
2. **The value written must be computed inside the lock**, from facts re-read there — otherwise `renormalize` has BUG-010 itself.

Resolution: keep the pre-pass, but treat its result as a **candidate list**, not as the value to write. Recompute inside the lock and count only what actually changed. A vendor whose candidacy evaporates between the pre-pass and the lock (a run stored a fresh `normalized` meanwhile) is simply not counted.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_renormalize.py
def test_renormalize_writes_only_normalized(tmp_path):
    root = str(tmp_path)
    _project(root, "p", target_currency="USD", fx_rates={"EUR": 1.08})
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="K",
        commercial={"vendor": "K", "currency": "EUR", "base_price": 1000.0,
                    "freight_included": True},
        technical=[{"doc_id": "d1", "parameter": "flow"}],
        technical_feedback="a reviewer's note",
        quotation_doc_id="d1"))

    assert renormalize(root, "p") == 1

    stored = snapshots.load_facts(root, "p", "K")
    assert abs(stored.normalized["normalized_total"] - 1080.0) < 0.01
    assert stored.technical_feedback == "a reviewer's note"
    assert stored.technical == [{"doc_id": "d1", "parameter": "flow"}]
    assert stored.quotation_doc_id == "d1"


def test_renormalize_recomputes_from_facts_read_inside_the_lock(tmp_path, monkeypatch):
    """The pre-pass is a candidate list, not the value to write. If the stored
    commercial changes between the pre-pass and the write, the value written
    must follow the NEW commercial, not the one the pre-pass saw."""
    root = str(tmp_path)
    _project(root, "p", target_currency="USD", fx_rates={"EUR": 1.0})
    snapshots.save_facts(root, "p", VendorFacts(
        vendor="K", commercial={"vendor": "K", "currency": "EUR",
                                "base_price": 1000.0, "freight_included": True}))

    real_update = snapshots.update_facts
    swapped = []

    @contextmanager
    def swap_then_update(root_, slug_, vendor_, **kw):
        if not swapped:
            swapped.append(True)
            # a concurrent run stores a different price, then we take the lock
            snapshots.save_facts(root_, slug_, VendorFacts(
                vendor=vendor_, commercial={"vendor": vendor_, "currency": "EUR",
                                            "base_price": 2000.0,
                                            "freight_included": True}))
        with real_update(root_, slug_, vendor_, **kw) as f:
            yield f

    monkeypatch.setattr(snapshots, "update_facts", swap_then_update)
    monkeypatch.setattr(renormalize_module.snapshots, "update_facts", swap_then_update)
    renormalize(root, "p")

    stored = snapshots.load_facts(root, "p", "K")
    assert abs(stored.normalized["normalized_total"] - 2000.0) < 0.01, \
        "wrote a total derived from the commercial the pre-pass saw"
```

- [ ] **Step 2: Run to verify the second fails**

Run: `python -m pytest tests/test_renormalize.py -v`
Expected: `test_renormalize_recomputes_from_facts_read_inside_the_lock` FAILS — today's code assigns `facts.normalized` on the object the pre-pass loaded and saves that whole object, so the stored total is 1000.0 and the concurrent 2000.0 commercial is gone too.

- [ ] **Step 3: Write the implementation**

> **Reference code below is intent, not paste-able.** **Load-bearing:** the early `return 0` before any transaction; recomputing inside the lock; counting only real changes; assigning `normalized` and nothing else. **Illustrative:** the name `candidates` and the event payload.

```python
# procurement/renormalize.py
def renormalize(root: str, slug: str) -> int:
    project = load_project(root, slug)

    # Compute first, to decide whether anything needs writing at all.
    # `transaction` bumps `generation` on any body that completes, and this is
    # called from the read path -- opening one before knowing would advance
    # `generation` on every page load, forever.
    candidates = []
    for vendor in snapshots.list_fact_vendors(root, slug):
        facts = snapshots.load_facts(root, slug, vendor)
        if facts is None or not facts.commercial:
            continue                      # skipped, never zeroed
        fresh = normalize_bid(
            VendorBid.model_validate(facts.commercial),
            project.target_currency, project.fx_rates).model_dump()
        if fresh != facts.normalized:
            candidates.append(vendor)

    if not candidates:
        return 0

    changed = 0
    with snapshots.transaction(root, slug):
        for vendor in candidates:
            # Recompute inside the lock, from the facts and the project as
            # they are NOW. The pre-pass is a candidate list, not the value:
            # a run may have stored fresh facts since, and writing the
            # pre-pass's number back over them is BUG-010.
            with snapshots.update_facts(root, slug, vendor) as facts:
                if not facts.commercial:
                    continue
                current = load_project(root, slug)
                fresh = normalize_bid(
                    VendorBid.model_validate(facts.commercial),
                    current.target_currency, current.fx_rates).model_dump()
                if fresh == facts.normalized:
                    continue              # candidacy evaporated; not a change
                facts.normalized = fresh
                changed += 1

    events.append_event(root, slug, Event(
        at=_now(), run_id=events.new_run_id(), actor="renormalize",
        action="fx.renormalized", detail={"vendors": changed}))
    return changed
```

**Note the `continue` inside `update_facts`.** The body completing without assigning still writes the object back — harmlessly, since nothing changed, but it is a write. That is acceptable and simpler than adding an abort path; the test above pins that no other field moves.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_renormalize.py -v`
Expected: PASS, all of the file's existing tests included — especially the no-op-when-unchanged and single-generation-bump ones.

- [ ] **Step 5: Commit**

```bash
git add procurement/renormalize.py tests/test_renormalize.py
git commit -m "fix(renormalize): recompute inside the vendor lock, write only normalized (BUG-010)"
```

---

### Task 4: the extraction save — the S1

**Files:**
- Modify: `procurement/pipeline.py:833-955`
- Test: `tests/test_pipeline_lifecycle.py`

**Interfaces:**
- Consumes: `snapshots.update_facts` (Task 1)
- Produces: no signature change
- **Store invariant owned:** after a per-document save, every field the run does not own holds the value stored at the moment of that save. `technical_feedback` is the field this is measured on, because the run never modifies it at all.

This is the reported S1. It is **not** a one-line swap: the per-document merges read `base` too —

```python
technical = [f for f in base.technical if f.get("doc_id") != doc.doc_id]
technical += [f.model_dump() for f in facts]
```

— and `base` is the pre-extraction copy. A concurrent writer that added another document's technical facts would be dropped by that filter. So the **merge moves inside the lock**; only the extraction stays outside it, which is the whole point (the lock must not span an LLM call, or a reviewer waits minutes).

Restructure in one sentence: the extraction branches stop computing merged lists and instead record *what this document yielded*; a single `update_facts` block at the end applies that to the freshly-read object.

`None` means "this document yielded nothing new — leave the stored value alone". That is how every existing failure guard is preserved: a failed branch simply leaves its variable `None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_lifecycle.py
def test_a_run_does_not_write_back_feedback_it_read_before_extracting(tmp_path, monkeypatch):
    """BUG-010's H1, without threads: prove the run writes the CURRENT stored
    feedback rather than the copy it read before the extraction."""
    root, slug = _project_with_one_vendor_doc(tmp_path, monkeypatch)

    # a first run, so facts exist
    run_ingestion(root, slug)
    assert snapshots.load_facts(root, slug, "KERUI") is not None

    # a reviewer writes during the second run's extraction
    real_extract = pipeline.extract_bid

    def extract_then_review(*a, **kw):
        result = real_extract(*a, **kw)
        save_feedback(root, slug, "KERUI", "reviewed mid-run", reason="audit")
        return result

    monkeypatch.setattr(pipeline, "extract_bid", extract_then_review)
    run_ingestion(root, slug, force=True)

    assert snapshots.load_facts(root, slug, "KERUI").technical_feedback \
        == "reviewed mid-run"


def test_a_run_normalizes_with_the_rate_current_at_the_save(tmp_path, monkeypatch):
    """BUG-010's H2 severity variant: a rate CORRECTED mid-run must not leave a
    plausible wrong number in the store."""
    root, slug = _project_with_one_vendor_doc(tmp_path, monkeypatch)
    _set_rates(root, slug, {"EUR": 1.08})
    _make_the_mock_quote_eur_1000(monkeypatch)      # vacuity guard, see below
    run_ingestion(root, slug)

    stored = snapshots.load_facts(root, slug, "KERUI")
    assert stored.commercial["currency"] == "EUR"
    assert stored.commercial["base_price"] > 0, \
        "vacuous fixture: a zero price is rate-insensitive and cannot detect this"

    real_extract = pipeline.extract_bid

    def extract_then_correct_the_rate(*a, **kw):
        result = real_extract(*a, **kw)
        _set_rates(root, slug, {"EUR": 1.15})
        return result

    monkeypatch.setattr(pipeline, "extract_bid", extract_then_correct_the_rate)
    run_ingestion(root, slug, force=True)

    stored = snapshots.load_facts(root, slug, "KERUI")
    assert abs(stored.normalized["normalized_total"] - 1150.0) < 0.01, \
        f"stale rate: stored {stored.normalized['normalized_total']}, want 1150.0"
```

`_make_the_mock_quote_eur_1000` is required, not optional: the stock mock client returns `{}` for every schema, so `currency=""` and `base_price=0.0`, and `normalize_bid` yields `0.0` at any rate. Patch `shared.llm.mock_client` (or the client the fixture injects) to return a `EUR 1000` quotation for the quotation schema. The `base_price > 0` assertion above is what stops this silently rotting back into a vacuous test.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_pipeline_lifecycle.py -k "write_back_feedback or rate_current_at_the_save" -v`
Expected: both FAIL. The first because `technical_feedback=base.technical_feedback` writes the pre-extraction value back. The second with `1080.0` — the run-start rate.

- [ ] **Step 3: Write the implementation**

> **Reference code below is intent, not paste-able.** **Load-bearing:** `None` meaning "unchanged" so every failure guard survives; the merge, `reconcile`, `apply_overrides` and the `normalize_bid` call all happening *inside* the lock; `technical_feedback` never being assigned; the project being re-read inside the lock. **Illustrative:** the helper name `_replace_doc_records`, and the exact local variable names — the current code calls the loaded copy `base` and the resolved dict `resolved`; match whatever is actually there.

Replace the branch bodies' merged-list assignments with per-document outcomes:

```python
# what THIS document yielded; None means "leave the stored value alone"
doc_commercial = None          # a VendorBid dump
doc_technical = None           # list[dict] for this doc_id
doc_deviations = None          # list[dict] for this doc_id
claims_quotation = False

if not run_primary:
    ...                        # unchanged: carries prior's cache fields only
elif route == "quotation":
    bid = extract_bid(...)
    status = bid.extraction_status
    doc.notes = bid.notes
    if status == "ok":
        # the same guard as today: a failed overwrite must not publish a
        # half-good record, and must not repoint the link at a document that
        # produced nothing
        doc_commercial = bid.model_dump()
        claims_quotation = True
elif route == "datasheet":
    facts, status, notes = extract_tech_facts(...)
    doc.vocabulary_sha = vocab_sha
    doc.notes = notes
    if status == "ok":
        doc_technical = [f.model_dump() for f in facts]
else:   # deviation
    items, status, notes = extract_deviations(...)
    doc.notes = notes
    if status == "ok":
        doc_deviations = [d.model_dump() for d in items]

# ... run_secondary unchanged, except it sets doc_technical rather than
# rebuilding the merged list:
if secondary_status == "ok":
    doc_technical = [f.model_dump() for f in facts]
```

Then one locked block replaces `pipeline.py:930-955`:

```python
def _replace_doc_records(stored: list[dict], doc_id: str,
                         produced: list[dict] | None) -> list[dict]:
    """This document's records replaced; every other document's kept."""
    if produced is None:
        return stored                      # nothing new: leave it alone
    return [r for r in stored if r.get("doc_id") != doc_id] + produced


with snapshots.update_facts(root, slug, doc.vendor, create=True) as facts:
    # Everything below reads `facts`, re-read inside the lock -- never the
    # copy loaded before the extraction. `technical_feedback` is never
    # assigned: it is the reviewer's, and carrying it forward from a
    # pre-extraction copy is BUG-010.
    view = {
        "commercial": doc_commercial if doc_commercial is not None
                      else facts.commercial,
        "technical": _replace_doc_records(facts.technical, doc.doc_id,
                                          doc_technical),
        "deviations": _replace_doc_records(facts.deviations, doc.doc_id,
                                           doc_deviations),
    }
    overrides = reconcile(facts.overrides, view)
    resolved = apply_overrides(view, overrides)

    facts.commercial = resolved["commercial"]
    facts.technical = resolved["technical"]
    facts.deviations = resolved["deviations"]
    facts.overrides = overrides
    if claims_quotation:
        facts.quotation_doc_id = doc.doc_id

    if resolved["commercial"]:
        # the project as it is NOW, not pipeline.py:618's copy: a rate
        # corrected during this run must not leave a stale total
        current = load_project(root, slug)
        facts.normalized = normalize_bid(
            VendorBid.model_validate(resolved["commercial"]),
            current.target_currency, current.fx_rates).model_dump()
    # else: leave facts.normalized alone -- no commercial, nothing to derive
```

The `for o in overrides:` conflict-event loop that follows stays where it is, reading the `overrides` local — it emits events, not store writes, and must stay outside the lock so it does not hold the vendor lock across event appends.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_pipeline_lifecycle.py tests/test_pipeline_incremental.py tests/test_extraction_status.py tests/test_fact_identity_lifecycle.py -v`
Expected: PASS. These four files carry the failure guards, the cache behaviour and the override reconciliation that the restructure moves; a regression here is a real one, not a fixture to adjust.

- [ ] **Step 5: Commit**

```bash
git add procurement/pipeline.py tests/test_pipeline_lifecycle.py
git commit -m "fix(pipeline): merge and normalize inside the vendor lock (BUG-010)"
```

---

### Task 5: the prune save and the migration write

(`save_facts`'s docstring — spec §1.6's "closing the old path" — lands in Task 1
instead, since it is an edit to the same file and the same commit.)

**Files:**
- Modify: `procurement/pipeline.py:202-243`, `procurement/store/migrate.py:38-50`
- Test: `tests/test_pipeline_incremental.py`, `tests/test_store_migrate.py`

**Interfaces:**
- Consumes: `snapshots.update_facts`, `snapshots.facts_lock` (Task 1)
- Produces: no signature change
- **Store invariant owned:** a stored collection contains exactly the records of its currently-live sources — and pruning changes only `technical`, `deviations`, and (when the stored quotation is no longer live) `commercial`, `normalized` and `quotation_doc_id`. `technical_feedback` and `overrides` are untouched by a prune.

`_prune_orphan_facts` already mutates a loaded object rather than reconstructing one, so its conversion is mechanical — the read moves inside the lock and the `save_facts` becomes the context manager's exit. Its early `continue` for "nothing to prune" must move to *before* the lock is taken, so an unchanged vendor is not rewritten on every run.

`migrate_dataset_json` is different and does **not** become an `update_facts`. Its write is conditional on the vendor having no facts at all (`migrate.py:45-46`), which is a first write, not a read-modify-write. Expressing it through `update_facts` would change the skip rule. It takes `facts_lock` instead and keeps its own check inside it — which also closes the TOCTOU it has today, where a run can store real facts between the check and the write and have them overwritten by legacy `dataset.json` data.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pipeline_incremental.py
def test_pruning_does_not_disturb_feedback_or_overrides(tmp_path, monkeypatch):
    root, slug = _project_with_two_docs(tmp_path, monkeypatch)
    run_ingestion(root, slug)
    with snapshots.update_facts(root, slug, "KERUI") as f:
        f.technical_feedback = "a reviewer's note"

    _delete_one_source_document(root, slug)
    run_ingestion(root, slug)

    stored = snapshots.load_facts(root, slug, "KERUI")
    assert stored.technical_feedback == "a reviewer's note"
    assert not any(f.get("doc_id") == _DELETED_DOC for f in stored.technical)


# tests/test_store_migrate.py
def test_migration_does_not_overwrite_facts_a_run_stored_meanwhile(tmp_path):
    """The check-then-write TOCTOU: facts appearing between the check and the
    write must win -- they are newer than dataset.json by construction."""
    root = str(tmp_path)
    _legacy_dataset_json(root, "p", vendor="KERUI", base_price=100.0)

    real_lock = snapshots.facts_lock

    @contextmanager
    def store_then_lock(root_, slug_, vendor_):
        snapshots.save_facts(root_, slug_, VendorFacts(
            vendor=vendor_, commercial={"vendor": vendor_, "base_price": 999.0}))
        with real_lock(root_, slug_, vendor_):
            yield

    with mock.patch.object(migrate.snapshots, "facts_lock", store_then_lock):
        migrate.migrate_dataset_json(root, "p")

    assert snapshots.load_facts(root, "p", "KERUI").commercial["base_price"] == 999.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_pipeline_incremental.py tests/test_store_migrate.py -v`
Expected: the migration test FAILS (today's check runs before any lock, so the legacy 100.0 overwrites the 999.0). The prune test may pass today — its window is short — and is a regression guard; say so rather than claiming red.

- [ ] **Step 3: Write the implementation**

> **Reference code below is intent, not paste-able.** **Load-bearing:** the "nothing to prune" `continue` staying *outside* the lock; migrate's existence check moving *inside* it. **Illustrative:** variable names.

The live sets (`live_technical`, `live_deviations`, `live_quotations`) are
derived from `documents`, not from stored facts, so they are computed once
outside the loop exactly as today. What must move inside the lock is every
*filter over stored facts*, because those read the vendor's records.

```python
# procurement/pipeline.py, in _prune_orphan_facts
for vendor in vendors:
    peek = snapshots.load_facts(root, slug, vendor)
    if peek is None:
        continue
    # Cheap pre-check on an unlocked read, to avoid taking the lock and
    # rewriting a vendor with nothing to prune. Deliberately advisory: the
    # authoritative filter runs again inside the lock below.
    if (all(f.get("doc_id") in live_technical for f in peek.technical)
            and all(d.get("doc_id") in live_deviations for d in peek.deviations)
            and not (peek.quotation_doc_id is not None
                     and peek.quotation_doc_id not in live_quotations)):
        continue

    with snapshots.update_facts(root, slug, vendor) as facts:
        # Re-filter against what is stored NOW: another writer may have added
        # records for a live document since the pre-check.
        technical = [f for f in facts.technical
                     if f.get("doc_id") in live_technical]
        deviations = [d for d in facts.deviations
                      if d.get("doc_id") in live_deviations]
        commercial_dead = (facts.quotation_doc_id is not None
                           and facts.quotation_doc_id not in live_quotations)
        dropped = sorted(
            {f.get("doc_id") for f in facts.technical
             if f.get("doc_id") not in live_technical}
            | {d.get("doc_id") for d in facts.deviations
               if d.get("doc_id") not in live_deviations})
        detail = {"doc_ids": dropped,
                  "technical_dropped": len(facts.technical) - len(technical),
                  "deviations_dropped": len(facts.deviations) - len(deviations)}
        if commercial_dead:
            detail["commercial_dropped"] = facts.quotation_doc_id
            facts.commercial = None
            facts.normalized = None
            facts.quotation_doc_id = None
        facts.technical = technical
        facts.deviations = deviations
        # Overrides untouched, for pipeline.py:239-242's reason, and
        # technical_feedback untouched because it is never the run's.

    events.append_event(root, slug, Event(              # outside the lock
        at=_now(), run_id=run_id, actor="pipeline",
        action="facts.pruned", target=vendor, detail=detail))
```

Note `detail` is built **inside** the lock, from the same fresh object the
filters ran over. Building it outside from the pre-check's read would report
counts that do not match what was written.

```python
# procurement/store/migrate.py
for bid in bids:
    vendor = bid.get("vendor")
    if not vendor:
        continue
    with snapshots.facts_lock(root, slug, vendor):
        # Inside the lock: a run storing real facts between this check and the
        # write below would otherwise be overwritten by legacy data.
        if snapshots.load_facts(root, slug, vendor) is not None:
            continue          # already in the store and newer than this
        snapshots.save_facts(root, slug, VendorFacts(
            vendor=vendor,
            commercial=bid,
            normalized=normalized_by_vendor.get(vendor),
        ))
    imported.append(vendor)
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_pipeline_incremental.py tests/test_store_migrate.py tests/test_pipeline_lifecycle.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add procurement/pipeline.py procurement/store/migrate.py tests/test_pipeline_incremental.py tests/test_store_migrate.py
git commit -m "fix(pipeline,migrate): prune and import under the vendor lock (BUG-010)"
```

---

### Task 6: Integration — the concurrent mutation matrix

**Files:**
- Test: `tests/test_facts_concurrency.py` (new)
- Modify: `BUGS_TRACKER.md` (close BUG-010), `CLAUDE.md` (baselines)

**Interfaces:**
- Consumes: everything above
- Produces: no new interface
- **Store invariant owned:** the union of Tasks 1–5, observed across two concurrent writers rather than one — no writer's completed write is undone by another's, and `normalized` matches the project's current settings once both have finished.

Per-task TDD structurally produces single-writer tests. Every defect BUG-010 names needs **two writers** to see. This task is the structural defence, and it is the only place several of the earlier tasks get red/green proof at all — Tasks 2 and 5 are honest regression guards on their own.

**Deviation from PLAN-TEMPLATE Rule 2, declared rather than faked.** The nine pre-declared rows are extraction-cache mutations *between two runs*. This plan changes no prompt, extractor, provider or cache-keying code, so five of them (prompt-version bumps, cross-class cache invalidation, an unrecognised sole source, an omitted optional array, a sibling revision) would be re-testing `tests/test_pipeline_incremental.py` verbatim. Those stay where they are; confirm that file still passes rather than duplicating it. The four that **do** apply are kept, because Task 4 restructures the code that implements them, and each is paired here with a concurrent writer.

- [ ] **Step 1: Write the matrix**

One test per row. Each names in its docstring the invariant it defends and which task owns it.

| mutation | invariant at risk | assert |
|---|---|---|
| feedback written during a run's extraction (H1) | Task 4 | the note survives the run's saves |
| an FX rate set during a run, none before (H2) | Task 4 | no vendor's stored `normalized` is stale against the project's stored rate |
| an FX rate **corrected** during a run, 1.08→1.15 (H2 severity) | Task 4 | stored total is 1150.0, not 1080.0 — a wrong *number*, not a blank |
| a run finishes while a feedback write is in flight (reverse) | Tasks 2, 4 | both survive: the extraction result and the note |
| `renormalize` races a run's save (reverse-fx) | Tasks 3, 4 | once both finish, every vendor satisfies the §2 invariant |
| a re-extraction fails after a successful one, with feedback written between | Task 4 | previously-good stored data is not blanked **and** the note survives |
| an LLM call fails on run 1, succeeds on run 2, feedback written between | Task 4 | total goes `None` → real; note survives; status `ok` |
| a document is deleted, with feedback written during the run | Task 5 | facts pruned; note survives; `overrides` untouched |
| a newer revision arrives while a rate is corrected | Tasks 4, 5 | new revision's facts normalized at the new rate; no total from the superseded one survives |
| a run over N documents, concurrent writers throughout | Task 1 | `generation` advances by exactly 1 |
| `save_feedback` for a vendor with no stored facts | Task 2 | `LookupError`, nothing written, `generation` unchanged |
| `update_facts` re-entered for one vendor on one thread | Task 1 | hangs or raises — never silently loses a write |

The run-side rows need no forcing hook: the window is the extraction, so patching latency into `classify_structure` (or issuing the write from inside a patched `extract_bid`, as Task 4's tests do) is enough. The short-window rows — `save_feedback` against `renormalize` — do need one; reuse the `_hold_first_project_write` shape from `tests/test_project_concurrency.py:31-64`, retargeted at `layout.atomic_write_json` matching a facts path.

**Every rate-sensitive row carries the vacuity guard.** Assert the fixture holds a vendor with a non-zero, rate-sensitive price before asserting the outcome, and fail with a message saying so if not. H2 first measured 0/8 against a fixture that could not fail.

- [ ] **Step 2: Run to verify they fail where expected**

Run: `python -m pytest tests/test_facts_concurrency.py -v`
Expected: rows fail against any incomplete task. A row that passes before its task is implemented is not testing what it claims.

- [ ] **Step 3: Verify the matrix is real, not decorative**

Reinstate each defect one at a time and confirm **the intended row fails and nothing else does**:

| reinstate | expected failure |
|---|---|
| `technical_feedback=base.technical_feedback` in the run's save | H1, reverse, the failed-re-extraction row, the deleted-document row |
| normalize from `pipeline.py:618`'s project rather than a re-read | H2, H2-severity, the newer-revision row |
| move `update_facts`'s `load_facts` outside the lock | H1 and the reverse rows (and Task 1's own re-read test) |
| bump `generation` inside `update_facts` | the generation row (and Task 1's own bump test) |
| `renormalize` writing the pre-pass's value | reverse-fx (and Task 3's own recompute test) |
| migrate checking existence outside the lock | Task 5's migration test |

Revert each probe **immediately** after observing the failure. Do not end a session with a probe applied: BUG-005's Task 8 left `fx_rates.get(currency, 1.0)` in the working tree after its commit, and the next reader could not distinguish it from a regression without comparing mtimes against the commit clock.

- [ ] **Step 4: Re-measure both baselines and close the bug**

Run: `python -m pytest -q`, then `npm test` and `npm run build` under `web/`.

Update `CLAUDE.md`'s baseline table: measure the **workstation** row, then derive the CI row as `workstation − 4 corpus-coverage − 3 data/`, with those seven becoming skips. Editing the two rows independently is how they drift apart. The current committed figures are 950 / 3 and 943 / 10.

Close BUG-010 in `BUGS_TRACKER.md`: move it to the Closed table, and fill the Fix block with the spec, this plan, the commits, the tests that fail without the fix, and the verified counts. Record the two corrections this work made to the original report — `base` is re-loaded per document, and the run-start `project` is the more dangerous half.

- [ ] **Step 5: Commit**

```bash
git add tests/test_facts_concurrency.py BUGS_TRACKER.md CLAUDE.md
git commit -m "test(store): concurrent mutation matrix for facts.json; close BUG-010"
```

---

## Checklist before this plan is approved

- [x] Every task's `Interfaces` block has a **Store invariant owned** bullet.
- [x] No invariant is claimed twice — Task 1 owns the primitive's guarantee, 2 `technical_feedback`, 3 `normalized`, 4 the run's non-owned fields, 5 the pruned collections, 6 their union under concurrency.
- [x] The integration task has a mutation matrix; the five inapplicable pre-declared rows are named and argued in Task 6 rather than dropped silently.
- [x] Every matrix row names an invariant; every invariant has a row.
- [x] The Rule 3 banner appears above the first reference block (Task 1, Step 3) and each subsequent one.
- [x] Each reference block states which parts are load-bearing and which are illustrative.
