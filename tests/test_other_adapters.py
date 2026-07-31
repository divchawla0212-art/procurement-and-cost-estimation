import json
import sys
import types

import pytest
from pydantic import BaseModel
from shared.llm.interface import LLMClient
from shared.llm.openai_client import OpenAIClient
from shared.llm.gemini_client import GeminiClient


class _Layout(BaseModel):
    header_row: int
    columns: dict[str, int]


def _fake_openai(monkeypatch, payload):
    """Replace the lazily-imported SDK with one that replays `payload`."""
    mod = types.ModuleType("openai")

    class _Client:
        def __init__(self, **kw):
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(
                create=lambda **kwargs: types.SimpleNamespace(choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(content=json.dumps(payload)))])))

    mod.OpenAI = _Client
    monkeypatch.setitem(sys.modules, "openai", mod)


def _fake_genai(monkeypatch, payload):
    genai = types.ModuleType("google.generativeai")
    genai.configure = lambda **kw: None

    class _Model:
        def __init__(self, *a, **kw):
            pass

        def generate_content(self, _prompt):
            return types.SimpleNamespace(text=json.dumps(payload))

    genai.GenerativeModel = _Model
    google = types.ModuleType("google")
    google.generativeai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.generativeai", genai)


# These two adapters parsed the model's JSON straight through, so they carried
# the same defect the Claude adapters did: a structure nested under a wrapper
# key validates into a record of defaults and is stored as a successful
# extraction of nothing.
def test_openai_unwraps_an_envelope_around_the_structure(monkeypatch):
    _fake_openai(monkeypatch, {"parameters": {"header_row": 3, "columns": {}}})
    out = OpenAIClient(api_key="x").classify_structure("p", _Layout, "ctx")
    assert out == {"header_row": 3, "columns": {}}


def test_openai_rejects_a_payload_naming_no_field_of_the_schema(monkeypatch):
    _fake_openai(monkeypatch, {"totally": "unrelated"})
    with pytest.raises(RuntimeError):
        OpenAIClient(api_key="x").classify_structure("p", _Layout, "ctx")


def test_gemini_unwraps_an_envelope_around_the_structure(monkeypatch):
    _fake_genai(monkeypatch, {"parameters": {"header_row": 4, "columns": {}}})
    out = GeminiClient(api_key="x").classify_structure("p", _Layout, "ctx")
    assert out == {"header_row": 4, "columns": {}}


def test_gemini_rejects_a_payload_naming_no_field_of_the_schema(monkeypatch):
    _fake_genai(monkeypatch, {"totally": "unrelated"})
    with pytest.raises(RuntimeError):
        GeminiClient(api_key="x").classify_structure("p", _Layout, "ctx")


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
