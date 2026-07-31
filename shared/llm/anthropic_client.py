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


_MAX_ENVELOPE_DEPTH = 3


def unwrap_envelope(data: dict, output_schema: type[BaseModel]) -> dict:
    """Strip a wrapper the model put around the whole structure.

    Observed live on real quotations: the entire answer arrives nested under
    `parameters` — `{"parameters": {"currency": "EUR", "base_price": ...}}` —
    and only on some calls, which is what disguised it as a per-document
    extraction problem. Pydantic ignores unknown keys, so the wrapped payload
    validates into a record with every field at its default and is stored as a
    successful extraction of nothing.

    The test is structural, not a list of known wrapper names: a payload of one
    key that the schema does not declare, holding a dict, cannot be the answer
    itself. A lone key the schema *does* declare is that field's value and is
    left alone, so a single-field schema is never mistaken for an envelope.
    """
    fields = set(output_schema.model_fields)
    for _ in range(_MAX_ENVELOPE_DEPTH):
        if len(data) != 1 or (set(data) & fields):
            break
        inner = next(iter(data.values()))
        if not isinstance(inner, dict):
            break
        data = inner
    return data


def recover_stringified_envelope(data: dict, output_schema: type[BaseModel]) -> dict:
    """Recover the structure when it arrives as a string on one scalar field.

    Observed live on the same quotations the `parameters` envelope hit, and
    only on some calls: the whole answer comes back as the string value of a
    single field — `{"base_price": "{\\"currency\\": \\"EUR\\", ...}"}`. The
    list/dict decoding below cannot reach it, because `base_price` is a float,
    so the string reached pydantic and failed validation on that one field.

    Deliberately narrow, so it can never swallow a value the vendor actually
    printed: the string must parse as a JSON *object* naming at least one
    schema field *other* than the one carrying it. `"50"` decodes to a number
    rather than an object and is left alone, and `{"facts": "{\\"facts\\":
    [...]}"}` — a list field wrapped in its own envelope — names no other
    field, so it still falls through to the list decoding that handles it.
    """
    fields = set(output_schema.model_fields)
    for name, value in data.items():
        if name not in fields or not isinstance(value, str):
            continue
        try:
            decoded = json.loads(value)
        except ValueError:
            continue
        if isinstance(decoded, dict) and (set(decoded) & fields) - {name}:
            return decoded
    return data


def require_known_field(data: dict, output_schema: type[BaseModel]) -> None:
    """Reject a payload naming nothing the schema declares.

    Defence behind `unwrap_envelope`, for the shapes it cannot repair. Every
    field of a quotation carries a default, so such a payload validates into an
    all-defaults record — currency "", base_price 0.0 — that reports `ok`. That
    silently breaks the store invariant "missing data is never coerced to a
    passing or zero value", and the zero then flows into the comparison as a
    real bid. Raising routes it to each extractor's failure path, which keeps
    the previously-good record and writes the reason into DocumentRecord.notes.
    """
    if set(data) & set(output_schema.model_fields):
        return
    raise RuntimeError(
        f"the model returned no field of {output_schema.__name__}: "
        f"got keys {sorted(data)!r}, expected any of "
        f"{sorted(output_schema.model_fields)!r}. Treating this as an empty "
        "extraction would store defaults as if they were the vendor's terms.")


def coerce_structured(data: dict, output_schema: type[BaseModel]) -> dict:
    """Normalise a model's structured answer, or raise if it cannot be read.

    Unwraps an envelope around the whole structure (nested under a wrapper key,
    or stringified onto one scalar field), rejects a payload that names nothing
    the schema declares, then decodes list-valued fields returned as a JSON
    string.

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
    out = unwrap_envelope(dict(data), output_schema)
    out = recover_stringified_envelope(out, output_schema)
    require_known_field(out, output_schema)
    out = dict(out)
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
