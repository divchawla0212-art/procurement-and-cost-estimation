import pytest
from shared.llm.factory import get_client
from shared.llm.mock_client import MockLLMClient
from shared.llm.anthropic_client import AnthropicClient


def test_default_is_mock(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert isinstance(get_client(), MockLLMClient)


def test_selects_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    assert isinstance(get_client(), AnthropicClient)


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nope")
    with pytest.raises(ValueError):
        get_client()
