import os
from shared.llm.interface import LLMClient
from shared.llm.mock_client import MockLLMClient
from shared.llm.anthropic_client import AnthropicClient
from shared.llm.openai_client import OpenAIClient
from shared.llm.gemini_client import GeminiClient
from shared.llm.bedrock_client import BedrockClient


def get_client(provider: str | None = None) -> LLMClient:
    """Build the extraction client.

    `provider=None` resolves through LLM_PROVIDER, which is what every
    environment-driven caller relies on. An explicit name wins, so a request
    can choose a provider without mutating the process environment. Neither
    an explicit empty string nor an unset/empty LLM_PROVIDER defaults to
    "mock" — there is no argument-free path to the mock client that does not
    name it.
    """
    name = (provider or "").strip().lower() or None
    if name is None:
        env = os.getenv("LLM_PROVIDER") or ""
        name = env.strip().lower() or None
    if name is None:
        raise ValueError(
            "No LLM_PROVIDER is configured. Set the LLM_PROVIDER environment "
            "variable (one of: mock, anthropic, openai, gemini, bedrock), or "
            "pass a provider explicitly to get_client()."
        )
    model = os.getenv("LLM_MODEL")
    kwargs = {"model": model} if model else {}
    if name == "mock":
        return MockLLMClient(response={})
    if name == "anthropic":
        return AnthropicClient(**kwargs)
    if name == "openai":
        return OpenAIClient(**kwargs)
    if name == "gemini":
        return GeminiClient(**kwargs)
    if name == "bedrock":
        return BedrockClient(**kwargs)
    raise ValueError(f"Unknown LLM_PROVIDER: {name}")
