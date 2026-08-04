import pytest
from shared.llm.factory import get_client
from shared.llm.mock_client import MockLLMClient
from shared.llm.anthropic_client import AnthropicClient
from shared.llm.bedrock_client import BedrockClient
from shared.llm.openai_client import OpenAIClient


def test_unset_provider_raises(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    with pytest.raises(ValueError, match="No LLM_PROVIDER is configured"):
        get_client()


def test_empty_string_provider_counts_as_unset(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "   ")
    with pytest.raises(ValueError, match="No LLM_PROVIDER is configured"):
        get_client()


def test_explicit_mock_still_works_with_provider_unset(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert isinstance(get_client("mock"), MockLLMClient)


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


def test_explicit_provider_wins_over_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    assert isinstance(get_client("openai"), OpenAIClient)


def test_none_reads_the_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    assert isinstance(get_client(), MockLLMClient)
    assert isinstance(get_client(None), MockLLMClient)


def test_provider_argument_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert isinstance(get_client("MOCK"), MockLLMClient)


def test_unknown_provider_argument_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    with pytest.raises(ValueError, match="banana"):
        get_client("banana")
