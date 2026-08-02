"""Phase 4 integration: the two-run mutation matrix for extraction coverage.

Every row runs ingestion at least twice against the same project directory
with one thing mutated in between, and asserts over a **loaded snapshot**
after the last run. A row that could be satisfied by a single-run assertion is
not testing what it claims: a fact that should have been pruned, a cache that
should have been invalidated and a stored set that silently shrank are all
invisible to a run that has nothing to lose yet. That is the phase-2 defect
class this file exists to close, per docs/superpowers/PLAN-TEMPLATE.md Rule 2.

Each row names the invariant it defends:

* INV-A  every document with a vendor and an ok/failed status carries a
         `text_source`; an unreadable document is `failed`, never `skipped`.
* INV-B  `VendorFacts.technical` holds exactly the facts of the vendor's
         currently-live, currently-routable documents.
* INV-C  `requirements.json` holds exactly the requirements of the currently
         authoritative spec revision; a chunk failure changes nothing.
* INV-D  a `stated` requirement carries a parameter, a scalar-or-null value
         and null operator/unit; an `auto` one carries all four.
* INV-E  every verdict names its `req_id`, cites only a fact that is still
         stored, and is never `fail` for want of evidence.
"""
import os

import pytest

from procurement import pipeline
from procurement.classify import classify_document
from procurement.compliance import vocabulary_sha
from procurement.extract_requirements import REQUIREMENTS_PROMPT_VERSION
from procurement.extract_tech import TECH_PROMPT_VERSION
from procurement.pipeline import VENDOR_ROUTE, run_ingestion
from procurement.project import load_project, save_project
from procurement.store import events, snapshots
from procurement.store.models import Override
from shared.llm.anthropic_client import coerce_structured

from tests.test_pipeline_rfq import RfqClient          # the schema-aware stub
from tests.test_pipeline_vocabulary import _PAD, _project

_MR = "ADN-AEC-ME-SPC-026 MR Gas Genset.txt"
_MR_REV1 = "ADN-AEC-ME-SPC-026 MR Gas Genset(Rev1).txt"
_SECOND_SPEC = "ADN-AEC-ME-SPC-027 MR Gas Genset B.txt"
_MOM = "00 MOM 20241111 ASTRA.txt"
_QUOTATION = "Quotation.txt"
_DATASHEET = "01 DataSheet Gas Generator.txt"
_DATASHEET_REV1 = "01 DataSheet Gas Generator(Rev1).txt"
_DATASHEET_BODY = "H2S up to 70 ppm"                   # written by _project
_DEVIATION = "03 Attachment-2 Vendor Deviation Form.txt"
_SPARES = "08 Two Years Operation Spares.txt"
_NOW = "2026-08-02T00:00:00+00:00"

# One vendor document per doc_class in VENDOR_ROUTE. Six of the eight route to
# the technical extractor; only `quotation` and `deviation` have an extractor
# of their own. Task 2 widened this table, so a row that asserts "every routed
# document" needs a fixture that actually holds one of each - otherwise
# reverting the table to its old three-class gate would leave the row green.
_WIDE_DOCUMENTS = {
    "ADN-AEC-ME-SPC-026 MR Gas Genset copy.txt": "our marked-up copy of the MR",
    "BOM.txt": "bill of material: engine MAN, alternator Stamford",
    _SPARES: "two years of operating spares",
    "15 LAYOUT - KGW550GF-T.txt": "general arrangement layout",
    _MOM: "minutes of the kick-off meeting",
    _DEVIATION: "4.2.7 we differ",
}


