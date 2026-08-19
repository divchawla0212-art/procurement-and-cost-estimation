"""Whether a bid is admissible against what the enquiry asked for — BD-7.

Pure module, tested directly, plus one thin route at the HTTP boundary.

Category (c) — the compliance / deviation sheet — is the load-bearing rule
here. It is not a fixed member of `MANDATORY`: it becomes required the moment
anything has been issued at all, and *what satisfies it* depends on whether
the issued package itself carried a compliance sheet. Getting this wrong the
"optional unless a compliance sheet was issued" way would let an enquiry that
shipped one accept a bid that ignored it — `workflow/eligibility.py`'s module
docstring says so at length.

`missing` lists **every** absent mandatory category, not the first found —
tested directly below, because a bidder told about one gap at a time is made
to do two rounds for no reason.
"""
import hashlib

from fastapi.testclient import TestClient

from tests.auth_helpers import signed_in_admin
from workflow import persistence
from workflow.eligibility import EligibilityVerdict, assess
from workflow.models.rfq import ChecklistItem, ShortlistEntry
from workflow.models.rfq_document import EligibilityCategory, RfqDocument

C = EligibilityCategory


# -- helpers ------------------------------------------------------------------


def a_document(**overrides) -> RfqDocument:
    defaults = dict(
        rfq_id="rfq_1",
        filename="doc.pdf",
        rel_path="doc.pdf",
        sha256=hashlib.sha256(b"x").hexdigest(),
        size_bytes=1,
        uploaded_by="buyer@example.com",
        uploaded_at="2026-08-15T09:00:00+00:00",
        submitted_by_vendor_id=None,
    )
    return RfqDocument(**{**defaults, **overrides})


def issued(category: EligibilityCategory) -> RfqDocument:
    """A document the contractor put in the enquiry package."""
    return a_document(category=category, submitted_by_vendor_id=None)


def submitted(category: EligibilityCategory, vendor_id: str = "bdr_1") -> RfqDocument:
    """A document one bidder returned."""
    return a_document(category=category, submitted_by_vendor_id=vendor_id)


THE_THREE = (C.TECHNICAL_OFFER, C.COMMERCIAL_OFFER, C.TBE_SHEET)


# -- assess(): the happy path --------------------------------------------------


def test_all_three_mandatories_present_and_nothing_issued_is_admissible():
    result = assess(issued=[], submitted=[submitted(c) for c in THE_THREE])
    assert result == EligibilityVerdict(admissible=True, missing=[], reason=None)


# -- assess(): each mandatory missing individually -----------------------------


def test_a_missing_technical_offer_is_named():
    result = assess(issued=[], submitted=[submitted(C.COMMERCIAL_OFFER), submitted(C.TBE_SHEET)])
    assert result.admissible is False
    assert result.missing == [C.TECHNICAL_OFFER]
    assert result.reason == "The bid is missing a technical offer."


def test_a_missing_commercial_offer_is_named():
    result = assess(issued=[], submitted=[submitted(C.TECHNICAL_OFFER), submitted(C.TBE_SHEET)])
    assert result.admissible is False
    assert result.missing == [C.COMMERCIAL_OFFER]
    assert result.reason == "The bid is missing a commercial offer."


def test_a_missing_tbe_sheet_is_named():
    result = assess(issued=[], submitted=[submitted(C.TECHNICAL_OFFER), submitted(C.COMMERCIAL_OFFER)])
    assert result.admissible is False
    assert result.missing == [C.TBE_SHEET]
    assert result.reason == "The bid is missing a technical bid evaluation sheet."


def test_two_missing_mandatories_are_both_named():
    result = assess(issued=[], submitted=[submitted(C.TBE_SHEET)])
    assert result.admissible is False
    # Declared enum order, not the order the two happened to be checked.
    assert result.missing == [C.TECHNICAL_OFFER, C.COMMERCIAL_OFFER]
    assert result.reason == "The bid is missing a technical offer and a commercial offer."


