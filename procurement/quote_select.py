import os

_POSITIVE = ("quotation", "quote", "offer", "proposal", "techno", "commercial")
_NEGATIVE = (
    "bom", "datasheet", "data sheet", "deviation", "mom", "spec", "load list",
    "synchron", "spare", "tool", "gas quality", "single line", "layout",
    "nameplate", "p&id", "pid", "drawing", "consumption", "power auxiliary",
    "codes and standards", "documents list", "outline",
)


def _score(path: str) -> int:
    name = os.path.basename(path).lower()
    ext = os.path.splitext(name)[1]
    score = 0
    if any(k in name for k in _POSITIVE):
        score += 100
    if any(k in name for k in _NEGATIVE):
        score -= 50
    if ext in (".pdf", ".xlsx"):
        score += 5
    return score


def pick_quote(files: list[str]) -> str | None:
    if not files:
        return None
    return max(files, key=lambda f: (_score(f), -len(os.path.basename(f))))
