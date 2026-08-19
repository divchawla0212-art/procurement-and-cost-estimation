"""The contact directory across two runs.

Every defect this repository has shipped and not caught needed two runs or two
modules to see. These are the two-run tests; the per-module ones live in
`test_contact_db.py`, `test_vendor_contact_store.py`,
`test_contact_import.py` and `test_vendor_contact_endpoints.py`.

The load-bearing property of every test here is that **run 2 reads state that
came off disk**, never state that came out of the call that wrote it. A test
that asserts against the store object it just mutated is a single-run test
wearing two runs' clothes.

Each test names the invariant it defends (§9 of the design):

- **V-A** `vendor_contacts` holds exactly the vendors of the most recent upload
- **V-B** `vendor_contact_emails` holds exactly the addresses of those rows
- **V-C** `workflow.json` has no `vendor_contacts` key
- **V-D** reloading the registry leaves both contact tables untouched
- **V-E** every stored `name_key` equals `fold(vendor_name)` of its own row
- **V-F** no `ShortlistEntry` stores the address it reports

**Five of PLAN-TEMPLATE's nine required matrix rows have no analogue here and
are deliberately absent rather than fabricated.** This subsystem calls no
model — the parser is `openpyxl` over a two-column sheet and the read is a dict
lookup — so the four rows about a provider failing mid-run, a partial LLM
answer, a prompt-version bump and an omitted optional array read as a failure
have nothing to mutate. And an uploaded contact sheet has no revision lineage:
a later sheet *replaces* the directory rather than superseding a sibling
document, so the superseded-document row has no door to knock on. Inventing
five rows that assert nothing would make the matrix look complete while
measuring less than it does now.

The API helpers are **imported** from `test_vendor_contact_endpoints.py` rather
than copied. A copy would agree with a wrong original, which is the rule
`test_mock_round_fixtures.py` records for `_already_done`.
"""
import json
from datetime import datetime, timezone

from tests.test_vendor_contact_endpoints import (
    _an_rfq_with_one_invited_vendor,
    _client,
    _sheet,
    _upload,
)
from workflow import bidder_db, persistence
from workflow.models.bidder import ADNOC, ASTRA, Bidder
from workflow.models.vendor_contact import VendorContact

WHEN = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)


def a_contact(name, emails) -> VendorContact:
    return VendorContact(
        vendor_name=name, emails=emails, source_document="vendors.xlsx",
        uploaded_by="buyer@example.com", uploaded_at=WHEN,
    )


def test_a_second_upload_does_not_leave_the_first_uploads_vendor_behind(tmp_path):
    """V-A. Run 2 replaces; it does not merge.

    Single-run blindness: an implementation that `update`s the dict instead of
    rebinding it holds exactly the right vendors on the run that wrote them,
    because run 1's upload is the only one there is. Only a second upload over
    a directory that came off disk can tell the two apart.
    """
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([
            a_contact("DANWAY ABU DHABI L.L.C", ["sales@danway.example"]),
            a_contact("BIN SARI", ["bids@binsari.example"]),
        ])
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([a_contact("BIN SARI", ["bids@binsari.example"])])
    reloaded = persistence.load(root)
    assert reloaded.emails_for("DANWAY ABU DHABI L.L.C") is None
    assert reloaded.emails_for("BIN SARI") == ["bids@binsari.example"]


def test_a_second_upload_does_not_leave_a_dropped_address_behind(tmp_path):
    """V-B. The vendor stays and one of their addresses goes.

    Single-run blindness: the child table is only reachable through its parent,
    so a `DELETE FROM vendor_contacts` that fails to cascade leaves orphaned
    addresses that no run-1 read can see. They surface on run 2, when the
    parent row is re-inserted under the same `name_key` and adopts them.
    """
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([
            a_contact("DANWAY ABU DHABI L.L.C",
                      ["sales@danway.example", "old@danway.example"]),
        ])
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([
            a_contact("DANWAY ABU DHABI L.L.C", ["sales@danway.example"]),
        ])
    assert persistence.load(root).emails_for("DANWAY ABU DHABI L.L.C") == [
        "sales@danway.example",
    ]


