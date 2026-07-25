import os
import pytest
from pydantic import BaseModel
from shared.llm.interface import LLMClient
from shared.llm.anthropic_client import AnthropicClient


class _Layout(BaseModel):
    header_row: int
    columns: dict[str, int]


def test_anthropic_client_satisfies_protocol():
    client = AnthropicClient(api_key="dummy")
    assert isinstance(client, LLMClient)
    assert client.supports_vision is True


def test_build_tool_uses_schema():
    client = AnthropicClient(api_key="dummy")
    tool = client._build_tool(_Layout)
    assert tool["name"] == "emit_structure"
    assert "header_row" in tool["input_schema"]["properties"]


@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY")
def test_live_classify_structure_returns_dict():
    client = AnthropicClient()
    out = client.classify_structure(
        prompt="Return header_row=1 and empty columns.",
        output_schema=_Layout,
        context_text="col A | col B",
    )
    assert isinstance(out, dict)
    assert "header_row" in out
