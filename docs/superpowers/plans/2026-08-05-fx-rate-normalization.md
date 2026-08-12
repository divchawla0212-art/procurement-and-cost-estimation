# BUG-005 — FX rate normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A bid in a currency with no configured rate is reported as unconvertible instead of being silently converted at 1.0 and ranked as the cheapest offer.

**Architecture:** `normalize_bid` becomes the single chokepoint that refuses to invent a rate, returning `normalized_total=None` plus a structured `normalization_status`. A new `procurement/renormalize.py` recomputes stored totals from already-extracted commercial facts whenever rates change — pure arithmetic, no LLM calls — and the same routine runs once per pre-existing project as a store migration. The EUR 1.08 default is a prefilled row in the setup form, never a stored value.

**Tech Stack:** Python 3.12, pydantic, FastAPI, pytest; React + TypeScript + vitest under `web/`.

## Global Constraints

- **Spec:** [`docs/superpowers/specs/2026-08-05-fx-rate-normalization-design.md`](../specs/2026-08-05-fx-rate-normalization-design.md). Section references below (§1.1 … §1.5) point there.
- **Tracker:** [`BUGS_TRACKER.md`](../../../BUGS_TRACKER.md) § BUG-005. It closes only when a test fails without the fix and the Fix block links this plan and that spec.
- Run tests from the repo root with `python -m pytest`. Tests are key-free — use `shared/llm/mock_client.py`. No test may require `ANTHROPIC_API_KEY`.
- **Baseline before this plan starts: 903 passed, 3 skipped** on a workstation. Note `CLAUDE.md` still records the older `897 passed, 3 skipped` — it was not updated when commit `1e47c35` added six tests. Task 7 re-measures and corrects both rows; do not "fix" the discrepancy earlier by editing one row in isolation, which is how the two rows drift apart.
- Derive the CI row from the workstation row: `CI = workstation - 4 corpus-coverage - 3 data/ tests`, with those seven becoming skips.
- Web suite is separate: `npm test` under `web/` (currently **31 passed**), and `npm run build` type-checks the test files.
- **Arithmetic stays in Python.** No extractor, prompt, or provider change appears anywhere in this plan. Re-normalization must never trigger an LLM call.
- Missing data is never coerced to a passing or zero value: `None`, never `0.0`.
- Every store write goes through `procurement/store/snapshots.py`. `generation` bumps once per write transaction, never once per file.

---

## File Structure

| file | responsibility | task |
|---|---|---|
| `procurement/models.py` | add `normalization_status` to `NormalizedBid` and `ComparisonRow` | 1, 5 |
| `procurement/normalize.py` | refuse to convert without a usable rate | 1 |
| `api/main.py` | reject non-positive rates; call `renormalize` after saving rates | 2, 3 |
| `procurement/renormalize.py` | **new** — recompute stored totals; run the one-shot migration | 3, 4 |
| `procurement/pipeline.py` | call the migration from `_live_fact_vendors` | 4 |
| `procurement/project.py` | stamp `store_version` on create | 4 |
| `procurement/store/layout.py` | `STORE_VERSION` 1 → 2 | 4 |
| `procurement/compare.py`, `procurement/export.py` | carry the reason into the dataset and the export | 5 |
| `procurement/statement.py` | emit the Normalised cell with a reason instead of dropping it | 6 |
| `web/src/pages/Setup.tsx` | prefill the suggested EUR row | 7 |

**Two corrections found while writing this plan — read these before Task 6.**

`web/src` renders `normalized_total` **nowhere**; grep it and you get no hits. `ComparisonRow` reaches only `load_dataset`'s `dataset["comparison"]` and the xlsx export. There is no comparison table component to change, and a task written against one would have had its implementer inventing a file.

What the buyer actually reads is the **Comparative statement**, whose rows are built server-side in `procurement/statement.py` and rendered generically. That matters because `statement.py:262` reads `if norm_total is not None and base is not None:` — so under Task 1 a bid with no rate loses its Normalised row **entirely**, with no explanation. A silently absent row is the same defect as a fabricated number, one layer up, and it is what Task 6 exists to prevent.

The web needs no change for this: `ComparativeStatement.tsx:274` already renders `cell.note`. The fix is entirely in `statement.py`.

**Why `renormalize.py` is a new module and not a function in `pipeline.py`:** the migration has to run from `_live_fact_vendors` (the read path) *and* be importable by the API. Putting it in `procurement/store/migrate.py` beside the existing migration would be the obvious home, but `migrate.py` cannot import `pipeline` — `pipeline` already imports `migrate`, and that is a circular import. A standalone module depending only on `normalize`, `models`, `project` and `store` breaks the cycle.

---

## Reference code

> **Reference code below is intent, not paste-able.** It shows the shape and the
> reasoning, and it has not been executed. Read it, then write the
> implementation against the test.

**Load-bearing** (get these exactly right):
- the *early return* in `normalize_bid` — an unconvertible bid produces no adjustments at all
- `normalization_status` defaulting to `"ok"`, so snapshots already on disk parse unchanged
- `renormalize` computing everything **before** opening a transaction, and opening none when nothing changed
- treating a non-positive rate as missing rather than applying it

