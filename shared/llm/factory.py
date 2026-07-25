import os
from shared.llm.interface import LLMClient
from shared.llm.mock_client import MockLLMClient
from shared.llm.anthropic_client import AnthropicClient
from shared.llm.openai_client import OpenAIClient
from shared.llm.gemini_client import GeminiClient


def get_client() -> LLMClient:
    provider = os.getenv("LLM_PROVIDER", "mock").lower()
    model = os.getenv("LLM_MODEL")
    kwargs = {"model": model} if model else {}
    if provider == "mock":
        return MockLLMClient(response={})
    if provider == "anthropic":
        return AnthropicClient(**kwargs)
    if provider == "openai":
        return OpenAIClient(**kwargs)
    if provider == "gemini":
        return GeminiClient(**kwargs)
    raise ValueError(f"Unknown LLM_PROVIDER: {provider}")