class CoverageClient(RfqClient):
    """RfqClient with the knobs this matrix needs.

    Every response is post-processed rather than re-dispatched, so the base
    stub stays the one place that maps a schema to a kind, records the call
    and injects a whole-kind failure.
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        # --- requirements
        self.requirements_response = None      # None -> the base two clauses
        self.requirements_by_chunk = None      # one response per chunk
        self.fail_requirements_chunk = None    # 1-based chunk to raise on
        self._requirements_calls = 0
        # --- facts. The vendor answers the parameter the requirement asks
        # about unless a row deliberately makes it unanswerable.
        self.fact_parameter = None             # None -> requirement_parameter
        self.fact_value = 70
        self.fact_unit = "ppm"
        self.fail_facts_containing = None      # substring of the document text
        self.omit_facts_containing = None      # substring of the document text

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        fields = output_schema.model_fields
        response = super().classify_structure(prompt, output_schema,
                                              context_text, images)
        if "requirements" in fields:
            self._requirements_calls += 1
            if self._requirements_calls == self.fail_requirements_chunk:
                raise RuntimeError("requirements provider unavailable")
            if self.requirements_by_chunk is not None:
                index = min(self._requirements_calls - 1,
                            len(self.requirements_by_chunk) - 1)
                return _copy(self.requirements_by_chunk[index])
            if self.requirements_response is not None:
                return _copy(self.requirements_response)
        elif "facts" in fields:
            if self._matches(self.fail_facts_containing, context_text):
                raise RuntimeError("facts provider unavailable")
            if self._matches(self.omit_facts_containing, context_text):
                return {}                      # a tool call omitting the array
            for entry in response["facts"]:
                entry["parameter"] = self.fact_parameter or self.requirement_parameter
                entry["value"] = self.fact_value
                entry["unit"] = self.fact_unit
        return response

    @staticmethod
    def _matches(marker, context_text) -> bool:
        return bool(marker) and marker in context_text


class BoxedQuotationClient(CoverageClient):
    """Answers the way the real provider client does: every payload goes
    through `coerce_structured`, the repair layer `AnthropicClient` applies to
    a raw tool_use input, and the quotation comes back with `base_price` boxed
    as `{"value": N}` - a shape observed live on real quotations."""

    def classify_structure(self, prompt, output_schema, context_text, images=None):
        response = super().classify_structure(prompt, output_schema,
                                              context_text, images)
        if "base_price" in output_schema.model_fields:
            response = {"currency": "AED", "base_price": {"value": 1110836}}
        return coerce_structured(response, output_schema)


def _copy(response: dict) -> dict:
    """A fresh copy per call: the pipeline reads these dicts, but a row that
    hands the same object to three chunks must not see one chunk's edits."""
    return {key: [dict(entry) for entry in value] if isinstance(value, list) else value
            for key, value in response.items()}


def _write_rfq(tmp_path, name, body="4.2.7 H2S at least 50 ppm"):
    # Padded, like every other synthetic fixture here that is meant to reach
    # an extractor: a body under MIN_EXTRACTABLE_CHARS would exercise the
    # no-readable-text guard instead of the logic the row is about.
    (tmp_path / "p" / "requirements" / name).write_text(body + _PAD, encoding="utf-8")


def _write_vendor(tmp_path, vendor, name, body):
    vdir = tmp_path / "p" / "vendors" / vendor
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / name).write_text(body + _PAD, encoding="utf-8")


def _wide_project(tmp_path) -> str:
    """The MR, plus one KERUI document per doc_class in VENDOR_ROUTE."""
    root = _project(tmp_path)
    for name, body in _WIDE_DOCUMENTS.items():
        _write_vendor(tmp_path, "KERUI", name, body)
    return root


def _set_vendors(root, vendors):
    project = load_project(root, "p")
    project.vendors = list(vendors)
    save_project(root, project)


def _docs(root):
    return snapshots.load_documents(root, "p")


def _doc(root, suffix):
    return next(d for d in _docs(root) if d.path.endswith(suffix))


def _facts(root, vendor="KERUI"):
    return snapshots.load_facts(root, "p", vendor)


def _reqs(root):
    return snapshots.load_requirements(root, "p")


def _routed_to_tech(root, vendor="KERUI"):
    return [d for d in _docs(root)
            if d.vendor == vendor and VENDOR_ROUTE.get(d.doc_class) == "datasheet"]


def _last_run_started(root):
    return [e for e in events.read_events(root, "p")
            if e.action == "run.started"][-1].at


def _long_mr(lines: int, marker: str = "body text here") -> str:
    """A body long enough to chunk. 700 lines of this shape is the fixture
    tests/test_extract_requirements.py already uses for three chunks under the
    shipped 8000-char budget."""
    return "\n".join(f"clause {i} {marker}" for i in range(lines))


def _clause(ref: str, text: str) -> dict:
    return {"clause_ref": ref, "text": text, "category": "technical",
            "checkability": "judgement"}


def _unclassified_under(marker: str):
    """A classifier that declines one document and answers normally for the
    rest. `unclassified` is the one doc_class VENDOR_ROUTE has no entry for -
    `classify_document` degrades everything it cannot decide to `other`, which
    Task 2 routed - so this is how a document stops being routable at all."""
    def classify(path, client, text_head=""):
        if marker in path.replace(os.sep, "/"):
            return "unclassified", "llm"
        return classify_document(path, client, text_head)
    return classify


# --- INV-B: the stored facts are exactly those of the live, routed documents