**Illustrative** (shape only — name and structure to taste):
- the exact split between `_usable_rate` and `normalize_bid`
- the event `detail` payload
- the exact wording of UI copy

---

### Task 1: `normalize_bid` refuses to convert without a usable rate

**Files:**
- Modify: `procurement/models.py:52-57` (`NormalizedBid`)
- Modify: `procurement/normalize.py:1-49`
- Test: `tests/test_procurement_normalize.py`

**Interfaces:**
- Consumes: `VendorBid`, `NormalizedBid` from `procurement.models`
- Produces: `normalize_bid(bid, target_currency, fx_rates) -> NormalizedBid`, where `NormalizedBid.normalization_status` is `"ok"` or `"no_fx_rate"`
- **Store invariant owned:** no `VendorFacts.normalized` anywhere in the store carries a non-null `normalized_total` for a currency that has no positive rate in the owning project's `fx_rates`. `normalize_bid` is the sole producer of that value, so it is the sole place this can be violated.

`tests/test_procurement_normalize.py::test_missing_fx_rate_is_surfaced` currently asserts the defect as intended behaviour (`assert abs(n.normalized_total - 1000.0) <= 0.01  # assumed 1:1`). It is a characterization test. **Rewrite it — do not extend it.** Read it first: it is the specification you are replacing.

One behaviour is deliberately preserved: a bid whose extracted `currency` is the empty string is still treated as already being in the target currency, exactly as today (`is_foreign = bool(bid.currency) and ...`). That is arguably the same family of defect — an unknown currency assumed to be the target — but it is outside this spec's scope, and several existing tests depend on it. Step 1 pins it with a test so a later change to it is a deliberate act rather than an accident.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from procurement.models import VendorBid
from procurement.normalize import normalize_bid


def _eur_bid():
    return VendorBid(vendor="A", currency="EUR", base_price=1000.0, freight_included=True)


def test_missing_fx_rate_leaves_the_bid_unconverted():
    """Replaces test_missing_fx_rate_is_surfaced, which asserted the 1:1 assumption."""
    n = normalize_bid(_eur_bid(), "USD", {})
    assert n.normalized_total is None
    assert n.normalization_status == "no_fx_rate"


def test_an_unconvertible_bid_records_no_adjustments():
    # A list of adjustments leading to no total describes arithmetic that did
    # not happen.
    assert normalize_bid(_eur_bid(), "USD", {}).adjustments == []


def test_unconvertible_is_not_the_same_as_a_failed_extraction():
    # Both produce normalized_total=None, and they send the user to different
    # screens: one needs a rate, the other needs the document re-read.
    n = normalize_bid(_eur_bid(), "USD", {})
    assert n.extraction_status == "ok"
    assert n.normalization_status == "no_fx_rate"

    failed = normalize_bid(VendorBid(vendor="B", extraction_status="failed"), "USD", {})
    assert failed.extraction_status == "failed"
    assert failed.normalization_status == "ok"


@pytest.mark.parametrize("rate", [0.0, -1.08])
def test_a_non_positive_rate_is_treated_as_missing(rate):
    # A rate of 0 converts the bid to 0.0 and sorts it first -- the same defect
    # in different clothing.
    n = normalize_bid(_eur_bid(), "USD", {"EUR": rate})
    assert n.normalized_total is None
    assert n.normalization_status == "no_fx_rate"


def test_a_configured_rate_still_converts():
    n = normalize_bid(_eur_bid(), "USD", {"EUR": 1.08})
    assert abs(n.normalized_total - 1080.0) <= 0.01
    assert n.normalization_status == "ok"


def test_a_bid_already_in_the_target_currency_is_never_flagged():
    bid = VendorBid(vendor="A", currency="USD", base_price=1000.0, freight_included=True)
    n = normalize_bid(bid, "USD", {})
    assert abs(n.normalized_total - 1000.0) <= 0.01
    assert n.normalization_status == "ok"


def test_a_bid_with_no_extracted_currency_keeps_todays_behaviour():
    # Pinned deliberately: an empty currency is treated as the target currency,
    # as it is today. Out of scope for BUG-005; this test makes a future change
    # to it deliberate.
    bid = VendorBid(vendor="A", currency="", base_price=1000.0, freight_included=True)
    n = normalize_bid(bid, "USD", {})
    assert abs(n.normalized_total - 1000.0) <= 0.01
    assert n.normalization_status == "ok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_procurement_normalize.py -v`
Expected: the new tests FAIL — `AttributeError`/`ValidationError` on `normalization_status`, and `test_missing_fx_rate_leaves_the_bid_unconverted` failing with `normalized_total == 1000.0` rather than `None`. That second failure is the bug itself; confirm you see it before writing any implementation.

- [ ] **Step 3: Write minimal implementation**

`procurement/models.py` — add to `NormalizedBid` (the default is load-bearing: every snapshot already on disk must still parse):

```python
class NormalizedBid(BaseModel):
    vendor: str
    normalized_currency: str
    normalized_total: float | None
    adjustments: list[NormalizationAdjustment] = []
    extraction_status: str = "ok"
    normalization_status: str = "ok"       # "ok" | "no_fx_rate"
```

`procurement/normalize.py`:

