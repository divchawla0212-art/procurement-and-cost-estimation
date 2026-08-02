# LLM Provider Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a reviewer pick the extraction provider in the React Setup wizard, applied to that ingestion run only.

**Architecture:** `get_client()` gains an optional provider argument, so the API builds the client for the requested provider and passes it to `run_ingestion` — the environment is never mutated. `GET /setup` reports a readiness catalog for all five providers; `POST /ingest` accepts an optional `provider` and rejects unknown or unready ones with 400 before any work starts.

**Tech Stack:** FastAPI + Pydantic, pytest with `fastapi.testclient`, React 19 + TypeScript + Vite, `shared/llm/mock_client.py` for key-free tests.

Implements [the LLM provider selection design](../specs/2026-08-02-llm-provider-selection-design.md).

## Global Constraints

- **No test may require a provider API key.** Every test runs against `mock` or a monkeypatched env. This is the repository rule in CLAUDE.md.
- **Green baseline before and after:** `python -m pytest` from the repo root gives **649 passed, 3 skipped, 1 failed** on a workstation with `.env` and `data/` present. The one failure is `tests/test_portal_app.py::test_missing_api_key_does_not_block_creation` and it is **not yours to fix**. On a clean checkout the baseline is 647 passed, 6 skipped, 0 failed.
- **The five providers are exactly:** `anthropic`, `openai`, `gemini`, `bedrock`, `mock`. This list comes from `shared/llm/factory.py` and appears verbatim in `PROVIDER_KEYS`.
- **Key variables, verbatim:** `anthropic` → `ANTHROPIC_API_KEY`; `openai` → `OPENAI_API_KEY`; `gemini` → `GEMINI_API_KEY`; `bedrock` → `None`; `mock` → `None`.
- **No API key is ever entered, transported, or echoed by the browser.** The UI reports presence only.
- **`os.environ` is never assigned in `api/`.** That mechanism is what this design exists to avoid.
- **The existing three keys of the `provider` object** (`provider`, `needs_key`, `ready`) keep describing the **server default**, not the selection. `tests/test_api_setup.py::test_setup_reports_provider_state` must pass untouched.
- Run all commands from the repo root. On this Windows workstation the interpreter is `.venv/Scripts/python.exe`; on Linux/CI it is `.venv/bin/python`.

## A note on this plan and `PLAN-TEMPLATE.md`

The template's Rule 1 requires every task to name a **store invariant owned**, and Rule 2 requires the integration task to carry a two-run mutation matrix with nine named rows.

**This feature writes nothing to the store.** There is no new persisted collection, no snapshot write, and no `generation` bump — §7 of the spec is explicit. Applying Rules 1 and 2 by fabricating nine extraction-defect rows would produce exactly the decoration the template warns against ("a row defending no named invariant is decoration").

So they are applied in their honest form:

- **Rule 1** — every task names a store invariant, and for this feature the invariant is a *negative* one: a rejected or misconfigured provider must leave the store byte-identical. That is a real invariant, assertable over a loaded snapshot, and genuinely at risk — a validation bug that lets an unready provider through starts a run that mutates the store before failing.
- **Rule 2** — the integration task (Task 5) carries a two-run matrix, but its mutations are **provider mutations between runs**, not document mutations. It defends the one thing this design's mechanism could plausibly break: leakage of provider choice from one run into the next. The nine document-mutation rows belong to the ingestion pipeline, which this plan does not touch; they are already defended by the phase-2 and phase-3 suites and re-asserting them here would test other people's code.
- **Rule 3** — applied literally. The banner is below.

> **Reference code below is intent, not paste-able.** It shows the shape and the reasoning, and it has not been executed. Read it, then write the implementation against the test.