def test_row1_a_newer_revision_takes_over_the_facts_of_the_one_it_supersedes(tmp_path):
    # INV-B
    root = _project(tmp_path)
    run_ingestion(root, "p", CoverageClient())
    superseded = _doc(root, _DATASHEET)
    assert {f["doc_id"] for f in _facts(root).technical} == {superseded.doc_id}

    _write_vendor(tmp_path, "KERUI", _DATASHEET_REV1, "H2S up to 65 ppm")
    second = CoverageClient()
    second.fact_value = 65
    run_ingestion(root, "p", second)

    old, new = _doc(root, _DATASHEET), _doc(root, _DATASHEET_REV1)
    assert old.superseded_by == new.doc_id
    assert old.extraction_status == "skipped" and "superseded by" in old.notes
    technical = _facts(root).technical
    assert {f["doc_id"] for f in technical} == {new.doc_id}
    assert [f["value"] for f in technical] == [65]


def test_row2_deleting_a_document_removes_exactly_its_own_facts(tmp_path):
    # INV-B
    root = _project(tmp_path)
    _write_vendor(tmp_path, "KERUI", "02 DataSheet Alternator.txt", "H2S up to 80 ppm")
    run_ingestion(root, "p", CoverageClient())
    deleted = _doc(root, "02 DataSheet Alternator.txt")
    survivor = _doc(root, _DATASHEET)
    before = [f for f in _facts(root).technical if f["doc_id"] == survivor.doc_id]
    assert {f["doc_id"] for f in _facts(root).technical} == {deleted.doc_id,
                                                            survivor.doc_id}

    os.remove(os.path.join(root, "p", "vendors", "KERUI", "02 DataSheet Alternator.txt"))
    run_ingestion(root, "p", CoverageClient())

    technical = _facts(root).technical
    assert all(f["doc_id"] != deleted.doc_id for f in technical)
    assert technical == before          # the survivor's fact, byte for byte
    assert "facts.pruned" in [e.action for e in events.read_events(root, "p")]


def test_row3_a_sibling_revision_arriving_later_keeps_both_lineage_pointers(tmp_path):
    # INV-B. The revision is a cache hit on run 2, and the cache carries the
    # extraction forward onto the *fresh* record: appending the stored one
    # instead would throw away the lineage this run resolved.
    root = _project(tmp_path)
    os.remove(os.path.join(root, "p", "vendors", "KERUI", _DATASHEET))
    _write_vendor(tmp_path, "KERUI", _DATASHEET_REV1, "H2S up to 65 ppm")
    run_ingestion(root, "p", CoverageClient())
    revision = _doc(root, _DATASHEET_REV1)
    assert (revision.supersedes, revision.superseded_by) == (None, None)

    _write_vendor(tmp_path, "KERUI", _DATASHEET, _DATASHEET_BODY)   # a second upload
    second = CoverageClient()
    run_ingestion(root, "p", second)

    base, revision = _doc(root, _DATASHEET), _doc(root, _DATASHEET_REV1)
    assert revision.supersedes == base.doc_id
    assert base.superseded_by == revision.doc_id
    assert revision.extraction_status == "ok"       # carried from the cache
    assert second.calls.count("facts") == 0         # and it cost nothing
    assert {f["doc_id"] for f in _facts(root).technical} == {revision.doc_id}


def test_row5_bumping_the_tech_prompt_reextracts_every_routed_document_once(
        tmp_path, monkeypatch):
    # INV-B, across all eight routed classes
    root = _wide_project(tmp_path)
    first = CoverageClient()
    run_ingestion(root, "p", first)

    vendor_docs = [d for d in _docs(root) if d.vendor]
    assert {d.doc_class for d in vendor_docs} == set(VENDOR_ROUTE), \
        "the fixture no longer holds one document per routed class"
    routed = _routed_to_tech(root)
    assert len(routed) == 6                 # everything but quotation/deviation
    assert first.calls.count("facts") == 6
    assert {f["doc_id"] for f in _facts(root).technical} == {d.doc_id for d in routed}

    monkeypatch.setitem(pipeline.PROMPT_VERSION_BY_CLASS, "datasheet", "tech_facts_v2")
    second = CoverageClient()
    run_ingestion(root, "p", second)

    assert second.calls.count("facts") == 6, "not every routed document was re-asked"
    assert second.calls.count("bid") == 0
    assert second.calls.count("deviations") == 0
    assert all(d.prompt_version == "tech_facts_v2" for d in _routed_to_tech(root))

    third = CoverageClient()
    run_ingestion(root, "p", third)
    assert third.calls.count("facts") == 0, "the bumped version did not persist"


