"""Answering a bidder's question out of the enquiry package — BD-8's model half.

Key-free by construction: `draft_answer` takes the client as an argument rather
than fetching one, so every test here hands it a `MockLLMClient` and nothing
reaches `get_client` or a provider key. The index underneath is the real
`Bm25Index` over a `tmp_path`, which needs no key either.

Two rules carry this file.

**Code decides.** `supported` is computed in Python from the ids the model
cited against the ids that were retrieved. An empty citation list, or one id
that was never retrieved, is **not answered** however confident the prose — a
confidently wrong auto-answer sent to a bidder becomes a contractual position.

**An outage is not an absence.** A provider failure and "the passages do not
answer this" both escalate, but they must not arrive looking the same: the
first is something to retry, the second is something to answer by hand. The
distinction this repository keeps between `—` and `Nobody invited yet`.
"""
import re
from pathlib import Path

from shared.llm.mock_client import MockLLMClient
from workflow import mr_index, passages
from workflow.clarification_answers import (
    PROMPT_VERSION,
    AnswerOutcome,
    draft_answer,
)

#: Questions worded out of the passages' own vocabulary. There is no stemming
#: — "armouring" does not match "armoured" — which is a real property of the
#: index rather than a fixture quirk, and phrasing a question the index cannot
#: match would silently test the nothing-retrieved path in every case below.
ARMOUR_QUESTION = "how shall single core cables be armoured?"
INSULATION_QUESTION = "what insulation shall the conductor have?"

MR = [
    ("rdoc_a#1", "The conductor insulation shall be XLPE, rated ninety degrees "
                 "celsius continuous."),
    ("rdoc_a#2", "Single core cables shall be armoured with aluminium wire."),
    ("rdoc_a#3", "The outer sheath shall be black."),
]


def an_index(tmp_path) -> mr_index.Bm25Index:
    index = mr_index.Bm25Index(str(tmp_path))
    index.add("rfq_1", [
        passages.Passage(id=ident, rfq_id="rfq_1", text=text, ordinal=i)
        for i, (ident, text) in enumerate(MR, 1)
    ])
    return index


class Failing:
    """A provider that is down. Deliberately not a `MockLLMClient`, because the
    point is what happens when the call never returns a response at all."""

    def __init__(self) -> None:
        self.calls = 0

    def classify_structure(self, **_):
        self.calls += 1
        raise RuntimeError("provider is down")


# -- the verdict is computed, never asked for ----------------------------------


def test_an_answer_citing_a_retrieved_passage_is_supported(tmp_path):
    client = MockLLMClient(response={
        "answer": "Single core cables are armoured with aluminium wire.",
        "passage_ids": ["rdoc_a#2"],
    })

    result = draft_answer(client, an_index(tmp_path), rfq_id="rfq_1",
                          question=ARMOUR_QUESTION)

    assert result.supported is True
    assert result.outcome == AnswerOutcome.ANSWERED
    assert result.answer == "Single core cables are armoured with aluminium wire."
    assert [c.passage_id for c in result.citations] == ["rdoc_a#2"]
    assert result.escalation_reason is None


def test_an_uncited_answer_escalates_however_confident_the_prose(tmp_path):
    """`bool(cited)` is the first half of the formula, and this is why: a model
    that answers from its own knowledge of what cables usually are will sound
    exactly as certain as one reading the clause."""
    client = MockLLMClient(response={
        "answer": "Cables of this class are always armoured with galvanised steel.",
        "passage_ids": [],
    })

    result = draft_answer(client, an_index(tmp_path), rfq_id="rfq_1",
                          question=ARMOUR_QUESTION)

    assert result.supported is False
    assert result.outcome == AnswerOutcome.NOT_SUPPORTED
    assert result.answer is None, "an unsupported draft must not be sendable"
    assert result.citations == []
    assert result.escalation_reason


