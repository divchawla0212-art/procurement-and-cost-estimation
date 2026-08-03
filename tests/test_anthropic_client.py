import json
import os
import pytest
from pydantic import BaseModel
from shared.llm.interface import LLMClient
from shared.llm.anthropic_client import AnthropicClient


class _Layout(BaseModel):
    header_row: int
    columns: dict[str, int]


def test_anthropic_client_satisfies_protocol():
    client = AnthropicClient(api_key="dummy")
    assert isinstance(client, LLMClient)
    assert client.supports_vision is True


def test_build_tool_uses_schema():
    client = AnthropicClient(api_key="dummy")
    tool = client._build_tool(_Layout)
    assert tool["name"] == "emit_structure"
    assert "header_row" in tool["input_schema"]["properties"]


class _Block:
    def __init__(self, type, input=None, text=None):
        self.type, self.input, self.text = type, input or {}, text


class _Message:
    def __init__(self, blocks, stop_reason):
        self.content, self.stop_reason = blocks, stop_reason


class _FakeAnthropic:
    """Records the create() kwargs and replays a canned message."""
    last_kwargs: dict = {}

    def __init__(self, message):
        self._message = message
        self.messages = self

    def create(self, **kwargs):
        type(self).last_kwargs = kwargs
        return self._message

    def __call__(self, *a, **kw):
        return self


def _patch(monkeypatch, message):
    import shared.llm.anthropic_client as mod
    fake = _FakeAnthropic(message)
    monkeypatch.setattr(mod.anthropic, "Anthropic", lambda **kw: fake)
    return fake


def test_a_truncated_tool_call_raises_instead_of_returning_an_empty_dict(monkeypatch):
    # a response cut off by max_tokens leaves the tool JSON unparseable, so the
    # SDK hands back {}. Returning that makes an extractor store nothing and
    # report success — the failure has to be visible, not silently empty.
    _patch(monkeypatch, _Message([_Block("tool_use", input={})], "max_tokens"))
    with pytest.raises(RuntimeError) as exc:
        AnthropicClient(api_key="dummy").classify_structure("p", _Layout, "ctx")
    assert "max_tokens" in str(exc.value)


def test_a_partially_filled_truncated_tool_call_also_raises(monkeypatch):
    # the dangerous shape: enough JSON survived to look like a real answer,
    # so a caller replacing stored records would drop everything after the cut
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"header_row": 1})], "max_tokens"))
    with pytest.raises(RuntimeError):
        AnthropicClient(api_key="dummy").classify_structure("p", _Layout, "ctx")


def test_a_complete_tool_call_is_returned_normally(monkeypatch):
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"header_row": 1, "columns": {}})], "tool_use"))
    out = AnthropicClient(api_key="dummy").classify_structure("p", _Layout, "ctx")
    assert out == {"header_row": 1, "columns": {}}


def test_the_output_ceiling_is_large_enough_for_a_real_requirement_list(monkeypatch):
    # 1024 truncated the real MR at 13k input tokens; a spec yields far more
    # entries than that ceiling can hold
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"header_row": 1, "columns": {}})], "tool_use"))
    AnthropicClient(api_key="dummy").classify_structure("p", _Layout, "ctx")
    assert _FakeAnthropic.last_kwargs["max_tokens"] >= 8192


def test_the_output_ceiling_is_configurable(monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "2048")
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"header_row": 1, "columns": {}})], "tool_use"))
    AnthropicClient(api_key="dummy").classify_structure("p", _Layout, "ctx")
    assert _FakeAnthropic.last_kwargs["max_tokens"] == 2048


class _Listy(BaseModel):
    facts: list[dict] = []
    label: str = ""


_ENTRIES = [{"clause_ref": "1.1", "text": "one"}, {"clause_ref": "1.2", "text": "two"}]


def test_a_list_field_returned_as_a_json_string_is_decoded(monkeypatch):
    # observed live: the model emits the array as a JSON string, and
    # list("[{...}]") iterates it into single characters that every per-entry
    # validator then discards - a silent empty extraction reported as ok
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"facts": json.dumps(_ENTRIES)})], "tool_use"))
    out = AnthropicClient(api_key="dummy").classify_structure("p", _Listy, "ctx")
    assert out["facts"] == _ENTRIES


