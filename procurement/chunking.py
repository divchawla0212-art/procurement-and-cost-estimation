"""Splitting a document's text for a chunked extraction pass.

Lives here rather than inside either extractor because two of them now need
the same rule. A second copy would drift: a fix applied to one splitter would
silently leave the other tearing values from their units, which is the precise
failure the "arithmetic stays in Python" rule exists to prevent.
"""


def chunk_on_lines(text: str, budget: int) -> list[str]:
    """Split on line boundaries, in order, losslessly.

    Never mid-line: read_xlsx_text emits one spreadsheet row per line and
    read_pdf_text preserves line structure, so a value torn from its unit
    invites the model to pair the wrong number with the wrong unit. No overlap:
    overlap duplicates the records the chunk carries.

    A single line longer than `budget` is emitted whole rather than cut - the
    line boundary is the guarantee, and the budget is the target it is kept to
    wherever the text gives it a boundary to keep.
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
