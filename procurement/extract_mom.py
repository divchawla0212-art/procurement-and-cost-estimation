"""Minutes of Meeting -> requirement amendments.

The real MR is explicitly superseded by a MOM, so ignoring meetings would make
the requirement baseline simply wrong. Amendments are stored as their own
records and applied on top of base requirements at write time, with lineage
preserved both ways: the requirement names the amendment in `amended_by`, the
amendment names the requirement in `req_id`, and the pre-amendment body stays
in `base_body`.
"""
import logging
import re
from pathlib import Path
from pydantic import BaseModel

from procurement.loaders import read_text
from procurement.store.models import (Amendment, RequirementRecord,
                                      amendment_id_for)

_log = logging.getLogger(__name__)

MOM_PROMPT_VERSION = "mom_amend_v1"
_PROMPT = (Path(__file__).parents[1] / "shared" / "llm" / "prompts"
           / "mom_amend_v1.txt")

_ACTIONS = ("modify", "withdraw")
_OPERATORS = (">=", "<=", "==", "in")
# The pre-amendment fields captured in `base_body`, so a requirement can be
# rebuilt exactly once its amendment is withdrawn.
_BODY_FIELDS = ("text", "category", "checkability", "parameter", "operator",
                "value", "unit")
_CLAUSE_NOISE = re.compile(r"[^a-z0-9]+")


class _Amendment(BaseModel):
    clause_ref: str | None = None
    text: str = ""
    action: str = "modify"
    parameter: str | None = None
    operator: str | None = None
    value: str | float | list | None = None
    unit: str | None = None


class _AmendmentList(BaseModel):
    amendments: list[_Amendment] = []


def _norm_clause(ref: str | None) -> str:
    """Match "4.2.7", "Clause 4.2.7" and " 4.2.7 " as one clause."""
    return _CLAUSE_NOISE.sub("", (ref or "").lower().replace("clause", ""))


def extract_amendments(doc_id: str, path: str, client, pdf_fallback=None
                       ) -> tuple[list[Amendment], str, str | None]:
    """Return (amendments, status, notes). Never raises: one unreadable MOM
    must not abort a run. `notes` carries the reason on failure, None on
    success - without it a permanently failing document is retried every run
    with no record of why."""
    try:
        text = read_text(path, llm_fallback=pdf_fallback)
        prompt = _PROMPT.read_text(encoding="utf-8")
        raw = client.classify_structure(prompt, _AmendmentList, text)
        # An omitted optional array is an empty extraction, not a failed one.
        raw_items = list(raw.get("amendments") or [])
    except Exception as exc:
        _log.warning("amendment extraction failed for %s: %s", path, exc)
        return [], "failed", f"extraction error: {exc}"

    out: list[Amendment] = []
    for raw_item in raw_items:
        try:
            item = _Amendment.model_validate(raw_item)
        except Exception:
            # One malformed row must not discard every other, well-formed
            # row in the same meeting.
            continue
        body = item.text.strip()
        if not body:
            continue        # an amendment with no text cannot change anything

        operator = (item.operator or "").strip()
        out.append(Amendment(
            amendment_id=amendment_id_for(doc_id, item.clause_ref, body),
            clause_ref=(item.clause_ref or "").strip() or None,
            req_id=None,                 # resolved later, by apply_amendments
            text=body,
            parameter=(item.parameter or "").strip() or None,
            operator=operator if operator in _OPERATORS else None,
            value=item.value,
            unit=item.unit,
            # Degrade to "modify", never to "withdraw": silently deleting a
            # requirement because a word was unrecognised is the one failure
            # mode here that could corrupt an award decision.
            action=item.action if item.action in _ACTIONS else "modify",
            source_doc_id=doc_id,
        ))
    return out, "ok", None


def apply_amendments(requirements: list[RequirementRecord],
                     amendments: list[Amendment]
                     ) -> tuple[list[RequirementRecord], list[Amendment]]:
    """Resolve effective requirement values from base + live amendments.

    Pure: neither argument is mutated, both are copied before use.

    Idempotent by construction: every requirement is first rebuilt from
    `base_body or <its current body>`, discarding whatever a previous call
    layered on top, before any amendment in this call is applied. That is
    what makes running this twice over the same inputs produce the same
    output, and what makes removing an amendment revert the clause exactly -
    the arithmetic equivalent of the orphaning defect `_prune_orphan_facts`
    guards against in pipeline.py.
    """
    out = [r.model_copy(deep=True) for r in requirements]
    linked = [a.model_copy(deep=True) for a in amendments]

    by_clause: dict[str, RequirementRecord] = {}
    for req in out:
        base = req.base_body or {field: getattr(req, field) for field in _BODY_FIELDS}
        for field, value in base.items():
            setattr(req, field, value)
        req.base_body = None
        req.amended_by = None
        req.withdrawn = False
        by_clause.setdefault(_norm_clause(req.clause_ref), req)

    for amend in linked:
        target = by_clause.get(_norm_clause(amend.clause_ref))
        if target is None:
            # A MOM changing a clause the requirements extractor missed is a
            # signal the requirements are incomplete, not something to hide.
            amend.req_id = None
            continue
        amend.req_id = target.req_id

        if target.base_body is None:
            target.base_body = {field: getattr(target, field) for field in _BODY_FIELDS}
        target.amended_by = amend.amendment_id

        if amend.action == "withdraw":
            target.withdrawn = True
            continue

        target.text = f"{target.text}\n[amended] {amend.text}"
        for field in ("parameter", "operator", "value", "unit"):
            fresh = getattr(amend, field)
            if fresh is not None:
                setattr(target, field, fresh)

        # A partially-stated amendment must not leave an `auto` requirement
        # with a half-replaced bound - the same demotion rule extract_requirements
        # applies at extraction time, re-applied here after the amendment lands.
        if target.checkability == "auto" and not (
                target.parameter and target.operator
                and target.value is not None and target.unit is not None):
            target.checkability = "judgement"

    return out, linked
