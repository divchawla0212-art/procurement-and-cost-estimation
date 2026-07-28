# Store Foundation (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single end-of-run `dataset.json` write with a project-scoped store that records every source document and extracted fact durably, supports human overrides that survive re-runs, keeps an append-only audit log, and re-extracts only what changed.

**Architecture:** JSON snapshot files under `projects/<slug>/store/` are authoritative and human-readable. `projects/<slug>/index/store.db` is a derived SQLite cache, owned by exactly one module, rebuilt whenever the project's `generation` counter or a snapshot's mtime/size no longer matches what it was built from. Every snapshot write is atomic (`.tmp` + `os.replace`). A document's `content_sha256` plus the extractor's `prompt_version` is the cache key that makes re-runs incremental.

**Tech Stack:** Python 3.12, Pydantic v2, stdlib `sqlite3`, `hashlib`, `pytest`. No new dependencies.

## Global Constraints

- Python `>=3.12`; dependencies unchanged — `sqlite3` and `hashlib` are stdlib.
- Snapshots are the **only** authoritative store. `index/store.db` is derived and disposable; deleting it must change no query result.
- `procurement/store/index.py` is the **only** module that opens `store.db`. Nothing else writes to it.
- Every snapshot write is atomic: write `<name>.tmp`, then `os.replace()`.
- `generation` bumps **once per write transaction** (one ingestion run, or one UI edit), never once per file.
- `field_path` addresses list members by id, never by index — `technical[f-3a91].value`, not `technical[0].value`.
- `unanswered` / missing data is never silently coerced to a passing or zero value.
- Tests run key-free via `MockLLMClient`. No test may require `ANTHROPIC_API_KEY`.
- `projects/` is already in `.gitignore`, so `projects/*/index/` needs no new rule.
- Run all tests from the repo root: `python -m pytest`.

**Naming note:** the spec calls the override list `_overrides`; this plan uses `overrides` because Pydantic treats leading-underscore attributes as private. Same structure, valid identifier.

---

### Task 1: Paths, atomic writes, and content hashing

**Files:**
- Create: `procurement/store/__init__.py`
- Create: `procurement/store/layout.py`
- Test: `tests/test_store_layout.py`

**Interfaces:**
- Consumes: nothing. This module must import **only** stdlib — `project.py` will import it, so any import of `procurement.*` here creates a cycle.
- Produces: `project_dir(root, slug)`, `store_dir(root, slug)`, `index_path(root, slug)`, `documents_path(root, slug)`, `facts_path(root, slug, vendor)`, `events_path(root, slug)`, `atomic_write_json(path, data)`, `read_json(path, default=None)`, `content_sha256(path) -> str`, `doc_id_for(vendor, rel_path) -> str`, and the module constant `STORE_VERSION`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store_layout.py
import json
import os
import pytest
from procurement.store import layout


def test_atomic_write_creates_file(tmp_path):
    p = str(tmp_path / "a.json")
    layout.atomic_write_json(p, {"x": 1})
    assert json.load(open(p)) == {"x": 1}
    assert not os.path.exists(p + ".tmp")


def test_atomic_write_leaves_previous_intact_on_failure(tmp_path, monkeypatch):
    p = str(tmp_path / "a.json")
    layout.atomic_write_json(p, {"x": 1})

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(layout.os, "replace", boom)
    with pytest.raises(OSError):
        layout.atomic_write_json(p, {"x": 2})

    # the previous good copy must survive an interrupted write
    assert json.load(open(p)) == {"x": 1}


def test_read_json_missing_returns_default(tmp_path):
    assert layout.read_json(str(tmp_path / "nope.json"), default=[]) == []


def test_content_sha256_is_stable_and_content_sensitive(tmp_path):
    f = tmp_path / "d.bin"
    f.write_bytes(b"hello")
    first = layout.content_sha256(str(f))
    assert first == layout.content_sha256(str(f))
    f.write_bytes(b"hello!")
    assert layout.content_sha256(str(f)) != first


def test_doc_id_is_path_addressed_not_content_addressed():
    # same path -> same id regardless of content; different vendor -> different id
    a = layout.doc_id_for("KERUI", "vendors/KERUI/Quote.pdf")
    b = layout.doc_id_for("KERUI", "vendors/KERUI/Quote.pdf")
    c = layout.doc_id_for("ADPOWER", "vendors/ADPOWER/Quote.pdf")
    assert a == b and a != c
    assert len(a) == 12


def test_doc_id_for_rfq_document():
    assert layout.doc_id_for(None, "requirements/MR.pdf") != layout.doc_id_for("KERUI", "requirements/MR.pdf")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_store_layout.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'procurement.store'`

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/store/__init__.py
```

```python
# procurement/store/layout.py
"""Paths, atomic JSON IO, and content hashing for the project store.

Imports stdlib only: procurement.project imports this module, so importing
anything from procurement.* here would create a circular import.
"""
import hashlib
import json
import os

STORE_VERSION = 1


def project_dir(root: str, slug: str) -> str:
    return os.path.join(root, slug)


def store_dir(root: str, slug: str) -> str:
    return os.path.join(project_dir(root, slug), "store")


def index_path(root: str, slug: str) -> str:
    return os.path.join(project_dir(root, slug), "index", "store.db")


def documents_path(root: str, slug: str) -> str:
    return os.path.join(store_dir(root, slug), "documents.json")


def facts_path(root: str, slug: str, vendor: str) -> str:
    return os.path.join(store_dir(root, slug), "vendors", vendor, "facts.json")


def events_path(root: str, slug: str) -> str:
    return os.path.join(store_dir(root, slug), "events.jsonl")


def atomic_write_json(path: str, data) -> None:
    """Write JSON so an interrupted write cannot destroy the previous copy."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)
    try:
        os.replace(tmp, path)
    except OSError:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def read_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def content_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def doc_id_for(vendor: str | None, rel_path: str) -> str:
    """Path-addressed id: stable across content edits, so revision history
    attaches to a location and two vendors submitting a byte-identical file
    remain two distinct submissions."""
    key = f"{vendor or '_rfq'}/{rel_path.replace(os.sep, '/')}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_store_layout.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add procurement/store/__init__.py procurement/store/layout.py tests/test_store_layout.py
git commit -m "feat: store paths, atomic JSON writes, content hashing"
```

---

### Task 2: Store record models

