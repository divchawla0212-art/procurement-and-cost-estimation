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
            # field_path is relative to the snapshot root (VendorFacts), where
            # commercial/technical/deviations are siblings - so overrides must
            # be reconciled and applied against that shape, not against the
            # bare bid dump. Against the bid dump "commercial.base_price"
            # simply never resolves and the human's correction is discarded.
            facts_view = {
                "commercial": bid.model_dump(),
                "technical": prior_facts.technical if prior_facts else [],
                "deviations": prior_facts.deviations if prior_facts else [],
            }
            overrides = reconcile(prior_overrides, facts_view)
            resolved = apply_overrides(facts_view, overrides)
            normalized = normalize_bid(
                VendorBid.model_validate(resolved["commercial"]),
                project.target_currency, project.fx_rates)

            snapshots.save_facts(root, slug, VendorFacts(
                vendor=doc.vendor,
                commercial=resolved["commercial"],
                normalized=normalized.model_dump(),
                technical=resolved["technical"],
                deviations=resolved["deviations"],
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

        # A vendor removed from the project must not keep haunting the
        # comparison: documents.json is replaced wholesale every run, but
        # facts.json lives in a per-vendor directory nobody was pruning, and
        # load_dataset enumerates vendors from those directories. Snapshots are
        # authoritative, so the stale file is deleted rather than merely
        # filtered out at read time.
        for vendor in snapshots.list_fact_vendors(root, slug):
            if vendor in project.vendors:
                continue
            if snapshots.delete_facts(root, slug, vendor):
                events.append_event(root, slug, Event(
                    at=_now(), run_id=run_id, actor="pipeline",
                    action="vendor.pruned", target=vendor,
                    detail={"reason": "no longer in the project"}))

        snapshots.save_documents(root, slug, documents)

    project = load_project(root, slug)
    project.status = ("failed" if extracted == 0
                      else "done_with_failures" if failed else "done")
    save_project(root, project)

    events.append_event(root, slug, Event(
        at=_now(), run_id=run_id, actor="pipeline", action="run.finished",
        detail={"extracted": extracted, "failed": failed,
                "documents": len(documents), "status": project.status}))

    return load_dataset(root, slug) or {}


def load_dataset(root: str, slug: str) -> dict | None:
    """Assemble the UI-facing view from the store.

    Migrates on read as well as on ingestion: the portal calls this on every
    render, and a project whose results only exist in a legacy dataset.json
    would otherwise show nothing until the user paid for a full re-extraction.
    The migration is idempotent and a no-op once the store is populated.
    """
    migrate.migrate_dataset_json(root, slug)
    vendors = snapshots.list_fact_vendors(root, slug)
    if not vendors:
        return None
    project = load_project(root, slug)
    if project.vendors:
        # defence in depth behind run_ingestion's pruning: never show a vendor
        # the project no longer has. Skipped when the project lists no vendors
        # at all, since that cannot distinguish "none" from "not recorded" for
        # a project whose facts came from migration.
        vendors = [v for v in vendors if v in set(project.vendors)]
    if not vendors:
        return None
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