```python
def _usable_rate(currency: str, target_currency: str,
                 fx_rates: dict[str, float]) -> float | None:
    """The multiplier to reach the target currency, or None if there isn't one.

    None means "refuse", never "assume". A non-positive configured rate counts
    as absent: 0.0 would convert the bid to nothing and sort it first, which is
    the defect this function exists to stop, not a rate.
    """
    if not currency or currency == target_currency:
        return 1.0
    rate = fx_rates.get(currency)
    if rate is None or rate <= 0:
        return None
    return rate


def normalize_bid(bid, target_currency, fx_rates):
    if bid.extraction_status == "failed":
        return NormalizedBid(vendor=bid.vendor, normalized_currency=target_currency,
                             normalized_total=None, extraction_status="failed")

    rate = _usable_rate(bid.currency, target_currency, fx_rates)
    if rate is None:
        # Early return, before VAT/freight/discount: every one of those steps
        # operates on a converted figure.
        return NormalizedBid(vendor=bid.vendor, normalized_currency=target_currency,
                             normalized_total=None, normalization_status="no_fx_rate")

    adjustments = []
    base = bid.base_price
    converted = base * rate
    if rate != 1.0:
        adjustments.append(NormalizationAdjustment(
            kind="currency", description=f"{bid.currency}->{target_currency} @ {rate}",
            from_value=base, to_value=converted, delta=converted - base))
    # ... VAT, freight, discount blocks unchanged from here down ...
```

The old `elif is_foreign and bid.currency not in fx_rates:` branch — the one appending `"no FX rate for …; assumed 1.0"` — is deleted. Its meaning now lives in `normalization_status`, in a form code can branch on.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_procurement_normalize.py -v`
Expected: PASS.

Then run the full suite: `python -m pytest -q`. Other tests will fail if anything constructed a `NormalizedBid` positionally or asserted on the old adjustment text — fix those call sites, do not weaken the new assertions.

- [ ] **Step 5: Commit**

```bash
git add procurement/models.py procurement/normalize.py tests/test_procurement_normalize.py
git commit -m "fix(normalize): refuse to convert a currency with no usable rate (BUG-005)"
```

---

### Task 2: Reject non-positive FX rates at the API boundary

**Files:**
- Modify: `api/main.py:366-382` (`set_fx_rates`)
- Test: `tests/test_api_setup.py`

**Interfaces:**
- Consumes: `normalize_bid`'s treatment of a non-positive rate as missing (Task 1)
- Produces: `PUT /api/projects/{slug}/fx-rates` returns 422 naming the currency when any rate is `<= 0`
- **Store invariant owned:** `Project.fx_rates` contains only positive rates. Task 1 makes a non-positive rate harmless if one is already stored; this task stops a new one being written.

Task 1 is defence in depth for stores that already contain a bad rate. This task is the gate. Both are needed: neither alone covers both existing and future data.

- [ ] **Step 1: Write the failing tests**

```python
def test_fx_rates_rejects_a_non_positive_rate(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/projects", json={"name": "P"})
    client.put("/api/projects/p/fx-rates", json={"rates": {"EUR": 1.08}})

    res = client.put("/api/projects/p/fx-rates", json={"rates": {"EUR": 0}})

    assert res.status_code == 422
    assert "EUR" in res.json()["detail"]
    # the good rate must survive a rejected write
    assert client.get("/api/projects/p/setup").json()["fx_rates"] == {"EUR": 1.08}


def test_fx_rates_rejects_a_negative_rate(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/projects", json={"name": "P"})
    res = client.put("/api/projects/p/fx-rates", json={"rates": {"GBP": -1.27}})
    assert res.status_code == 422
    assert "GBP" in res.json()["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api_setup.py -k fx_rates -v`
Expected: FAIL — the endpoint returns 200 and stores `0.0`.

- [ ] **Step 3: Write minimal implementation**

In `set_fx_rates`, inside the existing `for code, value in raw.items():` loop, after the `float(value)` conversion succeeds:

```python
        if rate <= 0:
            raise HTTPException(
                status_code=422,
                detail=f"Rate for '{code}' must be greater than zero.")
```

Build the whole `rates` dict before assigning it to `project.fx_rates`, so a rejected entry leaves the stored rates untouched — the existing code already does this; keep it that way.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_api_setup.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/main.py tests/test_api_setup.py
git commit -m "fix(api): reject a non-positive FX rate instead of storing it (BUG-005)"
```

---

### Task 3: `renormalize` — recompute stored totals without an LLM call

**Files:**
- Create: `procurement/renormalize.py`
- Modify: `api/main.py` (`set_fx_rates` calls it)
- Test: `tests/test_renormalize.py` (new)

**Interfaces:**
- Consumes: `normalize_bid` (Task 1); `snapshots.load_facts`, `snapshots.save_facts`, `snapshots.list_fact_vendors`, `snapshots.transaction`; `events.append_event`, `events.new_run_id`
- Produces: `renormalize(root: str, slug: str) -> int` — the number of vendors whose stored `normalized` changed
- **Store invariant owned:** after `renormalize`, every vendor with stored `commercial` facts has a stored `normalized` exactly equal to what `normalize_bid` produces from those facts and the project's current `target_currency` and `fx_rates` — for every such vendor, and no others are written.

Why this exists at all: FX rates are baked into the store at ingestion, and a cached document `continue`s at `pipeline.py:811` before the `normalize_bid` call at line 935 — so a plain re-run after setting a rate changes nothing, and only `force=True` picks it up at the cost of a full re-extraction (§1.2, measured).

**The no-change-no-transaction rule is load-bearing.** `snapshots.transaction` bumps `generation` on any body that completes, written or not (`snapshots.py:38-39`). Task 4 calls this from the read path, so a version that always opened a transaction would advance `generation` on every page load, forever.

- [ ] **Step 1: Write the failing tests**

```python
import io, zipfile
from procurement.project import create_project, unpack_vendor_zip, load_project, save_project
from procurement.pipeline import run_ingestion
from procurement.renormalize import renormalize
from procurement.store import events, snapshots
from shared.llm.mock_client import MockLLMClient

_PAD = (b" This synthetic fixture body is padded with filler prose so its "
        b"character count clears the pipeline's minimum-extractable-text guard.")


def _eur_project(tmp_path):
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("EUROVEND/Quotation.txt", b"base price 1000" + _PAD)
    z = tmp_path / "v.zip"
    z.write_bytes(buf.getvalue())
    unpack_vendor_zip(root, "p", str(z))
    return root


def _client():
    return MockLLMClient(response={"currency": "EUR", "base_price": 1000.0,
                                   "freight_included": True})


def _set_rate(root, rates):
    project = load_project(root, "p")
    project.fx_rates = rates
    save_project(root, project)


def test_renormalize_applies_a_rate_set_after_ingestion(tmp_path):
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _client())
    assert snapshots.load_facts(root, "p", "EUROVEND").normalized["normalized_total"] is None

    _set_rate(root, {"EUR": 1.08})
    assert renormalize(root, "p") == 1

    facts = snapshots.load_facts(root, "p", "EUROVEND")
    assert abs(facts.normalized["normalized_total"] - 1080.0) <= 0.01
    assert facts.normalized["normalization_status"] == "ok"


def test_renormalize_makes_no_llm_call(tmp_path):
    """The whole point: conversion is arithmetic over an already-extracted price."""
    root = _eur_project(tmp_path)
    client = _client()
    run_ingestion(root, "p", client)
    calls_after_ingestion = len(client.calls)

    _set_rate(root, {"EUR": 1.08})
    renormalize(root, "p")

    assert len(client.calls) == calls_after_ingestion


def test_renormalize_bumps_generation_exactly_once(tmp_path):
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _client())
    before = load_project(root, "p").generation

    _set_rate(root, {"EUR": 1.08})
    renormalize(root, "p")

    assert load_project(root, "p").generation == before + 1


def test_renormalize_that_changes_nothing_bumps_nothing(tmp_path):
    # Task 4 calls this from the read path. Without this rule, `generation`
    # climbs on every page load.
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _client())
    _set_rate(root, {"EUR": 1.08})
    renormalize(root, "p")
    settled = load_project(root, "p").generation

    assert renormalize(root, "p") == 0
    assert load_project(root, "p").generation == settled