def test_a_list_field_wrapped_in_its_own_envelope_is_unwrapped(monkeypatch):
    # the exact live shape: {"facts": "{\"facts\": [ ... ]}"}
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"facts": json.dumps({"facts": _ENTRIES})})],
        "tool_use"))
    out = AnthropicClient(api_key="dummy").classify_structure("p", _Listy, "ctx")
    assert out["facts"] == _ENTRIES


def test_a_genuine_list_is_passed_through_untouched(monkeypatch):
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"facts": _ENTRIES})], "tool_use"))
    out = AnthropicClient(api_key="dummy").classify_structure("p", _Listy, "ctx")
    assert out["facts"] == _ENTRIES


def test_a_string_field_is_never_decoded_even_when_it_parses_as_json(monkeypatch):
    # only fields the schema declares as lists are coerced; "50" on a string
    # field is the vendor's printed value, not a number to be parsed
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"facts": [], "label": "50"})], "tool_use"))
    out = AnthropicClient(api_key="dummy").classify_structure("p", _Listy, "ctx")
    assert out["label"] == "50"


def test_an_undecodable_list_field_raises_rather_than_reading_as_empty(monkeypatch):
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"facts": "not json at all"})], "tool_use"))
    with pytest.raises(RuntimeError) as exc:
        AnthropicClient(api_key="dummy").classify_structure("p", _Listy, "ctx")
    assert "facts" in str(exc.value)


def test_a_payload_wrapped_in_a_parameters_envelope_is_unwrapped(monkeypatch):
    """The live shape that blanked two real quotations.

    The model nests the whole structure under "parameters" — not every call,
    which is what made it look like a per-document extraction problem. Pydantic
    ignores the unknown key, so every declared field falls back to its default
    and a fully-populated extraction validates cleanly as an empty one that
    still reports `ok`.
    """
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"parameters": {"facts": _ENTRIES, "label": "x"}})],
        "tool_use"))
    out = AnthropicClient(api_key="dummy").classify_structure("p", _Listy, "ctx")
    assert out == {"facts": _ENTRIES, "label": "x"}


def test_an_envelope_is_unwrapped_whatever_the_wrapper_is_called(monkeypatch):
    # "parameters" is what was observed, but the rule is structural: a lone key
    # the schema does not declare, wrapping a dict, is never the answer itself
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"input": {"header_row": 2, "columns": {}}})],
        "tool_use"))
    out = AnthropicClient(api_key="dummy").classify_structure("p", _Layout, "ctx")
    assert out == {"header_row": 2, "columns": {}}


def test_a_lone_key_the_schema_declares_is_never_unwrapped(monkeypatch):
    # _Listy declares `facts`, so this is the field's own value, not an
    # envelope — unwrapping it would discard the extraction it holds
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"facts": _ENTRIES})], "tool_use"))
    out = AnthropicClient(api_key="dummy").classify_structure("p", _Listy, "ctx")
    assert out == {"facts": _ENTRIES}


def test_the_whole_structure_returned_as_a_string_on_a_scalar_field_is_recovered(monkeypatch):
    """Third live shape on the same two quotations, after the envelope fix.

    The entire JSON answer arrives as the *string value of one scalar field* —
    `{"base_price": "{\\"currency\\": \\"EUR\\", \\"base_price\\": 1110836, ...}"}`.
    Only list and dict fields were decoded from strings, so a float field held
    the whole structure and validation failed on it.

    The recovery stays narrow so it cannot eat a real value: the string must
    parse as a JSON *object* that names at least one schema field besides the
    one holding it. "50" on a value field decodes to a number, not an object,
    and is still passed through untouched.
    """
    payload = {"header_row": 7, "columns": {"a": 1}}
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"header_row": json.dumps(payload)})], "tool_use"))
    out = AnthropicClient(api_key="dummy").classify_structure("p", _Layout, "ctx")
    assert out == payload


def test_a_stringified_envelope_under_an_unknown_key_is_unwrapped(monkeypatch):
    """The shape that lost a whole vendor: an envelope that is also a string.

    Live, one quotation came back as `{"parameter": {...}}` — handled — and on
    another call as a *stringified* object under `parameter_name`. Neither
    repair could reach it: `unwrap_envelope` needs the inner value to be a
    dict, and `recover_stringified_envelope` only inspects keys the schema
    declares. So it fell through to `require_known_field`, raised, and the
    vendor's column went blank with a full extraction inside the string.

    Same structural test as the plain envelope, one step later: a lone key the
    schema does not declare, holding an object that names fields it does.
    """
    payload = {"facts": _ENTRIES, "label": "x"}
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"parameter_name": json.dumps(payload)})],
        "tool_use"))
    out = AnthropicClient(api_key="dummy").classify_structure("p", _Listy, "ctx")
    assert out == payload