**Files:**
- Create: `procurement/store/models.py`
- Modify: `procurement/models.py` (add `generation`, `store_version` to `Project`)
- Test: `tests/test_store_models.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `DocumentRecord`, `Override`, `Event`, `VendorFacts`, and `Project.generation: int` / `Project.store_version: int`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store_models.py
from procurement.store.models import DocumentRecord, Override, Event, VendorFacts
from procurement.models import Project


def test_document_record_defaults():
    d = DocumentRecord(doc_id="abc123def456", path="vendors/KERUI/Q.pdf",
                       vendor="KERUI", content_sha256="0" * 64)
    assert d.doc_class == "unclassified"
    assert d.extraction_status == "pending"
    assert d.superseded_by is None


def test_override_defaults_to_no_conflict():
    o = Override(field_path="commercial.base_price", value=1200.0,
                 extracted_value=1000.0, author="rahul",
                 at="2026-07-29T10:00:00Z", reason="typo in quote")
    assert o.conflict is False


def test_vendor_facts_round_trips_through_json():
    f = VendorFacts(vendor="KERUI", commercial={"base_price": 1000.0},
                    overrides=[Override(field_path="commercial.base_price",
                                        value=1200.0, author="rahul",
                                        at="2026-07-29T10:00:00Z",
                                        reason="corrected")])
    restored = VendorFacts.model_validate(f.model_dump())
    assert restored.overrides[0].value == 1200.0
    assert restored.technical == [] and restored.deviations == []


def test_event_carries_run_id_and_actor():
    e = Event(at="2026-07-29T10:00:00Z", run_id="r1", actor="pipeline",
              action="document.extracted", target="abc123def456")
    assert e.detail == {}


def test_project_gains_generation_and_store_version():
    p = Project(name="P", slug="p", created_at="2026-07-29T00:00:00Z")
    assert p.generation == 0
    assert p.store_version == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_store_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'procurement.store.models'`

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/store/models.py
from typing import Any
from pydantic import BaseModel


class Override(BaseModel):
    """A human correction. Survives re-extraction; `conflict` is set when a
    later extraction disagreed with `extracted_value`."""
    field_path: str
    value: Any
    extracted_value: Any = None
    author: str
    at: str
    reason: str
    conflict: bool = False


class DocumentRecord(BaseModel):
    doc_id: str
    path: str                       # relative to the project dir, posix separators
    vendor: str | None = None       # None for RFQ documents
    doc_class: str = "unclassified"
    classified_by: str | None = None
    revision_label: str | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    content_sha256: str
    text_source: str | None = None
    extraction_status: str = "pending"   # pending | ok | failed | skipped
    notes: str | None = None
    extracted_at: str | None = None
    extractor: str | None = None
    prompt_version: str | None = None


class VendorFacts(BaseModel):
    vendor: str
    commercial: dict | None = None      # BidExtraction dump, from the quotation
    normalized: dict | None = None      # NormalizedBid dump
    technical: list[dict] = []          # populated in phase 2
    deviations: list[dict] = []         # populated in phase 2
    overrides: list[Override] = []


class Event(BaseModel):
    at: str
    run_id: str
    actor: str                          # "pipeline" or a username
    action: str
    target: str | None = None
    detail: dict = {}
```

Then add two fields to the existing `Project` model in `procurement/models.py`, immediately after `requirements_file`:

```python
    requirements_file: str | None = None
    store_version: int = 1
    generation: int = 0
    status: str = "new"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_store_models.py tests/test_procurement_project.py -v`
Expected: PASS — the existing project tests must still pass, since both new fields have defaults.

- [ ] **Step 5: Commit**

```bash
git add procurement/store/models.py procurement/models.py tests/test_store_models.py
git commit -m "feat: store record models and project generation counter"
```

---

### Task 3: Snapshot read/write with transactional generation bumps

**Files:**
- Create: `procurement/store/snapshots.py`
- Modify: `procurement/project.py` (make `save_project` atomic)
- Test: `tests/test_store_snapshots.py`

**Interfaces:**
- Consumes: `layout.atomic_write_json`, `layout.read_json`, `layout.documents_path`, `layout.facts_path` (Task 1); `DocumentRecord`, `VendorFacts` (Task 2)
- Produces: `get_generation(root, slug)`, `bump_generation(root, slug)`, `transaction(root, slug)` context manager, `load_documents(root, slug)`, `save_documents(root, slug, docs)`, `load_facts(root, slug, vendor)`, `save_facts(root, slug, facts)`, `list_fact_vendors(root, slug)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store_snapshots.py
from procurement.project import create_project
from procurement.store import snapshots
from procurement.store.models import DocumentRecord, VendorFacts


def _doc(i="a" * 12):
    return DocumentRecord(doc_id=i, path=f"vendors/K/{i}.pdf", vendor="K",
                          content_sha256="0" * 64)


def test_documents_round_trip(tmp_path):
    create_project(str(tmp_path), "P")
    snapshots.save_documents(str(tmp_path), "p", [_doc()])
    got = snapshots.load_documents(str(tmp_path), "p")
    assert len(got) == 1 and got[0].doc_id == "a" * 12


def test_load_documents_empty_when_absent(tmp_path):
    create_project(str(tmp_path), "P")
    assert snapshots.load_documents(str(tmp_path), "p") == []


def test_facts_round_trip_per_vendor(tmp_path):
    create_project(str(tmp_path), "P")
    snapshots.save_facts(str(tmp_path), "p", VendorFacts(vendor="KERUI",
                                                         commercial={"base_price": 5.0}))
    assert snapshots.load_facts(str(tmp_path), "p", "KERUI").commercial == {"base_price": 5.0}
    assert snapshots.load_facts(str(tmp_path), "p", "NOPE") is None
    assert snapshots.list_fact_vendors(str(tmp_path), "p") == ["KERUI"]


def test_saves_alone_do_not_bump_generation(tmp_path):
    create_project(str(tmp_path), "P")
    before = snapshots.get_generation(str(tmp_path), "p")
    snapshots.save_documents(str(tmp_path), "p", [_doc()])
    snapshots.save_facts(str(tmp_path), "p", VendorFacts(vendor="K"))
    assert snapshots.get_generation(str(tmp_path), "p") == before


def test_transaction_bumps_generation_exactly_once(tmp_path):
    create_project(str(tmp_path), "P")
    before = snapshots.get_generation(str(tmp_path), "p")
    with snapshots.transaction(str(tmp_path), "p"):
        snapshots.save_documents(str(tmp_path), "p", [_doc()])
        snapshots.save_facts(str(tmp_path), "p", VendorFacts(vendor="K"))
        snapshots.save_facts(str(tmp_path), "p", VendorFacts(vendor="A"))
    assert snapshots.get_generation(str(tmp_path), "p") == before + 1


