import pytest
from shared.llm.factory import get_client
from shared.llm.mock_client import MockLLMClient
from shared.llm.anthropic_client import AnthropicClient
from shared.llm.bedrock_client import BedrockClient


def test_default_is_mock(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert isinstance(get_client(), MockLLMClient)


def test_selects_bedrock(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    assert isinstance(get_client(), BedrockClient)


def test_selects_openai(monkeypatch):
    from shared.llm.openai_client import OpenAIClient
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    assert isinstance(get_client(), OpenAIClient)


def test_bedrock_honors_llm_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("LLM_MODEL", "anthropic.claude-opus-4-8")
    assert get_client().model == "anthropic.claude-opus-4-8"


def test_selects_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    assert isinstance(get_client(), AnthropicClient)


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nope")
    with pytest.raises(ValueError):
        get_client()