**Load-bearing** across every reference block: the exact provider ids and key-variable names; the **400 vs 502** distinction; `provider=None` meaning "read the env"; validation happening **before** `run_ingestion` is called; and the `provider` object's three legacy keys keeping their current meaning. **Illustrative:** exact wording of error strings, exact JSX layout, exact CSS class composition.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `shared/llm/factory.py` | Modify (~line 10) | Resolve a provider name — from the argument if given, else the env — to a client. |
| `tests/test_llm_factory.py` | Create or extend | The argument-beats-env contract, key-free. |
| `api/main.py` | Modify (~lines 124-136, 241-262) | `PROVIDER_KEYS`, the readiness catalog, and provider validation on the ingest route. |
| `tests/test_api_setup.py` | Extend | Catalog shape, the four ingest cases, and the store-untouched assertion. |
| `web/src/types.ts` | Modify (~lines 59-63) | `ProviderOption`, and `catalog` on `ProviderState`. |
| `web/src/api.ts` | Modify (~line 97) | `runIngestion` carries the optional provider. |
| `web/src/pages/Setup.tsx` | Modify (~lines 541-635) | The dropdown, selection state, re-pointed banner, and the scanned-PDF disclosure. |

Check first whether `tests/test_llm_factory.py` already exists — extend it if so rather than creating a second file.

---

### Task 1: `get_client` accepts a provider argument

**Files:**
- Modify: `shared/llm/factory.py:10-24`
- Test: `tests/test_llm_factory.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `get_client(provider: str | None = None) -> LLMClient`. Tasks 2 and 3 depend on `None` meaning "read `LLM_PROVIDER`, defaulting to `mock`".
- **Store invariant owned:** none — this task touches no store path. `get_client` constructs a client and reads no snapshot. Task 3 owns the invariant that protects the store from this argument.

The whole point is that `None` changes nothing. Every existing caller — `portal/app.py`, the CLI, the current ingest route — passes no argument and must keep resolving through the environment exactly as today. `LLM_MODEL` is still read from the env on both paths; this task does not touch model resolution.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_llm_factory.py
import pytest

from shared.llm.factory import get_client
from shared.llm.mock_client import MockLLMClient
from shared.llm.openai_client import OpenAIClient


def test_explicit_provider_wins_over_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    assert isinstance(get_client("openai"), OpenAIClient)


def test_none_reads_the_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    assert isinstance(get_client(), MockLLMClient)
    assert isinstance(get_client(None), MockLLMClient)


def test_provider_argument_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert isinstance(get_client("MOCK"), MockLLMClient)


def test_unknown_provider_argument_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    with pytest.raises(ValueError, match="banana"):
        get_client("banana")
```

`test_explicit_provider_wins_over_env` constructs a real `OpenAIClient`. Confirm that its `__init__` does not require `OPENAI_API_KEY` — read `shared/llm/openai_client.py` before running. If it does read the key at construction time, keep the test key-free by asserting on a stub instead:

```python
def test_explicit_provider_wins_over_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    seen = {}
    monkeypatch.setattr(
        "shared.llm.factory.OpenAIClient",
        lambda **kw: seen.setdefault("built", True),
    )
    get_client("openai")
    assert seen["built"] is True
```

Pick one of the two forms based on what you find. Do not set a real key either way.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_llm_factory.py -v
```

Expected: FAIL — `get_client() takes 0 positional arguments but 1 was given`.

- [ ] **Step 3: Write the implementation**

```python
def get_client(provider: str | None = None) -> LLMClient:
    """Build the extraction client.

    `provider=None` resolves through LLM_PROVIDER, which is what every
    environment-driven caller relies on. An explicit name wins, so a request
    can choose a provider without mutating the process environment.
    """
    name = (provider or os.getenv("LLM_PROVIDER", "mock")).lower()
    model = os.getenv("LLM_MODEL")
    kwargs = {"model": model} if model else {}
    if name == "mock":
        return MockLLMClient(response={})
    if name == "anthropic":
        return AnthropicClient(**kwargs)
    if name == "openai":
        return OpenAIClient(**kwargs)
    if name == "gemini":
        return GeminiClient(**kwargs)
    if name == "bedrock":
        return BedrockClient(**kwargs)
    raise ValueError(f"Unknown LLM_PROVIDER: {name}")
