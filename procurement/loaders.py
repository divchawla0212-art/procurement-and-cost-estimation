import os
import shutil
import subprocess
import docx
import openpyxl
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from openpyxl.utils import get_column_letter


def read_xlsx_text(path: str) -> str:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    lines: list[str] = []
    for ws in wb.worksheets:
        for r, row in enumerate(ws.iter_rows(values_only=True), 1):
            cells = [
                f"{get_column_letter(c)}{r}={v}"
                for c, v in enumerate(row, 1)
                if v is not None
            ]
            if cells:
                lines.append(f"{ws.title} | " + " | ".join(cells))
    return "\n".join(lines)


def _docx_lines(parent, element) -> list[str]:
    """Body text of one container, in document order.

    python-docx exposes `.paragraphs` and `.tables` as separate flat lists, so
    reading them in turn would tear a numbered clause away from the table that
    qualifies it. Walking the XML children keeps the original order.
    """
    lines: list[str] = []
    for child in element.iterchildren():
        if child.tag == qn("w:p"):
            text = Paragraph(child, parent).text.strip()
            if text:
                lines.append(text)
        elif child.tag == qn("w:tbl"):
            for row in Table(child, parent).rows:
                cells: list[str] = []
                seen: set[int] = set()
                for cell in row.cells:
                    # A horizontally merged cell is yielded once per column it
                    # spans; emitting it each time would triple a clause.
                    if id(cell._tc) in seen:
                        continue
                    seen.add(id(cell._tc))
                    # Nested tables collapse into their host cell rather than
                    # becoming rows of their own.
                    cells.append(" ".join(_docx_lines(cell, cell._tc)).strip())
                if any(cells):
                    # One row per line, cells joined as read_xlsx_text joins
                    # them: a clause split from its unit across lines invites
                    # the model to pair the wrong number with the wrong unit.
                    lines.append(" | ".join(cells))
    return lines


def read_docx_text(path: str) -> str:
    """Paragraph and table text of a Word (.docx) document.

    Raises ValueError if the file is not a readable OOXML package. It must not
    degrade to "": an empty string reads downstream as "the document said
    nothing", and a requirements extraction would be recorded `ok` with no
    clauses rather than `failed` with a reason.
    """
    try:
        document = docx.Document(path)
        return "\n".join(_docx_lines(document, document.element.body))
    except Exception as exc:
        raise ValueError(f"unreadable .docx document {path!r}: {exc}") from exc


def _pdftotext(path: str) -> str:
    exe = shutil.which("pdftotext")
    if not exe:
        return ""
    try:
        out = subprocess.run([exe, "-layout", path, "-"], capture_output=True, timeout=120)
        return out.stdout.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _pypdf(path: str) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""


def read_pdf_text(path: str, llm_fallback=None, min_chars: int = 200) -> str:
    """Robust PDF text: pdftotext CLI -> pypdf -> LLM transcription fallback."""
    text = _pdftotext(path)
    if len(text.strip()) < min_chars:
        alt = _pypdf(path)
        if len(alt.strip()) > len(text.strip()):
            text = alt
    if len(text.strip()) < min_chars and llm_fallback is not None:
        try:
            fb = llm_fallback(path)
            if fb and fb.strip():
                return fb
        except Exception:
            pass
    return text


def read_text(path: str, llm_fallback=None) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xlsx":
        return read_xlsx_text(path)
    if ext == ".pdf":
        return read_pdf_text(path, llm_fallback=llm_fallback)
    if ext == ".docx":
        return read_docx_text(path)
    if ext == ".doc":
        # Legacy binary Word, not an OOXML package — nothing here reads it. The
        # raw fallback below would decode it to mojibake and hand that to an
        # extractor as though it were clause text, so say so instead.
        raise ValueError(
            f"legacy binary .doc is not supported: {path!r}. "
            "Convert it to .docx or .pdf and re-upload.")
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        return fh.read()
