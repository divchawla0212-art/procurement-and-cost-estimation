"""Asking a model which companies supply a kind of equipment.

Key-free by construction: `suggest` takes the client as an argument rather than
fetching one, so every test here hands it a `MockLLMClient` and nothing reaches
`get_client` or a provider key.

The module stores nothing and has no store. Its guarantee is the negative one —
it returns candidates and writes nothing — so what is worth testing is the
shape of what comes back and, above all, **what is asked**. A prompt that
requested a verdict would be a judgement made where nobody reviews it.
"""
import pytest
from pydantic import ValidationError

from shared.llm.mock_client import MockLLMClient
from workflow.vendor_suggestions import SuggestedVendor, SuggestedVendors, suggest


def test_a_response_omitting_the_array_reads_as_no_vendors():
    """CLAUDE.md records this exact defect (I5): an omitted optional array read
    as a failure rather than as no results. A model asked about an obscure
    discipline may legitimately name none."""
    client = MockLLMClient(response={})

    assert suggest(client, discipline="Cables", description="11 kV", exclude=[]) == []


def test_names_already_on_the_item_are_filtered_in_python():
    """The prompt asks; only the filter guarantees. A model that ignores the
    instruction must not put a duplicate on screen."""
    client = MockLLMClient(response={"vendors": [
        {"name": "Al Munara Switchgear LLC"}, {"name": "New Co"}]})

    found = suggest(client, discipline="Cables", description="",
                    exclude=["AL MUNARA SWITCHGEAR LLC"])

    assert [v.name for v in found] == ["New Co"]


def test_an_excluded_name_matches_through_the_shared_fold():
    """`disciplines.fold` is the one definition of how a label is matched, and
    it collapses internal whitespace as well as case. A name that differs only
    by a double space is the same company, and re-suggesting it would put a
    duplicate row on screen."""
    client = MockLLMClient(response={"vendors": [{"name": "Ducab  HV   LLC"}]})

    assert suggest(client, discipline="Cables", description="",
                   exclude=["ducab hv llc"]) == []


def test_one_company_named_twice_lands_once():
    """A model listing the same company under two spellings would otherwise
    give a reader two rows to add, and adding both is two entries for one
    supplier."""
    client = MockLLMClient(response={"vendors": [
        {"name": "Ducab"}, {"name": "DUCAB"}, {"name": "Other Co"}]})

    found = suggest(client, discipline="Cables", description="", exclude=[])

    assert [v.name for v in found] == ["Ducab", "Other Co"]


def test_a_nameless_suggestion_is_dropped():
    """A vendor with no name cannot be added, and a blank row on screen is one
    no action can be taken on — the same rule the hand-add route keeps."""
    client = MockLLMClient(response={"vendors": [
        {"name": "   "}, {"supplies": "cable"}, {"name": "Real Co"}]})

    assert [v.name for v in suggest(
        client, discipline="Cables", description="", exclude=[])] == ["Real Co"]


def test_a_name_is_returned_trimmed_as_it_will_be_stored():
    """What the reader sees is what an add would store, so the trimming happens
    here rather than at the row that accepts it."""
    client = MockLLMClient(response={"vendors": [{"name": "  Ducab  "}]})

    found = suggest(client, discipline="Cables", description="", exclude=[])

    assert found[0].name == "Ducab"


def test_the_model_is_never_asked_for_a_verdict():
    """The rule the extractors keep: the model reads, code decides. A prompt
    that asked whether a vendor is suitable would be a judgement made where it
    cannot be reviewed.

    Asserted against the prompt alone, which is why the item's own details ride
    in `context_text`: a company legitimately called "Best Cables" appearing in
    `exclude` must not be able to fail this."""
    client = MockLLMClient(response={"vendors": []})
    suggest(client, discipline="Cables", description="", exclude=[])

    prompt = client.last_call["prompt"].lower()
    for word in ("approved", "eligible", "qualified", "recommend", "best", "rank"):
        assert word not in prompt, f"the prompt asks for a verdict: {word!r}"


def test_the_prompt_is_the_versioned_file_and_not_a_string_literal():
    """Every other prompt in this repository lives in `shared/llm/prompts/`, so
    a change to what is asked is a reviewable diff. This one is asked without a
    document, which is exactly the case where an inline literal is tempting."""
    from pathlib import Path

    from workflow.vendor_suggestions import PROMPT_VERSION

    path = Path("shared/llm/prompts") / f"{PROMPT_VERSION}.txt"
    client = MockLLMClient(response={"vendors": []})
    suggest(client, discipline="Cables", description="", exclude=[])

    assert client.last_call["prompt"] == path.read_text(encoding="utf-8")


def test_the_item_is_described_to_the_model():
    """A suggestion for "Cables" with the item's own description withheld is a
    worse answer for no reason. It rides in the context, not the prompt."""
    client = MockLLMClient(response={"vendors": []})

    suggest(client, discipline="Cables", description="11 kV, 3-core XLPE",
            exclude=["Ducab"])

    context = client.last_call["context_text"]
    assert "Cables" in context
    assert "11 kV, 3-core XLPE" in context
    assert "Ducab" in context, "the model is told what is already on the list"


def test_a_provider_failure_is_raised_rather_than_read_as_no_vendors():
    """An outage and "no such vendors exist" must not look the same — the
    distinction the covering-RFQ summary keeps between `—` and `Nobody invited
    yet`."""
    class Failing:
        def classify_structure(self, **_):
            raise RuntimeError("provider is down")

    with pytest.raises(RuntimeError):
        suggest(Failing(), discipline="Cables", description="", exclude=[])


def test_a_malformed_response_is_raised_rather_than_read_as_no_vendors():
    """Same rule one layer down. A response that is not the shape asked for is
    a failure, and swallowing it would report "no such vendors" for a bug."""
    client = MockLLMClient(response={"vendors": "Ducab, and some others"})

    with pytest.raises(ValidationError):
        suggest(client, discipline="Cables", description="", exclude=[])


def test_a_suggestion_carries_no_approval_and_no_registry_link():
    """A model-named company claiming ADNOC approval is the failure
    `test_no_invented_vendor_claims_the_clients_approval` guards, arriving
    through a new door."""
    fields = set(SuggestedVendor.model_fields)

    assert "approved_by" not in fields
    assert "vendor_id" not in fields
    assert "prequal_status" not in fields


def test_the_array_defaults_to_empty_on_the_schema_itself():
    """Load-bearing, and asserted on the model rather than only through a call:
    the default is what makes an omitted array read as no results."""
    assert SuggestedVendors().vendors == []