def test_failed_transaction_does_not_bump_generation(tmp_path):
    create_project(str(tmp_path), "P")
    before = snapshots.get_generation(str(tmp_path), "p")
    try:
        with snapshots.transaction(str(tmp_path), "p"):
            snapshots.save_documents(str(tmp_path), "p", [_doc()])
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert snapshots.get_generation(str(tmp_path), "p") == before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_store_snapshots.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'procurement.store.snapshots'`

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/store/snapshots.py
"""Read and write the authoritative JSON snapshots.

Generation semantics: individual saves never bump. Wrap a set of related
saves in `transaction()` and the counter bumps exactly once, on success.
"""
import os
from contextlib import contextmanager

from procurement.project import load_project, save_project
from procurement.store import layout
from procurement.store.models import DocumentRecord, VendorFacts


def get_generation(root: str, slug: str) -> int:
    return load_project(root, slug).generation


def bump_generation(root: str, slug: str) -> int:
    project = load_project(root, slug)
    project.generation += 1
    save_project(root, project)
    return project.generation


@contextmanager
def transaction(root: str, slug: str):
    """Bump `generation` once, only if the body completes without raising."""
    yield
    bump_generation(root, slug)


def load_documents(root: str, slug: str) -> list[DocumentRecord]:
    raw = layout.read_json(layout.documents_path(root, slug), default=[])
    return [DocumentRecord.model_validate(r) for r in raw]


def save_documents(root: str, slug: str, docs: list[DocumentRecord]) -> None:
    layout.atomic_write_json(layout.documents_path(root, slug),
                             [d.model_dump() for d in docs])


def load_facts(root: str, slug: str, vendor: str) -> VendorFacts | None:
    raw = layout.read_json(layout.facts_path(root, slug, vendor))
    return VendorFacts.model_validate(raw) if raw is not None else None


def save_facts(root: str, slug: str, facts: VendorFacts) -> None:
    layout.atomic_write_json(layout.facts_path(root, slug, facts.vendor),
                             facts.model_dump())


def list_fact_vendors(root: str, slug: str) -> list[str]:
    vdir = os.path.join(layout.store_dir(root, slug), "vendors")
    if not os.path.isdir(vdir):
        return []
    return sorted(v for v in os.listdir(vdir)
                  if os.path.exists(layout.facts_path(root, slug, v)))
```

Then make `save_project` atomic in `procurement/project.py`. Replace the body:

```python
from procurement.store import layout


def save_project(root: str, project: Project) -> None:
    layout.atomic_write_json(
        os.path.join(_project_dir(root, project.slug), "project.json"),
        project.model_dump(),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_store_snapshots.py tests/test_procurement_project.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add procurement/store/snapshots.py procurement/project.py tests/test_store_snapshots.py
git commit -m "feat: snapshot IO with transactional generation bumps"
```

---

### Task 4: Append-only event log

**Files:**
- Create: `procurement/store/events.py`
- Test: `tests/test_store_events.py`

**Interfaces:**
- Consumes: `layout.events_path` (Task 1); `Event` (Task 2)
- Produces: `new_run_id() -> str`, `append_event(root, slug, event)`, `read_events(root, slug, run_id=None) -> list[Event]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store_events.py
from procurement.project import create_project
from procurement.store import events
from procurement.store.models import Event


def _ev(run_id, action, target=None):
    return Event(at="2026-07-29T10:00:00Z", run_id=run_id, actor="pipeline",
                 action=action, target=target)


def test_events_append_in_order(tmp_path):
    create_project(str(tmp_path), "P")
    events.append_event(str(tmp_path), "p", _ev("r1", "run.started"))
    events.append_event(str(tmp_path), "p", _ev("r1", "document.extracted", "d1"))
    got = events.read_events(str(tmp_path), "p")
    assert [e.action for e in got] == ["run.started", "document.extracted"]


def test_events_filter_by_run(tmp_path):
    create_project(str(tmp_path), "P")
    events.append_event(str(tmp_path), "p", _ev("r1", "run.started"))
    events.append_event(str(tmp_path), "p", _ev("r2", "run.started"))
    assert len(events.read_events(str(tmp_path), "p", run_id="r2")) == 1


def test_read_events_empty_when_absent(tmp_path):
    create_project(str(tmp_path), "P")
    assert events.read_events(str(tmp_path), "p") == []


def test_appending_never_rewrites_earlier_lines(tmp_path):
    create_project(str(tmp_path), "P")
    events.append_event(str(tmp_path), "p", _ev("r1", "run.started"))
    first_line = open(events.layout.events_path(str(tmp_path), "p"),
                      encoding="utf-8").readline()
    events.append_event(str(tmp_path), "p", _ev("r1", "run.finished"))
    assert open(events.layout.events_path(str(tmp_path), "p"),
                encoding="utf-8").readline() == first_line


def test_run_ids_are_unique():
    assert events.new_run_id() != events.new_run_id()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_store_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'procurement.store.events'`

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/store/events.py
"""Append-only audit log. Never rewritten, only appended to."""
import json
import os
import uuid

from procurement.store import layout
from procurement.store.models import Event


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def append_event(root: str, slug: str, event: Event) -> None:
    path = layout.events_path(root, slug)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event.model_dump(), default=str) + "\n")


def read_events(root: str, slug: str, run_id: str | None = None) -> list[Event]:
    path = layout.events_path(root, slug)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            event = Event.model_validate(json.loads(line))
            if run_id is None or event.run_id == run_id:
                out.append(event)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_store_events.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add procurement/store/events.py tests/test_store_events.py
