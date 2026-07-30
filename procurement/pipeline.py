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


def _rel_path(pdir: str, path: str) -> str:
    """A store-facing document path: relative to the project dir, posix separators."""
    return os.path.relpath(path, pdir).replace(os.sep, "/")


def _prune_orphan_facts(root: str, slug: str, run_id: str, vendors: list[str],
                        documents: list[DocumentRecord]) -> None:
    """Drop facts belonging to documents that are no longer extractable.

    Lineage stops an obsolete revision from being *re-extracted*; it does not
    remove what an earlier run already stored, so a superseded, reclassified or
    deleted document would keep contributing withdrawn numbers to every
    downstream reader (phase 3's compliance matrix among them). In phase 1 one
    document per vendor overwrote `VendorFacts` wholesale, which pruned itself;
    multi-document accumulation removed that property, so this replaces it.

    Vendors are walked from the project rather than from `documents`, because a
    vendor whose only datasheet was deleted produces no document at all.
    """
    live_by_vendor: dict[str, set[str]] = {}
    for doc in documents:
        # "failed" counts as live: the document is still present and still
        # routed to an extractor, so a transient provider outage must not
        # delete the facts an earlier successful run stored for it.
        if doc.vendor and doc.extraction_status in ("ok", "failed"):
            live_by_vendor.setdefault(doc.vendor, set()).add(doc.doc_id)

    for vendor in vendors:
        facts = snapshots.load_facts(root, slug, vendor)
        if facts is None:
            continue
        live = live_by_vendor.get(vendor, set())
        technical = [f for f in facts.technical if f.get("doc_id") in live]
        deviations = [d for d in facts.deviations if d.get("doc_id") in live]
        if (len(technical) == len(facts.technical)
                and len(deviations) == len(facts.deviations)):
            continue
        dropped = sorted(
            {f.get("doc_id") for f in facts.technical if f.get("doc_id") not in live}
            | {d.get("doc_id") for d in facts.deviations if d.get("doc_id") not in live})
        detail = {"doc_ids": dropped,
                  "technical_dropped": len(facts.technical) - len(technical),
                  "deviations_dropped": len(facts.deviations) - len(deviations)}
        facts.technical = technical
        facts.deviations = deviations
        # Overrides are left untouched: they are re-reconciled against the
        # fresh extraction on the next run, where a path that no longer
        # resolves correctly raises conflict=True. Re-reconciling here would
        # compare against already-resolved values and corrupt extracted_value.
        snapshots.save_facts(root, slug, facts)
        events.append_event(root, slug, Event(
            at=_now(), run_id=run_id, actor="pipeline",
            action="facts.pruned", target=vendor, detail=detail))


