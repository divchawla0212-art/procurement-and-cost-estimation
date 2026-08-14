"""Asking a model which companies supply a kind of equipment.

**This is not a web search, and the screens must not call it one.**
`shared/llm/` wraps Anthropic, OpenAI, Gemini and Bedrock through one
`classify_structure` call. There is no crawler, no search index and no
provider web-search tool wired up, so what comes back is what the model recalls
from training — undated, unsourced, and capable of being confidently wrong in
the one way that matters here, by inventing a plausible company name. The
feature is therefore built and labelled as *suggested*, which is the same rule
`avl_import`, the mock rounds and `rfq_extractor` keep: a synthesised fact must
not be indistinguishable on screen from a recorded one.

**The client is passed in, never fetched.** That is what keeps the tests
key-free — they hand it a `MockLLMClient` and nothing here reaches `get_client`.

**Nothing here is stored.** `suggest` returns candidates; recording one is a
separate, attributed act by a person, one vendor at a time. Same shape as
`rfq_extractor`, and for the same reason: a suggestion the reader rejects
should leave nothing behind, and one they accept should be recorded as *their*
act rather than the model's.

**The model reads; code decides.** It is asked which companies supply this kind
of equipment and nothing else — never whether one is approved, prequalified or
preferable. Every judgement stays in Python, the rule the extractors already
keep, and `test_the_model_is_never_asked_for_a_verdict` asserts it against the
prompt file rather than against a sentence in this docstring.
"""
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel

from workflow.disciplines import fold

PROMPT_VERSION = "vendor_search_v1"
_PROMPT = (Path(__file__).parents[1] / "shared" / "llm" / "prompts"
           / f"{PROMPT_VERSION}.txt")


class SuggestedVendor(BaseModel):
    """One company the model named. Never a registry row, and never a bidder.

    There is deliberately no `vendor_id` — it is not in the registry, which is
    why it was suggested — no `approved_by`, and no prequalification. A
    model-named company claiming the client's approval is exactly the failure
    `test_no_invented_vendor_claims_the_clients_approval` guards for the mock
    rounds, arriving through a new door.

    `basis` is the model's own reason, carried through unedited so the reader
    can judge it. It is evidence to weigh, not a verdict to act on.
    """

    name: str = ""
    country: str | None = None
    supplies: str | None = None
    basis: str | None = None


class SuggestedVendors(BaseModel):
    """The response shape.

    **The `[]` default is load-bearing.** `CLAUDE.md` records `I5` — a model
    returning a response that omits an optional array, read as a failure rather
    than as no results. A model asked for vendors in an obscure discipline may
    legitimately name none, and that answer must arrive as an empty list rather
    than as an error.
    """

    vendors: list[SuggestedVendor] = []


def _context(discipline: str, description: str, exclude: Iterable[str]) -> str:
    """What the model is told about this item.

    In `context_text` rather than appended to the prompt, so the prompt stays
    exactly the versioned file. That is what lets the never-asked-for-a-verdict
    test read the prompt alone: a company legitimately trading as "Best Cables"
    arriving in `exclude` must not be able to fail it.
    """
    lines = [f"Trade discipline: {discipline}"]
    if description.strip():
        lines.append(f"Item description: {description.strip()}")
    already = [name.strip() for name in exclude if name.strip()]
    if already:
        lines.append("")
        lines.append("Already on the buyer's list — do not name these again:")
        lines.extend(f"- {name}" for name in already)
    return "\n".join(lines)


def suggest(
    client,
    *,
    discipline: str,
    description: str = "",
    exclude: Iterable[str] = (),
) -> list[SuggestedVendor]:
    """Companies the model believes supply this kind of equipment.

    Raises whatever the provider raises, and whatever the response fails
    validation with. Neither is swallowed into an empty list: an outage and
    "no such companies exist" must not look the same on screen, the same
    distinction the covering-RFQ summary keeps between `—` and `Nobody invited
    yet`. Only a well-formed response naming nobody returns `[]`.
    """
    raw = client.classify_structure(
        prompt=_PROMPT.read_text(encoding="utf-8"),
        output_schema=SuggestedVendors,
        context_text=_context(discipline, description, exclude),
    )
    parsed = SuggestedVendors.model_validate(raw)

    # Filtered here and not merely requested in the prompt: an instruction to
    # the model is a request, and only this is a guarantee. Folded through the
    # shared rule so a name differing by case or by a doubled space is the same
    # company — the exports themselves disagree about that whitespace.
    seen = {fold(name) for name in exclude if name.strip()}
    out: list[SuggestedVendor] = []
    for vendor in parsed.vendors:
        name = vendor.name.strip()
        if not name:
            # A vendor with no name cannot be added later, and a blank row is
            # one no action can be taken on — the rule the hand-add route keeps.
            continue
        key = fold(name)
        if key in seen:
            continue
        seen.add(key)
        out.append(vendor.model_copy(update={"name": name}))
    return out
