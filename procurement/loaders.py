import os
import shutil
import subprocess
import openpyxl
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
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        return fh.read()
