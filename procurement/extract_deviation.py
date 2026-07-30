"""Vendor deviation form -> DeviationRecords.

`disposition` degrades to "noted" on anything unrecognised — never to
"comply". Silently upgrading an unreadable entry to compliance is the one
failure mode here that could corrupt an award decision.
"""
from pathlib import Path
from pydantic import BaseModel

from procurement.loaders import read_text
from procurement.store.models import DeviationRecord, deviation_id_for

DEVIATION_PROMPT_VERSION = "deviation_v1"
_PROMPT = (Path(__file__).parents[1] / "shared" / "llm" / "prompts"
           / "deviation_v1.txt")
_DISPOSITIONS = ("comply", "deviate", "noted")


class _Deviation(BaseModel):
    clause_ref: str | None = None
    statement: str = ""
    disposition: str = "noted"


class _DeviationList(BaseModel):
    deviations: list[_Deviation] = []


def extract_deviations(doc_id: str, path: str, client, pdf_fallback=None
                       ) -> tuple[list[DeviationRecord], str]:
    """Return (deviations, status). Status is "ok" or "failed"; a failure
    never raises."""
    try:
        text = read_text(path, llm_fallback=pdf_fallback)
        prompt = _PROMPT.read_text(encoding="utf-8")
        parsed = _DeviationList.model_validate(
            client.classify_structure(prompt, _DeviationList, text))
    except Exception:
        return [], "failed"

    out: list[DeviationRecord] = []
    for item in parsed.deviations:
        statement = item.statement.strip()
        if not statement:
            continue
        disposition = (item.disposition or "").strip().lower()
        out.append(DeviationRecord(
            deviation_id=deviation_id_for(doc_id, item.clause_ref, statement),
            clause_ref=item.clause_ref,
            statement=statement,
            disposition=disposition if disposition in _DISPOSITIONS else "noted",
            doc_id=doc_id,
        ))
    return out, "ok"