def test_all_three_missing_are_named_with_an_oxford_comma():
    result = assess(issued=[], submitted=[])
    assert result.admissible is False
    assert result.missing == [C.TECHNICAL_OFFER, C.COMMERCIAL_OFFER, C.TBE_SHEET]
    assert result.reason == (
        "The bid is missing a technical offer, a commercial offer, "
        "and a technical bid evaluation sheet."
    )


# -- assess(): category (c) is a rule, not a flag ------------------------------


def test_a_compliance_sheet_issued_demands_the_sheet_back():
    result = assess(
        issued=[issued(C.COMPLIANCE_SHEET)],
        submitted=[submitted(c) for c in THE_THREE],
    )
    assert result.admissible is False
    assert result.missing == [C.COMPLIANCE_SHEET]
    assert result.reason == "The bid is missing a returned compliance sheet."


def test_no_compliance_sheet_issued_still_demands_a_deviation_list():
    # Something was issued — an enquiry exists — but not a compliance sheet.
    result = assess(
        issued=[issued(C.TECHNICAL_DATASHEET)],
        submitted=[submitted(c) for c in THE_THREE],
    )
    assert result.admissible is False
    assert result.missing == [C.COMPLIANCE_SHEET]
    assert result.reason == "The bid is missing a deviation list."


def test_a_returned_compliance_sheet_satisfies_the_category_when_one_was_issued():
    result = assess(
        issued=[issued(C.COMPLIANCE_SHEET)],
        submitted=[submitted(c) for c in THE_THREE] + [submitted(C.COMPLIANCE_SHEET)],
    )
    assert result.admissible is True


def test_a_returned_compliance_sheet_satisfies_the_category_when_none_was_issued():
    # The same category value stands in for "a deviation list" here — there is
    # no tenth enum member, and this pure module cannot inspect content.
    result = assess(
        issued=[issued(C.TECHNICAL_DATASHEET)],
        submitted=[submitted(c) for c in THE_THREE] + [submitted(C.COMPLIANCE_SHEET)],
    )
    assert result.admissible is True


def test_nothing_issued_at_all_means_no_compliance_requirement():
    result = assess(issued=[], submitted=[submitted(c) for c in THE_THREE])
    assert result.admissible is True
    assert C.COMPLIANCE_SHEET not in result.missing


# -- assess(): optional categories never block ---------------------------------


def test_optional_categories_issued_but_not_submitted_never_block():
    result = assess(
        issued=[issued(C.DRAWINGS), issued(C.CATALOGUES)],
        # (c) is required because something was issued at all — satisfied
        # here so the assertion below isolates the optional categories.
        submitted=[submitted(c) for c in THE_THREE] + [submitted(C.COMPLIANCE_SHEET)],
    )
    assert result.admissible is True
    assert result.missing == []


# -- I-F: no verdict is stored anywhere ----------------------------------------


def test_no_document_or_shortlist_entry_carries_a_verdict_field():
    """An absence assertion, so it passed the moment it was written. Verified
    by temporarily adding `verdict: EligibilityVerdict | None = None` to
    `RfqDocument` and watching this test go red, then reverting the field."""
    assert "verdict" not in RfqDocument.model_fields
    assert "verdict" not in ShortlistEntry.model_fields


# -- the route ------------------------------------------------------------------


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return signed_in_admin(TestClient(api_main.app), tmp_path)


def make_rfq(client: TestClient) -> str:
    r = client.post("/api/workflow/projects", json={
        "name": "Haliba Field Development", "code": "HAL",
        "client": "Al Dhafra Petroleum", "location": "Haliba field, UAE",
        "live_period_start": "2026-01-01", "live_period_end": "2029-12-31",
    })
    project_id = r.json()["id"]
    r = client.post(f"/api/workflow/projects/{project_id}/items", json={
        "item_type": "HV cable", "description": "11 kV, 3-core",
        "qty": 1200, "uom": "m", "discipline": "Cables",
        "estimated_value_aed": 900000,
    })
    item_id = r.json()["id"]
    r = client.post("/api/workflow/rfqs", json={
        "project_id": project_id, "item_ids": [item_id],
        "reference": "ADP-RFQ-2026-014", "package": "HV cable",
        "discipline": "Cables", "value_estimate_aed": 900000,
    })
    return r.json()["id"]