```

`provider or os.getenv(...)` also sends the empty string to the env, which is what you want — an empty field is an unspecified field.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_llm_factory.py -v
```

Then confirm nothing that calls `get_client()` regressed:

```bash
.venv/Scripts/python.exe -m pytest tests/ -q -k "factory or portal or pipeline"
```

- [ ] **Step 5: Commit**

```bash
git add shared/llm/factory.py tests/test_llm_factory.py
git commit -m "feat(llm): get_client accepts an explicit provider argument"
```

---

### Task 2: Readiness catalog for all five providers

**Files:**
- Modify: `api/main.py:124-136`
- Test: `tests/test_api_setup.py`

**Interfaces:**
- Consumes: nothing from Task 1 at runtime; the provider id strings must match the names `get_client` accepts.
- Produces: `PROVIDER_KEYS: dict[str, str | None]` and `_provider_state() -> dict` with keys `provider`, `needs_key`, `ready`, `catalog`. `catalog` is `list[dict]`, each `{"id": str, "needs_key": str | None, "ready": bool}`, in the fixed order `anthropic, openai, gemini, bedrock, mock`. Task 3 imports `PROVIDER_KEYS`; Task 4 consumes the JSON shape.
- **Store invariant owned:** none — `_provider_state()` is a pure read of the environment and opens no snapshot. Calling `GET /setup` any number of times leaves `generation` unchanged.

Order is fixed rather than sorted so the dropdown does not reshuffle between renders. `bedrock` and `mock` are always ready: bedrock authenticates via AWS IAM and mock needs nothing.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_api_setup.py
def test_setup_reports_provider_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = _client(tmp_path, monkeypatch)
    client.post("/api/projects", json={"name": "P"})
    provider = client.get("/api/projects/p/setup").json()["provider"]

    assert [entry["id"] for entry in provider["catalog"]] == [
        "anthropic", "openai", "gemini", "bedrock", "mock",
    ]
    by_id = {entry["id"]: entry for entry in provider["catalog"]}
    assert by_id["openai"] == {
        "id": "openai", "needs_key": "OPENAI_API_KEY", "ready": True,
    }
    assert by_id["gemini"] == {
        "id": "gemini", "needs_key": "GEMINI_API_KEY", "ready": False,
    }
    assert by_id["bedrock"] == {"id": "bedrock", "needs_key": None, "ready": True}
    assert by_id["mock"] == {"id": "mock", "needs_key": None, "ready": True}


def test_catalog_does_not_change_the_default_keys(tmp_path, monkeypatch):
    """The three legacy keys still describe the server default, not a selection."""
    client = _client(tmp_path, monkeypatch)
    client.post("/api/projects", json={"name": "P"})
    provider = client.get("/api/projects/p/setup").json()["provider"]
    assert provider["provider"] == "mock"
    assert provider["needs_key"] is None
    assert provider["ready"] is True
```

`_client` already sets `LLM_PROVIDER=mock`, so the default stays `mock`. The fake OpenAI key is never sent anywhere — readiness is a presence check, and no client is constructed on this path.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_api_setup.py -k catalog -v
```

Expected: FAIL with `KeyError: 'catalog'`.

- [ ] **Step 3: Write the implementation**

Replace the inline dict in `_provider_state()` with a module-level constant, placed next to `REQUIREMENTS_EXTS` near the top of `api/main.py`:

```python
# Provider → the env var holding its API key. None means no key is needed:
# bedrock authenticates via AWS IAM, and mock calls nothing. Insertion order is
# the order the UI lists them in, so it must stay stable.
PROVIDER_KEYS: dict[str, str | None] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "bedrock": None,
    "mock": None,
}


def _provider_ready(provider: str) -> bool:
    if provider not in PROVIDER_KEYS:
        return False
    needed = PROVIDER_KEYS[provider]
    return needed is None or bool(os.getenv(needed))
```