def test_row5_the_shipped_tech_prompt_version_is_what_is_stored(tmp_path):
    # the other half of row 5: the bump above is only meaningful against a
    # recorded baseline, so pin what an unbumped run stores.
    root = _project(tmp_path)
    run_ingestion(root, "p", CoverageClient())
    assert _doc(root, _DATASHEET).prompt_version == TECH_PROMPT_VERSION == "tech_facts_v1"


@pytest.mark.parametrize("tier", ["auto", "stated"])
def test_row6_editing_a_requirement_parameter_reextracts_every_datasheet_once(
        tmp_path, tier):
    # INV-B, INV-D. The vocabulary is an input to the tech prompt, so a
    # parameter edit is a cache-key change - and a `stated` parameter is part
    # of that vocabulary exactly as much as an `auto` one.
    root = _wide_project(tmp_path)
    run_ingestion(root, "p", CoverageClient())
    assert all(d.vocabulary_sha == vocabulary_sha(["h2s_tolerance"])
               for d in _routed_to_tech(root))

    _write_rfq(tmp_path, _MR, "4.2.7 the parameter this clause states has changed")
    second = CoverageClient()
    if tier == "auto":
        second.requirement_parameter = "continuous_rating"
        expected = ["continuous_rating"]
    else:
        second.requirements_response = {"requirements": [
            {"clause_ref": "4.2.7", "text": "H2S at least 50 ppm",
             "category": "technical", "checkability": "auto",
             "parameter": "h2s_tolerance", "operator": ">=",
             "value": 50, "unit": "ppm"},
            {"clause_ref": "2.6", "text": "Generator insulation Class F",
             "category": "technical", "checkability": "stated",
             "parameter": "generator_insulation_class", "value": "Class F"},
        ]}
        expected = ["generator_insulation_class", "h2s_tolerance"]
    run_ingestion(root, "p", second)

    assert second.calls.count("facts") == 6, "not every datasheet was re-asked"
    assert all(d.vocabulary_sha == vocabulary_sha(expected)
               for d in _routed_to_tech(root))
    assert vocabulary_sha(expected) != vocabulary_sha(["h2s_tolerance"])

    third = CoverageClient()
    third.requirement_parameter = second.requirement_parameter
    third.requirements_response = second.requirements_response
    run_ingestion(root, "p", third)
    assert third.calls.count("facts") == 0, "the vocabulary bump did not persist"


def test_row7_a_transient_tech_failure_is_not_cached_as_an_answer(tmp_path):
    # INV-B
    root = _project(tmp_path)
    run_ingestion(root, "p", CoverageClient(fail_on=("facts",)))
    failed = _doc(root, _DATASHEET)
    assert failed.extraction_status == "failed"
    assert "provider unavailable" in failed.notes
    assert _facts(root).technical == []

    second = CoverageClient()
    run_ingestion(root, "p", second)

    assert second.calls.count("facts") == 1, "the failure was cached as an answer"
    doc = _doc(root, _DATASHEET)
    assert (doc.extraction_status, doc.notes) == ("ok", None)
    assert [f["parameter"] for f in _facts(root).technical] == ["h2s_tolerance"]


def test_row8_a_permanently_failing_document_keeps_its_reason_every_run(tmp_path):
    # INV-B. One document fails; the facts its five neighbours stored must
    # survive both failures, and each run must record why it failed.
    root = _wide_project(tmp_path)
    first = CoverageClient()
    first.fail_facts_containing = _DATASHEET_BODY
    run_ingestion(root, "p", first)

    failing = _doc(root, _DATASHEET)
    after_one = _facts(root).technical
    assert failing.extraction_status == "failed"
    assert "provider unavailable" in failing.notes
    assert len(after_one) == 5
    assert all(f["doc_id"] != failing.doc_id for f in after_one)

    second = CoverageClient()
    second.fail_facts_containing = _DATASHEET_BODY
    run_ingestion(root, "p", second)

    again = _doc(root, _DATASHEET)
    assert second.calls.count("facts") == 1      # only the failure is re-asked
    assert again.extraction_status == "failed"
    assert "provider unavailable" in again.notes
    assert _facts(root).technical == after_one


