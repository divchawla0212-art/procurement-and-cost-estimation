from pydantic import BaseModel
from shared.llm.mock_client import MockLLMClient


class _Layout(BaseModel):
    header_row: int
    columns: dict[str, int]


def test_mock_returns_configured_response():
    canned = {"header_row": 7, "columns": {"code": 2, "quantity": 6}}
    client = MockLLMClient(response=canned)
    out = client.classify_structure("map this", _Layout, "row preview")
    assert out == canned
    assert _Layout.model_validate(out).header_row == 7


def test_mock_records_last_call():
    client = MockLLMClient(response={})
    client.classify_structure("PROMPT", _Layout, "PREVIEW")
    assert client.last_call["prompt"] == "PROMPT"
    assert client.last_call["context_text"] == "PREVIEW"


def test_mock_returns_a_copy():
    canned = {"columns": {"code": 2}}
    client = MockLLMClient(response=canned)
    out = client.classify_structure("p", _Layout, "c")
    out["columns"]["code"] = 999
    assert canned["columns"]["code"] == 2


def test_mock_client_returns_a_sequence_in_order():
    c = MockLLMClient([{"rows": 1}, {"rows": 2}])
    assert c.classify_structure("p", _Layout, "t")["rows"] == 1
    assert c.classify_structure("p", _Layout, "t")["rows"] == 2
    # exhausted: the last response repeats rather than raising, so a test that
    # miscounts chunks fails on its assertion, not on an IndexError
    assert c.classify_structure("p", _Layout, "t")["rows"] == 2


def test_mock_client_still_accepts_a_single_dict():
    c = MockLLMClient({"rows": 1})
    assert c.classify_structure("p", _Layout, "t")["rows"] == 1
    assert c.classify_structure("p", _Layout, "t")["rows"] == 1
