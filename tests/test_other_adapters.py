from pydantic import BaseModel
from shared.llm.interface import LLMClient
from shared.llm.openai_client import OpenAIClient
from shared.llm.gemini_client import GeminiClient


class _Layout(BaseModel):
    header_row: int
    columns: dict[str, int]


def test_adapters_satisfy_protocol():
    assert isinstance(OpenAIClient(api_key="x"), LLMClient)
    assert isinstance(GeminiClient(api_key="x"), LLMClient)


def test_openai_response_format_embeds_schema():
    rf = OpenAIClient(api_key="x")._response_format(_Layout)
    assert rf["type"] == "json_schema"
    assert "header_row" in rf["json_schema"]["schema"]["properties"]


def test_gemini_response_schema_is_schema():
    rs = GeminiClient(api_key="x")._response_schema(_Layout)
    assert "header_row" in rs["properties"]