git commit -m "feat: append-only event log"
```

---

### Task 5: Override resolution and conflict detection

**Files:**
- Create: `procurement/store/overrides.py`
- Test: `tests/test_store_overrides.py`

**Interfaces:**
- Consumes: `Override` (Task 2)
- Produces: `get_by_path(obj, field_path)`, `set_by_path(obj, field_path, value)`, `apply_overrides(record, overrides) -> dict`, `reconcile(overrides, fresh_record) -> list[Override]`

**Path grammar:** dot-separated segments; a segment may carry an id selector in brackets. `commercial.base_price` walks dicts. `technical[f-3a91].value` selects the member of the `technical` list whose `fact_id` equals `f-3a91`. Index selectors are not supported by design — list order is not stable across re-extractions.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store_overrides.py
import pytest
from procurement.store import overrides as ov
from procurement.store.models import Override


def _rec():
    return {
        "commercial": {"base_price": 1000.0, "currency": "USD"},
        "technical": [{"fact_id": "f-1", "parameter": "h2s", "value": 40.0},
                      {"fact_id": "f-2", "parameter": "kw", "value": 550.0}],
    }


def _o(path, value, extracted=None):
    return Override(field_path=path, value=value, extracted_value=extracted,
                    author="rahul", at="2026-07-29T10:00:00Z", reason="r")


def test_get_by_dotted_path():
    assert ov.get_by_path(_rec(), "commercial.base_price") == 1000.0


def test_get_by_id_selector():
    assert ov.get_by_path(_rec(), "technical[f-2].value") == 550.0


def test_get_missing_path_returns_none():
    assert ov.get_by_path(_rec(), "commercial.nope") is None
    assert ov.get_by_path(_rec(), "technical[f-9].value") is None


def test_index_selectors_are_rejected():
    with pytest.raises(ValueError):
        ov.get_by_path(_rec(), "technical[0].value")


def test_apply_override_wins_over_extracted_value():
    out = ov.apply_overrides(_rec(), [_o("commercial.base_price", 1200.0, 1000.0)])
    assert out["commercial"]["base_price"] == 1200.0


def test_apply_override_survives_list_reordering():
    rec = _rec()
    rec["technical"].reverse()          # order changed by re-extraction
    out = ov.apply_overrides(rec, [_o("technical[f-1].value", 55.0, 40.0)])
    by_id = {f["fact_id"]: f for f in out["technical"]}
    assert by_id["f-1"]["value"] == 55.0
    assert by_id["f-2"]["value"] == 550.0


def test_reconcile_flags_conflict_when_extraction_moved():
    fresh = _rec()
    fresh["commercial"]["base_price"] = 1100.0      # re-extraction disagrees
    out = ov.reconcile([_o("commercial.base_price", 1200.0, 1000.0)], fresh)
    assert out[0].conflict is True
    assert out[0].value == 1200.0                   # override still wins
    assert out[0].extracted_value == 1100.0         # and records what changed


def test_reconcile_clears_conflict_when_extraction_agrees_again():
    prior = _o("commercial.base_price", 1200.0, 1000.0)
    prior.conflict = True
    fresh = _rec()                                   # back to 1000.0
    out = ov.reconcile([prior], fresh)
    assert out[0].conflict is False


def test_reconcile_leaves_untouched_paths_alone():
    fresh = _rec()
    out = ov.reconcile([_o("commercial.base_price", 1200.0, 1000.0)], fresh)
    assert out[0].conflict is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_store_overrides.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'procurement.store.overrides'`

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/store/overrides.py
"""Resolve human overrides against extracted records.

Overrides always win. When a fresh extraction disagrees with the value the
override was recorded against, the override is flagged `conflict` and both
values are kept for review — nothing is silently dropped either way.
"""
import copy
import re

from procurement.store.models import Override

_SEGMENT = re.compile(r"^(?P<name>[^\[\]]+)(?:\[(?P<sel>[^\[\]]+)\])?$")


def _parse(field_path: str) -> list[tuple[str, str | None]]:
    parts = []
    for raw in field_path.split("."):
        m = _SEGMENT.match(raw)
        if not m:
            raise ValueError(f"Malformed field_path segment: {raw!r}")
        sel = m.group("sel")
        if sel is not None and sel.isdigit():
            raise ValueError(
                f"Index selectors are not supported ({raw!r}); address list "
                "members by id, because list order is not stable across re-extractions"
            )
        parts.append((m.group("name"), sel))
    return parts


def _id_of(item: dict) -> str | None:
    for key in ("fact_id", "req_id", "doc_id", "clause_ref"):
        if key in item:
            return item[key]
    return None


def _walk(obj, parts, create: bool = False):
    """Return (container, last_key) or None when the path does not resolve."""
    cur = obj
    for name, sel in parts[:-1]:
        if not isinstance(cur, dict) or name not in cur:
            return None
        cur = cur[name]
        if sel is not None:
            if not isinstance(cur, list):
                return None
            match = next((i for i in cur if isinstance(i, dict) and _id_of(i) == sel), None)
            if match is None:
                return None
            cur = match
    name, sel = parts[-1]
    if sel is not None:
        if not isinstance(cur, dict) or not isinstance(cur.get(name), list):
            return None
        match = next((i for i in cur[name] if isinstance(i, dict) and _id_of(i) == sel), None)
        return None if match is None else (match, None)
    if not isinstance(cur, dict):
        return None
    if name not in cur and not create:
        return None
    return (cur, name)


def get_by_path(obj: dict, field_path: str):
    parts = _parse(field_path)
    found = _walk(obj, parts)
    if found is None:
        return None
    container, key = found
    return container if key is None else container.get(key)


def set_by_path(obj: dict, field_path: str, value) -> bool:
    parts = _parse(field_path)
    found = _walk(obj, parts, create=True)
    if found is None:
        return False
    container, key = found
    if key is None:
        return False
    container[key] = value
    return True


def apply_overrides(record: dict, overrides: list[Override]) -> dict:
    out = copy.deepcopy(record)
    for o in overrides:
        set_by_path(out, o.field_path, o.value)
    return out


def reconcile(overrides: list[Override], fresh_record: dict) -> list[Override]:
    """Update each override against a fresh extraction, flagging disagreement."""
    out = []
    for o in overrides:
        updated = o.model_copy()
        current = get_by_path(fresh_record, o.field_path)
        updated.conflict = current is not None and current != o.extracted_value
        if updated.conflict:
            updated.extracted_value = current
        out.append(updated)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_store_overrides.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add procurement/store/overrides.py tests/test_store_overrides.py
