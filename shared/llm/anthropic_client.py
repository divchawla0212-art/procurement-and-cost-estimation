import os
from pydantic import BaseModel
import anthropic


class AnthropicClient:
    supports_vision = True

    def __init__(self, model: str = "claude-opus-4-8", api_key: str | None = None):
        self.model = model
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")

    def _build_tool(self, output_schema: type[BaseModel]) -> dict:
        return {
            "name": "emit_structure",
            "description": "Return the extracted structure.",
            "input_schema": output_schema.model_json_schema(),
        }

    def classify_structure(
        self,
        prompt: str,
        output_schema: type[BaseModel],
        context_text: str,
        images: list | None = None,
    ) -> dict:
        client = anthropic.Anthropic(api_key=self._api_key)
        tool = self._build_tool(output_schema)
        message = client.messages.create(
            model=self.model,
            max_tokens=1024,
            tools=[tool],
            tool_choice={"type": "tool", "name": "emit_structure"},
            messages=[{"role": "user", "content": f"{prompt}\n\n{context_text}"}],
        )
        for block in message.content:
            if block.type == "tool_use":
                return dict(block.input)
        raise RuntimeError("Anthropic response contained no tool_use block")
