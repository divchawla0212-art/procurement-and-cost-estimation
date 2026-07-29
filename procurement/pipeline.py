import os
from datetime import datetime, timezone

from procurement.project import load_project, save_project, vendor_files
from procurement.extract import extract_bid
from procurement.normalize import normalize_bid
from procurement.compare import build_comparison
from procurement.quote_select import pick_quote
from procurement.models import VendorBid, NormalizedBid
from procurement.store import events, layout, migrate, snapshots
from procurement.store.models import DocumentRecord, Event, VendorFacts
from procurement.store.overrides import apply_overrides, reconcile

PROMPT_VERSION = "bid_extract_v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def inventory_documents(root: str, slug: str) -> list[DocumentRecord]:
    """Hash and record every vendor file. Classification is phase 2, so
    doc_class stays 'unclassified' here."""
    project = load_project(root, slug)
    pdir = layout.project_dir(root, slug)
    out: list[DocumentRecord] = []
    for vendor in project.vendors:
        for path in vendor_files(root, slug, vendor):
            rel = os.path.relpath(path, pdir).replace(os.sep, "/")
            out.append(DocumentRecord(
                doc_id=layout.doc_id_for(vendor, rel),
                path=rel,
                vendor=vendor,
                content_sha256=layout.content_sha256(path),
            ))
    return out


def run_ingestion(root: str, slug: str, client, pdf_fallback=None,
                  force: bool = False) -> dict:
    migrate.migrate_dataset_json(root, slug)

    project = load_project(root, slug)
    run_id = events.new_run_id()
    events.append_event(root, slug, Event(at=_now(), run_id=run_id, actor="pipeline",
                                          action="run.started"))

    prior_docs = {d.doc_id: d for d in snapshots.load_documents(root, slug)}
    fresh_docs = inventory_documents(root, slug)
    pdir = layout.project_dir(root, slug)

    quote_rel_by_vendor: dict[str, str] = {}
    for vendor in project.vendors:
        quote = pick_quote(vendor_files(root, slug, vendor))
        if quote:
            quote_rel_by_vendor[vendor] = os.path.relpath(quote, pdir).replace(os.sep, "/")

    documents: list[DocumentRecord] = []
    extracted, failed = 0, 0

    with snapshots.transaction(root, slug):
        for doc in fresh_docs:
            prior = prior_docs.get(doc.doc_id)
            is_quote = quote_rel_by_vendor.get(doc.vendor) == doc.path

            if not is_quote:
                doc.extraction_status = "skipped"
                doc.notes = "not the selected quotation document"
                documents.append(doc)
                events.append_event(root, slug, Event(
                    at=_now(), run_id=run_id, actor="pipeline",
                    action="document.skipped", target=doc.doc_id,
                    detail={"reason": "not_quote"}))
                continue

            unchanged = (prior is not None
                         and prior.content_sha256 == doc.content_sha256
                         and prior.prompt_version == PROMPT_VERSION
                         and prior.extraction_status == "ok")
            if unchanged and not force:
                documents.append(prior)
                extracted += 1
                events.append_event(root, slug, Event(
                    at=_now(), run_id=run_id, actor="pipeline",
                    action="document.skipped", target=doc.doc_id,
                    detail={"reason": "unchanged"}))
                continue

            bid = extract_bid(doc.vendor, [os.path.join(pdir, doc.path)],
                              client, pdf_fallback=pdf_fallback)
            doc.extraction_status = bid.extraction_status
            doc.notes = bid.notes
            doc.extracted_at = _now()
            doc.extractor = f"llm:{PROMPT_VERSION}"
            doc.prompt_version = PROMPT_VERSION
            doc.doc_class = "quotation"
            doc.classified_by = "rule"
            documents.append(doc)

            if bid.extraction_status == "ok":
                extracted += 1
            else:
                failed += 1

            prior_facts = snapshots.load_facts(root, slug, doc.vendor)
            prior_overrides = prior_facts.overrides if prior_facts else []
            commercial = bid.model_dump()
            overrides = reconcile(prior_overrides, commercial)
            resolved = apply_overrides(commercial, overrides)
            normalized = normalize_bid(
                VendorBid.model_validate(resolved),
                project.target_currency, project.fx_rates)

            snapshots.save_facts(root, slug, VendorFacts(
                vendor=doc.vendor,
                commercial=resolved,
                normalized=normalized.model_dump(),
                technical=prior_facts.technical if prior_facts else [],
                deviations=prior_facts.deviations if prior_facts else [],
                overrides=overrides))

            events.append_event(root, slug, Event(
                at=_now(), run_id=run_id, actor="pipeline",
                action="document.extracted", target=doc.doc_id,
                detail={"status": bid.extraction_status}))
            for o in overrides:
                if o.conflict:
                    events.append_event(root, slug, Event(
                        at=_now(), run_id=run_id, actor="pipeline",
                        action="override.conflicted", target=doc.doc_id,
                        detail={"field_path": o.field_path}))

        snapshots.save_documents(root, slug, documents)

    project = load_project(root, slug)
    project.status = ("failed" if extracted == 0 and failed
                      else "done_with_failures" if failed else "done")
    save_project(root, project)

    events.append_event(root, slug, Event(
        at=_now(), run_id=run_id, actor="pipeline", action="run.finished",
        detail={"extracted": extracted, "failed": failed,
                "documents": len(documents), "status": project.status}))

    return load_dataset(root, slug) or {}


def load_dataset(root: str, slug: str) -> dict | None:
    """Assemble the UI-facing view from the store."""
    vendors = snapshots.list_fact_vendors(root, slug)
    if not vendors:
        return None
    project = load_project(root, slug)
    bids, normalized = [], []
    for vendor in vendors:
        facts = snapshots.load_facts(root, slug, vendor)
        if facts is None:
            continue
        if facts.commercial:
            bids.append(facts.commercial)
        if facts.normalized:
            normalized.append(facts.normalized)
    comparison = build_comparison(
        [VendorBid.model_validate(b) for b in bids],
        [NormalizedBid.model_validate(n) for n in normalized],
        project.target_currency)
    return {"bids": bids, "normalized": normalized,
            "comparison": comparison.model_dump()}
