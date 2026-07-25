import copy
from pydantic import BaseModel


class MockLLMClient:
    def __init__(self, response: dict, supports_vision: bool = True):
        self._response = response
        self.supports_vision = supports_vision
        self.last_call: dict | None = None

    def classify_structure(
        self,
        prompt: str,
        output_schema: type[BaseModel],
        context_text: str,
        images: list | None = None,
    ) -> dict:
        self.last_call = {"prompt": prompt, "context_text": context_text}
        return copy.deepcopy(self._response)
