"""Key-free API tests for the project-setup + ingestion routes."""

import io
import os
import zipfile

from fastapi.testclient import TestClient

# `import api.main` is deliberately kept *lazy* — inside `_client` and
# `_unconfigured_client` below, not at module scope. `api/main.py` calls
# `dotenv.load_dotenv()` at import time, and a module-scope import here would
# run it during pytest's *collection* phase (this file collects before
# `test_procurement_real_data.py`, alphabetically), populating
# `ANTHROPIC_API_KEY` from `.env` before that file's
# `@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY") and ...)` decorator
# evaluates — turning its permanently-skipped live-Anthropic-call test into
# an actual billed API call. (Caught by re-measuring the full suite's skip
# count after trying that approach: 3 skips became 2.) So the fix instead
# keeps every import lazy and only reorders it — see `_unconfigured_client`.
#
# The helper below is safe to import at module scope, and must stay that way:
# it reaches `api.auth.store` only, which pulls in no part of `api.main` and
# so runs no `load_dotenv()`. Anything added to `tests/auth_helpers.py` that
# imports `api.main` would silently re-open the hole this comment describes.
from tests.auth_helpers import signed_in_admin


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return signed_in_admin(TestClient(api_main.app), tmp_path)


def _make_zip(paths: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in paths.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_create_project_returns_setup_and_lists(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    res = client.post("/api/projects", json={"name": "Gas Genset A", "target_currency": "eur"})
    assert res.status_code == 201
    body = res.json()
    assert body["slug"] == "gas-genset-a"
    assert body["target_currency"] == "EUR"
    assert body["vendors"] == []
    assert body["requirements_file"] is None
    assert client.get("/api/projects").json()[0]["slug"] == "gas-genset-a"


def test_create_requires_name(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.post("/api/projects", json={"name": "   "}).status_code == 422


def test_create_duplicate_conflicts(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/projects", json={"name": "Dup"})
    assert client.post("/api/projects", json={"name": "Dup"}).status_code == 409


def test_upload_requirements_stores_and_validates(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/projects", json={"name": "P"})
    ok = client.post(
        "/api/projects/p/requirements",
        files={"file": ("spec.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert ok.status_code == 200
    assert ok.json()["requirements_file"] == "spec.pdf"

    bad = client.post(
        "/api/projects/p/requirements",
        files={"file": ("notes.txt", b"nope", "text/plain")},
    )
    assert bad.status_code == 422


def test_upload_vendor_zip_detects_vendors(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/projects", json={"name": "P"})
    archive = _make_zip({
        "ADPOWER/quotation.pdf": b"%PDF quote",
        "MKON/offer.pdf": b"%PDF offer",
    })
    res = client.post(
        "/api/projects/p/vendors",
        files={"file": ("bids.zip", archive, "application/zip")},
    )
    assert res.status_code == 200
    names = {v["name"] for v in res.json()["vendors"]}
    assert names == {"ADPOWER", "MKON"}


def test_upload_bad_zip_rejected(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/projects", json={"name": "P"})
    res = client.post(
        "/api/projects/p/vendors",
        files={"file": ("bids.zip", b"not a zip", "application/zip")},
    )
    assert res.status_code == 422


def test_set_fx_rates_normalises_and_validates(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/projects", json={"name": "P"})
    ok = client.put("/api/projects/p/fx-rates", json={"rates": {"eur": 1.08, "gbp": "1.27"}})
    assert ok.status_code == 200
    assert ok.json()["fx_rates"] == {"EUR": 1.08, "GBP": 1.27}

    bad = client.put("/api/projects/p/fx-rates", json={"rates": {"EUR": "abc"}})
    assert bad.status_code == 422


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


def test_ingest_without_vendors_is_rejected(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/projects", json={"name": "P"})
    res = client.post("/api/projects/p/ingest")
    assert res.status_code == 422


def _project_with_vendor(client) -> None:
    client.post("/api/projects", json={"name": "P"})
    # Padded past MIN_EXTRACTABLE_CHARS: a body this short would otherwise be
    # caught by the pipeline's no-readable-text guard and never reach the
    # extractor these tests are actually exercising.
    body = (b"unit price 10 USD. This synthetic fixture body is padded with "
           b"filler prose so its character count clears the pipeline's "
           b"minimum-extractable-text guard, letting the extraction logic "
           b"under test run rather than the guard itself.")
    payload = _make_zip({"ACME/quote.txt": body})
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
    # `_client` first, `delenv` after (I3, final-review report): `_client`'s
    # lazy `import api.main` triggers `dotenv.load_dotenv()` on first import
    # in the process, which would otherwise repopulate a just-deleted
    # `GEMINI_API_KEY` from the developer's `.env` if this were the first
    # test in the file to run.
    client = _client(tmp_path, monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _project_with_vendor(client)
    before = client.get("/api/projects/p/setup").json()

    res = client.post("/api/projects/p/ingest", json={"provider": "gemini"})

    assert res.status_code == 400
    assert "GEMINI_API_KEY" in res.json()["detail"]
    after = client.get("/api/projects/p/setup").json()
    assert after["generation"] == before["generation"]
    assert after["has_results"] == before["has_results"]


def test_ingest_without_a_provider_uses_the_default(tmp_path, monkeypatch):
    """Regression guard: a healthy server default still runs, and still writes."""
    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    assert client.post("/api/projects/p/ingest").status_code == 200
    assert client.post("/api/projects/p/ingest", json={}).status_code == 200
    after = client.get("/api/projects/p/setup").json()
    assert after["has_results"] is True
    assert after["generation"] > 0


def test_project_summary_reports_has_results_independent_of_status(
    tmp_path, monkeypatch
):
    """I2 (final-review report): `has_results` must reflect the store, not
    `status` — CLAUDE.md: "a failed extraction never blanks previously-good
    stored data" means a project can be `status: failed` (its most recent run
    extracted nothing) while the store still holds a complete earlier
    extraction. `_project_summary` — the source for both `GET /api/projects`
    and `GET /api/projects/{slug}` — must report `has_results: true` in that
    state, not tie it to `status`.
    """
    from procurement import project as proj

    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    assert client.post("/api/projects/p/ingest").status_code == 200
    setup_after_good_run = client.get("/api/projects/p/setup").json()
    assert setup_after_good_run["has_results"] is True
    assert setup_after_good_run["status"] != "failed"

    # Simulate exactly the scenario I2 describes: a second run that failed
    # every document, recomputing `status` to `"failed"` without touching the
    # store the first run wrote (`procurement/pipeline.py`:
    # `project.status = "failed" if extracted == 0 else ...`).
    project = proj.load_project(str(tmp_path), "p")
    project.status = "failed"
    proj.save_project(str(tmp_path), project)

    summary = client.get("/api/projects/p").json()
    assert summary["status"] == "failed"
    assert summary["has_results"] is True  # the store still holds the first run

    listing = client.get("/api/projects").json()
    assert listing[0]["status"] == "failed"
    assert listing[0]["has_results"] is True


def test_project_listing_does_not_rebuild_the_price_comparison(tmp_path, monkeypatch):
    """`has_results` costs one directory read per project, not a full load.

    The dashboard listing renders every project, and `_project_summary` needs
    only the boolean "is there an extraction to open?". Answering it with
    `bool(load_dataset(...))` loaded every vendor's facts and built a price
    comparison per project, purely to discard it -- so the cost of opening the
    dashboard grew with the corpus it lists.
    """
    from procurement import pipeline

    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    assert client.post("/api/projects/p/ingest").status_code == 200

    def _refuse(*args, **kwargs):
        raise AssertionError(
            "GET /api/projects built a price comparison it then threw away")

    monkeypatch.setattr(pipeline, "build_comparison", _refuse)

    listing = client.get("/api/projects").json()

    assert [p["slug"] for p in listing] == ["p"]
    assert listing[0]["has_results"] is True


def test_ingest_rejects_an_unknown_default_provider(tmp_path, monkeypatch):
    """An omitted `provider` is validated too — the default is not exempt.

    Before this was fixed the request fell through to `get_client(None)` inside
    the `try`, so an unrecognised `LLM_PROVIDER` surfaced as a 502 "Ingestion
    failed" — a configuration error dressed up as a model failure.
    """
    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    before = client.get("/api/projects/p/setup").json()
    monkeypatch.setenv("LLM_PROVIDER", "banana")

    res = client.post("/api/projects/p/ingest")

    assert res.status_code == 400  # never the 502 of a failed run
    detail = res.json()["detail"]
    assert "banana" in detail
    assert "mock" in detail  # names the valid set
    after = client.get("/api/projects/p/setup").json()
    assert after["generation"] == before["generation"]
    assert after["has_results"] is False


def test_ingest_rejects_an_unready_default_provider(tmp_path, monkeypatch):
    """An omitted `provider` with an unconfigured default is a 400, not a run.

    Before this was fixed the run went ahead against a keyless client, wrote a
    full store in which every extraction had failed, and reported
    `has_results: true` with `generation` bumped 0 → 1.
    """
    # `_client` first, `delenv` after (I3, final-review report) — see the
    # comment in `test_ingest_rejects_a_provider_with_no_key`.
    client = _client(tmp_path, monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _project_with_vendor(client)
    before = client.get("/api/projects/p/setup").json()
    monkeypatch.setenv("LLM_PROVIDER", "gemini")

    res = client.post("/api/projects/p/ingest")

    assert res.status_code == 400
    assert "GEMINI_API_KEY" in res.json()["detail"]
    after = client.get("/api/projects/p/setup").json()
    assert after["generation"] == before["generation"]
    assert after["has_results"] is False


def test_setup_reports_provider_state(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/projects", json={"name": "P"})
    provider = client.get("/api/projects/p/setup").json()["provider"]
    assert provider["provider"] == "mock"
    assert provider["ready"] is True


def test_setup_reports_provider_catalog(tmp_path, monkeypatch):
    # `_client` first, `delenv` after (I3, final-review report) — see the
    # comment in `test_ingest_rejects_a_provider_with_no_key`. `setenv` has
    # no such hazard (`load_dotenv()` never overrides an already-set var),
    # so only the `delenv` needs to move.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    client = _client(tmp_path, monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
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


def test_unrecognized_default_provider_is_not_ready(tmp_path, monkeypatch):
    """An out-of-catalog LLM_PROVIDER must report ready=False, not True."""
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "banana")
    client.post("/api/projects", json={"name": "P"})
    provider = client.get("/api/projects/p/setup").json()["provider"]
    assert provider["provider"] == "banana"
    assert provider["needs_key"] is None
    assert provider["ready"] is False


# ------------------------------------- two-run matrix: provider isolation
#
# A provider choice must govern exactly one request and leak nowhere. These
# five tests are the mutation matrix for that invariant: each drives two
# ingestion runs (or one run plus one rejection) and asserts that nothing run 1
# chose survives into run 2, and that a rejected run changes nothing at all.


def _record_provider_requests(monkeypatch) -> list[str | None]:
    """Record the provider argument every run hands to the client factory.

    Two providers cannot both be *constructed* in a key-free test — only `mock`
    builds without credentials — so the recorder always returns a real mock
    client and the assertions are about what each run **requested**.

    `ingest` does `from shared.llm.factory import get_client` inside the
    function body, so the name is resolved on the factory module at call time.
    That module attribute is therefore the patch target that takes effect;
    there is no module-level `api.main.get_client` to patch. Each test that
    uses this helper asserts on the recorded list, so a patch that failed to
    take effect shows up as an empty list rather than as a silent pass.
    """
    import shared.llm.factory as factory

    real_get_client = factory.get_client
    requested: list[str | None] = []

    def recording_get_client(provider: str | None = None):
        requested.append(provider)
        return real_get_client("mock")

    monkeypatch.setattr(factory, "get_client", recording_get_client)
    return requested


def test_provider_choice_does_not_leak_into_the_environment(tmp_path, monkeypatch):
    """Row 1: run 1 explicit `mock`, run 2 omits `provider`.

    The rejected design mutated `os.environ` from the request handler, which
    would have redirected every other user's run. With `LLM_PROVIDER` unset,
    *any* write of the requested name into the environment is visible.

    BUG-004: the run-2 assertion originally expected a *200*, on the theory
    that a leaked `mock` from run 1 would be the only way to tell — but that
    only worked because `ingest` itself defaulted an omitted provider to
    `mock` when `LLM_PROVIDER` was unset. That default was the bug: it let an
    unconfigured deployment ingest and store fabricated facts. With the
    fallback removed, run 2 (no provider, `LLM_PROVIDER` unset) correctly
    gets a 400 — which still proves the point, since a leaked `mock` from run
    1 would have made it a 200.
    """
    client = _client(tmp_path, monkeypatch)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    _project_with_vendor(client)
    before = os.environ.get("LLM_PROVIDER")
    assert before is None  # guard: the check below is only meaningful unset

    assert client.post("/api/projects/p/ingest", json={"provider": "mock"}).status_code == 200
    assert os.environ.get("LLM_PROVIDER") == before

    assert client.post("/api/projects/p/ingest").status_code == 400
    assert os.environ.get("LLM_PROVIDER") == before


def test_rejected_run_does_not_disturb_a_previous_good_run(tmp_path, monkeypatch):
    """Row 2: run 1 explicit `mock`, run 2 a rejected unknown.

    A configuration error is a 400 and must leave the store exactly as run 1
    left it — same `generation`, same `has_results`.
    """
    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    assert client.post("/api/projects/p/ingest", json={"provider": "mock"}).status_code == 200
    after_good = client.get("/api/projects/p/setup").json()
    assert after_good["has_results"] is True
    assert after_good["generation"] > 0  # run 1 really did write

    res = client.post("/api/projects/p/ingest", json={"provider": "banana"})
    assert res.status_code == 400  # a config error, never the 502 of a failed run

    after_bad = client.get("/api/projects/p/setup").json()
    assert after_bad["generation"] == after_good["generation"]
    assert after_bad["has_results"] == after_good["has_results"]
    assert after_bad["has_results"] is True


def test_a_rejection_does_not_poison_the_next_run(tmp_path, monkeypatch):
    """Row 3: run 1 a rejected unready provider, run 2 valid.

    Rejection is request-scoped: it leaves no state that could stop the next
    run from succeeding.
    """
    # `_client` first, `delenv` after (I3, final-review report) — see the
    # comment in `test_ingest_rejects_a_provider_with_no_key`.
    client = _client(tmp_path, monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _project_with_vendor(client)

    assert client.post("/api/projects/p/ingest", json={"provider": "gemini"}).status_code == 400
    assert client.post("/api/projects/p/ingest", json={"provider": "mock"}).status_code == 200
    assert client.get("/api/projects/p/setup").json()["has_results"] is True


def test_the_second_run_uses_the_second_choice(tmp_path, monkeypatch):
    """Row 4: two different valid providers across two runs; the second governs."""
    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    requested = _record_provider_requests(monkeypatch)

    assert client.post("/api/projects/p/ingest", json={"provider": "mock"}).status_code == 200
    assert client.post("/api/projects/p/ingest", json={"provider": "bedrock"}).status_code == 200

    assert requested == ["mock", "bedrock"]


def test_empty_provider_string_uses_the_default(tmp_path, monkeypatch):
    """Row 5: an empty field is unspecified, not invalid.

    The dropdown offers only the five catalog ids, so this is not a UI contract
    — it is API robustness for non-browser clients, which do send `""` for an
    unset field. `""` must resolve to `None`, letting the factory read
    `LLM_PROVIDER`, rather than being validated as a provider literally named
    `""`.
    """
    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    requested = _record_provider_requests(monkeypatch)

    assert client.post("/api/projects/p/ingest", json={"provider": ""}).status_code == 200
    assert requested == [None]


# ------------------------------------- BUG-004: no implicit mock fallback
#
# `_provider_state` and `ingest` both used to read
# `os.getenv("LLM_PROVIDER", "mock")`. Because `mock` needs no key,
# `_provider_ready("mock")` was always `True`, so a deployment with no
# configuration at all silently ingested against the mock client and wrote
# fabricated facts into the store, indistinguishable from a real extraction.
# These tests drive a client with `LLM_PROVIDER` and every provider key
# unset — the exact state of a freshly started container — and assert the
# absent provider is surfaced, not defaulted.


def _unconfigured_client(tmp_path, monkeypatch) -> TestClient:
    # I3 (final-review report): import *before* the `delenv` calls below,
    # not after. `api/main.py:48` calls `dotenv.load_dotenv()` at import
    # time, which repopulates a just-deleted env var (it never overrides one
    # that is still set, only refills one that was removed) — so importing
    # first means any `.env` values load and get immediately wiped by the
    # `delenv`s that follow, rather than being loaded *after* the wipe and
    # silently surviving it. This was the exact bug: with the import last,
    # `test_ingest_without_provider_is_400_and_leaves_store_untouched` ran a
    # real, billed ingestion against the developer's configured Anthropic key
    # when run in isolation (verified: `200` where `400` was expected).
    import api.main as api_main
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    # Signing in needs no provider — signup and login are allowlisted paths —
    # so an unconfigured client can still hold a session, which every
    # non-public `/api/` path below now requires.
    return signed_in_admin(TestClient(api_main.app), tmp_path)


def test_ingest_without_provider_is_400_and_leaves_store_untouched(tmp_path, monkeypatch):
    from procurement.store import layout

    client = _unconfigured_client(tmp_path, monkeypatch)
    _project_with_vendor(client)

    docs_path = layout.documents_path(str(tmp_path), "p")
    events_file = layout.events_path(str(tmp_path), "p")
    docs_before = layout.read_json(docs_path)
    events_before = (
        open(events_file, "r", encoding="utf-8").read()
        if os.path.exists(events_file) else None
    )
    before = client.get("/api/projects/p/setup").json()

    res = client.post("/api/projects/p/ingest", json={})

    assert res.status_code == 400
    assert "LLM_PROVIDER" in res.json()["detail"]

    after = client.get("/api/projects/p/setup").json()
    assert after["generation"] == before["generation"]
    assert after["has_results"] == before["has_results"]
    assert layout.read_json(docs_path) == docs_before
    events_after = (
        open(events_file, "r", encoding="utf-8").read()
        if os.path.exists(events_file) else None
    )
    assert events_after == events_before


def test_provider_state_reports_null_when_unset(tmp_path, monkeypatch):
    client = _unconfigured_client(tmp_path, monkeypatch)
    client.post("/api/projects", json={"name": "P"})

    provider = client.get("/api/projects/p/setup").json()["provider"]

    assert provider["provider"] is None
    assert provider["needs_key"] is None
    assert provider["ready"] is False
    assert [entry["id"] for entry in provider["catalog"]] == [
        "anthropic", "openai", "gemini", "bedrock", "mock",
    ]


def test_explicit_mock_still_ingests(tmp_path, monkeypatch):
    client = _unconfigured_client(tmp_path, monkeypatch)
    _project_with_vendor(client)

    res = client.post("/api/projects/p/ingest", json={"provider": "mock"})

    assert res.status_code == 200


# ------------------------------------- BUG-002: the force bypass, reached
#
# `run_ingestion(force=True)` already bypasses the per-document extraction
# cache; the whole defect was that nothing in the API payload could set it.
# These drive two ingestion runs each and assert on the *second* run's
# `document.extracted` / `document.skipped` events, per task-5-brief.md
# Step 1.


def test_ingest_force_reextracts_a_document_whose_cache_key_is_unchanged(
    tmp_path, monkeypatch
):
    from procurement.store import events as store_events

    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    assert client.post("/api/projects/p/ingest").status_code == 200

    events_before = len(store_events.read_events(str(tmp_path), "p"))
    res = client.post("/api/projects/p/ingest", json={"force": True})
    assert res.status_code == 200

    run2 = store_events.read_events(str(tmp_path), "p")[events_before:]
    doc_events = [e for e in run2
                  if e.action in ("document.extracted", "document.skipped")]
    assert doc_events, "run 2 produced no per-document extraction events"
    assert all(e.action == "document.extracted" for e in doc_events), \
        "force=True must re-extract, not skip, a document whose bytes and " \
        "prompt version are otherwise unchanged"


def test_ingest_without_force_still_skips_unchanged_documents(tmp_path, monkeypatch):
    from procurement.store import events as store_events

    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    assert client.post("/api/projects/p/ingest").status_code == 200

    events_before = len(store_events.read_events(str(tmp_path), "p"))
    res = client.post("/api/projects/p/ingest", json={"force": False})
    assert res.status_code == 200

    run2 = store_events.read_events(str(tmp_path), "p")[events_before:]
    doc_events = [e for e in run2
                  if e.action in ("document.extracted", "document.skipped")]
    assert doc_events
    assert all(e.action == "document.skipped" for e in doc_events), \
        "an explicit force=False must behave exactly like today: a cache hit"


def test_ingest_omitting_force_defaults_to_the_cache(tmp_path, monkeypatch):
    """The payload field defaults to false: an existing client that has never
    heard of `force` must keep today's behaviour exactly (design spec §1.2)."""
    from procurement.store import events as store_events

    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    assert client.post("/api/projects/p/ingest").status_code == 200

    events_before = len(store_events.read_events(str(tmp_path), "p"))
    res = client.post("/api/projects/p/ingest", json={})
    assert res.status_code == 200

    run2 = store_events.read_events(str(tmp_path), "p")[events_before:]
    doc_events = [e for e in run2
                  if e.action in ("document.extracted", "document.skipped")]
    assert doc_events
    assert all(e.action == "document.skipped" for e in doc_events)


def test_ingest_rejects_a_non_boolean_force(tmp_path, monkeypatch):
    """`force` is the widest-blast-radius write in the system, so a truthy-
    coerced typo like the *string* "false" must not silently become a real
    forced run. `bool("false")` is `True` in Python — the exact accident this
    guards against."""
    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    assert client.post("/api/projects/p/ingest").status_code == 200
    before = client.get("/api/projects/p/setup").json()

    res = client.post("/api/projects/p/ingest", json={"force": "false"})

    assert res.status_code == 422
    assert "force" in res.json()["detail"]
    after = client.get("/api/projects/p/setup").json()
    assert after["generation"] == before["generation"]
    assert after["has_results"] == before["has_results"]


# ------------------------------------- M5: `provider` type-checked like `force`
#
# `force` is validated as an actual boolean two lines above `provider`'s
# resolution (see `test_ingest_rejects_a_non_boolean_force`), but until now
# `provider` was only ever coerced: `False`/`0` are falsy and fell straight
# through to the env default instead of being rejected, and `123` was
# stringified to `"123"` and rejected as an *unknown provider* (400) rather
# than as a malformed field (422). These three make `provider` consistent
# with `force`'s standard.


def test_ingest_rejects_a_boolean_provider(tmp_path, monkeypatch):
    """`{"provider": false}` must not silently fall through to the env
    default — it is a malformed field, not an absent one."""
    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    before = client.get("/api/projects/p/setup").json()

    res = client.post("/api/projects/p/ingest", json={"provider": False})

    assert res.status_code == 422
    assert "provider" in res.json()["detail"]
    after = client.get("/api/projects/p/setup").json()
    assert after["generation"] == before["generation"]
    assert after["has_results"] == before["has_results"]


def test_ingest_rejects_a_zero_provider(tmp_path, monkeypatch):
    """`{"provider": 0}` is falsy like `false`, and must be rejected the
    same way rather than silently resolving to the env default."""
    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)

    res = client.post("/api/projects/p/ingest", json={"provider": 0})

    assert res.status_code == 422
    assert "provider" in res.json()["detail"]


def test_ingest_rejects_a_numeric_provider_as_malformed_not_unknown(
    tmp_path, monkeypatch
):
    """`{"provider": 123}` is truthy, so before this fix it stringified to
    `"123"` and was rejected as an *unknown provider* (400) — a confusing
    message for a client that sent the wrong JSON type. It must be a 422
    naming `provider`, the same shape of error a non-boolean `force` gets."""
    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)

    res = client.post("/api/projects/p/ingest", json={"provider": 123})

    assert res.status_code == 422
    assert "provider" in res.json()["detail"]


def test_run_started_event_names_the_client_class(tmp_path, monkeypatch):
    from procurement.store import events as store_events

    client = _client(tmp_path, monkeypatch)  # LLM_PROVIDER=mock
    _project_with_vendor(client)

    assert client.post("/api/projects/p/ingest").status_code == 200

    evs = store_events.read_events(str(tmp_path), "p")
    started = [e for e in evs if e.action == "run.started"]
    assert started
    assert started[-1].detail.get("client") == "MockLLMClient"


def test_api_loads_the_dotenv_file_itself(monkeypatch):
    """The API must read `.env` on its own, not depend on how it was launched.

    `portal/app.py` calls `load_dotenv()` at import; `api/main.py` did not, so
    every provider needing a key reported `ready: false` in `/setup` — the
    dropdown showed "ANTHROPIC_API_KEY not set" beside a key that was sitting
    in `.env`. The only thing that had been loading it was a `--env-file` flag
    in the launcher, which `uvicorn api.main:app`, Docker and any other entry
    point do not pass.
    """
    import importlib

    import dotenv

    calls: list[tuple] = []
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: calls.append((a, k)))
    import api.main

    try:
        importlib.reload(api.main)
        assert calls, "api/main.py must call load_dotenv() at import"
    finally:
        monkeypatch.undo()
        importlib.reload(api.main)


def test_a_real_environment_variable_beats_the_dotenv_file(monkeypatch):
    """`.env` is a fallback for local dev, never an override.

    Docker Compose and CI inject provider keys as real environment variables.
    Loading `.env` with `override=True` would silently swap a deployment's key
    for whatever a stray file on the image holds.
    """
    import importlib

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sentinel-not-a-real-key")
    import api.main

    try:
        importlib.reload(api.main)
        assert os.environ["ANTHROPIC_API_KEY"] == "sentinel-not-a-real-key"
    finally:
        monkeypatch.undo()
        importlib.reload(api.main)


# --------------------------------------------------------------------------
# BUG-008: one ingestion run per project at a time.
#
# `ingest` is a synchronous FastAPI handler, so two requests execute
# concurrently in the threadpool. Before this guard, two overlapping runs
# breached the store's generation invariant in 30 of 30 measured trials
# (generation reached 1, not 2, after two runs) and one of the two always died
# of a filesystem race, surfacing as a 502. A second run is always either a
# mistake or a duplicate submission - never something to execute, since it
# re-spends the full LLM cost of the project for a result the first run is
# already producing.
# --------------------------------------------------------------------------

def _blocking_ingestion(monkeypatch):
    """Hold the FIRST `run_ingestion` open so a second request arrives mid-run.

    Only the first call blocks. Blocking every call would make a test unable to
    tell "refused by the run lock" apart from "still waiting on this fixture",
    which is how the per-project test below first went green for the wrong
    reason.

    Returns (entered, release): wait on `entered` to know the first run is
    inside the handler, set `release` to let it finish.
    """
    import threading

    import api.main as api_main

    entered, release = threading.Event(), threading.Event()
    real = api_main.run_ingestion
    first_call = threading.Lock()

    def blocking(*args, **kwargs):
        if first_call.acquire(blocking=False):
            entered.set()
            assert release.wait(timeout=30), "test never released the blocked run"
        return real(*args, **kwargs)

    monkeypatch.setattr(api_main, "run_ingestion", blocking)
    return entered, release


def test_a_second_ingest_while_one_is_running_is_rejected(tmp_path, monkeypatch):
    import threading

    import api.main as api_main

    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    entered, release = _blocking_ingestion(monkeypatch)

    first: list = []
    runner = threading.Thread(
        target=lambda: first.append(client.post("/api/projects/p/ingest")))
    runner.start()
    try:
        assert entered.wait(timeout=30), "the first run never reached the handler"
        # A separate client, so this cannot be serialised by the transport.
        # It carries the first client's session cookie rather than signing up
        # again: a second signup would collide on the admin's email, and the
        # point of the separate client is a separate *transport*, not a
        # separate account.
        second_client = TestClient(api_main.app)
        second_client.cookies.update(client.cookies)
        second = second_client.post("/api/projects/p/ingest")
    finally:
        release.set()
        runner.join(timeout=60)

    assert second.status_code == 409, \
        f"a concurrent run should be refused, got {second.status_code}"
    assert first[0].status_code == 200, "the first run must still succeed"


def test_the_run_lock_is_released_so_a_later_ingest_still_works(tmp_path, monkeypatch):
    """A lock that outlives its run would break ingestion permanently - a worse
    bug than the race it closes. The 502 path is the one that matters: it
    leaves the handler by exception, not by return."""
    import api.main as api_main

    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)

    def boom(*args, **kwargs):
        raise RuntimeError("extraction exploded")

    monkeypatch.setattr(api_main, "run_ingestion", boom)
    assert client.post("/api/projects/p/ingest").status_code == 502

    monkeypatch.undo()
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    assert client.post("/api/projects/p/ingest").status_code == 200, \
        "the lock was not released after the failed run"


def test_a_run_does_not_block_ingestion_of_a_different_project(tmp_path, monkeypatch):
    """Exclusivity is per project. A shared lock would serialise unrelated
    projects, turning one slow run into a queue for everyone."""
    import threading

    import api.main as api_main

    client = _client(tmp_path, monkeypatch)
    _project_with_vendor(client)
    # A second, independent project with its own vendor.
    client.post("/api/projects", json={"name": "Q"})
    body = (b"unit price 20 USD. This synthetic fixture body is padded with "
            b"filler prose so its character count clears the pipeline's "
            b"minimum-extractable-text guard, letting the extraction logic "
            b"under test run rather than the guard itself.")
    client.post("/api/projects/q/vendors",
                files={"file": ("bids.zip", _make_zip({"ACME/quote.txt": body}),
                                "application/zip")})

    entered, release = _blocking_ingestion(monkeypatch)
    first: list = []
    runner = threading.Thread(
        target=lambda: first.append(client.post("/api/projects/p/ingest")))
    runner.start()
    try:
        assert entered.wait(timeout=30), "the first run never reached the handler"
        # Separate transport, same session — see the note in the test above.
        other_client = TestClient(api_main.app)
        other_client.cookies.update(client.cookies)
        other = other_client.post("/api/projects/q/ingest")
    finally:
        release.set()
        runner.join(timeout=60)

    assert other.status_code == 200, \
        f"an unrelated project must not be blocked, got {other.status_code}"
