"""Resolve human overrides against extracted records.

Overrides always win. When a fresh extraction disagrees with the value the
override was recorded against, the override is flagged `conflict` and both
values are kept for review — nothing is silently dropped either way.
"""
import copy
import logging
import re

from procurement.store.models import Override

_log = logging.getLogger(__name__)

_SEGMENT = re.compile(r"^(?P<name>[^\[\]]+)(?:\[(?P<sel>[^\[\]]+)\])?$")

# Sentinel distinguishing "path does not resolve" from "path resolves to a
# stored None". A field legitimately extracted as None is not the same as a
# vanished field — conflating them would let a dropped fact masquerade as
# quiet agreement.
_MISSING = object()


def _parse(field_path: str) -> list[tuple[str, str | None]]:
    parts = []
    for raw in field_path.split("."):
        m = _SEGMENT.match(raw)
        if not m:
            raise ValueError(f"Malformed field_path segment: {raw!r}")
        sel = m.group("sel")
        if sel is not None and sel.isdigit():
            raise ValueError(
                f"Index selectors are not supported ({raw!r}); address list "
                "members by id, because list order is not stable across re-extractions"
            )
        parts.append((m.group("name"), sel))
    return parts


def _id_of(item: dict) -> str | None:
    for key in ("fact_id", "req_id", "doc_id", "clause_ref"):
        if key in item:
            return item[key]
    return None


def _walk(obj, parts, create: bool = False):
    """Return (container, last_key) or None when the path does not resolve."""
    cur = obj
    for name, sel in parts[:-1]:
        if not isinstance(cur, dict) or name not in cur:
            return None
        cur = cur[name]
        if sel is not None:
            if not isinstance(cur, list):
                return None
            match = next((i for i in cur if isinstance(i, dict) and _id_of(i) == sel), None)
            if match is None:
                return None
            cur = match
    name, sel = parts[-1]
    if sel is not None:
        if not isinstance(cur, dict) or not isinstance(cur.get(name), list):
            return None
        match = next((i for i in cur[name] if isinstance(i, dict) and _id_of(i) == sel), None)
        return None if match is None else (match, None)
    if not isinstance(cur, dict):
        return None
    if name not in cur and not create:
        return None
    return (cur, name)


def _resolve(obj: dict, field_path: str):
    """Return the value at field_path, or _MISSING if the path does not resolve."""
    parts = _parse(field_path)
    found = _walk(obj, parts)
    if found is None:
        return _MISSING
    container, key = found
    return container if key is None else container.get(key)


def get_by_path(obj: dict, field_path: str):
    value = _resolve(obj, field_path)
    return None if value is _MISSING else value


def set_by_path(obj: dict, field_path: str, value) -> bool:
    parts = _parse(field_path)
    found = _walk(obj, parts, create=True)
    if found is None:
        return False
    container, key = found
    if key is None:
        return False
    container[key] = value
    return True


def apply_overrides(record: dict, overrides: list[Override]) -> dict:
    out = copy.deepcopy(record)
    for o in overrides:
        if not set_by_path(out, o.field_path, o.value):
            _log.warning(
                "override for %r could not be applied: path does not resolve "
                "in the current record (the field may have been removed by "
                "re-extraction)",
                o.field_path,
            )
    return out


def reconcile(overrides: list[Override], fresh_record: dict) -> list[Override]:
    """Update each override against a fresh extraction, flagging disagreement.

    A path that no longer resolves at all (e.g. a list member dropped by a
    re-extraction) is its own state, distinct from "extraction agrees" —
    it always flags a conflict and never silently clears one that was
    already flagged, because data disappearing is not the same as data
    matching.
    """
    out = []
    for o in overrides:
        updated = o.model_copy()
        current = _resolve(fresh_record, o.field_path)
        if current is _MISSING:
            updated.conflict = True
            updated.extracted_value = None
        else:
            updated.conflict = current != o.extracted_value
            if updated.conflict:
                updated.extracted_value = current
        out.append(updated)
    return out