def test_renormalize_reverts_to_unconvertible_when_a_rate_is_withdrawn(tmp_path):
    # A stale number surviving the removal of its rate is the original bug
    # running backwards.
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _client())
    _set_rate(root, {"EUR": 1.08})
    renormalize(root, "p")

    _set_rate(root, {})
    assert renormalize(root, "p") == 1

    facts = snapshots.load_facts(root, "p", "EUROVEND")
    assert facts.normalized["normalized_total"] is None
    assert facts.normalized["normalization_status"] == "no_fx_rate"


def test_renormalize_records_an_event(tmp_path):
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _client())
    _set_rate(root, {"EUR": 1.08})
    renormalize(root, "p")

    actions = [e.action for e in events.read_events(root, "p")]
    assert "fx.renormalized" in actions


def test_renormalize_skips_a_vendor_with_no_commercial_facts(tmp_path):
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _client())
    facts = snapshots.load_facts(root, "p", "EUROVEND")
    facts.commercial = None
    snapshots.save_facts(root, "p", facts)

    assert renormalize(root, "p") == 0
    # skipped, not zeroed: a failed extraction never blanks stored data
    assert snapshots.load_facts(root, "p", "EUROVEND").commercial is None
```

`MockLLMClient` records calls — confirm the attribute name (`.calls`) before relying on it and adjust the assertion to whatever it actually exposes.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_renormalize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'procurement.renormalize'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Recompute stored normalized totals from stored commercial facts.

Currency conversion is arithmetic over a price that has already been
extracted, so changing a rate must not cost an LLM call or a re-extraction.
"""
from datetime import datetime, timezone

from procurement.models import VendorBid
from procurement.normalize import normalize_bid
from procurement.project import load_project
from procurement.store import events, snapshots
from procurement.store.models import Event


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def renormalize(root: str, slug: str) -> int:
    """Returns the number of vendors whose stored `normalized` changed."""
    project = load_project(root, slug)

    # Compute everything first. `transaction` bumps `generation` on any body
    # that completes, so opening one before knowing whether anything changed
    # would make every caller a writer -- and this runs on the read path.
    pending = []
    for vendor in snapshots.list_fact_vendors(root, slug):
        facts = snapshots.load_facts(root, slug, vendor)
        if facts is None or not facts.commercial:
            continue                      # skipped, never zeroed
        fresh = normalize_bid(
            VendorBid.model_validate(facts.commercial),
            project.target_currency, project.fx_rates).model_dump()
        if fresh != facts.normalized:
            facts.normalized = fresh
            pending.append(facts)

    if not pending:
        return 0

    with snapshots.transaction(root, slug):
        for facts in pending:
            snapshots.save_facts(root, slug, facts)

    events.append_event(root, slug, Event(
        at=_now(), run_id=events.new_run_id(), actor="renormalize",
        action="fx.renormalized", detail={"vendors": len(pending)}))
    return len(pending)
```

