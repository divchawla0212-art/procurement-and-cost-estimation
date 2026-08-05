"""Key-free API tests for the technical-feedback write route.

These replace the three `save_feedback` tests that lived in
`test_statement_view.py` while the Streamlit portal was the only writer of
`VendorFacts.technical_feedback`. The invariants they pin are unchanged —
INV-S5 (one generation bump per write transaction) and the rule that a note
without a reason is refused *before* anything is written — but the surface
that has to honour them is now the API.
"""

from fastapi.testclient import TestClient

from procurement.pipeline import run_ingestion
from procurement.store import events, snapshots

from tests.test_pipeline_rfq import RfqClient
from tests.test_pipeline_vocabulary import _project


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return TestClient(api_main.app)


def _ingested(tmp_path, monkeypatch):
    """A project with one vendor's facts already in the store."""
    root = _project(tmp_path)
    run_ingestion(root, "p", RfqClient())
    return root, _client(tmp_path, monkeypatch)


def _put(client, vendor, text, reason):
    return client.put(f"/api/projects/p/vendors/{vendor}/feedback",
                      json={"text": text, "reason": reason})


def test_saving_a_note_bumps_the_generation_once(tmp_path, monkeypatch):
    """INV-S5: one write transaction, one bump — not one per file touched."""
    root, client = _ingested(tmp_path, monkeypatch)
    before = snapshots.get_generation(root, "p")

    res = _put(client, "KERUI", "FULLY COMPLIED", "checked against MR 4.2.7")

    assert res.status_code == 200
    assert snapshots.get_generation(root, "p") == before + 1
    assert snapshots.load_facts(root, "p", "KERUI").technical_feedback == "FULLY COMPLIED"


def test_saving_a_note_records_one_event_carrying_the_reason(tmp_path, monkeypatch):
    root, client = _ingested(tmp_path, monkeypatch)

    _put(client, "KERUI", "FULLY COMPLIED", "checked against MR 4.2.7")

    edits = [e for e in events.read_events(root, "p")
             if e.action == "facts.feedback_edited"]
    assert len(edits) == 1
    assert edits[0].target == "KERUI"
    assert edits[0].detail["reason"] == "checked against MR 4.2.7"


def test_a_note_without_a_reason_is_refused(tmp_path, monkeypatch):
    """The reason is what makes the note auditable. A blank one is refused
    before anything is written, so the store is untouched — not rolled back."""
    root, client = _ingested(tmp_path, monkeypatch)
    before = snapshots.get_generation(root, "p")

    res = _put(client, "KERUI", "FULLY COMPLIED", "   ")

    assert res.status_code == 422
    assert snapshots.get_generation(root, "p") == before
    assert snapshots.load_facts(root, "p", "KERUI").technical_feedback is None


def test_a_vendor_with_no_stored_facts_is_a_404(tmp_path, monkeypatch):
    root, client = _ingested(tmp_path, monkeypatch)
    before = snapshots.get_generation(root, "p")

    res = _put(client, "NOBODY", "FULLY COMPLIED", "typo in the vendor name")

    assert res.status_code == 404
    assert snapshots.get_generation(root, "p") == before


def test_an_unknown_project_is_a_404(tmp_path, monkeypatch):
    _, client = _ingested(tmp_path, monkeypatch)

    res = client.put("/api/projects/no-such/vendors/KERUI/feedback",
                     json={"text": "x", "reason": "y"})

    assert res.status_code == 404


def test_the_note_is_readable_back_through_the_statement(tmp_path, monkeypatch):
    """The write is only worth having because the statement renders it — the
    row exists in `build_statement` either way, so a route that wrote to the
    wrong place would still 200 without this."""
    _, client = _ingested(tmp_path, monkeypatch)

    _put(client, "KERUI", "FULLY COMPLIED", "checked against MR 4.2.7")

    statement = client.get("/api/projects/p/statement").json()
    row = next(r for r in statement["rows"] if r["key"] == "technical_feedback")
    assert row["cells"]["KERUI"]["text"] == "FULLY COMPLIED"