git commit -m "feat: override resolution with conflict detection"
```

---

### Task 6: Derived SQLite index

**Files:**
- Create: `procurement/store/index.py`
- Test: `tests/test_store_index.py`

**Interfaces:**
- Consumes: `layout.index_path`, `layout.documents_path`, `layout.facts_path` (Task 1); `snapshots.load_documents`, `snapshots.load_facts`, `snapshots.list_fact_vendors`, `snapshots.get_generation` (Task 3)
- Produces: `rebuild(root, slug)`, `query_documents(root, slug, vendor=None, doc_class=None) -> list[dict]`, `query_vendor_summary(root, slug) -> list[dict]`, `is_stale(root, slug) -> bool`

**Contract:** every public query calls `_ensure_fresh` first, which rebuilds on a `generation` mismatch or on any snapshot mtime/size mismatch. Any `sqlite3.Error` falls back to reading snapshots directly and logs a warning — a cache must never be able to break the app.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store_index.py
import os
import sqlite3
import pytest
from procurement.project import create_project
from procurement.store import index, layout, snapshots
from procurement.store.models import DocumentRecord, VendorFacts


def _seed(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    with snapshots.transaction(root, "p"):
        snapshots.save_documents(root, "p", [
            DocumentRecord(doc_id="d1", path="vendors/KERUI/Q.pdf", vendor="KERUI",
                           doc_class="quotation", content_sha256="a" * 64,
                           extraction_status="ok"),
            DocumentRecord(doc_id="d2", path="vendors/KERUI/BOM.pdf", vendor="KERUI",
                           doc_class="bom", content_sha256="b" * 64,
                           extraction_status="skipped"),
        ])
        snapshots.save_facts(root, "p", VendorFacts(vendor="KERUI",
                                                    commercial={"base_price": 1000.0},
                                                    normalized={"normalized_total": 1000.0}))
    return root


def test_query_documents_returns_rows(tmp_path):
    root = _seed(tmp_path)
    assert len(index.query_documents(root, "p")) == 2
    assert len(index.query_documents(root, "p", doc_class="quotation")) == 1
    assert len(index.query_documents(root, "p", vendor="NOPE")) == 0


def test_deleting_the_index_changes_no_result(tmp_path):
    root = _seed(tmp_path)
    before = index.query_documents(root, "p")
    os.remove(layout.index_path(root, "p"))
    assert index.query_documents(root, "p") == before


def test_stale_generation_triggers_rebuild_before_answering(tmp_path):
    root = _seed(tmp_path)
    index.query_documents(root, "p")            # builds
    assert index.is_stale(root, "p") is False

    with snapshots.transaction(root, "p"):      # generation moves on
        snapshots.save_documents(root, "p", [
            DocumentRecord(doc_id="d1", path="vendors/KERUI/Q.pdf", vendor="KERUI",
                           doc_class="quotation", content_sha256="a" * 64,
                           extraction_status="ok"),
        ])
    assert index.is_stale(root, "p") is True
    assert len(index.query_documents(root, "p")) == 1   # rebuilt, not stale data
    assert index.is_stale(root, "p") is False


def test_snapshot_edited_without_generation_bump_is_still_detected(tmp_path):
    root = _seed(tmp_path)
    index.query_documents(root, "p")
    # hand-edit the snapshot, bypassing the generation counter entirely
    layout.atomic_write_json(layout.documents_path(root, "p"), [])
    assert index.query_documents(root, "p") == []


def test_vendor_summary_joins_documents_and_facts(tmp_path):
    root = _seed(tmp_path)
    rows = index.query_vendor_summary(root, "p")
    assert rows == [{"vendor": "KERUI", "documents": 2, "extracted": 1,
                     "normalized_total": 1000.0}]


def test_index_is_never_opened_writable_outside_rebuild(tmp_path, monkeypatch):
    root = _seed(tmp_path)
    index.query_documents(root, "p")
    real_connect = sqlite3.connect
    opened = []

    def spy(path, *a, **k):
        opened.append(k.get("uri", False))
        return real_connect(path, *a, **k)

    monkeypatch.setattr(index.sqlite3, "connect", spy)
    index.query_documents(root, "p")
    assert opened and all(opened), "queries must open the index read-only (uri=True)"


def test_corrupt_index_falls_back_to_snapshots(tmp_path):
    root = _seed(tmp_path)
    index.query_documents(root, "p")
    with open(layout.index_path(root, "p"), "wb") as fh:
        fh.write(b"not a database")
    assert len(index.query_documents(root, "p")) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_store_index.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'procurement.store.index'`

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/store/index.py
"""Derived SQLite index over the JSON snapshots.

This module is the ONLY one that opens store.db. The index is disposable:
deleting it must change no query result. Every query checks freshness first
and rebuilds on any mismatch, so stale reads are structurally impossible.
"""
import logging
import os
import sqlite3