Then wire it into `api/main.py::set_fx_rates`, after `proj.save_project(ROOT, project)` and before building the response, so the returned setup state already reflects the recomputed totals:

```python
    proj.save_project(ROOT, project)
    renormalize(ROOT, slug)
    return _setup_state(project)
```

Note `_setup_state(project)` receives the in-memory project, whose `generation` is now stale relative to disk. Reload it — `return _setup_state(_load_or_404(slug))` — or the response under-reports `generation` by one.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_renormalize.py -v` then `python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add procurement/renormalize.py api/main.py tests/test_renormalize.py
git commit -m "feat(renormalize): apply FX rate changes without a re-extraction (BUG-005)"
```

---

### Task 4: Re-normalize existing stores once, on first read

**Files:**
- Modify: `procurement/store/layout.py:10` (`STORE_VERSION` 1 → 2)
- Modify: `procurement/renormalize.py` (add `migrate_normalization`)
- Modify: `procurement/pipeline.py` (`_live_fact_vendors` calls it)
- Modify: `procurement/project.py` (`create_project` stamps `store_version`)
- Test: `tests/test_renormalize.py`, `tests/test_store_models.py`

**Interfaces:**
- Consumes: `renormalize(root, slug) -> int` (Task 3)
- Produces: `migrate_normalization(root: str, slug: str) -> bool` — True when it migrated
- **Store invariant owned:** every project's `store_version` equals `layout.STORE_VERSION` after any read of it, and no project's stored `normalized` predates the current version's rules.

Projects ingested before this change hold totals computed under the 1.0 assumption — including the sample project baked into the Docker image. Task 3 only fires when rates change, so a project sitting at no-rates would keep its wrong numbers indefinitely.

Two corrections this task must make, both found by reading rather than assuming:

- `Project.store_version` is **dormant** — declared at `models.py:85`, written and read by nothing. `create_project` must start stamping it with `layout.STORE_VERSION`, or every newly created project reads as "needs migrating" forever.
- `tests/test_store_models.py:40` asserts the default is `1`. Once `STORE_VERSION` is 2 and `create_project` stamps it, that test is asserting the wrong thing — update it to assert against `layout.STORE_VERSION` rather than a literal, so it stops needing an edit on every future bump.

Completion is keyed on the stamp written **inside** the transaction, not inferred from disk state: `snapshots.transaction` is not a rollback (`snapshots.py:28-37`), so a half-finished migration must be retried rather than declared done. This migration is idempotent by construction — it derives every value from `commercial` facts it never modifies — so a retry recomputes the same answers and cannot lose data.

- [ ] **Step 1: Write the failing tests**

```python
from procurement.renormalize import migrate_normalization
from procurement.store import layout
from procurement.pipeline import load_dataset


def test_a_new_project_is_stamped_at_the_current_store_version(tmp_path):
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    assert load_project(root, "p").store_version == layout.STORE_VERSION


def test_migration_renormalizes_a_project_left_at_an_older_version(tmp_path):
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _client())
    # a store written before this change: a rate configured, but the stored
    # total still computed under the 1.0 assumption
    _set_rate(root, {"EUR": 1.08})
    facts = snapshots.load_facts(root, "p", "EUROVEND")
    facts.normalized = {"vendor": "EUROVEND", "normalized_currency": "USD",
                        "normalized_total": 1000.0, "adjustments": [],
                        "extraction_status": "ok"}
    snapshots.save_facts(root, "p", facts)
    project = load_project(root, "p")
    project.store_version = 1
    save_project(root, project)

    assert migrate_normalization(root, "p") is True

    facts = snapshots.load_facts(root, "p", "EUROVEND")
    assert abs(facts.normalized["normalized_total"] - 1080.0) <= 0.01
    assert load_project(root, "p").store_version == layout.STORE_VERSION


def test_migration_is_idempotent(tmp_path):
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _client())
    project = load_project(root, "p")
    project.store_version = 1
    save_project(root, project)

    assert migrate_normalization(root, "p") is True
    generation = load_project(root, "p").generation
    assert migrate_normalization(root, "p") is False
    assert load_project(root, "p").generation == generation


