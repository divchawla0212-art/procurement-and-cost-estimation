"""Datasheet -> technical FactRecords.

The model reports what a document states; it never converts units, totals
anything, or judges compliance. Comparison happens in phase 3, in Python.
"""
import logging
from pathlib import Path
from pydantic import BaseModel

from procurement.chunking import ask_each_chunk, budget_from_env, chunk_on_lines
from procurement.loaders import read_text
from procurement.store.models import FactRecord, fact_id_for

_log = logging.getLogger(__name__)

TECH_PROMPT_VERSION = "tech_facts_v1"
_PROMPT = (Path(__file__).parents[1] / "shared" / "llm" / "prompts"
           / "tech_facts_v1.txt")

# Budget over INPUT chars, because the output ceiling is what a long document
# overruns: AESL's 220,933-char proposal and KERUI's 79,735-char commented
# datasheet both came back truncated at the shipped 8192-token ceiling, and
# check_truncated correctly refused both, leaving the vendors factless.
#
# Sized from the corpus rather than picked: the densest document over 10k chars
# in data/procurement-data/ is KERUI's "02 Detailed Vendor Standard Datasheet"
# at 15,067 chars for 71 facts - one fact per 212 input chars - and the 95th
# percentile stored fact serialises to ~236 chars, i.e. ~70 output tokens. So
# 12,000 input chars costs about 12000/212 * 70 = 3,960 output tokens at the
# corpus's worst measured density: under half the 8192 ceiling, leaving room
# for a document twice as dense as anything here. Tune with the env var if a
# corpus needs it - budget_from_env floors what the env var may set it to, so a
# typo cannot turn the budget into one paid call per line.
TECH_CHUNK_CHARS = budget_from_env("TECH_CHUNK_CHARS", 12000)


class _TechFact(BaseModel):
    parameter: str = ""
    value: str | float | None = None
    unit: str | None = None
    verbatim: str | None = None


class _TechFactList(BaseModel):
    facts: list[_TechFact] = []


def extract_tech_facts(doc_id: str, path: str, client, pdf_fallback=None,
                       parameters: list[str] | None = None,
                       text: str | None = None
                       ) -> tuple[list[FactRecord], str, str | None]:
    """Return (facts, status, notes). Status is "ok" or "failed"; a failure never
    raises, so one unreadable datasheet cannot abort a run. `notes` carries the
    reason on failure and is None on success — without it a permanently failing
    document is retried on every run with no record of why it fails.

    `text`, when given, is used as-is — the pipeline has already read the
    document once and passes it down so this does not read (and, for a scanned
    PDF, LLM-transcribe) it a second time."""
    try:
        if text is None:                       # the pipeline reads once and
            text = read_text(path, llm_fallback=pdf_fallback)   # passes it down
        prompt = _PROMPT.read_text(encoding="utf-8")
        # The vocabulary is an instruction, not document content, so it is
        # appended to every chunk rather than chunked alongside the text - a
        # chunk asked without it would be asked a different question.
        vocabulary = ""
        if parameters:
            vocabulary = ("\n\nThe buyer will check these parameters. Report them "
                          "when the document states them:\n"
                          + "\n".join(f"- {p}" for p in parameters))
        # ask_each_chunk retries a chunk once before letting it fail: the merge
        # below is all-or-nothing, so per-document failure probability scales
        # with chunk count, and one degenerate response on chunk 4 of 7 cost
        # KERUI's commented datasheet all 110 of its facts.
        raw_items = ask_each_chunk(client, prompt, _TechFactList,
                                   chunk_on_lines(text, TECH_CHUNK_CHARS),
                                   "facts", suffix=vocabulary)
    except Exception as exc:
        # All-or-nothing. The pipeline replaces this document's stored facts
        # with the fresh set whenever status is "ok", so storing 3 chunks of 5
        # as a success deletes the facts the missing chunks carried, and nothing
        # records why the vendor's count shrank. A response check_truncated
        # refuses is a chunk failure like any other, and this path keeps the
        # prior records and writes the reason to DocumentRecord.notes.
        _log.warning("technical extraction failed for %s: %s", path, exc)
        return [], "failed", f"extraction error: {exc}"

    out: list[FactRecord] = []
    seen: set[tuple] = set()
    for raw_item in raw_items:
        try:
            fact = _TechFact.model_validate(raw_item)
        except Exception:
            # One malformed field on one fact must not discard every other,
            # well-formed fact extracted from the same datasheet.
            continue
        name = fact.parameter.strip()
        if not name:
            continue          # a fact with no parameter name cannot be checked

        # A header row reprinted at the top of two chunks yields the same fact
        # twice. The whole fact is the key, not just its parameter: a datasheet
        # that genuinely states one parameter twice with different numbers -
        # ADPOWER prints continuous_rating at both 525 kW and 700 kW, and
        # ambient_design_temp four different ways - is stating two facts, and
        # dropping the second would hand compliance.py whichever the model
        # happened to print first. Only an exact repeat is an echo. First
        # occurrence wins, so the merged order stays document order.
        key = (name.lower(), fact.value, fact.unit)
        if key in seen:
            continue
        seen.add(key)

        out.append(FactRecord(
            # Not unique when one parameter is stated twice: fact_id_for keys
            # on (doc_id, parameter), so the two ADPOWER ratings above share an
            # id. That collision predates chunking and is not this extractor's
            # to fix - the id is the override/verdict address every stored
            # snapshot already uses, and widening the key (as deviation_id_for
            # does with its statement) would shift every id in every store.
            fact_id=fact_id_for(doc_id, name),
            parameter=name,
            value=fact.value,
            unit=fact.unit,
            verbatim=fact.verbatim,
            doc_id=doc_id,
        ))
    return out, "ok", None
