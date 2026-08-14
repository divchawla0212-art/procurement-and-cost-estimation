"""API tests for the bidder registry and the candidate view.

Like every other `/api/workflow/*` path these are absent from
`middleware.PUBLIC_PATHS`, so each test signs in first.

What these tests are *not* for: re-asserting the store's rules. The delete
guard and the override requirement are the store's, and they are tested there.
What matters here is that the route decides nothing — it maps an exception to a
status code and passes the store's own sentence through. A guard implemented
here rather than in the store would be a read outside `locked_update`, which is
the defect class CLAUDE.md records this repository shipping twice.
"""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from tests.auth_helpers import ADMIN_EMAIL, signed_in_admin


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import api.main as api_main
    monkeypatch.setattr(api_main, "ROOT", str(tmp_path))
    return signed_in_admin(TestClient(api_main.app), tmp_path)


def create_rfq(client: TestClient) -> str:
    r = client.post("/api/workflow/projects", json={
        "name": "Haliba Field Development",
        "code": "HAL",
        "client": "Al Dhafra Petroleum",
        "location": "Haliba field, UAE",
        "live_period_start": "2026-01-01",
        "live_period_end": "2029-12-31",
    })
    project_id = r.json()["id"]
    r = client.post(f"/api/workflow/projects/{project_id}/items", json={
        "item_type": "LV switchgear",
        "description": "LV switchboards and MCCs",
        "qty": 6,
        "uom": "ea",
        "discipline": "Electrical",
        "estimated_value_aed": 4000000,
    })
    item_id = r.json()["id"]
    r = client.post("/api/workflow/rfqs", json={
        "project_id": project_id,
        "item_ids": [item_id],
        "reference": "ADP-RFQ-2026-014",
        "package": "LV switchgear",
        "discipline": "Electrical",
        "value_estimate_aed": 4000000,
    })
    return r.json()["id"]


