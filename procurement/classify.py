"""Assign a doc_class to a document.

Two passes: deterministic filename rules first, an LLM call only for what
the rules cannot decide. `classified_by` records which pass decided, so the
model's calls can be audited separately from the free ones.
"""
import os

DOC_CLASSES = ("spec", "quotation", "datasheet", "deviation",
               "bom", "drawing", "mom", "other")

# Ordered: the first class whose keywords match wins, so specific beats broad.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("datasheet", ("datasheet", "data sheet")),
    ("deviation", ("deviation",)),
    ("mom", ("mom ", " mom", "minutes of meeting")),
    ("bom", ("bom", "bill of material")),
    ("drawing", ("drawing", "layout", "p&id", "pid ", " pid", "single line",
                 "nameplate", "name plate", "outline", "general arrangement",
                 "diagram", "architecture")),
    ("quotation", ("quotation", "quote", "offer", "proposal",
                   "techno commercial", "commercial proposal")),
    ("spec", ("material requisition", " mr ", "mr_", "-mr-", "spc-", " spec ")),
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
        if any(k in name for k in keywords):
            return doc_class
    return None
