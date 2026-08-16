"""Drafting an answer to a bidder's question out of the enquiry package — BD-8.

**The client is passed in, never fetched.** That is what keeps the tests
key-free — they hand `draft_answer` a `MockLLMClient` and nothing here reaches
`get_client`. Same rule as `workflow/vendor_suggestions.py`, which is the
closest thing to this module already in the repository.

**Nothing here is stored.** `draft_answer` reads the index and returns; sending
an answer to a bidder is a separate, attributed act by a person. Same shape as
`rfq_extractor` and the vendor suggestions, and for the same reason: a draft the
reader rejects should leave nothing behind, and one they send should be recorded
as *their* act rather than the model's.

**The model reads; code decides.** The model is asked for an answer and for the
ids of the passages it used. It is never asked whether the answer is good enough
to send — that verdict is computed here:

    supported = bool(cited) and set(cited) <= {h.passage_id for h in hits}

An empty citation list, or one id that was never retrieved, is **not answered**
however confident the prose sounds. This is the one rule in BD-8 that must not
be softened: a confidently wrong auto-answer sent to a bidder becomes a
contractual position. `test_the_model_is_never_asked_for_a_verdict` asserts the
other half of it against the prompt file, rather than against a sentence in this
docstring.

**An outage is not an absence, and neither is a wrong answer.** Five outcomes,
and they are five rather than two because "escalate" is the same destination for
very different reasons: a provider failure is something to retry; an unsupported
answer is a model that went off the passages; a package that does not cover the
question is an ordinary finding about the enquiry; and nothing retrieved at all
means the question shares no vocabulary with the package, which is as often a
badly worded question as a gap in the MR. Collapsing them would send a buyer
hunting through an MR for an answer that is in it, hide an outage as a shrug, or
report an honest "the package is silent on this" as ungrounded prose and send
somebody looking for a model bug that did not happen. The distinction this
repository keeps between `—` and `Nobody invited yet`, and between an empty
approver list refused and no approver named.
"""
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from workflow.mr_index import Hit, PassageIndex

PROMPT_VERSION = "mr_answer_v1"
_PROMPT = (Path(__file__).parents[1] / "shared" / "llm" / "prompts"
           / f"{PROMPT_VERSION}.txt")

#: How many passages the model is shown. Enough that a question answered in two
#: places still gets both, few enough that the passages stay the short list a
#: reader can check a citation against by eye.
DEFAULT_K = 6


class AnswerOutcome(str, Enum):
    """Why this answer is, or is not, sendable.

    Every member but `ANSWERED` escalates to the contractor. They are kept apart
    because the *next action* differs for each — see the module docstring.
    """

    ANSWERED = "Answered from the enquiry package"
    NOT_SUPPORTED = "The model's answer was not supported by the passages"
    NOT_IN_PACKAGE = "The retrieved passages do not answer the question"
    NOTHING_RETRIEVED = "No passage of the enquiry package matched the question"
    PROVIDER_FAILED = "The model could not be reached"


class ModelAnswer(BaseModel):
    """What the model is asked for, and the whole of it.

    Two fields, and deliberately no third: a `confidence`, a `should_escalate`
    or a `sufficient` here would be a verdict requested in code rather than in
    prose, and the prompt guard reads the prompt file only.

    Both defaults are load-bearing in the way `SuggestedVendors.vendors` is
    (`CLAUDE.md`'s `I5`): a model that reads the passages and finds nothing is
    asked to return an empty answer and an empty list, and that must arrive as
    an ordinary "not supported" rather than as a validation failure reported as
    an outage.
    """

    answer: str = ""
    passage_ids: list[str] = []


class Citation(BaseModel):
    """One passage the answer rests on, with its text.

    The text rides along because a citation a reader cannot read is a citation
    nobody checks — and checking it is the entire safeguard.
    """

    passage_id: str
    text: str


class GroundedAnswer(BaseModel):
    """The result, in one shape for all four outcomes.

    `answer` is `None` unless `supported`. The unsupported prose is deliberately
    **not** carried anywhere on this model: a field holding a fluent, plausible,
    ungrounded answer beside an escalation is a field somebody eventually
    pastes into a reply to a bidder.

    `escalation_reason` is `None` exactly when `supported` — the shape
    `EligibilityVerdict.reason` already uses. `provider_error` is set only for
    `PROVIDER_FAILED`, and it is what makes an outage tellable from an absence
    without reading prose.

    `retrieved_ids` rides along so an escalation can be reviewed: a human handed
    "we could not answer this" with no record of what was searched starts from
    nothing.
    """

    outcome: AnswerOutcome
    supported: bool
    answer: str | None = None
    citations: list[Citation] = []
    escalation_reason: str | None = None
    provider_error: str | None = None
    retrieved_ids: list[str] = []


