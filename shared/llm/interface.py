from typing import Protocol, runtime_checkable
from pydantic import BaseModel


@runtime_checkable
class LLMClient(Protocol):
    supports_vision: bool

    def classify_structure(
        self,
        prompt: str,
        output_schema: type[BaseModel],
        context_text: str,
        images: list | None = None,
    ) -> dict: ...
