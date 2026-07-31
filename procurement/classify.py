"""Assign a doc_class to a document.

Two passes: deterministic filename rules first, an LLM call only for what
the rules cannot decide. `classified_by` records which pass decided, so the
model's calls can be audited separately from the free ones.
"""
import logging
import os
import re

_log = logging.getLogger(__name__)

DOC_CLASSES = ("spec", "quotation", "datasheet", "deviation",
               "bom", "drawing", "mom", "other")

# Ordered: the first class whose keywords match wins, so specific beats broad.
# Short keywords use lookaround patterns to avoid false matches in larger words
# while correctly treating underscore as a delimiter (not a word character).
# Longer phrases use substring matching which is already unambiguous.
_RULES: tuple[tuple[str, tuple[str | re.Pattern, ...]], ...] = (
    ("datasheet", ("datasheet", "data sheet")),
    ("deviation", ("deviation",)),
    ("mom", (re.compile(r"(?<![a-z0-9])mom(?![a-z0-9])"), "minutes of meeting")),
    ("bom", (re.compile(r"(?<![a-z0-9])bom(?![a-z0-9])"), "bill of material")),
    ("drawing", ("drawing", "layout", re.compile(r"(?<![a-z0-9])p&id(?![a-z0-9])"), re.compile(r"(?<![a-z0-9])pid(?![a-z0-9])"), "single line",
                 "nameplate", "name plate", "outline", "general arrangement",
                 "diagram", "architecture")),
    ("quotation", ("quotation", "quote", "offer", "proposal",
                   "techno commercial", "commercial proposal")),
    ("spec", ("material requisition", re.compile(r"(?<![a-z0-9])mr(?![a-z0-9])"), "spc-", re.compile(r"(?<![a-z0-9])spec(?![a-z0-9])"))),
    # Supporting documents: recognised so they cost no LLM call, but not
    # extractable this phase. Phase 3 may promote some of these.
    ("other", ("consumption", "equipment list", "special tools", "spares",
               "power auxiliary", "codes and standards", "documents list",
               "load list", "gas quality", "synchron")),
)


def classify_by_rules(path: str) -> str | None:
    """Return a doc_class, or None when no rule is confident enough."""
    name = os.path.basename(path).lower()
    for doc_class, keywords in _RULES:
        for k in keywords:
            if isinstance(k, re.Pattern):
                if k.search(name):
                    return doc_class
            elif k in name:
                return doc_class
    return None


from pathlib import Path
from pydantic import BaseModel

CLASSIFY_PROMPT_VERSION = "doc_class_v1"
_CLASSIFY_PROMPT = (Path(__file__).parents[1] / "shared" / "llm" / "prompts"
                    / "doc_class_v1.txt")
_TEXT_HEAD_CHARS = 500


class _DocClass(BaseModel):
    doc_class: str = "other"


def classify_document(path: str, client, text_head: str = "") -> tuple[str, str]:
    """Return (doc_class, classified_by). The model is consulted only when the
    filename rules decline, and never gets to abort a run: any failure
    degrades to ("other", "llm-failed") and any unrecognised answer degrades
    to ("other", "llm")."""
    ruled = classify_by_rules(path)
    if ruled is not None:
        return ruled, "rule"

    context = (f"Filename: {os.path.basename(path)}\n\n"
               f"Opening text:\n{text_head[:_TEXT_HEAD_CHARS]}")
    try:
        prompt = _CLASSIFY_PROMPT.read_text(encoding="utf-8")
        result = _DocClass.model_validate(
            client.classify_structure(prompt, _DocClass, context))
        answer = result.doc_class
    except Exception as exc:
        # "llm-failed" is what lets the caller retry a transient outage rather
        # than caching the degraded answer; without a log line there is no way
        # to tell a provider blip from a genuinely unclassifiable document.
        _log.warning("classification failed for %s: %s", os.path.basename(path), exc)
        return "other", "llm-failed"
    return (answer if answer in DOC_CLASSES else "other"), "llm"
