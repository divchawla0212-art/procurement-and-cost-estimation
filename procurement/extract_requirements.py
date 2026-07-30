"""MR / spec document -> RequirementRecords.

Two tiers, per spec section 4: `auto` requirements carry a machine-checkable
bound (parameter, operator, value, unit) and `judgement` requirements carry
clause text for a human. A clause that looks machine-checkable but is missing
any part of its bound is stored as `judgement` - never as an `auto` with a
null bound, which compliance.py would compare against and blame a vendor for.
"""
import logging
from pathlib import Path
from pydantic import BaseModel

from procurement.loaders import read_text
from procurement.store.models import RequirementRecord, req_id_for

_log = logging.getLogger(__name__)

REQUIREMENTS_PROMPT_VERSION = "requirements_v1"
_PROMPT = (Path(__file__).parents[1] / "shared" / "llm" / "prompts"
           / "requirements_v1.txt")

_OPERATORS = (">=", "<=", "==", "in")
_CATEGORIES = ("technical", "commercial", "documentation", "testing", "codes")


class _Requirement(BaseModel):
    clause_ref: str | None = None
    text: str = ""
    category: str = "technical"
    checkability: str = "judgement"
    parameter: str | None = None
    operator: str | None = None
    value: str | float | list | None = None
    unit: str | None = None


class _RequirementList(BaseModel):
    requirements: list[_Requirement] = []


def extract_requirements(doc_id: str, path: str, client, pdf_fallback=None
                         ) -> tuple[list[RequirementRecord], str, str | None]:
    """Return (records, status, notes). Never raises: one unreadable MR must
    not abort a run. `notes` carries the reason on failure, None on success -
    without it a permanently failing document is retried every run with no
    record of why."""
    try:
        text = read_text(path, llm_fallback=pdf_fallback)
        prompt = _PROMPT.read_text(encoding="utf-8")
        raw = client.classify_structure(prompt, _RequirementList, text)
        # An omitted optional array is an empty extraction, not a failed one.
        raw_items = list(raw.get("requirements") or [])
    except Exception as exc:
        _log.warning("requirement extraction failed for %s: %s", path, exc)
        return [], "failed", f"extraction error: {exc}"

    out: list[RequirementRecord] = []
    seen: set[str] = set()
    for position, raw_item in enumerate(raw_items, 1):
        try:
            item = _Requirement.model_validate(raw_item)
        except Exception:
            # One malformed clause must not discard the other ninety.
            continue
        body = item.text.strip()
        if not body:
            continue        # a clause with no text cannot be reviewed or checked

        clause = (item.clause_ref or "").strip() or f"#{position}"
        req_id = req_id_for(doc_id, clause)
        if req_id in seen:
            # Two clauses printed under one ref: keep both, distinguished by
            # position, rather than letting the second overwrite the first.
            clause = f"{clause}#{position}"
            req_id = req_id_for(doc_id, clause)
        seen.add(req_id)

        operator = (item.operator or "").strip()
        operator = operator if operator in _OPERATORS else None
        auto = (item.checkability == "auto"
                and bool((item.parameter or "").strip())
                and operator is not None
                and item.value is not None
                and item.unit is not None)

        out.append(RequirementRecord(
            req_id=req_id,
            clause_ref=clause,
            text=body,
            category=item.category if item.category in _CATEGORIES else "technical",
            checkability="auto" if auto else "judgement",
            parameter=(item.parameter or "").strip() or None if auto else None,
            operator=operator if auto else None,
            value=item.value if auto else None,
            unit=item.unit if auto else None,
            source_doc_id=doc_id,
        ))
    return out, "ok", None
