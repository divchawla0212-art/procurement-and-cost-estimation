"""(documents, facts, compliance) -> one extraction status per vendor.

Answers "which of this vendor's files did we actually read, and what did each
one give us". A read-only derivation over the snapshots, like matrix.py:
recomputed wholesale on every call, so a status can never outlive the document
it describes, and no new stored collection is introduced.
"""
from pydantic import BaseModel

from procurement.pipeline import SECONDARY_VENDOR_ROUTE, VENDOR_ROUTE
from procurement.project import load_project
from procurement.store import snapshots

# The verdicts that mean "we did not read the answer". `review` is excluded on
# purpose: 255 of gas-11's 300 cells are `review` and none of them says
# anything about extraction coverage.
_UNANSWERED = ("unanswered",)


class DocumentStatus(BaseModel):
    doc_id: str
    filename: str
    path: str
    doc_class: str              # what the classifier decided
    route: str | None           # which extractor read it; differs from the class
    status: str                 # ok | failed | skipped | pending
    notes: str | None = None
    text_source: str | None = None
    fact_count: int = 0
    is_quotation: bool = False
    superseded_by: str | None = None
    # A vendor document can feed a *second* extractor (Task 7b's
    # SECONDARY_VENDOR_ROUTE: an insufficiently-documented vendor's quotation
    # also feeds the technical extractor). These mirror DocumentRecord's own
    # secondary_status/secondary_notes fields so that outcome is not lost —
    # `status` alone cannot say "ok" and "the second read of this file failed"
    # at once, and `notes` carries only the merged, human-facing sentence.
    secondary_route: str | None = None
    secondary_status: str | None = None   # ok | failed; None: no second route
    secondary_notes: str | None = None


class VendorExtraction(BaseModel):
    vendor: str
    documents: list[DocumentStatus] = []
    extracted: int = 0
    failed: int = 0
    skipped: int = 0
    fact_count: int = 0
    has_commercial: bool = False
    unanswered: int = 0
    # Documents whose primary pass is `ok` but whose secondary pass is
    # `failed` — counted separately from `failed` because the primary
    # extraction really did succeed and still belongs in `extracted`. Without
    # this a document sitting `ok` with a quietly-failed second read is
    # indistinguishable from one that was never routed a second time at all.
    secondary_failed: int = 0


class ExtractionStatus(BaseModel):
    vendors: list[VendorExtraction] = []
    totals: dict[str, int] = {}


def build_extraction_status(root: str, slug: str) -> ExtractionStatus:
    documents = snapshots.load_documents(root, slug)
    compliance = snapshots.load_compliance(root, slug)
    # Vendors from the project, not from the facts directory: a vendor whose
    # extraction produced nothing must still appear, because a missing entry
    # reads as "did not bid" — the rule build_matrix and evaluate_project share.
    vendors = load_project(root, slug).vendors

    unanswered_by_vendor: dict[str, int] = {}
    for cell in compliance:
        if cell.verdict in _UNANSWERED:
            unanswered_by_vendor[cell.vendor] = (
                unanswered_by_vendor.get(cell.vendor, 0) + 1)

    out: list[VendorExtraction] = []
    for vendor in vendors:
        facts = snapshots.load_facts(root, slug, vendor)
        facts_by_doc: dict[str, int] = {}
        for fact in (facts.technical if facts else []):
            doc_id = fact.get("doc_id")
            facts_by_doc[doc_id] = facts_by_doc.get(doc_id, 0) + 1
        quotation_doc_id = facts.quotation_doc_id if facts else None

        entry = VendorExtraction(
            vendor=vendor,
            has_commercial=bool(facts and facts.commercial),
            unanswered=unanswered_by_vendor.get(vendor, 0))

        for doc in sorted((d for d in documents if d.vendor == vendor),
                          key=lambda d: d.path):
            # re-derived, never re-invented: VENDOR_ROUTE is the pipeline's own
            # table. An inferred quotation is reported by its stored link
            # rather than by re-running the fallback heuristic.
            route = ("quotation" if doc.doc_id == quotation_doc_id
                     else VENDOR_ROUTE.get(doc.doc_class))
            # `secondary_status is None` is DocumentRecord's own documented
            # contract for "no second route" — the same signal pipeline.py
            # writes, so this needs no independent notion of eligibility.
            secondary_route = (SECONDARY_VENDOR_ROUTE.get(route)
                               if doc.secondary_status is not None else None)

            entry.documents.append(DocumentStatus(
                doc_id=doc.doc_id,
                filename=doc.path.rsplit("/", 1)[-1],
                path=doc.path,
                doc_class=doc.doc_class,
                route=route,
                status=doc.extraction_status,
                notes=doc.notes,
                text_source=doc.text_source,
                fact_count=facts_by_doc.get(doc.doc_id, 0),
                is_quotation=doc.doc_id == quotation_doc_id,
                superseded_by=doc.superseded_by,
                secondary_route=secondary_route,
                secondary_status=doc.secondary_status,
                secondary_notes=doc.secondary_notes))
            if doc.extraction_status == "ok":
                entry.extracted += 1
            elif doc.extraction_status == "failed":
                entry.failed += 1
            elif doc.extraction_status == "skipped":
                entry.skipped += 1
            if doc.extraction_status == "ok" and doc.secondary_status == "failed":
                entry.secondary_failed += 1

        entry.fact_count = sum(d.fact_count for d in entry.documents)
        out.append(entry)

    totals = {
        "vendors": len(out),
        "documents": sum(len(v.documents) for v in out),
        "extracted": sum(v.extracted for v in out),
        "failed": sum(v.failed for v in out),
        "skipped": sum(v.skipped for v in out),
        "facts": sum(v.fact_count for v in out),
        "unanswered": sum(v.unanswered for v in out),
        "secondary_failed": sum(v.secondary_failed for v in out),
    }
    return ExtractionStatus(vendors=out, totals=totals)


def rollup(status: ExtractionStatus) -> list[dict]:
    """The per-vendor counts without the document list, for the Dashboard card."""
    return [v.model_dump(exclude={"documents"}) for v in status.vendors]
