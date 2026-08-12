"""Ground truth for the parser A/B, from the one document read losslessly.

MKON submitted ADNOC datasheet DOD-30201-50150-BH-000-16-00-004 as .xlsx.
KERUI submitted *the same form*, filled in, as a 14-page PDF. openpyxl reads
the spreadsheet cell by cell with no parsing step to get wrong, so its
DESCRIPTION column is the parameter list any parser should recover from
KERUI's PDF -- a hard number that needs no model, no key and no network, and
that explains *why* an arm produces fewer review flags rather than only that
it does.

Not in `procurement/`: this scores an experiment, it is not part of the
pipeline. Not packaged either -- pyproject's package list covers
cost_estimation, procurement, shared and api only.
"""
import re

import openpyxl

# Column A of a parameter row is a sub-number: "1.1", "3.12". A bare integer
# ("1", "2") is a section banner -- "GENERAL", "DESIGN PARAMETERS FOR ENGINE" --
# whose DESCRIPTION is a heading, not a parameter, and whose requirement and
# vendor-answer cells are empty.
_PARAMETER_NUMBER = re.compile(r"^\d+\.\d+")

_NOISE = re.compile(r"[^a-z0-9]+")

_MARKUP = re.compile(r"<[^>]+>")


def content_length(text: str) -> int:
    """Alphanumeric characters of actual content, with any markup removed.

    The only character count safe to compare across arms, and both of the other
    obvious ones are traps. Raw length rewards `pdftotext -layout` for the
    spaces it pads columns with -- it returns 3x pypdf's characters on the KERUI
    datasheet and exactly the same 20,855 alphanumerics. Alphanumeric length
    alone then rewards LlamaParse for its HTML table markup, because
    `<table><tbody><tr><td>` normalizes to `tabletbodytrtd`: on the ADPOWER spec
    that inflates 17,721 real characters to 23,997, turning a 1% edge over
    pdftotext into an apparent 37% one.
    """
    return len(normalize(_MARKUP.sub(" ", text)))

# Below this many normalized characters a name is not evidence of anything: at
# 4-5 characters a substring search starts matching by accident, and a scorer
# that counts accidents flatters whichever arm emitted the most text. Measured
# against this sheet the rule drops a handful of names such as "Make" and
# "Type"; `parameters()` reports what it dropped rather than hiding it.
MIN_NORMALIZED_CHARS = 8


def normalize(text: str | None) -> str:
    """`compliance._norm`, reused deliberately.

    Stripping every non-alphanumeric means a name broken across a line, padded
    into a table cell, or re-wrapped by a parser still matches. That is the
    right call here: the question is whether the *content* survived the parse,
    and a scorer that penalised line wrapping would mostly measure page width.
    """
    return _NOISE.sub("", (text or "").lower())


def parameters(xlsx_path: str, sheet: str | None = None
               ) -> tuple[list[str], list[str]]:
    """(scorable parameter names, names dropped as too short).

    Reads the first worksheet unless told otherwise -- the second sheet of this
    workbook is 'Emission Data', a differently-shaped table that is not part of
    the numbered parameter list.
    """
    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]

    kept: list[str] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for row in ws.iter_rows(values_only=True):
        if len(row) < 2:
            continue
        number, description = row[0], row[1]
        if not _PARAMETER_NUMBER.match(str(number or "").strip()):
            continue
        name = str(description or "").strip()
        if not name:
            continue
        norm = normalize(name)
        if norm in seen:
            continue
        seen.add(norm)
        (kept if len(norm) >= MIN_NORMALIZED_CHARS else dropped).append(name)
    return kept, dropped


def recall(text: str, names: list[str]) -> tuple[list[str], list[str]]:
    """(names present contiguously in `text`, names not).

    The whole document is normalized once and searched as a single string, so a
    name the parser merely wrapped across two lines still counts as recovered.

    What this does *not* forgive is interleaving, and that is not a flaw to
    paper over -- it is half the measurement. `pdftotext -layout` renders a tall
    wrapped table cell line-by-line beside its neighbours, so a long
    DESCRIPTION comes back as

        Gas Gensets Operation: Parallel
        1.14 Generator Control Relay shall ... from one   Compliance Required
        unit to the other after defined time

    with the row number and the requirement column spliced into the middle of
    the sentence. The description survived; it is no longer contiguous. Read
    this number together with `recall_unordered` below: the two agree when a
    reader lost content, and diverge when it only rearranged it.
    """
    haystack = normalize(text)
    found, missing = [], []
    for name in names:
        (found if normalize(name) in haystack else missing).append(name)
    return found, missing


def recall_unordered(text: str, names: list[str]) -> tuple[list[str], list[str]]:
    """(names whose every word appears somewhere, names with a word missing).

    Deliberately weak, and only meaningful next to `recall`: over an 80k-char
    document most short words appear somewhere, so a high score here is not
    evidence of a good parse. The *gap* between the two is the signal --
    contiguous-but-not-unordered is impossible, so every name in the gap is one
    the reader emitted in pieces rather than dropped.

    Which of the two matters more is not obvious and is not settled here. The
    pipeline's own `_docx_lines` argues for keeping a row's cells together, on
    the grounds that splitting a clause from its unit invites the model to pair
    the wrong number with the wrong unit -- and by that argument interleaving is
    a feature. The A/B's `needs_human` counts are what adjudicate it; these two
    numbers only explain the result.
    """
    words_by_name = {}
    for name in names:
        words_by_name[name] = {w for w in _NOISE.split(name.lower()) if w}
    haystack_words = {w for w in _NOISE.split(text.lower()) if w}
    found, missing = [], []
    for name in names:
        words = words_by_name[name]
        (found if words and words <= haystack_words else missing).append(name)
    return found, missing
