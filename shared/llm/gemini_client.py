import os, json
from pydantic import BaseModel


class GeminiClient:
    supports_vision = True

    def __init__(self, model: str = "gemini-1.5-pro", api_key: str | None = None):
        self.model = model
        self._api_key = api_key or os.getenv("GEMINI_API_KEY")

    def _response_schema(self, output_schema: type[BaseModel]) -> dict:
        return output_schema.model_json_schema()

    def classify_structure(self, prompt, output_schema, context_text, images=None) -> dict:
        import google.generativeai as genai
        genai.configure(api_key=self._api_key)
        model = genai.GenerativeModel(
            self.model,
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": self._response_schema(output_schema),
            },
        )
        resp = model.generate_content(f"{prompt}\n\n{context_text}")
        return json.loads(resp.text)