def test_reading_a_project_repeatedly_does_not_advance_generation(tmp_path):
    """The read-path hazard. Nothing else in the suite would catch this."""
    root = _eur_project(tmp_path)
    run_ingestion(root, "p", _client())
    load_dataset(root, "p")
    settled = load_project(root, "p").generation

    for _ in range(3):
        load_dataset(root, "p")

    assert load_project(root, "p").generation == settled
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_renormalize.py -v`
Expected: FAIL — `ImportError` on `migrate_normalization`, and the `store_version` test failing because `create_project` does not stamp.

- [ ] **Step 3: Write minimal implementation**

`procurement/store/layout.py`: `STORE_VERSION = 2`.

`procurement/project.py`, in `create_project`, set `store_version=layout.STORE_VERSION` on the `Project` it constructs.

`procurement/renormalize.py`:

```python
def migrate_normalization(root: str, slug: str) -> bool:
    """One-shot: bring a pre-BUG-005 store's normalized totals up to date.

    Idempotent, and safe to retry after a partial run -- every value is derived
    from `commercial` facts this never modifies.
    """
    project = load_project(root, slug)
    if project.store_version >= layout.STORE_VERSION:
        return False

    renormalize(root, slug)        # opens no transaction when nothing changes

    project = load_project(root, slug)     # reload: renormalize may have bumped
    project.store_version = layout.STORE_VERSION
    save_project(root, project)
    return True
```

`procurement/pipeline.py`, in `_live_fact_vendors`, beside the existing migration:

```python
    migrate.migrate_dataset_json(root, slug)
    renormalize_mod.migrate_normalization(root, slug)
```

Import it as a module (`from procurement import renormalize as renormalize_mod`) or import the function directly — either is fine, but do not import `pipeline` from `renormalize.py`, which would close the import cycle this module exists to avoid.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_renormalize.py tests/test_store_models.py -v` then `python -m pytest -q`
Expected: PASS. Update `tests/test_store_models.py:40` to compare against `layout.STORE_VERSION`.

- [ ] **Step 5: Commit**

```bash
git add procurement/store/layout.py procurement/renormalize.py procurement/pipeline.py procurement/project.py tests/
git commit -m "feat(store): re-normalize pre-BUG-005 stores once, on first read"
```

---

### Task 5: Carry the reason to the reader

**Files:**
- Modify: `procurement/models.py` (`ComparisonRow`)
- Modify: `procurement/compare.py:10-20`
- Modify: `procurement/export.py:7` (`_HEADERS`)
- Test: `tests/test_procurement_compare.py`, `tests/test_procurement_export.py`

**Interfaces:**
- Consumes: `NormalizedBid.normalization_status` (Task 1)
- Produces: `ComparisonRow.normalization_status: str = "ok"`; an export column of the same name
- **Store invariant owned:** none — `ComparisonRow` is computed on read and never stored. This task defends Task 1's invariant in the dataset and the export; Task 6 defends it in the screen the buyer reads.

`compare.py:21` already sorts `normalized_total is None` rows last. No sorting change.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_unconvertible_row_carries_its_reason_and_sorts_last():
    bids = [
        VendorBid(vendor="EUROVEND", currency="EUR", base_price=1000.0, freight_included=True),
        VendorBid(vendor="USVEND", currency="USD", base_price=1050.0, freight_included=True),
    ]
    normalized = [normalize_bid(b, "USD", {}) for b in bids]

    table = build_comparison(bids, normalized, "USD")

    assert table.rows[0].vendor == "USVEND"          # the only rankable bid
    assert table.rows[-1].vendor == "EUROVEND"
    assert table.rows[-1].normalized_total is None
    assert table.rows[-1].normalization_status == "no_fx_rate"
    assert table.rows[-1].raw_base_price == 1000.0   # the real price still shows
```

For `tests/test_procurement_export.py`, assert `"normalization_status"` appears in the exported header row.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_procurement_compare.py tests/test_procurement_export.py -v`
Expected: FAIL — `AttributeError: 'ComparisonRow' object has no attribute 'normalization_status'`.

- [ ] **Step 3: Write minimal implementation**

Add `normalization_status: str = "ok"` to `ComparisonRow`; in `build_comparison` pass `normalization_status=n.normalization_status if n else "ok"`; append `"normalization_status"` to `export._HEADERS`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add procurement/models.py procurement/compare.py procurement/export.py tests/
git commit -m "feat(compare,export): carry the no-rate reason to the reader (BUG-005)"
```

---

### Task 6: The statement says why, instead of dropping the row

**Files:**
- Modify: `procurement/statement.py:245-265` (the `normalised` cell)
- Test: `tests/test_statement_pricing.py`

**Interfaces:**
- Consumes: `NormalizedBid.normalization_status` as stored in `VendorFacts.normalized` (Tasks 1, 3)
- Produces: a `normalised` `StatementCell` with `total=None` and a `note` when the vendor's bid could not be converted
- **Store invariant owned:** none — the statement is computed on read. This task defends Task 1's invariant on the screen the buyer actually reads.

`statement.py:262` currently reads `if norm_total is not None and base is not None:`, so a vendor whose bid cannot be converted loses its Normalised row with no explanation. A silently absent row invites the same wrong conclusion as a fabricated number.

Emit the cell instead, with no total and a note naming the missing currency. This is an established pattern in this file, not a new one — line 279 already builds a cell whose note explains an absent number (`note="; ".join(_notes("base price not stated", ...))`). Follow it.

**No web change is needed.** `ComparativeStatement.tsx:274` already renders `cell?.note`. Verify that rather than editing the component.

Keep the existing `base is not None` gate exactly as it is. Its comment (lines 252-260) explains that gating on the *total* would also hide a genuine zero — a 100% discount really does normalise to nothing. The new branch is additive: an unconvertible bid, not a bid with an unknown base.

- [ ] **Step 1: Write the failing test**

```python
def test_an_unconvertible_bid_keeps_its_normalised_row_with_a_reason():
    """BUG-005 §1.3. A dropped row reads as 'nothing to compare here'; the
    buyer needs to know a rate is missing, not that the vendor is silent."""
    # build a project whose stored facts hold a EUR bid and no EUR rate,
    # following the fixture shape already used at test_statement_pricing.py:43
    statement = build_statement(...)

    row = next(r for r in statement.rows if r.key == "normalised")
    cell = row.cells["EUROVEND"]
    assert cell.total is None
    assert "EUR" in cell.note