def test_row9_a_vendor_whose_only_document_stops_routing_keeps_its_column(
        tmp_path, monkeypatch):
    # INV-B, INV-E: the vendor is silent, not absent, and silence is
    # `unanswered` - never `fail`, and never a vanished column.
    root = _project(tmp_path)
    _write_vendor(tmp_path, "MKON", _DATASHEET, "H2S up to 65 ppm")
    _set_vendors(root, ["KERUI", "MKON"])
    run_ingestion(root, "p", CoverageClient())
    assert any(r.vendor == "MKON" and r.verdict == "pass"
               for r in snapshots.load_compliance(root, "p"))

    # The re-upload is what re-opens classification: an unchanged document
    # keeps its cached class, so a row that only patched the classifier would
    # be asserting over run 1's answer.
    _write_vendor(tmp_path, "MKON", _DATASHEET, "a body this build cannot place")
    monkeypatch.setattr(pipeline, "classify_document", _unclassified_under("/MKON/"))
    run_ingestion(root, "p", CoverageClient())

    results = snapshots.load_compliance(root, "p")
    assert {r.vendor for r in results} == {"KERUI", "MKON"}
    mkon = [r for r in results if r.vendor == "MKON"]
    assert mkon and all(r.verdict in ("unanswered", "review") for r in mkon)
    assert any(r.verdict == "unanswered" for r in mkon)
    assert all(r.fact_id is None for r in mkon)
    assert _facts(root, "MKON").technical == []


def test_row10_a_failed_reextraction_keeps_the_facts_the_good_run_stored(tmp_path):
    # INV-B
    root = _project(tmp_path)
    run_ingestion(root, "p", CoverageClient())
    before = _facts(root).technical
    assert before

    _write_vendor(tmp_path, "KERUI", _DATASHEET, "H2S up to 40 ppm")
    run_ingestion(root, "p", CoverageClient(fail_on=("facts",)))

    doc = _doc(root, _DATASHEET)
    assert doc.extraction_status == "failed" and doc.notes
    assert _facts(root).technical == before


def test_row11_a_response_omitting_facts_is_ok_with_no_facts(tmp_path):
    # INV-B: "the model returned nothing" is an empty extraction, not a failed
    # one. Calling it failed would make the document a permanent per-run charge.
    root = _project(tmp_path)
    _write_vendor(tmp_path, "KERUI", "02 DataSheet Alternator.txt", "H2S up to 80 ppm")
    run_ingestion(root, "p", CoverageClient())
    neighbour = _doc(root, "02 DataSheet Alternator.txt")
    kept = [f for f in _facts(root).technical if f["doc_id"] == neighbour.doc_id]
    assert kept

    _write_vendor(tmp_path, "KERUI", _DATASHEET, "this datasheet states nothing checkable")
    second = CoverageClient()
    second.omit_facts_containing = "states nothing checkable"
    run_ingestion(root, "p", second)

    doc = _doc(root, _DATASHEET)
    assert doc.extraction_status == "ok"        # never `failed`
    assert doc.extracted_at and doc.prompt_version == TECH_PROMPT_VERSION
    assert _facts(root).technical == kept       # its own facts are gone, no others
    # `notes` stays None on an `ok` extraction, per the amended row 11 - the
    # same convention tests/test_extract_tech.py::
    # test_a_response_omitting_the_facts_key_is_ok_with_no_facts pins on the
    # extractor, and phase 3's row 9 pins on the RFQ side.
    assert doc.notes is None

    third = CoverageClient()
    run_ingestion(root, "p", third)
    assert third.calls.count("facts") == 0, "an empty extraction was retried"


def test_row13_a_document_that_stops_routing_loses_its_facts(tmp_path, monkeypatch):
    # INV-B. Task 2 widened routing so an `other` document contributes facts;
    # that makes reclassification-to-unroutable a live orphaning surface, not
    # a theoretical one.
    root = _project(tmp_path)
    _write_vendor(tmp_path, "KERUI", _SPARES, "two years of operating spares")
    run_ingestion(root, "p", CoverageClient())
    spares = _doc(root, _SPARES)
    assert (spares.doc_class, spares.extraction_status) == ("other", "ok")
    assert spares.doc_id in {f["doc_id"] for f in _facts(root).technical}

    # Re-uploaded, so classification is re-opened: an unchanged document keeps
    # the class run 1 cached for it.
    _write_vendor(tmp_path, "KERUI", _SPARES, "a body this build cannot place")
    monkeypatch.setattr(pipeline, "classify_document", _unclassified_under("Spares"))
    run_ingestion(root, "p", CoverageClient())

    after = _doc(root, _SPARES)
    assert after.doc_class == "unclassified"
    assert after.extraction_status == "skipped" and "not extracted" in after.notes
    technical = _facts(root).technical
    assert all(f["doc_id"] != spares.doc_id for f in technical)
    assert technical, "only the unroutable document's facts should have gone"