def add_document(root: str, rfq_id: str, category: EligibilityCategory, vendor_id: str | None) -> None:
    """Straight through the store, not the upload route — the upload route
    always writes `submitted_by_vendor_id=None` (see its own docstring: a
    vendor's submission arrives through a later phase's door), so a vendor
    document has to be added here the same way `test_rfq_documents.py` does."""
    with persistence.locked_update(root) as store:
        store.add_rfq_document(a_document(
            rfq_id=rfq_id,
            filename=f"{category.name.lower()}.pdf",
            rel_path=f"{category.name.lower()}.pdf",
            sha256=hashlib.sha256(f"{category.name}-{vendor_id}".encode()).hexdigest(),
            category=category,
            submitted_by_vendor_id=vendor_id,
        ))


def test_the_route_returns_an_admissible_verdict(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)
    for category in THE_THREE:
        add_document(str(tmp_path), rfq_id, category, "bdr_1")

    r = client.get(f"/api/workflow/rfqs/{rfq_id}/bidders/bdr_1/eligibility")

    assert r.status_code == 200, r.text
    assert r.json() == {"admissible": True, "missing": [], "reason": None}


def test_the_route_names_what_is_missing(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)
    add_document(str(tmp_path), rfq_id, C.TECHNICAL_OFFER, "bdr_1")

    r = client.get(f"/api/workflow/rfqs/{rfq_id}/bidders/bdr_1/eligibility")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["admissible"] is False
    assert body["missing"] == ["Commercial offer", "Technical bid evaluation sheet"]
    assert body["reason"] == "The bid is missing a commercial offer and a technical bid evaluation sheet."


def test_the_route_splits_issued_from_a_different_bidders_submission(tmp_path, monkeypatch):
    """A different bidder's own bid must not count towards this one's
    checklist, and the contractor's own issued package must not count as
    anybody's submission."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)
    add_document(str(tmp_path), rfq_id, C.TECHNICAL_OFFER, "bdr_2")  # somebody else's bid
    add_document(str(tmp_path), rfq_id, C.COMMERCIAL_OFFER, None)  # the contractor's own package

    r = client.get(f"/api/workflow/rfqs/{rfq_id}/bidders/bdr_1/eligibility")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["admissible"] is False
    # The contractor's own commercial offer counts as "something was issued",
    # so (c) is required here too — and demands a deviation list, since none
    # of what was issued was itself a compliance sheet.
    assert set(body["missing"]) == {
        "Technical offer", "Commercial offer", "Technical bid evaluation sheet",
        "Compliance / deviation sheet",
    }


def test_the_route_404s_for_an_unknown_rfq(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.get("/api/workflow/rfqs/rfq_nope/bidders/bdr_1/eligibility")
    assert r.status_code == 404


def test_an_unknown_vendor_id_reads_as_an_inadmissible_verdict_not_a_404(tmp_path, monkeypatch):
    """The controller's ruling on the ambiguous case: a bidder who submitted
    nothing is straightforwardly missing everything mandatory, so this stays
    a verdict computed at read time rather than a special-cased 404 — see
    task-7-report.md for the reasoning."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = make_rfq(client)

    r = client.get(f"/api/workflow/rfqs/{rfq_id}/bidders/bdr_ghost/eligibility")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["admissible"] is False
    assert set(body["missing"]) == {"Technical offer", "Commercial offer", "Technical bid evaluation sheet"}