def create_bidder(client: TestClient, **overrides) -> dict:
    body = {
        "name": "Al Munara Switchgear LLC",
        "country": "United Arab Emirates",
        "trade_categories": ["Electrical"],
        "prequal_status": "Approved",
        "prequal_expires_on": "2027-03-31",
        "approved_by": ["ADNOC"],
        **overrides,
    }
    r = client.post("/api/workflow/bidders", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# -- the registry ------------------------------------------------------------


def test_the_registry_starts_empty(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.get("/api/workflow/bidders")
    assert r.status_code == 200
    assert r.json()["bidders"] == []


def test_a_created_bidder_appears_on_the_roster(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    bidder = create_bidder(client)

    roster = client.get("/api/workflow/bidders").json()["bidders"]
    assert [b["id"] for b in roster] == [bidder["id"]]
    assert roster[0]["effective_prequal"] == "Approved"
    # Counted here so no screen re-derives it from a list it only partly holds
    # — the same reasoning as `list_projects`' item and RFQ counts.
    assert roster[0]["invited_count"] == 0


def test_the_roster_reports_a_lapsed_approval_as_expired(tmp_path, monkeypatch):
    """The stored status is still "Approved". Only the derived one moves, which
    is the whole reason it is derived."""
    client = _client(tmp_path, monkeypatch)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    create_bidder(client, prequal_expires_on=yesterday)

    roster = client.get("/api/workflow/bidders").json()["bidders"]
    assert roster[0]["prequal_status"] == "Approved"
    assert roster[0]["effective_prequal"] == "Expired"


def test_one_bidder_carries_the_rfqs_that_invited_them(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    bidder = create_bidder(client)
    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": bidder["id"]})

    r = client.get(f"/api/workflow/bidders/{bidder['id']}")
    assert r.status_code == 200
    assert r.json()["invited_by"] == ["ADP-RFQ-2026-014"]


def test_an_unknown_bidder_is_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/workflow/bidders/bdr_nope").status_code == 404


def test_a_patch_is_partial(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    bidder = create_bidder(client, notes="Preferred on HAL")

    r = client.patch(
        f"/api/workflow/bidders/{bidder['id']}",
        json={"prequal_status": "Suspended"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["prequal_status"] == "Suspended"
    assert r.json()["notes"] == "Preferred on HAL"


def test_a_patch_with_an_unknown_status_is_422(tmp_path, monkeypatch):
    """Refused by the request model, so the store never sees it."""
    client = _client(tmp_path, monkeypatch)
    bidder = create_bidder(client)
    r = client.patch(
        f"/api/workflow/bidders/{bidder['id']}",
        json={"prequal_status": "Sort of approved"},
    )
    assert r.status_code == 422


def test_patching_an_unknown_bidder_is_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.patch("/api/workflow/bidders/bdr_nope", json={"country": "Oman"})
    assert r.status_code == 404


def test_an_unreferenced_bidder_can_be_deleted(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    bidder = create_bidder(client)
    assert client.delete(f"/api/workflow/bidders/{bidder['id']}").status_code == 204
    assert client.get("/api/workflow/bidders").json()["bidders"] == []


def test_deleting_a_shortlisted_bidder_is_409_and_names_the_rfq(tmp_path, monkeypatch):
    """409, not 422: the bidder exists and the request is well-formed. The
    workflow is what says no."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    bidder = create_bidder(client)
    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": bidder["id"]})

    r = client.delete(f"/api/workflow/bidders/{bidder['id']}")
    assert r.status_code == 409
    assert "ADP-RFQ-2026-014" in r.json()["detail"]


def test_deleting_an_unknown_bidder_is_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.delete("/api/workflow/bidders/bdr_nope").status_code == 404


# -- candidates --------------------------------------------------------------


def test_candidates_evaluate_every_bidder_against_this_rfq(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    fits = create_bidder(client)
    misfits = create_bidder(
        client, name="Sandstone Piping Industries", trade_categories=["Piping"]
    )

    r = client.get(f"/api/workflow/rfqs/{rfq_id}/candidates")
    assert r.status_code == 200
    by_id = {c["bidder"]["id"]: c for c in r.json()["candidates"]}

    assert by_id[fits["id"]]["suitability"]["scope_fit"] is True
    assert by_id[fits["id"]]["suitability"]["eligible"] is True
    assert by_id[misfits["id"]]["suitability"]["scope_fit"] is False
    # A mismatch is a caution, so they stay invitable.
    assert by_id[misfits["id"]]["suitability"]["eligible"] is True
    assert by_id[misfits["id"]]["suitability"]["cautions"]


def test_candidates_lead_with_who_can_be_invited_without_an_argument(tmp_path, monkeypatch):
    """Ordered by how easy the invitation is to justify, then by name.

    Sorted server-side so the ordering rule has one definition, and because a
    registry of any size makes an unordered list useless: the reader is looking
    for who they *can* invite, not for who was registered first.
    """
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    create_bidder(client, name="Zephyr Switchgear Co.")          # eligible, fits
    create_bidder(client, name="Blue Harbour Marine Services",
                  trade_categories=["Marine"])                    # eligible, misfits
    create_bidder(client, name="Aurora Steelworks", prequal_status="Suspended")  # blocked

    order = [
        c["bidder"]["name"]
        for c in client.get(f"/api/workflow/rfqs/{rfq_id}/candidates").json()["candidates"]
    ]
    assert order == [
        "Zephyr Switchgear Co.",
        "Blue Harbour Marine Services",
        "Aurora Steelworks",
    ]


def test_candidates_flag_who_is_already_shortlisted(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    invited = create_bidder(client)
    spare = create_bidder(client, name="Northwind Valve Works")
    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": invited["id"]})

    by_id = {
        c["bidder"]["id"]: c
        for c in client.get(f"/api/workflow/rfqs/{rfq_id}/candidates").json()["candidates"]
    }
    assert by_id[invited["id"]]["shortlisted"] is True
    assert by_id[spare["id"]]["shortlisted"] is False


def test_candidates_for_an_unknown_rfq_is_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/workflow/rfqs/rfq_nope/candidates").status_code == 404


def test_a_blocked_bidder_reports_the_blocker_on_the_candidate_list(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    blocked = create_bidder(
        client, on_hold=True, hold_reason="Unresolved dispute on HAL-19"
    )

    candidate = next(
        c for c in client.get(f"/api/workflow/rfqs/{rfq_id}/candidates").json()["candidates"]
        if c["bidder"]["id"] == blocked["id"]
    )
    assert candidate["suitability"]["eligible"] is False
    assert "Unresolved dispute on HAL-19" in " ".join(candidate["suitability"]["blockers"])


# -- inviting from the registry ----------------------------------------------


def test_inviting_a_registry_bidder_snapshots_the_registry(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    bidder = create_bidder(client)

    r = client.post(
        f"/api/workflow/rfqs/{rfq_id}/shortlist",
        # A client sending a contradicting status is exactly what the store
        # ignores. The route must not helpfully forward it either.
        json={"vendor_id": bidder["id"], "prequal_status": "Not qualified"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["vendor_id"] == bidder["id"]
    assert r.json()["vendor_name"] == "Al Munara Switchgear LLC"
    assert r.json()["prequal_status"] == "Approved"


def test_inviting_a_blocked_bidder_without_a_reason_is_409(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    blocked = create_bidder(client, prequal_status="Suspended")

    r = client.post(
        f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": blocked["id"]}
    )
    assert r.status_code == 409
    assert "suspended" in r.json()["detail"].lower()
    assert client.get(f"/api/workflow/rfqs/{rfq_id}").json()["shortlist"] == []


def test_an_override_is_attributed_to_the_session_not_the_body(tmp_path, monkeypatch):
    """`override_by` is never an input, for the same reason `by` never is: a
    client-supplied one would let any signed-in user sign somebody else's name
    to the exception."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    blocked = create_bidder(client, prequal_status="Suspended")

    r = client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={
        "vendor_id": blocked["id"],
        "override_reason": "Sole source for this frame size",
        "override_by": "someone.else@example.com",
    })
    assert r.status_code == 201, r.text
    assert r.json()["override_by"] == ADMIN_EMAIL


def test_inviting_an_unknown_bidder_is_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": "bdr_nope"})
    assert r.status_code == 404


def test_the_free_text_shortlist_route_is_unchanged(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={
        "vendor_name": "A one-off fabricator",
        "prequal_status": "Under review",
        "scope_code_fit": False,
        "included": True,
    })
    assert r.status_code == 201, r.text
    assert r.json()["vendor_id"] is None
    assert r.json()["prequal_status"] == "Under review"


def test_a_shortlist_post_with_neither_a_vendor_id_nor_a_name_is_422(tmp_path, monkeypatch):
    """Missing data is never coerced to a passing value — an unnamed vendor
    assumed qualified is the record this feature exists to prevent."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    r = client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"included": True})
    assert r.status_code == 422


# -- the client-approval caution ----------------------------------------------


def test_the_roster_reports_a_bidder_off_the_client_list(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    create_bidder(client, approved_by=["Astra"])

    roster = client.get("/api/workflow/bidders").json()["bidders"]
    assert roster[0]["approval_caution"] == (
        "Al Munara Switchgear LLC is not on the ADNOC Approved Vendor List "
        "— approved by Astra only."
    )


def test_a_client_approved_bidder_carries_no_caution(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    create_bidder(client)

    roster = client.get("/api/workflow/bidders").json()["bidders"]
    assert roster[0]["approval_caution"] is None


def test_the_single_bidder_view_carries_it_too(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    bidder = create_bidder(client, approved_by=[])

    body = client.get(f"/api/workflow/bidders/{bidder['id']}").json()
    assert "has no approval recorded." in body["approval_caution"]


def test_the_caution_follows_a_patch_with_no_second_write(tmp_path, monkeypatch):
    """Derived, not stored: correcting `approved_by` is the only edit needed."""
    client = _client(tmp_path, monkeypatch)
    bidder = create_bidder(client, approved_by=["Astra"])

    r = client.patch(
        f"/api/workflow/bidders/{bidder['id']}", json={"approved_by": ["ADNOC", "Astra"]}
    )
    assert r.status_code == 200, r.text
    body = client.get(f"/api/workflow/bidders/{bidder['id']}").json()
    assert body["approval_caution"] is None


def test_a_bidder_off_the_client_list_needs_no_override_to_be_shortlisted(
    tmp_path, monkeypatch
):
    """A caution is not a blocker. If this ever 409s, the feature has changed
    meaning."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    bidder = create_bidder(client, approved_by=["Astra"])

    r = client.post(
        f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": bidder["id"]}
    )
    assert r.status_code == 201, r.text


# -- client approval on the shortlist ----------------------------------------
#
# The shortlist table shows who was invited; these say whether each of them is
# on the client's list. Derived on read from the registry — never a field on
# `ShortlistEntry`, for the same reason `approval_caution` is not a field on
# `Bidder`: a snapshot taken at invitation is wrong the moment `approved_by` is
# corrected, and the correction is the common case.
#
# `prequal_status` on the same row *is* a snapshot, deliberately, because it
# records the state the invitation was issued against. The two differ because
# they answer different questions: "what did we know then" and "who is approved
# now". Both on one row is the point.


def shortlist_of(client: TestClient, rfq_id: str) -> list[dict]:
    r = client.get(f"/api/workflow/rfqs/{rfq_id}")
    assert r.status_code == 200, r.text
    return r.json()["shortlist"]


def test_a_shortlisted_bidder_on_the_client_list_says_so(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    bidder = create_bidder(client)

    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": bidder["id"]})

    assert shortlist_of(client, rfq_id)[0]["client_approved"] is True


def test_a_shortlisted_bidder_off_the_client_list_says_so(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    bidder = create_bidder(client, approved_by=["Astra"])

    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": bidder["id"]})

    assert shortlist_of(client, rfq_id)[0]["client_approved"] is False


def test_a_free_text_vendor_reports_unknown_rather_than_unapproved(
    tmp_path, monkeypatch
):
    """None, not False. A vendor typed in by hand has no registry row, so there
    is nothing that says they are off the client's list — only that we cannot
    tell. Coercing that to False is the "missing data as a passing or failing
    value" mistake, and it would read on screen as a finding nobody made."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)

    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={
        "vendor_name": "Galfar", "prequal_status": "Qualified",
        "scope_code_fit": True, "included": True,
    })

    assert shortlist_of(client, rfq_id)[0]["client_approved"] is None


def test_the_shortlist_answer_follows_the_registry_with_no_second_write(
    tmp_path, monkeypatch
):
    """The invariant, stated as a test. Correcting the registry is the only edit
    needed; nothing rewrites the shortlist entry. A stored copy would still read
    False here, which is exactly the bug this shape exists to prevent."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    bidder = create_bidder(client, approved_by=["Astra"])
    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": bidder["id"]})
    assert shortlist_of(client, rfq_id)[0]["client_approved"] is False

    r = client.patch(
        f"/api/workflow/bidders/{bidder['id']}", json={"approved_by": ["ADNOC", "Astra"]}
    )
    assert r.status_code == 200, r.text

    assert shortlist_of(client, rfq_id)[0]["client_approved"] is True


def test_a_shortlist_row_carries_both_approvals_not_just_the_clients(
    tmp_path, monkeypatch
):
    """`client_approved` is one boolean about the client. Astra approval is not
    a function of it, so a screen wanting both has to be sent the list too."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    bidder = create_bidder(client, approved_by=["ADNOC", "Astra"])

    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": bidder["id"]})

    assert shortlist_of(client, rfq_id)[0]["approved_by"] == ["ADNOC", "Astra"]


def test_a_registry_row_nobody_approved_reports_an_empty_list(tmp_path, monkeypatch):
    """`[]` is a real answer: the row exists and carries no approval."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    bidder = create_bidder(client, approved_by=[])

    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": bidder["id"]})

    assert shortlist_of(client, rfq_id)[0]["approved_by"] == []


def test_a_free_text_vendor_reports_no_approvals_rather_than_none_held(
    tmp_path, monkeypatch
):
    """None, not `[]`. There is no registry row, so nothing says this vendor
    holds no approvals — only that we cannot tell. The same distinction
    `client_approved` already keeps, and for the same reason."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)

    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={
        "vendor_name": "Galfar", "prequal_status": "Qualified",
        "scope_code_fit": True, "included": True,
    })

    assert shortlist_of(client, rfq_id)[0]["approved_by"] is None


def test_the_approvals_follow_the_registry_with_no_second_write(tmp_path, monkeypatch):
    """Derived on read. A stored copy would still read ["Astra"] here, which is
    exactly what this shape exists to prevent."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)
    bidder = create_bidder(client, approved_by=["Astra"])
    client.post(f"/api/workflow/rfqs/{rfq_id}/shortlist", json={"vendor_id": bidder["id"]})
    assert shortlist_of(client, rfq_id)[0]["approved_by"] == ["Astra"]

    r = client.patch(
        f"/api/workflow/bidders/{bidder['id']}", json={"approved_by": ["ADNOC", "Astra"]}
    )
    assert r.status_code == 200, r.text

    assert shortlist_of(client, rfq_id)[0]["approved_by"] == ["ADNOC", "Astra"]


def test_the_rfq_names_the_client_approver_rather_than_leaving_it_spelled_out(
    tmp_path, monkeypatch
):
    """Same reason `/bidders/approved` sends it: the browser labelling a column
    "ADNOC" itself would be a second place that has to change for a second
    client."""
    client = _client(tmp_path, monkeypatch)
    rfq_id = create_rfq(client)

    assert client.get(f"/api/workflow/rfqs/{rfq_id}").json()["client_approver"] == "ADNOC"


# -- the available vendor list ------------------------------------------------
#
# `/bidders/available` is the list the item screen renders: on the client's
# Approved Vendor List *and* on ours. The filtering
# rule is `bidders.client_approved` and is tested there; what matters here is
# that the literal path is not swallowed by `/bidders/{bidder_id}`, that the
# optional narrowing works, and that the payload is the same shape every other
# bidder list returns.


def approved(client: TestClient, params: str = ""):
    r = client.get(f"/api/workflow/bidders/available{params}")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_available_list_needs_both_approvals(tmp_path, monkeypatch):
    """The whole point of the screen. One approval is the common case and the
    one being excluded: the client's list runs to 1 346 and ours to a hundred."""
    client = _client(tmp_path, monkeypatch)
    create_bidder(client, name="Al Munara Switchgear LLC",
                  approved_by=["ADNOC", "Astra"],
                  trade_categories=["SWITCHGEARS - LV -415V"])
    create_bidder(client, name="Northwind Valve Works",
                  approved_by=["ADNOC", "Astra"],
                  trade_categories=['VALVES - BALL - API 6D - UP TO 12"'])
    create_bidder(client, name="Client Only", approved_by=["ADNOC"])
    create_bidder(client, name="Ours Only", approved_by=["Astra"])

    body = approved(client)
    assert [b["name"] for b in body["bidders"]] == [
        "Al Munara Switchgear LLC",
        "Northwind Valve Works",
    ]
    assert body["total"] == 2
    # Which approvals this stands for, sent rather than spelled out in the
    # browser.
    assert body["approvers"] == ["ADNOC", "Astra"]


def test_a_vendor_only_we_approved_is_not_available(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    create_bidder(client, name="Silverdune Process Systems", approved_by=["Astra"])

    assert approved(client)["bidders"] == []


def test_a_vendor_only_the_client_approved_is_not_available(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    create_bidder(client, name="Al Munara Switchgear LLC", approved_by=["ADNOC"])

    assert approved(client)["bidders"] == []


def test_the_available_path_is_not_read_as_a_bidder_id(tmp_path, monkeypatch):
    """`/bidders/{bidder_id}` would swallow it if declared first, and the
    symptom is a 404 on every request rather than an error anybody can read."""
    client = _client(tmp_path, monkeypatch)
    r = client.get("/api/workflow/bidders/available")
    assert r.status_code == 200
    assert "bidders" in r.json()


def test_the_available_list_narrows_to_a_discipline_when_asked(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    create_bidder(client, name="Al Munara Switchgear LLC",
                  approved_by=["ADNOC", "Astra"],
                  trade_categories=["SWITCHGEARS - LV -415V"])
    create_bidder(client, name="Northwind Valve Works",
                  approved_by=["ADNOC", "Astra"],
                  trade_categories=['VALVES - BALL - API 6D - UP TO 12"'])

    body = approved(client, "?discipline=SWITCHGEARS - LV -415V")
    assert [b["name"] for b in body["bidders"]] == ["Al Munara Switchgear LLC"]
    assert body["total"] == 1


def test_a_blank_discipline_returns_the_whole_list(tmp_path, monkeypatch):
    """Absent means "the whole available list", not "nobody" — an unscoped item
    has no discipline to send."""
    client = _client(tmp_path, monkeypatch)
    create_bidder(client, approved_by=["ADNOC", "Astra"],
                  trade_categories=["SWITCHGEARS - LV -415V"])

    assert approved(client, "?discipline=")["total"] == 1


def test_an_available_bidder_carries_the_values_the_screen_must_not_compute(
    tmp_path, monkeypatch
):
    """Same payload as every other bidder list. A screen deriving expiry from
    its own clock would be a second definition of `effective_prequal`."""
    client = _client(tmp_path, monkeypatch)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    create_bidder(client, approved_by=["ADNOC", "Astra"],
                  prequal_expires_on=yesterday)

    only = approved(client)["bidders"][0]
    assert only["effective_prequal"] == "Expired"
    assert only["approval_caution"] is None
    assert only["invited_count"] == 0


# -- the discipline vocabulary ------------------------------------------------


def test_the_disciplines_route_serves_the_vocabulary(tmp_path, monkeypatch):
    """Served, not hardcoded in the browser: `workflow/disciplines.py` is the
    one definition, and a third family should be an edit there alone."""
    client = _client(tmp_path, monkeypatch)
    r = client.get("/api/workflow/disciplines")
    assert r.status_code == 200, r.text
    names = [d["name"] for d in r.json()["disciplines"]]
    assert names == ["Cables", "Generators"]


def test_each_discipline_carries_the_product_groups_behind_it(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    body = client.get("/api/workflow/disciplines").json()["disciplines"]
    cables = next(d for d in body if d["name"] == "Cables")
    assert "CABLES - MV (UP TO 33KV)POWER TRANSMISSION" in cables["product_groups"]


def test_a_discipline_narrows_the_approved_list_through_its_family(
    tmp_path, monkeypatch
):
    """The join this vocabulary exists for. The vendor is registered for a
    product group, never for "Cables" — asking for the family has to reach
    them anyway, or the screen empties exactly as it did before."""
    client = _client(tmp_path, monkeypatch)
    create_bidder(client, name="Ras Dana Cables & Conductors",
                  approved_by=["ADNOC", "Astra"],
                  trade_categories=["CABLES - MV (UP TO 33KV)POWER TRANSMISSION"])
    create_bidder(client, name="Falcon Bay Rotating Equipment",
                  approved_by=["ADNOC", "Astra"],
                  trade_categories=["GENERATOR POWER-DIESEL ENGINE DRIVEN"])

    body = approved(client, "?discipline=Cables")
    assert [b["name"] for b in body["bidders"]] == ["Ras Dana Cables & Conductors"]
    assert body["total"] == 1
