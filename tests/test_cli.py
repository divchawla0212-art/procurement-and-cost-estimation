import json
from tests.fixtures.make_mini_boq import make
from cost_estimation.cli import run_ingest, main


def test_run_ingest_writes_json(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")  # deterministic, no key
    # mock returns {} -> header mapper yields no columns -> zero items,
    # so seed the layout by pointing the CLI's client at a canned response:
    from cost_estimation import cli
    from shared.llm.mock_client import MockLLMClient
    monkeypatch.setattr(cli, "get_client", lambda: MockLLMClient(response={
        "header_row": 7, "columns": {"code": 2, "description": 3, "uom": 5,
                                     "quantity": 6, "unit_price": 11, "total": 12}}))

    make(str(tmp_path / "COSTING mini Electrical.xlsx"))
    out = tmp_path / "out.json"
    ds = run_ingest(str(tmp_path), str(out))

    assert out.exists()
    data = json.loads(out.read_text())
    assert len(data["work_packages"]) == 1
    assert len(ds.work_packages[0].cost_items) == 2


def test_main_returns_zero(tmp_path, monkeypatch):
    from cost_estimation import cli
    from shared.llm.mock_client import MockLLMClient
    monkeypatch.setattr(cli, "get_client", lambda: MockLLMClient(response={
        "header_row": 7, "columns": {"code": 2, "quantity": 6, "unit_price": 11, "total": 12}}))
    make(str(tmp_path / "COSTING mini Electrical.xlsx"))
    code = main(["ingest", str(tmp_path), "--out", str(tmp_path / "o.json")])
    assert code == 0
