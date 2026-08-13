"""The single-page app's deep links have to survive a refresh.

Every screen now has a URL (`docs/superpowers/specs/2026-08-13-url-backed-navigation-design.md`),
which means the browser will ask this server for paths like
`/projects/prj_5049beff` that exist only inside the React bundle's route table.
`StaticFiles(html=True)` serves `index.html` for *directory* requests and 404s
everything else, so without a fallback every bookmarked or shared URL breaks on
reload.

**These tests exist because the bug is invisible on a workstation.** Vite's dev
server performs this fallback itself, so `run.ps1` never shows it; it appears
only where the compiled bundle is served from this app, which is a container.

The second half matters as much as the first: a catch-all that answers
*anything* unmatched with `index.html` will hand a misspelled API route a 200
and a page, turning a 404 a developer can read into a JSON parse error they
cannot. `/api/*` must still 404 as JSON.
"""

import importlib
import json

import pytest

from fastapi.testclient import TestClient


@pytest.fixture
def dist(tmp_path):
    """A minimal built front end, standing in for `web/dist`."""
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(
        "<!doctype html><title>Tender Eval</title><div id=root></div>",
        encoding="utf-8",
    )
    (d / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (d / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return d


@pytest.fixture
def client(tmp_path, dist, monkeypatch):
    """A client over an app that believes a front end has been built.

    `api.main` reads `WEB_DIST` and decides whether to mount at import time, so
    the module is reloaded inside the patched environment rather than reused
    from whatever the rest of the suite imported first.
    """
    monkeypatch.setenv("WEB_DIST", str(dist))
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("AUTH_DISABLED", "0")
    import api.main as api_main

    api_main = importlib.reload(api_main)
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path / "projects"))
    yield TestClient(api_main.app)
    # Leave the module as the rest of the suite expects to find it: without a
    # WEB_DIST there is no mount, which is the source-checkout default.
    monkeypatch.delenv("WEB_DIST", raising=False)
    importlib.reload(api_main)


# --------------------------------------------------------------- deep links


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/projects",
        "/projects/prj_5049beff",
        "/projects/prj_5049beff/items/itm_7",
        "/bidders",
        "/rfqs",
        "/rfqs/rfq_1",
        "/bid-sets",
        "/bid-sets/new",
        "/bid-sets/haliba/setup",
        "/bid-sets/haliba/matrix",
        "/admin",
        # Not in the route table either — the client sends it to the roster, and
        # the server's job is only to hand over the app that can decide that.
        "/nowhere/at/all",
    ],
)
def test_a_deep_link_serves_the_app(client, path):
    res = client.get(path)
    assert res.status_code == 200, path
    assert "text/html" in res.headers["content-type"]
    assert 'id=root' in res.text


def test_a_query_string_does_not_change_the_answer(client):
    res = client.get("/bid-sets/haliba/matrix?vendor=Alpha%20Systems")
    assert res.status_code == 200
    assert 'id=root' in res.text


# ------------------------------------------------------------- real assets


def test_a_real_asset_is_still_served_as_itself(client):
    res = client.get("/assets/index-abc123.js")
    assert res.status_code == 200
    assert "console.log(1)" in res.text
    # The fallback must not shadow the bundle: an asset answered with HTML is a
    # blank page and a syntax error in the console.
    assert "text/html" not in res.headers["content-type"]


def test_index_html_is_reachable_by_its_own_name(client):
    assert client.get("/index.html").status_code == 200


# ---------------------------------------------------------- the /api carve-out


@pytest.mark.parametrize("path", ["/api/nope", "/api/projects", "/api/workflow/bogus"])
def test_an_api_path_is_never_answered_with_the_app(client, path):
    """The trap this fallback invites.

    Answering an unmatched `/api/...` with the app turns a readable refusal into
    a JSON parse error at the call site, and hides typos in the front end's own
    fetchers behind a 200.

    The status is deliberately not pinned to 404. The auth middleware guards
    every `/api/` path outside `PUBLIC_PATHS` *including ones that do not
    exist*, so an anonymous caller gets 401 before routing is ever consulted —
    fail-closed, and exactly what `test_auth_middleware.py` asserts. What must
    hold here is narrower and survives that: never 200, and never HTML.
    """
    res = client.get(path)
    assert res.status_code != 200, path
    assert "text/html" not in res.headers["content-type"], path
    json.loads(res.text)  # parses as JSON, whatever the body says


def test_a_real_api_route_is_untouched(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"ok": True}


# ------------------------------------------------------- no build present


def test_a_source_checkout_without_a_build_serves_the_api_alone(tmp_path, monkeypatch):
    """`run.ps1` runs the API against Vite, with no `web/dist` at all.

    The mount is conditional, and so is the fallback: with nothing built there
    is no `index.html` to serve, and a deep link must not 500 trying.
    """
    monkeypatch.setenv("WEB_DIST", str(tmp_path / "does-not-exist"))
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main

    api_main = importlib.reload(api_main)
    try:
        client = TestClient(api_main.app)
        assert client.get("/api/health").status_code == 200
        assert client.get("/projects/prj_1").status_code == 404
    finally:
        monkeypatch.delenv("WEB_DIST", raising=False)
        importlib.reload(api_main)