def test_the_folded_lookup_still_resolves_after_a_reload(tmp_path):
    """V-E. Run 2's key came off disk, not out of the call that wrote it. A
    fold applied on write and not on read passes run 1 and fails here."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([
            a_contact("DANWAY  Abu Dhabi L.L.C", ["sales@danway.example"]),
        ])
    reloaded = persistence.load(root)
    assert reloaded.emails_for("danway abu dhabi l.l.c") == ["sales@danway.example"]


def test_reloading_the_registry_between_runs_leaves_every_address(tmp_path):
    """V-D. Two modules, one file. This is the row a single-module test cannot
    reach.

    `bidder_db` and `contact_db` share `bidders.db`, so a `DROP`-and-recreate
    on either side would silently take the other's tables with it — and a
    `bidder_db` test would stay green throughout, because the registry it
    rebuilt is exactly right.
    """
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([
            a_contact("DANWAY ABU DHABI L.L.C",
                      ["sales@danway.example", "bids@danway.example"]),
        ])
    bidder_db.replace_all(root, [
        Bidder(id="bdr_1", name="DANWAY ABU DHABI L.L.C", approved_by=[ADNOC]),
    ])
    assert persistence.load(root).emails_for("DANWAY ABU DHABI L.L.C") == [
        "sales@danway.example", "bids@danway.example",
    ]


def test_a_shortlist_added_after_the_upload_still_gets_the_address(
    tmp_path, monkeypatch
):
    """V-F. Derived on read — the entry did not exist when the sheet landed.

    Run 1 is the upload. Run 2 raises the RFQ and invites the vendor, and every
    call in between goes over HTTP, so the store the invitation is written into
    and the store the row is read out of were both loaded from disk. An
    implementation that copied the address onto `ShortlistEntry` at invitation
    would pass the endpoint tests, where the sheet lands first — but it would
    have *written* the address, and the raw-document assertion below is what
    tells the two apart.
    """
    client = _client(tmp_path, monkeypatch)
    _upload(client, _sheet(tmp_path, [
        ("DANWAY ABU DHABI L.L.C", "sales@danway.example; bids@danway.example"),
    ]))

    rfq_id = _an_rfq_with_one_invited_vendor(
        client, tmp_path, "DANWAY ABU DHABI L.L.C"
    )

    (row,) = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["shortlist"]
    assert row["email"] == ["sales@danway.example", "bids@danway.example"]
    # Nothing rewrote the shortlist: the address is on the payload and nowhere
    # in the document the payload was built from.
    with open(persistence.workflow_path(str(tmp_path)), encoding="utf-8") as handle:
        raw = handle.read()
    assert "sales@danway.example" not in raw
    assert "vendor_contacts" not in json.loads(raw)


def test_editing_a_vendors_approvals_does_not_disturb_their_address(
    tmp_path, monkeypatch
):
    """V-F. The three derived keys on that row are independent.

    `client_approved`, `approved_by` and `email` are all computed by
    `_shortlist_payload`, and the first two come from the registry while the
    third comes from the directory. Editing the registry between runs must move
    the first two and leave the third exactly where it was.

    Two runs, and run 2 reads off disk: the PATCH goes through
    `locked_update`, which reloads the store, rewrites `workflow.json` and —
    because the directory did not change — must leave `bidders.db`'s contact
    tables alone. A save path that rewrote the directory from a store field it
    had failed to hydrate would empty it here and nowhere else.
    """
    client = _client(tmp_path, monkeypatch)
    bidder_db.replace_all(str(tmp_path), [
        Bidder(id="bdr_1", name="DANWAY ABU DHABI L.L.C", approved_by=[ADNOC]),
    ])
    _upload(client, _sheet(tmp_path, [
        ("DANWAY ABU DHABI L.L.C", "sales@danway.example"),
    ]))
    rfq_id = _an_rfq_with_one_invited_vendor(
        client, tmp_path, "DANWAY ABU DHABI L.L.C", vendor_id="bdr_1"
    )

    (before,) = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["shortlist"]
    assert before["approved_by"] == [ADNOC]
    assert before["email"] == ["sales@danway.example"]

    patched = client.patch("/api/workflow/bidders/bdr_1",
                           json={"approved_by": [ADNOC, ASTRA]})
    assert patched.status_code == 200, patched.text

    (after,) = client.get(f"/api/workflow/rfqs/{rfq_id}").json()["shortlist"]
    assert after["approved_by"] == [ADNOC, ASTRA]
    assert after["email"] == ["sales@danway.example"]
    # Still derived, not copied across by the write that moved the approvals.
    with open(persistence.workflow_path(str(tmp_path)), encoding="utf-8") as handle:
        raw = handle.read()
    assert "sales@danway.example" not in raw


def test_a_document_written_on_run_one_still_carries_no_contact_key(tmp_path):
    """V-C. Written by run 1, loaded and rewritten by run 2 — the key must be
    absent both times, since a `from_document` that invented one would only
    show up on the second write."""
    root = str(tmp_path)
    with persistence.locked_update(root) as store:
        store.set_vendor_contacts([a_contact("DANWAY", ["sales@danway.example"])])
    first = json.loads(open(persistence.workflow_path(root), encoding="utf-8").read())
    with persistence.locked_update(root):
        pass
    second = json.loads(open(persistence.workflow_path(root), encoding="utf-8").read())
    assert "vendor_contacts" not in first
    assert "vendor_contacts" not in second