def test_a_fabricated_citation_escalates(tmp_path):
    """The second half of the formula. An id nobody retrieved is an id the model
    invented, and an invented citation reads on screen exactly like a real
    one."""
    client = MockLLMClient(response={
        "answer": "The cable shall be rated 11 kV.",
        "passage_ids": ["rdoc_a#9"],
    })

    result = draft_answer(client, an_index(tmp_path), rfq_id="rfq_1",
                          question=INSULATION_QUESTION)

    assert result.supported is False
    assert result.outcome == AnswerOutcome.NOT_SUPPORTED
    assert result.answer is None


def test_one_real_citation_does_not_rescue_a_fabricated_one(tmp_path):
    """Subset, not intersection. Half-grounded prose is prose whose other half
    came from somewhere nobody can check."""
    client = MockLLMClient(response={
        "answer": "XLPE, and rated 11 kV.",
        "passage_ids": ["rdoc_a#1", "rdoc_a#9"],
    })

    result = draft_answer(client, an_index(tmp_path), rfq_id="rfq_1",
                          question=INSULATION_QUESTION)

    assert result.supported is False
    assert result.outcome == AnswerOutcome.NOT_SUPPORTED, \
        "the model was reached and answered; this is not an empty retrieval"
    assert result.answer is None


def test_a_citation_of_a_passage_that_was_not_retrieved_this_time_escalates(tmp_path):
    """The set is what `search` returned for *this* question, not everything in
    the index. A model citing a passage it was never shown did not read it."""
    client = MockLLMClient(response={
        "answer": "The sheath shall be black.",
        "passage_ids": ["rdoc_a#3"],
    })

    index = an_index(tmp_path)
    result = draft_answer(client, index, rfq_id="rfq_1",
                          question="armoured aluminium wire", k=1)

    retrieved = index.search("rfq_1", "armoured aluminium wire", k=1)
    assert [h.passage_id for h in retrieved] == ["rdoc_a#2"], "the fixture moved"
    assert result.supported is False
    assert result.outcome == AnswerOutcome.NOT_SUPPORTED


def test_a_cited_answer_with_no_prose_escalates(tmp_path):
    """Stricter than the formula, deliberately: an empty answer with a citation
    is nothing to send, and returning it as `supported` would put a blank draft
    in front of a bidder."""
    client = MockLLMClient(response={"answer": "   ", "passage_ids": ["rdoc_a#2"]})

    result = draft_answer(client, an_index(tmp_path), rfq_id="rfq_1",
                          question=ARMOUR_QUESTION)

    assert result.supported is False
    assert result.outcome == AnswerOutcome.NOT_SUPPORTED
    assert result.answer is None


def test_the_model_is_asked_for_an_answer_and_ids_and_not_for_a_verdict(tmp_path):
    """The schema is the other half of the prompt guard: a field asking whether
    to escalate would be a verdict requested in code rather than in prose."""
    from workflow.clarification_answers import ModelAnswer

    assert set(ModelAnswer.model_fields) == {"answer", "passage_ids"}


# -- an outage is not an absence -----------------------------------------------


def test_a_provider_failure_escalates_and_says_so(tmp_path):
    provider = Failing()

    result = draft_answer(provider, an_index(tmp_path), rfq_id="rfq_1",
                          question=ARMOUR_QUESTION)

    assert provider.calls == 1
    assert result.supported is False
    assert result.outcome == AnswerOutcome.PROVIDER_FAILED
    assert result.provider_error and "provider is down" in result.provider_error
    assert result.escalation_reason


def test_a_provider_failure_is_distinguishable_from_an_unsupported_answer(tmp_path):
    """The load-bearing one. Both escalate; a caller must be able to tell "retry
    this" from "answer this by hand", and reading two identical results cannot.
    """
    index = an_index(tmp_path)
    question = ARMOUR_QUESTION

    outage = draft_answer(Failing(), index, rfq_id="rfq_1", question=question)
    unsupported = draft_answer(
        MockLLMClient(response={"answer": "Steel wire.", "passage_ids": []}),
        index, rfq_id="rfq_1", question=question)

    assert outage.supported is unsupported.supported is False
    assert outage.outcome != unsupported.outcome
    assert outage.escalation_reason != unsupported.escalation_reason
    assert outage.provider_error is not None
    assert unsupported.provider_error is None