**The membership check is load-bearing.** Without it, `.get` yields `None` for an unrecognized provider and `needed is None` reads that as "needs no key", reporting an unknown `LLM_PROVIDER` as **ready** — where the code this replaces reported `False`. Task 3's validation does **not** cover this case: it guards the provider *named in a request*, whereas `_provider_state()` reports the *server default*, which no request ever names. An unknown default would otherwise show a green banner and an enabled Run button, then fail as a 502 out of `get_client(None)` — the exact 400-vs-502 confusion §4.3 exists to prevent. Task 4's catalog fallback keys off this same value.

Then rewrite `_provider_state()`:

```python
def _provider_state() -> dict:
    """The server's default provider, plus what every provider would need.

    The first three keys describe the default, not any selection — the front end
    and tests both depend on that meaning being unchanged.
    """
    provider = os.getenv("LLM_PROVIDER", "mock").lower()
    return {
        "provider": provider,
        "needs_key": PROVIDER_KEYS.get(provider),
        "ready": _provider_ready(provider),
        "catalog": [
            {"id": name, "needs_key": key, "ready": _provider_ready(name)}
            for name, key in PROVIDER_KEYS.items()
        ],
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_api_setup.py -v
```

Expected: all pass, including the pre-existing `test_setup_reports_provider_state` — unmodified.

- [ ] **Step 5: Commit**

```bash
git add api/main.py tests/test_api_setup.py
git commit -m "feat(api): report per-provider readiness catalog on the setup route"
```

---

### Task 3: The ingest route accepts and validates a provider

**Files:**
- Modify: `api/main.py:241-262`
- Test: `tests/test_api_setup.py`

**Interfaces:**
- Consumes: `get_client(provider)` from Task 1; `PROVIDER_KEYS` and `_provider_ready` from Task 2.
- Produces: `POST /api/projects/{slug}/ingest` with optional JSON body `{"provider": str}`. Absent or `null` → server default. Unknown → 400. Known but unready → 400. Task 4 consumes these status codes and surfaces `detail` verbatim.
- **Store invariant owned:** **a rejected ingest request leaves the store exactly as it was — same `generation`, same snapshot contents, no new collection.** Validation completes before `run_ingestion` is called, so a 400 never starts a run.

This is the task that carries real risk. If validation runs after `run_ingestion`, or if a bad provider reaches `get_client()` inside the try block, the `ValueError` is caught by the existing `except Exception` and returned as **502 — "Ingestion failed"** — after work has already begun. A configuration typo would then be indistinguishable from a model outage, and the store would have been touched. Validate first, outside the try.

The body must stay optional. `tests/test_api_setup.py` posts to this route with no body at all and expects the existing no-vendors **422**; `Body(...)` would turn that into a 422 about a missing body and the test would pass for the wrong reason. Use `Body(default=None)`.

Keep the vendors check first. It describes the project; the provider check describes the request.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_api_setup.py
def _project_with_vendor(client) -> None:
    client.post("/api/projects", json={"name": "P"})
    payload = _make_zip({"ACME/quote.txt": b"unit price 10 USD"})
    client.post(
        "/api/projects/p/vendors",
        files={"file": ("bids.zip", payload, "application/zip")},
    )