def test_an_envelope_beside_a_sibling_key_is_unwrapped(monkeypatch):
    """The shape that lost ADPOWER's commercial terms on gas-14.

    `{"parameter_name": ..., "parameter_value": ...}` - TWO keys, neither of
    them a schema field. `unwrap_envelope` bailed on `len(data) != 1` before
    looking at any value, `recover_stringified_envelope` only inspects keys the
    schema declares, so the payload reached `require_known_field` and raised
    with a full extraction sitting inside one of the two values. The vendor's
    quotation column went blank: no base price, no currency, no delivery terms.

    The lone-key rule was never what made the unwrap safe - the inner object
    naming schema fields is. So the count stops mattering and the structural
    test stands on its own.
    """
    payload = {"facts": _ENTRIES, "label": "x"}
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"parameter_name": payload,
                                   "parameter_value": None})], "tool_use"))
    out = AnthropicClient(api_key="dummy").classify_structure("p", _Listy, "ctx")
    assert out == payload


def test_a_stringified_envelope_beside_a_sibling_key_is_unwrapped(monkeypatch):
    # Same shape one step stringified - the two live repairs compose, and the
    # string form is the one actually seen under `parameter_name`.
    payload = {"facts": _ENTRIES, "label": "x"}
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"parameter_name": json.dumps(payload),
                                   "parameter_value": "base_price"})], "tool_use"))
    out = AnthropicClient(api_key="dummy").classify_structure("p", _Listy, "ctx")
    assert out == payload


def test_a_bare_name_value_pair_still_raises(monkeypatch):
    """The other reading of the same two keys, and it must NOT be recovered.

    `{"parameter_name": "label", "parameter_value": "x"}` is the model
    answering with one parameter instead of the structure. Rebuilding it into
    `{"label": "x"}` would store one field and thirteen defaults as a
    successful extraction - "missing data is never coerced to a passing or zero
    value", the store invariant `require_known_field` exists to defend. No
    value here is an object naming schema fields, so nothing is recoverable and
    the loud failure is the correct outcome.
    """
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"parameter_name": "label",
                                   "parameter_value": "x"})], "tool_use"))
    with pytest.raises(RuntimeError) as exc:
        AnthropicClient(api_key="dummy").classify_structure("p", _Listy, "ctx")
    assert "no field" in str(exc.value).lower()


def test_two_candidate_envelopes_raise_rather_than_guess(monkeypatch):
    # Two values both name schema fields. Picking one would decide the vendor's
    # terms on key order; there is no evidence for either, so it raises.
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"a": {"facts": _ENTRIES},
                                   "b": {"label": "other"}})], "tool_use"))
    with pytest.raises(RuntimeError) as exc:
        AnthropicClient(api_key="dummy").classify_structure("p", _Listy, "ctx")
    assert "no field" in str(exc.value).lower()


def test_the_failure_names_what_the_payload_actually_held(monkeypatch):
    """The gas-14 note recorded only key NAMES, so the shape could not be
    diagnosed after the fact - the values needed to tell a recoverable envelope
    from a bare name/value pair were discarded by the error itself. A bounded
    preview is what makes the next occurrence answerable.
    """
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"parameter_name": "base_price",
                                   "parameter_value": 1110836})], "tool_use"))
    with pytest.raises(RuntimeError) as exc:
        AnthropicClient(api_key="dummy").classify_structure("p", _Listy, "ctx")
    message = str(exc.value)
    assert "base_price" in message and "1110836" in message


def test_the_failure_preview_is_bounded(monkeypatch):
    # The note goes into DocumentRecord.notes and is rendered in the portal; a
    # 200k-character payload must not be pasted into the store wholesale.
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"junk": "x" * 50_000})], "tool_use"))
    with pytest.raises(RuntimeError) as exc:
        AnthropicClient(api_key="dummy").classify_structure("p", _Listy, "ctx")
    assert len(str(exc.value)) < 2_000