def test_a_malformed_response_is_a_provider_failure_and_not_an_absence(tmp_path):
    """Same rule one layer down. A response that is not the shape asked for is a
    bug or an outage, and reporting it as "the package does not say" would send
    a buyer looking through an MR for an answer that is in it."""
    client = MockLLMClient(response={"passage_ids": "rdoc_a#2, and one more"})

    result = draft_answer(client, an_index(tmp_path), rfq_id="rfq_1",
                          question=ARMOUR_QUESTION)

    assert result.outcome == AnswerOutcome.PROVIDER_FAILED
    assert result.provider_error


def test_nothing_retrieved_is_its_own_outcome_and_the_model_is_never_asked(tmp_path):
    """A question no passage matches is not a question the model should be asked
    from an empty page — that is an invitation to answer from memory, and the
    call costs money to be told nothing."""
    client = MockLLMClient(response={"answer": "It shall be black.",
                                     "passage_ids": ["rdoc_a#3"]})

    result = draft_answer(client, an_index(tmp_path), rfq_id="rfq_1",
                          question="liquidated damages warranty period")

    assert client.calls == []
    assert result.outcome == AnswerOutcome.NOTHING_RETRIEVED
    assert result.supported is False
    assert result.answer is None
    assert result.escalation_reason


def test_an_rfq_with_no_indexed_package_is_nothing_retrieved(tmp_path):
    client = MockLLMClient(response={"answer": "x", "passage_ids": []})

    result = draft_answer(client, mr_index.Bm25Index(str(tmp_path)),
                          rfq_id="rfq_unindexed", question=ARMOUR_QUESTION)

    assert result.outcome == AnswerOutcome.NOTHING_RETRIEVED
    assert client.calls == []


# -- what is asked ---------------------------------------------------------------


VERDICT_WORDS = (
    "escalate", "escalation", "escalated", "confident", "confidence",
    "sufficient", "decide", "decision", "verdict", "judge", "judgement",
    "assess", "rank", "score", "recommend", "approve", "approved", "eligible",
    "qualified", "good enough",
)


def test_the_model_is_never_asked_for_a_verdict():
    """The rule the extractors keep: the model reads, code decides. A prompt
    that asked whether the answer was good enough to send would be a judgement
    made where nobody reviews it, and `supported` would become advisory.

    Asserted against the prompt file alone, which is why the question and the
    passages ride in `context_text`: a bidder who writes "please confirm this is
    sufficient" must not be able to fail it.

    Matched on word boundaries rather than as substrings — the vendor-suggestion
    guard next door uses plain `in`, and a list this long would otherwise trip
    over the "rate" inside "accurate" and read as a defect that is not there."""
    prompt = (Path("shared/llm/prompts") / f"{PROMPT_VERSION}.txt").read_text(
        encoding="utf-8").lower()

    for word in VERDICT_WORDS:
        assert not re.search(rf"\b{re.escape(word)}\b", prompt), \
            f"the prompt asks for a verdict: {word!r}"


def test_the_prompt_is_the_versioned_file_and_not_a_string_literal(tmp_path):
    """Every prompt in this repository lives in `shared/llm/prompts/`, so a
    change to what is asked is a reviewable diff rather than an edited literal."""
    client = MockLLMClient(response={"answer": "x", "passage_ids": ["rdoc_a#2"]})
    draft_answer(client, an_index(tmp_path), rfq_id="rfq_1",
                 question=ARMOUR_QUESTION)

    path = Path("shared/llm/prompts") / f"{PROMPT_VERSION}.txt"
    assert client.last_call["prompt"] == path.read_text(encoding="utf-8")


def test_the_question_and_the_retrieved_passages_ride_in_the_context(tmp_path):
    client = MockLLMClient(response={"answer": "x", "passage_ids": ["rdoc_a#2"]})

    draft_answer(client, an_index(tmp_path), rfq_id="rfq_1",
                 question=ARMOUR_QUESTION)

    context = client.last_call["context_text"]
    assert ARMOUR_QUESTION in context
    assert "aluminium wire" in context
    assert "rdoc_a#2" in context, "the model cannot cite an id it was never shown"


