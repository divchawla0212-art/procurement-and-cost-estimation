# Design: LLM Provider Selection in the Enterprise Front End

Status: approved, not yet planned
Depends on: the enterprise review app (`api/` + `web/`) shipped in `bbfd6b9`
Belongs to: phase 4 — review UI
Touches: `shared/llm/factory.py`, `api/main.py`, `web/src/{types,api}.ts`,
  `web/src/pages/Setup.tsx`

---

## 1. Context and goal

Extraction runs against whichever provider `LLM_PROVIDER` names.
`_provider_state()` in [`api/main.py`](../../../api/main.py) reads that env var
per request and reports `{provider, needs_key, ready}`; the `ProviderBanner` in
`Setup.tsx` renders it and gates **Run ingestion** on `ready`. The value is
**reported but not settable** — changing provider means editing `.env` and
restarting uvicorn.

The Streamlit portal does offer a selector ([`portal/app.py`](../../../portal/app.py),
sidebar), but it works by assigning `os.environ["LLM_PROVIDER"]`. That is a
process-global write from a request path. In Streamlit's one-session-per-process
model it is merely untidy; in the FastAPI app it would mean one user's choice
silently redirects every other user's next run, and it breaks outright under
more than one worker.

This design adds the selector to the React app **without** adopting that
mechanism.

### Success criteria

- A reviewer can choose the extraction provider immediately before running
  ingestion, without touching `.env` or restarting anything.
- Two concurrent runs may use different providers and neither perturbs the
  other.
- A provider whose key is absent cannot be run by accident, and the UI names
  the exact variable to set.
- Nothing about the choice reaches the store. `generation` does not move,
  because nothing was written.

---

## 2. Scope: per run, not per session or per project

The choice travels **with the ingestion request** and governs that request only.
It is not persisted, not remembered across a reload, and not stored on the
project.

Two alternatives were considered and rejected.

**A sticky server setting** (a `PUT` that mutates the process env, mirroring
Streamlit) reintroduces exactly the cross-user side effect described above. The
convenience of stickiness is not worth an invisible action-at-a-distance on a
surface that decides awards.

**Persisting the provider on the project** is the appealing one: a project would
record which provider produced its verdicts, which is real provenance. It is
rejected here on scope, not on merit — it is a store write, so it must route
through `procurement/store/snapshots.py` and bump `generation`, and that is a
larger change than adding a dropdown. If provenance is wanted later it should be
designed as *what actually ran*, recorded at write time, rather than as *what
was selected*, which is a different and weaker claim.

The cost accepted: the dropdown resets to the server default on every page load.
That is the honest reading of "per run".

---

## 3. `get_client` takes an argument

```python
def get_client(provider: str | None = None) -> LLMClient
```

`None` preserves today's behaviour exactly — read `LLM_PROVIDER`, defaulting to
`mock`. Every existing caller, the portal included, is unchanged. `LLM_MODEL` is
still consulted the same way for either path, so a `.env` model override keeps
working. The `ValueError` on an unknown provider stays.

The API therefore never mutates the environment. It builds the client for the
requested provider and hands it to `run_ingestion`, which already accepts a
client as a parameter.

---

## 4. API surface

### 4.1 A single source for provider→key

The provider→key-variable mapping currently exists twice: inline in
`_provider_state()` (three entries) and in `portal/app.py` (five). This design
adds a module-level `PROVIDER_KEYS` in `api/main.py` covering all five
providers the factory supports, and `_provider_state()` reads from it.

`bedrock` and `mock` map to `None` — neither needs a key, and both are
consequently always ready. `bedrock` authenticates via AWS IAM.

### 4.2 `GET /api/projects/{slug}/setup`

Gains one key, `catalog`:

```json
"provider": {
  "provider": "anthropic",
  "needs_key": "ANTHROPIC_API_KEY",
  "ready": true,
  "catalog": [
    {"id": "anthropic", "needs_key": "ANTHROPIC_API_KEY", "ready": true},
    {"id": "openai",    "needs_key": "OPENAI_API_KEY",    "ready": false},
    {"id": "gemini",    "needs_key": "GEMINI_API_KEY",    "ready": false},
    {"id": "bedrock",   "needs_key": null,                "ready": true},
    {"id": "mock",      "needs_key": null,                "ready": true}
  ]
}
```

The three existing keys keep their present meaning: they describe the **server
default**, not the selection. This is what keeps
`test_setup_reports_provider_state` and the current banner working untouched.

### 4.3 `POST /api/projects/{slug}/ingest`

Accepts an optional `provider` in the body.

| Case | Response |
|---|---|
| absent | server default runs (unchanged behaviour) |
| known and ready | that provider runs |
| not one of the five | **400**, naming the valid set |
| known but key absent | **400**, naming the missing variable |

Both rejections are **400, not 502**. The existing 502 at `api/main.py:260`
means "extraction failed"; a provider that was never runnable is a
configuration error caught before any work starts, and conflating the two would
make a typo look like a model failure.

Validation happens **before** `run_ingestion` is called, so a rejected request
leaves the store untouched.

---

## 5. Front end

`types.ts` gains `ProviderOption {id, needs_key, ready}` and
`ProviderState.catalog: ProviderOption[]`. `api.ts`'s `runIngestion` takes an
optional provider and sends it as JSON.

In `Setup.tsx`, the dropdown sits inside `IngestStep`, directly above **Run
ingestion** — adjacent to the action it modifies, because its effect lasts
exactly as long as that action. `IngestStep` holds the selection in `useState`
seeded from `setup.provider.provider`. `ProviderBanner` is re-pointed at the
**selected** provider rather than the server default, and `canRun` gates on the
selected provider's `ready`.

Unready entries render disabled with the missing variable inline
(`gemini — GEMINI_API_KEY not set`), so the list doubles as configuration
documentation. They are listed rather than hidden: a provider you have not set
up is still a provider you should know exists.

Styling reuses the existing `.banner--ok` / `.banner--warn` classes and the FX
step's `select`. No new design vocabulary.

### 5.1 The scanned-PDF fallback disclosure

`api/main.py` gates the scanned-PDF transcription fallback on
`ANTHROPIC_API_KEY` regardless of which provider is selected. Choosing `openai`
with no Anthropic key configured therefore means image-only PDFs are silently
not transcribed: the run succeeds and extracts less.

This is pre-existing behaviour and this design does not change the gating —
rerouting the fallback is a separate question about which model should do
transcription. It does surface it: when the selected provider is not
`anthropic` and no Anthropic key is present, one line of copy appears under the
dropdown saying scanned PDFs will not be transcribed. A silent reduction in
extraction coverage is the kind of thing that must not be discovered later from
a thin matrix.

---

## 6. Testing

Key-free, extending `tests/test_api_setup.py`, with the env monkeypatched:

- `catalog` lists all five providers with `ready` correct for the patched env.
- The three legacy `provider` keys still describe the server default.
- Explicit `{"provider": "mock"}` runs.
- An unknown provider returns 400 naming the valid set, and no run occurs.
- A known-but-unready provider returns 400 naming the missing variable, and no
  run occurs.
- An omitted provider still uses the server default.

On the factory, directly: an explicit argument wins over `LLM_PROVIDER`;
`None` still reads the env; an unknown argument raises `ValueError`.

No test requires a provider key, per the repository rule.

---

## 7. Not in scope

- **No API-key entry in the browser.** Keys live in the server's `.env`; the UI
  reports presence and never transports a secret. This is a firm boundary, not
  a deferral.
- **No model override.** `LLM_MODEL` continues to work from `.env`. A per-run
  model field was considered and dropped: a free-text field turns a typo into a
  mid-run provider error, and a curated dropdown goes stale on every provider
  release.
- **No persistence**, and therefore no snapshot write, no `generation` bump, and
  no store invariant in play.
- **The Streamlit sidebar is left alone.** It keeps its `os.environ` assignment.
  Converting it is unrelated to this goal.
