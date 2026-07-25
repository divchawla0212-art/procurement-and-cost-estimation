import re
import os
from cost_estimation.models.schema import Document, DocType
from cost_estimation.config.loader import DisciplineConfig

_REV = re.compile(r"rev[.\s\-]*([0-9]+)", re.IGNORECASE)
_SECTION = re.compile(r"\b3\s*([A-G])\b", re.IGNORECASE)


def _doc_type(name: str) -> DocType:
    low = name.lower()
    ext = os.path.splitext(low)[1]
    if ext == ".docx":
        return DocType.PROPOSAL_TEMPLATE
    if "schedule of prices" in low:
        return DocType.SCHEDULE_OF_PRICES
    if "costing" in low or "unit rates" in low or "unit rate" in low:
        return DocType.COSTING_WORKBOOK
    if ext == ".pdf" or "sow" in low or "scope of work" in low:
        return DocType.SCOPE_OF_WORK
    return DocType.COSTING_WORKBOOK


def _discipline_from_text(text: str, config: DisciplineConfig) -> str | None:
    low = text.lower()
    for name in config.disciplines.values():
        if name in low:
            return name
    return None


def classify_document(path: str, config: DisciplineConfig) -> Document:
    name = os.path.basename(path)
    rev_matches = _REV.findall(name)
    revision = rev_matches[-1] if rev_matches else None
    return Document(
        path=path,
        doc_type=_doc_type(name),
        discipline=_discipline_from_text(name, config),
        revision=revision,
    )


def classify_sheet(sheet_name: str, config: DisciplineConfig) -> tuple[str | None, str | None]:
    name = sheet_name.strip()
    area = None
    for a in config.areas:
        if name.upper().startswith(a.upper()):
            area = a
            break
    discipline = _discipline_from_text(name, config)
    if discipline is None:
        m = _SECTION.search(name)
        if m:
            discipline = config.disciplines.get("3" + m.group(1).upper())
    return discipline, area
