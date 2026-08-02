"""Vendor deviation form -> DeviationRecords.

`disposition` degrades to "noted" on anything unrecognised — never to
"comply". Silently upgrading an unreadable entry to compliance is the one
failure mode here that could corrupt an award decision.
"""
import logging
from pathlib import Path
from pydantic import BaseModel

from procurement.loaders import read_text
from procurement.store.models import DeviationRecord, deviation_id_for

_log = logging.getLogger(__name__)

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


def extract_deviations(doc_id: str, path: str, client, pdf_fallback=None,
                       text: str | None = None
                       ) -> tuple[list[DeviationRecord], str, str | None]:
    """Return (deviations, status, notes). Status is "ok" or "failed"; a failure
    never raises. `notes` carries the reason on failure and is None on success —
    without it a permanently failing document is retried on every run with no
    record of why it fails.

    `text`, when given, is used as-is — the pipeline has already read the
    document once and passes it down so this does not read it a second time."""
    try:
        if text is None:
            text = read_text(path, llm_fallback=pdf_fallback)
        prompt = _PROMPT.read_text(encoding="utf-8")
        raw = client.classify_structure(prompt, _DeviationList, text)
        # Same reasoning as extract_tech_facts: an omitted optional array is an
        # empty extraction, not a failed one.
        raw_items = list(raw.get("deviations") or [])
    except Exception as exc:
        _log.warning("deviation extraction failed for %s: %s", path, exc)
        return [], "failed", f"extraction error: {exc}"

    out: list[DeviationRecord] = []
    for raw_item in raw_items:
        try:
            item = _Deviation.model_validate(raw_item)
        except Exception:
            # One malformed row (e.g. a null or numeric field the model
            # emitted) must not discard every other, well-formed row in the
            # same document.
            continue
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
    return out, "ok", None
