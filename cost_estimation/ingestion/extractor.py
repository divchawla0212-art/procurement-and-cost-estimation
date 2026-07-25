import os
import openpyxl
from shared.llm.interface import LLMClient
from cost_estimation.config.loader import DisciplineConfig
from cost_estimation.models.schema import Document, DocType, WorkPackage, CostDataset
from cost_estimation.ingestion.classifier import classify_document, classify_sheet
from cost_estimation.ingestion.header_mapper import map_sheet
from cost_estimation.ingestion.workbook_loader import read_items
from cost_estimation.ingestion.summary_parser import parse_summary, attach_rollups

_SKIP_SHEETS = {"summary", "cover", "notes"}
_WORKBOOK_TYPES = {DocType.COSTING_WORKBOOK, DocType.SCHEDULE_OF_PRICES}


def ingest_workbook(path: str, client: LLMClient, config: DisciplineConfig) -> tuple[Document, list[WorkPackage]]:
    document = classify_document(path, config)
    # NOTE: not read_only — read_items/sheet_preview use random .cell() access,
    # which is unreliable in openpyxl read-only mode (max_row can be None).
    wb = openpyxl.load_workbook(path, data_only=True)
    packages: list[WorkPackage] = []
    for ws in wb.worksheets:
        if ws.title.strip().lower() in _SKIP_SHEETS:
            continue
        layout = map_sheet(ws, client, config, ws.title)
        items = read_items(ws, layout, path, ws.title)
        if not items:
            continue
        discipline, area = classify_sheet(ws.title, config)
        packages.append(WorkPackage(name=ws.title.strip(), discipline=discipline, area=area, cost_items=items))
    summary_ws = next((s for s in wb.worksheets if s.title.strip().lower() == "summary"), None)
    if summary_ws is not None:
        attach_rollups(packages, parse_summary(summary_ws, path))
    return document, packages


def ingest_directory(root: str, client: LLMClient, config: DisciplineConfig) -> CostDataset:
    dataset = CostDataset()
    for dirpath, _dirs, files in os.walk(root):
        for fname in files:
            if fname.startswith("~$"):
                continue
            path = os.path.join(dirpath, fname)
            doc = classify_document(path, config)
            if doc.doc_type in _WORKBOOK_TYPES and fname.lower().endswith(".xlsx"):
                document, packages = ingest_workbook(path, client, config)
                dataset.documents.append(document)
                dataset.work_packages.extend(packages)
            else:
                dataset.documents.append(doc)
    return dataset