def inventory_documents(root: str, slug: str) -> list[DocumentRecord]:
    """Hash and record every vendor file. Classification is phase 2, so
    doc_class stays 'unclassified' here."""
    project = load_project(root, slug)
    pdir = layout.project_dir(root, slug)
    out: list[DocumentRecord] = []
    for vendor in project.vendors:
        for path in vendor_files(root, slug, vendor):
            rel = _rel_path(pdir, path)
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
        # classified_by is part of the gate: "llm-failed" means the model never
        # answered, so the stored "other" is a degraded placeholder, not a
        # classification. Caching it would let one network blip demote a
        # document out of extraction permanently.
        if (prior is not None and prior.content_sha256 == doc.content_sha256
                and prior.doc_class != "unclassified"
                and prior.classified_by != "llm-failed"
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

    # Among several quotations for one vendor, pick_quote still chooses which
    # one carries the commercial terms.
    quote_rel_by_vendor: dict[str, str] = {}
    inferred_quotes: set[str] = set()   # doc_ids routed to the quotation
                                        # extractor despite their doc_class
    for vendor in project.vendors:
        live = [d for d in fresh_docs
                if d.vendor == vendor and d.superseded_by is None]
        chosen = pick_quote([os.path.join(pdir, d.path) for d in live
                             if d.doc_class == "quotation"])
        if chosen is not None:
            quote_rel_by_vendor[vendor] = _rel_path(pdir, chosen)
            continue

        # Phase 1 ran pick_quote over every vendor file with max(), so a vendor
        # always had a quotation candidate and never silently left the
        # comparison. Classification can now decline every document a vendor
        # uploaded — which is exactly the shape of a real opaque quotation
        # filename — so fall back to the phase-1 heuristic and record the
        # inference, instead of dropping the vendor without a word. The pool
        # excludes documents another extractor already claims: stealing a
        # datasheet to guess a price would lose its technical facts.
        fallback = [d for d in live if d.doc_class not in _EXTRACTABLE]
        chosen = pick_quote([os.path.join(pdir, d.path) for d in fallback])
        if chosen is None:
            events.append_event(root, slug, Event(
                at=_now(), run_id=run_id, actor="pipeline",
                action="vendor.no_quotation", target=vendor,
                detail={"reason": "no document could serve as a quotation",
                        "documents": len(live)}))
            continue
        rel = _rel_path(pdir, chosen)
        doc = next(d for d in fallback if d.path == rel)
        inferred_quotes.add(doc.doc_id)
        quote_rel_by_vendor[vendor] = rel
        events.append_event(root, slug, Event(
            at=_now(), run_id=run_id, actor="pipeline",
            action="vendor.quotation_inferred", target=vendor,
            detail={"doc_id": doc.doc_id, "path": rel,
                    "doc_class": doc.doc_class}))

    # Order quotations first within each vendor: facts_view["commercial"] is
    # None until a vendor's quotation has been extracted, and a field path
    # that does not resolve is flagged conflict=True. Processing a datasheet
    # first would raise a spurious conflict on any commercial.* override. An
    # inferred quotation must sort first too, hence this runs after selection.
    fresh_docs.sort(key=lambda d: (
        d.vendor or "",
        d.doc_class != "quotation" and d.doc_id not in inferred_quotes,
        d.path))

    documents: list[DocumentRecord] = []
    extracted, failed = 0, 0

    with snapshots.transaction(root, slug):
        for doc in fresh_docs:
            prior = prior_docs.get(doc.doc_id)
            # A document the fallback above picked as a vendor's quotation is
            # extracted as one whatever the classifier called it. doc_class
            # itself is left alone: documents.json must keep reporting what the
            # classifier actually decided, not what routing did with it.
            route = "quotation" if doc.doc_id in inferred_quotes else doc.doc_class
            expected_version = PROMPT_VERSION_BY_CLASS.get(route)

            skip_reason = None
            if doc.superseded_by is not None:
                skip_reason = f"superseded by {doc.superseded_by}"
            elif route not in _EXTRACTABLE:
                skip_reason = f"{doc.doc_class} documents are not extracted"
            elif (route == "quotation"
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
                # Carry the cached extraction forward onto the *fresh* record
                # rather than appending `prior` verbatim. Passes 1 and 2 wrote
                # this run's classification and lineage onto `doc`; appending
                # `prior` threw both away, so a CLASSIFY_PROMPT_VERSION bump
                # never persisted (re-classifying every cached document on
                # every run, forever) and a revision arriving in a second
                # upload lost its `supersedes` pointer. Cache semantics are
                # unchanged: nothing here triggers an extraction.
                doc.extraction_status = prior.extraction_status
                doc.notes = prior.notes
                doc.extracted_at = prior.extracted_at
                doc.extractor = prior.extractor
                doc.prompt_version = prior.prompt_version
                doc.text_source = prior.text_source
                documents.append(doc)
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

            if route == "quotation":
                bid = extract_bid(doc.vendor, [full], client, pdf_fallback=pdf_fallback)
                status = bid.extraction_status
                doc.notes = bid.notes
                # the same guard the other two branches carry: phase 2 keeps
                # technical and deviations in this file too, so a failed
                # overwrite would publish a half-good record - facts intact,
                # commercial blanked - straight into the comparison
                commercial_dump = bid.model_dump() if status == "ok" else base.commercial
                technical = list(base.technical)
                deviations = list(base.deviations)
            elif route == "datasheet":
                facts, status, notes = extract_tech_facts(doc.doc_id, full, client,
                                                          pdf_fallback=pdf_fallback)
                doc.notes = notes
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
                items, status, notes = extract_deviations(doc.doc_id, full, client,
                                                          pdf_fallback=pdf_fallback)
                doc.notes = notes
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

        _prune_orphan_facts(root, slug, run_id, project.vendors, documents)

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