def test_a_question_using_a_forbidden_word_cannot_fail_the_verdict_guard(tmp_path):
    """The reason the question is context and not appended to the prompt. A
    bidder asking "is our proposed cable approved?" is an ordinary question."""
    client = MockLLMClient(response={"answer": "x", "passage_ids": ["rdoc_a#2"]})

    draft_answer(client, an_index(tmp_path), rfq_id="rfq_1",
                 question="are the cables armoured as approved and sufficient?")

    prompt = client.last_call["prompt"].lower()
    for word in VERDICT_WORDS:
        assert not re.search(rf"\b{re.escape(word)}\b", prompt)


def test_the_index_is_asked_for_the_k_the_caller_named(tmp_path):
    """`k` is a cut-off: a passage ranked below it is one the model never sees,
    so a caller narrowing it has to actually narrow it."""
    client = MockLLMClient(response={"answer": "x", "passage_ids": ["rdoc_a#2"]})

    draft_answer(client, an_index(tmp_path), rfq_id="rfq_1",
                 question="cables shall be armoured sheath insulation", k=2)

    assert client.last_call["context_text"].count("passage id:") == 2


# -- what comes back --------------------------------------------------------------


def test_the_citations_carry_the_text_the_answer_rests_on(tmp_path):
    """A citation a reader cannot read is a citation nobody checks."""
    client = MockLLMClient(response={"answer": "Aluminium wire.",
                                     "passage_ids": ["rdoc_a#2"]})

    result = draft_answer(client, an_index(tmp_path), rfq_id="rfq_1",
                          question=ARMOUR_QUESTION)

    assert result.citations[0].text == dict(MR)["rdoc_a#2"]


def test_citations_are_only_the_passages_the_model_used(tmp_path):
    """Not everything retrieved. Six passages listed under a two-line answer
    tell a reader nothing about where it came from."""
    client = MockLLMClient(response={"answer": "XLPE, aluminium wire.",
                                     "passage_ids": ["rdoc_a#1", "rdoc_a#2"]})

    result = draft_answer(client, an_index(tmp_path), rfq_id="rfq_1",
                          question="insulation and armoured cables sheath")

    assert [c.passage_id for c in result.citations] == ["rdoc_a#1", "rdoc_a#2"]


def test_a_result_carries_what_was_retrieved_so_an_escalation_can_be_reviewed(tmp_path):
    """An escalation with no trace of what was searched is one a human has to
    start from nothing."""
    client = MockLLMClient(response={"answer": "Steel.", "passage_ids": []})

    result = draft_answer(client, an_index(tmp_path), rfq_id="rfq_1",
                          question=ARMOUR_QUESTION)

    assert "rdoc_a#2" in result.retrieved_ids


def test_the_same_passage_cited_twice_is_one_citation(tmp_path):
    """A duplicate id is not a second source."""
    client = MockLLMClient(response={"answer": "Aluminium wire.",
                                     "passage_ids": ["rdoc_a#2", "rdoc_a#2"]})

    result = draft_answer(client, an_index(tmp_path), rfq_id="rfq_1",
                          question=ARMOUR_QUESTION)

    assert [c.passage_id for c in result.citations] == ["rdoc_a#2"]
    assert result.supported is True


def test_nothing_is_stored_by_drafting_an_answer(tmp_path):
    """Same shape as `/rfqs/extract` and the vendor suggestions: a draft the
    reader rejects leaves nothing behind, and one they send is recorded as their
    act. The index is read here and never written."""
    index = an_index(tmp_path)
    before = Path(mr_index.db_path(str(tmp_path))).read_bytes()

    draft_answer(MockLLMClient(response={"answer": "Aluminium wire.",
                                         "passage_ids": ["rdoc_a#2"]}),
                 index, rfq_id="rfq_1", question=ARMOUR_QUESTION)

    assert Path(mr_index.db_path(str(tmp_path))).read_bytes() == before
