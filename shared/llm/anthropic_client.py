import json
import os
import typing
from pydantic import BaseModel
import anthropic

# A structured extraction returns one entry per thing found, so the ceiling
# scales with the document, not with the prompt. The real client MR truncated
# at 1024 and came back as an empty dict that every extractor read as "this
# document states nothing" - see check_truncated below.
DEFAULT_MAX_TOKENS = 8192


def max_output_tokens() -> int:
    try:
        return int(os.getenv("LLM_MAX_TOKENS") or DEFAULT_MAX_TOKENS)
    except ValueError:
        return DEFAULT_MAX_TOKENS


def check_truncated(stop_reason: str | None, model: str) -> None:
    """Raise when a response was cut off mid-structure.

    A truncated tool call is not a short answer. The JSON never closes, so the
    SDK yields `{}` or a partial object, and every extractor here reads an
    absent list as an empty extraction and reports `ok`. That turns "the
    document was too long to read in one call" into "the document says
    nothing", stores it, and prunes the previously-good records to match.
    Raising instead routes it through each extractor's failure path, which
    keeps the prior records and writes the reason to DocumentRecord.notes.
    """
    if stop_reason == "max_tokens":
        raise RuntimeError(
            f"response truncated at the max_tokens ceiling "
            f"({max_output_tokens()}) for model {model}: the structure is "
            "incomplete and cannot be trusted. Raise LLM_MAX_TOKENS, or split "
            "the document.")


def coerce_structured(data: dict, output_schema: type[BaseModel]) -> dict:
    """Decode list-valued fields the model returned as a JSON string.

    Observed against the real client MR: `requirements` came back as a string
    holding the whole `{"requirements": [...]}` envelope. Callers then do
    `list(value)`, which on a string iterates *characters* — tens of thousands
    of them, each discarded by a per-entry validator whose job is to survive
    one malformed row. The result is a successful, empty, unexplained
    extraction.

    Only fields the schema declares as a list or dict are touched. A string
    field is left exactly as it arrived, because "50" on a value field is the
    vendor's printed text, not a number waiting to be parsed.
    """
    out = dict(data)
    for name, field in output_schema.model_fields.items():
        if typing.get_origin(field.annotation) not in (list, dict):
            continue
        value = out.get(name)
        if not isinstance(value, str):
            continue
        try:
            decoded = json.loads(value)
        except ValueError as exc:
            raise RuntimeError(
                f"field {name!r} came back as a string that is not JSON, so "
                f"the structure cannot be read: {exc}") from exc
        # the model sometimes emits the whole envelope as the field's value
        if isinstance(decoded, dict) and name in decoded:
            decoded = decoded[name]
        out[name] = decoded
    return out


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
            max_tokens=max_output_tokens(),
            tools=[tool],
            tool_choice={"type": "tool", "name": "emit_structure"},
            messages=[{"role": "user", "content": f"{prompt}\n\n{context_text}"}],
        )
        # before reading the block: a truncated tool call still arrives as a
        # tool_use block, just with the object cut short
        check_truncated(message.stop_reason, self.model)
        for block in message.content:
            if block.type == "tool_use":
                return coerce_structured(dict(block.input), output_schema)
        raise RuntimeError("Anthropic response contained no tool_use block")