```

Reuse the stored-facts fixture shape already in this file (`tests/test_statement_pricing.py:43` shows a `normalized` dict). Give that fixture `"normalized_total": None` and `"normalization_status": "no_fx_rate"`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_statement_pricing.py -k unconvertible -v`
Expected: FAIL — `StopIteration` or a `KeyError` on the vendor, because no `normalised` cell is emitted at all.

- [ ] **Step 3: Write minimal implementation**

```python
        norm_total = _money((normalized or {}).get("normalized_total"))
        if norm_total is not None and base is not None:
            cell("normalised",
                 f"Normalised (ex-VAT, ex-options, {project.target_currency})",
                 "priced", vendor, total=norm_total)
        elif (normalized or {}).get("normalization_status") == "no_fx_rate":
            # Emitted with no total: the row must not vanish, or its absence
            # reads as "nothing to compare" rather than "a rate is missing".
            cell("normalised",
                 f"Normalised (ex-VAT, ex-options, {project.target_currency})",
                 "priced", vendor,
                 note=f"no FX rate set for {(commercial or {}).get('currency')}")
```

Confirm the local name holding the vendor's commercial facts before using it — the snippet above assumes `commercial`, which may be spelled differently in this function.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_statement_pricing.py -v` then `python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add procurement/statement.py tests/test_statement_pricing.py
git commit -m "fix(statement): keep the Normalised row and say why it is empty (BUG-005)"
```

---

### Task 7: Setup form suggests EUR 1.08

**Files:**
- Modify: `web/src/pages/Setup.tsx:439-449` (the FX rates card's `useEffect`)
- Test: `web/src/pages/Setup.test.tsx`

**Interfaces:**
- Consumes: `ProjectSetup.fx_rates`, `ProjectSetup.target_currency`
- Produces: no new API surface
- **Store invariant owned:** none — this task is presentation. It defends §1.4: nothing reaches `project.fx_rates` without a human saving it.

**The prefill is a suggestion, not a default.** It appears only when `Object.keys(setup.fx_rates).length === 0` **and** `setup.target_currency === 'USD'` — 1.08 is a EUR→USD rate and is simply wrong for any other target, where being pre-filled would make it *less* likely to be questioned than an empty field.

It populates the form's editable rows only. Nothing is saved until the user clicks Save, which is what keeps the setup checklist at `Setup.tsx:151-157` honest: that checklist reads `Object.keys(setup.fx_rates).length > 0` from the **server's** state, so an unsaved suggestion leaves the step incomplete and the wizard still pointing at it.

Label it with its as-of date — `Suggested: 1.08 as of 2026-08-05 — check before saving.` The rate is a dated snapshot, not a live quote.

- [ ] **Step 1: Write the failing tests**

```tsx
describe('FX rate suggestion (BUG-005 §1.4)', () => {
  it('offers EUR 1.08 when no rates are set and the target is USD', () => {
    renderSetup({ fx_rates: {}, target_currency: 'USD' })
    expect(screen.getByDisplayValue('EUR')).toBeInTheDocument()
    expect(screen.getByDisplayValue('1.08')).toBeInTheDocument()
  })

  it('does not offer it when the target is not USD', () => {
    renderSetup({ fx_rates: {}, target_currency: 'GBP' })
    expect(screen.queryByDisplayValue('1.08')).not.toBeInTheDocument()
  })

  it('does not offer it when rates already exist', () => {
    renderSetup({ fx_rates: { EUR: 1.12 }, target_currency: 'USD' })
    expect(screen.getByDisplayValue('1.12')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('1.08')).not.toBeInTheDocument()
  })

  it('does not save the suggestion on its own', () => {
    renderSetup({ fx_rates: {}, target_currency: 'USD' })
    expect(saveFxRatesMock).not.toHaveBeenCalled()
  })
})
```

`renderSetup` and `saveFxRatesMock` are illustrative — match the harness conventions already in `web/src/pages/Setup.test.tsx` (its fixture at line 38 shows the `ProjectSetup` shape, including `fx_rates: {}`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test` in `web/`
Expected: FAIL — no suggested row is rendered.

- [ ] **Step 3: Write minimal implementation**

