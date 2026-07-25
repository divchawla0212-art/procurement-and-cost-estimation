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
