"""Reading an enquiry document into the raise-an-RFQ form.

The extractor itself is a stand-in — `workflow/rfq_extractor.py` says so at
length — so there is nothing here asserting that a particular document yields a
particular reference. What is worth holding is everything *around* it: that the
route exists at a path FastAPI does not read as an RFQ id, that it is behind
the session middleware like every other workflow route, that it stores nothing,
and that the discipline it hands back is one the form's picker actually offers.

That last one is the test with a real failure behind it. The form's discipline
field became a controlled vocabulary after this feature was first written, and
a value outside it arrives marked "not a listed discipline" — the auto-fill
looks broken in exactly the demonstration it exists for, and nothing else
notices.
"""
import json

from fastapi.testclient import TestClient

from tests.auth_helpers import signed_in_admin
from workflow import disciplines
from workflow.rfq_extractor import extract_rfq_info


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return signed_in_admin(TestClient(api_main.app), tmp_path)


def _upload(client: TestClient, name: str = "enquiry.pdf", body: bytes = b"%PDF-1.4"):
    return client.post(
        "/api/workflow/rfqs/extract",
        files={"file": (name, body, "application/pdf")},
    )


# ------------------------------------------------------------ the extractor

def test_it_yields_the_four_fields_the_form_asks_for():
    info = extract_rfq_info("enquiry.pdf", b"")
    assert set(info) == {"reference", "package", "discipline", "value_estimate_aed"}


def test_the_discipline_is_one_the_picker_offers():
    # Whole-string against the vocabulary, the same rule `_registered_for` and
    # `covering` apply — a value that only matched as a substring would still
    # land in the form as unlisted.
    info = extract_rfq_info("enquiry.pdf", b"")
    offered = {
        disciplines.fold(label)
        for family, groups in disciplines.DISCIPLINES.items()
        for label in (family, *groups)
    }
    assert disciplines.fold(info["discipline"]) in offered


def test_an_invented_reference_says_so():
    # A synthesised fact must not be indistinguishable on screen from a
    # recorded one, and this value can reach a real RFQ record if the reader
    # accepts the auto-fill unedited.
    assert extract_rfq_info("enquiry.pdf", b"")["reference"].startswith("MOCK-")


# ---------------------------------------------------------------- the route

def test_the_route_returns_the_extracted_fields(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = _upload(client)
    assert r.status_code == 200, r.text
    assert r.json() == extract_rfq_info("enquiry.pdf", b"%PDF-1.4")


def test_the_extract_path_is_not_read_as_an_rfq_id(tmp_path, monkeypatch):
    # `/rfqs/extract` is declared above `/rfqs/{rfq_id}`. Were it below, the
    # literal segment would bind as an id and the answer would be a 404.
    client = _client(tmp_path, monkeypatch)
    assert _upload(client).status_code == 200


def test_it_challenges_a_caller_without_a_session(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    r = _upload(TestClient(api_main.app))
    assert r.status_code == 401


def test_reading_a_document_stores_nothing(tmp_path, monkeypatch):
    # The RFQ is created by the form's submit, not by the read. Were the route
    # to write, an abandoned upload would leave an RFQ nobody raised.
    client = _client(tmp_path, monkeypatch)
    before = (tmp_path / "workflow.json").read_text() if (tmp_path / "workflow.json").exists() else None
    assert _upload(client).status_code == 200
    after = (tmp_path / "workflow.json").read_text() if (tmp_path / "workflow.json").exists() else None
    assert after == before
    if after is not None:
        assert json.loads(after).get("rfqs", []) == []
