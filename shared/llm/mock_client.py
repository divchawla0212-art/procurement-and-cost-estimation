import copy
from pydantic import BaseModel


class MockLLMClient:
    def __init__(self, response: dict | list[dict], supports_vision: bool = True):
        # A list is a call sequence, for extractors that chunk their input. The
        # last entry repeats once exhausted: a test that miscounts chunks should
        # fail on its own assertion, not on an IndexError from the fixture.
        self._responses = list(response) if isinstance(response, list) else [response]
        self.supports_vision = supports_vision
        self.last_call: dict | None = None
        self.calls: list[dict] = []

    def classify_structure(
        self,
        prompt: str,
        output_schema: type[BaseModel],
        context_text: str,
        images: list | None = None,
    ) -> dict:
        self.last_call = {"prompt": prompt, "context_text": context_text}
        self.calls.append(self.last_call)
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return copy.deepcopy(self._responses[index])
