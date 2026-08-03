"""(documents, facts, compliance) -> one extraction status per vendor.

Answers "which of this vendor's files did we actually read, and what did each
one give us". A read-only derivation over the snapshots, like matrix.py:
recomputed wholesale on every call, so a status can never outlive the document
it describes, and no new stored collection is introduced.
"""
from pydantic import BaseModel

from procurement.pipeline import (PROMPT_VERSION_BY_CLASS,
                                  SECONDARY_VENDOR_ROUTE, VENDOR_ROUTE)
from procurement.project import load_project
from procurement.store import snapshots

# The verdicts that mean "we did not read the answer". `review` is excluded on
# purpose: 255 of gas-11's 300 cells are `review` and none of them says
# anything about extraction coverage.
_UNANSWERED = ("unanswered",)

# Inverted, never transcribed: PROMPT_VERSION_BY_CLASS is the pipeline's own
# route -> prompt_version table (pipeline.py:30-34), and it is 1:1 across all
# three routes, so inverting it recovers the route a document was actually
# dispatched to from doc.prompt_version alone. This is the fix for the
# quotation-fallback case VENDOR_ROUTE.get(doc.doc_class) gets wrong: a
# document the inferred-quotation pool routed to "quotation" whose extraction
# then failed never repoints facts.quotation_doc_id at it (pipeline.py:774,
# only on status == "ok"), so doc.doc_id == quotation_doc_id is false even
# though the pipeline dispatched it to the quotation extractor. prompt_version,
# by contrast, is written whenever a primary pass RAN — `failed` exactly as
# `ok` (pipeline.py:798-802, unconditional on status) — and is carried forward
# on a cache hit (pipeline.py:691-692, :759-760), so it is reliable wherever a
# primary pass has ever run.
_ROUTE_BY_PROMPT_VERSION = {version: route
                           for route, version in PROMPT_VERSION_BY_CLASS.items()}


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
            # The route the pipeline actually dispatched to, recovered from
            # doc.prompt_version via the inverted PROMPT_VERSION_BY_CLASS.
            # Falls back to the doc_class/quotation_doc_id derivation only
            # when no primary pass has ever run (skipped, pending) and there
            # is therefore no dispatched route on the record to recover.
            route = _ROUTE_BY_PROMPT_VERSION.get(doc.prompt_version)
            if route is None:
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
            # Not conditioned on the primary status: run_secondary does not
            # depend on the primary's outcome (pipeline.py:804), and a
            # primary-failed/secondary-failed vendor is reachable and likely —
            # both passes hit the same provider on the same text, so one
            # outage or one token-ceiling condition fails both. Excluding that
            # case here would make rollup()'s only surfaced view of secondary
            # failures (rollup() strips `documents`, below) silently miss it.
            # Not a term in the extracted+failed+skipped==len(documents)
            # invariant below, so this does not disturb it.
            if doc.secondary_status == "failed":
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