def _context(question: str, hits: list[Hit]) -> str:
    """What the model is shown.

    The question and the passages ride here rather than being appended to the
    prompt, so the prompt stays exactly the versioned file. That is what lets
    the never-asked-for-a-verdict test read the prompt alone: a bidder asking
    "is our proposed cable approved?" must not be able to fail it.
    """
    lines = ["Question:", question.strip(), "", "Passages:"]
    for hit in hits:
        lines.extend(["", f"passage id: {hit.passage_id}", hit.text])
    return "\n".join(lines)


def _escalate(
    outcome: AnswerOutcome,
    reason: str,
    hits: list[Hit],
    provider_error: str | None = None,
) -> GroundedAnswer:
    return GroundedAnswer(
        outcome=outcome,
        supported=False,
        answer=None,
        citations=[],
        escalation_reason=reason,
        provider_error=provider_error,
        retrieved_ids=[h.passage_id for h in hits],
    )


def draft_answer(
    client,
    index: PassageIndex,
    *,
    rfq_id: str,
    question: str,
    k: int = DEFAULT_K,
) -> GroundedAnswer:
    """Answer `question` out of `rfq_id`'s indexed package, or say why not.

    The model is called at most once, and not at all when nothing was retrieved
    — asking it to answer from an empty page is an invitation to answer from
    memory, and the call costs money to be told nothing.

    The provider call and the response validation are the one place an exception
    is caught here, and it is caught rather than raised because every outcome of
    this function routes to the same place — a person — and only the *reason*
    differs. A raise would make every caller re-derive the escalation, and the
    first one to forget would report an outage as "the package does not say".
    The exception's type and message are carried through in `provider_error`
    rather than logged and dropped, so what failed is answerable without the
    server's log.
    """
    hits = index.search(rfq_id, question, k)
    if not hits:
        return _escalate(
            AnswerOutcome.NOTHING_RETRIEVED,
            "No passage of the enquiry package matched this question, so there "
            "is nothing to answer it from. It needs answering by hand.",
            hits,
        )

    try:
        raw = client.classify_structure(
            prompt=_PROMPT.read_text(encoding="utf-8"),
            output_schema=ModelAnswer,
            context_text=_context(question, hits),
        )
        parsed = ModelAnswer.model_validate(raw)
    except Exception as exc:
        return _escalate(
            AnswerOutcome.PROVIDER_FAILED,
            "The question could not be answered because the model could not be "
            "reached. This is not a finding about the enquiry package, and it "
            "may succeed on a retry.",
            hits,
            provider_error=f"{type(exc).__name__}: {exc}",
        )

    retrieved = {hit.passage_id: hit for hit in hits}
    # Deduplicated in order, because a duplicate id is not a second source.
    cited: list[str] = []
    for passage_id in parsed.passage_ids:
        if passage_id not in cited:
            cited.append(passage_id)

    # The formula, and the whole point of the module. Subset, not intersection:
    # half-grounded prose is prose whose other half came from somewhere nobody
    # can check.
    supported = bool(cited) and set(cited) <= set(retrieved)
    answer = parsed.answer.strip()

    if not cited and not answer:
        # The model read the passages and said they do not answer the question,
        # which is exactly what the prompt asks it to do when they do not. That
        # is an ordinary finding about the *package*, not a failure of the
        # model, and it is the commonest legitimate escalation there is —
        # reporting it as ungrounded prose sends a buyer looking for a model bug
        # that did not happen. `not cited` already makes `supported` false, so
        # this branch relaxes nothing; it only stops naming the wrong cause.
        return _escalate(
            AnswerOutcome.NOT_IN_PACKAGE,
            "The passages retrieved from the enquiry package do not appear to "
            "answer this question, and nothing was drafted from them. It needs "
            "answering by hand.",
            hits,
        )
    if not supported:
        return _escalate(
            AnswerOutcome.NOT_SUPPORTED,
            "The drafted answer did not rest on the passages retrieved from the "
            "enquiry package, so it has not been sent. It needs answering by "
            "hand.",
            hits,
        )
    if not answer:
        # Stricter than the formula, deliberately: an empty answer with a
        # citation is nothing to send, and returning it as supported would put a
        # blank draft in front of a bidder.
        return _escalate(
            AnswerOutcome.NOT_SUPPORTED,
            "The passages were cited but no answer was drafted from them, so "
            "there is nothing to send. It needs answering by hand.",
            hits,
        )

    return GroundedAnswer(
        outcome=AnswerOutcome.ANSWERED,
        supported=True,
        answer=answer,
        citations=[
            Citation(passage_id=passage_id, text=retrieved[passage_id].text)
            for passage_id in cited
        ],
        escalation_reason=None,
        provider_error=None,
        retrieved_ids=[h.passage_id for h in hits],
    )