In the FX card's `useEffect` (currently `setRows(entries.map(...))`), when `entries.length === 0 && setup.target_currency === 'USD'`, set `[{ code: 'EUR', rate: '1.08' }]` instead. Render the dated label beside the card. Leave `save()` untouched — it already reads from `rows`, so a user saving an untouched suggestion is a deliberate act.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test` then `npm run build` in `web/`
Expected: PASS, and the build type-checks (`web/tsconfig.app.json` includes `src`, so test files are type-checked too).

- [ ] **Step 5: Commit**

```bash
git add web/
git commit -m "feat(web): suggest a dated EUR 1.08 rate without storing it (BUG-005)"
```

---

### Task 8: Integration — two-run mutation matrix

**Files:**
- Test: `tests/test_fx_normalization_lifecycle.py` (new)
- Modify: `BUGS_TRACKER.md` (close BUG-005), `CLAUDE.md` (baselines)

**Interfaces:**
- Consumes: everything above
- Produces: no new interface
- **Store invariant owned:** the union of Tasks 1, 3 and 4 across two runs — a stored `normalized` reflects the project's current rates and the vendor's current commercial facts, and nothing else.

Per-task TDD structurally produces single-run, single-module tests. Every phase-2 defect that survived to final review needed **two runs or two modules** to see. This task is the structural defence.

- [ ] **Step 1: Write the mutation matrix tests**

One test per row. Each names the invariant it defends in its docstring.

| mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|
| an FX rate is added, no `force` | Task 3 | totals update; zero LLM calls; `generation` +1 |
| an FX rate is changed | Task 3 | totals follow the new rate, not the old |
| an FX rate is withdrawn | Task 3 | totals revert to `None`/`no_fx_rate`; no stale number survives |
| an FX rate is set to 0 via the API | Task 2 | 422; stored totals unchanged |
| a newer revision of an already-extracted quotation arrives | Task 1 | the new revision's facts are normalized; no total from the superseded one survives |
| a document is deleted from its source folder | Task 3 | the vendor's facts are pruned; `renormalize` does not resurrect them |
| a second upload carries a sibling revision | Task 1 | lineage intact and the surviving revision's total is correct |
| an LLM call fails on run 1, succeeds on run 2 | Task 1 | `normalized_total` goes `None`→ a real total; status `ok` |
| an LLM call fails on both runs | Task 1 | stays `None` with `extraction_status: failed` and `normalization_status: ok` — **not** `no_fx_rate`; the two blanks must stay distinguishable |
| a re-extraction fails after a successful one | Task 1 | the previously-good stored total survives; it is never blanked |
| the project is read repeatedly | Task 4 | `generation` does not advance |
| a rate is withdrawn, then the statement is rebuilt | Task 6 | the Normalised row is still present, with a note naming the currency — it does not silently disappear |

Four of the nine rows the template pre-declares are **not applicable** and are deliberately absent rather than faked: prompt-version bumps, cross-class cache invalidation, and the omitted-optional-array row all concern the extraction cache, which this plan does not touch — no prompt, extractor or provider changes anywhere in it. Their existing coverage in `tests/test_pipeline_incremental.py` stands unchanged. Confirm that file still passes rather than duplicating its rows here.

- [ ] **Step 2: Run to verify they fail where expected**

Run: `python -m pytest tests/test_fx_normalization_lifecycle.py -v`
Expected: rows fail against any incomplete task. If a row passes before its task is implemented, that row is not testing what it claims.

- [ ] **Step 3: Verify the matrix is real, not decorative**

Reintroduce each defect one at a time and confirm **the intended row fails and nothing else does**:

| reinstate | expected failure |
|---|---|
| `rate = fx_rates.get(bid.currency, 1.0)` in `normalize.py` | the added/changed/withdrawn-rate rows and Task 1's unit tests |
| drop the `rate <= 0` guard | the set-to-0 row |
| always open a `transaction` in `renormalize` | the read-repeatedly row |
| skip `migrate_normalization` in `_live_fact_vendors` | the migration tests in Task 4 |
| restore `statement.py`'s bare `if norm_total is not None` gate | the withdrawn-rate statement row |

Revert each probe immediately after observing the failure. A row that still passes with its defect reinstated is not testing what it claims. Record the outcome in the commit message.

- [ ] **Step 4: Re-measure both baselines and close the bug**

Run: `python -m pytest -q`, then `npm test` and `npm run build` in `web/`.

Update `CLAUDE.md`'s baseline table. **It is currently stale even before this plan** — it records `897 passed, 3 skipped` but commit `1e47c35` added six tests, making the true pre-plan figure `903 passed, 3 skipped`. Measure the workstation row, then derive the CI row as `workstation − 4 corpus-coverage − 3 data/`, with those seven becoming skips. Editing the two rows independently is how they drift apart.

Close BUG-005 in `BUGS_TRACKER.md`: move it to the Closed table, and fill the Fix block with the spec, this plan, the commits, the tests that fail without the fix, and the verified counts.

- [ ] **Step 5: Commit**

```bash
git add tests/test_fx_normalization_lifecycle.py BUGS_TRACKER.md CLAUDE.md
git commit -m "test(fx): two-run mutation matrix; close BUG-005"
```
