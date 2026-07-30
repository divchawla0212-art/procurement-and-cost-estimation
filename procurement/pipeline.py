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
from procurement.classify import (classify_by_rules, classify_document,
                                  CLASSIFY_PROMPT_VERSION)
from procurement.revisions import resolve_supersession
from procurement.extract_tech import extract_tech_facts, TECH_PROMPT_VERSION
from procurement.extract_deviation import extract_deviations, DEVIATION_PROMPT_VERSION
from procurement.loaders import read_text

PROMPT_VERSION = "bid_extract_v1"        # kept: the quotation prompt version
PROMPT_VERSION_BY_CLASS = {
    "quotation": PROMPT_VERSION,
    "datasheet": TECH_PROMPT_VERSION,
    "deviation": DEVIATION_PROMPT_VERSION,
}
_EXTRACTABLE = tuple(PROMPT_VERSION_BY_CLASS)


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

    # Pass 1: classify. Rules decide most of it for free; text is read only
    # for the leftovers the rules declined.
    for doc in fresh_docs:
        prior = prior_docs.get(doc.doc_id)
        if (prior is not None and prior.content_sha256 == doc.content_sha256
                and prior.doc_class != "unclassified"
                and prior.classified_with == CLASSIFY_PROMPT_VERSION):
            doc.doc_class = prior.doc_class
            doc.classified_by = prior.classified_by
            doc.classified_with = prior.classified_with
            continue
        full = os.path.join(pdir, doc.path)
        head = ""
        if classify_by_rules(full) is None:
            # only the leftovers cost a text read; rules decide the rest free
            try:
                head = read_text(full, llm_fallback=None)[:500]
            except Exception:
                head = ""
        doc.doc_class, doc.classified_by = classify_document(full, client, head)
        doc.classified_with = CLASSIFY_PROMPT_VERSION
        events.append_event(root, slug, Event(
            at=_now(), run_id=run_id, actor="pipeline",
            action="document.classified", target=doc.doc_id,
            detail={"doc_class": doc.doc_class, "by": doc.classified_by}))

    # Pass 2: lineage, so an obsolete revision is never extracted.
    fresh_docs = resolve_supersession(fresh_docs)

    # Order quotations first within each vendor: facts_view["commercial"] is
    # None until a vendor's quotation has been extracted, and a field path
    # that does not resolve is flagged conflict=True. Processing a datasheet
    # first would raise a spurious conflict on any commercial.* override.
    fresh_docs.sort(key=lambda d: (d.vendor or "", d.doc_class != "quotation", d.path))

    # Among several quotations for one vendor, pick_quote still chooses which
    # one carries the commercial terms.
    quote_rel_by_vendor: dict[str, str] = {}
    for vendor in project.vendors:
        candidates = [os.path.join(pdir, d.path) for d in fresh_docs
                      if d.vendor == vendor and d.doc_class == "quotation"
                      and d.superseded_by is None]
        chosen = pick_quote(candidates)
        if chosen:
            quote_rel_by_vendor[vendor] = os.path.relpath(chosen, pdir).replace(os.sep, "/")

    documents: list[DocumentRecord] = []
    extracted, failed = 0, 0

    with snapshots.transaction(root, slug):
        for doc in fresh_docs:
            prior = prior_docs.get(doc.doc_id)
            expected_version = PROMPT_VERSION_BY_CLASS.get(doc.doc_class)

            skip_reason = None
            if doc.superseded_by is not None:
                skip_reason = f"superseded by {doc.superseded_by}"
            elif doc.doc_class not in _EXTRACTABLE:
                skip_reason = f"{doc.doc_class} documents are not extracted"
            elif (doc.doc_class == "quotation"
                  and quote_rel_by_vendor.get(doc.vendor) != doc.path):
                skip_reason = "not the selected quotation document"

            if skip_reason is not None:
                doc.extraction_status = "skipped"
                doc.notes = skip_reason
                documents.append(doc)
                events.append_event(root, slug, Event(
                    at=_now(), run_id=run_id, actor="pipeline",
                    action="document.skipped", target=doc.doc_id,
                    detail={"reason": skip_reason}))
                continue

            unchanged = (prior is not None
                         and prior.content_sha256 == doc.content_sha256
                         and prior.prompt_version == expected_version
                         and prior.extraction_status == "ok")
            if unchanged and not force:
                documents.append(prior)
                extracted += 1
                events.append_event(root, slug, Event(
                    at=_now(), run_id=run_id, actor="pipeline",
                    action="document.skipped", target=doc.doc_id,
                    detail={"reason": "unchanged"}))
                continue

            full = os.path.join(pdir, doc.path)
            prior_facts = snapshots.load_facts(root, slug, doc.vendor)
            base = prior_facts or VendorFacts(vendor=doc.vendor)
            status = "ok"

            if doc.doc_class == "quotation":
                bid = extract_bid(doc.vendor, [full], client, pdf_fallback=pdf_fallback)
                status = bid.extraction_status
                doc.notes = bid.notes
                commercial_dump = bid.model_dump()
                technical = list(base.technical)
                deviations = list(base.deviations)
            elif doc.doc_class == "datasheet":
                facts, status = extract_tech_facts(doc.doc_id, full, client,
                                                   pdf_fallback=pdf_fallback)
                if status == "ok":
                    # replace only this document's facts; other datasheets survive
                    technical = [f for f in base.technical if f.get("doc_id") != doc.doc_id]
                    technical += [f.model_dump() for f in facts]
                else:
                    # a failed re-extraction must not erase this document's
                    # previously-good, already-stored facts
                    technical = list(base.technical)
                commercial_dump = base.commercial
                deviations = list(base.deviations)
            else:   # deviation
                items, status = extract_deviations(doc.doc_id, full, client,
                                                   pdf_fallback=pdf_fallback)
                if status == "ok":
                    deviations = [d for d in base.deviations if d.get("doc_id") != doc.doc_id]
                    deviations += [d.model_dump() for d in items]
                else:
                    # same guard as the datasheet branch: a failed re-extraction
                    # must not erase this document's previously-good deviations
                    deviations = list(base.deviations)
                commercial_dump = base.commercial
                technical = list(base.technical)

            doc.extraction_status = status
            doc.extracted_at = _now()
            doc.extractor = f"llm:{expected_version}"
            doc.prompt_version = expected_version
            documents.append(doc)
            extracted += 1 if status == "ok" else 0
            failed += 0 if status == "ok" else 1

            facts_view = {"commercial": commercial_dump,
                          "technical": technical,
                          "deviations": deviations}
            overrides = reconcile(base.overrides, facts_view)
            resolved = apply_overrides(facts_view, overrides)
            normalized = (normalize_bid(
                VendorBid.model_validate(resolved["commercial"]),
                project.target_currency, project.fx_rates).model_dump()
                if resolved["commercial"] else base.normalized)

            snapshots.save_facts(root, slug, VendorFacts(
                vendor=doc.vendor,
                commercial=resolved["commercial"],
                normalized=normalized,
                technical=resolved["technical"],
                deviations=resolved["deviations"],
                overrides=overrides))

            events.append_event(root, slug, Event(
                at=_now(), run_id=run_id, actor="pipeline",
                action="document.extracted", target=doc.doc_id,
                detail={"status": status, "doc_class": doc.doc_class}))
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