def test_row9_a_document_inferred_as_the_quotation_gives_up_its_technical_facts(tmp_path):
    # INV-B, with no monkeypatching anywhere: the naturally-reachable form of
    # the defect row 9 found. `_EXTRACTABLE` still names three routes, so the
    # no-quotation fallback pool holds every document Task 2 widened routing
    # to - `other` among them. A vendor whose quotation is withdrawn therefore
    # has one of its datasheet-routed documents promoted to the quotation
    # extractor, and the facts that document stored on run 1 are refreshed by
    # nothing from then on. pipeline.py's own comment on the fallback pool -
    # "stealing a datasheet to guess a price would lose its technical facts" -
    # is this path, stated and until now unasserted.
    root = _project(tmp_path)
    _write_vendor(tmp_path, "KERUI", _SPARES, "two years of operating spares")
    run_ingestion(root, "p", CoverageClient())
    spares, datasheet = _doc(root, _SPARES), _doc(root, _DATASHEET)
    kept = [f for f in _facts(root).technical if f["doc_id"] == datasheet.doc_id]
    assert spares.doc_id in {f["doc_id"] for f in _facts(root).technical}
    assert kept and _facts(root).quotation_doc_id == _doc(root, _QUOTATION).doc_id

    os.remove(os.path.join(root, "p", "vendors", "KERUI", _QUOTATION))
    run_ingestion(root, "p", CoverageClient())

    promoted = _doc(root, _SPARES)
    assert promoted.doc_class == "other"        # routing never rewrites the class
    assert promoted.extraction_status == "ok"
    assert promoted.prompt_version == "bid_extract_v1", "not read as the quotation"
    assert "vendor.quotation_inferred" in [e.action for e in events.read_events(root, "p")]

    facts = _facts(root)
    assert facts.quotation_doc_id == promoted.doc_id
    assert facts.commercial["base_price"] == 1000.0
    # the promoted document's facts are pruned: no extractor maintains them any
    # more, and its stored `verbatim` quotes text nothing re-reads
    assert all(f["doc_id"] != promoted.doc_id for f in facts.technical)
    assert facts.technical == kept              # and only that document's went


# --- INV-A: an unreadable document is `failed`, and `failed` counts as live

def test_row12_a_text_layer_disappearing_does_not_delete_the_facts(tmp_path):
    # INV-A: the `pdftotext`-missing scenario. The document is still there and
    # still routed; this run simply reads nothing out of it.
    root = _project(tmp_path)
    run_ingestion(root, "p", CoverageClient())
    before = _facts(root).technical
    assert before

    (tmp_path / "p" / "vendors" / "KERUI" / _DATASHEET).write_text("x", encoding="utf-8")
    second = CoverageClient()
    run_ingestion(root, "p", second)

    doc = _doc(root, _DATASHEET)
    assert doc.extraction_status == "failed"       # `failed`, never `skipped`
    assert "no readable text" in doc.notes
    assert doc.text_source == "text:1chars"
    assert second.calls.count("facts") == 0, "the guard fires before the model"
    # `failed` counts as live in _prune_orphan_facts, so the good facts survive
    assert _facts(root).technical == before
    for record in _docs(root):
        if record.vendor and record.extraction_status in ("ok", "failed"):
            assert record.text_source, f"{record.path} has no text_source"


# --- INV-C: requirements are exactly those of the authoritative revision

def test_row4_bumping_the_requirements_prompt_reextracts_the_spec_once(
        tmp_path, monkeypatch):
    # INV-C
    root = _project(tmp_path)
    run_ingestion(root, "p", CoverageClient())
    assert REQUIREMENTS_PROMPT_VERSION == "requirements_v4"
    assert _doc(root, _MR).prompt_version == "requirements_v4"

    unchanged = CoverageClient()
    run_ingestion(root, "p", unchanged)
    assert unchanged.calls.count("requirements") == 0
    assert _doc(root, _MR).prompt_version == "requirements_v4", "the version did not persist"

    monkeypatch.setitem(pipeline.RFQ_PROMPT_VERSION_BY_CLASS, "spec", "requirements_v5")
    bumped = CoverageClient()
    run_ingestion(root, "p", bumped)
    assert bumped.calls.count("requirements") == 1
    assert _doc(root, _MR).prompt_version == "requirements_v5"
    assert _reqs(root).requirements

    settled = CoverageClient()
    run_ingestion(root, "p", settled)
    assert settled.calls.count("requirements") == 0, "the bump did not persist"


