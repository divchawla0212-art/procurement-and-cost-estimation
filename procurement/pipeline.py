import os
from datetime import datetime, timezone

from procurement.project import load_project, save_project, vendor_files
from procurement.extract import extract_bid
from procurement.normalize import normalize_bid
from procurement.compare import build_comparison
from procurement.quote_select import pick_quote
from procurement.models import VendorBid, NormalizedBid
from procurement.store import events, layout, migrate, snapshots
from procurement.store.models import (Amendment, DocumentRecord, Event,
                                      RequirementRecord, RequirementSet,
                                      VendorFacts)
from procurement.store.overrides import apply_overrides, reconcile
from procurement.classify import (classify_by_rules, classify_document,
                                  CLASSIFY_PROMPT_VERSION)
from procurement.revisions import resolve_supersession
from procurement.extract_tech import extract_tech_facts, TECH_PROMPT_VERSION
from procurement.extract_deviation import extract_deviations, DEVIATION_PROMPT_VERSION
from procurement.extract_requirements import (extract_requirements,
                                              REQUIREMENTS_PROMPT_VERSION)
from procurement.extract_mom import (apply_amendments, extract_amendments,
                                     MOM_PROMPT_VERSION)
from procurement import compliance
from procurement.compliance import vocabulary, vocabulary_sha
from procurement.loaders import (MIN_EXTRACTABLE_CHARS, read_text,
                                 read_text_with_source)

PROMPT_VERSION = "bid_extract_v1"        # kept: the quotation prompt version
PROMPT_VERSION_BY_CLASS = {
    "quotation": PROMPT_VERSION,
    "datasheet": TECH_PROMPT_VERSION,
    "deviation": DEVIATION_PROMPT_VERSION,
}
_EXTRACTABLE = tuple(PROMPT_VERSION_BY_CLASS)

# Vendor-side routing. A vendor's copy of the client spec is their marked-up
# compliance response; the BOM, attachments and drawings state parameters too.
# All of them feed the technical extractor. doc_class is left untouched —
# documents.json must keep reporting what the classifier decided, not what
# routing did with it (the rule _rfq_route and inferred_quotes already follow).
#
# Values are ROUTES (keys of PROMPT_VERSION_BY_CLASS), not doc classes.
# _EXTRACTABLE deliberately keeps its current three-route membership: the
# inferred-quotation fallback pool filters on `doc_class not in _EXTRACTABLE`
# and must keep meaning "no extractor claims this document by its class".
# Public, unlike the tables above it: Task 8's coverage.py reports the route
# beside the class, and re-deriving the mapping there would be a second place
# for it to drift.
VENDOR_ROUTE = {
    "quotation": "quotation",
    "datasheet": "datasheet",
    "deviation": "deviation",
    "spec": "datasheet",
    "bom": "datasheet",
    "other": "datasheet",
    "drawing": "datasheet",
    "mom": "datasheet",
}

# A *second* extractor a routed document also feeds, when the vendor's own
# document set leaves the first one insufficient. A vendor whose entire
# submission is one techno-commercial proposal otherwise contributes commercial
# facts only and is checkable against no requirement at all — the phase-4
# motivating defect (a marked-up spec nothing read) one route over, and the one
# case Task 2 could not fix by widening VENDOR_ROUTE, because `quotation` was
# already routed: its single route produces the wrong *kind* of fact.
#
# A separate table rather than plural VENDOR_ROUTE values: VENDOR_ROUTE is
# public and read as a doc_class -> route map (Task 8's coverage.py reports the
# route beside the class), and a secondary route is a different thing anyway —
# conditional on the vendor's other documents, where the primary is not. Keyed
# by route, not by doc_class, so an inferred quotation gets the same treatment
# as a classified one; _vendor_route is the one place that knows which is which.
SECONDARY_VENDOR_ROUTE = {"quotation": "datasheet"}

