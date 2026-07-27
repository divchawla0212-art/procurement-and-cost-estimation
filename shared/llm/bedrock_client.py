import os
from pydantic import BaseModel


class BedrockClient:
    """LLMClient adapter for Claude on Amazon Bedrock.

    Auth is AWS IAM (no API keys). Model IDs carry an ``anthropic.`` prefix.
    The Bedrock SDK client is imported and constructed lazily inside
    ``classify_structure`` so importing this module — and running the
    non-live tests — needs no boto3, no AWS credentials, and no network.
    """

    supports_vision = True

    def __init__(self, model: str = "anthropic.claude-sonnet-5", region: str | None = None):
        self.model = model
        self.region = region or os.getenv("AWS_REGION")

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
        from anthropic import AnthropicBedrock

        client = AnthropicBedrock(aws_region=self.region)
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
        raise RuntimeError("Bedrock response contained no tool_use block")
