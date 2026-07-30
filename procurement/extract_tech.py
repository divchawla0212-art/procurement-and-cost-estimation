"""Datasheet -> technical FactRecords.

The model reports what a document states; it never converts units, totals
anything, or judges compliance. Comparison happens in phase 3, in Python.
"""
from pathlib import Path
from pydantic import BaseModel

from procurement.loaders import read_text
from procurement.store.models import FactRecord, fact_id_for

TECH_PROMPT_VERSION = "tech_facts_v1"
_PROMPT = (Path(__file__).parents[1] / "shared" / "llm" / "prompts"
           / "tech_facts_v1.txt")


class _TechFact(BaseModel):
    parameter: str = ""
    value: str | float | None = None
    unit: str | None = None
    verbatim: str | None = None


class _TechFactList(BaseModel):
    facts: list[_TechFact] = []


def extract_tech_facts(doc_id: str, path: str, client, pdf_fallback=None,
                       parameters: list[str] | None = None
                       ) -> tuple[list[FactRecord], str]:
    """Return (facts, status). Status is "ok" or "failed"; a failure never
    raises, so one unreadable datasheet cannot abort a run."""
    try:
        text = read_text(path, llm_fallback=pdf_fallback)
        prompt = _PROMPT.read_text(encoding="utf-8")
        if parameters:
            text += ("\n\nThe buyer will check these parameters. Report them "
                      "when the document states them:\n"
                      + "\n".join(f"- {p}" for p in parameters))
        raw = client.classify_structure(prompt, _TechFactList, text)
        raw_items = list(raw["facts"])
    except Exception:
        return [], "failed"

    out: list[FactRecord] = []
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
        out.append(FactRecord(
            fact_id=fact_id_for(doc_id, name),
            parameter=name,
            value=fact.value,
            unit=fact.unit,
            verbatim=fact.verbatim,
            doc_id=doc_id,
        ))
    return out, "ok"