# The RFQ side routes by its own table: the same doc_class means something
# different on the client's side of the tender. A `datasheet` here is the
# client's blank datasheet — a statement of what is required, not of what a
# vendor offers — so it is routed as a spec (see `_rfq_route`).
RFQ_PROMPT_VERSION_BY_CLASS = {
    "spec": REQUIREMENTS_PROMPT_VERSION,
    "mom": MOM_PROMPT_VERSION,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel_path(pdir: str, path: str) -> str:
    """A store-facing document path: relative to the project dir, posix separators."""
    return os.path.relpath(path, pdir).replace(os.sep, "/")


def _prune_orphan_facts(root: str, slug: str, run_id: str, vendors: list[str],
                        documents: list[DocumentRecord],
                        inferred_quotes: set[str],
                        secondary_routes: dict[str, str]) -> None:
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
    # Keyed by route, not merely by vendor: each collection is produced by one
    # extractor, so "still live" has to mean "still routed to the extractor
    # that produces this collection". A doc_id-only set is class-agnostic - a
    # datasheet the model later reads as a quotation, or one the no-quotation
    # fallback infers as one, stays in it under its new route - and its
    # technical facts would then sit in the store forever, refreshed by
    # nothing, still quoting a `verbatim` the document no longer contains.
    # commercial already had this narrower set; technical and deviations were
    # the same gap, one field over.
    live_by_route: dict[tuple[str, str], set[str]] = {}
    for doc in documents:
        # "failed" counts as live: the document is still present and still
        # routed to an extractor, so a transient provider outage must not
        # delete the facts an earlier successful run stored for it.
        if not (doc.vendor and doc.extraction_status in ("ok", "failed")):
            continue
        # A quotation candidate is either classified as one or, for a vendor
        # with no recognised quotation, inferred as one; routing leaves
        # doc_class untouched, so that case cannot be read back off the
        # document alone and _vendor_route is the one place that knows.
        # _vendor_routes, plural, for the same reason one field over: a
        # quotation that also feeds the technical extractor is live under both
        # routes, and stops being live under the second one - facts and all -
        # the run a datasheet of that vendor's arrives.
        for route in _vendor_routes(doc, inferred_quotes, secondary_routes):
            live_by_route.setdefault((doc.vendor, route), set()).add(doc.doc_id)

    for vendor in vendors:
        facts = snapshots.load_facts(root, slug, vendor)
        if facts is None:
            continue
        live_technical = live_by_route.get((vendor, "datasheet"), set())
        live_deviations = live_by_route.get((vendor, "deviation"), set())
        live_quotations = live_by_route.get((vendor, "quotation"), set())
        technical = [f for f in facts.technical if f.get("doc_id") in live_technical]
        deviations = [d for d in facts.deviations if d.get("doc_id") in live_deviations]
        # commercial has no per-record doc_id, so it is pruned via the stored
        # link: a vendor whose quotation was deleted, or whose document is
        # still present but no longer classifies (or infers) as a quotation,
        # kept its prices forever otherwise. The None guard matters: facts
        # written before quotation_doc_id existed have no link, and reading
        # that absence as "dead" would delete every pre-existing vendor's
        # prices on the first run after upgrade.
        commercial_dead = (facts.quotation_doc_id is not None
                           and facts.quotation_doc_id not in live_quotations)
        if (len(technical) == len(facts.technical)
                and len(deviations) == len(facts.deviations)
                and not commercial_dead):
            continue
        dropped = sorted(
            {f.get("doc_id") for f in facts.technical
             if f.get("doc_id") not in live_technical}
            | {d.get("doc_id") for d in facts.deviations
               if d.get("doc_id") not in live_deviations})
        detail = {"doc_ids": dropped,
                  "technical_dropped": len(facts.technical) - len(technical),
                  "deviations_dropped": len(facts.deviations) - len(deviations)}
        if commercial_dead:
            detail["commercial_dropped"] = facts.quotation_doc_id
            facts.commercial = None
            facts.normalized = None
            facts.quotation_doc_id = None
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


def inventory_rfq_documents(root: str, slug: str) -> list[DocumentRecord]:
    """Hash and record every file under requirements/. vendor is None, which
    is what keeps these documents out of every VendorFacts code path."""
    pdir = layout.project_dir(root, slug)
    rdir = os.path.join(pdir, "requirements")
    out: list[DocumentRecord] = []
    for dirpath, _dirs, files in os.walk(rdir):
        for name in sorted(files):
            if name.startswith(".") or name.startswith("~$"):
                continue
            full = os.path.join(dirpath, name)
            rel = _rel_path(pdir, full)
            out.append(DocumentRecord(
                doc_id=layout.doc_id_for(None, rel),
                path=rel,
                vendor=None,
                content_sha256=layout.content_sha256(full),
            ))
    return out


def _classify_pass(root: str, slug: str, docs: list[DocumentRecord],
                   prior_docs: dict[str, DocumentRecord], client,
                   run_id: str) -> None:
    """Assign doc_class/classified_by/classified_with to every document.

    Shared by the vendor and RFQ passes on purpose. classified_by is part of
    the gate: "llm-failed" means the model never answered, so the stored
    "other" is a degraded placeholder, not a classification, and caching it
    would let one network blip demote a document out of extraction
    permanently. A second copy of this loop would be a second place for that
    condition — and for the persisted classified_with beside it — to rot.
    """
    pdir = layout.project_dir(root, slug)
    for doc in docs:
        prior = prior_docs.get(doc.doc_id)
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


def _vendor_route(doc: DocumentRecord, inferred_quotes: set[str]) -> str | None:
    """The extractor a vendor document feeds, or None when nothing reads it.
    None is now reachable only for `unclassified`."""
    if doc.doc_id in inferred_quotes:
        return "quotation"
    return VENDOR_ROUTE.get(doc.doc_class)


def _vendor_routes(doc: DocumentRecord, inferred_quotes: set[str],
                   secondary_routes: dict[str, str]) -> list[str]:
    """Every extractor this document feeds, primary route first.

    The single answer to "which routes does this document reach", consulted by
    the vendor loop's dispatch and by _prune_orphan_facts alike. 80adec0 was a
    real defect that existed precisely because the prune's notion of "live" and
    the extractor's notion of "routed" were computed in two places and drifted;
    a document that reaches two extractors is exactly the shape that would let
    that happen again, so there is one place, and `secondary_routes` is
    computed once per run and handed to both.
    """
    route = _vendor_route(doc, inferred_quotes)
    if route is None:
        return []
    secondary = secondary_routes.get(doc.doc_id)
    return [route] if secondary is None else [route, secondary]


def _secondary_routes(docs: list[DocumentRecord], inferred_quotes: set[str],
                      quote_rel_by_vendor: dict[str, str]) -> dict[str, str]:
    """doc_id -> the additional extractor that document feeds, this run.

    A vendor's quotation also feeds the technical extractor when no other live
    document of theirs reaches it. Bounded by need rather than applied blanket:
    routing every quotation through the technical extractor as well would
    roughly double the quotation-class calls of every vendor in every run, to
    re-read parameters that the vendor's datasheets already state better. The
    condition is empty for a vendor who submitted a datasheet.

    Decided from doc_class, lineage and quotation selection alone — never from
    how a run *went*. Asking whether the datasheet happened to be readable this
    time would make routing vary with a transient provider outage, and the
    prune (which runs after extraction) would then disagree with the dispatch
    (which ran before it) about what a document feeds.

    Withdrawable by construction: it is recomputed from the live document set
    every run, so a vendor who later submits a datasheet stops having their
    quotation read technically, and the facts it contributed are then orphans
    that _prune_orphan_facts removes.
    """
    live_by_vendor: dict[str, list[DocumentRecord]] = {}
    for doc in docs:
        if doc.vendor and doc.superseded_by is None:
            live_by_vendor.setdefault(doc.vendor, []).append(doc)

    out: dict[str, str] = {}
    for vendor, live in live_by_vendor.items():
        routes = {d.doc_id: _vendor_route(d, inferred_quotes) for d in live}
        for doc in live:
            secondary = SECONDARY_VENDOR_ROUTE.get(routes[doc.doc_id])
            if secondary is None or secondary in routes.values():
                continue
            # Only the vendor's selected quotation reaches an extractor at all;
            # a second one is skipped, and a skipped document feeds nothing.
            if quote_rel_by_vendor.get(vendor) == doc.path:
                out[doc.doc_id] = secondary
    return out


def _rfq_route(doc: DocumentRecord) -> str:
    """An RFQ-side datasheet is the client's blank datasheet: it states what is
    required, so it feeds the requirements prompt. doc_class is left alone — documents
    .json must keep reporting what the classifier decided, not what routing
    did with it (the same rule the quotation fallback follows)."""
    return "spec" if doc.doc_class == "datasheet" else doc.doc_class


def _run_rfq_pass(root: str, slug: str, client,
                  prior_docs: dict[str, DocumentRecord], run_id: str,
                  pdf_fallback=None, force: bool = False
                  ) -> tuple[list[DocumentRecord], int, int]:
    """Extract requirements and amendments. Returns (documents, ok, failed).

    Mirrors the vendor pass — inventory, classify, resolve lineage, route,
    cache per class — and then prunes, because lineage only stops an obsolete
    document from being re-extracted; it does not remove what an earlier run
    already stored. A stale amendment is worse than a stale fact: it does not
    merely sit there, it rewrites a live requirement's value.
    """
    pdir = layout.project_dir(root, slug)
    docs = inventory_rfq_documents(root, slug)
    _classify_pass(root, slug, docs, prior_docs, client, run_id)
    docs = resolve_supersession(docs)

    prior = snapshots.load_requirements(root, slug)
    requirements = list(prior.requirements)
    amendments = list(prior.amendments)
    extracted = failed = 0

    for doc in docs:
        route = _rfq_route(doc)
        if route == "spec" and route != doc.doc_class:
            events.append_event(root, slug, Event(
                at=_now(), run_id=run_id, actor="pipeline",
                action="rfq.requirements_inferred", target=doc.doc_id,
                detail={"doc_class": doc.doc_class, "path": doc.path}))
        expected_version = RFQ_PROMPT_VERSION_BY_CLASS.get(route)

        skip_reason = None
        if doc.superseded_by is not None:
            skip_reason = f"superseded by {doc.superseded_by}"
        elif expected_version is None:
            skip_reason = f"{doc.doc_class} documents are not extracted"
        if skip_reason is not None:
            doc.extraction_status = "skipped"
            doc.notes = skip_reason
            events.append_event(root, slug, Event(
                at=_now(), run_id=run_id, actor="pipeline",
                action="document.skipped", target=doc.doc_id,
                detail={"reason": skip_reason}))
            continue

        prior_doc = prior_docs.get(doc.doc_id)
        unchanged = (prior_doc is not None
                     and prior_doc.content_sha256 == doc.content_sha256
                     and prior_doc.prompt_version == expected_version
                     and prior_doc.extraction_status == "ok")
        if unchanged and not force:
            # Carry the cached extraction forward onto the fresh record, never
            # append `prior_doc` — that was C2, which threw away this run's
            # classification and lineage.
            doc.extraction_status = prior_doc.extraction_status
            doc.notes = prior_doc.notes
            doc.extracted_at = prior_doc.extracted_at
            doc.extractor = prior_doc.extractor
            doc.prompt_version = prior_doc.prompt_version
            doc.text_source = prior_doc.text_source
            extracted += 1
            events.append_event(root, slug, Event(
                at=_now(), run_id=run_id, actor="pipeline",
                action="document.skipped", target=doc.doc_id,
                detail={"reason": "unchanged"}))
            continue

        full = os.path.join(pdir, doc.path)
        try:
            text, text_source = read_text_with_source(
                full, llm_fallback=pdf_fallback)
        except Exception as exc:
            text, text_source = "", "unreadable"
            doc.extraction_status = "failed"
            doc.notes = f"unreadable document: {exc}"
        doc.text_source = f"{text_source}:{len(text)}chars"

        if (doc.extraction_status != "failed"
                and len(text.strip()) < MIN_EXTRACTABLE_CHARS):
            # `failed`, never `skipped`: the live-set check below (and
            # _prune_orphan_facts' vendor-side counterpart) treats skipped as
            # dead and would delete requirements/amendments a good earlier run
            # stored, the first time `pdftotext` is missing from the PATH.
            doc.extraction_status = "failed"
            doc.notes = (f"no readable text layer "
                         f"({len(text.strip())} chars via {text_source}); "
                         "scanned or drawing-only document")
        if doc.extraction_status == "failed":
            failed += 1
            events.append_event(root, slug, Event(
                at=_now(), run_id=run_id, actor="pipeline",
                action="document.unreadable", target=doc.doc_id,
                detail={"text_source": doc.text_source}))
            continue

        if route == "spec":
            fresh, status, notes = extract_requirements(
                doc.doc_id, full, client, pdf_fallback=pdf_fallback, text=text)
            if status == "ok":
                # replace only this document's requirements; other specs survive
                requirements = [r for r in requirements
                                if r.source_doc_id != doc.doc_id] + fresh
            # on failure the previously-stored records for this document stay
        else:
            fresh, status, notes = extract_amendments(
                doc.doc_id, full, client, pdf_fallback=pdf_fallback, text=text)
            if status == "ok":
                amendments = [a for a in amendments
                              if a.source_doc_id != doc.doc_id] + fresh

        doc.notes = notes
        doc.extraction_status = status
        doc.extracted_at = _now()
        doc.extractor = f"llm:{expected_version}"
        doc.prompt_version = expected_version
        extracted += 1 if status == "ok" else 0
        failed += 0 if status == "ok" else 1
        events.append_event(root, slug, Event(
            at=_now(), run_id=run_id, actor="pipeline",
            action="document.extracted", target=doc.doc_id,
            detail={"status": status, "doc_class": doc.doc_class}))

    # Prune BEFORE applying: an amendment removed here must not still be named
    # by a requirement's amended_by. "failed" counts as live for the same
    # reason it does in _prune_orphan_facts — a transient outage must not
    # delete what a good earlier run stored.
    live_spec = {d.doc_id for d in docs if _rfq_route(d) == "spec"
                 and d.extraction_status in ("ok", "failed")}
    live_mom = {d.doc_id for d in docs if _rfq_route(d) == "mom"
                and d.extraction_status in ("ok", "failed")}
    kept_reqs = [r for r in requirements if r.source_doc_id in live_spec]
    kept_amends = [a for a in amendments if a.source_doc_id in live_mom]
    if len(kept_reqs) != len(requirements) or len(kept_amends) != len(amendments):
        events.append_event(root, slug, Event(
            at=_now(), run_id=run_id, actor="pipeline",
            action="requirements.pruned", target=None,
            detail={"requirements_dropped": len(requirements) - len(kept_reqs),
                    "amendments_dropped": len(amendments) - len(kept_amends)}))

    resolved_reqs, linked_amends = apply_amendments(kept_reqs, kept_amends)
    view = {"requirements": [r.model_dump() for r in resolved_reqs],
            "amendments": [a.model_dump() for a in linked_amends]}
    overrides = reconcile(prior.overrides, view)
    applied = apply_overrides(view, overrides)
    snapshots.save_requirements(root, slug, RequirementSet(
        requirements=[RequirementRecord.model_validate(r)
                      for r in applied["requirements"]],
        amendments=[Amendment.model_validate(a) for a in applied["amendments"]],
        overrides=overrides))
    for o in overrides:
        if o.conflict:
            events.append_event(root, slug, Event(
                at=_now(), run_id=run_id, actor="pipeline",
                action="override.conflicted", target=None,
                detail={"field_path": o.field_path}))
    return docs, extracted, failed


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
    _classify_pass(root, slug, fresh_docs, prior_docs, client, run_id)

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

    # The one structure that answers "which extractors does this document
    # feed": computed here, from the live document set and the selection above,
    # and handed to both the dispatch below and _prune_orphan_facts, so the two
    # cannot form separate opinions about it (80adec0's defect shape).
    secondary_routes = _secondary_routes(fresh_docs, inferred_quotes,
                                         quote_rel_by_vendor)

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
        # The RFQ side first: it writes requirements.json, which phase 3's
        # datasheet pass reads for its parameter vocabulary.
        rfq_docs, rfq_ok, rfq_failed = _run_rfq_pass(
            root, slug, client, prior_docs, run_id,
            pdf_fallback=pdf_fallback, force=force)
        extracted += rfq_ok
        failed += rfq_failed

        # Requirements first, then their vocabulary, then the datasheets — the
        # ordering spec section 7 requires. Read back from the store rather
        # than from the pass's return value, so overrides applied to a
        # requirement's parameter are part of the vocabulary too.
        parameters = vocabulary(snapshots.load_requirements(root, slug))
        vocab_sha = vocabulary_sha(parameters)

        for doc in fresh_docs:
            prior = prior_docs.get(doc.doc_id)
            # A document the fallback above picked as a vendor's quotation is
            # extracted as one whatever the classifier called it. doc_class
            # itself is left alone: documents.json must keep reporting what the
            # classifier actually decided, not what routing did with it.
            routes = _vendor_routes(doc, inferred_quotes, secondary_routes)
            route = routes[0] if routes else None
            secondary = routes[1] if len(routes) > 1 else None
            expected_version = PROMPT_VERSION_BY_CLASS.get(route)
            secondary_version = PROMPT_VERSION_BY_CLASS.get(secondary)

            skip_reason = None
            if doc.superseded_by is not None:
                skip_reason = f"superseded by {doc.superseded_by}"
            elif route is None:
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

            # The vocabulary is an input to the tech_facts_v1 prompt, so it
            # belongs in the datasheet cache key and nowhere else: a quotation
            # or deviation form is never asked about parameters, and folding it
            # into their key would re-extract them for nothing.
            unchanged = (prior is not None
                         and prior.content_sha256 == doc.content_sha256
                         and prior.prompt_version == expected_version
                         and prior.extraction_status == "ok"
                         and (route != "datasheet"
                              or prior.vocabulary_sha == vocab_sha))
            # The secondary pass caches on its own key. Both passes read the
            # same bytes, but they ask different prompts of them, so a
            # vocabulary edit must re-ask the technical pass without re-asking
            # the quotation one — and a quotation prompt bump the reverse.
            secondary_unchanged = (prior is not None
                                   and prior.content_sha256 == doc.content_sha256
                                   and prior.secondary_prompt_version == secondary_version
                                   and prior.secondary_status == "ok"
                                   and prior.vocabulary_sha == vocab_sha)
            run_primary = force or not unchanged
            run_secondary = (secondary is not None
                             and (force or not secondary_unchanged))
            if not run_primary and not run_secondary:
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
                # Carried forward or the bump never persists, and every
                # datasheet re-extracts on every run forever — C2a exactly.
                # Only while the document still reaches the technical
                # extractor, though: carrying a technical cache key onto a
                # document whose secondary route has been withdrawn (and whose
                # facts _prune_orphan_facts is about to delete) would make the
                # route's eventual return a cache *hit*, and the vendor would
                # go silently factless for good.
                if route == "datasheet" or secondary is not None:
                    doc.vocabulary_sha = prior.vocabulary_sha
                if secondary is not None:
                    doc.secondary_status = prior.secondary_status
                    doc.secondary_notes = prior.secondary_notes
                    doc.secondary_prompt_version = prior.secondary_prompt_version
                documents.append(doc)
                extracted += 1
                events.append_event(root, slug, Event(
                    at=_now(), run_id=run_id, actor="pipeline",
                    action="document.skipped", target=doc.doc_id,
                    detail={"reason": "unchanged"}))
                continue

            full = os.path.join(pdir, doc.path)
            try:
                text, text_source = read_text_with_source(
                    full, llm_fallback=pdf_fallback)
            except Exception as exc:
                text, text_source = "", "unreadable"
                doc.extraction_status = "failed"
                doc.notes = f"unreadable document: {exc}"
            doc.text_source = f"{text_source}:{len(text)}chars"

            if (doc.extraction_status != "failed"
                    and len(text.strip()) < MIN_EXTRACTABLE_CHARS):
                # `failed`, never `skipped`: _prune_orphan_facts treats skipped
                # as dead and would delete the facts a good earlier run stored,
                # the first time `pdftotext` is missing from the PATH.
                doc.extraction_status = "failed"
                doc.notes = (f"no readable text layer "
                             f"({len(text.strip())} chars via {text_source}); "
                             "scanned or drawing-only document")
            if doc.extraction_status == "failed":
                documents.append(doc)
                failed += 1
                events.append_event(root, slug, Event(
                    at=_now(), run_id=run_id, actor="pipeline",
                    action="document.unreadable", target=doc.doc_id,
                    detail={"text_source": doc.text_source}))
                continue

            prior_facts = snapshots.load_facts(root, slug, doc.vendor)
            base = prior_facts or VendorFacts(vendor=doc.vendor)
            status = "ok"
            commercial_dump = base.commercial
            quotation_doc_id = base.quotation_doc_id
            technical = list(base.technical)
            deviations = list(base.deviations)

            if not run_primary:
                # Only the secondary pass is stale, so the primary's cached
                # record — and the facts it stored — carry forward untouched.
                status = prior.extraction_status
                doc.extraction_status = prior.extraction_status
                doc.notes = prior.notes
                doc.extracted_at = prior.extracted_at
                doc.extractor = prior.extractor
                doc.prompt_version = prior.prompt_version
            elif route == "quotation":
                bid = extract_bid(doc.vendor, [full], client,
                                  pdf_fallback=pdf_fallback, text=text)
                status = bid.extraction_status
                doc.notes = bid.notes
                # the same guard the other two branches carry: phase 2 keeps
                # technical and deviations in this file too, so a failed
                # overwrite would publish a half-good record - facts intact,
                # commercial blanked - straight into the comparison
                commercial_dump = bid.model_dump() if status == "ok" else base.commercial
                # only on success: a failed re-extraction must not repoint the
                # link at a document that produced nothing, which would then
                # prune good prices
                quotation_doc_id = doc.doc_id if status == "ok" else base.quotation_doc_id
            elif route == "datasheet":
                facts, status, notes = extract_tech_facts(
                    doc.doc_id, full, client, pdf_fallback=pdf_fallback,
                    parameters=parameters, text=text)
                doc.vocabulary_sha = vocab_sha
                doc.notes = notes
                if status == "ok":
                    # replace only this document's facts; other datasheets survive
                    technical = [f for f in base.technical if f.get("doc_id") != doc.doc_id]
                    technical += [f.model_dump() for f in facts]
                # on failure a re-extraction must not erase this document's
                # previously-good, already-stored facts
            else:   # deviation
                items, status, notes = extract_deviations(doc.doc_id, full, client,
                                                          pdf_fallback=pdf_fallback,
                                                          text=text)
                doc.notes = notes
                if status == "ok":
                    deviations = [d for d in base.deviations if d.get("doc_id") != doc.doc_id]
                    deviations += [d.model_dump() for d in items]
                # same guard as the datasheet branch: a failed re-extraction
                # must not erase this document's previously-good deviations

            if run_primary:
                doc.extraction_status = status
                doc.extracted_at = _now()
                doc.extractor = f"llm:{expected_version}"
                doc.prompt_version = expected_version

            if run_secondary:
                # The additional pass, on text this iteration has already read.
                # Its outcome is recorded on its own fields: a failure here must
                # not blank the primary result, and marking the whole document
                # `failed` would do exactly that to the primary's cache entry.
                facts, secondary_status, secondary_notes = extract_tech_facts(
                    doc.doc_id, full, client, pdf_fallback=pdf_fallback,
                    parameters=parameters, text=text)
                doc.vocabulary_sha = vocab_sha
                doc.secondary_prompt_version = secondary_version
                doc.secondary_status = secondary_status
                doc.secondary_notes = secondary_notes
                if secondary_status == "ok":
                    technical = [f for f in technical if f.get("doc_id") != doc.doc_id]
                    technical += [f.model_dump() for f in facts]
                events.append_event(root, slug, Event(
                    at=_now(), run_id=run_id, actor="pipeline",
                    action="document.extracted_secondary", target=doc.doc_id,
                    detail={"status": secondary_status, "route": secondary,
                            "doc_class": doc.doc_class}))
            elif secondary is not None:
                doc.vocabulary_sha = prior.vocabulary_sha
                doc.secondary_prompt_version = prior.secondary_prompt_version
                doc.secondary_status = prior.secondary_status
                doc.secondary_notes = prior.secondary_notes

            documents.append(doc)
            # One document, one outcome: a document whose secondary pass failed
            # is a document this run did not fully read, and the run is
            # `done_with_failures` for it even though its primary pass stands.
            document_ok = status == "ok" and doc.secondary_status in (None, "ok")
            extracted += 1 if document_ok else 0
            failed += 0 if document_ok else 1

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
                overrides=overrides,
                quotation_doc_id=quotation_doc_id,
                # carried or a re-extraction silently discards the reviewer's
                # judgement — vocabulary_sha's defect, one field over
                technical_feedback=base.technical_feedback))

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

        _prune_orphan_facts(root, slug, run_id, project.vendors, documents,
                            inferred_quotes, secondary_routes)

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

        # Both lists, always. documents.json is replaced wholesale every run,
        # so saving only the vendor half would drop every RFQ record and the
        # next run would re-classify and re-extract the MR from scratch — an
        # unbounded per-run cost of exactly the C2a kind.
        snapshots.save_documents(root, slug, rfq_docs + documents)

        # Last inside the transaction, on purpose: every earlier position would
        # evaluate against facts that _prune_orphan_facts or the stale-vendor
        # sweep then removes, producing a verdict citing a fact that no longer
        # exists.
        results = compliance.evaluate_project(root, slug)
        events.append_event(root, slug, Event(
            at=_now(), run_id=run_id, actor="pipeline",
            action="compliance.evaluated", target=None,
            detail={"cells": len(results),
                    "by_verdict": {v: sum(1 for r in results if r.verdict == v)
                                   for v in compliance.VERDICTS}}))

    project = load_project(root, slug)
    project.status = ("failed" if extracted == 0
                      else "done_with_failures" if failed else "done")
    save_project(root, project)

    events.append_event(root, slug, Event(
        at=_now(), run_id=run_id, actor="pipeline", action="run.finished",
        detail={"extracted": extracted, "failed": failed,
                "documents": len(rfq_docs) + len(documents),
                "status": project.status}))

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
