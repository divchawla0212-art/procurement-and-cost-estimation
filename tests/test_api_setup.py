"""Key-free API tests for the project-setup + ingestion routes."""

import io
import zipfile

from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return TestClient(api_main.app)


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


def test_ingest_without_vendors_is_rejected(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/projects", json={"name": "P"})
    res = client.post("/api/projects/p/ingest")
    assert res.status_code == 422


def test_setup_reports_provider_state(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/projects", json={"name": "P"})
    provider = client.get("/api/projects/p/setup").json()["provider"]
    assert provider["provider"] == "mock"
    assert provider["ready"] is True


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
