"""`LLM_TEMPERATURE`: opt-in determinism for the parser A/B.

The A/B compares three PDF readers by how many `needs_human` compliance cells
each produces. Extraction is a model call, so the same text can yield different
facts run to run, and a few flags of run-to-run drift would be
indistinguishable from a real parser difference. Pinning temperature shrinks
that drift.

Opt-in rather than a new default: every existing caller keeps today's exact
behaviour, so an experiment does not get to change what production sends by
being merged.
"""
import pytest
from pydantic import BaseModel

from shared.llm.anthropic_client import AnthropicClient, sampling_temperature


class _Layout(BaseModel):
    header_row: int
    columns: dict[str, int]


class _Block:
    def __init__(self, type, input=None):
        self.type, self.input = type, input or {}


class _Message:
    def __init__(self, blocks, stop_reason):
        self.content, self.stop_reason = blocks, stop_reason


class _FakeAnthropic:
    last_kwargs: dict = {}

    def __init__(self, message):
        self._message = message
        self.messages = self

    def create(self, **kwargs):
        type(self).last_kwargs = kwargs
        return self._message


def _patch(monkeypatch):
    import shared.llm.anthropic_client as mod
    fake = _FakeAnthropic(_Message(
        [_Block("tool_use", input={"header_row": 1, "columns": {}})], "tool_use"))
    monkeypatch.setattr(mod.anthropic, "Anthropic", lambda **kw: fake)
    return fake


def test_unset_means_no_temperature_is_sent(monkeypatch):
    """Today's behaviour, exactly. The request must not gain a field."""
    monkeypatch.delenv("LLM_TEMPERATURE", raising=False)
    fake = _patch(monkeypatch)
    AnthropicClient(api_key="dummy").classify_structure("p", _Layout, "ctx")
    assert "temperature" not in fake.last_kwargs


def test_setting_it_sends_it(monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "0")
    fake = _patch(monkeypatch)
    AnthropicClient(api_key="dummy").classify_structure("p", _Layout, "ctx")
    assert fake.last_kwargs["temperature"] == 0.0


@pytest.mark.parametrize("raw", ["", "   ", "hot", "None"])
def test_an_unparseable_value_is_ignored_rather_than_raising(monkeypatch, raw):
    """Mirrors `max_output_tokens`: a bad env var degrades to the default
    instead of aborting a run that is otherwise fine."""
    monkeypatch.setenv("LLM_TEMPERATURE", raw)
    assert sampling_temperature() is None


def test_zero_survives_the_falsy_trap(monkeypatch):
    """0.0 is the whole point and is falsy, so `or` chaining would discard it
    and silently give the experiment an unpinned temperature."""
    monkeypatch.setenv("LLM_TEMPERATURE", "0.0")
    assert sampling_temperature() == 0.0