def test_ingest_accepts_an_explicit_provider(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    res = client.post("/api/projects/p/ingest", json={"provider": "mock"})
    assert res.status_code == 200


def test_ingest_rejects_an_unknown_provider(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    before = client.get("/api/projects/p/setup").json()

    res = client.post("/api/projects/p/ingest", json={"provider": "banana"})

    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "banana" in detail
    assert "mock" in detail  # names the valid set
    after = client.get("/api/projects/p/setup").json()
    assert after["generation"] == before["generation"]
    assert after["has_results"] == before["has_results"]


def test_ingest_rejects_a_provider_with_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    before = client.get("/api/projects/p/setup").json()

    res = client.post("/api/projects/p/ingest", json={"provider": "gemini"})

    assert res.status_code == 400
    assert "GEMINI_API_KEY" in res.json()["detail"]
    after = client.get("/api/projects/p/setup").json()
    assert after["generation"] == before["generation"]
    assert after["has_results"] == before["has_results"]


def test_ingest_without_a_provider_uses_the_default(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    assert client.post("/api/projects/p/ingest").status_code == 200
    assert client.post("/api/projects/p/ingest", json={}).status_code == 200
```

The two rejection tests each assert **400 and an unchanged store**. That pairing is the point — a 400 that still ran ingestion would pass a status-only assertion.

If `_make_zip` with a single vendor folder does not produce a runnable project under `mock`, copy the vendor-upload setup from the existing ingest tests in this file rather than inventing a new fixture.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_api_setup.py -k ingest -v
```

Expected: the explicit-provider and rejection tests fail — the route ignores the body, so a bad provider returns 200.

- [ ] **Step 3: Write the implementation**

```python
@app.post("/api/projects/{slug}/ingest")
def ingest(slug: str, payload: dict | None = Body(default=None)) -> dict:
    project = _load_or_404(slug)
    if not project.vendors:
        raise HTTPException(
            status_code=422,
            detail="Add at least one vendor before running ingestion.",
        )

    # Validate before any work starts. A provider that was never runnable is a
    # configuration error (400), not an extraction failure (502) — and a 400
    # must leave the store untouched.
    requested = (payload or {}).get("provider")
    provider = str(requested).strip().lower() if requested else None
    if provider is not None:
        if provider not in PROVIDER_KEYS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown provider '{provider}'. "
                    f"Choose one of: {', '.join(PROVIDER_KEYS)}."
                ),
            )
        if not _provider_ready(provider):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Provider '{provider}' is not configured. "
                    f"Set {PROVIDER_KEYS[provider]} in the API environment "
                    f"and restart it."
                ),
            )

    from shared.llm.factory import get_client

    # The scanned-PDF transcription fallback uses Anthropic; enable it only when
    # an Anthropic key is present, mirroring the Streamlit portal. This is
    # deliberately independent of the selected provider — see §5.1 of the spec.
    pdf_fallback = None
    if os.getenv("ANTHROPIC_API_KEY"):
        from procurement.pdf_llm import transcribe_pdf

        pdf_fallback = transcribe_pdf
    try:
        run_ingestion(ROOT, slug, get_client(provider), pdf_fallback=pdf_fallback)
    except Exception as exc:  # surface extraction failures to the UI verbatim
        raise HTTPException(status_code=502, detail=f"Ingestion failed: {exc}") from exc
    return _setup_state(proj.load_project(ROOT, slug))
```

Load-bearing: validation sits **above** `from shared.llm.factory import get_client` and outside the `try`; `provider` stays `None` when unspecified so `get_client(None)` reads the env; `Body(default=None)`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_api_setup.py -v
```

Expected: all pass, including the pre-existing no-body 422 test.

- [ ] **Step 5: Commit**

```bash
git add api/main.py tests/test_api_setup.py
git commit -m "feat(api): accept an optional provider on the ingest route"
```

---

### Task 4: The provider dropdown in Setup step 4

**Files:**
- Modify: `web/src/types.ts:59-63`, `web/src/api.ts:97-101`, `web/src/pages/Setup.tsx:541-635`
- Test: type-check and browser verification (this repo has no front-end test runner — do not add one)

**Interfaces:**
- Consumes: the `catalog` JSON from Task 2 and the 400 responses from Task 3.
- Produces: no interface later tasks depend on. Task 5 drives this UI.
- **Store invariant owned:** **selecting a provider writes nothing.** Changing the dropdown issues no request; only pressing **Run ingestion** does. `generation` must not move on selection alone.

The selection lives in `IngestStep`'s `useState`, seeded from `setup.provider.provider` — the server default. It is deliberately not lifted to `App` and not persisted: per-run means the dropdown resets on reload, and that is the honest reading of the design.

`ProviderBanner` currently receives the whole `ProviderState` and reports the server default. It must be re-pointed at the **selected** provider, otherwise the banner says "ready" while a different, unready provider is selected.

- [ ] **Step 1: Extend the types**

```typescript
// web/src/types.ts
export interface ProviderOption {
  id: string
  needs_key: string | null
  ready: boolean
}

export interface ProviderState {
  provider: string
  needs_key: string | null
  ready: boolean
  catalog: ProviderOption[]
}
```

- [ ] **Step 2: Carry the provider through the API client**

```typescript
// web/src/api.ts — replaces the existing runIngestion
export function runIngestion(
  slug: string,
  provider?: string,
): Promise<ProjectSetup> {
  return sendJson<ProjectSetup>(
    `/api/projects/${encodeURIComponent(slug)}/ingest`,
    'POST',
    provider ? { provider } : {},
  )
}
```

`sendJson` already exists at `web/src/api.ts:27` and already routes errors through `unwrap`, which lifts FastAPI's `detail` into the thrown `Error.message`. That is how a 400 reaches the error banner as readable prose. Sending `{}` rather than no body when no provider is chosen keeps one code path; Task 3 accepts both.

- [ ] **Step 3: Rewrite `ProviderBanner` and `IngestStep`**

```tsx
// web/src/pages/Setup.tsx
function ProviderBanner({ option }: { option: ProviderOption }): JSX.Element {
  if (option.ready) {
    return (
      <div className="banner banner--ok">
        Extraction provider: {option.id} — ready.
      </div>
    )
  }
  return (
    <div className="banner banner--warn">
      Extraction provider "{option.id}" is not ready.
      {option.needs_key
        ? ` Set ${option.needs_key} in the API environment and restart it.`
        : ''}
    </div>
  )
}
```

Inside `IngestStep`, replace the two state lines and `canRun`:

```tsx
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [provider, setProvider] = useState(setup.provider.provider)

  const catalog = setup.provider.catalog
  const selected =
    catalog.find((entry) => entry.id === provider) ??
    { id: provider, needs_key: setup.provider.needs_key, ready: setup.provider.ready }

  const anthropic = catalog.find((entry) => entry.id === 'anthropic')
  const scannedPdfsUnsupported =
    selected.id !== 'anthropic' && anthropic !== undefined && !anthropic.ready

  const noVendors = setup.vendors.length === 0
  const canRun = !noVendors && selected.ready && !running
```

The `??` fallback covers a server default that is somehow absent from the catalog. It cannot happen with Task 2's implementation, but the component must not crash on an undefined lookup if the API and bundle ever drift.

`run()` passes the selection:

```tsx
      await runIngestion(slug, provider)
```

Render the banner from `selected`, and add the dropdown immediately above the button row:

```tsx
      <ProviderBanner option={selected} />
      {/* …existing running / has_results / error banners, unchanged… */}
      <div style={{ marginTop: '0.6rem' }}>
        <label className="field-label" htmlFor="provider-select">
          Extraction provider
        </label>
        <select
          id="provider-select"
          value={provider}
          disabled={running}
          onChange={(e) => setProvider(e.target.value)}
        >
          {catalog.map((entry) => (
            <option key={entry.id} value={entry.id} disabled={!entry.ready}>
              {entry.ready
                ? entry.id
                : `${entry.id} — ${entry.needs_key ?? 'not configured'} not set`}
            </option>
          ))}
        </select>
        <p className="hint" style={{ marginTop: '0.3rem' }}>
          Applies to this run only. Reloading returns to the server default.
        </p>
        {scannedPdfsUnsupported && (
          <p className="hint" style={{ marginTop: '0.3rem' }}>
            No ANTHROPIC_API_KEY is set, so scanned image-only PDFs will not be
            transcribed on this run.
          </p>
        )}
      </div>
```

Illustrative: the exact markup, the `field-label` and `hint` class names, and the wording. Match whatever the FX step at `web/src/pages/Setup.tsx` already uses for its labelled `select` — reuse its classes rather than these guesses. Load-bearing: the dropdown is disabled while `running`; unready options are `disabled`; the option text names the missing variable; and the disclosure appears only when the selected provider is not `anthropic` **and** no Anthropic key is present.

- [ ] **Step 4: Type-check and lint**

```bash
npm --prefix web run build
```

Expected: `tsc -b` clean, Vite build succeeds. Then:

```bash
npm --prefix web run lint
```

- [ ] **Step 5: Commit**

```bash
git add web/src/types.ts web/src/api.ts web/src/pages/Setup.tsx
git commit -m "feat(web): choose the extraction provider for a single ingestion run"
```

---

### Task 5: Integration — provider isolation across runs

**Files:**
- Test: `tests/test_api_setup.py`
- Verify: the running app via the preview tooling

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: the final deliverable. Nothing consumes this task.
- **Store invariant owned:** **after run 2, the store reflects run 2's provider and carries nothing that only run 1's provider could have produced.** No provider choice survives its own request.

Rule 2 of `PLAN-TEMPLATE.md` in its honest form for this change. The nine document-mutation rows defend the ingestion pipeline, which this plan does not modify; re-asserting them here would test phase-2 and phase-3 code. What *this* mechanism could break is leakage between runs — precisely the failure mode that rejecting the `os.environ` approach was meant to prevent. So the mutations are provider mutations.

- [ ] **Step 1: Write the two-run mutation matrix tests**

| mutation between run 1 and run 2 | invariant at risk | assert |
|---|---|---|
| run 1 explicit `mock`, run 2 omits `provider` | run 1's choice must not persist into run 2 | after run 2, `os.environ["LLM_PROVIDER"]` is unchanged from its pre-run value |
| run 1 explicit `mock`, run 2 a rejected unknown | a rejected run leaves the store as run 1 left it | `generation` and `has_results` identical before and after the 400 |
| run 1 rejected unready provider, run 2 valid | a rejection must not poison the next run | run 2 returns 200 and `has_results` is true |
| run 1 and run 2 both explicit, different valid providers | the second choice governs the second run | the client class built on run 2 is the one run 2 asked for |
| a run is attempted with `provider: ""` | an empty field is unspecified, not invalid | 200, and the default was used |

```python
def test_provider_choice_does_not_leak_into_the_environment(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    before = os.environ.get("LLM_PROVIDER")

    assert client.post("/api/projects/p/ingest", json={"provider": "mock"}).status_code == 200
    assert os.environ.get("LLM_PROVIDER") == before

    assert client.post("/api/projects/p/ingest").status_code == 200
    assert os.environ.get("LLM_PROVIDER") == before


def test_rejected_run_does_not_disturb_a_previous_good_run(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    client.post("/api/projects/p/ingest", json={"provider": "mock"})
    after_good = client.get("/api/projects/p/setup").json()

    assert client.post("/api/projects/p/ingest", json={"provider": "banana"}).status_code == 400

    after_bad = client.get("/api/projects/p/setup").json()
    assert after_bad["generation"] == after_good["generation"]
    assert after_bad["has_results"] == after_good["has_results"] is True


def test_a_rejection_does_not_poison_the_next_run(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)

    assert client.post("/api/projects/p/ingest", json={"provider": "gemini"}).status_code == 400
    assert client.post("/api/projects/p/ingest", json={"provider": "mock"}).status_code == 200
    assert client.get("/api/projects/p/setup").json()["has_results"] is True


def test_the_second_run_uses_the_second_choice(tmp_path, monkeypatch):
    """Two different valid providers across two runs; the second governs."""
    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    built: list[str | None] = []

    import shared.llm.factory as factory
    real = factory.get_client
    monkeypatch.setattr(
        "api.main.get_client" if hasattr(__import__("api.main", fromlist=["x"]), "get_client")
        else "shared.llm.factory.get_client",
        lambda provider=None: (built.append(provider), real("mock"))[1],
    )

    client.post("/api/projects/p/ingest", json={"provider": "mock"})
    client.post("/api/projects/p/ingest", json={"provider": "bedrock"})
    assert built == ["mock", "bedrock"]


def test_empty_provider_string_uses_the_default(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    assert client.post("/api/projects/p/ingest", json={"provider": ""}).status_code == 200
```

`test_the_second_run_uses_the_second_choice` patches the factory because `bedrock` cannot really be constructed without AWS credentials — the assertion is about **what was requested**, not what was built. The `hasattr` dance in that reference is ugly; `ingest` imports `get_client` inside the function body, so patch `shared.llm.factory.get_client` and confirm the patch takes effect before relying on it. Simplify it once you have seen which target works.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_api_setup.py -v
```

- [ ] **Step 3: Fix whatever the matrix exposes**

If every row passes on the first run, that is expected — Tasks 1-3 were written to satisfy them. Do not weaken a row to make it interesting.

- [ ] **Step 4: Verify the matrix is real, not decorative**

Reintroduce each defect one at a time and confirm the intended row fails and nothing else does:

| reinstate this defect | expect this row to fail |
|---|---|
| add `os.environ["LLM_PROVIDER"] = provider` in `ingest` | leakage row |
| move provider validation below the `run_ingestion` call | both store-untouched rows |
| change the unknown-provider status from 400 to 502 | unknown-provider row |
| drop the `if requested else None` guard so `""` becomes `""` | empty-string row |

A row that still passes with its defect reinstated is not testing what it claims. Revert each defect before moving on.

- [ ] **Step 5: Run the whole suite**

```bash
.venv/Scripts/python.exe -m pytest
```

Expected on this workstation: **649 + your new tests passed, 3 skipped, 1 failed** — the failure being only `test_missing_api_key_does_not_block_creation`, which is the documented `.env` artefact. Any other failure is a real regression.

- [ ] **Step 6: Verify in the running app**

Start both servers and drive the wizard:

```bash
.venv/Scripts/python.exe -m uvicorn api.main:app --reload --port 8000
```

```bash
npm --prefix web run dev
```

On a project with vendors, open **Set up & ingest** → step 4 and confirm: all five providers listed; unready ones greyed with their variable named; the banner tracks the selection rather than the server default; **Run ingestion** disables on an unready selection; and the scanned-PDF line appears only when a non-Anthropic provider is selected with no Anthropic key present. Screenshot step 4.

- [ ] **Step 7: Commit**

```bash
git add tests/test_api_setup.py
git commit -m "test(api): two-run matrix for provider isolation across ingestion runs"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §2 per-run scope, no persistence | 3 (request-scoped), 4 (state resets on reload) |
| §3 `get_client(provider=None)` | 1 |
| §4.1 single `PROVIDER_KEYS` | 2 |
| §4.2 `catalog` on the setup route | 2 |
| §4.3 ingest accepts + validates, 400 not 502 | 3 |
| §5 types, api client, dropdown, re-pointed banner | 4 |
| §5.1 scanned-PDF disclosure | 4 |
| §6 test list | 1, 2, 3, 5 |
| §7 no key entry, no model field, no store write | enforced by Global Constraints; the no-store-write claim is asserted in 3 and 5 |

No gaps.

**Type consistency:** `ProviderOption {id, needs_key, ready}` is defined in Task 4 Step 1 and consumed by `ProviderBanner` in Step 3 under the prop name `option` — the prop was renamed from `provider`, so every call site changes with it. The API emits `id` (Task 2) and the TypeScript reads `id` (Task 4). `get_client(provider)` in Task 3 matches the signature from Task 1. `_provider_ready` is defined in Task 2 and used in Task 3.

**Known rough edges, deliberately left for the implementer:** the client-class assertion in Task 5 Step 1 needs the patch target confirmed by running it; Task 1's first test needs `OpenAIClient.__init__` checked before choosing between two given forms; Task 4's CSS classes should be matched to the FX step rather than taken from the reference. Each is flagged in place with what to check.