def test_row14_a_failing_chunk_leaves_the_stored_requirement_set_whole(tmp_path):
    # INV-C. Three chunks succeed on run 1; the second fails on run 2. A
    # partial merge would be stored as a complete success, deleting the
    # clauses the failed chunks carried with nothing recording why.
    root = _project(tmp_path)
    _write_rfq(tmp_path, _MR, _long_mr(700))
    first = CoverageClient()
    first.requirements_by_chunk = [
        {"requirements": [_clause("1.1", "first clause body")]},
        {"requirements": [_clause("2.2", "second clause body")]},
        {"requirements": [_clause("3.3", "third clause body")]},
    ]
    run_ingestion(root, "p", first)
    assert first.calls.count("requirements") == 3, "the MR did not chunk"
    before = _reqs(root).model_dump()
    assert [r.clause_ref for r in _reqs(root).requirements] == ["1.1", "2.2", "3.3"]

    _write_rfq(tmp_path, _MR, _long_mr(700, "body text here, revised"))
    second = CoverageClient()
    second.requirements_by_chunk = [
        {"requirements": [_clause("9.1", "a clause that must never be stored")]},
    ]
    second.fail_requirements_chunk = 2
    run_ingestion(root, "p", second)

    assert second.calls.count("requirements") == 2   # stopped at the failure
    assert _reqs(root).model_dump() == before        # nothing partial, nothing lost
    doc = _doc(root, _MR)
    assert doc.extraction_status == "failed"
    assert "provider unavailable" in doc.notes


def test_row15_an_mr_growing_past_one_chunk_keeps_every_clause_and_its_ids(tmp_path):
    # INV-C. Growing the MR must add clauses, not renumber the ones already
    # stored: a req_id that shifts orphans every override and verdict keyed on it.
    root = _project(tmp_path)
    first = CoverageClient()
    run_ingestion(root, "p", first)
    assert first.calls.count("requirements") == 1     # one chunk
    kept = next(r for r in _reqs(root).requirements if r.clause_ref == "4.2.7")

    _write_rfq(tmp_path, _MR, _long_mr(700))
    second = CoverageClient()
    second.requirements_by_chunk = [
        {"requirements": [
            {"clause_ref": "4.2.7", "text": "H2S at least 50 ppm",
             "category": "technical", "checkability": "auto",
             "parameter": "h2s_tolerance", "operator": ">=",
             "value": 50, "unit": "ppm"},
            _clause("5.1", "fifth clause body")]},
        {"requirements": [_clause("6.1", "sixth clause body")]},
        {"requirements": [_clause("7.1", "seventh clause body")]},
    ]
    run_ingestion(root, "p", second)

    assert second.calls.count("requirements") == 3
    stored = _reqs(root).requirements
    assert [r.clause_ref for r in stored] == ["4.2.7", "5.1", "6.1", "7.1"]
    assert next(r for r in stored if r.clause_ref == "4.2.7").req_id == kept.req_id


def test_row16_a_superseded_spec_revision_hands_over_its_requirements(tmp_path):
    # INV-C
    root = _project(tmp_path)
    run_ingestion(root, "p", CoverageClient())
    assert {r.source_doc_id for r in _reqs(root).requirements} == {_doc(root, _MR).doc_id}

    _write_rfq(tmp_path, _MR_REV1)
    run_ingestion(root, "p", CoverageClient())

    old, new = _doc(root, _MR), _doc(root, _MR_REV1)
    assert old.superseded_by == new.doc_id
    assert old.extraction_status == "skipped" and "superseded by" in old.notes
    sources = {r.source_doc_id for r in _reqs(root).requirements}
    assert sources == {new.doc_id}
    assert [r.clause_ref for r in _reqs(root).requirements] == ["4.2.7", "9.1"]


def test_row17_withdrawing_a_mom_restores_the_clause_body_exactly(tmp_path):
    # INV-C
    root = _project(tmp_path)
    _write_rfq(tmp_path, _MOM, "Clause 4.2.7 revised to 60 ppm")
    run_ingestion(root, "p", CoverageClient())
    amended = next(r for r in _reqs(root).requirements if r.clause_ref == "4.2.7")
    base_body = dict(amended.base_body)
    assert amended.value == 60 and amended.amended_by
    assert "[amended]" in amended.text

    os.remove(os.path.join(root, "p", "requirements", _MOM))
    run_ingestion(root, "p", CoverageClient())

    reqset = _reqs(root)
    assert reqset.amendments == []
    reverted = next(r for r in reqset.requirements if r.clause_ref == "4.2.7")
    assert {k: getattr(reverted, k) for k in base_body} == base_body
    assert (reverted.amended_by, reverted.base_body) == (None, None)


