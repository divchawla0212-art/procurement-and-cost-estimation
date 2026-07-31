import os, json
from pydantic import BaseModel

from shared.llm.anthropic_client import coerce_structured


class OpenAIClient:
    supports_vision = True

    def __init__(self, model: str = "gpt-4o", api_key: str | None = None):
        self.model = model
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")

    def _response_format(self, output_schema: type[BaseModel]) -> dict:
        return {
            "type": "json_schema",
            "json_schema": {"name": "structure", "schema": output_schema.model_json_schema()},
        }

    def classify_structure(self, prompt, output_schema, context_text, images=None) -> dict:
        from openai import OpenAI
        client = OpenAI(api_key=self._api_key)
        resp = client.chat.completions.create(
            model=self.model,
            response_format=self._response_format(output_schema),
            messages=[{"role": "user", "content": f"{prompt}\n\n{context_text}"}],
        )
        # same seam as the Claude adapters: an envelope around the structure,
        # or a payload naming no field of the schema, must not validate into a
        # record of defaults that reports success
        return coerce_structured(
            json.loads(resp.choices[0].message.content), output_schema)
