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

    The wrapped object is sometimes a *string* rather than a dict — the same
    quotation returned `{"parameter": {...}}` on one call and the stringified
    equivalent under `parameter_name` on another. That shape reached neither
    repair (this one needs a dict; `recover_stringified_envelope` only inspects
    keys the schema declares), so a fully-read quotation raised and the vendor
    went blank. A decoded string is only accepted when it names a field the
    schema declares, so a stray string is still rejected downstream rather than
    stored as an answer.

    The envelope is sometimes not alone. ADPOWER's gas-14 quotation came back
    as `{"parameter_name": ..., "parameter_value": ...}` — two keys, neither a
    schema field — and the old one-key test bailed before looking at either
    value, losing the vendor's whole commercial column. The count was never
    what made the unwrap safe; the inner object naming schema fields is. So a
    sibling key no longer blocks the unwrap, and the structural test stands on
    its own. When more than one value qualifies there is no evidence for
    either, so it raises rather than deciding the vendor's terms on key order.
    """
    fields = set(output_schema.model_fields)
    for _ in range(_MAX_ENVELOPE_DEPTH):
        if set(data) & fields:
            break
        inner = _sole_envelope_value(data, fields)
        if inner is None:
            break
        data = inner
    return data


def _decoded_answer(value, fields: set[str]) -> dict | None:
    """`value` as an object naming at least one schema field, or None.

    A JSON string holding such an object counts; anything else does not. This
    is the whole safety test: an object that names a field the schema declares
    is the answer, and nothing else is ever treated as one.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if isinstance(value, dict) and (set(value) & fields):
        return value
    return None


def _sole_envelope_value(data: dict, fields: set[str]) -> dict | None:
    """The one value that is the wrapped structure, or None to stop unwrapping.

    A single key keeps its original latitude: any dict is descended into, so a
    two- or three-deep wrapper still unwraps on the next pass. With siblings
    present that latitude would be a guess, so a value must *demonstrably* be
    the answer — and exactly one of them may be.
    """
    if len(data) == 1:
        inner = next(iter(data.values()))
        if isinstance(inner, str):
            return _decoded_answer(inner, fields)
        return inner if isinstance(inner, dict) else None
    answers = [a for a in (_decoded_answer(v, fields) for v in data.values())
               if a is not None]
    return answers[0] if len(answers) == 1 else None


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
        "extraction would store defaults as if they were the vendor's terms. "
        f"payload was {_preview(data)}")


# Long enough to show the shape of a wrapper and what it holds, short enough
# that the note it lands in stays readable in DocumentRecord.notes and in the
# web app. The gas-14 failure recorded key names only, which is exactly the
# information that cannot distinguish a recoverable envelope from a bare
# name/value pair - so the next occurrence of an unrepairable shape has to
# carry its values with it.
_PREVIEW_CHARS = 800


def _preview(data: dict) -> str:
    try:
        text = json.dumps(data, default=str, sort_keys=True)
    except (TypeError, ValueError):
        text = repr(data)
    if len(text) <= _PREVIEW_CHARS:
        return text
    return f"{text[:_PREVIEW_CHARS]}... [{len(text)} chars total]"


def _admits_str(annotation) -> bool:
    """True when the field could legitimately hold a string the vendor printed."""
    return annotation is str or any(a is str for a in typing.get_args(annotation))


def unbox_scalar_fields(data: dict, output_schema: type[BaseModel]) -> dict:
    """Unbox a number the model wrapped in `{"value": …}`.

    Observed live on the same quotations as the two envelope shapes above, and
    again only on some calls: `base_price` arrives as `{"value": 1110836}`, or
    as that object stringified. Pydantic rejects the box on a float field, so
    the whole extraction fails and the vendor's column goes blank in the
    statement — with the number the model read sitting inside the box.

    Narrow on both sides, because the alternative to unboxing is a loud
    failure, never a guess:

    - only a *lone* `value` key is a box. `{"value": 1110836, "currency":
      "AED"}` is an answer; unboxing it would drop the currency and price the
      bid in the wrong money, so it falls through to pydantic.
    - only fields the schema cannot hold a string in are unboxed, so a vendor
      who literally prints `{"value": …}` in their delivery terms keeps it.

    This reads the number the model returned. It never supplies one.
    """
    out = dict(data)
    for name, field in output_schema.model_fields.items():
        if typing.get_origin(field.annotation) in (list, dict):
            continue
        if _admits_str(field.annotation):
            continue
        value = out.get(name)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        if not isinstance(value, dict) or set(value) != {"value"}:
            continue
        inner = value["value"]
        if inner is None or isinstance(inner, (bool, int, float, str)):
            out[name] = inner
    return out


def coerce_structured(data: dict, output_schema: type[BaseModel]) -> dict:
    """Normalise a model's structured answer, or raise if it cannot be read.

    Unwraps an envelope around the whole structure (nested under a wrapper key,
    or stringified onto one scalar field), rejects a payload that names nothing
    the schema declares, unboxes a number returned as `{"value": …}`, then
    decodes list-valued fields returned as a JSON string.

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
    out = unbox_scalar_fields(out, output_schema)
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