def test_it_challenges_a_caller_without_a_session(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    client = TestClient(api_main.app)

    r = client.get("/api/workflow/rfqs/rfq_1/bidders/bdr_1/eligibility")

    assert r.status_code == 401


# -- custom checklist items ----------------------------------------------------
#
# The nine categories are a fixed vocabulary; a buyer may append items of their
# own to one RFQ's checklist, and mark them must-have. Those cannot ride in
# `missing`, which is typed to the enum, so they arrive in `missing_items`
# beside it — a genuinely different kind of thing (per-RFQ, not vocabulary),
# not the same thing spelled twice.
#
# A custom item is satisfied by a submitted document naming it in
# `checklist_item_id`. Without that field a must-have custom item would be
# unsatisfiable *by construction* rather than merely unsatisfied, which is the
# trap this field exists to avoid.


def an_item(label: str = "Site acceptance test plan", mandatory: bool = True) -> ChecklistItem:
    return ChecklistItem(label=label, mandatory=mandatory)


def test_a_mandatory_custom_item_nobody_submitted_blocks_the_bid():
    item = an_item()
    result = assess(
        issued=[],
        submitted=[submitted(c) for c in THE_THREE],
        extra_items=[item],
    )
    assert result.admissible is False
    assert [i.label for i in result.missing_items] == ["Site acceptance test plan"]
    assert result.reason == "The bid is missing Site acceptance test plan."


def test_an_optional_custom_item_never_blocks():
    result = assess(
        issued=[],
        submitted=[submitted(c) for c in THE_THREE],
        extra_items=[an_item(mandatory=False)],
    )
    assert result.admissible is True
    assert result.missing_items == []
    assert result.reason is None


def test_a_document_naming_the_item_satisfies_it():
    item = an_item()
    result = assess(
        issued=[],
        submitted=[submitted(c) for c in THE_THREE]
        + [a_document(submitted_by_vendor_id="bdr_1", checklist_item_id=item.id)],
        extra_items=[item],
    )
    assert result.admissible is True
    assert result.missing_items == []


def test_a_document_naming_a_different_item_does_not_satisfy_it():
    """The one that catches a satisfaction check written as `is not None`. A
    bidder who returned something for item A has not answered item B."""
    wanted, other = an_item("Spare parts list"), an_item("Welding procedure")
    result = assess(
        issued=[],
        submitted=[submitted(c) for c in THE_THREE]
        + [a_document(submitted_by_vendor_id="bdr_1", checklist_item_id=other.id)],
        extra_items=[wanted],
    )
    assert result.admissible is False
    assert [i.label for i in result.missing_items] == ["Spare parts list"]


def test_a_document_naming_an_item_does_not_satisfy_a_category():
    """`checklist_item_id` and `category` answer different questions. A file
    tagged only to a custom item leaves the technical offer still missing."""
    item = an_item()
    result = assess(
        issued=[],
        submitted=[
            submitted(C.COMMERCIAL_OFFER),
            submitted(C.TBE_SHEET),
            a_document(submitted_by_vendor_id="bdr_1", checklist_item_id=item.id),
        ],
        extra_items=[item],
    )
    assert result.missing == [C.TECHNICAL_OFFER]
    assert result.missing_items == []


def test_a_missing_category_and_a_missing_item_read_as_one_sentence():
    """Both kinds in one reason, categories first, so a bidder is told
    everything at once — the same rule `missing` already keeps for the three."""
    result = assess(
        issued=[],
        submitted=[submitted(C.COMMERCIAL_OFFER), submitted(C.TBE_SHEET)],
        extra_items=[an_item("Spare parts list")],
    )
    assert result.reason == (
        "The bid is missing a technical offer and Spare parts list."
    )


def test_extras_default_to_none_so_every_existing_caller_is_unchanged():
    result = assess(issued=[], submitted=[submitted(c) for c in THE_THREE])
    assert result.admissible is True
    assert result.missing_items == []


def test_no_verdict_is_stored_on_a_checklist_item_either():
    """The absence rule of `test_no_document_or_shortlist_entry_carries_a_verdict_field`,
    extended to the record this change adds."""
    assert "verdict" not in ChecklistItem.model_fields
    assert "satisfied" not in ChecklistItem.model_fields
