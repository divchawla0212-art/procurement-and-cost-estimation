"""Assign a doc_class to a document.

Two passes: deterministic filename rules first, an LLM call only for what
the rules cannot decide. `classified_by` records which pass decided, so the
model's calls can be audited separately from the free ones.
"""
import os
import re

DOC_CLASSES = ("spec", "quotation", "datasheet", "deviation",
               "bom", "drawing", "mom", "other")

# Ordered: the first class whose keywords match wins, so specific beats broad.
# Short keywords use word-boundary regex to avoid false matches in larger words.
# Longer phrases use substring matching which is already unambiguous.
_RULES: tuple[tuple[str, tuple[str | re.Pattern, ...]], ...] = (
    ("datasheet", ("datasheet", "data sheet")),
    ("deviation", ("deviation",)),
    ("mom", (re.compile(r"\bmom\b"), "minutes of meeting")),
    ("bom", (re.compile(r"\bbom\b"), "bill of material")),
    ("drawing", ("drawing", "layout", re.compile(r"\bp&id\b"), re.compile(r"\bpid\b"), "single line",
                 "nameplate", "name plate", "outline", "general arrangement",
                 "diagram", "architecture")),
    ("quotation", ("quotation", "quote", "offer", "proposal",
                   "techno commercial", "commercial proposal")),
    ("spec", ("material requisition", re.compile(r"\bmr\b"), "spc-", re.compile(r"\bspec\b"))),
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