def test_row18_an_override_on_a_deleted_requirement_is_kept_and_flagged(tmp_path):
    # INV-C: pruning is not licence to drop a human's judgement silently.
    root = _project(tmp_path)
    _write_rfq(tmp_path, _SECOND_SPEC)
    run_ingestion(root, "p", CoverageClient())

    reqset = _reqs(root)
    doomed = next(r for r in reqset.requirements
                  if r.source_doc_id == _doc(root, _MR).doc_id
                  and r.checkability == "auto")
    reqset.overrides = [Override(field_path=f"requirements[{doomed.req_id}].value",
                                 value=55.0, extracted_value=doomed.value,
                                 author="rj", at=_NOW, reason="clarified at the meeting")]
    snapshots.save_requirements(root, "p", reqset)

    os.remove(os.path.join(root, "p", "requirements", _MR))
    run_ingestion(root, "p", CoverageClient())

    after = _reqs(root)
    [override] = after.overrides
    assert override.conflict is True and override.value == 55.0
    assert doomed.req_id not in {r.req_id for r in after.requirements}
    assert after.requirements and all(r.value != 55.0 for r in after.requirements)


# --- INV-D / INV-E: tiers stay well-formed, and verdicts cite what still exists

def test_row19_a_requirement_flipping_to_stated_leaves_no_half_stated_bound(tmp_path):
    # INV-D, INV-E
    root = _project(tmp_path)
    run_ingestion(root, "p", CoverageClient())
    auto = next(r for r in _reqs(root).requirements if r.checkability == "auto")
    assert (auto.operator, auto.unit) == (">=", "ppm")
    assert any(c.fact_id for c in snapshots.load_compliance(root, "p"))

    _write_rfq(tmp_path, _MR, "2.6 Generator insulation Class F")
    second = CoverageClient()
    second.requirements_response = {"requirements": [{
        "clause_ref": "2.6", "text": "Generator insulation Class F",
        "category": "technical", "checkability": "stated",
        "parameter": "generator_insulation_class",
        # the model still volunteers a bound; a `stated` row must not keep it
        "operator": ">=", "value": "Class F", "unit": "ppm"}]}
    second.fact_parameter = "generator_insulation_class"
    second.fact_value = "Class F / Class B rise"
    second.fact_unit = None
    run_ingestion(root, "p", second)

    [stated] = _reqs(root).requirements
    assert stated.checkability == "stated"
    assert stated.parameter == "generator_insulation_class"
    assert (stated.operator, stated.unit) == (None, None)
    assert not isinstance(stated.value, (list, dict))
    for requirement in _reqs(root).requirements:
        assert not (requirement.operator is not None and requirement.unit is None), \
            "an operator with no unit is a half-stated bound"
        if requirement.checkability == "auto":
            assert (requirement.parameter and requirement.operator
                    and requirement.value is not None and requirement.unit is not None)

    stored_facts = {f["fact_id"] for f in _facts(root).technical}
    live = {r.req_id for r in _reqs(root).requirements}
    results = snapshots.load_compliance(root, "p")
    assert results and all(r.req_id in live for r in results)
    assert all(r.fact_id is None or r.fact_id in stored_facts for r in results), \
        "a verdict outlived the fact it named"
    assert all(r.evaluated_at >= _last_run_started(root) for r in results)
    [cell] = results
    assert (cell.verdict, cell.fact_id) == ("pass", next(iter(stored_facts)))


def test_row19_a_stated_requirement_with_no_matching_fact_is_unanswered(tmp_path):
    # INV-E, the other half: silence is never `fail`.
    root = _project(tmp_path)
    run_ingestion(root, "p", CoverageClient())

    _write_rfq(tmp_path, _MR, "2.6 Generator insulation Class F")
    second = CoverageClient()
    second.requirements_response = {"requirements": [{
        "clause_ref": "2.6", "text": "Generator insulation Class F",
        "category": "technical", "checkability": "stated",
        "parameter": "generator_insulation_class", "value": "Class F"}]}
    second.fact_parameter = "continuous_rating"        # the vendor stays silent
    run_ingestion(root, "p", second)

    [cell] = snapshots.load_compliance(root, "p")
    assert cell.verdict == "unanswered" and cell.fact_id is None
    assert cell.req_id == _reqs(root).requirements[0].req_id


# --- regression: a quotation whose numbers arrive boxed

def test_row20_a_boxed_base_price_still_extracts_cleanly(tmp_path):
    root = _project(tmp_path)
    run_ingestion(root, "p", BoxedQuotationClient())

    quotation = _doc(root, _QUOTATION)
    assert quotation.extraction_status == "ok" and quotation.notes is None
    assert _facts(root).commercial["base_price"] == 1110836.0
    assert _facts(root).commercial["currency"] == "AED"

    second = BoxedQuotationClient()
    run_ingestion(root, "p", second)
    assert second.calls.count("bid") == 0               # cached, not re-asked
    assert _facts(root).commercial["base_price"] == 1110836.0
    assert _facts(root).normalized["normalized_total"]