from procurement.store import layout, snapshots

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE documents (
    doc_id TEXT PRIMARY KEY, path TEXT, vendor TEXT, doc_class TEXT,
    content_sha256 TEXT, extraction_status TEXT, superseded_by TEXT
);
CREATE TABLE facts (
    vendor TEXT PRIMARY KEY, normalized_total REAL
);
CREATE INDEX idx_documents_vendor ON documents(vendor);
CREATE INDEX idx_documents_class ON documents(doc_class);
"""


def _snapshot_stat(root: str, slug: str) -> str:
    """mtime+size of every snapshot, so hand-edits that bypass the generation
    counter are still detected."""
    paths = [layout.documents_path(root, slug)]
    paths += [layout.facts_path(root, slug, v) for v in snapshots.list_fact_vendors(root, slug)]
    parts = []
    for p in sorted(paths):
        if os.path.exists(p):
            st = os.stat(p)
            parts.append(f"{os.path.basename(os.path.dirname(p))}/{os.path.basename(p)}:{st.st_mtime_ns}:{st.st_size}")
    return "|".join(parts)


def _expected_meta(root: str, slug: str) -> dict:
    return {"generation": str(snapshots.get_generation(root, slug)),
            "snapshot_stat": _snapshot_stat(root, slug)}


def is_stale(root: str, slug: str) -> bool:
    path = layout.index_path(root, slug)
    if not os.path.exists(path):
        return True
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            rows = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error:
        return True
    return rows != _expected_meta(root, slug)


def rebuild(root: str, slug: str) -> None:
    path = layout.index_path(root, slug)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    meta = _expected_meta(root, slug)      # captured BEFORE reading, so a
    docs = snapshots.load_documents(root, slug)   # concurrent write invalidates
    with sqlite3.connect(tmp) as conn:
        conn.executescript(_SCHEMA)
        conn.executemany(
            "INSERT INTO documents VALUES (?,?,?,?,?,?,?)",
            [(d.doc_id, d.path, d.vendor, d.doc_class, d.content_sha256,
              d.extraction_status, d.superseded_by) for d in docs])
        for vendor in snapshots.list_fact_vendors(root, slug):
            facts = snapshots.load_facts(root, slug, vendor)
            total = (facts.normalized or {}).get("normalized_total") if facts else None
            conn.execute("INSERT INTO facts VALUES (?,?)", (vendor, total))
        conn.executemany("INSERT INTO meta VALUES (?,?)", list(meta.items()))
    os.replace(tmp, path)


def _connect_fresh(root: str, slug: str) -> sqlite3.Connection:
    if is_stale(root, slug):
        rebuild(root, slug)
    return sqlite3.connect(f"file:{layout.index_path(root, slug)}?mode=ro", uri=True)


def query_documents(root: str, slug: str, vendor: str | None = None,
                    doc_class: str | None = None) -> list[dict]:
    sql = "SELECT doc_id, path, vendor, doc_class, extraction_status FROM documents WHERE 1=1"
    args: list = []
    if vendor is not None:
        sql += " AND vendor = ?"
        args.append(vendor)
    if doc_class is not None:
        sql += " AND doc_class = ?"
        args.append(doc_class)
    sql += " ORDER BY path"
    try:
        with _connect_fresh(root, slug) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, args)]
    except sqlite3.Error as exc:
        log.warning("index unavailable (%s); falling back to snapshots", exc)
        return _fallback_documents(root, slug, vendor, doc_class)


def _fallback_documents(root, slug, vendor, doc_class) -> list[dict]:
    out = []
    for d in snapshots.load_documents(root, slug):
        if vendor is not None and d.vendor != vendor:
            continue
        if doc_class is not None and d.doc_class != doc_class:
            continue
        out.append({"doc_id": d.doc_id, "path": d.path, "vendor": d.vendor,
                    "doc_class": d.doc_class, "extraction_status": d.extraction_status})
    return sorted(out, key=lambda r: r["path"])


def query_vendor_summary(root: str, slug: str) -> list[dict]:
    sql = """
        SELECT d.vendor AS vendor,
               COUNT(*) AS documents,
               SUM(CASE WHEN d.extraction_status = 'ok' THEN 1 ELSE 0 END) AS extracted,
               f.normalized_total AS normalized_total
        FROM documents d LEFT JOIN facts f ON f.vendor = d.vendor
        WHERE d.vendor IS NOT NULL
        GROUP BY d.vendor ORDER BY d.vendor
    """
    try:
        with _connect_fresh(root, slug) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql)]
    except sqlite3.Error as exc:
        log.warning("index unavailable (%s); falling back to snapshots", exc)
        rows: dict[str, dict] = {}
        for d in snapshots.load_documents(root, slug):
            if d.vendor is None:
                continue
            r = rows.setdefault(d.vendor, {"vendor": d.vendor, "documents": 0,
                                           "extracted": 0, "normalized_total": None})
            r["documents"] += 1
            r["extracted"] += 1 if d.extraction_status == "ok" else 0
        for vendor, r in rows.items():
            facts = snapshots.load_facts(root, slug, vendor)
            r["normalized_total"] = (facts.normalized or {}).get("normalized_total") if facts else None
        return [rows[k] for k in sorted(rows)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_store_index.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add procurement/store/index.py tests/test_store_index.py
git commit -m "feat: derived SQLite index with enforced freshness contract"
```

---

### Task 7: Migrate an existing dataset.json into the store

**Files:**
- Create: `procurement/store/migrate.py`
- Test: `tests/test_store_migrate.py`

**Interfaces:**
- Consumes: `layout.read_json` (Task 1); `VendorFacts` (Task 2); `snapshots.save_facts`, `snapshots.load_facts`, `snapshots.transaction` (Task 3)
- Produces: `migrate_dataset_json(root, slug) -> bool` — True when a migration was performed, False when there was nothing to migrate or the store already holds facts

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store_migrate.py
import json
import os
from procurement.project import create_project
from procurement.store import migrate, snapshots


def _write_dataset(root, slug):
    path = os.path.join(root, slug, "dataset.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "bids": [{"vendor": "KERUI", "base_price": 1000.0, "currency": "USD"},
                     {"vendor": "MKON", "base_price": 900.0, "currency": "EUR"}],
            "normalized": [{"vendor": "KERUI", "normalized_total": 1000.0},
                           {"vendor": "MKON", "normalized_total": 972.0}],
            "comparison": {"target_currency": "USD", "rows": []},
        }, fh)
    return path


def test_migrates_bids_and_normalized_into_facts(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    _write_dataset(root, "p")
    assert migrate.migrate_dataset_json(root, "p") is True

    kerui = snapshots.load_facts(root, "p", "KERUI")
    assert kerui.commercial["base_price"] == 1000.0
    assert kerui.normalized["normalized_total"] == 1000.0
    assert snapshots.load_facts(root, "p", "MKON").normalized["normalized_total"] == 972.0


def test_migration_bumps_generation_once(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    _write_dataset(root, "p")
    before = snapshots.get_generation(root, "p")
    migrate.migrate_dataset_json(root, "p")
    assert snapshots.get_generation(root, "p") == before + 1


def test_migration_is_idempotent(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    _write_dataset(root, "p")
    assert migrate.migrate_dataset_json(root, "p") is True
    gen = snapshots.get_generation(root, "p")
    assert migrate.migrate_dataset_json(root, "p") is False
    assert snapshots.get_generation(root, "p") == gen


def test_migration_leaves_the_source_file_alone(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    path = _write_dataset(root, "p")
    migrate.migrate_dataset_json(root, "p")
    assert os.path.exists(path)


def test_no_dataset_is_not_an_error(tmp_path):
    root = str(tmp_path)
    create_project(root, "P")
    assert migrate.migrate_dataset_json(root, "p") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_store_migrate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'procurement.store.migrate'`

- [ ] **Step 3: Write minimal implementation**

```python
# procurement/store/migrate.py
"""One-shot import of a pre-store dataset.json into the project store.