def test_a_lone_unknown_key_holding_unrelated_json_still_raises(monkeypatch):
    """The unwrap must not turn any stray string into an answer.

    A string that parses but names nothing the schema declares is not the
    structure — it is the model answering a different question, and storing it
    would mean storing defaults as the vendor's terms.
    """
    _patch(monkeypatch, _Message(
        [_Block("tool_use",
                input={"parameter_name": json.dumps({"unit": "kW", "qty": 2})})],
        "tool_use"))
    with pytest.raises(RuntimeError) as exc:
        AnthropicClient(api_key="dummy").classify_structure("p", _Listy, "ctx")
    assert "no field" in str(exc.value).lower()


def test_a_payload_naming_no_field_of_the_schema_raises(monkeypatch):
    """Defence behind the unwrap: never let a blank validate as a real answer.

    Every field of a quotation carries a default, so an unrecognisable payload
    validates into an all-defaults record — currency "", base_price 0.0 — and
    is stored as a successful extraction. That is the store invariant in
    CLAUDE.md ("missing data is never coerced to a passing or zero value")
    breaking silently. Raising routes it to the extractor's failure path, which
    keeps the previously-good record and writes the reason into notes.
    """
    _patch(monkeypatch, _Message([_Block("tool_use", input={})], "tool_use"))
    with pytest.raises(RuntimeError) as exc:
        AnthropicClient(api_key="dummy").classify_structure("p", _Listy, "ctx")
    assert "no field" in str(exc.value).lower()


class _Priced(BaseModel):
    """The scalar/str mix of a real quotation schema, minus the noise."""
    base_price: float = 0.0
    vat_rate: float = 0.0
    currency: str = ""
    delivery_terms: str | None = None


def test_a_number_boxed_in_a_value_object_is_unboxed(monkeypatch):
    """Fourth live shape, on the same quotations as the envelope and the string.

    `base_price` arrives as `{"value": 1110836}` — the number is right there,
    boxed. Pydantic rejects the box on a float field, so a fully-read quotation
    failed whole and the vendor's column went blank. Unboxing reads the number
    the model actually returned; it invents nothing.
    """
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"base_price": {"value": 1110836}, "currency": "AED"})],
        "tool_use"))
    out = AnthropicClient(api_key="dummy").classify_structure("p", _Priced, "ctx")
    assert out == {"base_price": 1110836, "currency": "AED"}


def test_a_boxed_number_arriving_as_a_string_is_unboxed(monkeypatch):
    """The exact shape from the store: the box itself is stringified.

    `1 validation error for BidExtraction / base_price / Input should be a
    valid number, unable to parse string as a number
    [input_value='{"value": 1110836}', input_type=str]`.
    """
    _patch(monkeypatch, _Message(
        [_Block("tool_use",
                input={"base_price": json.dumps({"value": 1110836}), "vat_rate": 0.05})],
        "tool_use"))
    out = AnthropicClient(api_key="dummy").classify_structure("p", _Priced, "ctx")
    assert out == {"base_price": 1110836, "vat_rate": 0.05}


def test_a_box_carrying_more_than_the_value_is_left_alone(monkeypatch):
    """`{"value": 1110836, "currency": "AED"}` is not a box — it is an answer.

    Unboxing it would silently discard the currency and price the bid in the
    wrong money. Anything but a lone `value` key falls through to pydantic,
    which fails loudly, which is the correct outcome.
    """
    boxed = {"value": 1110836, "currency": "AED"}
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"base_price": boxed})], "tool_use"))
    out = AnthropicClient(api_key="dummy").classify_structure("p", _Priced, "ctx")
    assert out == {"base_price": boxed}


def test_a_text_field_is_never_unboxed(monkeypatch):
    """A vendor can print `{"value": ...}`; on a text field that is their text.

    Unboxing is limited to fields the schema cannot hold a string in, so a
    printed literal is never mistaken for a wrapper and rewritten.
    """
    literal = json.dumps({"value": "ex-works"})
    _patch(monkeypatch, _Message(
        [_Block("tool_use", input={"delivery_terms": literal})], "tool_use"))
    out = AnthropicClient(api_key="dummy").classify_structure("p", _Priced, "ctx")
    assert out == {"delivery_terms": literal}


@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY")
def test_live_classify_structure_returns_dict():
    client = AnthropicClient()
    out = client.classify_structure(
        prompt="Return header_row=1 and empty columns.",
        output_schema=_Layout,
        context_text="col A | col B",
    )
    assert isinstance(out, dict)
    assert "header_row" in out
