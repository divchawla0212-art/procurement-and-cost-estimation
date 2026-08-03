"""Datasheet -> technical FactRecords.

The model reports what a document states; it never converts units, totals
anything, or judges compliance. Comparison happens in phase 3, in Python.
"""
import logging
import os
from pathlib import Path
from pydantic import BaseModel

from procurement.chunking import chunk_on_lines
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
# corpus needs it.
TECH_CHUNK_CHARS = int(os.getenv("TECH_CHUNK_CHARS") or 12000)


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
        raw_items: list = []
        for chunk in chunk_on_lines(text, TECH_CHUNK_CHARS):
            raw = client.classify_structure(prompt, _TechFactList,
                                            chunk + vocabulary)
            # A tool call may legitimately omit an optional array. "The model
            # returned no facts" is an empty chunk, not a failed one; treating
            # it as a failure would make the document a permanent per-run charge.
            raw_items.extend(raw.get("facts") or [])
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
    seen: set[str] = set()
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
        # twice. fact_id_for keys on (doc_id, parameter) alone - nothing
        # ordinal - so both copies carry one id, and two records sharing an id
        # leave an id-addressed override ambiguous about which row it corrects.
        # First occurrence wins, so the merged order stays document order.
        fact_id = fact_id_for(doc_id, name)
        if fact_id in seen:
            continue
        seen.add(fact_id)

        out.append(FactRecord(
            fact_id=fact_id,
            parameter=name,
            value=fact.value,
            unit=fact.unit,
            verbatim=fact.verbatim,
            doc_id=doc_id,
        ))
    return out, "ok", None