Idempotent: a project that already has vendor facts is left untouched. The
source dataset.json is never modified or deleted.
"""
import os

from procurement.store import layout, snapshots
from procurement.store.models import VendorFacts


def migrate_dataset_json(root: str, slug: str) -> bool:
    if snapshots.list_fact_vendors(root, slug):
        return False                      # store already populated
    path = os.path.join(layout.project_dir(root, slug), "dataset.json")
    data = layout.read_json(path)
    if not data:
        return False

    normalized_by_vendor = {n.get("vendor"): n for n in data.get("normalized", [])}
    bids = data.get("bids", [])
    if not bids:
        return False

    with snapshots.transaction(root, slug):
        for bid in bids:
            vendor = bid.get("vendor")
            if not vendor:
                continue
            snapshots.save_facts(root, slug, VendorFacts(
                vendor=vendor,
                commercial=bid,
                normalized=normalized_by_vendor.get(vendor),
            ))
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_store_migrate.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add procurement/store/migrate.py tests/test_store_migrate.py
git commit -m "feat: idempotent dataset.json migration into the store"
```

---

### Task 8: Rewire the pipeline onto the store with incremental re-runs

**Files:**
- Modify: `procurement/pipeline.py` (full rewrite)
- Modify: `shared/llm/mock_client.py` (add a call counter the tests need)
- Modify: `tests/test_procurement_pipeline.py` (existing end-to-end test)
- Test: `tests/test_pipeline_incremental.py`

**Interfaces:**
- Consumes: everything from Tasks 1–7
- Produces: `inventory_documents(root, slug) -> list[DocumentRecord]`, `run_ingestion(root, slug, client, pdf_fallback=None, force=False) -> dict`, `load_dataset(root, slug) -> dict | None` (kept, now reading from the store)

**Behaviour:** every vendor file is inventoried and hashed. Only the file `pick_quote()` selects is extracted — classification and multi-document extraction are phase 2. A document is re-extracted only when its `content_sha256` or the extractor's `prompt_version` changed, or `force=True`. `project.status` becomes `done`, `done_with_failures`, or `failed`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_incremental.py
import io
import zipfile
from procurement.project import create_project, unpack_vendor_zip, load_project
from procurement.pipeline import run_ingestion, inventory_documents
from procurement.store import events, snapshots
from shared.llm.mock_client import MockLLMClient


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, d in entries.items():
            zf.writestr(n, d)
    return buf.getvalue()


def _project(tmp_path):
    root = str(tmp_path)
    create_project(root, "P", target_currency="USD")
    z = tmp_path / "v.zip"
    z.write_bytes(_zip({
        "KERUI/Quotation.txt": b"base price 1000",
        "KERUI/BOM.txt": b"bill of materials",
        "MKON/Quotation.txt": b"base price 900",
    }))
    unpack_vendor_zip(root, "p", str(z))
    return root


def _client():
    return MockLLMClient(response={"currency": "USD", "base_price": 1000.0,
                                   "freight_included": True})


def test_inventory_records_every_vendor_file(tmp_path):
    root = _project(tmp_path)
    docs = inventory_documents(root, "p")
    assert len(docs) == 3
    assert all(d.content_sha256 for d in docs)
    assert {d.vendor for d in docs} == {"KERUI", "MKON"}


def test_first_run_extracts_one_document_per_vendor(tmp_path):
    root = _project(tmp_path)
    client = _client()
    run_ingestion(root, "p", client)
    assert len(client.calls) == 2                 # the quote only, not the BOM
    docs = {d.path: d for d in snapshots.load_documents(root, "p")}
    assert sum(1 for d in docs.values() if d.extraction_status == "ok") == 2
    assert sum(1 for d in docs.values() if d.extraction_status == "skipped") == 1


def test_rerun_with_no_changes_makes_zero_llm_calls(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    second = _client()
    run_ingestion(root, "p", second)
    assert second.calls == []


def test_touching_one_document_reextracts_exactly_that_one(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    (tmp_path / "p" / "vendors" / "KERUI" / "Quotation.txt").write_bytes(b"base price 2000")
    second = _client()
    run_ingestion(root, "p", second)
    assert len(second.calls) == 1


def test_force_reextracts_everything(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    second = _client()
    run_ingestion(root, "p", second, force=True)
    assert len(second.calls) == 2


def test_facts_are_stored_per_vendor(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    kerui = snapshots.load_facts(root, "p", "KERUI")
    assert kerui.commercial["base_price"] == 1000.0
    assert kerui.normalized["normalized_total"] == 1000.0


def test_run_appends_events(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    actions = [e.action for e in events.read_events(root, "p")]
    assert actions[0] == "run.started" and actions[-1] == "run.finished"
    assert "document.extracted" in actions
    assert "document.skipped" in actions


def test_status_reports_failures_instead_of_always_done(tmp_path):
    root = _project(tmp_path)
    # a client that always raises -> every extraction fails
    class Boom:
        supports_vision = True

        def __init__(self):
            self.calls = []      # instance attribute: a mutable class attribute
                                 # would be shared across every instance

        def classify_structure(self, *a, **k):
            raise RuntimeError("no")

    run_ingestion(root, "p", Boom())
    assert load_project(root, "p").status == "failed"


def test_partial_failure_is_reported_distinctly(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", _client())
    # corrupt one vendor's quote so only it fails on the next forced run
    (tmp_path / "p" / "vendors" / "MKON" / "Quotation.txt").write_bytes(b"x")

    class OneBad:
        supports_vision = True

        def __init__(self):
            self.calls = []

        def classify_structure(self, prompt, schema, context_text, images=None):
            self.calls.append(context_text)
            if context_text.strip() == "x":
                raise RuntimeError("unreadable")
            return {"currency": "USD", "base_price": 1000.0, "freight_included": True}

    run_ingestion(root, "p", OneBad(), force=True)
    assert load_project(root, "p").status == "done_with_failures"
```

Also update the existing end-to-end test's persistence assertion in `tests/test_procurement_pipeline.py`, replacing its last two lines:

```python
    # persisted to the store
    from procurement.store import snapshots
    assert snapshots.load_facts(str(tmp_path), "proj", "KERUI").commercial is not None
    assert snapshots.load_facts(str(tmp_path), "proj", "ADPOWER").commercial is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline_incremental.py -v`
Expected: FAIL with `ImportError: cannot import name 'inventory_documents' from 'procurement.pipeline'`

- [ ] **Step 3: Write minimal implementation**

First add the call counter to `shared/llm/mock_client.py` — `last_call` is kept so existing tests still pass:

```python
class MockLLMClient:
    def __init__(self, response: dict, supports_vision: bool = True):
        self._response = response
        self.supports_vision = supports_vision
        self.last_call: dict | None = None
        self.calls: list[dict] = []

    def classify_structure(self, prompt, output_schema, context_text, images=None) -> dict:
        self.last_call = {"prompt": prompt, "context_text": context_text}
        self.calls.append(self.last_call)
        return copy.deepcopy(self._response)
```

Then rewrite `procurement/pipeline.py`:

```python
import os
from datetime import datetime, timezone

from procurement.project import load_project, save_project, vendor_files
from procurement.extract import extract_bid
from procurement.normalize import normalize_bid
from procurement.compare import build_comparison
from procurement.quote_select import pick_quote
from procurement.models import VendorBid, NormalizedBid
from procurement.store import events, layout, migrate, snapshots
from procurement.store.models import DocumentRecord, Event, VendorFacts
from procurement.store.overrides import apply_overrides, reconcile

PROMPT_VERSION = "bid_extract_v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def inventory_documents(root: str, slug: str) -> list[DocumentRecord]:
    """Hash and record every vendor file. Classification is phase 2, so
    doc_class stays 'unclassified' here."""
    project = load_project(root, slug)
    pdir = layout.project_dir(root, slug)
    out: list[DocumentRecord] = []
    for vendor in project.vendors:
        for path in vendor_files(root, slug, vendor):
            rel = os.path.relpath(path, pdir).replace(os.sep, "/")
            out.append(DocumentRecord(
                doc_id=layout.doc_id_for(vendor, rel),
                path=rel,
                vendor=vendor,
                content_sha256=layout.content_sha256(path),
            ))
    return out


def run_ingestion(root: str, slug: str, client, pdf_fallback=None,
                  force: bool = False) -> dict:
    migrate.migrate_dataset_json(root, slug)

    project = load_project(root, slug)
    run_id = events.new_run_id()
    events.append_event(root, slug, Event(at=_now(), run_id=run_id, actor="pipeline",
                                          action="run.started"))

    prior_docs = {d.doc_id: d for d in snapshots.load_documents(root, slug)}
    fresh_docs = inventory_documents(root, slug)
    pdir = layout.project_dir(root, slug)

    quote_rel_by_vendor: dict[str, str] = {}
    for vendor in project.vendors:
        quote = pick_quote(vendor_files(root, slug, vendor))
        if quote:
            quote_rel_by_vendor[vendor] = os.path.relpath(quote, pdir).replace(os.sep, "/")

    documents: list[DocumentRecord] = []
    extracted, failed = 0, 0

    with snapshots.transaction(root, slug):
        for doc in fresh_docs:
            prior = prior_docs.get(doc.doc_id)
            is_quote = quote_rel_by_vendor.get(doc.vendor) == doc.path

            if not is_quote:
                doc.extraction_status = "skipped"
                doc.notes = "not the selected quotation document"
                documents.append(doc)
                events.append_event(root, slug, Event(
                    at=_now(), run_id=run_id, actor="pipeline",
                    action="document.skipped", target=doc.doc_id,
                    detail={"reason": "not_quote"}))
                continue

            unchanged = (prior is not None
                         and prior.content_sha256 == doc.content_sha256
                         and prior.prompt_version == PROMPT_VERSION
                         and prior.extraction_status == "ok")
            if unchanged and not force:
                documents.append(prior)
                extracted += 1
                events.append_event(root, slug, Event(
                    at=_now(), run_id=run_id, actor="pipeline",
                    action="document.skipped", target=doc.doc_id,
                    detail={"reason": "unchanged"}))
                continue

            bid = extract_bid(doc.vendor, [os.path.join(pdir, doc.path)],
                              client, pdf_fallback=pdf_fallback)
            doc.extraction_status = bid.extraction_status
            doc.notes = bid.notes
            doc.extracted_at = _now()
            doc.extractor = f"llm:{PROMPT_VERSION}"
            doc.prompt_version = PROMPT_VERSION
            doc.doc_class = "quotation"
            doc.classified_by = "rule"
            documents.append(doc)

            if bid.extraction_status == "ok":
                extracted += 1
            else:
                failed += 1

            prior_facts = snapshots.load_facts(root, slug, doc.vendor)
            prior_overrides = prior_facts.overrides if prior_facts else []
            commercial = bid.model_dump()
            overrides = reconcile(prior_overrides, commercial)
            resolved = apply_overrides(commercial, overrides)
            normalized = normalize_bid(
                VendorBid.model_validate(resolved),
                project.target_currency, project.fx_rates)

            snapshots.save_facts(root, slug, VendorFacts(
                vendor=doc.vendor,
                commercial=resolved,
                normalized=normalized.model_dump(),
                technical=prior_facts.technical if prior_facts else [],
                deviations=prior_facts.deviations if prior_facts else [],
                overrides=overrides))

            events.append_event(root, slug, Event(
                at=_now(), run_id=run_id, actor="pipeline",
                action="document.extracted", target=doc.doc_id,
                detail={"status": bid.extraction_status}))
            for o in overrides:
                if o.conflict:
                    events.append_event(root, slug, Event(
                        at=_now(), run_id=run_id, actor="pipeline",
                        action="override.conflicted", target=doc.doc_id,
                        detail={"field_path": o.field_path}))

        snapshots.save_documents(root, slug, documents)

    project = load_project(root, slug)
    project.status = ("failed" if extracted == 0 and failed
                      else "done_with_failures" if failed else "done")
    save_project(root, project)

    events.append_event(root, slug, Event(
        at=_now(), run_id=run_id, actor="pipeline", action="run.finished",
        detail={"extracted": extracted, "failed": failed,
                "documents": len(documents), "status": project.status}))

    return load_dataset(root, slug) or {}


def load_dataset(root: str, slug: str) -> dict | None:
    """Assemble the UI-facing view from the store."""
    vendors = snapshots.list_fact_vendors(root, slug)
    if not vendors:
        return None
    project = load_project(root, slug)
    bids, normalized = [], []
    for vendor in vendors:
        facts = snapshots.load_facts(root, slug, vendor)
        if facts is None:
            continue
        if facts.commercial:
            bids.append(facts.commercial)
        if facts.normalized:
            normalized.append(facts.normalized)
    comparison = build_comparison(
        [VendorBid.model_validate(b) for b in bids],
        [NormalizedBid.model_validate(n) for n in normalized],
        project.target_currency)
    return {"bids": bids, "normalized": normalized,
            "comparison": comparison.model_dump()}
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -v`
Expected: PASS — the new incremental tests plus every pre-existing test. The portal reads `load_dataset()`, whose return shape is unchanged, so `portal/app.py` needs no edit in this phase.

- [ ] **Step 5: Commit**

```bash
git add procurement/pipeline.py shared/llm/mock_client.py tests/test_pipeline_incremental.py tests/test_procurement_pipeline.py
git commit -m "feat: pipeline writes to the store with incremental re-extraction"
```

---

## Verification of Phase 1 done-criteria

Run from the repo root after Task 8:

```bash
python -m pytest -v
```

Maps to the spec's §11 phase 1 exit criteria:

| criterion | proving test |
|---|---|
| Re-running an unchanged project makes zero LLM calls | `test_rerun_with_no_changes_makes_zero_llm_calls` |
| Changing one document re-extracts exactly that document | `test_touching_one_document_reextracts_exactly_that_one` |
| Deleting `store.db` changes nothing | `test_deleting_the_index_changes_no_result` |
| An existing `dataset.json` migrates cleanly | `test_migrates_bids_and_normalized_into_facts`, `test_migration_is_idempotent` |
| An interrupted write cannot destroy the previous copy | `test_atomic_write_leaves_previous_intact_on_failure` |
| Overrides survive re-runs, conflicts surfaced | `test_reconcile_flags_conflict_when_extraction_moved` |
| `project.status` is honest | `test_status_reports_failures_instead_of_always_done`, `test_partial_failure_is_reported_distinctly` |
