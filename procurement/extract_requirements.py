"""MR / spec document -> RequirementRecords.

Three tiers, per spec section 4: `auto` requirements carry a machine-checkable
bound (parameter, operator, value, unit); `stated` requirements carry a
parameter and an optional non-numeric value, with operator and unit always
null; `judgement` requirements carry clause text for a human. A clause that
looks machine-checkable but is missing any part of its bound is stored as
`judgement` - never as an `auto` with a null bound, which compliance.py would
compare against and blame a vendor for.
"""
import logging
import os
import re
from pathlib import Path
from pydantic import BaseModel

from procurement.loaders import read_text
from procurement.store.models import RequirementRecord, req_id_for

_log = logging.getLogger(__name__)

REQUIREMENTS_PROMPT_VERSION = "requirements_v4"
_PROMPT = (Path(__file__).parents[1] / "shared" / "llm" / "prompts"
           / "requirements_v4.txt")

# The output JSON echoes every clause verbatim, so it is larger than the input.
# The live 25,176-char MR overflowed the 8192-token ceiling in one call and
# came back empty. Budget is over INPUT chars; tune with the env var if a
# corpus needs it.
REQUIREMENTS_CHUNK_CHARS = int(os.getenv("REQUIREMENTS_CHUNK_CHARS") or 8000)

_NOISE = re.compile(r"[^a-z0-9]+")

# `in` is a set of permitted values ("50 or 60 Hz"); `between` is a stated
# range ("5-58 deg C"). They are decided by the model and never inferred from
# the value's shape: [50, 60] read as a range would accept 55 Hz, and [5, 58]
# read as a set fails a vendor offering 55 degC for meeting the requirement.
_OPERATORS = (">=", "<=", "==", "in", "between")
_CATEGORIES = ("technical", "commercial", "documentation", "testing", "codes")
_CHECKABILITY = ("auto", "stated", "judgement")


def _is_range(value) -> bool:
    """Exactly two numeric bounds. Anything else is not a usable range."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return False
    for bound in value:
        if isinstance(bound, bool):
            return False
        if isinstance(bound, (int, float)):
            continue
        try:
            float(str(bound).strip().replace(",", ""))
        except (TypeError, ValueError):
            return False
    return True


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


def _chunks(text: str, budget: int) -> list[str]:
    """Split on line boundaries, in order, losslessly.

    Never mid-line: read_xlsx_text emits one spreadsheet row per line, and a
    value torn from its unit invites the model to pair the wrong number with
    the wrong unit. No overlap: overlap duplicates clauses.
    """
    out: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines():
        cost = len(line) + 1
        if current and size + cost > budget:
            out.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += cost
    if current:
        out.append("\n".join(current))
    return out or [""]


def extract_requirements(doc_id: str, path: str, client, pdf_fallback=None,
                         text: str | None = None
                         ) -> tuple[list[RequirementRecord], str, str | None]:
    """Return (records, status, notes). Never raises: one unreadable MR must
    not abort a run. `notes` carries the reason on failure, None on success -
    without it a permanently failing document is retried every run with no
    record of why.

    `text`, when given, is used as-is — the pipeline has already read the
    document once and passes it down so this does not read it a second time."""
    try:
        if text is None:
            text = read_text(path, llm_fallback=pdf_fallback)
        prompt = _PROMPT.read_text(encoding="utf-8")
        raw_items: list = []
        for chunk in _chunks(text, REQUIREMENTS_CHUNK_CHARS):
            raw = client.classify_structure(prompt, _RequirementList, chunk)
            # An omitted optional array is an empty chunk, not a failed one.
            raw_items.extend(raw.get("requirements") or [])
    except Exception as exc:
        # All-or-nothing. A partial merge is stored as a complete success, and
        # the pipeline then replaces this document's requirements with it -
        # deleting the clauses the failed chunks carried, with nothing recording
        # why the matrix shrank.
        _log.warning("requirement extraction failed for %s: %s", path, exc)
        return [], "failed", f"extraction error: {exc}"

    out: list[RequirementRecord] = []
    seen: set[str] = set()
    seen_bodies: set[tuple[str, str]] = set()
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

        # A header row repeated at the top of two chunks yields the same clause
        # twice. Dropping the exact repeat is right; the position-suffix branch
        # below stays, because two genuinely different clauses printed under one
        # ref must both survive.
        body_key = (clause, _NOISE.sub("", body.lower()))
        if body_key in seen_bodies:
            continue
        seen_bodies.add(body_key)

        req_id = req_id_for(doc_id, clause)
        if req_id in seen:
            # Two clauses printed under one ref: keep both, distinguished by
            # position, rather than letting the second overwrite the first.
            clause = f"{clause}#{position}"
            req_id = req_id_for(doc_id, clause)
        seen.add(req_id)

        operator = (item.operator or "").strip()
        operator = operator if operator in _OPERATORS else None
        if operator == "between" and not _is_range(item.value):
            # a range missing an end is a half-stated bound, and INV-2 says
            # those are judgement - compliance.py must never receive one
            operator = None
        auto = (item.checkability == "auto"
                and bool((item.parameter or "").strip())
                and operator is not None
                and item.value is not None
                and item.unit is not None)

        # A stated clause needs something to match a fact against, so the
        # parameter is mandatory; the value is not, because "Anchor Bolt
        # Required" genuinely states none. A list value is the `in` operator's
        # shape - an auto bound - and never a stated one.
        stated = (not auto
                  and item.checkability == "stated"
                  and bool((item.parameter or "").strip())
                  and not isinstance(item.value, (list, tuple, dict)))

        if auto:
            tier, parameter, value, unit = ("auto", (item.parameter or "").strip(),
                                            item.value, item.unit)
        elif stated:
            # operator and unit stay None: a stated row that acquired either
            # would be an auto row with a half-stated bound (INV-2, phase 3).
            tier, parameter, value, unit = ("stated", (item.parameter or "").strip(),
                                            item.value, None)
            operator = None
        else:
            tier, parameter, value, unit, operator = ("judgement", None, None, None, None)

        out.append(RequirementRecord(
            req_id=req_id, clause_ref=clause, text=body,
            category=item.category if item.category in _CATEGORIES else "technical",
            checkability=tier, parameter=parameter, operator=operator,
            value=value, unit=unit, source_doc_id=doc_id))
    return out, "ok", None
